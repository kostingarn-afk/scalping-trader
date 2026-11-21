"""Стратегия mean reversion v2: глубина отклонения + фильтр режима + фильтр волатильности.

Зачем отдельный модуль, а не правка `strategy.py`: боевой Analyzer продолжает работать
на специфицированных порогах (RSI 30/70 + цена за BB), пока v2 не подтвердит преимущество
в бэктесте. Переключение live-контура — отдельный шаг после гейта (`to_do.md` §1).

Отличия от production-правила:

- **Порог по z-score.** Вход требует отклонения на `z_entry` σ от средней BB, а не просто
  выхода цены за полосу: мелкие откаты (|z| < 2) не торгуются — они не окупают издержки.
- **Фильтр режима.** `ADX > adx_max` — тренд; возврата к средней в нём нет, вход запрещён.
  Замер: 47 % сделок старого правила закрывались по SL, покупая падения внутри тренда.
- **Фильтр волатильности.** `atr_pct < min_atr_pct` — движение бара меньше издержек круга;
  вход запрещён. Порог задаётся в процентах цены.
- **Более строгий RSI** (`25/75` против `30/70`) — подтверждение разворота, а не любого отката.

SL/TP по-прежнему от ATR: выход не меняется, меняются только условия входа.
"""
from __future__ import annotations

from dataclasses import dataclass

from .indicators import Indicators
from .strategy import Signal, SignalType


@dataclass(frozen=True)
class StrategyV2Config:
    """Пороги входа v2.

    `min_atr_pct=0` отключает фильтр волатильности; `adx_max` — верхняя граница ADX для
    боковика (значения > 30 — отключение режимного фильтра на практике).
    """

    z_entry: float = 2.0
    rsi_buy: float = 25.0
    rsi_sell: float = 75.0
    adx_max: float = 25.0
    min_atr_pct: float = 0.0

    def __post_init__(self) -> None:
        if self.z_entry <= 0:
            raise ValueError("z_entry must be positive")
        if not 0 < self.rsi_buy < 50:
            raise ValueError("rsi_buy must be in (0, 50)")
        if not 50 < self.rsi_sell < 100:
            raise ValueError("rsi_sell must be in (50, 100)")
        if self.adx_max <= 0:
            raise ValueError("adx_max must be positive")
        if self.min_atr_pct < 0:
            raise ValueError("min_atr_pct must not be negative")

    @property
    def label(self) -> str:
        """Короткая метка варианта для отчёта: `v2-z2-adx25`, `v2-z2.5-adx20-atr0.15`."""
        parts = [f"z{self.z_entry:g}", f"adx{self.adx_max:g}"]
        if self.min_atr_pct:
            parts.append(f"atr{self.min_atr_pct:g}")
        return "v2-" + "-".join(parts)


def mean_reversion_v2(
    ind: Indicators,
    price: float,
    atr_mult_sl: float = 1.5,
    atr_mult_tp: float = 2.0,
    config: StrategyV2Config = StrategyV2Config(),
) -> Signal:
    """Сигнал v2. `reason` называет сработавший фильтр — по нему видно, что отсекло вход."""
    if ind.zscore is None or ind.rsi is None or ind.atr is None or ind.atr <= 0:
        return Signal(SignalType.HOLD, 0.0, price, None, None, "insufficient data")
    if ind.adx is None:
        # Без ADX режим рынка неизвестен: торговать «вслепую» дороже, чем пропустить вход.
        return Signal(SignalType.HOLD, 0.0, price, None, None, "insufficient data (adx)")
    if ind.atr_pct is None or ind.atr_pct < config.min_atr_pct:
        return Signal(SignalType.HOLD, 0.0, price, None, None, "volatility below threshold")
    if ind.adx > config.adx_max:
        return Signal(SignalType.HOLD, 0.0, price, None, None, "trending market")

    if ind.zscore <= -config.z_entry and ind.rsi < config.rsi_buy:
        sl = _sl_tp(price, ind.atr, atr_mult_sl, sign=-1)
        tp = _sl_tp(price, ind.atr, atr_mult_tp, sign=+1)
        return Signal(
            SignalType.BUY,
            _confidence(-ind.zscore - config.z_entry, config.z_entry),
            price,
            sl,
            tp,
            f"z {ind.zscore:.2f} <= -{config.z_entry:g} + rsi {ind.rsi:.1f}",
        )

    if ind.zscore >= config.z_entry and ind.rsi > config.rsi_sell:
        sl = _sl_tp(price, ind.atr, atr_mult_sl, sign=+1)
        tp = _sl_tp(price, ind.atr, atr_mult_tp, sign=-1)
        return Signal(
            SignalType.SELL,
            _confidence(ind.zscore - config.z_entry, config.z_entry),
            price,
            sl,
            tp,
            f"z {ind.zscore:.2f} >= {config.z_entry:g} + rsi {ind.rsi:.1f}",
        )

    return Signal(SignalType.HOLD, 0.0, price, None, None, "no signal")


def _sl_tp(price: float, atr: float, mult: float, sign: int) -> float:
    return round(price + sign * mult * atr, 9)


def _confidence(excess: float, scale: float) -> float:
    """confidence 0.5..1.0 по превышению порога отклонения."""
    if scale <= 0:
        return 0.5
    return min(1.0, 0.5 + 0.5 * (max(excess, 0.0) / scale))


__all__ = ["StrategyV2Config", "mean_reversion_v2"]
