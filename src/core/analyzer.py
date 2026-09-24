"""
Модуль технического анализа свечей
Анализ паттернов, RSI, позиции закрытия и сбора стопов
"""

import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


def calculate_rsi(closes: List[float], period: int = 14) -> Optional[float]:
    """
    Расчёт RSI по формуле Wilder (сглаживание через EMA)
    
    Args:
        closes: Список цен закрытия (от старых к новым)
        period: Период RSI (по умолчанию 14)
    
    Returns:
        Значение RSI или None если недостаточно данных
    """
    if len(closes) < period + 1:
        return None
    
    # Считаем изменения цен
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    
    # Разделяем на gains и losses
    gains = [max(c, 0) for c in changes]
    losses = [max(-c, 0) for c in changes]
    
    # Первые period значений — простое среднее
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    # Дальше — сглаживание Wilder
    for i in range(period, len(changes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    
    if avg_loss == 0:
        return 100.0
    
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def detect_pattern(candle: Dict[str, float], prev_candle: Dict[str, float]) -> Optional[str]:
    """
    Определяет разворотный паттерн по последней свече
    
    Args:
        candle: Текущая свеча {open, high, low, close}
        prev_candle: Предыдущая свеча
    
    Returns:
        'bullish_pinbar', 'bearish_pinbar', 'bullish_engulfing', 
        'bearish_engulfing' или None
    """
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
    
    # --- Пин-бар (молот / падающая звезда) ---
    # Бычий пин-бар (молот)
    if (lower_wick >= 2 * body and 
        upper_wick <= 0.3 * body and
        (c - l) / range_hl >= 0.66):  # тело в верхней трети
        return 'bullish_pinbar'
    
    # Медвежий пин-бар (падающая звезда)
    if (upper_wick >= 2 * body and 
        lower_wick <= 0.3 * body and
        (c - l) / range_hl <= 0.33):  # тело в нижней трети
        return 'bearish_pinbar'
    
    # --- Поглощение ---
    # Бычье поглощение
    if (pc < po and  # предыдущая медвежья
        c > o and    # текущая бычья
        o <= pc and  # тело текущей перекрывает тело предыдущей
        c >= po):
        return 'bullish_engulfing'
    
    # Медвежье поглощение
    if (pc > po and  # предыдущая бычья
        c < o and    # текущая медвежья
        o >= pc and  # тело текущей перекрывает тело предыдущей
        c <= po):
        return 'bearish_engulfing'
    
    return None


def get_close_position(candle: Dict[str, float]) -> str:
    """Определяет, в какой трети диапазона закрылась свеча"""
    o, h, l, c = candle['open'], candle['high'], candle['low'], candle['close']
    range_hl = h - l
    if range_hl == 0:
        return 'middle'
    
    ratio = (c - l) / range_hl
    if ratio >= 0.66:
        return 'upper_third'
    elif ratio <= 0.33:
        return 'lower_third'
    return 'middle'


def check_wick_beyond_level(candle: Dict[str, float], level: float, direction: str) -> bool:
    """
    Проверяет, проколола ли тень уровень (сбор стопов)
    
    Args:
        candle: Свеча
        level: Ценовой уровень алерта
        direction: 'up' (long) или 'down' (short)
    
    Returns:
        True если тень проколола уровень на ≥ 0.2%
    """
    threshold_pct = 0.002  # 0.2%
    
    if direction == 'up':
        # Для лонга: нижняя тень должна проколоть уровень снизу
        # (цена опускалась ниже уровня, но закрылась выше)
        wick_low = candle['low']
        if wick_low < level:
            penetration = (level - wick_low) / level
            return penetration >= threshold_pct
    else:
        # Для шорта: верхняя тень должна проколоть уровень сверху
        wick_high = candle['high']
        if wick_high > level:
            penetration = (wick_high - level) / level
            return penetration >= threshold_pct
    
    return False


def analyze_candle_confirmation(
    klines: List[List],
    level: float,
    direction: str,
    volume_ratio: float = 0.0
) -> Dict[str, Any]:
    """
    Главная функция анализа подтверждений сетапа
    
    Args:
        klines: Список свечей от Bybit API (от новых к старым)
                Формат: [startTime, open, high, low, close, volume, turnover]
        level: Ценовой уровень алерта
        direction: 'up' (long) или 'down' (short)
        volume_ratio: Отношение объема текущей свечи к среднему
    
    Returns:
        Словарь с результатами анализа
    """
    # Пустой результат по умолчанию
    empty_result = {
        'pattern': 'none',
        'pattern_name_ru': 'Не обнаружен',
        'rsi': None,
        'rsi_zone': 'neutral',
        'close_position': 'middle',
        'wick_beyond_level': False,
        'volume_ratio': volume_ratio,
        'strength_score': 0,
        'verdict': 'none',
        'verdict_text': 'Недостаточно данных для анализа'
    }
    
    if not klines or len(klines) < 20:
        return empty_result
    
    # Парсим свечи (Bybit отдаёт от новых к старым)
    candles = []
    for k in klines:
        candles.append({
            'open': float(k[1]),
            'high': float(k[2]),
            'low': float(k[3]),
            'close': float(k[4]),
            'volume': float(k[5]),
            'turnover': float(k[6])
        })
    
    # Берём последнюю свечу для анализа паттерна
    last_candle = candles[0]
    prev_candle = candles[1]
    
    # Определяем паттерн
    pattern = detect_pattern(last_candle, prev_candle)
    pattern_names = {
        'bullish_pinbar': 'Пин-бар (молот) 🟢',
        'bearish_pinbar': 'Пин-бар (падающая звезда) 🔴',
        'bullish_engulfing': 'Бычье поглощение 🟢',
        'bearish_engulfing': 'Медвежье поглощение ',
        'none': 'Не обнаружен'
    }
    
    # Считаем RSI по закрытиям (берём последние 30 свечей, от старых к новым)
    closes_reversed = [c['close'] for c in reversed(candles[:30])]
    rsi = calculate_rsi(closes_reversed, period=14)
    
    if rsi is not None:
        if rsi < 30:
            rsi_zone = 'oversold'
        elif rsi > 70:
            rsi_zone = 'overbought'
        else:
            rsi_zone = 'neutral'
    else:
        rsi_zone = 'neutral'
    
    # Позиция закрытия
    close_position = get_close_position(last_candle)
    
    # Тень за уровнем
    wick_beyond = check_wick_beyond_level(last_candle, level, direction)
    
    # === Подсчёт strength_score (0-5) ===
    score = 0
    
    # +2: Паттерн совпадает с направлением
    if direction == 'up' and pattern in ['bullish_pinbar', 'bullish_engulfing']:
        score += 2
    elif direction == 'down' and pattern in ['bearish_pinbar', 'bearish_engulfing']:
        score += 2
    
    # +1: RSI в нужной зоне
    if direction == 'up' and rsi_zone == 'oversold':
        score += 1
    elif direction == 'down' and rsi_zone == 'overbought':
        score += 1
    
    # +1: Закрытие в нужной трети
    if direction == 'up' and close_position == 'upper_third':
        score += 1
    elif direction == 'down' and close_position == 'lower_third':
        score += 1
    
    # +1: Тень за уровнем (сбор стопов)
    if wick_beyond:
        score += 1
    
    # +1: Объём ≥ 2.5x
    if volume_ratio >= 2.5:
        score += 1
    
    # === Вердикт ===
    if score >= 4:
        verdict = 'strong'
        verdict_text = '🔥 Сильные признаки разворота — высокий шанс входа'
    elif score >= 2:
        verdict = 'weak'
        verdict_text = '️ Есть признаки силы — можно рассматривать вход'
    else:
        verdict = 'none'
        verdict_text = '❌ Слабые сигналы — лучше пропустить'
    
    return {
        'pattern': pattern,
        'pattern_name_ru': pattern_names.get(pattern, 'Не обнаружен'),
        'rsi': round(rsi, 1) if rsi is not None else None,
        'rsi_zone': rsi_zone,
        'close_position': close_position,
        'wick_beyond_level': wick_beyond,
        'volume_ratio': volume_ratio,
        'strength_score': score,
        'verdict': verdict,
        'verdict_text': verdict_text
    }


def format_confirmation_message(analysis: Dict[str, Any]) -> str:
    """
    Форматирует результат анализа в HTML-сообщение для Telegram
    
    Returns:
        HTML-строка с блоком подтверждений
    """
    lines = []
    lines.append("\n📊 <b>Подтверждения:</b>")
    
    # Паттерн
    lines.append(f"• Паттерн: {analysis['pattern_name_ru']}")
    
    # RSI
    if analysis['rsi'] is not None:
        rsi_zone_ru = {
            'oversold': 'перепроданность',
            'overbought': 'перекупленность',
            'neutral': 'нейтрально'
        }
        lines.append(f"• RSI(14): <b>{analysis['rsi']}</b> ({rsi_zone_ru[analysis['rsi_zone']]})")
    else:
        lines.append("• RSI(14): нет данных")
    
    # Объём
    if analysis['volume_ratio'] > 0:
        lines.append(f"• Объём: <b>{analysis['volume_ratio']:.1f}x</b> от среднего")
    else:
        lines.append("• Объём: нет данных")
    
    # Закрытие
    close_ru = {
        'upper_third': 'верхняя треть',
        'lower_third': 'нижняя треть',
        'middle': 'середина'
    }
    lines.append(f"• Закрытие: {close_ru.get(analysis['close_position'], 'неизвестно')}")
    
    # Тень за уровнем
    wick_text = "✅ да" if analysis['wick_beyond_level'] else "❌ нет"
    lines.append(f"• Тень за уровнем: {wick_text}")
    
    # Вердикт
    verdict_emoji = {'strong': '🔥', 'weak': '⚠️', 'none': '❌'}
    emoji = verdict_emoji.get(analysis['verdict'], '❓')
    lines.append(f"\n{emoji} <b>Вердикт:</b> {analysis['verdict_text']} ({analysis['strength_score']}/5)")
    
    return "\n".join(lines)