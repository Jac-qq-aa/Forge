# Forge 项目完整说明文档

> 适用于学习、展示和项目理解的综合文档

---

## 一、项目概述

### 1.1 项目简介

Forge 是一个基于 LangGraph 的**多智能体协作内容转换系统**，专为锐博集团打造。系统支持从知乎、微信公众号等平台抓取内容，通过 AI 改写生成原创内容，并结合企业知识库自然融入品牌信息。

### 1.2 核心能力

| 能力维度 | 具体功能 |
|---------|---------|
| 内容抓取 | 知乎问答、知乎文章、微信公众号文章 |
| AI改写 | Qwen LLM智能改写，保留核心观点 |
| 知识融合 | Milvus向量库检索，自然融入品牌信息 |
| 内容审核 | 多轮迭代审核，确保内容质量 |
| 视频生成 | 数字人视频、TTS语音合成 |
| 两种模式 | 快速改写模式 + 深度生成模式 |

### 1.3 技术亮点

- **LangGraph 工作流引擎**：可视化节点流程，状态持久化
- **多智能体协作**：Scout → Editor → Reviewer → Director → Publisher
- **浏览器自动化**：Playwright + playwright-stealth 反爬虫规避
- **向量数据库**：Milvus 高性能语义检索
- **WebSocket 实时通信**：深度模式支持人机交互循环

---

## 二、系统架构

### 2.1 架构图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          Forge System Architecture                        │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐ │
│  │  Scout  │───►│ Editor  │───►│Reviewer │───►│Director │───►│Publisher│ │
│  │ (抓取)  │    │ (改写)  │    │ (审核)  │    │ (输出)  │    │ (发布)  │ │
│  └────┬────┘    └────┬────┘    └────┬────┘    └────┬────┘    └────┬────┘ │
│       │              │              │              │              │       │
│       ▼              ▼              ▼              ▼              ▼       │
│  ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐ │
│  │知乎/微信│    │Qwen LLM │    │质量检查 │    │视频合成 │    │目标平台 │ │
│  │  爬虫   │    │+ RAG    │    │迭代修订 │    │TTS生成  │    │  发布   │ │
│  └─────────┘    └─────────┘    └─────────┘    └─────────┘    └─────────┘ │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │                     Supporting Infrastructure                         │ │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐            │ │
│  │  │  Milvus  │  │PostgreSQL│  │  Redis   │  │FastAPI   │            │ │
│  │  │向量数据库│  │状态持久化│  │队列/缓存 │  │Web服务   │            │ │
│  │  └──────────┘  └──────────┘  └──────────┘  └──────────┘            │ │
│  └─────────────────────────────────────────────────────────────────────┘ │
│                                                                           │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.2 双模式架构

Forge 提供两种内容生成模式：

#### 快速模式 (Fast Mode)

```
START → Scout → Editor → AI_Detector → Humanizer → Reviewer → Director → END
```

- 适用场景：快速改写已有内容
- 特点：自动化程度高，去AI化流程
- 流程：抓取 → 改写 → 检测 → 人性化 → 审核 → 输出

#### 深度模式 (Deep Mode)

```
START → Scout → Research → Outline(用户确认) → Content → Critic → Tuning(人机交互) → END
```

- 适用场景：深度创作原创内容
- 特点：人机交互循环，大纲确认机制
- 流程：研究 → 大纲 → 确认 → 生成 → 批评 → 微调

---

## 三、核心模块详解

### 3.1 工作流节点 (Agents)

| 节点 | 文件位置 | 核心职责 | 输入 | 输出 |
|------|---------|---------|------|------|
| **Scout** | `forge/agents/scout.py` | 多平台内容抓取 | URL/关键词 | raw_content |
| **Editor** | `forge/agents/editor.py` | AI改写+知识库融合 | raw_content | rewritten_draft |
| **AI_Detector** | `forge/agents/ai_detector.py` | AI率检测 | rewritten_draft | ai_score |
| **Humanizer** | `forge/agents/humanizer_editor.py` | 去AI化改写 | draft + feedback | humanized_draft |
| **Reviewer** | `forge/agents/reviewer.py` | 内容质量审核 | draft | reflection_feedback |
| **Director** | `forge/agents/director.py` | 最终输出生成 | final_script | script_path, video_path |
| **Publisher** | `forge/agents/publisher.py` | 平台发布 | script_path | publish_status |

### 3.2 工具模块 (Tools)

| 工具 | 文件位置 | 功能说明 |
|------|---------|---------|
| **zhihu_scraper_persistent.py** | 知乎爬虫 | 持久化登录，文章/问答/回答抓取 |
| **wechat_scraper.py** | 微信爬虫 | 搜狗微信搜索，文章抓取 |
| **xhs_scraper_persistent.py** | 小红书爬虫 | 笔记抓取（已部分弃用） |
| **llm_client.py** | LLM调用封装 | Qwen API异步调用 |
| **judge_llm_client.py** | 判断LLM | AI检测专用模型 |
| **tts_generator.py** | TTS生成 | Edge TTS语音合成 |
| **video_generator.py** | 视频生成 | HeyGen数字人视频 |
| **video_composer.py** | 视频合成 | FFmpeg音视频合成 |
| **web_search.py** | 网络搜索 | 辅助素材检索 |

### 3.3 状态管理 (Graph State)

```python
class UnifiedState(TypedDict):
    # 基础字段
    session_id: str          # 会话ID
    mode: str                # "fast" / "deep"
    topic: str               # 输入主题/URL
    source_platform: str     # zhihu/wechat/manual
    target_platform: str     # xhs_video/zhihu_article

    # 快速模式字段
    raw_content: dict        # 原始抓取内容
    rewritten_draft: str     # 改写草稿
    ai_score: float          # AI检测得分 (0-1)
    humanize_revisions: int  # 人性化迭代次数
    final_script: str        # 最终文案

    # 深度模式字段
    user_input: str          # 用户需求描述
    outline: str             # 大纲内容
    outline_version: int     # 大纲版本号
    rag_context: str         # RAG知识库素材
    current_draft: str       # 当前草稿
    tuning_messages: list    # 微调对话历史

    # 控制字段
    stage: str               # 当前阶段
    generate_video: bool     # 是否生成视频
```

---

## 四、平台支持矩阵

### 4.1 来源平台

| 平台 | 内容类型 | 抓取方式 | 特殊处理 |
|------|---------|---------|---------|
| **知乎** | 问题/回答/文章 | playwright-stealth | 持久化登录，回答筛选 |
| **微信公众号** | 文章 | 搜狗微信搜索 | 从跳转链接提取关键词 |
| **小红书** | 笔记 | playwright-stealth | 图片下载（部分弃用） |
| **手动输入** | 文本 | 直接输入 | 无爬虫，直接处理 |

### 4.2 目标平台

| 平台 | 输出格式 | 发布方式 |
|------|---------|---------|
| **知乎文章** | 文本(.txt) | 手动发布 |
| **微信公众号** | 文本(.txt) | 手动发布 |
| **小红书视频** | 视频(.mp4) + 文案 | 手动发布 |
| **知乎视频** | 视频 + 文案 | 手动发布 |

---

## 五、知识库系统

### 5.1 架构设计

```
┌───────────────────────────────────────────────────────────────┐
│                    Knowledge Base System                        │
├───────────────────────────────────────────────────────────────┤
│                                                                 │
│   ┌─────────────┐      ┌─────────────┐      ┌─────────────┐   │
│   │  文档导入   │ ──► │  文档切分   │ ──► │  向量化     │   │
│   │ import_docs │      │ 段落切分    │      │ MiniLM-L6   │   │
│   └─────────────┘      └─────────────┘      └─────────────┘   │
│                                                     │           │
│                                                     ▼           │
│                                              ┌─────────────┐   │
│                                              │   Milvus    │   │
│                                              │  向量数据库 │   │
│                                              └─────────────┘   │
│                                                     │           │
│   ┌─────────────┐      ┌─────────────┐      ┌─────┴───────┐   │
│   │  查询接口   │ ──► │  语义检索   │ ──► │  返回文档   │   │
│   │ query_kb    │      │ 相似度匹配  │      │ top-k结果   │   │
│   └─────────────┘      └─────────────┘      └─────────────┘   │
│                                                                 │
└───────────────────────────────────────────────────────────────┘
```

### 5.2 配置参数

| 参数 | 值 | 说明 |
|------|---|------|
| 向量模型 | all-MiniLM-L6-v2 | 384维向量 |
| Collection | ruibo_knowledge | 知识库集合名 |
| 切分长度 | 100-500字 | 保持语义完整性 |
| 检索数量 | top-3 | 返回最相关3条 |

### 5.3 文档切分策略

```
原始文档 → 按段落切分 → 短段落合并 → 长段落再切分 → 向量化存储

规则：
1. 保持语义完整性（不跨段落切分）
2. 短段落 (<100字) 合并为一条
3. 长段落 (>500字) 按句号/问号切分
4. 添加 metadata: title, category
```

---

## 六、深度模式详解

### 6.1 流程图

```
┌─────────────────────────────────────────────────────────────────────┐
│                      Deep Mode Workflow                               │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  ┌─────────┐                                                         │
│  │  Scout  │ 抓取原始内容                                             │
│  └────┬────┘                                                         │
│       │                                                              │
│       ▼                                                              │
│  ┌─────────┐                                                         │
│  │ Research│ 搜索知识库 + 网络素材                                    │
│  │  Agent  │                                                         │
│  └────┬────┘                                                         │
│       │                                                              │
│       ▼                                                              │
│  ┌─────────┐     ┌───────────────┐                                   │
│  │ Outline │────►│ 用户确认大纲   │                                   │
│  │ Agent   │     │ (最多3次修改) │                                   │
│  └────┬────┘     └───────┬───────┘                                   │
│       │                  │                                          │
│       │   修改◄──────────┘                                          │
│       │                  │                                          │
│       ▼                  ▼ 确认                                     │
│  ┌─────────┐                                                         │
│  │ Content │ 根据大纲生成内容                                        │
│  │  Agent  │                                                         │
│  └────┬────┘                                                         │
│       │                                                              │
│       ▼                                                              │
│  ┌─────────┐                                                         │
│  │  Critic │ 自动审核批评                                            │
│  │  Agent  │                                                         │
│  └────┬────┘                                                         │
│       │                                                              │
│       ▼                                                              │
│  ┌─────────┐     ┌───────────────┐                                   │
│  │ Tuning  │────►│ 人机交互微调   │◄──── WebSocket实时通信            │
│  │  Agent  │     │ 用户修改反馈  │                                   │
│  └────┬────┘     └───────┬───────┘                                   │
│       │                  │                                          │
│       │   修改◄──────────┘                                          │
│       │                  │                                          │
│       ▼                  ▼ 定稿                                     │
│  ┌─────────┐                                                         │
│  │ Director│ 生成最终输出                                           │
│  └────┬────┘                                                         │
│       │                                                              │
│       ▼                                                              │
│  ┌─────────┐                                                         │
│  │   END   │                                                         │
│  └─────────┘                                                         │
│                                                                       │
└─────────────────────────────────────────────────────────────────────┘
```

### 6.2 阶段状态 (Stage)

| Stage | 说明 | 用户交互 |
|-------|------|---------|
| `planning` | 正在生成大纲 | 无 |
| `waiting_outline` | 等待用户确认大纲 | 确认/修改 |
| `outline_revision` | 大纲修改中 | 无 |
| `executing` | 正在生成全文 | 无 |
| `tuning` | 微调对话中 | 多轮对话 |
| `waiting_finalize` | 等待用户定稿 | 定稿/放弃 |
| `completed` | 已完成 | 无 |

### 6.3 WebSocket 通信

```javascript
// 客户端发送
{
  "type": "approve_outline",
  "session_id": "xxx",
  "approved": true
}

// 服务端推送
{
  "type": "stage_update",
  "stage": "executing",
  "message": "正在生成内容..."
}
```

---

## 七、API 接口文档

### 7.1 核心接口

#### 搜索文章

```http
POST /api/search
Content-Type: application/json

{
  "source": "人力资源",
  "source_platform": "zhihu",
  "max_results": 5,
  "search_mode": "keyword"
}

Response:
{
  "success": true,
  "articles": [
    {
      "title": "文章标题",
      "summary": "摘要内容",
      "source_url": "https://zhihu.com/...",
      "type": "question"
    }
  ]
}
```

#### 处理单篇文章

```http
POST /api/process
Content-Type: application/json

{
  "source_url": "https://zhihu.com/question/xxx",
  "source_platform": "zhihu",
  "target_platform": "zhihu_article"
}

Response:
{
  "success": true,
  "script_content": "改写后的内容",
  "script_path": "/output/scripts/xxx.txt"
}
```

#### 获取知乎回答列表

```http
POST /api/get_answers
Content-Type: application/json

{
  "question_url": "https://zhihu.com/question/xxx",
  "max_answers": 10
}

Response:
{
  "success": true,
  "question_title": "问题标题",
  "answers": [
    {
      "id": 1,
      "text": "回答内容摘要",
      "likes": 1234,
      "author": "作者名",
      "char_count": 500
    }
  ]
}
```

#### 深度模式 WebSocket

```javascript
ws://localhost:8000/ws/deep/{session_id}

消息格式:
{
  "type": "stage_update" | "outline_ready" | "content_ready" | "error",
  "stage": "planning" | "waiting_outline" | ...,
  "message": "状态描述",
  "data": { ... }
}
```

### 7.2 状态持久化接口

```http
GET /api/session/{session_id}

Response:
{
  "session_id": "xxx",
  "mode": "deep",
  "stage": "tuning",
  "current_draft": "当前内容",
  "outline": "大纲内容"
}
```

---

## 八、配置与部署

### 8.1 环境变量

```bash
# .env 文件

# LLM配置
QWEN_API_KEY=your-api-key
QWEN_MODEL=qwen-plus
JUDGE_MODEL=qwen-max

# LangSmith追踪
LANGCHAIN_API_KEY=your-key
LANGCHAIN_TRACING_V2=true
LANGCHAIN_PROJECT=Forge-Workflow

# 数字人视频
HEYGEN_API_KEY=your-key

# PostgreSQL
PG_HOST=localhost
PG_PORT=5432
PG_USER=forge
PG_PASSWORD=forge123
PG_DATABASE=forge

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379

# Milvus
MILVUS_HOST=localhost
MILVUS_PORT=19530
```

### 8.2 Docker部署

```yaml
# docker-compose.yml
services:
  milvus:
    image: milvusdb/milvus:latest
    ports:
      - "19530:19530"
    volumes:
      - ./volumes/milvus:/var/lib/milvus

  postgres:
    image: postgres:15
    environment:
      POSTGRES_USER: forge
      POSTGRES_PASSWORD: forge123
      POSTGRES_DB: forge
    ports:
      - "5432:5432"

  redis:
    image: redis:7
    ports:
      - "6379:6379"
```

### 8.3 启动命令

```bash
# 1. 启动基础设施
docker-compose up -d

# 2. 安装依赖
pip install -r requirements.txt

# 3. 知乎登录（首次使用）
python login_zhihu.py

# 4. 导入知识库文档
python import_docs.py

# 5. 启动Web服务
python run_web.py

# 6. 访问界面
http://localhost:8000
```

---

## 九、项目结构详解

```
Forge/
├── forge/                     # 核心代码目录
│   ├── __init__.py            # 版本信息
│   ├── config.py              # 全局配置
│   │
│   ├── agents/                # 工作流节点
│   │   ├── scout.py           # 内容抓取节点
│   │   ├── editor.py          # AI改写节点
│   │   ├── ai_detector.py     # AI检测节点
│   │   ├── humanizer_editor.py# 人性化改写节点
│   │   ├── reviewer.py        # 审核节点
│   │   ├── director.py        # 输出生成节点
│   │   ├── publisher.py       # 发布节点
│   │   ├── deep_nodes.py      # 深度模式节点
│   │   ├── research_agent.py  # 研究Agent
│   │   └── reflection_writer.py# 反思Agent
│   │
│   ├── tools/                 # 工具模块
│   │   ├── llm_client.py      # LLM调用封装
│   │   ├── judge_llm_client.py# 判断LLM
│   │   ├── zhihu_scraper_persistent.py# 知乎爬虫
│   │   ├── wechat_scraper.py  # 微信爬虫
│   │   ├── xhs_scraper_persistent.py# 小红书爬虫
│   │   ├── tts_generator.py   # TTS生成
│   │   ├── video_generator.py # 视频生成
│   │   ├── video_composer.py  # 视频合成
│   │   ├── digital_human_generator.py# 数字人
│   │   └── web_search.py      # 网络搜索
│   │
│   ├── graph/                 # 工作流定义
│   │   ├── state.py           # 状态定义
│   │   ├── workflow.py        # 快速模式工作流
│   │   ├── unified_workflow.py# 统一工作流
│   │   ├── checkpointer.py    # 状态持久化
│   │   └── __init__.py        # 导出接口
│   │
│   ├── deep_mode/             # 深度模式模块
│   │   ├── workflow.py        # 深度模式工作流
│   │   ├── graph.py           # LangGraph定义
│   │   ├── graph_hil.py       # 人机交互图
│   │   ├── session_state.py   # 会话状态
│   │   ├── session_manager.py # 会话管理
│   │   ├── websocket_handler.py# WebSocket处理
│   │   └── errors.py          # 错误定义
│   │
│   ├── knowledge/             # 知识库模块
│   │   ├── config.py          # Milvus配置
│   │   └── manager.py         # 知识库管理
│   │
│   ├── storage/               # 存储模块
│   │   ├── pg_client.py       # PostgreSQL
│   │   └ redis_client.py      # Redis
│   │
│   ├── web/                   # Web服务
│   │   ├── app.py             # FastAPI应用
│   │   ├── templates/         # HTML模板
│   │   └── static/            # 静态资源
│   │
│   └── evaluation/            # 评估模块
│       ├── storage.py         # 评估结果存储
│       ├── worker.py          # 评估Worker
│       └── probe_decorator.py # 探针装饰器
│
├── docs/                      # 文档目录
│   ├── project-overview.md    # 项目概述
│   ├── technical-architecture.md# 技术架构
│   ├── api-reference.md       # API文档
│   └── deployment-guide.md    # 部署指南
│
├── tests/                     # 测试目录
│   ├── test_workflow_nodes.py # 节点测试
│   └ test_web_api.py          # API测试
│
├── output/                    # 输出目录
│   ├── videos/                # 视频输出
│   ├── images/                # 图片输出
│   └ scripts/                 # 文案输出
│
├── main.py                    # CLI入口
├── run_web.py                 # Web服务入口
├── login_zhihu.py             # 知乎登录脚本
├── import_docs.py             # 知识库导入脚本
├── requirements.txt           # Python依赖
├── docker-compose.yml         # Docker配置
├── langgraph.json             # LangGraph配置
├ README.md                    # 项目README
└ PROJECT_GUIDE.md             # 本文档
```

---

## 十、技术栈总览

| 层级 | 技术 | 版本/说明 |
|------|------|----------|
| **工作流引擎** | LangGraph | 状态图 + 持久化 |
| **LLM框架** | LangChain | Agent + Tools |
| **Web框架** | FastAPI + Uvicorn | 异步API服务 |
| **LLM** | Qwen API | qwen-plus/qwen-max |
| **向量数据库** | Milvus | 语义检索 |
| **关系数据库** | PostgreSQL | 状态持久化 |
| **缓存** | Redis | 会话 + 队列 |
| **浏览器自动化** | Playwright + playwright-stealth | 反爬虫规避 |
| **向量模型** | SentenceTransformers | all-MiniLM-L6-v2 |
| **TTS** | Edge TTS | Microsoft语音 |
| **数字人** | HeyGen API | AI视频生成 |
| **视频合成** | FFmpeg | 音视频合成 |
| **前端** | HTML + CSS + JavaScript | 腾讯风格UI |
| **追踪** | LangSmith | 工作流可视化 |

---

## 十一、学习路径建议

### 11.1 入门阶段

1. **理解工作流概念**
   - 阅读 `forge/graph/state.py` 理解状态定义
   - 阅读 `forge/graph/workflow.py` 理解节点连接

2. **运行示例**
   ```bash
   python main.py  # CLI模式体验快速改写
   python run_web.py  # Web界面体验
   ```

3. **调试单个节点**
   - 查看 `forge/agents/scout.py` 了解爬虫实现
   - 查看 `forge/agents/editor.py` 了解改写逻辑

### 11.2 进阶阶段

1. **深度模式理解**
   - 阅读 `forge/deep_mode/workflow.py`
   - 理解 Plan-Execute + ReAct 架构

2. **知识库集成**
   - 阅读 `forge/knowledge/manager.py`
   - 运行 `import_docs.py` 导入文档

3. **WebSocket通信**
   - 阅读 `forge/deep_mode/websocket_handler.py`
   - 理解人机交互循环

### 11.3 高级阶段

1. **自定义节点**
   - 在 `forge/agents/` 添加新节点
   - 在 `forge/graph/workflow.py` 注册节点

2. **自定义工具**
   - 在 `forge/tools/` 添加新工具
   - 实现爬虫、LLM调用等

3. **评估体系**
   - 阅读 `forge/evaluation/` 目录
   - 理解探针装饰器和评估Worker

---

## 十二、常见问题解答

### Q1: 知乎登录失败怎么办？

```bash
# 清除浏览器数据重新登录
rm -rf ~/.forge/browser_data/zhihu
python login_zhihu.py
```

### Q2: Milvus 连接失败？

```bash
# 检查 Milvus 是否启动
docker ps | grep milvus

# 启动 Milvus
docker-compose up -d milvus
```

### Q3: 内容改写质量不佳？

- 检查知识库是否有相关素材
- 调整 `MAX_REVISIONS` 增加迭代次数
- 使用更强的模型 (qwen-max)

### Q4: 深度模式卡在大纲确认？

- 检查 WebSocket 连接状态
- 查看 `forge/deep_mode/session_state.py` 阶段状态
- 使用 `/api/session/{session_id}` 查询状态

### Q5: 视频生成失败？

- 检查 HeyGen API Key 是否有效
- 确认 TTS 生成的音频文件存在
- 查看 FFmpeg 日志

---

## 十三、扩展与定制

### 13.1 新增来源平台

1. 创建爬虫模块 `forge/tools/new_platform_scraper.py`
2. 在 `forge/agents/scout.py` 添加平台检测
3. 在 `forge/config.py` 添加平台配置

### 13.2 新增目标平台

1. 在 `forge/tools/new_platform_publisher.py` 实现发布逻辑
2. 在 `forge/agents/director.py` 添加输出格式
3. 更新 `TARGET_PLATFORMS` 配置

### 13.3 自定义改写策略

修改 `forge/agents/editor.py` 中的 prompt：

```python
REWRITE_PROMPT = """
你的自定义改写指令...
"""
```

### 13.4 添加评估指标

在 `forge/evaluation/` 添加新的评估维度：

```python
# probe_decorator.py
@with_probe("custom_metric")
async def custom_evaluation(state):
    # 你的评估逻辑
    return {"custom_score": score}
```

---

## 十四、总结

Forge 是一个功能完整的多智能体内容转换系统，核心特点：

1. **双模式架构**：快速改写 + 深度生成，满足不同场景需求
2. **工作流引擎**：LangGraph 提供可视化流程和状态持久化
3. **知识库融合**：RAG 系统自然融入企业品牌信息
4. **去AI化流程**：AI检测 + 人性化改写，降低AI痕迹
5. **人机交互**：WebSocket 实现深度模式的人机协作
6. **完整评估体系**：探针装饰器 + 评估Worker

适合用于：
- 企业内容运营自动化
- 多平台内容转换
- 知识库驱动的内容创作
- AI辅助写作系统

---

## 十五、事实校验与评估重设计计划

> 目标：降低 Research + Reflection 写作链路中的事实幻觉风险，并重新设计更贴合项目场景的 Faithfulness / Relevance 评估指标。

### 15.1 当前问题总结

当前事实清单是 Markdown 字符串，通过 LangGraph `UnifiedState["fact_sheet"]` 从 Research Agent 传给 Reflection Writer。Reflection Writer 内部已有 Fact Checker、Generator、Critic 流程，但事实检测仍是软校验：

- Fact Checker 主要依赖 LLM 判断事实是否合理。
- 没有强制逐条核对来源 URL。
- 没有抽取最终文章中的事实断言做 claim 级校验。
- Generator 可能基于事实清单扩写出无来源的新事实。
- Critic 可能放过数字、日期、人名、机构名等细节错误。
- 当前评估中的 `faithfulness_score` / `relevance_score` 实际是兼容字段，真实指标是 `summarization_score` / `rubrics_score`。

因此，现有链路能降低明显错误，但不能稳定阻断事实幻觉。

### 15.2 设计原则

1. **LangGraph 管主流程，MQ 管慢任务和副作用**
   - Research -> Reflection 继续用 LangGraph state 传递。
   - 视频生成、发布、评估、自进化等慢任务适合放入队列。

2. **状态共享，Prompt 局部隔离**
   - 多智能体可以共享同一个 session state。
   - 每个 agent 只允许读取自己的输入 schema。
   - 不把整个 `UnifiedState` 直接传给 LLM。

3. **事实校验从整篇评分改为 claim 级校验**
   - 不只问 LLM “是否忠实”。
   - 必须抽取文章中的具体事实断言，并逐条映射到事实来源。

4. **Faithfulness 和 Relevance 分工明确**
   - Faithfulness：最终文章是否只说来源能支持的话。
   - Relevance：最终文章是否满足用户目标、主题和目标平台要求。

### 15.3 结构化事实清单

将现有 Markdown Fact Sheet 升级为结构化对象，保留 Markdown 展示层，但内部用 JSON/Pydantic 表示。

建议结构：

```json
{
  "facts": [
    {
      "fact_id": "F1",
      "claim": "2023年AI市场规模达到150亿美元",
      "source_type": "web",
      "source_url": "https://example.com/report",
      "source_excerpt": "原文摘录片段",
      "confidence": "high",
      "risk_level": "medium"
    }
  ],
  "references": [
    {
      "ref_id": "R1",
      "url": "https://example.com/report",
      "title": "报告标题"
    }
  ]
}
```

要求：

- 每条关键事实必须有 `fact_id`。
- 数字、日期、人名、机构名、专家观点必须带来源。
- 来源缺失的事实只能作为低置信度素材，不允许直接写成确定表达。
- Research Agent 输出后先做 schema 校验，失败时进入 fallback 或要求重试。

### 15.4 写作阶段约束

Generator 写作时必须遵守：

- 所有具体事实必须来自结构化事实清单。
- 具体事实句后标注 `fact_id`，例如 `[F3]`。
- 不得新增事实清单之外的数字、日期、人名、机构名、政策、报告结论。
- 观点可以润色，但不能把观点改写成确定事实。
- 对低置信度事实使用保守表达，或直接不用。

Reflection Writer 内部流程建议调整为：

```text
fact_checker
  -> generator
  -> claim_extractor
  -> claim_verifier
  -> critic
  -> reviser / approved
```

其中 `claim_verifier` 应成为硬门槛，而不是只给建议。

### 15.5 Claim 级事实校验

新增 Claim Extractor，从最终文章中抽取事实断言，重点识别：

- 数字、百分比、金额、排名
- 日期、时间线
- 人名、机构名、专家观点
- 政策、法规、报告结论
- 因果关系
- “唯一”“最大”“必然”“全部”等强断言

Claim Verifier 对每条 claim 进行分类：

```text
supported      来源明确支持
partial        来源部分支持，但有轻微扩写
unsupported    没有来源支持
contradicted   和来源冲突
overstated     说得比来源更绝对
needs_review   无法自动判断，需人工确认
```

阻断规则：

- `contradicted > 0`：必须打回或人工确认。
- `unsupported` 中包含数字、日期、人名、机构名：必须打回。
- `unsupported_ratio > 20%`：不允许发布。
- `needs_review` 超过阈值：进入人工确认。

### 15.6 Faithfulness 重新设计

Faithfulness 只评估事实是否被来源支持。

建议公式：

```text
faithfulness =
  (supported * 1.0 + partial * 0.6 + overstated * 0.3) / total_claims
```

硬性封顶：

```text
contradicted > 0              => faithfulness <= 0.60
unsupported_ratio > 20%       => faithfulness <= 0.70
无来源关键数字/日期/人名      => faithfulness <= 0.80
```

输出结构：

```json
{
  "faithfulness": {
    "score": 0.82,
    "supported_claims": 12,
    "partial_claims": 2,
    "unsupported_claims": 1,
    "contradicted_claims": 0,
    "overstated_claims": 1,
    "details": [
      {
        "claim": "2023年AI市场规模达到150亿美元",
        "status": "supported",
        "source_fact_id": "F3"
      }
    ]
  }
}
```

### 15.7 Relevance 重新设计

Relevance 不再表示事实相关性，而是评估文章是否完成任务目标。

输入：

- 原始主题
- 用户需求
- 目标平台
- 大纲
- 最终文章

子维度：

```text
topic_alignment      是否围绕原主题
user_intent_match    是否满足用户改写要求
platform_fit         是否符合目标平台
audience_value       是否对目标读者有价值
structure_fit        结构是否适合输出形态
```

建议公式：

```text
relevance =
  topic_alignment * 0.30
+ user_intent_match * 0.30
+ platform_fit * 0.20
+ audience_value * 0.10
+ structure_fit * 0.10
```

输出结构：

```json
{
  "relevance": {
    "score": 0.88,
    "topic_alignment": 0.90,
    "user_intent_match": 0.85,
    "platform_fit": 0.90,
    "audience_value": 0.80,
    "structure_fit": 0.90,
    "reason": "文章围绕原主题展开，符合知乎长文风格，但用户指定案例覆盖略弱。"
  }
}
```

### 15.8 总分与发布门槛

新综合质量分：

```text
quality_score =
  faithfulness * 0.45
+ relevance * 0.35
+ human_score * 0.20
```

发布建议：

```text
faithfulness < 0.75       => 不建议发布
contradicted_claims > 0   => 必须人工确认
unsupported_claims > 3    => 打回重写
quality_score >= 0.85     => 可自动通过
0.70 <= quality_score < 0.85 => 建议人工复核
quality_score < 0.70      => 打回重写
```

注意：人性化分数不能掩盖事实错误。事实错误应优先阻断。

### 15.9 多用户与模型并发限制

如果模型同时只能处理一个请求：

- 按 `session_id` 隔离用户上下文。
- 按 `task_id` 隔离一次模型任务。
- Web/API 层只负责接单和返回任务状态。
- 单个 LLM Worker 串行消费模型任务队列。
- Worker 执行时用 `thread_id=session_id` 恢复 LangGraph state。

建议队列：

```text
forge:llm:queue
```

任务结构：

```json
{
  "task_id": "uuid",
  "session_id": "uuid",
  "task_type": "reflection_writer",
  "status": "queued",
  "retry_count": 0
}
```

优先队列化：

- `deep_outline_generator`
- `research_agent`
- `reflection_writer`
- `tuning_agent`
- 视频生成
- 发布
- 评估

### 15.10 实施步骤

#### Phase 1：评估命名与兼容修正

- 明确当前 `faithfulness_score` / `relevance_score` 是兼容字段。
- 在评估结果中正式引入：
  - `faithfulness`
  - `relevance`
  - `claim_details`
- 修复 fallback faithfulness 评分的 0-1 / 1-10 范围转换问题。
- 更新测试，移除旧公式假设。

#### Phase 2：结构化事实清单

- 新增 `StructuredFactSheet` schema。
- Research Agent 输出结构化事实。
- 增加 schema 校验和 fallback。
- 保留 Markdown 渲染，方便 prompt 和前端展示。

#### Phase 3：写作引用 fact_id

- Generator prompt 要求具体事实标注 `[Fxx]`。
- Reviser 修改时不得删除或伪造 fact_id。
- Critic 检查 fact_id 使用情况。

#### Phase 4：Claim Extractor + Claim Verifier

- 新增文章 claim 抽取节点。
- 新增 claim-to-fact 校验节点。
- 输出 supported / partial / unsupported / contradicted / overstated。
- 有硬错误时打回 Reviser。

#### Phase 5：新版 Faithfulness / Relevance 评分

- Faithfulness 基于 claim verifier 聚合。
- Relevance 基于主题、用户需求、平台适配度打分。
- 总分改为 `45/35/20` 权重。
- 前端展示 claim 明细和发布建议。

#### Phase 6：任务队列与并发控制

- 新增 LLM 任务队列。
- 单 worker 串行调用模型。
- 任务状态写入 PG/Redis。
- 前端通过轮询或 WebSocket 展示 queued/running/completed/failed。

### 15.11 验收标准

- 最终文章中的数字、日期、人名、机构名都能映射到 `fact_id`。
- 无来源的关键事实会被拦截。
- 与事实清单冲突的 claim 不会通过 Reflection。
- Faithfulness 和 Relevance 输出明细，不只输出单一分数。
- 多用户同时提交时，session state 不串，模型调用按队列串行执行。
- 视频、发布、评估等慢任务不阻塞 Web 请求。

---

**文档版本**: v1.0
**生成日期**: 2026-05-07
**适用项目版本**: Forge 0.1.0
