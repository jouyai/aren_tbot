"""
Database module — async engine untuk Neon serverless PostgreSQL.

Neon free tier "tidur" setelah 5 menit idle dan butuh ~5-10 detik cold-start.
Solusi:
  - NullPool: tidak ada koneksi yang di-cache, buka baru tiap request
  - connect_args timeout 60 detik: cukup untuk Neon cold-start
  - wake_up_db(): ping database saat startup untuk membangunkan Neon
    sebelum user pertama datang
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from bot.config import DATABASE_URL
from bot.models.db_models import Base

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
engine: AsyncEngine = create_async_engine(
    DATABASE_URL,
    echo=False,
    poolclass=NullPool,
    # asyncpg menerima 'timeout' sebagai waktu koneksi dalam detik
    connect_args={"timeout": 60},
)

# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------
AsyncSessionFactory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


# ---------------------------------------------------------------------------
# Wake-up ping — panggil saat startup untuk membangunkan Neon
# ---------------------------------------------------------------------------
async def wake_up_db(max_attempts: int = 5) -> bool:
    """Ping database hingga berhasil konek. Return True jika sukses.

    Neon cold-start bisa butuh 5-15 detik. Fungsi ini mencoba koneksi
    berulang dengan jeda eksponensial sampai berhasil atau habis percobaan.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            logger.info("Database wake-up successful (attempt %d/%d)", attempt, max_attempts)
            return True
        except Exception as exc:
            wait = min(attempt * 3, 15)  # 3s, 6s, 9s, 12s, 15s
            logger.warning(
                "Database wake-up attempt %d/%d failed (%s), retrying in %ds...",
                attempt, max_attempts, type(exc).__name__, wait
            )
            if attempt < max_attempts:
                await asyncio.sleep(wait)

    logger.error("Database wake-up failed after %d attempts", max_attempts)
    return False


# ---------------------------------------------------------------------------
# Session context manager
# ---------------------------------------------------------------------------
@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional async database session."""
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with get_session() as session:
        yield session


# ---------------------------------------------------------------------------
# Schema management helpers
# ---------------------------------------------------------------------------
async def create_all_tables() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_all_tables() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
