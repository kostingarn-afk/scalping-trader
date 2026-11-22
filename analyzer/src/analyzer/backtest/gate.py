"""Гейт §1: автоматическая проверка результата прогона — шаг 1.8 `to_do.md` §4.1.

Пороги (`to_do.md` §1): `gate = Σgross/Σcommission ≥ 1`, `t` на out-of-sample ≥ 2, сделок ≥ 50,
прибыль переживает удаление трёх лучших периодов. Диагностика ряда — `docs/mat_analyze.md` §5:
ADF, Ljung–Box, Jarque–Bera и эксцесс по дневным доходностям.

Jarque–Bera не входит в вердикт: в скальпинге тяжёлые хвосты ожидаемы, и провал нормальности не
означает отсутствия преимущества.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.stats import jarque_bera, kurtosis
from statsmodels.stats.diagnostic import acorr_ljungbox
from statsmodels.tsa.stattools import adfuller

from .models import BacktestResult, Trade

MIN_TRADES = 50
MIN_OOS_TRADES = 10
MIN_DAYS = 8
TRIM_DROP = 3
OOS_SHARE = 0.7
GATE_MIN = 1.0
T_MIN = 2.0


@dataclass(frozen=True)
class GateMetrics:
    trades: int
    gross_rub: float
    commission_rub: float
    gate: Optional[float]
    t_all: Optional[float]
    oos_trades: int
    t_oos: Optional[float]
    net_trimmed_rub: float
    net_trimmed_pct: float
    days: int
    adf_p: Optional[float]
    ljung_box_p: Optional[float]
    jarque_bera_p: Optional[float]
    excess_kurtosis: Optional[float]
    passed: bool
    failed_checks: Tuple[str, ...]


def evaluate_gate(result: BacktestResult) -> GateMetrics:
    return evaluate_trades(result.trades, result.starting_deposit)


def evaluate_trades(trades: Sequence[Trade], deposit: float) -> GateMetrics:
    gross = sum(trade.gross_pnl for trade in trades)
    commission = sum(trade.commission for trade in trades)
    gate = gross / commission if commission > 0 else None

    nets = [trade.net_pnl for trade in trades]
    daily = _daily_nets(trades)
    trimmed = _trimmed_net(daily)
    oos_nets = _out_of_sample_nets(trades)

    adf_p, ljung_p, jb_p, excess = _diagnostics(daily, deposit)
    t_oos = _t_stat(oos_nets) if len(oos_nets) >= MIN_OOS_TRADES else None

    checks: List[Tuple[str, bool]] = [
        ("gate ≥ 1", gate is not None and gate >= GATE_MIN),
        ("t OOS ≥ 2", t_oos is not None and t_oos >= T_MIN),
        ("сделок ≥ 50", len(trades) >= MIN_TRADES),
        ("прибыль без 3 лучших дней > 0", trimmed > 0),
    ]
    failed = tuple(name for name, ok in checks if not ok)
    return GateMetrics(
        trades=len(trades),
        gross_rub=gross,
        commission_rub=commission,
        gate=gate,
        t_all=_t_stat(nets),
        oos_trades=len(oos_nets),
        t_oos=t_oos,
        net_trimmed_rub=trimmed,
        net_trimmed_pct=trimmed / deposit * 100.0 if deposit else 0.0,
        days=len(daily),
        adf_p=adf_p,
        ljung_box_p=ljung_p,
        jarque_bera_p=jb_p,
        excess_kurtosis=excess,
        passed=not failed,
        failed_checks=failed,
    )


def _t_stat(values: Sequence[float]) -> Optional[float]:
    if len(values) < 2:
        return None
    array = np.asarray(values, dtype=float)
    deviation = float(array.std(ddof=1))
    if deviation <= 0:
        return None
    return float(array.mean() / (deviation / math.sqrt(array.size)))


def _daily_nets(trades: Sequence[Trade]) -> Dict[date, float]:
    daily: Dict[date, float] = {}
    for trade in trades:
        day = trade.exit_ts.date()
        daily[day] = daily.get(day, 0.0) + trade.net_pnl
    return daily


def _trimmed_net(daily: Dict[date, float]) -> float:
    if not daily:
        return 0.0
    values = sorted(daily.values(), reverse=True)
    return float(sum(values[TRIM_DROP:]))


def _out_of_sample_nets(trades: Sequence[Trade]) -> List[float]:
    if len(trades) < 2:
        return []
    start = min(trade.entry_ts for trade in trades)
    end = max(trade.exit_ts for trade in trades)
    boundary = start + (end - start) * OOS_SHARE
    return [trade.net_pnl for trade in trades if trade.exit_ts >= boundary]


def _diagnostics(
    daily: Dict[date, float], deposit: float
) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    if len(daily) < MIN_DAYS or deposit <= 0:
        return None, None, None, None
    series = np.asarray(
        [daily[day] / deposit for day in sorted(daily)], dtype=float
    )
    # Вырожденный ряд: `std` у одинаковых значений не строго 0 из-за ошибки суммирования,
    # поэтому проверяется размах, а не только отклонение.
    if (
        not np.all(np.isfinite(series))
        or float(np.ptp(series)) == 0.0
        or float(series.std()) == 0.0
    ):
        return None, None, None, None
    try:
        adf_p = _finite(adfuller(series, autolag="AIC")[1])
    except (ValueError, np.linalg.LinAlgError):
        adf_p = None
    try:
        lag = max(1, min(10, series.size // 2))
        ljung_p = _finite(
            acorr_ljungbox(series, lags=[lag], return_df=True)["lb_pvalue"].iloc[0]
        )
    except (ValueError, np.linalg.LinAlgError):
        ljung_p = None
    try:
        jb_p = _finite(jarque_bera(series).pvalue)
        excess = _finite(kurtosis(series, fisher=True))
    except (ValueError, np.linalg.LinAlgError):
        jb_p, excess = None, None
    return adf_p, ljung_p, jb_p, excess


def _finite(value: float) -> Optional[float]:
    """Статистика, вернувшая `nan`/`inf`, для отчёта не оценка — печатается «—»."""
    number = float(value)
    return number if math.isfinite(number) else None


def format_gate_block(results: Sequence[BacktestResult]) -> List[str]:
    lines = [
        "",
        "## Гейт §1",
        "",
        "| Прогон | Сделок | Gate | t всего | t OOS | Сделок OOS | Net без 3 лучших, ₽ "
        "| ADF p | Ljung–Box p | JB p | Эксцесс | Вердикт |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for result in results:
        metrics = evaluate_gate(result)
        verdict = (
            "пройден"
            if metrics.passed
            else "не пройден: " + ", ".join(metrics.failed_checks)
        )
        lines.append(
            f"| {result.run_name} | {metrics.trades} | {_number(metrics.gate)} | "
            f"{_number(metrics.t_all)} | {_number(metrics.t_oos)} | "
            f"{metrics.oos_trades} | "
            f"{_money(metrics.net_trimmed_rub)} | {_p_value(metrics.adf_p)} | "
            f"{_p_value(metrics.ljung_box_p)} | {_p_value(metrics.jarque_bera_p)} | "
            f"{_kurtosis(metrics.excess_kurtosis)} | {verdict} |"
        )
    lines.extend(
        [
            "",
            "Порог: `gate ≥ 1`, `t OOS ≥ 2`, сделок ≥ 50, прибыль после удаления трёх лучших "
            "дней положительна. OOS — последние 30 % периода по времени; `t всего` — по всей "
            "выборке (показывает, значим ли результат вне OOS). ADF/Ljung–Box/JB — "
            "по дневным доходностям; JB не входит в вердикт (тяжёлые хвосты в скальпинге — норма).",
            "",
        ]
    )
    return lines


def _number(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.2f}"


def _p_value(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.3g}"


def _kurtosis(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.2f}"


def _money(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ")


__all__ = [
    "GateMetrics",
    "evaluate_gate",
    "evaluate_trades",
    "format_gate_block",
    "GATE_MIN",
    "T_MIN",
    "MIN_TRADES",
]
