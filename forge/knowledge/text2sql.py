from __future__ import annotations

from dataclasses import dataclass
import asyncio
from pathlib import Path
import re
from typing import Any, Awaitable, Callable, Mapping, Sequence

from sqlglot import exp, parse

from forge.application.ports import ModelGateway
from forge.domain.models import Evidence, Identity
from forge.infrastructure.telemetry import emit, fingerprint, error_fields, traced


class UnsafeQuery(ValueError):
    """模型生成的查询不符合只读和授权约束。"""


@dataclass(frozen=True)
class QueryPolicy:
    tables: Mapping[str, frozenset[str]]
    functions: frozenset[str] = frozenset(
        {
            "avg", "cast", "coalesce", "count", "current_date", "current_timestamp",
            "date_trunc", "lower", "max", "min", "sum", "upper",
        }
    )
    max_rows: int = 100


class ReadOnlySqlGuard:
    """以 AST 验证 SQL；数据库只读角色与 RLS 仍是最终防线。"""

    _forbidden = (
        exp.Insert,
        exp.Update,
        exp.Delete,
        exp.Create,
        exp.Drop,
        exp.Alter,
        exp.Command,
        exp.Copy,
        exp.Merge,
        exp.Grant,
        exp.Revoke,
        exp.Transaction,
        exp.Lock,
    )

    def __init__(self, policy: QueryPolicy) -> None:
        self._policy = policy

    def validate(self, sql: str) -> str:
        statements = parse(sql, read="postgres")
        if len(statements) != 1:
            raise UnsafeQuery("只允许一条 SQL")
        root = statements[0]
        if not isinstance(root, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
            raise UnsafeQuery("只允许 SELECT 查询")
        if any(root.find_all(self._forbidden)):
            raise UnsafeQuery("查询包含写入、DDL 或控制命令")
        if root.args.get("locks"):
            raise UnsafeQuery("禁止锁定读取")

        allowed_tables = {name.casefold(): columns for name, columns in self._policy.tables.items()}
        cte_columns = {
            cte.alias_or_name.casefold(): frozenset(
                expression.alias_or_name.casefold()
                for expression in cte.this.expressions
                if expression.alias_or_name
            )
            for cte in root.find_all(exp.CTE)
        }
        referenced_tables: set[str] = set()
        for table in root.find_all(exp.Table):
            if table.catalog or table.db:
                raise UnsafeQuery("禁止显式跨库或跨 schema 查询")
            name = table.name.casefold()
            if name in cte_columns:
                continue
            if name not in allowed_tables:
                raise UnsafeQuery(f"未授权的数据表：{table.name}")
            referenced_tables.add(name)
        if not referenced_tables:
            raise UnsafeQuery("查询必须访问获授权的数据表")

        if root.find(exp.Star) is not None:
            raise UnsafeQuery("禁止 SELECT *，必须声明所需字段")

        available_columns = set().union(
            *(allowed_tables[name] for name in referenced_tables),
            *(columns for columns in cte_columns.values()),
        )
        for column in root.find_all(exp.Column):
            if isinstance(column.this, exp.Star):
                raise UnsafeQuery("禁止 SELECT *，必须声明所需字段")
            if column.name.casefold() not in available_columns:
                raise UnsafeQuery(f"未授权的字段：{column.name}")

        for function in root.find_all(exp.Func):
            if isinstance(function, exp.Binary):
                continue
            name = function.sql_name().casefold()
            if name not in self._policy.functions:
                raise UnsafeQuery(f"未授权的函数：{name}")

        limit = root.args.get("limit")
        if limit is None:
            root = root.limit(self._policy.max_rows)
        else:
            expression = limit.expression
            if not isinstance(expression, exp.Literal) or not expression.is_int:
                raise UnsafeQuery("LIMIT 必须是整数常量")
            if int(expression.this) > self._policy.max_rows:
                limit.set("expression", exp.Literal.number(self._policy.max_rows))
        return root.sql(dialect="postgres")

    def referenced_tables(self, sql: str) -> frozenset[str]:
        statements = parse(sql, read="postgres")
        if len(statements) != 1:
            return frozenset()
        root = statements[0]
        cte_names = {cte.alias_or_name.casefold() for cte in root.find_all(exp.CTE)}
        allowed = {name.casefold() for name in self._policy.tables}
        return frozenset(
            table.name.casefold()
            for table in root.find_all(exp.Table)
            if table.name.casefold() in allowed and table.name.casefold() not in cte_names
        )


class SkillResourceRouter:
    """根据问题域选择最小必要的 Text2SQL skill 资源集合。"""

    all_resources = ("company_relations", "company_business", "hr_business_terms", "legal_terms")
    model_categories = {
        "company_relations": ("company_relations",),
        "company_business": ("company_business",),
        "hr_business_terms": ("hr_business_terms",),
        "legal_terms": ("legal_terms",),
    }

    @traced("skill.route_model")
    async def select_with_model(self, question: str, model: ModelGateway) -> tuple[str, ...]:
        """始终由小模型选择预设类别；模型不能指定文件路径。"""
        categories = list(self.model_categories)
        try:
            result = await model.structured(
                system=(
                    "根据问题选择需要加载的公司知识 Markdown 类别，可多选。"
                    "只允许返回 company_relations、company_business、hr_business_terms、legal_terms。"
                    "每个类别文件都包含业务口径、schema 和只读查询示例。"
                ),
                prompt=f"问题：{question}",
                schema={
                    "type": "object",
                    "properties": {
                        "categories": {
                            "type": "array",
                            "items": {"type": "string", "enum": categories},
                        }
                    },
                    "required": ["categories"],
                    "additionalProperties": False,
                },
            )
            chosen = result.get("categories", [])
            if not isinstance(chosen, list) or not chosen:
                await emit("fallback.used", status="degraded", reason="router_empty_selection", strategy="all_resources")
                return self.all_resources
            selected: list[str] = []
            for category in chosen:
                resources = self.model_categories.get(category)
                if resources is None:
                    await emit("fallback.used", status="degraded", reason="router_invalid_selection", strategy="all_resources")
                    return self.all_resources
                for resource in resources:
                    if resource not in selected:
                        selected.append(resource)
            return tuple(selected) if selected else self.all_resources
        except Exception as exc:
            await emit("fallback.used", status="degraded", reason="router_model_failed", strategy="all_resources", **error_fields(exc))
            return self.all_resources


class SkillLoader:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def load(self, names: Sequence[str]) -> str:
        return "\n\n".join(self.load_documents(names).values())

    def load_documents(self, names: Sequence[str]) -> dict[str, str]:
        documents: dict[str, str] = {}
        for name in names:
            if not name.replace("_", "").isalnum():
                raise ValueError("非法 skill 资源名")
            path = (self._root / f"{name}.md").resolve()
            if self._root not in path.parents:
                raise ValueError("skill 资源越界")
            documents[name] = path.read_text(encoding="utf-8")
        return documents


class Text2SqlSkill:
    def __init__(
        self,
        *,
        model: ModelGateway,
        loader: SkillLoader,
        guard: ReadOnlySqlGuard,
        execute: Callable[[str, Identity], Awaitable[list[dict[str, Any]]]],
        router: SkillResourceRouter | None = None,
        router_model: ModelGateway | None = None,
    ) -> None:
        self._model = model
        self._loader = loader
        self._guard = guard
        self._execute = execute
        self._router = router or SkillResourceRouter()
        self._router_model = router_model or model

    @traced("skill.text2sql")
    async def ask(self, question: str, *, identity: Identity) -> Sequence[Evidence]:
        resources = await self._router.select_with_model(question, self._router_model)
        route = "model"
        documents = await asyncio.to_thread(self._loader.load_documents, resources)
        await emit("skill.loaded", route=route, resources={name: fingerprint(content) for name, content in documents.items()})
        context = "\n\n".join(documents.values())
        phase = "sql_generation"
        try:
            result = await self._model.structured(
                system=(
                    "你是只读公司知识查询器。只生成一个 PostgreSQL SELECT；"
                    "身份和租户过滤由数据库执行，不要猜测权限。"
                ),
                prompt=f"{context}\n\n问题：{question}",
                schema={
                    "type": "object",
                    "properties": {"sql": {"type": "string"}, "reason": {"type": "string"}},
                    "required": ["sql"],
                    "additionalProperties": False,
                },
            )
            phase = "sql_guard"
            sql = self._guard.validate(str(result["sql"]))
            tables = sorted(self._guard.referenced_tables(sql))
            await emit("sql.validated", tables=tables, query_fingerprint=fingerprint(sql))
            phase = "sql_execution"
            rows = await self._execute(sql, identity)
        except Exception as exc:
            fallback = self._markdown_fallback(question, documents, exc)
            if fallback:
                await emit("fallback.used", status="degraded", reason="sql_query_failed", strategy="skill_md",
                           failed_phase=phase, verification="unverified", result_count=len(fallback), **error_fields(exc))
                return fallback
            raise
        return [
            Evidence(
                source_kind="database",
                title=str(
                    row.get("term_name")
                    or row.get("name")
                    or row.get("title")
                    or "锐博公司知识库"
                ),
                url=str(row.get("source_url") or ""),
                quote=str(
                    row.get("claim")
                    or row.get("definition")
                    or row.get("required_action")
                    or row.get("source_quote")
                    or row.get("description")
                    or row
                ),
                authority=0.9,
                metadata={
                    "storage": "postgresql",
                    "tables": tables,
                    "row": row,
                    "query_reason": result.get("reason", ""),
                    "loaded_resources": list(resources),
                },
            )
            for row in rows
        ]

    @staticmethod
    def _markdown_fallback(
        question: str, documents: Mapping[str, str], error: Exception
    ) -> list[Evidence]:
        """返回已加载口径文件中的原文段落，不把 schema 当业务数据。"""
        query_terms = Text2SqlSkill._search_terms(question)
        if not query_terms:
            return []
        warning = (
            f"PostgreSQL 查询失败（{type(error).__name__}）；以下内容仅为 Skill Markdown 原文口径，"
            "未从数据库查证，不能视为数据库记录或已核实事实。"
        )
        matches: list[tuple[int, str, str, int]] = []
        for resource, content in documents.items():
            if resource.startswith("schema_"):
                continue
            for fragment, line_number in Text2SqlSkill._markdown_fragments(content):
                terms = Text2SqlSkill._search_terms(fragment)
                score = sum(1 for term in query_terms if term in terms)
                if score:
                    matches.append((score, resource, fragment, line_number))
        matches.sort(key=lambda item: (-item[0], item[1], item[3], item[2]))
        evidence: list[Evidence] = []
        seen: set[str] = set()
        total_chars = 0
        for _, resource, fragment, line_number in matches:
            if fragment in seen or total_chars + len(fragment) > 2400:
                continue
            seen.add(fragment)
            total_chars += len(fragment)
            evidence.append(
                Evidence(
                    source_kind="skill_md",
                    title=f"Skill Markdown · {resource}.md",
                    quote=fragment,
                    authority=0.2,
                    metadata={
                        "storage": "markdown_skill",
                        "resource": resource,
                        "file": f"skills/company_knowledge/{resource}.md",
                        "line": line_number,
                        "verification": "unverified",
                        "database_query_status": "failed",
                        "warning": warning,
                    },
                )
            )
            if len(evidence) >= 4:
                break
        return evidence

    @staticmethod
    def _markdown_fragments(content: str) -> list[tuple[str, int]]:
        lines = content.splitlines()
        fragments: list[tuple[str, int]] = []
        index = 0
        while index < len(lines):
            while index < len(lines) and (
                not lines[index].strip()
                or lines[index].lstrip().startswith("#")
                or lines[index].lstrip().startswith("```")
            ):
                index += 1
            if index >= len(lines):
                break
            start = index
            block: list[str] = []
            while index < len(lines) and lines[index].strip():
                if lines[index].lstrip().startswith("```"):
                    break
                block.append(lines[index].strip())
                index += 1
            if not block:
                index += 1
                continue
            if len(block) > 1 and all(
                line.startswith(("- ", "* ", "+ ")) or re.match(r"\d+[.)] ", line)
                for line in block
            ):
                fragments.extend((line, start + offset + 1) for offset, line in enumerate(block))
            else:
                fragments.append(("\n".join(block), start + 1))
        return fragments

    @staticmethod
    def _search_terms(text: str) -> set[str]:
        normalized = text.casefold()
        terms = set(re.findall(r"[a-z0-9_]{2,}", normalized))
        stop = {"哪些", "如何", "查询", "问题", "公司", "业务", "相关", "数据", "口径"}
        for run in re.findall(r"[\u4e00-\u9fff]+", normalized):
            terms.update(
                run[index : index + 2]
                for index in range(len(run) - 1)
                if run[index : index + 2] not in stop
            )
        return terms
