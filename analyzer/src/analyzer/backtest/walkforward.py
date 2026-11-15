"""Walk-forward прогон: подбор параметров на обучающем окне, проверка на следующем.

Зачем отдельный режим: все прежние замеры in-sample — параметры подбирались и оценивались
на одних и тех же данных, поэтому «лучшая конфигурация» могла быть подгонкой. Здесь окна
разделены: на `train` выбирается лучшая конфигурация по net, на `test` она запускается без
подстройки. Оценка — только по сделкам, открытым внутри тестового окна; бары из хвоста `train`
перед ним идут как прогревочное окно индикаторов и в результат не попадают.

Метрики тестового окна: gross, комиссия, net, отношение gross/комиссия (гейт: ≥ 1) и
t-статистика среднего net-PnL (значимость: |t| ≥ 2).
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .commission import CommissionModel
from .engine import StrategyFn, run_backtest
from .models import Bar, Instrument, RunConfig, Trade

Params = Dict[str, float]
# Фабрика стратегии по точке сетки: (параметры) -> (функция сигнала, метка варианта).
StrategyBuilder = Callable[[Params], Tuple[StrategyFn, str]]


@dataclass(frozen=True)
class TradeStats:
    """Сводка по сделкам; `pnls` нужен для t-статистики (её нельзя сложить из сумм)."""

    trades: int = 0
    wins: int = 0
    gross: float = 0.0
    commission: float = 0.0
    net: float = 0.0
    pnls: Tuple[float, ...] = ()

    @classmethod
    def of(cls, trades: Sequence[Trade]) -> "TradeStats":
        pnls = tuple(trade.net_pnl for trade in trades)
        return cls(
            trades=len(trades),
            wins=sum(1 for value in pnls if value > 0),
            gross=sum(trade.gross_pnl for trade in trades),
            commission=sum(trade.commission for trade in trades),
            net=sum(pnls),
            pnls=pnls,
        )

    @property
    def ratio(self) -> Optional[float]:
        """gross / комиссия; None — комиссии не было (сделок нет)."""
        return self.gross / self.commission if self.commission else None

    @property
    def win_pct(self) -> float:
        return self.wins / self.trades * 100.0 if self.trades else 0.0

    @property
    def t_stat(self) -> float:
        if len(self.pnls) < 2:
            return 0.0
        std = statistics.stdev(self.pnls)
        if not std:
            return 0.0
        return statistics.fmean(self.pnls) / (std / len(self.pnls) ** 0.5)

    def merge(self, other: "TradeStats") -> "TradeStats":
        return TradeStats(
            trades=self.trades + other.trades,
            wins=self.wins + other.wins,
            gross=self.gross + other.gross,
            commission=self.commission + other.commission,
            net=self.net + other.net,
            pnls=self.pnls + other.pnls,
        )


@dataclass(frozen=True)
class Fold:
    """Границы окон в индексах баров; `*_end` — не включая."""

    index: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int


@dataclass(frozen=True)
class FoldResult:
    fold: Fold
    label: str
    train: TradeStats
    test: TradeStats
    test_from: Optional[datetime] = None
    note: str = ""

    @property
    def chosen(self) -> bool:
        return not self.note


@dataclass(frozen=True)
class WalkforwardResult:
    instrument: Instrument
    interval_minutes: int
    tariff: str
    train_bars: int
    test_bars: int
    min_train_trades: int
    folds: Tuple[FoldResult, ...]

    @property
    def out_of_sample(self) -> TradeStats:
        """Сводка по всем тестовым окнам — то, чему можно верить больше in-sample."""
        total = TradeStats()
        for fold in self.folds:
            total = total.merge(fold.test)
        return total

    @property
    def run_name(self) -> str:
        return f"{self.instrument.name} {self.interval_minutes}min {self.tariff}"


def trades_from(trades: Sequence[Trade], since: datetime) -> List[Trade]:
    """Оставить сделки, открытые не раньше `since` (отсечка прогревочного окна)."""
    return [trade for trade in trades if trade.entry_ts >= since]


def build_folds(total_bars: int, train_bars: int, test_bars: int) -> List[Fold]:
    """Скользящие окна: train фиксированной длины, test — следующий блок, шаг = длина test.

    Неполный последний блок отбрасывается: обрезанное тестовое окно сравнимо только само
    с собой и искажает агрегат.
    """
    if train_bars < 1 or test_bars < 1:
        raise ValueError("train_bars and test_bars must be positive")
    folds: List[Fold] = []
    start = 0
    while start + train_bars + test_bars <= total_bars:
        folds.append(
            Fold(
                index=len(folds) + 1,
                train_start=start,
                train_end=start + train_bars,
                test_start=start + train_bars,
                test_end=start + train_bars + test_bars,
            )
        )
        start += test_bars
    return folds


def run_walkforward(
    bars: Sequence[Bar],
    instrument: Instrument,
    config: RunConfig,
    commission: CommissionModel,
    interval_minutes: int,
    grid: Sequence[Params],
    build_strategy: StrategyBuilder,
    train_bars: int,
    test_bars: int,
    min_train_trades: int = 30,
    strategy_name: str = "",
) -> WalkforwardResult:
    """Прогнать walk-forward по одной серии.

    Кандидат на `train` — конфигурация с наибольшим net при числе сделок ≥ `min_train_trades`.
    Если кандидата нет, окно помечается `note` и тестовый прогон не делается: подгонка
    на пустой выборке — это выбор шума.
    """
    if not grid:
        raise ValueError("grid must not be empty")
    if min_train_trades < 1:
        raise ValueError("min_train_trades must be positive")

    bar_list = list(bars)
    folds = build_folds(len(bar_list), train_bars, test_bars)
    if not folds:
        raise ValueError(
            f"баров {len(bar_list)} мало для окон train={train_bars} + test={test_bars}"
        )

    results: List[FoldResult] = []
    for fold in folds:
        train_slice = bar_list[fold.train_start : fold.train_end]
        best: Optional[Tuple[TradeStats, Params, str]] = None
        for params in grid:
            strategy_fn, label = build_strategy(params)
            train_result = run_backtest(
                bars=train_slice,
                instrument=instrument,
                config=config,
                commission=commission,
                interval_minutes=interval_minutes,
                strategy_fn=strategy_fn,
                label=label,
                strategy_name=strategy_name,
            )
            stats = TradeStats.of(train_result.trades)
            if stats.trades < min_train_trades:
                continue
            if best is None or stats.net > best[0].net:
                best = (stats, params, label)

        if best is None:
            results.append(
                FoldResult(
                    fold=fold,
                    label="",
                    train=TradeStats(),
                    test=TradeStats(),
                    note=f"нет кандидата с >= {min_train_trades} сделками",
                )
            )
            continue

        train_stats, params, label = best
        # Прогрев индикаторов: хвост train перед test, сделки из него не учитываются.
        warmup = min(config.window, fold.test_start - fold.train_start)
        test_slice = bar_list[fold.test_start - warmup : fold.test_end]
        strategy_fn, label = build_strategy(params)
        test_result = run_backtest(
            bars=test_slice,
            instrument=instrument,
            config=config,
            commission=commission,
            interval_minutes=interval_minutes,
            strategy_fn=strategy_fn,
            label=label,
            strategy_name=strategy_name,
        )
        test_start_ts = bar_list[fold.test_start].ts
        test_trades = trades_from(test_result.trades, test_start_ts)
        results.append(
            FoldResult(
                fold=fold,
                label=label,
                train=train_stats,
                test=TradeStats.of(test_trades),
                test_from=test_start_ts,
            )
        )

    return WalkforwardResult(
        instrument=instrument,
        interval_minutes=interval_minutes,
        tariff=commission.tariff,
        train_bars=train_bars,
        test_bars=test_bars,
        min_train_trades=min_train_trades,
        folds=tuple(results),
    )


def format_walkforward_markdown(
    results: Sequence[WalkforwardResult], title: str = "Walk-forward mean reversion"
) -> str:
    if not results:
        return f"# {title}\n\nПрогонов нет: данные или сетка не заданы.\n"

    first = results[0]
    lines = [
        f"# {title}",
        "",
        (
            f"Окна: train {first.train_bars} баров → test {first.test_bars} баров, шаг = test, "
            f"минимум сделок на train {first.min_train_trades}. Оценка — только сделки, "
            "открытые внутри тестового окна; хвост train используется как прогрев индикаторов."
        ),
        "",
        "| Прогон | Окно | Train сделок | Train net ₽ | Параметры | Test сделок | Win % "
        "| Gross ₽ | Комиссия ₽ | Net ₽ | Гейт | t |",
        "|---|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for result in results:
        for fold in result.folds:
            window = (
                f"{fold.fold.index}: {fold.test_from.date().isoformat()}"
                if fold.test_from
                else str(fold.fold.index)
            )
            if not fold.chosen:
                lines.append(
                    f"| {result.run_name} | {window} | — | — | — | — | — | — | — | — | — | — |"
                )
                continue
            test = fold.test
            lines.append(
                f"| {result.run_name} | {window} | {fold.train.trades} | "
                f"{_money(fold.train.net)} | {fold.label} | {test.trades} | "
                f"{test.win_pct:.1f} | {_money(test.gross)} | {_money(test.commission)} | "
                f"{_money(test.net)} | {_ratio(test.ratio)} | {test.t_stat:.2f} |"
            )
        oos = result.out_of_sample
        lines.append(
            f"| **{result.run_name} OOS** | все | — | — | — | {oos.trades} | {oos.win_pct:.1f} | "
            f"{_money(oos.gross)} | {_money(oos.commission)} | {_money(oos.net)} | "
            f"{_ratio(oos.ratio)} | {oos.t_stat:.2f} |"
        )

    lines.extend(
        [
            "",
            "«Гейт» = gross / комиссия на тестовых окнах: кандидат только при ≥ 1 "
            "и положительном Net. «t» — t-статистика среднего net-PnL: значимость при |t| ≥ 2. "
            "Строки «нет кандидата» означают, что на train ни одна конфигурация не набрала "
            "минимум сделок — тест не запускался.",
            "",
        ]
    )
    return "\n".join(lines)


def _money(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ")


def _ratio(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.2f}"


__all__ = [
    "Fold",
    "FoldResult",
    "TradeStats",
    "WalkforwardResult",
    "build_folds",
    "format_walkforward_markdown",
    "run_walkforward",
    "trades_from",
]
