"""
Тесты src/core/levels.py.

Стратегия: проверяем ИНВАРИАНТЫ, а не магические числа там, где это
возможно. Sanity-ассерты перед вызовом модуля — чтобы при падении
сразу было видно: данные плохие или код.
"""
import pytest

from src.core.levels import (
    Level,
    find_hh_hl,
    find_level_above,
    find_level_below,
    find_levels,
    is_level_valid,
    is_mirror_level,
)

# ==================== helpers ====================

def _kline(o, h, low, c, v=1.0) -> list:
    """Одна Bybit-свеча. Формат: [ts, open, high, low, close, volume]."""
    return [0, o, h, low, c, v]


def _klines_from_chrono(*candles: tuple) -> list[list]:
    """
    Хронологический порядок (старые → новые) → Bybit-порядок
    (новые первые). Каждый элемент — (o, h, l, c).
    """
    rows = [_kline(*c) for c in candles]
    return list(reversed(rows))


def _flat_candle(price: float, wick: float = 0.1, v: float = 1.0) -> tuple:
    """Свеча-«плато» около цены."""
    return (price, price + wick, price - wick, price)


# ==================== _find_extreme_pivots (через find_hh_hl) ====================

def test_find_hh_hl_short_data_safe():
    klines = _klines_from_chrono((1, 1.1, 0.9, 1))
    s = find_hh_hl(klines)
    assert s.direction == "side"
    assert s.last_high is None
    assert s.last_low is None
    assert not (s.hh or s.hl or s.lh or s.ll)


def test_find_hh_hl_empty_safe():
    s = find_hh_hl([])
    assert s.direction == "side"


# ==================== find_hh_hl: восходящий ====================

def _make_uptrend():
    """
    Явный восходящий зигзаг:
      low1=100, high1=110, low2=105 (HL), high2=120 (HH), low3=115 (HL).
    Строим так, чтобы пивоты были очевидными (минимум 4 бара между).
    """
    # Хронологически:
    #   старт 102
    #   low 100   → пивот low
    #   вверх
    #   high 110  → пивот high
    #   вниз
    #   low 105   → пивот low (HL)
    #   вверх
    #   high 120  → пивот high (HH)
    #   вниз
    #   low 115   → пивот low (HL)
    #   вверх
    #   close 118
    chrono = [
        (102, 102.5, 101.5, 102),  # 0
        (101, 101.5, 100.5, 101),  # 1
        (100.5, 100.6, 100.0, 100.2),  # 2  ← low A = 100
        (101, 102, 100.8, 101.8),  # 3
        (103, 104, 102.5, 103.5),  # 4
        (105, 107, 104.5, 106),    # 5
        (108, 110, 107.5, 109.5),  # 6  ← high A = 110
        (109, 109.5, 108, 108.5),  # 7
        (107, 108, 106, 106.5),    # 8
        (106, 106.5, 105.2, 105.5),# 9
        (105.5, 105.6, 105.0, 105.2),  # 10 ← low B = 105 (HL)
        (106, 108, 105.8, 107.5),  # 11
        (110, 112, 109.5, 111.5),  # 12
        (115, 118, 114.5, 117.5),  # 13
        (119, 120, 118.5, 119.5),  # 14 ← high B = 120 (HH)
        (118, 118.5, 117, 117.5),  # 15
        (117, 117.5, 116, 116.5),  # 16
        (116.5, 116.6, 115.5, 115.8),  # 17
        (115.5, 115.6, 115.0, 115.2),  # 18 ← low C = 115 (HL)
        (116, 117, 115.8, 116.8),  # 19
        (117.5, 118, 117, 117.8),  # 20
    ]
    return _klines_from_chrono(*chrono)


def test_find_hh_hl_uptrend():
    klines = _make_uptrend()
    s = find_hh_hl(klines, lookback=len(klines),
                   pivots_left=2, pivots_right=2)
    assert s.direction == "up", f"HH={s.hh} HL={s.hl} LH={s.lh} LL={s.ll}"
    assert s.hh is True
    assert s.hl is True
    assert s.last_high == pytest.approx(120.0)
    assert s.prev_high == pytest.approx(110.0)
    assert s.last_low == pytest.approx(115.0)
    assert s.prev_low == pytest.approx(105.0)


def _make_downtrend():
    """
    Явный нисходящий зигзаг:
      high1=120, low1=110, high2=115 (LH), low2=100 (LL), high3=105 (LH).
    """
    chrono = [
        (118, 118.5, 117.5, 118),
        (119, 119.5, 118.5, 119),
        (120, 120.5, 119.5, 120.2),  # high A = 120
        (119, 119.5, 118, 118.5),
        (117, 117.5, 116, 116.5),
        (115, 115.5, 114, 114.5),
        (113, 113.5, 112, 112.5),
        (111, 111.5, 110.5, 110.8),
        (110.5, 110.6, 110.0, 110.2),  # low A = 110
        (110, 111, 109.5, 110.5),
        (112, 113, 111.5, 112.5),
        (114, 115, 113.5, 114.5),      # high B = 115 (LH)
        (114, 114.5, 113, 113.5),
        (112, 112.5, 111, 111.5),
        (110, 110.5, 109, 109.5),
        (108, 108.5, 107, 107.5),
        (105, 105.5, 104, 104.5),
        (102, 102.5, 101, 101.5),
        (100, 100.5, 99.5, 99.8),
        (100.5, 100.6, 100.0, 100.2),  # low B = 100 (LL)
        (101, 102, 100.8, 101.5),
        (103, 104, 102.5, 103.5),
        (104.5, 105, 104, 104.8),      # high C = 105 (LH)
        (103.5, 104, 102.5, 103),
        (102, 102.5, 101, 101.5),
    ]
    return _klines_from_chrono(*chrono)


def test_find_hh_hl_downtrend():
    klines = _make_downtrend()
    s = find_hh_hl(klines, lookback=len(klines),
                   pivots_left=2, pivots_right=2)
    assert s.direction == "down", f"HH={s.hh} HL={s.hl} LH={s.lh} LL={s.ll}"
    assert s.lh is True
    assert s.ll is True


def test_find_hh_hl_sideways():
    # Плоский шум: пивоты примерно на одной высоте → side
    chrono = [_flat_candle(100.0, wick=0.5) for _ in range(30)]
    # Чуть-чуть варьируем, чтобы были локальные экстремумы
    for i, p in enumerate([100, 100.2, 100.1, 100.3, 100.0, 100.2, 100.1,
                           100.0, 100.2, 100.1, 100.3, 100.0, 100.2]):
        chrono[i] = (p, p + 0.5, p - 0.5, p)
    klines = _klines_from_chrono(*chrono)
    s = find_hh_hl(klines, lookback=len(klines),
                   pivots_left=2, pivots_right=2)
    assert s.direction == "side"


# ==================== find_levels: поиск уровней ====================

def _make_candles_with_support_bounces(support: float, bounces: int,
                                       top: float = 120.0):
    """
    Строит свечи, где цена bounces раз отбивается от `support`.
    Между отскоками цена уходит наверх к `top`.
    """
    chrono: list[tuple] = []
    # Начальный уровень
    chrono.append(_flat_candle(top, wick=0.5))
    for _ in range(bounces):
        # Спуск
        chrono.append((top - 5, top - 4, top - 6, top - 5))
        chrono.append((top - 10, top - 9, top - 11, top - 10))
        # Отбой от support (свеча-молот: длинный нижний хвост)
        chrono.append((support + 1, support + 1.2, support - 0.5, support + 0.8))
        # Подъём
        chrono.append((top - 10, top - 9, top - 11, top - 10))
        chrono.append((top - 5, top - 4, top - 6, top - 5))
        chrono.append(_flat_candle(top, wick=0.5))
    return _klines_from_chrono(*chrono)


def test_find_levels_detects_support():
    klines = _make_candles_with_support_bounces(support=100.0, bounces=3)
    levels = find_levels(
        klines, source_tf="240",
        tolerance_pct=0.005,
        min_touches=2,
        worn_touches=5,
        lookback=len(klines),
        cluster_eps_pct=0.005,
        pivots_left=1, pivots_right=1,
    )
    assert len(levels) >= 1
    # Уровень около 99.5 (низ свечи-отбоя в хелпере) должен быть найден.
    # Допуск ±1% — потому что хелпер даёт low = support - 0.5.
    near_support = [
        lv for lv in levels
        if abs(lv.price - 99.5) / 99.5 < 0.01
    ]
    assert near_support, f"уровни: {[lv.price for lv in levels]}"
    lv = near_support[0]
    assert lv.touches >= 2
    assert lv.source_tf == "240"


def test_find_levels_marks_worn_after_many_touches():
    klines = _make_candles_with_support_bounces(support=100.0, bounces=6)
    levels = find_levels(
        klines, source_tf="240",
        tolerance_pct=0.005,
        min_touches=2,
        worn_touches=5,
        lookback=len(klines),
        cluster_eps_pct=0.005,
        pivots_left=1, pivots_right=1,
    )
    near_support = [
        lv for lv in levels
        if abs(lv.price - 99.5) / 99.5 < 0.01
    ]
    assert near_support, f"уровни: {[lv.price for lv in levels]}"
    assert near_support[0].is_worn is True


def test_find_levels_empty_safe():
    assert find_levels([], source_tf="240") == []


def test_find_levels_short_data_safe():
    klines = _klines_from_chrono(*[_flat_candle(100.0) for _ in range(2)])
    assert find_levels(klines, source_tf="240") == []


def test_find_levels_bad_pivots_params():
    klines = _klines_from_chrono(*[_flat_candle(100.0) for _ in range(20)])
    with pytest.raises(ValueError):
        find_levels(klines, source_tf="240", pivots_left=0)


def test_find_levels_sorted_by_strength():
    klines = _make_candles_with_support_bounces(support=100.0, bounces=5)
    levels = find_levels(
        klines, source_tf="240",
        tolerance_pct=0.005, min_touches=2, worn_touches=10,
        lookback=len(klines), cluster_eps_pct=0.005,
        pivots_left=1, pivots_right=1,
    )
    strengths = [lv.strength for lv in levels]
    assert strengths == sorted(strengths, reverse=True)


def test_find_levels_source_tf_affects_strength():
    klines = _make_candles_with_support_bounces(support=100.0, bounces=3)
    lv_240 = find_levels(klines, source_tf="240",
                         tolerance_pct=0.005, min_touches=2,
                         lookback=len(klines), cluster_eps_pct=0.005,
                         pivots_left=1, pivots_right=1)
    lv_d = find_levels(klines, source_tf="D",
                       tolerance_pct=0.005, min_touches=2,
                       lookback=len(klines), cluster_eps_pct=0.005,
                       pivots_left=1, pivots_right=1)
    assert lv_240 and lv_d
    # На D strength должен быть выше при прочих равных
    assert lv_d[0].strength >= lv_240[0].strength


# ==================== is_mirror_level ====================

def test_is_mirror_level_true_when_sides_flip():
    """
    Строим окно, где:
      - старые свечи (индекс по Bybit > mid): цена ПОД уровнем
        (подходы снизу = тесты как сопротивление);
      - свежие свечи (индекс < mid): цена НАД уровнем, но иногда
        спускается к зоне (подходы сверху = тесты как поддержка).
    """
    level = 100.0
    # Bybit-порядок: [0..n-1], свежие первыми. Значит свежие — в начале.
    # Свежие: цена 101-105, иногда тень достаёт до зоны.
    fresh = [
        (102, 102.5, 99.9, 102),    # close > zone → from_above
        (103, 103.5, 102, 103),
        (104, 104.5, 103, 104),
        (103, 103.5, 99.9, 103),    # close > zone → from_above
        (102, 102.5, 101, 102),
    ]
    # Старые: цена 95-99, иногда тень достаёт до зоны.
    old = [
        (98, 100.1, 97, 98),        # close < zone → from_below
        (97, 98, 96, 97),
        (96, 97, 95, 96),
        (98, 100.1, 97, 98),        # close < zone → from_below
        (97, 98, 96, 97),
    ]
    candles = [
        {"open": o, "high": h, "low": low, "close": c, "volume": 1.0}
        for (o, h, low, c) in fresh + old
    ]
    assert is_mirror_level(candles, level) is True


def test_is_mirror_level_false_when_same_side():
    level = 100.0
    # Все свечи — подходы сверху. Зеркальности нет.
    candles = [
        {"open": 102, "high": 102.5, "low": 99.9, "close": 102, "volume": 1.0}
        for _ in range(10)
    ]
    assert is_mirror_level(candles, level) is False


def test_is_mirror_level_short_data_safe():
    candles = [{"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1}]
    assert is_mirror_level(candles, 100.0) is False


# ==================== find_level_above / find_level_below ====================

def _make_level(price: float, touches: int = 3, mirror: bool = False) -> Level:
    tol = price * 0.0015
    return Level(
        price=price, price_low=price - tol, price_high=price + tol,
        touches=touches, is_mirror=mirror, is_worn=False,
        source_tf="240", strength=touches,
    )


def test_find_level_above():
    levels = [_make_level(90), _make_level(110), _make_level(120)]
    lv = find_level_above(levels, price=100.0)
    assert lv is not None
    assert lv.price == pytest.approx(110.0)


def test_find_level_above_none():
    levels = [_make_level(90), _make_level(95)]
    assert find_level_above(levels, price=100.0) is None


def test_find_level_below():
    levels = [_make_level(90), _make_level(95), _make_level(120)]
    lv = find_level_below(levels, price=100.0)
    assert lv is not None
    assert lv.price == pytest.approx(95.0)


def test_find_level_below_none():
    levels = [_make_level(110), _make_level(120)]
    assert find_level_below(levels, price=100.0) is None


# ==================== is_level_valid ====================

def test_is_level_valid_ok():
    lv = _make_level(100.0, touches=3, mirror=False)
    assert is_level_valid(lv, min_touches=2) is True


def test_is_level_valid_too_few_touches():
    lv = _make_level(100.0, touches=1)
    assert is_level_valid(lv, min_touches=2) is False


def test_is_level_valid_worn_blocks_by_default():
    tol = 100.0 * 0.0015
    lv = Level(price=100.0, price_low=100 - tol, price_high=100 + tol,
               touches=6, is_mirror=False, is_worn=True,
               source_tf="240", strength=5)
    assert is_level_valid(lv) is False
    assert is_level_valid(lv, allow_worn=True) is True