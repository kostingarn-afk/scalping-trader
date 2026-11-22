"""Обработчик Kafka-сообщений raw-market-data → AnalyzerPipeline.

Создаётся внутри каждого процесса-консьюмера (см. `consumer.start_workers`):
asyncio-loop и пул asyncpg нельзя наследовать от родительского процесса.

Поток данных: Protobuf `RawMarketData` → состояние микроструктуры (стакан, сделки) →
завершённые свечи → скользящее окно по figi → `AnalyzerPipeline.process`
(индикаторы → стратегия → фильтр → БД → Kafka).
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
from .microstructure import BUY, SELL, UNKNOWN, MicrostructureState

logger = logging.getLogger(__name__)

# MIN_CANDLES: MACD(21) и ATR(14) требуют ≥21 свечи — берём с запасом.
MIN_CANDLES = 25
CANDLE_WINDOW = 200

Candle = Dict[str, Any]
ParsedCandle = Tuple[str, Candle]
# (figi, вид payload, данные): вид — "candle", "book" или "trade".
ParsedMessage = Tuple[str, str, Dict[str, Any]]

# TradeDirection из common.proto: 0 unspecified, 1 buy, 2 sell.
_DIRECTIONS = {1: BUY, 2: SELL}


def _to_float(quotation: Any) -> float:
    """Quotation (units/nano) → float."""
    return quotation.units + quotation.nano / 1e9


def _timestamp_seconds(message: Any) -> Optional[float]:
    """Время события в секундах; None, если биржа время не передала.

    Подставлять время получения нельзя: Hawkes оценивает поток биржи, а не задержку Kafka.
    """
    if not message.HasField("ts"):
        return None
    return message.ts.seconds + message.ts.nanos / 1e9


def parse_message(payload: bytes) -> Optional[ParsedMessage]:
    """Разобрать `RawMarketData` в свечу, стакан или сделку.

    @return (figi, вид, данные) или None для незавершённых свечей, пустых и битых сообщений.
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

    # oneof payload: payload_type не различает CANDLE и отсутствие payload (оба = 0),
    # поэтому вид определяется по HasField, а не по enum.
    if message.HasField("candle"):
        candle = message.candle
        if not candle.is_complete:
            return None
        candle_ts: Optional[datetime] = (
            candle.end.ToDatetime(tzinfo=timezone.utc)
            if candle.HasField("end")
            else None
        )
        return message.figi, "candle", {
            "candle_ts": candle_ts,
            "close": _to_float(candle.close),
            "high": _to_float(candle.high),
            "low": _to_float(candle.low),
            "volume": int(candle.volume),
        }

    if message.HasField("order_book"):
        book = message.order_book
        return message.figi, "book", {
            "bids_qty": [int(item.quantity) for item in book.bids],
            "asks_qty": [int(item.quantity) for item in book.asks],
        }

    if message.HasField("trade"):
        trade = message.trade
        return message.figi, "trade", {
            "price": _to_float(trade.price),
            "volume": int(trade.quantity),
            "direction": _DIRECTIONS.get(int(trade.direction), UNKNOWN),
            "ts": _timestamp_seconds(message),
        }

    return None


def parse_candle(payload: bytes) -> Optional[ParsedCandle]:
    """Совместимая обёртка: только завершённые свечи, остальное — None."""
    parsed = parse_message(payload)
    if parsed is None or parsed[1] != "candle":
        return None
    return parsed[0], parsed[2]


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
    microstructure = MicrostructureState(
        depth=config.order_book_depth,
        bucket_volume=config.vpin_bucket_volume,
        buckets=config.vpin_buckets,
        hawkes_window=config.hawkes_window,
        hawkes_refresh=config.hawkes_refresh,
    )

    def handler(message: Any) -> None:
        parsed = parse_message(message.value)
        if parsed is None:
            return
        figi, kind, data = parsed

        # Стакан и сделки обновляют состояние, но не запускают анализ: сигнал рождается на
        # завершённой свече, иначе он обновлялся бы на каждое сообщение стакана.
        if kind == "book":
            microstructure.update_book(figi, data["bids_qty"], data["asks_qty"])
            return
        if kind == "trade":
            microstructure.on_trade(
                figi,
                data["price"],
                data["volume"],
                data["direction"],
                timestamp=data.get("ts"),
            )
            return

        window_data = window.add(figi, data)
        if window_data is None:
            return
        window_data.update(microstructure.snapshot(figi))
        try:
            loop.run_until_complete(pipeline.process(figi, window_data))
        except Exception:
            # Kafka-consumer коммитит offset только при отсутствии исключения,
            # поэтому ошибка обработки не должна валить весь процесс-консьюмер.
            logger.exception("Pipeline failed for figi=%s", figi)

    return handler
