"""Golden-тесты протокола `SignalModel` — шаг 1.1 `to_do.md` §4.1.

Смысл: адаптер обязан повторять исходное правило без расхождений, иначе единый протокол
меняет результат прогона. Сравнение идёт с прямой функцией в одном процессе, поэтому
расхождение версий ta-lib не маскируется сохранёнными числами.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Callable

import numpy as np
import pytest

from analyzer.backtest.models import Bar
from analyzer.indicators import Indicators, compute_all
from analyzer.signal_models import (
    MODEL_SPECS,
    BarLike,
    MeanReversionModel,
    MeanReversionV2Model,
    MomentumModel,
    SignalModel,
    UnknownModelError,
    build_model,
    model_names,
)
from analyzer.strategy import Signal, SignalType, mean_reversion
from analyzer.strategy_momentum import MomentumConfig, momentum
from analyzer.strategy_v2 import StrategyV2Config, mean_reversion_v2

Rule = Callable[[Indicators, float, float, float], Signal]

MODELS: tuple[tuple[str, Rule], ...] = (
    ("mean_reversion", mean_reversion),
    ("momentum", momentum),
    ("v2", mean_reversion_v2),
)


def _fixed_series(size: int = 260) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Детерминированный ряд: тренд + волна + ступеньки + фиксированный шум."""
    rng = np.random.default_rng(20261121)
    steps = np.arange(size, dtype=float)
    closes = (
        100.0
        + 0.02 * steps
        + 3.0 * np.sin(steps / 7.0)
        + np.where((steps // 40) % 2 == 0, 0.0, 1.5)
        + rng.normal(0.0, 0.12, size)
    )
    return (
        closes,
        closes + 0.25,
        closes - 0.25,
        np.full(size, 1000.0),
    )


@pytest.mark.parametrize("name,rule", MODELS)
def test_predict_matches_rule_on_fixed_series(name: str, rule: Rule) -> None:
    closes, highs, lows, volumes = _fixed_series()
    model = build_model(name)

    for end in range(60, len(closes)):
        ind = compute_all(closes[: end + 1], highs[: end + 1], lows[: end + 1], volumes[: end + 1])
        price = float(closes[end])
        assert model.predict(ind, price, 1.5, 2.0) == rule(ind, price, 1.5, 2.0)


@pytest.mark.parametrize(
    "name,rule,ind,price",
    [
        (
            "mean_reversion",
            mean_reversion,
            Indicators(rsi=20.0, bb_lower=100.0, bb_upper=110.0, bb_middle=105.0, atr=1.0),
            99.0,
        ),
        (
            "momentum",
            momentum,
            Indicators(rsi=70.0, zscore=1.6, atr=1.0, atr_pct=0.5),
            100.0,
        ),
        (
            "v2",
            mean_reversion_v2,
            Indicators(rsi=20.0, zscore=-2.1, atr=1.0, atr_pct=0.5, adx=15.0),
            100.0,
        ),
    ],
)
def test_actionable_signal_parity(name: str, rule: Rule, ind: Indicators, price: float) -> None:
    assert rule(ind, price, 1.5, 2.0).type == SignalType.BUY
    assert build_model(name).predict(ind, price, 1.5, 2.0) == rule(ind, price, 1.5, 2.0)


def test_models_satisfy_protocol_and_are_callable() -> None:
    ind = Indicators(rsi=20.0, bb_lower=100.0, bb_upper=110.0, atr=1.0)
    for name, _ in MODELS:
        model = build_model(name)
        assert isinstance(model, SignalModel)
        # `backtest.engine` вызывает модель как функцию — контракт должен это выдерживать.
        assert model(ind, 99.0, 1.5, 2.0) == model.predict(ind, 99.0, 1.5, 2.0)


def test_bar_satisfies_history_protocol() -> None:
    bar = Bar(ts=datetime(2026, 11, 21, tzinfo=timezone.utc), open=1.0, high=1.1, low=0.9, close=1.0)
    assert isinstance(bar, BarLike)


@pytest.mark.parametrize("name", [name for name, _ in MODELS])
def test_params_are_immutable_and_json_serializable(name: str) -> None:
    params = build_model(name).params()

    json.dumps(dict(params))
    with pytest.raises(TypeError):
        params["injected"] = 1  # type: ignore[index]


@pytest.mark.parametrize("name", [name for name, _ in MODELS])
def test_noop_fit_does_not_change_prediction(name: str) -> None:
    model = build_model(name)
    ind = Indicators(rsi=20.0, bb_lower=100.0, bb_upper=110.0, atr=1.0)
    before = model.predict(ind, 99.0, 1.5, 2.0)

    model.fit([])

    assert model.predict(ind, 99.0, 1.5, 2.0) == before


def test_registry_exposes_builtin_models() -> None:
    assert model_names() == ("mean_reversion", "momentum", "v2", "ou")
    assert tuple(MODEL_SPECS) == model_names()
    assert isinstance(build_model("mean_reversion"), MeanReversionModel)
    assert isinstance(build_model("momentum"), MomentumModel)
    assert isinstance(build_model("v2"), MeanReversionV2Model)


def test_registry_applies_config_override() -> None:
    model = build_model("momentum", MomentumConfig(z_entry=2.5))
    v2_model = build_model("v2", StrategyV2Config(z_entry=3.0))

    assert model.params()["z_entry"] == 2.5
    assert v2_model.params()["z_entry"] == 3.0


def test_unknown_model_fails_instead_of_fallback() -> None:
    with pytest.raises(UnknownModelError) as excinfo:
        build_model("no_such_model")

    message = str(excinfo.value)
    assert "no_such_model" in message
    assert "mean_reversion" in message and "momentum" in message and "v2" in message
    assert isinstance(excinfo.value, KeyError)
