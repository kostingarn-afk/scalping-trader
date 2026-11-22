"""Удержание позиции: правило, онлайн-тест валидности и таблица выходов.

`holding_exiting.md` §7: `Hold ⇔ m_rem − λ·s_rem > E[C_later] − C_now`, где `m_rem, s_rem` —
условные ожидание и СКО **остаточного** движения при барьерах, посчитанные тем же протоколом,
что и `EV` (эмпирика по 1m, конвенция «SL первым»). Безбарьерная форма §7 остаётся
`REFERENCE_ONLY`: в решении она не участвует, иначе подмена протокола была бы незаметной.

`A_t ≤ 0` — выход (overshoot): остаточный edge против позиции, продление в MR-режиме запрещено.

§8: нижняя полоса `A_0^LCB·e^{−θ_LCB t} − z_{1−α_check}·√(1 − e^{−2θ_UCB t})`,
`α_check = α_total/n_checks`, `n_checks = τ_max/Δt`. Пробой полосы — выход по политике;
это тест модели, а не стоп-лосс.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .first_passage import NEUTRAL, SL_WIN, TP_WIN, resolve_path
from .signals import SessionArrays

#: Порог CUSUM для триггера `regime_breaker`. Объявлен, не подбирался: при |CUSUM| > 4
#: накопленное отклонение доходностей от нуля считается сменой характера движения.
CUSUM_THRESHOLD = 4.0
#: Прореживание баров при оценке остаточных моментов: полный перебор слишком дорог,
#: а прореживание смещает только дисперсию оценки, не её центр.
DEFAULT_STRIDE = 5
DEFAULT_MIN_PATHS = 30

KIND_HARD = "жёсткий"
KIND_REDUCTION = "редукция"
KIND_REGULAR = "штатный"
KIND_COST = "по §7"


class HoldingError(ValueError):
    """Правило удержания нельзя применить к заданному состоянию."""


@dataclass(frozen=True)
class TriggerSpec:
    """Триггер выхода из таблицы §9."""

    name: str
    kind: str
    reasons: Tuple[str, ...]
    description: str
    #: Где проверяется: `BAR` — на каждом баре удержания, `ENTRY` — только при входе.
    scope: str = "BAR"

    def to_dict(self) -> Dict[str, object]:
        return {
            "trigger": self.name,
            "kind": self.kind,
            "reasons": list(self.reasons),
            "scope": self.scope,
            "description": self.description,
        }


#: Одиннадцать триггеров §9. `TP` и `SL` — разные причины в журнале, но одна строка таблицы.
EXIT_TRIGGERS: Tuple[TriggerSpec, ...] = (
    TriggerSpec("anchor_edge", KIND_REGULAR, ("ANCHOR_EDGE",),
                "A_t не выше порога §7 — остаточного edge нет"),
    TriggerSpec("overshoot", KIND_REGULAR, ("OVERSHOOT",),
                "A_t ≤ 0 — цена прошла якорь, edge против позиции"),
    TriggerSpec("tp_sl", KIND_HARD, ("TP", "SL"),
                "касание барьера; в одном баре побеждает SL"),
    TriggerSpec("time_stop", KIND_HARD, ("TIME_STOP",),
                "t ≥ τ выбранного горизонта"),
    TriggerSpec("model_invalid", KIND_HARD, ("MODEL_INVALID",),
                "A_t ниже нижней полосы §8 — модель не описывает движение"),
    TriggerSpec("regime_breaker", KIND_HARD, ("REGIME_BREAKER",),
                "CUSUM квадратов центрированных доходностей пробил порог — смена дисперсии"),
    TriggerSpec("regime_confidence", KIND_HARD, ("REGIME_CONFIDENCE",),
                "γ_t(fav) < 1−q — вход запрещён; γ из статичной матрицы, проверка при входе",
                scope="ENTRY"),
    TriggerSpec("mtf_confirmation", KIND_REDUCTION, ("MTF_DISAGREEMENT",),
                "состояния 10m и 15m расходятся — редукция τ_max / no-trade",
                scope="DIAGNOSTIC"),
    TriggerSpec("vol_spike", KIND_REDUCTION, ("VOL_SPIKE",),
                "выброс range-based σ — пересчёт τ_max"),
    TriggerSpec("close_cost_drift", KIND_COST, ("CLOSE_COST_DRIFT",),
                "E[C_later] − C_now вырос: держать дороже, чем закрыть"),
    TriggerSpec("session", KIND_HARD, ("SESSION",),
                "τ_session меньше допустимого — позиция не успеет закрыться"),
)

CLOSING_TRIGGERS: Tuple[str, ...] = tuple(
    spec.name for spec in EXIT_TRIGGERS if spec.kind != KIND_REDUCTION
)
REDUCING_TRIGGERS: Tuple[str, ...] = tuple(
    spec.name for spec in EXIT_TRIGGERS if spec.kind == KIND_REDUCTION
)


@dataclass(frozen=True)
class OnlineBand:
    """Точка нижней полосы валидности модели."""

    minutes: int
    lower: float
    mean: float
    alpha_check: float
    checks: int
    z_value: float

    def to_dict(self) -> Dict[str, object]:
        return {
            "minutes": self.minutes,
            "lower": round(self.lower, 6),
            "mean": round(self.mean, 6),
            "alpha_check": round(self.alpha_check, 8),
            "checks": self.checks,
            "z_value": round(self.z_value, 6),
        }


@dataclass(frozen=True)
class RemainingMoments:
    """Остаточное движение при барьерах: `m_rem`, `s_rem` и вероятности исходов."""

    m_rem: float
    s_rem: float
    p_tp: float
    p_sl: float
    p_n: float
    paths: int
    to_tp: int
    to_sl: int
    tau_rem: int
    stride: int
    unconditional: bool = True

    def to_dict(self) -> Dict[str, object]:
        return {
            "m_rem": round(self.m_rem, 6),
            "s_rem": round(self.s_rem, 6),
            "p_tp": round(self.p_tp, 6),
            "p_sl": round(self.p_sl, 6),
            "p_n": round(self.p_n, 6),
            "paths": self.paths,
            "to_tp": self.to_tp,
            "to_sl": self.to_sl,
            "tau_rem": self.tau_rem,
            "stride": self.stride,
            "unconditional": self.unconditional,
            "units": {"m_rem": "ticks", "s_rem": "ticks"},
        }


@dataclass(frozen=True)
class HoldDecision:
    """Решение «держать/закрыть» с причиной и слагаемыми правила."""

    hold: bool
    reason: str
    m_rem: Optional[float]
    risk_term: Optional[float]
    cost_drift: float
    a_t: float
    moments: Optional[RemainingMoments]

    def to_dict(self) -> Dict[str, object]:
        return {
            "hold": self.hold,
            "reason": self.reason,
            "m_rem": None if self.m_rem is None else round(self.m_rem, 6),
            "risk_term": None if self.risk_term is None else round(self.risk_term, 6),
            "cost_drift": round(self.cost_drift, 6),
            "a_t": round(self.a_t, 6),
            "moments": None if self.moments is None else self.moments.to_dict(),
        }


def a_coordinate(price: float, anchor: float, sigma_p: float, direction: int) -> float:
    """`A_t = −s·(P_t − μ)/σ_P`: > 0 — остаточный edge есть, ≤ 0 — edge исчерпан."""
    if sigma_p <= 0:
        raise HoldingError("sigma_p must be positive")
    if direction not in (1, -1):
        raise HoldingError("direction must be +1 or -1")
    return -direction * (price - anchor) / sigma_p


def a0_bounds(
    price: float,
    anchor: float,
    *,
    sigma_p: float,
    sigma_p_lcb: float,
    sigma_p_ucb: float,
    direction: int,
) -> Tuple[float, float]:
    """`A_0` и его нижняя граница: минимум по интервалу `σ_P` (CI пропагируется в `A_0`)."""
    point = a_coordinate(price, anchor, sigma_p, direction)
    lower = min(
        a_coordinate(price, anchor, sigma_p_lcb, direction),
        a_coordinate(price, anchor, sigma_p_ucb, direction),
    )
    return point, min(point, lower)


def band_checks(
    tau_max: int, alpha_total: float, step_minutes: int = 1
) -> Tuple[int, float, float]:
    """`n_checks = τ_max/Δt`, `α_check = α_total/n_checks` и `z_{1−α_check}` (Бонферрони)."""
    if step_minutes < 1:
        raise HoldingError("step_minutes must be at least 1")
    if not 0.0 < alpha_total < 1.0:
        raise HoldingError(f"alpha_total must be in (0, 1), got {alpha_total}")
    if tau_max < 1:
        raise HoldingError("tau_max must be at least 1")
    from scipy.stats import norm

    checks = max(1, int(math.ceil(tau_max / step_minutes)))
    alpha_check = alpha_total / checks
    return checks, alpha_check, float(norm.ppf(1.0 - alpha_check))


def online_band(
    *,
    a0_lcb: float,
    theta_lcb: float,
    theta_ucb: float,
    minutes: int,
    tau_max: int,
    alpha_total: float,
    step_minutes: int = 1,
) -> OnlineBand:
    """Нижняя полоса §8 на горизонте `minutes` от входа."""
    if minutes < 0:
        raise HoldingError("minutes must be non-negative")
    if theta_lcb <= 0 or theta_ucb <= 0 or theta_lcb > theta_ucb:
        raise HoldingError("theta bounds must satisfy 0 < lcb <= ucb")
    checks, alpha_check, z_value = band_checks(tau_max, alpha_total, step_minutes)

    mean = a0_lcb * math.exp(-theta_lcb * minutes)
    spread = math.sqrt(max(0.0, 1.0 - math.exp(-2.0 * theta_ucb * minutes)))
    return OnlineBand(
        minutes=minutes,
        lower=mean - z_value * spread,
        mean=mean,
        alpha_check=alpha_check,
        checks=checks,
        z_value=z_value,
    )


def remaining_moments(
    sessions: Sequence[SessionArrays],
    *,
    to_tp: int,
    to_sl: int,
    tau_rem: int,
    tick_value: float,
    stride: int = DEFAULT_STRIDE,
    min_paths: int = DEFAULT_MIN_PATHS,
    until_ts: Optional[int] = None,
) -> Optional[RemainingMoments]:
    """`m_rem, s_rem` эмпирически: те же барьеры, тот же протокол «SL первым».

    Направление не параметр: расстояния заданы в «благоприятную» и «неблагоприятную» стороны,
    поэтому исходы симметричны для лонга и шорта.

    `until_ts` отсекает бары не раньше текущего момента: путь целиком должен лежать в прошлом,
    иначе оценка на баре t подглядывает в будущее. Условность по `A_t` не реализуется —
    моменты безусловны, и это помечено в артефакте.
    """
    if to_tp < 1 or to_sl < 1:
        raise HoldingError("barriers must be at least one tick")
    if tau_rem < 1:
        raise HoldingError("tau_rem must be at least 1")
    if stride < 1:
        raise HoldingError("stride must be at least 1")

    outcomes: List[float] = []
    wins: List[int] = []
    for session in sessions:
        length = len(session["close"])
        stamps = session.get("ts")
        for index in range(0, max(0, length - tau_rem), stride):
            if until_ts is not None:
                if stamps is None:
                    raise HoldingError("until_ts requires timestamps")
                if int(stamps[index + tau_rem]) >= until_ts:
                    break
            resolved = resolve_path(
                session,
                index,
                tau_rem,
                direction=1,
                tp_ticks=to_tp,
                sl_ticks=to_sl,
                tick_value=tick_value,
            )
            if resolved is None:
                continue
            kind, value = resolved
            outcomes.append(value)
            wins.append(kind)

    if len(outcomes) < min_paths:
        return None
    values = np.asarray(outcomes, dtype=float)
    kinds = np.asarray(wins)
    return RemainingMoments(
        m_rem=float(np.mean(values)),
        s_rem=float(np.std(values, ddof=1)) if len(values) > 1 else 0.0,
        p_tp=float(np.mean(kinds == TP_WIN)),
        p_sl=float(np.mean(kinds == SL_WIN)),
        p_n=float(np.mean(kinds == NEUTRAL)),
        paths=len(values),
        to_tp=to_tp,
        to_sl=to_sl,
        tau_rem=tau_rem,
        stride=stride,
    )


def close_cost_drift(
    *,
    c_now: float,
    sigma_now: float,
    sigma_later: float,
    spread_now: float,
    spread_later: float,
) -> float:
    """`E[C_later] − C_now` при пропорциональном спреде: издержки закрытия растут со спредом.

    Модель объявлена, а не измерена: спред будущего момента неизвестен, поэтому разность
    считается по отношению спредов (или по отношению range-based σ как прокси волатильности).
    """
    if c_now < 0 or sigma_now <= 0 or sigma_later <= 0:
        raise HoldingError("cost and volatilities must be positive")
    if spread_now < 0 or spread_later < 0:
        raise HoldingError("spreads must be non-negative")
    if spread_now > 0:
        ratio = spread_later / spread_now
    else:
        # Спред не измерен: прокси изменения издержек — отношение волатильностей.
        ratio = sigma_later / sigma_now
    return c_now * (ratio - 1.0)


def hold_decision(
    *,
    a_t: float,
    to_tp: int,
    to_sl: int,
    tau_rem: int,
    lambda_risk: float,
    cost_drift: float,
    moments: Optional[RemainingMoments],
) -> HoldDecision:
    """Правило §7: `Hold ⇔ m_rem − λ·s_rem > E[C_later] − C_now`."""
    if lambda_risk <= 0:
        raise HoldingError("lambda_risk must be positive")
    if a_t <= 0:
        return HoldDecision(False, "OVERSHOOT", None, None, cost_drift, a_t, moments)
    if moments is None:
        return HoldDecision(False, "ESTIMATE_MISSING", None, None, cost_drift, a_t, moments)

    risk_term = lambda_risk * moments.s_rem
    edge = moments.m_rem - risk_term
    if edge > cost_drift:
        return HoldDecision(
            hold=True,
            reason="HOLD",
            m_rem=moments.m_rem,
            risk_term=risk_term,
            cost_drift=cost_drift,
            a_t=a_t,
            moments=moments,
        )
    # Разделяем причины: дорогое закрытие позже отличается от исчерпанного edge.
    reason = "CLOSE_COST_DRIFT" if cost_drift > 0 and edge <= cost_drift else "ANCHOR_EDGE"
    return HoldDecision(
        hold=False,
        reason=reason,
        m_rem=moments.m_rem,
        risk_term=risk_term,
        cost_drift=cost_drift,
        a_t=a_t,
        moments=moments,
    )


def cusum_breaker(
    returns: Sequence[float], *, threshold: float = CUSUM_THRESHOLD, min_points: int = 10
) -> bool:
    """CUSUM-of-squares (`buy_frame.md`): `S_k = Σ(ε_i²/σ² − 1)`, триггер по `max|S_k|`.

    `ε_i` — центрированная доходность: тест ловит **смену дисперсии**, а не сдвиг среднего
    (постоянный дрейф не должен закрывать позицию). Порог `threshold·√n` масштабируется с
    длиной ряда: для однородного гауссовского ряда `max|S_k| ≈ 2√n`, поэтому порог 4 консервативен.
    """
    if threshold <= 0:
        raise HoldingError("threshold must be positive")
    sample = np.asarray(list(returns), dtype=float)
    if sample.size < min_points:
        return False
    centred = sample - float(np.mean(sample))
    scale = float(np.std(centred, ddof=1))
    if not np.isfinite(scale) or scale <= 0:
        return False
    standardised = centred / scale
    cumulative = np.cumsum(standardised**2 - 1.0)
    peak = float(np.max(np.abs(cumulative)))
    return peak > threshold * math.sqrt(sample.size)


__all__ = [
    "CLOSING_TRIGGERS",
    "CUSUM_THRESHOLD",
    "DEFAULT_MIN_PATHS",
    "DEFAULT_STRIDE",
    "EXIT_TRIGGERS",
    "HoldDecision",
    "HoldingError",
    "KIND_COST",
    "KIND_HARD",
    "KIND_REDUCTION",
    "KIND_REGULAR",
    "OnlineBand",
    "REDUCING_TRIGGERS",
    "RemainingMoments",
    "TriggerSpec",
    "a0_bounds",
    "a_coordinate",
    "band_checks",
    "close_cost_drift",
    "cusum_breaker",
    "hold_decision",
    "online_band",
    "remaining_moments",
]
