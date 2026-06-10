from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    stripe_api_key: str
    stripe_webhook_secret: str
    kintsugi_api_key: str
    kintsugi_organization_id: str


settings = Settings()
