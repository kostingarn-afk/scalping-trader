"""Точка входа Analyzer — связывает consumer → индикаторы → стратегия → фильтр → БД → publisher."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from .commission_filter import CommissionFilter
from .config import AnalyzerConfig
from .consumer import start_workers, join_workers
from .indicators import compute_all
from .metrics import start_metrics_server, signals_generated, signals_rejected, analysis_latency
from .repository import AnalysisRepository, AnalysisRecord
from .signal_publisher import SignalPublisher
from .strategy import mean_reversion, SignalType

logger = logging.getLogger(__name__)


class AnalyzerPipeline:
    """Связывает компоненты: индикаторы → стратегия → фильтр → БД → Kafka."""

    def __init__(self, config: AnalyzerConfig):
        self.config = config
        self.filter = CommissionFilter(ratio=config.commission_profit_ratio)
        self.repository = AnalysisRepository(config.db_url)
        self.publisher = SignalPublisher(config)
        # Кэш последних свечей по figi (упрощённо; в проде — ring buffer)
        self._candles: dict[str, list] = {}

    async def start(self) -> None:
        await self.repository.connect()
        self.publisher.start()

    async def stop(self) -> None:
        await self.repository.close()
        self.publisher.close()

    async def process(self, figi: str, candle_data: dict) -> None:
        """Обработка одной свечи: расчёт → сигнал → фильтр → БД → Kafka."""
        with analysis_latency.time():
            closes = candle_data.get("closes")
            highs = candle_data.get("highs")
            lows = candle_data.get("lows")
            volumes = candle_data.get("volumes")
            price = candle_data.get("price")
            if not all([closes, highs, lows, volumes, price]):
                return

            ind = compute_all(closes, highs, lows, volumes)
            signal = mean_reversion(ind, price)

            signal_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc)
            expires_at = datetime.fromtimestamp(
                now.timestamp() + self.config.signal_ttl_seconds, tz=timezone.utc
            )

            # Комиссионный фильтр
            kept = self.filter.should_keep(signal)
            final_signal = signal if kept else type(signal)(SignalType.HOLD, 0.0, price, None, None, "commission filter")

            # Сохранение в БД (всегда — даже HOLD)
            rec = AnalysisRecord(
                signal_id=signal_id, ts=now, instrument_id=figi,
                candle_ts=candle_data.get("candle_ts"), candle_interval="1min",
                strategy_id=self.config.strategy_id, analyzer_version=self.config.analyzer_version,
                indicators=ind.to_dict(), signal=final_signal.type.value,
                confidence=final_signal.confidence, suggested_price=price,
                suggested_sl=final_signal.suggested_sl, suggested_tp=final_signal.suggested_tp,
                position_size_hint=1, expires_at=expires_at,
            )
            await self.repository.save(rec)

            # Публикация (только BUY/SELL)
            published = self.publisher.publish(
                final_signal, signal_id, figi,
                int(expires_at.timestamp() * 1000),
                self.config.strategy_id, self.config.analyzer_version, ind.to_dict(),
            )
            if published:
                signals_generated.inc()
            else:
                signals_rejected.inc()


def main() -> None:
    import sys
    config = AnalyzerConfig.from_yaml(sys.argv[1]) if len(sys.argv) > 1 else AnalyzerConfig()
    logging.basicConfig(level=logging.INFO)
    start_metrics_server(config.metrics_port)
    logger.info("Starting analyzer with %d processes", config.num_processes)

    pipeline = AnalyzerPipeline(config)

    def handler(msg):
        # Упрощённый обработчик: десериализация Protobuf и вызов pipeline.process
        # Полная реализация — после генерации protobuf-кода.
        logger.debug("Received message from partition %d", msg.partition)

    workers = start_workers(config, handler, config.num_processes)
    join_workers(workers)


if __name__ == "__main__":
    main()
