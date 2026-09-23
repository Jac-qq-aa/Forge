# Forge 高并发异步架构实施说明

第二阶段生产集群、对象存储、KEDA 与容量验收说明见 [高并发第二阶段：生产集群与容量治理](high-concurrency-phase-two.md)。

结合关键代码理解线程、协程、Worker 和浏览器池时，请阅读 [多线程与高并发处理说明](high-concurrency-code-guide.md)。

第一阶段的有界消费、会话锁、JWT 配额、SSE 和 Outbox 死信实现见 [高并发第一阶段加固说明](high-concurrency-phase-one.md)。

## 目标与完成状态

Forge 已将长耗时的内容生成和知乎采集从 Web 请求线程迁移到持久化异步任务。当前实现覆盖：

- 深度工作流创建、事实审核、大纲操作和定稿异步执行。
- 知乎搜索、博主文章、文章详情和问题回答异步执行。
- PostgreSQL 任务状态、执行租约和事务 Outbox。
- RocketMQ 5.x 双 Topic 分发。
- 工作流 Worker 与知乎 Worker 独立扩容。
- 知乎长驻浏览器池、Redis 全局限速、结果缓存和请求合并。
- 前端统一使用 `job_id` 查询任务结果。

快速模式及其旧工作流、路由、界面和数据库迁移已删除。所有内容生成统一进入深度生成流程。

## 总体架构

```text
                                         ┌───────────────────────┐
Browser ──▶ Web ──▶ Gateway ────────────▶│ PostgreSQL            │
   │          │                           │ workflow_jobs         │
   │          └─ 知乎任务直接提交 ───────▶│ workflow_outbox       │
   │                                      └───────────┬───────────┘
   │                                                  │
   │                                          Outbox Dispatcher
   │                                                  │
   │                         ┌────────────────────────┴─────────────────────┐
   │                         ▼                                              ▼
   │              forge-workflow-jobs                           forge-zhihu-jobs
   │                         │                                              │
   │                  Workflow Worker                                Zhihu Worker
   │                         │                                   ┌──────────┴──────────┐
   │                  LangGraph Server                           Redis            浏览器池
   │                         │                              限速/缓存          Playwright
   │                         └────────────────────────┬───────────────────────────────┘
   │                                                  ▼
   └────── SSE GET /api/jobs/{job_id}/events ◀── Redis 通知 + PostgreSQL 任务结果
```

Web、Gateway 和 Worker 不保存本地任务状态。进程重启后，任务状态、重试次数和 Outbox 事件仍保存在 PostgreSQL 中。

## 请求与执行流程

### 1. 提交任务

工作流接口和知乎接口在创建任务后立即返回：

```json
{
  "job_id": "uuid",
  "operation": "create_session",
  "status": "queued",
  "deduplicated": false,
  "status_url": "/api/jobs/uuid"
}
```

HTTP 状态码为 `202 Accepted`。任务记录和 Outbox 事件在同一个 PostgreSQL 事务中写入，避免出现任务已创建但消息永久丢失的情况。

### 2. 发布消息

Outbox Dispatcher 使用 `FOR UPDATE SKIP LOCKED` 批量领取尚未发布的事件。RocketMQ 发送成功后才写入 `published_at`；发送失败时按照指数退避再次尝试。

发布成功但 `published_at` 尚未写入时进程崩溃，可能产生重复消息。因此消费者按至少一次投递设计，不能依赖消息只出现一次。

### 3. 消费与租约

消费者先在 PostgreSQL 中领取任务，再执行实际操作：

- `queued` 任务进入 `running`。
- 领取时生成唯一 `lease_token`。
- 长任务周期性续期数据库租约和 RocketMQ 消息不可见时间。
- 同一 `session_id` 同时只允许一个工作流任务运行。
- 任务结果成功落库后才 ACK RocketMQ 消息。
- 重复投递遇到 `succeeded`、`failed` 或 `cancelled` 任务时直接 ACK。

默认失败重试 3 次。可重试失败在 10 秒后重新可见；同一会话正忙时在 5 秒后重新尝试。超过最大次数后任务进入 `failed` 终态。

### 4. 订阅和查询结果

前端通过 `GET /api/jobs/{job_id}/events` 订阅 SSE 状态，`GET /api/jobs/{job_id}` 保留为单次查询接口：

| 状态 | 含义 |
|---|---|
| `queued` | 已持久化，等待发布或消费 |
| `running` | Worker 已领取并持有有效租约 |
| `succeeded` | 结果位于 `result` 字段 |
| `failed` | 错误位于 `error` 字段 |
| `cancelled` | 任务已取消 |

状态变化由 Redis Pub/Sub 通知，SSE 收到通知后读取 PostgreSQL 真值。连接心跳不会查询数据库，用户关闭页面后后台任务继续执行。

## RocketMQ 资源划分

| 资源 | Topic | 消费组 | 执行内容 |
|---|---|---|---|
| 深度工作流 | `forge-workflow-jobs` | `forge-workflow-workers` | 创建会话、事实审核、大纲操作、定稿 |
| 知乎采集 | `forge-zhihu-jobs` | `forge-zhihu-workers` | 搜索、博主文章、文章详情、问题回答 |

两类任务分开部署和扩容。浏览器页面阻塞不会占用内容生成 Worker，模型供应商响应变慢也不会阻塞知乎采集队列。

## PostgreSQL 数据模型

### `workflow_jobs`

保存任务输入、状态、结果、错误、重试次数和执行租约。核心约束包括：

- `(tenant_id, idempotency_key)` 唯一。
- `request_hash` 防止同一个幂等键用于不同请求。
- `session_id` 用于串行化同一 LangGraph 会话的操作。
- `lease_token` 防止过期 Worker 覆盖新 Worker 的结果。

### `workflow_outbox`

保存待投递的 RocketMQ 事件、发布锁、失败次数和下次可用时间。Outbox 已发布记录默认保留 24 小时，终态任务默认保留 7 天。

数据库迁移位于 `migrations/011_async_workflow_jobs.sql`，容器启动时由 `migrate` 服务执行。

## 幂等与重复执行

工作流客户端应为一次逻辑操作生成一个 `Idempotency-Key`，网络重试时复用该值。同一个键和相同请求返回原任务；同一个键对应不同请求时返回 `409 Conflict`。

知乎采集由服务端根据操作和参数生成请求摘要：

- 30 秒内的相同采集请求复用同一个任务。
- 成功结果在 Redis 缓存 300 秒。
- 缓存命中时不会启动 Chromium。

系统提供消息消费幂等和任务终态幂等。LangGraph 远程运行与 PostgreSQL 任务终态不在同一个事务内，因此在远程运行已经完成、任务终态尚未写入的极小崩溃窗口中，仍可能重新调用 LangGraph。后续可持久化远程 `run_id` 并在重试时重新 join 原运行，进一步收窄该窗口。

## 知乎浏览器池

每个 Zhihu Worker 包含固定容量的长驻浏览器池：

- 一个槽位对应一个独立 Playwright persistent profile。
- 一个槽位同时只执行一个页面任务。
- 浏览器在任务结束后继续复用，避免每次启动 Chromium。
- 页面崩溃、登录跳转或人机验证只回收对应槽位。
- 下一个任务使用该槽位时再创建浏览器。
- 文章详情和问题回答只允许访问 `zhihu.com` 及其子域名。
- 单次搜索最多返回 20 条，问题回答最多 50 条。
- 推荐接口最多处理 50 个博主，每个博主最多 10 条内容。

浏览器池控制单实例资源量，Redis 令牌桶控制整个集群的知乎请求速率。默认全局速率为每秒 0.5 个请求，突发容量为 2。增加 Worker 数量不会自动提高全局访问频率。

## 配置参数

### PostgreSQL 与任务

| 环境变量 | 默认值 | 说明 |
|---|---:|---|
| `PG_POOL_MIN_SIZE` | `2` | 每个进程的最小连接数 |
| `PG_POOL_MAX_SIZE` | `10` | 每个进程的最大连接数 |
| `JOB_CONSUMER_CONCURRENCY` | `4` | 单个工作流 Worker 并发 |
| `JOB_LEASE_SECONDS` | `1800` | 数据库执行租约时长 |
| `JOB_MAX_ATTEMPTS` | `3` | 最大执行次数 |
| `JOB_BUSY_RETRY_SECONDS` | `5` | 会话忙碌时的重试间隔 |
| `JOB_FAILURE_RETRY_SECONDS` | `10` | 可重试失败的重试间隔 |
| `JOB_RETENTION_DAYS` | `7` | 终态任务保留天数 |
| `OUTBOX_RETENTION_HOURS` | `24` | 已发布 Outbox 保留小时数 |

### RocketMQ

| 环境变量 | 默认值 |
|---|---|
| `ROCKETMQ_ENDPOINTS` | `rocketmq:8081`（容器内） |
| `ROCKETMQ_WORKFLOW_TOPIC` | `forge-workflow-jobs` |
| `ROCKETMQ_WORKFLOW_CONSUMER_GROUP` | `forge-workflow-workers` |
| `ROCKETMQ_ZHIHU_TOPIC` | `forge-zhihu-jobs` |
| `ROCKETMQ_ZHIHU_CONSUMER_GROUP` | `forge-zhihu-workers` |

### 知乎采集

| 环境变量 | 默认值 | 说明 |
|---|---:|---|
| `ZHIHU_JOB_CONCURRENCY` | `4` | 单个知乎 Worker 消费并发 |
| `ZHIHU_BROWSER_POOL_SIZE` | `4` | 单个 Worker 浏览器槽位数 |
| `ZHIHU_GLOBAL_REQUESTS_PER_SECOND` | `0.5` | 集群全局请求速率 |
| `ZHIHU_GLOBAL_BURST` | `2` | 令牌桶突发容量 |
| `ZHIHU_RESULT_CACHE_SECONDS` | `300` | 成功结果缓存时间 |
| `ZHIHU_REQUEST_DEDUP_SECONDS` | `30` | 相同请求任务合并窗口 |

完整示例见项目根目录 `.env.example`。

## 部署与扩容

生产编排包含 PostgreSQL、Redis、RocketMQ nameserver、带 gRPC Proxy 的 RocketMQ broker、迁移服务、Outbox、Gateway、Web、LangGraph Server 和两类 Worker。

```bash
# 启动完整环境
docker compose -f docker-compose.prod.yml up -d

# 按模型额度扩容内容生成
docker compose -f docker-compose.prod.yml up -d --scale workflow-worker=8

# 按浏览器资源扩容知乎采集
docker compose -f docker-compose.prod.yml up -d --scale zhihu-worker=3
```

扩容前应核对：

1. PostgreSQL 最大连接数是否覆盖所有进程的连接池上限。
2. LangGraph Server 和模型供应商的并发及速率限制。
3. Zhihu Worker 的内存、共享内存和 Chromium 数量。
4. Redis 令牌桶速率、账号数量、出口代理和知乎风控预算。
5. RocketMQ broker 的队列数、磁盘水位和消费积压。

当前 Compose 使用单个 RocketMQ broker，适用于单机部署和功能验证。正式多机生产环境应使用独立 RocketMQ 集群，并为 nameserver、broker 和 proxy 配置高可用及监控。

## 运维查询

查询任务积压：

```sql
SELECT status, operation, COUNT(*)
FROM workflow_jobs
GROUP BY status, operation
ORDER BY status, operation;
```

查询等待时间较长的任务：

```sql
SELECT id, operation, status, attempt_count, created_at
FROM workflow_jobs
WHERE status IN ('queued', 'running')
ORDER BY created_at
LIMIT 100;
```

查询待发布 Outbox：

```sql
SELECT topic, COUNT(*), MIN(created_at) AS oldest
FROM workflow_outbox
WHERE published_at IS NULL
GROUP BY topic;
```

建议监控以下指标：任务入队速率、排队时长、执行时长、失败率、重试次数、Outbox 积压、RocketMQ 消费延迟、浏览器池使用率、Redis 限速等待时间和知乎验证触发率。

## 故障行为

| 故障 | 系统行为 |
|---|---|
| RocketMQ 暂时不可用 | Outbox 保留事件并持续重试 |
| Outbox 发布后进程崩溃 | 可能重复投递，消费者按任务终态去重 |
| Worker 执行中崩溃 | 租约和消息不可见时间到期后重新领取 |
| PostgreSQL 不可用 | 新任务返回 `503`，已运行 Worker 无法提交结果 |
| Redis 不可用 | 知乎 Worker 使用进程内限速，结果缓存暂停 |
| Chromium 页面崩溃 | 回收单个槽位，任务按失败策略重试 |
| 知乎登录或人机验证 | 回收槽位并重试；超过次数后任务失败 |
| 用户关闭浏览器页面 | 后台任务继续执行，可凭 `job_id` 查询 |

## 验证结果

本次实现已完成以下检查：

- 异步提交、幂等键传递和任务状态接口。
- 成功 ACK、失败不 ACK、终态重复消息 ACK。
- 会话忙碌短间隔重试和消息可见时间调整。
- 浏览器池容量、浏览器复用和故障槽位回收。
- 知乎域名校验、登录跳转识别和缓存短路。
- 知乎任务进入独立 Topic。
- PostgreSQL 存储回归测试。
- Python 编译、前端 JavaScript 语法和 Docker Compose 配置检查。

相关测试共 31 个，全部通过。当前开发机 Docker 守护进程未启动，因此真实 PostgreSQL、RocketMQ、Redis 和 Chromium 容器联调需在可用的 Docker 环境中执行。

## 关键代码位置

| 文件 | 作用 |
|---|---|
| `forge/async_jobs/repository.py` | 任务、租约、Outbox 和清理 |
| `forge/async_jobs/rocketmq.py` | RocketMQ 5.x SDK 适配 |
| `forge/async_jobs/worker.py` | Outbox、工作流消费者和知乎消费者 |
| `forge/async_jobs/workflow_executor.py` | 工作流任务分派 |
| `forge/scraping/rate_limit.py` | Redis 分布式令牌桶 |
| `forge/scraping/zhihu_pool.py` | 长驻浏览器池 |
| `forge/scraping/zhihu_executor.py` | 知乎操作、缓存和结果规范化 |
| `migrations/011_async_workflow_jobs.sql` | 异步任务数据库结构 |
| `docker-compose.prod.yml` | 完整服务编排和扩容参数 |
