"""Модель микроструктуры: дисбаланс стакана и токсичность потока — шаг 1.3.

Направление входа задаёт знак order imbalance (кто доминирует в стакане), а VPIN служит
фильтром информированного потока: при высокой доле дисбаланса объёма цена, скорее всего,
движется против маркет-мейкера, и входить по стакану дороже, чем пропустить.

Модель stateless: стакан и VPIN приходят в `Indicators`. Бэктест на CSV стакана не содержит,
поэтому в прогоне модель всегда остаётся в HOLD — это ограничение данных, не поведения.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, ClassVar, Mapping, Sequence

from ..indicators import Indicators
from ..strategy import Signal, SignalType

_VERSION = "1.0"
MICRO_PARAMS = ("obi_entry", "vpin_max")


@dataclass(frozen=True)
class MicroConfig:
    """Порог дисбаланса стакана и потолок токсичности потока."""

    obi_entry: float = 0.2
    vpin_max: float = 0.5

    def __post_init__(self) -> None:
        if not 0.0 < self.obi_entry <= 1.0:
            raise ValueError("micro: obi_entry must be in (0, 1]")
        if not 0.0 < self.vpin_max <= 1.0:
            raise ValueError("micro: vpin_max must be in (0, 1]")

    @property
    def label(self) -> str:
        return f"micro-{self.obi_entry:g}-{self.vpin_max:g}"


@dataclass(frozen=True)
class MicrostructureModel:
    config: MicroConfig = MicroConfig()

    name: ClassVar[str] = "microstructure"
    version: ClassVar[str] = _VERSION

    @property
    def label(self) -> str:
        return self.config.label

    def params(self) -> Mapping[str, Any]:
        return MappingProxyType(
            {"obi_entry": self.config.obi_entry, "vpin_max": self.config.vpin_max}
        )

    def fit(self, history: Sequence[Any]) -> None:
        """Параметров оценки нет: стакан и VPIN приходят в индикаторах."""
        return None

    def predict(
        self,
        ind: Indicators,
        price: float,
        atr_mult_sl: float = 1.5,
        atr_mult_tp: float = 2.0,
    ) -> Signal:
        if ind.order_imbalance is None:
            return Signal(SignalType.HOLD, 0.0, price, None, None, "micro: no order book")
        if ind.vpin is None:
            return Signal(SignalType.HOLD, 0.0, price, None, None, "micro: no trades")
        if ind.vpin > self.config.vpin_max:
            return Signal(
                SignalType.HOLD,
                0.0,
                price,
                None,
                None,
                f"micro: toxic flow (vpin {ind.vpin:.2f} > {self.config.vpin_max:g})",
            )
        if ind.atr is None or ind.atr <= 0:
            return Signal(SignalType.HOLD, 0.0, price, None, None, "insufficient data")

        threshold = self.config.obi_entry
        if ind.order_imbalance >= threshold:
            return Signal(
                SignalType.BUY,
                _confidence(ind.order_imbalance - threshold, 1.0 - threshold),
                price,
                round(price - atr_mult_sl * ind.atr, 9),
                round(price + atr_mult_tp * ind.atr, 9),
                f"micro: obi {ind.order_imbalance:.2f} >= {threshold:g}",
            )
        if ind.order_imbalance <= -threshold:
            return Signal(
                SignalType.SELL,
                _confidence(-ind.order_imbalance - threshold, 1.0 - threshold),
                price,
                round(price + atr_mult_sl * ind.atr, 9),
                round(price - atr_mult_tp * ind.atr, 9),
                f"micro: obi {ind.order_imbalance:.2f} <= -{threshold:g}",
            )
        return Signal(SignalType.HOLD, 0.0, price, None, None, "micro: no signal")

    def __call__(
        self, ind: Indicators, price: float, atr_mult_sl: float, atr_mult_tp: float
    ) -> Signal:
        """Совместимость с `backtest.engine.StrategyFn`."""
        return self.predict(ind, price, atr_mult_sl, atr_mult_tp)


def _confidence(excess: float, scale: float) -> float:
    if scale <= 0:
        return 0.5
    return min(1.0, 0.5 + 0.5 * (max(excess, 0.0) / scale))


def microstructure_config(options: Any) -> MicroConfig:
    raw = dict(getattr(options, "model_params", {}) or {})
    unknown = sorted(set(raw) - set(MICRO_PARAMS))
    if unknown:
        raise ValueError(
            f"micro: неизвестные параметры {unknown}; доступны {list(MICRO_PARAMS)}"
        )
    defaults = MicroConfig()
    return MicroConfig(
        obi_entry=float(raw.get("obi_entry", defaults.obi_entry)),
        vpin_max=float(raw.get("vpin_max", defaults.vpin_max)),
    )
