from __future__ import annotations

from dataclasses import dataclass
import asyncio
from typing import Any
from redis.asyncio import Redis
from forge.infrastructure.model_limits import Budget, SharedModelLimiter

from forge.config import Settings, get_settings
from forge.infrastructure.jobs import JobRepository
from forge.infrastructure.model import EmbeddingGateway, OpenAICompatibleModel
from forge.infrastructure.postgres import Database, PgVectorSearch, PostgresArticleStore
from forge.infrastructure.projection import PostgresProgressReporter, WorkflowProjection
from forge.infrastructure.search import CompositeSearch
from forge.knowledge.research import EvidenceResearcher
from forge.knowledge.article_skills import ArticleTypeSkill
from forge.knowledge.fact_skill import ArticleFactsSkill
from forge.knowledge.text2sql import QueryPolicy, ReadOnlySqlGuard, SkillLoader, Text2SqlSkill
from forge.workflow.executor import RemoteLangGraphExecutor
from forge.workflow.graph import build_workflow
from forge.workflow.nodes import WorkflowNodes
from forge.infrastructure.telemetry import PostgresTraceRecorder, configure_logging


@dataclass
class Runtime:
    settings: Settings
    database: Database
    store: PostgresArticleStore
    jobs: JobRepository
    projection: WorkflowProjection
    telemetry: PostgresTraceRecorder
    executor: Any = None
    embeddings: EmbeddingGateway | None = None
    compiled_graph: Any = None
    redis: Redis | None = None

    async def close(self) -> None:
        if self.executor is not None:
            await self.executor.close()
        if self.redis is not None:
            await self.redis.aclose()
        await self.database.close()


async def build_runtime(
    *, with_graph: bool = False, with_embeddings: bool = False
) -> Runtime:
    configure_logging()
    settings = get_settings()
    from forge.infrastructure.metrics import start
    await asyncio.to_thread(start, settings)
    database = Database(settings.database_url)
    await database.open()
    jobs = JobRepository(database)
    runtime = Runtime(
        settings=settings,
        database=database,
        store=PostgresArticleStore(database),
        jobs=jobs,
        projection=WorkflowProjection(database),
        telemetry=PostgresTraceRecorder(database),
        executor=RemoteLangGraphExecutor(
            server_url=settings.langgraph_server_url,
            assistant_id=settings.langgraph_assistant_id,
        ),
    )
    if not with_graph and not with_embeddings:
        return runtime

    runtime.redis = Redis.from_url(settings.redis_url, socket_connect_timeout=3, socket_timeout=3)
    budgets = {}
    for role, name in (("main", settings.llm_model), ("router", settings.skill_router_model),
                       ("embedding", settings.embedding_model)):
        budget = Budget(*(getattr(settings, f"model_{role}_{suffix}")
                          for suffix in ("rpm", "tpm", "concurrency")))
        if name in budgets:
            old = budgets[name]
            budget = Budget(min(old.rpm, budget.rpm), min(old.tpm, budget.tpm),
                            min(old.concurrency, budget.concurrency))
        budgets[name] = budget

    def limiter(name, timeout):
        return SharedModelLimiter(runtime.redis, provider=settings.llm_base_url, model=name,
                                  budget=budgets[name], wait_seconds=settings.model_limit_wait_seconds,
                                  timeout_seconds=timeout)

    embeddings = EmbeddingGateway(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
        limiter=limiter(settings.embedding_model, settings.llm_timeout_seconds),
        timeout=settings.llm_timeout_seconds,
    )
    runtime.embeddings = embeddings
    if not with_graph:
        return runtime

    model = OpenAICompatibleModel(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        timeout=settings.llm_timeout_seconds,
        limiter=limiter(settings.llm_model, settings.llm_timeout_seconds),
        max_output_tokens=settings.model_max_output_tokens,
    )
    router_model = OpenAICompatibleModel(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.skill_router_model,
        timeout=min(settings.llm_timeout_seconds, 30),
        limiter=limiter(settings.skill_router_model, min(settings.llm_timeout_seconds, 30)),
        max_output_tokens=1024,
    )
    vector = PgVectorSearch(database, embeddings)
    search = CompositeSearch(
        tavily_api_key=settings.tavily_api_key,
        vector_search=vector,
        max_results=settings.tavily_max_results,
    )
    policy = QueryPolicy(
        tables={
            "organizations": frozenset({"id", "legal_name", "organization_type", "region", "registration_code", "source_url", "verified_at", "valid_from", "valid_to"}),
            "organization_aliases": frozenset({"organization_id", "alias", "normalized_alias", "valid_from", "valid_to"}),
            "organization_relations": frozenset({"subject_id", "object_id", "relation_type", "confidence", "valid_from", "valid_to", "source_url", "source_quote"}),
            "business_offerings": frozenset({"organization_id", "name", "description", "region", "valid_from", "valid_to", "source_url", "source_quote"}),
            "qualifications": frozenset({"organization_id", "name", "certificate_number", "issuer", "valid_from", "valid_to", "source_url", "source_quote"}),
            "legal_references": frozenset({"title", "jurisdiction", "article_number", "source_url", "source_quote", "effective_from", "effective_to"}),
            "hr_term_sources": frozenset({"id", "source_kind", "title", "issuing_authority", "document_number", "source_url", "article_clause", "published_on", "effective_from", "effective_to", "source_quote", "verification_status"}),
            "hr_business_terms": frozenset({"id", "term_code", "term_name", "domain", "term_type", "definition", "calculation_rule", "unit", "applicable_scope", "exclusions", "mandatory_level", "status", "effective_from", "effective_to", "version", "source_id"}),
            "hr_compliance_obligations": frozenset({"id", "obligation_code", "actor_type", "trigger_event", "required_action", "deadline_value", "deadline_unit", "threshold_expression", "exception_rule", "article_clause", "effective_from", "effective_to", "version", "source_id"}),
            "hr_regional_parameters": frozenset({"id", "term_id", "region_code", "employment_type", "parameter_name", "value_numeric", "value_text", "unit", "valid_from", "valid_to", "source_id"}),
            "hr_classification_codes": frozenset({"id", "classification_system", "code", "parent_code", "name", "definition", "inclusions", "exclusions", "version", "valid_from", "valid_to", "source_id"}),
        },
        max_rows=settings.sql_result_limit,
    )
    company = Text2SqlSkill(
        model=model,
        loader=await asyncio.to_thread(SkillLoader, settings.skill_root),
        router_model=router_model,
        guard=ReadOnlySqlGuard(policy),
        execute=lambda sql, identity: database.execute_readonly(
            sql, identity, timeout_ms=settings.sql_timeout_ms
        ),
    )
    fact_skill = await asyncio.to_thread(ArticleFactsSkill, settings.article_facts_skill_root)
    fact_policy = await fact_skill.load()
    researcher = EvidenceResearcher(model, search, company, fact_policy)
    nodes = WorkflowNodes(
        model=model,
        researcher=researcher,
        search=search,
        company=company,
        article_types=await asyncio.to_thread(ArticleTypeSkill, model=model, root=settings.article_skill_root),
        progress=PostgresProgressReporter(database),
        critic_max_failures=settings.critic_max_failures,
        fact_policy=fact_policy,
        fact_judge=router_model,
    )
    runtime.compiled_graph = build_workflow(nodes, recorder=runtime.telemetry).compile()
    return runtime
