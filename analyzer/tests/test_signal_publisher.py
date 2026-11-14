"""Тесты publisher — задача 3.7: BUY публикуется, HOLD — нет."""
from __future__ import annotations

from unittest.mock import MagicMock

from analyzer.config import AnalyzerConfig
from analyzer.signal_publisher import SignalPublisher
from analyzer.strategy import Signal, SignalType


def test_hold_not_published():
    config = AnalyzerConfig()
    pub = SignalPublisher(config)
    pub._producer = MagicMock()
    # protobuf-модуль может отсутствовать — publisher вернёт False
    sig = Signal(SignalType.HOLD, 0.0, 100.0, None, None)
    result = pub.publish(sig, "s1", "SBER", 0, "strat", "0.1.0", {})
    assert result is False
    pub._producer.send.assert_not_called()
