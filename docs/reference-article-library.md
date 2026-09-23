# Forge 参考文章库

参考文章库只负责回答“这类文章通常怎样组织、怎样表达”，不承担事实真伪判断。

## 三库边界

| 数据 | 保存位置 | 是否可直接进入生成提示 |
| --- | --- | --- |
| 参考文章的原型、结构角色、风格画像 | `reference_articles` + Milvus `reference_articles_v1` | 只以压缩后的 `ReferencePack` 进入 |
| 锐博集团事实 | `reference_company_fact_candidates` 待审，审核后转正式公司事实知识库 | 仅审核通过的事实可进入 |
| 法律、政策与时效新闻 | 法律/政策版本库 | 必须按发布日期、施行日期、地域和效力状态过滤 |

严禁把整篇参考文章直接塞给 Generator。`ReferencePack` 只包含文章原型、结构角色、统计风格指引、禁止复用短语和参考文章 ID。

## 初始化

先执行 PostgreSQL 迁移：

```powershell
psql -f migrations/010_reference_articles.sql
```

再导入本地知乎快照并建立双向量索引：

```powershell
python scripts/import_reference_articles.py data/articles/zhihu
```

若只想检查分类、去重和待审事实，不调用嵌入模型：

```powershell
python scripts/import_reference_articles.py data/articles/zhihu --skip-vectors
```

导入过程会：

1. 发现至少三篇文章共同出现的重复长句，并在风格分析前移除。
2. 将文章分成集团事实型、政策热点型和混合型。
3. 识别公司介绍、政策速读、风险预警、行业分析、实操指南、趋势与品牌混合六种原型。
4. 用内容哈希和近重复指纹阻止重复入库。
5. 将含锐博集团事实的句子放入 HITL 待审表，不直接发布。
6. 将带年份或“当前、目前、截至、最新”等词的事实标成时效事实。
7. 分别生成主题向量和风格向量，写入独立 Milvus collection。

## 运行时

大纲节点调用：

```python
pack = await library.select_reference_pack(
    ReferenceRequest(topic=topic, target_platform="zhihu_article")
)
```

检索采用主题 70%、风格 30% 的融合分数。大纲、Generator 和 Critic 使用同一个 `ReferencePack`：

- 大纲学习结构角色；
- Generator 学习语气、句长、段长和节奏；
- Critic 检查结构偏离、模板套话与复制风险；
- 事实、数字、案例仍只能来自用户审核后的事实注册表。

## HITL 事实审核

`reference_company_fact_candidates.review_status` 初始均为 `pending`。建议审核界面至少提供：

- `accepted`：事实稳定、来源可信，可转正式公司事实库；
- `rejected`：宣传套话、错误或无法核验；
- `conflicted`：同一事实存在不同年份或数字，必须人工选定版本；
- `is_temporal=true`：即使接受，也需要有效期或复核日期，不能永久有效。

正式公司事实库发布与参考文章导入应保持两个独立事务，防止文章风格样本把未经确认的集团数据带入生成。

## DeepEval 数据

先导出已去模板的上下文：

```powershell
python scripts/export_reference_eval_contexts.py
```

生成的 `tests/evals/reference_contexts.json` 是 DeepEval 合成数据源，不是手写 goldens。当前项目继续使用 `JUDGE_MODEL`，且按项目评测规范不接 DeepEval tracing 或 Confident AI。
