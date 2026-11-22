"""Контракт модели сигнала — `to_do.md` §4.1, шаг 1.1.

Модель делает одно: по рассчитанным индикаторам и цене возвращает сигнал. `fit` держится
в контракте ради статистических моделей (1.2–1.5): правило без параметров реализует его как
no-op, чтобы точка вызова, отчёт и гейт опрашивали все модели одинаково.

История описана структурным `BarLike`, а не `backtest.models.Bar`: бэктест импортирует
production-модули, поэтому обратный импорт дал бы цикл `signal_models <-> backtest`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence, runtime_checkable

from ..indicators import Indicators
from ..strategy import Signal


def as_int_param(prefix: str, name: str, value: Any) -> int:
    """Привести параметр запуска к int; дробное или нечисловое значение — ошибка.

    Тихий `int()` скрыл бы опечатку (`window=240.5`), поэтому проверка явная.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{prefix}: {name} must be an integer, got {value!r}")
    if float(value) != int(value):
        raise ValueError(f"{prefix}: {name} must be an integer, got {value!r}")
    return int(value)


@runtime_checkable
class BarLike(Protocol):
    """Минимальный бар для оценки параметров; `backtest.models.Bar` подходит структурно."""

    open: float
    high: float
    low: float
    close: float
    volume: float


@runtime_checkable
class SignalModel(Protocol):
    """Модель сигнала: правило входа или статистическая модель."""

    name: str
    version: str

    def params(self) -> Mapping[str, Any]:
        """Параметры модели для отчёта: примитивы, сериализуемые в JSON."""

    def fit(self, history: Sequence[BarLike]) -> None:
        """Оценить параметры на train-части. Правило без параметров — no-op."""

    def predict(
        self,
        ind: Indicators,
        price: float,
        atr_mult_sl: float = 1.5,
        atr_mult_tp: float = 2.0,
    ) -> Signal:
        """Сигнал по индикаторам и цене. Множители ATR задаёт прогон, а не модель."""


@dataclass(frozen=True)
class ModelSpec:
    """Запись реестра: сборка модели, её конфиг из параметров запуска и метка варианта."""

    name: str
    version: str
    config_cls: Optional[type]
    factory: Callable[[Any], SignalModel]
    make_config: Callable[[Any], Any]
    label: Callable[[Any], str]
