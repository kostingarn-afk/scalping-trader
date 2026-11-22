"""Тесты режимной модели (волна 2 R3)."""
from __future__ import annotations

import numpy as np
import pytest

from analyzer.research.regime import (
    MTF_DISAGREEMENT_LIMIT,
    RegimeError,
    Survival,
    aggregate_minutes,
    evaluate_regime,
    fit_hmm,
    markov_counts,
    mtf_consistency,
    regime_features,
    survival,
    trusted_tau,
    wilson_lower,
)

MINUTE = 60


def _arrays(close, *, high=None, low=None, open_=None, volume=None, start_ts=1_700_000_000):
    close = np.asarray(close, dtype=float)
    n = len(close)
    return {
        "ts": np.asarray([start_ts + MINUTE * index for index in range(n)], dtype=np.int64),
        "open": close.copy() if open_ is None else np.asarray(open_, dtype=float),
        "high": close.copy() if high is None else np.asarray(high, dtype=float),
        "low": close.copy() if low is None else np.asarray(low, dtype=float),
        "close": close,
        "volume": np.ones(n) if volume is None else np.asarray(volume, dtype=float),
    }


def test_aggregate_buckets_from_window_start():
    close = [100 + index for index in range(45)]
    session = _arrays(close, high=[price + 0.5 for price in close],
                      low=[price - 0.5 for price in close], volume=[2.0] * 45)

    agg = aggregate_minutes(session, 15)

    assert len(agg["close"]) == 3
    assert agg["close"][0] == pytest.approx(114.0)
    assert agg["high"][0] == pytest.approx(114.5)
    assert agg["low"][0] == pytest.approx(99.5)
    assert agg["volume"][0] == pytest.approx(30.0)
    assert agg["ts"][1] - agg["ts"][0] == 15 * MINUTE


def test_aggregate_keeps_partial_last_bucket():
    session = _arrays([100.0] * 20)

    agg = aggregate_minutes(session, 15)

    assert len(agg["close"]) == 2
    assert agg["volume"][1] == pytest.approx(5.0)


def test_aggregate_requires_timestamps():
    session = {"open": np.ones(3), "high": np.ones(3), "low": np.ones(3),
               "close": np.ones(3), "volume": np.ones(3)}

    with pytest.raises(RegimeError):
        aggregate_minutes(session, 15)


def test_features_shape_and_imbalance_bounds():
    session = _arrays([100.0, 101.0, 100.5], high=[101.0, 102.0, 101.0],
                      low=[99.0, 100.0, 100.0], open_=[100.0, 100.5, 101.0])

    features, lengths = regime_features([session])

    assert features.shape == (3, 3)
    assert lengths == [3]
    assert np.all(np.abs(features[:, 2]) <= 1.0)
    assert features[1, 0] == pytest.approx(np.log(101.0 / 100.0))


def test_features_reject_empty_sessions():
    with pytest.raises(RegimeError):
        regime_features([_arrays([100.0])])


def test_markov_counts_count_switches_not_pairs():
    """`N_trans` — число смен состояния, а не число пар баров (buy_frame §16)."""
    counts, transitions = markov_counts([0, 1, 1, 0, 0], [0, 0, 0, 1, 1])

    assert transitions == 1
    assert counts[0, 1] == 1
    assert counts[1, 1] == 1
    assert counts[0, 0] == 1  # пара 0→0 в другом окне: пара есть, смены нет


def test_markov_counts_ignore_session_boundary():
    counts, transitions = markov_counts([0, 1, 1, 0], [0, 0, 0, 0])

    assert transitions == 2
    assert counts[1, 1] == 1
    assert counts[0, 1] + counts[1, 0] == 2


def test_wilson_lower_is_conservative_and_monotone():
    assert wilson_lower(0, 10) == 0.0
    assert wilson_lower(10, 10) < 1.0
    assert wilson_lower(90, 100) < 0.9
    assert wilson_lower(900, 1000) > wilson_lower(90, 100)


def test_wilson_rejects_impossible_counts():
    with pytest.raises(RegimeError):
        wilson_lower(11, 10)


def test_survival_strong_persistence_gives_horizon():
    counts = np.asarray([[990, 10], [50, 950]])

    item = survival(counts, state=0, q=0.10, htf_minutes=15)

    assert item.gate_passed is True
    assert item.gamma == pytest.approx(0.99)
    assert item.tau_steps >= 1
    assert item.tau_minutes == item.tau_steps * 15
    assert item.tau_minutes_alt >= item.tau_minutes


def test_survival_weak_persistence_blocks_horizon():
    """γ < 1−q: режим не удерживается на шаге — горизонт не назначается."""
    counts = np.asarray([[70, 30], [40, 60]])

    item = survival(counts, state=0, q=0.10, htf_minutes=15)

    assert item.gate_passed is False
    assert item.tau_steps == 0
    assert item.tau_minutes == 0


def test_survival_without_departures_is_zero():
    counts = np.asarray([[0, 0], [1, 1]])

    item = survival(counts, state=0, q=0.10)

    assert item.tau_minutes == 0
    assert item.gate_passed is False


def _synthetic_sessions(*, sessions: int = 20, buckets: int = 40, seed: int = 11,
                        p_stay_calm: float = 0.95, p_stay_volatile: float = 0.9):
    """Минутные бары, у которых 15m-корзины следуют двухрежимной цепи с разной σ."""
    rng = np.random.default_rng(seed)
    out = []
    for session_index in range(sessions):
        states = []
        state = 0
        for _ in range(buckets):
            states.append(state)
            if state == 0:
                state = 0 if rng.random() < p_stay_calm else 1
            else:
                state = 1 if rng.random() < p_stay_volatile else 0

        closes, highs, lows, opens, volumes = [], [], [], [], []
        level = 100.0
        for state in states:
            sigma = 0.0004 if state == 0 else 0.004
            # Дрейф задан, иначе `fav` (знак дрейфа) не определится на синтетике.
            drift = 0.0005 if state == 0 else -0.0005
            steps = [level]
            for _ in range(15):
                level *= float(np.exp(drift / 15.0 + sigma * rng.normal()))
                steps.append(level)
            opens.extend([steps[0]] * 15)
            highs.extend([max(steps)] * 15)
            lows.extend([min(steps)] * 15)
            closes.extend(steps[1:])
            volumes.extend([100.0] * 15)
        out.append(
            _arrays(
                closes,
                high=highs,
                low=lows,
                open_=opens,
                volume=volumes,
                start_ts=1_700_000_000 + session_index * 86_400,
            )
        )
    return out


def test_regime_fit_orders_states_by_volatility_and_passes_gate2():
    sessions = _synthetic_sessions()
    features, lengths = regime_features(
        [aggregate_minutes(session, 15) for session in sessions]
    )

    model, order, score, mean_volatility, converged = fit_hmm(features, lengths, seeds=2)

    assert np.isfinite(score)
    assert isinstance(converged, bool)
    assert mean_volatility[int(order[0])] < mean_volatility[int(order[1])]


def test_evaluate_regime_reports_gate2_and_survival():
    sessions = _synthetic_sessions()

    report = evaluate_regime(sessions, instrument="TEST@TQBR", q=0.10, seeds=2)

    assert report.fit is not None
    assert report.fit.gate2_passed is True
    assert report.gates["regime"] == "PASS"
    assert report.fit.volatility[0] < report.fit.volatility[1]
    # fav задаётся знаком дрейфа: у синтетики спокойное состояние с положительным дрейфом.
    assert report.fit.fav(1) is not None
    assert set(report.survival_by_direction) == {1, -1}
    assert report.survival_by_direction[1] is not None
    assert report.mtf_disagreement is not None
    assert 0.0 <= report.mtf_disagreement <= 1.0
    # Синтетика даёт γ ниже 1−q: измеренная низкая уверенность помечается, а горизонт
    # направления в τ_regime не входит (R4) — иначе проваленный гейт обрезал бы доверенный.
    assert "REGIME_CONFIDENCE" in report.gates["survival"]
    assert report.tau_regime_minutes is None
    assert report.tau_mtf_minutes is None


def test_tau_regime_uses_only_directions_that_pass_gate():
    def item(tau: int, passed: bool) -> Survival:
        return Survival(
            state=0,
            gamma=0.99 if passed else 0.2,
            p_lcb=0.9 if passed else 0.1,
            transitions_from=100,
            q=0.1,
            tau_steps=tau,
            tau_minutes=tau,
            tau_minutes_alt=tau,
            gate_passed=passed,
        )

    assert trusted_tau({1: item(180, True), -1: item(0, False)}) == 180
    assert trusted_tau({1: item(180, True), -1: None}) == 180
    assert trusted_tau({1: item(120, True), -1: item(180, True)}) == 120
    # Доверенных направлений нет — горизонт не определён, а не ноль.
    assert trusted_tau({1: None, -1: None}) is None
    assert trusted_tau({1: item(0, False), -1: item(0, False)}) is None


def test_evaluate_regime_marks_failure_on_short_history():
    sessions = [_arrays([100.0 + 0.01 * index for index in range(160)])]

    report = evaluate_regime(sessions, instrument="TEST@TQBR", q=0.10, seeds=1)

    assert report.fit is None
    assert report.gates["regime"].startswith("GATE2_FAILED")
    assert report.survival_by_direction == {1: None, -1: None}
    # При провале Gate 2 гейт не блокирует вход: компонент уходит в REFERENCE_ONLY.
    assert report.gate_passed == {1: True, -1: True}


def test_evaluate_regime_rejects_incompatible_frames():
    with pytest.raises(RegimeError):
        evaluate_regime(_synthetic_sessions(sessions=2), instrument="X", q=0.10,
                        htf_minutes=10, mtf_minutes=15)


def test_mtf_consistency_measures_share_of_mismatched_bars():
    ts = np.asarray([0, 900, 1800, 2700], dtype=np.int64)
    htf_states = np.asarray([0, 0, 0, 0])
    mtf_ts = np.asarray([0, 600, 1200, 1800, 2400, 3000], dtype=np.int64)
    same = [(ts, htf_states)]
    match = [(mtf_ts, np.asarray([0, 0, 0, 0, 0, 0]))]
    other = [(mtf_ts, np.asarray([1, 1, 1, 1, 1, 1]))]

    agree = mtf_consistency(same, match)
    disagree = mtf_consistency(same, other)

    assert agree == pytest.approx(0.0)
    assert disagree == pytest.approx(1.0)
    assert disagree > MTF_DISAGREEMENT_LIMIT
