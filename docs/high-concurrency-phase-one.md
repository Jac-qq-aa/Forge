# 高并发第一阶段加固说明

本阶段解决高并发上线前的正确性和失控风险，包含有界消费、会话互斥、租户认证与配额、SSE 状态推送、Outbox 并发发布及死信重放。

## 1. 有界消费与背压

消费者的内存中任务上限为：

```text
max_in_flight = JOB_CONSUMER_CONCURRENCY + JOB_CONSUMER_PREFETCH
```

只有存在空闲 in-flight 容量时才继续从 RocketMQ 拉取消息，单次拉取数量也不会超过剩余容量。默认 `JOB_CONSUMER_PREFETCH=0`，表示消息进入进程后立即占用一个执行槽位，不在内存中形成无界等待队列。

建议先保持：

```dotenv
JOB_CONSUMER_CONCURRENCY=4
JOB_CONSUMER_PREFETCH=0
```

完成压测并确认消息可见时间充足后，可以将预取调整为并发数的 25%～50%。

## 2. 同会话严格串行

`claim_job()` 在领取带 `session_id` 的任务时，先尝试获取以会话 ID 为键的 PostgreSQL 事务级 advisory lock。只有一个领取事务能够进入会话运行状态；其他领取者返回 `busy`，并延迟消息后重试。

会话创建成功后，Worker 在 `workflow_session_owners` 中登记 `session_id → tenant_id + owner_subject`。后续大纲、事实审核、定稿、历史读取和 WebSocket 连接都必须属于同一租户和用户。

数据库变更位于：

```text
migrations/012_async_job_phase_one.sql
```

## 3. JWT 身份认证

除健康检查和 Web 首页外，所有 `/api/*` 请求都需要 JWT。租户身份只读取 Token 中的 `tenant_id`，不再信任客户端提供的 `X-Tenant-ID`。

必填配置：

```dotenv
FORGE_JWT_SECRET=至少32字节的随机密钥
FORGE_JWT_ISSUER=forge-auth
FORGE_JWT_AUDIENCE=forge-api
```

Token 必须包含：

```json
{
  "sub": "user-id",
  "tenant_id": "tenant-id",
  "scope": "jobs",
  "iat": 1789600000,
  "exp": 1789603600,
  "iss": "forge-auth",
  "aud": "forge-api"
}
```

接口客户端使用：

```http
Authorization: Bearer <token>
```

浏览器使用名为 `forge_access_token` 的 Cookie。生产环境应由登录系统或身份网关设置 `Secure; HttpOnly; SameSite=Lax` Cookie。Forge 当前只验证令牌，不负责用户注册、密码登录或令牌签发。

本地联调可以生成令牌：

```bash
python scripts/create_access_token.py \
  --subject user-1 \
  --tenant tenant-1 \
  --scope "jobs admin" \
  --ttl 3600
```

`admin` 权限用于死信查看、重放和管理端评估接口。

## 4. 租户配额

```dotenv
TENANT_SUBMISSIONS_PER_MINUTE=60
USER_SUBMISSIONS_PER_MINUTE=20
TENANT_MAX_ACTIVE_JOBS=10
USER_MAX_ACTIVE_JOBS=3
```

- 每分钟提交量由 Redis Lua 脚本分别按租户和用户原子计数，任一超过后返回 `429` 和 `Retry-After`。
- 活跃任务指 `queued` 和 `running`。提交事务使用租户和用户 advisory lock，确保并发提交不会同时越过上限。
- Redis 不可用时提交返回 `503`，防止配额系统故障后所有请求无限进入队列。
- 幂等重试会返回已有任务，不额外占用活跃任务名额，但仍计入入口请求频率。

## 5. SSE 任务状态

前端提交任务后连接：

```http
GET /api/jobs/{job_id}/events
Accept: text/event-stream
```

Worker 状态变化时向 Redis Pub/Sub 发布轻量通知。每个 Web/Gateway 进程只维护一个 `job-events:*` Redis 模式订阅，再通过进程内队列把消息分发给各 SSE 连接，因此 Redis 连接数随应用实例数增长，不随在线任务数增长。SSE 收到通知后从 PostgreSQL 读取该用户的最新任务真值，并发送 `job` 事件。终态包括 `succeeded`、`failed` 和 `cancelled`，发送后连接关闭。

连接每 15 秒发送注释心跳，不产生 PostgreSQL 查询。客户端断线重连时会重新读取一次当前状态，因此不会依赖历史 Pub/Sub 消息。

原来的 1～5 秒数据库轮询已经从前端主流程删除，`GET /api/jobs/{job_id}` 继续保留给人工查询和降级处理。

## 6. Outbox 并发与死信

```dotenv
OUTBOX_BATCH_SIZE=100
OUTBOX_PUBLISH_CONCURRENCY=16
OUTBOX_LOCK_SECONDS=120
OUTBOX_MAX_ATTEMPTS=10
```

一个批次内最多同时发布 `OUTBOX_PUBLISH_CONCURRENCY` 条消息。发布失败后执行指数退避；达到最大尝试次数后设置 `dead_lettered_at`，不再由正常发布器领取。

具有 `admin` 权限的运维人员可以查看死信：

```http
GET /api/admin/outbox/dead-letters?limit=100
```

确认故障已经修复后重放：

```http
POST /api/admin/outbox/dead-letters/{outbox_id}/replay
```

重放会清空尝试次数和错误，将记录重新放回待发布队列。任务执行仍受终态检查、幂等键和租约保护。

## 7. 上线步骤

1. 生成并安全保存至少 32 字节的 `FORGE_JWT_SECRET`。
2. 配置身份网关，使 Web 浏览器获得 `forge_access_token` HttpOnly Cookie。
3. 执行迁移服务，应用 `012_async_job_phase_one.sql`。
4. 先启动 PostgreSQL、Redis 和 RocketMQ，再启动 Outbox、Worker、Gateway 和 Web。
5. 使用一个普通 Token 验证提交、SSE 和租户隔离。
6. 使用一个 `admin` Token 验证死信查询权限。
7. 逐步压测并观察 in-flight、Outbox 积压、SSE 连接数和 Redis 延迟。

## 8. 关键边缘情况

| 场景 | 行为 |
|---|---|
| 未携带或携带过期 JWT | 返回 `401` |
| JWT 配置缺失或密钥过短 | 返回 `503` |
| 跨租户或同租户跨用户访问任务、会话 | 返回 `404`，不暴露资源是否存在 |
| 租户每分钟提交超限 | 返回 `429` 和 `Retry-After` |
| 租户或用户活跃任务达到上限 | 返回 `429` |
| Redis 配额存储不可用 | 新任务提交返回 `503` |
| SSE 连接断开 | 浏览器自动重连，服务端重新发送当前状态 |
| Worker 崩溃 | 租约到期后任务重新领取 |
| Outbox 连续发布失败 | 达到上限后进入死信，不再无限重试 |
| 重放已经执行成功的任务消息 | Worker 识别终态后直接 ACK |

## 9. 相关代码

| 文件 | 作用 |
|---|---|
| `forge/security/auth.py` | JWT 验证、租户身份和 admin 权限 |
| `forge/security/quota.py` | Redis 原子提交限流 |
| `forge/async_jobs/repository.py` | 活跃任务配额、会话锁、租户所有权和死信 |
| `forge/async_jobs/worker.py` | 有界 in-flight、状态事件和并发 Outbox |
| `forge/async_jobs/events.py` | Redis 通知和 SSE 事件流 |
| `scripts/create_access_token.py` | 本地联调令牌生成工具 |
| `migrations/012_async_job_phase_one.sql` | 第一阶段数据库结构 |
