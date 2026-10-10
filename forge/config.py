from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    database_url: str = "postgresql://forge_app:forge_app@127.0.0.1:5432/forge"
    migration_database_url: str = "postgresql://forge_owner:forge_owner@127.0.0.1:5432/forge"
    jwt_secret: str = Field(default="development-only-change-this-secret", min_length=32)
    jwt_issuer: str = "forge"
    jwt_audience: str = "forge-api"

    llm_api_key: str = ""
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen3.7-max"
    skill_router_model: str = "qwen-turbo"
    embedding_model: str = "text-embedding-v3"
    embedding_dimensions: int = 1024
    llm_timeout_seconds: int = 180
    redis_url: str = "redis://127.0.0.1:6379/0"
    model_main_rpm: int = Field(default=20, ge=1)
    model_main_tpm: int = Field(default=100000, ge=1)
    model_main_concurrency: int = Field(default=3, ge=1)
    model_router_rpm: int = Field(default=30, ge=1)
    model_router_tpm: int = Field(default=30000, ge=1)
    model_router_concurrency: int = Field(default=2, ge=1)
    model_embedding_rpm: int = Field(default=30, ge=1)
    model_embedding_tpm: int = Field(default=60000, ge=1)
    model_embedding_concurrency: int = Field(default=2, ge=1)
    model_limit_wait_seconds: float = Field(default=180, gt=0)
    model_max_output_tokens: int = Field(default=4096, ge=1)
    graph_max_concurrency: int = Field(default=3, ge=1)
    index_max_concurrency: int = Field(default=1, ge=1)
    audit_max_concurrency: int = Field(default=1, ge=1)
    consumer_kind: Literal["start", "index_document", "audit_job"] = "start"
    metrics_port: int = Field(default=9001, ge=1, le=65535)

    tavily_api_key: str = ""
    tavily_max_results: int = 5
    critic_max_failures: int = 3
    consumer_max_attempts: int = 3
    sql_timeout_ms: int = 5000
    sql_result_limit: int = 100
    skill_root: Path = Path("skills/company_knowledge")
    article_skill_root: Path = Path("skills/article_types")
    article_facts_skill_root: Path = Path("skills/article_facts")

    langgraph_server_url: str = "http://127.0.0.1:8123"
    langgraph_assistant_id: str = "forge"
    langgraph_reconcile_seconds: float = 1.0

    rocketmq_bridge_url: str = "http://127.0.0.1:8090"
    rocketmq_bridge_token: str = "development-rocketmq-bridge-token"
    rocketmq_publish_timeout_seconds: float = 10.0
    rocketmq_consumer_secret: str = "development-rocketmq-consumer-secret"
    outbox_poll_seconds: float = 0.5
    outbox_batch_size: int = 50
    outbox_max_attempts: int = 3
    trace_retention_days: int = Field(default=30, ge=1)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
