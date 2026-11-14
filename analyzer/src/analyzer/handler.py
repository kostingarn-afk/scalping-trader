"""Обработчик Kafka-сообщений raw-market-data → AnalyzerPipeline.

Создаётся внутри каждого процесса-консьюмера (см. `consumer.start_workers`):
asyncio-loop и пул asyncpg нельзя наследовать от родительского процесса.

Поток данных: Protobuf `RawMarketData` → завершённые свечи → скользящее окно по figi
→ `AnalyzerPipeline.process` (индикаторы → стратегия → фильтр → БД → Kafka).
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Callable, Deque, Dict, Optional, Tuple

import numpy as np

from . import proto_loader
from .config import AnalyzerConfig

logger = logging.getLogger(__name__)

# MIN_CANDLES: MACD(21) и ATR(14) требуют ≥21 свечи — берём с запасом.
MIN_CANDLES = 25
CANDLE_WINDOW = 200

Candle = Dict[str, Any]
ParsedCandle = Tuple[str, Candle]


def _to_float(quotation: Any) -> float:
    """Quotation (units/nano) → float."""
    return quotation.units + quotation.nano / 1e9


def parse_candle(payload: bytes) -> Optional[ParsedCandle]:
    """Разбор `RawMarketData`.

    @return (figi, свеча) для завершённой свечи; None для прочих payload'ов,
            незавершённых свечей и битых сообщений.
    """
    pb = proto_loader.raw_market_data_pb2
    if pb is None or not payload:
        return None

    message = pb.RawMarketData()
    try:
        message.ParseFromString(payload)
    except Exception:
        logger.warning("Failed to parse RawMarketData (%d bytes)", len(payload))
        return None

    # oneof payload: payload_type не различает CANDLE и отсутствие payload (оба = 0)
    if not message.HasField("candle"):
        return None

    candle = message.candle
    if not candle.is_complete:
        return None

    candle_ts: Optional[datetime] = (
        candle.end.ToDatetime(tzinfo=timezone.utc) if candle.HasField("end") else None
    )
    return message.figi, {
        "candle_ts": candle_ts,
        "close": _to_float(candle.close),
        "high": _to_float(candle.high),
        "low": _to_float(candle.low),
        "volume": int(candle.volume),
    }


class CandleWindow:
    """Скользящее окно завершённых свечей по каждому инструменту."""

    def __init__(self, maxlen: int = CANDLE_WINDOW, min_candles: int = MIN_CANDLES):
        if min_candles > maxlen:
            raise ValueError("min_candles must not exceed maxlen")
        self._min_candles = min_candles
        self._maxlen = maxlen
        self._candles: Dict[str, Deque[Candle]] = defaultdict(lambda: deque(maxlen=self._maxlen))

    def add(self, figi: str, candle: Candle) -> Optional[Dict[str, Any]]:
        """Добавить свечу; вернуть данные для индикаторов, когда окно заполнено."""
        window = self._candles[figi]
        # Повторное сообщение по той же свече (тот же candle_ts) заменяет её, а не дублирует:
        # стрим может прислать одну и ту же свечу несколько раз.
        last_ts = window[-1]["candle_ts"] if window else None
        if last_ts is not None and last_ts == candle["candle_ts"]:
            window[-1] = candle
        else:
            window.append(candle)
        if len(window) < self._min_candles:
            return None

        return {
            "closes": np.array([c["close"] for c in window], dtype=float),
            "highs": np.array([c["high"] for c in window], dtype=float),
            "lows": np.array([c["low"] for c in window], dtype=float),
            "volumes": np.array([c["volume"] for c in window], dtype=np.int64),
            "price": window[-1]["close"],
            "candle_ts": window[-1]["candle_ts"],
        }


def make_handler(config: AnalyzerConfig) -> Callable[[Any], None]:
    """Фабрика синхронного обработчика для одного процесса-консьюмера."""
    if not proto_loader.PROTO_AVAILABLE:
        raise RuntimeError(
            "protobuf-модули не сгенерированы: выполните "
            "`poetry run protoc -I ../proto --python_out src/analyzer ../proto/*.proto`"
        )

    # Ленивый импорт: AnalyzerPipeline живёт в main и сам импортирует эту фабрику.
    from .main import AnalyzerPipeline

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    pipeline = AnalyzerPipeline(config)
    loop.run_until_complete(pipeline.start())
    window = CandleWindow()

    def handler(message: Any) -> None:
        parsed = parse_candle(message.value)
        if parsed is None:
            return
        figi, candle = parsed
        data = window.add(figi, candle)
        if data is None:
            return
        try:
            loop.run_until_complete(pipeline.process(figi, data))
        except Exception:
            # Kafka-consumer коммитит offset только при отсутствии исключения,
            # поэтому ошибка обработки не должна валить весь процесс-консьюмер.
            logger.exception("Pipeline failed for figi=%s", figi)

    return handler
