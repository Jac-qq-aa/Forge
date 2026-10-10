# 公司业务与资质口径

业务记录必须绑定具体组织实体、适用地区、有效时间和出处。集团宣传的业务范围不能自动归属每一家分支机构。规模、客户数、服务人数和营收属于高时效事实；来源没有统计期时保留“统计期未知”。

锐博首批业务类别包括人力资源服务、商务流程外包、招聘流程外包、生产制造与岗位外包、物流辅助服务、企业出海和跨境人才服务、机器人与智能装备、信息技术外包。具体主体是否提供该业务仍需来源支持。

公司业务查询使用 `business_offerings`，公司资质查询使用 `qualifications`。关联组织时使用组织 UUID，不以名称文本猜测主体。业务描述、资质名称、证号、适用地区和有效期都必须返回对应来源。


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
SELECT name, description, source_url, source_quote
FROM business_offerings
WHERE valid_to IS NULL OR valid_to >= CURRENT_DATE
LIMIT 20;
```
