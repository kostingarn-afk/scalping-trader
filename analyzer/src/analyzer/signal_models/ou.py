"""Mean reversion на процессе Орнштейна–Уленбека — шаг 1.2 `to_do.md` §4.1.

Модель оценивает параметры возврата к среднему на скользящем окне и входит против отклонения
от оценённого уровня. Основание — `docs/mat_analyze.md` §2: дискретное решение OU на шаге Δt —
это AR(1), поэтому θ = −ln(b)/Δt, μ = a/(1−b), а σ пересчитывается из остатка.

Оценка идёт в `fit` (прогон вызывает её на окне перед каждым предсказанием), поэтому модель
stateful: конфиг frozen, состояние перезаписывается на каждом баре.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, ClassVar, Mapping, Optional, Sequence

import numpy as np

from ..indicators import Indicators
from ..strategy import Signal, SignalType

_VERSION = "1.0"
# Параметры запуска, которые принимает `--model-param key=value`.
OU_PARAMS = ("window", "min_bars", "max_half_life_bars")


@dataclass(frozen=True)
class OuParams:
    """Оценённые параметры процесса: dP = θ(μ − P)dt + σ dW."""

    mu: float
    theta: float
    sigma: float
    sigma_eq: float
    half_life_bars: float


def estimate_ou(closes: Sequence[float], dt: float = 1.0) -> Optional[OuParams]:
    """Оценить параметры OU по AR(1) на ряду закрытий.

    Возвращает None, если ряд не возвращается к среднему (наклон вне (0, 1)), короче трёх
    точек, содержит нечисловые значения или даёт нулевую волатильность: фиктивные параметры
    опаснее отказа — они превращают любое отклонение в сигнал.
    """
    values = np.asarray(closes, dtype=float)
    if values.size < 3 or not np.all(np.isfinite(values)):
        return None

    prev, nxt = values[:-1], values[1:]
    mean_prev = float(prev.mean())
    var_prev = float(((prev - mean_prev) ** 2).mean())
    if var_prev <= 0.0:
        return None

    # OLS-наклон AR(1); при центрировании совпадает с ρ из `docs/mat_analyze.md` §2.
    slope = float(((prev - mean_prev) * (nxt - float(nxt.mean()))).mean()) / var_prev
    if not 0.0 < slope < 1.0:
        return None

    intercept = float(nxt.mean()) - slope * mean_prev
    theta = -math.log(slope) / dt
    mu = intercept / (1.0 - slope)

    residual = nxt - (intercept + slope * prev)
    sigma_eps = float(np.sqrt((residual**2).mean()))
    if sigma_eps <= 0.0:
        return None

    # Var(X_{t+1}|X_t) = σ²(1 − b²)/(2θ), поэтому σ = σ_ε·√(2θ/(1 − b²)).
    sigma = sigma_eps * math.sqrt(2.0 * theta / (1.0 - slope * slope))
    sigma_eq = sigma / math.sqrt(2.0 * theta)
    if not math.isfinite(sigma_eq) or sigma_eq <= 0.0:
        return None

    return OuParams(
        mu=mu,
        theta=theta,
        sigma=sigma,
        sigma_eq=sigma_eq,
        half_life_bars=math.log(2.0) / theta,
    )


@dataclass(frozen=True)
class OuConfig:
    """Окно оценки и пороги входа. Значения меняются запуском, не кодом."""

    window: int = 240
    min_bars: int = 30
    z_entry: float = 2.0
    max_half_life_bars: float = 10.0

    def __post_init__(self) -> None:
        if not isinstance(self.window, int) or self.window < 10:
            raise ValueError("ou: window must be an integer >= 10")
        if not isinstance(self.min_bars, int) or self.min_bars < 3:
            raise ValueError("ou: min_bars must be an integer >= 3")
        if self.min_bars > self.window:
            raise ValueError("ou: min_bars must not exceed window")
        if self.z_entry <= 0:
            raise ValueError("ou: z_entry must be positive")
        if self.max_half_life_bars <= 0:
            raise ValueError("ou: max_half_life_bars must be positive")

    @property
    def label(self) -> str:
        return f"ou-z{self.z_entry:g}-w{self.window}"


@dataclass
class OuMeanReversionModel:
    """Вход против отклонения от оценённого среднего; параметры — из последнего `fit`."""

    config: OuConfig = OuConfig()
    _params: Optional[OuParams] = field(default=None, init=False, repr=False)

    name: ClassVar[str] = "ou"
    version: ClassVar[str] = _VERSION

    @property
    def warmup(self) -> int:
        """Сколько баров нужно для оценки: прогон обязан подать не меньше."""
        return self.config.window

    @property
    def label(self) -> str:
        return self.config.label

    def fit(self, history: Sequence[Any]) -> None:
        window = [float(bar.close) for bar in history][-self.config.window :]
        self._params = (
            estimate_ou(window) if len(window) >= self.config.min_bars else None
        )

    def params(self) -> Mapping[str, Any]:
        payload: dict[str, Any] = {
            "window": self.config.window,
            "min_bars": self.config.min_bars,
            "z_entry": self.config.z_entry,
            "max_half_life_bars": self.config.max_half_life_bars,
            "fitted": self._params is not None,
        }
        if self._params is not None:
            payload.update(
                {
                    "theta": self._params.theta,
                    "mu": self._params.mu,
                    "sigma": self._params.sigma,
                    "sigma_eq": self._params.sigma_eq,
                    "half_life_bars": self._params.half_life_bars,
                }
            )
        return MappingProxyType(payload)

    def predict(
        self,
        ind: Indicators,
        price: float,
        atr_mult_sl: float = 1.5,
        atr_mult_tp: float = 2.0,
    ) -> Signal:
        params = self._params
        if params is None:
            return Signal(SignalType.HOLD, 0.0, price, None, None, "ou: not fitted")
        if params.half_life_bars > self.config.max_half_life_bars:
            return Signal(
                SignalType.HOLD,
                0.0,
                price,
                None,
                None,
                f"ou: slow mean reversion (half-life {params.half_life_bars:.1f} > "
                f"{self.config.max_half_life_bars:g})",
            )
        if ind.atr is None or ind.atr <= 0:
            return Signal(SignalType.HOLD, 0.0, price, None, None, "insufficient data")

        z = (price - params.mu) / params.sigma_eq
        z_entry = self.config.z_entry
        if z <= -z_entry:
            return Signal(
                SignalType.BUY,
                _confidence(-z - z_entry, z_entry),
                price,
                round(price - atr_mult_sl * ind.atr, 9),
                round(price + atr_mult_tp * ind.atr, 9),
                f"ou: z {z:.2f} <= -{z_entry:g}",
            )
        if z >= z_entry:
            return Signal(
                SignalType.SELL,
                _confidence(z - z_entry, z_entry),
                price,
                round(price + atr_mult_sl * ind.atr, 9),
                round(price - atr_mult_tp * ind.atr, 9),
                f"ou: z {z:.2f} >= {z_entry:g}",
            )
        return Signal(SignalType.HOLD, 0.0, price, None, None, "ou: no signal")

    def __call__(
        self, ind: Indicators, price: float, atr_mult_sl: float, atr_mult_tp: float
    ) -> Signal:
        """Совместимость с `backtest.engine.StrategyFn`."""
        return self.predict(ind, price, atr_mult_sl, atr_mult_tp)


def _confidence(excess: float, scale: float) -> float:
    if scale <= 0:
        return 0.5
    return min(1.0, 0.5 + 0.5 * (max(excess, 0.0) / scale))


def _as_int(name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"ou: {name} must be an integer, got {value!r}")
    if float(value) != int(value):
        raise ValueError(f"ou: {name} must be an integer, got {value!r}")
    return int(value)


def ou_config(options: Any) -> OuConfig:
    """Конфиг из флагов запуска: `--z-entry` и `--model-param key=value`."""
    raw = dict(getattr(options, "model_params", {}) or {})
    unknown = sorted(set(raw) - set(OU_PARAMS))
    if unknown:
        raise ValueError(
            f"ou: неизвестные параметры {unknown}; доступны {list(OU_PARAMS)}"
        )
    defaults = OuConfig()
    return OuConfig(
        window=_as_int("window", raw.get("window", defaults.window)),
        min_bars=_as_int("min_bars", raw.get("min_bars", defaults.min_bars)),
        z_entry=float(options.z_entry),
        max_half_life_bars=float(
            raw.get("max_half_life_bars", defaults.max_half_life_bars)
        ),
    )
