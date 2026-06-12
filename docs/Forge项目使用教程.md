# Forge 项目使用教程

本文档面向 Forge 项目的安装、配置、启动和日常使用。Forge 是一个多平台内容转换工作流系统，支持从知乎、微信公众号等来源抓取内容，通过 Qwen 模型改写、审核并输出适合知乎文章或微信公众号文章的成稿；项目还包含知识库融合、AI 检测、人性化改写、视频生成和深度写作模式。

## 1. 项目能力概览

Forge 的核心使用场景包括：

- 关键词搜索知乎或微信公众号文章。
- 获取知乎博主文章。
- 抓取知乎问题回答，并按点赞数、字数筛选。
- 合并多个知乎回答后统一改写。
- 将原文改写成知乎文章或微信公众号文章。
- 结合锐博集团知识库自然融入品牌信息。
- 编辑、保存生成结果。
- 可选生成视频或数字人视频。
- 使用深度模式进行更长篇、更可控的文章生成。

## 2. 运行环境

推荐环境：

- Linux、macOS 或 Windows WSL2
- Python 3.10+
- Docker 与 Docker Compose
- 8GB 以上内存
- 20GB 以上可用磁盘空间

最低环境：

- Python 3.10+
- 4GB 内存
- 10GB 可用磁盘空间

## 3. 安装步骤

进入项目目录：

```bash
cd /home/hugo/Forge
```

创建并激活 Python 环境。使用 conda：

```bash
conda create -n forge python=3.10
conda activate forge
```

或使用 venv：

```bash
python -m venv venv
source venv/bin/activate
```

安装依赖：

```bash
pip install -r requirements.txt
```

安装 Playwright 浏览器：

```bash
playwright install chromium
```

## 4. 配置环境变量

复制示例配置：

```bash
cp .env.example .env
```

编辑 `.env`，至少配置以下内容：

```ini
QWEN_API_KEY=your-qwen-api-key
QWEN_API_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen3.6-plus

WEB_PORT=8000
GATEWAY_PORT=8001
GATEWAY_API_URL=http://localhost:8001
LANGGRAPH_SERVER_URL=http://localhost:2024
DEEP_MODE_AGENT_ID=deep_mode_agent
```

常用可选配置：

```ini
LANGCHAIN_API_KEY=your-langchain-api-key
LANGCHAIN_TRACING_V2=true
LANGCHAIN_PROJECT=Forge-Content-Workflow

AI_THRESHOLD=0.7
MAX_HUMANIZE_REVISIONS=3

REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
EVAL_REDIS_DB=1

HEYGEN_API_KEY=your-heygen-api-key
OSS_BUCKET=your-bucket-name
OSS_ENDPOINT=oss-cn-hangzhou.aliyuncs.com
OSS_ACCESS_KEY_ID=your-access-key-id
OSS_ACCESS_KEY_SECRET=your-access-key-secret
```

注意：真实 `.env` 包含密钥，不要提交到 Git，也不要放进对外分发的压缩包。

## 5. 启动基础依赖

项目使用 Milvus 作为知识库向量数据库。启动 Milvus、etcd 和 minio：

```bash
docker compose up -d
```

查看状态：

```bash
docker compose ps
```

Milvus 默认端口：

- `19530`：Milvus 服务端口
- `9091`：Milvus 健康检查端口
- `9000`：MinIO API
- `9001`：MinIO Console

可选启动 Attu 图形界面：

```bash
docker run -d --name attu \
  -p 3000:3000 \
  -e MILVUS_URL=host.docker.internal:19530 \
  zilliz/attu:latest
```

访问 `http://localhost:3000` 管理 Milvus 数据。

## 6. 初始化知识库

项目根目录中已有锐博集团相关文档时，可以导入知识库：

```bash
python import_docs.py
```

查询知识库验证导入结果：

```bash
python query_knowledge.py
```

知识库默认配置位于 `forge/knowledge/config.py`，默认连接 `127.0.0.1:19530`，collection 名称为 `ruibo_knowledge`。

## 7. 平台登录

### 7.1 知乎登录

首次使用知乎搜索、抓取、博主文章或回答筛选前，建议先登录：

```bash
python login_zhihu.py
```

操作流程：

1. 脚本会打开浏览器。
2. 使用知乎 App 扫码登录。
3. 登录成功后按 `Ctrl+C` 退出脚本。
4. 登录状态会保存到本机浏览器缓存。

缓存位置：

```text
~/.forge/browser_data/zhihu
```

### 7.2 微信公众号

微信公众号内容通过搜狗微信搜索访问，通常不需要单独登录。缓存位置：

```text
~/.forge/browser_data/wechat
```

## 8. 启动 Web 服务

确保 `.env` 中配置了 `WEB_PORT`，例如：

```ini
WEB_PORT=8000
```

启动：

```bash
python run_web.py
```

访问：

```text
http://localhost:8000
```

后台运行：

```bash
nohup python run_web.py > forge-web.log 2>&1 &
```

查看日志：

```bash
tail -f forge-web.log
```

## 9. Web 界面使用流程

### 9.1 搜索文章

1. 打开 `http://localhost:8000`。
2. 选择来源平台：知乎或微信公众号。
3. 选择目标平台：知乎文章或微信公众号文章。
4. 选择搜索方式：
   - 关键词搜索：输入主题关键词。
   - 博主文章：输入知乎博主 ID 或从已保存博主中选择。
5. 设置最大结果数。
6. 点击搜索。

搜索结果会展示标题、摘要、作者、来源链接和内容类型。

### 9.2 处理单篇文章

1. 在搜索结果中选择一篇文章。
2. 点击处理或生成。
3. 系统会抓取原文。
4. Editor 节点调用 Qwen 进行改写。
5. Reviewer 节点审核内容。
6. Director 节点输出最终文案。
7. 页面展示原文与改写结果对比。

生成后可以直接编辑结果，并保存文案。

### 9.3 处理知乎问题的多个回答

当搜索结果是知乎问题时：

1. 点击获取回答。
2. 系统会抓取回答列表。
3. 回答会展示点赞数、字数、作者和摘要。
4. 选择一个或多个回答。
5. 点击合并处理。
6. 系统会把选中回答合并后统一改写。

建议优先选择字数较多、点赞较高、观点互补的回答。

### 9.4 手动输入内容

如果不想从平台抓取，也可以使用手动输入接口或页面上的手动处理功能：

1. 输入标题。
2. 粘贴原文。
3. 选择目标平台。
4. 生成改写结果。

适合处理本地文档、访谈稿、内部资料或临时文本。

### 9.5 保存结果

生成或编辑后的内容可以保存。输出通常会进入项目或用户目录下的输出路径，常见目录包括：

```text
~/.forge/output/
output/
```

具体文件位置以页面返回或服务日志为准。

## 10. 深度模式

深度模式用于更长篇、更可控的内容生成，涉及三个服务：

- LangGraph Server：运行深度模式图。
- Gateway API：对接 LangGraph SDK，并处理 LangSmith trace stitching。
- Web UI：用户界面。

### 10.1 启动 LangGraph Server

项目已有 `langgraph.json`，图名称为 `deep_mode_agent`。

```bash
langgraph dev --port 2024
```

### 10.2 启动 Gateway API

确保 `.env` 中配置：

```ini
GATEWAY_PORT=8001
LANGGRAPH_SERVER_URL=http://localhost:2024
DEEP_MODE_AGENT_ID=deep_mode_agent
```

启动：

```bash
python run_gateway.py
```

健康检查：

```bash
curl http://localhost:8001/health
```

### 10.3 启动 Web UI

确保 Web 能访问 Gateway：

```ini
WEB_PORT=8000
GATEWAY_API_URL=http://localhost:8001
```

启动：

```bash
python run_web.py
```

访问 `http://localhost:8000`，进入深度模式相关页面或功能区。

## 11. API 快速使用

服务地址默认是：

```text
http://localhost:8000
```

### 11.1 服务状态

```bash
curl http://localhost:8000/api/status
```

### 11.2 搜索文章

```bash
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d '{
    "source": "人力资源管理",
    "source_platform": "zhihu",
    "max_results": 5,
    "search_mode": "keyword"
  }'
```

### 11.3 处理单篇文章

```bash
curl -X POST http://localhost:8000/api/process \
  -H "Content-Type: application/json" \
  -d '{
    "source_url": "https://www.zhihu.com/question/xxx",
    "source_platform": "zhihu",
    "target_platform": "zhihu_article"
  }'
```

### 11.4 手动处理文本

```bash
curl -X POST http://localhost:8000/api/process_manual \
  -H "Content-Type: application/json" \
  -d '{
    "title": "示例标题",
    "text": "这里粘贴需要改写的正文。",
    "source_platform": "manual",
    "target_platform": "wechat_article"
  }'
```

### 11.5 获取知乎回答

```bash
curl -X POST http://localhost:8000/api/get_answers \
  -H "Content-Type: application/json" \
  -d '{
    "question_url": "https://www.zhihu.com/question/xxx",
    "max_answers": 10
  }'
```

### 11.6 合并多个回答处理

```bash
curl -X POST http://localhost:8000/api/process_multi \
  -H "Content-Type: application/json" \
  -d '{
    "question_url": "https://www.zhihu.com/question/xxx",
    "answer_urls": [
      "https://www.zhihu.com/question/xxx/answer/yyy",
      "https://www.zhihu.com/question/xxx/answer/zzz"
    ],
    "source_platform": "zhihu",
    "target_platform": "wechat_article"
  }'
```

## 12. 常用命令速查

```bash
# 激活环境
conda activate forge

# 安装依赖
pip install -r requirements.txt
playwright install chromium

# 启动 Milvus
docker compose up -d

# 知乎登录
python login_zhihu.py

# 导入知识库
python import_docs.py

# 查询知识库
python query_knowledge.py

# 启动 Web
python run_web.py

# 启动 Gateway
python run_gateway.py

# 启动 LangGraph Server
langgraph dev --port 2024

# 运行测试
pytest
```

## 13. 测试与验证

运行全部测试：

```bash
pytest
```

运行 Web API 测试：

```bash
pytest tests/test_web_api.py
```

运行工作流相关测试：

```bash
pytest tests/test_workflow_nodes.py tests/test_graph_hil.py
```

运行评估系统测试：

```bash
pytest tests/test_evaluation
```

## 14. 目录说明

```text
forge/
  agents/          AI 工作流节点：抓取、改写、审核、发布等
  deep_mode/       深度模式工作流和会话管理
  evaluation/      评估系统
  gateway/         Gateway API
  graph/           LangGraph 工作流
  knowledge/       知识库和向量检索
  storage/         Redis、PostgreSQL 存储客户端
  tools/           爬虫、LLM、视频、发布等工具
  web/             FastAPI Web 应用、模板和静态资源

docs/              项目文档
migrations/        数据库迁移 SQL
scripts/           导入和批处理脚本
tests/             自动化测试
volumes/           Docker 本地数据卷
```

## 15. 常见问题

### 15.1 启动 Web 时报 `WEB_PORT` 错误

原因：`.env` 中没有配置 `WEB_PORT`。

处理：

```ini
WEB_PORT=8000
```

然后重新启动：

```bash
python run_web.py
```

### 15.2 Qwen 调用失败

检查：

- `.env` 是否存在。
- `QWEN_API_KEY` 是否填写。
- `QWEN_API_URL` 是否为 `https://dashscope.aliyuncs.com/compatible-mode/v1`。
- 当前网络是否可以访问百炼 API。
- API Key 是否有对应模型权限。

### 15.3 知乎抓取失败或总是跳登录

处理：

```bash
python login_zhihu.py
```

如果仍失败，可以删除旧缓存后重新登录：

```bash
rm -rf ~/.forge/browser_data/zhihu
python login_zhihu.py
```

### 15.4 Playwright 找不到浏览器

处理：

```bash
playwright install chromium
```

Linux 服务器缺少系统依赖时：

```bash
playwright install-deps chromium
```

### 15.5 Milvus 连接失败

检查 Docker 服务：

```bash
docker compose ps
docker compose logs standalone
```

确认端口：

```bash
curl http://localhost:9091/healthz
```

### 15.6 深度模式无法创建会话

按顺序检查三个服务：

1. LangGraph Server 是否运行在 `http://localhost:2024`。
2. Gateway 是否运行在 `http://localhost:8001`。
3. Web 的 `GATEWAY_API_URL` 是否指向 Gateway。

示例：

```bash
curl http://localhost:8001/health
curl http://localhost:8000/api/status
```

### 15.7 评估 Worker 或 Redis 报错

项目启动 Web 时会尝试启动评估 Worker，并连接 Redis。如果本地没有 Redis，部分评估功能可能不可用，但基础内容处理不一定受影响。

如果需要完整评估功能，请配置并启动 Redis，然后确认 `.env`：

```ini
REDIS_HOST=localhost
REDIS_PORT=6379
EVAL_REDIS_DB=1
```

## 16. 生产部署建议

生产环境建议：

- 使用 `.env.prod` 管理生产配置。
- 使用 `docker-compose.prod.yml` 部署依赖服务。
- 使用 systemd、supervisor 或容器编排托管 Web、Gateway 和 LangGraph Server。
- 给 Web 服务配置反向代理和 HTTPS。
- 不要暴露 Milvus、Redis、PostgreSQL 到公网。
- 对 `.env`、浏览器缓存、输出文件和上传文件做权限控制。
- 使用 LangSmith 追踪关键生产工作流。

生产 Web 启动示例：

```bash
gunicorn forge.web.app:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000
```

## 17. 推荐首次验证路径

第一次部署完成后，建议按以下顺序验证：

1. `docker compose up -d` 启动 Milvus。
2. `python import_docs.py` 导入知识库。
3. `python query_knowledge.py` 验证知识库。
4. `python login_zhihu.py` 完成知乎登录。
5. `python run_web.py` 启动 Web。
6. 打开 `http://localhost:8000`。
7. 用一个简单关键词搜索知乎文章。
8. 选择一篇文章生成改写结果。
9. 编辑并保存结果。
10. 如需深度模式，再启动 `langgraph dev --port 2024` 和 `python run_gateway.py`。
