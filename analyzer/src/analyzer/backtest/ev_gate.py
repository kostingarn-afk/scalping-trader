"""Гейт входа по ожидаемой доходности — шаг 1.10 `to_do.md` §4.1.

`EV = p·avgWin − (1−p)·avgLoss`, где `p` — доля прибыльных сделок, `avgWin`/`avgLoss` — средние
модули `net_pnl`. Комиссия и издержки исполнения уже вычтены при расчёте сделки (спред и импакт
входят в исполненную цену), поэтому отдельно в формуле не вычитаются: `C` и `S` из §1 —
это те же деньги, снятые один раз.

Гейт смотрит только назад: оценка строится по уже закрытым сделкам, поэтому заглядывания в
будущее нет. Меньше `min_trades` сделок — оценки нет, гейт не действует: запрет торговли не дал
бы набрать статистику, которую он же и проверяет.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from .models import Trade

# Порог выборки совпадает с критерием гейта §1 (не меньше 50 сделок);
# окно ограничивает влияние старых режимов на текущую оценку.
MIN_TRADES = 50
WINDOW = 200


@dataclass(frozen=True)
class EvEstimate:
    """Оценка по закрытым сделкам: доли, средние модули и EV в рублях на сделку."""

    trades: int
    win_rate: float
    avg_win_rub: float
    avg_loss_rub: float
    ev_rub: float

    @property
    def is_positive(self) -> bool:
        return self.ev_rub > 0


def estimate(
    trades: Sequence[Trade],
    min_trades: int = MIN_TRADES,
    window: int = WINDOW,
) -> Optional[EvEstimate]:
    """Оценить EV по последним `window` сделкам; `None` — оценка невозможна."""
    if min_trades <= 0 or window <= 0:
        return None
    sample = list(trades)[-window:]
    if len(sample) < min_trades:
        return None

    wins = [trade.net_pnl for trade in sample if trade.net_pnl > 0]
    losses = [-trade.net_pnl for trade in sample if trade.net_pnl <= 0]
    # Односторонняя выборка формально даёт EV, но отражает не преимущество, а отсутствие
    # противоположных исходов в окне: считать такую оценку основанием для входа нельзя.
    if not wins or not losses:
        return None

    win_rate = len(wins) / len(sample)
    avg_win = sum(wins) / len(wins)
    avg_loss = sum(losses) / len(losses)
    ev = win_rate * avg_win - (1.0 - win_rate) * avg_loss
    return EvEstimate(
        trades=len(sample),
        win_rate=win_rate,
        avg_win_rub=avg_win,
        avg_loss_rub=avg_loss,
        ev_rub=ev,
    )


__all__ = ["EvEstimate", "estimate", "MIN_TRADES", "WINDOW"]
