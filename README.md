# Forge

Forge 是锐博内部使用的对话式事实写作系统。一段对话只处理一篇文章；员工粘贴原文后，系统通过 planner 汇总原文事实、公司数据库、公司历史文档和 Tavily 证据，生成事实清单与逐段大纲。事实与大纲校验后自动进入 generator → critic ⇄ revise，冲突按系统规则处理并记录。

## 当前架构

- Gateway：外部入口、JWT 预校验、限流、请求追踪和 SSE 转发；
- FastAPI：认证、对话、事实 CRUD、自动版本快照和 SSE 事件；
- LangGraph Server：意图、planner、outline、generator、critic、revise、checkpoint 和自动决策；
- RocketMQ：工作流命令、索引命令、重试和死信；
- PostgreSQL + pgvector：业务状态、任务、证据、组织数据、文档和向量；
- Transactional Outbox：保证业务任务与待发送命令在同一事务落库；
- Runtime Consumer：重新解析权限、持久入队、按全局容量提交远程 run，并投影执行结果；
- 运行时 Text2SQL skill：只读 AST 校验、数据库只读角色和 RLS；SQL 失败时可返回相关口径 Markdown 原文，并明确标记为未核实；
- Markdown 路由：关键词先选公司知识资源，未命中时由 `SKILL_ROUTER_MODEL` 小模型选择预设类别；
- 文章类型 skill：Planner 识别原文类型并加载对应规范，贯穿大纲、生成、Critic 和修订；
- 文章事实 skill：先抽取文章主旨和表达重点，再按删除影响及逐字来源筛选核心事实；Tavily 只补充核心事实，公司 pgvector 只补充公司自身说明；planner 与 critic 共用筛选规则；
- React + Vite：统一对话、事实清单和大纲工作区。

浏览器只访问 Gateway。LangGraph Server、RocketMQ Bridge 和 Runtime Consumer 都是内网服务；`workflow_jobs` 保存状态、幂等键、远程 thread/run 映射和审计，并承担消费者接收消息后的持久准入队列。

## 启动

需要 Python 3.11+、Node.js 20+ 和 Docker。

```powershell
Copy-Item .env.example .env
docker compose up -d --build
python -m forge.cli create-user admin@example.com change-this-password --type admin
python -m forge.cli import-seed seed/ruibo_official.json
```

Gateway 地址是 `http://127.0.0.1:8000`，LangGraph Studio/API 调试地址是 `http://127.0.0.1:8123`。本地前端仍使用：

```powershell
Set-Location web
npm install
npm run dev
```

打开 `http://localhost:5173`。

建立公司租户并给员工授权：

```powershell
python -m forge.cli create-tenant 锐博
python -m forge.cli add-member employee@example.com <tenant-id> --role employee
```

为公司知识空间导入经过官方来源核验的人力资源业务口径：

```powershell
python -m forge.cli migrate
python -m forge.cli import-hr-terms seed/hr_business_terms.json <tenant-id>
```

导入命令只接受 `https://*.gov.cn` 官方来源，按稳定编码幂等更新。运行时 Text2SQL 对人力资源口径表只有 `SELECT` 权限；所有记录继续由 `space_id` 和 RLS 隔离。

首次启动容器会执行 `migrations/001_initial.sql`，随后 `migrate` 服务按顺序应用其余迁移。手动初始化可运行 `python -m forge.cli migrate`；迁移记录会阻止重复执行。此基线面向干净数据库，不迁移旧 Forge 数据。

本机不使用 Docker 时，需要分别启动以下进程：

```powershell
python run_api.py
python run_consumer.py
python -m forge.outbox_worker
langgraph dev --host 127.0.0.1 --port 8123 --no-browser
```

RocketMQ Bridge 使用官方 Java 客户端，解决 Python 进程直接依赖非官方 RocketMQ 客户端的问题。Bridge 只接受带内部令牌的发布请求，并用独立共享密钥回调 Runtime Consumer。

数据库使用两个账号：`forge_owner` 只负责迁移、种子与租户授权命令；API、worker 和普通用户命令使用无 RLS 绕过权限的 `forge_app`。生产环境必须替换示例密码。

Windows 本地连接使用 `127.0.0.1`，避免 `localhost` 优先尝试不可达 IPv6 端口造成约 20 秒连接延迟。

工作台支持事实新增、编辑、排除、自动冲突记录、事实与大纲自动通过、正文修订和定稿。定稿会分块写入个人知识空间，并由 worker 生成 pgvector 向量。

## 权限

账号分访客、员工和管理员。公开知识空间可供全部账号查询；公司空间通过授权记录开放；个人空间只属于本人。每次请求重新从数据库解析权限。

agent 生成的 SQL 依次经过允许表字段、函数、单语句和只读 AST 检查，再切换到 `forge_query` 角色及只读事务执行。RLS 负责最终数据隔离。查询失败与查询无结果是不同状态。

公司知识 Markdown 路由优先使用关键词；未命中时调用 `SKILL_ROUTER_MODEL`，其输出只能选择预设类别，无法传入文件路径。分类调用失败会加载全部只读知识资源。文章类型规范位于 `skills/article_types/`，覆盖公司介绍、业务介绍、行业解读与行规、法律合规说明和案例分析；模型无法判断类型时保留原文类型并使用通用规范。

## 验证

```powershell
pytest
$env:FORGE_INTEGRATION_DSN='postgresql://forge_app:forge_app@127.0.0.1:5432/forge'
$env:FORGE_INTEGRATION_OWNER_DSN='postgresql://forge_owner:forge_owner@127.0.0.1:5432/forge'
pytest tests/integration/test_postgres_runtime.py
Set-Location web
npm run typecheck
npm run build
```

设计、删除范围和验收标准见：

- `docs/2026-09-25-forge-rebuild-design.md`
- `docs/2026-09-25-legacy-removal-map.md`

容量、共享限流及监控配置见 [运行说明](docs/runtime-capacity-monitoring.md)。
