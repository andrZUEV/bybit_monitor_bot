"""
Модуль уровней и структуры тренда для стратегии «Уровневый отбой».

Соответствие STRATEGY.md:
  ШАГ 2 — валидация уровня:
    * минимум 2–3 касания;
    * уровень виден на старшем ТФ (4H / 1D);
    * зеркальность (бывшее сопротивление → поддержка, или наоборот)
      — приоритет №1;
    * фильтр износа: 5+ тестов подряд без пробоя → уровень хрупкий.

  ШАГ 1 — контекст:
    * HH/HL — восходящая структура, LH/LL — нисходящая;
    * торгуем только по направлению старшего ТФ.

ВАЖНО про порядок данных:
  Все klines — в порядке Bybit (новые первые, [0] = самая свежая).
  `find_levels` работает по high/low, а не по close (см. STRATEGY.md:
  «уровень — зона, не линия»).
  `find_hh_hl` использует `find_pivots` из divergence.py, но по high/low.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# ==================== ТИПЫ ====================

Direction = Literal["up", "down"]
TrendDir = Literal["up", "down", "side"]


# ==================== DATACLASSES ====================

@dataclass(frozen=True)
class Level:
    """Ценовой уровень (зона) на старшем ТФ."""
    price: float                    # центр зоны
    price_low: float                # нижняя граница (price - tolerance)
    price_high: float               # верхняя граница (price + tolerance)
    touches: int                    # касаний в окне lookback
    is_mirror: bool                 # был сопротивлением → стал поддержкой (или наоборот)
    is_worn: bool                   # touches >= worn_threshold
    source_tf: str                  # '240' | 'D'
    strength: int                   # 0..N — условный score для сортировки

    def contains(self, price: float) -> bool:
        """Попадает ли цена в зону уровня."""
        return self.price_low <= price <= self.price_high


@dataclass(frozen=True)
class TrendStructure:
    direction: TrendDir
    last_high: float | None
    prev_high: float | None
    last_low: float | None
    prev_low: float | None
    hh: bool
    hl: bool
    lh: bool
    ll: bool


# ==================== ВСПОМОГАТЕЛЬНЫЕ ====================

def _parse_candles(klines: list[list]) -> list[dict[str, float]]:
    """Bybit klines → список dict. Порядок сохраняется (новые первые)."""
    return [
        {
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        }
        for k in klines
    ]


def _find_extreme_pivots(
    candles: list[dict[str, float]],
    left: int = 3,
    right: int = 3,
) -> list[tuple[int, float, Literal["high", "low"]]]:
    """
    Локальные экстремумы по high/low свечи.

    Возвращает список (index, price, kind) в Bybit-порядке
    (сначала свежие).

    high-пивот: candle.high больше, чем у `left` соседей слева и
    `right` соседей справа. Симметрично для low.
    """
    if left < 1 or right < 1:
        raise ValueError("left and right must be >= 1")
    n = len(candles)
    if n < left + right + 1:
        return []

    pivots: list[tuple[int, float, Literal["high", "low"]]] = []
    for i in range(right, n - left):
        c = candles[i]
        is_high = True
        is_low = True

        # Свежее (меньший index)
        for j in range(i - right, i):
            if candles[j]["high"] >= c["high"]:
                is_high = False
            if candles[j]["low"] <= c["low"]:
                is_low = False
            if not is_high and not is_low:
                break

        # Старше (больший index)
        if is_high or is_low:
            for j in range(i + 1, i + left + 1):
                if candles[j]["high"] >= c["high"]:
                    is_high = False
                if candles[j]["low"] <= c["low"]:
                    is_low = False
                if not is_high and not is_low:
                    break

        if is_high:
            pivots.append((i, c["high"], "high"))
        elif is_low:
            pivots.append((i, c["low"], "low"))

    return pivots


def _cluster_pivots(
    pivots: list[tuple[int, float, Literal["high", "low"]]],
    eps_pct: float,
) -> list[dict]:
    """
    Группирует близкие пивоты одного типа в кластеры (уровни).

    Простой однопроходный алгоритм:
      - идём по пивотам;
      - если пивот близок к уже существующему центру (в пределах eps_pct
        от центра) и того же типа — добавляем;
      - иначе — новый кластер.

    Возвращает список dict:
      {center, kind, indices: [...], prices: [...]}.
    """
    clusters: list[dict] = []
    for idx, price, kind in pivots:
        placed = False
        for cl in clusters:
            if cl["kind"] != kind:
                continue
            if cl["center"] == 0:
                continue
            rel = abs(price - cl["center"]) / cl["center"]
            if rel <= eps_pct:
                cl["indices"].append(idx)
                cl["prices"].append(price)
                # Обновляем центр как среднее всех цен кластера
                cl["center"] = sum(cl["prices"]) / len(cl["prices"])
                placed = True
                break
        if not placed:
            clusters.append({
                "center": price,
                "kind": kind,
                "indices": [idx],
                "prices": [price],
            })
    return clusters


def _count_touches(
    candles: list[dict[str, float]],
    zone_low: float,
    zone_high: float,
    max_candles: int,
) -> int:
    """
    Считает касания зоны: сколько свечей задели [zone_low, zone_high]
    (high >= zone_low и low <= zone_high).
    """
    n = min(len(candles), max_candles)
    touches = 0
    for i in range(n):
        c = candles[i]
        if c["high"] >= zone_low and c["low"] <= zone_high:
            touches += 1
    return touches


def is_mirror_level(
    candles: list[dict[str, float]],
    level_price: float,
    *,
    tolerance_pct: float = 0.0015,
    mirror_min_side_touches: int = 1,
) -> bool:
    """
    Зеркальный ли уровень.

    Окно делится ровно пополам по порядку Bybit (свежие в начале):
      fresh_half  = candles[:n//2]
      old_half    = candles[n//2:]

    Для каждой половины считаем:
      - подходы СВЕРХУ: бары, где зона задевается, а close > zone_high
        (цена была выше, спустилась — тестирует как поддержку);
      - подходы СНИЗУ: бары, где зона задевается, а close < zone_low
        (цена была ниже, поднялась — тестирует как сопротивление).

    Уровень зеркальный, если:
      - в свежей половине доминирует один тип подходов,
      - в старой половине — другой тип,
      - в каждой половине минимум `mirror_min_side_touches` касаний
        в доминирующей стороне.
    """
    n = len(candles)
    if n < 4:
        return False

    tol = level_price * tolerance_pct
    zone_low = level_price - tol
    zone_high = level_price + tol

    def count_sides(slc: list[dict[str, float]]) -> tuple[int, int]:
        """(подходы_сверху, подходы_снизу) для среза свечей."""
        from_above = 0
        from_below = 0
        for c in slc:
            # Свеча «касалась» зоны
            if c["high"] < zone_low or c["low"] > zone_high:
                continue
            if c["close"] > zone_high:
                from_above += 1
            elif c["close"] < zone_low:
                from_below += 1
        return from_above, from_below

    mid = n // 2
    fresh = candles[:mid]     # свежие
    old = candles[mid:]       # старые

    fa, fb = count_sides(fresh)
    oa, ob = count_sides(old)

    # Зеркальность: в одной половине преобладает один тип, в другой — другой.
    fresh_dominant = (
        "above" if fa > fb else "below" if fb > fa else "none"
    )
    old_dominant = (
        "above" if oa > ob else "below" if ob > oa else "none"
    )

    if fresh_dominant == "none" or old_dominant == "none":
        return False
    if fresh_dominant == old_dominant:
        return False

    # Требуем минимум `mirror_min_side_touches` касаний в доминирующей
    # стороне каждой половины. Идея: раньше цена тестировала уровень
    # с одной стороны (например, как сопротивление), теперь — с другой.
    fresh_touches = max(fa, fb)
    old_touches = max(oa, ob)
    if fresh_touches < mirror_min_side_touches:
        return False
    if old_touches < mirror_min_side_touches:
        return False

    return True


# ==================== ПУБЛИЧНЫЙ API ====================

def find_levels(
    klines: list[list],
    *,
    source_tf: str,
    tolerance_pct: float = 0.0015,
    min_touches: int = 2,
    worn_touches: int = 5,
    lookback: int = 400,
    cluster_eps_pct: float = 0.003,
    pivots_left: int = 3,
    pivots_right: int = 3,
) -> list[Level]:
    """
    Ищет уровни на старшем ТФ.

    Пайплайн:
      1. Обрезаем до `lookback` свечей (свежие первые).
      2. Находим все high/low-пивоты (`_find_extreme_pivots`).
      3. Кластеризуем близкие пивоты одного типа в уровни.
      4. Для каждого кластера считаем касания (по зоне ± tolerance).
      5. Помечаем зеркальность и износ.
      6. Возвращаем список, отсортированный по strength DESC.

    Returns:
        [] если данных мало или уровней не нашли.
        Не бросает исключений на битых данных — только на неверных
        параметрах (left/right < 1 → ValueError).
    """
    if pivots_left < 1 or pivots_right < 1:
        raise ValueError("pivots_left/right must be >= 1")

    candles_all = _parse_candles(klines)
    if not candles_all:
        return []

    candles = candles_all[:lookback]
    if len(candles) < pivots_left + pivots_right + 1:
        return []

    # 1. Пивоты
    pivots = _find_extreme_pivots(candles, pivots_left, pivots_right)
    if not pivots:
        return []

    # 2. Кластеры
    clusters = _cluster_pivots(pivots, cluster_eps_pct)
    if not clusters:
        return []

    # 3. Оценка каждого кластера
    levels: list[Level] = []
    for cl in clusters:
        center = float(cl["center"])
        tol = center * tolerance_pct
        zone_low = center - tol
        zone_high = center + tol

        touches = _count_touches(candles, zone_low, zone_high, lookback)
        if touches < min_touches:
            continue

        is_mirror = is_mirror_level(
            candles, center, tolerance_pct=tolerance_pct,
        )
        is_worn = touches >= worn_touches

        # Простой score: касания + бонус за зеркальность и старший ТФ.
        strength = touches
        if is_mirror:
            strength += 2
        if source_tf == "D":
            strength += 1
        if is_worn:
            strength -= 1

        levels.append(Level(
            price=center,
            price_low=zone_low,
            price_high=zone_high,
            touches=touches,
            is_mirror=is_mirror,
            is_worn=is_worn,
            source_tf=source_tf,
            strength=max(0, strength),
        ))

    # 4. Сортировка: strength DESC, потом touches DESC, потом цена ASC
    levels.sort(key=lambda lv: (-lv.strength, -lv.touches, lv.price))
    return levels


def is_level_valid(
    level: Level,
    *,
    min_touches: int = 2,
    allow_worn: bool = False,
) -> bool:
    """Сильный ли уровень по стратегии."""
    if level.touches < min_touches:
        return False
    if level.is_worn and not allow_worn:
        return False
    return True


def find_level_above(levels: list[Level], price: float) -> Level | None:
    """
    Ближайший уровень строго выше цены (используется для TP на лонге
    или SL на шорте).
    """
    candidates = [lv for lv in levels if lv.price_low > price]
    if not candidates:
        return None
    return min(candidates, key=lambda lv: lv.price_low - price)


def find_level_below(levels: list[Level], price: float) -> Level | None:
    """
    Ближайший уровень строго ниже цены (используется для TP на шорте
    или SL на лонге).
    """
    candidates = [lv for lv in levels if lv.price_high < price]
    if not candidates:
        return None
    return min(candidates, key=lambda lv: price - lv.price_high)


# ==================== СТРУКТУРА ТРЕНДА ====================

def find_hh_hl(
    klines: list[list],
    *,
    lookback: int = 50,
    pivots_left: int = 3,
    pivots_right: int = 3,
) -> TrendStructure:
    """
    Определяет структуру тренда по последним N свечам.

    Логика:
      - найти high/low-пивоты в окне;
      - взять два последних high-пивота → сравнить (HH / LH);
      - взять два последних low-пивота  → сравнить (HL / LL);
      - direction = 'up'   если HH и HL;
      - direction = 'down' если LH и LL;
      - иначе 'side'.

    Если пивотов меньше двух с какой-то стороны — соответствующие
    флаги False, direction='side'.
    """
    candles_all = _parse_candles(klines)
    if not candles_all:
        return TrendStructure(
            direction="side",
            last_high=None, prev_high=None,
            last_low=None, prev_low=None,
            hh=False, hl=False, lh=False, ll=False,
        )

    candles = candles_all[:lookback]
    if len(candles) < pivots_left + pivots_right + 1:
        return TrendStructure(
            direction="side",
            last_high=None, prev_high=None,
            last_low=None, prev_low=None,
            hh=False, hl=False, lh=False, ll=False,
        )

    pivots = _find_extreme_pivots(candles, pivots_left, pivots_right)

    # pivots в Bybit-порядке (свежие первыми)
    highs = [p for p in pivots if p[2] == "high"]   # [(index, price, kind)]
    lows = [p for p in pivots if p[2] == "low"]

    last_high = highs[0][1] if len(highs) >= 1 else None
    prev_high = highs[1][1] if len(highs) >= 2 else None
    last_low = lows[0][1] if len(lows) >= 1 else None
    prev_low = lows[1][1] if len(lows) >= 2 else None

    hh = (last_high is not None and prev_high is not None
          and last_high > prev_high)
    lh = (last_high is not None and prev_high is not None
          and last_high < prev_high)
    hl = (last_low is not None and prev_low is not None
          and last_low > prev_low)
    ll = (last_low is not None and prev_low is not None
          and last_low < prev_low)

    if hh and hl:
        direction: TrendDir = "up"
    elif lh and ll:
        direction = "down"
    else:
        direction = "side"

    return TrendStructure(
        direction=direction,
        last_high=last_high, prev_high=prev_high,
        last_low=last_low, prev_low=prev_low,
        hh=hh, hl=hl, lh=lh, ll=ll,
    )