"""
Модуль технического анализа с весами, штрафами и фильтрами контекста.

Версия 4.0 (Этап 4):
- Единый источник RSI/EMA — src/utils/indicators.py
- Структура тренда через find_hh_hl (HH/HL), а не только EMA50
- Уровни через find_levels (4H / 1D)
- Risk-модуль: build_setup → RiskResult в результате оценки
- Обратная совместимость: без risk_cfg / klines_1h / klines_1d работает
  как в 3.x
"""

import logging
import time
from typing import Any

from src.core.divergence import find_divergences
from src.core.levels import (
    Level,
    TrendStructure,
    find_hh_hl,
    find_level_above,
    find_level_below,
    find_levels,
)
from src.core.risk import (
    RiskConfig,
    RiskResult,
    build_setup,
    format_reason_ru,
)
from src.utils.indicators import (
    calculate_atr,
    calculate_ema_from_bybit,
    calculate_rsi,
    calculate_rsi_series,
)

logger = logging.getLogger(__name__)


# ==================== КОНФИГУРАЦИЯ ====================

WEIGHTS = {
    "pattern": 2.0,
    "volume": 2.0,
    "wick": 1.5,
    "close_third": 1.0,
    "rsi": 1.0,
}

PENALTIES = {
    "low_volume": -2.0,
    "rsi_against": -1.0,
    "worn_level": -1.5,
    "htf_against": -1.5,
    "old_alert": -1.0,
    "hard_filter": -2.0,
}

THRESHOLDS = {
    "strong_min": 5.5,
    "weak_min": 3.5,
    "volume_good": 1.8,
    "volume_low": 1.0,
    "alert_max_age_hours": 36,
    "worn_touches": 5,
    "touch_tolerance_pct": 0.0015,
    "touches_lookback_hours": 48,
    # Этап 4:
    "divergence_min_rsi_gap": 5.0,
    "divergence_min_price_gap_pct": 0.001,
    "divergence_max_lookback": 60,
    "levels_tolerance_pct": 0.0015,
    "levels_min_touches": 2,
    "levels_cluster_eps_pct": 0.003,
    "st_stop_lookback": 20,        # свечей 15m для поиска structure_stop
    "atr_period": 14,
    "atr_runway_mult": 2.0,        # запас хода ≥ N×ATR(1h)
}

# Периоды индикаторов
RSI_PERIOD = 14
HTF_EMA_PERIOD = 50
HTF_EMA_SLOPE_LOOKBACK = 10
VOLUME_AVG_PERIODS = 20


# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================

def parse_klines(klines: list[list]) -> list[dict[str, float]]:
    """
    Парсит сырые klines в список словарей.
    Порядок сохраняется как есть: candles[0] — самая свежая свеча.
    """
    candles = []
    for k in klines:
        candles.append({
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        })
    return candles


def detect_pattern(
    candle: dict[str, float],
    prev_candle: dict[str, float],
    direction: str,
) -> str | None:
    """Определяет разворотный паттерн на свече."""
    o, h, low, c = candle["open"], candle["high"], candle["low"], candle["close"]
    po, pc = prev_candle["open"], prev_candle["close"]

    body = abs(c - o)
    if body == 0:
        return None

    range_hl = h - low
    if range_hl == 0:
        return None

    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - low

    # Бычий пин-бар (молот)
    if direction == "up":
        if lower_wick >= 2 * body and upper_wick <= 0.3 * body and (c - low) / range_hl >= 0.66:
            return "bullish_pinbar"

    # Медвежий пин-бар (падающая звезда)
    elif direction == "down":
        if upper_wick >= 2 * body and lower_wick <= 0.3 * body and (c - low) / range_hl <= 0.33:
            return "bearish_pinbar"

    # Бычье поглощение
    if direction == "up":
        if pc < po and c > o and o <= pc and c >= po:
            return "bullish_engulfing"

    # Медвежье поглощение
    elif direction == "down":
        if pc > po and c < o and o >= pc and c <= po:
            return "bearish_engulfing"

    return None


def has_wick_beyond_level(
    candle: dict[str, float], level: float, direction: str
) -> bool:
    """Проверяет, проколола ли тень уровень."""
    if direction == "up":
        return candle["low"] < level
    return candle["high"] > level


def close_in_correct_third(candle: dict[str, float], direction: str) -> bool:
    """Закрылась ли свеча в нужной трети диапазона."""
    range_hl = candle["high"] - candle["low"]
    if range_hl == 0:
        return False

    third = range_hl / 3
    if direction == "up":
        return candle["close"] >= candle["high"] - third
    return candle["close"] <= candle["low"] + third


def count_touches(
    candles: list[dict[str, float]],
    level: float,
    timeframe_minutes: int = 15,
    lookback_hours: int = 48,
) -> int:
    """
    Считает касания уровня за последние lookback_hours.

    ВАЖНО: вызывающий обязан передать достаточно свечей.
    Формула: нужно >= lookback_hours * (60 / timeframe_minutes) свечей.
    Для 15m и 48ч: 192 свечи.
    Если свечей меньше — используется всё, что есть (с предупреждением).
    """
    candles_needed = lookback_hours * (60 // timeframe_minutes)

    if len(candles) < candles_needed:
        logger.debug(
            f"count_touches: передано {len(candles)} свечей, "
            f"нужно {candles_needed} для {lookback_hours}ч. "
            f"Использую доступное."
        )
        candles_slice = candles
    else:
        candles_slice = candles[:candles_needed]

    tolerance = level * THRESHOLDS["touch_tolerance_pct"]
    touches = 0

    for candle in candles_slice:
        if candle["high"] >= (level - tolerance) and candle["low"] <= (level + tolerance):
            touches += 1

    return touches


def get_htf_trend(candles_4h: list[dict[str, float]]) -> str:
    """
    Определяет тренд на 4H по EMA50.

    ВАЖНО: candles_4h в порядке Bybit (новые первые).
    calculate_ema_from_bybit внутри разворачивает корректно.

    Возвращает: 'up' | 'down' | 'side'.
    """
    if len(candles_4h) < HTF_EMA_PERIOD + HTF_EMA_SLOPE_LOOKBACK:
        return "side"

    closes = [c["close"] for c in candles_4h]  # новые первые
    ema50 = calculate_ema_from_bybit(closes, HTF_EMA_PERIOD)  # тоже новые первые

    if len(ema50) < HTF_EMA_SLOPE_LOOKBACK:
        return "side"

    # ema50[0] — самое свежее значение
    ema_slope = ema50[0] - ema50[HTF_EMA_SLOPE_LOOKBACK - 1]
    current_price = closes[0]  # новейшая цена
    ema_value = ema50[0]

    price_above_ema = current_price > ema_value
    ema_rising = ema_slope > 0

    if price_above_ema and ema_rising:
        return "up"
    if not price_above_ema and not ema_rising:
        return "down"
    return "side"


def calc_volume_ratio(
    candles: list[dict[str, float]], periods: int = VOLUME_AVG_PERIODS
) -> float:
    """
    Отношение объёма ТЕКУЩЕЙ свечи [0] к среднему за предыдущие `periods`.

    candles[0] — текущая (незакрытая)
    candles[1:periods+1] — предыдущие закрытые
    """
    if len(candles) < periods + 1:
        return 1.0

    current_volume = candles[0]["volume"]
    avg_volume = sum(c["volume"] for c in candles[1:periods + 1]) / periods

    if avg_volume == 0:
        return 1.0

    return current_volume / avg_volume


# ==================== НОВЫЕ ХЕЛПЕРЫ ЭТАПА 4 ====================

def _calc_atr_15m(candles_15m: list[dict[str, float]]) -> float | None:
    """ATR(14) на 15m свечах. Порядок Bybit (новые первые)."""
    if len(candles_15m) < THRESHOLDS["atr_period"] + 1:
        return None
    highs = [c["high"] for c in candles_15m]
    lows = [c["low"] for c in candles_15m]
    closes = [c["close"] for c in candles_15m]
    return calculate_atr(highs, lows, closes, period=THRESHOLDS["atr_period"])


def _calc_atr_1h(candles_1h: list[dict[str, float]] | None) -> float | None:
    """ATR(14) на 1H свечах. None если данных нет."""
    if not candles_1h:
        return None
    if len(candles_1h) < THRESHOLDS["atr_period"] + 1:
        return None
    highs = [c["high"] for c in candles_1h]
    lows = [c["low"] for c in candles_1h]
    closes = [c["close"] for c in candles_1h]
    return calculate_atr(highs, lows, closes, period=THRESHOLDS["atr_period"])


def _structure_stop_15m(
    candles_15m: list[dict[str, float]],
    direction: str,
    lookback: int = 20,
) -> float | None:
    """
    Structure_stop для risk-модуля.
    'up'  → минимум low за последние `lookback` свечей (исключая текущую [0]);
    'down' → максимум high за тот же период.
    """
    if len(candles_15m) < 2:
        return None
    window = candles_15m[1:lookback + 1]
    if not window:
        return None
    if direction == "up":
        return min(c["low"] for c in window)
    if direction == "down":
        return max(c["high"] for c in window)
    return None


def _find_tp_levels(
    entry: float,
    direction: str,
    levels_4h: list[Level],
    atr_1h: float | None,
    min_rr: float,
    risk: float,
) -> tuple[float, float]:
    """
    Собирает TP1 и TP2.

    TP1 — ближайший уровень в сторону движения, строго выше/ниже entry.
    TP2 — следующий уровень за TP1, либо entry + min_rr × risk,
          если уровней мало.

    Гарантирует TP2 > TP1 для long и TP2 < TP1 для short.
    """
    fallback_tp2_long = entry + min_rr * risk
    fallback_tp2_short = entry - min_rr * risk

    if direction == "up":
        tp1_level = find_level_above(levels_4h, entry)
        tp1 = tp1_level.price if tp1_level else fallback_tp2_long
        # следующий уровень за TP1
        tp2_level = find_level_above(levels_4h, tp1 + 1e-9)
        tp2 = tp2_level.price if tp2_level else max(tp1 * 1.01, fallback_tp2_long)
        if tp2 <= tp1:
            tp2 = max(tp1 * 1.01, fallback_tp2_long)
        return tp1, tp2

    # direction == "down"
    tp1_level = find_level_below(levels_4h, entry)
    tp1 = tp1_level.price if tp1_level else fallback_tp2_short
    tp2_level = find_level_below(levels_4h, tp1 - 1e-9)
    tp2 = tp2_level.price if tp2_level else min(tp1 * 0.99, fallback_tp2_short)
    if tp2 >= tp1:
        tp2 = min(tp1 * 0.99, fallback_tp2_short)
    return tp1, tp2


# ==================== ГЛАВНАЯ ФУНКЦИЯ ОЦЕНКИ ====================

def evaluate_alert(
    symbol: str,
    level: float,
    direction: str,
    current_price: float,
    alert_created_at: float,
    klines_15m: list[list],
    klines_4h: list[list],
    klines_1h: list[list] | None = None,
    klines_1d: list[list] | None = None,
    *,
    risk_cfg: RiskConfig | None = None,
) -> dict[str, Any]:
    """
    Оценивает алерт. Версия 4.0 — с интегрированным risk-модулем.

    Args:
        klines_15m: рабочий ТФ (Bybit-порядок, новые первые).
        klines_4h: старший ТФ для тренда и уровней.
        klines_1h: опциональный 1H для ATR-1h.
        klines_1d: опциональный 1D для «бетонных» уровней.
        risk_cfg: если задан → собираем RiskResult (Entry/SL/TP1/TP2/RR/size).

    Returns:
        Прежний dict + новые ключи:
          - "structure": TrendStructure | None
          - "levels_4h": list[Level]
          - "levels_1d": list[Level]
          - "divergence": dict | None
          - "risk": RiskResult | None
          - "hard_filter": str | None      (reason если valid=False)
          - "hard_filter_ru": str | None   (перевод)
    """
    candles_15m = parse_klines(klines_15m)
    candles_4h = parse_klines(klines_4h)
    candles_1h = parse_klines(klines_1h) if klines_1h else None

    # значения по умолчанию для пустого результата
    empty = {
        "score": 0.0,
        "verdict": "❌ None",
        "signals": ["Недостаточно данных"],
        "filters": [],
        "vol_ratio": 0.0,
        "rsi": None,
        "touches": 0,
        "htf_trend": "side",
        "age_hours": 0,
        "pattern": None,
        "strength_score": 0,
        "details": ["Недостаточно данных"],
        "structure": None,
        "levels_4h": [],
        "levels_1d": [],
        "divergence": None,
        "risk": None,
        "hard_filter": None,
        "hard_filter_ru": None,
    }

    if len(candles_15m) < 3:
        return empty

    last_closed = candles_15m[1]
    prev_closed = candles_15m[2]

    score = 0.0
    signals: list[str] = []
    filters: list[str] = []

    # 1. Паттерн
    pattern = detect_pattern(last_closed, prev_closed, direction)
    pattern_names = {
        "bullish_pinbar": "Пин-бар (молот)",
        "bearish_pinbar": "Пин-бар (звезда)",
        "bullish_engulfing": "Бычье поглощение",
        "bearish_engulfing": "Медвежье поглощение",
    }
    if pattern:
        score += WEIGHTS["pattern"]
        signals.append(
            f"Паттерн: {pattern_names.get(pattern, pattern)} +{WEIGHTS['pattern']}"
        )
    else:
        signals.append("Паттерн: нет")

    # 2. Объём
    vol_ratio = calc_volume_ratio(candles_15m, periods=VOLUME_AVG_PERIODS)
    if vol_ratio >= THRESHOLDS["volume_good"]:
        score += WEIGHTS["volume"]
        signals.append(f"Объём: {vol_ratio:.1f}x +{WEIGHTS['volume']}")
    elif vol_ratio < THRESHOLDS["volume_low"]:
        score += PENALTIES["low_volume"]
        signals.append(f"Объём: {vol_ratio:.1f}x (штраф) {PENALTIES['low_volume']}")
    else:
        signals.append(f"Объём: {vol_ratio:.1f}x (нейтрально)")

    # 3. Тень
    if has_wick_beyond_level(last_closed, level, direction):
        score += WEIGHTS["wick"]
        signals.append(f"Тень за уровнем: да +{WEIGHTS['wick']}")
    else:
        signals.append("Тень за уровнем: нет")

    # 4. Закрытие в трети
    if close_in_correct_third(last_closed, direction):
        score += WEIGHTS["close_third"]
        signals.append(f"Закрытие: правильная треть +{WEIGHTS['close_third']}")
    else:
        signals.append("Закрытие: не в нужной трети")

    # 5. RSI
    closes_15m = [c["close"] for c in candles_15m]
    rsi = calculate_rsi(closes_15m, period=RSI_PERIOD)
    if rsi is not None:
        rsi_ok = (direction == "up" and rsi < 40) or (direction == "down" and rsi > 60)
        rsi_against = (direction == "up" and rsi > 60) or (direction == "down" and rsi < 40)
        if rsi_ok:
            score += WEIGHTS["rsi"]
            signals.append(f"RSI: {rsi:.1f} (в зоне) +{WEIGHTS['rsi']}")
        elif rsi_against:
            score += PENALTIES["rsi_against"]
            signals.append(f"RSI: {rsi:.1f} (против) {PENALTIES['rsi_against']}")
        else:
            signals.append(f"RSI: {rsi:.1f} (нейтрально)")
    else:
        signals.append("RSI: нет данных")

    # 6. Дивергенция RSI (новое в 4.x)
    divergence_info: dict | None = None
    if rsi is not None:
        rsi_series = calculate_rsi_series(closes_15m, period=RSI_PERIOD)
        divs = find_divergences(
            closes_15m, rsi_series, direction,
            min_rsi_gap=THRESHOLDS["divergence_min_rsi_gap"],
            min_price_gap_pct=THRESHOLDS["divergence_min_price_gap_pct"],
            max_lookback=THRESHOLDS["divergence_max_lookback"],
        )
        if divs:
            d = divs[0]
            divergence_info = {
                "kind": d.kind,
                "rsi_gap": round(d.rsi_gap, 1),
                "price_gap_pct": round(d.price_gap_pct * 100, 2),
                "age_bars": d.age_bars,
            }
            score += 1.5
            signals.append(
                f"RSI-дивергенция ({d.kind}), gap {d.rsi_gap:.0f}, "
                f"{d.age_bars} бар. +1.5"
            )
        else:
            signals.append("RSI-дивергенция: нет")

    # 7. Касания уровня (legacy)
    touches = count_touches(
        candles_15m, level,
        timeframe_minutes=15,
        lookback_hours=THRESHOLDS["touches_lookback_hours"],
    )
    if touches >= THRESHOLDS["worn_touches"]:
        score += PENALTIES["worn_level"]
        filters.append(f"Уровень изношен ({touches} касаний) {PENALTIES['worn_level']}")
    else:
        filters.append(f"Уровень свежий ({touches} касаний)")

    # 8. Уровни 4H/1D (новое)
    levels_4h: list[Level] = []
    levels_1d: list[Level] = []
    if klines_4h:
        levels_4h = find_levels(
            klines_4h, source_tf="240",
            tolerance_pct=THRESHOLDS["levels_tolerance_pct"],
            min_touches=THRESHOLDS["levels_min_touches"],
            worn_touches=THRESHOLDS["worn_touches"],
            cluster_eps_pct=THRESHOLDS["levels_cluster_eps_pct"],
        )
    if klines_1d:
        levels_1d = find_levels(
            klines_1d, source_tf="D",
            tolerance_pct=THRESHOLDS["levels_tolerance_pct"],
            min_touches=THRESHOLDS["levels_min_touches"],
            worn_touches=THRESHOLDS["worn_touches"],
            cluster_eps_pct=THRESHOLDS["levels_cluster_eps_pct"],
        )
    if levels_4h:
        filters.append(f"Уровней 4H: {len(levels_4h)}")
    if levels_1d:
        filters.append(f"Уровней 1D: {len(levels_1d)}")

    # 9. Структура тренда (новое) + legacy htf_trend
    structure: TrendStructure | None = None
    htf_trend_legacy = get_htf_trend(candles_4h)  # эта функция хочет dict — оставляем

    if klines_4h:
        structure = find_hh_hl(
            klines_4h, lookback=min(len(klines_4h), 100),
        )

    # 4H-тренд для risk: up/down/side → long/short/None
    trend_4h_dir: str | None = None
    if structure and structure.direction == "up":
        trend_4h_dir = "long"
    elif structure and structure.direction == "down":
        trend_4h_dir = "short"

    # Реакция score на тренд против
    if structure and structure.direction != "side":
        against = (
            (direction == "up" and structure.direction == "down")
            or (direction == "down" and structure.direction == "up")
        )
        if against:
            score += PENALTIES["htf_against"]
            filters.append(f"4H против направления {PENALTIES['htf_against']}")
        else:
            filters.append(f"4H структура: {structure.direction} (согласовано)")
    else:
        filters.append(f"4H структура: side (legacy EMA: {htf_trend_legacy})")

    # 10. Возраст алерта
    age_hours = max(0, (time.time() - alert_created_at) / 3600)
    if age_hours > THRESHOLDS["alert_max_age_hours"]:
        score += PENALTIES["old_alert"]
        filters.append(f"Алерт старый ({age_hours:.0f}ч) {PENALTIES['old_alert']}")
    else:
        filters.append(f"Возраст алерта: {age_hours:.0f}ч")

    # 11. Risk-модуль (новое, только если передан risk_cfg)
    risk_result: RiskResult | None = None
    hard_filter: str | None = None
    hard_filter_ru: str | None = None

    if risk_cfg is not None:
        atr_15m = _calc_atr_15m(candles_15m)
        atr_1h = _calc_atr_1h(candles_1h)

        # ATR для runway — 1h, если есть, иначе 15m
        atr_for_runway = atr_1h if atr_1h is not None else atr_15m
        # ATR для стопа — 15m
        atr_for_stop = atr_15m

        if atr_for_stop and atr_for_stop > 0:
            risk_dir = "long" if direction == "up" else "short"
            structure_stop = _structure_stop_15m(
                candles_15m, direction,
                lookback=THRESHOLDS["st_stop_lookback"],
            )
            entry = current_price

            # risk в единицах цены: |entry - structure_stop| или ATR-фолбэк
            if structure_stop is not None:
                risk_per_unit = abs(entry - structure_stop)
            else:
                risk_per_unit = atr_for_stop * risk_cfg.atr_multiplier
            if risk_per_unit <= 0:
                risk_per_unit = atr_for_stop * risk_cfg.atr_multiplier

            tp1, tp2 = _find_tp_levels(
                entry, direction, levels_4h,
                atr_for_runway, risk_cfg.min_rr, risk_per_unit,
            )

            # если runway считаем по atr_1h, риск для стопа по 15m,
            # передаём в validate_setup средний ATR (для runway-проверки)
            atr_for_validate = atr_for_runway or atr_for_stop
            risk_result = build_setup(
                entry=entry,
                structure_stop=structure_stop,
                atr=atr_for_validate,
                direction=risk_dir,
                tp1=tp1,
                tp2=tp2,
                cfg=risk_cfg,
                trend_4h=trend_4h_dir,
            )

            if not risk_result.valid:
                hard_filter = risk_result.reason or "unknown"
                hard_filter_ru = format_reason_ru(hard_filter)
                score += PENALTIES["hard_filter"]
                filters.append(
                    f"⛔ Hard filter: {hard_filter_ru} "
                    f"({PENALTIES['hard_filter']})"
                )
            else:
                filters.append(
                    f"Risk OK: RR={risk_result.rr:.2f} "
                    f"size={risk_result.size:.4f}"
                )
        else:
            filters.append("ATR(15m) недоступен → risk не посчитан")

    # 12. Вердикт
    if score >= THRESHOLDS["strong_min"]:
        verdict = "💪 Strong"
    elif score >= THRESHOLDS["weak_min"]:
        verdict = "⚠️ Weak"
    else:
        verdict = "❌ None"

    return {
        "score": round(score, 1),
        "verdict": verdict,
        "signals": signals,
        "filters": filters,
        "vol_ratio": vol_ratio,
        "rsi": round(rsi, 1) if rsi else None,
        "touches": touches,
        "htf_trend": htf_trend_legacy,
        "age_hours": round(age_hours, 1),
        "pattern": pattern,
        "strength_score": max(0, int(score)),
        "details": signals + filters,
        # Этап 4:
        "structure": structure,
        "levels_4h": levels_4h,
        "levels_1d": levels_1d,
        "divergence": divergence_info,
        "risk": risk_result,
        "hard_filter": hard_filter,
        "hard_filter_ru": hard_filter_ru,
    }


def format_alert_message(
    symbol: str,
    level: float,
    direction: str,
    current_price: float,
    evaluation: dict[str, Any],
    setup_note: str = "",
) -> str:
    """Форматирует сообщение алерта в HTML для Telegram."""
    cross_text = "🟢 СНИЗУ ВВЕРХ" if direction == "up" else "🔴 СВЕРХУ ВНИЗ"

    lines = [
        f"🚨 <b>Price Alert: {symbol}</b>",
        f"Уровень: <code>{level:,.2f}</code> | {cross_text}",
        f"💰 Цена: <code>{current_price:,.2f}</code>",
        "",
        "📊 <b>Подтверждения:</b>",
    ]

    for signal in evaluation.get("signals", []):
        lines.append(f"• {signal}")

    lines.append("")
    lines.append("🔍 <b>Фильтры:</b>")

    for f in evaluation.get("filters", []):
        lines.append(f"• {f}")

    lines.append("")
    lines.append(f"<b>Итого: {evaluation['score']}  →  {evaluation['verdict']}</b>")

    if setup_note:
        lines.append(f"📝 <b>Сетап:</b> <code>{setup_note}</code>")

    return "\n".join(lines)


# ==================== СТАРЫЕ ОБЁРТКИ (обратная совместимость) ====================

def analyze_candle_confirmation(
    klines: list[list],
    level: float,
    direction: str,
    volume_ratio: float = 0.0,
    interval_minutes: int = 15,
) -> dict[str, Any]:
    """Старая функция. Делегирует в evaluate_alert."""
    klines_4h = klines[:30] if len(klines) >= 30 else klines

    return evaluate_alert(
        symbol="UNKNOWN",
        level=level,
        direction=direction,
        current_price=float(klines[0][4]) if klines else 0.0,
        alert_created_at=time.time(),
        klines_15m=klines,
        klines_4h=klines_4h,
    )


def format_confirmation_message(analysis: dict[str, Any]) -> str:
    """Старая функция. Делегирует в format_alert_message."""
    return format_alert_message(
        symbol="UNKNOWN",
        level=0.0,
        direction="up",
        current_price=0.0,
        evaluation=analysis,
    )