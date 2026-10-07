"""
Сканер уровней (level_scanner).

Раз в сутки (или 2 раза) анализирует отслеживаемые активы по уже имеющимся
klines и формирует список «сильных» уровней-кандидатов. Пользователь вручную
выбирает, какие добавить в alerts.json. Сканер НЕ ставит алерты сам и НЕ
влияет на score текущих price-alert'ов.

Пайплайн на символ:
  1. Тянем 15m / 1H / 4H / 1D klines.
  2. Ищем свинги (пивоты) на 15m и 4H отдельно (levels._find_extreme_pivots).
  3. Кластеризуем свинги в уровни (levels._cluster_pivots).
  4. Для каждого 15m-кластера считаем касания (_count_touches с tol из ТЗ).
  5. Проверяем видимость на 4H / 1D через центры 4H/1D-кластеров.
  6. Зеркальность — levels.is_mirror_level на 4H (fallback: 15m).
  7. Скоринг по таблице ТЗ 4.5.
  8. Hard-фильтры (износ / дистанция / room_to_next).
  9. Топ-3 support + топ-3 resistance.

Room-to-next:
  Если ATR_1h известен — фильтр в единицах ATR (MIN_ROOM_ATR = 2.0).
  Если ATR_1h нет — fallback в % от цены (MIN_ROOM_PCT = 1.2).
  Если следующего уровня нет вообще — soft-pass (room_ok=True).

Ресурсы: 1 CPU / 2 GB (плюс Amnezia). Модуль CPU-friendly: _parse_candles
вызывается один раз на символ, ATR считается один раз, никаких O(N²).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC
from typing import Any, Literal

from src.api.bybit_client import BybitClient
from src.core.levels import (
    _cluster_pivots,
    _count_touches,
    _find_extreme_pivots,
    _parse_candles,
    is_mirror_level,
)
from src.utils.indicators import calculate_atr

logger = logging.getLogger(__name__)


# ==================== КОНСТАНТЫ АЛГОРИТМА ====================
# Все — module-level defaults, можно переопределить аргументами scan_symbol/scan_all.

SWING_WINDOW: int = 2

# Кластеризация: 15m — базовые пороги.
CLUSTER_PCT: float = 0.0015          # 0.15% для BTC/ETH/SOL
CLUSTER_PCT_ALT: float = 0.0025      # 0.25% для альтов

# Множители для старших ТФ: 4H-свечи крупнее, цена за свечу проходит больше,
# поэтому кластеризация должна быть шире, иначе получим 70+ микро-кластеров.
CLUSTER_MULT_4H: float = 5.0         # 4H: 0.15% × 5 = 0.75% (BTC/ETH/SOL)
CLUSTER_MULT_1D: float = 10.0        # 1D: 0.15% × 10 = 1.5%  (BTC/ETH/SOL)

TOUCHES_LOOKBACK_15M: int = 48 * 4   # 48ч на 15m = 192 свечи
TOUCHES_LOOKBACK_4H: int = 30 * 6    # 30 дней на 4H = 180 свечей

MAX_TOUCHES: int = 12                # > 12 касаний → износ, отсев
MAX_DISTANCE_PCT: float = 3.5        # мажоры: > 3.5% от цены → отсев
MAX_DISTANCE_PCT_ALT: float = 4.5    # альты: > 4.5% от цены → отсев
MIN_DISTANCE_PCT: float = 0.05       # базовый пол, % от цены
MIN_DISTANCE_ATR_FACTOR: float = 0.25  # формула: max(0.05%, 0.25 * ATR_15m / price * 100)
MIN_ROOM_ATR: float = 2.0            # запас хода < 2 ATR → отсев (если ATR есть)
MIN_ROOM_PCT: float = 1.2            # fallback-порог, % от цены (если ATR нет)
MIN_TOUCHES_HARD: int = 2            # < 2 касаний и не видно на 4H/1D → отсев

MIN_SCORE: float = 5.0               # порог попадания в сводку
TOP_N: int = 3                       # топ-3 support + топ-3 resistance

# Символы, для которых CLUSTER_PCT = 0.15% (major). Для остальных — 0.25%.
_MAJOR_SYMBOLS: frozenset[str] = frozenset({
    "BTCUSDT", "ETHUSDT", "SOLUSDT",
})


# ==================== DTO ====================

@dataclass
class LevelCandidate:
    """Уровень-кандидат, найденный сканером."""
    symbol: str
    price: float
    side: Literal["support", "resistance"]
    direction_for_alert: Literal["up", "down"]
    score: float
    touches_15m: int
    touches_4h: int
    is_mirror: bool
    visible_on_4h: bool
    visible_on_1d: bool
    distance_pct: float
    atr_1h: float | None                # None, если нет данных 1H
    room_to_next_atr: float | None      # None, если ATR_1h нет
    room_to_next_pct: float             # всегда считаем
    room_ok: bool                       # итог фильтра room
    tf_sources: list[str] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "price": self.price,
            "side": self.side,
            "direction_for_alert": self.direction_for_alert,
            "score": self.score,
            "touches_15m": self.touches_15m,
            "touches_4h": self.touches_4h,
            "is_mirror": self.is_mirror,
            "visible_on_4h": self.visible_on_4h,
            "visible_on_1d": self.visible_on_1d,
            "distance_pct": self.distance_pct,
            "atr_1h": self.atr_1h,
            "room_to_next_atr": self.room_to_next_atr,
            "room_to_next_pct": self.room_to_next_pct,
            "room_ok": self.room_ok,
            "tf_sources": list(self.tf_sources),
            "note": self.note,
        }


# ==================== ВСПОМОГАТЕЛЬНЫЕ ====================

def _cluster_pct_for(symbol: str, base_pct: float | None = None) -> float:
    """
    Порог кластеризации для символа.
    0.15% — для мажоров, 0.25% — для остальных.
    base_pct — если пользователь передал явно, используем его.
    """
    if base_pct is not None:
        return base_pct
    if symbol.upper() in _MAJOR_SYMBOLS:
        return CLUSTER_PCT
    return CLUSTER_PCT_ALT

def _cluster_pct_for_tf(symbol: str, tf: str) -> float:
    """
    Порог кластеризации для конкретного ТФ.
    15m — базовый; 4H — ×3; 1D — ×6.
    tf: "15" | "60" | "240" | "D"
    """
    base = _cluster_pct_for(symbol)
    if tf == "240":
        return base * CLUSTER_MULT_4H
    if tf == "D":
        return base * CLUSTER_MULT_1D
    return base


def _round_price_for_callback(price: float) -> str:
    """
    Округление цены до строки для callback_data.

    5 знаков после запятой, но без лишних нулей; для больших чисел (>1000)
    оставляем 2 знака. Это компромисс между точностью уровня и 64-байтным
    лимитом callback_data.
    """
    if price <= 0:
        return "0"
    if price >= 1000:
        s = f"{price:.2f}"
    elif price >= 1:
        s = f"{price:.4f}"
    else:
        s = f"{price:.6f}"
    s = s.rstrip("0").rstrip(".")
    return s or "0"


def _tol_for(level_price: float, atr_15m: float | None) -> float:
    """
    TOLERANCE = max(0.1% * level, 0.15 * ATR_15m), как в ТЗ 4.1.
    Если ATR_15m неизвестен — только 0.1% * level.
    """
    pct_tol = 0.001 * level_price
    if atr_15m is None or atr_15m <= 0:
        return pct_tol
    return max(pct_tol, 0.15 * atr_15m)


def _atr_from_candles(candles: list[dict[str, float]]) -> float | None:
    """ATR(14) по свечам (в Bybit-порядке: свежие первые)."""
    if not candles or len(candles) < 15:
        return None
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    closes = [c["close"] for c in candles]
    return calculate_atr(highs, lows, closes, period=14)


# ==================== ОСНОВНЫЕ ПРИМИТИВЫ (обёртки над levels.py) ====================

def find_swings(
    klines: list[list],
    window: int = SWING_WINDOW,
) -> list[tuple[int, float, str]]:
    """
    Свинги на сырых klines. Обёртка над levels._find_extreme_pivots.
    """
    candles = _parse_candles(klines)
    if not candles:
        return []
    return _find_extreme_pivots(candles, left=window, right=window)


def cluster_levels(
    pivots: list[tuple[int, float, str]],
    cluster_pct: float,
) -> list[dict]:
    """
    Кластеризация пивотов. Обёртка над levels._cluster_pivots.
    Возвращает список dict: {center, kind, indices, prices}.
    """
    if not pivots:
        return []
    return _cluster_pivots(pivots, cluster_pct)


def count_touches(
    candles: list[dict[str, float]],
    level: float,
    tol: float,
    lookback: int,
) -> int:
    """
    Касания уровня за `lookback` свечей.
    Обёртка над levels._count_touches (принимает зону, не tolerance).
    """
    return _count_touches(
        candles,
        zone_low=level - tol,
        zone_high=level + tol,
        max_candles=lookback,
    )


def is_visible_on_tf(
    level: float,
    cluster_centers: list[float],
    cluster_pct: float,
) -> bool:
    """
    Уровень «виден» на ТФ, если его цена попадает в зону одного из
    кластеров ТФ (±cluster_pct). Сторона (support/resistance) не важна —
    зеркальность считается отдельно через is_mirror_level.
    """
    if not cluster_centers:
        return False
    for c in cluster_centers:
        if c <= 0:
            continue
        if abs(level - c) / c < cluster_pct:
            return True
    return False


# ==================== СКОРИНГ ====================

def score_candidate(
    *,
    visible_on_1d: bool,
    visible_on_4h: bool,
    is_mirror: bool,
    touches_15m: int,
    room_to_next_atr: float | None,
    distance_pct: float,
) -> float:
    """
    Скоринг уровня 0..11 по таблице ТЗ 4.5.

    Факторы и баллы:
      +3  виден на 1D
      +2  виден на 4H
      +2  зеркальный
      +2  касания 2–4
      +1  касания 5–7
       0  касания 8–12
      +1  запас хода ≥ 3 ATR (только если ATR известен)
      +1  расстояние 0.3–1.5% от цены
    """
    score = 0.0
    if visible_on_1d:
        score += 3.0
    if visible_on_4h:
        score += 2.0
    if is_mirror:
        score += 2.0

    if 2 <= touches_15m <= 4:
        score += 2.0
    elif 5 <= touches_15m <= 7:
        score += 1.0

    if room_to_next_atr is not None and room_to_next_atr >= 3.0:
        score += 1.0

    if 0.3 <= distance_pct <= 1.5:
        score += 1.0

    return score


# ==================== ROOM-TO-NEXT ====================

def _calc_room_to_next(
    *,
    level: float,
    all_centers: list[float],
    side: str,
    atr_1h: float | None,
) -> tuple[float | None, float, bool]:
    """
    Запас хода от `level` до ближайшего уровня с ПРОТИВОПОЛОЖНОЙ стороны.

    Для support (вход up) — следующий уровень ВЫШЕ.
    Для resistance (вход down) — следующий уровень НИЖЕ.

    Возвращает (room_atr, room_pct, room_ok):
      - ATR_1h > 0 → room_atr = distance / atr_1h; room_ok = room_atr >= MIN_ROOM_ATR
      - ATR_1h нет → room_atr = None; room_ok = room_pct >= MIN_ROOM_PCT
      - Следующего уровня нет → room_atr = None, room_pct = inf,
        room_ok = True (soft-pass)
    """
    if side == "support":
        candidates = [c for c in all_centers if c > level * 1.0001]
        nearest = min(candidates) if candidates else None
        distance = (nearest - level) if nearest is not None else None
    else:  # resistance
        candidates = [c for c in all_centers if c < level * 0.9999]
        nearest = max(candidates) if candidates else None
        distance = (level - nearest) if nearest is not None else None

    if distance is None or distance <= 0:
        return None, float("inf"), True

    room_pct = distance / level * 100.0 if level > 0 else 0.0

    if atr_1h is not None and atr_1h > 0:
        room_atr = distance / atr_1h
        room_ok = room_atr >= MIN_ROOM_ATR
        return room_atr, room_pct, room_ok

    room_ok = room_pct >= MIN_ROOM_PCT
    return None, room_pct, room_ok


# ==================== СКАН ОДНОГО СИМВОЛА ====================

def scan_symbol(
    symbol: str,
    klines_map: dict[str, list[list]],
    current_price: float,
    *,
    cluster_pct: float | None = None,
    max_touches: int = MAX_TOUCHES,
    max_distance_pct: float = MAX_DISTANCE_PCT,
    min_distance_pct: float = MIN_DISTANCE_PCT,
    min_room_atr: float = MIN_ROOM_ATR,
    min_room_pct: float = MIN_ROOM_PCT,
    min_score: float = MIN_SCORE,
    top_n: int = TOP_N,
    swing_window: int = SWING_WINDOW,
) -> list[LevelCandidate]:
    """
    Полный пайплайн по одному символу.

    klines_map: {"15": [...], "60": [...], "240": [...], "D": [...]}
    current_price: цена, относительно которой считаем distance_pct.

    Возвращает топ-N support + топ-N resistance.
    Пустой список — если данных мало, нет уровней или все отсеяны.
    """
    klines_15m = klines_map.get("15") or []
    klines_1h = klines_map.get("60") or []
    klines_4h = klines_map.get("240") or []
    klines_1d = klines_map.get("D") or []

    if not klines_15m or current_price <= 0:
        return []

    candles_15m = _parse_candles(klines_15m)
    candles_4h = _parse_candles(klines_4h) if klines_4h else []
    candles_1d = _parse_candles(klines_1d) if klines_1d else []
    candles_1h = _parse_candles(klines_1h) if klines_1h else []

    if not candles_15m:
        return []

    pct = _cluster_pct_for(symbol, cluster_pct)
    if max_distance_pct is None:
        max_distance_pct = (
            MAX_DISTANCE_PCT
            if symbol.upper() in _MAJOR_SYMBOLS
            else MAX_DISTANCE_PCT_ALT
        )

    atr_15m = _atr_from_candles(candles_15m)
    atr_1h = _atr_from_candles(candles_1h) if candles_1h else None

    pivots_15m = _find_extreme_pivots(candles_15m, left=swing_window, right=swing_window)
    clusters_15m = _cluster_pivots(pivots_15m, pct)
    if not clusters_15m:
        return []

    pct_4h = _cluster_pct_for_tf(symbol, "240")
    pct_1d = _cluster_pct_for_tf(symbol, "D")

    cluster_centers_4h: list[float] = []
    if candles_4h:
        piv4 = _find_extreme_pivots(candles_4h, left=swing_window, right=swing_window)
        cluster_centers_4h = [c["center"] for c in _cluster_pivots(piv4, pct_4h)]

    cluster_centers_1d: list[float] = []
    if candles_1d:
        piv1 = _find_extreme_pivots(candles_1d, left=swing_window, right=swing_window)
        cluster_centers_1d = [c["center"] for c in _cluster_pivots(piv1, pct_1d)]

    all_centers_15m = [float(c["center"]) for c in clusters_15m]

    candidates: list[LevelCandidate] = []
    for cl in clusters_15m:
        level = float(cl["center"])
        if level <= 0:
            continue

        tol = _tol_for(level, atr_15m)
        touches_15m = _count_touches(
            candles_15m,
            zone_low=level - tol,
            zone_high=level + tol,
            max_candles=TOUCHES_LOOKBACK_15M,
        )

        touches_4h = 0
        if candles_4h:
            tol_4h = _tol_for(level, None)
            touches_4h = _count_touches(
                candles_4h,
                zone_low=level - tol_4h,
                zone_high=level + tol_4h,
                max_candles=TOUCHES_LOOKBACK_4H,
            )

        visible_on_4h = is_visible_on_tf(level, cluster_centers_4h, pct_4h)
        visible_on_1d = is_visible_on_tf(level, cluster_centers_1d, pct_1d)

        if touches_15m < MIN_TOUCHES_HARD and not (visible_on_4h or visible_on_1d):
            continue

        if touches_15m > max_touches:
            continue

        if level < current_price:
            side: Literal["support", "resistance"] = "support"
        elif level > current_price:
            side = "resistance"
        else:
            continue

        direction_for_alert: Literal["up", "down"] = (
            "up" if side == "support" else "down"
        )

        distance_pct = abs(current_price - level) / current_price * 100.0
        if distance_pct > max_distance_pct:
            continue
        # MIN_DISTANCE: формула max(fixed, factor * ATR_15m / price * 100)
        # чтобы порог адаптировался к волатильности символа.
        min_dist_eff = min_distance_pct
        if atr_15m is not None and atr_15m > 0 and current_price > 0:
            atr_based = MIN_DISTANCE_ATR_FACTOR * atr_15m / current_price * 100.0
            min_dist_eff = max(min_distance_pct, atr_based)
        if distance_pct < min_dist_eff:
            continue

        # Зеркальность: 1D → 4H → 15m (больше истории — больше шансов
        # на переворот доминанты подходов).
        if candles_1d:
            mirror_candles = candles_1d
        elif candles_4h:
            mirror_candles = candles_4h
        else:
            mirror_candles = candles_15m
        is_mirror = is_mirror_level(mirror_candles, level)

        # Room-to-next считаем по 4H-кластерам (настоящие препятствия),
        # fallback на 15m — только если 4H-кластеров совсем мало.
        room_centers = (
            cluster_centers_4h
            if len(cluster_centers_4h) >= 3
            else all_centers_15m
        )
        room_atr, room_pct, room_ok = _calc_room_to_next(
            level=level,
            all_centers=room_centers,
            side=side,
            atr_1h=atr_1h,
        )
        # room_ok рассчитан в _calc_room_to_next по актуальным порогам.
        # Если пользователь передал min_room_atr / min_room_pct явно —
        # делаем повторную проверку с его порогами.
        if atr_1h is not None and atr_1h > 0:
            if room_atr is not None and room_atr < min_room_atr:
                continue
        else:
            if room_pct != float("inf") and room_pct < min_room_pct:
                continue


        score = score_candidate(
            visible_on_1d=visible_on_1d,
            visible_on_4h=visible_on_4h,
            is_mirror=is_mirror,
            touches_15m=touches_15m,
            room_to_next_atr=room_atr,
            distance_pct=distance_pct,
        )
        if score < min_score:
            continue

        tf_sources: list[str] = ["15m"]
        if visible_on_4h:
            tf_sources.append("4H")
        if visible_on_1d:
            tf_sources.append("1D")

        note = _build_note(
            side=side,
            score=score,
            touches_15m=touches_15m,
            is_mirror=is_mirror,
            visible_on_4h=visible_on_4h,
            visible_on_1d=visible_on_1d,
        )

        candidates.append(LevelCandidate(
            symbol=symbol,
            price=level,
            side=side,
            direction_for_alert=direction_for_alert,
            score=round(score, 2),
            touches_15m=touches_15m,
            touches_4h=touches_4h,
            is_mirror=is_mirror,
            visible_on_4h=visible_on_4h,
            visible_on_1d=visible_on_1d,
            distance_pct=round(distance_pct, 3),
            atr_1h=round(atr_1h, 6) if atr_1h else None,
            room_to_next_atr=round(room_atr, 2) if room_atr is not None else None,
            room_to_next_pct=round(room_pct, 3) if room_pct != float("inf") else 0.0,
            room_ok=room_ok,
            tf_sources=tf_sources,
            note=note,
        ))

    if not candidates:
        return []

    supports = [c for c in candidates if c.side == "support"]
    resistances = [c for c in candidates if c.side == "resistance"]
    supports.sort(key=lambda c: (-c.score, -c.touches_15m, abs(c.price - current_price)))
    resistances.sort(key=lambda c: (-c.score, -c.touches_15m, abs(c.price - current_price)))
    return supports[:top_n] + resistances[:top_n]


def _build_note(
    *,
    side: str,
    score: float,
    touches_15m: int,
    is_mirror: bool,
    visible_on_4h: bool,
    visible_on_1d: bool,
) -> str:
    """Короткий setup_note для alerts.json."""
    parts = [f"сканер: {side}, score {score:.1f}, {touches_15m} кас."]
    if is_mirror:
        parts.append("зеркало")
    if visible_on_1d:
        parts.append("1D")
    elif visible_on_4h:
        parts.append("4H")
    return " | ".join(parts)


# ==================== СКАН ВСЕХ СИМВОЛОВ ====================

def _fetch_klines_for_symbol(
    client: BybitClient,
    symbol: str,
    category: str = "linear",
) -> dict[str, list[list]]:
    """
    Тянет 4 ТФ по символу. Ошибки отдельного ТФ не валят весь символ.
    Лимиты — из Config.EXPORT_LIMITS (15:300, 60:300, 240:400, D:200).
    """
    from src.utils.config import Config
    limits = Config.EXPORT_LIMITS
    result: dict[str, list[list]] = {}
    for tf in ("15", "60", "240", "D"):
        try:
            data = client.get_klines(symbol, category, tf, limits.get(tf, 300))
            if data:
                result[tf] = data
        except Exception as e:
            logger.warning(f"⚠️ {symbol} {tf}: ошибка get_klines: {e}")
    return result


def scan_all(
    symbols: list[str],
    client: BybitClient,
    *,
    category_provider: Callable[[str], str] | None = None,
    min_score: float = MIN_SCORE,
    top_n: int = TOP_N,
) -> dict[str, list[LevelCandidate]]:
    """
    Сканирует список символов. Возвращает {symbol: [LevelCandidate, ...]}.

    category_provider: (symbol) -> "linear"|"spot". Если None — "linear".
    Символы без данных или без кандидатов в результат НЕ попадают.
    """
    out: dict[str, list[LevelCandidate]] = {}
    total = len(symbols)
    for i, sym in enumerate(symbols, 1):
        t0 = time.monotonic()
        cat = category_provider(sym) if category_provider else "linear"
        try:
            klines_map = _fetch_klines_for_symbol(client, sym, cat)
            ticker = client.get_ticker(sym, cat)
            if not ticker:
                logger.warning(f"⚠️ {sym}: нет тикера")
                continue
            candidates = scan_symbol(
                symbol=sym,
                klines_map=klines_map,
                current_price=ticker.price,
                min_score=min_score,
                top_n=top_n,
            )
            if candidates:
                out[sym] = candidates
            logger.info(
                f"🔍 [{i}/{total}] {sym}: {len(candidates)} кандидатов "
                f"({time.monotonic() - t0:.2f}с)"
            )
        except Exception as e:
            logger.error(f"❌ {sym}: ошибка скана: {e}", exc_info=True)
    return out


# ==================== ФОРМАТИРОВАНИЕ ====================

def _fmt_price(price: float) -> str:
    """Формат цены для сообщения (адаптивно к величине)."""
    if price >= 1000:
        return f"{price:,.2f}"
    if price >= 1:
        return f"{price:,.4f}".rstrip("0").rstrip(".")
    return f"{price:,.6f}".rstrip("0").rstrip(".")


def _fmt_room(c: LevelCandidate) -> str:
    """Формат room: '2.4×ATR' если ATR есть, иначе '1.5%'."""
    if c.room_to_next_atr is not None:
        return f"{c.room_to_next_atr:.1f}×ATR"
    if c.room_to_next_pct > 0:
        return f"{c.room_to_next_pct:.1f}%"
    return "—"


def format_summary(
    results: dict[str, list[LevelCandidate]],
    prices: dict[str, float],
) -> str:
    """
    Сводка для Telegram в формате ТЗ 3.2.
    """
    from datetime import datetime

    if not results:
        return (
            "📊 <b>СВОДКА УРОВНЕЙ</b>\n\n"
            "Уровней, прошедших фильтры, не найдено."
        )

    now = datetime.now(UTC).strftime("%d.%m %H:%M UTC")
    lines: list[str] = [f"📊 <b>СВОДКА УРОВНЕЙ · {now}</b>", ""]

    for sym, cands in results.items():
        price = prices.get(sym)
        price_str = f"~{_fmt_price(price)}" if price else ""
        lines.append(f"<b>{sym}</b>  {price_str}")
        for c in cands:
            emoji = "🟢" if c.side == "support" else "🔴"
            side_label = "support" if c.side == "support" else "resist "
            tags: list[str] = []
            if c.is_mirror:
                tags.append("зеркало")
            if c.visible_on_1d:
                tags.append("1D")
            elif c.visible_on_4h:
                tags.append("4H")
            tags.append(f"{c.distance_pct:.2f}%")
            tags.append(_fmt_room(c))
            tags_str = " · ".join(tags)
            lines.append(
                f"  {emoji} {_fmt_price(c.price)} {side_label} · "
                f"score {c.score:.1f} · {c.touches_15m} кас. · {tags_str}"
            )
        lines.append("")

    return "\n".join(lines).rstrip()


def format_full_list(
    results: dict[str, list[LevelCandidate]],
    prices: dict[str, float],
) -> str:
    """Развёрнутый список — то же, что format_summary, но с note'ами."""
    from datetime import datetime

    if not results:
        return "📋 <b>Все уровни</b>\n\nПусто."

    now = datetime.now(UTC).strftime("%d.%m %H:%M UTC")
    lines: list[str] = [f"📋 <b>ВСЕ УРОВНИ · {now}</b>", ""]
    for sym, cands in results.items():
        price = prices.get(sym)
        price_str = f"~{_fmt_price(price)}" if price else ""
        lines.append(f"<b>{sym}</b>  {price_str}")
        for c in cands:
            emoji = "🟢" if c.side == "support" else "🔴"
            lines.append(
                f"  {emoji} {_fmt_price(c.price)} "
                f"({c.direction_for_alert}, score {c.score:.1f}, "
                f"{_fmt_room(c)})"
            )
            if c.note:
                lines.append(f"     <i>{c.note}</i>")
        lines.append("")
    return "\n".join(lines).rstrip()


# ==================== КЛАВИАТУРА ====================

def build_keyboard(results: dict[str, list[LevelCandidate]]) -> Any:
    """
    Компактная клавиатура:
      - по одной кнопке на символ «➕ SYMBOL · N уровней» → добавить все
        уровни этого символа,
      - «➕ ➕ Добавить все уровни» → добавить все уровни всех символов,
      - «📋 Все уровни текстом» → переключение на полный список с
        индивидуальными кнопками,
      - «🔄 Обновить» / «🏠 Главное меню».

    Отдельные кнопки на каждый уровень — в build_full_keyboard
    (показывается по кнопке «📋 Все уровни текстом»).
    """
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    rows: list[list[InlineKeyboardButton]] = []

    # Сортировка символов для предсказуемого порядка
    for sym in sorted(results.keys()):
        cands = results.get(sym) or []
        if not cands:
            continue
        n = len(cands)
        word = "уровень" if n == 1 else ("уровня" if 2 <= n <= 4 else "уровней")
        label = f"➕ {sym} · {n} {word}"
        rows.append([
            InlineKeyboardButton(label, callback_data=f"ls_add_sym|{sym}"),
        ])

    if rows:
        rows.append([
            InlineKeyboardButton(
                "➕ ➕ Добавить все уровни",
                callback_data="ls_add_all",
            ),
        ])
        rows.append([
            InlineKeyboardButton(
                "📋 Все уровни текстом",
                callback_data="ls_show_all",
            ),
        ])

    rows.append([InlineKeyboardButton("🔄 Обновить", callback_data="ls_scan_now")])
    rows.append([InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")])
    return InlineKeyboardMarkup(rows)


def build_full_keyboard(results: dict[str, list[LevelCandidate]]) -> Any:
    """
    Полная клавиатура: ВСЕ найденные уровни, по одному в ряд.
    Callback — тот же формат ls_add|SYM|price|dir.
    """
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    rows: list[list[InlineKeyboardButton]] = []
    for sym, cands in results.items():
        for c in cands:
            cb = _make_add_callback(c)
            emoji = "🟢" if c.side == "support" else "🔴"
            label = (
                f"{emoji} {sym} {_round_price_for_callback(c.price)} "
                f"{c.direction_for_alert} · {c.score:.1f}"
            )
            rows.append([InlineKeyboardButton(label, callback_data=cb)])

    rows.append([InlineKeyboardButton("◀ Компактно", callback_data="ls_show_compact")])
    rows.append([InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")])
    return InlineKeyboardMarkup(rows)


def _make_add_callback(c: LevelCandidate) -> str:
    """
    callback_data для добавления уровня.
    Формат: ls_add|SYMBOL|price|direction
    """
    return f"ls_add|{c.symbol}|{_round_price_for_callback(c.price)}|{c.direction_for_alert}"


# ==================== СЕРВИСНАЯ ОБЁРТКА ====================

def scan_and_format(
    symbols: list[str],
    client: BybitClient,
    *,
    category_provider: Callable[[str], str] | None = None,
    min_score: float = MIN_SCORE,
    top_n: int = TOP_N,
) -> tuple[str, str, dict[str, list[LevelCandidate]], dict[str, float], Any, Any]:
    """
    Удобная обёртка «всё в одном»: скан → тексты → клавиатуры.

    Returns:
        (summary_text, full_text, results, prices, compact_kb, full_kb)
    """
    results = scan_all(
        symbols, client,
        category_provider=category_provider,
        min_score=min_score,
        top_n=top_n,
    )
    prices: dict[str, float] = {}
    for sym in results.keys():
        cat = category_provider(sym) if category_provider else "linear"
        ticker = client.get_ticker(sym, cat)
        if ticker:
            prices[sym] = ticker.price

    summary = format_summary(results, prices)
    full = format_full_list(results, prices)
    compact_kb = build_keyboard(results)
    full_kb = build_full_keyboard(results)
    return summary, full, results, prices, compact_kb, full_kb