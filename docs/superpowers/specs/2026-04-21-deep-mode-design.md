# 深度生成模式设计文档

## 概述

在现有 Forge 内容改写系统中添加"深度生成模式"，作为可选增强功能。通过多智能体协作系统，实现用户意图解析、大纲确认、多轮微调的高质量内容生成。

---

## 需求范围

| 功能 | 是否实现 |
|------|---------|
| 画像表单输入 | ✅ 第一期 |
| Plan-Execute Agent（大纲确认） | ✅ 第一期 |
| ReAct Agent（微调对话） | ✅ 第一期 |
| RAG 知识库搜索 | ✅ 第一期 |
| Wikipedia 事实核查 | ✅ 第一期 |
| 联网搜索（Tavily/Bing） | ❌ 后续迭代 |
| 多版本回溯 | ✅ 第一期（保留 5 个版本） |

---

## 技术选型

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 与现有流程关系 | 可选增强模式 | 保留快速改写，深度模式满足高质量需求 |
| Agent 框架 | LangChain Agent + Tools | 对话式交互更灵活，与 LangGraph 工作流解耦 |
| Agent 类型 | Plan-Execute + ReAct 结合 | 大纲确认需确定性，微调需灵活性 |
| 异步机制 | Redis + Celery 消息队列 | 主流程不阻塞，定稿后回调通知 |
| 会话存储 | SQLite | 轻量、支持并发读、无需额外部署 |
| 前端交互 | 混合式：画像表单 + 对话微调 | 表单减少歧义，对话灵活响应 |

---

## 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                      用户界面 (Web Frontend)                      │
│                                                                  │
│  [选择文章] → [画像表单] → [大纲确认] → [微调对话] → [定稿]      │
│                                                                  │
│       │ WebSocket          │ WebSocket        │ POST finalize   │
│       ▼                    ▼                  ▼                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                  Deep Mode Agent Service                         │
│                                                                  │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  Session Manager + SQLite Storage                         │  │
│  │  - session_id → stage, profile, outline, draft, history   │  │
│  │  - 共享黑板：注入状态到 Agent System Prompt                │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌─────────────────┐              ┌─────────────────────────┐   │
│  │ Plan-Execute    │              │ ReAct Agent             │   │
│  │ Agent           │              │ (微调阶段)               │   │
│  │ (大纲确认阶段)  │              │                         │   │
│  │                 │              │                         │   │
│  │ Tools:          │              │ Tools:                  │   │
│  │ - profile_      │              │ - section_rewriter      │   │
│  │   extractor     │              │ - rag_search            │   │
│  │ - rag_search    │              │ - wikipedia_check       │   │
│  │ - outline_      │              │ - tone_adjuster         │   │
│  │   generator     │              │                         │   │
│  │ - content_      │              │                         │   │
│  │   generator     │              │                         │   │
│  └─────────────────┘              └─────────────────────────┘   │
│                                                                  │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │              RAG 知识库 + Wikipedia API                    │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │              Celery Worker (消息队列)                       │  │
│  │  - 接收 finalize 结果                                      │  │
│  │  - 推送到 Redis Queue: "deep_mode_completed"               │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼ Redis Queue
┌─────────────────────────────────────────────────────────────────┐
│                      Redis                                       │
│  Queue: deep_mode_completed                                     │
│  Payload: {session_id, draft, article_id}                       │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼ 主流程监听
┌─────────────────────────────────────────────────────────────────┐
│                  主工作流 (LangGraph)                            │
│                                                                  │
│  scout → deep_mode_entry → [挂起，监听 Redis]                   │
│                                                                  │
│  收到队列消息后：                                                 │
│  → ai_detector → humanizer → reviewer → director → publisher   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 异步机制详解

### 主工作流不阻塞

主流程在 `deep_mode_entry` 节点只做"创建会话 + 返回 session_id"，然后立即释放进程。

用户通过前端 WebSocket 与 Agent Service 独立交互，完成画像填写、大纲确认、微调对话。

定稿后：
1. 前端调用 `/api/deep_mode/finalize`
2. Celery 任务将结果推送到 Redis Queue
3. 主流程监听队列，收到消息后继续执行 `ai_detector` 等后续节点

---

## Session State 定义

```python
# forge/deep_mode/session_state.py

from typing import TypedDict, Literal, Optional
from datetime import datetime

class ProfileInfo(TypedDict):
    """用户画像"""
    tone: str              # 语气风格：幽默、专业、轻松、犀利...
    target_audience: str   # 目标读者：职场新人、HR从业者、管理者...
    focus_point: str       # 侧重点：实用工具、理论分析、案例故事...
    length_preference: str # 篇幅偏好：简洁、中等、深度...
    special_request: str   # 用户特殊要求（自由文本）

class DeepModeSession(TypedDict):
    """深度生成会话状态"""

    # 基础信息
    session_id: str
    article_id: str              # 关联的原始文章
    created_at: datetime
    updated_at: datetime

    # 阶段状态
    stage: Literal[
        "waiting_profile",       # 等待用户填写画像表单
        "generating_outline",    # Agent 正在生成大纲
        "waiting_outline",       # 等待用户确认大纲
        "generating_content",    # Agent 正在生成全文
        "tuning",                # 微调对话阶段
        "completed",             # 已定稿
        "cancelled"              # 用户取消
    ]

    # Plan-Execute Agent 输出（单向写入）
    profile: ProfileInfo
    outline: str                 # 大纲文本
    outline_version: int         # 大纲版本号（用户可能要求多次修改）
    draft_v1: str                # 初稿（大纲确认后的第一次生成）

    # ReAct Agent 输出（增量更新）
    current_draft: str           # 微调后的最新草稿
    tuning_history: list[dict]   # 微调对话记录
    # [{"role": "user", "content": "..."}, {"role": "agent", "content": "..."}]

    # 共享数据（Agent 初始化时注入）
    source_article: dict         # 原知乎文章 {title, text, url, ...}
    rag_context: str             # RAG 知识库搜索结果

    # 最终输出
    final_draft: str             # 用户定稿的内容
    finalized_at: Optional[datetime]
```

### 状态流转

```
waiting_profile
    │ 用户提交表单
    ▼
generating_outline (Plan-Execute Agent 运行)
    │ Agent 完成
    ▼
waiting_outline
    │ 用户确认/修改
    ├→ [修改大纲] → generating_outline (重新生成)
    │
    └ [确认大纲]
    ▼
generating_content (Plan-Execute Agent 运行)
    │ Agent 完成
    ▼
tuning (ReAct Agent 运行)
    │ 用户对话微调
    │ 用户点击"定稿"
    ▼
completed → 推送到 Redis Queue
```

---

## Agent 工具设计

### Plan-Execute Agent 专属工具

#### 1. `profile_extractor`

从用户自然语言输入中提取结构化画像。

```python
@tool
def profile_extractor(user_input: str, article_context: str) -> dict:
    """从用户自然语言输入中提取结构化画像。

    Example:
        用户输入: "改成小红书种草文，语气活泼点，主要给职场新人看"
        输出: {"tone": "活泼", "target_audience": "职场新人",
               "focus_point": "实用推荐", "length_preference": "中等"}
    """
```

#### 2. `rag_search`

搜索锐博集团知识库，复用 `forge/knowledge/` 现有基础设施。

```python
@tool
def rag_search(query: str, max_docs: int = 3) -> str:
    """搜索知识库，获取相关参考资料。

    Returns:
        知识库相关内容摘要，用于注入到生成 prompt
    """
```

#### 3. `outline_generator`

根据原文章、用户画像、知识库素材生成大纲。

```python
@tool
def outline_generator(
    source_article: str,
    profile: dict,
    rag_context: str
) -> str:
    """生成结构化大纲。

    Example Output:
        一、开篇引入：职场新人的常见困境
        二、核心观点：XX方法如何解决
        三、案例支撑：锐博集团培训实践
        四、结尾升华：给读者的建议
    """
```

#### 4. `content_generator`

根据大纲生成完整文章。

```python
@tool
def content_generator(
    outline: str,
    source_article: str,
    profile: dict,
    rag_context: str
) -> str:
    """根据大纲生成完整文章。

    Note:
        必须保留原文核心观点，RAG 素材自然融入
    """
```

### ReAct Agent 专属工具

#### 5. `section_rewriter`

根据用户要求重写指定段落。

```python
@tool
def section_rewriter(
    current_draft: str,
    section_identifier: str,
    user_request: str
) -> str:
    """重写指定段落。

    Example:
        用户: "把第二段改得更通俗一点"
        输出: 更新后的全文
    """
```

#### 6. `tone_adjuster`

调整整体语气风格。

```python
@tool
def tone_adjuster(current_draft: str, target_tone: str) -> str:
    """调整整体语气风格。

    Args:
        target_tone: 目标语气（幽默、专业、犀利、温和...）
    """
```

#### 7. `wikipedia_check`

使用 Wikipedia API 核查专有名词/事实。

```python
@tool
def wikipedia_check(term: str) -> str:
    """核查专有名词/事实。

    Implementation:
        使用 wikipedia-api Python 库
        优先查中文 Wikipedia，fallback 英文
    """
```

---

## 工具权限隔离

| Agent | 专属工具 | 说明 |
|-------|---------|------|
| Plan-Execute | `profile_extractor` | 提取画像 |
| Plan-Execute | `rag_search` | 知识库搜索 |
| Plan-Execute | `outline_generator` | 生成大纲 |
| Plan-Execute | `content_generator` | 生成全文 |
| ReAct | `section_rewriter` | 局部重写 |
| ReAct | `rag_search` | 局部补充素材 |
| ReAct | `wikipedia_check` | 事实核查 |
| ReAct | `tone_adjuster` | 调整语气风格 |

---

## API 端点设计

### REST API

```python
# 1. 创建深度生成会话
@app.post("/api/deep_mode/create_session")
async def create_deep_mode_session(request: CreateSessionRequest):
    """创建会话，启动 Plan-Execute Agent。

    Request: {article_id: str, profile: ProfileInfo}
    Response: {session_id: str, status: "generating_outline"}
    """

# 2. 获取会话状态
@app.get("/api/deep_mode/session/{session_id}")
async def get_session_status(session_id: str):
    """查询会话当前状态。

    Response: {session_id, stage, outline, current_draft, tuning_history}
    """

# 3. 大纲确认/修改
@app.post("/api/deep_mode/outline_action")
async def outline_action(request: OutlineActionRequest):
    """用户确认或修改大纲。

    Request: {session_id, action: "accept"|"modify", modification}
    Response: {status: "accepted"|"regenerating", outline}
    """

# 4. 定稿
@app.post("/api/deep_mode/finalize")
async def finalize_session(request: FinalizeRequest):
    """用户定稿，推送到 Redis Queue 触发主流程。

    Request: {session_id: str}
    Response: {status: "completed", final_draft, article_id}
    """

# 5. 取消会话
@app.delete("/api/deep_mode/session/{session_id}")
async def cancel_session(session_id: str):
    """取消深度生成会话。"""
```

### WebSocket 端点

```python
@app.websocket("/ws/deep_mode/{session_id}")
async def deep_mode_websocket(websocket: WebSocket, session_id: str):
    """深度生成实时对话通道。

    消息类型:
        - tuning_message: 用户发送修改请求
        - tuning_response: Agent 返回修改结果
        - stage_update: Agent 状态变化推送
        - error: 错误消息
    """
```

---

## WebSocket 消息协议

```python
# 1. 创建会话
{
    "type": "create_session",
    "article_id": "xxx",
    "profile": {"tone": "幽默", "target_audience": "职场新人", ...}
}
# Response
{
    "type": "session_created",
    "session_id": "abc123",
    "stage": "generating_outline"
}

# 2. Agent 状态更新（推送）
{
    "type": "stage_update",
    "session_id": "abc123",
    "stage": "waiting_outline",
    "outline": "一、开篇..."
}

# 3. 大纲确认/修改
{
    "type": "confirm_outline",
    "session_id": "abc123",
    "action": "accept" | "modify",
    "modification": "把第二部分改成案例分析"
}

# 4. 微调对话
{
    "type": "tuning_message",
    "session_id": "abc123",
    "content": "把第二段改得更通俗一点"
}
# Response
{
    "type": "tuning_response",
    "session_id": "abc123",
    "content": "已修改...",
    "updated_draft": "[完整更新后的文章]"
}

# 5. 定稿
{
    "type": "finalize",
    "session_id": "abc123"
}
# Response
{
    "type": "finalized",
    "session_id": "abc123",
    "final_draft": "...",
    "status": "completed"
}
```

---

## Celery 任务定义

```python
# forge/deep_mode/tasks.py

from celery import Celery

celery_app = Celery('forge', broker='redis://localhost:6379/0')

@celery_app.task
def push_to_main_workflow(session_id: str, final_draft: str, article_id: str):
    """将定稿内容推送到主流程队列。

    1. 更新 SQLite session 状态为 completed
    2. 推送到 Redis Queue: "deep_mode_completed"
    """

@celery_app.task
def generate_outline_async(session_id: str):
    """异步生成大纲（Plan-Execute Agent）。"""

@celery_app.task
def generate_content_async(session_id: str):
    """异步生成全文（Plan-Execute Agent）。"""
```

---

## 错误处理

### 错误类型

```python
class DeepModeError(Exception):
    """深度生成模式基础异常"""
    pass

class SessionNotFoundError(DeepModeError):
    """会话不存在"""
    pass

class InvalidStageError(DeepModeError):
    """操作与当前阶段不匹配"""
    pass

class AgentTimeoutError(DeepModeError):
    """Agent 执行超时"""
    pass

class WikipediaCheckFailedError(DeepModeError):
    """Wikipedia API 调用失败"""
    pass

class RAGSearchFailedError(DeepModeError):
    """知识库搜索失败"""
    pass
```

### 边缘情况处理

| 场景 | 处理方案 |
|------|---------|
| 用户中途离开 | Session TTL 24小时，超时自动 `cancelled` |
| 大纲多次修改 | `outline_version` 上限 3 次 |
| Agent 执行超时 | 单次上限 60s，超时返回错误 |
| Wikipedia 无结果 | 返回"未找到相关条目" |
| RAG 搜索失败 | Fallback：不使用知识库素材 |
| WebSocket 断开 | 前端自动重连，恢复对话历史 |
| 定稿后反悔 | `tuning_history` 保留，可回溯 5 个版本 |
| 并发多用户 | SQLite 行锁，Session ID 用 UUID |
| 消息队列失败 | Celery 重试 3 次 |

### 配置项

```python
# forge/config.py 新增

DEEP_MODE_SESSION_TTL = 24 * 60 * 60  # 24小时
OUTLINE_MAX_REVISIONS = 3             # 大纲最多修改 3 次
AGENT_EXECUTION_TIMEOUT = 60          # Agent 单次执行超时 60s
CELERY_TASK_RETRY = 3                 # Celery 任务重试次数
```

---

## 文件结构

```
forge/
├── deep_mode/                    # 深度生成模式（新增模块）
│   ├── __init__.py
│   ├── session_state.py          # Session State 定义
│   ├── session_manager.py        # Session Manager（SQLite 存储）
│   ├── errors.py                 # 异常定义
│   │
│   ├── agents/                   # Agent 实现
│   │   ├── __init__.py
│   │   ├── plan_execute_agent.py # Plan-Execute Agent
│   │   ├── react_agent.py        # ReAct Agent
│   │   └── agent_router.py       # Agent 路由
│   │
│   ├── tools/                    # Agent 工具
│   │   ├── __init__.py
│   │   ├── profile_extractor.py
│   │   ├── rag_search.py
│   │   ├── outline_generator.py
│   │   ├── content_generator.py
│   │   ├── section_rewriter.py
│   │   ├── tone_adjuster.py
│   │   ├── wikipedia_check.py
│   │
│   ├── tasks.py                  # Celery 异步任务
│   ├── websocket_handler.py      # WebSocket 消息处理
│   └── queue_listener.py         # Redis Queue 监听服务
│
├── web/
│   ├── app.py                    # 新增 API 端点
│   ├── templates/
│   │   ├── index.html            # 新增深度生成 UI
│   │   └── deep_mode.html        # 深度生成专用页面（可选）
│
├── config.py                     # 新增配置项
├── knowledge/                    # 现有知识库（复用）
└── sessions.db                   # SQLite 数据库（位于项目根目录，便于统一管理）
```

---

## 主流程监听机制

LangGraph 主流程通过以下方式监听 Redis Queue：

### 方案：独立监听服务

主 LangGraph 流程不直接阻塞监听，而是由独立的服务监听队列并触发后续流程：

```python
# forge/deep_mode/queue_listener.py

import redis
import asyncio
from forge.graph.workflow import workflow

async def listen_deep_mode_completed():
    """监听 Redis Queue，收到消息后触发主流程后续节点。

    Implementation:
        1. 订阅 Redis List: "deep_mode_completed"
        2. 收到消息后，从 SQLite 加载 session
        3. 构造 GraphState，调用 workflow 从 ai_detector 开始执行
    """
    r = redis.Redis(host='localhost', port=6379, db=0)

    while True:
        # BLPOP 阻塞读取（超时 60s）
        result = r.blpop('deep_mode_completed', timeout=60)

        if result:
            _, payload = result
            data = json.loads(payload)

            session_id = data['session_id']
            final_draft = data['final_draft']
            article_id = data['article_id']

            # 从 SQLite 加载完整 session
            session = session_manager.load(session_id)

            # 构造 GraphState
            state = GraphState(
                raw_content=session['source_article'],
                rewritten_draft=final_draft,
                target_platform=session.get('target_platform', 'zhihu_article'),
                revision_count=0,
                ai_score=0.0,
                humanize_revisions=0,
            )

            # 执行后续流程（从 ai_detector 开始）
            # 注意：这里需要创建子图或直接调用节点
            await run_post_deep_mode_workflow(state)

async def run_post_deep_mode_workflow(state: GraphState):
    """执行深度生成后的后续流程。

    ai_detector → humanizer_editor → reviewer → director → publisher
    """
    # 直接调用节点函数（跳过 scout 和 editor）
    from forge.agents.nodes import (
        ai_detector_node,
        humanizer_editor_node,
        reviewer_node,
        director_node,
        publisher_node,
    )

    # 依次执行
    state.update(await ai_detector_node(state))
    state.update(await humanizer_editor_node(state))  # 如果需要
    state.update(await reviewer_node(state))
    state.update(await director_node(state))
    state.update(await publisher_node(state))
```

### 启动方式

```bash
# 启动主流程（用于普通改写）
python main.py

# 启动队列监听服务（用于深度生成回调）
python -m forge.deep_mode.queue_listener
```

---

## 实现优先级

1. **Session Manager + SQLite** — 基础设施
2. **Plan-Execute Agent + 工具** — 核心生成逻辑
3. **REST API 端点** — 前端交互入口
4. **WebSocket + ReAct Agent** — 实时微调
5. **Celery + Redis** — 异步回调机制
6. **前端 UI** — 用户交互界面
7. **Wikipedia API** — 事实核查功能

---

## 测试要点

1. Session 创建、状态查询、取消流程
2. Plan-Execute Agent 工具调用链路
3. 大纲确认/修改多轮交互
4. ReAct Agent 微调对话
5. WebSocket 连接、断线重连
6. Celery 任务执行、重试、失败处理
7. Redis Queue 消息传递
8. 并发多用户场景
9. 超时、错误边界处理