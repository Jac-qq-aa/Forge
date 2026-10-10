from __future__ import annotations

import os
from uuid import uuid4

import asyncpg
import pytest

from forge.domain.models import Identity
from forge.infrastructure.jobs import JobRepository
from forge.infrastructure.postgres import Database, PgVectorSearch, PostgresArticleStore
from forge.infrastructure.projection import PostgresProgressReporter
from forge.infrastructure.telemetry import PostgresTraceRecorder, emit, trace_context


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_trace_events_are_persistent_private_and_query_role_cannot_access(database):
    owner_dsn = os.getenv("FORGE_INTEGRATION_OWNER_DSN")
    if not owner_dsn:
        pytest.skip("FORGE_INTEGRATION_OWNER_DSN 未设置")
    owner = await asyncpg.connect(owner_dsn)
    users = []
    try:
        for _ in range(2):
            users.append(await owner.fetchval(
                "INSERT INTO users(email,password_hash) VALUES($1,'test') RETURNING id",
                f"trace-{uuid4().hex}@example.com"))
        first, second = Identity(user_id=users[0]), Identity(user_id=users[1])
        conversation = await PostgresArticleStore(database).create_conversation(first)
        job, trace = uuid4(), uuid4()
        await JobRepository(database).enqueue(job_id=job, conversation_id=conversation, kind="start",
            payload={"identity": first.model_dump(mode="json")}, actor_id=first.user_id, trace_id=trace)
        recorder = PostgresTraceRecorder(database)
        with trace_context(recorder=recorder, conversation_id=str(conversation), job_id=str(job),
                           trace_id=str(trace), identity=first.model_dump(mode="json")):
            await emit("fallback.used", status="degraded", reason="test_sql_failure", strategy="skill_md")
        review = await recorder.review(conversation, job, first)
        assert review["summary"]["degradation_count"] == 1
        assert review["summary"]["event_count"] == 2
        assert review["events"][-1]["payload"]["trace_id"] == str(trace)
        cursor = review["next_cursor"]
        assert (await recorder.review(conversation, job, first, after=cursor))["events"] == []
        with pytest.raises(LookupError):
            await recorder.review(conversation, job, second)
        async with database.authorized(second) as connection:
            assert await connection.fetch("SELECT id FROM trace_events WHERE conversation_id=$1", conversation) == []
        async with database.authorized(first) as connection:
            await connection.execute("SET LOCAL ROLE forge_query")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await connection.fetch("SELECT id FROM trace_events")
        async with database.authorized(first) as connection:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await connection.execute("DELETE FROM trace_events WHERE conversation_id=$1", conversation)
    finally:
        if users:
            await owner.execute("DELETE FROM conversations WHERE owner_id=ANY($1::uuid[])", users)
            await owner.execute("DELETE FROM users WHERE id=ANY($1::uuid[])", users)
        await owner.close()


class FixedEmbedding:
    model = "integration-test"

    async def embed(self, _text: str) -> list[float]:
        return [1.0, *([0.0] * 1023)]


@pytest.fixture
async def database():
    dsn = os.getenv("FORGE_INTEGRATION_DSN")
    if not dsn:
        pytest.skip("FORGE_INTEGRATION_DSN 未设置")
    db = Database(dsn)
    await db.open()
    try:
        yield db
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_pgvector_and_rls_isolate_company_and_personal_knowledge(database: Database) -> None:
    owner_dsn = os.getenv("FORGE_INTEGRATION_OWNER_DSN")
    if not owner_dsn:
        pytest.skip("FORGE_INTEGRATION_OWNER_DSN 未设置")
    suffix = uuid4().hex
    connection = await asyncpg.connect(owner_dsn)
    try:
        async with connection.transaction():
            extension = await connection.fetchval(
                "SELECT extversion FROM pg_extension WHERE extname='vector'"
            )
            assert extension
            first_user = await connection.fetchval(
                "INSERT INTO users (email, password_hash) VALUES ($1,'test') RETURNING id",
                f"first-{suffix}@example.com",
            )
            second_user = await connection.fetchval(
                "INSERT INTO users (email, password_hash) VALUES ($1,'test') RETURNING id",
                f"second-{suffix}@example.com",
            )
            tenant_id = await connection.fetchval(
                "INSERT INTO tenants (name) VALUES ($1) RETURNING id", f"tenant-{suffix}"
            )
            company_space = await connection.fetchval(
                """
                INSERT INTO knowledge_spaces (tenant_id, scope, name)
                VALUES ($1,'company',$2) RETURNING id
                """,
                tenant_id, f"company-{suffix}",
            )
            await connection.execute(
                "INSERT INTO resource_grants (space_id,user_id,permission) VALUES ($1,$2,'read')",
                company_space, first_user,
            )
            legal_name = f"RLS测试组织-{suffix}"
            await connection.execute(
                """
                INSERT INTO organizations (
                    space_id, legal_name, organization_type, source_url
                ) VALUES ($1,$2,'test','https://example.com')
                """,
                company_space, legal_name,
            )
    finally:
        await connection.close()

    first = Identity(user_id=first_user, tenant_id=tenant_id)
    second = Identity(user_id=second_user)
    conversation_id = await PostgresArticleStore(database).create_conversation(first)
    job_id = uuid4()
    jobs = JobRepository(database)
    await jobs.enqueue(
        job_id=job_id,
        conversation_id=conversation_id,
        kind="start",
        payload={"conversation_id": str(conversation_id)},
        stage="researching",
        actor_id=first_user,
    )
    await jobs.enqueue(
        job_id=job_id,
        conversation_id=conversation_id,
        kind="start",
        payload={"conversation_id": str(conversation_id)},
        stage="generating",
        actor_id=first_user,
    )
    async with database.authorized(first) as authorized:
        assert await authorized.fetchval(
            "SELECT stage::text FROM conversations WHERE id=$1", conversation_id
        ) == "researching"
        assert await authorized.fetchval(
            "SELECT count(*) FROM workflow_events WHERE job_id=$1 AND event_type='queued'",
            job_id,
        ) == 1
    await PostgresProgressReporter(database).report(
        conversation_id=conversation_id,
        identity=first,
        stage="planning",
        code="outline",
        message="正在生成逐段大纲",
    )
    async with database.authorized(first) as authorized:
        assert await authorized.fetchval(
            "SELECT stage::text FROM conversations WHERE id=$1", conversation_id
        ) == "planning"
        progress = await authorized.fetchval(
            """
            SELECT payload FROM workflow_events
            WHERE conversation_id=$1 AND event_type='progress'
            ORDER BY sequence DESC LIMIT 1
            """,
            conversation_id,
        )
        assert progress["code"] == "outline"

    sql = (
        "SELECT legal_name FROM organizations "
        f"WHERE legal_name='{legal_name}' LIMIT 5"
    )
    assert await database.execute_readonly(sql, first) == [{"legal_name": legal_name}]
    assert await database.execute_readonly(sql, second) == []

    async with database.pool.acquire() as untrusted_connection:
        async with untrusted_connection.transaction():
            await untrusted_connection.execute(
                "SELECT set_config('app.is_admin', 'true', true)"
            )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await untrusted_connection.execute(
                    "INSERT INTO knowledge_spaces (scope, name) VALUES ('public','越权测试')"
                )

    async with database.authorized(first) as connection:
        personal_space = await connection.fetchval(
            """
            INSERT INTO knowledge_spaces (owner_id, scope, name)
            VALUES ($1,'personal',$2) RETURNING id
            """,
            first_user, f"personal-{suffix}",
        )
        document_id = await connection.fetchval(
            """
            INSERT INTO documents (
                space_id, external_id, version, title, content_hash, status
            ) VALUES ($1,$2,'1','隔离测试',$3,'published') RETURNING id
            """,
            personal_space, f"test-{suffix}", "0" * 64,
        )
        chunk_id = await connection.fetchval(
            """
            INSERT INTO document_chunks (document_id, position, content, content_hash)
            VALUES ($1,0,$2,$3) RETURNING id
            """,
            document_id, f"仅第一个用户可见-{suffix}", "1" * 64,
        )
    embedding = FixedEmbedding()
    await PostgresArticleStore(database).save_embedding(
        chunk_id=chunk_id,
        vector=await embedding.embed(""),
        model=embedding.model,
        dimensions=1024,
        identity=first,
    )
    search = PgVectorSearch(database, embedding)
    first_results = await search.semantic("测试", identity=first)
    second_results = await search.semantic("测试", identity=second)
    assert any(item.document_id == document_id for item in first_results)
    own_result = next(item for item in first_results if item.document_id == document_id)
    assert own_result.metadata["storage"] == "postgresql_pgvector"
    assert own_result.metadata["table"] == "chunk_embeddings"
    assert all(item.document_id != document_id for item in second_results)

    cleanup = await asyncpg.connect(owner_dsn)
    try:
        async with cleanup.transaction():
            await cleanup.execute("DELETE FROM users WHERE id=ANY($1::uuid[])", [first_user, second_user])
            await cleanup.execute("DELETE FROM tenants WHERE id=$1", tenant_id)
    finally:
        await cleanup.close()


@pytest.mark.asyncio
async def test_hr_business_terms_are_tenant_isolated_and_runtime_read_only(
    database: Database,
) -> None:
    owner_dsn = os.getenv("FORGE_INTEGRATION_OWNER_DSN")
    if not owner_dsn:
        pytest.skip("FORGE_INTEGRATION_OWNER_DSN 未设置")
    suffix = uuid4().hex
    owner = await asyncpg.connect(owner_dsn)
    try:
        async with owner.transaction():
            employee_id = await owner.fetchval(
                "INSERT INTO users (email,password_hash,account_type) "
                "VALUES ($1,'test','employee') RETURNING id",
                f"hr-employee-{suffix}@example.com",
            )
            outsider_id = await owner.fetchval(
                "INSERT INTO users (email,password_hash) VALUES ($1,'test') RETURNING id",
                f"hr-outsider-{suffix}@example.com",
            )
            tenant_id = await owner.fetchval(
                "INSERT INTO tenants (name) VALUES ($1) RETURNING id",
                f"hr-tenant-{suffix}",
            )
            space_id = await owner.fetchval(
                "INSERT INTO knowledge_spaces (tenant_id,scope,name) "
                "VALUES ($1,'company',$2) RETURNING id",
                tenant_id,
                f"hr-company-{suffix}",
            )
            await owner.execute(
                "INSERT INTO memberships (tenant_id,user_id,role) "
                "VALUES ($1,$2,'employee')",
                tenant_id,
                employee_id,
            )
            await owner.execute(
                "INSERT INTO resource_grants (space_id,user_id,permission) "
                "VALUES ($1,$2,'read')",
                space_id,
                employee_id,
            )
            source_id = await owner.fetchval(
                """
                INSERT INTO hr_term_sources (
                    space_id,source_kind,title,issuing_authority,source_url,
                    article_clause,source_quote,content_hash
                ) VALUES ($1,'department_rule',$2,'人力资源和社会保障部',
                    'https://www.mohrss.gov.cn/test','第四条','不得超过10%',$3)
                RETURNING id
                """,
                space_id,
                f"集成测试来源-{suffix}",
                "0" * 64,
            )
            await owner.execute(
                """
                INSERT INTO hr_business_terms (
                    space_id,term_code,term_name,domain,term_type,definition,
                    applicable_scope,mandatory_level,version,source_id
                ) VALUES ($1,$2,'劳务派遣用工比例','劳务派遣','legal_mandatory',
                    '被派遣劳动者不得超过用工总量的10%。','用工单位','部门规章','test',$3)
                """,
                space_id,
                f"HR_TEST_{suffix}",
                source_id,
            )
    finally:
        await owner.close()

    employee = Identity(user_id=employee_id, tenant_id=tenant_id)
    outsider = Identity(user_id=outsider_id)
    sql = (
        "SELECT term_name, definition FROM hr_business_terms "
        f"WHERE term_code='HR_TEST_{suffix}'"
    )
    assert await database.execute_readonly(sql, employee) == [{
        "term_name": "劳务派遣用工比例",
        "definition": "被派遣劳动者不得超过用工总量的10%。",
    }]
    assert await database.execute_readonly(sql, outsider) == []

    async with database.authorized(employee) as connection:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await connection.execute(
                "UPDATE hr_business_terms SET definition='越权修改' "
                "WHERE term_code=$1",
                f"HR_TEST_{suffix}",
            )

    cleanup = await asyncpg.connect(owner_dsn)
    try:
        async with cleanup.transaction():
            await cleanup.execute(
                "DELETE FROM users WHERE id=ANY($1::uuid[])",
                [employee_id, outsider_id],
            )
            await cleanup.execute("DELETE FROM tenants WHERE id=$1", tenant_id)
    finally:
        await cleanup.close()
