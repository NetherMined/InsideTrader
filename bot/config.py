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

    trading_mode: str = "FUTURES"
    trading_pairs: str = "AUTO"
    analysis_timeframe: str = "1h"
    futures_leverage: int = 5

    # Ensemble stacking (FeaturesGrok)
    ensemble_enabled: bool = True
    entry_model: str = "ensemble"          # "path" = path model v2 entries, paper only (FIX_PLAN_OCT14 8.2c)
    ensemble_timeframe: str = "15m"           # true 15-minute horizon for fee label
    ensemble_fee_threshold_pct: float = 0.28  # classify moves larger than round-trip fees
    ensemble_horizon_bars: int = 1            # 1 bar of 15m = 15 minutes
    lightgbm_momentum_enabled: bool = True
    catboost_regime_enabled: bool = True
    momentum_timeframe: str = "1m"            # short-term momentum candles
    max_prediction_age_minutes: int = 10      # skip new entries once predictions are this old
    starting_capital_usdt: float = 100.0

    futures_confidence_threshold: float = 0.55
    volatility_futures_cap_percent: float = 5.0
    adx_futures_threshold: float = 25.0

    daily_target_percent: float = 2.0
    confidence_threshold: float = 0.70
    stop_loss_percent: float = 2.0
    take_profit_percent: float = 3.0
    daily_loss_limit_percent: float = 10.0
    negative_trade_timeout_minutes: int = 60
    taker_fee_rate: float = 0.0005  # Binance USDT-M VIP0 taker
    paper_slippage_pct: float = 0.03       # per paper fill when the live order book can't be read (measured median 0.018% at ~$50)
    paper_stop_slippage_pct: float = 0.05  # extra on stop-loss exits — assumption: spreads widen in the moves that trigger stops
    entry_cost_check: str = "log"           # path-mode entry cost check: off | log (record only) | enforce (skip)
    entry_max_slippage_pct: float = 0.10    # round-trip book slippage above this (or a thin book) fails the check
    trail_min_profit_raw_pct: float = 0.40  # trailing/TP-follower exits only fire once price moved this far (covers fees + exit slippage)
    trail_reversal_raw_pct: float = 0.50    # trailing stop closes on this raw price pullback from peak (independent of leverage)

    heat_limit_pct: float = 40.0
    max_single_symbol_heat_pct: float = 10.0
    max_trade_margin_usdt: float = 10.0  # max margin per position (~$2 max loss at 4%)

    min_rr_ratio: float = 2.5
    zone_sl_max_pct: float = 3.0
    sl_atr_mult: float = 1.0          # stop distance = ATR% x this, clamped to [sl_min_pct, sl_max_pct]
    sl_min_pct: float = 0.8           # tighter stops sit inside normal noise
    sl_max_pct: float = 1.25          # also capped so the leveraged loss stays under 3.8%
    tp_max_sl_multiple: float = 3.0   # TP no further than this many stop distances
    max_entry_atr_pct: float = 2.5    # skip coins moving more than this per bar
    loss_cooldown_minutes: int = 45   # symbol cooldown after a losing close
    win_cooldown_minutes: int = 5     # symbol cooldown after a winning close
    loss_streak_block: int = 2        # block a symbol after this many losses within 2h
    baseline_dir: str = "/app/baseline"  # daily hindsight baseline output (bind-mounted to ./baseline)
    zone_sl_buffer_atr: float = 0.15
    zone_proximity_atr: float = 1.0
    swing_lookback: int = 3
    structure_cache_ttl: int = 3600

    paper_trading_mode: bool = True
    research_enabled: bool = True
    research_timeframe: str = "15m"
    research_min_confidence: float = 0.55
    require_15m_confirm: bool = False
    allow_1h_only_if_research_stale: bool = False
    research_stale_minutes: int = 20
    invalidation_min_hold_minutes: float = 5.0  # structure_invalidation cannot close a trade younger than this
    invalidation_buffer_pct: float = 0.3        # price must break the level by this much before closing
    patch_window_hours: int = 10
    patch_min_label_count: int = 4
    patch_auto_accept_tighten_only: bool = True
    patch_expiry_hours: int = 48
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


def get_mode_limits(mode: str) -> dict:
    """Futures-only. Spot is disabled; mode argument is ignored."""
    return {"max_simultaneous": 8}
