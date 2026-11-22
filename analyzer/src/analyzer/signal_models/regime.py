"""HMM-переключение режимов как фильтр входа — шаг 1.4 `to_do.md` §4.1.

`v2` фильтрует режим константой ADX. Здесь режим оценивается по данным: два состояния
Gaussian HMM на логе абсолютных доходностей окна (лог-волатильность); «спокойное» — состояние
с наименьшим средним. Вход mean reversion разрешён только в спокойном состоянии: в волатильном
отклонение цены чаще продолжение движения, а не возврат.

Модель stateful: `fit` переобучает HMM не чаще `refit_bars`, но текущее состояние пересчитывает
на каждом вызове. `hmmlearn` импортируется лениво — реестр и остальные модели работают без него.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, ClassVar, Mapping, Optional, Sequence

import numpy as np

from ..indicators import Indicators
from ..strategy import Signal, SignalType
from .base import as_int_param

logger = logging.getLogger(__name__)

_VERSION = "1.0"
HMM_PARAMS = ("window", "min_bars", "states", "refit_bars", "n_iter", "seed")


@dataclass(frozen=True)
class HmmConfig:
    """Окно оценки, параметры HMM и пороги входа mean reversion."""

    window: int = 240
    min_bars: int = 60
    states: int = 2
    refit_bars: int = 30
    n_iter: int = 100
    seed: int = 7
    z_entry: float = 2.0
    rsi_buy: float = 25.0
    rsi_sell: float = 75.0

    def __post_init__(self) -> None:
        if not isinstance(self.window, int) or self.window < 30:
            raise ValueError("hmm: window must be an integer >= 30")
        if not isinstance(self.min_bars, int) or self.min_bars < 10:
            raise ValueError("hmm: min_bars must be an integer >= 10")
        if self.min_bars > self.window:
            raise ValueError("hmm: min_bars must not exceed window")
        if not 2 <= self.states <= 6:
            raise ValueError("hmm: states must be in [2, 6]")
        if not isinstance(self.refit_bars, int) or self.refit_bars < 1:
            raise ValueError("hmm: refit_bars must be an integer >= 1")
        if self.n_iter < 1:
            raise ValueError("hmm: n_iter must be positive")
        if self.z_entry <= 0:
            raise ValueError("hmm: z_entry must be positive")
        if not 0 < self.rsi_buy < 50:
            raise ValueError("hmm: rsi_buy must be in (0, 50)")
        if not 50 < self.rsi_sell < 100:
            raise ValueError("hmm: rsi_sell must be in (50, 100)")

    @property
    def label(self) -> str:
        return f"hmm-{self.states}st-z{self.z_entry:g}-w{self.window}"


@dataclass
class HmmRegimeModel:
    """Mean reversion, разрешённый только в спокойном режиме HMM."""

    config: HmmConfig = HmmConfig()
    _model: Any = field(default=None, init=False, repr=False)
    _calm_state: Optional[int] = field(default=None, init=False, repr=False)
    _current_state: Optional[int] = field(default=None, init=False, repr=False)
    _bars_since_fit: int = field(default=0, init=False, repr=False)

    name: ClassVar[str] = "hmm"
    version: ClassVar[str] = _VERSION

    @property
    def warmup(self) -> int:
        return self.config.window

    @property
    def label(self) -> str:
        return self.config.label

    def fit(self, history: Sequence[Any]) -> None:
        closes = np.asarray(
            [float(bar.close) for bar in history][-self.config.window :], dtype=float
        )
        if closes.size < self.config.min_bars + 1 or np.any(closes <= 0):
            self._reset()
            return

        returns = np.diff(np.log(closes))
        if returns.size < self.config.min_bars or not np.all(np.isfinite(returns)):
            self._reset()
            return

        # Признак — лог |доходности| (лог-волатильность): знаковые доходности имеют среднее ~0
        # в обоих режимах, а модуль в исходном масштабе разваливает kmeans-инициализацию
        # hmmlearn (волатильные точки доминируют, спокойный режим теряется).
        feature = np.log(np.maximum(np.abs(returns), 1e-12))
        features = feature.reshape(-1, 1)
        if self._model is None or self._bars_since_fit >= self.config.refit_bars:
            if not self._refit(features):
                return
        else:
            self._bars_since_fit += 1

        try:
            states = self._model.predict(features)
        except Exception:  # hmmlearn на вырожденных данных может бросить из predict
            logger.debug("HMM predict failed", exc_info=True)
            self._reset()
            return
        self._current_state = int(states[-1])

    def _refit(self, features: np.ndarray) -> bool:
        # Ленивый импорт: без hmmlearn остальные модели и реестр остаются рабочими.
        from hmmlearn.hmm import GaussianHMM

        states = self.config.states
        means, covars = _initial_parameters(features[:, 0], states)
        model = GaussianHMM(
            n_components=states,
            covariance_type="diag",
            n_iter=self.config.n_iter,
            random_state=self.config.seed,
            tol=1e-4,
            min_covar=1e-6,
            # Инициализация по квантилям, а не kmeans: в лог-волатильности kmeans стабильно
            # выбирает неудачный бассейн и EM не выходит из него.
            init_params="",
        )
        model.means_ = means
        model.covars_ = covars
        model.startprob_ = np.full(states, 1.0 / states)
        transition = np.full((states, states), 0.1 / (states - 1))
        np.fill_diagonal(transition, 0.9)
        model.transmat_ = transition
        try:
            model.fit(features)
        except ValueError:
            logger.debug("HMM fit failed on degenerate window", exc_info=True)
            self._reset()
            return False

        state_means = np.asarray(model.means_).reshape(states, -1).mean(axis=1)
        self._model = model
        # «Спокойное» состояние — с наименьшей средней лог-волатильностью.
        self._calm_state = int(np.argmin(state_means))
        self._bars_since_fit = 0
        return True

    def _reset(self) -> None:
        self._model = None
        self._calm_state = None
        self._current_state = None
        self._bars_since_fit = 0

    def params(self) -> Mapping[str, Any]:
        payload: dict[str, Any] = {
            "window": self.config.window,
            "min_bars": self.config.min_bars,
            "states": self.config.states,
            "refit_bars": self.config.refit_bars,
            "n_iter": self.config.n_iter,
            "seed": self.config.seed,
            "z_entry": self.config.z_entry,
            "rsi_buy": self.config.rsi_buy,
            "rsi_sell": self.config.rsi_sell,
            "fitted": self._current_state is not None,
        }
        if self._calm_state is not None:
            payload["calm_state"] = self._calm_state
        if self._current_state is not None:
            payload["current_state"] = self._current_state
        return MappingProxyType(payload)

    def predict(
        self,
        ind: Indicators,
        price: float,
        atr_mult_sl: float = 1.5,
        atr_mult_tp: float = 2.0,
    ) -> Signal:
        if self._current_state is None or self._calm_state is None:
            return Signal(SignalType.HOLD, 0.0, price, None, None, "hmm: not fitted")
        if ind.zscore is None or ind.rsi is None or ind.atr is None or ind.atr <= 0:
            return Signal(SignalType.HOLD, 0.0, price, None, None, "insufficient data")
        if self._current_state != self._calm_state:
            return Signal(
                SignalType.HOLD,
                0.0,
                price,
                None,
                None,
                f"hmm: volatile regime (state {self._current_state})",
            )

        if ind.zscore <= -self.config.z_entry and ind.rsi < self.config.rsi_buy:
            return Signal(
                SignalType.BUY,
                _confidence(-ind.zscore - self.config.z_entry, self.config.z_entry),
                price,
                round(price - atr_mult_sl * ind.atr, 9),
                round(price + atr_mult_tp * ind.atr, 9),
                f"hmm calm: z {ind.zscore:.2f} <= -{self.config.z_entry:g}",
            )
        if ind.zscore >= self.config.z_entry and ind.rsi > self.config.rsi_sell:
            return Signal(
                SignalType.SELL,
                _confidence(ind.zscore - self.config.z_entry, self.config.z_entry),
                price,
                round(price + atr_mult_sl * ind.atr, 9),
                round(price - atr_mult_tp * ind.atr, 9),
                f"hmm calm: z {ind.zscore:.2f} >= {self.config.z_entry:g}",
            )
        return Signal(SignalType.HOLD, 0.0, price, None, None, "hmm: no signal")

    def __call__(
        self, ind: Indicators, price: float, atr_mult_sl: float, atr_mult_tp: float
    ) -> Signal:
        """Совместимость с `backtest.engine.StrategyFn`."""
        return self.predict(ind, price, atr_mult_sl, atr_mult_tp)


def _confidence(excess: float, scale: float) -> float:
    if scale <= 0:
        return 0.5
    return min(1.0, 0.5 + 0.5 * (max(excess, 0.0) / scale))


def _initial_parameters(feature: np.ndarray, states: int) -> tuple[np.ndarray, np.ndarray]:
    """Начальные средние и дисперсии по квантилям признака.

    Детерминированная инициализация вместо kmeans: на лог-волатильности kmeans стабильно
    выбирает бассейн, из которого EM не выходит (оба состояния описывают волатильный режим).
    """
    edges = np.quantile(feature, np.linspace(0.0, 1.0, states + 1))
    means = np.empty((states, 1))
    covars = np.empty((states, 1))
    for index in range(states):
        chunk = feature[(feature >= edges[index]) & (feature <= edges[index + 1])]
        if chunk.size == 0:
            chunk = feature
        means[index, 0] = float(chunk.mean())
        covars[index, 0] = float(chunk.var()) + 1e-6
    return means, covars


def hmm_config(options: Any) -> HmmConfig:
    """Конфиг из флагов запуска: `--z-entry`, `--rsi-*` и `--model-param`."""
    raw = dict(getattr(options, "model_params", {}) or {})
    unknown = sorted(set(raw) - set(HMM_PARAMS))
    if unknown:
        raise ValueError(
            f"hmm: неизвестные параметры {unknown}; доступны {list(HMM_PARAMS)}"
        )
    defaults = HmmConfig()
    rsi_buy = defaults.rsi_buy if options.rsi_buy is None else options.rsi_buy
    rsi_sell = defaults.rsi_sell if options.rsi_sell is None else options.rsi_sell
    return HmmConfig(
        window=as_int_param("hmm", "window", raw.get("window", defaults.window)),
        min_bars=as_int_param("hmm", "min_bars", raw.get("min_bars", defaults.min_bars)),
        states=as_int_param("hmm", "states", raw.get("states", defaults.states)),
        refit_bars=as_int_param(
            "hmm", "refit_bars", raw.get("refit_bars", defaults.refit_bars)
        ),
        n_iter=as_int_param("hmm", "n_iter", raw.get("n_iter", defaults.n_iter)),
        seed=as_int_param("hmm", "seed", raw.get("seed", defaults.seed)),
        z_entry=float(options.z_entry),
        rsi_buy=float(rsi_buy),
        rsi_sell=float(rsi_sell),
    )
