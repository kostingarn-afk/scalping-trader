"""Кросс-секционный momentum на панели инструментов.

Отличие от `strategy_momentum` принципиальное: там правило решало «входить или нет» по одному
инструменту, здесь — «какие бумаги держать в длинной и короткой части книги». Итог зависит не
от того, угадано ли движение одной бумаги, а от того, устойчиво ли разделение вселенной на
сильных и слабых: один неудачный тикер тонет в корзине.

Конструкция: на каждой дате ребаланса вселенная ранжируется по доходности за `lookback_days`,
`legs` имён из верхушки покупаются, `legs` из низа продаются, веса равные, книга
долларово-нейтральна (каждая сторона — половина капитала). Позиции удерживаются до следующего
ребаланса, стопов нет: для нейтральной книги они искажают сигнал, а не защищают его.

Что модель **не** учитывает (известные искажения, а не «потом посчитаем»):
- стоимость заимствования бумаг и требования по ГО для шорта;
- делистинг: вселенная собрана из сегодняшних ликвидных имён (survivorship bias);
- внутрипериодные движения: PnL считается от закрытия до закрытия;
- проскальзывание — только комиссия, по издержкам изменившихся ног.

Издержки платят только изменившиеся позиции: имя, сохранившее сторону, не переторговывается.
Это существенно — при устойчивом ранжировании оборот и комиссия падают кратно.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .loader import HistoryDataset
from .nullmodel import empirical_p_value
from .walkforward import Fold, build_folds

# Функция преобразования моментума в оценки: точка расширения для нулевой модели.
Ranker = Callable[[Sequence[float]], Sequence[float]]


@dataclass(frozen=True)
class PanelConfig:
    """Параметры панельного прогона.

    `cost_per_side` — ставка брокера за сторону сделки по бумагам (тариф «Трейдер» — 0.0005).
    """

    lookback_days: int = 5
    rebalance_days: int = 5
    legs: int = 4
    cost_per_side: float = 0.0005

    def __post_init__(self) -> None:
        if self.lookback_days < 1:
            raise ValueError("lookback_days must be positive")
        if self.rebalance_days < 1:
            raise ValueError("rebalance_days must be positive")
        if self.legs < 1:
            raise ValueError("legs must be positive")
        if self.cost_per_side < 0:
            raise ValueError("cost_per_side must not be negative")

    @property
    def label(self) -> str:
        base = f"L{self.lookback_days}-R{self.rebalance_days}-k{self.legs}"
        # Ставка входит в метку, только если отличается от стандартной: иначе одинаковые
        # метки у точек сетки, различающихся лишь издержками, склеили бы разные прогоны.
        if self.cost_per_side != 0.0005:
            base += f"-c{self.cost_per_side * 100:g}"
        return base


@dataclass(frozen=True)
class PanelPeriod:
    """Один период удержания: от даты ребаланса до следующей даты ребаланса."""

    start: date
    end: date
    universe: int
    longs: Tuple[str, ...]
    shorts: Tuple[str, ...]
    # Доходности в долях капитала периода: gross — до издержек, cost — комиссия, net — после.
    gross: float
    cost: float
    net: float
    benchmark: float


@dataclass(frozen=True)
class PanelResult:
    config: PanelConfig
    periods: Tuple[PanelPeriod, ...]
    skipped: int = 0

    @property
    def gross(self) -> float:
        return sum(period.gross for period in self.periods)

    @property
    def cost(self) -> float:
        return sum(period.cost for period in self.periods)

    @property
    def net(self) -> float:
        return sum(period.net for period in self.periods)

    @property
    def benchmark(self) -> float:
        """Суммарная доходность равновзвешенной корзины за те же периоды — «рынок»."""
        return sum(period.benchmark for period in self.periods)

    @property
    def nets(self) -> Tuple[float, ...]:
        return tuple(period.net for period in self.periods)

    @property
    def mean(self) -> float:
        nets = self.nets
        return float(np.mean(nets)) if nets else 0.0

    @property
    def hit_rate(self) -> float:
        nets = self.nets
        if not nets:
            return 0.0
        return sum(1 for value in nets if value > 0) / len(nets) * 100.0

    @property
    def t_stat(self) -> float:
        nets = self.nets
        if len(nets) < 2:
            return 0.0
        std = float(np.std(nets, ddof=1))
        if not std:
            return 0.0
        return self.mean / (std / len(nets) ** 0.5)

    @property
    def max_drawdown(self) -> float:
        """Максимальная просадка суммы периодов, в долях капитала."""
        equity = np.cumsum([0.0, *self.nets])
        peak = np.maximum.accumulate(equity)
        return float(np.max(peak - equity)) if len(equity) else 0.0

    @property
    def ratio(self) -> Optional[float]:
        """gross / комиссия — гейт `to_do.md` §3 (≥ 1)."""
        return self.gross / self.cost if self.cost else None

    @property
    def run_name(self) -> str:
        return f"panel {self.config.label}"


@dataclass(frozen=True)
class PanelFoldResult:
    """Результат walk-forward: параметры выбирались на train, оценены на test."""

    fold: Fold
    label: str
    train: PanelResult
    test: PanelResult
    test_from: Optional[date] = None
    note: str = ""

    @property
    def chosen(self) -> bool:
        return not self.note


@dataclass(frozen=True)
class PanelNull:
    """Распределение net по периодам при перемешанном ранжировании."""

    config: PanelConfig
    seeds: int
    actual_mean: float
    means: Tuple[float, ...]
    totals: Tuple[float, ...]

    @property
    def median(self) -> float:
        return float(np.median(self.totals)) if self.totals else 0.0

    @property
    def p_value(self) -> float:
        return empirical_p_value(self.means, self.actual_mean)

    @property
    def percentile(self) -> float:
        if not self.means:
            return 0.0
        return sum(1 for mean in self.means if mean < self.actual_mean) / len(self.means) * 100.0


def build_daily_closes(datasets: Sequence[HistoryDataset]) -> pd.DataFrame:
    """Матрица закрытий по торговым дням: индекс — дата, столбцы — тикеры.

    Из часовых баров берётся последнее закрытие дня. Дни, в которые инструмент не торговался,
    остаются `NaN` — заполнять их нельзя: пропуск торгов и нулевая доходность это разные факты.
    """
    columns: Dict[str, pd.Series] = {}
    for dataset in datasets:
        if not dataset.bars:
            continue
        frame = pd.DataFrame(
            {
                "date": [bar.ts.date() for bar in dataset.bars],
                "close": [bar.close for bar in dataset.bars],
            }
        )
        columns[dataset.instrument.ticker] = frame.groupby("date")["close"].last()
    if not columns:
        return pd.DataFrame()
    return pd.DataFrame(columns).sort_index()


def select_universe(closes: pd.DataFrame, spec: str) -> pd.DataFrame:
    """Оставить только перечисленные тикеры (`"SBER,GAZP"`); пустой список — вся панель.

    Явный список, а не автоматический отбор: вселенная — исследовательское решение (уже
    отсеяны фьючерсы и бумаги с ATR ниже издержек), и оно должно быть видно в команде,
    а не прятаться в коде.
    """
    names = [name.strip().upper() for name in spec.split(",") if name.strip()]
    if not names:
        return closes
    unknown = [name for name in names if name not in closes.columns]
    if unknown:
        raise ValueError(f"panel-universe: нет данных по {', '.join(unknown)}")
    return closes[[name for name in names if name in closes.columns]]


def run_panel(
    closes: pd.DataFrame,
    config: PanelConfig,
    start: Optional[date] = None,
    end: Optional[date] = None,
    ranker: Optional[Ranker] = None,
) -> PanelResult:
    """Прогнать кросс-секционную книгу.

    Периоды не перекрываются: шаг равен `rebalance_days`. Период пропускается, если во вселенной
    меньше `2 × legs` имён с данными на все три даты (прошлое, вход, выход) — собрать обе стороны
    нечем.
    """
    if closes.empty:
        return PanelResult(config=config, periods=())

    dates = list(closes.index)
    periods: List[PanelPeriod] = []
    skipped = 0
    previous_longs: frozenset = frozenset()
    previous_shorts: frozenset = frozenset()

    step = config.rebalance_days
    index = config.lookback_days
    while index + step < len(dates):
        past_index = index - config.lookback_days
        entry_index = index
        exit_index = index + step
        rebalance_date = dates[entry_index]
        exit_date = dates[exit_index]
        index += step
        if start is not None and rebalance_date < start:
            continue
        if end is not None and rebalance_date >= end:
            continue

        past = closes.iloc[past_index]
        entry = closes.iloc[entry_index]
        exit_row = closes.iloc[exit_index]

        usable = past.notna() & entry.notna() & exit_row.notna()
        names = list(usable[usable].index)
        if len(names) < 2 * config.legs:
            skipped += 1
            continue

        momentum = (entry[names] / past[names] - 1.0).to_numpy(dtype=float)
        scores = np.asarray(ranker(momentum) if ranker else momentum, dtype=float)
        # Сортировка с разрешением ничьих по имени: результат не должен зависеть от порядка CSV.
        order = sorted(range(len(names)), key=lambda position: (scores[position], names[position]))
        shorts = tuple(names[position] for position in order[: config.legs])
        longs = tuple(names[position] for position in order[-config.legs :])

        long_returns = [(exit_row[name] / entry[name] - 1.0) for name in longs]
        short_returns = [(exit_row[name] / entry[name] - 1.0) for name in shorts]
        gross = float(np.mean(long_returns)) - float(np.mean(short_returns))

        # Платит только изменившаяся нога: две стороны по cost_per_side в доле её номинала.
        changed = len(set(longs) - previous_longs) + len(set(shorts) - previous_shorts)
        cost = changed * 2 * config.cost_per_side / (2 * config.legs)
        previous_longs = frozenset(longs)
        previous_shorts = frozenset(shorts)

        periods.append(
            PanelPeriod(
                start=rebalance_date,
                end=exit_date,
                universe=len(names),
                longs=longs,
                shorts=shorts,
                gross=gross,
                cost=cost,
                net=gross - cost,
                benchmark=float(np.mean([(exit_row[name] / entry[name] - 1.0) for name in names])),
            )
        )

    return PanelResult(config=config, periods=tuple(periods), skipped=skipped)


def _shuffled_ranker(rng: random.Random) -> Ranker:
    """Оценки перемешиваются внутри сечения: состав периода и оборот те же, информация — нет."""

    def ranker(momentum: Sequence[float]) -> Sequence[float]:
        values = list(momentum)
        rng.shuffle(values)
        return values

    return ranker


def run_panel_null(
    closes: pd.DataFrame,
    config: PanelConfig,
    actual: PanelResult,
    seeds: int = 200,
    seed0: int = 0,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> PanelNull:
    """Нулевая модель панели: ранжирование перемешано, всё остальное как в фактическом прогоне."""
    if seeds < 1:
        raise ValueError("seeds must be positive")

    means: List[float] = []
    totals: List[float] = []
    for offset in range(seeds):
        result = run_panel(
            closes,
            config,
            start=start,
            end=end,
            ranker=_shuffled_ranker(random.Random(seed0 + offset)),
        )
        means.append(result.mean)
        totals.append(result.net)

    return PanelNull(
        config=config,
        seeds=seeds,
        actual_mean=actual.mean,
        means=tuple(means),
        totals=tuple(totals),
    )


def run_panel_walkforward(
    closes: pd.DataFrame,
    grid: Sequence[PanelConfig],
    train_days: int,
    test_days: int,
    min_periods: int = 10,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> List[PanelFoldResult]:
    """Подбор конфигурации на train, оценка на следующем блоке.

    Выбор — по net на train при числе периодов ≥ `min_periods`. Прогрев индикаторов не нужен
    (сигнал считается по доходностям, а не по скользящим средним), поэтому тестовый прогон
    начинается ровно с границы окна: сделки внутри test не могут пересечь её начало, так как
    период удержания не короче `rebalance_days`.
    """
    if not grid:
        raise ValueError("grid must not be empty")
    if train_days < 1 or test_days < 1:
        raise ValueError("train_days and test_days must be positive")

    dates = list(closes.index)
    if start is not None:
        dates = [value for value in dates if value >= start]
    if end is not None:
        dates = [value for value in dates if value < end]
    folds = build_folds(len(dates), train_days, test_days)
    if not folds:
        raise ValueError(
            f"дней {len(dates)} мало для окон train={train_days} + test={test_days}"
        )

    results: List[PanelFoldResult] = []
    for fold in folds:
        train_from = dates[fold.train_start]
        train_to = dates[fold.train_end] if fold.train_end < len(dates) else None
        test_from = dates[fold.test_start]
        test_to = dates[fold.test_end] if fold.test_end < len(dates) else None

        best: Optional[Tuple[float, PanelConfig, PanelResult]] = None
        for config in grid:
            train = run_panel(closes, config, start=train_from, end=train_to)
            if len(train.periods) < min_periods:
                continue
            if best is None or train.net > best[2].net:
                best = (train.net, config, train)

        if best is None:
            results.append(
                PanelFoldResult(
                    fold=fold,
                    label="",
                    train=PanelResult(config=grid[0], periods=()),
                    test=PanelResult(config=grid[0], periods=()),
                    test_from=test_from,
                    note=f"нет кандидата с >= {min_periods} периодами",
                )
            )
            continue

        _, config, train = best
        test = run_panel(closes, config, start=test_from, end=test_to)
        results.append(
            PanelFoldResult(
                fold=fold,
                label=config.label,
                train=train,
                test=test,
                test_from=test_from,
            )
        )
    return results


def aggregate_panel(results: Sequence[PanelResult]) -> PanelResult:
    """Склеить периоды нескольких прогонов одной конфигурации (для строки OOS)."""
    if not results:
        raise ValueError("results must not be empty")
    periods = tuple(period for result in results for period in result.periods)
    return PanelResult(
        config=results[0].config,
        periods=periods,
        skipped=sum(result.skipped for result in results),
    )


def format_panel_markdown(
    results: Sequence[PanelResult],
    nulls: Sequence[PanelNull] = (),
    title: str = "Кросс-секционный momentum",
) -> str:
    if not results:
        return f"# {title}\n\nПрогонов нет: данных или сетки не задано.\n"

    first = results[0].config
    lines = [
        f"# {title}",
        "",
        (
            f"Панель: long/short по {first.legs} имён, доходность за {first.lookback_days} дн, "
            f"ребаланс каждые {first.rebalance_days} дн, издержки {first.cost_per_side * 100:.2f} % "
            "за сторону (платят только изменившиеся ноги), книга долларово-нейтральна."
        ),
        "",
        "| Прогон | Периодов | Пропуск | Gross % | Комиссия % | Net % | Net/период % | Hit % "
        "| t | Гейт | Max DD % |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for result in results:
        lines.append(
            f"| {result.run_name} | {len(result.periods)} | {result.skipped} | "
            f"{result.gross * 100:.2f} | {result.cost * 100:.2f} | {result.net * 100:.2f} | "
            f"{result.mean * 100:.3f} | {result.hit_rate:.1f} | {result.t_stat:.2f} | "
            f"{'—' if result.ratio is None else f'{result.ratio:.2f}'} | "
            f"{result.max_drawdown * 100:.2f} |"
        )

    if results:
        bench = results[0].benchmark * 100
        lines.extend(
            [
                "",
                f"Равновзвешенная корзина тех же периодов: **{bench:.2f} %** — ориентир рынка. "
                "Нейтральная книга не должна повторять этот результат; совпадение знака и "
                "масштаба означает, что «нейтральность» не работает.",
            ]
        )

    if nulls:
        lines.extend(
            [
                "",
                "### Нулевая модель: ранжирование перемешано",
                "",
                "| Прогон | Сидов | Факт net % | Null медиана net % | Null средний/период % "
                "| p-value | Перцентиль |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for null in nulls:
            lines.append(
                f"| panel {null.config.label} | {null.seeds} | {null.actual_mean * 100:.3f} | "
                f"{null.median * 100:.2f} | {float(np.mean(null.means)) * 100:.3f} | "
                f"{null.p_value:.3f} | {null.percentile:.0f} |"
            )
        lines.extend(
            [
                "",
                "p — доля перемешанных прогонов с средним net на период не хуже фактического. "
                "p ≤ 0.05: ранжирование несёт информацию; p > 0.20: разделение на сильных и "
                "слабых неотличимо от случайного.",
            ]
        )

    lines.append("")
    return "\n".join(lines)


def format_panel_walkforward_markdown(
    folds: Sequence[PanelFoldResult],
    title: str = "Walk-forward панели",
) -> str:
    if not folds:
        return f"# {title}\n\nОкон нет: данных или сетки не задано.\n"

    lines = [
        f"# {title}",
        "",
        "Параметры выбираются на train по net, затем конфигурация прогоняется на следующем "
        "блоке без подстройки. Оценка — только периоды, начавшиеся внутри test.",
        "",
        "| Окно | Train: периодов / net % | Параметры | Test: периодов / net % | Net/период % "
        "| t | Гейт |",
        "|---|---:|---|---:|---:|---:|---:|",
    ]
    for fold in folds:
        window = f"{fold.fold.index}: {fold.test_from.isoformat() if fold.test_from else '—'}"
        if not fold.chosen:
            lines.append(f"| {window} | — | — | — | — | — | — |")
            continue
        test = fold.test
        lines.append(
            f"| {window} | {len(fold.train.periods)} / {fold.train.net * 100:.2f} | {fold.label} | "
            f"{len(test.periods)} / {test.net * 100:.2f} | {test.mean * 100:.3f} | "
            f"{test.t_stat:.2f} | {'—' if test.ratio is None else f'{test.ratio:.2f}'} |"
        )

    oos = aggregate_panel([fold.test for fold in folds if fold.chosen])
    lines.extend(
        [
            f"| **OOS** | — | — | {len(oos.periods)} / {oos.net * 100:.2f} | "
            f"{oos.mean * 100:.3f} | {oos.t_stat:.2f} | "
            f"{'—' if oos.ratio is None else f'{oos.ratio:.2f}'} |",
            "",
            "Гейт = gross / комиссия, кандидат при ≥ 1 и положительном net; |t| ≥ 2 — "
            "средний net на период значимо отличен от нуля.",
            "",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "PanelConfig",
    "PanelFoldResult",
    "PanelNull",
    "PanelPeriod",
    "PanelResult",
    "aggregate_panel",
    "build_daily_closes",
    "format_panel_markdown",
    "format_panel_walkforward_markdown",
    "run_panel",
    "run_panel_null",
    "run_panel_walkforward",
    "select_universe",
]
