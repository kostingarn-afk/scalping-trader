"""Адаптеры действующих правил к протоколу `SignalModel` — шаг 1.1.

Поведение не меняется: адаптер делегирует в исходную функцию, а golden-тест сравнивает
сигналы адаптера и правила побарно на одном ряду.

`make_config` читает параметры запуска (duck-typed `argparse.Namespace`), поэтому разбор
флагов живёт рядом с моделью, а CLI-реестр не правится при добавлении модели.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, ClassVar, Mapping, Sequence

from ..indicators import Indicators
from ..strategy import Signal, mean_reversion
from ..strategy_momentum import MomentumConfig, momentum
from ..strategy_v2 import StrategyV2Config, mean_reversion_v2

_VERSION = "1.0"


@dataclass(frozen=True)
class MeanReversionModel:
    """Production-правило BB + RSI. Собственных параметров нет."""

    name: ClassVar[str] = "mean_reversion"
    version: ClassVar[str] = _VERSION

    def params(self) -> Mapping[str, Any]:
        return MappingProxyType({})

    def fit(self, history: Sequence[Any]) -> None:
        """Правило без оцениваемых параметров: обучение ничего не меняет."""
        return None

    def predict(
        self,
        ind: Indicators,
        price: float,
        atr_mult_sl: float = 1.5,
        atr_mult_tp: float = 2.0,
    ) -> Signal:
        return mean_reversion(ind, price, atr_mult_sl, atr_mult_tp)

    def __call__(
        self, ind: Indicators, price: float, atr_mult_sl: float, atr_mult_tp: float
    ) -> Signal:
        """Совместимость с `backtest.engine.StrategyFn`."""
        return self.predict(ind, price, atr_mult_sl, atr_mult_tp)


@dataclass(frozen=True)
class MomentumModel:
    """Правило продолжения движения с порогами `MomentumConfig`."""

    config: MomentumConfig = MomentumConfig()

    name: ClassVar[str] = "momentum"
    version: ClassVar[str] = _VERSION

    @property
    def label(self) -> str:
        return self.config.label

    def params(self) -> Mapping[str, Any]:
        cfg = self.config
        return MappingProxyType(
            {
                "z_entry": cfg.z_entry,
                "rsi_buy": cfg.rsi_buy,
                "rsi_sell": cfg.rsi_sell,
                "adx_min": cfg.adx_min,
                "min_atr_pct": cfg.min_atr_pct,
            }
        )

    def fit(self, history: Sequence[Any]) -> None:
        """Пороги заданы конфигом: на истории ничего не оценивается."""
        return None

    def predict(
        self,
        ind: Indicators,
        price: float,
        atr_mult_sl: float = 1.5,
        atr_mult_tp: float = 2.0,
    ) -> Signal:
        return momentum(ind, price, atr_mult_sl, atr_mult_tp, config=self.config)

    def __call__(
        self, ind: Indicators, price: float, atr_mult_sl: float, atr_mult_tp: float
    ) -> Signal:
        return self.predict(ind, price, atr_mult_sl, atr_mult_tp)


@dataclass(frozen=True)
class MeanReversionV2Model:
    """Mean reversion с фильтрами режима и волатильности (`StrategyV2Config`)."""

    config: StrategyV2Config = StrategyV2Config()

    name: ClassVar[str] = "v2"
    version: ClassVar[str] = _VERSION

    @property
    def label(self) -> str:
        return self.config.label

    def params(self) -> Mapping[str, Any]:
        cfg = self.config
        return MappingProxyType(
            {
                "z_entry": cfg.z_entry,
                "rsi_buy": cfg.rsi_buy,
                "rsi_sell": cfg.rsi_sell,
                "adx_max": cfg.adx_max,
                "min_atr_pct": cfg.min_atr_pct,
            }
        )

    def fit(self, history: Sequence[Any]) -> None:
        """Пороги заданы конфигом: на истории ничего не оценивается."""
        return None

    def predict(
        self,
        ind: Indicators,
        price: float,
        atr_mult_sl: float = 1.5,
        atr_mult_tp: float = 2.0,
    ) -> Signal:
        return mean_reversion_v2(ind, price, atr_mult_sl, atr_mult_tp, config=self.config)

    def __call__(
        self, ind: Indicators, price: float, atr_mult_sl: float, atr_mult_tp: float
    ) -> Signal:
        return self.predict(ind, price, atr_mult_sl, atr_mult_tp)


def _rsi_thresholds(options: Any, default_buy: float, default_sell: float) -> tuple[float, float]:
    buy = default_buy if options.rsi_buy is None else options.rsi_buy
    sell = default_sell if options.rsi_sell is None else options.rsi_sell
    return buy, sell


def mean_reversion_config(options: Any) -> None:
    """Production-правило не принимает параметров волатильности."""
    if options.min_atr_pct or options.atr_pct_quantile is not None:
        raise ValueError(
            "--min-atr-pct/--atr-pct-quantile применяются только с --strategy v2/momentum"
        )
    return None


def momentum_config(options: Any) -> MomentumConfig:
    if options.min_atr_pct and options.atr_pct_quantile is not None:
        raise ValueError("задайте одно из двух: --min-atr-pct или --atr-pct-quantile")
    rsi_buy, rsi_sell = _rsi_thresholds(options, 65.0, 35.0)
    return MomentumConfig(
        z_entry=options.z_entry,
        rsi_buy=rsi_buy,
        rsi_sell=rsi_sell,
        adx_min=options.adx_min,
        min_atr_pct=options.min_atr_pct,
    )


def v2_config(options: Any) -> StrategyV2Config:
    if options.min_atr_pct and options.atr_pct_quantile is not None:
        raise ValueError("задайте одно из двух: --min-atr-pct или --atr-pct-quantile")
    rsi_buy, rsi_sell = _rsi_thresholds(options, 25.0, 75.0)
    return StrategyV2Config(
        z_entry=options.z_entry,
        rsi_buy=rsi_buy,
        rsi_sell=rsi_sell,
        adx_max=options.adx_max,
        min_atr_pct=options.min_atr_pct,
    )


def mean_reversion_label(config: Any) -> str:
    return ""


def config_label(config: Any) -> str:
    return config.label if config is not None else ""
