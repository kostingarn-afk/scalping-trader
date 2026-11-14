"""Публикация BUY/SELL сигналов в Kafka trading-signals — задача 3.7.

HOLD не публикуется (только сохраняется в БД).
"""
from __future__ import annotations

import logging
from typing import Optional

from kafka import KafkaProducer

from . import proto_loader
from .config import AnalyzerConfig
from .strategy import Signal, SignalType

# Модуль генерируется build-шагом (protoc); при отсутствии публикация пропускается.
trading_signal_pb2 = proto_loader.trading_signal_pb2

logger = logging.getLogger(__name__)


class SignalPublisher:
    def __init__(self, config: AnalyzerConfig):
        self._config = config
        self._producer: Optional[KafkaProducer] = None

    def start(self) -> None:
        self._producer = KafkaProducer(
            bootstrap_servers=self._config.kafka_bootstrap,
            key_serializer=lambda k: k.encode("utf-8") if k else None,
            value_serializer=lambda v: v,
            acks=1,
            compression_type=self._config.compression_type,
        )

    def close(self) -> None:
        if self._producer:
            self._producer.close()

    def publish(self, signal: Signal, signal_id: str, figi: str, expires_at_ts_ms: int,
                strategy_id: str, analyzer_version: str, indicators: dict) -> bool:
        """Публикация сигнала. Возвращает True если опубликован, False если HOLD (не публикуется)."""
        if signal.type == SignalType.HOLD:
            logger.debug("HOLD signal for %s — not published", figi)
            return False
        if self._producer is None:
            raise RuntimeError("SignalPublisher not started")
        if trading_signal_pb2 is None:
            logger.warning("protobuf module not available — skipping publish")
            return False

        pb = trading_signal_pb2.TradingSignal(
            signal_id=signal_id,
            figi=figi,
            signal=(trading_signal_pb2.BUY if signal.type == SignalType.BUY
                    else trading_signal_pb2.SELL),
            confidence=signal.confidence,
            strategy_id=strategy_id,
            analyzer_version=analyzer_version,
            position_size_hint=1,
        )
        pb.expires_at.FromMilliseconds(expires_at_ts_ms)
        if signal.suggested_price is not None:
            pb.suggested_price.units = int(signal.suggested_price)
            pb.suggested_price.nano = int((signal.suggested_price - int(signal.suggested_price)) * 1e9)
        if signal.suggested_sl is not None:
            pb.suggested_sl.units = int(signal.suggested_sl)
            pb.suggested_sl.nano = int((signal.suggested_sl - int(signal.suggested_sl)) * 1e9)
        if signal.suggested_tp is not None:
            pb.suggested_tp.units = int(signal.suggested_tp)
            pb.suggested_tp.nano = int((signal.suggested_tp - int(signal.suggested_tp)) * 1e9)
        for k, v in indicators.items():
            pb.indicators[k] = float(v)

        self._producer.send(self._config.topic_signals, key=figi, value=pb.SerializeToString())
        return True
