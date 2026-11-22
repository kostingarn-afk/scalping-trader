"""Тесты микроструктуры: стакан, VPIN и модель входа (шаг 1.3 `to_do.md` §4.1)."""
from __future__ import annotations

import numpy as np
import pytest

from analyzer.config import AnalyzerConfig
from analyzer.indicators import Indicators, compute_all
from analyzer.microstructure import (
    BUY,
    SELL,
    UNKNOWN,
    MicrostructureState,
    VolumeBuckets,
    classify_trade,
)
from analyzer.signal_models import (
    MicroConfig,
    MicrostructureModel,
    SignalModel,
    build_model,
    model_names,
)
from analyzer.strategy import SignalType


def _micro(**overrides):
    defaults = {"obi_entry": 0.2, "vpin_max": 0.5}
    defaults.update(overrides)
    return MicrostructureModel(config=MicroConfig(**defaults))


# --- тик-правило ---------------------------------------------------------------


def test_tick_rule_uses_previous_price() -> None:
    assert classify_trade(101.0, 100.0, UNKNOWN) == BUY
    assert classify_trade(99.0, 100.0, UNKNOWN) == SELL
    assert classify_trade(100.0, 100.0, BUY) == BUY
    assert classify_trade(100.0, 100.0, UNKNOWN) == UNKNOWN


def test_tick_rule_without_previous_price_is_unknown() -> None:
    assert classify_trade(100.0, None, BUY) == UNKNOWN


# --- VPIN ----------------------------------------------------------------------


def test_vpin_requires_full_window() -> None:
    buckets = VolumeBuckets(bucket_volume=100, buckets=3)
    buckets.add(100, BUY)
    buckets.add(100, BUY)

    assert buckets.vpin() is None


def test_vpin_one_sided_flow_is_one() -> None:
    buckets = VolumeBuckets(bucket_volume=100, buckets=3)
    for _ in range(3):
        buckets.add(100, BUY)

    assert buckets.vpin() == pytest.approx(1.0)


def test_vpin_balanced_flow_is_zero() -> None:
    buckets = VolumeBuckets(bucket_volume=100, buckets=3)
    for _ in range(3):
        buckets.add(50, BUY)
        buckets.add(50, SELL)

    assert buckets.vpin() == pytest.approx(0.0)


def test_vpin_splits_trade_across_buckets() -> None:
    buckets = VolumeBuckets(bucket_volume=100, buckets=1)
    buckets.add(150, BUY)

    # 100 ушло в первый бакет, остаток 50 — в незавершённый второй.
    assert buckets.vpin() == pytest.approx(1.0)


def test_vpin_ignores_unknown_direction() -> None:
    buckets = VolumeBuckets(bucket_volume=100, buckets=1)
    buckets.add(100, UNKNOWN)

    assert buckets.vpin() is None


# --- состояние стакана и сделок ------------------------------------------------


def test_state_snapshot_includes_book_and_vpin() -> None:
    state = MicrostructureState(depth=2, bucket_volume=100, buckets=1)
    state.update_book("FIGI", [10, 20, 30], [5, 5, 5])
    state.on_trade("FIGI", 100.0, 100, BUY)

    snapshot = state.snapshot("FIGI")

    assert snapshot["bids_qty"] == [10, 20]  # глубина 2
    assert snapshot["asks_qty"] == [5, 5]
    assert snapshot["vpin"] == pytest.approx(1.0)


def test_state_without_data_returns_empty_snapshot() -> None:
    state = MicrostructureState()

    assert state.snapshot("FIGI") == {
        "bids_qty": [],
        "asks_qty": [],
        "vpin": None,
        "hawkes": None,
    }


def test_state_uses_tick_rule_for_unspecified_direction() -> None:
    state = MicrostructureState(bucket_volume=100, buckets=1)
    state.on_trade("FIGI", 100.0, 100)  # первая сделка: направление неизвестно
    state.on_trade("FIGI", 101.0, 100)  # выше предыдущей — покупка

    assert state.snapshot("FIGI")["vpin"] == pytest.approx(1.0)


# --- модель --------------------------------------------------------------------


def test_model_buys_on_bid_dominance() -> None:
    signal = _micro().predict(Indicators(order_imbalance=0.4, vpin=0.1, atr=1.0), 100.0)

    assert signal.type == SignalType.BUY
    assert signal.suggested_sl == pytest.approx(98.5)
    assert signal.suggested_tp == pytest.approx(102.0)


def test_model_sells_on_ask_dominance() -> None:
    signal = _micro().predict(Indicators(order_imbalance=-0.4, vpin=0.1, atr=1.0), 100.0)

    assert signal.type == SignalType.SELL
    assert signal.suggested_sl == pytest.approx(101.5)
    assert signal.suggested_tp == pytest.approx(98.0)


def test_model_holds_on_toxic_flow() -> None:
    signal = _micro().predict(Indicators(order_imbalance=0.9, vpin=0.8, atr=1.0), 100.0)

    assert signal.type == SignalType.HOLD
    assert "toxic flow" in signal.reason


@pytest.mark.parametrize(
    "ind,reason",
    [
        (Indicators(vpin=0.1, atr=1.0), "no order book"),
        (Indicators(order_imbalance=0.5, atr=1.0), "no trades"),
        (Indicators(order_imbalance=0.5, vpin=0.1), "insufficient data"),
        (Indicators(order_imbalance=0.05, vpin=0.1, atr=1.0), "no signal"),
    ],
)
def test_model_holds_without_required_data(ind: Indicators, reason: str) -> None:
    signal = _micro().predict(ind, 100.0)

    assert signal.type == SignalType.HOLD
    assert reason in signal.reason


def test_model_is_registered_and_conforms() -> None:
    assert "microstructure" in model_names()
    model = build_model("microstructure")
    assert isinstance(model, MicrostructureModel)
    assert isinstance(model, SignalModel)
    # Без стакана в бэктесте модель обязана молчать, а не падать.
    assert model.predict(Indicators(atr=1.0), 100.0).type == SignalType.HOLD


def test_micro_config_validates_thresholds() -> None:
    with pytest.raises(ValueError, match="obi_entry"):
        MicroConfig(obi_entry=0.0)
    with pytest.raises(ValueError, match="vpin_max"):
        MicroConfig(vpin_max=1.5)


# --- индикаторы и конфиг -------------------------------------------------------


def test_compute_all_passes_vpin_through() -> None:
    array = np.full(30, 100.0)
    ind = compute_all(
        array,
        array,
        array,
        np.full(30, 1),
        bids_qty=[10, 10],
        asks_qty=[5, 5],
        vpin=0.42,
    )

    assert ind.vpin == 0.42
    assert ind.order_imbalance == pytest.approx((20 - 10) / 30)
    assert ind.to_dict()["vpin"] == 0.42


def test_analyzer_config_validates_microstructure_params() -> None:
    config = AnalyzerConfig()
    assert config.order_book_depth == 10
    assert config.vpin_bucket_volume == 1000
    assert config.vpin_buckets == 20
    with pytest.raises(ValueError, match="vpin_bucket_volume"):
        AnalyzerConfig(vpin_bucket_volume=0)


async def test_pipeline_passes_book_and_vpin_into_indicators() -> None:
    """Production-путь: стакан из handler'а доходит до индикаторов и записи в БД."""
    pytest.importorskip("kafka", reason="AnalyzerPipeline импортирует kafka-консьюмер")
    from analyzer.main import AnalyzerPipeline

    pipeline = AnalyzerPipeline(AnalyzerConfig())
    captured: dict = {}

    async def fake_save(record) -> None:
        captured["indicators"] = record.indicators

    pipeline.repository.save = fake_save  # type: ignore[method-assign]
    pipeline.publisher.publish = lambda *args, **kwargs: False  # type: ignore[method-assign]

    closes = np.linspace(100.0, 105.0, 30)
    candle_data = {
        "closes": closes,
        "highs": closes + 0.2,
        "lows": closes - 0.2,
        "volumes": np.full(30, 10),
        "price": float(closes[-1]),
        "bids_qty": [10] * 10,
        "asks_qty": [0] * 10,
        "vpin": 0.3,
    }

    await pipeline.process("FIGI", candle_data)

    assert captured["indicators"]["order_imbalance"] == pytest.approx(1.0)
    assert captured["indicators"]["vpin"] == pytest.approx(0.3)
