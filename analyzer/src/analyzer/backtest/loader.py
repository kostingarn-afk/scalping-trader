"""Чтение исторических свечей, выгруженных Collector'ом.

Контракт выгрузки (владелец — Collector, Java; Analyzer к T-Invest API не обращается):

- `{ticker}_{interval}min.csv` — заголовок `ts,open,high,low,close,volume`, `ts` в ISO-8601 UTC
  (время закрытия свечи), строки по возрастанию времени;
- `{ticker}_{interval}min.meta.json` — поля `ticker`, `class_code`, `lot_size`,
  `interval_minutes` (обязательные), `fetched_at`, `source` (информационные) и
  `min_price_increment` (шаг цены; с шага 0.2 пишется всегда, в старых выгрузках отсутствует).

Выгрузка 1-минутной истории за месяцы одним запросом `GetCandles` невозможна из-за лимита
периода, поэтому Collector собирает файл постранично и отдаёт уже склеенный ряд.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from .models import Bar, Instrument

META_SUFFIX = ".meta.json"
REQUIRED_COLUMNS = ("ts", "open", "high", "low", "close")
NUMERIC_COLUMNS = ("open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class HistoryMeta:
    ticker: str
    class_code: str
    lot_size: int
    interval_minutes: int
    fetched_at: Optional[str] = None
    source: Optional[str] = None
    # Шаг цены (шаг 0.2 `to_do.md`). В выгрузках до появления поля отсутствует: тогда шаг
    # определяет исследовательский контур, помечая выведенное значение как UNVERIFIED.
    min_price_increment: Optional[float] = None

    @classmethod
    def from_dict(cls, data: dict) -> "HistoryMeta":
        missing = [
            key
            for key in ("ticker", "class_code", "lot_size", "interval_minutes")
            if data.get(key) in (None, "")
        ]
        if missing:
            raise ValueError(f"meta.json misses required fields: {', '.join(missing)}")
        raw_increment = data.get("min_price_increment")
        increment: Optional[float] = None
        if raw_increment not in (None, ""):
            increment = float(raw_increment)
            if increment <= 0:
                raise ValueError(f"meta.json: min_price_increment must be positive, got {increment}")
        return cls(
            ticker=str(data["ticker"]),
            class_code=str(data["class_code"]),
            lot_size=int(data["lot_size"]),
            interval_minutes=int(data["interval_minutes"]),
            fetched_at=data.get("fetched_at"),
            source=data.get("source"),
            min_price_increment=increment,
        )


@dataclass(frozen=True)
class HistoryDataset:
    instrument: Instrument
    interval_minutes: int
    bars: Tuple[Bar, ...]
    source: Path
    meta: Optional[HistoryMeta] = None

    def __len__(self) -> int:
        return len(self.bars)


def read_meta(csv_path: Path) -> Optional[HistoryMeta]:
    """Прочитать sidecar `.meta.json` рядом с CSV; None, если файла нет."""
    meta_path = _meta_path(csv_path)
    if not meta_path.exists():
        return None
    try:
        raw = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{meta_path}: invalid JSON ({exc})") from exc
    return HistoryMeta.from_dict(raw)


def load_dataset(
    csv_path: Path,
    instrument: Optional[Instrument] = None,
    interval_minutes: Optional[int] = None,
) -> HistoryDataset:
    """Загрузить выгрузку.

    `instrument`/`interval_minutes` нужны, только если sidecar-файла нет: без meta контракт
    неполон, и прогон по угаданным параметрам запрещён.
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"history file not found: {csv_path}")

    meta = read_meta(csv_path)
    if meta is not None:
        instrument = instrument or Instrument(meta.ticker, meta.class_code, meta.lot_size)
        interval_minutes = interval_minutes or meta.interval_minutes
    if instrument is None or interval_minutes is None:
        raise ValueError(
            f"{csv_path.name}: нет {META_SUFFIX} и не заданы instrument/interval — "
            "контракт выгрузки Collector неполон"
        )
    if interval_minutes < 1:
        raise ValueError("interval_minutes must be positive")

    bars = tuple(read_bars(csv_path))
    _validate_step(bars, interval_minutes, csv_path)

    return HistoryDataset(
        instrument=instrument,
        interval_minutes=interval_minutes,
        bars=bars,
        source=csv_path,
        meta=meta,
    )


def read_bars(csv_path: Path) -> List[Bar]:
    """Разобрать CSV в список свечей; `ts` трактуется как UTC."""
    csv_path = Path(csv_path)
    bars: List[Bar] = []
    with csv_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"{csv_path.name}: пустой файл без заголовка")
        header = {name.strip().lower(): name for name in reader.fieldnames}
        missing = [
            key
            for key in REQUIRED_COLUMNS
            if key not in header and not (key == "ts" and "timestamp" in header)
        ]
        if missing:
            raise ValueError(f"{csv_path.name}: нет колонок {', '.join(missing)}")

        previous: Optional[datetime] = None
        for line_no, row in enumerate(reader, start=2):
            ts = _parse_timestamp(row[header.get("ts") or header["timestamp"]], csv_path, line_no)
            if previous is not None and ts <= previous:
                raise ValueError(
                    f"{csv_path.name}:{line_no}: время не возрастает "
                    f"({ts.isoformat()} после {previous.isoformat()})"
                )
            previous = ts
            try:
                values = {
                    column: float(row[header[column]])
                    for column in NUMERIC_COLUMNS
                    if column in header
                }
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{csv_path.name}:{line_no}: нечисловое значение ({exc})") from exc
            _validate_ohlc(values, csv_path, line_no)
            bars.append(
                Bar(
                    ts=ts,
                    open=values["open"],
                    high=values["high"],
                    low=values["low"],
                    close=values["close"],
                    volume=int(values.get("volume", 0.0)),
                )
            )
    if not bars:
        raise ValueError(f"{csv_path.name}: нет ни одной свечи")
    return bars


def scan_dir(directory: Path) -> List[HistoryDataset]:
    """Загрузить все выгрузки каталога (`*.csv`), используя sidecar-файлы."""
    directory = Path(directory)
    if not directory.is_dir():
        raise FileNotFoundError(f"history directory not found: {directory}")
    return [load_dataset(csv_path) for csv_path in sorted(directory.glob("*.csv"))]


def aggregate(bars: Sequence[Bar], target_minutes: int, native_minutes: int = 1) -> List[Bar]:
    """Слить бары в более крупный таймфрейм.

    Группировка идёт по времени **открытия** нативного бара (`ts` — время закрытия, поэтому
    из него вычитается длительность бара): иначе 5-мин свеча собиралась бы из закрытий
    07:00…07:04 и была бы смещена на один нативный бар относительно границ брокерских свечей.

    Разрывы (ночь, выходные) новых групп не создают, но крайние группы сессии остаются
    неполными — в них попадает меньше `target_minutes // native_minutes` баров. Это
    ожидаемо: недостающие бары в выгрузке отсутствуют, свеча отражает только реальные данные.
    """
    if target_minutes < native_minutes:
        raise ValueError("target interval must be greater than or equal to native interval")
    if target_minutes % native_minutes:
        raise ValueError("target interval must be a multiple of native interval")
    if target_minutes == native_minutes:
        return list(bars)

    step = target_minutes * 60
    native = native_minutes * 60
    groups: List[List[Bar]] = []
    current_key: Optional[int] = None
    for bar in bars:
        key = (int(bar.ts.timestamp()) - native) // step
        if key != current_key:
            groups.append([])
            current_key = key
        groups[-1].append(bar)

    return [
        Bar(
            ts=group[-1].ts,
            open=group[0].open,
            high=max(item.high for item in group),
            low=min(item.low for item in group),
            close=group[-1].close,
            volume=sum(item.volume for item in group),
        )
        for group in groups
    ]


def _meta_path(csv_path: Path) -> Path:
    name = csv_path.name
    if name.endswith(".csv"):
        name = name[: -len(".csv")]
    return csv_path.with_name(name + META_SUFFIX)


def _validate_ohlc(values: dict, csv_path: Path, line_no: int) -> None:
    """Проверить OHLC-инварианты: битая выгрузка не должна доезжать до прогона."""
    open_price = values["open"]
    high = values["high"]
    low = values["low"]
    close = values["close"]
    if min(open_price, high, low, close) <= 0:
        raise ValueError(f"{csv_path.name}:{line_no}: цена должна быть положительной")
    if high < max(open_price, close) or low > min(open_price, close):
        raise ValueError(
            f"{csv_path.name}:{line_no}: нарушен OHLC "
            f"(open={open_price}, high={high}, low={low}, close={close})"
        )
    if high < low:
        raise ValueError(f"{csv_path.name}:{line_no}: high < low ({high} < {low})")
    if values.get("volume", 0.0) < 0:
        raise ValueError(f"{csv_path.name}:{line_no}: отрицательный объём")


def _validate_step(bars: Sequence[Bar], interval_minutes: int, csv_path: Path) -> None:
    """Шаг между свечами должен быть кратен заявленному таймфрейму.

    Ловит рассинхрон `.meta.json` и данных: выгрузка 5-мин свечей, помеченная как 1-мин,
    иначе поедет в прогон с неверной агрегацией.
    """
    step = interval_minutes * 60
    for previous, current in zip(bars, bars[1:]):
        delta = int((current.ts - previous.ts).total_seconds())
        if delta % step:
            raise ValueError(
                f"{csv_path.name}: шаг {delta} с не кратен таймфрейму {interval_minutes} мин"
            )


def _parse_timestamp(value: Optional[str], csv_path: Path, line_no: int) -> datetime:
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{csv_path.name}:{line_no}: пустое время")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{csv_path.name}:{line_no}: неверное время {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def expected_file_names(ticker: str, interval_minutes: int) -> Iterable[str]:
    """Имена файлов выгрузки по контракту (используется Collector'ом и тестами)."""
    base = f"{ticker}_{interval_minutes}min"
    return (f"{base}.csv", f"{base}{META_SUFFIX}")


__all__ = [
    "HistoryDataset",
    "HistoryMeta",
    "META_SUFFIX",
    "aggregate",
    "expected_file_names",
    "load_dataset",
    "read_bars",
    "read_meta",
    "scan_dir",
]
