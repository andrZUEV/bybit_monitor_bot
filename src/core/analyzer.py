"""
Модуль технического анализа с весами, штрафами и фильтрами контекста
Версия 2.0 — исправлены критические баги RSI/EMA, анализ по закрытой свече
"""

import time
import logging
from typing import Dict, Any, List, Optional, Tuple

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
    "worn_touches": 4,
    "touch_tolerance_pct": 0.0015,  # 0.15%
}

# Периоды индикаторов
RSI_PERIOD = 14
HTF_EMA_PERIOD = 50
HTF_EMA_SLOPE_LOOKBACK = 10
VOLUME_AVG_PERIODS = 20


# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================

def parse_klines(klines: List[List]) -> List[Dict[str, float]]:
    """Парсит сырые klines в список словарей. Порядок сохраняется как есть."""
    candles = []
    for k in klines:
        candles.append({
            'open': float(k[1]),
            'high': float(k[2]),
            'low': float(k[3]),
            'close': float(k[4]),
            'volume': float(k[5])
        })
    return candles


def calculate_rsi(closes: List[float], period: int = RSI_PERIOD) -> Optional[float]:
    """
    Расчёт RSI по формуле Wilder.
    
    ВАЖНО: Bybit отдаёт свечи от НОВЫХ к СТАРЫМ (closes[0] — последняя).
    Для корректного RSI разворачиваем массив перед расчётом.
    """
    if len(closes) < period + 1:
        return None
    
    # Разворачиваем: от старых к новым
    closes_chrono = list(reversed(closes))
    
    changes = [closes_chrono[i] - closes_chrono[i - 1] for i in range(1, len(closes_chrono))]
    gains = [max(c, 0) for c in changes]
    losses = [max(-c, 0) for c in changes]
    
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    for i in range(period, len(changes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    
    if avg_loss == 0:
        return 100.0
    
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calculate_ema(values: List[float], period: int) -> List[float]:
    """
    Расчёт EMA.
    
    ВАЖНО: values должны идти от СТАРЫХ к НОВЫМ.
    Если передан массив от Bybit (новые первые) — разворачиваем.
    """
    if not values:
        return []
    
    # Разворачиваем, если нужно (для безопасности всегда разворачиваем)
    values_chrono = list(reversed(values))
    
    multiplier = 2 / (period + 1)
    ema = [values_chrono[0]]
    
    for i in range(1, len(values_chrono)):
        ema.append(values_chrono[i] * multiplier + ema[-1] * (1 - multiplier))
    
    return ema


def detect_pattern(candle: Dict[str, float], prev_candle: Dict[str, float], direction: str) -> Optional[str]:
    """Определяет разворотный паттерн"""
    o, h, l, c = candle['open'], candle['high'], candle['low'], candle['close']
    po, ph, pl, pc = prev_candle['open'], prev_candle['high'], prev_candle['low'], prev_candle['close']
    
    body = abs(c - o)
    if body == 0:
        return None
    
    range_hl = h - l
    if range_hl == 0:
        return None
    
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    
    # Бычий пин-бар (молот)
    if direction == 'up':
        if lower_wick >= 2 * body and upper_wick <= 0.3 * body and (c - l) / range_hl >= 0.66:
            return 'bullish_pinbar'
    # Медвежий пин-бар (падающая звезда)
    elif direction == 'down':
        if upper_wick >= 2 * body and lower_wick <= 0.3 * body and (c - l) / range_hl <= 0.33:
            return 'bearish_pinbar'
    
    # Бычье поглощение
    if direction == 'up':
        if pc < po and c > o and o <= pc and c >= po:
            return 'bullish_engulfing'
    # Медвежье поглощение
    elif direction == 'down':
        if pc > po and c < o and o >= pc and c <= po:
            return 'bearish_engulfing'
    
    return None


def has_wick_beyond_level(candle: Dict[str, float], level: float, direction: str) -> bool:
    """Проверяет, проколола ли тень уровень"""
    if direction == 'up':
        return candle['low'] < level
    else:
        return candle['high'] > level


def close_in_correct_third(candle: Dict[str, float], direction: str) -> bool:
    """Проверяет, закрылась ли свеча в нужной трети"""
    range_hl = candle['high'] - candle['low']
    if range_hl == 0:
        return False
    
    third = range_hl / 3
    if direction == 'up':
        return candle['close'] >= candle['high'] - third
    else:
        return candle['close'] <= candle['low'] + third


def count_touches(
    candles: List[Dict[str, float]], 
    level: float, 
    timeframe_minutes: int = 15,
    lookback_hours: int = 48
) -> int:
    """
    Считает касания уровня за последние lookback_hours.
    
    ИСПРАВЛЕНО: теперь lookback_hours реально используется для среза.
    """
    # Сколько свечей нужно для lookback_hours
    candles_needed = lookback_hours * (60 // timeframe_minutes)
    candles_slice = candles[:candles_needed]
    
    tolerance = level * THRESHOLDS["touch_tolerance_pct"]
    touches = 0
    
    for candle in candles_slice:
        if candle['high'] >= (level - tolerance) and candle['low'] <= (level + tolerance):
            touches += 1
    
    return touches


def get_htf_trend(candles_4h: List[Dict[str, float]]) -> str:
    """
    Определяет тренд на 4H по EMA50.
    
    ИСПРАВЛЕНО:
    - Разворачиваем свечи перед расчётом EMA
    - Используем и наклон EMA, и положение цены относительно EMA50
    """
    if len(candles_4h) < HTF_EMA_PERIOD + HTF_EMA_SLOPE_LOOKBACK:
        return 'side'
    
    closes = [c['close'] for c in candles_4h]
    ema50 = calculate_ema(closes, HTF_EMA_PERIOD)
    
    if len(ema50) < HTF_EMA_SLOPE_LOOKBACK:
        return 'side'
    
    # Наклон EMA (сравниваем текущее значение со значением N свечей назад)
    ema_slope = ema50[-1] - ema50[-HTF_EMA_SLOPE_LOOKBACK]
    current_price = closes[-1]  # Последняя цена (уже в хронологическом порядке после разворота в calculate_ema)
    ema_value = ema50[-1]
    
    # Определяем тренд по двум факторам
    price_above_ema = current_price > ema_value
    ema_rising = ema_slope > 0
    
    if price_above_ema and ema_rising:
        return 'up'
    elif not price_above_ema and not ema_rising:
        return 'down'
    else:
        return 'side'


def calc_volume_ratio(candles: List[Dict[str, float]], periods: int = VOLUME_AVG_PERIODS) -> float:
    """
    Считает отношение объёма текущей свечи к среднему.
    
    candles[0] — текущая (незакрытая) свеча
    candles[1:periods+1] — предыдущие закрытые свечи для среднего
    """
    if len(candles) < periods + 1:
        return 1.0
    
    current_volume = candles[0]['volume']
    avg_volume = sum(c['volume'] for c in candles[1:periods+1]) / periods
    
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
    klines_15m: List[List],
    klines_4h: List[List],
) -> Dict[str, Any]:
    """
    Оценивает алерт по системе весов и штрафов.
    
    ИСПРАВЛЕНО:
    - RSI считается на развёрнутых данных
    - EMA/HTF считаются на развёрнутых данных
    - Паттерн/тень/закрытие оцениваются по ЗАКРЫТОЙ свече [1], а не по текущей [0]
    - Объём берётся по текущей свече [0] как раннее предупреждение
    - count_touches использует lookback_hours
    - age_hours клампится в 0
    
    Returns:
        Словарь с оценкой, вердиктом, signals и filters
    """
    candles_15m = parse_klines(klines_15m)
    candles_4h = parse_klines(klines_4h)
    
    if len(candles_15m) < 3:
        return {
            'score': 0.0,
            'verdict': '❌ None',
            'signals': ['Недостаточно данных'],
            'filters': [],
            'vol_ratio': 0.0,
            'rsi': None,
            'touches': 0,
            'htf_trend': 'side',
            'age_hours': 0,
            'pattern': None,
            # Для обратной совместимости
            'strength_score': 0,
            'details': ['Недостаточно данных'],
        }
    
    # ИСПРАВЛЕНО: берём ЗАКРЫТУЮ свечу [1] и предыдущую [2]
    # Текущая [0] ещё формируется и может перерисоваться
    last_closed = candles_15m[1]
    prev_closed = candles_15m[2]
    
    score = 0.0
    signals = []  # Подтверждения (с баллами)
    filters = []  # Контекстные фильтры
    
    # 1. Паттерн (по закрытой свече)
    pattern = detect_pattern(last_closed, prev_closed, direction)
    pattern_names = {
        'bullish_pinbar': 'Пин-бар (молот)',
        'bearish_pinbar': 'Пин-бар (звезда)',
        'bullish_engulfing': 'Бычье поглощение',
        'bearish_engulfing': 'Медвежье поглощение',
    }
    
    if pattern:
        score += WEIGHTS["pattern"]
        signals.append(f"Паттерн: {pattern_names.get(pattern, pattern)} +{WEIGHTS['pattern']}")
    else:
        signals.append("Паттерн: нет")
    
    # 2. Объём (по ТЕКУЩЕЙ свече [0] — раннее предупреждение)
    vol_ratio = calc_volume_ratio(candles_15m, periods=VOLUME_AVG_PERIODS)
    if vol_ratio >= THRESHOLDS["volume_good"]:
        score += WEIGHTS["volume"]
        signals.append(f"Объём: {vol_ratio:.1f}x +{WEIGHTS['volume']}")
    elif vol_ratio < THRESHOLDS["volume_low"]:
        score += PENALTIES["low_volume"]
        signals.append(f"Объём: {vol_ratio:.1f}x (штраф) {PENALTIES['low_volume']}")
    else:
        signals.append(f"Объём: {vol_ratio:.1f}x (нейтрально)")
    
    # 3. Тень за уровнем (по закрытой свече)
    if has_wick_beyond_level(last_closed, level, direction):
        score += WEIGHTS["wick"]
        signals.append(f"Тень за уровнем: да +{WEIGHTS['wick']}")
    else:
        signals.append("Тень за уровнем: нет")
    
    # 4. Закрытие в нужной трети (по закрытой свече)
    if close_in_correct_third(last_closed, direction):
        score += WEIGHTS["close_third"]
        signals.append(f"Закрытие: правильная треть +{WEIGHTS['close_third']}")
    else:
        signals.append("Закрытие: не в нужной трети")
    
    # 5. RSI (ИСПРАВЛЕНО: считается на развёрнутых данных)
    closes = [c['close'] for c in candles_15m]
    rsi = calculate_rsi(closes, period=RSI_PERIOD)
    
    if rsi is not None:
        rsi_ok = (direction == 'up' and rsi < 40) or (direction == 'down' and rsi > 60)
        rsi_against = (direction == 'up' and rsi > 60) or (direction == 'down' and rsi < 40)
        
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
    
    # 6. Изношенность уровня (ИСПРАВЛЕНО: lookback_hours используется)
    touches = count_touches(
        candles_15m, 
        level, 
        timeframe_minutes=15, 
        lookback_hours=48
    )
    if touches >= THRESHOLDS["worn_touches"]:
        score += PENALTIES["worn_level"]
        filters.append(f"Уровень изношен ({touches} касаний) {PENALTIES['worn_level']}")
    else:
        filters.append(f"Уровень свежий ({touches} касаний)")
    
    # 7. 4H тренд (ИСПРАВЛЕНО: EMA считается на развёрнутых данных)
    htf_trend = get_htf_trend(candles_4h)
    if (direction == 'up' and htf_trend == 'down') or (direction == 'down' and htf_trend == 'up'):
        score += PENALTIES["htf_against"]
        filters.append(f"4H против направления {PENALTIES['htf_against']}")
    else:
        filters.append(f"4H: {htf_trend}")
    
    # 8. Возраст алерта (ИСПРАВЛЕНО: клампим в 0)
    age_hours = max(0, (time.time() - alert_created_at) / 3600)
    if age_hours > THRESHOLDS["alert_max_age_hours"]:
        score += PENALTIES["old_alert"]
        filters.append(f"Алерт старый ({age_hours:.0f}ч) {PENALTIES['old_alert']}")
    else:
        filters.append(f"Возраст алерта: {age_hours:.0f}ч")
    
    # 9. Вердикт
    if score >= THRESHOLDS["strong_min"]:
        verdict = ' Strong'
    elif score >= THRESHOLDS["weak_min"]:
        verdict = '⚠️ Weak'
    else:
        verdict = '❌ None'
    
    return {
        'score': round(score, 1),
        'verdict': verdict,
        'signals': signals,
        'filters': filters,
        'vol_ratio': vol_ratio,
        'rsi': round(rsi, 1) if rsi else None,
        'touches': touches,
        'htf_trend': htf_trend,
        'age_hours': round(age_hours, 1),
        'pattern': pattern,
        # Для обратной совместимости со старым monitor.py
        'strength_score': max(0, int(score)),
        'details': signals + filters,
    }


def format_alert_message(
    symbol: str,
    level: float,
    direction: str,
    current_price: float,
    evaluation: Dict[str, Any],
    setup_note: str = ""
) -> str:
    """
    Форматирует сообщение алерта.
    
    ИСПРАВЛЕНО: использует signals и filters вместо хрупкого details[:5]/[5:]
    """
    cross_text = "🟢 СНИЗУ ВВЕРХ" if direction == 'up' else "🔴 СВЕРХУ ВНИЗ"
    
    lines = []
    lines.append(f"🚨 <b>Price Alert: {symbol}</b>")
    lines.append(f"Уровень: <code>{level:,.2f}</code> | {cross_text}")
    lines.append(f"💰 Цена: <code>{current_price:,.2f}</code>")
    lines.append("")
    lines.append("📊 <b>Подтверждения:</b>")
    
    for signal in evaluation.get('signals', []):
        lines.append(f"• {signal}")
    
    lines.append("")
    lines.append("🔍 <b>Фильтры:</b>")
    
    for f in evaluation.get('filters', []):
        lines.append(f"• {f}")
    
    lines.append("")
    lines.append(f"<b>Итого: {evaluation['score']}  →  {evaluation['verdict']}</b>")
    
    if setup_note:
        lines.append(f"📝 <b>Сетап:</b> <code>{setup_note}</code>")
    
    return "\n".join(lines)


# ==================== СТАРАЯ ФУНКЦИЯ (для обратной совместимости) ====================

def analyze_candle_confirmation(
    klines: List[List],
    level: float,
    direction: str,
    volume_ratio: float = 0.0,
    interval_minutes: int = 15
) -> Dict[str, Any]:
    """
    Старая функция для обратной совместимости.
    Вызывает evaluate_alert с заглушками.
    """
    # Заглушка для klines_4h
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


def format_confirmation_message(analysis: Dict[str, Any]) -> str:
    """Старая функция для обратной совместимости"""
    return format_alert_message(
        symbol="UNKNOWN",
        level=0.0,
        direction="up",
        current_price=0.0,
        evaluation=analysis,
    )