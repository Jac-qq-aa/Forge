# Forge Long-form Deep Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Forge 深度模式从“一次性全文生成”升级为“RAG 事实包 → 大纲 → 章节计划 → 逐节生成 → 章节校验 → 合并润色 → 最终验证”的稳定长文本生成流程，并把操作规则沉淀到 `forge-ops` skill。

**Architecture:** 保留现有 Web/Gateway/LangGraph HITL 架构，不改 API 交互形态：用户仍先确认大纲，再生成全文。新增 `forge.deep_mode.longform` 模块承载长文本生成纯业务逻辑，`graph_hil.py` 只负责调用编排，避免继续膨胀。新增测试覆盖 fact pack、章节计划、逐节生成、校验和最终集成节点。

**Tech Stack:** Python 3.12、FastAPI、LangGraph、Milvus RAG、Qwen Chat/Embedding、pytest、Claude Code skill。

---

## Scope

本计划分两部分：

1. **代码层长文本生成能力**：让 deep mode 在确认大纲后分章节生成长文。
2. **Skill 层流程沉淀**：创建 `forge-ops` skill，记录 Forge 长文生成、RAG、部署和诊断流程。

本计划不包含：

- 前端 UI 大改；接口返回仍沿用现有 `draft/current_draft` 字段。
- 改造为异步任务队列/轮询；仍使用当前 HTTP 等待链路，依赖此前已提升的 600 秒 Web timeout。
- 重新设计 Milvus schema。

---

## File Structure

### Create

- `forge/deep_mode/longform.py`
  长文本生成核心模块：数据结构、事实包构建、章节计划解析、章节生成、章节校验、合并润色、最终验证。

- `tests/test_longform_generation.py`
  单元测试：不访问真实 LLM/Milvus，通过 fake LLM 验证流程和输出约束。

- `/home/hugo/.claude/skills/forge-ops/SKILL.md`
  Forge 项目 skill 入口：什么时候触发、标准服务图、长文生成规则入口。

- `/home/hugo/.claude/skills/forge-ops/LONGFORM.md`
  Forge 长文本生成运行手册：RAG、事实包、大纲、章节生成、验证标准。

- `/home/hugo/.claude/skills/forge-ops/REFERENCE.md`
  Forge 运维参考：systemd、PG、Milvus、深度模式常见故障。

### Modify

- `forge/deep_mode/graph_hil.py`
  扩展 state 字段；把 `generate_content_node` 改为调用 `generate_longform_article()`；graph 节点数量不变。

- `forge/deep_mode/__init__.py`
  导出长文本生成模块的公共函数和类型。

---

## Design Notes

### Current flow

```text
START
  -> rag_search
  -> generate_outline
  -> wait_outline_approval
  -> generate_content
  -> wait_content_approval
  -> tuning/finalize
```

### Target flow, preserving graph shape

为了少改 Gateway/Web/HITL，本次不新增 LangGraph 节点，而是在 `generate_content_node` 内部执行长文 pipeline：

```text
generate_content_node
  -> build_fact_pack
  -> build_section_plan
  -> generate_sections
  -> verify_sections
  -> merge_sections
  -> final_verify
  -> return current_draft
```

这样前端仍然只看到：

```json
{
  "stage": "tuning",
  "draft": "..."
}
```

---

### Task 1: Add longform core module tests

**Files:**
- Create: `tests/test_longform_generation.py`
- Create later: `forge/deep_mode/longform.py`

- [ ] **Step 1: Write tests for fact extraction and required entity detection**

Create `tests/test_longform_generation.py` with:

```python
"""Tests for Forge long-form generation helpers."""

import pytest

from forge.deep_mode.longform import (
    LongformSection,
    LongformPlan,
    VerificationResult,
    build_fact_pack,
    contains_required_entities,
    parse_section_plan,
    verify_section,
    verify_final_article,
)


def test_build_fact_pack_extracts_ruibo_facts():
    rag_context = """
【锐博集团相关知识】
1. [recruitment] 云南锐博人力资源服务有限公司招聘简章
锐博集团创办于2002年，深耕人力资源行业20余年，业务包括人力资源服务、RPO、灵活用工、薪酬福利管理。

2. [case] 天津消防研究所招聘劳务派遣制工作人员面试通知
锐博天津分公司承接劳务派遣制工作人员招聘项目，采用远程视频面试方式。
"""

    fact_pack = build_fact_pack(
        rag_context=rag_context,
        required_entities=["锐博集团"],
    )

    assert "锐博集团" in fact_pack
    assert "2002" in fact_pack
    assert "RPO" in fact_pack
    assert "远程视频面试" in fact_pack


def test_contains_required_entities():
    text = "锐博集团深耕人力资源服务行业，提供RPO和灵活用工服务。"

    assert contains_required_entities(text, ["锐博集团", "RPO"])
    assert not contains_required_entities(text, ["锐博集团", "不存在的实体"])
```

- [ ] **Step 2: Write tests for section plan parsing**

Append to `tests/test_longform_generation.py`:

```python

def test_parse_section_plan_from_json():
    raw = """
{
  "title": "人力资源系统怎么选",
  "target_words": 3000,
  "sections": [
    {
      "id": 1,
      "title": "人力资源系统的核心价值",
      "goal": "解释系统价值",
      "target_words": 700,
      "required_facts": ["系统化管理"],
      "required_entities": []
    },
    {
      "id": 2,
      "title": "锐博集团为什么值得关注",
      "goal": "介绍锐博服务能力",
      "target_words": 900,
      "required_facts": ["创办于2002年", "RPO", "灵活用工"],
      "required_entities": ["锐博集团"]
    }
  ]
}
"""

    plan = parse_section_plan(raw)

    assert isinstance(plan, LongformPlan)
    assert plan.title == "人力资源系统怎么选"
    assert plan.target_words == 3000
    assert len(plan.sections) == 2
    assert plan.sections[1].required_entities == ["锐博集团"]
```

- [ ] **Step 3: Write tests for section and final verification**

Append:

```python

def test_verify_section_requires_entities_and_facts():
    section = LongformSection(
        id=1,
        title="锐博集团服务能力",
        goal="介绍锐博集团",
        target_words=300,
        required_facts=["创办于2002年", "RPO"],
        required_entities=["锐博集团"],
    )
    text = "锐博集团创办于2002年，长期提供RPO、灵活用工和薪酬福利管理服务。"

    result = verify_section(section, text)

    assert result.passed is True
    assert result.missing_entities == []
    assert result.missing_facts == []


def test_verify_section_reports_missing_requirements():
    section = LongformSection(
        id=1,
        title="锐博集团服务能力",
        goal="介绍锐博集团",
        target_words=300,
        required_facts=["创办于2002年", "RPO"],
        required_entities=["锐博集团"],
    )
    text = "这是一段泛泛而谈的人力资源系统介绍。"

    result = verify_section(section, text)

    assert result.passed is False
    assert result.missing_entities == ["锐博集团"]
    assert "创办于2002年" in result.missing_facts
    assert "RPO" in result.missing_facts


def test_verify_final_article_requires_ruibo_when_requested():
    article = "标题\n\n锐博集团创办于2002年，提供RPO和灵活用工服务。"

    result = verify_final_article(
        article=article,
        required_entities=["锐博集团"],
        required_facts=["创办于2002年", "RPO"],
        min_chars=20,
    )

    assert result.passed is True
```

- [ ] **Step 4: Run tests and verify they fail because module does not exist**

Run:

```bash
pytest tests/test_longform_generation.py -v
```

Expected:

```text
ModuleNotFoundError: No module named 'forge.deep_mode.longform'
```

---

### Task 2: Implement longform data structures and pure helpers

**Files:**
- Create: `forge/deep_mode/longform.py`
- Test: `tests/test_longform_generation.py`

- [ ] **Step 1: Create `longform.py` with data classes**

Create `forge/deep_mode/longform.py`:

```python
"""Long-form article generation pipeline for deep mode.

The module keeps long-form orchestration out of graph_hil.py so the graph node
stays focused on LangGraph state transitions.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, List

from forge.tools.llm_client import LLMClient

logger = logging.getLogger(__name__)


@dataclass
class LongformSection:
    """A single section generation task."""

    id: int
    title: str
    goal: str
    target_words: int
    required_facts: list[str] = field(default_factory=list)
    required_entities: list[str] = field(default_factory=list)


@dataclass
class LongformPlan:
    """Structured long-form article plan."""

    title: str
    target_words: int
    sections: list[LongformSection]


@dataclass
class VerificationResult:
    """Verification result for a generated section or final article."""

    passed: bool
    missing_entities: list[str] = field(default_factory=list)
    missing_facts: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
```

- [ ] **Step 2: Add fact pack and entity helpers**

Append to `forge/deep_mode/longform.py`:

```python

def _clean_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def build_fact_pack(rag_context: str, required_entities: list[str] | None = None, max_chars: int = 2400) -> str:
    """Build a compact fact pack from RAG context.

    The fact pack is intentionally extractive: it preserves source wording and
    avoids inventing company facts.
    """
    required_entities = required_entities or []
    if not rag_context or not rag_context.strip():
        return ""

    lines = _clean_lines(rag_context)
    selected: list[str] = []

    for line in lines:
        if any(entity in line for entity in required_entities):
            selected.append(line)
        elif any(keyword in line for keyword in ["锐博", "RPO", "灵活用工", "薪酬", "招聘", "外包", "消防"]):
            selected.append(line)

    if not selected:
        selected = lines[:8]

    fact_pack = "\n".join(f"- {line}" for line in selected)
    return fact_pack[:max_chars]


def contains_required_entities(text: str, required_entities: list[str]) -> bool:
    """Return True when all required entities appear in text."""
    return all(entity in text for entity in required_entities)
```

- [ ] **Step 3: Add plan parsing**

Append:

```python

def _extract_json_object(raw: str) -> str:
    """Extract the first JSON object from an LLM response."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw).strip()
        raw = re.sub(r"```$", "", raw).strip()

    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in section plan response")
    return raw[start : end + 1]


def parse_section_plan(raw: str) -> LongformPlan:
    """Parse an LLM-generated JSON section plan."""
    data = json.loads(_extract_json_object(raw))
    sections = [
        LongformSection(
            id=int(item.get("id", index + 1)),
            title=str(item.get("title", "")).strip(),
            goal=str(item.get("goal", "")).strip(),
            target_words=int(item.get("target_words", 600)),
            required_facts=[str(value).strip() for value in item.get("required_facts", []) if str(value).strip()],
            required_entities=[str(value).strip() for value in item.get("required_entities", []) if str(value).strip()],
        )
        for index, item in enumerate(data.get("sections", []))
    ]

    if not sections:
        raise ValueError("Section plan contains no sections")

    return LongformPlan(
        title=str(data.get("title", "")).strip() or "未命名长文",
        target_words=int(data.get("target_words", sum(section.target_words for section in sections))),
        sections=sections,
    )
```

- [ ] **Step 4: Add verification helpers**

Append:

```python

def _missing_items(text: str, items: Iterable[str]) -> list[str]:
    return [item for item in items if item and item not in text]


def verify_section(section: LongformSection, text: str) -> VerificationResult:
    """Verify a generated section against required facts and entities."""
    missing_entities = _missing_items(text, section.required_entities)
    missing_facts = _missing_items(text, section.required_facts)
    notes: list[str] = []

    min_chars = max(120, int(section.target_words * 0.45))
    if len(text.strip()) < min_chars:
        notes.append(f"section_too_short:{len(text.strip())}<{min_chars}")

    passed = not missing_entities and not missing_facts and not notes
    return VerificationResult(
        passed=passed,
        missing_entities=missing_entities,
        missing_facts=missing_facts,
        notes=notes,
    )


def verify_final_article(
    article: str,
    required_entities: list[str],
    required_facts: list[str],
    min_chars: int,
) -> VerificationResult:
    """Verify final merged article."""
    missing_entities = _missing_items(article, required_entities)
    missing_facts = _missing_items(article, required_facts)
    notes: list[str] = []

    if len(article.strip()) < min_chars:
        notes.append(f"article_too_short:{len(article.strip())}<{min_chars}")

    passed = not missing_entities and not missing_facts and not notes
    return VerificationResult(
        passed=passed,
        missing_entities=missing_entities,
        missing_facts=missing_facts,
        notes=notes,
    )
```

- [ ] **Step 5: Run longform helper tests**

Run:

```bash
pytest tests/test_longform_generation.py -v
```

Expected:

```text
passed
```

---

### Task 3: Implement async LLM longform pipeline

**Files:**
- Modify: `forge/deep_mode/longform.py`
- Test: `tests/test_longform_generation.py`

- [ ] **Step 1: Add prompt builders**

Append to `forge/deep_mode/longform.py`:

```python

def build_section_plan_prompt(
    *,
    outline: str,
    source_article: dict[str, Any],
    user_input: str,
    fact_pack: str,
    target_words: int,
    required_entities: list[str],
) -> str:
    """Build prompt for structured section planning."""
    return f"""请把文章大纲拆成可逐节生成的长文写作计划。

## 原文章
标题：{source_article.get('title', '')}
内容摘要：{source_article.get('text', '')[:1000]}

## 用户需求
{user_input or '无'}

## 已确认大纲
{outline}

## 必须使用的事实包
{fact_pack or '无'}

## 硬性要求
1. 总字数约 {target_words} 字。
2. 如果事实包包含锐博集团，必须至少有一个章节专门承接锐博集团服务能力。
3. required_facts 必须使用事实包里的原文短语，不要编造。
4. required_entities 必须包含用户要求必须出现的实体：{required_entities}
5. 只输出 JSON，不要输出 Markdown。

JSON 格式：
{{
  "title": "文章标题",
  "target_words": {target_words},
  "sections": [
    {{
      "id": 1,
      "title": "章节标题",
      "goal": "本节写作目标",
      "target_words": 700,
      "required_facts": ["必须出现的事实短语"],
      "required_entities": ["必须出现的实体"]
    }}
  ]
}}
"""


def build_section_prompt(
    *,
    plan: LongformPlan,
    section: LongformSection,
    source_article: dict[str, Any],
    user_input: str,
    fact_pack: str,
    previous_summary: str,
    previous_text_tail: str,
) -> str:
    """Build prompt for one section."""
    all_sections = "\n".join(
        f"{item.id}. {item.title} - {item.goal} - 约{item.target_words}字"
        for item in plan.sections
    )

    return f"""你正在逐节生成一篇长文。请只写当前章节，不要输出其他章节。

## 全文标题
{plan.title}

## 全文结构
{all_sections}

## 原文章
标题：{source_article.get('title', '')}
内容摘要：{source_article.get('text', '')[:1200]}

## 用户需求
{user_input or '无'}

## 事实包（只能基于这些事实写锐博集团相关内容）
{fact_pack or '无'}

## 前文摘要
{previous_summary or '无'}

## 前文结尾
{previous_text_tail or '无'}

## 当前章节
标题：{section.title}
目标：{section.goal}
目标字数：{section.target_words}字左右
必须出现实体：{section.required_entities}
必须包含事实：{section.required_facts}

## 写作要求
1. 只输出当前章节正文，包含章节标题。
2. 必须自然包含 required_entities 和 required_facts。
3. 不要编造事实包之外的锐博集团数据。
4. 避免重复前文表达。
5. 除最后一节外，不要写全文总结式结尾。
"""


def build_merge_prompt(
    *,
    title: str,
    sections: list[str],
    required_entities: list[str],
    required_facts: list[str],
) -> str:
    """Build prompt for light merge and polish."""
    section_text = "\n\n".join(sections)
    return f"""请对以下分节文章做轻量合并和润色。

## 硬性要求
1. 不要删除必须实体：{required_entities}
2. 不要删除必须事实：{required_facts}
3. 只做衔接、去重、标题层级和语气统一。
4. 不要新增事实性数字或公司荣誉。
5. 输出完整文章。

## 标题
{title}

## 分节草稿
{section_text}
"""
```

- [ ] **Step 2: Add summary helper and retry repair prompt**

Append:

```python

def summarize_previous_text(text: str, max_chars: int = 600) -> str:
    """Cheap summary for previous sections; keeps prompts bounded without another LLM call."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars // 2] + " ... " + text[-max_chars // 2 :]


def build_section_repair_prompt(section: LongformSection, draft: str, verification: VerificationResult) -> str:
    """Build prompt to repair a section that missed hard requirements."""
    return f"""请重写下面这个章节，使它满足缺失要求。

## 原章节标题
{section.title}

## 原草稿
{draft}

## 缺失实体
{verification.missing_entities}

## 缺失事实
{verification.missing_facts}

## 其他问题
{verification.notes}

## 要求
1. 输出完整章节。
2. 必须补齐缺失实体和事实。
3. 不要编造新事实。
4. 字数仍控制在 {section.target_words} 字左右。
"""
```

- [ ] **Step 3: Add pipeline class**

Append:

```python

class LongformGenerator:
    """Generate long-form article section by section."""

    def __init__(self, llm: LLMClient | None = None):
        self.llm = llm or LLMClient()

    async def create_plan(
        self,
        *,
        outline: str,
        source_article: dict[str, Any],
        user_input: str,
        fact_pack: str,
        target_words: int,
        required_entities: list[str],
    ) -> LongformPlan:
        prompt = build_section_plan_prompt(
            outline=outline,
            source_article=source_article,
            user_input=user_input,
            fact_pack=fact_pack,
            target_words=target_words,
            required_entities=required_entities,
        )
        raw = await self.llm.chat_with_retry(prompt)
        plan = parse_section_plan(raw)
        logger.info("[Longform] Created section plan: %s sections", len(plan.sections))
        return plan

    async def generate_section(
        self,
        *,
        plan: LongformPlan,
        section: LongformSection,
        source_article: dict[str, Any],
        user_input: str,
        fact_pack: str,
        previous_text: str,
    ) -> str:
        prompt = build_section_prompt(
            plan=plan,
            section=section,
            source_article=source_article,
            user_input=user_input,
            fact_pack=fact_pack,
            previous_summary=summarize_previous_text(previous_text),
            previous_text_tail=previous_text[-500:],
        )
        draft = await self.llm.chat_with_retry(prompt)
        verification = verify_section(section, draft)
        if verification.passed:
            return draft.strip()

        logger.warning(
            "[Longform] Section %s failed verification: entities=%s facts=%s notes=%s",
            section.id,
            verification.missing_entities,
            verification.missing_facts,
            verification.notes,
        )
        repair_prompt = build_section_repair_prompt(section, draft, verification)
        repaired = await self.llm.chat_with_retry(repair_prompt)
        return repaired.strip()

    async def merge_sections(
        self,
        *,
        plan: LongformPlan,
        sections: list[str],
        required_entities: list[str],
        required_facts: list[str],
    ) -> str:
        prompt = build_merge_prompt(
            title=plan.title,
            sections=sections,
            required_entities=required_entities,
            required_facts=required_facts,
        )
        merged = await self.llm.chat_with_retry(prompt)
        return merged.strip()
```

- [ ] **Step 4: Add public orchestration function**

Append:

```python

def infer_target_words(user_input: str, default: int = 3000) -> int:
    """Infer desired article length from user input."""
    if not user_input:
        return default

    patterns = [
        r"(\d{3,5})\s*字",
        r"(\d{3,5})\s*中文字",
    ]
    for pattern in patterns:
        match = re.search(pattern, user_input)
        if match:
            return int(match.group(1))

    if any(word in user_input for word in ["长文", "深度", "详细", "全面"]):
        return 5000
    if any(word in user_input for word in ["短文", "简短", "精简"]):
        return 1500
    return default


def infer_required_entities(user_input: str, rag_context: str) -> list[str]:
    """Infer entities that should appear in the output."""
    entities: list[str] = []
    combined = f"{user_input}\n{rag_context}"
    if "锐博" in combined:
        entities.append("锐博")
    if "锐博集团" in combined:
        entities.append("锐博集团")
    return list(dict.fromkeys(entities))


async def generate_longform_article(
    *,
    outline: str,
    source_article: dict[str, Any],
    user_input: str,
    rag_context: str,
    llm: LLMClient | None = None,
) -> dict[str, Any]:
    """Generate a long-form article using fact pack and section-by-section drafting."""
    required_entities = infer_required_entities(user_input, rag_context)
    fact_pack = build_fact_pack(rag_context, required_entities=required_entities)
    target_words = infer_target_words(user_input)

    generator = LongformGenerator(llm=llm)
    plan = await generator.create_plan(
        outline=outline,
        source_article=source_article,
        user_input=user_input,
        fact_pack=fact_pack,
        target_words=target_words,
        required_entities=required_entities,
    )

    section_drafts: list[str] = []
    previous_text = ""
    for section in plan.sections:
        draft = await generator.generate_section(
            plan=plan,
            section=section,
            source_article=source_article,
            user_input=user_input,
            fact_pack=fact_pack,
            previous_text=previous_text,
        )
        section_drafts.append(draft)
        previous_text = previous_text + "\n\n" + draft
        logger.info("[Longform] Generated section %s/%s: %s chars", section.id, len(plan.sections), len(draft))

    required_facts = list(dict.fromkeys(fact for section in plan.sections for fact in section.required_facts))
    merged = await generator.merge_sections(
        plan=plan,
        sections=section_drafts,
        required_entities=required_entities,
        required_facts=required_facts,
    )

    verification = verify_final_article(
        article=merged,
        required_entities=required_entities,
        required_facts=required_facts,
        min_chars=max(800, int(plan.target_words * 0.45)),
    )

    if not verification.passed:
        logger.warning(
            "[Longform] Final verification failed: entities=%s facts=%s notes=%s",
            verification.missing_entities,
            verification.missing_facts,
            verification.notes,
        )

    return {
        "draft": merged,
        "fact_pack": fact_pack,
        "section_plan": {
            "title": plan.title,
            "target_words": plan.target_words,
            "sections": [section.__dict__ for section in plan.sections],
        },
        "section_drafts": section_drafts,
        "verification": verification.__dict__,
    }
```

- [ ] **Step 5: Add tests for orchestration using fake LLM**

Append to `tests/test_longform_generation.py`:

```python
class FakeLongformLLM:
    def __init__(self):
        self.calls = []

    async def chat_with_retry(self, prompt: str, system_prompt: str = None, max_retries: int = 3) -> str:
        self.calls.append(prompt)
        if "只输出 JSON" in prompt:
            return """
{
  "title": "人力资源系统怎么选",
  "target_words": 1800,
  "sections": [
    {
      "id": 1,
      "title": "人力资源系统的价值",
      "goal": "解释系统价值",
      "target_words": 500,
      "required_facts": [],
      "required_entities": []
    },
    {
      "id": 2,
      "title": "锐博集团的服务能力",
      "goal": "介绍锐博集团",
      "target_words": 700,
      "required_facts": ["创办于2002年", "RPO"],
      "required_entities": ["锐博集团"]
    }
  ]
}
"""
        if "轻量合并和润色" in prompt:
            return "人力资源系统很重要。\n\n锐博集团创办于2002年，提供RPO和灵活用工服务。"
        if "锐博集团的服务能力" in prompt:
            return "## 锐博集团的服务能力\n\n锐博集团创办于2002年，提供RPO、灵活用工和薪酬福利管理服务。"
        return "## 人力资源系统的价值\n\n人力资源系统可以帮助企业提升招聘、考勤、薪酬和绩效管理效率。"


@pytest.mark.asyncio
async def test_generate_longform_article_with_fake_llm():
    from forge.deep_mode.longform import generate_longform_article

    result = await generate_longform_article(
        outline="一、人力资源系统的价值\n二、锐博集团的服务能力",
        source_article={"title": "人力资源系统怎么选", "text": "原文内容"},
        user_input="请写长文并结合锐博集团。",
        rag_context="锐博集团创办于2002年，提供RPO、灵活用工和薪酬福利管理服务。",
        llm=FakeLongformLLM(),
    )

    assert "锐博集团" in result["draft"]
    assert "创办于2002年" in result["draft"]
    assert "RPO" in result["draft"]
    assert result["verification"]["passed"] is True
    assert len(result["section_plan"]["sections"]) == 2
```

- [ ] **Step 6: Run tests**

Run:

```bash
pytest tests/test_longform_generation.py -v
```

Expected:

```text
passed
```

---

### Task 4: Integrate longform pipeline into LangGraph deep mode

**Files:**
- Modify: `forge/deep_mode/graph_hil.py`
- Modify: `forge/deep_mode/__init__.py`
- Test: `tests/test_graph_hil.py`

- [ ] **Step 1: Extend `DeepModeHILState` fields**

Modify `forge/deep_mode/graph_hil.py` inside `DeepModeHILState` after `rag_context`:

```python
    # 长文本生成中间产物
    fact_pack: Optional[str]
    section_plan: Optional[Dict[str, Any]]
    section_drafts: Optional[List[str]]
    longform_verification: Optional[Dict[str, Any]]
```

- [ ] **Step 2: Replace `generate_content_node` one-pass fallback with longform pipeline**

In `forge/deep_mode/graph_hil.py`, replace the body of `generate_content_node` from `outline = ...` through the `try/except` with this implementation:

```python
async def generate_content_node(state: DeepModeHILState) -> Dict[str, Any]:
    """节点: 生成全文。"""
    logger.info("[Node:generate_content] Generating content with longform pipeline...")

    outline = state.get("outline", "")
    source_article = state.get("source_article", {})
    user_input = state.get("user_input", "")
    rag_context = state.get("rag_context", "")

    try:
        from forge.deep_mode.longform import generate_longform_article

        result = await generate_longform_article(
            outline=outline,
            source_article=source_article,
            user_input=user_input,
            rag_context=rag_context,
        )
        draft = result["draft"]

        logger.info(
            "[Node:generate_content] Longform generated: %s chars, sections=%s, verified=%s",
            len(draft),
            len(result.get("section_drafts", [])),
            result.get("verification", {}).get("passed"),
        )

        return {
            "draft_v1": draft,
            "current_draft": draft,
            "fact_pack": result.get("fact_pack", ""),
            "section_plan": result.get("section_plan", {}),
            "section_drafts": result.get("section_drafts", []),
            "longform_verification": result.get("verification", {}),
            "stage": "waiting_content",
        }
    except Exception as e:
        logger.error(f"[Node:generate_content] Longform failed: {e}")
        return {"error": str(e)}
```

This intentionally removes the previous one-pass content generation from `graph_hil.py`. If a fallback is desired later, add it as an explicit product decision, not hidden compatibility behavior.

- [ ] **Step 3: Export longform APIs from `forge/deep_mode/__init__.py`**

Add imports near existing paragraph generation exports:

```python
from forge.deep_mode.longform import (
    LongformSection,
    LongformPlan,
    VerificationResult,
    build_fact_pack,
    generate_longform_article,
)
```

Add names to `__all__`:

```python
    "LongformSection",
    "LongformPlan",
    "VerificationResult",
    "build_fact_pack",
    "generate_longform_article",
```

- [ ] **Step 4: Update graph build tests for node count unchanged**

No expected node count changes. Run existing tests first:

```bash
pytest tests/test_graph_hil.py::TestGraphBuild -v
```

Expected:

```text
3 passed
```

- [ ] **Step 5: Add test that `generate_content_node` uses longform result**

Append to `tests/test_graph_hil.py`:

```python
@pytest.mark.asyncio
async def test_generate_content_node_uses_longform_pipeline(sample_source_article):
    from forge.deep_mode.graph_hil import generate_content_node

    async def fake_generate_longform_article(**kwargs):
        return {
            "draft": "锐博集团创办于2002年，提供RPO服务。",
            "fact_pack": "- 锐博集团创办于2002年",
            "section_plan": {"title": "测试", "sections": []},
            "section_drafts": ["锐博集团创办于2002年，提供RPO服务。"],
            "verification": {"passed": True},
        }

    with patch("forge.deep_mode.longform.generate_longform_article", side_effect=fake_generate_longform_article):
        result = await generate_content_node({
            "outline": "一、锐博集团服务能力",
            "source_article": sample_source_article,
            "user_input": "结合锐博集团",
            "rag_context": "锐博集团创办于2002年",
        })

    assert result["current_draft"].startswith("锐博集团")
    assert result["fact_pack"] == "- 锐博集团创办于2002年"
    assert result["longform_verification"]["passed"] is True
    assert result["stage"] == "waiting_content"
```

- [ ] **Step 6: Run graph tests**

Run:

```bash
pytest tests/test_graph_hil.py -v
```

Expected:

```text
passed
```

---

### Task 5: Add deployment-safe smoke test script

**Files:**
- Create: `scripts/test_longform_deep_mode.py`

- [ ] **Step 1: Create API smoke test script**

Create `scripts/test_longform_deep_mode.py`:

```python
#!/usr/bin/env python3
"""Smoke test Forge deep mode long-form generation through Web API."""

from __future__ import annotations

import argparse
import sys

import httpx


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:13326")
    parser.add_argument("--timeout", type=float, default=700.0)
    args = parser.parse_args()

    source_article = {
        "title": "人力资源系统是什么，有何作用，哪家好用？一文解读",
        "text": "人力资源系统是企业用于招聘、员工管理、薪酬绩效、考勤排班、培训发展等环节的一体化管理工具。选择人力资源服务商时，企业通常关注服务经验、全国交付能力、招聘流程外包、灵活用工、薪酬福利管理、系统化能力和成功案例。",
        "source_url": "smoke://longform-deep-mode",
    }

    with httpx.Client(timeout=args.timeout) as client:
        create_resp = client.post(
            f"{args.base_url}/api/deep_mode/create_session",
            json={
                "article_id": "smoke-longform-deep-mode",
                "source_article": source_article,
                "user_input": "请生成一篇长文，必须结合锐博集团的人力资源服务能力、RPO、灵活用工和项目案例。",
            },
        )
        create_resp.raise_for_status()
        create_data = create_resp.json()
        session_id = create_data["session_id"]
        outline = create_data.get("outline", "")
        print(f"session_id={session_id}")
        print(f"outline_len={len(outline)} outline_has_ruibo={'锐博' in outline}")

        accept_resp = client.post(
            f"{args.base_url}/api/deep_mode/outline_action",
            json={"session_id": session_id, "action": "accept", "modification": ""},
        )
        accept_resp.raise_for_status()
        accept_data = accept_resp.json()
        draft = accept_data.get("draft") or accept_data.get("current_draft") or ""
        print(f"draft_len={len(draft)} draft_has_ruibo={'锐博' in draft} draft_has_rpo={'RPO' in draft}")

        if "锐博" not in draft or "RPO" not in draft:
            print("ERROR: draft missing required Ruibo/RPO content", file=sys.stderr)
            return 1
        if len(draft) < 1200:
            print("ERROR: draft too short for long-form smoke test", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Make script executable**

Run:

```bash
chmod +x scripts/test_longform_deep_mode.py
```

Expected: exit code 0.

- [ ] **Step 3: Run local syntax check**

Run:

```bash
python3 -m py_compile scripts/test_longform_deep_mode.py
```

Expected: exit code 0.

---

### Task 6: Create forge-ops skill docs

**Files:**
- Create: `/home/hugo/.claude/skills/forge-ops/SKILL.md`
- Create: `/home/hugo/.claude/skills/forge-ops/LONGFORM.md`
- Create: `/home/hugo/.claude/skills/forge-ops/REFERENCE.md`

- [ ] **Step 1: Create skill directory**

Run:

```bash
mkdir -p /home/hugo/.claude/skills/forge-ops
```

Expected: directory exists.

- [ ] **Step 2: Create `SKILL.md`**

Write `/home/hugo/.claude/skills/forge-ops/SKILL.md`:

```markdown
---
name: forge-ops
description: Operate, diagnose, and guide content generation workflows for the Forge content transformation project. Use when working on Forge deployment, systemd services, PostgreSQL, Milvus/RAG, deep mode generation, Ruibo knowledge base synchronization, or long-form article generation.
---

# Forge Ops

## Quick start

When invoked, identify the task type first:

1. Deployment/systemd
2. Runtime diagnosis
3. Deep mode generation
4. Long-form article generation
5. PostgreSQL/checkpointer
6. Milvus/RAG/Ruibo knowledge base
7. Search/scraper issues

Always follow `/home/hugo/Forge/CLAUDE.md`:

- Reply in Chinese.
- Start each response with `hugo`.
- Before code/config changes, describe the plan and wait for approval.
- After code changes, list edge cases and tests.

## Service map

- Local repo: `/home/hugo/Forge`
- Remote source dir: `/mnt/Forge`
- Remote env file: `/mnt/Forge/.env.systemd`
- Web: `forge-web.service`, port `13326`
- Gateway: `forge-gateway.service`, port `13324`
- LangGraph: `forge-langgraph.service`, port `13325`
- Milvus: `127.0.0.1:19530`
- PostgreSQL: `127.0.0.1:5433`, database `forge`

## Standard health check

Run on server:

```bash
systemctl --no-pager --full status forge-langgraph forge-gateway forge-web
curl -sS -o /tmp/forge-web.out -w '%{http_code}' http://127.0.0.1:13326/
curl -sS -o /tmp/forge-gateway.out -w '%{http_code}' http://127.0.0.1:13324/health
curl -sS -o /tmp/forge-langgraph.out -w '%{http_code}' http://127.0.0.1:13325/ok
```

## Long-form generation

Use [LONGFORM.md](LONGFORM.md) when:

- deep mode output is too short,
- generated content misses Ruibo Group facts,
- user asks for long article generation,
- article must combine source article + RAG knowledge.

## Reference

Use [REFERENCE.md](REFERENCE.md) for deployment, PostgreSQL, Milvus, and common incident runbooks.
```

- [ ] **Step 3: Create `LONGFORM.md`**

Write `/home/hugo/.claude/skills/forge-ops/LONGFORM.md`:

```markdown
# Forge Long-form Generation

## Trigger

Use this workflow when:

- User asks for long article generation.
- Deep mode output is too short.
- Generated content misses Ruibo Group facts.
- Article needs platform-specific structure.
- Content must combine source article + RAG knowledge.

## Required workflow

Never generate long-form content in one pass unless the user explicitly asks for a short draft.

1. Retrieve RAG context from `ruibo_knowledge`.
2. Build an extractive fact pack.
3. Generate a detailed outline.
4. Convert outline into a section plan.
5. Generate section by section.
6. Verify each section against required entities and facts.
7. Merge sections with light editing only.
8. Verify final article.

## RAG checks

Before debugging prompt quality, verify:

```text
ruibo_knowledge num_entities > 0
kb.search("锐博集团 人力资源系统", n_results=3) returns results
LangGraph logs do not contain _encode failed
```

## Required final checks

For Ruibo-related long-form output:

- Contains `锐博` or `锐博集团`.
- Contains at least one service capability: `RPO`, `灵活用工`, `薪酬福利`, `招聘流程外包`.
- Contains at least one evidence/case detail from RAG.
- Does not invent unsupported Ruibo facts.
- Is not empty and is long enough for the requested platform.

## Failure handling

If RAG returns empty, stop and diagnose Milvus instead of hallucinating Ruibo facts.

If generated content lacks required entities, regenerate the missing section instead of rewriting the whole article.

If final merge removes required facts, restore them from the section drafts and rerun final verification.
```

- [ ] **Step 4: Create `REFERENCE.md`**

Write `/home/hugo/.claude/skills/forge-ops/REFERENCE.md`:

```markdown
# Forge Ops Reference

## Production source deployment

Application services run from `/mnt/Forge` with systemd:

```text
forge-web.service       13326
forge-gateway.service   13324
forge-langgraph.service 13325
```

Environment file:

```text
/mnt/Forge/.env.systemd
```

Important values:

```text
GATEWAY_API_URL=http://127.0.0.1:13324
LANGGRAPH_SERVER_URL=http://127.0.0.1:13325
REDIS_HOST=127.0.0.1
MILVUS_HOST=127.0.0.1
MILVUS_PORT=19530
PG_HOST=127.0.0.1
PG_PORT=5433
PG_USER=forge
PG_DATABASE=forge
LLM_TIMEOUT=180
```

Do not store secrets in this skill.

## Deep mode log markers

Check:

```text
[Node:rag_search]
[KnowledgeBase] Search
[Node:generate_outline]
[Node:generate_content]
[Longform]
_encode failed
Request timed out
Gateway API request failed
```

## Known incident lessons

- `.env` can contain Docker network names; systemd should use `.env.systemd`.
- PostgreSQL runs on `5433`, not `5432`.
- `ruibo_knowledge` must contain data on remote Milvus.
- `KnowledgeBase._encode` must work inside LangGraph ThreadPoolExecutor.
- Web deep mode timeout should be long enough for full article generation.
```

- [ ] **Step 5: Validate skill docs are concise**

Run:

```bash
wc -l /home/hugo/.claude/skills/forge-ops/SKILL.md /home/hugo/.claude/skills/forge-ops/LONGFORM.md /home/hugo/.claude/skills/forge-ops/REFERENCE.md
```

Expected:

```text
SKILL.md under 100 lines
LONGFORM.md under 100 lines
REFERENCE.md under 100 lines
```

---

### Task 7: Full local verification

**Files:**
- Runtime only

- [ ] **Step 1: Run focused tests**

Run:

```bash
pytest tests/test_longform_generation.py tests/test_graph_hil.py -v
```

Expected:

```text
passed
```

- [ ] **Step 2: Compile changed Python files**

Run:

```bash
python3 -m py_compile \
  forge/deep_mode/longform.py \
  forge/deep_mode/graph_hil.py \
  forge/deep_mode/__init__.py \
  scripts/test_longform_deep_mode.py
```

Expected: exit code 0.

- [ ] **Step 3: Check local diff**

Run:

```bash
git diff -- forge/deep_mode/longform.py forge/deep_mode/graph_hil.py forge/deep_mode/__init__.py tests/test_longform_generation.py scripts/test_longform_deep_mode.py
```

Expected: diff only contains planned changes.

- [ ] **Step 4: Commit local repo changes**

Run:

```bash
git add forge/deep_mode/longform.py forge/deep_mode/graph_hil.py forge/deep_mode/__init__.py tests/test_longform_generation.py scripts/test_longform_deep_mode.py
git commit -m "feat: add long-form deep mode generation pipeline"
```

Expected: commit succeeds.

Note: Do not commit `/home/hugo/.claude/skills/forge-ops/*` unless the user explicitly wants personal skills tracked elsewhere; it is outside the repo.

---

### Task 8: Remote deployment and smoke test

**Files:**
- Copy local files to remote `/mnt/Forge`
- Runtime only

- [ ] **Step 1: Upload changed files to server**

Upload:

```text
/home/hugo/Forge/forge/deep_mode/longform.py -> /mnt/Forge/forge/deep_mode/longform.py
/home/hugo/Forge/forge/deep_mode/graph_hil.py -> /mnt/Forge/forge/deep_mode/graph_hil.py
/home/hugo/Forge/forge/deep_mode/__init__.py -> /mnt/Forge/forge/deep_mode/__init__.py
/home/hugo/Forge/scripts/test_longform_deep_mode.py -> /mnt/Forge/scripts/test_longform_deep_mode.py
```

- [ ] **Step 2: Compile on server**

Run on server:

```bash
cd /mnt/Forge
.venv/bin/python -m py_compile \
  forge/deep_mode/longform.py \
  forge/deep_mode/graph_hil.py \
  forge/deep_mode/__init__.py \
  scripts/test_longform_deep_mode.py
```

Expected: exit code 0.

- [ ] **Step 3: Restart services**

Run on server:

```bash
systemctl restart forge-langgraph forge-gateway forge-web
sleep 10
systemctl --no-pager --full status forge-langgraph forge-gateway forge-web | grep -E 'Loaded:|Active:|Main PID:'
```

Expected:

```text
forge-langgraph active running
forge-gateway active running
forge-web active running
```

- [ ] **Step 4: Health check**

Run on server:

```bash
printf 'web=' && curl -sS -o /tmp/forge-web.out -w '%{http_code}' http://127.0.0.1:13326/
printf '\ngateway=' && curl -sS -o /tmp/forge-gateway.out -w '%{http_code}' http://127.0.0.1:13324/health
printf '\nlanggraph=' && curl -sS -o /tmp/forge-langgraph.out -w '%{http_code}' http://127.0.0.1:13325/ok
printf '\n'
```

Expected:

```text
web=200
gateway=200
langgraph=200
```

- [ ] **Step 5: Run longform smoke test**

Run on server:

```bash
cd /mnt/Forge
.venv/bin/python scripts/test_longform_deep_mode.py --base-url http://127.0.0.1:13326
```

Expected:

```text
outline_has_ruibo=True
draft_has_ruibo=True
draft_has_rpo=True
```

- [ ] **Step 6: Check logs for longform and RAG markers**

Run on server:

```bash
journalctl -u forge-langgraph --since '15 minutes ago' --no-pager | grep -E 'Longform|Node:rag_search|KnowledgeBase|_encode failed|Generated:' | tail -n 160
```

Expected:

```text
[Node:rag_search] Found: >0 chars
[Longform] Created section plan
[Longform] Generated section ...
[Node:generate_content] Longform generated: ... verified=True
```

Not expected:

```text
_encode failed
Embedding failed
Traceback
```

---

## Edge Cases to Review After Implementation

1. **RAG empty**
   Expected behavior: fact pack is empty; if user explicitly requires Ruibo content, generation should fail verification or produce warning logs instead of hallucinating unsupported details.

2. **LLM returns invalid JSON for section plan**
   Expected behavior: `parse_section_plan` raises `ValueError`; `generate_content_node` returns `error` and logs failure. Future enhancement can add one repair retry.

3. **Section misses required facts after repair**
   Expected behavior: final verification logs warning; response still returns draft for user review. Future enhancement can block output when hard requirements fail.

4. **Very long target length**
   Expected behavior: section count and total calls increase; current HTTP 600s may be insufficient for 8000+字. Future enhancement should move to async task/streaming progress.

5. **Final merge deletes required facts**
   Expected behavior: `verify_final_article` logs missing facts. Future enhancement can restore original section drafts or rerun merge with stricter prompt.

6. **Prompt facts are unsupported**
   Expected behavior: facts come from `fact_pack`, not user-provided arbitrary claims. User claims should be treated as requirements, not verified company facts.

---

## Self-Review

### Spec coverage

- RAG fact pack: Task 2 and Task 3.
- Detailed outline to section plan: Task 3.
- Section-by-section generation: Task 3.
- Section verification: Task 2 and Task 3.
- Merge and polish: Task 3.
- Final verification: Task 2 and Task 3.
- LangGraph integration: Task 4.
- Skill documentation: Task 6.
- Local and remote tests: Task 7 and Task 8.

### Placeholder scan

No `TBD`, `TODO`, or unspecified implementation steps remain. All code steps include concrete content.

### Type consistency

The plan consistently uses:

- `LongformSection`
- `LongformPlan`
- `VerificationResult`
- `generate_longform_article`
- `section_plan`
- `section_drafts`
- `longform_verification`

These names are defined before use and exported in Task 4.
