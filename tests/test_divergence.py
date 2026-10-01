"""
Тесты src/core/divergence.py.

Синтетические данные, без сети. Массивы — в Bybit-порядке (новые первые).
"""
import pytest

from src.core.divergence import (
    find_divergences,
    find_pivots,
    has_divergence,
)

# ==================== helpers ====================

def _series(*values):
    """Строит массив в Bybit-порядке (новые первые) из хронологического."""
    return list(reversed(values))


# ==================== find_pivots ====================

def test_find_pivots_empty_on_short_data():
    closes = [1.0, 2.0]
    rsi = [50.0, 50.0]
    assert find_pivots(closes, rsi, left=2, right=2) == []

def test_find_pivots_high():
    # Хронологически: 1,2,3,4,5,4,3,2 → пик 5.
    closes = _series(1, 2, 3, 4, 5, 4, 3, 2)
    rsi = [50.0] * len(closes)
    pivots = find_pivots(closes, rsi, left=2, right=2)
    assert any(p.kind == "high" and p.price == 5.0 for p in pivots)

def test_find_pivots_low():
    closes = _series(5, 4, 3, 2, 1, 2, 3, 4)
    rsi = [50.0] * len(closes)
    pivots = find_pivots(closes, rsi, left=2, right=2)
    assert any(p.kind == "low" and p.price == 1.0 for p in pivots)

def test_find_pivots_skips_none_rsi():
    closes = _series(1, 2, 3, 4, 5, 4, 3, 2)
    rsi = [None] * len(closes)
    assert find_pivots(closes, rsi, left=2, right=2) == []

def test_find_pivots_bad_params():
    with pytest.raises(ValueError):
        find_pivots([1, 2, 3], [1, 2, 3], left=0, right=2)

def test_find_pivots_sorted_fresh_first():
    # Несколько пиков; проверяем, что список идёт от свежих к старым.
    closes = _series(1, 3, 1, 4, 1, 5, 1, 4, 1, 3, 1)
    rsi = [50.0] * len(closes)
    pivots = find_pivots(closes, rsi, left=1, right=1)
    assert all(pivots[i].index <= pivots[i + 1].index
               for i in range(len(pivots) - 1))


# ==================== find_divergences ====================

def _make_bullish_divergence():
    """
    Хронологически:
      price low #1 ≈ 100 при RSI ≈ 20,
      price low #2 ≈ 95  при RSI ≈ 35 (LL цены, HL RSI).
    """
    closes_chrono = [120, 110, 100, 105, 108, 106, 100, 96, 95, 98, 102, 104, 105]
    rsi_chrono    = [60,  45,  20,  35,  50,  45,  30,  35, 35, 45, 55,  60,  62]
    return _series(*closes_chrono), _series(*rsi_chrono)


def test_bullish_divergence_found():
    closes, rsi = _make_bullish_divergence()
    divs = find_divergences(
        closes, rsi, direction="up",
        min_rsi_gap=5.0, min_price_gap_pct=0.0,
        max_lookback=len(closes), pivots_left=1, pivots_right=1,
    )
    assert len(divs) == 1
    d = divs[0]
    assert d.kind == "bullish"
    assert d.pivot_b.price < d.pivot_a.price   # LL
    assert d.pivot_b.rsi > d.pivot_a.rsi       # HL
    assert d.pivot_a.index > d.pivot_b.index   # a старше
    assert d.rsi_gap >= 5.0
    assert d.age_bars == d.pivot_b.index


def test_bearish_divergence_found():
    # Зеркальная картина: цена HH, RSI LH.
    closes_chrono = [80, 90, 100, 95, 92, 94, 100, 104, 105, 102, 98, 96, 95]
    rsi_chrono    = [40, 55, 80,  65, 50, 55, 70, 65, 65, 55, 45, 40, 38]
    closes = _series(*closes_chrono)
    rsi = _series(*rsi_chrono)

    divs = find_divergences(
        closes, rsi, direction="down",
        min_rsi_gap=5.0, min_price_gap_pct=0.0,
        max_lookback=len(closes), pivots_left=1, pivots_right=1,
    )
    assert len(divs) == 1
    d = divs[0]
    assert d.kind == "bearish"
    assert d.pivot_b.price > d.pivot_a.price
    assert d.pivot_b.rsi < d.pivot_a.rsi


def test_no_divergence_when_price_and_rsi_agree():
    # Цена LL и RSI тоже LL → это НЕ дивергенция.
    closes_chrono = [120, 110, 100, 105, 108, 106, 100, 96, 95, 98, 102, 104, 105]
    rsi_chrono    = [60,  45,  40,  35,  30,  25,  22,  20, 18, 25, 35,  45,  55]
    closes = _series(*closes_chrono)
    rsi = _series(*rsi_chrono)
    divs = find_divergences(
        closes, rsi, "up",
        min_rsi_gap=5.0, min_price_gap_pct=0.0,
        max_lookback=len(closes), pivots_left=1, pivots_right=1,
    )
    assert divs == []


def test_rsi_gap_below_threshold_rejected():
    # Цена LL, RSI чуть-HL, но gap < min_rsi_gap.
    closes_chrono = [120, 110, 100, 105, 108, 106, 100, 96, 95, 98, 102, 104, 105]
    rsi_chrono    = [60,  45,  30,  35,  40,  45,  30,  32, 33, 45, 55,  60,  62]
    closes = _series(*closes_chrono)
    rsi = _series(*rsi_chrono)
    divs = find_divergences(
        closes, rsi, "up",
        min_rsi_gap=10.0, min_price_gap_pct=0.0,
        max_lookback=len(closes), pivots_left=1, pivots_right=1,
    )
    assert divs == []


def test_max_lookback_excludes_old_pivot():
    closes, rsi = _make_bullish_divergence()
    divs = find_divergences(
        closes, rsi, "up",
        min_rsi_gap=5.0, min_price_gap_pct=0.0,
        max_lookback=3, pivots_left=1, pivots_right=1,
    )
    assert divs == []


def test_bad_direction_raises():
    with pytest.raises(ValueError):
        find_divergences([1, 2, 3], [1, 2, 3], direction="side")


def test_none_rsi_does_not_crash():
    closes = list(range(50, 0, -1))
    rsi = [None] * len(closes)
    divs = find_divergences(
        closes, rsi, "up",
        max_lookback=len(closes), pivots_left=1, pivots_right=1,
    )
    assert divs == []


def test_empty_rsi_series_safe():
    closes = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert find_divergences(closes, [], "up") == []


def test_short_data_safe():
    assert find_divergences([1.0], [50.0], "up") == []


def test_min_price_gap_filters_small_price_diff():
    """
    Два low-пивота с разницей 0.1%, RSI разошёлся сильно.
    При min_price_gap_pct=0.005 (0.5%) → дивергенция должна отсечься,
    даже если по RSI условие выполнено.
    """
    closes_chrono = [
        120, 110,
        100,        # c2: low A = 100
        105, 108, 106, 105, 103,
        99.9,       # c8: low B = 99.9 (−0.1% от A) → LL цены
        102, 104, 105, 106,
    ]
    rsi_chrono = [
        60, 45,
        20,         # RSI на low A
        35, 45, 50, 45, 40,
        45,         # RSI на low B — выше (HL), gap = 25
        50, 55, 60, 62,
    ]
    closes = _series(*closes_chrono)
    rsi = _series(*rsi_chrono)

    # sanity-проверка: ровно 2 low-пивота с ожидаемыми ценами
    pivots = find_pivots(closes, rsi, left=1, right=1)
    lows = [p for p in pivots if p.kind == "low"]
    assert len(lows) == 2, f"ожидалось 2 low-пивота, найдено {lows}"
    assert lows[0].price == pytest.approx(99.9)    # свежий
    assert lows[1].price == pytest.approx(100.0)   # старый

    # с порогом 0.5% — отсекается
    divs = find_divergences(
        closes, rsi, "up",
        min_rsi_gap=5.0,
        min_price_gap_pct=0.005,
        max_lookback=len(closes), pivots_left=1, pivots_right=1,
    )
    assert divs == []

    # с порогом 0.0 — проходит (контроль)
    divs_ok = find_divergences(
        closes, rsi, "up",
        min_rsi_gap=5.0,
        min_price_gap_pct=0.0,
        max_lookback=len(closes), pivots_left=1, pivots_right=1,
    )
    assert len(divs_ok) == 1
    assert divs_ok[0].price_gap_pct == pytest.approx(0.001, abs=1e-6)


# ==================== has_divergence ====================

def test_has_divergence_true():
    closes, rsi = _make_bullish_divergence()
    assert has_divergence(
        closes, rsi, "up",
        min_rsi_gap=5.0, min_price_gap_pct=0.0,
        max_lookback=len(closes), pivots_left=1, pivots_right=1,
    ) is True


def test_has_divergence_false():
    closes = [1.0, 2.0, 3.0, 4.0, 5.0, 4.0, 3.0, 2.0, 1.0]
    rsi = [50.0] * len(closes)
    assert has_divergence(
        closes, rsi, "up",
        max_lookback=len(closes), pivots_left=1, pivots_right=1,
    ) is False


class TestDirectionParameter:
    def test_invalid_direction_raises(self):
        with pytest.raises(ValueError, match="'up'\\|'down'\\|'any'"):
            find_divergences([1, 2, 3], [50, 50, 50], "sideways")

    def test_any_accepted_on_empty(self):
        assert find_divergences([], [], "any") == []
        assert find_divergences([1, 2, 3], [50, 50, 50], "any") == []

    def test_up_down_still_work(self):
        # не должно ломаться
        find_divergences([], [], "up")
        find_divergences([], [], "down")