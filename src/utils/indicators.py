"""
Модуль технических индикаторов (единая точка входа)
"""

from typing import List, Optional


def calculate_rsi(closes: List[float], period: int = 14) -> Optional[float]:
    """
    Расчёт RSI по формуле Wilder.
    
    ВАЖНО: Bybit отдаёт свечи от НОВЫХ к СТАРЫМ (closes[0] — последняя).
    Для корректного RSI мы разворачиваем массив перед расчётом, 
    чтобы на растущем рынке RSI был ~100, а не ~0.
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