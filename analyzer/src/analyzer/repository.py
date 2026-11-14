"""Сохранение результатов анализа в БД — задача 3.6.

asyncpg, таблица analysis_results.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import asyncpg


@dataclass(frozen=True)
class AnalysisRecord:
    signal_id: str
    ts: datetime
    instrument_id: str
    candle_ts: Optional[datetime]
    candle_interval: str
    strategy_id: str
    analyzer_version: str
    indicators: dict
    signal: str
    confidence: float
    suggested_price: float
    suggested_sl: Optional[float]
    suggested_tp: Optional[float]
    position_size_hint: int
    expires_at: datetime


class AnalysisRepository:
    """Репозиторий для analysis_results через asyncpg."""

    INSERT_SQL = """
        INSERT INTO analysis_results (
            signal_id, ts, instrument_id, candle_ts, candle_interval,
            strategy_id, analyzer_version, indicators, signal, confidence,
            suggested_price, suggested_sl, suggested_tp, position_size_hint, expires_at
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
        ON CONFLICT (signal_id) DO NOTHING
    """

    def __init__(self, dsn: str):
        self._dsn = dsn
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()

    async def save(self, rec: AnalysisRecord) -> None:
        assert self._pool is not None, "not connected"
        async with self._pool.acquire() as conn:
            await conn.execute(
                self.INSERT_SQL,
                uuid.UUID(rec.signal_id), rec.ts, rec.instrument_id,
                rec.candle_ts, rec.candle_interval,
                rec.strategy_id, rec.analyzer_version,
                json.dumps(rec.indicators), rec.signal, rec.confidence,
                rec.suggested_price, rec.suggested_sl, rec.suggested_tp,
                rec.position_size_hint, rec.expires_at,
            )

    async def get(self, signal_id: str) -> Optional[dict]:
        assert self._pool is not None, "not connected"
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM analysis_results WHERE signal_id=$1",
                uuid.UUID(signal_id),
            )
            return dict(row) if row else None
