"""CLI исследовательского контура: `integrity` и `report`.

Точка входа этапа R0. Отчёты обязаны сохраняться (`--out`): проверка без артефакта не
воспроизводима. Код возврата: 0 — без критичных замечаний, 1 — критичный провал Gate D,
2 — ошибка ввода.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from ..backtest.loader import HistoryDataset, scan_dir
from .artifacts import build_artifact, collect_flags
from .costs import CostEstimate, estimate_cost
from .ev import (
    COMPONENT_OK,
    COMPONENT_REFERENCE,
    entry_decision,
    select_tau,
    tau_max_components,
)
from .first_passage import FirstPassageError, build_curve
from .gate0a import (
    DEFAULT_TAUS as GATE_TAUS,
    PhiStarTable,
    build_phi_star_table,
    format_markdown as format_gate_markdown,
)
from .gate0a import build_artifact as build_gate_artifact
from .integrity import CRITICAL, IntegrityReport, check_integrity
from .policy import PolicyError, declare_barriers, load_policy
from .regime import (
    MTF_DISAGREEMENT_LIMIT,
    RegimeError,
    RegimeReport,
    evaluate_regime,
)
from .registry import RegistryError, TrialRegistry
from .sessions import SessionSchedule
from .signals import SignalError, ou_signals, session_ohlcv
from .ticks import TickSize, TickSizeError, describe, resolve_tick_size
from .theta import (
    DEFAULT_BOOTSTRAP,
    DEFAULT_DELTAS,
    DEFAULT_NULL_REPS,
    ESCALATED_DELTAS,
    ESCALATION_THETA,
    ThetaError,
    aggregate_verdict,
    estimate_theta,
    fit_ou,
)
from .units import (
    COUNT,
    LOG_PRICE,
    LOG_PRICE_SQRT_MIN,
    LOG_RETURNS,
    LOG_RETURNS_SQ,
    MINUTE_INVERSE,
    MINUTE_INVERSE_SQRT,
    MINUTES,
    RATIO,
    TICKS,
    TICKS_SQ,
    TICKS_SQRT_MIN,
)
from .variance import (
    DeseasonResult,
    VarianceError,
    VarianceEstimate,
    deseasonalize_returns,
    estimate_variances,
    sigma_tau,
    variance_ratio,
    variance_ratio_curve,
)

DEFAULT_TAUS = (1, 3, 5, 10, 15, 30)
DEFAULT_EV_TAUS = (1, 3, 5, 10, 15, 30, 60)
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
        ("phi-star", "Gate 0a: φ*(τ) = Cost / σ_τ"),
        ("theta", "Gate 1: θ по инвариантности и OU-параметры"),
        ("ev", "R3: вероятности касания, EV(τ), τ* и входной гейт"),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("--data-dir", required=True, type=Path, help="каталог выгрузок")
        command.add_argument(
            "--out", required=True, type=Path, help="каталог артефактов (обязателен)"
        )
        command.add_argument(
            "--tariffs",
            "--tariff",
            dest="tariff",
            default="trader",
            help="тариф брокера (как в analyzer-backtest)",
        )
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
        if name == "phi-star":
            command.add_argument(
                "--policy", type=Path, default=None, help="YAML с параметрами политики"
            )
            command.add_argument(
                "--stress",
                type=float,
                default=None,
                metavar="MULTIPLIER",
                help="стресс издержек (например, 1.5); вердикт считается без него",
            )
            command.add_argument(
                "--taus", default=",".join(str(tau) for tau in GATE_TAUS), help="сетка τ"
            )
        if name == "theta":
            command.add_argument(
                "--deltas",
                default=None,
                help=(
                    "сетка Δ в минутах; по умолчанию "
                    f"{','.join(str(delta) for delta in DEFAULT_DELTAS)}"
                ),
            )
            command.add_argument(
                "--escalate",
                action="store_true",
                help=(
                    "расширить сетку Δ до 60 мин (повод: θ ≤ 0.02 не идентифицируется "
                    "на сетке по умолчанию)"
                ),
            )
            command.add_argument(
                "--bootstrap",
                type=int,
                default=DEFAULT_BOOTSTRAP,
                help="число ресамплов CI по сессиям",
            )
            command.add_argument(
                "--null-reps",
                type=int,
                default=DEFAULT_NULL_REPS,
                help="число прогонов нулевой полосы (случайное блуждание)",
            )
            command.add_argument(
                "--weeks",
                type=int,
                default=8,
                help=(
                    "окно оценки в неделях (buy_frame §6.3: 4–8); 0 — вся история "
                    "в диагностическом режиме"
                ),
            )
        if name == "ev":
            command.add_argument(
                "--theta-dir",
                type=Path,
                default=None,
                help="каталог артефактов Gate 1 (по умолчанию — каталог --out)",
            )
            command.add_argument(
                "--taus",
                default=",".join(str(tau) for tau in DEFAULT_EV_TAUS),
                help="сетка τ в минутах",
            )
            command.add_argument(
                "--weeks",
                type=int,
                default=8,
                help="окно оценки сигналов и EV (4–8); 0 — вся история",
            )
            command.add_argument(
                "--bootstrap",
                type=int,
                default=DEFAULT_BOOTSTRAP,
                help="число ресамплов CI (кластеры — торговые окна)",
            )
            command.add_argument(
                "--policy", type=Path, default=None, help="YAML с параметрами политики"
            )
            command.add_argument(
                "--regime-seeds",
                type=int,
                default=5,
                help="число рестартов HMM (берётся лучший likelihood)",
            )
            command.add_argument(
                "--position-rub",
                type=float,
                default=200_000.0,
                help="размер позиции в рублях для τ_liq",
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
    if args.command == "phi-star":
        return _run_phi_star(args, datasets, schedule)
    if args.command == "theta":
        return _run_theta(args, datasets, schedule)
    if args.command == "ev":
        return _run_ev(args, datasets, schedule)
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


def _run_phi_star(
    args: argparse.Namespace, datasets: Sequence[HistoryDataset], schedule: SessionSchedule
) -> int:
    taus = _parse_grid(args.taus)
    if taus is None:
        print(f"ошибка ввода: неверная сетка τ {args.taus!r}", file=sys.stderr)
        return EXIT_INPUT
    try:
        policy, sources = load_policy(args.policy)
    except (ValueError, OSError) as exc:
        print(f"ошибка политики: {exc}", file=sys.stderr)
        return EXIT_INPUT
    if args.stress is not None and args.stress <= 0:
        print("ошибка ввода: --stress должен быть положительным", file=sys.stderr)
        return EXIT_INPUT

    registry_path = args.registry or (args.out / "registry.jsonl")
    registry = TrialRegistry(registry_path)
    try:
        trial = registry.register(
            {
                "model": "gate0a",
                "frame": f"{datasets[0].interval_minutes}min",
                "window": _window(datasets),
                "tariff": args.tariff,
                "spread_bps": args.spread_bps,
                "taus": list(taus),
                "policy": policy.to_mapping(),
                "policy_sources": sources,
            }
        )
    except RegistryError as exc:
        print(f"ошибка реестра: {exc}", file=sys.stderr)
        return EXIT_INPUT

    tables = []
    flags: Dict[str, str] = {}
    for dataset in datasets:
        try:
            table = _phi_star_entry(dataset, args, schedule, policy, taus)
        except (TickSizeError, VarianceError) as exc:
            print(f"{dataset.instrument.name}: {exc}", file=sys.stderr)
            return EXIT_INPUT
        tables.append(table)
        tick = _resolve_tick(dataset)
        if tick.flag:
            flags[f"tick:{dataset.instrument.name}"] = tick.flag
        if not args.spread_verified:
            flags[f"spread:{dataset.instrument.name}"] = "UNVERIFIED"

    artifact = build_gate_artifact(
        tables,
        trial_id=trial.id,
        policy=dict(policy.to_mapping(), lambda_risk=round(policy.lambda_risk, 6)),
        flags=flags,
    )
    markdown = format_gate_markdown(tables)

    if args.stress is not None:
        stressed = [
            _phi_star_entry(dataset, args, schedule, policy, taus, stress=args.stress)
            for dataset in datasets
        ]
        artifact["payload"]["stress"] = {  # type: ignore[index]
            "multiplier": args.stress,
            "verdict_note": "вердикт Gate 0a считается по базовым издержкам",
            "instruments": [table.to_dict() for table in stressed],
        }
        markdown += "\n\n" + _format_stress(tables, stressed, args.stress)

    _write(args.out / "phi-star.md", markdown)
    _write(args.out / "phi-star.json", json.dumps(artifact, ensure_ascii=False, indent=2))
    verdict = artifact["payload"]["verdict"]  # type: ignore[index]
    print(f"отчёт сохранён: {args.out / 'phi-star.md'}; вердикт: {verdict}; опыт: {trial.id}")
    return EXIT_OK if verdict == "PASS" else EXIT_GATE


def _format_stress(
    tables: Sequence[PhiStarTable], stressed: Sequence[PhiStarTable], multiplier: float
) -> str:
    lines = [f"## Стресс издержек ×{multiplier:g}", ""]
    lines.append("Вердикт Gate 0a считается по базовым издержкам; стресс — диагностика Gate 4.")
    lines.append("")
    lines.append("| Инструмент | φ* базовый | φ* стресс | Базовый вердикт | Стресс-вердикт |")
    lines.append("|---|---|---|---|---|")
    for base, hard in zip(tables, stressed):
        base_phi = "—" if base.min_phi is None else f"{base.min_phi:.3f}"
        hard_phi = "—" if hard.min_phi is None else f"{hard.min_phi:.3f}"
        lines.append(
            f"| {base.instrument} | {base_phi} | {hard_phi} | {base.verdict} | {hard.verdict} |"
        )
    return "\n".join(lines)


def _phi_star_entry(
    dataset: HistoryDataset,
    args: argparse.Namespace,
    schedule: SessionSchedule,
    policy,
    taus: Sequence[int],
    stress: float = 1.0,
) -> PhiStarTable:
    name = dataset.instrument.name
    tick = _resolve_tick(dataset)
    price = dataset.bars[-1].close
    cost = estimate_cost(
        price,
        tick,
        tariff=args.tariff,
        spread_bps=args.spread_bps,
        spread_verified=args.spread_verified,
        stress=stress,
    )
    variance = estimate_variances(dataset.bars)
    sigma_1_log = variance.parkinson**0.5
    deseason = deseasonalize_returns(
        dataset.bars, schedule, min_observations=MIN_DESEASON_OBSERVATIONS
    )
    vr_by_tau: Dict[int, Optional[float]] = {}
    for tau in taus:
        try:
            vr_by_tau[tau] = variance_ratio(deseason.returns, tau)
        except VarianceError:
            vr_by_tau[tau] = None
    return build_phi_star_table(
        name,
        price,
        tick.value,
        sigma_1_log,
        vr_by_tau,
        cost,
        taus=taus,
    )


def _run_theta(
    args: argparse.Namespace, datasets: Sequence[HistoryDataset], schedule: SessionSchedule
) -> int:
    if args.deltas and args.escalate:
        print("ошибка ввода: --deltas и --escalate взаимоисключающие", file=sys.stderr)
        return EXIT_INPUT
    if args.deltas:
        deltas = _parse_grid(args.deltas)
        if deltas is None:
            print(f"ошибка ввода: неверная сетка Δ {args.deltas!r}", file=sys.stderr)
            return EXIT_INPUT
    else:
        deltas = ESCALATED_DELTAS if args.escalate else DEFAULT_DELTAS
    if args.bootstrap < 20:
        print("ошибка ввода: --bootstrap должен быть не меньше 20", file=sys.stderr)
        return EXIT_INPUT
    if args.null_reps < 50:
        print("ошибка ввода: --null-reps должен быть не меньше 50", file=sys.stderr)
        return EXIT_INPUT
    # Окно оценки объявлено спекой как 4–8 недель; 0 — явно диагностический режим.
    # Промежуточные значения (1–3 недели, больше 8) не являются ни отчётными, ни диагностикой
    # и могли бы просочиться в отчёт как «trailing-weeks».
    if args.weeks != 0 and not 4 <= args.weeks <= 8:
        print(
            "ошибка ввода: --weeks допускает 0 (вся история) или 4–8 недель",
            file=sys.stderr,
        )
        return EXIT_INPUT

    slices = [(dataset, *_estimation_slice(dataset, args.weeks)) for dataset in datasets]
    estimation_from = min(str(window.get("from")) for _, _, window in slices)
    estimation_to = max(str(window.get("to")) for _, _, window in slices)

    registry_path = args.registry or (args.out / "registry.jsonl")
    registry = TrialRegistry(registry_path)
    policy, _sources = load_policy(None)
    tau_cap = policy.tau_cap_minutes
    try:
        trial = registry.register(
            {
                "model": "gate1",
                "frame": f"{datasets[0].interval_minutes}min",
                "window": _window(datasets),
                "estimation_weeks": args.weeks,
                "estimation_from": estimation_from,
                "estimation_to": estimation_to,
                "escalated": bool(args.escalate),
                "deltas": list(deltas),
                "bootstrap": args.bootstrap,
                "null_reps": args.null_reps,
                "schedule": schedule.name,
                "tau_cap_minutes": tau_cap,
            }
        )
    except RegistryError as exc:
        print(f"ошибка реестра: {exc}", file=sys.stderr)
        return EXIT_INPUT

    entries: List[Dict[str, object]] = []
    flags: Dict[str, str] = {}
    for dataset, bars, estimation_window in slices:
        try:
            fit = estimate_theta(
                bars,
                schedule,
                deltas=deltas,
                bootstrap=args.bootstrap,
                null_reps=args.null_reps,
            )
        except ThetaError as exc:
            # Один непригодный ряд не должен обрывать прогон по 20 инструментам:
            # вердикт выносится по инструменту.
            entries.append(
                {
                    "instrument": dataset.instrument.name,
                    "bars": len(bars),
                    "estimation_window": estimation_window,
                    "sessions": 0,
                    "gate1": "THETA_UNDEFINED",
                    "gate1_reasons": [str(exc)],
                    "ou_fit": None,
                    "points": [],
                    "null_band": None,
                    "variance_estimate": None,
                    "t_half_minutes": None,
                    "t_half_exceeds_cap": False,
                    "theta": None,
                    "theta_ci": [None, None],
                }
            )
            flags[f"theta:{dataset.instrument.name}"] = f"NO_DATA: {exc}"
            continue

        entry: Dict[str, object] = {
            "instrument": dataset.instrument.name,
            "bars": len(bars),
            "estimation_window": estimation_window,
            "gate1": fit.verdict,
            **fit.to_dict(),
        }
        half_life = entry.get("t_half_minutes")
        exceeds_cap = isinstance(half_life, (int, float)) and float(half_life) > tau_cap
        entry["t_half_exceeds_cap"] = exceeds_cap
        if exceeds_cap:
            flags[f"horizon:{dataset.instrument.name}"] = (
                f"SCOPE: t1/2={float(half_life):.1f} мин > tau_cap={tau_cap}"
            )
        if fit.theta_used <= ESCALATION_THETA and not args.escalate:
            flags[f"escalation:{dataset.instrument.name}"] = (
                f"SUGGEST_ESCALATION: θ={fit.theta_used:.4f} ≤ {ESCALATION_THETA} — "
                "повторить с --escalate (сетка Δ до 60)"
            )
        if fit.gate1_passed:
            try:
                ou = fit_ou(
                    dataset.instrument.name,
                    bars,
                    schedule,
                    fit,
                    registry_id=trial.id,
                )
                entry["ou_fit"] = ou.to_dict()
                for key, value in ou.flags.items():
                    flags[f"{key}:{dataset.instrument.name}"] = value
            except ThetaError as exc:
                entry["gate1"] = "THETA_UNDEFINED"
                entry["ou_fit"] = None
                entry["gate1_reasons"] = [*fit.reasons, f"OU-параметры не построены: {exc}"]
        else:
            entry["ou_fit"] = None
        entries.append(entry)

        for key, value in fit.flags.items():
            flags[f"{key}:{dataset.instrument.name}"] = value
        if fit.band is None:
            flags[f"band:{dataset.instrument.name}"] = "NO_BAND"
        if args.escalate:
            flags[f"deltas:{dataset.instrument.name}"] = "ESCALATED:60"

    verdict = aggregate_verdict(str(entry["gate1"]) for entry in entries)
    passed = sum(1 for entry in entries if entry["gate1"] == "PASS")
    instruments_n = {str(entry["instrument"]): int(entry["bars"]) for entry in entries}  # type: ignore[arg-type]
    common = {
        "trial_id": trial.id,
        "gate1": verdict,
        "deltas": list(deltas),
        "escalated": bool(args.escalate),
        "estimation_weeks": args.weeks,
        "tau_cap_minutes": tau_cap,
    }

    _write(
        args.out / "theta-invariance.json",
        json.dumps(
            build_artifact(
                name="theta_invariance_regression",
                units={"theta": MINUTE_INVERSE, "horizon": MINUTES, "count": COUNT},
                n=instruments_n,
                method=(
                    "ACF лог-цены внутри торгового окна (пары только при точном шаге Δ), "
                    "регрессия θ̂ на 1/Δ, CI блочным bootstrap по сессиям; дисперсионная "
                    "оценка Var(x_{t+Δ}−x_t) = (σ²/θ)(1−e^{−θΔ}) + 2ω² профильным МНК"
                ),
                flags=flags,
                payload={
                    **common,
                    "instruments": [
                        {
                            key: entry.get(key)
                            for key in (
                                "instrument",
                                "bars",
                                "sessions",
                                "estimation_window",
                                "gate1",
                                "gate1_reasons",
                                "theta",
                                "theta_ci",
                                "theta_used",
                                "slope",
                                "slope_ci",
                                "noise_ratio",
                                "method",
                                "t_half_minutes",
                                "t_half_ci",
                                "t_half_exceeds_cap",
                                "variance_estimate",
                                "null_band",
                                "flags",
                            )
                        }
                        for entry in entries
                    ],
                },
            ),
            ensure_ascii=False,
            indent=2,
        ),
    )
    _write(
        args.out / "signature-plot.json",
        json.dumps(
            build_artifact(
                name="signature_plot",
                units={"theta": MINUTE_INVERSE, "horizon": MINUTES, "count": COUNT},
                n=instruments_n,
                method="θ̂(Δ) и ρ̂(Δ) по окнам; точки с ρ̂ вне (0, 1) помечены NO_DATA",
                flags={},
                payload={
                    "trial_id": trial.id,
                    "instruments": [
                        {
                            "instrument": entry["instrument"],
                            "points": entry.get("points") or [],
                        }
                        for entry in entries
                    ],
                },
            ),
            ensure_ascii=False,
            indent=2,
        ),
    )
    _write(
        args.out / "null-band.json",
        json.dumps(
            build_artifact(
                name="null_band",
                units={"theta": MINUTE_INVERSE, "count": COUNT},
                n={
                    str(entry["instrument"]): int(entry["sessions"] or 0)
                    for entry in entries
                },  # type: ignore[arg-type]
                method=(
                    "Та же процедура оценки θ на симулированном случайном блуждании "
                    "с теми же длинами торговых окон; 2.5–97.5 перцентили; зависит только "
                    "от геометрии окон"
                ),
                flags={},
                payload={
                    "trial_id": trial.id,
                    "replications": args.null_reps,
                    "instruments": [
                        {
                            "instrument": entry["instrument"],
                            "sessions": entry.get("sessions"),
                            "band": entry.get("null_band"),
                        }
                        for entry in entries
                    ],
                },
            ),
            ensure_ascii=False,
            indent=2,
        ),
    )
    _write(
        args.out / "ou-fit.json",
        json.dumps(
            build_artifact(
                name="ou_fit",
                units={
                    "theta": MINUTE_INVERSE,
                    "horizon": MINUTES,
                    "sigma": LOG_PRICE_SQRT_MIN,
                    "sigma_p": LOG_PRICE,
                    "mu": LOG_PRICE,
                    "count": COUNT,
                },
                n={
                    str(entry["instrument"]): int(entry["bars"])
                    for entry in entries
                    if entry.get("ou_fit")
                }
                or {"instruments": 0},
                method=(
                    "σ² из дисперсионной модели (A·θ), σ_P = σ/√(2θ_used), "
                    "μ — среднее лог-цены в окнах; ρ_model — диагностика"
                ),
                flags={
                    key: value
                    for key, value in flags.items()
                    if key.startswith(("sigma:", "attenuation:"))
                },
                payload={
                    "trial_id": trial.id,
                    "instruments": [
                        entry["ou_fit"] for entry in entries if entry.get("ou_fit")
                    ],
                },
            ),
            ensure_ascii=False,
            indent=2,
        ),
    )

    _write(args.out / "theta.md", _format_theta(entries, trial.id, verdict, args.weeks))
    print(
        f"отчёт сохранён: {args.out / 'theta.md'}; вердикт Gate 1: {verdict} "
        f"({passed}/{len(entries)}); опыт: {trial.id}"
    )
    return EXIT_OK if verdict == "PASS" else EXIT_GATE


def _run_ev(
    args: argparse.Namespace, datasets: Sequence[HistoryDataset], schedule: SessionSchedule
) -> int:
    """EV-ядро R3: вероятности касания, `EV(τ)`, `τ*` и решение о входе."""
    if args.bootstrap < 20:
        print("ошибка ввода: --bootstrap должен быть не меньше 20", file=sys.stderr)
        return EXIT_INPUT
    if args.weeks != 0 and not 4 <= args.weeks <= 8:
        print(
            "ошибка ввода: --weeks допускает 0 (вся история) или 4–8 недель",
            file=sys.stderr,
        )
        return EXIT_INPUT
    taus = _parse_grid(args.taus)
    if taus is None:
        print(f"ошибка ввода: неверная сетка τ {args.taus!r}", file=sys.stderr)
        return EXIT_INPUT

    theta_dir = args.theta_dir or args.out
    try:
        upstream, theta_trial, theta_extras = _load_theta_upstream(theta_dir)
    except (OSError, ValueError) as exc:
        print(f"ошибка ввода: {exc}", file=sys.stderr)
        return EXIT_INPUT
    try:
        policy, policy_sources = load_policy(args.policy)
    except PolicyError as exc:
        print(f"ошибка политики: {exc}", file=sys.stderr)
        return EXIT_INPUT

    registry_path = args.registry or (args.out / "registry.jsonl")
    registry = TrialRegistry(registry_path)
    try:
        trial = registry.register(
            {
                "model": "ev_tau_star",
                "frame": f"{datasets[0].interval_minutes}min",
                "window": _window(datasets),
                "estimation_weeks": args.weeks,
                "theta_trial_id": theta_trial,
                "policy": policy.to_mapping(),
                "policy_sources": policy_sources,
                "taus": list(taus),
                "bootstrap": args.bootstrap,
                "schedule": schedule.name,
                "protocol": "SL_FIRST_EMPIRICAL_1M",
            }
        )
    except RegistryError as exc:
        print(f"ошибка реестра: {exc}", file=sys.stderr)
        return EXIT_INPUT

    entries: List[Dict[str, object]] = []
    skipped: List[Dict[str, object]] = []
    flags: Dict[str, str] = {}
    for dataset in datasets:
        name = dataset.instrument.name
        try:
            tick = _resolve_tick(dataset)
        except TickSizeError as exc:
            skipped.append({"instrument": name, "reason": f"TICK: {exc}"})
            flags[f"tick:{name}"] = f"NO_TICK: {exc}"
            continue

        source = upstream.get(name)
        if source is None:
            skipped.append({"instrument": name, "reason": "NO_THETA_ARTIFACT"})
            continue
        if source.get("gate1") != "PASS":
            reasons = source.get("reasons") or []
            detail = f": {reasons[0]}" if reasons else ""
            skipped.append(
                {"instrument": name, "reason": f"GATE1:{source.get('gate1')}{detail}"}
            )
            continue

        try:
            bars, estimation_window = _estimation_slice(dataset, args.weeks)
        except ThetaError as exc:
            skipped.append({"instrument": name, "reason": f"NO_DATA: {exc}"})
            continue
        price = _median_price(bars)
        sigma_ticks = float(source["sigma"]) * price / tick.value
        sigma_p_ticks = float(source["sigma_p"]) * price / tick.value
        cost = estimate_cost(
            price,
            tick,
            tariff=args.tariff,
            spread_bps=args.spread_bps,
            spread_verified=args.spread_verified,
        )
        try:
            barriers = declare_barriers(
                sigma_ticks, policy, cost_ticks=cost.round_trip_ticks
            )
        except PolicyError as exc:
            skipped.append({"instrument": name, "reason": f"POLICY: {exc}"})
            continue
        sessions = session_ohlcv(bars, schedule)
        try:
            signals = ou_signals(
                sessions,
                sigma_p_ticks=sigma_p_ticks,
                z_entry=policy.z_entry,
                tick_value=tick.value,
            )
        except SignalError as exc:
            skipped.append({"instrument": name, "reason": f"SIGNAL: {exc}"})
            continue
        if not signals.signals:
            skipped.append({"instrument": name, "reason": "NO_SIGNALS"})
            flags[f"signals:{name}"] = "NO_SIGNALS"
            continue

        try:
            curve = build_curve(
                sessions,
                signals.signals,
                tp_ticks=barriers.tp_ticks,
                sl_ticks=barriers.sl_ticks,
                cost_ticks=cost.round_trip_ticks,
                tick_value=tick.value,
                taus=taus,
                bootstrap=args.bootstrap,
            )
        except FirstPassageError as exc:
            skipped.append({"instrument": name, "reason": f"EV: {exc}"})
            continue

        selection = select_tau(
            curve, waiting_bars=signals.waiting_bars, tau_cap=policy.tau_cap_minutes
        )
        regime, regime_error = _regime_report(sessions, name, policy.q, args.regime_seeds)
        if regime_error:
            flags[f"regime:{name}"] = f"REGIME_FAILED: {regime_error}"
        tau_regime = (
            regime.survival.tau_minutes
            if regime is not None and regime.survival is not None
            else None
        )
        tau_mtf = regime.mtf_tau_minutes if regime is not None else None
        regime_status = (
            COMPONENT_REFERENCE
            if regime is None or tau_regime is None
            else COMPONENT_OK
        )
        tau_liq = _tau_liquidation(
            bars,
            price=price,
            lot_size=int(dataset.instrument.lot_size),
            tick_value=tick.value,
            position_rub=args.position_rub,
            rho_max=policy.rho_max,
        )
        tau_session, tau_session_min = _session_horizon(
            sessions, signals.signals, schedule, tau_liq
        )
        tau_model = theta_extras.get("tau_model")
        if not isinstance(tau_model, int) or tau_model < 1:
            tau_model = policy.tau_cap_minutes
            flags[f"tau_model:{name}"] = "TAU_MODEL_MISSING: взят τ_cap"
        tau_max = tau_max_components(
            curve,
            tau_model=tau_model,
            tau_liq=tau_liq,
            tau_session=tau_session,
            tau_cap=policy.tau_cap_minutes,
            tau_regime=tau_regime,
            tau_mtf=tau_mtf,
            regime_status=regime_status,
        )
        # τ_max из семи компонентов собран; неполный набор виден в complete и во флагах.
        decision = entry_decision(curve, selection, tau_max=tau_max.value or 0)
        # §3 holding_exiting.md: EV ≤ a − Cost, поэтому a ≤ Cost исключает положительный EV
        # при любых вероятностях. §2 требует a ≤ D = A_0·σ_P: барьер выше стационарного
        # масштаба — уже не mean reversion. Оба условия диагностические, не гейты.
        bound = barriers.tp_ticks - cost.round_trip_ticks
        d_ticks = policy.z_entry * sigma_p_ticks

        entries.append(
            {
                "instrument": name,
                "bars": len(bars),
                "tick_value": tick.value,
                "tick_source": tick.source,
                "price_reference": round(price, 6),
                "estimation_window": estimation_window,
                "theta_trial_id": theta_trial,
                "theta": source.get("theta"),
                "t_half_minutes": source.get("t_half"),
                "sigma_log": source.get("sigma"),
                "sigma_p_log": source.get("sigma_p"),
                "mu_log": source.get("mu"),
                "sigma_ticks": round(sigma_ticks, 6),
                "sigma_p_ticks": round(sigma_p_ticks, 6),
                "barriers": barriers.to_mapping(),
                "cost": cost.to_dict(),
                "sanity": {
                    "max_ev_upper_bound": round(bound, 6),
                    "viable": bool(bound > 0),
                    "d_ticks": round(d_ticks, 6),
                    "a_le_d": bool(barriers.tp_ticks <= d_ticks),
                    "rule": (
                        "EV ≤ a − Cost: при a ≤ Cost положительный EV невозможен; "
                        "§2 требует a ≤ D = z_entry·σ_P"
                    ),
                },
                "signals": signals.to_dict(),
                "curve_meta": {
                    "protocol": curve.protocol,
                    "block_rule": curve.block_rule,
                    "bootstrap": curve.bootstrap,
                    "min_paths": curve.min_paths,
                    "min_cluster_bars": curve.min_cluster_bars,
                    "taus": list(taus),
                },
                "points": [point.to_dict() for point in curve.points],
                "tau_star": selection.to_dict(),
                "tau_max": tau_max.to_dict(),
                "regime": None if regime is None else regime.to_dict(),
                "horizons": {
                    "tau_liq": tau_liq,
                    "tau_session_median": tau_session,
                    "tau_session_min": tau_session_min,
                    "tau_model": tau_model,
                },
                "entry": decision.to_dict(),
            }
        )

        for key, value in cost.flags.items():
            flags[f"cost:{name}"] = value
        if bound <= 0:
            flags[f"sanity:{name}"] = (
                f"NEGATIVE_BOUND: TP={barriers.tp_ticks} тиков ≤ Cost="
                f"{cost.round_trip_ticks:.2f} — EV > 0 невозможно при любых p"
            )
        if barriers.tp_ticks > d_ticks:
            flags[f"barrier:{name}"] = (
                f"ABOVE_D: TP={barriers.tp_ticks} > D={d_ticks:.2f} тиков "
                "(z_entry·σ_P) — это не mean reversion"
            )
        if barriers.floor_applied:
            flags[f"barrier_floor:{name}"] = (
                f"COST_FLOOR: TP={barriers.tp_ticks} = ceil({policy.k_cost}·"
                f"Cost={cost.round_trip_ticks:.2f})"
            )
        flags[f"signal:{name}"] = "PROVISIONAL_SIGNAL"
        flags[f"liq:{name}"] = "ASSUMPTION:volume-in-lots"
        if not tau_max.complete:
            flags[f"tau_max:{name}"] = "TAU_MAX_INCOMPLETE: " + "; ".join(tau_max.reasons)
        if regime is not None:
            for key, value in regime.gates.items():
                flags[f"regime_{key}:{name}"] = value
        flags[f"entry:{name}"] = (
            f"ALLOWED tau={decision.tau}" if decision.allowed else f"BLOCKED {decision.reason}"
        )
        if selection.reasons:
            flags[f"tau_star:{name}"] = "; ".join(selection.reasons)

    allowed = sum(1 for entry in entries if entry["entry"]["allowed"])  # type: ignore[index]
    verdict = "PASS" if allowed else "NO_ENTRY"
    instruments_n = {str(entry["instrument"]): int(entry["bars"]) for entry in entries} or {
        "instruments": 0
    }

    _write(
        args.out / "first_passage-protocol.json",
        json.dumps(
            build_artifact(
                name="first_passage_protocol",
                units={
                    "barrier_tp": TICKS,
                    "barrier_sl": TICKS,
                    "cost": TICKS,
                    "sigma": TICKS_SQRT_MIN,
                    "sigma_p": TICKS,
                    "horizon": MINUTES,
                },
                n=instruments_n,
                method=(
                    "Эмпирические вероятности первого касания по 1m, конвенция «SL первым»; "
                    "путь, не умещающийся в окно, отбрасывается; CI — кластерный bootstrap "
                    "по торговым окнам (кластер ≥ 2τ баров); сверка с краевой задачей OU — "
                    "REFERENCE_ONLY на синтетике"
                ),
                flags=flags,
                payload={
                    "trial_id": trial.id,
                    "theta_trial_id": theta_trial,
                    "policy": policy.to_mapping(),
                    "policy_sources": policy_sources,
                    "split_policy": "single-window; train/OOS фиксирует R4",
                    "skipped": skipped,
                    "instruments": [
                        {
                            key: entry.get(key)
                            for key in (
                                "instrument",
                                "bars",
                                "estimation_window",
                                "tick_value",
                                "tick_source",
                                "price_reference",
                                "sigma_ticks",
                                "sigma_p_ticks",
                                "barriers",
                                "cost",
                                "sanity",
                                "signals",
                                "curve_meta",
                                "horizons",
                            )
                        }
                        for entry in entries
                    ],
                },
            ),
            ensure_ascii=False,
            indent=2,
        ),
    )
    _write(
        args.out / "ev-curve.json",
        json.dumps(
            build_artifact(
                name="ev_curve",
                units={
                    "ev": TICKS,
                    "var": TICKS_SQ,
                    "sd": TICKS,
                    "horizon": MINUTES,
                },
                n={
                    str(entry["instrument"]): int(
                        sum(point["paths"] for point in entry["points"])  # type: ignore[union-attr]
                    )
                    for entry in entries
                }
                or {"instruments": 0},
                method=(
                    "EV(τ) = a·p_TP − b·p_SL + E[ΔP|N]·p_N − Cost; V(τ) = Var(X); "
                    "EV_LCB — 2.5-й перцентиль кластерного bootstrap"
                ),
                flags=flags,
                payload={
                    "trial_id": trial.id,
                    "instruments": [
                        {
                            "instrument": entry["instrument"],
                            "barriers": entry["barriers"],
                            "cost_ticks": entry["cost"]["round_trip_ticks"],  # type: ignore[index]
                            "points": entry["points"],
                        }
                        for entry in entries
                    ],
                },
            ),
            ensure_ascii=False,
            indent=2,
        ),
    )
    _write(
        args.out / "tau-star.json",
        json.dumps(
            build_artifact(
                name="tau_star_selection",
                units={"horizon": MINUTES, "objective": MINUTE_INVERSE_SQRT},
                n=instruments_n,
                method=(
                    "τ* = argmax EV(τ)/√(V(τ)(τ+w)) на τ ≤ τ_cap; вход разрешает EV_LCB(τ*); "
                    "τ > τ_cap — SCOPE_VIOLATION, граница сетки — BOUNDARY"
                ),
                flags=flags,
                payload={
                    "trial_id": trial.id,
                    "theta_trial_id": theta_trial,
                    "tau_cap_minutes": policy.tau_cap_minutes,
                    "verdict": verdict,
                    "skipped": skipped,
                    "instruments": [
                        {
                            "instrument": entry["instrument"],
                            "tau_star": entry["tau_star"],
                            "entry": entry["entry"],
                            "tau_max": entry["tau_max"],
                            "waiting_bars": entry["signals"]["waiting_bars"],  # type: ignore[index]
                        }
                        for entry in entries
                    ],
                },
            ),
            ensure_ascii=False,
            indent=2,
        ),
    )

    _write(
        args.out / "tau-max.json",
        json.dumps(
            build_artifact(
                name="tau_max_components",
                units={"horizon": MINUTES, "count": COUNT},
                n=instruments_n,
                method=(
                    "τ_max = min(τ_edge, τ_regime, τ_mtf, τ_model, τ_liq, τ_session, τ_cap); "
                    "отсутствующий компонент помечается REFERENCE_ONLY/MISSING и не заменяется "
                    "единицей; τ_edge, τ_regime, τ_mtf, τ_liq округляются вверх, τ_max — вниз"
                ),
                flags=flags,
                payload={
                    "trial_id": trial.id,
                    "tau_cap_minutes": policy.tau_cap_minutes,
                    "instruments": [
                        {
                            "instrument": entry["instrument"],
                            "tau_max": entry["tau_max"],
                            "entry": entry["entry"],
                        }
                        for entry in entries
                    ],
                },
            ),
            ensure_ascii=False,
            indent=2,
        ),
    )
    _write(
        args.out / "regime.json",
        json.dumps(
            build_artifact(
                name="hmm_fit",
                units={"horizon": MINUTES, "ratio": RATIO, "count": COUNT},
                n={
                    str(entry["instrument"]): int(entry["regime"]["hmm"]["observations"])  # type: ignore[index]
                    for entry in entries
                    if entry.get("regime") and entry["regime"].get("hmm")  # type: ignore[union-attr]
                }
                or {"instruments": 0},
                method=(
                    "GaussianHMM(K=2, diag) на 15m-барах внутри торговых окон; состояния "
                    "упорядочены по range-волатильности; Gate 2 = N_trans ≥ 10·K(K−1); "
                    "γ и P_LCB (Уилсон) по матрице переходов; τ_mtf укорачивается при "
                    "расхождении 10m-разметки"
                ),
                flags=flags,
                payload={
                    "trial_id": trial.id,
                    "q": policy.q,
                    "mtf_disagreement_limit": MTF_DISAGREEMENT_LIMIT,
                    "instruments": [
                        {"instrument": entry["instrument"], "regime": entry["regime"]}
                        for entry in entries
                    ],
                },
            ),
            ensure_ascii=False,
            indent=2,
        ),
    )

    _write(
        args.out / "ev.md",
        _format_ev(entries, skipped, trial.id, verdict, args.weeks),
    )
    print(
        f"отчёт сохранён: {args.out / 'ev.md'}; вердикт: {verdict} "
        f"({allowed}/{len(entries)}); опыт: {trial.id}"
    )
    return EXIT_OK if verdict == "PASS" else EXIT_GATE


def _load_theta_upstream(
    directory: Path,
) -> Tuple[Dict[str, Dict[str, object]], str, Dict[str, object]]:
    """Прочитать θ, σ, σ_P и горизонт инвариантности из артефактов Gate 1."""
    theta_path = directory / "theta-invariance.json"
    ou_path = directory / "ou-fit.json"
    missing = [str(path) for path in (theta_path, ou_path) if not path.is_file()]
    if missing:
        raise ValueError(
            f"нет артефактов Gate 1 в {directory}: {', '.join(missing)}; "
            "укажите --theta-dir или сначала выполните подкоманду theta"
        )

    theta_artifact = json.loads(theta_path.read_text(encoding="utf-8"))
    ou_artifact = json.loads(ou_path.read_text(encoding="utf-8"))
    theta_payload = theta_artifact.get("payload") or {}
    ou_payload = ou_artifact.get("payload") or {}
    theta_trial = str(theta_payload.get("trial_id") or "")

    deltas = [int(delta) for delta in (theta_payload.get("deltas") or []) if int(delta) > 0]
    extras: Dict[str, object] = {
        "tau_model": max(deltas) if deltas else None,
        "escalated": bool(theta_payload.get("escalated")),
        "estimation_weeks": theta_payload.get("estimation_weeks"),
    }

    by_name: Dict[str, Dict[str, object]] = {}
    for entry in theta_payload.get("instruments") or []:
        by_name[str(entry.get("instrument"))] = {
            "gate1": entry.get("gate1"),
            "reasons": entry.get("gate1_reasons") or [],
            "theta": entry.get("theta_used"),
            "t_half": entry.get("t_half_minutes"),
        }
    for entry in ou_payload.get("instruments") or []:
        target = by_name.setdefault(str(entry.get("instrument")), {})
        target.update(
            {
                "sigma": entry.get("sigma"),
                "sigma_p": entry.get("sigma_p"),
                "mu": entry.get("mu"),
            }
        )

    for name, source in by_name.items():
        if source.get("gate1") != "PASS":
            continue
        for key in ("sigma", "sigma_p"):
            value = source.get(key)
            if not isinstance(value, (int, float)) or not value > 0:
                raise ValueError(f"{name}: артефакт Gate 1 не содержит положительного {key}")
    return by_name, theta_trial, extras


def _median_price(bars: Sequence) -> float:
    """Опорная цена для перевода лог-величин в тики: медиана close окна."""
    closes = sorted(float(bar.close) for bar in bars)
    if not closes:
        raise ThetaError("нет баров для опорной цены")
    return closes[len(closes) // 2]


def _regime_report(
    sessions: Sequence, instrument: str, q: float, seeds: int
) -> Tuple[Optional[RegimeReport], Optional[str]]:
    """Режимная модель инструмента; ошибка не обрывает прогон, а идёт в флаги."""
    try:
        return evaluate_regime(sessions, instrument=instrument, q=q, seeds=seeds), None
    except RegimeError as exc:
        return None, str(exc)


def _tau_liquidation(
    bars: Sequence,
    *,
    price: float,
    lot_size: int,
    tick_value: float,
    position_rub: float,
    rho_max: float,
) -> int:
    """`τ_liq = Δt·⌈Q/(ρ_max·V_1m)⌉`: минут на выход без превышения доли объёма бара.

    `Q` — позиция в лотах, `V_1m` — медианный объём бара. Единицы объёма в CSV не размечены
    (T-Invest отдаёт объём в лотах), поэтому оба числа берутся в лотах, а допущение помечается
    `ASSUMPTION:volume-in-lots` в флагах артефакта.
    """
    if position_rub <= 0:
        raise ValueError("position size must be positive")
    if lot_size < 1 or price <= 0 or tick_value <= 0:
        raise ValueError("price, lot_size and tick_value must be positive")
    lots = max(1, int(position_rub // (price * lot_size)))
    volumes = sorted(float(bar.volume) for bar in bars)
    median_volume = volumes[len(volumes) // 2] if volumes else 0.0
    if median_volume <= 0:
        return 1
    return max(1, int(math.ceil(lots / (rho_max * median_volume))))


def _session_horizon(
    sessions: Sequence, signals: Sequence, schedule: SessionSchedule, tau_liq: int
) -> Tuple[int, int]:
    """Горизонт до закрытия окна по сигналам: медиана и минимум.

    Медиана — политический горизонт (типичный вход), минимум — худший случай: сигнал у самого
    закрытия отбрасывается на исполнении, а не обнуляет горизонт всего инструмента.
    """
    horizons = []
    for signal in signals:
        stamps = sessions[signal.session]["ts"]
        if signal.index >= len(stamps):
            continue
        moment = datetime.fromtimestamp(int(stamps[signal.index]), tz=timezone.utc)
        horizons.append(schedule.tau_session(moment, tau_liq_minutes=tau_liq, bar_minutes=1))
    if not horizons:
        return 0, 0
    ordered = sorted(horizons)
    return max(0, ordered[len(ordered) // 2]), max(0, ordered[0])


def _format_ev(
    entries: Sequence[Dict[str, object]],
    skipped: Sequence[Dict[str, object]],
    trial_id: str,
    verdict: str,
    weeks: int,
) -> str:
    lines = ["# R3 — EV(τ), τ* и решение о входе", ""]
    lines.append(
        f"Опыт `{trial_id}`; вердикт `{verdict}`; окно {weeks} нед; "
        f"протокол: SL первым (смещение EV вниз)."
    )
    lines.append("")
    if entries:
        lines.append(
            "| Инструмент | θ, мин⁻¹ | σ_P, тики | Барьеры TP/SL | Cost "
            "| Верх. граница EV = a−C | τ* | EV(τ*) | EV_LCB | τ_max | Вход |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for entry in entries:
            tau_star = entry["tau_star"]
            entry_decision_payload = entry["entry"]
            barriers = entry["barriers"]
            tau_max_payload = entry["tau_max"]
            lines.append(
                f"| {entry['instrument']} | {_fmt(entry['theta'])} | {_fmt(entry['sigma_p_ticks'])} "
                f"| {barriers['tp_ticks']}/{barriers['sl_ticks']} "  # type: ignore[index]
                f"| {_fmt(entry['cost']['round_trip_ticks'])} "  # type: ignore[index]
                f"| {_fmt(entry['sanity']['max_ev_upper_bound'])} "  # type: ignore[index]
                f"| {_fmt(tau_star['tau'])} | {_fmt(tau_star['ev'])} | {_fmt(tau_star['ev_lcb'])} "  # type: ignore[index]
                f"| {_fmt(tau_max_payload['value'])} "  # type: ignore[index]
                f"| {entry_decision_payload['reason']} |"  # type: ignore[index]
            )
        lines.append("")
        lines.append("EV и EV_LCB — в тиках на одну сделку после круговых издержек.")
        lines.append("")
        lines.append("## Компоненты `τ_max`")
        lines.append("")
        for entry in entries:
            tau_max_payload = entry["tau_max"]
            parts = []
            for item in tau_max_payload["components"]:  # type: ignore[index]
                value = "—" if item["value"] is None else item["value"]
                parts.append(f"{item['component']}={value}({item['status']})")
            lines.append(
                f"- **{entry['instrument']}**: τ_max={tau_max_payload['value']}; "  # type: ignore[index]
                + ", ".join(parts)
            )
        lines.append("")
        lines.append("## Режим")
        lines.append("")
        for entry in entries:
            regime = entry.get("regime")
            if not regime:
                lines.append(f"- **{entry['instrument']}**: режимная модель не построена")
                continue
            hmm = regime.get("hmm") or {}
            survival_payload = regime.get("survival") or {}
            lines.append(
                f"- **{entry['instrument']}**: Gate 2 — {hmm.get('gate2', '—')} "
                f"({hmm.get('transition_counts', '—')} переходов при пороге "
                f"{hmm.get('gate2_threshold', '—')}); γ = {_fmt(survival_payload.get('gamma'))}, "
                f"P_LCB = {_fmt(survival_payload.get('p_lcb'))}, "
                f"расхождение 10m = {_fmt(regime.get('mtf_disagreement'))}"
            )
        lines.append("")
        lines.append("## Причины по инструментам")
        lines.append("")
        for entry in entries:
            reasons = entry["tau_star"].get("reasons") or []  # type: ignore[union-attr]
            bound = entry["sanity"]  # type: ignore[index]
            if not bound["viable"]:  # type: ignore[index]
                reasons = [
                    *reasons,
                    (
                        f"граница §3: TP={bound['max_ev_upper_bound'] + entry['cost']['round_trip_ticks']:.2f} "  # type: ignore[index]
                        f"≤ Cost={entry['cost']['round_trip_ticks']:.2f} — "  # type: ignore[index]
                        "положительный EV недостижим при любых вероятностях"
                    ),
                ]
            lines.append(f"- **{entry['instrument']}**: " + ("; ".join(str(reason) for reason in reasons) or "—"))
        lines.append("")
    else:
        lines.append("Ни один инструмент не доведён до EV-кривой.")
        lines.append("")
    if skipped:
        lines.append("## Пропущенные")
        lines.append("")
        lines.append("| Инструмент | Причина |")
        lines.append("|---|---|")
        for item in skipped:
            lines.append(f"| {item['instrument']} | `{item['reason']}` |")
        lines.append("")
    lines.append(
        "`τ_max` посчитан только по `τ_cap`: остальные компоненты — шаг R3.2, "
        "флаг `TAU_MAX_INCOMPLETE`."
    )
    return "\n".join(lines)


def _estimation_slice(
    dataset: HistoryDataset, weeks: int
) -> Tuple[Sequence, Dict[str, object]]:
    """Бары последних `weeks` недель: окно оценки 4–8 недель по `buy_frame.md` §6.3.

    `weeks = 0` — вся история (диагностический режим): тогда окно фиксируется фактическими
    границами, чтобы артефакт нельзя было прочитать как «4–8 недель».
    """
    bars = dataset.bars
    if not bars:
        raise ThetaError(f"{dataset.instrument.name}: нет баров")
    last = bars[-1].ts
    window: Dict[str, object] = {"weeks": weeks, "to": last.isoformat()}
    if weeks == 0:
        window["from"] = bars[0].ts.isoformat()
        window["mode"] = "full-history"
        return bars, window
    cutoff = last - timedelta(weeks=weeks)
    sliced = [bar for bar in bars if bar.ts >= cutoff]
    window["from"] = cutoff.isoformat()
    window["mode"] = "trailing-weeks"
    window["bars"] = len(sliced)
    return sliced, window


def _format_theta(
    entries: Sequence[Dict[str, object]], trial_id: str, verdict: str, weeks: int
) -> str:
    passed = sum(1 for entry in entries if entry["gate1"] == "PASS")
    lines = ["# Gate 1 — идентификация θ", ""]
    window_note = (
        "вся история (диагностический режим)"
        if weeks == 0
        else f"последние {weeks} недель от последнего бара"
    )
    lines.append(
        f"Опыт `{trial_id}`; инструментов: {len(entries)}; окно оценки: {window_note}; "
        f"Gate 1 пройден: {passed}; вердикт: **{verdict}**."
    )
    lines.append("")
    lines.append(
        "θ ACF — оценка по спеке (`buy_frame.md` §6.3), θ дисп. — по дисперсии приращений. "
        "Gate 1 требует, чтобы обе были выше полосы случайного блуждания; расхождение "
        "оценок и t½ больше τ_cap печатаются как флаги."
    )
    lines.append("")
    lines.append(
        "| Инструмент | θ ACF | CI θ ACF | θ дисп. | Полоса ACF | Полоса дисп. "
        "| t½, мин | Gate 1 |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for entry in entries:
        band = entry.get("null_band") or {}
        # Для PLATEAU сравнение идёт с полосой плато — печатать полосу интерсепта вводило бы
        # в заблуждение: у двух методов верхние границы разные.
        key = "acf_plateau" if entry.get("method") == "PLATEAU" else "acf_intercept"
        acf_band = band.get(key) if isinstance(band, dict) else None
        var_band = band.get("variance") if isinstance(band, dict) else None
        ci = entry.get("theta_ci") or [None, None]
        lines.append(
            "| {name} | {acf} | {ci} | {var} | {acf_band} | {var_band} | {half} | {gate} |".format(
                name=entry["instrument"],
                acf=_fmt(entry.get("theta")),
                ci=f"{_fmt(ci[0])}…{_fmt(ci[1])}",
                var=_fmt(_nested(entry, "variance_estimate", "theta")),
                acf_band=_fmt(None if not acf_band else acf_band[1]),
                var_band=_fmt(None if not var_band else var_band[1]),
                half=_fmt(entry.get("t_half_minutes")),
                gate=entry["gate1"],
            )
        )
    lines.append("")
    reasons = [
        (str(entry["instrument"]), reason)
        for entry in entries
        for reason in (entry.get("gate1_reasons") or [])
    ]
    if reasons:
        lines.append("## Причины провала")
        lines.append("")
        for instrument, reason in reasons:
            lines.append(f"- **{instrument}**: {reason}")
        lines.append("")

    lines.append("## Signature plot")
    lines.append("")
    lines.append("| Инструмент | Δ, мин | ρ̂ | θ̂ | пар |")
    lines.append("|---|---|---|---|---|")
    for entry in entries:
        for point in entry.get("points") or []:
            lines.append(
                f"| {entry['instrument']} | {point['delta']} | {point['rho']:.4f} "
                f"| {point['theta']:.4f} | {point['pairs']} |"
            )
    lines.append("")

    lines.append("## OU-параметры")
    lines.append("")
    lines.append(
        "| Инструмент | θ used | σ (модель) | σ наивная | σ_P | μ | attenuation "
        "| ρ_model(10) | Флаги |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")
    fitted = [entry for entry in entries if entry.get("ou_fit")]
    for entry in fitted:
        ou = entry["ou_fit"]
        rho_ten = (ou.get("rho_model") or {}).get("10")
        ou_flags = "; ".join(f"{key}: {value}" for key, value in (ou.get("flags") or {}).items())
        lines.append(
            f"| {entry['instrument']} | {_fmt(ou.get('theta_used'))} | {_fmt(ou.get('sigma'))} "
            f"| {_fmt(ou.get('sigma_naive'))} | {_fmt(ou.get('sigma_p'))} | {_fmt(ou.get('mu'))} "
            f"| {_fmt(ou.get('attenuation'))} | {_fmt(rho_ten)} | {ou_flags or '—'} |"
        )
    if not fitted:
        lines.append("| — | — | — | — | — | — | — | — | — |")
    return "\n".join(lines)


def _nested(entry: Dict[str, object], outer: str, inner: str) -> Optional[object]:
    block = entry.get(outer)
    if not isinstance(block, dict):
        return None
    return block.get(inner)


def _fmt(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, (int, float)):
        return f"{float(value):.4f}"
    return str(value)


def _run_report(
    args: argparse.Namespace, datasets: Sequence[HistoryDataset], schedule: SessionSchedule
) -> int:
    taus = _parse_grid(args.taus)
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
            "variance": LOG_RETURNS_SQ,
            "sigma": LOG_RETURNS,
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


def _parse_grid(raw: str) -> Optional[Tuple[int, ...]]:
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
