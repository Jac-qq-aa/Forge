import pytest
from uuid import uuid4

from forge.domain.models import Identity
from forge.knowledge.text2sql import (
    QueryPolicy,
    ReadOnlySqlGuard,
    SkillLoader,
    Text2SqlSkill,
    UnsafeQuery,
)


@pytest.fixture
def guard() -> ReadOnlySqlGuard:
    return ReadOnlySqlGuard(
        QueryPolicy(
            tables={
                "organizations": frozenset({"id", "legal_name", "region"}),
                "organization_relations": frozenset({"subject_id", "object_id", "relation_type"}),
            },
            max_rows=20,
        )
    )


def test_adds_result_limit(guard: ReadOnlySqlGuard) -> None:
    sql = guard.validate("SELECT legal_name, region FROM organizations WHERE region = '广东'")
    assert "LIMIT 20" in sql


def test_caps_requested_limit(guard: ReadOnlySqlGuard) -> None:
    sql = guard.validate("SELECT legal_name FROM organizations LIMIT 999")
    assert "LIMIT 20" in sql


def test_allows_read_only_cte_over_authorized_tables(guard: ReadOnlySqlGuard) -> None:
    sql = guard.validate(
        "WITH matched AS (SELECT id, legal_name FROM organizations) "
        "SELECT id, legal_name FROM matched"
    )
    assert "WITH matched AS" in sql
    assert "LIMIT 20" in sql
    assert guard.referenced_tables(sql) == frozenset({"organizations"})


def test_allows_boolean_operators(guard: ReadOnlySqlGuard) -> None:
    sql = guard.validate(
        "SELECT legal_name FROM organizations "
        "WHERE legal_name ILIKE '%锐博%' OR region = '广东'"
    )
    assert " OR " in sql


def test_allows_safe_type_casts(guard: ReadOnlySqlGuard) -> None:
    sql = guard.validate(
        "SELECT CAST(legal_name AS TEXT) AS legal_name_text FROM organizations"
    )
    assert "CAST(legal_name AS TEXT)" in sql


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM organizations",
        "SELECT * FROM organizations",
        "SELECT email FROM users",
        "SELECT pg_read_file('/etc/passwd') FROM organizations",
        "SELECT legal_name FROM public.organizations",
        "SELECT legal_name FROM organizations; SELECT region FROM organizations",
        "SELECT legal_name FROM organizations FOR UPDATE",
    ],
)
def test_rejects_unsafe_queries(guard: ReadOnlySqlGuard, sql: str) -> None:
    with pytest.raises(UnsafeQuery):
        guard.validate(sql)


class HrQueryModel:
    async def structured(self, *, system, prompt, schema):
        return {
            "sql": (
                "SELECT t.term_name, t.definition, s.source_url "
                "FROM hr_business_terms AS t "
                "JOIN hr_term_sources AS s ON s.id=t.source_id "
                "WHERE t.term_code='HR_DISPATCH_RATIO'"
            ),
            "reason": "查询劳务派遣比例及官方来源",
        }


@pytest.mark.asyncio
async def test_hr_term_query_returns_database_provenance(tmp_path) -> None:
    for name in ("hr_business_terms", "schema_hr", "legal_terms", "schema_legal"):
        (tmp_path / f"{name}.md").write_text(name, encoding="utf-8")
    guard = ReadOnlySqlGuard(
        QueryPolicy(
            tables={
                "hr_business_terms": frozenset(
                    {"id", "term_code", "term_name", "definition", "source_id"}
                ),
                "hr_term_sources": frozenset({"id", "source_url"}),
            }
        )
    )

    async def execute(sql, identity):
        assert "LIMIT 100" in sql
        return [{
            "term_name": "劳务派遣用工比例",
            "definition": "被派遣劳动者不得超过用工总量的10%。",
            "source_url": "https://www.mohrss.gov.cn/example",
        }]

    skill = Text2SqlSkill(
        model=HrQueryModel(),
        loader=SkillLoader(tmp_path),
        guard=guard,
        execute=execute,
    )
    evidence = await skill.ask(
        "劳务派遣用工比例是多少？",
        identity=Identity(user_id=uuid4()),
    )
    assert evidence[0].title == "劳务派遣用工比例"
    assert evidence[0].url == "https://www.mohrss.gov.cn/example"
    assert evidence[0].metadata["tables"] == [
        "hr_business_terms",
        "hr_term_sources",
    ]
    assert evidence[0].metadata["loaded_resources"] == [
        "hr_business_terms",
        "schema_hr",
        "legal_terms",
        "schema_legal",
    ]
