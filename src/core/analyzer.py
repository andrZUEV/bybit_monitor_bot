"""
Модуль технического анализа свечей
"""

import time
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

def calculate_rsi(closes: List[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
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

def detect_pattern(candle: Dict[str, float], prev_candle: Dict[str, float]) -> Optional[str]:
    o, h, l, c = candle['open'], candle['high'], candle['low'], candle['close']
    po, ph, pl, pc = prev_candle['open'], prev_candle['high'], prev_candle['low'], prev_candle['close']
    
    body = abs(c - o)
    if body == 0: return None
    range_hl = h - l
    if range_hl == 0: return None
    
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    
    if (lower_wick >= 2 * body and upper_wick <= 0.3 * body and (c - l) / range_hl >= 0.66):
        return 'bullish_pinbar'
    if (upper_wick >= 2 * body and lower_wick <= 0.3 * body and (c - l) / range_hl <= 0.33):
        return 'bearish_pinbar'
    if (pc < po and c > o and o <= pc and c >= po):
        return 'bullish_engulfing'
    if (pc > po and c < o and o >= pc and c <= po):
        return 'bearish_engulfing'
    
    return None

def get_close_position(candle: Dict[str, float]) -> str:
    range_hl = candle['high'] - candle['low']
    if range_hl == 0: return 'middle'
    ratio = (candle['close'] - candle['low']) / range_hl
    if ratio >= 0.66: return 'upper_third'
    if ratio <= 0.33: return 'lower_third'
    return 'middle'

def check_wick_beyond_level(candle: Dict[str, float], level: float, direction: str) -> bool:
    threshold_pct = 0.002  # 0.2%
    if direction == 'up':
        if candle['low'] < level:
            return (level - candle['low']) / level >= threshold_pct
    else:
        if candle['high'] > level:
            return (candle['high'] - level) / level >= threshold_pct
    return False

def analyze_candle_confirmation(
    klines: List[List],
    level: float,
    direction: str,
    volume_ratio: float = 0.0,
    interval_minutes: int = 15
) -> Dict[str, Any]:
    empty_result = {
        'pattern': 'none', 'pattern_name_ru': 'Не обнаружен', 'rsi': None,
        'rsi_zone': 'neutral', 'close_position': 'middle', 'wick_beyond_level': False,
        'volume_ratio': volume_ratio, 'strength_score': 0, 'verdict': 'none',
        'verdict_text': 'Недостаточно данных'
    }
    
    if not klines or len(klines) < 3:
        return empty_result
    
    # === ЛОГИКА ВОЗРАСТА СВЕЧИ ===
    now_ms = int(time.time() * 1000)
    current_start_ms = int(klines[0][0])
    interval_ms = interval_minutes * 60 * 1000
    age_pct = (now_ms - current_start_ms) / interval_ms
    
    if age_pct < 0.70:
        # Свеча "сырая", берем предыдущую закрытую
        last_idx = 1
        prev_idx = 2
        logger.debug(f"Свеча заполнена на {age_pct*100:.1f}%, анализируем предыдущую закрытую")
    else:
        # Свеча зрелая, можно брать текущую для более раннего сигнала
        last_idx = 0
        prev_idx = 1
        logger.debug(f"Свеча заполнена на {age_pct*100:.1f}%, анализируем текущую")

    if len(klines) <= prev_idx:
        return empty_result

    candles = []
    for k in klines:
        candles.append({
            'open': float(k[1]), 'high': float(k[2]), 'low': float(k[3]),
            'close': float(k[4]), 'volume': float(k[5]), 'turnover': float(k[6])
        })
    
    last_candle = candles[last_idx]
    prev_candle = candles[prev_idx]
    
    pattern = detect_pattern(last_candle, prev_candle)
    pattern_names = {
        'bullish_pinbar': 'Пин-бар (молот) 🟢', 'bearish_pinbar': 'Пин-бар (звезда) 🔴',
        'bullish_engulfing': 'Бычье поглощение 🟢', 'bearish_engulfing': 'Медвежье поглощение 🔴',
        'none': 'Не обнаружен'
    }
    
    closes_reversed = [c['close'] for c in reversed(candles[:30])]
    rsi = calculate_rsi(closes_reversed, period=14)
    rsi_zone = 'oversold' if rsi and rsi < 30 else 'overbought' if rsi and rsi > 70 else 'neutral'
    
    close_position = get_close_position(last_candle)
    wick_beyond = check_wick_beyond_level(last_candle, level, direction)
    
    # === ПОДСЧЁТ SCORE ===
    score = 0
    if direction == 'up' and pattern in ['bullish_pinbar', 'bullish_engulfing']: score += 2
    elif direction == 'down' and pattern in ['bearish_pinbar', 'bearish_engulfing']: score += 2
    
    if direction == 'up' and rsi_zone == 'oversold': score += 1
    elif direction == 'down' and rsi_zone == 'overbought': score += 1
    
    if direction == 'up' and close_position == 'upper_third': score += 1
    elif direction == 'down' and close_position == 'lower_third': score += 1
    
    if wick_beyond: score += 1
    if volume_ratio >= 2.5: score += 1
    
    if score >= 4: verdict, verdict_text = 'strong', '🔥 Сильные признаки разворота'
    elif score >= 3: verdict, verdict_text = 'weak', '⚠️ Есть признаки силы'
    else: verdict, verdict_text = 'none', '❌ Слабые сигналы'
    
    return {
        'pattern': pattern, 'pattern_name_ru': pattern_names.get(pattern, 'Не обнаружен'),
        'rsi': round(rsi, 1) if rsi else None, 'rsi_zone': rsi_zone,
        'close_position': close_position, 'wick_beyond_level': wick_beyond,
        'volume_ratio': volume_ratio, 'strength_score': score,
        'verdict': verdict, 'verdict_text': f"{verdict_text} ({score}/5)"
    }

def format_confirmation_message(analysis: Dict[str, Any]) -> str:
    lines = ["\n📊 <b>Подтверждения:</b>"]
    lines.append(f"• Паттерн: {analysis['pattern_name_ru']}")
    
    if analysis['rsi'] is not None:
        rsi_ru = {'oversold': 'перепроданность', 'overbought': 'перекупленность', 'neutral': 'нейтрально'}
        lines.append(f"• RSI(14): <b>{analysis['rsi']}</b> ({rsi_ru[analysis['rsi_zone']]})")
    else:
        lines.append("• RSI(14): нет данных")
        
    if analysis['volume_ratio'] > 0:
        lines.append(f"• Объём: <b>{analysis['volume_ratio']:.1f}x</b> от среднего")
        
    close_ru = {'upper_third': 'верхняя треть', 'lower_third': 'нижняя треть', 'middle': 'середина'}
    lines.append(f"• Закрытие: {close_ru.get(analysis['close_position'], 'неизвестно')}")
    
    wick_text = "✅ да" if analysis['wick_beyond_level'] else "❌ нет"
    lines.append(f"• Тень за уровнем: {wick_text}")
    lines.append(f"\n{analysis['verdict_text']}")
    
    return "\n".join(lines)