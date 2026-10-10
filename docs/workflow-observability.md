# 流程日志与复盘

Forge 使用 LangSmith 保存通用调用树、模型耗时、Token 用量和异常；JSON 运行日志输出到容器标准输出。PostgreSQL `trace_events` 保留关键业务事件，`workflow_events` 负责页面进度。事实、证据、草稿段落、Critic 审查和人工决定由业务表持久保存，不依赖 LangSmith 的采样或保留期。

每次 API 请求有业务 trace_id；任务、Outbox、RocketMQ、Consumer 和 LangGraph run 传播 LangSmith 上下文。每次提交另有 execution_id，用于消息重投后的精确查重。一个对话可以包含多个任务；人工确认恢复 Graph 时创建新的任务与执行轮次。节点、工具和模型在 LangSmith 形成父子调用树；人工等待不计入模型执行耗时。

LangSmith 自动采集 OpenAI 兼容模型调用及 Token，Forge 手工埋点采集节点与工具调用；业务事件记录 Skill 版本、授权 SQL 表、检索来源、降级策略、Critic 问题和用户裁决。部署默认关闭 LangSmith 输入与输出正文采集。正文、提示词、SQL 返回行和访问令牌不写入 Forge 通用日志。业务表按授权保存文章及证据内容，供段落出处复盘。来源 URL 在通用事件中去掉用户信息、查询参数与 fragment。

## 启用 LangSmith

在 `.env` 填写 `LANGSMITH_TRACING=true`、`LANGSMITH_API_KEY` 和 `LANGSMITH_PROJECT=forge` 后重新创建 API、Outbox、Consumer、LangGraph Server 容器。`LANGSMITH_HIDE_INPUTS` 与 `LANGSMITH_HIDE_OUTPUTS` 在 Compose 中固定为 `true`。未配置密钥时保持 `LANGSMITH_TRACING=false`，业务功能仍可运行。LangSmith 项目需对这些服务使用同一密钥与项目名。跨服务传播只接收内部消息中的 `langsmith-trace` 和 `baggage`，外部请求头不会直接成为父 Span。

草稿每次生成或修订保存一个 `draft_versions` 快照；`paragraph_provenance` 保存段落对应的事实版本、证据引用、人工裁决 ID、修订问题 ID 与前一版本指纹。`review_reports` 保存每轮 Critic 的判断；用户可在工作台展开“查看本段依据”。这些记录随文章保存，不能以 LangSmith Trace 替代。

## 查询

携带登录 Bearer token：

- `GET /v1/conversations/{conversation_id}/jobs/{job_id}/review`：任务状态、关键业务事件聚合、失败与降级次数、首批 200 条事件，以及每次提交的 execution_id、LangGraph run_id 和 LangSmith trace_id。详细耗时和调用树在 LangSmith 中按 trace_id 查看。
- `GET /v1/conversations/{conversation_id}/trace`：对话的跨任务事件，包括不触发生成任务的手工事实修改。
- 继续翻页时传 `?after={next_cursor}`。复盘接口返回的是基于实际事件的确定性统计，不调用模型编写推测性总结。

接口按对话所有者验证，表启用并强制 RLS；SQL Skill 的 forge_query 角色不能读取复盘表。运行时账号只允许追加和读取日志。

## 查看与留存

`docker compose logs api consumer outbox langgraph rocketmq-bridge gateway` 查看运行日志。Gateway 日志含请求 ID 与 API 返回的 trace_id；拒绝在 Gateway 的请求尚未建立业务任务，因此只有 Gateway 日志。

`TRACE_RETENTION_DAYS` 默认 30 天，独立于文章和知识库的留存。管理员使用 `python -m forge.cli prune-traces` 删除过期复盘事件；命令使用迁移账号，不给应用账号删除日志的权限。需要定期清理时，在部署平台调度此命令。容器标准输出的轮转由 Docker logging 配置管理。

日志数据库写入最多等待 2 秒，失败会在标准输出记录 telemetry.persistence_failed，业务仍继续，所以日志可能存在缺口。关键用户决策仍由业务投影事务持久化，保存失败会使对账失败，不会将该任务标成已完成。

异常事件、工具事件和节点事件可能来自同一个根因；聚合的 failure_count 按业务事件计数。底层 SDK 内部重试不一定形成独立 Span；消息投递重试次数有明确记录。模型的具体计费价格不由 Token 统计自动推算。

## 验证

单元测试与 PostgreSQL 集成测试：`python -m pytest tests`。集成测试通过 `FORGE_INTEGRATION_DSN`、`FORGE_INTEGRATION_OWNER_DSN` 指定测试数据库。

Docker 与真实模型验收：设置 `FORGE_LIVE_TRACE_SMOKE=1` 和迁移账号 `FORGE_INTEGRATION_OWNER_DSN`，运行 `python -m pytest tests/integration/test_live_trace.py -s`。它创建独立测试用户与文章，检查 Gateway→队列→Consumer→Graph 的关联、检索与模型记录、人工暂停、复盘查询权限，结束后清理测试用户和对话。默认跳过，显式运行会调用配置的模型、Tavily 与向量模型。

Agent Server 的 run 返回 success 仍可能意味着 Graph 已暂停等待确认。对账同时检查线程的 interrupts 与 next 节点，避免将等待确认误记为完成。Skill 的初始化和 Markdown 读取在线程中执行，以满足 Server 对事件循环同步文件调用的限制。
