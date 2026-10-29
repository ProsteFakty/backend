from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Defaults to prod so a missing APP_ENV never exposes Swagger.
    app_env: Literal["local", "prod"] = "prod"
    database_url: str = ""
    qdrant_url: str = "http://qdrant:6333"
    gemini_api_key: str = ""

    @property
    def is_local(self) -> bool:
        return self.app_env == "local"


@lru_cache
def get_settings() -> Settings:
    return Settings()
