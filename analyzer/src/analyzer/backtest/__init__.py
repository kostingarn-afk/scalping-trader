"""Бэктест стратегии на исторической выгрузке истории.

Точка входа — console script `analyzer-backtest` (см. `backtest.cli`). Пакет не обращается
к T-Invest API: историю выгружает Collector в CSV + `.meta.json` (`backtest.loader`).
"""
from __future__ import annotations

from .commission import TARIFFS, CommissionModel
from .engine import passes_commission_filter, run_backtest, size_position
from .loader import HistoryDataset, HistoryMeta, aggregate, load_dataset, read_bars, scan_dir
from .models import (
    Bar,
    BacktestResult,
    Direction,
    ExitReason,
    Instrument,
    RunConfig,
    Trade,
)
from .report import Summary, format_markdown, summarize, summary_json, trades_csv, write_report

__all__ = [
    "Bar",
    "BacktestResult",
    "CommissionModel",
    "Direction",
    "ExitReason",
    "HistoryDataset",
    "HistoryMeta",
    "Instrument",
    "RunConfig",
    "Summary",
    "TARIFFS",
    "Trade",
    "aggregate",
    "format_markdown",
    "load_dataset",
    "passes_commission_filter",
    "read_bars",
    "run_backtest",
    "scan_dir",
    "size_position",
    "summarize",
    "summary_json",
    "trades_csv",
    "write_report",
]
