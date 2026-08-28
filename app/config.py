from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "human-llm-gateway"
    database_url: str = "sqlite:///./data/human_llm_gateway.db"
    app_secret: str = "development-only-secret"
    admin_username: str = "admin"
    admin_password: str = "change-me-now"
    human_timeout_seconds: float = 300.0
    stream_chunk_size: int = 24
    stream_delay_min_ms: int = 20
    stream_delay_max_ms: int = 90
    allow_plain_human_reply: bool = True
    binding_code_ttl_seconds: int = 300

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    def ensure_data_dir(self) -> None:
        if self.database_url.startswith("sqlite:///"):
            path = Path(self.database_url.removeprefix("sqlite:///"))
            path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_data_dir()
    return settings
