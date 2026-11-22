"""Trial registry — журнал опытов.

Без реестра DSR невычислим (§10.4 `buy_frame.md`): число испытаний `N_trials` — вход
поправки на множественное тестирование. Журнал только дописывается: правка опыта — новая
строка, иначе `N_trials` занижается задним числом.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Mapping, Optional, Tuple

REQUIRED_FIELDS = ("model", "frame", "window")


class RegistryError(ValueError):
    """Реестр опытов не позволяет выполнить операцию."""


@dataclass(frozen=True)
class TrialRecord:
    id: str
    created_at: str
    params: Dict[str, object]

    def to_dict(self) -> Dict[str, object]:
        return {"id": self.id, "created_at": self.created_at, "params": dict(self.params)}

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "TrialRecord":
        params = payload.get("params")
        if not isinstance(params, Mapping):
            raise RegistryError("trial record is missing params")
        trial_id = payload.get("id")
        created_at = payload.get("created_at")
        if not isinstance(trial_id, str) or not trial_id:
            raise RegistryError("trial record is missing id")
        if not isinstance(created_at, str) or not created_at:
            raise RegistryError(f"trial {trial_id}: created_at is missing")
        return cls(id=trial_id, created_at=created_at, params=dict(params))


class TrialRegistry:
    """JSONL-журнал опытов в указанном файле."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def register(
        self,
        params: Mapping[str, object],
        *,
        trial_id: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> TrialRecord:
        """Дописать опыт. Обязательные поля фиксируют, что именно варьировалось."""
        normalized = dict(params)
        missing = [field for field in REQUIRED_FIELDS if not normalized.get(field)]
        if missing:
            raise RegistryError(f"trial is missing fields: {', '.join(missing)}")
        identifier = trial_id or uuid.uuid4().hex[:12]
        stamp = created_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
        record = TrialRecord(id=identifier, created_at=stamp, params=normalized)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True))
            handle.write("\n")
        return record

    def records(self) -> Tuple[TrialRecord, ...]:
        if not self.path.exists():
            return ()
        result: List[TrialRecord] = []
        with self.path.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise RegistryError(f"{self.path}:{line_no}: invalid JSON ({exc})") from exc
                result.append(TrialRecord.from_dict(payload))
        return tuple(result)

    @property
    def n_trials(self) -> int:
        return len(self.records())

    def n_trials_for_dsr(self) -> int:
        """Число испытаний для поправки DSR; пустой реестр — отказ, а не «один опыт»."""
        count = self.n_trials
        if count == 0:
            raise RegistryError(
                "trial registry is empty: DSR cannot be computed without N_trials"
            )
        return count

    def find(self, trial_id: str) -> Optional[TrialRecord]:
        for record in self.records():
            if record.id == trial_id:
                return record
        return None

    def __iter__(self) -> Iterator[TrialRecord]:
        return iter(self.records())


__all__ = [
    "REQUIRED_FIELDS",
    "RegistryError",
    "TrialRecord",
    "TrialRegistry",
]
