"""Режимная модель: HMM на 15-минутных барах, Gate 2 и выживание режима.

Матрица переходов оценивается **внутри торговых окон** (`lengths` в `hmmlearn`): переход через
закрытие — не переход режима, и учитывать его значило бы занижать диагональ.

Состояния упорядочиваются по range-based волатильности детерминированно: «спокойное» — то, у
которого средняя волатильность ниже. Это защищает от перестановки меток между рестартами.

`N_trans` считается как в `buy_frame.md` §16 — число **смен** состояния
(`argmax γ_t ≠ argmax γ_{t+1}`), а не число пар баров. Переходы внутри состояния — не переходы,
и считать их означало бы проходить Gate 2 на почти постоянном ряду.

`fav`-состояние задаётся знаком дрейфа: для лонга благоприятно состояние с положительной
средней доходностью, для шорта — с отрицательной. Гейт и горизонт берутся по `fav`, а не по
худшему состоянию: «худшее» относится к направлению, в котором мы не торгуем.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .signals import SessionArrays

HTF_MINUTES = 15
MTF_MINUTES = 10
STATES = 2
GATE2_PER_PARAMETER = 10
#: Доля несогласованных 10m-баров, выше которой горизонт берётся как минимум двух оценок.
MTF_DISAGREEMENT_LIMIT = 0.25
#: z для односторонней нижней границы Уилсона (95 %).
WILSON_Z = 1.645
DEFAULT_SEEDS = 5
DEFAULT_SEED = 20261124
DEFAULT_ITERATIONS = 200

GATE2_PASSED = "PASS"
GATE2_FAILED = "GATE2_FAILED"
REFERENCE_ONLY = "REFERENCE_ONLY"
#: Статус направления в артефакте: `fav` не определён — это не `PASS` и не провал Gate 2.
DIRECTION_PASS = "PASS"
DIRECTION_LOW_CONFIDENCE = "REGIME_CONFIDENCE"
DIRECTION_FAV_UNDEFINED = "FAV_UNDEFINED"
PROXY_IMBALANCE = "PROXY:volume-imbalance"
LONG = 1
SHORT = -1


class RegimeError(ValueError):
    """Режимную модель нельзя построить по заданным данным."""


@dataclass(frozen=True)
class RegimeFit:
    """Подогнанная модель: состояния по барам, матрица переходов и проверка Gate 2."""

    states: Tuple[int, ...]
    session_of: Tuple[int, ...]
    transition: Tuple[Tuple[float, ...], ...]
    counts: Tuple[Tuple[int, ...], ...]
    drift: Tuple[float, ...]
    volatility: Tuple[float, ...]
    transitions: int
    gate2_threshold: int
    gate2_passed: bool
    observations: int
    sessions: int
    log_likelihood: float
    converged: bool
    seeds: int
    flags: Dict[str, str] = field(default_factory=dict)
    method: str = "GaussianHMM, diag covariance, states ordered by range volatility"

    def fav(self, direction: int) -> Optional[int]:
        """Благоприятное состояние для направления: знак дрейфа совпадает со знаком `s`."""
        if direction not in (LONG, SHORT):
            raise RegimeError("direction must be +1 or -1")
        candidates = [
            state
            for state, drift in enumerate(self.drift)
            if direction * drift > 0 and math.isfinite(drift)
        ]
        if len(candidates) != 1:
            return None
        return candidates[0]

    def to_dict(self) -> Dict[str, object]:
        return {
            "observations": self.observations,
            "sessions": self.sessions,
            "transitions": self.transitions,
            "gate2_threshold": self.gate2_threshold,
            "gate2": GATE2_PASSED if self.gate2_passed else GATE2_FAILED,
            "converged": self.converged,
            "transition": [_finite_row(row) for row in self.transition],
            "counts": [list(row) for row in self.counts],
            "drift": [_number(value) for value in self.drift],
            "volatility": [_number(value) for value in self.volatility],
            "fav": {
                "long": self.fav(LONG),
                "short": self.fav(SHORT),
            },
            "log_likelihood": _number(self.log_likelihood),
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
            "gamma": _number(self.gamma),
            "p_lcb": _number(self.p_lcb),
            "transitions_from": self.transitions_from,
            "q": self.q,
            "tau_steps": self.tau_steps,
            "tau_minutes": self.tau_minutes,
            "tau_minutes_alt": self.tau_minutes_alt,
            "gate_passed": self.gate_passed,
        }


@dataclass(frozen=True)
class RegimeReport:
    """Итог режимной части: 15m-модель, выживание по направлениям, согласованность 10m."""

    instrument: str
    fit: Optional[RegimeFit]
    survival_by_direction: Dict[int, Optional[Survival]]
    mtf_survival_by_direction: Dict[int, Optional[Survival]]
    tau_regime_minutes: Optional[int]
    tau_mtf_minutes: Optional[int]
    mtf_disagreement: Optional[float]
    gates: Dict[str, str]

    @property
    def gate_passed(self) -> Dict[int, bool]:
        """Блокирующий гейт `γ_t(fav) ≥ 1−q` по направлениям.

        `True` означает «не блокирует»: `False` возвращается только при **измеренной** низкой
        уверенности. Неизмеренное (модель не построена, Gate 2 провален, `fav` не определён)
        даёт `REFERENCE_ONLY` и не блокирует вход (M3) — но и не считается `PASS`: причина
        видна в `gates` (`GATE2_FAILED` / `FAV_UNDEFINED` / `REGIME_CONFIDENCE`).
        """
        if self.fit is None or not self.fit.gate2_passed:
            return {LONG: True, SHORT: True}
        result: Dict[int, bool] = {}
        for direction in (LONG, SHORT):
            item = self.survival_by_direction.get(direction)
            result[direction] = True if item is None else item.gate_passed
        return result

    @property
    def reference_reason(self) -> str:
        """Почему `τ_regime` не участвует в `τ_max`: причины различаются и по тексту."""
        if self.fit is None:
            return "режимная модель не построена"
        if not self.fit.gate2_passed:
            return (
                f"Gate 2 провален: {self.fit.transitions} смен < "
                f"{self.fit.gate2_threshold} — режим REFERENCE_ONLY"
            )
        if all(item is None for item in self.survival_by_direction.values()):
            return "fav не определён: знак дрейфа не выделяет одно состояние"
        return "горизонт режима не посчитан (REFERENCE_ONLY)"

    def to_dict(self) -> Dict[str, object]:
        return {
            "instrument": self.instrument,
            "hmm": None if self.fit is None else self.fit.to_dict(),
            "survival": _direction_dict(self.survival_by_direction),
            "mtf_survival": _direction_dict(self.mtf_survival_by_direction),
            "tau_regime_minutes": self.tau_regime_minutes,
            "tau_mtf_minutes": self.tau_mtf_minutes,
            "mtf_disagreement": _number(self.mtf_disagreement),
            "gates": dict(self.gates),
        }


def _number(value: Optional[float]) -> Optional[float]:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), 8)


def _finite_row(row: Sequence[float]) -> List[Optional[float]]:
    return [_number(float(value)) for value in row]


def _survival_dict(item: Optional[Survival]) -> Optional[Dict[str, object]]:
    return None if item is None else item.to_dict()


def _direction_dict(
    items: Dict[int, Optional[Survival]]
) -> Dict[str, Optional[Dict[str, object]]]:
    """Артефакт по направлениям: порядок ключей фиксирован, отсутствие — `null`."""
    return {
        "long": _survival_dict(items.get(LONG)),
        "short": _survival_dict(items.get(SHORT)),
    }


def _direction_status(item: Optional[Survival]) -> str:
    """Статус направления: `PASS` не выдаётся за `FAV_UNDEFINED` (m9)."""
    if item is None:
        return DIRECTION_FAV_UNDEFINED
    return DIRECTION_PASS if item.gate_passed else DIRECTION_LOW_CONFIDENCE


def trusted_tau(survival_by_direction: Dict[int, Optional[Survival]]) -> Optional[int]:
    """Минимум `τ` по направлениям, чей гейт пройден (R4).

    Направление без `fav` (`None`) и направление с проваленным гейтом в минимум не входят:
    первое — неизмеренное, второе — `REFERENCE_ONLY` на входе. Если доверенных направлений нет,
    горизонт не определён (`None`), а не ноль: ноль означал бы измеренный отказ удержания.
    """
    trusted = [
        item.tau_minutes
        for item in survival_by_direction.values()
        if item is not None and item.gate_passed
    ]
    return min(trusted) if trusted else None


def aggregate_minutes(arrays: SessionArrays, minutes: int) -> SessionArrays:
    """Свернуть минутные бары окна в бары по `minutes` минут.

    Корзины считаются от первого бара окна, а не от начала часа: окно, начавшееся не на границе
    часа, иначе дало бы неполную первую корзину. Неполная корзина (в ней меньше `minutes`
    минут) всё равно возвращается: её фактическая длина видна по длине массивов результата.
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
    Сессии с менее чем двумя барами пропускаются; вызывающий передаёт только пригодные.
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
) -> Tuple[object, np.ndarray, float, Dict[int, float], bool]:
    """Подогнать `GaussianHMM` с рестартами.

    Возвращает модель, перестановку состояний (по волатильности), лучший likelihood, среднюю
    волатильность по внутренним состояниям и признак сходимости.
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
    converged = False
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
            converged = bool(getattr(getattr(model, "monitor_", None), "converged", False))
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
    return best_model, order, best_score, mean_volatility, converged


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
    """Счётчики пар и число **смен** состояния внутри окон.

    Возвращается `(counts, transitions)`, где `counts[i, j]` — число пар `i→j`, а `transitions`
    — число пар с `i ≠ j` (`buy_frame.md` §16: `N_trans = #{argmax γ_t ≠ argmax γ_{t+1}}`).
    Пара через закрытие окна не считается ни тем, ни другим.
    """
    counts = np.zeros((states_n, states_n), dtype=int)
    transitions = 0
    for index in range(1, len(states)):
        if session_of[index] != session_of[index - 1]:
            continue
        previous, current = int(states[index - 1]), int(states[index])
        counts[previous, current] += 1
        if previous != current:
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

    `τ` — по формуле дизайна: `Δ·⌊ln((1−q)/γ)/ln P_LCB⌋`. Рядом публикуется альтернатива
    `Δ·⌊ln(1−q)/ln P_LCB⌋` без `γ` в числителе: формулы расходятся, и выбор между ними —
    трактовка «горизонта удержания режима», а не арифметика.
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
    limit: float = MTF_DISAGREEMENT_LIMIT,
) -> Optional[float]:
    """Доля 10m-баров, состояние которых не совпало с 15m-баром той же корзины.

    Сопоставление идёт по времени начала корзины, а не по номеру: 15 и 10 минут не кратны.
    """
    if limit <= 0:
        raise RegimeError("limit must be positive")
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
        return None
    return mismatch / total


def evaluate_regime(
    sessions: Sequence[SessionArrays],
    *,
    instrument: str,
    q: float,
    htf_minutes: int = HTF_MINUTES,
    mtf_minutes: int = MTF_MINUTES,
    seeds: int = DEFAULT_SEEDS,
    seed: int = DEFAULT_SEED,
    train_lengths: Optional[Sequence[int]] = None,
    train_until_ts: Optional[int] = None,
) -> RegimeReport:
    """Полный цикл: 15m-модель, Gate 2, выживание по направлениям и согласованность 10m.

    `train_lengths`/`train_until_ts` — обучающая часть: модель обучается только на ней, а
    разметка применяется ко всей выборке. Без них обучение и разметка совпадают, и это видно
    в артефакте (`in_sample`). Сессии, из которых после агрегации получается один бар,
    отбрасываются синхронно для 15m и 10m, иначе ярлыки разъезжаются с окнами.
    """
    if mtf_minutes > htf_minutes:
        raise RegimeError("mtf frame must not be longer than the htf frame")
    usable = [session for session in sessions if len(session["close"]) >= 2]
    if not usable:
        return _empty_report(instrument, "нет окон с двумя и более барами")

    htf_pairs: List[Tuple[SessionArrays, SessionArrays]] = []
    for session in usable:
        aggregated = aggregate_minutes(session, htf_minutes)
        if len(aggregated["close"]) >= 2:
            htf_pairs.append((session, aggregated))
    if not htf_pairs:
        return _empty_report(instrument, "после агрегации не осталось окон с двумя барами")

    features, lengths = regime_features([aggregated for _, aggregated in htf_pairs])
    if train_until_ts is not None:
        fit_lengths = [
            int(np.sum(aggregated["ts"] < train_until_ts))
            for _, aggregated in htf_pairs
        ]
        if sum(fit_lengths) < 20:
            return _empty_report(instrument, "в обучающей части меньше 20 15m-баров")
    elif train_lengths is not None:
        fit_lengths = list(train_lengths)
    else:
        fit_lengths = lengths
    if len(fit_lengths) != len(lengths) or any(
        fit_len > length for fit_len, length in zip(fit_lengths, lengths)
    ):
        raise RegimeError("train lengths must not exceed the per-session lengths")

    # Обучающая часть — первые `fit_len` строк каждого окна: берётся срез, а не маска,
    # иначе hmmlearn получит несовпадающие `sum(lengths)` и число наблюдений.
    train_rows: List[np.ndarray] = []
    offset = 0
    for length, fit_len in zip(lengths, fit_lengths):
        if fit_len:
            train_rows.append(features[offset : offset + fit_len])
        offset += length
    train_features = np.vstack(train_rows) if train_rows else features
    if train_features.shape[0] != sum(fit_lengths):
        raise RegimeError("train slice does not match the declared lengths")
    try:
        model, order, score, mean_volatility, converged = fit_hmm(
            train_features, [fit_len for fit_len in fit_lengths if fit_len], seeds=seeds, seed=seed
        )
    except RegimeError as exc:
        return _empty_report(instrument, str(exc))

    labels = model.predict(features, lengths)  # type: ignore[attr-defined]
    states = tuple(int(state) for state in canonical_labels(labels, order))
    session_of = tuple(
        session_index for session_index, length in enumerate(lengths) for _ in range(length)
    )
    counts, transitions = markov_counts(states, session_of)
    threshold = GATE2_PER_PARAMETER * STATES * (STATES - 1)

    means = np.asarray(model.means_, dtype=float)[order]  # type: ignore[attr-defined]
    drift = tuple(float(row[0]) for row in means)
    flags: Dict[str, str] = {
        "imbalance": PROXY_IMBALANCE,
        "fit": "train" if (train_until_ts is not None or train_lengths is not None) else "in_sample",
    }
    if not converged:
        flags["convergence"] = "NOT_CONVERGED"
    fit = RegimeFit(
        states=states,
        session_of=session_of,
        transition=tuple(
            tuple(float(value) for value in row)
            for row in canonical_transition(model.transmat_, order)  # type: ignore[attr-defined]
        ),
        counts=tuple(tuple(int(value) for value in row) for row in counts),
        drift=drift,
        volatility=tuple(float(mean_volatility[int(state)]) for state in order),
        transitions=transitions,
        gate2_threshold=threshold,
        gate2_passed=transitions >= threshold,
        observations=int(features.shape[0]),
        sessions=len(lengths),
        log_likelihood=score,
        converged=converged,
        seeds=seeds,
        flags=flags,
    )
    if not fit.gate2_passed:
        flags["regime"] = (
            f"{GATE2_FAILED}: {transitions} смен < {threshold}; {REFERENCE_ONLY}"
        )
        return RegimeReport(
            instrument=instrument,
            fit=fit,
            survival_by_direction={LONG: None, SHORT: None},
            mtf_survival_by_direction={LONG: None, SHORT: None},
            tau_regime_minutes=None,
            tau_mtf_minutes=None,
            mtf_disagreement=None,
            gates={"regime": flags["regime"]},
        )

    survivals = {
        state: survival(counts, state=state, q=q, htf_minutes=htf_minutes)
        for state in range(STATES)
    }
    survival_by_direction: Dict[int, Optional[Survival]] = {}
    for direction in (LONG, SHORT):
        fav = fit.fav(direction)
        survival_by_direction[direction] = None if fav is None else survivals[fav]

    # В `τ_regime` участвуют только направления, прошедшие гейт: провал гейта — `REFERENCE_ONLY`,
    # и его горизонт не должен обрезать горизонт направления, которому режим доверяет (R4).
    tau_regime = trusted_tau(survival_by_direction)

    mtf_pairs: List[Tuple[SessionArrays, SessionArrays]] = []
    for session, _ in htf_pairs:
        aggregated = aggregate_minutes(session, mtf_minutes)
        if len(aggregated["close"]) >= 2:
            mtf_pairs.append((session, aggregated))
    mtf_features, mtf_lengths = regime_features([aggregated for _, aggregated in mtf_pairs])
    mtf_labels = model.predict(mtf_features, mtf_lengths)  # type: ignore[attr-defined]
    mtf_canonical = canonical_labels(mtf_labels, order)
    disagreement = mtf_consistency(
        [
            (aggregated["ts"], labels_part)
            for (_, aggregated), labels_part in zip(
                htf_pairs, split_by_lengths(states, lengths)
            )
        ],
        [
            (aggregated["ts"], labels_part)
            for (_, aggregated), labels_part in zip(
                mtf_pairs, split_by_lengths(mtf_canonical, mtf_lengths)
            )
        ],
    )

    mtf_counts, _ = markov_counts(
        tuple(int(state) for state in mtf_canonical), _session_of(mtf_lengths)
    )
    # 10m-выживание — по `fav` того же направления: состояния сопоставляются через один `fit`,
    # и подставлять лонговое состояние шорту нельзя (m11).
    mtf_survival_by_direction: Dict[int, Optional[Survival]] = {}
    tau_mtf_by_direction: Dict[int, Optional[int]] = {}
    reduce_by_mtf = disagreement is not None and disagreement > MTF_DISAGREEMENT_LIMIT
    for direction in (LONG, SHORT):
        fav = fit.fav(direction)
        item = (
            survival(mtf_counts, state=fav, q=q, htf_minutes=mtf_minutes)
            if fav is not None
            else None
        )
        mtf_survival_by_direction[direction] = item
        htf_item = survival_by_direction[direction]
        base = (
            htf_item.tau_minutes if htf_item is not None and htf_item.gate_passed else None
        )
        if base is None:
            tau_mtf_by_direction[direction] = None
        elif reduce_by_mtf:
            tau_mtf_by_direction[direction] = _min_tau(
                [base, item.tau_minutes if item is not None else None]
            )
        else:
            tau_mtf_by_direction[direction] = base
    tau_mtf = _min_tau(list(tau_mtf_by_direction.values()))

    gates = {
        "regime": GATE2_PASSED,
        "survival": "; ".join(
            f"{'long' if direction == LONG else 'short'}:"
            f"{_direction_status(survival_by_direction[direction])}"
            for direction in (LONG, SHORT)
        ),
    }
    if disagreement is not None and disagreement > MTF_DISAGREEMENT_LIMIT:
        gates["mtf"] = (
            f"MTF_DISAGREE: {disagreement:.2%} > {MTF_DISAGREEMENT_LIMIT:.0%}; "
            "τ_mtf = min(τ_regime, τ_10m)"
        )
    return RegimeReport(
        instrument=instrument,
        fit=fit,
        survival_by_direction=survival_by_direction,
        mtf_survival_by_direction=mtf_survival_by_direction,
        tau_regime_minutes=tau_regime,
        tau_mtf_minutes=tau_mtf,
        mtf_disagreement=disagreement,
        gates=gates,
    )


def _session_of(lengths: Sequence[int]) -> Tuple[int, ...]:
    return tuple(
        session_index for session_index, length in enumerate(lengths) for _ in range(length)
    )


def _min_tau(values: Sequence[Optional[int]]) -> Optional[int]:
    present = [int(value) for value in values if value is not None]
    return min(present) if present else None


def _empty_report(instrument: str, reason: str) -> RegimeReport:
    return RegimeReport(
        instrument=instrument,
        fit=None,
        survival_by_direction={LONG: None, SHORT: None},
        mtf_survival_by_direction={LONG: None, SHORT: None},
        tau_regime_minutes=None,
        tau_mtf_minutes=None,
        mtf_disagreement=None,
        gates={"regime": f"{GATE2_FAILED}: {reason}"},
    )


__all__ = [
    "DEFAULT_ITERATIONS",
    "DEFAULT_SEED",
    "DEFAULT_SEEDS",
    "DIRECTION_FAV_UNDEFINED",
    "DIRECTION_LOW_CONFIDENCE",
    "DIRECTION_PASS",
    "GATE2_FAILED",
    "GATE2_PASSED",
    "GATE2_PER_PARAMETER",
    "HTF_MINUTES",
    "LONG",
    "MTF_DISAGREEMENT_LIMIT",
    "MTF_MINUTES",
    "PROXY_IMBALANCE",
    "REFERENCE_ONLY",
    "RegimeError",
    "RegimeFit",
    "RegimeReport",
    "SHORT",
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
    "trusted_tau",
    "wilson_lower",
]
