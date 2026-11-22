"""Тесты Hawkes-оценки и модели-фильтра (шаг 1.5 `to_do.md` §4.1)."""
from __future__ import annotations

import pytest

from analyzer.hawkes import estimate_hawkes, simulate_hawkes
from analyzer.indicators import Indicators
from analyzer.microstructure import BUY, MicrostructureState
from analyzer.signal_models import (
    HawkesConfig,
    HawkesModel,
    build_model,
    model_names,
)
from analyzer.strategy import SignalType

# --- оценка потока -------------------------------------------------------------


def test_poisson_flow_has_low_branching() -> None:
    times = simulate_hawkes(mu=0.2, branching=0.0, beta=1.0, duration=3000, seed=1)

    params = estimate_hawkes(times)

    assert times.size > 300
    assert params is not None
    assert params.branching < 0.15


def test_hawkes_flow_recovers_branching() -> None:
    times = simulate_hawkes(mu=0.05, branching=0.6, beta=1.0, duration=4000, seed=2)

    params = estimate_hawkes(times)

    assert params is not None
    assert params.branching == pytest.approx(0.6, abs=0.15)
    assert params.branching < 1.0


def test_estimate_refuses_short_or_flat_series() -> None:
    assert estimate_hawkes([0.0, 0.5, 1.0]) is None
    assert estimate_hawkes([1.0] * 60) is None


def test_simulation_rejects_invalid_parameters() -> None:
    with pytest.raises(ValueError):
        simulate_hawkes(mu=0.1, branching=1.0, beta=1.0, duration=10)
    with pytest.raises(ValueError):
        simulate_hawkes(mu=-0.1, branching=0.0, beta=1.0, duration=10)


# --- состояние ------------------------------------------------------------------


def test_state_reports_hawkes_after_enough_trades() -> None:
    state = MicrostructureState(hawkes_window=120, hawkes_refresh=60)
    for index in range(130):
        state.on_trade("FIGI", 100.0, 10, BUY, timestamp=index * 0.5)

    hawkes = state.snapshot("FIGI")["hawkes"]

    assert hawkes is not None
    assert 0.0 <= hawkes < 1.0


def test_state_ignores_trades_without_timestamp() -> None:
    state = MicrostructureState(hawkes_window=120, hawkes_refresh=60)
    for _ in range(130):
        state.on_trade("FIGI", 100.0, 10, BUY)

    assert state.snapshot("FIGI")["hawkes"] is None


def test_state_validates_hawkes_parameters() -> None:
    with pytest.raises(ValueError, match="hawkes_window"):
        MicrostructureState(hawkes_window=10)
    with pytest.raises(ValueError, match="hawkes_refresh"):
        MicrostructureState(hawkes_refresh=0)


# --- модель ---------------------------------------------------------------------


def _model(**overrides) -> HawkesModel:
    defaults = {"branching_threshold": 0.7, "z_entry": 2.0}
    defaults.update(overrides)
    return HawkesModel(config=HawkesConfig(**defaults))


def test_calm_flow_allows_buy_and_sell() -> None:
    buy = _model().predict(
        Indicators(hawkes=0.2, zscore=-2.5, rsi=20.0, atr=1.0), 100.0
    )
    sell = _model().predict(
        Indicators(hawkes=0.2, zscore=2.5, rsi=80.0, atr=1.0), 100.0
    )

    assert buy.type == SignalType.BUY
    assert buy.suggested_sl == pytest.approx(98.5)
    assert sell.type == SignalType.SELL
    assert sell.suggested_sl == pytest.approx(101.5)


def test_clustered_flow_blocks_entry() -> None:
    signal = _model().predict(
        Indicators(hawkes=0.9, zscore=-3.0, rsi=10.0, atr=1.0), 100.0
    )

    assert signal.type == SignalType.HOLD
    assert "clustered trades" in signal.reason


def test_missing_flow_blocks_entry() -> None:
    signal = _model().predict(Indicators(zscore=-3.0, rsi=10.0, atr=1.0), 100.0)

    assert signal.type == SignalType.HOLD
    assert signal.reason == "hawkes: no trades"


def test_model_is_registered() -> None:
    assert "hawkes" in model_names()
    assert isinstance(build_model("hawkes"), HawkesModel)


def test_config_validates_threshold() -> None:
    with pytest.raises(ValueError, match="branching_threshold"):
        HawkesConfig(branching_threshold=0.0)
    with pytest.raises(ValueError, match="branching_threshold"):
        HawkesConfig(branching_threshold=1.5)
