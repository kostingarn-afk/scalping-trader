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
    # Дефолт — «Трейдер»: порог цели выхода 0.30 % вместо 1.8 % (шаг 1.9, to_do.md §4.1).
    commission_rate_per_side: float = 0.0005
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
    # Hawkes: окно времён сделок (не меньше 50 — порог устойчивости MLE) и период пересчёта.
    hawkes_window: int = 500
    hawkes_refresh: int = 25

    def __post_init__(self) -> None:
        if self.order_book_depth <= 0:
            raise ValueError("order_book_depth must be positive")
        if self.vpin_bucket_volume <= 0:
            raise ValueError("vpin_bucket_volume must be positive")
        if self.vpin_buckets <= 0:
            raise ValueError("vpin_buckets must be positive")
        if self.hawkes_window < 50:
            raise ValueError("hawkes_window must be at least 50")
        if self.hawkes_refresh <= 0:
            raise ValueError("hawkes_refresh must be positive")
        # Ставка и ratio задают порог цели выхода и комиссионный фильтр: ноль или минус
        # отключают фильтр молча (все сигналы проходят), поэтому проверяются явно.
        if not 0 < self.commission_rate_per_side <= 0.01:
            raise ValueError("commission_rate_per_side must be in (0, 0.01]")
        if self.commission_profit_ratio <= 0:
            raise ValueError("commission_profit_ratio must be positive")
        if self.signal_ttl_seconds <= 0:
            raise ValueError("signal_ttl_seconds must be positive")

    @property
    def required_tp_pct(self) -> float:
        """Порог цели выхода, % от суммы сделки (шаг 1.9).

        Цель должна покрывать хотя бы столько, иначе комиссионный фильтр отбросит сигнал;
        фактическое кратное ATR считает `strategy.target_mult`.

        Круг считается по обеим ногам: `d ≥ ratio·rate·(entry + tp)`, откуда при `tp = entry·(1+d)`
        получаем `d/entry ≥ 2·ratio·rate/(1 − ratio·rate)`. Формула «2·ratio·rate» без знаменателя
        недобирает ровно на член, которым выход дороже входа, и цель на пороге отбраковывалась.
        """
        ratio_rate = self.commission_profit_ratio * self.commission_rate_per_side
        return 2.0 * ratio_rate / (1.0 - ratio_rate) * 100.0

    @classmethod
    def from_yaml(cls, path: str | Path) -> "AnalyzerConfig":
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
