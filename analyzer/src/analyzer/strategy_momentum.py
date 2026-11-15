"""Стратегия momentum: вход в сторону движения (продолжение тренда).

Основание — замер, а не вкус: mean reversion (`strategy.py`, `strategy_v2.py`) на этих данных
даёт gross ≈ 0 при любом таймфрейме и любой комбинации фильтров, а перевёрнутый вход на
часовом GAZP дал единственную конфигурацию, где gross покрывает издержки (+310 ₽ на сделку,
gross/комиссия 1.65, 98 сделок за 10 месяцев).

Правило: цена ушла за канал BB на `z_entry` σ и RSI подтверждает силу движения — вход в сторону
отклонения. SL/TP считаются от ATR, как в остальных правилах: сравнение стратегий идёт на
одинаковом выходе, чтобы различие было только во входе.

Стратегия не проверена вне выборки: до подтверждения walk-forward она не подключается к
боевому Analyzer (production использует `strategy.mean_reversion`).
"""
from __future__ import annotations

from dataclasses import dataclass

from .indicators import Indicators
from .strategy import Signal, SignalType


@dataclass(frozen=True)
class MomentumConfig:
    """Пороги входа momentum.

    `rsi_buy` — минимум RSI для BUY (подтверждение силы), `rsi_sell` — максимум для SELL.
    `adx_min=0` отключает фильтр тренда (ADX в этих данных на часах чаще ниже порогов).
    """

    z_entry: float = 1.5
    rsi_buy: float = 65.0
    rsi_sell: float = 35.0
    adx_min: float = 0.0
    min_atr_pct: float = 0.0

    def __post_init__(self) -> None:
        if self.z_entry <= 0:
            raise ValueError("z_entry must be positive")
        if not 50 < self.rsi_buy < 100:
            raise ValueError("rsi_buy must be in (50, 100)")
        if not 0 < self.rsi_sell < 50:
            raise ValueError("rsi_sell must be in (0, 50)")
        if self.adx_min < 0:
            raise ValueError("adx_min must not be negative")
        if self.min_atr_pct < 0:
            raise ValueError("min_atr_pct must not be negative")

    @property
    def label(self) -> str:
        parts = [f"z{self.z_entry:g}"]
        if self.adx_min:
            parts.append(f"adx{self.adx_min:g}")
        if self.min_atr_pct:
            parts.append(f"atr{self.min_atr_pct:g}")
        return "mom-" + "-".join(parts)


def momentum(
    ind: Indicators,
    price: float,
    atr_mult_sl: float = 1.5,
    atr_mult_tp: float = 2.0,
    config: MomentumConfig = MomentumConfig(),
) -> Signal:
    """Сигнал по продолжению движения. `reason` называет сработавший фильтр."""
    if ind.zscore is None or ind.rsi is None or ind.atr is None or ind.atr <= 0:
        return Signal(SignalType.HOLD, 0.0, price, None, None, "insufficient data")
    if ind.atr_pct is None or ind.atr_pct < config.min_atr_pct:
        return Signal(SignalType.HOLD, 0.0, price, None, None, "volatility below threshold")
    if ind.adx is not None and ind.adx < config.adx_min:
        return Signal(SignalType.HOLD, 0.0, price, None, None, "no trend")

    if ind.zscore >= config.z_entry and ind.rsi > config.rsi_buy:
        return Signal(
            SignalType.BUY,
            _confidence(ind.zscore - config.z_entry, config.z_entry),
            price,
            _sl_tp(price, ind.atr, atr_mult_sl, sign=-1),
            _sl_tp(price, ind.atr, atr_mult_tp, sign=+1),
            f"momentum: z {ind.zscore:.2f} >= {config.z_entry:g}, rsi {ind.rsi:.1f}",
        )

    if ind.zscore <= -config.z_entry and ind.rsi < config.rsi_sell:
        return Signal(
            SignalType.SELL,
            _confidence(-ind.zscore - config.z_entry, config.z_entry),
            price,
            _sl_tp(price, ind.atr, atr_mult_sl, sign=+1),
            _sl_tp(price, ind.atr, atr_mult_tp, sign=-1),
            f"momentum: z {ind.zscore:.2f} <= -{config.z_entry:g}, rsi {ind.rsi:.1f}",
        )

    return Signal(SignalType.HOLD, 0.0, price, None, None, "no signal")


def _sl_tp(price: float, atr: float, mult: float, sign: int) -> float:
    return round(price + sign * mult * atr, 9)


def _confidence(excess: float, scale: float) -> float:
    if scale <= 0:
        return 0.5
    return min(1.0, 0.5 + 0.5 * (max(excess, 0.0) / scale))


__all__ = ["MomentumConfig", "momentum"]
