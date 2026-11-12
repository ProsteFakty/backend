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

    embedding_model: str = "BAAI/bge-m3"  # multilingual (Polish), 8192-token context
    # POST /api/v1/articles/search: how many articles and the minimum cosine similarity to return
    search_top_k: int = 3
    search_min_score: float = 0.4

    @property
    def is_local(self) -> bool:
        return self.app_env == "local"

    @property
    def async_database_url(self) -> str:
        return self.database_url.replace("postgresql://", "postgresql+psycopg://", 1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
