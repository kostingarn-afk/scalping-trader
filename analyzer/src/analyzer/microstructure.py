"""Состояние микроструктуры: стакан, поток сделок и VPIN — шаг 1.3 `to_do.md` §4.1.

Collector публикует `ORDER_BOOK` и `TRADE`, но Analyzer раньше их отбрасывал. Здесь живёт
состояние по каждому инструменту: последний стакан (для order imbalance) и объёмные бакеты
сделок (для VPIN). Состояние обновляет handler-консьюмер, а не пайплайн: порядок сообщений
гарантирован внутри процесса (partition by `instrument_id`).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Sequence, Tuple

from .hawkes import MIN_EVENTS, estimate_hawkes

BUY = 1
SELL = -1
UNKNOWN = 0


def classify_trade(
    price: float, previous_price: Optional[float], previous_direction: int
) -> int:
    """Тик-правило: выше предыдущей цены — покупка, ниже — продажа, равна — как раньше.

    Без предыдущей цены направление неизвестно: угадывать его значит вносить смещение в VPIN.
    """
    if previous_price is None:
        return UNKNOWN
    if price > previous_price:
        return BUY
    if price < previous_price:
        return SELL
    return previous_direction


class VolumeBuckets:
    """VPIN по бакетам фиксированного объёма.

    Бакет закрывается, когда набрал `bucket_volume`; VPIN — среднее |buy − sell| / bucket по
    последним `buckets` завершённым бакетам. Незавершённый бакет в расчёт не входит.
    """

    def __init__(self, bucket_volume: int, buckets: int) -> None:
        if bucket_volume <= 0:
            raise ValueError("bucket_volume must be positive")
        if buckets <= 0:
            raise ValueError("buckets must be positive")
        self._bucket_volume = float(bucket_volume)
        self._buckets = buckets
        self._completed: Deque[Tuple[float, float]] = deque(maxlen=buckets)
        self._buy = 0.0
        self._sell = 0.0

    def add(self, volume: int, direction: int) -> None:
        """Добавить сделку; объём сверх остатка бакета переносится в следующий."""
        if volume <= 0 or direction == UNKNOWN:
            return
        remaining = float(volume)
        while remaining > 0:
            capacity = self._bucket_volume - (self._buy + self._sell)
            take = min(remaining, capacity)
            if direction == BUY:
                self._buy += take
            else:
                self._sell += take
            remaining -= take
            if self._buy + self._sell >= self._bucket_volume:
                self._completed.append((self._buy, self._sell))
                self._buy = 0.0
                self._sell = 0.0

    def vpin(self) -> Optional[float]:
        if len(self._completed) < self._buckets:
            return None
        imbalance = sum(abs(buy - sell) for buy, sell in self._completed)
        return imbalance / (len(self._completed) * self._bucket_volume)


@dataclass
class _Book:
    bids: List[int] = field(default_factory=list)
    asks: List[int] = field(default_factory=list)


class MicrostructureState:
    """Последний стакан и поток сделок по каждому инструменту."""

    def __init__(
        self,
        depth: int = 10,
        bucket_volume: int = 1000,
        buckets: int = 20,
        hawkes_window: int = 500,
        hawkes_refresh: int = 25,
    ) -> None:
        if depth <= 0:
            raise ValueError("depth must be positive")
        if hawkes_window < MIN_EVENTS:
            raise ValueError(f"hawkes_window must be at least {MIN_EVENTS}")
        if hawkes_refresh < 1:
            raise ValueError("hawkes_refresh must be positive")
        self._depth = depth
        self._bucket_volume = bucket_volume
        self._buckets = buckets
        self._hawkes_window = hawkes_window
        self._hawkes_refresh = hawkes_refresh
        self._books: Dict[str, _Book] = {}
        self._flows: Dict[str, VolumeBuckets] = {}
        self._last_price: Dict[str, float] = {}
        self._last_direction: Dict[str, int] = {}
        self._trade_times: Dict[str, Deque[float]] = {}
        self._hawkes: Dict[str, Optional[float]] = {}
        self._trades_since_hawkes: Dict[str, int] = {}

    def update_book(
        self, figi: str, bids_qty: Sequence[int], asks_qty: Sequence[int]
    ) -> None:
        self._books[figi] = _Book(
            bids=[int(q) for q in list(bids_qty)[: self._depth]],
            asks=[int(q) for q in list(asks_qty)[: self._depth]],
        )

    def on_trade(
        self,
        figi: str,
        price: float,
        volume: int,
        direction: int = UNKNOWN,
        timestamp: Optional[float] = None,
    ) -> None:
        resolved = direction
        if resolved not in (BUY, SELL):
            resolved = classify_trade(
                price, self._last_price.get(figi), self._last_direction.get(figi, UNKNOWN)
            )
        self._last_price[figi] = price
        self._last_direction[figi] = resolved
        self._flow(figi).add(volume, resolved)
        if timestamp is not None:
            self._add_trade_time(figi, float(timestamp))

    def _add_trade_time(self, figi: str, timestamp: float) -> None:
        times = self._trade_times.get(figi)
        if times is None:
            times = deque(maxlen=self._hawkes_window)
            self._trade_times[figi] = times
        times.append(timestamp)
        pending = self._trades_since_hawkes.get(figi, 0) + 1
        if pending >= self._hawkes_refresh and len(times) >= MIN_EVENTS:
            params = estimate_hawkes(list(times))
            self._hawkes[figi] = None if params is None else params.branching
            pending = 0
        self._trades_since_hawkes[figi] = pending

    def snapshot(self, figi: str) -> Dict[str, Any]:
        """Данные для расчёта индикаторов на завершённой свече."""
        book = self._books.get(figi)
        flow = self._flows.get(figi)
        return {
            "bids_qty": list(book.bids) if book is not None else [],
            "asks_qty": list(book.asks) if book is not None else [],
            "vpin": flow.vpin() if flow is not None else None,
            "hawkes": self._hawkes.get(figi),
        }

    def _flow(self, figi: str) -> VolumeBuckets:
        flow = self._flows.get(figi)
        if flow is None:
            flow = VolumeBuckets(self._bucket_volume, self._buckets)
            self._flows[figi] = flow
        return flow
