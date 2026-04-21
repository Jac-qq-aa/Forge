# Forge 技术架构文档

## 系统架构概览

Forge 是基于 LangGraph 的多智能体协作系统，采用流水线式工作流处理内容转换任务。

### 架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Forge Workflow                               │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│   START ──► Scout ──► Editor ──► Reviewer ──► Director ──► Publisher │
│                           │              │                           │
│                           │              │                           │
│                           └──────────────┘                           │
│                          (revision loop)                             │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### LangGraph 工作流 ASCII 图

```
           ┌─────────────┐
           │    START    │
           └──────┬──────┘
                  │
                  ▼
           ┌─────────────┐
           │    Scout    │
           └──────┬──────┘
                  │
                  ▼
           ┌─────────────┐
           │    Editor   │
           └──────┬──────┘
                  │
                  ▼
           ┌─────────────┐
           │   Reviewer  │
           └──────┬──────┘
                  │
        ┌─────────┴─────────┐
        │                   │
        ▼                   ▼
   ┌─────────┐        ┌─────────────┐
   │ Editor  │        │   Director  │
   │(revision)│       └──────┬──────┘
   └──────┬──┘               │
          │                  ▼
          │           ┌─────────────┐
          │           │  Publisher  │
          │           └──────┬──────┘
          │                  │
          ▼                  ▼
     ┌─────────┐        ┌───────┐
     │(loopback)│        │  END  │
     └─────────┘        └───────┘
```

## 核心组件

### 1. GraphState（状态定义）

状态是工作流中各节点传递的核心数据结构：

```python
class GraphState(TypedDict, total=False):
    # 输入
    topic: str                 # 输入主题或URL
    source_platform: str       # 来源平台: "zhihu" | "wechat"
    target_platform: str       # 目标平台: "zhihu_article" | "wechat_article"

    # Scout 输出
    raw_content: dict          # 抓取的原始内容
    article_list: List[dict]   # 多文章列表

    # Editor 输出
    rewritten_draft: str       # AI改写草稿

    # Reviewer 输出
    reflection_feedback: str   # 审核反馈
    final_script: str          # 最终文案

    # Director 输出
    video_path: str            # 视频文件路径
    script_path: str           # 文案文件路径

    # Publisher 输出
    publish_status: str        # 发布状态

    # 控制流
    revision_count: int        # 修订次数
    skip_publish: bool         # 是否跳过发布
```

### 2. 工作流节点

#### Scout Node（内容抓取）

**职责**：从知乎或微信公众号抓取原始内容

**输入**：
- `topic`: URL 或关键词
- `source_platform`: 平台标识

**输出**：
- `raw_content`: 包含 title, text, images, likes, author, source_url

**逻辑流程**：
1. URL 平台自动检测
2. 根据平台选择对应爬虫
3. 处理不同内容类型（知乎问题/文章/回答，微信文章）
4. 返回结构化内容

**代码位置**：`forge/agents/scout.py`

#### Editor Node（AI改写）

**职责**：使用 Qwen LLM 改写内容，融合知识库

**输入**：
- `raw_content`: 原始内容
- `reflection_feedback`: 审核反馈（如有）
- `revision_count`: 当前修订次数
- `target_platform`: 目标平台

**输出**：
- `rewritten_draft`: 改写后的内容
- `revision_count`: 更新后的修订次数

**改写策略**：
- 根据原文长度动态调整篇幅
- 搜索知识库相关内容作为参考
- 保留核心观点，确保原创性
- 自然融入锐博集团品牌信息

**篇幅规则**：
| 原文长度 | 目标篇幅 |
|---------|---------|
| >3000字 | 1500-2000字 |
| >1500字 | 800-1200字 |
| >500字 | 600-900字 |
| ≤500字 | 500-800字 |

**代码位置**：`forge/agents/editor.py`

#### Reviewer Node（内容审核）

**职责**：审核改写内容质量

**输入**：
- `rewritten_draft`: 改写草稿
- `revision_count`: 修订次数

**输出**：
- `reflection_feedback`: 审核反馈（需要修订时）
- `final_script`: 最终文案（通过时）

**审核逻辑**：
- 检查内容完整性
- 评估观点保留程度
- 验证原创性
- 最大修订次数限制（3次）

**代码位置**：`forge/agents/reviewer.py`

#### Director Node（输出生成）

**职责**：生成最终输出文件

**输入**：
- `final_script`: 最终文案
- `target_platform`: 目标平台

**输出**：
- `script_path`: 文案文件路径
- `video_path`: 视频文件路径（视频平台时）

**处理逻辑**：
- 文章平台：保存为 `.txt` 文件
- 视频平台：生成脚本 + 调用视频合成

**代码位置**：`forge/agents/director.py`

#### Publisher Node（发布节点）

**职责**：执行内容发布（dry-run 模式）

**输入**：
- `script_path`: 文案路径
- `video_path`: 视频路径
- `skip_publish`: 是否跳过发布

**输出**：
- `publish_status`: 发布状态

**当前状态**：dry-run 模式，生成本地文件供手动发布

**代码位置**：`forge/agents/publisher.py`

### 3. 路由逻辑

```python
def route_after_review(state: GraphState) -> Literal["director", "editor"]:
    """
    审核后路由逻辑：
    - 有反馈且 revision_count < 3 -> 返回 Editor 修订
    - 通过或达到最大修订次数 -> 跳转 Director
    """
    if reflection_feedback and revision_count < 3:
        return "editor"
    return "director"
```

## 爬虫实现

### 知乎爬虫

**技术栈**：Playwright + playwright-stealth + 持久化上下文

**核心特性**：
- 持久化浏览器上下文保存登录状态
- 反爬虫检测规避
- 多内容类型支持（问题、回答、文章）

**数据目录**：`~/.forge/browser_data/zhihu`

**主要方法**：
| 方法 | 功能 |
|------|------|
| `search_articles(keyword)` | 关键词搜索文章 |
| `get_user_articles(user_id)` | 获取博主文章 |
| `scrape_question(url)` | 抓取问题详情 |
| `scrape_answer(url)` | 抓取回答内容 |
| `scrape_article(url)` | 抓取专栏文章 |
| `get_question_answers(url)` | 获取问题回答列表 |

**回答筛选逻辑**：
1. 获取问题下所有回答
2. 解析点赞数（支持多种格式）
3. 计算字数统计
4. 按点赞排序返回

**代码位置**：`forge/tools/zhihu_scraper_persistent.py`

### 微信公众号爬虫

**技术栈**：Playwright + 搜狗微信搜索

**搜索入口**：`weixin.sogou.com`

**核心特性**：
- 通过搜狗搜索获取微信文章
- 持久化上下文避免反爬
- 从搜狗链接提取关键词

**数据目录**：`~/.forge/browser_data/wechat`

**代码位置**：`forge/tools/wechat_scraper.py`

## 知识库架构

### 向量数据库

**数据库**：Milvus
**向量模型**：all-MiniLM-L6-v2（384维）
**Collection**：ruibo_knowledge

### Schema 定义

```python
fields = [
    FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
    FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=384),
    FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=2000),
    FieldSchema(name="category", dtype=DataType.VARCHAR, max_length=64),
    FieldSchema(name="title", dtype=DataType.VARCHAR, max_length=256),
]
```

### 搜索流程

```
原始内容 → 提取关键词 → 向量化 → Milvus搜索 → 返回相关文档
```

### 文档导入策略

**切分规则**：
1. 按段落切分保持语义完整性
2. 短段落合并为一条
3. 长段落按句号/问号切分
4. 片段长度 100-500字

**代码位置**：`forge/knowledge/manager.py`

## LLM 集成

### Qwen API

**模型**：qwen3.5-plus
**API Key**：通过环境变量配置

### 调用方式

```python
class LLMClient:
    async def chat_with_retry(self, prompt: str, system_prompt: str) -> str:
        """
        带重试机制的 LLM 调用
        - 自动处理 API 错误
        - 支持异步调用
        """
```

**代码位置**：`forge/tools/llm_client.py`

## Web 服务架构

### FastAPI 后端

**端口**：8000
**路由**：
- `GET /`: 主页面
- `POST /api/search`: 搜索文章
- `POST /api/process`: 处理单篇文章
- `POST /api/get_answers`: 获取回答列表
- `POST /api/process_multi`: 合并多回答处理
- `POST /api/save`: 保存编辑内容
- `GET /api/status`: 服务状态

### 前端架构

**技术栈**：HTML + CSS + JavaScript（原生）
**风格**：腾讯风格 UI
**特性**：
- 响应式设计
- 卡片式布局
- 锐博集团 Logo
- 步骤式操作流程

**代码位置**：`forge/web/templates/index.html`, `forge/web/static/style.css`

## 数据流图

### 单篇文章处理

```
用户输入 URL → Scout 抓取 → Editor 改写 → Reviewer 审核 → Director 输出 → 用户获取
```

### 多回答合并处理

```
知乎问题 URL → get_answers 获取列表 → 用户选择 → 合并内容 → workflow 处理 → 输出
```

### 知识库搜索流程

```
Editor 改写 → 提取关键词 → KnowledgeBase.search → 返回相关文档 → 融入改写
```

## 配置架构

### 环境变量

| 变量 | 用途 | 默认值 |
|------|------|--------|
| QWEN_API_KEY | LLM API密钥 | 必填 |
| QWEN_MODEL | 模型名称 | qwen3.5-plus |
| MILVUS_HOST | Milvus地址 | localhost |
| MILVUS_PORT | Milvus端口 | 19530 |

### 配置文件

**位置**：`forge/config.py`

**功能**：
- 加载环境变量
- 定义默认配置
- 配置日志格式

## 扩展点

### 新增平台

1. 在 `forge/tools/` 创建爬虫模块
2. 在 `forge/agents/scout.py` 添加平台检测和处理
3. 在 `forge/config.py` 添加平台配置

### 新增节点

1. 在 `forge/agents/` 创建节点模块
2. 在 `forge/graph/workflow.py` 添加节点和边
3. 更新 `GraphState` 状态定义

### 新增知识库来源

1. 在 `forge/knowledge/` 扩展导入逻辑
2. 更新文档切分策略
3. 添加新的 metadata 字段

## 技术栈总结

| 层级 | 技术 |
|------|------|
| 工作流引擎 | LangGraph |
| Web框架 | FastAPI + Uvicorn |
| LLM | Qwen API |
| 向量数据库 | Milvus |
| 浏览器自动化 | Playwright + playwright-stealth |
| 向量模型 | SentenceTransformers (all-MiniLM-L6-v2) |
| 前端 | HTML + CSS (腾讯风格) |