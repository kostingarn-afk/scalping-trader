"""Модели данных бэктеста.

Пакет переиспользует production-расчёт (`analyzer.indicators`, `analyzer.strategy`), поэтому
прогон сопоставим с живым пайплайном: расхождения возможны только по модели издержек,
цене входа и правилам выхода.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Tuple

FUTURE_CLASS_CODES = frozenset({"SPBFUT", "FORTS"})


@dataclass(frozen=True)
class Bar:
    """Завершённая свеча; `ts` — время закрытия свечи (UTC)."""

    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0


@dataclass(frozen=True)
class Instrument:
    """Инструмент прогона.

    `lot_size` — множитель «единиц в лоте × размер контракта»: для акций это число акций
    в лоте (SBER — 10), для фьючерсов — размер контракта в единицах котировки (котировка Si
    уже задана за контракт, поэтому 1).
    """

    ticker: str
    class_code: str = "TQBR"
    lot_size: int = 1

    def __post_init__(self) -> None:
        if self.lot_size < 1:
            raise ValueError("lot_size must be a positive integer")

    @property
    def is_future(self) -> bool:
        return self.class_code.upper() in FUTURE_CLASS_CODES

    @property
    def name(self) -> str:
        return f"{self.ticker}@{self.class_code}"


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class ExitReason(str, Enum):
    TAKE_PROFIT = "TP"
    STOP_LOSS = "SL"
    TIMEOUT = "TIMEOUT"
    # Принудительный выход при пробитии дневного лимита убытка: зеркало
    # `PnlCircuitBreaker` из Trader, чтобы бэктест и боевой контур мерили одно и то же.
    DAILY_STOP = "DAILY_STOP"


@dataclass(frozen=True)
class Trade:
    direction: Direction
    entry_ts: datetime
    exit_ts: datetime
    entry_price: float
    exit_price: float
    sl: float
    # None — цель не задавалась (выход по SL/трейлингу/таймауту). Ноль здесь был бы
    # неотличим от вырожденной цели и врал бы в CSV.
    tp: Optional[float]
    lots: int
    lot_size: int
    exit_reason: ExitReason
    bars_held: int
    notional: float
    gross_pnl: float
    commission: float
    net_pnl: float

    @property
    def is_win(self) -> bool:
        return self.net_pnl > 0


@dataclass(frozen=True)
class RunConfig:
    """Параметры прогона.

    Депозит и риск на сделку согласованы с `to_do.md` §1; SL 1.5×ATR, TP 2×ATR,
    таймаут 10 баров и лимит номинала 100 % — дефолты бэктеста.
    """

    deposit: float = 200_000.0
    risk_per_trade_pct: float = 1.0
    max_position_notional_pct: float = 100.0
    atr_mult_sl: float = 1.5
    atr_mult_tp: float = 2.0
    # Выход только по SL/трейлингу/таймауту: цель не выставляется. Нужен, чтобы отделить
    # эффект сигнала от эффекта срезанного правого хвоста (см. `to_do.md` §2).
    no_tp: bool = False
    max_bars_in_trade: int = 10
    window: int = 200
    min_candles: int = 25
    commission_profit_ratio: float = 3.0
    # Сколько баров после выхода запрещены новые входы: ограничитель оборота
    # (комиссия линейна по числу сделок, преимущество — нет).
    cooldown_bars: int = 0
    # Трейлинг-стоп в ATR на момент входа: 0 — выключен. Стоп только подтягивается
    # (по закрытию предыдущего бара), поэтому не может расшириться.
    trailing_atr: float = 0.0
    # Вход по открытию следующего бара: сигнал рассчитан по закрытой свече, исполнение —
    # по первой доступной после неё цене. False — воспроизводит замер `to_be.md` §3.
    fill_on_next_bar_open: bool = True
    # Дневной лимит убытка в процентах депозита (0 — выключено). Семантика как у
    # `PnlCircuitBreaker` в Trader: считается **реализованный** PnL за торговый день
    # (UTC-дата закрытия сделки; сессия MOEX целиком лежит внутри одной UTC-даты),
    # при пробитии позиция ликвидируется и новые входы запрещены до следующего дня.
    daily_loss_stop_pct: float = 0.0
    # Запрет входов до указанного времени по Москве (минуты от полуночи; 0 — выключено).
    # 660 = 11:00 МСК: первый час основной сессии по замеру 2026-11-21 даёт −3.8 б.п. дрейфа
    # (t −2.53), и входить в него невыгодно. Зеркало `entry_after_msk_minutes` в Trader.
    entry_after_msk_minutes: int = 0

    def __post_init__(self) -> None:
        if self.deposit <= 0:
            raise ValueError("deposit must be positive")
        if self.risk_per_trade_pct <= 0:
            raise ValueError("risk_per_trade_pct must be positive")
        if not 0 < self.max_position_notional_pct <= 1000:
            raise ValueError("max_position_notional_pct must be in (0, 1000]")
        if self.atr_mult_sl <= 0 or self.atr_mult_tp <= 0:
            raise ValueError("ATR multipliers must be positive")
        if self.max_bars_in_trade < 1:
            raise ValueError("max_bars_in_trade must be at least 1")
        if self.min_candles < 1:
            raise ValueError("min_candles must be at least 1")
        if self.commission_profit_ratio <= 0:
            # ratio=0 отключает фильтр целиком — такая конфигурация должна задаваться явно,
            # а не проезжать молча через валидацию.
            raise ValueError("commission_profit_ratio must be positive")
        if self.cooldown_bars < 0:
            raise ValueError("cooldown_bars must not be negative")
        if self.trailing_atr < 0:
            raise ValueError("trailing_atr must not be negative")
        if not 0 <= self.daily_loss_stop_pct <= 100:
            raise ValueError("daily_loss_stop_pct must be in [0, 100]")
        if not 0 <= self.entry_after_msk_minutes <= 1440:
            raise ValueError("entry_after_msk_minutes must be in [0, 1440]")
        if self.window < self.min_candles:
            raise ValueError("window must not be less than min_candles")


@dataclass(frozen=True)
class BacktestResult:
    instrument: Instrument
    interval_minutes: int
    tariff: str
    config: RunConfig
    bars: int
    signals: int
    filtered_signals: int
    skipped_by_notional: int
    trades: Tuple[Trade, ...]
    equity_final: float
    max_drawdown_pct: float
    # Позиция, открытая к концу выгрузки: в сделках и PnL не учитывается, но должна быть видна
    # в отчёте — иначе сигнал «теряется» без следа.
    open_at_end: bool = False
    # Метка варианта прогона (например `tp3/sl2` или `v2-z2-adx25` при переборе): отличает
    # строки отчёта, у которых `instrument`/`interval_minutes`/`tariff` совпадают.
    label: str = ""
    # Правило входа: `mean_reversion` (production) или `v2`; печатается в шапке отчёта.
    strategy: str = ""

    @property
    def starting_deposit(self) -> float:
        return self.config.deposit

    @property
    def run_name(self) -> str:
        base = f"{self.instrument.name} {self.interval_minutes}min {self.tariff}"
        return f"{base} {self.label}" if self.label else base
