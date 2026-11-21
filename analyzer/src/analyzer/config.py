"""Конфигурация Analyzer."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml


@dataclass(frozen=True)
class AnalyzerConfig:
    kafka_bootstrap: str = "localhost:9092"
    consumer_group: str = "analyzer"
    topic_raw: str = "raw-market-data"
    topic_signals: str = "trading-signals"
    num_processes: int = 2
    strategy_id: str = "mean_reversion_bb"
    analyzer_version: str = "0.1.0"
    signal_ttl_seconds: int = 30
    # Риск-параметры (дубликат risk_config для автономии; актуальные — из БД)
    commission_profit_ratio: float = 3.0
    # Ставка брокера за сторону сделки: «Инвестор» 0.003, «Трейдер» 0.0005,
    # «Премиум» 0.0004. Отдельным полем, чтобы смена тарифа была правкой конфига,
    # а не кода (фильтр отбрасывает ~92 % сигналов на «Инвесторе» и ~8 % на «Трейдере»).
    commission_rate_per_side: float = 0.003
    db_url: str = "postgresql://scalping:scalping@localhost:5432/scalping"
    metrics_port: int = 8000
    # Сжатие producer'а trading-signals. gzip — из stdlib; lz4 требует пакета `lz4`,
    # без него kafka-python падает с AssertionError при создании producer.
    compression_type: Optional[str] = "gzip"
    # Микроструктура (шаг 1.3): глубина стакана из Collector и параметры VPIN.
    # Бакет — объём в лотах; VPIN считается по последним `vpin_buckets` завершённым бакетам.
    order_book_depth: int = 10
    vpin_bucket_volume: int = 1000
    vpin_buckets: int = 20

    def __post_init__(self) -> None:
        if self.order_book_depth <= 0:
            raise ValueError("order_book_depth must be positive")
        if self.vpin_bucket_volume <= 0:
            raise ValueError("vpin_bucket_volume must be positive")
        if self.vpin_buckets <= 0:
            raise ValueError("vpin_buckets must be positive")

    @classmethod
    def from_yaml(cls, path: str | Path) -> "AnalyzerConfig":
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
