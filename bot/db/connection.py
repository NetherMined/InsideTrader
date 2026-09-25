from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text
from loguru import logger
from bot.config import settings
from bot.db.models import Base

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=1800,
)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created/verified")

    # Ensure scanned_markets has the unique constraint on symbol
    # (handles migration from older schema without the constraint)
    async with engine.begin() as conn:
        await conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conrelid = 'scanned_markets'::regclass
                    AND contype = 'u'
                ) THEN
                    ALTER TABLE scanned_markets
                    ADD CONSTRAINT uq_scanned_markets_symbol UNIQUE (symbol);
                    RAISE NOTICE 'Added unique constraint on scanned_markets.symbol';
                ELSE
                    RAISE NOTICE 'Unique constraint on scanned_markets.symbol already exists';
                END IF;
            END $$;
        """))
    logger.info("Database constraints verified")

    # Migrate trades table: add columns added after initial schema creation
    async with engine.begin() as conn:
        await conn.execute(text("""
            ALTER TABLE trades
                ADD COLUMN IF NOT EXISTS estimated_fee_usdt FLOAT DEFAULT 0.0,
                ADD COLUMN IF NOT EXISTS gross_pnl_usdt FLOAT,
                ADD COLUMN IF NOT EXISTS funding_rate FLOAT DEFAULT 0.0,
                ADD COLUMN IF NOT EXISTS regime VARCHAR(20) DEFAULT 'UNKNOWN',
                ADD COLUMN IF NOT EXISTS archived BOOLEAN DEFAULT FALSE;
        """))
    logger.info("Database schema migration applied")

    # Create performance indexes (idempotent — one statement per execute for asyncpg compatibility)
    _indexes = [
        "CREATE INDEX IF NOT EXISTS idx_positions_trade_id ON positions (trade_id)",
        "CREATE INDEX IF NOT EXISTS idx_positions_opened_at ON positions (opened_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_trades_symbol_status ON trades (symbol, status)",
        "CREATE INDEX IF NOT EXISTS idx_trades_opened_at ON trades (opened_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_candles_symbol_tf_time ON candles (symbol, timeframe, open_time DESC)",
    ]
    async with engine.begin() as conn:
        for idx_sql in _indexes:
            await conn.execute(text(idx_sql))
    logger.info("Database indexes verified")



async def check_db() -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        return False
