"""CLI исследовательского контура: `integrity` и `report`.

Точка входа этапа R0. Отчёты обязаны сохраняться (`--out`): проверка без артефакта не
воспроизводима. Код возврата: 0 — без критичных замечаний, 1 — критичный провал Gate D,
2 — ошибка ввода.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from ..backtest.loader import HistoryDataset, scan_dir
from .artifacts import build_artifact, collect_flags
from .costs import CostEstimate, estimate_cost
from .gate0a import (
    DEFAULT_TAUS as GATE_TAUS,
    PhiStarTable,
    build_phi_star_table,
    format_markdown as format_gate_markdown,
)
from .gate0a import build_artifact as build_gate_artifact
from .integrity import CRITICAL, IntegrityReport, check_integrity
from .policy import load_policy
from .registry import RegistryError, TrialRegistry
from .sessions import SessionSchedule
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
    MINUTES,
    RATIO,
    TICKS,
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
