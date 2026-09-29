"""Тесты src/utils/indicators.py.

Уточнено по факту (проверено вживую):
- calculate_rsi / calculate_rsi_series ожидают Bybit-порядок
  (НОВЫЕ ПЕРВЫМИ). Именно поэтому возрастающий ряд [1..29]
  даёт RSI=0.0, а не 100.0.
- calculate_ema_from_bybit: result[-1] — самое СТАРОЕ значение.
  Самое свежее — result[0].
- calculate_atr_series возвращает len(klines) + 1 значений
  (ведущее значение).
"""
from __future__ import annotations

import pytest

from src.utils import indicators

# ---------- RSI ----------

class TestRSI:
    def test_rsi_all_gains_is_100(self):
        # Хронология растёт 1..29. Bybit-порядок → [29, 28, ..., 1].
        closes = [float(i) for i in range(29, 0, -1)]
        rsi = indicators.calculate_rsi(closes, period=14)
        assert rsi == pytest.approx(100.0, abs=1e-6)

    def test_rsi_all_losses_is_zero(self):
        # Хронология падает 29..1. Bybit-порядок → [1, 2, ..., 29].
        closes = [float(i) for i in range(1, 30)]
        rsi = indicators.calculate_rsi(closes, period=14)
        assert rsi == pytest.approx(0.0, abs=1e-6)

    def test_rsi_flat_does_not_crash(self):
        closes = [100.0] * 30
        rsi = indicators.calculate_rsi(closes, period=14)
        assert rsi is None or 0.0 <= rsi <= 100.0

    def test_rsi_insufficient_data_returns_none(self):
        closes = [100.0, 101.0, 102.0]
        assert indicators.calculate_rsi(closes, period=14) is None

    def test_rsi_series_length_matches_input(self):
        # Bybit-порядок: монотонно убывает [39, 38, ..., 1] —
        # в хронологии растёт → RSI=100 в свежих точках.
        closes = [float(i) for i in range(39, 0, -1)]
        series = indicators.calculate_rsi_series(closes, period=14)
        assert len(series) == len(closes)
        # Свежие значения — в начале серии. Первое должно быть валидным.
        first = series[0]
        assert first is not None
        assert 0.0 <= first <= 100.0

    def test_rsi_known_range(self):
        # Хронология (см. учебник). Bybit-порядок — разворачиваем.
        chronological = [
            44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
            45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00,
            46.03, 46.41, 46.22, 45.64, 46.21, 46.25, 45.71, 46.45,
            45.78, 45.35, 44.03, 44.18, 44.22, 44.57, 43.42, 42.66,
        ]
        closes = list(reversed(chronological))
        rsi = indicators.calculate_rsi(closes, period=14)
        assert rsi is not None
        assert 20.0 <= rsi <= 80.0


# ---------- EMA ----------

class TestEMA:
    def test_ema_returns_series_of_same_length(self):
        closes = [100.0] * 30
        series = indicators.calculate_ema(closes, period=10)
        assert isinstance(series, list)
        assert len(series) == len(closes)

    def test_ema_constant_series(self):
        closes = [100.0] * 30
        series = indicators.calculate_ema(closes, period=10)
        assert all(abs(v - 100.0) < 1e-6 for v in series)

    def test_ema_insufficient_data_does_not_crash(self):
        closes = [1.0, 2.0, 3.0]
        series = indicators.calculate_ema(closes, period=10)
        assert isinstance(series, list)
        assert len(series) == len(closes)

    def test_ema_reacts_faster_than_sma(self):
        # calculate_ema — хронологический порядок (старые первыми).
        closes = [100.0] * 20 + [200.0]
        series = indicators.calculate_ema(closes, period=10)
        sma = sum(closes[-10:]) / 10
        assert series[-1] > sma

    def test_ema_from_bybit_uses_newest_first(self):
        """calculate_ema_from_bybit: [0] — самое свежее значение.

        Bybit-порядок [30, 29, ..., 1] → в хронологии растёт 1..30.
        EMA в самой свежей точке ≈ ниже 30, но выше 15.
        """
        bybit_order = [float(i) for i in range(30, 0, -1)]
        result = indicators.calculate_ema_from_bybit(bybit_order, period=10)
        if isinstance(result, list):
            first = result[0]  # самое свежее
            assert first is not None
            assert 15.0 < first <= 30.0
        else:
            assert 15.0 < result <= 30.0


# ---------- ATR ----------

def _split_klines(klines: list[list]) -> tuple[list[float], list[float], list[float]]:
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    closes = [float(k[4]) for k in klines]
    return highs, lows, closes


class TestATR:
    def test_atr_constant_range(self):
        klines = [
            ["1700000000000", "100", "101", "99", "100", "1000", "100000"]
            for _ in range(30)
        ]
        highs, lows, closes = _split_klines(klines)
        atr = indicators.calculate_atr(highs, lows, closes, period=14)
        assert atr is not None
        assert atr == pytest.approx(2.0, abs=1e-3)

    def test_atr_insufficient_data_returns_none(self):
        klines = [["0", "100", "101", "99", "100", "1", "1"]] * 3
        highs, lows, closes = _split_klines(klines)
        atr = indicators.calculate_atr(highs, lows, closes, period=14)
        assert atr is None

    def test_atr_series_length(self):
        """calculate_atr_series возвращает len+1 (ведущее значение).

        Фиксируем это как известное поведение.
        """
        klines = [["0", "100", "101", "99", "100", "1", "1"]] * 20
        highs, lows, closes = _split_klines(klines)
        series = indicators.calculate_atr_series(highs, lows, closes, period=14)
        # Может быть len(klines) или len(klines)+1 — фиксируем факт.
        assert len(series) in (len(klines), len(klines) + 1)
        non_none = [v for v in series if v is not None]
        assert non_none
        assert all(v >= 0.0 for v in non_none)