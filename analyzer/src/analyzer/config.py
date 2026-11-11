"""Конфигурация Analyzer."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import yaml


@dataclass(frozen=True)
class AnalyzerConfig:
    kafka_bootstrap: str = "localhost:9092"
    consumer_group: str = "analyzer"
    topic_raw: str = "raw-market-data"
    topic_signals: str = "trading-signals"
    num_processes: int = 2
    instruments: List[str] = field(default_factory=lambda: ["BR", "SBER", "TCSG"])
    strategy_id: str = "mean_reversion_bb"
    analyzer_version: str = "0.1.0"
    signal_ttl_seconds: int = 30
    # Риск-параметры (дубликат risk_config для автономии; актуальные — из БД)
    commission_profit_ratio: float = 3.0
    db_url: str = "postgresql://scalping:scalping@localhost:5432/scalping"
    metrics_port: int = 8000

    @classmethod
    def from_yaml(cls, path: str | Path) -> "AnalyzerConfig":
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
