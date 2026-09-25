from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import computed_field
import os


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(__file__), "..", ".env"),
        case_sensitive=False,
        extra="ignore",
    )

    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_testnet_api_key: str = ""
    binance_testnet_api_secret: str = ""
    use_testnet: bool = False
    binance_region: str = "binance.com"

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "insidetrader"
    postgres_user: str = "insidetrader"
    postgres_password: str = "changeme"

    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = ""

    cors_allowed_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    dashboard_secret_key: str = "change_this_secret"
    dashboard_username: str = "admin"
    dashboard_password: str = "changeme"

    paper_trading_mode: bool = True
    live_trading_enabled: bool = False

    starting_capital_usdt: float = 100.0

    daily_target_percent: float = 2.0
    max_concurrent_trades: int = 5
    max_daily_trades: int = 200
    confidence_threshold: float = 0.75
    stop_loss_percent: float = 2.0
    take_profit_percent: float = 3.0
    daily_loss_limit_percent: float = 10.0
    futures_leverage: int = 2
    negative_trade_timeout_minutes: int = 30
    trading_mode: str = "DYNAMIC"

    @computed_field
    @property
    def order_api_key(self) -> str:
        return self.binance_testnet_api_key if self.use_testnet else self.binance_api_key

    @computed_field
    @property
    def order_api_secret(self) -> str:
        return self.binance_testnet_api_secret if self.use_testnet else self.binance_api_secret

    @computed_field
    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field
    @property
    def redis_url(self) -> str:
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/0"
        return f"redis://{self.redis_host}:{self.redis_port}/0"


settings = Settings()