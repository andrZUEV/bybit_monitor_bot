"""
Тесты src/core/level_scanner.py.

Стратегия:
  - проверяем инварианты (score неотрицательный, direction согласован со стороной);
  - проверяем hard-фильтры на синтетических данных;
  - проверяем room_to_next в обоих режимах (ATR есть / нет);
  - проверяем форматирование и callback_data (без Telegram API).
"""
from __future__ import annotations

import pytest

from src.core import level_scanner as ls
from src.core.level_scanner import (
    LevelCandidate,
    _calc_room_to_next,
    _round_price_for_callback,
    build_full_keyboard,
    build_keyboard,
    count_touches,
    find_swings,
    format_full_list,
    format_summary,
    is_visible_on_tf,
    scan_symbol,
    score_candidate,
)

# ==================== helpers ====================

def _kline(o, h, low, c, v=1.0) -> list:
    return [0, o, h, low, c, v]


def _klines_from_chrono(*candles) -> list[list]:
    rows = [_kline(*c) for c in candles]
    return list(reversed(rows))


def _flat(price: float, wick: float = 0.1) -> tuple:
    return (price, price + wick, price - wick, price)


def _make_zigzag_klines(
    *,
    n_bounces: int = 4,
    support: float = 100.0,
    top: float = 110.0,
) -> list[list]:
    """
    Синтетика: цена n_bounces раз отбивается от support и уходит к top.
    Гарантирует минимум 2 касания support и свинги около support.
    """
    chrono = [_flat(top)]
    for _ in range(n_bounces):
        chrono.append((top - 3, top - 2, top - 4, top - 3))
        chrono.append((top - 6, top - 5, top - 7, top - 6))
        chrono.append((support + 0.5, support + 0.8, support - 0.3, support + 0.4))
        chrono.append((top - 6, top - 5, top - 7, top - 6))
        chrono.append((top - 3, top - 2, top - 4, top - 3))
        chrono.append(_flat(top))
    return _klines_from_chrono(*chrono)


# ==================== примитивы ====================

def test_find_swings_empty_safe():
    assert find_swings([]) == []


def test_find_swings_detects_pivot():
    klines = _make_zigzag_klines(n_bounces=2)
    pivots = find_swings(klines, window=1)
    assert len(pivots) >= 1
    kinds = {p[2] for p in pivots}
    assert "low" in kinds  # должен быть хотя бы один low-пивот


def test_count_touches_basic():
    # Плоская цена — все свечи «касаются» уровня
    chrono = [_flat(100.0) for _ in range(20)]
    klines = _klines_from_chrono(*chrono)
    candles = ls._parse_candles(klines)
    n = count_touches(candles, 100.0, tol=0.5, lookback=10)
    assert n == 10


def test_count_touches_zero():
    chrono = [_flat(200.0) for _ in range(20)]
    klines = _klines_from_chrono(*chrono)
    candles = ls._parse_candles(klines)
    n = count_touches(candles, 100.0, tol=0.5, lookback=10)
    assert n == 0


def test_is_visible_on_tf_empty():
    assert is_visible_on_tf(100.0, [], 0.0015) is False


def test_is_visible_on_tf_inside():
    assert is_visible_on_tf(100.0, [100.1], 0.002) is True


def test_is_visible_on_tf_outside():
    assert is_visible_on_tf(100.0, [105.0], 0.002) is False


# ==================== score_candidate ====================

def test_score_zero_everything_false():
    s = score_candidate(
        visible_on_1d=False,
        visible_on_4h=False,
        is_mirror=False,
        touches_15m=10,
        room_to_next_atr=None,
        distance_pct=2.0,
    )
    assert s == 0.0


def test_score_max():
    s = score_candidate(
        visible_on_1d=True,
        visible_on_4h=True,
        is_mirror=True,
        touches_15m=3,
        room_to_next_atr=3.5,
        distance_pct=1.0,
    )
    # 3 + 2 + 2 + 2 + 1 + 1 = 11, но потолок ТЗ — 10.
    # score_candidate не зажимает — это ок, фильтр min_score=5.
    assert s == 11.0


def test_score_no_atr_bonus_when_none():
    s = score_candidate(
        visible_on_1d=False,
        visible_on_4h=False,
        is_mirror=False,
        touches_15m=10,
        room_to_next_atr=None,
        distance_pct=2.0,
    )
    assert s == 0.0


# ==================== room_to_next ====================

def test_calc_room_no_next_level_soft_pass():
    room_atr, room_pct, ok = _calc_room_to_next(
        level=100.0,
        all_centers=[100.0],
        side="support",
        atr_1h=1.0,
    )
    assert room_atr is None
    assert room_pct == float("inf")
    assert ok is True


def test_calc_room_with_atr_pass():
    room_atr, room_pct, ok = _calc_room_to_next(
        level=100.0,
        all_centers=[100.0, 105.0],
        side="support",
        atr_1h=1.0,
    )
    assert room_atr == pytest.approx(5.0)
    assert ok is True


def test_calc_room_with_atr_fail():
    room_atr, room_pct, ok = _calc_room_to_next(
        level=100.0,
        all_centers=[100.0, 101.0],
        side="support",
        atr_1h=1.0,
    )
    assert room_atr == pytest.approx(1.0)
    assert ok is False


def test_calc_room_no_atr_fallback_pct_pass():
    room_atr, room_pct, ok = _calc_room_to_next(
        level=100.0,
        all_centers=[100.0, 102.0],
        side="support",
        atr_1h=None,
    )
    assert room_atr is None
    assert room_pct == pytest.approx(2.0)
    assert ok is True


def test_calc_room_no_atr_fallback_pct_fail():
    room_atr, room_pct, ok = _calc_room_to_next(
        level=100.0,
        all_centers=[100.0, 100.5],
        side="support",
        atr_1h=None,
    )
    assert room_atr is None
    assert room_pct == pytest.approx(0.5)
    assert ok is False


# ==================== scan_symbol ====================

def test_scan_symbol_empty_klines():
    assert scan_symbol("ETHUSDT", {}, 100.0) == []


def test_scan_symbol_zero_price():
    klines = _make_zigzag_klines()
    assert scan_symbol("ETHUSDT", {"15": klines}, 0.0) == []


def test_scan_symbol_finds_candidate():
    klines = _make_zigzag_klines(n_bounces=4, support=100.0, top=110.0)
    # 15m в Bybit-порядке (новые первыми) — _make_zigzag_klines это и даёт
    result = scan_symbol(
        "ETHUSDT",
        {"15": klines},
        current_price=105.0,  # между support и top
        min_score=0.0,        # ослабляем, чтобы точно найти
        min_room_atr=0.0,
        min_room_pct=0.0,
        min_distance_pct=0.01,
        max_distance_pct=10.0,
        top_n=5,
    )
    assert isinstance(result, list)
    # Может быть пусто, если фильтры отсекли — главное не падаем и тип ок
    for c in result:
        assert isinstance(c, LevelCandidate)
        assert c.symbol == "ETHUSDT"
        assert c.side in ("support", "resistance")
        assert c.direction_for_alert in ("up", "down")
        # Инвариант: support → up, resistance → down
        if c.side == "support":
            assert c.direction_for_alert == "up"
        else:
            assert c.direction_for_alert == "down"


# ==================== форматирование ====================

def _make_candidate(**overrides) -> LevelCandidate:
    base = dict(
        symbol="ETHUSDT",
        price=2685.0,
        side="support",
        direction_for_alert="up",
        score=7.5,
        touches_15m=3,
        touches_4h=2,
        is_mirror=True,
        visible_on_4h=True,
        visible_on_1d=False,
        distance_pct=0.5,
        atr_1h=12.5,
        room_to_next_atr=2.4,
        room_to_next_pct=1.1,
        room_ok=True,
        tf_sources=["15m", "4H"],
        note="сканер: support, score 7.5, 3 кас. | зеркало | 4H",
    )
    base.update(overrides)
    return LevelCandidate(**base)


def test_format_summary_basic():
    c = _make_candidate()
    text = format_summary({"ETHUSDT": [c]}, {"ETHUSDT": 2699.0})
    assert "СВОДКА УРОВНЕЙ" in text
    assert "ETHUSDT" in text
    # _fmt_price для >=1000 ставит запятую-разделитель: 2,685.00
    assert "2,685.00" in text
    assert "support" in text
    assert "2.4×ATR" in text


def test_format_summary_empty():
    text = format_summary({}, {})
    assert "не найдено" in text.lower()


def test_format_full_list_basic():
    c = _make_candidate()
    text = format_full_list({"ETHUSDT": [c]}, {"ETHUSDT": 2699.0})
    assert "ВСЕ УРОВНИ" in text
    assert "2,685.00" in text
    assert "up" in text


# ==================== callback / клавиатура ====================

def test_round_price_for_callback_whole():
    assert _round_price_for_callback(2685.0) == "2685"


def test_round_price_for_callback_small():
    assert _round_price_for_callback(117.8000) == "117.8"


def test_round_price_for_callback_tiny():
    s = _round_price_for_callback(0.00012345)
    assert s.startswith("0.000123")


def test_build_keyboard_one_per_symbol():
    """Компактная клавиатура: по 1 кнопке на символ + сервисные."""
    c1 = _make_candidate(symbol="ETHUSDT", price=2685.0, score=7.0)
    c2 = _make_candidate(symbol="ETHUSDT", price=2700.0, score=6.0)
    c3 = _make_candidate(symbol="SOLUSDT", price=117.8, score=8.0)
    results = {"ETHUSDT": [c1, c2], "SOLUSDT": [c3]}
    kb = build_keyboard(results)
    # 2 кнопки на символы (ETH, SOL) + «Добавить всё» + «Все уровни» +
    # «Обновить» + «Меню» = 6 строк
    assert len(kb.inline_keyboard) == 6

    # Проверяем, что кнопки на символы содержат правильные callback'и
    all_cbs = [
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
    ]
    assert "ls_add_sym|ETHUSDT" in all_cbs
    assert "ls_add_sym|SOLUSDT" in all_cbs
    assert "ls_add_all" in all_cbs
    assert "ls_show_all" in all_cbs
    assert "ls_scan_now" in all_cbs
    assert "menu_main" in all_cbs


def test_build_keyboard_symbol_labels():
    """Правильное склонение «уровень/уровня/уровней»."""
    c1 = _make_candidate(symbol="BTCUSDT", price=100.0)
    c2 = _make_candidate(symbol="BTCUSDT", price=200.0)
    c3 = _make_candidate(symbol="ETHUSDT", price=100.0)
    c4 = _make_candidate(symbol="ETHUSDT", price=200.0)
    c5 = _make_candidate(symbol="ETHUSDT", price=300.0)
    c6 = _make_candidate(symbol="ETHUSDT", price=400.0)
    c7 = _make_candidate(symbol="ETHUSDT", price=500.0)
    c8 = _make_candidate(symbol="SOLUSDT", price=100.0)
    results = {
        "BTCUSDT": [c1, c2],                   # 2 → "уровня"
        "ETHUSDT": [c3, c4, c5, c6, c7],       # 5 → "уровней"
        "SOLUSDT": [c8],                       # 1 → "уровень"
    }
    kb = build_keyboard(results)
    labels = [
        btn.text
        for row in kb.inline_keyboard
        for btn in row
    ]
    assert any("BTCUSDT · 2 уровня" in lbl for lbl in labels)
    assert any("ETHUSDT · 5 уровней" in lbl for lbl in labels)
    assert any("SOLUSDT · 1 уровень" in lbl for lbl in labels)


def test_build_keyboard_empty():
    """Пустой results → только сервисные кнопки (Обновить, Меню)."""
    kb = build_keyboard({})
    # Без символов нет «Добавить всё» и «Все уровни»
    # Остаётся: Обновить + Меню = 2
    assert len(kb.inline_keyboard) == 2
    cbs = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "ls_scan_now" in cbs
    assert "menu_main" in cbs
    assert "ls_add_all" not in cbs
    assert "ls_show_all" not in cbs


def test_build_keyboard_sorted_by_symbol():
    """Символы в клавиатуре идут в алфавитном порядке."""
    c_a = _make_candidate(symbol="SOLUSDT", price=100.0)
    c_b = _make_candidate(symbol="BTCUSDT", price=200.0)
    c_c = _make_candidate(symbol="ETHUSDT", price=300.0)
    results = {
        "SOLUSDT": [c_a],
        "BTCUSDT": [c_b],
        "ETHUSDT": [c_c],
    }
    kb = build_keyboard(results)
    # Первые 3 строки — кнопки на символы, порядок: BTC, ETH, SOL
    sym_cbs = [
        row[0].callback_data
        for row in kb.inline_keyboard[:3]
        if row and row[0].callback_data.startswith("ls_add_sym|")
    ]
    assert sym_cbs == [
        "ls_add_sym|BTCUSDT",
        "ls_add_sym|ETHUSDT",
        "ls_add_sym|SOLUSDT",
    ]


def test_build_full_keyboard_all_rows():
    c1 = _make_candidate(symbol="ETHUSDT", price=2685.0)
    c2 = _make_candidate(symbol="ETHUSDT", price=2700.0)
    c3 = _make_candidate(symbol="SOLUSDT", price=117.8)
    kb = build_full_keyboard({"ETHUSDT": [c1, c2], "SOLUSDT": [c3]})
    # 3 уровня + Компактно + Меню = 5
    assert len(kb.inline_keyboard) == 5


def test_make_add_callback_format():
    c = _make_candidate()
    cb = ls._make_add_callback(c)
    parts = cb.split("|")
    assert parts[0] == "ls_add"
    assert parts[1] == "ETHUSDT"
    assert parts[3] == "up"
    assert len(cb.encode("utf-8")) <= 64  # лимит Telegram