# 自动生成、容量与监控

默认预算由用户授权采用保守值，均为 Forge 应用预算，不是供应商承诺或实际账号配额。

| 调用 | RPM | TPM | 同时请求数 |
|---|---:|---:|---:|
| 主模型 | 20 | 100000 | 3 |
| 小模型 | 30 | 30000 | 2 |
| 向量模型 | 30 | 60000 | 2 |

配置项见 `.env.example`。同一供应商端点、同一模型共享 Redis 键；若主模型与小模型配置成同一个名称，使用两者较低预算。所有副本必须使用同一 Redis 和相同预算配置。更换模型后按账号实际配额调整。

Redis Lua 原子检查滑动 60 秒窗口的请求数、token 预占和并发租约。输入按 UTF-8 字节保守估算，加输出上限预占，成功后按 usage 结算；失败也计请求数并保留预占。超过单次 TPM 预算直接报告，不能无限等待。预算等待最长 180 秒。429、408、部分 5xx、连接失败及超时最多尝试三次，指数退避并参考 Retry-After。Redis 异常时禁止绕过限制。Redis 使用 AOF 和 noeviction；崩溃中的请求会在租约到期后释放并发。

## 自动处理

`approval` 检查非空事实清单、大纲版本，以及每段引用的事实是否有效，通过后自动生成。`plan_conflict_decision`、`conflict_decision` 自动保留用户标记事实，记录系统规则、事实版本、冲突及证据，不冒充用户点击，不将冲突改为已核实。未标记问题仍交给 revise，再经 critic 检查；重复问题或第三次不通过停止。

事实增删改查保留。任务执行中暂时禁止事实修改，避免运行结果覆盖用户操作；执行结束后可以修改并重新生成/修订。三个旧确认入口已移除。定稿入库仍由用户主动操作。

## 调度状态

| 状态 | 意义 | 占用 Graph 容量 |
|---|---|---|
| pending | 等待 Outbox/MQ 投递 | 否 |
| queued | 消费者已持久接收，等待准入 | 否 |
| dispatching | 正在提交或提交重试 | 是 |
| submission_unknown | 三次提交失败后仍无法确认远端是否运行 | 是，防止重复提交 |
| submitted | 远端已接受，等待远端执行 | 是 |
| running | 远端明确报告执行中 | 是 |
| completed/failed/cancelled | 已结束 | 否 |

Graph 全局准入默认 3，LangGraph dev 的 `--n-jobs-per-worker` 同为 3。索引任务单独准入 1。RocketMQ 每个消费组线程固定 4（两个 topic 对应两个消费组），只负责入队；不等于模型并发或 Graph 并发。

PostgreSQL 全局事务锁控制准入；任务会话锁覆盖远端提交及结果投影，进程退出自动释放。重启后恢复 dispatching，先按 metadata.job_id 核对已有 run；三次异常后停止再次提交，仅查询已有 run 并告警。无法确定远端状态时不自动腾出容量。处理人员应核对远端 run 和对应 job 再处置，不能把不确定任务直接重排。重复消息通过 Inbox 和任务状态幂等，最终投影用 job_id 收据防止重复稿件。排队等待不增加失败次数。

当前 Compose 仍运行 LangGraph **dev** 服务，此配置不是生产高可用 Agent Server。重建旧 langgraph 容器前应备份 `/app/.langgraph_api`；当前容器可写层中的 checkpoint 不保证跨重建保留。升级生产部署需使用持久 Agent Server 存储。

## 监控与访问

执行数据库迁移后启动 Compose 服务。Prometheus 位于 `http://127.0.0.1:9090`，Grafana 位于 `http://127.0.0.1:3000`，Alertmanager 位于 `http://127.0.0.1:9093`。Grafana 初始账号 admin，密码由 `GRAFANA_ADMIN_PASSWORD` 配置。

三个预置看板：系统与积压、模型与工具、工作流与复盘。区分 MQ 待消费/消费中、应用排队、提交中和真实运行；监测等待时间、节点耗时、模型 token、429 重试、系统决定、日志持久化失败。指标不包含用户、租户、任务 ID 或文章内容。复盘明细继续走已有授权日志接口。

告警覆盖服务不可达、排队超过 5 分钟、提交状态不明、运行超过 30 分钟、模型频繁限流、日志写入失败、Outbox 耗尽、MQ 延迟及死信。Alertmanager 当前仅提供本机告警页面，尚未配置邮件/团队通知接收地址。

本机多 Python 进程运行时分别设置不同 `METRICS_PORT`，并同步修改 Prometheus targets；Docker 内每个服务独立使用 9001。

RocketMQ 原生指标和 Prometheus exporter 配置依据：[官方指标说明](https://rocketmq.apache.org/docs/observability/01metrics/)。LangGraph 并发参数依据：[CLI dev 参考](https://reference.langchain.com/python/langgraph-cli/cli/dev)。

## 验证边界

单元测试覆盖无 HITL 生成、空计划阻断、冲突保留且不伪造核实、重复问题停止、跨客户端 Redis 原子并发、TPM 结算/RPM 计数、超额拒绝、Redis 故障、取消释放与 429 三次终止。集成测试需真实 PostgreSQL/pgvector；真实模型验收通过 `FORGE_LIVE_TRACE_SMOKE=1` 显式开启。

本次不实现博主监听或批量导入二十篇文章。
