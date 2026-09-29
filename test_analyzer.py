"""
Тест модуля анализа свечей
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from src.api.bybit_client import BybitClient
from src.core.analyzer import analyze_candle_confirmation, format_confirmation_message

client = BybitClient()

print(" Тест анализатора свечей\n")

# Получаем свечи BTCUSDT
klines = client.get_klines("BTCUSDT", "linear", "15", 30)

if not klines:
    print("❌ Не удалось получить свечи")
    exit(1)

print(f"✅ Получено {len(klines)} свечей\n")

# Тест 1: Бычий алерт (long)
print("=" * 50)
print("ТЕСТ 1: Бычий алерт (long) на уровне 85000")
print("=" * 50)

# Берём текущую цену как уровень (для теста)
current_price = float(klines[0][4])
level = current_price * 0.99  # Уровень чуть ниже текущей цены

analysis = analyze_candle_confirmation(
    klines=klines,
    level=level,
    direction="up",
    volume_ratio=2.8
)

print(format_confirmation_message(analysis))

# Тест 2: Медвежий алерт (short)
print("\n" + "=" * 50)
print("ТЕСТ 2: Медвежий алерт (short) на уровне 86000")
print("=" * 50)

level_short = current_price * 1.01

analysis_short = analyze_candle_confirmation(
    klines=klines,
    level=level_short,
    direction="down",
    volume_ratio=1.2
)

print(format_confirmation_message(analysis_short))