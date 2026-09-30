"""
Модуль поиска RSI-дивергенций.

Соответствие STRATEGY.md (ШАГ 3, «Индикаторы-фильтр»):
  Бычья дивергенция (для лонга от поддержки):
      цена делает Lower Low, RSI делает Higher Low.
  Медвежья дивергенция (для шорта от сопротивления):
      цена делает Higher High, RSI делает Lower High.

Порог расхождения RSI по стратегии: 5–10 пунктов. Дефолт — 5.0.

ВАЖНО про порядок данных:
  Все массивы — в порядке Bybit (closes[0] — самая свежая).
  rsi_series — той же длины, что и closes, в том же порядке
  (см. calculate_rsi_series в src/utils/indicators.py).
  Индекс Pivot.index — в Bybit-порядке: чем больше index, тем СТАРШЕ бар.
  Соответственно: pivot_a.index > pivot_b.index (a — старше).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DivergenceKind = Literal["bullish", "bearish"]

# analyzer.py использует 'up'/'down'. Чтобы не тащить сюда risk.py,
# держим локальный маппинг.
_ANALYZER_TO_KIND: dict[str, DivergenceKind] = {
    "up": "bullish",
    "down": "bearish",
}


# ==================== DATACLASSES ====================

@dataclass(frozen=True)
class Pivot:
    """Локальный экстремум цены с известным RSI."""
    index: int                     # Bybit-порядок: 0 = свежая
    price: float
    rsi: float
    kind: Literal["high", "low"]   # high-пивот или low-пивот


@dataclass(frozen=True)
class Divergence:
    kind: DivergenceKind
    pivot_a: Pivot                 # более старый (index больше)
    pivot_b: Pivot                 # более свежий (index меньше)
    rsi_gap: float
    price_gap_pct: float

    @property
    def age_bars(self) -> int:
        """Насколько свежий пивот отстоит от текущего бара."""
        return self.pivot_b.index


# ==================== ПОИСК ПИВОТОВ ====================

def find_pivots(
    closes: list[float],
    rsi_series: list[float | None],
    left: int = 2,
    right: int = 2,
) -> list[Pivot]:
    """
    Локальные экстремумы цены.

    Бар i считается high-пивотом, если его цена выше `left` баров слева
    (i+1..i+left — старше) и `right` баров справа (i-1..i-right — свежее).
    Симметрично для low-пивота.

    Возвращает список в Bybit-порядке (сначала свежие).
    RSI=None на баре → бар не считается пивотом.
    """
    if left < 1 or right < 1:
        raise ValueError("left and right must be >= 1")
    n = len(closes)
    if n < left + right + 1:
        return []

    pivots: list[Pivot] = []
    for i in range(right, n - left):
        rsi = rsi_series[i] if i < len(rsi_series) else None
        if rsi is None:
            continue

        price = closes[i]
        is_high = True
        is_low = True

        # Свежее (меньший индекс)
        for j in range(i - right, i):
            if closes[j] >= price:
                is_high = False
            if closes[j] <= price:
                is_low = False
            if not is_high and not is_low:
                break

        # Старше (больший индекс)
        if is_high or is_low:
            for j in range(i + 1, i + left + 1):
                if closes[j] >= price:
                    is_high = False
                if closes[j] <= price:
                    is_low = False
                if not is_high and not is_low:
                    break

        if is_high:
            pivots.append(Pivot(index=i, price=price, rsi=rsi, kind="high"))
        elif is_low:
            pivots.append(Pivot(index=i, price=price, rsi=rsi, kind="low"))

    return pivots


# ==================== ПОИСК ДИВЕРГЕНЦИЙ ====================

def find_divergences(
    closes: list[float],
    rsi_series: list[float | None],
    direction: str,
    *,
    min_rsi_gap: float = 5.0,
    min_price_gap_pct: float = 0.001,
    max_lookback: int = 60,
    pivots_left: int = 2,
    pivots_right: int = 2,
) -> list[Divergence]:
    """
    Ищет одну (самую свежую) дивергенцию в окне max_lookback.

    Args:
        direction: 'up' → бычья, 'down' → медвежья (нотация analyzer.py).
        min_rsi_gap: минимальное |RSI_b - RSI_a| (стратегия: 5–10).
        min_price_gap_pct: минимальный относительный сдвиг цены между
            пивотами (защита от «шума» на плоскости).
        max_lookback: окно в барах от [0] (Bybit-порядок).
        pivots_left/right: параметры find_pivots.

    Returns:
        [] если ничего не нашли. Иначе список длины 1 (самая свежая).
        Всегда безопасно: на коротких/битых данных не бросает.
    """
    if direction not in _ANALYZER_TO_KIND:
        raise ValueError(f"direction must be 'up'|'down', got {direction!r}")

    kind = _ANALYZER_TO_KIND[direction]
    n = len(closes)

    if n < max(pivots_left + pivots_right + 1, 3):
        return []
    if not rsi_series:
        return []

    pivots = find_pivots(closes, rsi_series, pivots_left, pivots_right)
    if len(pivots) < 2:
        return []

    # Обрезаем окно. pivots уже в Bybit-порядке (свежие первыми).
    window = [p for p in pivots if p.index < max_lookback]
    if len(window) < 2:
        return []

    expected_pivot_kind: Literal["high", "low"] = (
        "low" if kind == "bullish" else "high"
    )

    # pivot_b — самый свежий подходящий, затем ищем старшего pivot_a.
    for b in window:
        if b.kind != expected_pivot_kind:
            continue

        for a in window:
            if a.index <= b.index:
                continue  # a должен быть старше
            if a.kind != expected_pivot_kind:
                continue

            rsi_gap = abs(b.rsi - a.rsi)
            if rsi_gap < min_rsi_gap:
                continue

            price_gap_pct = (
                abs(b.price - a.price) / a.price if a.price else 0.0
            )
            if price_gap_pct < min_price_gap_pct:
                continue

            # Семантика дивергенции:
            #   bullish: цена LL (b.price < a.price), RSI HL (b.rsi > a.rsi)
            #   bearish: цена HH (b.price > a.price), RSI LH (b.rsi < a.rsi)
            if kind == "bullish":
                ok = b.price < a.price and b.rsi > a.rsi
            else:
                ok = b.price > a.price and b.rsi < a.rsi

            if not ok:
                continue

            return [Divergence(
                kind=kind,
                pivot_a=a,
                pivot_b=b,
                rsi_gap=rsi_gap,
                price_gap_pct=price_gap_pct,
            )]

    return []


def has_divergence(
    closes: list[float],
    rsi_series: list[float | None],
    direction: str,
    **kwargs,
) -> bool:
    """Шорткат для analyzer.py: есть ли хоть одна дивергенция."""
    return bool(find_divergences(closes, rsi_series, direction, **kwargs))