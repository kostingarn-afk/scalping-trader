"""Тесты CLI бэктеста на локальной выгрузке."""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from analyzer.backtest.cli import _parse_msk_time, main

START = datetime(2026, 11, 13, 7, 0, tzinfo=timezone.utc)


def _write_history(
    directory: Path, ticker: str, class_code: str, bars: int, interval: int = 1
) -> Path:
    base = f"{ticker}_{interval}min"
    path = directory / f"{base}.csv"
    rows = ["ts,open,high,low,close,volume"]
    for index in range(bars):
        ts = (START + timedelta(minutes=index * interval)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows.append(f"{ts},100,100.2,99.8,100,10")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    (directory / f"{base}.meta.json").write_text(
        json.dumps(
            {
                "ticker": ticker,
                "class_code": class_code,
                "lot_size": 1,
                "interval_minutes": interval,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_cli_runs_on_data_dir_and_writes_report(tmp_path: Path, capsys):
    _write_history(tmp_path, "SBER", "TQBR", 60)
    out = tmp_path / "report.md"

    code = main(
        [
            "--data-dir",
            str(tmp_path),
            "--intervals",
            "1,5",
            "--tariffs",
            "trader,investor",
            "--out",
            str(out),
        ]
    )

    assert code == 0
    report = out.read_text(encoding="utf-8")
    assert "SBER@TQBR 1min trader" in report
    assert "SBER@TQBR 5min investor" in report
    assert "отчёт сохранён" in capsys.readouterr().out


def test_cli_prints_report_without_out_file(tmp_path: Path, capsys):
    path = _write_history(tmp_path, "SBER", "TQBR", 60)

    code = main(["--csv", str(path), "--intervals", "1"])

    assert code == 0
    assert "SBER@TQBR 1min investor" in capsys.readouterr().out


def test_cli_requires_history_source():
    assert main(["--intervals", "1"]) == 2


def test_cli_rejects_unknown_tariff(tmp_path: Path):
    _write_history(tmp_path, "SBER", "TQBR", 30)

    assert main(["--data-dir", str(tmp_path), "--tariffs", "scalper"]) == 2


def test_cli_rejects_ticker_with_data_dir(tmp_path: Path):
    _write_history(tmp_path, "SBER", "TQBR", 30)

    assert main(["--data-dir", str(tmp_path), "--ticker", "SBER"]) == 2


def test_cli_skips_interval_below_native(tmp_path: Path, capsys):
    """5-мин выгрузка и `--intervals 1`: интервал отбрасывается, прогонов не остаётся."""
    _write_history(tmp_path, "SBER", "TQBR", 40, interval=5)

    code = main(["--data-dir", str(tmp_path), "--intervals", "1"])

    captured = capsys.readouterr()
    assert code == 2
    assert "не кратен" in captured.err
    assert "ни одного прогона" in captured.err


def test_cli_native_interval_does_not_override_meta(tmp_path: Path, capsys):
    """`--native-interval` не задан → таймфрейм берётся из meta, а не подставляется как 1."""
    _write_history(tmp_path, "SBER", "TQBR", 40, interval=5)

    code = main(["--data-dir", str(tmp_path), "--intervals", "5"])

    assert code == 0
    assert "SBER@TQBR 5min investor" in capsys.readouterr().out


def test_cli_sweeps_tp_sl_pairs(tmp_path: Path, capsys):
    """`--tp-sl` даёт отдельный прогон на каждую пару; метка различает строки отчёта."""
    _write_history(tmp_path, "SBER", "TQBR", 60)

    code = main(["--data-dir", str(tmp_path), "--intervals", "1", "--tp-sl", "2:1.5,3:2"])

    out = capsys.readouterr().out
    assert code == 0
    assert "SBER@TQBR 1min investor tp2/sl1.5" in out
    assert "SBER@TQBR 1min investor tp3/sl2" in out
    assert "перебор TP:SL 2:1.5, 3:2" in out


def test_cli_single_tp_sl_pair_keeps_run_name(tmp_path: Path, capsys):
    """Одна пара в `--tp-sl` не добавляет метку: отчёт совпадает с `--atr-tp/--atr-sl`."""
    _write_history(tmp_path, "SBER", "TQBR", 60)

    code = main(["--data-dir", str(tmp_path), "--intervals", "1", "--tp-sl", "3:2"])

    assert code == 0
    assert "SBER@TQBR 1min investor |" in capsys.readouterr().out


def test_cli_rejects_malformed_tp_sl(tmp_path: Path, capsys):
    _write_history(tmp_path, "SBER", "TQBR", 30)

    code = main(["--data-dir", str(tmp_path), "--tp-sl", "2:1.5:3"])

    assert code == 2
    assert "TP:SL" in capsys.readouterr().err


def test_cli_runs_v2_strategy(tmp_path: Path, capsys):
    """`--strategy v2` подключает фильтры; вариант виден в шапке и в метке прогона."""
    _write_history(tmp_path, "SBER", "TQBR", 80)

    code = main(
        [
            "--data-dir",
            str(tmp_path),
            "--intervals",
            "1",
            "--strategy",
            "v2",
            "--min-atr-pct",
            "0.5",
        ]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "Стратегия: **v2**" in out
    assert "v2-z2-adx25-atr0.5" in out


def test_cli_atr_quantile_sets_v2_threshold(tmp_path: Path, capsys):
    """`--atr-pct-quantile` подставляет порог из самой серии (in-sample, см. отчёт)."""
    _write_history(tmp_path, "SBER", "TQBR", 80)

    code = main(
        ["--data-dir", str(tmp_path), "--intervals", "1", "--strategy", "v2", "--atr-pct-quantile", "80"]
    )

    assert code == 0
    assert "v2-z2-adx25-atr" in capsys.readouterr().out


def test_cli_rejects_atr_filter_without_v2(tmp_path: Path, capsys):
    _write_history(tmp_path, "SBER", "TQBR", 30)

    code = main(["--data-dir", str(tmp_path), "--min-atr-pct", "0.1"])

    assert code == 2
    assert "только с --strategy v2" in capsys.readouterr().err


def test_cli_rejects_both_atr_thresholds(tmp_path: Path, capsys):
    _write_history(tmp_path, "SBER", "TQBR", 30)

    code = main(
        [
            "--data-dir",
            str(tmp_path),
            "--strategy",
            "v2",
            "--min-atr-pct",
            "0.1",
            "--atr-pct-quantile",
            "80",
        ]
    )

    assert code == 2
    assert "одно из двух" in capsys.readouterr().err


def test_cli_runs_momentum_strategy(tmp_path: Path, capsys):
    _write_history(tmp_path, "SBER", "TQBR", 80)

    code = main(
        ["--data-dir", str(tmp_path), "--intervals", "1", "--strategy", "momentum", "--z-entry", "2"]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "Стратегия: **momentum**" in out
    assert "mom-z2" in out


def test_cli_runs_ou_with_model_param(tmp_path: Path, capsys):
    """`--strategy ou` берёт параметры модели из `--model-param`, метка содержит окно."""
    _write_history(tmp_path, "SBER", "TQBR", 80)

    code = main(
        [
            "--data-dir",
            str(tmp_path),
            "--intervals",
            "1",
            "--strategy",
            "ou",
            "--model-param",
            "window=120",
            "--model-param",
            "min_bars=30",
        ]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "Стратегия: **ou**" in out
    assert "ou-z2-w120" in out


def test_cli_rejects_unknown_model_param(tmp_path: Path, capsys):
    _write_history(tmp_path, "SBER", "TQBR", 30)

    code = main(
        ["--data-dir", str(tmp_path), "--strategy", "ou", "--model-param", "foo=1"]
    )

    assert code == 2
    assert "неизвестные параметры" in capsys.readouterr().err


def test_cli_rejects_malformed_model_param(tmp_path: Path, capsys):
    _write_history(tmp_path, "SBER", "TQBR", 30)

    code = main(["--data-dir", str(tmp_path), "--model-param", "window"])

    assert code == 2
    assert "ожидается key=value" in capsys.readouterr().err


def test_cli_walkforward_writes_report(tmp_path: Path, capsys):
    _write_history(tmp_path, "SBER", "TQBR", 120)
    out = tmp_path / "wf.md"

    code = main(
        [
            "--data-dir",
            str(tmp_path),
            "--intervals",
            "1",
            "--strategy",
            "momentum",
            "--walkforward",
            "--wf-train-bars",
            "60",
            "--wf-test-bars",
            "20",
            "--wf-grid",
            "z_entry=1.5,2",
            "--out",
            str(out),
        ]
    )

    assert code == 0
    report = out.read_text(encoding="utf-8")
    assert "walk-forward" in report.lower()
    assert "OOS" in report


def test_cli_walkforward_requires_window_sizes(tmp_path: Path, capsys):
    _write_history(tmp_path, "SBER", "TQBR", 120)

    code = main(["--data-dir", str(tmp_path), "--walkforward"])

    assert code == 2
    assert "wf-train-bars" in capsys.readouterr().err


def test_cli_wf_flags_require_walkforward(tmp_path: Path, capsys):
    _write_history(tmp_path, "SBER", "TQBR", 120)

    code = main(["--data-dir", str(tmp_path), "--wf-test-bars", "10"])

    assert code == 2
    assert "только вместе с --walkforward" in capsys.readouterr().err


def test_cli_walkforward_rejects_tp_sl_sweep(tmp_path: Path, capsys):
    _write_history(tmp_path, "SBER", "TQBR", 120)

    code = main(
        [
            "--data-dir",
            str(tmp_path),
            "--walkforward",
            "--wf-train-bars",
            "60",
            "--wf-test-bars",
            "20",
            "--tp-sl",
            "3:2",
        ]
    )

    assert code == 2
    assert "--tp-sl несовместим" in capsys.readouterr().err


def test_cli_walkforward_rejects_malformed_grid(tmp_path: Path, capsys):
    _write_history(tmp_path, "SBER", "TQBR", 120)

    code = main(
        [
            "--data-dir",
            str(tmp_path),
            "--strategy",
            "momentum",
            "--walkforward",
            "--wf-train-bars",
            "60",
            "--wf-test-bars",
            "20",
            "--wf-grid",
            "z_entry",
        ]
    )

    assert code == 2
    assert "wf-grid" in capsys.readouterr().err


def test_cli_walkforward_rejects_unknown_grid_param(tmp_path: Path, capsys):
    _write_history(tmp_path, "SBER", "TQBR", 120)

    code = main(
        [
            "--data-dir",
            str(tmp_path),
            "--strategy",
            "momentum",
            "--walkforward",
            "--wf-train-bars",
            "60",
            "--wf-test-bars",
            "20",
            "--wf-grid",
            "nonexistent=1",
        ]
    )

    assert code == 2
    assert "неизвестный параметр" in capsys.readouterr().err


def _write_wave_history(
    directory: Path, ticker: str, class_code: str, bars: int, interval: int = 1
) -> Path:
    """Ряд с колебаниями: на плоской выгрузке ни одно правило не открывает сделок."""
    base = f"{ticker}_{interval}min"
    path = directory / f"{base}.csv"
    rows = ["ts,open,high,low,close,volume"]
    previous = 100.0
    for index in range(bars):
        close = 100.0 + 6.0 * math.sin(index / 6.0)
        ts = (START + timedelta(minutes=index * interval)).strftime("%Y-%m-%dT%H:%M:%SZ")
        high = max(previous, close) + 0.5
        low = min(previous, close) - 0.5
        rows.append(f"{ts},{previous:.4f},{high:.4f},{low:.4f},{close:.4f},10")
        previous = close
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    (directory / f"{base}.meta.json").write_text(
        json.dumps(
            {
                "ticker": ticker,
                "class_code": class_code,
                "lot_size": 1,
                "interval_minutes": interval,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_cli_no_tp_marks_parameters(tmp_path: Path, capsys):
    _write_history(tmp_path, "SBER", "TQBR", 80)

    code = main(
        ["--data-dir", str(tmp_path), "--intervals", "1", "--strategy", "momentum", "--no-tp"]
    )

    assert code == 0
    assert "TP выключен" in capsys.readouterr().out


def test_cli_no_tp_rejects_tp_sl_sweep(tmp_path: Path, capsys):
    _write_history(tmp_path, "SBER", "TQBR", 80)

    code = main(["--data-dir", str(tmp_path), "--intervals", "1", "--no-tp", "--tp-sl", "3:2"])

    assert code == 2
    assert "несовместим" in capsys.readouterr().err


def test_cli_null_model_reports_p_value(tmp_path: Path, capsys):
    """Порог входа занижен намеренно: тест проверяет обвязку нулевой модели, а не правило."""
    _write_wave_history(tmp_path, "SBER", "TQBR", 160)

    code = main(
        [
            "--data-dir",
            str(tmp_path),
            "--intervals",
            "1",
            "--strategy",
            "momentum",
            "--z-entry",
            "0.3",
            "--rsi-buy",
            "55",
            "--rsi-sell",
            "45",
            "--null-seeds",
            "5",
        ]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "Нулевая модель" in out
    assert "p-value" in out


def test_cli_null_seeds_rejected_with_walkforward(tmp_path: Path, capsys):
    _write_history(tmp_path, "SBER", "TQBR", 120)

    code = main(
        [
            "--data-dir",
            str(tmp_path),
            "--walkforward",
            "--wf-train-bars",
            "60",
            "--wf-test-bars",
            "20",
            "--null-seeds",
            "5",
        ]
    )

    assert code == 2
    assert "несовместим" in capsys.readouterr().err


def test_parse_msk_time():
    assert _parse_msk_time("") == 0
    assert _parse_msk_time("11:00") == 660
    assert _parse_msk_time(" 9:30 ") == 570
    for broken in ("1100", "11", "25:00", "11:70", "aa:bb"):
        with pytest.raises(ValueError):
            _parse_msk_time(broken)
