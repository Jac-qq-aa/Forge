# Forge 消息路由与任务隔离

Bridge 从 `deploy/rocketmq-bridge/routes.json` 读取路由；每项明确指定 Topic、消费组、目标服务、凭据环境变量、投递线程数和重投次数。配置缺失、重复 Topic/消费组、无凭据或非法 URL 会阻止启动。没有旧环境变量回退逻辑。修改配置后重建或重启 Bridge；修改 Worker 凭据后也需重新创建对应 Worker。

| 工作 | Topic | 消费组 | 服务 | 业务并发默认值 |
| --- | --- | --- | --- | --- |
| 提交原文、问答、修改、人工恢复 | forge-workflow-commands | forge-workflow-workers | consumer | GRAPH_MAX_CONCURRENCY=3 |
| 定稿后分块向量入库 | forge-index-commands | forge-index-workers | index-consumer | INDEX_MAX_CONCURRENCY=1 |
| 终态任务持久化完整性检查 | forge-audit-commands | forge-audit-workers | audit-consumer | AUDIT_MAX_CONCURRENCY=1 |

每组的多个副本共享消费组；各副本必须使用相同的容量配置。PostgreSQL 在领取时计算该类型的全局活动任务数，防止副本数量扩大模型并发。Bridge 的线程数只控制短期 HTTP 投递并发，不代表文章生成并发。每个 Worker 校验类型、Topic、消费组、消息 ID、调用者和租户；Inbox 按消费组与消息 ID 去重，旧执行轮次的消息会被确认并丢弃。

API 将任务与 Outbox 在一个事务中写入 → Outbox 发布 → Bridge 按路由转发 → Worker 持久化接纳任务并返回 200 → MQ 确认消息 → Worker 按类型领取任务。文章 Worker 提交并核对 LangGraph run，只有文章 Worker 启动 run 核对循环。人工暂停释放执行容量；人工决定持久化后通过新的 Outbox 消息恢复。定稿正文先可靠保存，再由索引 Worker 调用向量模型写入 pgvector。

消息投递失败由 RocketMQ 重投，达到配置上限进入该消费组的死信队列。成功接纳之后的模型或数据库失败由数据库任务重试负责；索引和审计最多尝试 CONSUMER_MAX_ATTEMPTS 次。文章提交状态不明确时仍按 execution_id 查找既有 run，避免重复生成。Outbox 达到发布重试上限会将仍未接纳的任务标为失败。索引和审计失败不会把文章对话改为失败。

文章与索引任务完成、失败或取消时，任务终态及审计任务 Outbox 在同一事务中写入；暂停不触发终态审计。审计任务以来源 job_id、execution_id、终态生成确定性 ID，重复终态处理不会重复检查。审计任务自身不触发审计，防止递归。

审计不调用模型。它检查执行轮次、终态事件、文章投影、草稿快照、段落溯源数量及内容指纹和事实引用、Critic 报告、人工裁决、索引块与模型对应的向量记录、Trace 事件可用性。异常标为 attention；检查程序执行成功的任务仍为 completed。旧执行轮次只报告轮次已变化，不检查后来文章的内容。报告保存在有所有者 RLS 的 task_audit_reports，通过 `GET /v1/conversations/{conversation_id}/jobs/{job_id}/review` 的 audit_reports 字段查询。这是持久化完整性检查，不能替代 Critic 的内容正确性判断，也不保证采样或过期后的 Trace 仍完整。

关键业务数据、人工裁决与业务事件仍同步保存；消息队列只调度审计，不能作为唯一日志存储。Prometheus 已覆盖三个 Worker；任务积压和失败指标按 kind 区分。排查时查看 `docker compose logs consumer index-consumer audit-consumer outbox rocketmq-bridge`。

部署：`docker compose up -d --build`。migrate 服务执行 011_task_audit 后再启动 Worker。生产环境需为三个消费入口分别设置强凭据，并同步配置 Bridge 和各 Worker。Topic 和消费组名称同时属于 Python 的 COMMAND_ROUTES 合约；变更名称必须同步修改合约、路由及测试。
