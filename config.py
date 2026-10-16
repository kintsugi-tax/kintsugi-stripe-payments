from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

LogFormat = Literal["console", "json"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    stripe_api_key: str
    stripe_publishable_key: str
    stripe_webhook_secret: str
    stripe_pass_processing_fee_to_customer: bool = True
    stripe_processing_fee_percent: float = 0.029
    stripe_processing_fee_fixed_cents: int = 30
    kintsugi_api_key: str
    kintsugi_organization_id: str
    # Redirect URLs for hosted checkout must use one of these origins. They come
    # from server config, never from request headers, which the caller controls.
    # The first entry builds the default success and cancel URLs.
    allowed_redirect_origins: list[str] = ["http://localhost:8000"]
    log_level: str = "INFO"
    log_format: LogFormat = "console"


settings = Settings()
