from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Telegram
    bot_token: str
    webhook_base_url: str
    webhook_path: str = "/telegram/webhook"
    webhook_secret: str

    # Database
    database_url: str  # postgresql+asyncpg://...

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # Bot roles
    operator_group_id: int
    admin_telegram_id: int

    # Payouts
    operator_payout_percent: float = 70.0  # % of payment_amount a regular operator receives
    admin_payout_percent: float = 100.0    # % when an admin completes the order themselves

    # Robokassa
    robokassa_login: str
    robokassa_pass1: str
    robokassa_pass2: str
    robokassa_is_test: bool = True

    # Marketing
    # URL of your Avito profile/listing — used to nudge avito-source clients to leave a review
    # on Avito after submitting an internal review. Leave empty to disable the nudge.
    avito_profile_url: str = ""

    @property
    def webhook_full_url(self) -> str:
        return f"{self.webhook_base_url}{self.webhook_path}"

    @property
    def sync_database_url(self) -> str:
        """Sync URL for APScheduler jobstore (postgresql://, not postgresql+asyncpg://)."""
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://")


settings = Settings()
