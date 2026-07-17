from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"
    log_level: str = "INFO"
    api_key: SecretStr | None = None
    database_url: str = "postgresql+asyncpg://teampulse:teampulse@localhost:5432/teampulse"
    redis_url: str = "redis://localhost:6379/0"
    figma_webhook_passcode: SecretStr = Field(default=SecretStr("development-only-figma"))
    figma_access_token: SecretStr | None = None
    notion_webhook_verification_token: SecretStr = Field(
        default=SecretStr("development-only-notion")
    )
    notion_access_token: SecretStr | None = None
    discord_bot_token: SecretStr | None = None
    token_encryption_key: SecretStr | None = None
    ai_summarizer_url: str | None = None
    ai_summarizer_api_key: SecretStr | None = None
    ai_summarizer_model: str = "gpt-4.1-mini"
    daily_brief_hour: int = 18
    daily_brief_minute: int = 0


@lru_cache
def get_settings() -> Settings:
    return Settings()
