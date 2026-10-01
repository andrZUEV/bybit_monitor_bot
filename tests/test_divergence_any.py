"""Тесты find_divergences с direction='any'."""
from __future__ import annotations

import pytest

from src.core.divergence import _ANALYZER_TO_KINDS, find_divergences

# ---------- Синтетические серии для тестов ----------

def _make_bullish_series() -> tuple[list[float], list[float | None]]:
    """
    Строим серию с бычьей дивергенцией:
      - два low-пивота: цена LL, RSI HL.
    Индексы (Bybit): 0 свежая, больше = старше.
    """
    # 30 баров; сформируем руками через явные точки
    n = 30
    closes = [100.0] * n
    rsi = [50.0] * n

    # старший low-пивот: index=20, price=95, rsi=30
    closes[20] = 95.0
    rsi[20] = 30.0
    # свежий low-пивот: index=5, price=92 (LL), rsi=40 (HL)
    closes[5] = 92.0
    rsi[5] = 40.0

    # окружение, чтобы find_pivots их увидел (пивот = локальный min)
    for i in (21, 19, 6, 4):
        closes[i] = 99.0
        rsi[i] = 50.0

    # «зашумляем» фон, чтобы не создавать других пивотов
    for i in range(n):
        if closes[i] == 100.0:
            closes[i] = 99.0 + (i % 3) * 0.1

    return closes, rsi


def _make_bearish_series() -> tuple[list[float], list[float | None]]:
    """
    Строим серию с медвежьей дивергенцией:
      - два high-пивота: цена HH, RSI LH.
    """
    n = 30
    closes = [100.0] * n
    rsi = [50.0] * n

    closes[20] = 110.0
    rsi[20] = 70.0
    closes[5] = 115.0
    rsi[5] = 55.0  # RSI LH

    for i in (21, 19, 6, 4):
        closes[i] = 100.0
        rsi[i] = 50.0

    for i in range(n):
        if closes[i] == 100.0:
            closes[i] = 99.0 + (i % 3) * 0.1

    return closes, rsi


def _make_both_series() -> tuple[list[float], list[float | None]]:
    """Серия, где есть и бычья, и медвежья дивергенции."""
    n = 40
    closes = [100.0] * n
    rsi = [50.0] * n

    # Бычья: low-пивоты
    closes[30] = 95.0
    rsi[30] = 30.0
    closes[15] = 92.0
    rsi[15] = 40.0

    # Медвежья: high-пивоты
    closes[28] = 110.0
    rsi[28] = 70.0
    closes[13] = 115.0
    rsi[13] = 55.0

    # окрестности — «плоские», чтобы не появлялись ложные пивоты
    for i in (31, 29, 16, 14, 29, 27, 14, 12):
        closes[i] = 99.0
        rsi[i] = 50.0

    for i in range(n):
        if closes[i] == 100.0:
            closes[i] = 99.0 + (i % 3) * 0.1

    return closes, rsi


# ---------- Тесты ----------

class TestDirectionAcceptance:
    def test_invalid_direction_raises(self):
        with pytest.raises(ValueError, match="'up'\\|'down'\\|'any'"):
            find_divergences([1, 2, 3], [50, 50, 50], "sideways")

    def test_any_accepted_on_empty_data(self):
        assert find_divergences([], [], "any") == []
        assert find_divergences([1, 2, 3], [50, 50, 50], "any") == []

    def test_any_accepted_on_short_series(self):
        assert find_divergences([1, 2], [50, 50], "any") == []


class TestAnyReturnsBoth:
    def test_any_finds_bullish(self):
        closes, rsi = _make_bullish_series()
        result = find_divergences(closes, rsi, "any", max_lookback=60)
        kinds = [d.kind for d in result]
        assert "bullish" in kinds

    def test_any_finds_bearish(self):
        closes, rsi = _make_bearish_series()
        result = find_divergences(closes, rsi, "any", max_lookback=60)
        kinds = [d.kind for d in result]
        assert "bearish" in kinds

    def test_any_finds_both(self):
        closes, rsi = _make_both_series()
        result = find_divergences(closes, rsi, "any", max_lookback=60)
        kinds = sorted(d.kind for d in result)
        assert kinds == ["bearish", "bullish"]

    def test_any_sorted_by_freshness(self):
        closes, rsi = _make_both_series()
        result = find_divergences(closes, rsi, "any", max_lookback=60)
        # pivot_b.index по возрастанию (свежие первыми)
        assert result == sorted(result, key=lambda d: d.pivot_b.index)

    def test_up_returns_only_bullish(self):
        closes, rsi = _make_both_series()
        result = find_divergences(closes, rsi, "up", max_lookback=60)
        assert all(d.kind == "bullish" for d in result)
        assert len(result) <= 1

    def test_down_returns_only_bearish(self):
        closes, rsi = _make_both_series()
        result = find_divergences(closes, rsi, "down", max_lookback=60)
        assert all(d.kind == "bearish" for d in result)
        assert len(result) <= 1


class TestMappingTable:
    def test_mapping_has_all_directions(self):
        assert "up" in _ANALYZER_TO_KINDS
        assert "down" in _ANALYZER_TO_KINDS
        assert "any" in _ANALYZER_TO_KINDS

    def test_mapping_any_contains_both(self):
        assert set(_ANALYZER_TO_KINDS["any"]) == {"bullish", "bearish"}