"""应用配置。

`APP_SECRET` 必须是 32 字节 CSPRNG 随机值的 base64url 表示（无 padding，43 字符），
否则启动失败。缺失、长度不符或仍为 `.env.example` 默认值时直接拒绝，不降级为警告。
"""

from __future__ import annotations

import base64
import binascii
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .constants import (
    HUMAN_TIMEOUT_DEFAULT_SECONDS,
    HUMAN_TIMEOUT_MAX_SECONDS,
    HUMAN_TIMEOUT_MIN_SECONDS,
)

_DEFAULT_APP_SECRET = "replace-with-a-long-random-secret"


class Settings(BaseSettings):
    app_name: str = "human-llm-gateway"
    database_url: str = "sqlite:///./data/human_llm_gateway.db"
    app_secret: str = _DEFAULT_APP_SECRET
    admin_username: str = "admin"
    admin_password: str = "change-me-now"
    human_timeout_seconds: int = HUMAN_TIMEOUT_DEFAULT_SECONDS
    stream_chunk_size: int = 24
    stream_delay_min_ms: int = 20
    stream_delay_max_ms: int = 90
    allow_plain_human_reply: bool = True
    binding_code_ttl_seconds: int = 300
    # 私有/回环上游默认拒绝；自建网关连本机 Ollama/内网 vLLM 时显式开启
    # （云元数据段无论开关一律拒绝，见 app/core/ssrf.py）。
    llm_allow_private_upstream: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("app_secret")
    @classmethod
    def _validate_app_secret(cls, value: str) -> str:
        if value == _DEFAULT_APP_SECRET:
            raise ValueError(
                "APP_SECRET 仍是 .env.example 默认值，必须替换为 32 字节随机值的 base64url"
            )
        try:
            raw = base64.urlsafe_b64decode(value + "==")
        except (binascii.Error, ValueError) as exc:
            raise ValueError("APP_SECRET 不是合法的 base64url") from exc
        if len(raw) != 32:
            raise ValueError("APP_SECRET 必须解码为 32 字节")
        # 要求无 padding 的规范 base64url，避免 abc= 与 abc 两种表示并存。
        if base64.urlsafe_b64encode(raw).rstrip(b"=").decode() != value:
            raise ValueError("APP_SECRET 必须是无 padding 的规范 base64url（43 字符）")
        return value

    def ensure_data_dir(self) -> None:
        if self.database_url.startswith("sqlite:///"):
            path = Path(self.database_url.removeprefix("sqlite:///"))
            path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def human_timeout_in_range(self) -> bool:
        return HUMAN_TIMEOUT_MIN_SECONDS <= self.human_timeout_seconds <= HUMAN_TIMEOUT_MAX_SECONDS


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_data_dir()
    return settings
