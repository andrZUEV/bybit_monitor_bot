"""
Модуль технического анализа с весами, штрафами и фильтрами контекста.

Версия 3.0:
- Единый источник RSI/EMA — src/utils/indicators.py
- count_touches корректно использует lookback_hours
- Все индикаторы считаются на развёрнутых данных (порядок Bybit = новые первые)
- Паттерн/тень/закрытие оцениваются по ЗАКРЫТОЙ свече [1]
- Объём берётся по ТЕКУЩЕЙ свече [0] как раннее предупреждение
"""

import logging
import time
from typing import Any

from src.utils.indicators import (
    calculate_ema_from_bybit,
    calculate_rsi,
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
}

THRESHOLDS = {
    "strong_min": 5.5,
    "weak_min": 3.5,
    "volume_good": 1.8,
    "volume_low": 1.0,
    "alert_max_age_hours": 36,
    "worn_touches": 5,            # по стратегии: 5+ касаний = изношен
    "touch_tolerance_pct": 0.0015,  # 0.15%
    "touches_lookback_hours": 48,
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
    o, h, l, c = candle["open"], candle["high"], candle["low"], candle["close"]
    po, pc = prev_candle["open"], prev_candle["close"]

    body = abs(c - o)
    if body == 0:
        return None

    range_hl = h - l
    if range_hl == 0:
        return None

    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l

    # Бычий пин-бар (молот)
    if direction == "up":
        if lower_wick >= 2 * body and upper_wick <= 0.3 * body and (c - l) / range_hl >= 0.66:
            return "bullish_pinbar"

    # Медвежий пин-бар (падающая звезда)
    elif direction == "down":
        if upper_wick >= 2 * body and lower_wick <= 0.3 * body and (c - l) / range_hl <= 0.33:
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


# ==================== ГЛАВНАЯ ФУНКЦИЯ ОЦЕНКИ ====================

def evaluate_alert(
    symbol: str,
    level: float,
    direction: str,
    current_price: float,
    alert_created_at: float,
    klines_15m: list[list],
    klines_4h: list[list],
) -> dict[str, Any]:
    """
    Оценивает алерт по системе весов и штрафов.

    Returns:
        Словарь с оценкой, вердиктом, signals и filters.
    """
    candles_15m = parse_klines(klines_15m)
    candles_4h = parse_klines(klines_4h)

    if len(candles_15m) < 3:
        return {
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
        }

    # ЗАКРЫТАЯ свеча [1] и предыдущая [2]
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
        signals.append(f"Паттерн: {pattern_names.get(pattern, pattern)} +{WEIGHTS['pattern']}")
    else:
        signals.append("Паттерн: нет")

    # 2. Объём (по ТЕКУЩЕЙ свече [0])
    vol_ratio = calc_volume_ratio(candles_15m, periods=VOLUME_AVG_PERIODS)
    if vol_ratio >= THRESHOLDS["volume_good"]:
        score += WEIGHTS["volume"]
        signals.append(f"Объём: {vol_ratio:.1f}x +{WEIGHTS['volume']}")
    elif vol_ratio < THRESHOLDS["volume_low"]:
        score += PENALTIES["low_volume"]
        signals.append(f"Объём: {vol_ratio:.1f}x (штраф) {PENALTIES['low_volume']}")
    else:
        signals.append(f"Объём: {vol_ratio:.1f}x (нейтрально)")

    # 3. Тень за уровнем
    if has_wick_beyond_level(last_closed, level, direction):
        score += WEIGHTS["wick"]
        signals.append(f"Тень за уровнем: да +{WEIGHTS['wick']}")
    else:
        signals.append("Тень за уровнем: нет")

    # 4. Закрытие в нужной трети
    if close_in_correct_third(last_closed, direction):
        score += WEIGHTS["close_third"]
        signals.append(f"Закрытие: правильная треть +{WEIGHTS['close_third']}")
    else:
        signals.append("Закрытие: не в нужной трети")

    # 5. RSI
    closes = [c["close"] for c in candles_15m]  # новые первые
    rsi = calculate_rsi(closes, period=RSI_PERIOD)  # внутри корректно развернёт

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

    # 6. Изношенность уровня
    touches = count_touches(
        candles_15m,
        level,
        timeframe_minutes=15,
        lookback_hours=THRESHOLDS["touches_lookback_hours"],
    )
    if touches >= THRESHOLDS["worn_touches"]:
        score += PENALTIES["worn_level"]
        filters.append(
            f"Уровень изношен ({touches} касаний) {PENALTIES['worn_level']}"
        )
    else:
        filters.append(f"Уровень свежий ({touches} касаний)")

    # 7. 4H тренд
    htf_trend = get_htf_trend(candles_4h)
    if (direction == "up" and htf_trend == "down") or (
        direction == "down" and htf_trend == "up"
    ):
        score += PENALTIES["htf_against"]
        filters.append(f"4H против направления {PENALTIES['htf_against']}")
    else:
        filters.append(f"4H: {htf_trend}")

    # 8. Возраст алерта
    age_hours = max(0, (time.time() - alert_created_at) / 3600)
    if age_hours > THRESHOLDS["alert_max_age_hours"]:
        score += PENALTIES["old_alert"]
        filters.append(f"Алерт старый ({age_hours:.0f}ч) {PENALTIES['old_alert']}")
    else:
        filters.append(f"Возраст алерта: {age_hours:.0f}ч")

    # 9. Вердикт
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
        "htf_trend": htf_trend,
        "age_hours": round(age_hours, 1),
        "pattern": pattern,
        # Для обратной совместимости
        "strength_score": max(0, int(score)),
        "details": signals + filters,
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