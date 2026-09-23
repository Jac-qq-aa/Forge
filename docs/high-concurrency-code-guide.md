# Forge 多线程与高并发处理说明

Kubernetes、多副本扩容、大结果对象存储和 Prometheus 实现见 [高并发第二阶段：生产集群与容量治理](high-concurrency-phase-two.md)。

本文结合当前项目代码，说明 Forge 在大量用户同时提交深度工作流和知乎采集任务时，如何拆分请求、排队、并发执行、限流、重试和恢复。

相关总体设计见 [高并发异步架构实施说明](high-concurrency-async-architecture.md)。本文更关注运行时并发模型和关键代码。

## 1. 当前并发模型

Forge 没有采用传统的“每个请求一个业务线程”，而是使用三层并发：

1. **进程和容器级并发**：Web、Gateway、Outbox、Workflow Worker、Zhihu Worker 分开运行，Worker 可以水平扩容。
2. **协程级并发**：每个 Worker 在 Python `asyncio` 事件循环中同时执行多个 I/O 任务，并由信号量限制任务数。
3. **线程级适配**：RocketMQ Python SDK 是同步接口，使用 `asyncio.to_thread()` 把启动、拉取、发送和 ACK 放到默认线程池，避免阻塞事件循环。

所以当前“多线程高并发”的准确含义是：**多进程承载、协程调度为主、少量线程包装阻塞 SDK**。

```text
大量用户请求
    │
    ▼
Web / Gateway 异步接口
    │  只校验并持久化任务，返回 202
    ▼
PostgreSQL: workflow_jobs + workflow_outbox
    │
    ▼
Outbox Dispatcher ──▶ RocketMQ
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
     Workflow Worker         Zhihu Worker
     asyncio.Semaphore       asyncio.Semaphore
              │                     │
         LangGraph/LLM       Redis 限流 + 浏览器池
```

## 2. HTTP 请求如何卸载长任务

深度工作流接口不等待 LangGraph 执行结束。Gateway 只创建任务，然后立即返回 `202 Accepted` 和 `job_id`。

关键代码位于 `forge/gateway/app.py`：

```python
async def _enqueue_workflow_operation(
    operation: str,
    request: BaseModel,
    idempotency_key: Optional[str],
    tenant_id: str,
):
    key = idempotency_key or str(uuid4())
    job, created = await submit_job(
        operation=operation,
        payload=request.model_dump(),
        tenant_id=tenant_id,
        idempotency_key=key,
    )

    return JSONResponse(
        status_code=202,
        content={
            "job_id": job["id"],
            "status": job["status"],
            "deduplicated": not created,
            "status_url": f"/api/jobs/{job['id']}",
        },
    )
```

Web/Gateway 连接不会被几十秒或几分钟的生成任务长期占用。流量峰值先进入数据库和消息队列，Worker 再按自身容量平稳消费。

前端拿到 `job_id` 后建立 SSE 连接，由服务端推送状态变化：

```javascript
const events = new EventSource(`/api/jobs/${data.job_id}/events`, {
    withCredentials: true,
});
events.addEventListener('job', event => {
    const job = JSON.parse(event.data);
    if (job.status === 'succeeded') {
        events.close();
        resolve(job.result);
    }
});
```

Worker 通过 Redis Pub/Sub 通知 SSE 连接；每个应用进程复用一个 Redis 模式订阅，在进程内分发给各连接。状态变化时才读取一次 PostgreSQL，15 秒心跳不查询数据库。用户关闭页面后，服务端任务仍会继续执行。

## 3. Transactional Outbox 保证任务不丢

如果先写任务再发 RocketMQ，进程可能在两步之间崩溃，形成“数据库有任务，但队列没有消息”的孤儿任务。当前实现在同一个 PostgreSQL 事务中写入任务和待发布消息。

关键代码位于 `forge/async_jobs/repository.py`：

```python
async with pool.acquire() as conn:
    async with conn.transaction():
        row = await conn.fetchrow(
            """
            INSERT INTO workflow_jobs (
                id, tenant_id, idempotency_key, request_hash,
                operation, session_id, status, payload, max_attempts
            ) VALUES ($1, $2, $3, $4, $5, $6, 'queued', $7::jsonb, $8)
            ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
            RETURNING *
            """,
            ...,
        )

        if row is not None:
            await conn.execute(
                """
                INSERT INTO workflow_outbox (job_id, topic, message_key, payload)
                VALUES ($1, $2, $3, $4::jsonb)
                """,
                job_id, topic, str(job_id), json.dumps({"job_id": str(job_id)}),
            )
```

只有事务提交成功，任务和 Outbox 才会同时可见。RocketMQ 暂时不可用时，任务仍保存在数据库中，Outbox 发布器稍后重试。

多个 Outbox 实例使用 `FOR UPDATE SKIP LOCKED` 领取不同批次：

```sql
WITH candidates AS (
    SELECT id
    FROM workflow_outbox
    WHERE published_at IS NULL
      AND available_at <= NOW()
      AND (locked_until IS NULL OR locked_until < NOW())
    ORDER BY id
    FOR UPDATE SKIP LOCKED
    LIMIT $2
)
UPDATE workflow_outbox AS outbox
SET locked_by = $1,
    locked_until = NOW() + INTERVAL '30 seconds'
FROM candidates
WHERE outbox.id = candidates.id
RETURNING outbox.*;
```

当前单个 Outbox 进程逐条发布已领取的消息。积压较大时可增加 Outbox 实例，数据库锁会防止实例领取同一行。若发布端成为持续瓶颈，再改为有上限的并发批量发送。

## 4. RocketMQ 如何接入线程池

RocketMQ 5.x Python SDK 提供同步阻塞调用。直接调用会暂停同一事件循环内的所有协程，所以适配层统一使用 `asyncio.to_thread()`：

```python
class RocketMQConsumer:
    async def receive(self, batch_size: int, invisible_seconds: int) -> list[Any]:
        messages = await asyncio.to_thread(
            self._consumer.receive,
            batch_size,
            invisible_seconds,
        )
        return list(messages or [])

    async def acknowledge(self, message: Any) -> None:
        await asyncio.to_thread(self._consumer.ack, message)

    async def extend_visibility(self, message: Any, invisible_seconds: int) -> None:
        await asyncio.to_thread(
            self._consumer.change_invisible_duration,
            message,
            invisible_seconds,
        )
```

这些线程只等待 RocketMQ 网络 I/O，不保存业务状态。可靠状态仍由 PostgreSQL 管理。

| 任务类型 | Topic | 消费组 | 主要瓶颈 |
|---|---|---|---|
| 深度工作流 | `forge-workflow-jobs` | `forge-workflow-workers` | LangGraph、模型服务、数据库 |
| 知乎采集 | `forge-zhihu-jobs` | `forge-zhihu-workers` | Chromium、网络、知乎风控 |

两类任务隔离后，知乎页面卡顿不会占满内容生成 Worker，模型服务变慢也不会阻塞知乎任务。

## 5. Worker 如何限制单进程并发

Worker 为拉取到的每条消息创建协程，但所有任务必须先通过 `asyncio.Semaphore`：

```python
async def run_consumer(..., concurrency: int, ...):
    semaphore = asyncio.Semaphore(concurrency)
    running: set[asyncio.Task] = set()

    while not stop.is_set():
        messages = await consumer.receive(
            JOB_CONSUMER_BATCH_SIZE,
            JOB_MESSAGE_INVISIBLE_SECONDS,
        )
        for message in messages:
            task = asyncio.create_task(
                _handle_message(
                    message, consumer, repository, worker_id,
                    semaphore, executor, resource_name,
                )
            )
            running.add(task)
            task.add_done_callback(running.discard)
```

执行入口再次限制并发：

```python
async def _handle_message(..., semaphore: asyncio.Semaphore, ...):
    async with semaphore:
        claim = await repository.claim_job(
            job_id, worker_id, JOB_LEASE_SECONDS,
        )
        result = await executor(claim.job)
```

默认配置：

```text
JOB_CONSUMER_BATCH_SIZE=4
JOB_CONSUMER_CONCURRENCY=4
ZHIHU_JOB_CONCURRENCY=4
```

理论执行槽位按以下公式估算：

```text
Workflow 总并发 = workflow-worker 实例数 × JOB_CONSUMER_CONCURRENCY

单实例知乎并发 = min(ZHIHU_JOB_CONCURRENCY, ZHIHU_BROWSER_POOL_SIZE)
集群浏览器槽位 = zhihu-worker 实例数 × ZHIHU_BROWSER_POOL_SIZE
```

真实吞吐还受 Redis 全局令牌桶、模型供应商配额、PostgreSQL 连接池和机器资源约束。

## 6. 重复消息和 Worker 崩溃如何恢复

RocketMQ 使用至少一次投递时，同一消息可能被重复消费。Worker 先在 PostgreSQL 中领取任务，再开始执行：

```python
row = await conn.fetchrow(
    "SELECT * FROM workflow_jobs WHERE id = $1 FOR UPDATE",
    UUID(job_id),
)

if row["status"] in TERMINAL_STATUSES:
    return JobClaim("terminal")

if row["status"] == "running" and row["lease_expires_at"] > now:
    return JobClaim("busy")

claimed = await conn.fetchrow(
    """
    UPDATE workflow_jobs
    SET status = 'running',
        attempt_count = attempt_count + 1,
        lease_owner = $2,
        lease_token = $3,
        lease_expires_at = NOW() + ($4 * INTERVAL '1 second')
    WHERE id = $1
    RETURNING *
    """,
    ...,
)
```

处理规则：

- 任务已成功、失败或取消：直接 ACK 重复消息。
- 任务被其他 Worker 执行且租约有效：延长消息不可见时间，稍后再试。
- Worker 崩溃：数据库租约和消息不可见时间到期后，其他 Worker 可以重新领取。
- 执行失败但未达到 `max_attempts`：任务回到 `queued`，消息延迟后重试。
- 达到最大次数：任务进入 `failed`，记录错误并 ACK。

长任务执行期间，心跳协程同时续期数据库租约和 RocketMQ 消息可见性：

```python
async def _renew_lease(...):
    interval = max(5, JOB_LEASE_SECONDS // 3)
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), interval)
            return
        except asyncio.TimeoutError:
            renewed = await repository.renew_job_lease(
                job_id, lease_token, JOB_LEASE_SECONDS,
            )
            if not renewed:
                return
            await consumer.extend_visibility(
                message, JOB_MESSAGE_INVISIBLE_SECONDS,
            )
```

最终写结果必须携带当前 `lease_token`：

```sql
UPDATE workflow_jobs
SET status = 'succeeded', result = $3::jsonb, finished_at = NOW()
WHERE id = $1
  AND status = 'running'
  AND lease_token = $2;
```

旧 Worker 即使在网络恢复后返回结果，也不能覆盖已经被新 Worker 接管的任务。

## 7. 幂等如何挡住重复点击和网络重试

任务表对 `(tenant_id, idempotency_key)` 建立唯一约束。相同键和相同请求返回原任务；相同键对应不同请求时返回 `409 Conflict`。

```python
canonical_request = json.dumps(
    {"operation": operation, "payload": payload},
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
)
request_hash = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
```

浏览器端每次逻辑操作生成一次 `crypto.randomUUID()`。调用方超时重试时必须复用原来的 `Idempotency-Key`，否则服务端会将其视为新任务。

知乎接口还根据“操作类型 + 规范化请求内容 + 时间窗口”生成幂等键，默认 30 秒内的相同采集请求共用一个任务。

## 8. 知乎浏览器池如何处理并发

每个 Zhihu Worker 创建固定容量的槽位队列。一个槽位绑定一个独立 persistent profile，同一时间只交给一个任务。

```python
self._queue: asyncio.Queue[int] = asyncio.Queue(maxsize=size)
for index in range(size):
    self._queue.put_nowait(index)
self._scrapers: list[Any | None] = [None] * size

@asynccontextmanager
async def lease(self):
    index = await self._queue.get()
    try:
        await self._limiter.acquire()
        yield await self._scraper(index)
    except Exception:
        await self._discard(index)
        raise
    finally:
        self._queue.put_nowait(index)
```

这带来四个效果：

- 浏览器数量固定，突发任务只排队，不会无限启动 Chromium。
- 同一个 Playwright 页面不会被两个协程同时操作。
- 浏览器跨任务复用，避免重复支付启动成本。
- 某个页面异常时只销毁对应槽位，下次租用时延迟重建。

推荐文章会为多个博主并发创建协程：

```python
batches = await asyncio.gather(
    *(scrape_blogger(blogger) for blogger in bloggers),
    return_exceptions=True,
)
```

即使一次请求包含很多博主，真正同时运行的页面数仍由浏览器池控制。单个博主失败会被记录；只有全部博主失败时，整个任务才失败。

## 9. Redis 全局限速、缓存和降级

所有 Zhihu Worker 实例共享 Redis 中同一个令牌桶：

```python
self._limiter = DistributedTokenBucket(
    "rate-limit:zhihu:global",
    ZHIHU_GLOBAL_REQUESTS_PER_SECOND,
    ZHIHU_GLOBAL_BURST,
)
```

Lua 脚本在 Redis 内原子计算并扣减令牌。默认平均每 2 秒放行一个新的浏览器租用请求，短时间最多允许 2 个突发请求：

```text
ZHIHU_GLOBAL_REQUESTS_PER_SECOND=0.5
ZHIHU_GLOBAL_BURST=2
```

Redis 不可用时，限速器退化为进程内 `asyncio.Lock`：

```python
async def _acquire_local(self) -> None:
    async with self._local_lock:
        now = time.monotonic()
        wait_seconds = max(0.0, self._local_next_at - now)
        if wait_seconds:
            await asyncio.sleep(wait_seconds)
        self._local_next_at = max(now, self._local_next_at) + 1 / self.rate
```

降级后只能限制单进程速率，无法保证集群总速率。多个实例的合计速率可能放大到“实例数 × 单进程速率”，所以 Redis 故障应触发告警。

成功结果按请求哈希缓存在 Redis 中，默认 300 秒。命中缓存后不领取浏览器。

## 10. 同一会话的并发修改

领取任务时会检查相同 `session_id` 是否已有租约有效的 `running` 任务。如果存在，当前消息返回 `busy`，避免同一 LangGraph 会话的操作并发执行。

当前实现有一个竞态窗口：两个属于同一会话、但拥有不同 `job_id` 的任务如果被不同 Worker 同时领取，它们锁定的是各自任务行，都可能在检查时看不到对方已经进入 `running`。因此当前机制能处理大多数先后到达的操作，但还不是严格的会话级互斥锁。

要求严格串行时，应在 `claim_job()` 事务内增加以 `session_id` 为键的 PostgreSQL advisory lock，或增加会话锁表并锁定同一会话行。修复前，前端应等待上一次会话操作进入终态后再提交下一次操作。

## 11. 如何扩容和估算容量

生产编排将三类后台角色拆开：

```yaml
outbox:
  environment:
    ASYNC_SERVICE_ROLE: outbox

workflow-worker:
  environment:
    ASYNC_SERVICE_ROLE: worker
    JOB_CONSUMER_CONCURRENCY: 4

zhihu-worker:
  environment:
    ASYNC_SERVICE_ROLE: zhihu-worker
    ZHIHU_JOB_CONCURRENCY: 4
    ZHIHU_BROWSER_POOL_SIZE: 4
  shm_size: "1gb"
```

水平扩容：

```bash
docker compose -f docker-compose.prod.yml up -d --scale workflow-worker=8
docker compose -f docker-compose.prod.yml up -d --scale zhihu-worker=3
```

| 部署 | 理论槽位 | 还需核对的限制 |
|---|---:|---|
| 8 个 Workflow Worker | `8 × 4 = 32` | 模型限额、LangGraph、PG 连接池 |
| 3 个 Zhihu Worker | `3 × 4 = 12` | Redis 全局 0.5 请求/秒、内存、共享内存、知乎风控 |

每个 Worker 实例的 PostgreSQL 最大连接数当前生产默认是 6。扩容前需要估算所有服务连接池之和，并为运维连接和突发留出余量。

不要只提高 `JOB_CONSUMER_CONCURRENCY`。并发过高会把排队从 RocketMQ 转移到模型 API、数据库连接池或浏览器内部，形成超时和重试风暴。

## 12. CPU 密集任务的处理边界

当前协程模型适合网络、数据库、消息队列和浏览器等待等 I/O 密集任务。Python GIL 对这些任务影响较小，因为等待 I/O 时协程会主动让出执行权。

视频编码、大文件压缩、本地模型推理等 CPU 密集任务不应直接放进当前事件循环，也不适合无限使用 `asyncio.to_thread()`。这类任务应使用独立 Topic 和独立 Worker 进程，按 CPU/GPU 数量设置进程并发。

## 13. 关键边缘情况

| 场景 | 当前行为 | 注意事项 |
|---|---|---|
| RocketMQ 暂时不可用 | Outbox 保留消息并退避重试 | API 已创建的任务不会丢失 |
| 发布成功但 Outbox 未标记就崩溃 | 消息可能再次发布 | Worker 依靠状态和租约去重 |
| Worker 执行中崩溃 | 租约到期后其他 Worker 重新领取 | 外部副作用仍需幂等 |
| ACK 失败 | RocketMQ 可能再次投递 | 终态任务不会重复执行 |
| PostgreSQL 不可用 | 新任务返回 503 | 不会在无持久化保障时假装入队成功 |
| Redis 不可用 | 知乎限速退化为单进程限速，缓存失效 | 多实例总速率会上升 |
| Chromium 页面异常 | 丢弃对应槽位，下次延迟重建 | 其他槽位继续服务 |
| 知乎触发登录或人机验证 | 当前任务失败并进入重试 | 持续触发时需处理账号或出口 |
| 同一会话同时提交两个操作 | 通常会串行，但存在同时领取竞态 | 严格一致性需会话级数据库锁 |

## 14. 建议监控指标

- HTTP 任务提交速率，以及 202、409、503 比例。
- `queued` 数量、最老任务排队时间、执行时长 P50/P95/P99。
- Outbox 未发布数量、最老记录年龄、发布失败次数。
- RocketMQ 消费延迟、重复投递量、ACK 失败量。
- Worker 当前执行数、信号量等待数、租约续期失败数和重试次数。
- PostgreSQL 连接池使用率、锁等待、慢查询和最大连接数。
- 知乎浏览器池已用槽位、槽位重建次数和缓存命中率。
- Redis 令牌等待时间、限速降级次数和知乎验证触发率。

## 15. 测试用例

当前测试覆盖：

- 相同幂等键返回同一任务，不产生重复 Outbox。
- 同一幂等键对应不同请求时拒绝提交。
- Outbox 多实例领取时跳过已锁定记录。
- 消费者并发受 `Semaphore` 限制。
- 成功、失败、重试、终态重复消息和租约续期。
- 知乎浏览器槽位复用、异常槽位回收和关闭。
- Redis 缓存命中、URL 校验、登录或验证识别。
- 多博主部分失败时保留成功结果，全部失败时任务失败。
- Web/Gateway 返回 202，任务状态接口返回最终结果。

上线前建议补充压测：

1. 1000 个用户在 10 秒内提交任务，确认 API 延迟和 Outbox 积压可控。
2. 执行中强制终止 Worker，确认租约到期后任务被重新领取。
3. RocketMQ 停机 5 分钟后恢复，确认 Outbox 全部补发。
4. Redis 故障时运行多个 Zhihu Worker，确认集群限速降级告警。
5. 两个 Worker 同时领取同一会话的不同任务，复现并验证会话级竞态修复。
6. 持续提高 Worker 数量，找出模型、数据库和浏览器资源的第一个饱和点。

## 16. 关键代码位置

| 文件 | 作用 |
|---|---|
| `forge/gateway/app.py` | 工作流异步提交和任务查询接口 |
| `forge/web/app.py` | 知乎异步提交、Gateway 代理和任务查询 |
| `forge/async_jobs/service.py` | 请求哈希与统一任务提交入口 |
| `forge/async_jobs/repository.py` | 任务、Outbox、幂等、租约和重试状态 |
| `forge/async_jobs/rocketmq.py` | RocketMQ 5.x SDK 异步适配层 |
| `forge/async_jobs/worker.py` | Outbox 发布器、消费者并发和租约心跳 |
| `forge/scraping/zhihu_pool.py` | 固定容量的长驻浏览器池 |
| `forge/scraping/rate_limit.py` | Redis 分布式令牌桶与本地降级 |
| `forge/scraping/zhihu_executor.py` | 知乎任务路由、缓存和采集并发 |
| `migrations/011_async_workflow_jobs.sql` | 任务和 Outbox 表结构 |
| `docker-compose.prod.yml` | RocketMQ、Outbox 和 Worker 生产编排 |
