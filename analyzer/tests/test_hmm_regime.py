"""Тесты HMM-фильтра режима (шаг 1.4 `to_do.md` §4.1).

Проверяется обещание шага: смена волатильности обнаруживается до входа, и в волатильном
режиме mean reversion не торгуется.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("hmmlearn", reason="шаг 1.4 добавляет hmmlearn в зависимости Analyzer")

from analyzer.backtest.models import Bar  # noqa: E402
from analyzer.indicators import Indicators  # noqa: E402
from analyzer.signal_models import (  # noqa: E402
    HmmConfig,
    HmmRegimeModel,
    build_model,
    model_names,
)
from analyzer.signal_models.regime import hmm_config  # noqa: E402
from analyzer.strategy import SignalType  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore::sklearn.exceptions.ConvergenceWarning")

START = datetime(2026, 11, 2, 7, 0, tzinfo=timezone.utc)
CALM_VOL = 0.0005
VOLATILE_VOL = 0.01
SEGMENT = 300


def _prices() -> np.ndarray:
    """300 спокойных баров → 300 волатильных → 300 спокойных."""
    rng = np.random.default_rng(11)
    returns = np.concatenate(
        [
            rng.normal(0.0, CALM_VOL, SEGMENT),
            rng.normal(0.0, VOLATILE_VOL, SEGMENT),
            rng.normal(0.0, CALM_VOL, SEGMENT),
        ]
    )
    return 100.0 * np.exp(np.cumsum(returns))


def _bars(prices: np.ndarray) -> list[Bar]:
    return [
        Bar(
            ts=START + timedelta(minutes=index),
            open=float(price),
            high=float(price) + 0.01,
            low=float(price) - 0.01,
            close=float(price),
        )
        for index, price in enumerate(prices)
    ]


def _model(**overrides) -> HmmRegimeModel:
    defaults = {"window": 2 * SEGMENT, "min_bars": 60, "refit_bars": 1}
    defaults.update(overrides)
    return HmmRegimeModel(config=HmmConfig(**defaults))


def test_detects_volatile_regime_at_end_of_window() -> None:
    bars = _bars(_prices())
    model = _model()
    model.fit(bars[: 2 * SEGMENT])  # окно: спокойный + волатильный сегменты

    params = model.params()

    assert params["fitted"] is True
    assert params["current_state"] != params["calm_state"]


def test_detects_calm_regime_after_switch_back() -> None:
    bars = _bars(_prices())
    model = _model()
    model.fit(bars)  # окно: спокойный + волатильный + спокойный

    params = model.params()

    assert params["current_state"] == params["calm_state"]


def test_calm_regime_allows_mean_reversion_entry() -> None:
    model = _model()
    model.fit(_bars(_prices()))
    ind = Indicators(zscore=-2.5, rsi=20.0, atr=1.0)

    signal = model.predict(ind, 100.0)

    assert signal.type == SignalType.BUY
    assert signal.suggested_sl == pytest.approx(98.5)
    assert signal.suggested_tp == pytest.approx(102.0)


def test_volatile_regime_blocks_entry() -> None:
    model = _model()
    model.fit(_bars(_prices())[: 2 * SEGMENT])
    ind = Indicators(zscore=-2.5, rsi=20.0, atr=1.0)

    signal = model.predict(ind, 100.0)

    assert signal.type == SignalType.HOLD
    assert "volatile regime" in signal.reason


def test_unfitted_model_holds() -> None:
    signal = HmmRegimeModel().predict(Indicators(zscore=-3, rsi=10, atr=1.0), 100.0)

    assert signal.type == SignalType.HOLD
    assert "not fitted" in signal.reason


def test_missing_indicators_block_entry() -> None:
    model = _model()
    model.fit(_bars(_prices()))

    signal = model.predict(Indicators(atr=1.0), 100.0)

    assert signal.type == SignalType.HOLD
    assert signal.reason == "insufficient data"


def test_run_is_deterministic() -> None:
    bars = _bars(_prices())
    first, second = _model(), _model()
    first.fit(bars)
    second.fit(bars)
    ind = Indicators(zscore=-2.5, rsi=20.0, atr=1.0)

    assert first.params()["current_state"] == second.params()["current_state"]
    assert first.predict(ind, 100.0) == second.predict(ind, 100.0)


def test_model_is_registered() -> None:
    assert "hmm" in model_names()
    assert isinstance(build_model("hmm"), HmmRegimeModel)


def test_hmm_config_validation() -> None:
    with pytest.raises(ValueError, match="states"):
        HmmConfig(states=1)
    with pytest.raises(ValueError, match="min_bars must not exceed window"):
        HmmConfig(window=50, min_bars=60)
    with pytest.raises(ValueError, match="неизвестные параметры"):
        hmm_config(SimpleNamespace(z_entry=2.0, rsi_buy=None, rsi_sell=None, model_params={"foo": 1}))
    with pytest.raises(ValueError, match="window must be an integer"):
        hmm_config(
            SimpleNamespace(z_entry=2.0, rsi_buy=None, rsi_sell=None, model_params={"window": 30.5})
        )
