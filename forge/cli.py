from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

import asyncpg

from forge.api.auth import password_hash
from forge.config import get_settings
from forge.platform import run_async


async def migrate() -> None:
    connection = await asyncpg.connect(get_settings().migration_database_url)
    try:
        for path in sorted(Path("migrations").glob("*.sql")):
            exists = await connection.fetchval("SELECT to_regclass('public.schema_migrations')")
            if exists and await connection.fetchval(
                "SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE name=$1)", path.stem
            ):
                print(f"skipped {path}")
                continue
            await connection.execute(path.read_text(encoding="utf-8"))
            print(f"applied {path}")
    finally:
        await connection.close()


async def create_user(email: str, password: str, account_type: str) -> None:
    connection = await asyncpg.connect(get_settings().database_url)
    try:
        async with connection.transaction():
            user_id = await connection.fetchval(
                """
                INSERT INTO users (email, password_hash, account_type)
                VALUES ($1,$2,$3::account_type)
                ON CONFLICT (email) DO UPDATE SET password_hash=excluded.password_hash,
                    account_type=excluded.account_type
                RETURNING id
                """,
                email, password_hash.hash(password), account_type,
            )
            await connection.execute(
                "SELECT set_config('app.user_id', $1, true)", str(user_id)
            )
            await connection.execute(
                """
                INSERT INTO knowledge_spaces (owner_id, scope, name)
                SELECT $1, 'personal', '个人历史知识库'
                WHERE NOT EXISTS (
                    SELECT 1 FROM knowledge_spaces WHERE owner_id=$1 AND scope='personal'
                )
                """,
                user_id,
            )
            print(f"user {user_id} ready")
    finally:
        await connection.close()


async def import_seed(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    connection = await asyncpg.connect(get_settings().migration_database_url)
    try:
        async with connection.transaction():
            space_id = await connection.fetchval(
                "SELECT id FROM knowledge_spaces WHERE scope='public' ORDER BY created_at LIMIT 1"
            )
            if space_id is None:
                space_id = await connection.fetchval(
                    "INSERT INTO knowledge_spaces (scope, name) VALUES ('public','公开公司资料') RETURNING id"
                )
            ids: dict[str, object] = {}
            for organization in data["organizations"]:
                ids[organization["key"]] = await connection.fetchval(
                    """
                    INSERT INTO organizations (
                        space_id, legal_name, organization_type, region, source_url, verified_at
                    ) VALUES ($1,$2,$3,$4,$5,now())
                    ON CONFLICT (space_id, legal_name, registration_code)
                    DO UPDATE SET source_url=excluded.source_url, verified_at=now()
                    RETURNING id
                    """,
                    space_id, organization["legal_name"], organization["organization_type"],
                    organization.get("region", ""), organization["source_url"],
                )
            for relation in data.get("relations", []):
                await connection.execute(
                    """
                    INSERT INTO organization_relations (
                        space_id, subject_id, object_id, relation_type, confidence, source_url, source_quote
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7)
                    ON CONFLICT (space_id, subject_id, object_id, relation_type, source_url)
                    DO UPDATE SET confidence=excluded.confidence, source_quote=excluded.source_quote
                    """,
                    space_id, ids[relation["subject"]], ids[relation["object"]],
                    relation["relation_type"], relation["confidence"],
                    relation["source_url"], relation["source_quote"],
                )
            for alias in data.get("aliases", []):
                await connection.execute(
                    """
                    INSERT INTO organization_aliases (
                        space_id, organization_id, alias, normalized_alias
                    ) VALUES ($1,$2,$3,$4)
                    ON CONFLICT (organization_id, normalized_alias)
                    DO UPDATE SET alias=excluded.alias
                    """,
                    space_id, ids[alias["organization"]], alias["alias"],
                    alias["normalized_alias"],
                )
            for offering in data.get("business_offerings", []):
                await connection.execute(
                    """
                    INSERT INTO business_offerings (
                        space_id, organization_id, name, description, source_url, source_quote
                    ) VALUES ($1,$2,$3,$4,$5,$6)
                    ON CONFLICT (space_id, organization_id, name, source_url)
                    DO UPDATE SET description=excluded.description, source_quote=excluded.source_quote
                    """,
                    space_id, ids[offering["organization"]], offering["name"],
                    offering["description"], offering["source_url"], offering["source_quote"],
                )
        print(f"imported {path}")
    finally:
        await connection.close()


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _validate_official_source_url(value: str) -> str:
    parsed = urlparse(value)
    host = (parsed.hostname or "").casefold()
    if parsed.scheme != "https" or not (host == "gov.cn" or host.endswith(".gov.cn")):
        raise ValueError(f"人力资源口径只接受中国政府官方 HTTPS 来源：{value}")
    return value


async def import_hr_terms(path: Path, tenant_id: UUID) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    connection = await asyncpg.connect(get_settings().migration_database_url)
    try:
        async with connection.transaction():
            space_id = await connection.fetchval(
                """
                SELECT id FROM knowledge_spaces
                WHERE tenant_id=$1 AND scope='company'
                ORDER BY created_at LIMIT 1
                """,
                tenant_id,
            )
            if space_id is None:
                raise ValueError("租户不存在公司知识空间")

            source_ids: dict[str, UUID] = {}
            for source in data["sources"]:
                source_quote = source["source_quote"].strip()
                source_url = _validate_official_source_url(source["source_url"])
                digest = hashlib.sha256(
                    (source_url + "\n" + source_quote).encode("utf-8")
                ).hexdigest()
                source_ids[source["key"]] = await connection.fetchval(
                    """
                    INSERT INTO hr_term_sources (
                        space_id, source_kind, title, issuing_authority,
                        document_number, source_url, article_clause, published_on,
                        effective_from, effective_to, source_quote, content_hash,
                        verification_status
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                    ON CONFLICT (space_id, source_url, article_clause) DO UPDATE SET
                        source_kind=excluded.source_kind,
                        title=excluded.title,
                        issuing_authority=excluded.issuing_authority,
                        document_number=excluded.document_number,
                        published_on=excluded.published_on,
                        effective_from=excluded.effective_from,
                        effective_to=excluded.effective_to,
                        source_quote=excluded.source_quote,
                        content_hash=excluded.content_hash,
                        retrieved_at=now(),
                        verification_status=excluded.verification_status
                    RETURNING id
                    """,
                    space_id,
                    source["source_kind"],
                    source["title"],
                    source["issuing_authority"],
                    source.get("document_number", ""),
                    source_url,
                    source.get("article_clause", ""),
                    _date(source.get("published_on")),
                    _date(source.get("effective_from")),
                    _date(source.get("effective_to")),
                    source_quote,
                    digest,
                    source.get("verification_status", "verified"),
                )

            term_ids: dict[str, UUID] = {}
            for term in data["terms"]:
                term_ids[term["term_code"]] = await connection.fetchval(
                    """
                    INSERT INTO hr_business_terms (
                        space_id, term_code, term_name, domain, term_type,
                        definition, calculation_rule, unit, applicable_scope,
                        exclusions, mandatory_level, status, effective_from,
                        effective_to, version, source_id, reviewed_at
                    ) VALUES (
                        $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,now()
                    )
                    ON CONFLICT (space_id, term_code, version) DO UPDATE SET
                        term_name=excluded.term_name,
                        domain=excluded.domain,
                        term_type=excluded.term_type,
                        definition=excluded.definition,
                        calculation_rule=excluded.calculation_rule,
                        unit=excluded.unit,
                        applicable_scope=excluded.applicable_scope,
                        exclusions=excluded.exclusions,
                        mandatory_level=excluded.mandatory_level,
                        status=excluded.status,
                        effective_from=excluded.effective_from,
                        effective_to=excluded.effective_to,
                        source_id=excluded.source_id,
                        reviewed_at=now()
                    RETURNING id
                    """,
                    space_id,
                    term["term_code"],
                    term["term_name"],
                    term["domain"],
                    term["term_type"],
                    term["definition"],
                    term.get("calculation_rule", ""),
                    term.get("unit", ""),
                    term["applicable_scope"],
                    term.get("exclusions", ""),
                    term["mandatory_level"],
                    term.get("status", "verified"),
                    _date(term.get("effective_from")),
                    _date(term.get("effective_to")),
                    term["version"],
                    source_ids[term["source_key"]],
                )

            for obligation in data.get("obligations", []):
                await connection.execute(
                    """
                    INSERT INTO hr_compliance_obligations (
                        space_id, obligation_code, actor_type, trigger_event,
                        required_action, deadline_value, deadline_unit,
                        threshold_expression, exception_rule, article_clause,
                        effective_from, effective_to, version, source_id
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
                    ON CONFLICT (space_id, obligation_code, version) DO UPDATE SET
                        actor_type=excluded.actor_type,
                        trigger_event=excluded.trigger_event,
                        required_action=excluded.required_action,
                        deadline_value=excluded.deadline_value,
                        deadline_unit=excluded.deadline_unit,
                        threshold_expression=excluded.threshold_expression,
                        exception_rule=excluded.exception_rule,
                        article_clause=excluded.article_clause,
                        effective_from=excluded.effective_from,
                        effective_to=excluded.effective_to,
                        source_id=excluded.source_id
                    """,
                    space_id,
                    obligation["obligation_code"],
                    obligation["actor_type"],
                    obligation["trigger_event"],
                    obligation["required_action"],
                    obligation.get("deadline_value"),
                    obligation.get("deadline_unit", ""),
                    obligation.get("threshold_expression", ""),
                    obligation.get("exception_rule", ""),
                    obligation.get("article_clause", ""),
                    _date(obligation.get("effective_from")),
                    _date(obligation.get("effective_to")),
                    obligation["version"],
                    source_ids[obligation["source_key"]],
                )

            for parameter in data.get("regional_parameters", []):
                await connection.execute(
                    """
                    INSERT INTO hr_regional_parameters (
                        space_id, term_id, region_code, employment_type,
                        parameter_name, value_numeric, value_text, unit,
                        valid_from, valid_to, source_id
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                    ON CONFLICT (
                        space_id, term_id, region_code, employment_type,
                        parameter_name, valid_from
                    ) DO UPDATE SET
                        value_numeric=excluded.value_numeric,
                        value_text=excluded.value_text,
                        unit=excluded.unit,
                        valid_to=excluded.valid_to,
                        source_id=excluded.source_id
                    """,
                    space_id,
                    term_ids[parameter["term_code"]],
                    parameter["region_code"],
                    parameter["employment_type"],
                    parameter["parameter_name"],
                    parameter.get("value_numeric"),
                    parameter.get("value_text", ""),
                    parameter["unit"],
                    _date(parameter["valid_from"]),
                    _date(parameter.get("valid_to")),
                    source_ids[parameter["source_key"]],
                )

            for classification in data.get("classifications", []):
                await connection.execute(
                    """
                    INSERT INTO hr_classification_codes (
                        space_id, classification_system, code, parent_code,
                        name, definition, inclusions, exclusions, version,
                        valid_from, valid_to, source_id
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                    ON CONFLICT (space_id, classification_system, code, version)
                    DO UPDATE SET
                        parent_code=excluded.parent_code,
                        name=excluded.name,
                        definition=excluded.definition,
                        inclusions=excluded.inclusions,
                        exclusions=excluded.exclusions,
                        valid_from=excluded.valid_from,
                        valid_to=excluded.valid_to,
                        source_id=excluded.source_id
                    """,
                    space_id,
                    classification["classification_system"],
                    classification["code"],
                    classification.get("parent_code", ""),
                    classification["name"],
                    classification["definition"],
                    classification.get("inclusions", ""),
                    classification.get("exclusions", ""),
                    classification["version"],
                    _date(classification.get("valid_from")),
                    _date(classification.get("valid_to")),
                    source_ids[classification["source_key"]],
                )

        print(
            f"imported {len(data['terms'])} HR terms, "
            f"{len(data.get('obligations', []))} obligations and "
            f"{len(data.get('classifications', []))} classifications into {space_id}"
        )
    finally:
        await connection.close()


async def create_tenant(name: str) -> None:
    connection = await asyncpg.connect(get_settings().migration_database_url)
    try:
        async with connection.transaction():
            tenant_id = await connection.fetchval(
                "INSERT INTO tenants (name) VALUES ($1) RETURNING id", name
            )
            await connection.execute(
                "INSERT INTO knowledge_spaces (tenant_id, scope, name) VALUES ($1,'company',$2)",
                tenant_id, f"{name}公司知识库",
            )
        print(f"tenant {tenant_id} ready")
    finally:
        await connection.close()


async def add_member(email: str, tenant_id: UUID, role: str) -> None:
    connection = await asyncpg.connect(get_settings().migration_database_url)
    try:
        async with connection.transaction():
            user_id = await connection.fetchval(
                "SELECT id FROM users WHERE lower(email)=lower($1)", email
            )
            if user_id is None:
                raise ValueError("用户不存在")
            await connection.execute(
                """
                INSERT INTO memberships (tenant_id, user_id, role)
                VALUES ($1,$2,$3)
                ON CONFLICT (tenant_id, user_id) DO UPDATE
                SET role=excluded.role, active=true
                """,
                tenant_id, user_id, role,
            )
            await connection.execute(
                """
                INSERT INTO resource_grants (space_id, user_id, permission)
                SELECT id, $2, 'read' FROM knowledge_spaces
                WHERE tenant_id=$1 AND scope='company'
                ON CONFLICT DO NOTHING
                """,
                tenant_id, user_id,
            )
            await connection.execute(
                "UPDATE users SET account_type='employee' WHERE id=$1", user_id
            )
        print(f"member {user_id} added")
    finally:
        await connection.close()


async def prune_traces() -> None:
    settings = get_settings()
    connection = await asyncpg.connect(settings.migration_database_url)
    try:
        result = await connection.execute(
            "DELETE FROM trace_events WHERE created_at < now() - $1::interval",
            timedelta(days=settings.trace_retention_days),
        )
        print(f"trace retention {settings.trace_retention_days} days: {result}")
    finally:
        await connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="forge")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate")
    commands.add_parser("prune-traces")
    user = commands.add_parser("create-user")
    user.add_argument("email")
    user.add_argument("password")
    user.add_argument("--type", choices=("guest", "employee", "admin"), default="guest")
    seed = commands.add_parser("import-seed")
    seed.add_argument("path", type=Path)
    hr_seed = commands.add_parser("import-hr-terms")
    hr_seed.add_argument("path", type=Path)
    hr_seed.add_argument("tenant_id", type=UUID)
    tenant = commands.add_parser("create-tenant")
    tenant.add_argument("name")
    member = commands.add_parser("add-member")
    member.add_argument("email")
    member.add_argument("tenant_id", type=UUID)
    member.add_argument("--role", default="employee")
    args = parser.parse_args()
    if args.command == "migrate":
        run_async(migrate())
    elif args.command == "prune-traces":
        run_async(prune_traces())
    elif args.command == "create-user":
        run_async(create_user(args.email, args.password, args.type))
    elif args.command == "import-seed":
        run_async(import_seed(args.path))
    elif args.command == "import-hr-terms":
        run_async(import_hr_terms(args.path, args.tenant_id))
    elif args.command == "create-tenant":
        run_async(create_tenant(args.name))
    else:
        run_async(add_member(args.email, args.tenant_id, args.role))


if __name__ == "__main__":
    main()
