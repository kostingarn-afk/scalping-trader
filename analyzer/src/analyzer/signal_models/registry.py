"""Реестр моделей сигнала — единственный источник доступных имён.

Точка вызова (CLI бэктеста, будущий гейт) спрашивает модель по имени здесь; добавление
модели — новая запись в `MODEL_SPECS`, без правки вызывающего кода. Неизвестное имя —
ошибка, а не подстановка другой стратегии.
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Any, Mapping, Sequence

from ..strategy_momentum import MomentumConfig
from ..strategy_v2 import StrategyV2Config
from .base import ModelSpec, SignalModel
from .builtin import (
    MeanReversionModel,
    MeanReversionV2Model,
    MomentumModel,
    config_label,
    mean_reversion_config,
    mean_reversion_label,
    momentum_config,
    v2_config,
)
from .ou import OuConfig, OuMeanReversionModel, ou_config
from .microstructure import MicroConfig, MicrostructureModel, microstructure_config
from .regime import HmmConfig, HmmRegimeModel, hmm_config
from .hawkes import HawkesConfig, HawkesModel, hawkes_config


class UnknownModelError(KeyError):
    """Запрошено имя модели, которого нет в реестре."""

    def __init__(self, name: str, available: Sequence[str]) -> None:
        self.name = name
        self.available = tuple(available)
        super().__init__(name)

    def __str__(self) -> str:
        return f"неизвестная модель {self.name!r}; доступны: {', '.join(self.available)}"


MODEL_SPECS: Mapping[str, ModelSpec] = MappingProxyType(
    {
        "mean_reversion": ModelSpec(
            name="mean_reversion",
            version=MeanReversionModel.version,
            config_cls=None,
            factory=lambda config: MeanReversionModel(),
            make_config=mean_reversion_config,
            label=mean_reversion_label,
        ),
        "momentum": ModelSpec(
            name="momentum",
            version=MomentumModel.version,
            config_cls=MomentumConfig,
            factory=lambda config: MomentumModel(
                config=MomentumConfig() if config is None else config
            ),
            make_config=momentum_config,
            label=config_label,
        ),
        "v2": ModelSpec(
            name="v2",
            version=MeanReversionV2Model.version,
            config_cls=StrategyV2Config,
            factory=lambda config: MeanReversionV2Model(
                config=StrategyV2Config() if config is None else config
            ),
            make_config=v2_config,
            label=config_label,
        ),
        "ou": ModelSpec(
            name="ou",
            version=OuMeanReversionModel.version,
            config_cls=OuConfig,
            factory=lambda config: OuMeanReversionModel(
                config=OuConfig() if config is None else config
            ),
            make_config=ou_config,
            label=config_label,
        ),
        "microstructure": ModelSpec(
            name="microstructure",
            version=MicrostructureModel.version,
            config_cls=MicroConfig,
            factory=lambda config: MicrostructureModel(
                config=MicroConfig() if config is None else config
            ),
            make_config=microstructure_config,
            label=config_label,
        ),
        "hmm": ModelSpec(
            name="hmm",
            version=HmmRegimeModel.version,
            config_cls=HmmConfig,
            factory=lambda config: HmmRegimeModel(
                config=HmmConfig() if config is None else config
            ),
            make_config=hmm_config,
            label=config_label,
        ),
        "hawkes": ModelSpec(
            name="hawkes",
            version=HawkesModel.version,
            config_cls=HawkesConfig,
            factory=lambda config: HawkesModel(
                config=HawkesConfig() if config is None else config
            ),
            make_config=hawkes_config,
            label=config_label,
        ),
    }
)


def model_names() -> tuple[str, ...]:
    """Имена моделей в порядке регистрации: `--strategy` и справка берут их отсюда."""
    return tuple(MODEL_SPECS)


def build_model(name: str, config: Any = None) -> SignalModel:
    """Собрать модель по имени. `config` — готовый конфиг модели или None."""
    try:
        spec = MODEL_SPECS[name]
    except KeyError:
        raise UnknownModelError(name, model_names()) from None
    return spec.factory(config)
