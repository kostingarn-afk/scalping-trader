"""Тесты модели издержек бэктеста."""
from __future__ import annotations

import pytest

from analyzer.backtest.commission import TARIFFS, CommissionModel
from analyzer.backtest.engine import passes_commission_filter
from analyzer.backtest.models import Instrument, RunConfig
from analyzer.commission_filter import CommissionFilter
from analyzer.strategy import Signal, SignalType


def test_trader_rates_differ_for_security_and_future():
    model = CommissionModel("trader")

    assert model.per_side_rate(is_future=False) == pytest.approx(0.0005)
    assert model.per_side_rate(is_future=True) == pytest.approx(0.0004)


def test_investor_security_rate_matches_production_stub():
    model = CommissionModel("investor")

    assert model.per_side_rate(is_future=False) == pytest.approx(0.003)
    assert model.round_trip_rate == pytest.approx(0.006)


def test_default_tariff_is_investor():
    """Расчёт по умолчанию — по реальному тарифу счёта, а не по промо-сценарию."""
    assert CommissionModel().tariff == "investor"


def test_round_trip_sums_entry_and_exit():
    model = CommissionModel("trader")

    expected = 0.0005 * 270_000 + 0.0005 * 273_000
    assert model.round_trip(270_000, 273_000, is_future=False) == pytest.approx(expected)


def test_min_commission_applies_to_each_side():
    model = CommissionModel("trader", min_commission_rub=1.5)

    assert model.side_commission(1_000, is_future=False) == pytest.approx(1.5)
    assert model.round_trip(1_000, 1_000, is_future=False) == pytest.approx(3.0)


def test_unknown_tariff_is_rejected():
    with pytest.raises(ValueError):
        CommissionModel("scalper")
    assert "trader" in TARIFFS


def test_negative_min_commission_is_rejected():
    with pytest.raises(ValueError):
        CommissionModel("trader", min_commission_rub=-1.0)


@pytest.mark.parametrize("atr", [2.0, 2.5])
def test_commission_filter_matches_production_away_from_threshold(atr: float):
    """Вдали от порога решения бэктестового и production-фильтра совпадают.

    При investor и lot_size=1 обе модели требуют прибыли до TP ≥ 3 × комиссии круга.
    Порог отличается на доли процента: production берёт обе стороны по цене входа
    (`0.006·P`), бэктест — выход по фактической цене TP (`0.003·(P + TP)`).
    """
    price = 270.0
    instrument = Instrument("SBER", "TQBR", lot_size=1)
    config = RunConfig(commission_profit_ratio=3.0)
    signal = Signal(
        SignalType.BUY,
        confidence=0.8,
        suggested_price=price,
        suggested_sl=price - 1.5 * atr,
        suggested_tp=price + 2.0 * atr,
    )

    expected = CommissionFilter(ratio=3.0, rate_per_side=0.003).should_keep(signal, quantity=1)
    actual = passes_commission_filter(
        signal, price, instrument, CommissionModel("investor"), config
    )

    assert actual is expected


def test_filter_deviation_from_production_is_confined_to_threshold_band():
    """Расхождение моделей существует и известно: только в узкой полосе у порога.

    BUY: production-порог `ATR ≥ 0.009·P` = 2.43, бэктест — 2.452 → на 2.44 production
    оставляет сигнал, бэктест отбрасывает.
    SELL: production-порог тот же 2.43, бэктест — 2.408 → на 2.42 бэктест оставляет сигнал,
    production отбрасывает.
    """
    price = 270.0
    instrument = Instrument("SBER", "TQBR", lot_size=1)
    config = RunConfig(commission_profit_ratio=3.0)
    production = CommissionFilter(ratio=3.0, rate_per_side=0.003)
    investor = CommissionModel("investor")

    buy = Signal(SignalType.BUY, 0.8, price, price - 1.5 * 2.44, price + 2 * 2.44)
    assert production.should_keep(buy, quantity=1) is True
    assert passes_commission_filter(buy, price, instrument, investor, config) is False

    sell = Signal(SignalType.SELL, 0.8, price, price + 1.5 * 2.42, price - 2 * 2.42)
    assert production.should_keep(sell, quantity=1) is False
    assert passes_commission_filter(sell, price, instrument, investor, config) is True


def test_signal_without_stop_is_dropped_as_untradeable():
    """Без стопа нельзя посчитать объём — сигнал отбрасывается.

    Production-фильтр такой сигнал оставил бы: он проверяет только прибыль/комиссию.
    """
    instrument = Instrument("SBER", "TQBR", lot_size=1)
    signal = Signal(SignalType.BUY, 0.8, 100.0, None, 105.0)
    config = RunConfig()

    assert CommissionFilter(ratio=3.0).should_keep(signal, quantity=1) is True
    kept = passes_commission_filter(
        signal, 100.0, instrument, CommissionModel("trader"), config
    )

    assert not kept


# --- Биржевой сбор за контракт (срочный рынок) ---------------------------------------


def test_futures_exchange_fee_is_per_contract():
    model = CommissionModel("trader", exchange_fee_per_contract_rub=1.5)

    cost = model.side_commission(80_000.0, is_future=True, contracts=2)

    assert cost == pytest.approx(0.0004 * 80_000 + 3.0)


def test_futures_fee_is_skipped_without_contract_count():
    """Без числа контрактов модель считает только процент — и это видно по результату."""
    model = CommissionModel("trader", exchange_fee_per_contract_rub=1.5)

    assert model.side_commission(80_000.0, is_future=True) == pytest.approx(32.0)


def test_security_ignores_contract_fee():
    model = CommissionModel("trader", exchange_fee_per_contract_rub=1.5)

    assert model.side_commission(100_000.0, is_future=False, contracts=5) == pytest.approx(50.0)


def test_round_trip_charges_fee_on_both_sides():
    model = CommissionModel("trader", exchange_fee_per_contract_rub=1.0)

    cost = model.round_trip(80_000.0, 80_000.0, is_future=True, contracts=1)

    assert cost == pytest.approx(2 * (0.0004 * 80_000 + 1.0))


def test_negative_contracts_rejected():
    with pytest.raises(ValueError):
        CommissionModel("trader").side_commission(1.0, is_future=True, contracts=-1)


def test_negative_exchange_fee_rejected():
    with pytest.raises(ValueError):
        CommissionModel("trader", exchange_fee_per_contract_rub=-1)
