# 法律口径

法律事实优先引用国家法律法规数据库、国务院、部委和地方政府的原始页面。每条记录保存法规名称、条款、发布机关、适用地区、公布和生效时间、有效状态及原始 URL。

内部业务说明与法律规定分开保存。网页检索命中转载时应继续寻找原始官方出处；无法确认效力状态时标记待核验，不得写成确定性法律结论。

优先检索的官方入口：

- 国家法律法规数据库：https://flk.npc.gov.cn/
- 中国政府网政策文件库：https://www.gov.cn/zhengce/
- 人力资源和社会保障部政策法规目录：https://www.mohrss.gov.cn/xxgk2020/fdzdgknr/zcfg/

入口地址只是检索范围。最终证据必须指向具体法规或规范性文件页面，并记录条款、效力状态和适用时间。


## 数据库 Schema

# 法律引用 PostgreSQL schema

只允许显式列名查询以下表：

- `legal_references`：`title`, `jurisdiction`, `article_number`, `source_url`, `source_quote`, `effective_from`, `effective_to`

法律问题涉及人力资源业务口径时，还应读取 `hr_business_terms.md` 中的 schema，联结 `hr_business_terms`、`hr_compliance_obligations` 与 `hr_term_sources`。法律效力、适用地区、适用主体和有效期均应以具体记录为准。


## 查询示例

```sql
SELECT title, jurisdiction, article_number, source_url, source_quote, effective_from, effective_to
FROM legal_references
WHERE effective_to IS NULL OR effective_to >= CURRENT_DATE
LIMIT 20;
```
