"""Этап 9: строгое поглощение.

Условия строгости: тело ≥ 1.5× тела предыдущей свечи и «мешающая» тень
≤ 0.5 собственного тела. Слабое поглощение по-прежнему распознаётся, но
даёт половину веса паттерна.
"""
from __future__ import annotations

import time

from src.core import analyzer
from src.core.analyzer import detect_pattern, is_strict_engulfing


def _candle(o: float, h: float, low: float, c: float) -> dict[str, float]:
    return {"open": o, "high": h, "low": low, "close": c, "volume": 1000.0}


def _raw(rows_newest_first: list[dict[str, float]]) -> list[list[str]]:
    """Свечи-словари → сырой формат Bybit (новые первыми)."""
    out: list[list[str]] = []
    for i, row in enumerate(rows_newest_first):
        out.append([
            str(1_700_000_000_000 - i * 60_000),
            str(row["open"]), str(row["high"]), str(row["low"]),
            str(row["close"]), str(row["volume"]), "0",
        ])
    return out


def _base_rows(n: int = 60) -> list[dict[str, float]]:
    return [_candle(100.0, 100.5, 99.5, 100.0) for _ in range(n)]


def _evaluate(rows: list[dict[str, float]], direction: str = "up") -> dict:
    return analyzer.evaluate_alert(
        symbol="BTCUSDT",
        level=50.0,
        direction=direction,
        current_price=100.0,
        alert_created_at=time.time(),
        klines_15m=_raw(rows),
        klines_4h=_raw(_base_rows()),
    )


class TestDetectPattern:
    def test_bullish_engulfing_detected(self):
        prev = _candle(100.0, 100.2, 99.4, 99.6)     # маленькая медвежья
        last = _candle(99.5, 100.6, 99.3, 100.5)     # поглощает
        assert detect_pattern(last, prev, "up") == "bullish_engulfing"

    def test_bearish_engulfing_detected(self):
        prev = _candle(100.0, 100.6, 99.8, 100.4)    # маленькая бычья
        last = _candle(100.5, 100.7, 99.2, 99.4)     # поглощает вниз
        assert detect_pattern(last, prev, "down") == "bearish_engulfing"

    def test_direction_any_has_no_engulfing(self):
        prev = _candle(100.0, 100.2, 99.4, 99.6)
        last = _candle(99.5, 100.6, 99.3, 100.5)
        assert detect_pattern(last, prev, "any") is None


class TestStrictEngulfing:
    def test_strict_bullish(self):
        # тело 1.0 против 0.4 = 2.5×, верхняя тень 0.1 ≤ 0.5×1.0
        prev = _candle(100.0, 100.1, 99.6, 99.6)
        last = _candle(99.5, 100.6, 99.4, 100.5)
        assert is_strict_engulfing(last, prev, "up") is True

    def test_small_body_is_not_strict(self):
        # тело 0.25 против 0.4 = 0.6× — меньше 1.5×
        prev = _candle(100.0, 100.1, 99.6, 99.6)
        last = _candle(99.5, 100.0, 99.3, 99.75)
        assert is_strict_engulfing(last, prev, "up") is False

    def test_long_upper_wick_is_not_strict(self):
        # тело 1.0, но верхняя тень 0.7 > 0.5
        prev = _candle(100.0, 100.1, 99.6, 99.6)
        last = _candle(99.5, 101.2, 99.4, 100.5)
        assert is_strict_engulfing(last, prev, "up") is False

    def test_strict_bearish_mirror(self):
        prev = _candle(100.0, 100.4, 99.9, 100.4)
        last = _candle(100.5, 100.6, 99.4, 99.5)
        assert is_strict_engulfing(last, prev, "down") is True

    def test_bearish_long_lower_wick_is_not_strict(self):
        prev = _candle(100.0, 100.4, 99.9, 100.4)
        last = _candle(100.5, 100.6, 98.8, 99.5)
        assert is_strict_engulfing(last, prev, "down") is False


class TestEngulfingScoring:
    """Слабое поглощение даёт половину веса паттерна, строгое — полный вес."""

    def _rows(self, last: dict[str, float], prev: dict[str, float]) -> list[dict[str, float]]:
        rows = _base_rows()
        rows[1] = last
        rows[2] = prev
        return rows

    def test_strict_gets_full_pattern_score(self):
        prev = _candle(100.0, 100.1, 99.6, 99.6)
        last = _candle(99.5, 100.6, 99.4, 100.5)
        res = _evaluate(self._rows(last, prev))
        assert res["pattern"] == "bullish_engulfing"
        assert res["pattern_strict"] is True
        assert res["pattern_score"] == analyzer.WEIGHTS["pattern"]

    def test_loose_gets_half_pattern_score(self):
        # Поглощение есть (закрытие выше открытия предыдущей), но тело
        # 0.5 против 0.5 — меньше 1.5×, значит паттерн «слабый».
        prev = _candle(100.5, 100.55, 99.95, 100.0)
        last = _candle(100.0, 100.6, 99.9, 100.5)
        res = _evaluate(self._rows(last, prev))
        assert res["pattern"] == "bullish_engulfing"
        assert res["pattern_strict"] is False
        assert res["pattern_score"] == (
            analyzer.WEIGHTS["pattern"] * analyzer.THRESHOLDS["engulfing_loose_mult"]
        )

    def test_pinbar_is_not_marked_strict(self):
        # Пин-бар: pattern_strict остаётся None — поле только про поглощения.
        prev = _candle(100.0, 100.2, 99.8, 100.0)
        last = _candle(100.0, 100.02, 98.0, 99.9)
        res = _evaluate(self._rows(last, prev))
        assert res["pattern"] == "bullish_pinbar"
        assert res["pattern_strict"] is None
