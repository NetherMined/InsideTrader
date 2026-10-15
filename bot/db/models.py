from sqlalchemy import (
    Column, String, Float, Integer, Boolean,
    DateTime, JSON, Text, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class Candle(Base):
    __tablename__ = "candles"
    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", "open_time", name="uq_candle"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    timeframe = Column(String(5), nullable=False)
    open_time = Column(DateTime(timezone=True), nullable=False)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)
    close_time = Column(DateTime(timezone=True))


class ScannedMarket(Base):
    __tablename__ = "scanned_markets"

    symbol = Column(String(30), primary_key=True)
    base = Column(String(15), nullable=False)
    quote = Column(String(10), nullable=False)
    market_type = Column(String(10), nullable=False)
    volume_24h_usdt = Column(Float, default=0.0)
    active = Column(Boolean, default=True)
    enabled_for_trading = Column(Boolean, default=True)
    last_scanned = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(30), nullable=False, index=True)
    side = Column(String(10), nullable=False)
    mode = Column(String(10), nullable=False)
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float)
    quantity = Column(Float, nullable=False)
    leverage = Column(Integer, default=1)
    stop_loss_price = Column(Float)
    take_profit_price = Column(Float)
    pnl_usdt = Column(Float)
    pnl_percent = Column(Float)
    estimated_fee_usdt = Column(Float, default=0.0)
    gross_pnl_usdt = Column(Float)
    # Corrected values: pnl_usdt on trades closed before Oct 14 2026 counted leverage twice
    pnl_usdt_true = Column(Float)
    fee_usdt_true = Column(Float)
    funding_rate = Column(Float, default=0.0)
    regime = Column(String(20), default="UNKNOWN")  # TRENDING, RANGING, TRANSITION
    status = Column(String(15), default="OPEN")
    binance_order_id = Column(String(50))
    paper_trade = Column(Boolean, default=True)
    opened_at = Column(DateTime(timezone=True), server_default=func.now())
    closed_at = Column(DateTime(timezone=True))
    extra = Column(JSON)
    archived = Column(Boolean, default=False)


class Position(Base):
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(30), nullable=False, index=True)
    side = Column(String(10), nullable=False)
    mode = Column(String(10), nullable=False)
    entry_price = Column(Float, nullable=False)
    current_price = Column(Float)
    quantity = Column(Float, nullable=False)
    leverage = Column(Integer, default=1)
    stop_loss_price = Column(Float)
    take_profit_price = Column(Float)
    unrealized_pnl = Column(Float, default=0.0)
    trade_id = Column(Integer)
    paper_trade = Column(Boolean, default=True)
    opened_at = Column(DateTime(timezone=True), server_default=func.now())


class Prediction(Base):
    __tablename__ = "predictions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(30), nullable=False, index=True)
    prediction_date = Column(DateTime(timezone=True), nullable=False)
    current_price = Column(Float, nullable=False)
    target_price = Column(Float, nullable=False)
    predicted_change_pct = Column(Float, nullable=False)
    confidence = Column(Float, nullable=False)
    mode_recommendation = Column(String(10), nullable=False)
    features = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Setting(Base):
    __tablename__ = "settings"

    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class BacktestResult(Base):
    __tablename__ = "backtest_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy = Column(String(50), nullable=False)
    symbol = Column(String(30), nullable=False)
    timeframe = Column(String(5), nullable=False)
    start_date = Column(DateTime(timezone=True), nullable=False)
    end_date = Column(DateTime(timezone=True), nullable=False)
    total_trades = Column(Integer, default=0)
    win_rate = Column(Float)
    sharpe_ratio = Column(Float)
    max_drawdown = Column(Float)
    total_return_pct = Column(Float)
    params = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ResearchPacket(Base):
    __tablename__ = "research_packets"
    __table_args__ = (
        UniqueConstraint("symbol", "hour_start", "packet_time", name="uq_research_packet"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(30), nullable=False, index=True)
    hour_start = Column(DateTime(timezone=True), nullable=False, index=True)
    packet_time = Column(DateTime(timezone=True), nullable=False)
    pred_side = Column(String(8), nullable=False)
    pred_low = Column(Float)
    pred_high = Column(Float)
    invalidation = Column(Float)
    confidence = Column(Float, nullable=False)
    setup = Column(Text)
    features = Column(JSON)
    arm = Column(Boolean, default=False)
    raw = Column(JSON)


class HourReview(Base):
    __tablename__ = "hour_reviews"
    __table_args__ = (
        UniqueConstraint("symbol", "hour_start", name="uq_hour_review"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(30), nullable=False, index=True)
    hour_start = Column(DateTime(timezone=True), nullable=False, index=True)
    side_ok = Column(Boolean)
    range_ok = Column(Boolean)
    invalidation_hit = Column(Boolean)
    manager_action = Column(String(16))
    rule_version = Column(Integer)
    followed_advice = Column(Boolean)
    labels = Column(JSON)
    usefulness = Column(String(16))
    patch = Column(JSON)
    actual = Column(JSON)


class RulePatch(Base):
    __tablename__ = "rule_patches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    from_version = Column(Integer, nullable=False)
    to_version = Column(Integer)
    change = Column(Text, nullable=False)
    because = Column(Text)
    scope = Column(String(80))
    labels = Column(JSON)
    status = Column(String(16), default="pending")
    expires_at = Column(DateTime(timezone=True))
    decided_at = Column(DateTime(timezone=True))
    decided_by = Column(String(16))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class DerivsBar(Base):
    """Binance futures derivatives / order-flow series per 15m bar (Phase 8.2a).

    open_time is the endpoint timestamp: the start of the 15m period for the taker and
    long/short ratios and the kline fields, the snapshot time for open interest. Values are
    stored raw; feature builders lag them so only data public before a decision is used.
    """
    __tablename__ = "derivs_15m"
    __table_args__ = (UniqueConstraint("symbol", "open_time", name="uq_derivs_15m"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(30), nullable=False, index=True)
    open_time = Column(DateTime(timezone=True), nullable=False, index=True)
    oi_value = Column(Float)          # sumOpenInterestValue (USDT)
    taker_ratio = Column(Float)       # taker buy / sell volume
    gl_ratio = Column(Float)          # global long/short account ratio
    top_pos_ratio = Column(Float)     # top traders long/short position ratio
    taker_buy_quote = Column(Float)   # kline taker buy quote volume
    quote_volume = Column(Float)      # kline quote volume
    funding = Column(Float)           # last settled funding rate as of the bar
