"""
Модуль технических индикаторов (единая точка входа).

ВАЖНО: Bybit отдаёт свечи от НОВЫХ к СТАРЫМ (closes[0] — последняя).
Все функции модуля принимают массив именно в таком порядке и внутри
разворачивают его для корректных расчётов.
"""

from typing import List, Optional, Tuple


# ==================== RSI ====================

def calculate_rsi(closes: List[float], period: int = 14) -> Optional[float]:
    """
    Расчёт RSI по формуле Wilder. Возвращает ОДНО последнее значение.

    Args:
        closes: массив цен закрытия, closes[0] — самая свежая.
        period: период RSI (обычно 14).

    Returns:
        Последнее значение RSI или None, если данных мало.
    """
    series = calculate_rsi_series(closes, period)
    if not series:
        return None
    # Возвращаем последнее не-None значение
    for value in series:
        if value is not None:
            return value
    return None


def calculate_rsi_series(
    closes: List[float],
    period: int = 14
) -> List[Optional[float]]:
    """
    Расчёт серии RSI по формуле Wilder (для экспорта в CSV).

    Args:
        closes: массив цен закрытия, closes[0] — самая свежая.
        period: период RSI.

    Returns:
        Список той же длины, что и closes, в том же порядке
        (новые первые). Первые `period` значений — None.
    """
    if len(closes) < period + 1:
        return [None] * len(closes)

    # Разворачиваем: от старых к новым
    closes_chrono = list(reversed(closes))

    changes = [
        closes_chrono[i] - closes_chrono[i - 1]
        for i in range(1, len(closes_chrono))
    ]
    gains = [max(c, 0) for c in changes]
    losses = [max(-c, 0) for c in changes]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    rsi_values_chrono: List[Optional[float]] = [None] * period

    if avg_loss == 0:
        rsi_values_chrono.append(100.0)
    else:
        rs = avg_gain / avg_loss
        rsi_values_chrono.append(100 - (100 / (1 + rs)))

    for i in range(period, len(changes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            rsi_values_chrono.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_values_chrono.append(100 - (100 / (1 + rs)))

    # Возвращаем в исходном порядке (новые первые)
    return list(reversed(rsi_values_chrono))


# ==================== EMA ====================

def calculate_ema(values: List[float], period: int) -> List[float]:
    """
    Расчёт EMA.

    ВАЖНО: values должны идти от СТАРЫХ к НОВЫМ (хронологический порядок).
    Если передан массив от Bybit (новые первые) — разверните его сами
    или используйте calculate_ema_from_bybit().

    Args:
        values: массив значений в хронологическом порядке.
        period: период EMA.

    Returns:
        Список значений EMA той же длины.
    """
    if not values:
        return []

    multiplier = 2 / (period + 1)
    ema = [values[0]]

    for i in range(1, len(values)):
        ema.append(values[i] * multiplier + ema[-1] * (1 - multiplier))

    return ema


def calculate_ema_from_bybit(values: List[float], period: int) -> List[float]:
    """
    Обёртка для массива от Bybit (новые первые).
    Разворачивает, считает EMA, возвращает в исходном порядке.
    """
    if not values:
        return []
    values_chrono = list(reversed(values))
    ema_chrono = calculate_ema(values_chrono, period)
    return list(reversed(ema_chrono))


# ==================== ATR (задел под стратегию) ====================

def calculate_atr(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    period: int = 14
) -> Optional[float]:
    """
    Расчёт ATR по методу Wilder. Возвращает ОДНО последнее значение.

    ВАЖНО: массивы должны идти от НОВЫХ к СТАРЫМ (порядок Bybit).
    Если длина < period + 1 — вернёт None.

    Args:
        highs, lows, closes: массивы цен, [0] — самая свежая свеча.
        period: период ATR (обычно 14).

    Returns:
        Последнее значение ATR или None.
    """
    series = calculate_atr_series(highs, lows, closes, period)
    if not series:
        return None
    for value in series:
        if value is not None:
            return value
    return None


def calculate_atr_series(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    period: int = 14
) -> List[Optional[float]]:
    """
    Серия ATR (новые первые).

    True Range = max(
        high - low,
        |high - prev_close|,
        |low  - prev_close|
    )
    ATR = сглаживание TR по Wilder.
    """
    n = len(closes)
    if n < period + 1 or len(highs) != n or len(lows) != n:
        return [None] * n

    # Разворачиваем в хронологический порядок
    h = list(reversed(highs))
    l = list(reversed(lows))
    c = list(reversed(closes))

    tr = [h[0] - l[0]]
    for i in range(1, n):
        prev_close = c[i - 1]
        tr.append(max(
            h[i] - l[i],
            abs(h[i] - prev_close),
            abs(l[i] - prev_close),
        ))

    atr_chrono: List[Optional[float]] = [None] * period
    # Первое значение ATR = среднее TR за первые period свечей
    atr_chrono.append(sum(tr[:period]) / period)

    for i in range(period, len(tr)):
        prev_atr = atr_chrono[-1]
        atr_chrono.append((prev_atr * (period - 1) + tr[i]) / period)

    return list(reversed(atr_chrono))


# ==================== Bollinger Bands (задел, опционально) ====================

def calculate_bollinger(
    closes: List[float],
    period: int = 20,
    std_mult: float = 2.0
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Bollinger Bands (последнее значение).

    Returns:
        (upper, middle, lower) или (None, None, None), если данных мало.
    """
    if len(closes) < period:
        return None, None, None

    # Bybit: closes[0] — новая. Для SMA берём последние `period` свечей
    window = closes[:period]
    middle = sum(window) / period

    variance = sum((x - middle) ** 2 for x in window) / period
    std = variance ** 0.5

    upper = middle + std_mult * std
    lower = middle - std_mult * std
    return upper, middle, lower