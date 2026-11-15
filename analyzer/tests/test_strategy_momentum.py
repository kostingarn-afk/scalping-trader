"""Тесты стратегии momentum: вход в сторону движения, фильтры тренда и волатильности."""
from __future__ import annotations

import pytest

from analyzer.indicators import Indicators
from analyzer.strategy import SignalType
from analyzer.strategy_momentum import MomentumConfig, momentum


def _ind(**overrides) -> Indicators:
    """Пробой вверх с подтверждением силы: вход должен быть разрешён."""
    params = {
        "rsi": 72.0,
        "bb_lower": 98.0,
        "bb_middle": 100.0,
        "bb_upper": 102.0,
        "atr": 0.5,
        "atr_pct": 0.5,
        "adx": 30.0,
        "zscore": 1.8,
    }
    params.update(overrides)
    return Indicators(**params)


def test_buy_on_breakout_with_strength():
    sig = momentum(_ind(), price=102.0)

    assert sig.type is SignalType.BUY
    assert sig.suggested_sl < 102.0 < sig.suggested_tp


def test_hold_when_rsi_does_not_confirm():
    """Цена вышла за канал, но сила не подтверждена — вход не открывается."""
    sig = momentum(_ind(rsi=58.0), price=102.0)

    assert sig.type is SignalType.HOLD
    assert sig.reason == "no signal"


def test_hold_on_small_deviation():
    sig = momentum(_ind(zscore=1.2), price=101.0)

    assert sig.type is SignalType.HOLD


def test_sell_symmetric():
    sig = momentum(_ind(rsi=25.0, zscore=-1.9), price=98.0)

    assert sig.type is SignalType.SELL
    assert sig.suggested_tp < 98.0 < sig.suggested_sl


def test_adx_min_blocks_flat_market():
    config = MomentumConfig(adx_min=25.0)

    sig = momentum(_ind(adx=18.0), price=102.0, config=config)

    assert sig.type is SignalType.HOLD
    assert "no trend" in sig.reason


def test_adx_filter_off_by_default():
    sig = momentum(_ind(adx=5.0), price=102.0)

    assert sig.type is SignalType.BUY


def test_volatility_filter_blocks_thin_bars():
    config = MomentumConfig(min_atr_pct=0.3)

    sig = momentum(_ind(atr_pct=0.1), price=102.0, config=config)

    assert sig.type is SignalType.HOLD
    assert "volatility" in sig.reason


def test_hold_without_volatility_data():
    sig = momentum(_ind(atr=None), price=102.0)

    assert sig.type is SignalType.HOLD


def test_confidence_grows_with_deviation():
    weak = momentum(_ind(zscore=1.6), price=102.0)
    strong = momentum(_ind(zscore=3.0), price=103.0)

    assert weak.confidence < strong.confidence


def test_label_describes_variant():
    assert MomentumConfig().label == "mom-z1.5"
    assert MomentumConfig(z_entry=2.0, adx_min=25.0, min_atr_pct=0.2).label == (
        "mom-z2-adx25-atr0.2"
    )


@pytest.mark.parametrize(
    "kwargs",
    [{"z_entry": 0.0}, {"rsi_buy": 40.0}, {"rsi_sell": 60.0}, {"adx_min": -1.0}, {"min_atr_pct": -1.0}],
)
def test_invalid_config_is_rejected(kwargs):
    with pytest.raises(ValueError):
        MomentumConfig(**kwargs)
