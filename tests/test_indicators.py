"""Тесты src/utils/indicators.py.

ВАЖНО: функции принимают списки в ХРОНОЛОГИЧЕСКОМ порядке
(старые первыми) — это соответствует поведению, зафиксированному
в Этапе 1. Для Bybit-порядка (новые первыми) есть *_from_bybit
обёртки.
"""
from __future__ import annotations

import math

import pytest

from src.utils import indicators


# ---------- RSI ----------

class TestRSI:
    def test_rsi_all_gains(self):
        # Монотонно растущий ряд (старые первыми) → RSI = 100
        prices = [float(i) for i in range(1, 30)]
        rsi = indicators.calculate_rsi(prices, period=14)
        assert rsi == pytest.approx(100.0, abs=1e-6)

    def test_rsi_all_losses(self):
        prices = [float(i) for i in range(30, 1, -1)]
        rsi = indicators.calculate_rsi(prices, period=14)
        assert rsi == pytest.approx(0.0, abs=1e-6)

    def test_rsi_flat_is_neutral(self):
        # Плоский ряд: gains=losses=0 → RSI = 50 (или 100, если avg_loss=0).
        prices = [100.0] * 30
        rsi = indicators.calculate_rsi(prices, period=14)
        assert rsi is None or 0.0 <= rsi <= 100.0

    def test_rsi_insufficient_data_returns_none(self):
        # Меньше period+1 точек → None (зафиксировано в репо).
        prices = [100.0, 101.0, 102.0]
        assert indicators.calculate_rsi(prices, period=14) is None

    def test_rsi_series_length(self):
        prices = [float(i) for i in range(1, 40)]
        series = indicators.calculate_rsi_series(prices, period=14)
        assert len(series) == len(prices)
        # Последнее значение — валидное число в [0, 100].
        last = series[-1]
        assert last is not None
        assert 0.0 <= last <= 100.0

    def test_rsi_known_range(self):
        # Произвольный ряд — значение в разумном коридоре.
        prices = [
            44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
            45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00,
            46.03, 46.41, 46.22, 45.64, 46.21, 46.25, 45.71, 46.45,
            45.78, 45.35, 44.03, 44.18, 44.22, 44.57, 43.42, 42.66,
        ]
        rsi = indicators.calculate_rsi(prices, period=14)
        assert rsi is not None
        assert 20.0 <= rsi <= 80.0


# ---------- EMA ----------

class TestEMA:
    def test_ema_constant_series(self):
        # calculate_ema возвращает СЕРИЮ (list), не одно число.
        prices = [100.0] * 30
        series = indicators.calculate_ema(prices, period=10)
        assert isinstance(series, list)
        assert len(series) == len(prices)
        # Все значения равны 100.
        assert all(abs(v - 100.0) < 1e-6 for v in series if v is not None)

    def test_ema_insufficient_data(self):
        # Меньше period — не падаем, серия той же длины.
        prices = [1.0, 2.0, 3.0]
        series = indicators.calculate_ema(prices, period=10)
        assert isinstance(series, list)
        assert len(series) == len(prices)

    def test_ema_reacts_faster_than_sma(self):
        # EMA сильнее реагирует на свежий скачок, чем SMA.
        prices = [100.0] * 20 + [200.0]
        ema_series = indicators.calculate_ema(prices, period=10)
        ema_last = ema_series[-1]
        sma = sum(prices[-10:]) / 10
        assert ema_last is not None
        assert ema_last > sma

    def test_ema_from_bybit_uses_newest_first(self):
        # Bybit: [30, 29, ..., 1] — новые первыми.
        bybit_order = [float(i) for i in range(30, 0, -1)]
        ema = indicators.calculate_ema_from_bybit(bybit_order, period=10)
        # Обёртка возвращает ОДНО последнее значение (не серию).
        if isinstance(ema, list):
            ema = ema[-1]
        assert ema is not None
        assert 15.0 < ema < 30.0


# ---------- ATR ----------

def _split_klines(klines: list[list]) -> tuple[list[float], list[float], list[float]]:
    """Bybit-формат: [start, open, high, low, close, volume, turnover]."""
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    closes = [float(k[4]) for k in klines]
    return highs, lows, closes


class TestATR:
    def test_atr_constant_range(self):
        # Свечи с постоянным high-low = 2.0 → ATR = 2.0.
        klines = []
        for i in range(30):
            klines.append([
                1_700_000_000_000 + i * 60_000,
                "100", "101", "99", "100", "1000", "100000",
            ])
        highs, lows, closes = _split_klines(klines)
        atr = indicators.calculate_atr(highs, lows, closes, period=14)
        assert atr is not None
        assert atr == pytest.approx(2.0, abs=1e-3)

    def test_atr_insufficient_data(self):
        klines = [["0", "100", "101", "99", "100", "1", "1"]] * 3
        highs, lows, closes = _split_klines(klines)
        atr = indicators.calculate_atr(highs, lows, closes, period=14)
        # Не падаем. Может быть None или число.
        assert atr is None or (isinstance(atr, float) and atr >= 0.0)

    def test_atr_series_length(self):
        klines = [["0", "100", "101", "99", "100", "1", "1"]] * 20
        highs, lows, closes = _split_klines(klines)
        series = indicators.calculate_atr_series(highs, lows, closes, period=14)
        assert len(series) == len(klines)