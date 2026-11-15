"""Нулевая модель: случайный вход при той же геометрии выхода и тех же издержках.

Зачем: прежде чем искать «лучший сигнал», нужно знать, как выглядит результат без сигнала
вовсе. Если случайный вход с теми же выходами даёт распределение net, в которое попадает
фактический результат, — правило входа не несёт информации, а весь плюс пришёл из геометрии
выхода, дрейфа цены или везения.

Сравнение идёт по **net на сделку**, а не по сумме: число сделок в случайных прогонах
не совпадает с фактическим (занятость позиции), и сумма сравнивала бы разные объёмы
выборки. Сумма печатается справочно.

Модель переиспользует `run_backtest`: тот же комиссионный фильтр, тот же размер позиции,
те же SL/TP и таймаут. Совпадает всё, кроме входа, — поэтому различие интерпретируется
однозначно.
"""
from __future__ import annotations

import random
import statistics
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from ..strategy import Signal, SignalType
from .commission import CommissionModel
from .engine import StrategyFn, run_backtest
from .models import Bar, Instrument, RunConfig


def empirical_p_value(values: Sequence[float], actual: float) -> float:
    """Доля значений не меньше фактического, с поправкой `+1` (Монте-Карло).

    Без поправки при малом числе прогонов можно получить «p = 0», что означает не
    «невозможно», а «не встретилось».
    """
    if not values:
        return 1.0
    better = sum(1 for value in values if value >= actual)
    return (better + 1) / (len(values) + 1)


@dataclass(frozen=True)
class NullResult:
    """Распределение результата случайного входа по `seeds` прогонам."""

    run_name: str
    seeds: int
    rate: float
    actual_net: float
    actual_trades: int
    nets: Tuple[float, ...]
    trades: Tuple[int, ...]

    @property
    def means(self) -> Tuple[float, ...]:
        """Net на сделку по каждому прогону — величина, не зависящая от числа сделок."""
        return tuple(
            net / count if count else 0.0 for net, count in zip(self.nets, self.trades)
        )

    @property
    def actual_mean(self) -> float:
        return self.actual_net / self.actual_trades if self.actual_trades else 0.0

    @property
    def mean_net(self) -> float:
        return statistics.fmean(self.nets) if self.nets else 0.0

    @property
    def median_net(self) -> float:
        return statistics.median(self.nets) if self.nets else 0.0

    @property
    def mean_per_trade(self) -> float:
        means = self.means
        return statistics.fmean(means) if means else 0.0

    @property
    def p95_net(self) -> float:
        """95-й процентиль суммы: «сколько даёт удачный случайный сценарий»."""
        if not self.nets:
            return 0.0
        ordered = sorted(self.nets)
        position = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
        return ordered[position]

    @property
    def p_value(self) -> float:
        """Доля случайных прогонов не хуже фактического (по net на сделку)."""
        return empirical_p_value(self.means, self.actual_mean)

    @property
    def percentile(self) -> float:
        """Место фактического результата в распределении, % (50 — ровно медиана)."""
        actual = self.actual_mean
        below = sum(1 for mean in self.means if mean < actual)
        return below / len(self.means) * 100.0 if self.means else 0.0

    @property
    def median_trades(self) -> int:
        return int(statistics.median(self.trades)) if self.trades else 0


def _random_strategy(rate: float, rng: random.Random) -> StrategyFn:
    """Случайный вход с вероятностью `rate` на бар, направление — 50/50.

    Выходы не отличаются от штатных: SL и TP считаются от текущего ATR теми же
    множителями, что приходят из прогона.
    """

    def strategy(
        ind, price: float, atr_mult_sl: float, atr_mult_tp: float  # noqa: ANN001
    ) -> Signal:
        if ind.atr is None or ind.atr <= 0 or rng.random() >= rate:
            return Signal(SignalType.HOLD, 0.0, price, None, None, "null: no entry")
        long = rng.random() < 0.5
        sign = 1.0 if long else -1.0
        sl = round(price - sign * atr_mult_sl * ind.atr, 9)
        tp = round(price + sign * atr_mult_tp * ind.atr, 9)
        return Signal(
            SignalType.BUY if long else SignalType.SELL,
            0.5,
            price,
            sl,
            tp,
            "null: random entry",
        )

    return strategy


def run_null(
    bars: Sequence[Bar],
    instrument: Instrument,
    config: RunConfig,
    commission: CommissionModel,
    interval_minutes: int,
    actual: Tuple[float, int],
    rate: Optional[float] = None,
    seeds: int = 200,
    seed0: int = 0,
    run_name: str = "",
) -> NullResult:
    """Прогнать `seeds` случайных входов и вернуть их распределение.

    `actual` — (net, число сделок) фактического прогона: с ним сравнивается распределение.
    `rate` — вероятность входа на бар; по умолчанию — доля баров, давшая фактическое число
    сделок (оценка сверху: занятость позиции снижает фактическую частоту).
    """
    if seeds < 1:
        raise ValueError("seeds must be positive")

    actual_net, actual_trades = actual
    total_bars = max(1, len(bars) - config.min_candles)
    entry_rate = rate if rate is not None else actual_trades / total_bars
    if not 0 < entry_rate <= 1:
        raise ValueError(f"rate must be in (0, 1], got {entry_rate!r}; прогонов без сделок не бывает")

    nets: list[float] = []
    counts: list[int] = []
    for offset in range(seeds):
        result = run_backtest(
            bars=bars,
            instrument=instrument,
            config=config,
            commission=commission,
            interval_minutes=interval_minutes,
            strategy_fn=_random_strategy(entry_rate, random.Random(seed0 + offset)),
            label=f"null-{seed0 + offset}",
            strategy_name="null",
        )
        nets.append(sum(trade.net_pnl for trade in result.trades))
        counts.append(len(result.trades))

    return NullResult(
        run_name=run_name or f"{instrument.name} {interval_minutes}min {commission.tariff}",
        seeds=seeds,
        rate=entry_rate,
        actual_net=actual_net,
        actual_trades=actual_trades,
        nets=tuple(nets),
        trades=tuple(counts),
    )


def format_null_markdown(results: Sequence[NullResult], title: str = "Нулевая модель") -> str:
    if not results:
        return f"# {title}\n\nПрогонов нет: сиды не заданы.\n"

    lines = [
        f"# {title}",
        "",
        (
            "Случайный вход с той же вероятностью, геометрией выхода и издержками. "
            "Сравнение — по net на сделку: число сделок в случайных прогонах "
            "отличается из-за занятости позиции."
        ),
        "",
        "| Прогон | Сидов | Факт: сделок / net ₽ / средний ₽ | Null: сделок / медиана ₽ / p95 ₽ "
        "| Null средний ₽ | p-value | Перцентиль |",
        "|---|---:|---|---|---:|---:|---:|",
    ]
    for result in results:
        lines.append(
            f"| {result.run_name} | {result.seeds} | {result.actual_trades} / "
            f"{_money(result.actual_net)} / {_money(result.actual_mean)} | "
            f"{result.median_trades} / {_money(result.median_net)} / {_money(result.p95_net)} | "
            f"{_money(result.mean_per_trade)} | {result.p_value:.3f} | "
            f"{result.percentile:.0f} |"
        )
    lines.extend(
        [
            "",
            "**Как читать.** p-value — доля случайных прогонов, давших средний net на сделку "
            "не хуже фактического. p ≤ 0.05 означает, что правило входа приносит то, чего "
            "случайный вход с теми же выходами не даёт. p > 0.20 — сигнал неотличим от шума: "
            "улучшать нужно не стратегию, а выход, издержки или горизонт.",
            "",
        ]
    )
    return "\n".join(lines)


def _money(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ")


__all__ = ["NullResult", "empirical_p_value", "format_null_markdown", "run_null"]
