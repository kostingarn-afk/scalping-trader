"""Тесты идентификации θ и Gate 1 (шаги R2.1–R2.5 плана)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import log, sqrt

import numpy as np
import pytest

from analyzer.backtest.models import Bar
from analyzer.research.sessions import SessionSchedule
from analyzer.research.theta import (
    MIN_PAIRS,
    NO_DATA,
    PLATEAU,
    REG,
    UNDEFINED,
    NullBand,
    OuFit,
    ThetaError,
    ThetaFit,
    ThetaPoint,
    VarianceFit,
    _session_series,
    _session_sums,
    _select_estimate,
    aggregate_verdict,
    estimate_theta,
    evaluate_gate1,
    fit_ou,
)

MSK = timezone(timedelta(hours=3))
START_MINUTE = 10 * 60


def _ou_bars(
    theta: float,
    sigma: float,
    *,
    sessions: int = 30,
    minutes: int = 400,
    noise: float = 0.0,
    seed: int = 7,
) -> list[Bar]:
    """Синтетическая OU-цена: возврат к нулю внутри сессии, разрыв между сессиями."""
    rng = np.random.default_rng(seed)
    dt = 1.0
    bars: list[Bar] = []
    budget = sessions
    day = 0
    while budget > 0:
        date = datetime(2026, 9, 1, tzinfo=MSK) + timedelta(days=day)
        day += 1
        if date.weekday() >= 5:
            continue
        budget -= 1
        level = 0.0
        for minute in range(minutes):
            level += -theta * level * dt + sigma * sqrt(dt) * rng.normal()
            observed = level + (rng.normal() * noise if noise else 0.0)
            price = 100.0 * float(np.exp(observed))
            ts = date.replace(hour=10) + timedelta(minutes=1 + minute)
            bars.append(
                Bar(
                    ts=ts.astimezone(timezone.utc),
                    open=price,
                    high=price * 1.0001,
                    low=price * 0.9999,
                    close=price,
                    volume=100,
                )
            )
    return bars


def _schedule() -> SessionSchedule:
    return SessionSchedule.single(START_MINUTE, 18 * 60 + 40)


def _fit(**kwargs) -> ThetaFit:
    params = {"bootstrap": 20, "null_reps": 60}
    params.update(kwargs)
    return estimate_theta(_ou_bars(0.05, sigma=0.002), _schedule(), **params)


def _bar(ts: datetime, price: float) -> Bar:
    return Bar(ts=ts, open=price, high=price, low=price, close=price, volume=1)


def test_acf_estimate_recovers_theta_within_known_bias():
    """ACF-оценка по спеке смещена вверх; смещение фиксируется как известное (~+28 %)."""
    fit = _fit()

    assert fit.theta == pytest.approx(0.0639, abs=0.02)
    assert fit.method in (PLATEAU, REG)
    assert fit.band is not None


def test_variance_estimate_recovers_theta():
    fit = _fit()

    assert fit.variance is not None
    assert fit.variance.theta == pytest.approx(0.05, rel=0.15)
    assert fit.variance.identified is True
    assert fit.theta_used == pytest.approx(fit.variance.theta)


def test_noise_ratio_detected_and_variance_estimate_stays():
    clean = _fit()
    noisy = estimate_theta(
        _ou_bars(0.05, sigma=0.002, noise=0.0015, seed=11),
        _schedule(),
        bootstrap=20,
        null_reps=60,
    )

    assert noisy.noise_ratio > 0.05
    assert clean.noise_ratio < noisy.noise_ratio
    assert noisy.variance is not None
    assert noisy.variance.two_omega_sq > clean.variance.two_omega_sq
    assert noisy.variance.theta == pytest.approx(0.05, rel=0.2)


def test_gate1_passes_on_mean_reverting_series():
    fit = _fit()

    assert fit.gate1_passed is True
    assert fit.verdict == "PASS"
    assert fit.reasons == ()


def test_gate1_rejects_random_walk():
    """Ключевой тест дефекта: ACF-оценка на блуждании даёт ~0.01 и раньше проходила гейт."""
    bars = _ou_bars(0.0, sigma=0.002, seed=5)

    fit = estimate_theta(bars, _schedule(), bootstrap=20, null_reps=60)

    assert fit.gate1_passed is False
    assert any("полос" in reason for reason in fit.reasons)


def test_random_walk_variance_theta_is_unidentified():
    bars = _ou_bars(0.0, sigma=0.002, seed=5)

    fit = estimate_theta(bars, _schedule(), bootstrap=20, null_reps=60)

    assert fit.variance is not None
    assert fit.variance.identified is False
    assert fit.variance.theta < 0.001


def test_band_is_geometry_only():
    """Полоса масштабно-инвариантна: волатильность не входит в её расчёт."""
    small = estimate_theta(_ou_bars(0.05, 0.002), _schedule(), bootstrap=20, null_reps=60)
    large = estimate_theta(_ou_bars(0.05, 0.02), _schedule(), bootstrap=20, null_reps=60)

    assert small.band is not None and large.band is not None
    assert small.band.acf_intercept == large.band.acf_intercept
    assert small.band.variance == large.band.variance


def test_no_band_with_few_sessions_rejects_gate1():
    bars = _ou_bars(0.05, sigma=0.002, sessions=10)

    fit = estimate_theta(bars, _schedule(), bootstrap=20, null_reps=50)

    assert fit.band is None
    assert fit.gate1_passed is False
    assert any("полоса" in reason for reason in fit.reasons)


def test_pairs_require_exact_lag_and_skip_gaps():
    """Пропуск минут внутри окна не превращается в «доходность за Δ»."""
    base = datetime(2026, 9, 1, 10, 1, tzinfo=MSK)
    prices = {minute: 100.0 + minute for minute in range(10)}
    for missing in (5, 6):
        prices.pop(missing)
    bars = [_bar(base + timedelta(minutes=minute), price) for minute, price in prices.items()]

    sessions = _session_series(bars, _schedule())
    sums = _session_sums(sessions[0], (1, 2))

    assert sums[1][0] == 6  # 8 баров минус пары через пропуск
    assert sums[2][0] == 4


def test_pairs_do_not_cross_window_boundary():
    """Пара через границу окон исключается: ночной разрыв не даёт ложной корреляции."""
    day = datetime(2026, 9, 1, tzinfo=MSK)
    bars = [
        _bar(day.replace(hour=9, minute=49), 100.0),
        _bar(day.replace(hour=9, minute=50), 101.0),  # вне окон: между утренней и основной
        _bar(day.replace(hour=10, minute=0), 102.0),
        _bar(day.replace(hour=10, minute=1), 103.0),
    ]

    sessions = _session_series(bars, SessionSchedule())
    by_window = {session[0][1]: _session_sums(session, (1,)) for session in sessions}

    assert set(by_window) == {"morning", "main"}
    assert by_window["morning"][1][0] == 0  # одиночный бар: пары нет
    assert by_window["main"][1][0] == 1


def test_duplicate_timestamps_collapse_to_last():
    ts = datetime(2026, 9, 1, 10, 1, tzinfo=MSK)
    bars = [_bar(ts, 100.0), _bar(ts + timedelta(minutes=1), 101.0), _bar(ts, 105.0)]

    sessions = _session_series(bars, _schedule())

    assert sessions[0][1].size == 2
    assert sessions[0][2][0] == pytest.approx(log(105.0))


def test_deltas_are_deduplicated_and_sorted():
    bars = _ou_bars(0.05, sigma=0.002)

    fit = estimate_theta(
        bars, _schedule(), bootstrap=20, null_reps=50, deltas=(5, 1, 5, 1, 10)
    )

    assert [point.delta for point in fit.points] == [1, 5, 10]


def test_points_marked_no_data_below_minimum():
    bars = _ou_bars(0.05, sigma=0.001, minutes=400)
    # Δ=399 при 400 барах в окне даёт одну пару на сессию: ниже MIN_PAIRS суммарно.
    fit = estimate_theta(bars, _schedule(), bootstrap=20, null_reps=50, deltas=(1, 5, 399))

    assert fit.points[-1].status == NO_DATA
    assert fit.points[-1].pairs < MIN_PAIRS


def test_too_few_pairs_gives_undefined_theta():
    bars = _ou_bars(0.05, sigma=0.002, sessions=1, minutes=40)

    fit = estimate_theta(bars, _schedule(), bootstrap=20, null_reps=50)

    assert fit.gate1_passed is False
    assert fit.method == UNDEFINED
    assert fit.theta == 0.0


def test_escalated_deltas_include_sixty():
    from analyzer.research.theta import ESCALATED_DELTAS

    assert ESCALATED_DELTAS[-1] == 60
    fit = estimate_theta(
        _ou_bars(0.05, sigma=0.002),
        _schedule(),
        bootstrap=20,
        null_reps=50,
        deltas=ESCALATED_DELTAS,
    )
    assert [point.delta for point in fit.points] == list(ESCALATED_DELTAS)


def _band(
    acf_upper: float = 0.015,
    plateau_upper: float | None = None,
    var_upper: float = 0.001,
) -> NullBand:
    return NullBand(
        acf_intercept=(0.005, acf_upper),
        acf_plateau=(0.005, acf_upper if plateau_upper is None else plateau_upper),
        variance=(1e-6, var_upper),
        replications=100,
        sessions=30,
    )


def _variance(theta: float, identified: bool = True) -> VarianceFit:
    return VarianceFit(
        theta=theta,
        amplitude=1.0,
        two_omega_sq=0.0,
        points=(),
        identified=identified,
    )


def test_gate1_rejects_ci_containing_zero():
    reasons, _ = evaluate_gate1(
        theta=0.06,
        theta_ci=(-0.005, 0.09),
        t_half_ci=(7.7, 13.0),
        method=REG,
        variance=_variance(0.06),
        band=_band(),
    )

    assert any("CI θ содержит 0" in reason for reason in reasons)


def test_gate1_rejects_wide_half_life_interval():
    reasons, _ = evaluate_gate1(
        theta=0.06,
        theta_ci=(0.05, 0.07),
        t_half_ci=(9.9, 20.0),  # отношение 2.02 > 2
        method=REG,
        variance=_variance(0.06),
        band=_band(),
    )

    assert any("t_1/2 шире двукратного" in reason for reason in reasons)


def test_gate1_accepts_half_life_ratio_exactly_two():
    reasons, _ = evaluate_gate1(
        theta=0.06,
        theta_ci=(0.05, 0.07),
        t_half_ci=(9.9, 19.8),  # отношение ровно 2.0
        method=REG,
        variance=_variance(0.06),
        band=_band(),
    )

    assert reasons == ()


def test_gate1_rejects_acf_below_band():
    reasons, _ = evaluate_gate1(
        theta=0.012,
        theta_ci=(0.008, 0.015),
        t_half_ci=(46.0, 87.0),
        method=REG,
        variance=_variance(0.06),
        band=_band(acf_upper=0.015),
    )

    assert any("полосы случайного блуждания" in reason for reason in reasons)


def test_gate1_compares_plateau_with_plateau_band():
    """Для метода PLATEAU сравнение идёт с полосой плато, а не интерсепта."""
    reasons, _ = evaluate_gate1(
        theta=0.05,
        theta_ci=(0.04, 0.06),
        t_half_ci=(11.5, 17.3),
        method=PLATEAU,
        variance=_variance(0.05),
        band=_band(acf_upper=0.01, plateau_upper=0.06),
    )

    assert any("полосы случайного блуждания" in reason for reason in reasons)


def test_gate1_rejects_variance_below_band():
    reasons, _ = evaluate_gate1(
        theta=0.06,
        theta_ci=(0.05, 0.07),
        t_half_ci=(9.9, 13.0),
        method=REG,
        variance=_variance(0.0005),
        band=_band(var_upper=0.001),
    )

    assert any("дисперсионная" in reason for reason in reasons)


def test_gate1_rejects_unidentified_variance_theta():
    reasons, flags = evaluate_gate1(
        theta=0.06,
        theta_ci=(0.05, 0.07),
        t_half_ci=(9.9, 13.0),
        method=REG,
        variance=_variance(1e-5, identified=False),
        band=_band(),
    )

    assert any("не идентифицирована" in reason for reason in reasons)
    assert "variance_theta" in flags


def test_gate1_rejects_missing_band_components():
    band = NullBand(
        acf_intercept=None,
        acf_plateau=None,
        variance=None,
        replications=100,
        sessions=30,
    )

    reasons, _ = evaluate_gate1(
        theta=0.06,
        theta_ci=(0.05, 0.07),
        t_half_ci=(9.9, 13.0),
        method=REG,
        variance=_variance(0.06),
        band=band,
    )

    assert any("не построена" in reason for reason in reasons)


def test_gate1_flags_disagreeing_estimates():
    """Расхождение оценок — диагностика, а не блокер: у обеих есть известное смещение."""
    reasons, flags = evaluate_gate1(
        theta=0.05,
        theta_ci=(0.04, 0.06),
        t_half_ci=(11.0, 17.0),
        method=REG,
        variance=_variance(0.002),
        band=_band(),
    )

    assert reasons == ()
    assert "estimators" in flags


def test_gate1_flags_small_variance_margin():
    _, flags = evaluate_gate1(
        theta=0.05,
        theta_ci=(0.04, 0.06),
        t_half_ci=(11.0, 17.0),
        method=REG,
        variance=_variance(0.0011),
        band=_band(var_upper=0.001),
    )

    assert "variance_margin" in flags


def test_gate1_accepts_consistent_estimates():
    reasons, flags = evaluate_gate1(
        theta=0.06,
        theta_ci=(0.05, 0.07),
        t_half_ci=(9.0, 13.0),
        method=REG,
        variance=_variance(0.05),
        band=_band(),
    )

    assert reasons == ()
    assert flags == {}


def test_select_estimate_uses_plateau_when_slope_is_flat():
    """Ветка fallback: наклон неотличим от нуля и хвост θ̂(Δ) устойчив."""
    points = tuple(
        ThetaPoint(delta=delta, rho=0.9, theta=0.05, pairs=1000) for delta in (1, 2, 3, 5, 10, 15, 30)
    )

    theta, method, ci = _select_estimate(
        points=points,
        slope_ci=(-0.001, 0.001),
        intercept=0.05,
        intercepts=[0.049, 0.051],
        plateaus=[0.0495, 0.0505],
    )

    assert method == PLATEAU
    assert theta == pytest.approx(0.05)
    assert ci[0] <= theta <= ci[1]


def test_select_estimate_uses_intercept_when_slope_is_significant():
    points = tuple(
        ThetaPoint(delta=delta, rho=0.9, theta=0.05 + 1.0 / delta, pairs=1000)
        for delta in (1, 2, 3, 5, 10, 15, 30)
    )

    theta, method, _ = _select_estimate(
        points=points,
        slope_ci=(0.8, 1.2),
        intercept=0.05,
        intercepts=[0.049, 0.051],
        plateaus=[0.12, 0.13],
    )

    assert method == REG
    assert theta == pytest.approx(0.05)


def test_select_estimate_reports_plateau_method_flag():
    fit = _fit()

    assert fit.method in (PLATEAU, REG)
    if fit.method == PLATEAU:
        assert fit.flags["method"].startswith("PLATEAU")


def test_fit_ou_parameters():
    bars = _ou_bars(0.05, sigma=0.002)
    fit = estimate_theta(bars, _schedule(), bootstrap=20, null_reps=60)

    ou = fit_ou("TEST", bars, _schedule(), fit, registry_id="trial-1")

    assert ou.sigma > 0
    assert ou.sigma_p == pytest.approx(ou.sigma / sqrt(2 * fit.theta_used))
    assert ou.sigma_p == pytest.approx(sqrt(fit.variance.amplitude / 2))
    assert ou.sigma_naive is not None and ou.sigma_naive > 0
    assert 0 < ou.attenuation <= 1
    assert all(value < 0 for value in ou.rho_model.values())
    assert ou.registry_id == "trial-1"
    assert ou.to_dict()["registry_id"] == "trial-1"
    assert isinstance(ou, OuFit)


def test_fit_ou_requires_gate1_pass():
    import dataclasses

    bars = _ou_bars(0.05, sigma=0.002)
    fit = estimate_theta(bars, _schedule(), bootstrap=20, null_reps=50)
    blocked = dataclasses.replace(fit, reasons=("Gate 1 не пройден в тесте",))

    with pytest.raises(ThetaError):
        fit_ou("TEST", bars, _schedule(), blocked)


def test_zero_theta_rejected_for_ou_parameters():
    bars = _ou_bars(0.05, sigma=0.002)
    zero = ThetaFit(
        theta=0.0,
        slope=0.0,
        noise_ratio=0.0,
        intercept_ci=(-0.01, 0.01),
        slope_ci=(-1.0, 1.0),
        method=REG,
        points=(),
    )

    with pytest.raises(ThetaError):
        fit_ou("TEST", bars, _schedule(), zero)


def test_aggregate_verdict_requires_single_pass():
    assert aggregate_verdict(["PASS", UNDEFINED]) == "PASS"
    assert aggregate_verdict([UNDEFINED]) == UNDEFINED
    with pytest.raises(ThetaError):
        aggregate_verdict([])


def test_log_scale_sanity():
    fit = _fit()

    assert 0 < fit.theta < 1
    assert 0 < fit.t_half < 1000
    assert log(2) / fit.theta == pytest.approx(fit.t_half)
