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
    funding_rate = Column(Float, default=0.0)
    regime = Column(String(20), default="UNKNOWN")  # TRENDING, RANGING, TRANSITION
    status = Column(String(15), default="OPEN")
    binance_order_id = Column(String(50))
    paper_trade = Column(Boolean, default=True)
    opened_at = Column(DateTime(timezone=True), server_default=func.now())
    closed_at = Column(DateTime(timezone=True))
    extra = Column(JSON)


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
