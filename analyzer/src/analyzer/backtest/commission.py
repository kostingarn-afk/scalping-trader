"""Модель издержек по тарифу T-Invest.

Ставки — за одну сторону сделки, в долях суммы сделки; круговая комиссия = две стороны.
Биржевая комиссия включена в брокерскую. Тариф по умолчанию — `investor` (реальный счёт);
`trader` доступен для сценария «окупаемость абонплаты» (см. `to_do.md` §2).

Тарифы намеренно не перенесены в production `CommissionFilter`: ослабление боевого фильтра
включается только после подтверждённого edge (шаг 2 плана в `to_do.md`).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

SECURITY = "security"
FUTURE = "future"

# Ставка за сторону. Круговая комиссия в тарифах брокера: investor 0.60 % бумаги / 0.20 %
# фьючерсы, trader 0.10 % / 0.08 %, premium 0.08 % / 0.05 %.
RATES_PER_SIDE: Dict[str, Dict[str, float]] = {
    "investor": {SECURITY: 0.003, FUTURE: 0.001},
    "trader": {SECURITY: 0.0005, FUTURE: 0.0004},
    "premium": {SECURITY: 0.0004, FUTURE: 0.00025},
}

TARIFFS: Tuple[str, ...] = tuple(RATES_PER_SIDE)


@dataclass(frozen=True)
class CommissionModel:
    """Комиссия брокера по тарифу.

    `min_commission_rub` — минимальная комиссия за сделку (0, если брокер её не взимает);
    применяется к каждой стороне отдельно.
    """

    tariff: str = "investor"
    min_commission_rub: float = 0.0
    # Биржевой сбор за контракт, ₽ за сторону. На срочном рынке он фиксирован за контракт,
    # а не пропорционален номиналу, поэтому процентной моделью не описывается. 0 — сбор
    # не задан: фьючерсный прогон тогда считает только брокерский процент и недостоверен
    # (см. `to_do.md` §4.3). Ставку не выдумываем: она берётся из тарифного листа биржи.
    exchange_fee_per_contract_rub: float = 0.0

    def __post_init__(self) -> None:
        if self.tariff not in RATES_PER_SIDE:
            raise ValueError(
                f"unknown tariff {self.tariff!r}; expected one of {', '.join(TARIFFS)}"
            )
        if self.min_commission_rub < 0:
            raise ValueError("min_commission_rub must be non-negative")
        if self.exchange_fee_per_contract_rub < 0:
            raise ValueError("exchange_fee_per_contract_rub must be non-negative")

    @property
    def round_trip_rate(self) -> float:
        """Круговая ставка для бумаг — справочное значение для отчётов."""
        return 2 * self.per_side_rate(is_future=False)

    def per_side_rate(self, is_future: bool) -> float:
        kind = FUTURE if is_future else SECURITY
        return RATES_PER_SIDE[self.tariff][kind]

    def side_commission(
        self, notional: float, is_future: bool, contracts: int = 0
    ) -> float:
        """Комиссия одной стороны сделки (вход или выход).

        `contracts` — число контрактов; для фьючерсов добавляет фиксированный биржевой сбор
        за контракт. Ноль означает «сбор не передан»: остаётся только процент брокера.
        """
        if notional < 0:
            raise ValueError("notional must be non-negative")
        if contracts < 0:
            raise ValueError("contracts must not be negative")
        cost = max(self.min_commission_rub, self.per_side_rate(is_future) * notional)
        if is_future and contracts:
            cost += self.exchange_fee_per_contract_rub * contracts
        return cost

    def round_trip(
        self,
        entry_notional: float,
        exit_notional: float,
        is_future: bool,
        contracts: int = 0,
    ) -> float:
        """Комиссия входа и выхода вместе."""
        return self.side_commission(
            entry_notional, is_future, contracts
        ) + self.side_commission(exit_notional, is_future, contracts)
