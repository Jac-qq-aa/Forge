# Forge 旧实现移除清单

本清单记录全量重构中旧文件的处置。所有删除都可从 Git 历史恢复；本地密钥、数据库卷和未纳入 Git 的用户数据不在代码清理范围内。

## 删除

- `forge/`：旧 graph、agent、Gateway、RocketMQ、Milvus、抓取发布、音视频、评估和存储实现，全部由新模块替换。
- `tests/`、`forge/deep_mode/tests/`：只验证旧接口的测试，全部由新 Interface 测试替换。
- `migrations/001` 至 `014`：旧数据不迁移，改为一份新基线迁移。
- `deploy/`、旧 Docker Compose、旧 Gateway Dockerfile：由 PostgreSQL + pgvector 的新部署配置替换。
- 根目录旧调试、抓取、导入、登录、部署、知识库和多媒体脚本：不属于新产品主流程。
- 旧 API、部署、并发、Milvus、历史阶段设计文档和旧二进制演示资产：与新实现不一致。
- `.agents/skills/forge-article-eval`：依赖被删除的旧文章评估实现。

## 替换

- `run_web.py` → `run_api.py`。
- `requirements.txt`、`.env.example`、`README.md`、`pytest.ini` → 新运行方式。
- `docker-compose.yml` → PostgreSQL + pgvector；不包含 Milvus、RocketMQ、Gateway 或 LangGraph Server。
- `langgraph.json` → 仅作为未来 LangGraph Server Adapter 的扩展入口，初版运行不依赖它。

## 保留

- `AGENTS.md`：仓库级协作约束。
- `docs/2026-09-25-forge-rebuild-design.md`：已批准的目标方案。
- 本清单。
- `.gitignore`：按新目录更新。
- Git 历史。

## 不主动删除的本地内容

- `.env`、`.env.prod` 等未跟踪的密钥配置。
- `volumes/`、`backups/`、`data/`、`output/` 等未跟踪的本地数据。
- `CONTEXT.md` 等本轮开始前已存在且归属未确认的未跟踪文件。

这些本地内容不被新代码引用。需要清理运行数据时应在明确环境和备份策略后单独执行。
