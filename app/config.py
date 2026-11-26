from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Defaults to prod so a missing APP_ENV never exposes Swagger.
    app_env: Literal["local", "prod"] = "prod"
    database_url: str = ""
    qdrant_url: str = "http://qdrant:6333"
    gemini_api_key: str = ""
    sejm_term: int = 10
    # ONNX face models are downloaded here on first use (gitignored, survives via the bind mount).
    models_dir: str = "models"
    # SFace cosine similarity; 0.363 is the threshold recommended by the model authors.
    face_match_threshold: float = 0.363
    gemini_model: str = "gemini-3.7-flash"

    sejm_api_url: str = "https://api.sejm.gov.pl/sejm"
    # PDFs of acts downloaded by scripts.ingest_acts (gitignored, survives via the bind mount).
    acts_data_dir: str = "data/acts"
    # Copies of Sejm API data (MP list, photos) for its outages; gitignored, survives via the bind mount.
    sejm_cache_dir: str = "data/sejm"

    embedding_model: str = "BAAI/bge-m3"  # multilingual (Polish), 8192-token context
    # POST /api/v1/articles/search: minimum cosine similarity for a semantic hit. With bge-m3 an unrelated
    # query scores up to ~0.37 and clearly related articles 0.5+; recheck after changing what is embedded.
    search_min_score: float = 0.5

    # GET /api/v1/politicians/{mp_id}/news (docs/specs/mp-media-news.md)
    mp_news_ttl_hours: float = 24
    mp_news_retry_minutes: float = 60  # no new Claude call for an MP this long after a failed one
    mp_news_timeout_seconds: float = 90  # whole fetch incl. retries; the first view of an MP waits for it
    mp_news_model: str = "claude-haiku-5-5"
    mp_news_effort: Literal["low", "medium", "high"] = "low"
    mp_news_max_searches: int = 3  # web searches per fetch; each costs claude_web_search_usd
    # Never searched, and sources from these domains (and subdomains) are dropped.
    mp_news_blocked_domains: list[str] = []

    anthropic_api_key: str = ""
    # Hard cap on Claude spending per calendar month (UTC), tracked in the llm_usage table.
    claude_monthly_budget_usd: float = 5.0
    claude_web_search_usd: float = 0.01  # $10 per 1000 searches

    @property
    def is_local(self) -> bool:
        return self.app_env == "local"

    @property
    def async_database_url(self) -> str:
        return self.database_url.replace("postgresql://", "postgresql+psycopg://", 1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
