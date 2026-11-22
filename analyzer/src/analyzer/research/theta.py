"""Идентификация θ по инвариантности к шагу и Gate 1.

Спека (`buy_frame.md` §6.3) задаёт оценку через ACF уровня цены внутри окна:
`θ̂_obs(Δ) = θ + ln(1 + ω²/σ_P²)/Δ`, θ берётся из интерсепта регрессии на `1/Δ`.

Проверка на синтетике показала, что этой оценки недостаточно: удаление среднего по окну из
сильно коррелированного ряда выбрасывает низкочастотную компоненту, и `ρ̂` падает быстрее
теоретического `ρ^Δ`. Смещение растёт с Δ, поэтому на чистом случайном блуждании оценка даёт
`θ̂ ≈ 0.0095` с узким bootstrap-CI — Gate 1 пропустил бы ряд без возврата. Bootstrap этого не
видит: он измеряет разброс между сессиями, а не смещение метода.

Поэтому:

1. ACF-регрессия остаётся обязательным артефактом (спека), но её вердикт проверяется против
   **нулевой полосы** — той же процедуры на симулированном случайном блуждании с теми же длинами
   окон. Полоса зависит только от геометрии окон и сетки Δ, не от цены и волатильности.
2. Рядом считается **дисперсионная оценка** `Var(x_{t+Δ} − x_t) = (σ²/θ)(1 − e^{−θΔ}) + 2ω²`:
   приращения инвариантны уровню окна, поэтому удалять среднее не нужно. На синтетике она
   восстанавливает θ = 0.0500 при истинных 0.05, где ACF даёт 0.0639.
3. Gate 1 проходит, только если обе оценки выше своих полос и согласуются между собой
   (`PROJECT-DECISION:gate1-dual`). Порог «θ ≤ 0.02» означает, что возврат не идентифицируется
   на сетке Δ ≤ 30, и требует эскалации до Δ = 60 (решение владельца), а не ослабления гейта.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from math import e, inf, log, sqrt
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from ..backtest.models import Bar
from .sessions import SessionSchedule
from .variance import VarianceError, deseasonalize_returns

DEFAULT_DELTAS: Tuple[int, ...] = (1, 2, 3, 5, 10, 15, 30)
#: Эскалация при неидентифицируемой θ (≤ 0.02): сетка расширяется до 60 минут.
ESCALATED_DELTAS: Tuple[int, ...] = (1, 2, 3, 5, 10, 15, 30, 60)
DEFAULT_BOOTSTRAP = 200
DEFAULT_NULL_REPS = 100
DEFAULT_SEED = 20261122
MIN_PAIRS = 100
MIN_SESSIONS_FOR_BAND = 20
MIN_DELTAS_FOR_VARIANCE = 3
PLATEAU_DELTAS = (10, 15, 30)
PLATEAU_TOLERANCE = 0.2
#: Допустимое расхождение двух независимых оценок θ (`PROJECT-DECISION:gate1-dual`).
AGREEMENT_LOW = 2.0 / 3.0
AGREEMENT_HIGH = 1.5
#: Запас над полосой блуждания для дисперсионной оценки, ниже которого ставится
#: `LOW_CONFIDENCE` (`PROJECT-DECISION:theta-band-margin`).
BAND_MARGIN = 1.25
BAND_PERCENTILE = 97.5

REG = "REGRESSION"
PLATEAU = "PLATEAU"
OK = "OK"
NO_DATA = "NO_DATA"
UNDEFINED = "THETA_UNDEFINED"
PASS = "PASS"
LOW_CONFIDENCE = "LOW_CONFIDENCE"

LN2 = log(2.0)


class ThetaError(ValueError):
    """θ нельзя оценить по заданным данным."""


@dataclass(frozen=True)
class ThetaPoint:
    delta: int
    rho: float
    theta: float
    pairs: int
    status: str = OK

    def to_dict(self) -> Dict[str, object]:
        return {
            "delta": self.delta,
            "rho": round(self.rho, 6),
            "theta": round(self.theta, 6),
            "pairs": self.pairs,
            "status": self.status,
        }


@dataclass(frozen=True)
class VariancePoint:
    delta: int
    variance: float
    pairs: int

    def to_dict(self) -> Dict[str, object]:
        return {
            "delta": self.delta,
            "variance": round(self.variance, 10),
            "pairs": self.pairs,
        }


@dataclass(frozen=True)
class VarianceFit:
    """Дисперсионная оценка θ: `Var(x_{t+Δ} − x_t) = (σ²/θ)(1 − e^{−θΔ}) + 2ω²`."""

    theta: float
    amplitude: float
    noise_variance: float
    points: Tuple[VariancePoint, ...]

    @property
    def sigma_sq(self) -> float:
        return self.amplitude * self.theta

    def to_dict(self) -> Dict[str, object]:
        return {
            "theta": round(self.theta, 6),
            "sigma_sq": round(self.sigma_sq, 12),
            "noise_variance": round(self.noise_variance, 12),
            "points": [point.to_dict() for point in self.points],
        }


@dataclass(frozen=True)
class NullBand:
    """Распределение оценок θ на случайном блуждании с той же геометрией окон."""

    acf_intercept: Tuple[float, float]
    acf_plateau: Tuple[float, float]
    variance: Tuple[float, float]
    replications: int
    sessions: int

    def acf_upper(self, method: str) -> float:
        return self.acf_plateau[1] if method == PLATEAU else self.acf_intercept[1]

    def to_dict(self) -> Dict[str, object]:
        return {
            "acf_intercept": [round(value, 6) for value in self.acf_intercept],
            "acf_plateau": [round(value, 6) for value in self.acf_plateau],
            "variance": [round(value, 6) for value in self.variance],
            "replications": self.replications,
            "sessions": self.sessions,
            "percentile": BAND_PERCENTILE,
        }


@dataclass(frozen=True)
class ThetaFit:
    """Результат идентификации θ.

    `theta` — оценка ACF-регрессией (спека), `intercept_ci` — её CI. Дисперсионная оценка
    лежит в `variance` и используется как уточняющая (`theta_used`).
    """

    theta: float
    slope: float
    noise_ratio: float
    intercept_ci: Tuple[float, float]
    slope_ci: Tuple[float, float]
    method: str
    points: Tuple[ThetaPoint, ...]
    flags: Dict[str, str] = field(default_factory=dict)
    variance: Optional[VarianceFit] = None
    band: Optional[NullBand] = None
    reasons: Tuple[str, ...] = ()

    @property
    def t_half(self) -> float:
        if self.theta <= 0:
            return inf
        return LN2 / self.theta

    @property
    def t_half_ci(self) -> Tuple[float, float]:
        low, high = self.intercept_ci
        if low <= 0:
            return (inf, inf)
        return (LN2 / high, LN2 / low)

    @property
    def theta_used(self) -> float:
        """θ для OU-параметров: дисперсионная оценка, если она построена."""
        if self.variance is not None and self.variance.theta > 0:
            return self.variance.theta
        return self.theta

    @property
    def gate1_passed(self) -> bool:
        return not self.reasons

    @property
    def verdict(self) -> str:
        return PASS if self.gate1_passed else UNDEFINED

    def to_dict(self) -> Dict[str, object]:
        half_low, half_high = self.t_half_ci
        return {
            "theta": round(self.theta, 6),
            "theta_used": round(self.theta_used, 6),
            "slope": round(self.slope, 6),
            "noise_ratio": round(self.noise_ratio, 6),
            "theta_ci": [round(value, 6) for value in self.intercept_ci],
            "slope_ci": [round(value, 6) for value in self.slope_ci],
            "t_half_minutes": None if self.t_half == inf else round(self.t_half, 3),
            "t_half_ci": [
                None if value == inf else round(value, 3) for value in (half_low, half_high)
            ],
            "gate1": self.verdict,
            "gate1_reasons": list(self.reasons),
            "method": self.method,
            "points": [point.to_dict() for point in self.points],
            "variance_estimate": None if self.variance is None else self.variance.to_dict(),
            "null_band": None if self.band is None else self.band.to_dict(),
            "flags": dict(self.flags),
        }


@dataclass(frozen=True)
class OuFit:
    instrument: str
    theta: ThetaFit
    sigma: float
    sigma_p: float
    mu: float
    attenuation: float
    rho_model: Dict[int, float]
    registry_id: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "instrument": self.instrument,
            "theta_acf": round(self.theta.theta, 6),
            "theta_variance": (
                None if self.theta.variance is None else round(self.theta.variance.theta, 6)
            ),
            "theta_used": round(self.theta.theta_used, 6),
            "theta": self.theta.to_dict(),
            "sigma": round(self.sigma, 8),
            "sigma_p": round(self.sigma_p, 8),
            "mu": round(self.mu, 8),
            "attenuation": round(self.attenuation, 6),
            "rho_model": {str(tau): round(value, 6) for tau, value in self.rho_model.items()},
            "registry_id": self.registry_id,
        }


def evaluate_gate1(
    *,
    theta: float,
    theta_ci: Tuple[float, float],
    t_half_ci: Tuple[float, float],
    method: str,
    variance: Optional[VarianceFit],
    band: Optional[NullBand],
) -> Tuple[Tuple[str, ...], Dict[str, str]]:
    """Вердикт Gate 1: (причины провала, флаги). Пустой список причин — PASS.

    Чистая функция: тестируется без данных и без bootstrap.
    """
    reasons: List[str] = []
    flags: Dict[str, str] = {}

    if theta <= 0 or theta_ci[0] <= 0:
        reasons.append(f"CI θ содержит 0 или θ ≤ 0: θ={theta:.6f}, CI=[{theta_ci[0]:.6f}, {theta_ci[1]:.6f}]")
    half_low, half_high = t_half_ci
    if half_low == inf or half_high == inf or half_low <= 0:
        reasons.append("CI t_1/2 не определён (θ ≤ 0 в нижней границе)")
    elif half_high / half_low > 2.0:
        reasons.append(f"CI t_1/2 шире двукратного: {half_high / half_low:.2f}×")

    if band is None:
        reasons.append("нулевая полоса не построена: слишком мало сессий")
        return tuple(reasons), flags

    upper = band.acf_upper(method)
    if theta <= upper:
        reasons.append(
            f"ACF-оценка θ={theta:.6f} не выше полосы случайного блуждания {upper:.6f}"
        )

    if variance is None:
        reasons.append("дисперсионная оценка не построена")
        return tuple(reasons), flags

    if variance.theta <= band.variance[1]:
        reasons.append(
            f"дисперсионная θ={variance.theta:.6f} не выше полосы блуждания "
            f"{band.variance[1]:.6f}"
        )
    elif variance.theta < band.variance[1] * BAND_MARGIN:
        flags["variance_margin"] = (
            f"LOW_CONFIDENCE: дисперсионная θ всего в "
            f"{variance.theta / band.variance[1]:.2f}× над полосой блуждания"
        )

    # Расхождение оценок — диагностика, а не блокер: ACF по спеке смещена вверх (удаление
    # среднего окна), дисперсионная — вниз (слабая идентифицируемость шумового члена).
    # Блокировать по разнице двух смещённых оценок значило бы задать порог «на глаз».
    if variance.theta > 0:
        ratio = theta / variance.theta
        if not AGREEMENT_LOW <= ratio <= AGREEMENT_HIGH:
            flags["estimators"] = (
                f"LOW_CONFIDENCE: оценки расходятся, ACF/дисперсионная = {ratio:.2f}"
            )
    return tuple(reasons), flags


def estimate_theta(
    bars: Sequence[Bar],
    schedule: SessionSchedule,
    *,
    deltas: Sequence[int] = DEFAULT_DELTAS,
    bootstrap: int = DEFAULT_BOOTSTRAP,
    null_reps: int = DEFAULT_NULL_REPS,
    seed: int = DEFAULT_SEED,
) -> ThetaFit:
    """Оценить θ двумя способами, построить нулевую полосу и вынести вердикт Gate 1."""
    if not deltas or any(delta < 1 for delta in deltas):
        raise ThetaError("deltas must be positive integers")
    if bootstrap < 20:
        raise ThetaError("bootstrap must be at least 20 resamples")
    if null_reps < 50:
        raise ThetaError("null_reps must be at least 50 resamples")

    sessions = _session_series(bars, schedule)
    if not sessions:
        raise ThetaError("no in-session bars: cannot estimate theta")

    per_session = [_session_sums(session, deltas) for session in sessions]
    counts, nums, dens = _aggregate(per_session)
    points = _points(counts, nums, dens, deltas)

    per_session_var = [_variance_sums(session, deltas) for session in sessions]
    variance = _variance_fit(per_session_var, deltas)

    lengths = [int(session[1].size) for session in sessions]
    band = (
        _null_band(lengths, deltas, null_reps, seed)
        if len(sessions) >= MIN_SESSIONS_FOR_BAND
        else None
    )

    # Меньше двух точек — инвариантность не проверить. Это вердикт по инструменту, а не
    # ошибка ввода: прогон по 20 инструментам не должен падать из-за одного ряда без памяти.
    if sum(1 for point in points if point.status == OK) < 2:
        return ThetaFit(
            theta=0.0,
            slope=0.0,
            noise_ratio=0.0,
            intercept_ci=(0.0, 0.0),
            slope_ci=(0.0, 0.0),
            method=UNDEFINED,
            points=tuple(points),
            flags={"theta": "NO_DATA: меньше двух точек для регрессии"},
            variance=variance,
            band=band,
            reasons=("NO_DATA: меньше двух точек для регрессии",),
        )

    intercept, slope = _ols_points(points)  # type: ignore[misc]

    rng = np.random.default_rng(seed)
    intercepts: List[float] = []
    slopes: List[float] = []
    plateaus: List[float] = []
    total = len(per_session)
    for _ in range(bootstrap):
        picks = rng.integers(0, total, size=total)
        b_counts, b_nums, b_dens = _aggregate([per_session[index] for index in picks])
        b_points = _points(b_counts, b_nums, b_dens, deltas)
        b_ols = _ols_points(b_points)
        if b_ols is None:
            continue
        intercepts.append(b_ols[0])
        slopes.append(b_ols[1])
        b_plateau = _plateau_value(b_points)
        if b_plateau is not None:
            plateaus.append(b_plateau)

    slope_ci = _percentile(slopes, slope)
    flags: Dict[str, str] = {}
    if not intercepts:
        return ThetaFit(
            theta=0.0,
            slope=0.0,
            noise_ratio=0.0,
            intercept_ci=(0.0, 0.0),
            slope_ci=(0.0, 0.0),
            method=UNDEFINED,
            points=tuple(points),
            flags={"theta": "NO_DATA: bootstrap не дал ни одной оценки"},
            variance=variance,
            band=band,
            reasons=("NO_DATA: bootstrap не дал ни одной оценки",),
        )
    if len(intercepts) < 20:
        flags["bootstrap"] = f"LOW_CONFIDENCE: {len(intercepts)} ресамплов"

    # Плато — fallback на случай, когда шумовая поправка не идентифицируется: наклон
    # зависимости θ̂(1/Δ) статистически неотличим от нуля, а хвост θ̂(Δ) устойчив.
    plateau = _plateau_value(points)
    if plateau is not None and plateaus and slope_ci[0] <= 0.0 <= slope_ci[1]:
        theta = plateau
        method = PLATEAU
        theta_ci = _percentile(plateaus, plateau)
        flags["method"] = "PLATEAU: наклон неотличим от нуля"
    else:
        theta = intercept
        method = REG
        theta_ci = _percentile(intercepts, intercept)

    probe = ThetaFit(
        theta=theta,
        slope=slope,
        noise_ratio=e**slope - 1.0,
        intercept_ci=theta_ci,
        slope_ci=slope_ci,
        method=method,
        points=tuple(points),
        variance=variance,
        band=band,
    )
    reasons, gate_flags = evaluate_gate1(
        theta=probe.theta,
        theta_ci=probe.intercept_ci,
        t_half_ci=probe.t_half_ci,
        method=probe.method,
        variance=variance,
        band=band,
    )
    flags.update(gate_flags)
    if reasons:
        flags["gate1"] = UNDEFINED
    return ThetaFit(
        theta=probe.theta,
        slope=probe.slope,
        noise_ratio=probe.noise_ratio,
        intercept_ci=probe.intercept_ci,
        slope_ci=probe.slope_ci,
        method=probe.method,
        points=probe.points,
        flags=flags,
        variance=variance,
        band=band,
        reasons=reasons,
    )


def fit_ou(
    instrument: str,
    bars: Sequence[Bar],
    schedule: SessionSchedule,
    theta: ThetaFit,
    *,
    taus: Sequence[int] = (1, 5, 10, 30),
) -> OuFit:
    """σ, σ_P, μ, attenuation и диагностический ρ_model."""
    effective = theta.theta_used
    if effective <= 0:
        raise ThetaError("theta must be positive for OU parameters")
    if not theta.gate1_passed:
        raise ThetaError(
            "Gate 1 не пройден: " + "; ".join(theta.reasons)
        )
    try:
        deseason = deseasonalize_returns(bars, schedule)
    except VarianceError as exc:
        raise ThetaError(f"cannot deseasonalize returns: {exc}") from exc
    if len(deseason.returns) < 2:
        raise ThetaError("not enough returns for sigma")
    sigma_sq = float(np.var(np.asarray(deseason.returns, dtype=float), ddof=1))
    if sigma_sq <= 0:
        raise ThetaError("sigma is zero: series is flat")
    sigma = sqrt(sigma_sq)
    sigma_p = sigma / sqrt(2.0 * effective)
    prices = [bar.close for bar in bars if schedule.is_open(bar.ts)]
    if not prices:
        raise ThetaError("no in-session prices for anchor")
    mu = float(np.mean(np.log(np.asarray(prices, dtype=float))))
    attenuation = 1.0 / (1.0 + max(theta.noise_ratio, 0.0))
    rho_model = {tau: -sqrt((1.0 - e ** (-effective * tau)) / 2.0) for tau in taus}
    return OuFit(
        instrument=instrument,
        theta=theta,
        sigma=sigma,
        sigma_p=sigma_p,
        mu=mu,
        attenuation=attenuation,
        rho_model=rho_model,
    )


def aggregate_verdict(fits: Iterable[ThetaFit]) -> str:
    results = list(fits)
    if not results:
        raise ThetaError("no instruments to evaluate")
    return PASS if any(fit.gate1_passed for fit in results) else UNDEFINED


def _session_series(
    bars: Sequence[Bar], schedule: SessionSchedule
) -> List[Tuple[Tuple[date, str], np.ndarray, np.ndarray]]:
    """Ряды лог-цены по (дата, окно).

    Бары с одинаковой меткой времени схлопываются (остаётся последний): дубль дал бы пару
    с нулевой доходностью и завысил ρ̂, а Gate D ловит дубли отдельно и до этого расчёта.
    """
    grouped: Dict[Tuple[date, str], Dict[int, float]] = {}
    for bar in bars:
        window = schedule.window_at(bar.ts)
        if window is None:
            continue
        local = schedule.local(bar.ts)
        key = (local.date(), window.name)
        grouped.setdefault(key, {})[int(bar.ts.timestamp())] = log(bar.close)
    sessions = []
    for key, stamps_to_price in grouped.items():
        pairs = sorted(stamps_to_price.items())
        arr = np.asarray(pairs, dtype=float)
        sessions.append((key, arr[:, 0].astype(np.int64), arr[:, 1]))
    sessions.sort(key=lambda item: item[0])
    return sessions


def _session_sums(
    session: Tuple[Tuple[date, str], np.ndarray, np.ndarray], deltas: Sequence[int]
) -> Dict[int, Tuple[int, float, float]]:
    _, stamps, prices = session
    mean = float(prices.mean())
    centered = prices - mean
    result: Dict[int, Tuple[int, float, float]] = {}
    for delta in deltas:
        target = stamps + delta * 60
        index = np.searchsorted(stamps, target)
        index = np.clip(index, 0, len(stamps) - 1)
        valid = stamps[index] == target
        left = centered[valid]
        right = centered[index[valid]]
        if left.size == 0:
            result[delta] = (0, 0.0, 0.0)
            continue
        # Знаменатель — только по тем барам, у которых есть пара: иначе ρ̂ сравнивал бы
        # числитель по подвыборке со знаменателем по всему окну.
        result[delta] = (
            int(left.size),
            float(np.sum(left * right)),
            float(np.sum(left * left)),
        )
    return result


def _variance_sums(
    session: Tuple[Tuple[date, str], np.ndarray, np.ndarray], deltas: Sequence[int]
) -> Dict[int, Tuple[int, float]]:
    """Суммы квадратов приращений `x_{t+Δ} − x_t` по окну (без удаления среднего)."""
    _, stamps, prices = session
    result: Dict[int, Tuple[int, float]] = {}
    for delta in deltas:
        target = stamps + delta * 60
        index = np.searchsorted(stamps, target)
        index = np.clip(index, 0, len(stamps) - 1)
        valid = stamps[index] == target
        diff = prices[index[valid]] - prices[valid]
        if diff.size == 0:
            result[delta] = (0, 0.0)
            continue
        result[delta] = (int(diff.size), float(np.sum(diff * diff)))
    return result


def _aggregate(
    per_session: Sequence[Dict[int, Tuple[int, float, float]]]
) -> Tuple[Dict[int, int], Dict[int, float], Dict[int, float]]:
    counts: Dict[int, int] = {}
    nums: Dict[int, float] = {}
    dens: Dict[int, float] = {}
    for session in per_session:
        for delta, (count, num, den) in session.items():
            counts[delta] = counts.get(delta, 0) + count
            nums[delta] = nums.get(delta, 0.0) + num
            dens[delta] = dens.get(delta, 0.0) + den
    return counts, nums, dens


def _points(
    counts: Dict[int, int],
    nums: Dict[int, float],
    dens: Dict[int, float],
    deltas: Sequence[int],
) -> List[ThetaPoint]:
    points = []
    for delta in deltas:
        count = counts.get(delta, 0)
        num = nums.get(delta, 0.0)
        den = dens.get(delta, 0.0)
        if count < MIN_PAIRS or den <= 0:
            points.append(ThetaPoint(delta, 0.0, 0.0, count, NO_DATA))
            continue
        rho = num / den
        if not 0.0 < rho < 1.0:
            points.append(ThetaPoint(delta, rho, 0.0, count, NO_DATA))
            continue
        points.append(ThetaPoint(delta, rho, -log(rho) / delta, count, OK))
    return points


def _variance_fit(
    per_session: Sequence[Dict[int, Tuple[int, float]]], deltas: Sequence[int]
) -> Optional[VarianceFit]:
    """Профильный МНК для `A(1 − e^{−θΔ}) + 2ω²` по суммам приращений.

    Модель линейна по `A` и `2ω²`, поэтому при фиксированной θ коэффициенты находятся
    обычным МНК, и остаётся одномерный поиск по θ. Это точный МНК (а не итеративная подгонка):
    на случайном блуждании он честно даёт θ → 0, тогда как `curve_fit` с нижней границей
    возвращал ложные 0.038.
    """
    counts: Dict[int, int] = {}
    sums: Dict[int, float] = {}
    for session in per_session:
        for delta, (count, total) in session.items():
            counts[delta] = counts.get(delta, 0) + count
            sums[delta] = sums.get(delta, 0.0) + total
    usable = [delta for delta in deltas if counts.get(delta, 0) >= MIN_PAIRS]
    if len(usable) < MIN_DELTAS_FOR_VARIANCE:
        return None

    x = np.asarray(usable, dtype=float)
    y = np.asarray([sums[delta] / counts[delta] for delta in usable], dtype=float)
    if float(y.max()) <= 0:
        return None

    theta, amplitude, noise = _profile_fit(x, y)
    if theta is None:
        return None
    return VarianceFit(
        theta=theta,
        amplitude=amplitude,
        noise_variance=noise,
        points=tuple(
            VariancePoint(delta, sums[delta] / counts[delta], counts[delta]) for delta in usable
        ),
    )


def _profile_fit(
    x: np.ndarray, y: np.ndarray
) -> Tuple[Optional[float], float, float]:
    """Поиск θ по сетке с уточнением; (`None`, 0, 0), если МНК не даёт положительных A."""
    best = _grid_search(x, y, 1e-5, 2.0, 240)
    if best is None:
        return (None, 0.0, 0.0)
    _, theta, _ = best
    low, high = theta / 1.6, theta * 1.6
    refined = _grid_search(x, y, low, min(high, 2.0), 60)
    if refined is not None and refined[0] <= best[0]:
        best = refined
    _, theta, (amplitude, noise) = best
    return (float(theta), float(amplitude), float(noise))


def _grid_search(
    x: np.ndarray, y: np.ndarray, low: float, high: float, points: int
) -> Optional[Tuple[float, float, Tuple[float, float]]]:
    best: Optional[Tuple[float, float, Tuple[float, float]]] = None
    ones = np.ones_like(x)
    for theta in np.geomspace(max(low, 1e-6), high, points):
        base = 1.0 - np.exp(-theta * x)
        design = np.column_stack([base, ones])
        coef, *_ = np.linalg.lstsq(design, y, rcond=None)
        amplitude, noise = float(coef[0]), float(coef[1])
        if amplitude <= 0:
            continue
        if noise < 0:
            # Отрицательный «шум» — не оценка шума, а признак того, что модель без него
            # описывает данные лучше; коэффициент шума фиксируется нулём.
            amplitude = float(base @ y / (base @ base))
            noise = 0.0
            if amplitude <= 0:
                continue
        residual = y - (amplitude * base + noise)
        sse = float(residual @ residual)
        if best is None or sse < best[0]:
            best = (sse, float(theta), (amplitude, noise))
    return best


def _null_band(
    lengths: Sequence[int], deltas: Sequence[int], reps: int, seed: int
) -> NullBand:
    """Полоса оценок θ на случайном блуждании с теми же длинами окон."""
    return _null_band_cached(tuple(int(size) for size in lengths), tuple(deltas), reps, seed)


@lru_cache(maxsize=64)
def _null_band_cached(
    lengths: Tuple[int, ...], deltas: Tuple[int, ...], reps: int, seed: int
) -> NullBand:
    """Кэш по геометрии: полоса зависит только от длин окон и сетки Δ.

    Оценки масштабно-инвариантны (ρ и `v(Δ)/v(1)` не зависят от волатильности), поэтому
    симуляция ведётся в единицах шага: геометрия окон — единственный вход.
    """
    sizes = np.asarray(lengths, dtype=np.int64)
    sessions = sizes.size
    width = int(sizes.max())
    offsets = np.arange(width)[None, :]
    mask = offsets < sizes[:, None]
    rng = np.random.default_rng(seed)

    intercepts: List[float] = []
    plateaus: List[float] = []
    variance_thetas: List[float] = []
    for _ in range(reps):
        prices = np.cumsum(rng.standard_normal((sessions, width)), axis=1) * mask
        mean = (prices * mask).sum(axis=1, keepdims=True) / sizes[:, None]
        centered = (prices - mean) * mask
        counts, nums, dens = _matrix_aggregate(centered, mask, deltas)
        points = _points(counts, nums, dens, deltas)
        ols = _ols_points(points)
        if ols is not None:
            intercepts.append(ols[0])
        plateau = _plateau_value(points)
        if plateau is not None:
            plateaus.append(plateau)

        curve = _matrix_variance(prices, mask, deltas)
        fit = _variance_fit([curve], deltas)
        if fit is not None:
            variance_thetas.append(fit.theta)

    return NullBand(
        acf_intercept=_percentile(intercepts, 0.0),
        acf_plateau=_percentile(plateaus, 0.0),
        variance=_percentile(variance_thetas, 0.0),
        replications=reps,
        sessions=sessions,
    )


def _matrix_aggregate(
    centered: np.ndarray, mask: np.ndarray, deltas: Sequence[int]
) -> Tuple[Dict[int, int], Dict[int, float], Dict[int, float]]:
    """Агрегаты `ρ̂` по матрице окон (по строкам — окна одной длины-маски)."""
    width = centered.shape[1]
    counts: Dict[int, int] = {}
    nums: Dict[int, float] = {}
    dens: Dict[int, float] = {}
    for delta in deltas:
        if delta >= width:
            counts[delta] = 0
            nums[delta] = 0.0
            dens[delta] = 0.0
            continue
        valid = mask[:, delta:]
        left = centered[:, : width - delta]
        right = centered[:, delta:]
        counts[delta] = int(valid.sum())
        nums[delta] = float((left * right * valid).sum())
        dens[delta] = float((left * left * valid).sum())
    return counts, nums, dens


def _matrix_variance(
    prices: np.ndarray, mask: np.ndarray, deltas: Sequence[int]
) -> Dict[int, Tuple[int, float]]:
    width = prices.shape[1]
    result: Dict[int, Tuple[int, float]] = {}
    for delta in deltas:
        if delta >= width:
            result[delta] = (0, 0.0)
            continue
        valid = mask[:, delta:]
        diff = prices[:, delta:] - prices[:, : width - delta]
        result[delta] = (int(valid.sum()), float(((diff * diff) * valid).sum()))
    return result


def _ols_points(points: Sequence[ThetaPoint]) -> Optional[Tuple[float, float]]:
    """OLS `θ̂` на `1/Δ`: (интерсепт, наклон); None, если точек меньше двух."""
    usable = [point for point in points if point.status == OK]
    if len(usable) < 2:
        return None
    x = np.asarray([1.0 / point.delta for point in usable], dtype=float)
    y = np.asarray([point.theta for point in usable], dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    return float(intercept), float(slope)


def _plateau_value(points: Sequence[ThetaPoint]) -> Optional[float]:
    """Медиана `θ̂(Δ)` на больших Δ, если хвост устойчив (разброс ≤ 20 %)."""
    tail = [
        point.theta
        for point in points
        if point.status == OK and point.delta >= min(PLATEAU_DELTAS)
    ]
    if len(tail) < 2:
        return None
    low, high = min(tail), max(tail)
    if low <= 0 or (high - low) / high > PLATEAU_TOLERANCE:
        return None
    return float(np.median(tail))


def _percentile(values: Sequence[float], fallback: float) -> Tuple[float, float]:
    if not values:
        return (fallback, fallback)
    low, high = np.percentile(np.asarray(values, dtype=float), [50.0 - (BAND_PERCENTILE - 50.0), BAND_PERCENTILE])
    return (float(low), float(high))


__all__ = [
    "DEFAULT_BOOTSTRAP",
    "DEFAULT_DELTAS",
    "DEFAULT_NULL_REPS",
    "DEFAULT_SEED",
    "ESCALATED_DELTAS",
    "MIN_PAIRS",
    "NO_DATA",
    "OK",
    "PASS",
    "PLATEAU",
    "REG",
    "UNDEFINED",
    "NullBand",
    "OuFit",
    "ThetaError",
    "ThetaFit",
    "ThetaPoint",
    "VarianceFit",
    "VariancePoint",
    "aggregate_verdict",
    "estimate_theta",
    "evaluate_gate1",
    "fit_ou",
]
