"""Тесты OU-модели (шаг 1.2 `to_do.md` §4.1).

Проверяется главное обещание шага: на синтетическом процессе с известными θ/μ/σ оценки
восстанавливаются, а сигналы идут против отклонения от оценённого среднего.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, List, Sequence

import numpy as np
import pytest

from analyzer.backtest.commission import CommissionModel
from analyzer.backtest.engine import run_backtest
from analyzer.backtest.models import Bar, Instrument, RunConfig
from analyzer.indicators import Indicators
from analyzer.signal_models import (
    OuConfig,
    OuMeanReversionModel,
    SignalModel,
    build_model,
    estimate_ou,
    model_names,
)
from analyzer.signal_models.ou import ou_config
from analyzer.strategy import Signal, SignalType

START = datetime(2026, 11, 2, 7, 0, tzinfo=timezone.utc)
THETA = 0.2
MU = 100.0
SIGMA = 1.0


def _simulate_ou(
    size: int = 20_000, theta: float = THETA, mu: float = MU, sigma: float = SIGMA, seed: int = 7
) -> np.ndarray:
    """Точная дискретизация OU на шаге Δt = 1: X_{t+1} = μ + b(X_t − μ) + ε."""
    rng = np.random.default_rng(seed)
    b = math.exp(-theta)
    shock_sd = sigma * math.sqrt((1.0 - b * b) / (2.0 * theta))
    shocks = rng.normal(0.0, shock_sd, size)
    values = np.empty(size)
    values[0] = mu
    for i in range(1, size):
        values[i] = mu + b * (values[i - 1] - mu) + shocks[i]
    return values


def _fitted_model(closes: Sequence[float], **config: Any) -> OuMeanReversionModel:
    model = OuMeanReversionModel(config=OuConfig(**config))
    model.fit([_bar(close) for close in closes])
    return model


def _bar(close: float, minute: int = 0, ts: datetime = START) -> Bar:
    return Bar(
        ts=ts + timedelta(minutes=minute),
        open=close,
        high=close + 0.1,
        low=close - 0.1,
        close=close,
    )


def test_estimate_recovers_known_parameters() -> None:
    params = estimate_ou(_simulate_ou())

    assert params is not None
    assert params.mu == pytest.approx(MU, rel=0.01)
    assert params.theta == pytest.approx(THETA, rel=0.10)
    assert params.sigma == pytest.approx(SIGMA, rel=0.10)
    assert params.half_life_bars == pytest.approx(math.log(2.0) / THETA, rel=0.10)
    # σ_eq = σ/√(2θ) — проверяется через восстановленные значения, не через истинные.
    assert params.sigma_eq == pytest.approx(
        params.sigma / math.sqrt(2.0 * params.theta), rel=1e-9
    )


def test_estimate_refuses_trending_series() -> None:
    # Идеальный тренд даёт наклон AR(1) ровно 1: процесс не возвращается к среднему.
    assert estimate_ou(np.arange(1.0, 200.0)) is None


def test_estimate_refuses_short_or_flat_series() -> None:
    assert estimate_ou([100.0, 100.1]) is None
    assert estimate_ou([100.0] * 50) is None
    assert estimate_ou([100.0, float("nan"), 100.2]) is None


def test_predict_buy_below_equilibrium() -> None:
    model = _fitted_model(_simulate_ou())
    params = model.params()
    price = float(params["mu"]) - 3.0 * float(params["sigma_eq"])
    ind = Indicators(atr=1.0)

    signal = model.predict(ind, price)

    assert signal.type == SignalType.BUY
    assert signal.suggested_sl == pytest.approx(price - 1.5, rel=1e-9)
    assert signal.suggested_tp == pytest.approx(price + 2.0, rel=1e-9)


def test_predict_sell_above_equilibrium() -> None:
    model = _fitted_model(_simulate_ou())
    params = model.params()
    price = float(params["mu"]) + 3.0 * float(params["sigma_eq"])

    signal = model.predict(Indicators(atr=1.0), price)

    assert signal.type == SignalType.SELL
    assert signal.suggested_sl == pytest.approx(price + 1.5, rel=1e-9)
    assert signal.suggested_tp == pytest.approx(price - 2.0, rel=1e-9)


def test_predict_holds_near_mean() -> None:
    model = _fitted_model(_simulate_ou())
    params = model.params()

    signal = model.predict(Indicators(atr=1.0), float(params["mu"]))

    assert signal.type == SignalType.HOLD
    assert signal.reason == "ou: no signal"


def test_unfitted_model_holds() -> None:
    model = OuMeanReversionModel()

    signal = model.predict(Indicators(atr=1.0), 100.0)

    assert signal.type == SignalType.HOLD
    assert "not fitted" in signal.reason


def test_slow_mean_reversion_is_not_traded() -> None:
    # half-life θ=0.2 — 3.5 бара; порог 1.0 делает возврат «слишком медленным».
    model = _fitted_model(_simulate_ou(), max_half_life_bars=1.0)
    params = model.params()
    price = float(params["mu"]) + 5.0 * float(params["sigma_eq"])

    signal = model.predict(Indicators(atr=1.0), price)

    assert signal.type == SignalType.HOLD
    assert "slow mean reversion" in signal.reason


def test_missing_atr_blocks_entry() -> None:
    model = _fitted_model(_simulate_ou())
    params = model.params()
    price = float(params["mu"]) + 5.0 * float(params["sigma_eq"])

    signal = model.predict(Indicators(), price)

    assert signal.type == SignalType.HOLD
    assert signal.reason == "insufficient data"


def test_ou_is_registered_and_uses_registry_defaults() -> None:
    assert "ou" in model_names()
    model = build_model("ou")
    assert isinstance(model, OuMeanReversionModel)
    assert isinstance(model, SignalModel)
    assert model.warmup == OuConfig().window


def test_ou_config_rejects_unknown_and_fractional_params() -> None:
    with pytest.raises(ValueError, match="неизвестные параметры"):
        ou_config(SimpleNamespace(z_entry=2.0, model_params={"foo": 1}))
    with pytest.raises(ValueError, match="window must be an integer"):
        ou_config(SimpleNamespace(z_entry=2.0, model_params={"window": 240.5}))
    with pytest.raises(ValueError, match="min_bars must not exceed window"):
        OuConfig(window=30, min_bars=40)


def test_engine_fits_model_before_predict() -> None:
    class _Recorder:
        name = "recorder"
        version = "0"
        warmup = 5

        def __init__(self) -> None:
            self.windows: List[int] = []
            self.predictions = 0

        def params(self) -> dict:
            return {}

        def fit(self, history: Sequence[Any]) -> None:
            self.windows.append(len(history))

        def predict(
            self, ind: Indicators, price: float, atr_mult_sl: float, atr_mult_tp: float
        ) -> Signal:
            assert self.windows, "predict вызван до fit: модель осталась бы необученной"
            self.predictions += 1
            return Signal(SignalType.HOLD, 0.0, price, None, None, "hold")

        def __call__(
            self, ind: Indicators, price: float, atr_mult_sl: float, atr_mult_tp: float
        ) -> Signal:
            return self.predict(ind, price, atr_mult_sl, atr_mult_tp)

    recorder = _Recorder()
    bars = [_bar(100.0 + 0.01 * i, minute=i) for i in range(30)]

    run_backtest(
        bars=bars,
        instrument=Instrument(ticker="SBER"),
        config=RunConfig(window=5, min_candles=5),
        commission=CommissionModel(tariff="trader"),
        strategy_fn=recorder,
    )

    assert recorder.windows, "fit не вызывался"
    assert set(recorder.windows) <= {5}
    assert recorder.predictions == len(recorder.windows)
