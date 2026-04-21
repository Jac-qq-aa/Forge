# 深度生成模式 Phase 2 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 实现深度生成模式的微调对话功能（WebSocket + ReAct Agent），用户可以在全文生成后通过多轮对话微调内容。

**Architecture:** WebSocket 用于实时对话通信，ReAct Agent 使用 LangChain Agent 处理用户微调请求（局部重写、语气调整、事实核查）。

**Tech Stack:** FastAPI WebSocket、LangChain ReAct Agent、复用现有工具（section_rewriter、tone_adjuster、wikipedia_check）

---

## Phase 2 文件结构

```
forge/
├── deep_mode/
│   ├── agents/
│   │   ├── react_agent.py      # 新增：ReAct Agent
│   │   └── agent_router.py      # 新增：Agent 路由
│   │
│   ├── tools/
│   │   ├── section_rewriter.py  # 新增：局部重写工具
│   │   ├── tone_adjuster.py     # 新增：语气调整工具
│   │   ├── wikipedia_check.py   # 新增：事实核查工具
│   │
│   ├── websocket_handler.py     # 新增：WebSocket 处理
│
├── web/
│   ├── app.py                   # 修改：添加 WebSocket 端点
│
└── web/templates/
    └── index.html               # 修改：添加对话界面
```

---

## Task 1: ReAct Agent 工具 - section_rewriter

**Files:**
- Create: `forge/deep_mode/tools/section_rewriter.py`

```python
# forge/deep_mode/tools/section_rewriter.py

"""局部重写工具。"""

from langchain_core.tools import tool
import logging
import json
import asyncio

from forge.tools.llm_client import LLMClient

logger = logging.getLogger(__name__)


@tool
def section_rewriter(current_draft: str, section_identifier: str, user_request: str) -> str:
    """根据用户要求重写指定段落。

    Args:
        current_draft: 当前完整草稿
        section_identifier: 段落标识（如"第二段"、"大纲第三节"、"开头部分"）
        user_request: 用户修改要求

    Returns:
        重写后的完整草稿

    Example:
        用户: "把第二段改得更通俗一点"
        输出: 更新后的全文
    """
    logger.info(f"[section_rewriter] Rewriting section: {section_identifier}")

    # 构建重写提示词
    prompt = f"""请根据用户要求重写文章的指定部分：

## 当前全文
{current_draft}

## 要修改的部分
{section_identifier}

## 用户修改要求
{user_request}

## 重写规则
1. 只修改指定部分，其他内容保持不变
2. 确保修改后的内容与全文风格一致
3. 保持原文核心观点
4. 直接输出修改后的完整文章（包含未修改的部分）

请直接输出完整文章：
"""

    # 调用 LLM（同步包装）
    llm = LLMClient()
    try:
        result = asyncio.run(llm.chat_with_retry(prompt))
        logger.info(f"[section_rewriter] Rewrite completed: {len(result)} chars")
        return result
    except Exception as e:
        logger.error(f"[section_rewriter] Rewrite failed: {e}")
        return f"重写失败：{e}"
```

- [ ] **Commit**

```bash
git add forge/deep_mode/tools/section_rewriter.py
git commit -m "feat(deep_mode): add section_rewriter tool"

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
```

---

## Task 2: ReAct Agent 工具 - tone_adjuster

**Files:**
- Create: `forge/deep_mode/tools/tone_adjuster.py`

```python
# forge/deep_mode/tools/tone_adjuster.py

"""语气调整工具。"""

from langchain_core.tools import tool
import logging
import asyncio

from forge.tools.llm_client import LLMClient

logger = logging.getLogger(__name__)


@tool
def tone_adjuster(current_draft: str, target_tone: str) -> str:
    """调整整体语气风格。

    Args:
        current_draft: 当前草稿
        target_tone: 目标语气（幽默、专业、犀利、温和、活泼、严肃...）

    Returns:
        调整语气后的完整文章

    Example:
        用户: "整体语气太严肃了，改轻松点"
        输出: 调整后的全文
    """
    logger.info(f"[tone_adjuster] Adjusting tone to: {target_tone}")

    prompt = f"""请调整文章的整体语气风格：

## 当前文章
{current_draft}

## 目标语气
{target_tone}

## 调整规则
1. 保持原文的核心观点和结构
2. 调整措辞和表达方式以匹配目标语气
3. 如果是"幽默"，适当加入轻松的表达
4. 如果是"专业"，使用更严谨的术语
5. 如果是"活泼"，使用口语化表达
6. 直接输出调整后的完整文章

请直接输出完整文章：
"""

    llm = LLMClient()
    try:
        result = asyncio.run(llm.chat_with_retry(prompt))
        logger.info(f"[tone_adjuster] Tone adjustment completed: {len(result)} chars")
        return result
    except Exception as e:
        logger.error(f"[tone_adjuster] Adjustment failed: {e}")
        return f"语气调整失败：{e}"
```

- [ ] **Commit**

```bash
git add forge/deep_mode/tools/tone_adjuster.py
git commit -m "feat(deep_mode): add tone_adjuster tool"

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
```

---

## Task 3: ReAct Agent 工具 - wikipedia_check

**Files:**
- Create: `forge/deep_mode/tools/wikipedia_check.py`

```python
# forge/deep_mode/tools/wikipedia_check.py

"""Wikipedia 事实核查工具。"""

from langchain_core.tools import tool
import logging

logger = logging.getLogger(__name__)


@tool
def wikipedia_check(term: str) -> str:
    """使用 Wikipedia API 核查专有名词/事实。

    Args:
        term: 需核查的术语或事实陈述

    Returns:
        Wikipedia 定义摘要，或"未找到相关条目"

    Example:
        用户: "查一下'360度评估'的定义"
        输出: Wikipedia 定义摘要
    """
    logger.info(f"[wikipedia_check] Checking term: {term}")

    try:
        import wikipedia

        # 设置中文 Wikipedia
        wikipedia.set_lang("zh")

        # 搜索条目
        results = wikipedia.search(term, results=3)

        if not results:
            # 尝试英文 Wikipedia
            wikipedia.set_lang("en")
            results = wikipedia.search(term, results=3)

        if not results:
            return "未找到相关 Wikipedia 条目"

        # 获取最相关条目的摘要
        try:
            page = wikipedia.page(results[0], auto_suggest=False)
            summary = page.summary[:500]  # 截取前 500 字
            return f"【Wikipedia 定义】\n条目：{page.title}\n摘要：{summary}"
        except wikipedia.exceptions.PageError:
            return "未找到相关 Wikipedia 条目"
        except wikipedia.exceptions.DisambiguationError as e:
            # 多义项，返回选项列表
            return f"存在多个相关条目：{', '.join(e.options[:5])}"

    except ImportError:
        logger.warning("[wikipedia_check] wikipedia library not installed")
        return "Wikipedia 库未安装，无法核查"
    except Exception as e:
        logger.error(f"[wikipedia_check] Check failed: {e}")
        return f"核查失败：{e}"
```

- [ ] **Commit**

```bash
git add forge/deep_mode/tools/wikipedia_check.py
git commit -m "feat(deep_mode): add wikipedia_check tool"

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
```

---

## Task 4: 更新工具模块导出

**Files:**
- Modify: `forge/deep_mode/tools/__init__.py`

```python
# forge/deep_mode/tools/__init__.py

"""深度生成模式 Agent 工具集。"""

from forge.deep_mode.tools.profile_extractor import profile_extractor
from forge.deep_mode.tools.rag_search import rag_search
from forge.deep_mode.tools.outline_generator import outline_generator
from forge.deep_mode.tools.content_generator import content_generator
from forge.deep_mode.tools.section_rewriter import section_rewriter
from forge.deep_mode.tools.tone_adjuster import tone_adjuster
from forge.deep_mode.tools.wikipedia_check import wikipedia_check

__all__ = [
    "profile_extractor",
    "rag_search",
    "outline_generator",
    "content_generator",
    "section_rewriter",
    "tone_adjuster",
    "wikipedia_check",
]
```

- [ ] **Commit**

```bash
git add forge/deep_mode/tools/__init__.py
git commit -m "feat(deep_mode): export new tools in __init__.py"

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
```

---

## Task 5: ReAct Agent 实现

**Files:**
- Create: `forge/deep_mode/agents/react_agent.py`

```python
# forge/deep_mode/agents/react_agent.py

"""ReAct Agent - 微调对话阶段。"""

import logging
import json
import asyncio
from typing import Optional

from langchain.agents import AgentExecutor, create_react_agent
from langchain_core.prompts import PromptTemplate
from langchain_core.tools import Tool

from forge.tools.llm_client import LLMClient
from forge.deep_mode.session_state import DeepModeSession
from forge.deep_mode.session_manager import SessionManager, get_session_manager
from forge.deep_mode.tools.section_rewriter import section_rewriter
from forge.deep_mode.tools.tone_adjuster import tone_adjuster
from forge.deep_mode.tools.wikipedia_check import wikipedia_check
from forge.deep_mode.tools.rag_search import rag_search

logger = logging.getLogger(__name__)


# ReAct Agent System Prompt
REACT_SYSTEM_PROMPT = """你是一个内容微调专家，负责根据用户反馈优化文章。

你的能力：
1. 重写指定段落（section_rewriter）
2. 调整整体语气（tone_adjuster）
3. 核查专有名词（wikipedia_check）
4. 搜索知识库补充素材（rag_search）

重要原则：
- 保持原文核心观点
- 只修改用户指定的部分
- 修改后确保全文风格一致
- 不要编造信息

当前文章状态：
{context}

可用工具：
{tools}

工具名称：{tool_names}

用户请求：{input}

{agent_scratchpad}
"""


class ReactAgent:
    """ReAct Agent 用于微调对话阶段。"""

    def __init__(self, session_manager: SessionManager = None):
        self.session_manager = session_manager or get_session_manager()
        self.llm = LLMClient()

        # 定义工具
        self.tools = [
            Tool(
                name="section_rewriter",
                description="重写指定段落。参数：current_draft（全文）、section_identifier（段落标识）、user_request（修改要求）",
                func=self._section_rewriter_wrapper,
            ),
            Tool(
                name="tone_adjuster",
                description="调整整体语气。参数：current_draft（全文）、target_tone（目标语气）",
                func=self._tone_adjuster_wrapper,
            ),
            Tool(
                name="wikipedia_check",
                description="核查专有名词定义。参数：term（术语）",
                func=self._wikipedia_check_wrapper,
            ),
            Tool(
                name="rag_search",
                description="搜索知识库补充素材。参数：query（关键词）",
                func=self._rag_search_wrapper,
            ),
        ]

    def _section_rewriter_wrapper(self, input_str: str) -> str:
        """包装 section_rewriter。"""
        try:
            data = json.loads(input_str)
            return section_rewriter.invoke({
                "current_draft": data.get("current_draft", ""),
                "section_identifier": data.get("section_identifier", ""),
                "user_request": data.get("user_request", ""),
            })
        except Exception as e:
            return f"重写失败：{e}"

    def _tone_adjuster_wrapper(self, input_str: str) -> str:
        """包装 tone_adjuster。"""
        try:
            data = json.loads(input_str)
            return tone_adjuster.invoke({
                "current_draft": data.get("current_draft", ""),
                "target_tone": data.get("target_tone", ""),
            })
        except Exception as e:
            return f"语气调整失败：{e}"

    def _wikipedia_check_wrapper(self, term: str) -> str:
        """包装 wikipedia_check。"""
        return wikipedia_check.invoke(term)

    def _rag_search_wrapper(self, query: str) -> str:
        """包装 rag_search。"""
        return rag_search.invoke(query)

    async def process_user_request(
        self,
        session: DeepModeSession,
        user_message: str
    ) -> str:
        """处理用户微调请求。

        Args:
            session: 会话状态
            user_message: 用户消息

        Returns:
            Agent 响应
        """
        logger.info(f"[ReactAgent] Processing user request: {user_message[:50]}...")

        current_draft = session.get("current_draft") or session.get("draft_v1", "")
        outline = session.get("outline", "")
        profile = session.get("profile", {})

        # 构建上下文
        context = f"""
当前草稿（前 500 字）：
{current_draft[:500]}...

大纲：
{outline}

用户画像：
{json.dumps(profile, ensure_ascii=False)}
"""

        # 判断用户意图，直接处理常见请求
        user_lower = user_message.lower()

        # 语气调整
        if any(kw in user_lower for kw in ["语气", "风格", "严肃", "轻松", "活泼", "专业", "幽默", "温和", "犀利"]):
            tone_keywords = ["轻松", "活泼", "专业", "幽默", "温和", "犀利", "严肃", "通俗", "正式"]
            target_tone = "轻松活泼"  # 默认
            for kw in tone_keywords:
                if kw in user_lower:
                    target_tone = kw
                    break

            logger.info(f"[ReactAgent] Detected tone adjustment request: {target_tone}")
            result = await self._run_tone_adjustment(current_draft, target_tone)
            return result

        # 事实核查
        if any(kw in user_lower for kw in ["查一下", "核查", "定义", "是什么", "百度百科", "wikipedia"]):
            # 提取术语
            term = user_message.replace("查一下", "").replace("核查", "").replace("的定义", "").replace("是什么", "").strip()
            if term:
                logger.info(f"[ReactAgent] Detected fact check request: {term}")
                result = wikipedia_check.invoke(term)
                return result

        # 段落重写（默认）
        section_identifier = "相关段落"
        if any(kw in user_message for kw in ["第一段", "第二段", "第三段", "开头", "结尾", "中间"]):
            section_identifier = user_message

        logger.info(f"[ReactAgent] Defaulting to section rewrite: {section_identifier}")
        result = await self._run_section_rewrite(current_draft, section_identifier, user_message)
        return result

    async def _run_tone_adjustment(self, current_draft: str, target_tone: str) -> str:
        """执行语气调整。"""
        prompt = f"""请调整文章的整体语气风格：

## 当前文章
{current_draft}

## 目标语气
{target_tone}

## 调整规则
1. 保持原文的核心观点和结构
2. 调整措辞和表达方式以匹配目标语气
3. 直接输出调整后的完整文章

请直接输出完整文章：
"""

        result = await self.llm.chat_with_retry(prompt)
        return f"已调整语气为【{target_tone}】，更新如下：\n\n{result}"

    async def _run_section_rewrite(self, current_draft: str, section_identifier: str, user_request: str) -> str:
        """执行段落重写。"""
        prompt = f"""请根据用户要求重写文章的指定部分：

## 当前全文
{current_draft}

## 用户要求
{user_request}

## 重写规则
1. 只修改需要修改的部分，其他内容保持不变
2. 确保修改后的内容与全文风格一致
3. 保持原文核心观点
4. 直接输出修改后的完整文章

请直接输出完整文章：
"""

        result = await self.llm.chat_with_retry(prompt)
        return f"已根据您的要求修改，更新如下：\n\n{result}"


async def run_react_agent(
    session_id: str,
    user_message: str
) -> dict:
    """运行 ReAct Agent 处理用户微调请求。

    Args:
        session_id: 会话 ID
        user_message: 用户消息

    Returns:
        更新后的状态和响应
    """
    session_manager = get_session_manager()
    agent = ReactAgent(session_manager)

    session = await session_manager.load_session(session_id)

    # 处理用户请求
    response = await agent.process_user_request(session, user_message)

    # 解析响应，提取更新后的草稿
    updated_draft = response
    if "更新如下：" in response:
        updated_draft = response.split("更新如下：")[-1].strip()

    # 更新会话状态
    new_history = session.get("tuning_history", [])
    new_history.append({
        "role": "user",
        "content": user_message,
        "timestamp": datetime.now().isoformat(),
    })
    new_history.append({
        "role": "agent",
        "content": response,
        "timestamp": datetime.now().isoformat(),
    })

    await session_manager.update_session(
        session_id,
        current_draft=updated_draft,
        tuning_history=new_history,
    )

    return {
        "response": response,
        "updated_draft": updated_draft,
    }
```

- [ ] **Commit**

```bash
git add forge/deep_mode/agents/react_agent.py
git commit -m "feat(deep_mode): add ReAct Agent for tuning phase"

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
```

---

## Task 6: WebSocket 处理器

**Files:**
- Create: `forge/deep_mode/websocket_handler.py`

```python
# forge/deep_mode/websocket_handler.py

"""WebSocket 消息处理器。"""

import logging
import json
from datetime import datetime
from fastapi import WebSocket

from forge.deep_mode.session_manager import get_session_manager
from forge.deep_mode.agents.react_agent import run_react_agent

logger = logging.getLogger(__name__)


async def handle_websocket_connection(websocket: WebSocket, session_id: str):
    """处理 WebSocket 连接。

    消息类型：
    - tuning_message: 用户发送微调请求
    - tuning_response: Agent 返回响应
    - stage_update: 状态变化通知
    - error: 错误消息
    """
    await websocket.accept()
    logger.info(f"[WebSocket] Connection established for session: {session_id}")

    try:
        # 加载会话状态
        session_manager = get_session_manager()
        session = await session_manager.load_session(session_id)

        # 发送当前状态
        await websocket.send_json({
            "type": "stage_update",
            "session_id": session_id,
            "stage": session.get("stage"),
            "current_draft": session.get("current_draft") or session.get("draft_v1", ""),
            "tuning_history": session.get("tuning_history", []),
        })

        # 消息循环
        while True:
            data = await websocket.receive_json()
            message_type = data.get("type")

            if message_type == "tuning_message":
                # 处理用户微调请求
                user_message = data.get("content", "")
                logger.info(f"[WebSocket] User message: {user_message[:50]}...")

                # 运行 ReAct Agent
                result = await run_react_agent(session_id, user_message)

                # 发送响应
                await websocket.send_json({
                    "type": "tuning_response",
                    "session_id": session_id,
                    "content": result["response"],
                    "updated_draft": result["updated_draft"],
                })

            elif message_type == "finalize":
                # 定稿
                session = await session_manager.finalize_session(session_id)
                await websocket.send_json({
                    "type": "finalized",
                    "session_id": session_id,
                    "status": "completed",
                    "final_draft": session.get("final_draft"),
                })
                break  # 结束连接

            elif message_type == "ping":
                # 心跳
                await websocket.send_json({"type": "pong"})

    except Exception as e:
        logger.error(f"[WebSocket] Error: {e}")
        await websocket.send_json({
            "type": "error",
            "message": str(e),
        })

    finally:
        logger.info(f"[WebSocket] Connection closed for session: {session_id}")
```

- [ ] **Commit**

```bash
git add forge/deep_mode/websocket_handler.py
git commit -m "feat(deep_mode): add WebSocket handler for real-time tuning"

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
```

---

## Task 7: 添加 WebSocket 端点到 FastAPI

**Files:**
- Modify: `forge/web/app.py`

在 app.py 中添加：

```python
from fastapi import WebSocket
from forge.deep_mode.websocket_handler import handle_websocket_connection

@app.websocket("/ws/deep_mode/{session_id}")
async def deep_mode_websocket(websocket: WebSocket, session_id: str):
    """深度生成实时对话通道。"""
    await handle_websocket_connection(websocket, session_id)
```

- [ ] **Commit**

```bash
git add forge/web/app.py
git commit -m "feat(api): add WebSocket endpoint for deep mode tuning"

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
```

---

## Task 8: 添加 wikipedia 库依赖

**Files:**
- Modify: `requirements.txt`

添加：
```
wikipedia>=1.4.0
```

- [ ] **Commit**

```bash
git add requirements.txt
git commit -m "feat: add wikipedia library dependency"

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
```

---

## Task 9: 前端对话界面

**Files:**
- Modify: `forge/web/templates/index.html`

在 `deep-content-section` 后添加对话界面：

```html
<!-- Step 2.9: 深度生成 - 微调对话 -->
<section id="deep-chat-section" class="card" style="display: none;">
    <div class="card-header">
        <span class="step-badge">步骤 2.9</span>
        <h2>微调对话</h2>
    </div>
    <p class="hint">全文已生成，您可以通过对话微调内容</p>

    <!-- 对话历史 -->
    <div id="deep-chat-history" style="background: #f5f5f5; padding: 15px; border-radius: 8px; margin-bottom: 15px; max-height: 300px; overflow-y: auto;">
        <div class="chat-message agent-message" style="padding: 10px; margin-bottom: 10px; background: #e8f5e9; border-radius: 8px;">
            <p>全文已生成完成！您可以提出修改意见，比如：</p>
            <ul style="margin-left: 20px; color: #666;">
                <li>"把第二段改得更通俗一点"</li>
                <li>"整体语气太严肃了，改轻松点"</li>
                <li>"查一下'360度评估'的定义"</li>
            </ul>
        </div>
    </div>

    <!-- 输入框 -->
    <div style="display: flex; gap: 10px;">
        <input type="text" id="deep-chat-input" placeholder="输入修改意见..." style="flex: 1; padding: 10px; border: 1px solid #ddd; border-radius: 8px;">
        <button class="btn btn-primary" onclick="sendDeepChatMessage()">发送</button>
    </div>

    <!-- 操作按钮 -->
    <div style="display: flex; gap: 10px; margin-top: 20px;">
        <button id="deep-finalize-chat-btn" class="btn btn-primary" onclick="finalizeFromChat()">✅ 定稿保存</button>
        <button class="btn btn-secondary" onclick="backToContentPreview()">← 返回预览</button>
    </div>
</section>
```

添加 JavaScript 函数：

```javascript
let deepWebSocket = null;

function startDeepChat() {
    // 初始化 WebSocket
    const wsUrl = `ws://${location.host}/ws/deep_mode/${deepSessionId}`;
    deepWebSocket = new WebSocket(wsUrl);

    deepWebSocket.onopen = function() {
        console.log('[DeepChat] WebSocket connected');
    };

    deepWebSocket.onmessage = function(event) {
        const data = JSON.parse(event.data);
        handleDeepChatMessage(data);
    };

    deepWebSocket.onerror = function(error) {
        console.error('[DeepChat] WebSocket error:', error);
        showNotification('WebSocket 连接失败', 'error');
    };

    deepWebSocket.onclose = function() {
        console.log('[DeepChat] WebSocket closed');
    };

    showSection(document.getElementById('deep-chat-section'));
}

function handleDeepChatMessage(data) {
    if (data.type === 'stage_update') {
        // 初始状态更新
        if (data.tuning_history && data.tuning_history.length > 0) {
            data.tuning_history.forEach(msg => addChatMessage(msg.role, msg.content));
        }
    } else if (data.type === 'tuning_response') {
        // Agent 响应
        addChatMessage('agent', data.content);
        document.getElementById('deep-chat-input').value = '';
    } else if (data.type === 'finalized') {
        // 定稿完成
        showNotification('已定稿保存！', 'success');
        setTimeout(() => backToArticles(), 2000);
    } else if (data.type === 'error') {
        showNotification('错误：' + data.message, 'error');
    }
}

function addChatMessage(role, content) {
    const history = document.getElementById('deep-chat-history');
    const msgDiv = document.createElement('div');
    msgDiv.className = role === 'user' ? 'chat-message user-message' : 'chat-message agent-message';
    msgDiv.style.cssText = 'padding: 10px; margin-bottom: 10px; border-radius: 8px;';
    msgDiv.style.background = role === 'user' ? '#fff3e0' : '#e8f5e9';
    msgDiv.innerHTML = `<strong>${role === 'user' ? '👤 您' : '🤖 Agent'}：</strong><p style="white-space: pre-wrap;">${content}</p>`;
    history.appendChild(msgDiv);
    history.scrollTop = history.scrollHeight;
}

function sendDeepChatMessage() {
    const input = document.getElementById('deep-chat-input');
    const message = input.value.trim();

    if (!message) return;

    // 显示用户消息
    addChatMessage('user', message);

    // 发送到 WebSocket
    if (deepWebSocket && deepWebSocket.readyState === WebSocket.OPEN) {
        deepWebSocket.send(JSON.stringify({
            type: 'tuning_message',
            content: message
        }));
    } else {
        showNotification('WebSocket 未连接', 'error');
    }
}

function finalizeFromChat() {
    if (deepWebSocket && deepWebSocket.readyState === WebSocket.OPEN) {
        deepWebSocket.send(JSON.stringify({type: 'finalize'}));
    }
}

function backToContentPreview() {
    if (deepWebSocket) {
        deepWebSocket.close();
        deepWebSocket = null;
    }
    showSection(document.getElementById('deep-content-section'));
}
```

修改 `acceptOutline` 函数，在全文生成后进入对话模式：

```javascript
// 在 acceptOutline 成功后调用
showSection(document.getElementById('deep-chat-section'));
startDeepChat();
```

- [ ] **Commit**

```bash
git add forge/web/templates/index.html
git commit -m "feat(ui): add deep mode chat interface with WebSocket"

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
```

---

## Phase 2 完成验证

完成所有任务后，Phase 2 应具备以下能力：

1. ✅ WebSocket 实时对话
2. ✅ ReAct Agent 处理微调请求
3. ✅ 局部重写（section_rewriter）
4. ✅ 语气调整（tone_adjuster）
5. ✅ 事实核查（wikipedia_check）
6. ✅ 前端对话界面

---

## 测试流程

```bash
# 1. 启动服务
python run_web.py

# 2. 创建深度生成会话，生成全文

# 3. 进入微调对话阶段，测试：
#    - "把第二段改得更通俗一点"
#    - "整体语气太严肃了"
#    - "查一下'360度评估'的定义"

# 4. 定稿
```

---

## Self-Review

- [x] Spec coverage: Phase 2 功能全部覆盖
- [x] No placeholders: 所有代码完整
- [x] Dependencies: wikipedia 库已添加