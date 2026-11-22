"""Модель с фильтром кластеризации потока — шаг 1.5 `to_do.md` §4.1.

`branching ratio` из `Indicators.hawkes` показывает, идут ли сделки лавиной. При `n → 1` поток
информирован и кластеризован, mean reversion в нём убыточен (`docs/mat_analyze.md` §2), поэтому
модель фильтрует вход, а не переключает направление: знак лавины из интенсивности не следует.

Модель stateless: оценка потока приходит в индикаторах. В CSV-истории сделок нет, поэтому в
бэктесте модель остаётся в HOLD — ограничение данных, а не поведения.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, ClassVar, Mapping, Sequence

from ..indicators import Indicators
from ..strategy import Signal, SignalType

_VERSION = "1.0"
HAWKES_PARAMS = ("branching_threshold",)


@dataclass(frozen=True)
class HawkesConfig:
    """Порог кластеризации и пороги входа mean reversion."""

    branching_threshold: float = 0.7
    z_entry: float = 2.0
    rsi_buy: float = 25.0
    rsi_sell: float = 75.0

    def __post_init__(self) -> None:
        if not 0.0 < self.branching_threshold <= 1.0:
            raise ValueError("hawkes: branching_threshold must be in (0, 1]")
        if self.z_entry <= 0:
            raise ValueError("hawkes: z_entry must be positive")
        if not 0 < self.rsi_buy < 50:
            raise ValueError("hawkes: rsi_buy must be in (0, 50)")
        if not 50 < self.rsi_sell < 100:
            raise ValueError("hawkes: rsi_sell must be in (50, 100)")

    @property
    def label(self) -> str:
        return f"hawkes-{self.branching_threshold:g}-z{self.z_entry:g}"


@dataclass(frozen=True)
class HawkesModel:
    config: HawkesConfig = HawkesConfig()

    name: ClassVar[str] = "hawkes"
    version: ClassVar[str] = _VERSION

    @property
    def label(self) -> str:
        return self.config.label

    def params(self) -> Mapping[str, Any]:
        return MappingProxyType(
            {
                "branching_threshold": self.config.branching_threshold,
                "z_entry": self.config.z_entry,
                "rsi_buy": self.config.rsi_buy,
                "rsi_sell": self.config.rsi_sell,
            }
        )

    def fit(self, history: Sequence[Any]) -> None:
        """Оценка потока приходит в индикаторах: на истории баров ничего не оценивается."""
        return None

    def predict(
        self,
        ind: Indicators,
        price: float,
        atr_mult_sl: float = 1.5,
        atr_mult_tp: float = 2.0,
    ) -> Signal:
        if ind.hawkes is None:
            return Signal(SignalType.HOLD, 0.0, price, None, None, "hawkes: no trades")
        if ind.hawkes >= self.config.branching_threshold:
            return Signal(
                SignalType.HOLD,
                0.0,
                price,
                None,
                None,
                f"hawkes: clustered trades (branching {ind.hawkes:.2f} >= "
                f"{self.config.branching_threshold:g})",
            )
        if ind.zscore is None or ind.rsi is None or ind.atr is None or ind.atr <= 0:
            return Signal(SignalType.HOLD, 0.0, price, None, None, "insufficient data")

        if ind.zscore <= -self.config.z_entry and ind.rsi < self.config.rsi_buy:
            return Signal(
                SignalType.BUY,
                _confidence(-ind.zscore - self.config.z_entry, self.config.z_entry),
                price,
                round(price - atr_mult_sl * ind.atr, 9),
                round(price + atr_mult_tp * ind.atr, 9),
                f"hawkes calm: z {ind.zscore:.2f} <= -{self.config.z_entry:g}",
            )
        if ind.zscore >= self.config.z_entry and ind.rsi > self.config.rsi_sell:
            return Signal(
                SignalType.SELL,
                _confidence(ind.zscore - self.config.z_entry, self.config.z_entry),
                price,
                round(price + atr_mult_sl * ind.atr, 9),
                round(price - atr_mult_tp * ind.atr, 9),
                f"hawkes calm: z {ind.zscore:.2f} >= {self.config.z_entry:g}",
            )
        return Signal(SignalType.HOLD, 0.0, price, None, None, "hawkes: no signal")

    def __call__(
        self, ind: Indicators, price: float, atr_mult_sl: float, atr_mult_tp: float
    ) -> Signal:
        """Совместимость с `backtest.engine.StrategyFn`."""
        return self.predict(ind, price, atr_mult_sl, atr_mult_tp)


def _confidence(excess: float, scale: float) -> float:
    if scale <= 0:
        return 0.5
    return min(1.0, 0.5 + 0.5 * (max(excess, 0.0) / scale))


def hawkes_config(options: Any) -> HawkesConfig:
    raw = dict(getattr(options, "model_params", {}) or {})
    unknown = sorted(set(raw) - set(HAWKES_PARAMS))
    if unknown:
        raise ValueError(
            f"hawkes: неизвестные параметры {unknown}; доступны {list(HAWKES_PARAMS)}"
        )
    defaults = HawkesConfig()
    rsi_buy = defaults.rsi_buy if options.rsi_buy is None else options.rsi_buy
    rsi_sell = defaults.rsi_sell if options.rsi_sell is None else options.rsi_sell
    return HawkesConfig(
        branching_threshold=float(
            raw.get("branching_threshold", defaults.branching_threshold)
        ),
        z_entry=float(options.z_entry),
        rsi_buy=float(rsi_buy),
        rsi_sell=float(rsi_sell),
    )
