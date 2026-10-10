# 人力资源行业业务口径

人力资源口径分为 `legal_mandatory`、`statistical`、`industry_service` 和 `company_defined`。法律强制规则、统计定义、推荐性标准与公司内部 KPI 不得互相替代。

- 行业分类、工资总额和平均工资等统计口径，查询 `hr_business_terms` 与 `hr_classification_codes`；
- 劳务派遣比例、合同期限、招聘审查、工时休假等动作和阈值，查询 `hr_compliance_obligations`；
- 社保、公积金、最低工资等地区时效参数，查询 `hr_regional_parameters`，必须指定地区和日期；
- 每项定义的官方出处，联结 `hr_term_sources`，返回文件名、发布机关、条款和 URL。

“灵活用工”不是单一法律关系。人力资源外包也不能仅按合同标题认定为外包；实际履行可能构成劳务派遣。招聘成功率、离职率、招聘周期、培训 ROI 和人才测评合格线没有统一国家公式，只有经过公司审批并记录版本后才能作为 `company_defined` 口径使用。

查询法律强制口径时同时加载 `legal_terms.md`。统计定义和行业分类不应自动升级为法律义务；国家标准元数据标记为 `pending_fulltext` 时，不得生成标准正文中的细则。


## 数据库 Schema

# 人力资源业务口径 PostgreSQL schema

只允许显式列名查询以下表：

- `hr_term_sources`：`id`, `source_kind`, `title`, `issuing_authority`, `document_number`, `source_url`, `article_clause`, `published_on`, `effective_from`, `effective_to`, `source_quote`, `verification_status`
- `hr_business_terms`：`id`, `term_code`, `term_name`, `domain`, `term_type`, `definition`, `calculation_rule`, `unit`, `applicable_scope`, `exclusions`, `mandatory_level`, `status`, `effective_from`, `effective_to`, `version`, `source_id`
- `hr_compliance_obligations`：`id`, `obligation_code`, `actor_type`, `trigger_event`, `required_action`, `deadline_value`, `deadline_unit`, `threshold_expression`, `exception_rule`, `article_clause`, `effective_from`, `effective_to`, `version`, `source_id`
- `hr_regional_parameters`：`id`, `term_id`, `region_code`, `employment_type`, `parameter_name`, `value_numeric`, `value_text`, `unit`, `valid_from`, `valid_to`, `source_id`
- `hr_classification_codes`：`id`, `classification_system`, `code`, `parent_code`, `name`, `definition`, `inclusions`, `exclusions`, `version`, `valid_from`, `valid_to`, `source_id`

查询定义时优先联结 `hr_term_sources` 返回 `source_url` 和 `article_clause`。地区参数查询必须同时限定 `region_code` 与查询日期；缺少地区或日期时不得猜测当前值。`calculation_rule` 和 `threshold_expression` 只用于解释，不得作为动态 SQL 执行。


## 查询示例

```sql
SELECT t.term_name, t.definition, t.applicable_scope, s.source_url, s.article_clause
FROM hr_business_terms AS t
LEFT JOIN hr_term_sources AS s ON s.id = t.source_id
WHERE t.status = 'active'
LIMIT 20;
```
