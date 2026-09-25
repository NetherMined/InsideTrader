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

    trading_mode: str = "DYNAMIC"
    trading_pairs: str = "AUTO"
    analysis_timeframe: str = "15m"
    futures_leverage: int = 5
    starting_capital_usdt: float = 100.0

    futures_confidence_threshold: float = 0.55
    volatility_futures_cap_percent: float = 5.0
    adx_futures_threshold: float = 25.0

    daily_target_percent: float = 2.0
    max_concurrent_trades: int = 20
    max_daily_trades: int = 200
    min_daily_trades: int = 50
    min_concurrent_trades: int = 0
    confidence_threshold: float = 0.60
    max_risk_per_trade_percent: float = 2.0
    stop_loss_percent: float = 1.5
    take_profit_percent: float = 3.0
    take_profit_usdt: float = 0.0
    daily_loss_limit_percent: float = 10.0
    max_single_coin_exposure_percent: float = 30.0
    negative_trade_timeout_minutes: int = 60
    taker_fee_rate: float = 0.0004

    paper_trading_mode: bool = True
    live_trading_enabled: bool = False

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "insidetrader"
    postgres_user: str = "insidetrader"
    postgres_password: str = "changeme"

    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = ""

    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    api_port: int = 8000
    dashboard_secret_key: str = "change_this_secret"
    dashboard_username: str = "admin"
    dashboard_password: str = "changeme"

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
    def pairs_list(self) -> list[str] | None:
        if self.trading_pairs.upper() == "AUTO":
            return None
        return [p.strip() for p in self.trading_pairs.split(",") if p.strip()]


settings = Settings()

GOAL_PERIOD_HOURS = 168  # fixed 7-day window
MAX_GOAL_FACTOR = 0.15   # 15% of capital
