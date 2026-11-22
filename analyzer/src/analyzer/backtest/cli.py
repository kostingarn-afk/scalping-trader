"""CLI прогона бэктеста на локальной выгрузке истории.

Analyzer не обращается к T-Invest API: историю выгружает Collector (`MarketDataService.GetCandles`)
в CSV + `.meta.json` (контракт — `backtest.loader`), прогон работает офлайн.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from itertools import product
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from ..signal_models import MODEL_SPECS, build_model, model_names
from .commission import TARIFFS, CommissionModel
from .execution import ExecutionModel
from .engine import StrategyFn, atr_pct_quantile, run_backtest
from .loader import HistoryDataset, aggregate, load_dataset, scan_dir
from .models import BacktestResult, Bar, Instrument, RunConfig
from .nullmodel import format_null_markdown, run_null
from .panel import (
    SIGNAL_MODES,
    PanelConfig,
    build_daily_closes,
    format_panel_markdown,
    format_panel_walkforward_markdown,
    run_panel,
    run_panel_null,
    run_panel_walkforward,
    select_universe,
)
from .report import format_markdown, write_report
from .walkforward import (
    StrategyBuilder,
    WalkforwardResult,
    format_walkforward_markdown,
    run_walkforward,
)

# Сколько прогонов включать в блок нулевой модели: каждый сид — полный прогон по данным.
NULL_MAX_RUNS = 6


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="analyzer-backtest",
        description="Прогон стратегии mean_reversion на исторической выгрузке истории",
    )
    parser.add_argument("--data-dir", type=Path, help="каталог с выгрузками (*.csv + *.meta.json)")
    parser.add_argument(
        "--csv",
        type=Path,
        action="append",
        default=[],
        help="отдельный CSV-файл выгрузки (можно повторять)",
    )
    parser.add_argument(
        "--ticker", help="тикер для CSV без .meta.json (только для одной выгрузки)"
    )
    parser.add_argument("--class-code", default="TQBR", help="класс инструмента (TQBR, SPBFUT)")
    parser.add_argument("--lot-size", type=int, default=1, help="акций в лоте / размер контракта")
    parser.add_argument(
        "--native-interval",
        type=int,
        default=None,
        help="таймфрейм выгрузки, мин (по умолчанию — из .meta.json, иначе 1)",
    )
    parser.add_argument("--intervals", default="1,5,15", help="целевые таймфреймы через запятую")
    parser.add_argument(
        "--tariffs",
        default="investor",
        help=f"тарифы через запятую: {', '.join(TARIFFS)} (по умолчанию investor)",
    )
    parser.add_argument("--deposit", type=float, default=200_000.0, help="депозит, ₽")
    parser.add_argument("--risk-pct", type=float, default=1.0, help="риск на сделку, %% депозита")
    parser.add_argument(
        "--max-notional-pct", type=float, default=100.0, help="лимит номинала позиции, %% депозита"
    )
    parser.add_argument("--atr-sl", type=float, default=1.5, help="множитель ATR для SL")
    parser.add_argument(
        "--atr-tp",
        type=float,
        default=None,
        help=(
            "множитель ATR для TP; не задан — цель строится от порога комиссии "
            "(k×ATR, k = ceil(порог / ATR%))"
        ),
    )
    parser.add_argument(
        "--tp-sl",
        default="",
        help=(
            "перебор пар TP:SL через запятую, например 2:1.5,3:2,4:2.5; "
            "пусто — один прогон по --atr-tp/--atr-sl; заданные пары отключают цель от комиссии"
        ),
    )
    parser.add_argument(
        "--strategy",
        choices=model_names(),
        default="mean_reversion",
        help=(
            "модель входа из реестра signal_models: "
            + ", ".join(model_names())
        ),
    )
    parser.add_argument("--z-entry", type=float, default=2.0, help="порог |z| в σ BB (v2, momentum)")
    parser.add_argument(
        "--rsi-buy",
        type=float,
        default=None,
        help="RSI для BUY: v2 — потолок перепроданности (25), momentum — подтверждение силы (65)",
    )
    parser.add_argument(
        "--rsi-sell",
        type=float,
        default=None,
        help="RSI для SELL: v2 — потолок перекупленности (75), momentum — подтверждение слабости (35)",
    )
    parser.add_argument("--adx-max", type=float, default=25.0, help="v2: потолок ADX (боковик)")
    parser.add_argument(
        "--adx-min", type=float, default=0.0, help="momentum: минимум ADX (0 — фильтр выключен)"
    )
    parser.add_argument(
        "--min-atr-pct",
        type=float,
        default=0.0,
        help="минимальный ATR в %% цены (0 — фильтр волатильности выключен)",
    )
    parser.add_argument(
        "--atr-pct-quantile",
        type=float,
        default=None,
        help="порог ATR%% как квантиль серии (например 80), если --min-atr-pct не задан",
    )
    parser.add_argument(
        "--model-param",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help=(
            "параметр модели из реестра, key=value; повторяемый "
            "(например window=240 для ou). Неизвестный ключ — ошибка до прогона"
        ),
    )
    parser.add_argument(
        "--cooldown-bars", type=int, default=0, help="баров после выхода без новых входов"
    )
    parser.add_argument(
        "--trailing-atr",
        type=float,
        default=0.0,
        help="трейлинг-стоп в ATR от закрытия (0 — выключен)",
    )
    parser.add_argument(
        "--no-tp",
        action="store_true",
        help="не выставлять TP: выход только по SL/трейлингу/таймауту (проверка срезанного хвоста)",
    )
    parser.add_argument(
        "--null-seeds",
        type=int,
        default=0,
        help="число прогонов нулевой модели (случайный вход) для сравнения; 0 — выключено",
    )
    parser.add_argument(
        "--panel",
        action="store_true",
        help=(
            "кросс-секционный momentum по панели: long/short по рангу доходности, "
            "долларово-нейтрально (интервалы и правило входа не используются)"
        ),
    )
    parser.add_argument("--panel-lookback", type=int, default=5, help="окно доходности, торговых дней")
    parser.add_argument("--panel-rebalance", type=int, default=5, help="ребаланс, торговых дней")
    parser.add_argument("--panel-legs", type=int, default=4, help="имён в каждой стороне книги")
    parser.add_argument(
        "--panel-signal",
        choices=SIGNAL_MODES,
        default="raw",
        help=(
            "чем ранжировать вселенную: raw — сырая доходность за окно; residual — доходность "
            "минус рыночная часть (по бете); volscaled — доходность на единицу риска"
        ),
    )
    parser.add_argument(
        "--panel-daily-stop",
        type=float,
        default=0.0,
        help="дневной лимит убытка книги, %% (0 — выключено): период закрывается по дню пробития",
    )
    parser.add_argument(
        "--daily-stop",
        type=float,
        default=0.0,
        help="дневной лимит убытка на сделку, %% депозита (0 — выключено; зеркало PnL-CB Trader)",
    )
    parser.add_argument(
        "--entry-after-msk",
        default="",
        help=(
            "запрет входов до указанного времени по Москве, формат HH:MM "
            "(например 11:00 — не торговать первый час основной сессии; пусто — выключено)"
        ),
    )
    parser.add_argument(
        "--exchange-fee-per-contract",
        type=float,
        default=0.0,
        help=(
            "биржа берёт фиксированный сбор за контракт (₽ за сторону), а не процент "
            "с номинала: для фьючерсов задаётся этим флагом, иначе модель считает только "
            "процент брокера и результат недостоверен. Ставка — из тарифного листа биржи"
        ),
    )
    parser.add_argument(
        "--spread-bps",
        type=float,
        default=0.0,
        help="половина спреда в б.п. цены: покупка дороже, продажа дешевле (0 — выключено)",
    )
    parser.add_argument(
        "--impact-bps",
        type=float,
        default=0.0,
        help="временный импакт Almgren–Chriss при 100 %% участии в объёме бара, б.п.",
    )
    parser.add_argument(
        "--permanent-bps",
        type=float,
        default=0.0,
        help="постоянный импакт Almgren–Chriss при 100 %% участии (в средней цене — с 0.5), б.п.",
    )
    parser.add_argument(
        "--panel-grid",
        default="",
        help="сетка панели: 'lookback_days=5,10;rebalance_days=5;legs=3,4'",
    )
    parser.add_argument(
        "--panel-universe",
        default="",
        help="тикеры через запятую: панель только по ним (пусто — все выгрузки каталога)",
    )
    parser.add_argument(
        "--panel-null-seeds", type=int, default=0, help="сиды нулевой модели панели (0 — выключено)"
    )
    parser.add_argument(
        "--panel-walkforward", action="store_true", help="walk-forward по панели (по датам)"
    )
    parser.add_argument("--panel-train-days", type=int, default=0, help="train-окно, торговых дней")
    parser.add_argument("--panel-test-days", type=int, default=0, help="test-окно, торговых дней")
    parser.add_argument(
        "--panel-min-periods", type=int, default=10, help="минимум периодов на train для кандидата"
    )
    parser.add_argument(
        "--walkforward",
        action="store_true",
        help="walk-forward: подбор параметров на train-окне, оценка на следующем test-окне",
    )
    parser.add_argument(
        "--wf-train-bars", type=int, default=0, help="длина обучающего окна, баров"
    )
    parser.add_argument("--wf-test-bars", type=int, default=0, help="длина тестового окна, баров")
    parser.add_argument(
        "--wf-min-trades", type=int, default=30, help="минимум сделок на train для кандидата"
    )
    parser.add_argument(
        "--wf-grid",
        default="",
        help=(
            "сетка параметров для walk-forward: 'z_entry=1,1.5,2;adx_min=0,25' "
            "(декартово произведение; пусто — один прогон с флагами CLI)"
        ),
    )
    parser.add_argument("--max-bars", type=int, default=10, help="таймаут сделки, баров")
    parser.add_argument(
        "--fill-on-close",
        action="store_true",
        help="вход по закрытию сигнального бара (воспроизведение замера to_be.md §3)",
    )
    parser.add_argument("--out", type=Path, help="файл отчёта (.md, .json, .csv)")
    parser.add_argument("--title", default=None, help="заголовок отчёта (по умолчанию — по режиму)")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        datasets = _load_datasets(args)
        tariffs = _parse_tariffs(args.tariffs)
        intervals = _parse_ints(args.intervals, "intervals")
        variants = _parse_variants(args.tp_sl, args.atr_tp, args.atr_sl)
        _validate_flags(args)
        args.model_params = _parse_model_params(args.model_param)
        strategy_config = _strategy_config(args)
        panel = _panel_settings(args)
        config = RunConfig(
            deposit=args.deposit,
            risk_per_trade_pct=args.risk_pct,
            max_position_notional_pct=args.max_notional_pct,
            atr_mult_sl=args.atr_sl,
            max_bars_in_trade=args.max_bars,
            cooldown_bars=args.cooldown_bars,
            trailing_atr=args.trailing_atr,
            no_tp=args.no_tp,
            fill_on_next_bar_open=not args.fill_on_close,
            daily_loss_stop_pct=args.daily_stop,
            entry_after_msk_minutes=_parse_msk_time(args.entry_after_msk),
            execution=ExecutionModel(
                spread_bps=args.spread_bps,
                impact_bps_at_full=args.impact_bps,
                permanent_bps_at_full=args.permanent_bps,
            ),
        )
        walkforward = _walkforward_settings(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 2

    if panel is not None:
        return _run_panel(args, datasets, tariffs, panel)

    if walkforward is not None:
        return _run_walkforward(args, datasets, intervals, tariffs, config, strategy_config, walkforward)

    results: List[BacktestResult] = []
    # Источник (бары, инструмент, ТФ, тариф, конфиг) для каждого прогона: нужен нулевой
    # модели, которая переигрывает те же данные при случайном входе.
    sources: List[Tuple[Sequence[Bar], Instrument, int, str, RunConfig]] = []
    for dataset in datasets:
        if len(dataset) < config.min_candles:
            print(
                f"предупреждение: {dataset.instrument.name} — {len(dataset)} баров, "
                f"меньше min_candles={config.min_candles}; сделок не будет",
                file=sys.stderr,
            )
        for interval in intervals:
            if interval < dataset.interval_minutes or interval % dataset.interval_minutes:
                print(
                    f"пропуск {dataset.instrument.name} {interval}min: "
                    f"не кратен таймфрейму выгрузки {dataset.interval_minutes}min",
                    file=sys.stderr,
                )
                continue
            bars = (
                list(dataset.bars)
                if interval == dataset.interval_minutes
                else aggregate(dataset.bars, interval, dataset.interval_minutes)
            )
            try:
                atr_threshold = _atr_threshold(args, bars)
            except ValueError as exc:
                print(f"ошибка: {exc}", file=sys.stderr)
                return 2
            strategy_fn, strategy_label = _strategy_for(args, strategy_config, atr_threshold)
            for tariff in tariffs:
                model = _commission_model(args, tariff)
                for label, tp, sl in variants:
                    run_config = _run_config_for(config, tp, sl, model, dataset.instrument)
                    results.append(
                        run_backtest(
                            bars=bars,
                            instrument=dataset.instrument,
                            config=run_config,
                            commission=model,
                            interval_minutes=interval,
                            strategy_fn=strategy_fn,
                            label=_join_label(label, strategy_label),
                            strategy_name=args.strategy,
                        )
                    )
                    sources.append((bars, dataset.instrument, interval, tariff, run_config))

    if not results:
        print("ошибка: ни одного прогона не выполнено", file=sys.stderr)
        return 2

    title = args.title or "Бэктест mean reversion"
    report = format_markdown(results, title)
    null_text = _run_null_block(args, results, sources) if args.null_seeds else ""
    if null_text:
        report = f"{report}\n{null_text}"

    if args.out:
        try:
            if args.out.suffix.lower() == ".md":
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(report, encoding="utf-8")
            else:
                # JSON/CSV не вмещают дополнительный раздел — печатаем его отдельно.
                write_report(args.out, results, title)
                if null_text:
                    _safe_print(null_text)
        except OSError as exc:
            print(f"ошибка записи отчёта: {exc}", file=sys.stderr)
            return 2
        print(f"отчёт сохранён: {args.out}")
    else:
        _safe_print(report)
    return 0


def _run_null_block(
    args: argparse.Namespace,
    results: Sequence[BacktestResult],
    sources: Sequence[Tuple[Sequence[Bar], Instrument, int, str, RunConfig]],
) -> str:
    """Прогнать нулевую модель для одиночных прогонов и вернуть markdown-раздел.

    В переборах (много прогонов) нулевая модель считается только для лучших по итогу:
    каждый сид — это полный прогон, и считать её для всех вариантов бессмысленно дорого.
    Прогоны без сделок пропускаются: частота входа для них не определена.
    """
    indices = [
        index for index, result in enumerate(results) if result.trades
    ]
    if not indices:
        return ""
    if len(indices) > NULL_MAX_RUNS:
        indices = sorted(
            indices,
            key=lambda index: sum(t.net_pnl for t in results[index].trades),
            reverse=True,
        )[:NULL_MAX_RUNS]
        print(
            f"нулевая модель: прогонов {len(results)}, считается по {len(indices)} лучшим по итогу",
            file=sys.stderr,
        )

    nulls = []
    for index in indices:
        result = results[index]
        bars, instrument, interval, tariff, run_config = sources[index]
        total_net = sum(trade.net_pnl for trade in result.trades)
        nulls.append(
            run_null(
                bars=bars,
                instrument=instrument,
                config=run_config,
                commission=_commission_model(args, tariff),
                interval_minutes=interval,
                actual=(total_net, len(result.trades)),
                seeds=args.null_seeds,
                run_name=result.run_name,
            )
        )
    return format_null_markdown(nulls, "Нулевая модель: случайный вход")


def _safe_print(text: str) -> None:
    """Печать в консоль, устойчивая к кодировке.

    Windows-консоль по умолчанию cp1251 и не кодирует `₽`: без замены `print` падал бы
    с `UnicodeEncodeError` ровно на отчёте, который и нужен пользователю.
    """
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        print(text.encode(encoding, "replace").decode(encoding, "replace"))


def _load_datasets(args: argparse.Namespace) -> List[HistoryDataset]:
    if args.data_dir:
        if args.ticker:
            raise ValueError(
                "--ticker применяется только к --csv без .meta.json; "
                "для --data-dir инструмент берётся из meta"
            )
        datasets = scan_dir(args.data_dir)
        if not datasets:
            raise FileNotFoundError(f"{args.data_dir}: нет выгрузок *.csv")
        return datasets

    if not args.csv:
        raise ValueError("укажите --data-dir или хотя бы один --csv")
    if args.ticker and len(args.csv) > 1:
        raise ValueError(
            "--ticker задаёт один инструмент; для нескольких --csv "
            "используйте выгрузки с .meta.json"
        )

    instrument = None
    if args.ticker:
        instrument = Instrument(args.ticker, args.class_code, args.lot_size)
    return [
        load_dataset(path, instrument=instrument, interval_minutes=args.native_interval)
        for path in args.csv
    ]


def _parse_tariffs(value: str) -> List[str]:
    tariffs = [item.strip() for item in value.split(",") if item.strip()]
    unknown = [item for item in tariffs if item not in TARIFFS]
    if unknown:
        raise ValueError(f"неизвестный тариф {', '.join(unknown)}; доступны: {', '.join(TARIFFS)}")
    return tariffs


def _run_config_for(
    config: RunConfig,
    tp: Optional[float],
    sl: float,
    model: CommissionModel,
    instrument: Instrument,
) -> RunConfig:
    """Конфиг прогона: явное кратное TP или политика цели от порога комиссии."""
    run_config = replace(config, atr_mult_sl=sl)
    if tp is None:
        return replace(run_config, min_tp_pct=_required_tp_pct(config, model, instrument))
    return replace(run_config, atr_mult_tp=tp, min_tp_pct=0.0)


def _required_tp_pct(config: RunConfig, model: CommissionModel, instrument: Instrument) -> float:
    """Порог цели выхода, % от суммы сделки, по тарифу и классу инструмента."""
    return (
        config.commission_profit_ratio
        * 2
        * model.per_side_rate(instrument.is_future)
        * 100.0
    )


def _parse_variants(
    value: str, default_tp: Optional[float], default_sl: float
) -> List[Tuple[str, Optional[float], float]]:
    """Развернуть `--tp-sl` в список вариантов (метка, TP, SL).

    Метка нужна только при переборе: одиночный прогон остаётся неотличим от
    `--atr-tp/--atr-sl`, поэтому прежние отчёты и тесты не меняются.

    `default_tp = None` означает «цель строится от порога комиссии»: TP остаётся `None`,
    чтобы вызывающий код не подставлял константу вместо политики.
    """
    if not value.strip():
        return [("", default_tp, default_sl)]
    pairs = _parse_tp_sl_pairs(value)
    if len(pairs) == 1:
        return [("", pairs[0][0], pairs[0][1])]
    return [(f"tp{tp:g}/sl{sl:g}", tp, sl) for tp, sl in pairs]


def _parse_tp_sl_pairs(value: str) -> List[Tuple[float, float]]:
    pairs: List[Tuple[float, float]] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        parts = [part.strip() for part in item.replace("/", ":").split(":")]
        if len(parts) != 2:
            raise ValueError(f"tp-sl: ожидался формат TP:SL, получено {item!r}")
        try:
            tp, sl = float(parts[0]), float(parts[1])
        except ValueError as exc:
            raise ValueError(f"tp-sl: нечисловое значение в {item!r}") from exc
        if tp <= 0 or sl <= 0:
            raise ValueError(f"tp-sl: множители должны быть положительными, получено {item!r}")
        pairs.append((tp, sl))
    if not pairs:
        raise ValueError("tp-sl: пустой список")
    return pairs


def _parse_model_params(values: Sequence[str]) -> Dict[str, object]:
    """`--model-param key=value` → словарь: int, если целое, иначе float, иначе строка."""
    params: Dict[str, object] = {}
    for raw in values:
        key, separator, value = raw.partition("=")
        key = key.strip()
        if not separator or not key or not value.strip():
            raise ValueError(f"--model-param: ожидается key=value, получено {raw!r}")
        params[key] = _numeric_value(value.strip())
    return params


def _numeric_value(value: str) -> object:
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _with_atr_threshold(template, atr_threshold: float):
    """Подставить порог ATR в конфиг, только если у модели есть это поле.

    У статистических моделей (ou) порога `min_atr_pct` нет — безусловный `replace` падал бы
    с TypeError и ломал walk-forward.
    """
    if not hasattr(template, "min_atr_pct"):
        return template
    return replace(template, min_atr_pct=atr_threshold)


def _strategy_config(args: argparse.Namespace):
    """Конфиг модели из флагов запуска или None для правила без параметров.

    Разбор и проверка порогов живут в реестре моделей: новая модель не требует правки CLI.
    """
    return MODEL_SPECS[args.strategy].make_config(args)


def _atr_threshold(args: argparse.Namespace, bars: Sequence[Bar]) -> float:
    """Порог ATR%: явный (`--min-atr-pct`) или квантиль серии (`--atr-pct-quantile`)."""
    if args.strategy != "v2" or args.atr_pct_quantile is None:
        return args.min_atr_pct
    return atr_pct_quantile(bars, args.atr_pct_quantile)


def _strategy_for(
    args: argparse.Namespace, template, atr_threshold: float
) -> Tuple[StrategyFn, str]:
    """Модель и метка варианта для конкретного таймфрейма."""
    spec = MODEL_SPECS[args.strategy]
    if template is None:
        return build_model(args.strategy), spec.label(None)
    config = _with_atr_threshold(template, atr_threshold)
    return build_model(args.strategy, config), spec.label(config)


def _validate_flags(args: argparse.Namespace) -> None:
    """Проверить несовместимые сочетания флагов до запуска прогонов."""
    if args.no_tp and args.tp_sl:
        raise ValueError(
            "--no-tp несовместим с --tp-sl: цель не выставляется, перебирать пары TP:SL нечего"
        )
    if args.null_seeds < 0:
        raise ValueError("--null-seeds не может быть отрицательным")


def _walkforward_settings(args: argparse.Namespace) -> Optional[Dict[str, object]]:
    """Параметры walk-forward или None. Флаги проверяются до запуска прогонов."""
    wf_flags_given = args.wf_train_bars or args.wf_test_bars or args.wf_grid
    if not args.walkforward:
        if wf_flags_given:
            raise ValueError("--wf-* применяются только вместе с --walkforward")
        return None
    if args.null_seeds:
        raise ValueError(
            "--null-seeds несовместим с --walkforward: нулевая модель считается "
            "для одиночных прогонов, а не для окон"
        )
    if args.wf_train_bars < 1 or args.wf_test_bars < 1:
        raise ValueError("--walkforward требует --wf-train-bars и --wf-test-bars больше 0")
    if args.tp_sl:
        raise ValueError("--tp-sl несовместим с --walkforward: перебор задаётся --wf-grid")
    return {
        "train_bars": args.wf_train_bars,
        "test_bars": args.wf_test_bars,
        "min_trades": args.wf_min_trades,
        "grid": _parse_grid(args.wf_grid),
    }


def _parse_grid(value: str) -> List[Dict[str, float]]:
    """Развернуть `--wf-grid` в декартово произведение точек: `z=1,1.5;adx=0,25`."""
    axes: List[Tuple[str, List[float]]] = []
    for chunk in (value or "").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise ValueError(f"wf-grid: ожидался вид name=v1,v2, получено {chunk!r}")
        name, raw = chunk.split("=", 1)
        try:
            values = [float(item) for item in raw.split(",") if item.strip()]
        except ValueError as exc:
            raise ValueError(f"wf-grid: нечисловые значения у {name.strip()!r}") from exc
        if not values:
            raise ValueError(f"wf-grid: пустой список значений у {name.strip()!r}")
        axes.append((name.strip(), values))
    if not axes:
        return [{}]
    names = [name for name, _ in axes]
    return [dict(zip(names, combo)) for combo in product(*[values for _, values in axes])]


def _wf_builder(args: argparse.Namespace, template, atr_threshold: float) -> StrategyBuilder:
    """Фабрика модели для walk-forward: точка сетки → (модель, метка)."""
    spec = MODEL_SPECS[args.strategy]
    if template is None:

        def build(params: Dict[str, float]) -> Tuple[StrategyFn, str]:
            if params:
                raise ValueError(
                    f"wf-grid: у mean_reversion нет параметров, получено {sorted(params)}"
                )
            return build_model(args.strategy), spec.label(None)

        return build

    def build(params: Dict[str, float]) -> Tuple[StrategyFn, str]:
        try:
            config = replace(_with_atr_threshold(template, atr_threshold), **params)
        except TypeError as exc:
            raise ValueError(
                f"wf-grid: неизвестный параметр для {args.strategy}: {exc}"
            ) from exc
        return build_model(args.strategy, config), spec.label(config)

    return build


def _run_walkforward(
    args: argparse.Namespace,
    datasets: Sequence[HistoryDataset],
    intervals: Sequence[int],
    tariffs: Sequence[str],
    config: RunConfig,
    strategy_config,
    settings: Dict[str, object],
) -> int:
    results: List[WalkforwardResult] = []
    for dataset in datasets:
        for interval in intervals:
            if interval < dataset.interval_minutes or interval % dataset.interval_minutes:
                print(
                    f"пропуск {dataset.instrument.name} {interval}min: "
                    f"не кратен таймфрейму выгрузки {dataset.interval_minutes}min",
                    file=sys.stderr,
                )
                continue
            bars = (
                list(dataset.bars)
                if interval == dataset.interval_minutes
                else aggregate(dataset.bars, interval, dataset.interval_minutes)
            )
            try:
                atr_threshold = _atr_threshold(args, bars)
                builder = _wf_builder(args, strategy_config, atr_threshold)
            except ValueError as exc:
                print(f"ошибка: {exc}", file=sys.stderr)
                return 2
            for tariff in tariffs:
                try:
                    results.append(
                        run_walkforward(
                            bars=bars,
                            instrument=dataset.instrument,
                            config=config,
                            commission=_commission_model(args, tariff),
                            interval_minutes=interval,
                            grid=settings["grid"],  # type: ignore[arg-type]
                            build_strategy=builder,
                            train_bars=settings["train_bars"],  # type: ignore[arg-type]
                            test_bars=settings["test_bars"],  # type: ignore[arg-type]
                            min_train_trades=settings["min_trades"],  # type: ignore[arg-type]
                            strategy_name=args.strategy,
                        )
                    )
                except ValueError as exc:
                    print(
                        f"пропуск {dataset.instrument.name} {interval}min {tariff}: {exc}",
                        file=sys.stderr,
                    )

    if not results:
        print("ошибка: ни одного walk-forward прогона не выполнено", file=sys.stderr)
        return 2

    text = format_walkforward_markdown(results, args.title or "Walk-forward")
    if args.out:
        try:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8")
        except OSError as exc:
            print(f"ошибка записи отчёта: {exc}", file=sys.stderr)
            return 2
        print(f"отчёт сохранён: {args.out}")
    else:
        _safe_print(text)
    return 0


def _join_label(*parts: str) -> str:
    return " ".join(part for part in parts if part)


def _parse_ints(value: str, name: str) -> List[int]:
    try:
        parsed = [int(item) for item in value.split(",") if item.strip()]
    except ValueError as exc:
        raise ValueError(f"{name}: ожидался список целых чисел, получено {value!r}") from exc
    if not parsed:
        raise ValueError(f"{name}: пустой список")
    return parsed


def _parse_msk_time(value: str) -> int:
    """`HH:MM` по Москве → минуты от полуночи. Пустая строка — ограничения нет (0)."""
    if not value or not value.strip():
        return 0
    text = value.strip()
    parts = text.split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ValueError(f"--entry-after-msk: ожидался формат HH:MM, получено {value!r}")
    hours, minutes = int(parts[0]), int(parts[1])
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        raise ValueError(f"--entry-after-msk: время вне диапазона суток: {value!r}")
    return hours * 60 + minutes


def _commission_model(args: argparse.Namespace, tariff: str) -> CommissionModel:
    """Модель издержек тарифа с биржевым сбором за контракт.

    Сбор применяется только к фьючерсам: бумаги платят процент брокера, у срочного рынка
    есть ещё фиксированная плата за контракт, которую процентной ставкой не описать.
    """
    return CommissionModel(
        tariff,
        exchange_fee_per_contract_rub=getattr(args, "exchange_fee_per_contract", 0.0),
    )


def _panel_settings(args: argparse.Namespace) -> Optional[Dict[str, object]]:
    """Параметры панельного режима или None. Сетка разворачивается до запуска прогонов."""
    panel_flags = args.panel_grid or args.panel_null_seeds or args.panel_walkforward
    if not args.panel:
        if panel_flags:
            raise ValueError("--panel-* применяются только вместе с --panel")
        return None
    if args.null_seeds:
        raise ValueError(
            "--null-seeds относится к одиночным прогонам; для панели — --panel-null-seeds"
        )
    if args.panel_walkforward:
        if args.panel_train_days < 1 or args.panel_test_days < 1:
            raise ValueError(
                "--panel-walkforward требует --panel-train-days и --panel-test-days больше 0"
            )
    if args.panel_null_seeds < 0:
        raise ValueError("--panel-null-seeds не может быть отрицательным")

    configs = []
    for point in _parse_grid(args.panel_grid):
        params: Dict[str, object] = {
            "lookback_days": args.panel_lookback,
            "rebalance_days": args.panel_rebalance,
            "legs": args.panel_legs,
            "signal": args.panel_signal,
            "daily_stop_pct": args.panel_daily_stop,
        }
        params.update({key: int(value) for key, value in point.items()})
        try:
            configs.append(PanelConfig(**params))  # type: ignore[arg-type]
        except TypeError as exc:
            raise ValueError(f"panel-grid: неизвестный параметр: {exc}") from exc

    return {
        "configs": configs,
        "universe": args.panel_universe,
        "null_seeds": args.panel_null_seeds,
        "walkforward": args.panel_walkforward,
        "train_days": args.panel_train_days,
        "test_days": args.panel_test_days,
        "min_periods": args.panel_min_periods,
    }


def _run_panel(
    args: argparse.Namespace,
    datasets: Sequence[HistoryDataset],
    tariffs: Sequence[str],
    settings: Dict[str, object],
) -> int:
    """Панельный режим: сетка конфигураций и/или walk-forward по датам, нулевая модель."""
    closes = build_daily_closes(datasets)
    if closes.empty:
        print("ошибка: панель пуста — нет выгрузок с данными", file=sys.stderr)
        return 2
    try:
        closes = select_universe(closes, str(settings["universe"]))
    except ValueError as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 2

    print(
        f"панель: {closes.shape[1]} инструментов, {closes.shape[0]} торговых дней, "
        f"{int(closes.notna().sum().sum())} наблюдений",
        file=sys.stderr,
    )

    sections: List[str] = []
    for tariff in tariffs:
        cost = CommissionModel(tariff).per_side_rate(is_future=False)
        configs = [
            replace(config, cost_per_side=cost)  # type: ignore[arg-type]
            for config in settings["configs"]  # type: ignore[union-attr]
        ]
        if settings["walkforward"]:
            try:
                folds = run_panel_walkforward(
                    closes,
                    configs,
                    train_days=settings["train_days"],  # type: ignore[arg-type]
                    test_days=settings["test_days"],  # type: ignore[arg-type]
                    min_periods=settings["min_periods"],  # type: ignore[arg-type]
                )
            except ValueError as exc:
                print(f"пропуск панели {tariff}: {exc}", file=sys.stderr)
                continue
            sections.append(
                format_panel_walkforward_markdown(folds, f"Walk-forward панели ({tariff})")
            )
            continue

        results = [run_panel(closes, config) for config in configs]
        nulls = []
        seeds = settings["null_seeds"]
        if seeds and results:
            # Нулевая модель — для лучшей конфигурации: считать её для всей сетки бессмысленно дорого.
            best = max(results, key=lambda item: item.net)
            nulls.append(
                run_panel_null(closes, best.config, best, seeds=seeds)  # type: ignore[arg-type]
            )
        sections.append(
            format_panel_markdown(results, nulls, f"Кросс-секционный momentum ({tariff})")
        )

    if not sections:
        print("ошибка: ни одного панельного прогона не выполнено", file=sys.stderr)
        return 2

    text = "\n\n".join(sections)
    if args.out:
        try:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8")
        except OSError as exc:
            print(f"ошибка записи отчёта: {exc}", file=sys.stderr)
            return 2
        print(f"отчёт сохранён: {args.out}")
    else:
        _safe_print(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
