"""Конверт артефакта: единицы, `n`, метод и флаги обязательны (§14 `buy_frame.md`).

Отчёт без единиц и числа наблюдений невозможно пересчитать, поэтому артефакт собирается
только через конверт: недостающее поле — ошибка, а не «пустая строка по умолчанию».
"""
from __future__ import annotations

from typing import Dict, Iterable, Mapping

from .units import ALLOWED_UNITS

REQUIRED_KEYS = ("units", "n", "method", "flags")


class ArtifactError(ValueError):
    """Артефакт неполон."""


def build_artifact(
    *,
    name: str,
    units: Mapping[str, str],
    n: Mapping[str, int],
    method: str,
    flags: Mapping[str, str],
    payload: Mapping[str, object],
) -> Dict[str, object]:
    """Собрать артефакт с обязательными полями."""
    artifact: Dict[str, object] = {
        "name": name,
        "units": dict(units),
        "n": dict(n),
        "method": method,
        "flags": dict(flags),
        "payload": dict(payload),
    }
    validate_artifact(artifact)
    return artifact


def validate_artifact(artifact: Mapping[str, object]) -> None:
    """Проверить обязательные поля и их непустоту."""
    missing = [key for key in REQUIRED_KEYS if key not in artifact]
    if missing:
        raise ArtifactError(f"artifact misses required fields: {', '.join(missing)}")
    if not artifact.get("method"):
        raise ArtifactError("artifact method is required")
    for key in ("units", "n"):
        value = artifact.get(key)
        if not isinstance(value, Mapping) or not value:
            raise ArtifactError(f"artifact {key} must be a non-empty mapping")
    # Единицы сверяются с закрытым списком: опечатка (`tick` вместо `ticks`) иначе проходит
    # молча, а контракт §2 как раз про это.
    unknown = {str(unit) for unit in artifact["units"].values()} - ALLOWED_UNITS  # type: ignore[union-attr]
    if unknown:
        raise ArtifactError(
            f"artifact uses unknown units: {', '.join(sorted(unknown))}; "
            f"allowed: {', '.join(sorted(ALLOWED_UNITS))}"
        )
    flags = artifact.get("flags")
    if not isinstance(flags, Mapping):
        raise ArtifactError("artifact flags must be a mapping (may be empty)")


def collect_flags(*sources: Mapping[str, str] | Iterable[str]) -> Dict[str, str]:
    """Свести флаги из нескольких источников без перезаписи более строгого значения."""
    merged: Dict[str, str] = {}
    for source in sources:
        items = source.items() if isinstance(source, Mapping) else ((str(item), "FLAG") for item in source)
        for key, value in items:
            merged.setdefault(str(key), str(value))
    return merged


__all__ = [
    "ArtifactError",
    "REQUIRED_KEYS",
    "build_artifact",
    "collect_flags",
    "validate_artifact",
]
