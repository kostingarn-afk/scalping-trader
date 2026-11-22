"""Тесты политики цели выхода (шаг 1.9): TP = k×ATR от порога комиссии."""
from __future__ import annotations

import pytest

from analyzer.config import AnalyzerConfig
from analyzer.strategy import Signal, SignalType, target_mult, with_target


def _signal(kind: SignalType = SignalType.BUY, tp: float = 102.0) -> Signal:
    return Signal(
        type=kind,
        confidence=0.8,
        suggested_price=100.0,
        suggested_sl=99.0,
        suggested_tp=tp,
    )


class TestTargetMult:
    def test_mult_covers_threshold(self):
        # ATR = 0.06 % цены, порог 0.30 % → k = 5
        assert target_mult(100.0, 0.06, 0.30) == 5

    def test_mult_is_ceiled_not_rounded(self):
        # порог 0.30 / ATR% 0.11 = 2.72 → k = 3: недобор до порога недопустим
        assert target_mult(100.0, 0.11, 0.30) == 3

    def test_mult_is_at_least_one(self):
        # ATR% = 0.8 % уже больше порога 0.30 %: сокращать цель незачем
        assert target_mult(100.0, 0.8, 0.30) == 1

    def test_mult_without_atr_is_one(self):
        assert target_mult(100.0, None, 0.30) == 1
        assert target_mult(100.0, 0.0, 0.30) == 1

    def test_mult_without_threshold_is_one(self):
        assert target_mult(100.0, 0.06, 0.0) == 1


class TestWithTarget:
    def test_buy_target_is_k_atr_above_price(self):
        updated = with_target(_signal(SignalType.BUY), 100.0, 0.06, 0.30)

        assert updated.suggested_tp == pytest.approx(100.0 + 5 * 0.06)
        assert updated.suggested_tp > 100.0

    def test_sell_target_is_k_atr_below_price(self):
        updated = with_target(_signal(SignalType.SELL), 100.0, 0.06, 0.30)

        assert updated.suggested_tp == pytest.approx(100.0 - 5 * 0.06)
        assert updated.suggested_tp < 100.0

    def test_target_covers_commission_threshold(self):
        price, atr, min_tp_pct = 250.0, 0.15, 0.30

        updated = with_target(_signal(SignalType.BUY), price, atr, min_tp_pct)

        distance_pct = (updated.suggested_tp - price) / price * 100.0
        assert distance_pct >= min_tp_pct - 1e-9

    def test_disabled_policy_keeps_static_target(self):
        original = _signal(tp=102.0)

        assert with_target(original, 100.0, 0.06, 0.0) is original

    def test_hold_is_untouched(self):
        hold = _signal(SignalType.HOLD, tp=0.0)

        assert with_target(hold, 100.0, 0.06, 0.30) is hold

    def test_missing_atr_keeps_signal(self):
        original = _signal(tp=102.0)

        assert with_target(original, 100.0, None, 0.30) is original

    def test_other_fields_preserved(self):
        original = _signal()

        updated = with_target(original, 100.0, 0.06, 0.30)

        assert updated.type is original.type
        assert updated.suggested_price == original.suggested_price
        assert updated.suggested_sl == original.suggested_sl
        assert updated.confidence == original.confidence


class TestRequiredTpPct:
    def test_default_rate_is_trader_tariff(self):
        config = AnalyzerConfig()

        assert config.commission_rate_per_side == pytest.approx(0.0005)
        # ratio 3 × 2 стороны × 0.0005 → 0.30 %
        assert config.required_tp_pct == pytest.approx(0.30)

    def test_investor_threshold_is_wider(self):
        config = AnalyzerConfig(commission_rate_per_side=0.003)

        assert config.required_tp_pct == pytest.approx(1.80)
