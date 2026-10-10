# 公司关系口径

- `headquarters`：官网明确标注的集团总部。
- `official_listing`：集团官网“旗下机构”页面列示的机构，只证明官网列示关系。
- `branch`：名称或登记信息明确为某法人的分公司。
- `subsidiary`：必须有股权、登记或公司正式披露证据；官网列示不得自动升级为子公司。
- `equity_control`：必须记录持股比例、控制依据、有效期和原始出处。

锐博首批数据以 https://www.nnngu.com/ 的集团概况、旗下机构、业务和资质页面为候选来源，再进行实体归一和证据核验。


## 数据库 Schema

# 公司关系、业务与资质 PostgreSQL schema

只允许显式列名查询以下表：

- `organizations`：`id`, `legal_name`, `organization_type`, `region`, `registration_code`, `source_url`, `verified_at`, `valid_from`, `valid_to`
- `organization_aliases`：`organization_id`, `alias`, `normalized_alias`, `valid_from`, `valid_to`
- `organization_relations`：`subject_id`, `object_id`, `relation_type`, `confidence`, `valid_from`, `valid_to`, `source_url`, `source_quote`
- `business_offerings`：`organization_id`, `name`, `description`, `region`, `valid_from`, `valid_to`, `source_url`, `source_quote`
- `qualifications`：`organization_id`, `name`, `certificate_number`, `issuer`, `valid_from`, `valid_to`, `source_url`, `source_quote`

关联组织时必须使用 UUID 主键，不以同名文本猜测。不得查询用户、租户、授权记录或其他未列出的表。


## 查询示例

```sql
SELECT subject_id, object_id, relation_type, confidence, source_url, source_quote
FROM organization_relations
WHERE valid_to IS NULL OR valid_to >= CURRENT_DATE
LIMIT 20;
```
