from __future__ import annotations

import json
import hashlib
import asyncio
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4


from forge.application.ports import EvidenceSearch, ModelGateway, ProgressReporter, ReadOnlyQuery
from forge.domain.models import ArticleFact, CriticIssue, Evidence, Identity, OutlineSection
from forge.knowledge.research import EvidenceResearcher
from forge.knowledge.article_skills import ArticleTypeSkill
from forge.workflow.state import WorkflowState
from forge.infrastructure.telemetry import emit, error_fields
from langgraph.types import interrupt


@dataclass
class WorkflowNodes:
    model: ModelGateway
    researcher: EvidenceResearcher
    search: EvidenceSearch
    company: ReadOnlyQuery
    article_types: ArticleTypeSkill | None = None
    progress: ProgressReporter | None = None
    critic_max_failures: int = 3
    fact_policy: str = ""
    fact_judge: ModelGateway | None = None

    async def _progress(
        self, state: WorkflowState, stage: str, code: str, message: str
    ) -> None:
        if self.progress is None:
            return
        try:
            await self.progress.report(
                conversation_id=UUID(state["conversation_id"]),
                identity=Identity.model_validate(state["identity"]),
                stage=stage,
                code=code,
                message=message,
            )
        except Exception as exc:
            # 进度反馈不能中断文章工作流；业务结果仍由最终投影持久化。
            await emit("progress.failed", status="failed", code=code, **error_fields(exc))
            return

    async def intent(self, state: WorkflowState) -> dict[str, Any]:
        await self._progress(state, "researching", "intent", "正在识别写作或知识问答意图")
        reset = {
            "facts_changed": False, "outline_changed": False, "draft_changed": False,
            "critic_issues": [], "pending_conflicts": [], "critic_status": None,
            "stop_reason": "", "error": "", "warnings": [],
            "facts_confirmed": False, "outline_confirmed": False,
        }
        if state.get("requested_operation") == "generate":
            return {"intent": "generate", "stage": "routing", **reset}
        if state.get("requested_operation") == "modify":
            return {"intent": "modify", "stage": "routing", **reset}
        result = await self.model.structured(
            system=(
                "识别用户意图，只能输出 generate、modify、question、clarify。"
                "一段对话只有一篇文章，提问默认只回答，不修改文章。"
            ),
            prompt=f"当前有稿件：{bool(state.get('draft'))}\n用户消息：{state.get('user_message', '')}",
            schema={
                "type": "object",
                "properties": {"intent": {"enum": ["generate", "modify", "question", "clarify"]}},
                "required": ["intent"],
            },
        )
        return {"intent": result["intent"], "stage": "routing", **reset}

    async def planner(self, state: WorkflowState) -> dict[str, Any]:
        article_type: dict[str, Any] = {}
        type_warnings: list[str] = []
        await self._progress(
            state,
            "researching",
            "evidence_research",
            "正在抽取原文事实，并查询公司数据库、Tavily 与历史文档向量",
        )
        outcome = await self.researcher.collect(
            source_text=state["source_text"],
            direction=state.get("direction") or "沿用原文主题与方向",
            user_request=state.get("user_message", ""),
            identity=Identity.model_validate(state["identity"]),
        )
        if self.article_types is not None:
            await self._progress(state, "planning", "article_type", "正在根据原文事实选择文章类型")
            selection = await self.article_types.resolve(
                source_text=state["source_text"],
                direction=state.get("direction") or "沿用原文主题与方向",
                user_request=state.get("user_message", ""),
            )
            article_type = {"article_type": selection.code, "article_type_name": selection.name,
                            "article_skill": selection.skill}
            if selection.warning:
                type_warnings.append(selection.warning)
        source_names = {"database": "公司数据库", "web": "Tavily", "vector": "pgvector"}
        completed = "、".join(source_names[item] for item in sorted(outcome.completed_sources))
        await self._progress(
            state,
            "planning",
            "facts_ready",
            f"已完成{completed or '原文'}检索，正在整理事实清单",
        )
        return {
            **article_type,
            "article_thesis": outcome.thesis,
            "key_points": outcome.key_points,
            "fact_policy": self.fact_policy,
            "facts": [fact.model_dump(mode="json") for fact in outcome.facts],
            "fact_version": 1,
            "warnings": [*type_warnings, *outcome.warnings],
            "stage": "planning_outline",
            "facts_changed": True,
        }

    async def outline(self, state: WorkflowState) -> dict[str, Any]:
        await self._progress(state, "planning", "outline", "正在按事实清单编排逐段大纲")
        facts = [ArticleFact.model_validate(item) for item in state.get("facts", []) if not item.get("excluded")]
        result = await self.model.structured(
            system=(
                "为文章生成逐段大纲。每段写明目的、主要内容和使用的 fact_ids；"
                "只能引用提供的事实 ID。每段围绕一个表达重点，信息不重复且不扩展无来源旁支。"
                "严格遵守附带的事实筛选规范和文章类型规范。\n" + state.get("fact_policy", "")
            ),
            prompt=json.dumps(
                {
                    "direction": state.get("direction", "沿用原文主题与方向"),
                    "thesis": state.get("article_thesis", ""),
                    "key_points": state.get("key_points", []),
                    "article_type": state.get("article_type_name", "沿用原文类型"),
                    "article_type_skill": state.get("article_skill", ""),
                    "facts": [
                        {"fact_id": str(f.fact_id), "claim": f.claim, "verification": f.verification}
                        for f in facts
                    ],
                },
                ensure_ascii=False,
            ),
            schema={
                "type": "object",
                "properties": {
                    "sections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "purpose": {"type": "string"},
                                "main_points": {"type": "array", "items": {"type": "string"}},
                                "fact_ids": {"type": "array", "items": {"type": "string"}},
                                "paragraphs": {"type": "array", "minItems": 1, "items": {
                                    "type": "object",
                                    "properties": {
                                        "purpose": {"type": "string"},
                                        "main_points": {"type": "array", "items": {"type": "string"}},
                                        "fact_ids": {"type": "array", "items": {"type": "string"}},
                                    },
                                    "required": ["purpose", "main_points", "fact_ids"],
                                }},
                            },
                            "required": ["title", "purpose", "main_points", "fact_ids", "paragraphs"],
                        },
                    }
                },
                "required": ["sections"],
            },
        )
        allowed = {str(f.fact_id) for f in facts}
        sections: list[OutlineSection] = []
        for order, raw in enumerate(result.get("sections", []), start=1):
            raw_ids = [item for item in raw.get("fact_ids", []) if item in allowed]
            paragraphs = []
            used_ids: set[str] = set()
            for paragraph_order, paragraph in enumerate(raw.get("paragraphs", []), start=1):
                paragraph_ids = [item for item in paragraph.get("fact_ids", []) if item in allowed]
                if not paragraph_ids:
                    continue
                used_ids.update(paragraph_ids)
                paragraphs.append({
                    "order": paragraph_order,
                    "purpose": paragraph["purpose"],
                    "main_points": paragraph["main_points"],
                    "fact_ids": paragraph_ids,
                    "required_fact_ids": list(paragraph_ids),
                })
            if not paragraphs:
                continue
            sections.append(
                OutlineSection(
                    order=order,
                    title=raw["title"],
                    purpose=raw["purpose"],
                    main_points=raw["main_points"],
                    fact_ids=raw_ids,
                    paragraphs=paragraphs,
                )
            )
        if not sections:
            raise ValueError("模型没有生成有效大纲")
        assigned = {str(fact_id) for section in sections for paragraph in section.paragraphs
                    for fact_id in paragraph.required_fact_ids}
        missing = {str(fact.fact_id) for fact in facts if fact.required_for_article} - assigned
        if missing:
            raise ValueError("大纲遗漏必写事实：" + ", ".join(sorted(missing)))
        return {
            "outline": [section.model_dump(mode="json") for section in sections],
            "outline_version": state.get("outline_version", 0) + 1,
            "stage": "planning_outline",
            "outline_changed": True,
        }

    async def fact_approval(self, state: WorkflowState) -> dict[str, Any]:
        if state.get("facts_confirmed"):
            return {}
        conflicts = [item for item in state.get("facts", [])
                     if item.get("verification") == "conflicted" and not item.get("decision_id")]
        request = {"kind": "facts", "fact_version": state.get("fact_version"),
                   "facts": state.get("facts", []), "conflicts": conflicts,
                   "thesis": state.get("article_thesis"),
                   "key_points": state.get("key_points", [])}
        decision = interrupt(request)
        if decision.get("action") != "confirm" or decision.get("fact_version") != state.get("fact_version"):
            raise ValueError("事实清单需要确认，且事实版本必须匹配")
        original = {str(item["fact_id"]): ArticleFact.model_validate(item) for item in state.get("facts", [])}
        incoming = decision.get("facts", state.get("facts", []))
        selected = {str(item["fact_id"]): ArticleFact.model_validate(item) for item in incoming}
        records = list(state.get("user_decisions", []))
        changed = False
        conflict_choices = {str(item.get("fact_id")): item for item in decision.get("conflict_decisions", [])}
        if any(str(item["fact_id"]) not in conflict_choices for item in conflicts):
            raise ValueError("请逐条处理所有已发现的来源冲突")
        for item in conflicts:
            fact_id = str(item["fact_id"])
            choice = conflict_choices[fact_id]
            action = choice.get("action")
            fact = selected.get(fact_id)
            if fact is None or action not in {"keep", "replace", "exclude"}:
                raise ValueError("冲突事实必须选择保留、修改或删除")
            if action == "keep" and fact.claim != original[fact_id].claim:
                raise ValueError("选择保留时不能同时修改事实内容")
            if action == "replace":
                fact.claim = str(choice.get("claim", fact.claim)).strip()
                if not fact.claim:
                    raise ValueError("修改事实不能为空")
                fact.version = original[fact_id].version + 1
                fact.verification = "pending"
                fact.mark_reasons.add("user_edited")
            elif action == "exclude":
                fact.excluded = True
                fact.version = original[fact_id].version + 1
                fact.mark_reasons.add("user_deleted")
            else:
                fact.verification = "conflicted"
                fact.mark_reasons.add("user_locked")
            fact.marked = True
            decision_id = uuid4()
            fact.decision_id = decision_id
            changed = True
            records.append({"decision_id": str(decision_id), "fact_id": fact_id, "fact_version": fact.version,
                            "action": action, "actor_kind": "user", "rule": "user_planner_conflict",
                            "note": str(choice.get("note", "用户已裁决")), "conflict": item})
        for fact_id, fact in selected.items():
            before = original.get(fact_id)
            if before is None:
                fact.origin = "user"
                fact.version = 1
                fact.marked = True
                fact.required_for_article = True
                fact.mark_reasons.add("user_created")
                fact.verification = "unverified"
                fact.evidence = []
                changed = True
            else:
                content_changed = any((fact.claim != before.claim, fact.subject != before.subject,
                                       fact.predicate != before.predicate, fact.value != before.value,
                                       fact.unit != before.unit, fact.qualifiers != before.qualifiers,
                                       fact.excluded != before.excluded))
                fact.origin = before.origin
                fact.evidence = before.evidence
                fact.required_for_article = before.required_for_article or before.origin in {"source", "user"}
                if content_changed:
                    fact.version = before.version + 1
                    fact.marked = True
                    fact.mark_reasons.add("user_deleted" if fact.excluded else "user_edited")
                    fact.verification = "pending"
                    fact.decision_id = None
                    changed = True
        if set(original) - set(selected):
            raise ValueError("请用删除操作排除事实，不能无记录地从清单移除")
        new_version = state.get("fact_version", 0) + int(changed)
        return {"facts": [item.model_dump(mode="json") for item in selected.values()],
                "fact_version": new_version, "facts_changed": changed or state.get("facts_changed", False),
                "user_decisions": records, "facts_confirmed": True, "stage": "planning_outline"}

    async def outline_approval(self, state: WorkflowState) -> dict[str, Any]:
        request = {"kind": "outline", "fact_version": state.get("fact_version"),
                   "outline_version": state.get("outline_version"), "outline": state.get("outline", [])}
        decision = interrupt(request)
        if decision.get("action") != "confirm":
            raise ValueError("文章大纲需要确认后才能开始生成")
        if decision.get("fact_version") != state.get("fact_version") or decision.get("outline_version") != state.get("outline_version"):
            raise ValueError("确认的大纲或事实版本已失效，请重新规划")
        outline = [OutlineSection.model_validate(item) for item in decision.get("outline", state.get("outline", []))]
        if not outline:
            raise ValueError("大纲不能为空")
        allowed = {str(item["fact_id"]) for item in state.get("facts", []) if not item.get("excluded")}
        for section in outline:
            for paragraph in section.paragraphs:
                if not paragraph.fact_ids or not {str(item) for item in paragraph.fact_ids} <= allowed:
                    raise ValueError("大纲段落只能引用当前有效事实")
                paragraph.required_fact_ids = list(paragraph.fact_ids)
        assigned = {str(item) for section in outline for paragraph in section.paragraphs
                    for item in paragraph.required_fact_ids}
        mandatory = {str(item["fact_id"]) for item in state.get("facts", [])
                     if item.get("required_for_article") and not item.get("excluded")}
        if mandatory - assigned:
            raise ValueError("修改后的大纲遗漏必写事实")
        serialized = [item.model_dump(mode="json") for item in outline]
        changed = serialized != state.get("outline", [])
        return {"outline": serialized, "outline_version": state.get("outline_version", 0) + int(changed),
                "outline_changed": changed, "outline_confirmed": True, "stage": "generating"}

    async def validate_user_facts(self, state: WorkflowState) -> dict[str, Any]:
        await self._progress(state, "planning", "validate_user_facts", "正在核验用户修改的事实")
        identity = Identity.model_validate(state["identity"])
        facts = [ArticleFact.model_validate(item) for item in state.get("facts", [])]
        conflicts: list[dict[str, Any]] = []
        warnings = list(state.get("warnings", []))
        for fact in facts:
            requires_check = bool(
                fact.marked
                and not fact.excluded
                and fact.decision_id is None
                and fact.mark_reasons.intersection({"user_created", "user_edited"})
            )
            if not requires_check:
                continue
            checks = await asyncio.gather(
                self.company.ask(fact.claim, identity=identity),
                self.search.web(fact.claim),
                return_exceptions=True,
            )
            evidence = [*fact.evidence, *self._source_evidence(state.get("source_text", ""), fact.claim)]
            for source_name, result in zip(("database", "Tavily"), checks):
                if isinstance(result, Exception):
                    warnings.append(f"用户事实 {fact.fact_id} 的{source_name}核验失败：{type(result).__name__}")
                else:
                    evidence.extend(result)
            unique = {str(item.evidence_id): item for item in evidence}
            evidence = list(unique.values())
            trusted_candidates = [item for item in evidence
                                  if item.source_kind in {"database", "web", "source_article"}]
            if not trusted_candidates:
                fact.evidence = evidence
                fact.verification = "unverified"
                warnings.extend(str(item.metadata["warning"]) for item in evidence
                                if item.source_kind == "skill_md" and item.metadata.get("warning"))
                continue
            comparison = await self.model.structured(
                system=(
                    "比较用户事实与原文、数据库及网络证据，按主体、时间、地域、数值和适用范围判断。"
                    "输出 support、conflict 或 unclear。普通网络结果不能自动推翻用户事实；官网或法规原文权威性更高。"
                    "Markdown 兜底仅为未核实口径，不能说成数据库记录。无法判断时交用户裁决。"
                ),
                prompt=json.dumps(
                    {
                        "user_fact": fact.model_dump(mode="json"),
                        "evidence": [item.model_dump(mode="json") for item in trusted_candidates],
                    },
                    ensure_ascii=False,
                ),
                schema={
                    "type": "object",
                    "properties": {
                        "relation": {"enum": ["support", "conflict", "unclear"]},
                        "reason": {"type": "string"},
                    },
                    "required": ["relation", "reason"],
                },
            )
            fact.evidence = evidence
            if comparison.get("relation") in {"conflict", "unclear"}:
                fact.verification = "conflicted" if comparison.get("relation") == "conflict" else "pending"
                fact.mark_reasons.add("conflict")
                conflicts.append(
                    {
                        "fact_id": str(fact.fact_id),
                        "fact_version": fact.version,
                        "claim": fact.claim,
                        "reason": comparison.get("reason", "事实来源需要用户裁决"),
                        "evidence": [item.model_dump(mode="json") for item in evidence],
                    }
                )
            else:
                fact.verification = ("verified" if any(item.source_kind in {"database", "source_article"}
                                                       for item in trusted_candidates)
                                     else "unverified")
        return {
            "facts": [item.model_dump(mode="json") for item in facts],
            "pending_conflicts": conflicts,
            "warnings": list(dict.fromkeys(warnings)),
            "stage": "critic" if conflicts else "planning_outline",
            "facts_changed": True,
        }

    async def plan_conflict_decision(self, state: WorkflowState) -> dict[str, Any]:
        decision = interrupt({"kind": "plan_conflicts", "facts": state.get("pending_conflicts", []),
                              "fact_version": state.get("fact_version")})
        if decision.get("fact_version") != state.get("fact_version"):
            raise ValueError("事实版本已经变化，请重新裁决")
        selections = {str(item.get("fact_id")): item for item in decision.get("decisions", [])}
        conflicts = state.get("pending_conflicts", [])
        if any(str(item.get("fact_id")) not in selections for item in conflicts):
            raise ValueError("请先处理所有来源冲突")
        by_id = {str(item["fact_id"]): item for item in state.get("facts", [])}
        records = list(state.get("user_decisions", []))
        for conflict in conflicts:
            selection = selections[str(conflict.get("fact_id"))]
            fact = by_id.get(str(conflict.get("fact_id")))
            if fact is None:
                raise ValueError("裁决对应的事实不存在")
            action = selection.get("action")
            if action not in {"keep", "replace", "exclude"}:
                raise ValueError("请选择保留、修改或删除冲突事实")
            if action == "replace":
                fact["claim"] = str(selection.get("claim", "")).strip()
                if not fact["claim"]:
                    raise ValueError("修改事实不能为空")
                fact["version"] += 1
                fact["verification"] = "pending"
            elif action == "exclude":
                fact["excluded"] = True
                fact["version"] += 1
            else:
                fact["verification"] = "conflicted"
            fact["marked"] = True
            fact["mark_reasons"] = list(set(fact.get("mark_reasons", [])) | {"user_locked"})
            decision_id = uuid4()
            fact["decision_id"] = str(decision_id)
            records.append({"decision_id": str(decision_id), "fact_id": str(fact["fact_id"]),
                            "fact_version": fact["version"], "action": action, "actor_kind": "user",
                            "rule": "user_plan_conflict", "note": str(selection.get("note", "用户已裁决")),
                            "conflict": conflict})
        return {"facts": list(by_id.values()), "fact_version": state.get("fact_version", 0) + 1,
                "pending_conflicts": [], "facts_changed": True,
                "user_decisions": records, "stage": "planning_outline"}

    @staticmethod
    def _source_evidence(source_text: str, claim: str) -> list[Evidence]:
        paragraphs = [(match.start(), match.group(0).strip()) for match in re.finditer(r"[^\r\n]+", source_text)
                      if match.group(0).strip()]
        if not paragraphs:
            return []
        grams = {claim[index:index + 2].casefold() for index in range(max(0, len(claim) - 1))
                 if not claim[index].isspace() and not claim[index + 1].isspace()}
        if not grams:
            return []
        ranked = []
        for position, (start, paragraph) in enumerate(paragraphs, start=1):
            content_grams = {paragraph[index:index + 2].casefold() for index in range(max(0, len(paragraph) - 1))
                             if not paragraph[index].isspace() and not paragraph[index + 1].isspace()}
            overlap = len(grams & content_grams)
            if overlap:
                ranked.append((overlap, position, start, paragraph))
        ranked.sort(reverse=True)
        return [Evidence(source_kind="source_article", title=f"原文第 {position} 段", quote=paragraph,
                         authority=0.7, metadata={"paragraph": position, "char_start": start,
                                                 "char_end": start + len(paragraph)})
                for _, position, start, paragraph in ranked[:3]]

    async def approval(self, state: WorkflowState) -> dict[str, Any]:
        facts = [ArticleFact.model_validate(f) for f in state.get("facts", []) if not f.get("excluded")]
        outline = [OutlineSection.model_validate(s) for s in state.get("outline", [])]
        allowed = {f.fact_id for f in facts}
        if not facts or not outline or not state.get("fact_version") or not state.get("outline_version"):
            raise ValueError("必须先有有效事实清单和逐段大纲")
        if not state.get("facts_confirmed") or not state.get("outline_confirmed"):
            raise ValueError("事实清单和大纲必须经过用户确认")
        if any(not section.fact_ids or not set(section.fact_ids) <= allowed for section in outline):
            raise ValueError("自动通过失败：大纲每段必须引用当前有效事实")
        if not state.get("outline_confirmed"):
            raise ValueError("必须先人工确认大纲")
        await self._progress(state, "generating", "plan_ready", "事实与大纲已确认，正在生成文章")
        return {
            "approval": {"action": "approve", "actor_kind": "user", "rule": "confirmed_plan",
                         "fact_version": state["fact_version"], "outline_version": state["outline_version"]},
            "baseline_facts": state["facts"],
            "stage": "generating" if not state.get("draft") else "revising",
        }

    async def generator(self, state: WorkflowState) -> dict[str, Any]:
        await self._progress(state, "generating", "generator", "正在按已确认大纲逐段生成正文")
        paragraphs: list[dict[str, Any]] = []
        facts = {str(item["fact_id"]): item for item in state.get("facts", []) if not item.get("excluded")}
        system = ("只写当前大纲段落。只使用该段引用事实及证据，保留主体、数值、条件和范围；"
                  "不加入旁支信息，只返回段落正文。\n" + state.get("fact_policy", "") + "\n" + state.get("article_skill", ""))
        for section in state["outline"]:
            for paragraph in section.get("paragraphs", []):
                await self._progress(state, "generating", "paragraph_generation",
                                     f"正在生成第 {section['order']} 节第 {paragraph['order']} 段")
                selected = [facts[item] for item in paragraph["fact_ids"] if item in facts]
                preceding_text = "\n\n".join(item["content"] for item in paragraphs[-3:])[-2400:]
                preceding = ""
                if preceding_text:
                    try:
                        preceding = await (self.fact_judge or self.model).text(
                            system="用不超过两句话概括前文已写内容，只保留承接当前段落所需的信息，不补充新事实。",
                            prompt=preceding_text,
                        )
                    except Exception as exc:
                        preceding = preceding_text[-800:]
                        await emit("fallback.used", status="degraded", reason="generator_summary_failed",
                                   strategy="bounded_previous_context", **error_fields(exc))
                content = await self.model.text(
                    system=system,
                    prompt=json.dumps({
                        "article_type": state.get("article_type_name", "沿用原文类型"),
                        "thesis": state.get("article_thesis", ""), "key_points": state.get("key_points", []),
                        "full_outline": state["outline"], "section": section, "paragraph": paragraph,
                        "facts_and_evidence": selected, "previous_text_summary": preceding,
                        "platform": state.get("platform"),
                    }, ensure_ascii=False),
                )
                paragraphs.append({"section_id": section["section_id"], "paragraph_id": paragraph["paragraph_id"],
                                   "section_order": section["order"], "paragraph_order": paragraph["order"],
                                   "fact_ids": paragraph["fact_ids"], "content": content.strip()})
        draft = "\n\n".join(item["content"] for item in paragraphs)
        return {"draft": draft, "draft_paragraphs": paragraphs, "stage": "critic", "draft_changed": True}

    async def critic(self, state: WorkflowState) -> dict[str, Any]:
        await self._progress(state, "critic", "critic", "正在按事实清单逐条核对新文章")
        identity = Identity.model_validate(state["identity"])
        judge = self.fact_judge or self.model
        facts = {str(item["fact_id"]): item for item in state.get("facts", [])
                 if not item.get("excluded")}
        draft_paragraphs = {str(item["paragraph_id"]): item
                            for item in state.get("draft_paragraphs", [])}
        targets: dict[str, list[dict[str, Any]]] = {}
        for section in state.get("outline", []):
            for paragraph in section.get("paragraphs", []):
                for fact_id in dict.fromkeys([*paragraph.get("fact_ids", []),
                                              *paragraph.get("required_fact_ids", [])]):
                    if str(fact_id) in facts:
                        targets.setdefault(str(fact_id), []).append({
                            "section_id": str(section["section_id"]),
                            "paragraph_id": str(paragraph["paragraph_id"]),
                            "content": draft_paragraphs.get(str(paragraph["paragraph_id"]), {}).get("content", ""),
                        })
        fact_checks: list[dict[str, Any]] = []
        fact_issues: list[CriticIssue] = []
        baseline_system = (
            "依据已确认事实逐条核对文章。只判断当前目标段落是否准确表达这条事实；"
            "accept=主体、数值、条件和范围一致，refuse=明确冲突或表达错误，"
            "missing=目标段落没有表达，pending=无法判断。"
            "quote 必须逐字取自目标段落；没有对应文字时填空串。"
            "仅当事实来源互相冲突、或修改会违背用户明确保留的事实时 requires_user=true。"
        )
        baseline_schema = {"type": "object", "properties": {
            "decision": {"enum": ["accept", "refuse", "missing", "pending"]},
            "quote": {"type": "string"}, "reason": {"type": "string"},
            "requires_user": {"type": "boolean"}},
            "required": ["decision", "quote", "reason", "requires_user"],
            "additionalProperties": False}
        try:
            for fact_id, destinations in targets.items():
                fact = facts[fact_id]
                for target in destinations:
                    assessment = await judge.structured(
                        system=baseline_system,
                        prompt=json.dumps({"fact": fact, "target_paragraph": target,
                                           "thesis": state.get("article_thesis", "")}, ensure_ascii=False),
                        schema=baseline_schema,
                    )
                    if assessment["decision"] == "missing":
                        assessment = await judge.structured(
                            system=baseline_system + "这是遗漏复核；再次检查目标段落的上下文，仍未表达时返回 missing。",
                            prompt=json.dumps({"fact": fact, "target_paragraph": target,
                                               "section_paragraphs": [item for item in state.get("draft_paragraphs", [])
                                                                      if str(item["section_id"]) == target["section_id"]],
                                               "recheck": True}, ensure_ascii=False),
                            schema=baseline_schema,
                        )
                    quote = str(assessment["quote"]).strip()
                    decision = assessment["decision"]
                    if decision in {"accept", "refuse"} and (not quote or quote not in target["content"]):
                        raise ValueError("事实逐条审查的正文引文不在目标段落中")
                    check = {"check_type": "baseline", "fact_id": fact_id,
                             "fact_version": fact["version"], "section_id": target["section_id"],
                             "paragraph_id": target["paragraph_id"], "decision": decision,
                             "claim": quote, "expected_fact": fact["claim"],
                             "reason": assessment["reason"], "requires_user": assessment["requires_user"],
                             "evidence": fact.get("evidence", [])}
                    fact_checks.append(check)
                    if decision != "accept":
                        message = {"missing": "大纲指定段落遗漏了已确认事实",
                                   "refuse": "正文表述与已确认事实不符",
                                   "pending": "无法确认正文是否准确表达已确认事实"}[decision]
                        fact_issues.append(CriticIssue(
                            kind="fact", rule_code=f"baseline_{decision}",
                            message=f"{message}：{assessment['reason']}",
                            section_id=UUID(target["section_id"]),
                            paragraph_id=UUID(target["paragraph_id"]), fact_id=UUID(fact_id),
                            claim=quote, expected_fact=fact["claim"],
                            evidence=[Evidence.model_validate(item) for item in fact.get("evidence", [])],
                            requires_user=bool(assessment["requires_user"]),
                        ))
            extracted = await self.model.structured(
                system="从当前文章逐段摘录所有可核查的事实声明，claim 必须逐字存在于 paragraph_id 对应段落，包含新增细节、数字、范围和条件。忽略纯观点与修辞。",
                prompt=json.dumps({"paragraphs": state.get("draft_paragraphs", []), "draft": state.get("draft", "")}, ensure_ascii=False),
                schema={"type": "object", "properties": {"claims": {"type": "array", "items": {
                    "type": "object", "properties": {"claim": {"type": "string"}, "paragraph_id": {"type": "string"}},
                    "required": ["claim", "paragraph_id"]}}}, "required": ["claims"]})
            for claim in extracted.get("claims", []):
                paragraph_id = str(claim["paragraph_id"])
                if paragraph_id not in draft_paragraphs or str(claim["claim"]) not in draft_paragraphs[paragraph_id]["content"]:
                    raise ValueError("正文事实抽取结果无法定位到原段落")
                matched = await judge.structured(
                    system="逐条判断正文声明是否被已确认事实清单及证据支持。accept=含义、范围和条件一致；refuse=明确不符；pending=无法确定。无匹配本身不等于虚假。若与原文/权威证据互相冲突，或来源无法判断，requires_user=true；单纯无依据新增事实可由 Revise 删除，不需用户裁决。",
                    prompt=json.dumps({"draft_claim": claim["claim"], "facts": list(facts.values())}, ensure_ascii=False),
                    schema={"type": "object", "properties": {"decision": {"enum": ["accept", "refuse", "pending"]},
                                                                   "fact_id": {"type": ["string", "null"]},
                                                                   "reason": {"type": "string"},
                                                                   "expected_fact": {"type": "string"},
                                                                   "requires_user": {"type": "boolean"}},
                            "required": ["decision", "reason", "requires_user"], "additionalProperties": False})
                row = {**claim, **matched}
                if row.get("fact_id") and str(row["fact_id"]) not in facts:
                    raise ValueError("正文声明核对返回了事实清单之外的 fact_id")
                if row.get("decision") != "accept":
                    source_results = await asyncio.gather(
                        self.company.ask(str(claim["claim"]), identity=identity),
                        self.search.web(str(claim["claim"])),
                        return_exceptions=True,
                    )
                    candidate_evidence = []
                    for source_result in source_results:
                        if not isinstance(source_result, Exception):
                            candidate_evidence.extend(item.model_dump(mode="json") for item in source_result)
                    matched_fact = facts.get(str(row.get("fact_id")))
                    row["evidence"] = [*(matched_fact or {}).get("evidence", []), *candidate_evidence]
                fact_checks.append({"check_type": "draft_claim", **row})
        except Exception as exc:
            warnings = [*state.get("warnings", []), f"事实逐项审查失败：{type(exc).__name__}: {exc}"]
            return {"critic_status": "incomplete", "warnings": warnings, "stage": "needs_attention"}
        try:
            company_evidence = await self.company.ask(
                f"核验以下文章中的公司事实：\n{state['draft']}", identity=identity
            )
        except Exception as exc:
            warnings = [
                *state.get("warnings", []),
                f"critic 数据库核验失败：{type(exc).__name__}: {exc}",
            ]
            return {"critic_status": "incomplete", "warnings": warnings, "stage": "needs_attention"}

        evidence_warnings = list(dict.fromkeys(
            str(item.metadata["warning"])
            for item in company_evidence
            if item.source_kind == "skill_md" and item.metadata.get("warning")
        ))

        try:
            result = await self.model.structured(
                system=(
                "检查全文的重复、前后矛盾、业务和法律口径、表达及平台要求。"
                "已确认事实逐条核对和正文新增声明核对已单独完成；这里检查跨段问题及其遗漏。"
                "标记偏离主旨的枝节、重复信息、证据扩大适用范围、删除必要条件、跨来源冲突以及未列入事实清单的新事实。"
                "每个问题都必须定位到现有 paragraph_id，并用 claim 逐字引用该段正文；"
                "需要改写时填写正确的 expected_fact。"
                "所有无法确认的事实来源、事实冲突或证据口径不一致都必须 requires_user=true，不因事实是否 marked 而跳过。"
                "skill_md 仅是未验证的 Markdown 口径参考，不是数据库记录，不得据此将事实标为已核实。"
                    "同时检查文章类型规范是否落实。\n" + state.get("fact_policy", "") + "\n" + state.get("article_skill", "")
                ),
                prompt=json.dumps(
                    {
                        "draft": state["draft"],
                        "thesis": state.get("article_thesis", ""),
                        "key_points": state.get("key_points", []),
                        "article_type": state.get("article_type_name", "沿用原文类型"),
                        "article_type_skill": state.get("article_skill", ""),
                        "baseline_facts": state.get("baseline_facts", []),
                        "current_facts": state.get("facts", []),
                        "outline": state.get("outline", []),
                        "database_evidence": [
                            item.model_dump(mode="json") for item in company_evidence
                        ],
                    },
                    ensure_ascii=False,
                ),
                schema={
                    "type": "object",
                    "properties": {
                        "issues": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "kind": {
                                        "enum": [
                                            "fact", "outline", "business", "legal",
                                            "style", "duplication", "platform",
                                        ]
                                    },
                                    "severity": {"enum": ["error", "warning"]},
                                    "rule_code": {"type": "string"},
                                    "message": {"type": "string"},
                                    "section_id": {"type": ["string", "null"]},
                                    "paragraph_id": {"type": ["string", "null"]},
                                    "fact_id": {"type": ["string", "null"]},
                                    "claim": {"type": "string"},
                                    "expected_fact": {"type": "string"},
                                    "requires_user": {"type": "boolean"},
                                },
                                "required": ["kind", "message"],
                            },
                        },
                        "new_facts": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "claim": {"type": "string"},
                                    "subject": {"type": "string"},
                                    "predicate": {"type": "string"},
                                    "value": {"type": "string"},
                                    "unit": {"type": "string"},
                                    "supports_point": {"type": "string"},
                                    "omission_impact": {"type": "string"},
                                    "origin": {"enum": ["company", "web", "history", "draft"]},
                                    "verification": {"enum": ["verified", "unverified", "conflicted"]},
                                    "evidence": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "source_kind": {
                                                    "enum": ["database", "skill", "skill_md", "company_document", "web"]
                                                },
                                                "title": {"type": "string"},
                                                "url": {"type": "string"},
                                                "quote": {"type": "string"},
                                                "authority": {"type": "number"},
                                            },
                                            "required": ["source_kind", "quote"],
                                        },
                                    },
                                },
                                "required": ["claim", "verification", "evidence", "supports_point", "omission_impact"],
                            },
                        },
                    },
                    "required": ["issues", "new_facts"],
                },
            )
            issues = [*fact_issues]
            for raw in result.get("issues", []):
                issue = CriticIssue.model_validate(raw)
                paragraph = draft_paragraphs.get(str(issue.paragraph_id))
                if paragraph is None:
                    raise ValueError("全文审查问题没有定位到现有段落")
                if issue.claim and issue.claim not in paragraph["content"]:
                    raise ValueError("全文审查的问题引文不在指定段落中")
                issue.section_id = UUID(str(paragraph["section_id"]))
                issues.append(issue)
            for check in fact_checks:
                if check.get("check_type") != "draft_claim":
                    continue
                if check.get("decision") in {"refuse", "pending"}:
                    paragraph = draft_paragraphs[check["paragraph_id"]]
                    issues.append(CriticIssue(kind="fact", rule_code=f"claim_{check['decision']}",
                                              message=str(check.get("reason", "正文事实需要复核")),
                                              paragraph_id=UUID(check["paragraph_id"]),
                                              section_id=UUID(str(paragraph["section_id"])),
                                              fact_id=UUID(check["fact_id"]) if check.get("fact_id") else None,
                                              claim=str(check["claim"]), expected_fact=str(check.get("expected_fact", "")),
                                              evidence=[Evidence.model_validate(item) for item in check.get("evidence", [])],
                                              requires_user=bool(check.get("requires_user"))))
            new_facts = [ArticleFact.model_validate({
                **item,
                "qualifiers": {
                    **item.get("qualifiers", {}),
                    "supports_point": item.get("supports_point", ""),
                    "omission_impact": item.get("omission_impact", ""),
                },
            }) for item in result.get("new_facts", [])]
        except Exception as exc:
            warnings = [
                *state.get("warnings", []),
                *evidence_warnings,
                f"critic 模型审查失败：{type(exc).__name__}: {exc}",
            ]
            return {"critic_status": "incomplete", "warnings": warnings, "stage": "needs_attention"}
        # Critic 只能从它实际取得的数据库记录中补入事实；模型自报的来源字段不构成证据。
        database_evidence = [item for item in company_evidence if item.source_kind == "database"]
        evidence_by_quote = {item.quote.strip(): item for item in database_evidence if item.quote.strip()}
        allowed_points = {" ".join(item.casefold().split()) for item in state.get("key_points", [])}
        verified_new: list[ArticleFact] = []
        for candidate in new_facts:
            support = " ".join(candidate.qualifiers.get("supports_point", "").casefold().split())
            impact = candidate.qualifiers.get("omission_impact", "").strip()
            matching_evidence = next((evidence_by_quote.get(item.quote.strip())
                                      for item in candidate.evidence if item.source_kind == "database"
                                      and evidence_by_quote.get(item.quote.strip())), None)
            if candidate.verification != "verified" or not matching_evidence or support not in allowed_points or not impact:
                continue
            candidate.evidence = [matching_evidence]
            candidate.origin = "company"
            candidate.verification = "verified"
            candidate.qualifiers["fact_role"] = "company_context"
            verified_new.append(candidate)
        existing_keys = {ArticleFact.model_validate(item).semantic_key() for item in state.get("facts", [])}
        merged = [*state.get("facts", [])]
        merged.extend(
            fact.model_dump(mode="json")
            for fact in verified_new
            if fact.semantic_key() not in existing_keys
        )
        next_fact_version = state.get("fact_version", 0) + (1 if len(merged) > len(state.get("facts", [])) else 0)
        if not issues:
            return {
                "facts": merged, "fact_version": next_fact_version,
                "facts_changed": state.get("facts_changed", False) or next_fact_version != state.get("fact_version", 0),
                "critic_issues": [], "critic_status": "pass", "stage": "ready",
                "fact_checks": fact_checks,
                "warnings": list(dict.fromkeys([*state.get("warnings", []), *evidence_warnings])),
            }

        accepted_versions = {(d["fact_id"], d["fact_version"]) for d in state.get("user_decisions", [])
                             if d.get("action") == "keep"}
        current_versions = {str(item["fact_id"]): item.get("version") for item in merged}
        unsettled: list[CriticIssue] = []
        for issue in issues:
            kept = bool(issue.fact_id and
                        (str(issue.fact_id), current_versions.get(str(issue.fact_id))) in accepted_versions)
            if kept and issue.rule_code in {"fact_conflict", "source_conflict"}:
                continue
            if kept and issue.rule_code.startswith(("baseline_", "claim_")):
                issue.requires_user = False
            unsettled.append(issue)
        issues = unsettled
        needs_user = [issue for issue in issues if issue.requires_user or issue.rule_code == "fact_conflict"]
        if not issues:
            return {"facts": merged, "fact_version": next_fact_version,
                    "facts_changed": state.get("facts_changed", False) or next_fact_version != state.get("fact_version", 0),
                    "critic_issues": [], "fact_checks": fact_checks,
                    "critic_status": "pass", "stage": "ready"}
        if needs_user:
            return {
                "facts": merged,
                "fact_version": next_fact_version,
                "critic_issues": [issue.model_dump(mode="json") for issue in issues],
                "pending_conflicts": [issue.model_dump(mode="json") for issue in needs_user],
                "critic_status": "needs_user",
                "fact_checks": fact_checks,
                "stage": "critic",
                "facts_changed": state.get("facts_changed", False) or next_fact_version != state.get("fact_version", 0),
                "warnings": list(dict.fromkeys([*state.get("warnings", []), *evidence_warnings])),
            }

        fingerprints = self._failure_fingerprints(state, issues)
        prior = set(state.get("issue_history", []))
        repeated = any(item in prior for item in fingerprints)
        failures = state.get("critic_failures", 0) + 1
        stopped = repeated or failures >= self.critic_max_failures
        return {
            "facts": merged,
            "fact_version": next_fact_version,
            "critic_issues": [issue.model_dump(mode="json") for issue in issues],
            "fact_checks": fact_checks,
            "issue_history": [*state.get("issue_history", []), *fingerprints],
            "critic_failures": failures,
            "critic_status": "stopped" if stopped else "fail",
            "stage": "needs_attention" if stopped else "revising",
            "stop_reason": (
                "同一实质问题在修订后重复出现" if repeated else "完成三轮修订后仍未通过"
            ) if stopped else "",
            "facts_changed": state.get("facts_changed", False) or next_fact_version != state.get("fact_version", 0),
            "warnings": list(dict.fromkeys([*state.get("warnings", []), *evidence_warnings])),
        }

    async def revise(self, state: WorkflowState) -> dict[str, Any]:
        await self._progress(state, "revising", "revise", "正在按 critic 问题修订正文")
        previous = list(state.get("draft_paragraphs", []))
        if not previous:
            raise ValueError("稿件没有结构化段落，不能执行局部修订")
        prior_by_position = {(item["section_order"], item["paragraph_order"]): item for item in previous}
        prior_by_id = {str(item["paragraph_id"]): item for item in previous}
        issues = list(state.get("critic_issues", []))
        issue_positions: dict[tuple[int, int], list[dict[str, Any]]] = {}
        for issue in issues:
            previous_paragraph = prior_by_id.get(str(issue.get("paragraph_id")))
            if previous_paragraph:
                position = (previous_paragraph["section_order"], previous_paragraph["paragraph_order"])
                issue_positions.setdefault(position, []).append(issue)
                continue
            fact_id = str(issue.get("fact_id") or "")
            previous_paragraph = next((item for item in previous if fact_id in item.get("fact_ids", [])), None)
            if previous_paragraph:
                position = (previous_paragraph["section_order"], previous_paragraph["paragraph_order"])
                issue_positions.setdefault(position, []).append(issue)
        if sum(len(items) for items in issue_positions.values()) < len(issues):
            raise ValueError("Critic 问题没有定位章节段落，停止修订并要求人工处理")
        changed_fact_ids = {str(item["fact_id"]) for item in state.get("facts", [])
                            if item.get("excluded") or set(item.get("mark_reasons", []))
                            & {"user_created", "user_edited", "user_deleted"}}
        current_facts = {str(item["fact_id"]): item for item in state.get("facts", []) if not item.get("excluded")}
        paragraphs: list[dict[str, Any]] = []
        for section in state.get("outline", []):
            for outline_paragraph in section.get("paragraphs", []):
                position = (section["order"], outline_paragraph["order"])
                old = prior_by_position.get(position)
                issues_here = issue_positions.get(position, [])
                touched = bool(issues_here or (not issues and changed_fact_ids.intersection(
                    set(old.get("fact_ids", []) if old else []) | set(outline_paragraph.get("fact_ids", [])))))
                if old and not touched:
                    paragraphs.append({**old, "section_id": section["section_id"],
                                       "paragraph_id": outline_paragraph["paragraph_id"],
                                       "fact_ids": outline_paragraph["fact_ids"]})
                    continue
                selected = [current_facts[item] for item in outline_paragraph["fact_ids"] if item in current_facts]
                content = await self.model.text(
                    system="只重写目标段落，按正确事实及证据修正问题，不要输出其他段落。删除已排除事实的声明并修复语句衔接。\n" + state.get("fact_policy", ""),
                    prompt=json.dumps({"target_paragraph": old or outline_paragraph,
                                       "issues": issues_here,
                                       "correct_facts": [item.get("expected_fact", "") for item in issues_here],
                                       "outline_paragraph": outline_paragraph,
                                       "facts_and_evidence": selected,
                                       "thesis": state.get("article_thesis", "")}, ensure_ascii=False),
                )
                paragraphs.append({"section_id": section["section_id"],
                                   "paragraph_id": outline_paragraph["paragraph_id"],
                                   "section_order": section["order"], "paragraph_order": outline_paragraph["order"],
                                   "fact_ids": outline_paragraph["fact_ids"], "content": content.strip()})
        paragraphs.sort(key=lambda item: (item["section_order"], item["paragraph_order"]))
        return {"draft_paragraphs": paragraphs,
                "draft": "\n\n".join(item["content"] for item in paragraphs),
                "stage": "critic", "draft_changed": True}

    async def conflict_decision(self, state: WorkflowState) -> dict[str, Any]:
        decision = interrupt({"kind": "critic_conflicts", "issues": state.get("pending_conflicts", []),
                              "fact_version": state.get("fact_version")})
        if decision.get("fact_version") != state.get("fact_version"):
            raise ValueError("事实版本已经变化，请重新审查")
        decisions = {str(item.get("issue_id")): item for item in decision.get("decisions", [])}
        if any(str(item.get("issue_id")) not in decisions for item in state.get("pending_conflicts", [])):
            raise ValueError("请先处理所有待裁决问题")
        remaining = [item for item in state.get("critic_issues", [])
                     if str(item.get("issue_id")) not in decisions]
        for issue in state.get("pending_conflicts", []):
            choice = decisions[str(issue["issue_id"])]
            if choice.get("action") in {"replace", "exclude"}:
                fact = next((item for item in state.get("facts", [])
                             if str(item.get("fact_id")) == str(issue.get("fact_id"))), None)
                remaining.append({**issue, "requires_user": False,
                                  "rule_code": "apply_user_fact_decision",
                                  "message": "按用户裁决修改或删除对应段落中的事实声明",
                                  "expected_fact": choice.get("claim", "") if choice.get("action") == "replace" else "",
                                  "claim": issue.get("claim") or (fact or {}).get("claim", "")})
        facts = [ArticleFact.model_validate(item) for item in state.get("facts", [])]
        by_id = {str(item.fact_id): item for item in facts}
        records = list(state.get("user_decisions", []))
        for issue in state.get("pending_conflicts", []):
            choice = decisions[str(issue["issue_id"])]
            fact = by_id.get(str(issue.get("fact_id")))
            if fact is None:
                fact = ArticleFact(claim=str(issue.get("claim") or issue.get("message", "待用户确认声明")),
                                  origin="user", verification="unverified", marked=True,
                                  mark_reasons={"user_created"}, required_for_article=True)
                facts.append(fact)
                by_id[str(fact.fact_id)] = fact
            action = choice.get("action")
            if action == "replace":
                fact.claim = str(choice.get("claim", "")).strip()
                if not fact.claim:
                    raise ValueError("修改事实不能为空")
                fact.version += 1
                fact.verification = "pending"
                fact.mark_reasons.add("user_edited")
            elif action == "exclude":
                fact.excluded = True
                fact.version += 1
                fact.mark_reasons.add("user_deleted")
            elif action == "keep":
                fact.verification = "conflicted"
                fact.mark_reasons.add("user_locked")
            else:
                raise ValueError("裁决必须为 keep、replace 或 exclude")
            fact.marked = True
            decision_id = uuid4()
            fact.decision_id = decision_id
            records.append({"decision_id": str(decision_id), "fact_id": str(fact.fact_id),
                            "fact_version": fact.version, "action": action, "actor_kind": "user",
                            "rule": "user_critic_decision", "note": str(choice.get("note", "用户已裁决")),
                            "conflict": issue})
        update = {"facts": [item.model_dump(mode="json") for item in facts],
                  "fact_version": state.get("fact_version", 0) + 1, "facts_changed": True,
                  "user_decisions": records, "pending_conflicts": []}
        if not remaining:
            return {**update, "critic_issues": [], "critic_status": "pass", "stage": "ready"}
        return {**update, **self._review_failure(state, [CriticIssue.model_validate(item) for item in remaining])}

    def _review_failure(self, state, issues):
        fingerprints = self._failure_fingerprints(state, issues)
        repeated = bool(set(fingerprints) & set(state.get("issue_history", [])))
        failures = state.get("critic_failures", 0) + 1
        stopped = repeated or failures >= self.critic_max_failures
        return {
            "critic_issues": [issue.model_dump(mode="json") for issue in issues],
            "issue_history": [*state.get("issue_history", []), *fingerprints],
            "critic_failures": failures, "critic_status": "stopped" if stopped else "fail",
            "stage": "needs_attention" if stopped else "revising",
            "stop_reason": ("同一实质问题在修订后重复出现" if repeated else "完成三轮修订后仍未通过") if stopped else "",
        }

    @staticmethod
    def _failure_fingerprints(state: WorkflowState, issues: list[CriticIssue]) -> list[str]:
        paragraphs = {str(item.get("paragraph_id")): item.get("content", "")
                      for item in state.get("draft_paragraphs", [])}
        return [issue.fingerprint() + ":" + hashlib.sha256(
            paragraphs.get(str(issue.paragraph_id), "").encode("utf-8")).hexdigest()
            for issue in issues]

    async def answer(self, state: WorkflowState) -> dict[str, Any]:
        await self._progress(state, "researching", "knowledge_answer", "正在查询公司数据库、历史文档与 Tavily")
        identity = Identity.model_validate(state["identity"])
        question = state.get("user_message", "")
        results = await __import__("asyncio").gather(
            self.company.ask(question, identity=identity),
            self.search.semantic(question, identity=identity),
            self.search.web(question),
            return_exceptions=True,
        )
        evidence = [item for group in results if not isinstance(group, Exception) for item in group]
        labels = ("database", "vector", "web")
        warnings = [
            f"{labels[index]} 查询失败：{type(group).__name__}: {group}"
            for index, group in enumerate(results)
            if isinstance(group, Exception)
        ]
        warnings.extend(
            str(item.metadata["warning"])
            for item in evidence
            if item.source_kind == "skill_md" and item.metadata.get("warning")
        )
        for index, group in enumerate(results):
            if isinstance(group, Exception):
                await emit("fallback.used", status="degraded", source=labels[index],
                           reason="answer_source_failed", strategy="remaining_sources", **error_fields(group))
        answer = await self.model.text(
            system="根据获授权证据回答公司知识问题，明确引用出处；资料不足时说明不足。",
            prompt=json.dumps(
                {"question": question, "evidence": [item.model_dump(mode="json") for item in evidence]},
                ensure_ascii=False,
            ),
        )
        return {"answer": answer, "warnings": warnings, "stage": "answered"}

    async def clarify(self, state: WorkflowState) -> dict[str, Any]:
        return {"answer": "请粘贴要处理的文章，或明确提出公司知识问题。", "stage": "awaiting_input"}
