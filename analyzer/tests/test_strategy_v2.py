"""Тесты стратегии v2: порог z-score, фильтр режима (ADX), фильтр волатильности."""
from __future__ import annotations

import pytest

from analyzer.indicators import Indicators
from analyzer.strategy import SignalType
from analyzer.strategy_v2 import StrategyV2Config, mean_reversion_v2


def _ind(**overrides) -> Indicators:
    """Боковик с глубокой перепроданностью по умолчанию: вход должен быть разрешён."""
    params = {
        "rsi": 20.0,
        "bb_lower": 98.0,
        "bb_middle": 100.0,
        "bb_upper": 102.0,
        "atr": 0.5,
        "atr_pct": 0.5,
        "adx": 15.0,
        "zscore": -2.5,
    }
    params.update(overrides)
    return Indicators(**params)


def test_buy_on_deep_oversold_in_range():
    sig = mean_reversion_v2(_ind(), price=97.5)

    assert sig.type is SignalType.BUY
    assert sig.suggested_sl < 97.5 < sig.suggested_tp


def test_hold_on_shallow_pullback():
    """|z| ниже порога: мелкий откат не окупает круговую комиссию."""
    sig = mean_reversion_v2(_ind(zscore=-1.5), price=98.5)

    assert sig.type is SignalType.HOLD
    assert sig.reason == "no signal"


def test_hold_in_trending_market():
    """ADX выше потолка — возврата к средней не ждём (47 % SL старого правила отсюда)."""
    sig = mean_reversion_v2(_ind(adx=32.0), price=97.5)

    assert sig.type is SignalType.HOLD
    assert "trending" in sig.reason


def test_hold_on_low_volatility():
    config = StrategyV2Config(min_atr_pct=0.3)

    sig = mean_reversion_v2(_ind(atr_pct=0.1), price=97.5, config=config)

    assert sig.type is SignalType.HOLD
    assert "volatility" in sig.reason


def test_volatility_filter_off_by_default():
    sig = mean_reversion_v2(_ind(atr_pct=0.01), price=97.5)

    assert sig.type is SignalType.BUY


def test_sell_symmetric():
    sig = mean_reversion_v2(_ind(rsi=80.0, zscore=2.4), price=102.5)

    assert sig.type is SignalType.SELL
    assert sig.suggested_tp < 102.5 < sig.suggested_sl


def test_hold_without_adx():
    """Без ADX режим рынка неизвестен: вход не разрешается (reason это показывает)."""
    sig = mean_reversion_v2(_ind(adx=None), price=97.5)

    assert sig.type is SignalType.HOLD
    assert "adx" in sig.reason


def test_hold_without_volatility_data():
    sig = mean_reversion_v2(_ind(atr=None), price=97.5)

    assert sig.type is SignalType.HOLD


def test_confidence_grows_with_deviation():
    shallow = mean_reversion_v2(_ind(zscore=-2.1), price=98.0)
    deep = mean_reversion_v2(_ind(zscore=-3.9), price=96.0)

    assert shallow.confidence < deep.confidence


def test_label_describes_variant():
    assert StrategyV2Config().label == "v2-z2-adx25"
    assert (
        StrategyV2Config(z_entry=2.5, adx_max=20, min_atr_pct=0.15).label
        == "v2-z2.5-adx20-atr0.15"
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"z_entry": 0.0},
        {"rsi_buy": 60.0},
        {"rsi_sell": 40.0},
        {"adx_max": 0.0},
        {"min_atr_pct": -1.0},
    ],
)
def test_invalid_config_is_rejected(kwargs):
    with pytest.raises(ValueError):
        StrategyV2Config(**kwargs)
