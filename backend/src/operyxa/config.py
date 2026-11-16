"""Runtime configuration. Every value comes from the environment (see .env.example)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Works from the repository root and from backend/.
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    environment: Literal["local", "ci", "staging", "production"] = "local"
    database_url: str = "postgresql+psycopg://operyxa:operyxa@localhost:5432/operyxa"

    jwt_secret_key: str = "change-me"
    jwt_ttl_minutes: int = 60
    # Encrypts per-project GitHub tokens at rest. Required to connect private repositories.
    secrets_key: str = ""
    cors_origins: list[str] = ["http://localhost:5173"]

    # LLM. "demo" is a deterministic offline model: no key, no network, no cost.
    # "openai_compatible" covers NVIDIA NIM (the default base URL) and any OpenAI-style API.
    llm_provider: Literal["gemini", "openai_compatible", "demo"] = "demo"
    llm_base_url: str = "https://integrate.api.nvidia.com/v1"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_fallback_model: str = ""
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    # Tried when the main model stays unavailable after retries (overload, quota).
    gemini_fallback_model: str = ""
    llm_max_retries: int = 3
    llm_timeout_seconds: float = 90.0
    # Client-side throttle per model. The Gemini free tier allows 5 requests per minute.
    # 0 disables it (paid tiers).
    llm_requests_per_minute: int = 0
    llm_retry_base_seconds: float = 2.0
    # USD per 1M tokens, e.g. {"gemini-3.8-flash": {"input": 0.3, "output": 2.5}}.
    # Prices move: they are configuration, never constants in code.
    llm_pricing: dict[str, dict[str, float]] = Field(default_factory=dict)

    # Per-run budgets, enforced by the server (never by the prompt).
    max_agent_steps: int = 20  # LLM calls per run
    max_tool_calls: int = 40
    max_run_seconds: int = 600
    max_run_cost_usd: float = 2.0
    max_plan_steps: int = 4
    max_turns_per_step: int = 8
    max_parallel_steps: int = 3

    # Tool gateway
    tool_timeout_seconds: float = 30.0
    tool_max_retries: int = 2
    tool_output_max_chars: int = 40_000
    breaker_failure_threshold: int = 4
    breaker_reset_seconds: float = 30.0

    # Human approvals for high-risk tools
    approval_ttl_seconds: int = 600
    approval_poll_seconds: float = 1.0

    # MCP servers. Empty URL = run the server in-process (default for local/CI).
    mcp_github_url: str = ""
    mcp_analysis_url: str = ""
    # Vulnerability database. Only package names and versions are sent. Empty URL disables it.
    osv_api_url: str = "https://api.osv.dev"
    github_api_url: str = "https://api.github.com"
    github_token: str = ""

    # Worker
    worker_poll_seconds: float = 1.0
    worker_lease_seconds: int = 60
    worker_concurrency: int = 2


@lru_cache
def get_settings() -> Settings:
    return Settings()
