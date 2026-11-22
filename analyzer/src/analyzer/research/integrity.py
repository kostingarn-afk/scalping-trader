"""Gate D — целостность выгрузки.

Отчёт не бросает исключение: на реальных данных исследование должно продолжиться с пометками,
а критичный провал — это исход проверки, а не сбой программы. Код возврата решает CLI.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from statistics import median
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ..backtest.models import Bar
from .sessions import SessionSchedule
from .ticks import TickSize

CRITICAL = "critical"
WARNING = "warning"

#: Доля пропущенных баров внутри сессий, выше которой провал считается критичным.
MISSING_RATIO_CRITICAL = 0.5
#: Доля баров с нулевым объёмом, выше которой выдаётся предупреждение.
ZERO_VOLUME_WARN_RATIO = 0.01
#: Кратность медианного |ΔP|, выше которой движение помечается аномалией.
ANOMALY_FACTOR = 20.0


@dataclass(frozen=True)
class Finding:
    level: str
    check: str
    detail: str

    def __post_init__(self) -> None:
        if self.level not in (CRITICAL, WARNING):
            raise ValueError(f"unknown finding level {self.level!r}")

    def to_dict(self) -> Dict[str, str]:
        return {"level": self.level, "check": self.check, "detail": self.detail}


@dataclass(frozen=True)
class IntegrityReport:
    instrument: str
    bars: int
    findings: Tuple[Finding, ...] = ()
    stats: Dict[str, float] = field(default_factory=dict)

    @property
    def has_critical(self) -> bool:
        return any(finding.level == CRITICAL for finding in self.findings)

    def of_level(self, level: str) -> Tuple[Finding, ...]:
        return tuple(finding for finding in self.findings if finding.level == level)

    def to_dict(self) -> Dict[str, object]:
        return {
            "instrument": self.instrument,
            "bars": self.bars,
            "stats": dict(self.stats),
            "findings": [finding.to_dict() for finding in self.findings],
            "critical": self.has_critical,
        }

    def format_markdown(self) -> str:
        lines = [f"### {self.instrument}: {self.bars} баров"]
        lines.append("")
        lines.append("| Показатель | Значение |")
        lines.append("|---|---|")
        for name, value in self.stats.items():
            lines.append(f"| {name} | {_format_number(value)} |")
        lines.append("")
        if not self.findings:
            lines.append("Замечаний нет.")
            return "\n".join(lines)
        lines.append("| Уровень | Проверка | Деталь |")
        lines.append("|---|---|---|")
        for finding in self.findings:
            lines.append(f"| {finding.level} | {finding.check} | {finding.detail} |")
        return "\n".join(lines)


def check_integrity(
    bars: Sequence[Bar],
    tick: TickSize,
    *,
    instrument: str = "",
    interval_minutes: int = 1,
    schedule: Optional[SessionSchedule] = None,
    anomaly_factor: float = ANOMALY_FACTOR,
    zero_volume_warn_ratio: float = ZERO_VOLUME_WARN_RATIO,
) -> IntegrityReport:
    """Проверить выгрузку по §2 `buy_frame.md`.

    Без расписания проверки «пропуски внутри сессий» и «бары вне сессий» невозможны: тогда
    в отчёт попадает пометка, а не молчаливое «всё хорошо».
    """
    if interval_minutes < 1:
        raise ValueError("interval_minutes must be at least 1")
    if not bars:
        raise ValueError("cannot check integrity of an empty series")

    findings: List[Finding] = []
    stats: Dict[str, float] = {"bars": float(len(bars))}

    _check_order(bars, findings)
    _check_volumes(bars, findings, stats, zero_volume_warn_ratio)
    _check_prices(bars, tick, findings, stats)

    session_pairs = _session_pairs(bars, schedule)
    if schedule is None:
        findings.append(
            Finding(WARNING, "sessions", "расписание не задано: проверки сессий пропущены")
        )
        gaps = _gaps(bars, interval_minutes)
        stats["missing_bars"] = float(gaps[0])
        stats["bars_outside_session"] = 0.0
        stats["overnight_returns"] = 0.0
    else:
        outside = [bar for bar in bars if not schedule.is_open(bar.ts)]
        stats["bars_outside_session"] = float(len(outside))
        if outside:
            findings.append(
                Finding(
                    WARNING,
                    "bars_outside_session",
                    f"{len(outside)} баров вне сессии, первый {outside[0].ts.isoformat()}",
                )
            )
        gaps = _gaps_in_sessions(bars, interval_minutes, schedule)
        overnight = len(bars) - 1 - len(session_pairs)
        stats["overnight_returns"] = float(overnight)
        if overnight:
            findings.append(
                Finding(
                    WARNING,
                    "overnight_returns",
                    f"{overnight} переходов через границу сессии исключаются из статистики",
                )
            )
        stats["missing_bars"] = float(gaps[0])
        _check_auction_prints(bars, schedule, findings, stats)

    expected = max(len(bars) + int(stats["missing_bars"]), 1)
    missing_ratio = stats["missing_bars"] / expected
    stats["missing_ratio"] = missing_ratio
    if missing_ratio > MISSING_RATIO_CRITICAL:
        findings.append(
            Finding(
                CRITICAL,
                "missing_bars",
                f"пропущено {int(stats['missing_bars'])} баров ({missing_ratio:.1%} ожидаемых)",
            )
        )
    elif stats["missing_bars"]:
        findings.append(
            Finding(
                WARNING,
                "missing_bars",
                f"пропущено {int(stats['missing_bars'])} баров ({missing_ratio:.1%} ожидаемых)",
            )
        )

    _check_moves(session_pairs, tick, findings, stats, anomaly_factor)
    return IntegrityReport(
        instrument=instrument,
        bars=len(bars),
        findings=tuple(findings),
        stats=stats,
    )


def _check_order(bars: Sequence[Bar], findings: List[Finding]) -> None:
    seen = set()
    duplicates = 0
    regressions = 0
    for previous, current in zip(bars, bars[1:]):
        if current.ts <= previous.ts:
            regressions += 1
    for bar in bars:
        if bar.ts in seen:
            duplicates += 1
        seen.add(bar.ts)
    if duplicates or regressions:
        findings.append(
            Finding(
                CRITICAL,
                "timestamp_order",
                f"дубликатов {duplicates}, нарушений возрастания {regressions}",
            )
        )


def _check_volumes(
    bars: Sequence[Bar],
    findings: List[Finding],
    stats: Dict[str, float],
    warn_ratio: float,
) -> None:
    zero = sum(1 for bar in bars if bar.volume == 0)
    ratio = zero / len(bars)
    stats["zero_volume_bars"] = float(zero)
    stats["zero_volume_ratio"] = ratio
    if ratio > warn_ratio:
        findings.append(
            Finding(WARNING, "zero_volume", f"{zero} баров с нулевым объёмом ({ratio:.1%})")
        )


def _check_prices(
    bars: Sequence[Bar],
    tick: TickSize,
    findings: List[Finding],
    stats: Dict[str, float],
) -> None:
    off_grid = 0
    example: Optional[Tuple[datetime, float]] = None
    for bar in bars:
        for price in (bar.open, bar.high, bar.low, bar.close):
            if not tick.is_on_grid(price):
                off_grid += 1
                if example is None:
                    example = (bar.ts, price)
    stats["off_grid_prices"] = float(off_grid)
    if off_grid and example is not None:
        findings.append(
            Finding(
                CRITICAL,
                "price_grid",
                f"{off_grid} цен не кратны шагу {tick.value:g} "
                f"(первая {example[1]:g} в {example[0].isoformat()})",
            )
        )


def _session_pairs(
    bars: Sequence[Bar], schedule: Optional[SessionSchedule]
) -> List[Tuple[Bar, Bar]]:
    """Пары соседних баров внутри одной сессии: только по ним считаются доходности."""
    pairs: List[Tuple[Bar, Bar]] = []
    for previous, current in zip(bars, bars[1:]):
        if schedule is not None:
            if not (schedule.is_open(previous.ts) and schedule.is_open(current.ts)):
                continue
            if schedule.local(previous.ts).date() != schedule.local(current.ts).date():
                continue
        pairs.append((previous, current))
    return pairs


def _gaps(bars: Sequence[Bar], interval_minutes: int) -> Tuple[int, int]:
    """Пропущенные бары и самый длинный разрыв (без учёта сессий)."""
    step = float(interval_minutes * 60)
    missing = 0
    longest = 0
    for previous, current in zip(bars, bars[1:]):
        delta = (current.ts - previous.ts).total_seconds()
        gap = int(round(delta / step)) - 1
        if gap > 0:
            missing += gap
            longest = max(longest, gap)
    return missing, longest


def _gaps_in_sessions(
    bars: Sequence[Bar], interval_minutes: int, schedule: SessionSchedule
) -> Tuple[int, int]:
    step = float(interval_minutes * 60)
    missing = 0
    longest = 0
    for previous, current in zip(bars, bars[1:]):
        if not (schedule.is_open(previous.ts) and schedule.is_open(current.ts)):
            continue
        if schedule.local(previous.ts).date() != schedule.local(current.ts).date():
            continue
        delta = (current.ts - previous.ts).total_seconds()
        gap = int(round(delta / step)) - 1
        if gap > 0:
            missing += gap
            longest = max(longest, gap)
    return missing, longest


def _check_auction_prints(
    bars: Sequence[Bar],
    schedule: SessionSchedule,
    findings: List[Finding],
    stats: Dict[str, float],
) -> None:
    """Нулевой объём в первую/последнюю минуту сессии — признак аукционного принта.

    Такие бары не содержат непрерывной торговли: их доходности и объёмы нельзя смешивать с
    обычными, иначе спред и `Cost` занижаются.
    """
    last_minute = schedule.session_minutes() - 1
    auctions = 0
    for bar in bars:
        minute = schedule.session_minute(bar.ts)
        if minute in (0, last_minute) and bar.volume == 0:
            auctions += 1
    stats["auction_prints"] = float(auctions)
    if auctions:
        findings.append(
            Finding(
                WARNING,
                "auction_prints",
                f"{auctions} баров с нулевым объёмом на границах сессии: "
                "исключить из статистики исполнения",
            )
        )


def _check_moves(
    pairs: Iterable[Tuple[Bar, Bar]],
    tick: TickSize,
    findings: List[Finding],
    stats: Dict[str, float],
    anomaly_factor: float,
) -> None:
    moves = [abs(current.close - previous.close) for previous, current in pairs]
    if not moves:
        return
    ordered = sorted(moves)
    median_move = median(ordered)
    stats["dp_median_ticks"] = float(tick.to_ticks(median_move))
    stats["dp_p90_ticks"] = float(tick.to_ticks(_quantile(ordered, 0.90)))
    stats["dp_max_ticks"] = float(tick.to_ticks(ordered[-1]))
    if median_move <= 0:
        findings.append(
            Finding(WARNING, "flat_series", "медианное движение цены равно нулю: ряд не торгуется")
        )
        return
    threshold = anomaly_factor * median_move
    anomalies = [move for move in moves if move > threshold]
    stats["move_anomalies"] = float(len(anomalies))
    if anomalies:
        findings.append(
            Finding(
                WARNING,
                "move_anomaly",
                f"{len(anomalies)} движений больше {anomaly_factor:g} медиан "
                f"(максимум {tick.to_ticks(ordered[-1])} тиков) — проверить корпоративные действия",
            )
        )


def _quantile(ordered: Sequence[float], level: float) -> float:
    if not ordered:
        raise ValueError("cannot take quantile of an empty series")
    index = min(max(int(round(level * (len(ordered) - 1))), 0), len(ordered) - 1)
    return ordered[index]


def _format_number(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:.4f}"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "ANOMALY_FACTOR",
    "CRITICAL",
    "Finding",
    "IntegrityReport",
    "MISSING_RATIO_CRITICAL",
    "WARNING",
    "ZERO_VOLUME_WARN_RATIO",
    "check_integrity",
]
