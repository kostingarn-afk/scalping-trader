"""Политика фазы A: объявляется до расчётов и замораживается.

Значения входят в статистику и в гейты, поэтому живут в данных (конфиг + env), а не в коде:
смена тарифа поведения не должна требовать правки Python. `λ` намеренно не поле — она
производная от `|Z_entry|`, иначе можно объявить два противоречащих значения.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Mapping, Optional, Tuple

from .registry import TrialRecord, TrialRegistry

ENV_PREFIX = "RESEARCH_"
UNVERIFIED = "UNVERIFIED"

#: Условия «нет сделки» (§6 `holding_exiting.md`): выполняются до поиска входа.
NO_TRADE_CONDITIONS: Tuple[str, ...] = (
    "нет допустимого τ (EV_LCB ≤ 0 на всей сетке)",
    "γ_t(fav) < 1 − q",
    "τ_session < 0 (позиция не успеет закрыться)",
    "активен breaker или jump-фильтр",
)


class PolicyError(ValueError):
    """Политика объявлена некорректно."""


@dataclass(frozen=True)
class PolicyParams:
    """Параметры политики фазы A. Значения — стартовые, не результат подбора."""

    q: float = 0.10
    alpha_total: float = 0.05
    z_entry: float = 2.0
    rho_max: float = 0.25
    impact_budget: float = 0.25
    tau_cap_minutes: int = 60
    buffer_minutes: int = 5
    k_barrier_tp: float = 2.0
    k_barrier_sl: float = 1.5

    def __post_init__(self) -> None:
        if not 0.0 < self.q < 1.0:
            raise PolicyError(f"q must be in (0, 1), got {self.q}")
        if not 0.0 < self.alpha_total < 1.0:
            raise PolicyError(f"alpha_total must be in (0, 1), got {self.alpha_total}")
        if not self.z_entry > 0:
            raise PolicyError(f"z_entry must be positive, got {self.z_entry}")
        if not 0.0 < self.rho_max <= 1.0:
            raise PolicyError(f"rho_max must be in (0, 1], got {self.rho_max}")
        if not 0.0 <= self.impact_budget < 1.0:
            raise PolicyError(f"impact_budget must be in [0, 1), got {self.impact_budget}")
        if not isinstance(self.tau_cap_minutes, int) or self.tau_cap_minutes < 1:
            raise PolicyError(f"tau_cap_minutes must be an integer >= 1, got {self.tau_cap_minutes}")
        if not isinstance(self.buffer_minutes, int) or self.buffer_minutes < 0:
            raise PolicyError(f"buffer_minutes must be an integer >= 0, got {self.buffer_minutes}")
        if not self.k_barrier_tp > 0 or not self.k_barrier_sl > 0:
            raise PolicyError("barrier multipliers must be positive")

    @property
    def lambda_risk(self) -> float:
        """Риск-барьер удержания: `λ = |Z_entry|/(2√2)` (λ-форма правила удержания)."""
        return self.z_entry / (2.0 * math.sqrt(2.0))

    def to_mapping(self) -> Dict[str, float]:
        return {field.name: getattr(self, field.name) for field in fields(self)}

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> "PolicyParams":
        known = {field.name for field in fields(cls)}
        unknown = set(payload) - known
        if unknown:
            raise PolicyError(f"unknown policy fields: {', '.join(sorted(unknown))}")
        values = dict(payload)
        for name in ("tau_cap_minutes", "buffer_minutes"):
            if name in values:
                values[name] = int(values[name])
        for name in ("q", "alpha_total", "z_entry", "rho_max", "impact_budget",
                     "k_barrier_tp", "k_barrier_sl"):
            if name in values:
                values[name] = float(values[name])
        return cls(**values)  # type: ignore[arg-type]

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> "PolicyParams":
        """Прочитать переопределения из `RESEARCH_*`; неизвестный суффикс — ошибка."""
        source = os.environ if environ is None else environ
        known = {field.name: ENV_PREFIX + field.name.upper() for field in fields(cls)}
        by_env = {env_name: field_name for field_name, env_name in known.items()}
        values: Dict[str, object] = {}
        unknown = [
            name
            for name in source
            if name.startswith(ENV_PREFIX) and name not in by_env
        ]
        if unknown:
            raise PolicyError(f"unknown policy env vars: {', '.join(sorted(unknown))}")
        for env_name, field_name in by_env.items():
            raw = source.get(env_name)
            if raw is None or raw == "":
                continue
            values[field_name] = raw
        return cls.from_mapping(values)


def load_policy(
    yaml_path: Optional[Path] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Tuple[PolicyParams, Dict[str, str]]:
    """Собрать политику: значения по умолчанию → YAML → env.

    Возвращает параметры и карту `поле → источник`, которая попадает в `policy_inputs`:
    без неё нельзя понять, чем именно считали.
    """
    sources: Dict[str, str] = {field.name: "default" for field in fields(PolicyParams)}
    payload: Dict[str, object] = {}

    if yaml_path is not None:
        import yaml

        raw = yaml.safe_load(Path(yaml_path).read_text(encoding="utf-8")) or {}
        if not isinstance(raw, Mapping):
            raise PolicyError(f"{yaml_path}: policy file must contain a mapping")
        policy_part = raw.get("policy", raw)
        if not isinstance(policy_part, Mapping):
            raise PolicyError(f"{yaml_path}: 'policy' section must be a mapping")
        payload.update(policy_part)
        for name in policy_part:
            sources[str(name)] = "yaml"

    env_values = PolicyParams.from_env(environ).to_mapping()
    defaults = PolicyParams().to_mapping()
    for name, value in env_values.items():
        if value != defaults[name]:
            payload[name] = value
            sources[name] = "env"

    params = PolicyParams.from_mapping(payload)
    return params, {name: origin for name, origin in sources.items() if origin != "default"}


@dataclass(frozen=True)
class Barriers:
    """Барьеры в целых тиках, выведенные из `σ_1`."""

    tp_ticks: int
    sl_ticks: int
    k_tp: float
    k_sl: float
    sigma_ticks: float

    def __post_init__(self) -> None:
        if self.tp_ticks < 1 or self.sl_ticks < 1:
            raise PolicyError("barriers must be at least one tick")

    def to_mapping(self) -> Dict[str, object]:
        return {
            "tp_ticks": self.tp_ticks,
            "sl_ticks": self.sl_ticks,
            "k_tp": self.k_tp,
            "k_sl": self.k_sl,
            "sigma_ticks": round(self.sigma_ticks, 6),
        }


def declare_barriers(sigma_1_ticks: float, params: PolicyParams) -> Barriers:
    """`a = k_a·σ_1`, `b = k_b·σ_1`, округление половины вверх, минимум один тик."""
    if not sigma_1_ticks > 0:
        raise PolicyError(f"sigma_1 in ticks must be positive, got {sigma_1_ticks}")
    return Barriers(
        tp_ticks=_to_ticks(params.k_barrier_tp * sigma_1_ticks),
        sl_ticks=_to_ticks(params.k_barrier_sl * sigma_1_ticks),
        k_tp=params.k_barrier_tp,
        k_sl=params.k_barrier_sl,
        sigma_ticks=sigma_1_ticks,
    )


def _to_ticks(value: float) -> int:
    return max(1, int(math.floor(value + 0.5)))


@dataclass(frozen=True)
class FrozenPolicy:
    """Замороженная политика инструмента, привязанная к записи registry."""

    instrument: str
    params: PolicyParams
    barriers: Barriers
    registry_id: str
    frozen_at: str
    anchor_mu: Optional[float] = None

    def require_registered(self) -> "FrozenPolicy":
        if not self.registry_id:
            raise PolicyError("policy is not frozen: register it in trial registry first")
        return self

    def to_artifact(self) -> Dict[str, object]:
        return {
            "instrument": self.instrument,
            "registry_id": self.registry_id,
            "frozen_at": self.frozen_at,
            "policy": self.params.to_mapping(),
            "lambda_risk": round(self.params.lambda_risk, 6),
            "barriers": self.barriers.to_mapping(),
            "sizing": {
                "positions": 1,
                "rho_max": self.params.rho_max,
                "kappa_cap": "одна позиция на инструмент",
            },
            "flat_at_close": {"buffer_minutes": self.params.buffer_minutes},
            "no_trade": list(NO_TRADE_CONDITIONS),
            "anchor_mu": self.anchor_mu,
        }


def freeze_policy(
    registry: TrialRegistry,
    params: PolicyParams,
    instrument: str,
    sigma_1_ticks: float,
    *,
    anchor_mu: Optional[float] = None,
    frame: str = "1min",
    created_at: Optional[str] = None,
) -> FrozenPolicy:
    """Заморозить политику и записать её в registry.

    Запись создаётся до расчёта EV: иначе барьеры можно подогнать после результата.
    """
    barriers = declare_barriers(sigma_1_ticks, params)
    stamp = created_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    record = registry.register(
        {
            "model": "phase_a",
            "frame": frame,
            "window": f"frozen:{stamp}",
            "instrument": instrument,
            "policy": params.to_mapping(),
            "barriers": barriers.to_mapping(),
        },
        created_at=stamp,
    )
    return FrozenPolicy(
        instrument=instrument,
        params=params,
        barriers=barriers,
        registry_id=record.id,
        frozen_at=stamp,
        anchor_mu=anchor_mu,
    )


def anchor_freeze_entry(
    frozen: FrozenPolicy, registry_record: Optional[TrialRecord] = None
) -> Dict[str, object]:
    """Запись `anchor_freeze_log`: якорь `μ` не сдвигается задним числом."""
    return {
        "instrument": frozen.instrument,
        "anchor_mu": frozen.anchor_mu,
        "frozen_at": frozen.frozen_at,
        "registry_id": frozen.registry_id,
        "registry_created_at": registry_record.created_at if registry_record else None,
    }


__all__ = [
    "Barriers",
    "ENV_PREFIX",
    "FrozenPolicy",
    "NO_TRADE_CONDITIONS",
    "PolicyError",
    "PolicyParams",
    "anchor_freeze_entry",
    "declare_barriers",
    "freeze_policy",
    "load_policy",
]
