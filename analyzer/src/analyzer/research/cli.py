"""CLI исследовательского контура: `integrity` и `report`.

Точка входа этапа R0. Отчёты обязаны сохраняться (`--out`): проверка без артефакта не
воспроизводима. Код возврата: 0 — без критичных замечаний, 1 — критичный провал Gate D,
2 — ошибка ввода.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from ..backtest.loader import HistoryDataset, scan_dir
from .artifacts import build_artifact, collect_flags
from .costs import CostEstimate, estimate_cost
from .integrity import CRITICAL, IntegrityReport, check_integrity
from .registry import RegistryError, TrialRegistry
from .sessions import SessionSchedule
from .ticks import TickSize, TickSizeError, describe, resolve_tick_size
from .units import COUNT, RATIO, TICKS
from .variance import (
    DeseasonResult,
    VarianceError,
    VarianceEstimate,
    deseasonalize_returns,
    estimate_variances,
    sigma_tau,
    variance_ratio_curve,
)

DEFAULT_TAUS = (1, 3, 5, 10, 15, 30)
DEFAULT_MODEL = "baseline"
MIN_DESEASON_OBSERVATIONS = 30

EXIT_OK = 0
EXIT_GATE = 1
EXIT_INPUT = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="analyzer-research",
        description="Исследовательский контур: Gate D и сводка артефактов этапа R0",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (
        ("integrity", "Gate D: целостность выгрузок"),
        ("report", "сводка: шаг цены, издержки, дисперсии, VR"),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("--data-dir", required=True, type=Path, help="каталог выгрузок")
        command.add_argument(
            "--out", required=True, type=Path, help="каталог артефактов (обязателен)"
        )
        command.add_argument("--tariff", default="trader", help="тариф брокера")
        command.add_argument(
            "--spread-bps", type=float, default=0.0, help="спред за сторону, б.п."
        )
        command.add_argument(
            "--spread-verified",
            action="store_true",
            help="спред измерен (иначе помечается UNVERIFIED)",
        )
        command.add_argument(
            "--buffer-minutes", type=int, default=5, help="buffer до flat-at-close"
        )
        command.add_argument(
            "--registry", type=Path, default=None, help="путь к journal опытов (JSONL)"
        )
        if name == "report":
            command.add_argument("--model", default=DEFAULT_MODEL, help="имя модели отчёта")
            command.add_argument(
                "--taus", default=",".join(str(tau) for tau in DEFAULT_TAUS), help="сетка τ"
            )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        datasets = _load(args.data_dir)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ошибка ввода: {exc}", file=sys.stderr)
        return EXIT_INPUT
    if not datasets:
        print(f"ошибка ввода: в {args.data_dir} нет выгрузок *.csv", file=sys.stderr)
        return EXIT_INPUT

    schedule = SessionSchedule(buffer_minutes=args.buffer_minutes)
    args.out.mkdir(parents=True, exist_ok=True)

    if args.command == "integrity":
        return _run_integrity(args, datasets, schedule)
    return _run_report(args, datasets, schedule)


def _run_integrity(
    args: argparse.Namespace, datasets: Sequence[HistoryDataset], schedule: SessionSchedule
) -> int:
    reports: List[IntegrityReport] = []
    ticks: Dict[str, TickSize] = {}
    for dataset in datasets:
        try:
            tick = _resolve_tick(dataset)
        except TickSizeError as exc:
            print(f"{dataset.instrument.name}: {exc}", file=sys.stderr)
            return EXIT_INPUT
        ticks[dataset.instrument.name] = tick
        reports.append(
            check_integrity(
                dataset.bars,
                tick,
                instrument=dataset.instrument.name,
                interval_minutes=dataset.interval_minutes,
                schedule=schedule,
            )
        )

    critical = [report for report in reports if report.has_critical]
    lines = ["# Gate D — целостность выгрузок", ""]
    lines.append(f"Инструментов: {len(reports)}; критичных провалов: {len(critical)}.")
    lines.append("")
    lines.append("| Инструмент | Бары | Шаг цены | Critical | Warning |")
    lines.append("|---|---|---|---|---|")
    for report in reports:
        lines.append(
            f"| {report.instrument} | {report.bars} | {describe(ticks[report.instrument])} "
            f"| {len(report.of_level(CRITICAL))} | {len(report.of_level('warning'))} |"
        )
    lines.append("")
    for report in reports:
        lines.append(report.format_markdown())
        lines.append("")

    flags = collect_flags(
        {f"tick:{name}": tick.flag for name, tick in ticks.items() if tick.flag},
        {"gate_d": "CRITICAL"} if critical else {},
        {"sessions": "PROJECT-DECISION"},
    )
    payload = build_artifact(
        name="data_integrity_report",
        units={"price": TICKS, "delta_price": TICKS, "count": COUNT},
        n={report.instrument: report.bars for report in reports},
        method=(
            "Gate D: покрытие окнами сессий, монотонность и дубли ts, нулевые объёмы, "
            "аукционные принты, overnight-переходы, кратность цены шагу, аномалии |dP|"
        ),
        flags=flags,
        payload={
            "schedule": {
                "name": schedule.name,
                "weekday_windows": [
                    {
                        "name": window.name,
                        "open_minute": window.open_minute,
                        "close_minute": window.close_minute,
                    }
                    for window in schedule.weekday_windows
                ],
                "weekend_windows": [
                    {
                        "name": window.name,
                        "open_minute": window.open_minute,
                        "close_minute": window.close_minute,
                    }
                    for window in schedule.weekend_windows
                ],
                "timezone": schedule.timezone,
                "buffer_minutes": schedule.buffer_minutes,
            },
            "instruments": [
                dict(report.to_dict(), tick_size=describe(ticks[report.instrument]))
                for report in reports
            ],
            "critical": len(critical),
        },
    )
    _write(args.out / "integrity.md", "\n".join(lines))
    _write(args.out / "integrity.json", json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"отчёт сохранён: {args.out / 'integrity.md'}")
    return EXIT_GATE if critical else EXIT_OK


def _run_report(
    args: argparse.Namespace, datasets: Sequence[HistoryDataset], schedule: SessionSchedule
) -> int:
    taus = _parse_taus(args.taus)
    if taus is None:
        print(f"ошибка ввода: неверная сетка τ {args.taus!r}", file=sys.stderr)
        return EXIT_INPUT

    registry_path = args.registry or (args.out / "registry.jsonl")
    registry = TrialRegistry(registry_path)
    window = _window(datasets)
    try:
        trial = registry.register(
            {
                "model": args.model,
                "frame": f"{datasets[0].interval_minutes}min",
                "window": window,
                "tariff": args.tariff,
                "spread_bps": args.spread_bps,
                "taus": list(taus),
            }
        )
    except RegistryError as exc:
        print(f"ошибка реестра: {exc}", file=sys.stderr)
        return EXIT_INPUT

    lines = [
        "# Сводка исследования (этап R0)",
        "",
        f"Опыт: `{trial.id}`; окно: {window}; тариф: {args.tariff}; "
        f"спред: {args.spread_bps:g} б.п."
        f"{' (измерен)' if args.spread_verified else ' (UNVERIFIED)'}; τ: {list(taus)}.",
        "",
    ]
    payload: Dict[str, object] = {
        "trial_id": trial.id,
        "window": window,
        "tariff": args.tariff,
        "datasets": [],
    }

    for dataset in datasets:
        entry = _instrument_entry(dataset, args, schedule, taus)
        payload["datasets"].append(entry)  # type: ignore[union-attr]
        lines.append(_format_entry(entry))

    artifact = build_artifact(
        name="research_report",
        units={
            "price": TICKS,
            "variance": "log2_returns",
            "sigma": "log_returns",
            "cost": TICKS,
            "vr": RATIO,
        },
        n={str(entry["instrument"]): int(entry["bars"]) for entry in payload["datasets"]},  # type: ignore[union-attr,index]
        method=(
            "Parkinson/GK/RS по барам; VR по неперекрывающимся окнам; "
            "Cost = 2 x комиссия + спред в тиках; десезонализация по минуте окна"
        ),
        flags=collect_flags(
            {"spread": "UNVERIFIED"} if not args.spread_verified else {},
            {"tick": "UNVERIFIED"} if _all_ticks_unverified(payload) else {},
        ),
        payload=payload,
    )
    _write(args.out / "report.md", "\n".join(lines))
    _write(args.out / "report.json", json.dumps(artifact, ensure_ascii=False, indent=2))
    print(f"отчёт сохранён: {args.out / 'report.md'}; опыт: {trial.id}")
    return EXIT_OK


def _all_ticks_unverified(payload: Dict[str, object]) -> bool:
    datasets = payload.get("datasets") or []
    return any(entry.get("tick_flag") for entry in datasets)  # type: ignore[union-attr]


def _instrument_entry(
    dataset: HistoryDataset,
    args: argparse.Namespace,
    schedule: SessionSchedule,
    taus: Sequence[int],
) -> Dict[str, object]:
    name = dataset.instrument.name
    tick = _resolve_tick(dataset)
    last_close = dataset.bars[-1].close
    entry: Dict[str, object] = {
        "instrument": name,
        "bars": len(dataset.bars),
        "tick_size": tick.value,
        "tick_source": tick.source,
        "tick_flag": tick.flag,
    }

    report = check_integrity(
        dataset.bars,
        tick,
        instrument=name,
        interval_minutes=dataset.interval_minutes,
        schedule=schedule,
    )
    entry["gate_d"] = {
        "critical": len(report.of_level(CRITICAL)),
        "warning": len(report.of_level("warning")),
        "stats": dict(report.stats),
    }

    cost: CostEstimate = estimate_cost(
        last_close,
        tick,
        tariff=args.tariff,
        spread_bps=args.spread_bps,
        spread_verified=args.spread_verified,
    )
    entry["cost"] = cost.to_dict()

    variance: Optional[VarianceEstimate]
    try:
        variance = estimate_variances(dataset.bars)
        entry["variance"] = variance.to_dict()
        entry["sigma_1"] = _sqrt_or_none(variance.parkinson)
    except VarianceError as exc:
        variance = None
        entry["variance"] = {"error": str(exc)}

    entry["vr"] = {}
    entry["sigma_tau"] = {}
    deseason: Optional[DeseasonResult] = None
    try:
        deseason = deseasonalize_returns(
            dataset.bars, schedule, min_observations=MIN_DESEASON_OBSERVATIONS
        )
    except VarianceError as exc:
        entry["deseasonalization"] = {"error": str(exc)}
    if deseason is not None:
        entry["deseasonalization"] = {"flags": dict(deseason.flags)}
        sigma_1 = (variance.parkinson ** 0.5) if variance is not None else None
        for tau in taus:
            try:
                ratio = variance_ratio_curve(deseason.returns, (tau,))[tau]
            except VarianceError:
                continue
            entry["vr"][tau] = ratio  # type: ignore[index]
            if sigma_1 is not None:
                try:
                    entry["sigma_tau"][tau] = sigma_tau(sigma_1, tau, ratio)  # type: ignore[index]
                except VarianceError:
                    continue
    return entry


def _format_entry(entry: Dict[str, object]) -> str:
    lines = [f"## {entry['instrument']}", ""]
    flag = entry.get("tick_flag")
    suffix = f" ({flag})" if flag else ""
    lines.append(
        f"Шаг цены: {entry['tick_size']:g} [{entry['tick_source']}]{suffix}; "
        f"баров: {entry['bars']}."
    )
    gate = entry["gate_d"]  # type: ignore[index]
    lines.append(
        f"Gate D: critical {gate['critical']}, warning {gate['warning']}."  # type: ignore[index]
    )
    cost = entry["cost"]  # type: ignore[index]
    lines.append(
        f"Круг: {cost['round_trip_ticks']:.2f} тиков "  # type: ignore[index]
        f"(комиссия {cost['commission_ticks_per_side']:.2f} ×2, "
        f"спред {cost['spread_ticks']:.2f})."
    )
    variance = entry["variance"]  # type: ignore[index]
    if "error" in variance:  # type: ignore[operator]
        lines.append(f"Дисперсия: {variance['error']}")  # type: ignore[index]
    else:
        lines.append(
            f"Дисперсия (1m): Parkinson {variance['parkinson']:.3e}, "  # type: ignore[index]
            f"close-to-close {variance['close_to_close']:.3e}, "
            f"шум {variance['noise_ratio']:.2f}"
            f"{'' if not variance.get('flag') else ' (' + str(variance['flag']) + ')'}."  # type: ignore[union-attr]
        )
    vr = entry["vr"]  # type: ignore[index]
    if vr:
        lines.append(
            "VR и σ_τ: "  # type: ignore[arg-type]
            + ", ".join(
                f"τ={tau}: VR {value:.2f}"
                for tau, value in sorted(vr.items())  # type: ignore[union-attr]
            )
        )
    lines.append("")
    return "\n".join(lines)


def _load(data_dir: Path) -> List[HistoryDataset]:
    if not data_dir.is_dir():
        raise FileNotFoundError(f"каталог выгрузок не найден: {data_dir}")
    return scan_dir(data_dir)


def _resolve_tick(dataset: HistoryDataset) -> TickSize:
    meta_value = dataset.meta.min_price_increment if dataset.meta is not None else None
    prices = [bar.close for bar in dataset.bars]
    return resolve_tick_size(meta_value=meta_value, prices=prices)


def _parse_taus(raw: str) -> Optional[Tuple[int, ...]]:
    try:
        taus = tuple(int(part) for part in raw.split(",") if part.strip())
    except ValueError:
        return None
    if not taus or any(tau < 1 for tau in taus):
        return None
    return taus


def _window(datasets: Sequence[HistoryDataset]) -> str:
    first = min(dataset.bars[0].ts for dataset in datasets)
    last = max(dataset.bars[-1].ts for dataset in datasets)
    return f"{first.isoformat()}..{last.isoformat()}"


def _sqrt_or_none(value: float) -> Optional[float]:
    return value**0.5 if value > 0 else None


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
