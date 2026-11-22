"""Издержки исполнения в бэктесте — шаг 1.6 `to_do.md` §4.1.

Модель Almgren–Chriss в дискретном виде: поправка цены = спред + импакт, пропорциональный доле
объёма бара, которую составляет заявка. Коэффициенты задаются в б.п. цены, чтобы сравнение
инструментов с разной ценой было осмысленным.

Нулевые параметры дают тождество (`price * (1 + 0) == price`): модель вводится, не меняя
прежних результатов прогонов.
"""
from __future__ import annotations

from dataclasses import dataclass

BASIS_POINTS = 10_000.0


@dataclass(frozen=True)
class ExecutionModel:
    """Спред и импакт в базисных пунктах цены.

    `spread_bps` — половина спреда (покупка дороже, продажа дешевле).
    `impact_bps_at_full` — временный импакт при 100 % участии в объёме бара.
    `permanent_bps_at_full` — постоянный импакт при 100 % участии; в средней цене входа
    учитывается с множителем 0.5 (AC).
    """

    spread_bps: float = 0.0
    impact_bps_at_full: float = 0.0
    permanent_bps_at_full: float = 0.0

    def __post_init__(self) -> None:
        if self.spread_bps < 0:
            raise ValueError("spread_bps must be non-negative")
        if self.impact_bps_at_full < 0:
            raise ValueError("impact_bps_at_full must be non-negative")
        if self.permanent_bps_at_full < 0:
            raise ValueError("permanent_bps_at_full must be non-negative")

    @property
    def enabled(self) -> bool:
        return bool(self.spread_bps or self.impact_bps_at_full or self.permanent_bps_at_full)

    def participation(self, units: float, bar_volume_lots: float, lot_size: float) -> float:
        """Доля объёма бара, которую составляет заявка; 100 % — потолок."""
        bar_units = float(bar_volume_lots) * float(lot_size)
        if bar_units <= 0 or units <= 0:
            # Нулевой объём: либо данных нет, либо заявка пуста. Второй случай безразличен,
            # первый трактуем консервативно — как полное участие.
            return 1.0 if units > 0 else 0.0
        return min(float(units) / bar_units, 1.0)

    def cost_bps(self, units: float, bar_volume_lots: float, lot_size: float) -> float:
        share = self.participation(units, bar_volume_lots, lot_size)
        return self.spread_bps + share * (
            self.impact_bps_at_full + 0.5 * self.permanent_bps_at_full
        )

    def fill_price(
        self,
        price: float,
        side: float,
        units: float,
        bar_volume_lots: float,
        lot_size: float,
    ) -> float:
        """Цена исполнения: `side=+1` — покупка (дороже), `side=-1` — продажа (дешевле)."""
        if not self.enabled:
            return price
        adjustment = side * self.cost_bps(units, bar_volume_lots, lot_size) / BASIS_POINTS
        return price * (1.0 + adjustment)


__all__ = ["ExecutionModel"]
