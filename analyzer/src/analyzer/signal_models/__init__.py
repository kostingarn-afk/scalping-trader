"""Модели сигналов Analyzer: протокол, адаптеры действующих правил и реестр.

Подключение новой модели (шаги 1.2–1.5 `to_do.md`): реализовать протокол `SignalModel`
и добавить запись в `MODEL_SPECS` — точка вызова не меняется.
"""
from __future__ import annotations

from .base import BarLike, ModelSpec, SignalModel
from .builtin import MeanReversionModel, MeanReversionV2Model, MomentumModel
from .ou import OuConfig, OuMeanReversionModel, OuParams, estimate_ou
from .registry import MODEL_SPECS, UnknownModelError, build_model, model_names

__all__ = [
    "BarLike",
    "MeanReversionModel",
    "MeanReversionV2Model",
    "ModelSpec",
    "MODEL_SPECS",
    "MomentumModel",
    "OuConfig",
    "OuMeanReversionModel",
    "OuParams",
    "SignalModel",
    "UnknownModelError",
    "build_model",
    "estimate_ou",
    "model_names",
]
