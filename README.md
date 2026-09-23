# Forge - 内容转换平台

多平台内容转换工作流系统，支持从知乎/微信公众号抓取内容，AI改写生成原创内容。

## 功能特性

### 核心功能
- **多平台抓取**：知乎、微信公众号
- **深度内容生成**：事实研究、大纲审核、正文生成、微调与定稿
- **知识库融合**：自动搜索锐博集团知识库，自然融入品牌信息
- **回答筛选**：知乎问题支持多回答筛选，按点赞排序，字数过滤
- **合并洗稿**：支持合并多个回答后整体改写

### 平台支持

| 平台 | 作为来源 | 作为目标 |
|------|----------|----------|
| 知乎 | ✓ 问答、文章 | ✓ 知乎文章 |
| 微信公众号 | ✓ | ✓ 微信公众号文章 |

## 快速开始

### 1. 环境准备

```bash
# 克隆项目
git clone <repository-url>
cd Forge

# 创建 conda 环境
conda create -n forge python=3.10
conda activate forge

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置

创建 `.env` 文件：
```
QWEN_API_KEY=your-api-key
QWEN_API_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen3.6-plus
```

### 3. 启动服务

```bash
# 启动 Milvus 向量数据库（用于知识库）
docker-compose up -d

# 启动 Web 服务
python run_web.py
```

访问 http://localhost:8000 开始使用。

### 4. 知乎登录

首次使用知乎功能需要扫码登录：

```bash
python login_zhihu.py
```

浏览器会弹出知乎页面，扫码登录后自动保存状态。

## 项目结构

```
forge/
├── config.py                 # 配置管理
├── tools/
│   ├── llm_client.py         # Qwen LLM 封装
│   ├── zhihu_scraper_persistent.py  # 知乎爬虫
│   ├── wechat_scraper.py     # 微信公众号爬虫
│   ├── tts_generator.py      # Edge TTS
│   └── video_composer.py     # FFmpeg 合成
├── agents/
│   ├── research_agent.py     # 事实研究
│   ├── reflection_writer.py  # 正文生成与质量迭代
│   └── revision_agent.py     # 定向修改
├── knowledge/
│   ├── config.py             # Milvus 配置
│   └── manager.py            # 知识库管理
├── deep_mode/
│   ├── graph_hil.py          # 唯一 LangGraph 工作流
│   ├── session_state.py      # 会话状态
│   └── session_manager.py    # 会话持久化
├── graph/
│   └── checkpointer.py       # PostgreSQL 检查点
└── web/
    ├── app.py                # FastAPI 后端
    ├── templates/index.html  # 前端页面
    └── static/style.css      # 样式
```

## 工作流架构

Forge 只保留深度生成流程，基于 LangGraph 检查点支持人工审核后的暂停与恢复：

```
START → RAG → Research → 事实审核 → Outline → 大纲审核
      → Reflection → 内容审核/微调 → Finalize → END
```

| 节点 | 功能 |
|------|------|
| RAG | 检索知识库素材 |
| Research | 生成可追溯事实清单 |
| 事实审核 | 用户确认、排除或要求补充研究 |
| Outline | 基于确认事实生成大纲 |
| Reflection | 生成正文并进行质量迭代 |
| 内容审核/微调 | 用户逐轮调整正文 |
| Finalize | 保存定稿并投递异步质量评估 |

## 界面说明

### Web界面功能

1. **配置参数**
   - 来源平台：知乎/微信公众号
   - 目标平台：知乎文章/微信公众号文章
   - 搜索方式：关键词/博主文章

2. **搜索与筛选**
   - 关键词搜索文章
   - 知乎问题支持回答筛选（点赞、字数过滤）
   - 多回答合并洗稿

3. **深度生成**
   - 审核事实与来源
   - 确认或修改大纲
   - 对话微调并保存定稿

## 常用命令

```bash
# 激活环境
conda activate forge

# 启动 Web 服务
python run_web.py

# 知乎登录
python login_zhihu.py

# 导入知识库文档
python import_docs.py

# 查询知识库
python query_knowledge.py

# 启动 Milvus GUI (Attu)
docker run -d --name attu -p 3000:3000 -e MILVUS_URL=host.docker.internal:19530 zilliz/attu:latest
```

## 配置说明

### 环境变量 (.env)

| 变量 | 说明 | 必填 |
|------|------|------|
| QWEN_API_KEY | 通义千问 API Key | 是 |
| QWEN_MODEL | 模型名称 | 否（默认 qwen3.6-plus） |

### 浏览器缓存

登录状态保存在：
- 知乎：`~/.forge/browser_data/zhihu`
- 微信：`~/.forge/browser_data/wechat`

## 技术栈

- **后端**：FastAPI + LangGraph
- **前端**：HTML + CSS（腾讯风格UI）
- **AI**：Qwen LLM（通义千问）
- **向量数据库**：Milvus
- **浏览器自动化**：Playwright + playwright-stealth

## 文档

- [项目概述](docs/project-overview.md)
- [技术架构](docs/technical-architecture.md)
- [高并发异步架构实施说明](docs/high-concurrency-async-architecture.md)
- [RocketMQ 文章生成异步调度与故障恢复](docs/rocketmq-generation-async.md)
- [多线程与高并发处理说明（含关键代码）](docs/high-concurrency-code-guide.md)
- [高并发第一阶段加固说明](docs/high-concurrency-phase-one.md)
- [API接口](docs/api-reference.md)
- [部署指南](docs/deployment-guide.md)

## 许可证

MIT License
