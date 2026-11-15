"""Агрегация и вывод результатов прогона.

Основной формат — markdown: отчёт сравнивается в диффе и читается человеком. CSV/JSON —
для машинной обработки (сделки, сводка).
"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Sequence

from .models import BacktestResult, ExitReason, Trade

# Сколько строк печатать в таблице лучших прогонов (перебор TP/SL и прочие варианты).
TOP_RUNS = 10


@dataclass(frozen=True)
class Summary:
    run: str
    instrument: str
    interval_minutes: int
    tariff: str
    bars: int
    signals: int
    filtered_signals: int
    skipped_by_notional: int
    trades: int
    wins: int
    losses: int
    take_profits: int
    stop_losses: int
    timeouts: int
    win_rate_pct: float
    avg_net_rub: float
    expectancy_pct: float
    total_gross_rub: float
    total_commission_rub: float
    total_net_rub: float
    total_net_pct: float
    max_drawdown_pct: float
    avg_bars_held: float
    equity_final: float
    open_at_end: bool


def summarize(result: BacktestResult) -> Summary:
    trades = result.trades
    count = len(trades)
    exits = _exit_counts(trades)
    gross = sum(trade.gross_pnl for trade in trades)
    commission = sum(trade.commission for trade in trades)
    net = sum(trade.net_pnl for trade in trades)
    deposit = result.starting_deposit
    wins = sum(1 for trade in trades if trade.is_win)
    losses = sum(1 for trade in trades if trade.net_pnl < 0)
    avg_net = net / count if count else 0.0

    return Summary(
        run=result.run_name,
        instrument=result.instrument.name,
        interval_minutes=result.interval_minutes,
        tariff=result.tariff,
        bars=result.bars,
        signals=result.signals,
        filtered_signals=result.filtered_signals,
        skipped_by_notional=result.skipped_by_notional,
        trades=count,
        wins=wins,
        losses=losses,
        take_profits=exits.get(ExitReason.TAKE_PROFIT.value, 0),
        stop_losses=exits.get(ExitReason.STOP_LOSS.value, 0),
        timeouts=exits.get(ExitReason.TIMEOUT.value, 0),
        win_rate_pct=wins / count * 100.0 if count else 0.0,
        avg_net_rub=avg_net,
        expectancy_pct=avg_net / deposit * 100.0 if deposit else 0.0,
        total_gross_rub=gross,
        total_commission_rub=commission,
        total_net_rub=net,
        total_net_pct=net / deposit * 100.0 if deposit else 0.0,
        max_drawdown_pct=result.max_drawdown_pct,
        avg_bars_held=sum(t.bars_held for t in trades) / count if count else 0.0,
        equity_final=result.equity_final,
        open_at_end=result.open_at_end,
    )


def summarize_all(results: Sequence[BacktestResult]) -> List[Summary]:
    return [summarize(result) for result in results]


def format_markdown(
    results: Sequence[BacktestResult], title: str = "Бэктест mean_reversion"
) -> str:
    if not results:
        return f"# {title}\n\nПрогонов нет: данные не найдены.\n"

    lines = [
        f"# {title}",
        "",
        _parameters_line(results),
    ]
    variants_line = _variants_line(results)
    if variants_line:
        lines.append(variants_line)
    lines.extend(
        [
            "",
            "| Прогон | Бары | Сигналы | Фильтр | Пропуск | Сделок | Win % | TP/SL/TO "
            "| Expectancy ₽ | Expectancy % | Итог ₽ | Итог % | Max DD % | Комиссия ₽ | Открыта |",
            "|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for result in results:
        summary = summarize(result)
        lines.append(
            f"| {summary.run} | {summary.bars} | {summary.signals} | "
            f"{summary.filtered_signals} | {summary.skipped_by_notional} | {summary.trades} | "
            f"{summary.win_rate_pct:.1f} | "
            f"{summary.take_profits}/{summary.stop_losses}/{summary.timeouts} | "
            f"{_money(summary.avg_net_rub)} | {summary.expectancy_pct:.4f} | "
            f"{_money(summary.total_net_rub)} | {summary.total_net_pct:.2f} | "
            f"{summary.max_drawdown_pct:.2f} | {_money(summary.total_commission_rub)} | "
            f"{'да' if summary.open_at_end else '—'} |"
        )
    lines.extend(
        [
            "",
            "Колонки: «Сигналы» — сигналы, полученные при отсутствии открытой позиции "
            "(поэтому позиции в работе не порождают сигналов); «Фильтр» — сигналы, отброшенные "
            "комиссионным фильтром; «Пропуск» — сигналы без позиции из-за лимита номинала; "
            "TP/SL/TO — распределение выходов; «Открыта» — позиция осталась открытой на конец "
            "выгрузки и в сделках/PnL не учтена. Сигнал на последнем баре, для которого нет "
            "следующего бара под вход, в счётчиках сделок не отражается.",
            "",
        ]
    )
    lines.extend(_leaders_block(results))
    lines.extend(_exits_block(results))
    return "\n".join(lines)


def _parameters_line(results: Sequence[BacktestResult]) -> str:
    """Строка параметров; при переборе TP/SL вместо одной пары печатается список пар."""
    first = results[0].config
    fill = (
        "по открытию следующего бара"
        if first.fill_on_next_bar_open
        else "по закрытию бара сигнала"
    )
    if any(result.config != first for result in results):
        levels = "перебор TP:SL " + ", ".join(_tp_sl_variants(results))
    elif first.no_tp:
        levels = f"SL {first.atr_mult_sl:.2f}×ATR / TP выключен"
    else:
        levels = f"SL {first.atr_mult_sl:.2f}×ATR / TP {first.atr_mult_tp:.2f}×ATR"
    line = (
        f"Параметры: депозит {_money(first.deposit)} ₽, риск {first.risk_per_trade_pct:.2f} % "
        f"на сделку, лимит номинала {first.max_position_notional_pct:.0f} % депозита, "
        f"{levels}, таймаут {first.max_bars_in_trade} баров, вход {fill}, "
        f"filter ratio {first.commission_profit_ratio:.2f}"
    )
    if first.cooldown_bars:
        line += f", cooldown {first.cooldown_bars} баров"
    if first.trailing_atr:
        line += f", трейлинг {first.trailing_atr:.2f}×ATR"
    line += "."
    strategy = results[0].strategy
    if strategy:
        line = f"Стратегия: **{strategy}**. " + line
    return line


def _variants_line(results: Sequence[BacktestResult]) -> str:
    """Список вариантов, когда различие между прогонами в порогах стратегии, а не в TP/SL."""
    if not results[0].strategy:
        return ""
    labels = list(dict.fromkeys(result.label for result in results if result.label))
    if len(labels) < 2:
        return ""
    return "Варианты: " + ", ".join(labels) + "."


def _tp_sl_variants(results: Sequence[BacktestResult]) -> List[str]:
    """Уникальные пары `TP:SL` в порядке появления."""
    variants: List[str] = []
    for result in results:
        level = f"{result.config.atr_mult_tp:g}:{result.config.atr_mult_sl:g}"
        if level not in variants:
            variants.append(level)
    return variants


EXIT_ORDER = (ExitReason.TAKE_PROFIT, ExitReason.STOP_LOSS, ExitReason.TIMEOUT)
# Блок по типам выхода печатается целиком только для компактных наборов прогонов;
# в переборах (десятки вариантов) — только для лучшего по итогу.
EXITS_MAX_RUNS = 12


def _exits_block(results: Sequence[BacktestResult]) -> List[str]:
    """Куда уходит результат: сделки, win % и net по каждому типу выхода.

    Нужен, чтобы отделить «правило не работает» от «правило работает, но цель срезает
    правый хвост»: у momentum прибыль должна концентрироваться в редких крупных выходах.
    """
    selected = list(results)
    if len(selected) > EXITS_MAX_RUNS:
        selected = [max(selected, key=lambda item: sum(t.net_pnl for t in item.trades))]
    rows: List[str] = []
    for result in selected:
        total_net = sum(trade.net_pnl for trade in result.trades)
        for reason in EXIT_ORDER:
            group = [trade for trade in result.trades if trade.exit_reason is reason]
            if not group:
                continue
            net = sum(trade.net_pnl for trade in group)
            wins = sum(1 for trade in group if trade.is_win)
            share = f"{net / total_net * 100:.0f}" if total_net else "—"
            rows.append(
                f"| {result.run_name} | {reason.value} | {len(group)} | "
                f"{wins / len(group) * 100:.1f} | {_money(net)} | {share} |"
            )
    if not rows:
        return []
    return [
        "",
        "### Выходы: вклад каждого типа закрытия",
        "",
        "| Прогон | Выход | Сделок | Win % | Net ₽ | Доля net % |",
        "|---|---|---:|---:|---:|---:|",
        *rows,
        "",
        "«Доля net %» — часть итогового результата, принесённая этим типом выхода "
        "(при итоге около нуля доля не считается). Если прибыль почти вся в TP и его доля "
        "велика при малом числе сделок — это срезанный хвост, а не устойчивое преимущество.",
        "",
    ]


def _leaders_block(results: Sequence[BacktestResult]) -> List[str]:
    """Таблица лучших прогонов по итогу — только когда конфигурации действительно различаются.

    Порог плана (`to_do.md` §3): кандидат интересен, если gross покрывает комиссию
    (отношение ≥ 1) и итог положителен. Один положительный итог без запаса по издержкам — шум.
    """
    if not results or len({result.label for result in results}) < 2:
        return []
    summaries = [summary for summary in summarize_all(results) if summary.trades]
    if not summaries:
        return []
    summaries.sort(key=lambda item: item.total_net_rub, reverse=True)

    lines = [
        "",
        f"### Лучшие прогоны по итогу (топ-{TOP_RUNS} из {len(summaries)} со сделками)",
        "",
        "| Прогон | Сделок | Win % | Gross ₽ | Комиссия ₽ | Gross/комиссия | Итог ₽ | Итог % |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in summaries[:TOP_RUNS]:
        ratio = (
            f"{item.total_gross_rub / item.total_commission_rub:.2f}"
            if item.total_commission_rub
            else "—"
        )
        lines.append(
            f"| {item.run} | {item.trades} | {item.win_rate_pct:.1f} | "
            f"{_money(item.total_gross_rub)} | {_money(item.total_commission_rub)} | {ratio} | "
            f"{_money(item.total_net_rub)} | {item.total_net_pct:.2f} |"
        )
    lines.extend(
        [
            "",
            "Гейт (`to_do.md` §3): конфигурация — кандидат только при Gross/комиссия ≥ 1 "
            "и положительном итоге; меньше — издержки съедают преимущество.",
            "",
        ]
    )
    return lines


def trades_csv(results: Sequence[BacktestResult]) -> str:
    header = [
        "run",
        "direction",
        "entry_ts",
        "exit_ts",
        "entry_price",
        "exit_price",
        "sl",
        "tp",
        "lots",
        "lot_size",
        "exit_reason",
        "bars_held",
        "notional",
        "gross_pnl",
        "commission",
        "net_pnl",
    ]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    for result in results:
        for trade in result.trades:
            writer.writerow(
                [
                    result.run_name,
                    trade.direction.value,
                    trade.entry_ts.isoformat(),
                    trade.exit_ts.isoformat(),
                    f"{trade.entry_price:.6f}",
                    f"{trade.exit_price:.6f}",
                    f"{trade.sl:.6f}",
                    "" if trade.tp is None else f"{trade.tp:.6f}",
                    trade.lots,
                    trade.lot_size,
                    trade.exit_reason.value,
                    trade.bars_held,
                    f"{trade.notional:.2f}",
                    f"{trade.gross_pnl:.2f}",
                    f"{trade.commission:.2f}",
                    f"{trade.net_pnl:.2f}",
                ]
            )
    return buffer.getvalue()


def summary_json(results: Sequence[BacktestResult]) -> str:
    payload = [asdict(summary) for summary in summarize_all(results)]
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_report(
    path: Path, results: Sequence[BacktestResult], title: str = "Бэктест mean_reversion"
) -> None:
    """Записать отчёт: `.md` — таблица, `.json` — сводка, `.csv` — сделки всех прогонов."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    if suffix == ".json":
        body = summary_json(results)
    elif suffix == ".csv":
        body = trades_csv(results)
    else:
        body = format_markdown(results, title)
    path.write_text(body, encoding="utf-8")


def _exit_counts(trades: Sequence[Trade]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for trade in trades:
        key = trade.exit_reason.value
        counts[key] = counts.get(key, 0) + 1
    return counts


def _money(value: float) -> str:
    """Рубли с пробелом как разделителем тысяч: 1 234.56."""
    return f"{value:,.2f}".replace(",", " ")


__all__ = [
    "Summary",
    "format_markdown",
    "summarize",
    "summarize_all",
    "summary_json",
    "trades_csv",
    "write_report",
]
