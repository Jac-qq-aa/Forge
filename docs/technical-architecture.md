# Forge 技术架构

文章生成任务的 RocketMQ 调度、HITL 恢复、幂等和故障窗口见 [rocketmq-generation-async.md](rocketmq-generation-async.md)。整体高并发任务的部署、配置、扩容和运维查询见 [high-concurrency-async-architecture.md](high-concurrency-async-architecture.md)，线程、协程、Worker 和浏览器池的关键代码说明见 [high-concurrency-code-guide.md](high-concurrency-code-guide.md)。

Forge 只保留深度内容生成流程。Web 层负责页面与请求代理，Gateway 负责校验请求并创建持久化任务，RocketMQ 分发任务，Workflow Worker 创建和恢复 LangGraph 运行，PostgreSQL 保存任务、Outbox、会话与检查点，Redis 用于会话缓存，Milvus 提供知识检索。

## 工作流

```text
START
  → RAG 检索
  → 事实研究
  → 等待事实审核
  → 生成大纲
  → 等待大纲审核
  → 正文生成与质量迭代
  → 等待内容确认或微调
  → 定稿
  → END
```

唯一生产工作流定义在 `forge/deep_mode/graph_hil.py`，由 `forge/deep_mode/server_graph.py` 编译。每个会话使用稳定的 `thread_id`，并通过 PostgreSQL checkpointer 保存状态。人工审核节点使用 LangGraph interrupt 暂停；恢复请求使用同一 `thread_id` 继续执行。

## 运行模块

| 模块 | 职责 |
|---|---|
| `forge/web/app.py` | 页面、异步素材任务提交、深度流程代理、历史记录与产物下载 |
| `forge/gateway/app.py` | 深度工作流任务提交、任务状态查询和追踪上下文 |
| `forge/async_jobs/` | PostgreSQL 任务与 Outbox、RocketMQ 适配器和消费者租约 |
| `forge/scraping/` | 知乎浏览器池、Redis 全局限速、缓存和采集任务执行 |
| `forge/deep_mode/graph_hil.py` | 深度生成状态与节点编排 |
| `forge/deep_mode/server_graph.py` | LangGraph Server 入口与 PostgreSQL checkpointer |
| `forge/deep_mode/session_manager.py` | 会话业务状态的持久化与查询 |
| `forge/agents/research_agent.py` | 事实研究、证据归一化和覆盖检查 |
| `forge/agents/reflection_writer.py` | 正文生成、批评和修订循环 |
| `forge/evaluation/article_queue.py` | 定稿后投递表达质量评估事件 |

## 主要接口

| 接口 | 用途 |
|---|---|
| `POST /api/deep_mode/create_session` | 创建深度生成会话并运行到第一个审核点 |
| `POST /api/deep_mode/fact_action` | 提交事实审核或补充研究请求 |
| `POST /api/deep_mode/outline_action` | 确认或修改大纲 |
| `POST /api/deep_mode/finalize` | 保存最终正文 |
| `GET /api/deep_mode/session/{session_id}` | 查询会话状态 |
| `WS /ws/deep_mode/{session_id}` | 正文微调的双向通道 |

素材搜索和知乎回答筛选属于输入准备能力，不再直接触发旧的一次性改写流程。选中的文章、手动输入或多个回答都会进入同一深度生成会话。

## 状态与持久化

LangGraph checkpoint 保存节点执行状态；业务会话表保存页面需要展示的阶段、事实、大纲、草稿和定稿。常见阶段包括：

- `planning`
- `waiting_facts`
- `waiting_outline`
- `tuning`
- `completed`
- `cancelled`

定稿后的表达质量评估通过 `article_finalized` 事件异步执行，不再由旧工作流节点触发。

## 异步执行链路

```text
Browser → Web → Gateway → PostgreSQL(workflow_jobs + workflow_outbox)
                              ↓
                       Outbox Dispatcher
                              ↓
                         RocketMQ 5.x
                              ↓
                 Workflow Worker → LangGraph Server
                              ↓
                     PostgreSQL(job result)
                              ↑
              Browser SSE GET /api/jobs/{id}/events
```

Gateway 在同一个数据库事务中写入任务和 Outbox，随后以 `202 Accepted` 返回 `job_id`。Outbox 发布器使用 `FOR UPDATE SKIP LOCKED` 并行领取事件，发布成功后才记录 `published_at`。发布进程在发送后、落库前崩溃会造成重复消息，因此消费者不能假设消息只出现一次。

Workflow Worker 通过数据库租约领取任务。同一 `session_id` 同时只运行一个任务；消息重复投递时，已完成任务直接 ACK，正在执行的任务等待消息重新可见。工作流结果写入任务终态后才 ACK。失败任务在 RocketMQ 不可见时间结束后重新投递，达到 `JOB_MAX_ATTEMPTS` 后进入 `failed` 终态并 ACK。

`Idempotency-Key` 在租户内唯一。客户端重试同一个提交请求时必须复用该值，Gateway 会返回同一任务。租户身份来自签名 JWT 的 `tenant_id`，任务与会话查询都会校验租户所有权。

## 扩容

Gateway、Web、Outbox 发布器和 Workflow Worker 都不保存本地任务状态。Workflow Worker 可以按模型限额水平扩容：

```bash
docker compose -f docker-compose.prod.yml up -d --scale workflow-worker=8
```

单个 Worker 的并发由 `JOB_CONSUMER_CONCURRENCY` 控制。集群总并发必须低于 LangGraph Server、模型供应商和 PostgreSQL 的实际容量；增加容器数不会绕过上游限流。当前生产编排包含一个 RocketMQ broker，正式多机部署应把 nameserver、broker 和 proxy 迁到独立的 RocketMQ 集群。

## 知乎采集执行池

知乎搜索、博主文章、文章详情和问题回答使用独立的 `forge-zhihu-jobs` Topic 与 `forge-zhihu-workers` 消费组。Web 只提交任务，不在请求进程里启动 Chromium，因此浏览器崩溃或知乎响应变慢不会占满 Web 工作线程。

每个 Zhihu Worker 包含固定容量的长驻浏览器池。一个池槽位对应独立的 Playwright persistent profile 和一个串行页面，任务结束后浏览器继续复用；页面异常时只销毁对应槽位，下个任务再延迟创建。池容量限制单实例 Chromium 数量，`ZHIHU_JOB_CONCURRENCY` 限制消费者并发，两者通常设置为相同值。

所有实例在领取浏览器前使用 Redis 令牌桶执行全局限速。默认速率为每秒 `0.5` 个请求、突发容量为 `2`，避免水平扩容后同时冲击知乎。相同采集请求在 30 秒内复用同一个持久化任务，成功结果在 Redis 缓存 300 秒。文章详情和问题回答只允许访问 `zhihu.com` 及其子域名。

知乎 Worker 可以独立扩容：

```bash
docker compose -f docker-compose.prod.yml up -d --scale zhihu-worker=3
```

扩容时全局请求速率保持不变；增加实例主要用于隔离浏览器故障、提高可用槽位和处理等待队列。若要提高实际抓取吞吐，需要同步调整账号、出口代理、令牌桶速率和知乎风控预算。
