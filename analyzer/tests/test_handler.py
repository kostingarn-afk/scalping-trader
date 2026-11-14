"""Тесты обработчика raw-market-data: разбор Protobuf и окно свечей."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from analyzer import proto_loader
from analyzer.handler import CandleWindow, parse_candle

pytestmark = pytest.mark.skipif(
    not proto_loader.PROTO_AVAILABLE,
    reason="protobuf-модули не сгенерированы (build-шаг protoc)",
)


def _raw_market_data(close: float, is_complete: bool = True) -> bytes:
    pb = proto_loader.raw_market_data_pb2
    msg = pb.RawMarketData(figi="BBG004730N88", payload_type=pb.CANDLE)
    candle = msg.candle
    candle.is_complete = is_complete
    for field, value in (("open", close), ("close", close), ("high", close + 1), ("low", close - 1)):
        quotation = getattr(candle, field)
        quotation.units = int(value)
        quotation.nano = int(round((value - int(value)) * 1e9))
    candle.volume = 10
    candle.end.FromSeconds(1_700_000_000)
    return msg.SerializeToString()


def test_parse_candle_returns_figi_and_ohlcv():
    figi, candle = parse_candle(_raw_market_data(100.5))
    assert figi == "BBG004730N88"
    assert candle["close"] == pytest.approx(100.5)
    assert candle["high"] == pytest.approx(101.5)
    assert candle["low"] == pytest.approx(99.5)
    assert candle["volume"] == 10
    assert candle["candle_ts"] is not None
    assert candle["candle_ts"].tzinfo is not None


def test_parse_candle_skips_incomplete():
    assert parse_candle(_raw_market_data(100.0, is_complete=False)) is None


def test_parse_candle_skips_non_candle_payload():
    pb = proto_loader.raw_market_data_pb2
    msg = pb.RawMarketData(figi="BBG004730N88", payload_type=pb.TRADE)
    msg.trade.price.units = 100
    msg.trade.quantity = 1
    assert parse_candle(msg.SerializeToString()) is None


def test_parse_candle_skips_garbage():
    assert parse_candle(b"\xff\xff\xff") is None
    assert parse_candle(b"") is None


def test_candle_window_waits_for_minimum():
    window = CandleWindow(maxlen=10, min_candles=3)
    candle = {"close": 100.0, "high": 101.0, "low": 99.0, "volume": 1, "candle_ts": None}
    assert window.add("SBER", candle) is None
    assert window.add("SBER", candle) is None
    data = window.add("SBER", candle)
    assert data is not None
    assert data["price"] == 100.0
    assert len(data["closes"]) == 3
    assert data["volumes"].tolist() == [1, 1, 1]


def test_candle_window_replaces_same_candle_instead_of_duplicating():
    window = CandleWindow(maxlen=10, min_candles=1)
    ts = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
    first = {"close": 100.0, "high": 101.0, "low": 99.0, "volume": 1, "candle_ts": ts}
    assert len(window.add("SBER", first)["closes"]) == 1
    # повторная свеча с тем же временем — заменяет предыдущую, а не добавляется
    updated = {"close": 105.0, "high": 106.0, "low": 99.0, "volume": 7, "candle_ts": ts}
    data = window.add("SBER", updated)
    assert len(data["closes"]) == 1
    assert data["price"] == 105.0
    assert data["volumes"].tolist() == [7]


def test_candle_window_is_per_instrument_and_bounded():
    window = CandleWindow(maxlen=3, min_candles=1)
    for i in range(5):
        data = window.add("SBER", {"close": float(i), "high": 1.0, "low": 1.0, "volume": 1, "candle_ts": None})
    assert len(data["closes"]) == 3  # окно ограничено maxlen
    assert data["price"] == 4.0

    # другой инструмент имеет собственное окно
    other = window.add("GAZP", {"close": 7.0, "high": 1.0, "low": 1.0, "volume": 1, "candle_ts": None})
    assert other["price"] == 7.0
    assert len(other["closes"]) == 1
