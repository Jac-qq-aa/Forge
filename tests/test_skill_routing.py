from forge.knowledge.text2sql import SkillResourceRouter


def test_routes_company_relation_question_to_company_resources() -> None:
    router = SkillResourceRouter()
    assert router.select("锐博集团有哪些子公司和分支机构？") == (
        "company_relations",
        "schema_company",
    )


def test_routes_hr_statistical_question_without_legal_context() -> None:
    router = SkillResourceRouter()
    assert router.select("平均工资应该怎么计算？") == (
        "hr_business_terms",
        "schema_hr",
    )


def test_routes_dispatch_question_to_hr_and_legal_resources() -> None:
    router = SkillResourceRouter()
    assert router.select("劳务派遣用工比例上限是多少？") == (
        "hr_business_terms",
        "schema_hr",
        "legal_terms",
        "schema_legal",
    )


def test_routes_mixed_company_business_question_to_multiple_resources() -> None:
    router = SkillResourceRouter()
    assert router.select("锐博是否提供劳务派遣业务，具备哪些资质？") == (
        "company_business",
        "schema_company",
        "hr_business_terms",
        "schema_hr",
        "legal_terms",
        "schema_legal",
    )


def test_unknown_question_requires_model_selection() -> None:
    router = SkillResourceRouter()
    assert router.select("请查询相关情况") == ()
