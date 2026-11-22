"""Режимная модель: HMM на 15-минутных барах, Gate 2 и выживание режима.

Матрица переходов оценивается **внутри торговых окон** (`lengths` в `hmmlearn`): переход через
закрытие — не переход режима, и учитывать его значило бы занижать диагональ.

Состояния упорядочиваются по range-based волатильности детерминированно: «спокойное» — то, у
которого средняя волатильность ниже. Это защищает от перестановки меток между рестартами.

Gate 2 (`buy_frame.md` §16): `N_trans ≥ 10·K·(K−1)`; при `K = 2` это 20 переходов. При провале
модель понижается до `REFERENCE_ONLY` и не участвует в `τ_max`; понижать `K` или вводить приор
автоматически запрещено — это была бы подгонка под данные.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

import numpy as np

from .signals import SessionArrays

HTF_MINUTES = 15
MTF_MINUTES = 10
STATES = 2
GATE2_PER_PARAMETER = 10
#: Доля несогласованных 10m-баров, выше которой горизонт укорачивается вдвое.
MTF_DISAGREEMENT_LIMIT = 0.25
#: z для односторонней нижней границы Уилсона (95 %).
WILSON_Z = 1.645
DEFAULT_SEEDS = 5
DEFAULT_SEED = 20261124
DEFAULT_ITERATIONS = 200

GATE2_PASSED = "PASS"
GATE2_FAILED = "GATE2_FAILED"
REFERENCE_ONLY = "REFERENCE_ONLY"
PROXY_IMBALANCE = "PROXY:volume-imbalance"


class RegimeError(ValueError):
    """Режимную модель нельзя построить по заданным данным."""


@dataclass(frozen=True)
class RegimeFit:
    """Подогнанная модель: состояния по барам, матрица переходов и проверка Gate 2."""

    states: Tuple[int, ...]
    session_of: Tuple[int, ...]
    transition: Tuple[Tuple[float, ...], ...]
    counts: Tuple[Tuple[int, ...], ...]
    volatility: Tuple[float, ...]
    transition_counts: int
    gate2_threshold: int
    gate2_passed: bool
    observations: int
    sessions: int
    log_likelihood: float
    seeds: int
    flags: Dict[str, str] = field(default_factory=dict)
    method: str = "GaussianHMM, diag covariance, states ordered by range volatility"

    def to_dict(self) -> Dict[str, object]:
        return {
            "observations": self.observations,
            "sessions": self.sessions,
            "transition_counts": self.transition_counts,
            "gate2_threshold": self.gate2_threshold,
            "gate2": GATE2_PASSED if self.gate2_passed else GATE2_FAILED,
            "transition": [[round(value, 6) for value in row] for row in self.transition],
            "counts": [list(row) for row in self.counts],
            "volatility": [round(value, 8) for value in self.volatility],
            "log_likelihood": round(self.log_likelihood, 6),
            "seeds": self.seeds,
            "method": self.method,
            "flags": dict(self.flags),
        }


@dataclass(frozen=True)
class Survival:
    """Выживание режима: `γ`, нижняя граница и горизонт в шагах/минутах."""

    state: int
    gamma: float
    p_lcb: float
    transitions_from: int
    q: float
    tau_steps: int
    tau_minutes: int
    gate_passed: bool
    tau_minutes_alt: int

    def to_dict(self) -> Dict[str, object]:
        return {
            "state": self.state,
            "gamma": round(self.gamma, 6),
            "p_lcb": round(self.p_lcb, 6),
            "transitions_from": self.transitions_from,
            "q": self.q,
            "tau_steps": self.tau_steps,
            "tau_minutes": self.tau_minutes,
            "tau_minutes_alt": self.tau_minutes_alt,
            "gate_passed": self.gate_passed,
        }


@dataclass(frozen=True)
class RegimeReport:
    """Итог режимной части: 15m-модель, выживание, согласованность 10m."""

    instrument: str
    fit: RegimeFit | None
    survival: Survival | None
    survival_by_state: Dict[int, Survival]
    mtf_disagreement: float | None
    mtf_tau_minutes: int | None
    gates: Dict[str, str]

    def to_dict(self) -> Dict[str, object]:
        return {
            "instrument": self.instrument,
            "hmm": None if self.fit is None else self.fit.to_dict(),
            "survival": None if self.survival is None else self.survival.to_dict(),
            "survival_by_state": {
                str(state): item.to_dict() for state, item in self.survival_by_state.items()
            },
            "mtf_disagreement": None
            if self.mtf_disagreement is None
            else round(self.mtf_disagreement, 6),
            "mtf_tau_minutes": self.mtf_tau_minutes,
            "gates": dict(self.gates),
        }


def aggregate_minutes(arrays: SessionArrays, minutes: int) -> SessionArrays:
    """Свернуть минутные бары окна в бары по `minutes` минут.

    Корзины считаются от первого бара окна, а не от начала часа: окно, начавшееся не на границе
    часа, иначе дало бы неполную первую корзину.
    """
    if minutes < 1:
        raise RegimeError("minutes must be at least 1")
    close = arrays["close"]
    if len(close) == 0:
        return {
            "ts": np.asarray([], dtype=np.int64),
            "open": np.asarray([], dtype=float),
            "high": np.asarray([], dtype=float),
            "low": np.asarray([], dtype=float),
            "close": np.asarray([], dtype=float),
            "volume": np.asarray([], dtype=float),
        }
    ts = arrays.get("ts")
    if ts is None:
        raise RegimeError("bars must carry timestamps to aggregate")
    bucket = (ts - ts[0]) // (minutes * 60)

    starts = [0]
    for index in range(1, len(bucket)):
        if bucket[index] != bucket[index - 1]:
            starts.append(index)
    starts.append(len(bucket))

    ts_out: List[int] = []
    open_out: List[float] = []
    high_out: List[float] = []
    low_out: List[float] = []
    close_out: List[float] = []
    volume_out: List[float] = []
    for left, right in zip(starts, starts[1:]):
        block = slice(left, right)
        ts_out.append(int(ts[left]))
        open_out.append(float(arrays["open"][left]))
        high_out.append(float(np.max(arrays["high"][block])))
        low_out.append(float(np.min(arrays["low"][block])))
        close_out.append(float(arrays["close"][right - 1]))
        volume_out.append(float(np.sum(arrays["volume"][block])))

    return {
        "ts": np.asarray(ts_out, dtype=np.int64),
        "open": np.asarray(open_out, dtype=float),
        "high": np.asarray(high_out, dtype=float),
        "low": np.asarray(low_out, dtype=float),
        "close": np.asarray(close_out, dtype=float),
        "volume": np.asarray(volume_out, dtype=float),
    }


def regime_features(arrays: Sequence[SessionArrays]) -> Tuple[np.ndarray, List[int]]:
    """Признаки бара: доходность, range-based волатильность, дисбаланс закрытия.

    Дисбаланс `(close − open)/(high − low)` — прокси для дисбаланса объёма: разделения на
    покупки и продажи в выгрузке нет, поэтому признак помечается `PROXY:volume-imbalance`.
    """
    rows: List[np.ndarray] = []
    lengths: List[int] = []
    for session in arrays:
        close = session["close"]
        if len(close) < 2:
            continue
        high = session["high"]
        low = session["low"]
        if (
            np.any(close <= 0)
            or np.any(high <= 0)
            or np.any(low <= 0)
            or np.any(high < low)
        ):
            raise RegimeError("bar prices must be positive and high >= low")
        returns = np.concatenate(([0.0], np.log(close[1:] / close[:-1])))
        ratio = np.maximum(high / np.maximum(low, 1e-12), 1.0)
        parkinson = np.log(ratio) / (2.0 * math.sqrt(math.log(2.0)))
        span = high - low
        safe_span = np.where(span > 0, span, 1.0)
        imbalance = np.where(span > 0, (close - session["open"]) / safe_span, 0.0)
        rows.append(np.column_stack([returns, parkinson, np.clip(imbalance, -1.0, 1.0)]))
        lengths.append(len(close))
    if not rows:
        raise RegimeError("no sessions with at least two bars")
    return np.vstack(rows), lengths


def fit_hmm(
    features: np.ndarray,
    lengths: Sequence[int],
    *,
    states: int = STATES,
    seeds: int = DEFAULT_SEEDS,
    seed: int = DEFAULT_SEED,
    iterations: int = DEFAULT_ITERATIONS,
) -> Tuple[object, np.ndarray, float, Dict[int, float]]:
    """Подогнать `GaussianHMM` с рестартами.

    Возвращает модель, перестановку состояний (по волатильности), лучший likelihood и среднюю
    волатильность по внутренним состояниям.
    """
    from hmmlearn.hmm import GaussianHMM  # локальный импорт: тяжёлая зависимость

    if features.ndim != 2 or features.shape[1] != 3:
        raise RegimeError("features must be an (n, 3) array")
    if features.shape[0] < states * 10:
        raise RegimeError("too few observations for the regime model")
    if seeds < 1:
        raise RegimeError("seeds must be at least 1")

    best_model = None
    best_score = -math.inf
    for attempt in range(seeds):
        model = GaussianHMM(
            n_components=states,
            covariance_type="diag",
            n_iter=iterations,
            random_state=seed + attempt,
        )
        try:
            model.fit(features, lengths)
        except (ValueError, FloatingPointError) as exc:  # hmmlearn бросает разные типы
            raise RegimeError(f"HMM fit failed: {exc}") from exc
        score = float(model.score(features, lengths))
        if score > best_score:
            best_model, best_score = model, score
    if best_model is None:  # pragma: no cover - недостижимо при seeds >= 1
        raise RegimeError("HMM fit produced no model")

    volatility = features[:, 1]
    labels = best_model.predict(features, lengths)  # type: ignore[attr-defined]
    mean_volatility = {
        state: float(np.mean(volatility[labels == state]))
        if np.any(labels == state)
        else math.inf
        for state in range(states)
    }
    order = np.asarray(sorted(range(states), key=mean_volatility.__getitem__), dtype=int)
    return best_model, order, best_score, mean_volatility


def canonical_labels(labels: Sequence[int], order: np.ndarray) -> np.ndarray:
    """Перевести внутренние состояния в канонические (0 — спокойное)."""
    inverse = np.argsort(order)
    return np.asarray([inverse[int(state)] for state in labels], dtype=int)


def canonical_transition(transmat: np.ndarray, order: np.ndarray) -> np.ndarray:
    return np.asarray(transmat)[np.ix_(order, order)]


def split_by_lengths(values: Sequence[int], lengths: Sequence[int]) -> List[np.ndarray]:
    """Разбить плоский список меток по окнам."""
    result: List[np.ndarray] = []
    offset = 0
    for length in lengths:
        result.append(np.asarray(values[offset : offset + length], dtype=int))
        offset += length
    return result


def markov_counts(
    states: Sequence[int], session_of: Sequence[int], states_n: int = STATES
) -> Tuple[np.ndarray, int]:
    """Счётчики переходов внутри окон: пара через закрытие не считается переходом."""
    counts = np.zeros((states_n, states_n), dtype=int)
    transitions = 0
    for index in range(1, len(states)):
        if session_of[index] != session_of[index - 1]:
            continue
        counts[int(states[index - 1]), int(states[index])] += 1
        transitions += 1
    return counts, transitions


def wilson_lower(successes: int, trials: int, z: float = WILSON_Z) -> float:
    """Односторонняя нижняя граница доли по Уилсону (консервативнее нормальной)."""
    if trials <= 0:
        return 0.0
    if successes < 0 or successes > trials:
        raise RegimeError("successes must be within [0, trials]")
    share = successes / trials
    denominator = 1.0 + z * z / trials
    centre = share + z * z / (2.0 * trials)
    spread = z * math.sqrt(share * (1.0 - share) / trials + z * z / (4.0 * trials * trials))
    return max(0.0, (centre - spread) / denominator)


def survival(
    counts: np.ndarray, *, state: int, q: float, htf_minutes: int = HTF_MINUTES
) -> Survival:
    """Выживание в состоянии `state`: `γ`, нижняя граница и горизонт.

    `τ_regime` — по формуле дизайна: `Δ·⌊ln((1−q)/γ)/ln P_LCB⌋`. Рядом публикуется
    альтернатива `Δ·⌊ln(1−q)/ln P_LCB⌋` (без `γ` в числителе): формулы расходятся, и выбор между
    ними — трактовка «горизонта удержания режима», а не арифметика.
    """
    if not 0.0 < q < 1.0:
        raise RegimeError(f"q must be in (0, 1), got {q}")
    from_state = int(np.sum(counts[state]))
    if from_state <= 0:
        return Survival(state, 0.0, 0.0, 0, q, 0, 0, False, 0)

    gamma = float(counts[state, state] / from_state)
    p_lcb = wilson_lower(int(counts[state, state]), from_state)
    gate = gamma >= 1.0 - q

    tau_steps = 0
    if gate and 0.0 < p_lcb < 1.0 and 0.0 < gamma < 1.0:
        tau_steps = int(math.floor(max(0.0, math.log((1.0 - q) / gamma) / math.log(p_lcb))))
    tau_alt = 0
    if 0.0 < p_lcb < 1.0:
        tau_alt = int(math.floor(max(0.0, math.log(1.0 - q) / math.log(p_lcb))))
    return Survival(
        state=state,
        gamma=gamma,
        p_lcb=p_lcb,
        transitions_from=from_state,
        q=q,
        tau_steps=tau_steps,
        tau_minutes=tau_steps * htf_minutes,
        gate_passed=gate,
        tau_minutes_alt=tau_alt * htf_minutes,
    )


def mtf_consistency(
    htf_sessions: Sequence[Tuple[np.ndarray, np.ndarray]],
    mtf_sessions: Sequence[Tuple[np.ndarray, np.ndarray]],
    *,
    tau_minutes: int,
    limit: float = MTF_DISAGREEMENT_LIMIT,
) -> Tuple[float | None, int]:
    """Согласованность 10m-разметки с 15m: расхождение укорачивает горизонт вдвое.

    Сопоставление идёт по времени начала корзины, а не по номеру: 15 и 10 минут не кратны,
    и «третий 10m-бар — второй 15m-бар» было бы неверно. Порог `limit` объявлен, а не подобран.
    """
    mismatch = 0
    total = 0
    for (htf_ts, htf_states), (mtf_ts, mtf_states) in zip(htf_sessions, mtf_sessions):
        if len(htf_ts) == 0 or len(mtf_ts) == 0:
            continue
        index = np.clip(np.searchsorted(htf_ts, mtf_ts, side="right") - 1, 0, len(htf_ts) - 1)
        for position, state in enumerate(mtf_states):
            total += 1
            if int(state) != int(htf_states[index[position]]):
                mismatch += 1
    if total == 0:
        return None, tau_minutes
    disagreement = mismatch / total
    if disagreement > limit:
        return disagreement, max(0, tau_minutes // 2)
    return disagreement, tau_minutes


def evaluate_regime(
    sessions: Sequence[SessionArrays],
    *,
    instrument: str,
    q: float,
    htf_minutes: int = HTF_MINUTES,
    mtf_minutes: int = MTF_MINUTES,
    seeds: int = DEFAULT_SEEDS,
    seed: int = DEFAULT_SEED,
) -> RegimeReport:
    """Полный цикл: 15m-модель, Gate 2, выживание и согласованность 10m."""
    if mtf_minutes > htf_minutes:
        raise RegimeError("mtf frame must not be longer than the htf frame")
    htf_sessions = [aggregate_minutes(session, htf_minutes) for session in sessions]
    features, lengths = regime_features(htf_sessions)
    try:
        model, order, score, mean_volatility = fit_hmm(features, lengths, seeds=seeds, seed=seed)
    except RegimeError as exc:
        return RegimeReport(
            instrument=instrument,
            fit=None,
            survival=None,
            survival_by_state={},
            mtf_disagreement=None,
            mtf_tau_minutes=None,
            gates={"regime": f"{GATE2_FAILED}: {exc}"},
        )

    labels = model.predict(features, lengths)  # type: ignore[attr-defined]
    states = tuple(int(state) for state in canonical_labels(labels, order))
    session_of = tuple(
        session_index for session_index, length in enumerate(lengths) for _ in range(length)
    )
    counts, transitions = markov_counts(states, session_of)
    threshold = GATE2_PER_PARAMETER * STATES * (STATES - 1)

    flags: Dict[str, str] = {"imbalance": PROXY_IMBALANCE}
    fit = RegimeFit(
        states=states,
        session_of=session_of,
        transition=tuple(
            tuple(float(value) for value in row)
            for row in canonical_transition(model.transmat_, order)  # type: ignore[attr-defined]
        ),
        counts=tuple(tuple(int(value) for value in row) for row in counts),
        volatility=tuple(float(mean_volatility[int(state)]) for state in order),
        transition_counts=transitions,
        gate2_threshold=threshold,
        gate2_passed=transitions >= threshold,
        observations=int(features.shape[0]),
        sessions=len(lengths),
        log_likelihood=score,
        seeds=seeds,
        flags=flags,
    )
    if not fit.gate2_passed:
        flags["regime"] = (
            f"{GATE2_FAILED}: {transitions} переходов < {threshold}; {REFERENCE_ONLY}"
        )
        return RegimeReport(
            instrument=instrument,
            fit=fit,
            survival=None,
            survival_by_state={},
            mtf_disagreement=None,
            mtf_tau_minutes=None,
            gates={"regime": flags["regime"]},
        )

    survivals = {
        state: survival(counts, state=state, q=q, htf_minutes=htf_minutes)
        for state in range(STATES)
    }
    worst = min(survivals.values(), key=lambda item: item.tau_minutes)

    mtf_sessions = [aggregate_minutes(session, mtf_minutes) for session in sessions]
    mtf_features, mtf_lengths = regime_features(mtf_sessions)
    mtf_labels = model.predict(mtf_features, mtf_lengths)  # type: ignore[attr-defined]
    htf_by_session = [
        (session["ts"], labels)
        for session, labels in zip(htf_sessions, split_by_lengths(states, lengths))
        if len(session["ts"])
    ]
    mtf_by_session = [
        (session["ts"], labels)
        for session, labels in zip(
            mtf_sessions, split_by_lengths(canonical_labels(mtf_labels, order), mtf_lengths)
        )
        if len(session["ts"])
    ]
    disagreement, mtf_tau = mtf_consistency(
        htf_by_session, mtf_by_session, tau_minutes=worst.tau_minutes
    )
    gates = {
        "regime": GATE2_PASSED,
        "survival": "PASS" if worst.gate_passed else "REGIME_CONFIDENCE",
    }
    if disagreement is not None and disagreement > MTF_DISAGREEMENT_LIMIT:
        gates["mtf"] = f"MTF_DISAGREE: {disagreement:.2%} > {MTF_DISAGREEMENT_LIMIT:.0%}"
    return RegimeReport(
        instrument=instrument,
        fit=fit,
        survival=worst,
        survival_by_state=survivals,
        mtf_disagreement=disagreement,
        mtf_tau_minutes=mtf_tau,
        gates=gates,
    )


__all__ = [
    "DEFAULT_SEED",
    "DEFAULT_SEEDS",
    "GATE2_FAILED",
    "GATE2_PASSED",
    "GATE2_PER_PARAMETER",
    "HTF_MINUTES",
    "MTF_DISAGREEMENT_LIMIT",
    "MTF_MINUTES",
    "PROXY_IMBALANCE",
    "REFERENCE_ONLY",
    "RegimeError",
    "RegimeFit",
    "RegimeReport",
    "STATES",
    "Survival",
    "WILSON_Z",
    "aggregate_minutes",
    "canonical_labels",
    "canonical_transition",
    "evaluate_regime",
    "fit_hmm",
    "markov_counts",
    "mtf_consistency",
    "regime_features",
    "split_by_lengths",
    "survival",
    "wilson_lower",
]
