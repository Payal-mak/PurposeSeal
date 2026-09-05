from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PURPOSESEAL_", env_file=".env", extra="ignore")

    app_name: str = "PurposeSeal"
    database_url: str = "sqlite:///./purposeseal.db"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
