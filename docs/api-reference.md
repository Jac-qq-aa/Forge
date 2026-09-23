# Forge API 参考

Forge 只提供深度内容生成流程。旧的一次性改写接口已删除。

除健康检查外，请求必须携带访问令牌：

```http
Authorization: Bearer <jwt>
```

浏览器也可以使用 `forge_access_token` Cookie。JWT 必须包含 `sub`、`tenant_id`、`iat`、`exp`、`iss` 和 `aud`。

## 素材准备

### `POST /api/search`

搜索知乎或微信公众号文章。知乎请求返回异步任务；微信公众号当前仍直接返回结果。前端统一识别 `job_id` 并查询任务状态。

```json
{
  "source": "人力资源",
  "source_platform": "zhihu",
  "search_mode": "keyword",
  "max_results": 5
}
```

### `POST /api/article_detail`

获取选中的知乎文章全文，返回异步任务。

### `POST /api/get_answers`

获取知乎问题下可选择的回答，返回异步任务。前端将用户选择的回答合并成素材后，提交深度生成会话。

### `POST /api/recommend_articles`

抓取已关注博主的最新文章，返回异步任务。知乎采集任务与内容生成任务使用不同的 RocketMQ Topic 和 Worker 池。

## 深度生成

### `POST /api/deep_mode/create_session`

提交创建会话任务。接口写入 PostgreSQL 任务和 Outbox 后立即返回 `202 Accepted`，后台 Worker 执行到事实审核点。

```json
{
  "article_id": "https://example.com/article",
  "source_article": {
    "title": "文章标题",
    "text": "文章正文"
  },
  "memory_owner_id": "local-user",
  "user_input": "面向 HR 从业者，强调实操案例",
  "target_platform": "zhihu_article"
}
```

请求应携带 `Idempotency-Key`；网络重试时复用同一个值。响应示例：

```json
{
  "job_id": "uuid",
  "operation": "create_session",
  "status": "queued",
  "deduplicated": false,
  "status_url": "/api/jobs/uuid"
}
```

### `POST /api/deep_mode/fact_action`

提交事实审核结果，或请求补充研究。该接口同样返回异步任务。

```json
{
  "session_id": "uuid",
  "action": "accept",
  "reviews": [],
  "research_request": ""
}
```

### `POST /api/deep_mode/outline_action`

确认或修改大纲。该接口同样返回异步任务。

```json
{
  "session_id": "uuid",
  "action": "accept",
  "modification": ""
}
```

### `POST /api/deep_mode/update_outline`

保存用户直接编辑后的大纲。

### `POST /api/deep_mode/finalize`

提交定稿任务。`content` 为空时使用当前草稿。

```json
{
  "session_id": "uuid",
  "content": "最终正文"
}
```

### `GET /api/deep_mode/session/{session_id}`

查询会话状态。

### `GET /api/jobs/{job_id}`

查询任务状态。`queued` 和 `running` 表示尚未完成；`succeeded` 时从 `result` 读取原工作流响应；`failed` 时读取 `error`。

### `GET /api/jobs/{job_id}/events`

以 `text/event-stream` 返回任务状态。事件名称为 `job`，数据结构与任务查询接口一致。终态发送完成后连接关闭，非终态期间每 15 秒发送一次 SSE 心跳。

```json
{
  "id": "uuid",
  "status": "succeeded",
  "attempt_count": 1,
  "result": {
    "session_id": "uuid",
    "stage": "waiting_facts",
    "interrupt_type": "fact_approval",
    "research_result": {}
  },
  "error": null
}
```

### `WS /ws/deep_mode/{session_id}`

用于正文微调的双向消息通道。

## RocketMQ 文章生成任务

以下接口使用独立 Generation Worker 驱动同一个 LangGraph `thread_id`。客户端使用轮询查询状态；HITL 等待不会占用 HTTP 连接或 Worker。

### `POST /generation-tasks`

请求头必须包含 `Idempotency-Key`。成功创建或命中相同幂等请求时返回 `202`，响应包含 `task_id`、`thread_id`、`status`、`deduplicated` 和 `status_url`。

### `GET /generation-tasks/{task_id}`

返回任务状态、当前阶段、修订轮数、待审批内容、`interrupt_id`、`content_version`、结果和最近错误。

### `POST /generation-tasks/{task_id}/approvals`

请求头必须包含新的审批 `Idempotency-Key`，请求体包含 `interrupt_id`、`content_version`、`decision` 和可选 `data`。过期审批、同键不同内容或同一审批点的冲突决定返回 `409`。

### `POST /generation-tasks/{task_id}/cancel`

持久化取消请求并返回 `202`。正在运行的任务先进入 `cancelling`，迟到结果不能覆盖取消状态。

### 管理员死信接口

- `GET /api/admin/generation-dead-letters`
- `POST /api/admin/generation-dead-letters/{dead_letter_id}/replay`

完整状态机、消息确认与恢复机制见 [RocketMQ 文章生成异步调度与故障恢复](rocketmq-generation-async.md)。

## 历史与评估

- `GET /api/deep_mode/history`
- `GET /api/deep_mode/history/{session_id}`
- `DELETE /api/deep_mode/history/{session_id}`
- `POST /api/deep_mode/history/batch_delete`
- `DELETE /api/deep_mode/history/clear_all`
- `GET /api/evaluation/{session_id}`
- `GET /api/article-expression-evaluation/{session_id}`

## 媒体与文件

- `POST /api/generate_digital_human`
- `GET /api/task_status/{task_id}`
- `POST /api/upload_image`
- `GET /api/download_file`
- `GET /api/download_task_dir`
