"""
Тесты src/core/reversal_diary.py.

Стратегия:
  - чистые функции (_pattern_bucket, _vol_bucket, _htf_align, _rsi_bucket,
    build_setup_key, humanize_setup_key) — прямые ассерты;
  - resolve_outcome — на синтетических свечах (success / fail_stop /
    fail_timeout / pending);
  - ReversalDiary — JSONL roundtrip, stats, probability_for,
    формат, CSV;
  - resolve_all_pending — на фейковых провайдерах klines/atr.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core.reversal_diary import (
    LOOKAHEAD_BARS,
    MIN_MOVE_ATR,
    SL_BUFFER_ATR,
    ReversalDiary,
    ReversalResolution,
    SetupStat,
    _extract_level,
    _htf_align,
    _pattern_bucket,
    _rsi_bucket,
    _vol_bucket,
    build_record_key,
    build_setup_key,
    humanize_setup_key,
    resolve_outcome,
)


# ==================== helpers ====================

def _candle(
    o: float, h: float, low: float, c: float, ts: int = 0,
    v: float = 1.0,
) -> dict:
    return {
        "ts": ts, "open": o, "high": h, "low": low, "close": c, "volume": v,
    }


def _resolution(
    symbol: str = "ETHUSDT",
    level: float = 2600.0,
    direction: str = "up",
    candle_ts: int = 1000,
    setup_key: str = "pinbar|vol_high|wick_1|htf_with|rsi_low",
    status: str = "pending",
) -> ReversalResolution:
    return ReversalResolution(
        record_key=build_record_key(symbol, level, direction, candle_ts),
        symbol=symbol,
        direction=direction,
        setup_key=setup_key,
        status=status,
        created_at=1000.0,
    )


# ==================== _pattern_bucket ====================

def test_pattern_bucket_pinbar():
    assert _pattern_bucket("bullish_pinbar") == "pinbar"
    assert _pattern_bucket("bearish_pinbar") == "pinbar"


def test_pattern_bucket_engulfing():
    assert _pattern_bucket("bullish_engulfing") == "engulfing"
    assert _pattern_bucket("bearish_engulfing") == "engulfing"


def test_pattern_bucket_none():
    assert _pattern_bucket(None) == "none"
    assert _pattern_bucket("") == "none"


def test_pattern_bucket_unknown():
    assert _pattern_bucket("doji") == "other"


# ==================== _vol_bucket ====================

def test_vol_bucket_low():
    assert _vol_bucket(0.5) == "low"
    assert _vol_bucket(0.99) == "low"


def test_vol_bucket_mid():
    assert _vol_bucket(1.0) == "mid"
    assert _vol_bucket(1.5) == "mid"
    assert _vol_bucket(1.99) == "mid"


def test_vol_bucket_high():
    assert _vol_bucket(2.0) == "high"
    assert _vol_bucket(5.0) == "high"


# ==================== _htf_align ====================

def test_htf_align_side():
    assert _htf_align("side", False, "up") == "side"
    assert _htf_align("side", True, "down") == "side"


def test_htf_align_with():
    assert _htf_align("up", False, "up") == "with"
    assert _htf_align("down", False, "down") == "with"


def test_htf_align_against():
    assert _htf_align("down", True, "up") == "against"
    assert _htf_align("up", True, "down") == "against"


# ==================== _rsi_bucket ====================

def test_rsi_bucket_long():
    # Для up: <40 → low (в зоне), 40..60 → mid, >60 → high (не в зоне).
    assert _rsi_bucket(25.0, "up") == "low"
    assert _rsi_bucket(50.0, "up") == "mid"
    assert _rsi_bucket(75.0, "up") == "high"


def test_rsi_bucket_short():
    # Для down: >60 → high (в зоне), 40..60 → mid, <40 → low (не в зоне).
    assert _rsi_bucket(75.0, "down") == "high"
    assert _rsi_bucket(50.0, "down") == "mid"
    assert _rsi_bucket(25.0, "down") == "low"


def test_rsi_bucket_boundary():
    assert _rsi_bucket(40.0, "up") == "mid"
    assert _rsi_bucket(60.0, "up") == "mid"
    assert _rsi_bucket(40.0, "down") == "mid"
    assert _rsi_bucket(60.0, "down") == "mid"


def test_rsi_bucket_none():
    assert _rsi_bucket(None, "up") == "na"
    assert _rsi_bucket(None, "down") == "na"


# ==================== build_setup_key ====================

def test_build_setup_key_full():
    key = build_setup_key(
        pattern="bullish_pinbar",
        volume_ratio=2.5,
        wick_beyond=True,
        htf_trend="up",
        htf_against=False,
        rsi=25.0,
        direction="up",
    )
    assert key == "pinbar|vol_high|wick_1|htf_with|rsi_low"


def test_build_setup_key_minimal():
    key = build_setup_key(
        pattern=None,
        volume_ratio=0.5,
        wick_beyond=False,
        htf_trend="side",
        htf_against=False,
        rsi=None,
        direction="down",
    )
    assert key == "none|vol_low|wick_0|htf_side|rsi_na"


# ==================== humanize_setup_key ====================

def test_humanize_full():
    human = humanize_setup_key("pinbar|vol_high|wick_1|htf_with|rsi_low")
    assert human == "pinbar·vol≥2x·wick·htf+·RSI<40"


def test_humanize_minimal():
    # low vol, no wick, htf side, rsi mid — все «пустые» категории
    human = humanize_setup_key("none|vol_mid|wick_0|htf_side|rsi_mid")
    assert human == "базовый"


def test_humanize_partial():
    human = humanize_setup_key("engulfing|vol_high|wick_0|htf_against|rsi_high")
    assert human == "engulfing·vol≥2x·htf−·RSI>60"


def test_humanize_unknown_format():
    # Не 5 частей — возвращаем как есть.
    assert humanize_setup_key("bogus") == "bogus"


# ==================== resolve_outcome ====================

def test_resolve_outcome_pending_not_enough_bars():
    candles = [_candle(100, 101, 99, 100, ts=i) for i in range(3)]
    status, details = resolve_outcome(
        direction="up", level=100.0, atr_1h=1.0,
        candles_after=candles,
    )
    assert status == "pending"
    assert details == {}


def test_resolve_outcome_success_up():
    level = 100.0
    atr = 1.0
    # 8 свечей, на 4-й цена ушла выше 101 (level + 1×ATR).
    candles = [
        _candle(100, 100.5, 99.8, 100.2, ts=1),
        _candle(100, 100.5, 99.9, 100.0, ts=2),
        _candle(100, 100.4, 99.9, 100.1, ts=3),
        _candle(100, 101.5, 99.9, 101.2, ts=4),   # ← цель
        _candle(101, 101.5, 100.5, 101.0, ts=5),
        _candle(101, 101.5, 100.5, 101.0, ts=6),
        _candle(101, 101.5, 100.5, 101.0, ts=7),
        _candle(101, 101.5, 100.5, 101.0, ts=8),
    ]
    status, details = resolve_outcome(
        direction="up", level=level, atr_1h=atr, candles_after=candles,
    )
    assert status == "success"
    assert details["hit_stop"] is False
    assert details["bars_to_target"] == 4
    assert details["outcome_ts"] == 4
    assert details["move_atr"] == pytest.approx(1.5)


def test_resolve_outcome_success_down():
    level = 100.0
    atr = 1.0
    candles = [
        _candle(100, 100.2, 99.5, 99.8, ts=1),
        _candle(100, 100.2, 98.5, 99.0, ts=2),   # ← цель (low ≤ 99)
        _candle(99, 99.5, 98.0, 98.5, ts=3),
        _candle(99, 99.5, 98.0, 98.5, ts=4),
        _candle(99, 99.5, 98.0, 98.5, ts=5),
        _candle(99, 99.5, 98.0, 98.5, ts=6),
        _candle(99, 99.5, 98.0, 98.5, ts=7),
        _candle(99, 99.5, 98.0, 98.5, ts=8),
    ]
    status, details = resolve_outcome(
        direction="down", level=level, atr_1h=atr, candles_after=candles,
    )
    assert status == "success"
    assert details["hit_stop"] is False
    assert details["bars_to_target"] == 2


def test_resolve_outcome_fail_stop_up():
    level = 100.0
    atr = 1.0
    # Вторая свеча: low ≤ 99.5 (level - 0.5×ATR) — стоп.
    candles = [
        _candle(100, 100.2, 99.8, 100.0, ts=1),
        _candle(100, 100.2, 99.3, 99.4, ts=2),   # ← стоп
        _candle(99, 99.5, 98.0, 98.5, ts=3),
        _candle(99, 99.5, 98.0, 98.5, ts=4),
        _candle(99, 99.5, 98.0, 98.5, ts=5),
        _candle(99, 99.5, 98.0, 98.5, ts=6),
        _candle(99, 99.5, 98.0, 98.5, ts=7),
        _candle(99, 99.5, 98.0, 98.5, ts=8),
    ]
    status, details = resolve_outcome(
        direction="up", level=level, atr_1h=atr, candles_after=candles,
    )
    assert status == "fail"
    assert details["hit_stop"] is True
    assert details["bars_to_target"] == 2
    assert details["move_atr"] is None


def test_resolve_outcome_stop_wins_over_target():
    """Одна свеча, где и цель, и стоп — стоп выигрывает (консервативно)."""
    level = 100.0
    atr = 1.0
    candles = [
        _candle(100, 102.0, 99.0, 100.0, ts=1),   # high ≥ 101, low ≤ 99.5
        _candle(100, 100.5, 99.9, 100.0, ts=2),
        _candle(100, 100.5, 99.9, 100.0, ts=3),
        _candle(100, 100.5, 99.9, 100.0, ts=4),
        _candle(100, 100.5, 99.9, 100.0, ts=5),
        _candle(100, 100.5, 99.9, 100.0, ts=6),
        _candle(100, 100.5, 99.9, 100.0, ts=7),
        _candle(100, 100.5, 99.9, 100.0, ts=8),
    ]
    status, details = resolve_outcome(
        direction="up", level=level, atr_1h=atr, candles_after=candles,
    )
    assert status == "fail"
    assert details["hit_stop"] is True


def test_resolve_outcome_fail_timeout():
    """8 свечей прошли, цели не достигли, стопа не было."""
    level = 100.0
    atr = 1.0
    candles = [
        _candle(100, 100.4, 99.8, 100.0, ts=i + 1)
        for i in range(LOOKAHEAD_BARS)
    ]
    status, details = resolve_outcome(
        direction="up", level=level, atr_1h=atr, candles_after=candles,
    )
    assert status == "fail"
    assert details["hit_stop"] is False
    assert details["bars_to_target"] is None
    assert details["move_atr"] is None


# ==================== _extract_level / build_record_key ====================

def test_build_record_key_format():
    key = build_record_key("ETHUSDT", 2600.0, "up", 1000)
    assert key == "ETHUSDT|2600.0|up|1000"


def test_extract_level():
    assert _extract_level("ETHUSDT|2600.0|up|1000") == 2600.0
    assert _extract_level("BTCUSDT|85000.5|down|2000") == 85000.5


def test_extract_level_broken():
    assert _extract_level("bogus") == 0.0


# ==================== ReversalDiary: базовое ====================

def test_diary_create_and_get(tmp_path: Path):
    diary = ReversalDiary(tmp_path / "diary.jsonl")
    res = _resolution()
    diary.create_pending(res)
    assert diary.count() == 1
    got = diary.get(res.record_key)
    assert got is not None
    assert got.setup_key == res.setup_key


def test_diary_create_is_idempotent(tmp_path: Path):
    diary = ReversalDiary(tmp_path / "diary.jsonl")
    res = _resolution()
    diary.create_pending(res)
    diary.create_pending(res)   # ← второй раз
    assert diary.count() == 1


def test_diary_roundtrip(tmp_path: Path):
    """После переоткрытия данные восстанавливаются."""
    path = tmp_path / "diary.jsonl"
    diary = ReversalDiary(path)
    diary.create_pending(_resolution(symbol="ETHUSDT"))
    diary.create_pending(_resolution(symbol="BTCUSDT", level=85000.0))

    diary2 = ReversalDiary(path)
    assert diary2.count() == 2
    assert diary2.get("ETHUSDT|2600.0|up|1000") is not None
    assert diary2.get("BTCUSDT|85000.0|up|1000") is not None


def test_diary_clear(tmp_path: Path):
    path = tmp_path / "diary.jsonl"
    diary = ReversalDiary(path)
    diary.create_pending(_resolution())
    diary.clear()
    assert diary.count() == 0
    assert not path.exists()


# ==================== stats ====================

def _seed_stats(diary: ReversalDiary) -> None:
    """Засеивает 4 success + 2 fail для сетап-ключа X на ETHUSDT."""
    key = "pinbar|vol_high|wick_1|htf_with|rsi_low"
    for i in range(4):
        res = _resolution(symbol="ETHUSDT", candle_ts=1000 + i, setup_key=key)
        res.status = "success"
        diary.create_pending(res)
    for i in range(2):
        res = _resolution(symbol="ETHUSDT", candle_ts=2000 + i, setup_key=key)
        res.status = "fail"
        diary.create_pending(res)


def test_stats_symbol_only(tmp_path: Path):
    diary = ReversalDiary(tmp_path / "diary.jsonl")
    _seed_stats(diary)
    stats = diary.stats(symbol="ETHUSDT", include_all=False)
    assert len(stats) == 1
    st = stats[0]
    assert st.success == 4
    assert st.fail == 2
    assert st.n == 6
    assert st.rate == pytest.approx(4 / 6)


def test_stats_includes_all_aggregate(tmp_path: Path):
    diary = ReversalDiary(tmp_path / "diary.jsonl")
    _seed_stats(diary)
    stats = diary.stats(symbol=None, include_all=True)
    symbols = {st.symbol for st in stats}
    assert "ETHUSDT" in symbols
    assert "ALL" in symbols
    all_stat = next(st for st in stats if st.symbol == "ALL")
    assert all_stat.success == 4
    assert all_stat.fail == 2


def test_stats_min_n_filters(tmp_path: Path):
    diary = ReversalDiary(tmp_path / "diary.jsonl")
    _seed_stats(diary)  # n=6
    stats = diary.stats(symbol="ETHUSDT", min_n=10, include_all=False)
    assert stats == []


def test_stats_ignores_pending_and_expired(tmp_path: Path):
    diary = ReversalDiary(tmp_path / "diary.jsonl")
    for i in range(3):
        res = _resolution(candle_ts=1000 + i)
        res.status = "pending"
        diary.create_pending(res)
    for i in range(2):
        res = _resolution(candle_ts=2000 + i)
        res.status = "expired"
        diary.create_pending(res)
    assert diary.stats(symbol="ETHUSDT", include_all=False) == []


# ==================== probability_for ====================

def test_probability_for_symbol_hit(tmp_path: Path):
    diary = ReversalDiary(tmp_path / "diary.jsonl")
    _seed_stats(diary)  # n=6 у ETHUSDT
    key = "pinbar|vol_high|wick_1|htf_with|rsi_low"
    result = diary.probability_for("ETHUSDT", key, min_n=5)
    assert result is not None
    rate, n = result
    assert n == 6
    assert rate == pytest.approx(4 / 6)


def test_probability_for_below_min_n(tmp_path: Path):
    diary = ReversalDiary(tmp_path / "diary.jsonl")
    _seed_stats(diary)  # n=6
    key = "pinbar|vol_high|wick_1|htf_with|rsi_low"
    assert diary.probability_for("ETHUSDT", key, min_n=10) is None


def test_probability_for_fallback_all(tmp_path: Path):
    """По ETH n=3 (мало), по ALL n=10 → должен вернуть ALL."""
    diary = ReversalDiary(tmp_path / "diary.jsonl")
    key = "pinbar|vol_high|wick_1|htf_with|rsi_low"
    for i in range(3):
        res = _resolution(symbol="ETHUSDT", candle_ts=1000 + i, setup_key=key)
        res.status = "success"
        diary.create_pending(res)
    for i in range(7):
        res = _resolution(symbol="BTCUSDT", candle_ts=2000 + i, setup_key=key)
        res.status = "fail"
        diary.create_pending(res)

    result = diary.probability_for("ETHUSDT", key, min_n=10)
    assert result is not None
    rate, n = result
    assert n == 10  # ALL
    assert rate == pytest.approx(3 / 10)


# ==================== format_stats_message ====================

def test_format_stats_message_empty(tmp_path: Path):
    diary = ReversalDiary(tmp_path / "diary.jsonl")
    msg = diary.format_stats_message()
    assert "нет данных" in msg.lower()


def test_format_stats_message_basic(tmp_path: Path):
    diary = ReversalDiary(tmp_path / "diary.jsonl")
    _seed_stats(diary)
    msg = diary.format_stats_message(symbol="ETHUSDT")
    assert "ETHUSDT" in msg
    assert "pinbar" in msg
    assert "n=6" in msg


# ==================== resolve_all_pending ====================

def test_resolve_all_pending_timeout_to_expired(tmp_path: Path):
    """Pending старше 48ч → expired."""
    diary = ReversalDiary(tmp_path / "diary.jsonl")
    res = _resolution()
    res.created_at = 0.0
    diary.create_pending(res)
    # now = 49 часов после created_at
    diary.resolve_all_pending(
        klines_provider=lambda s, c: [],
        atr_1h_provider=lambda s, c: None,
        category_provider=lambda s: "linear",
        now=49 * 3600.0,
    )
    assert diary.get(res.record_key).status == "expired"


def test_resolve_all_pending_success(tmp_path: Path):
    diary = ReversalDiary(tmp_path / "diary.jsonl")
    level = 2600.0
    # klines в Bybit-порядке (новые первыми).
    # candle_ts = 1000 — это свеча алерта. Свечи ПОСЛЕ неё в этом списке
    # находятся на индексах с меньшим ts. Дадим 10 свечей после.
    klines = []
    # Свечи после (ts = 1100..2000): цена растёт.
    for i, ts in enumerate([2000, 1900, 1800, 1700, 1600, 1500, 1400, 1300, 1200, 1100]):
        # индекс 0 → ts=2000 (самая свежая) — цена 2602
        # индекс 9 → ts=1100 (самая старая) — цена 2600
        price = 2600 + (10 - i) * 0.2
        klines.append([ts, price, price + 0.5, price - 0.3, price, 1.0])
    # Затем свеча алерта ts=1000.
    klines.append([1000, 2599, 2600, 2598, 2599.5, 1.0])
    # И старые (ts < 1000) — для полноты.
    klines.append([900, 2598, 2599, 2597, 2598, 1.0])

    res = _resolution(symbol="ETHUSDT", level=level, candle_ts=1000)
    diary.create_pending(res)

    diary.resolve_all_pending(
        klines_provider=lambda s, c: klines,
        atr_1h_provider=lambda s, c: 1.0,
        category_provider=lambda s: "linear",
        now=res.created_at + 3600,   # через час после алерта
    )

    got = diary.get(res.record_key)
    assert got.status in ("success", "fail")
    assert got.resolved_at is not None


def test_resolve_all_pending_not_enough_data(tmp_path: Path):
    """Если klines после candle_ts < LOOKAHEAD_BARS — pending остаётся."""
    diary = ReversalDiary(tmp_path / "diary.jsonl")
    klines = [
        [2000, 2600, 2601, 2599, 2600, 1.0],
        [1000, 2599, 2600, 2598, 2599.5, 1.0],
    ]
    res = _resolution(symbol="ETHUSDT", level=2600.0, candle_ts=1000)
    diary.create_pending(res)

    diary.resolve_all_pending(
        klines_provider=lambda s, c: klines,
        atr_1h_provider=lambda s, c: 1.0,
        category_provider=lambda s: "linear",
        now=res.created_at + 600,
    )

    assert diary.get(res.record_key).status == "pending"


# ==================== CSV ====================

def test_export_csv(tmp_path: Path):
    diary = ReversalDiary(tmp_path / "diary.jsonl")
    _seed_stats(diary)
    out = tmp_path / "diary.csv"
    diary.export_csv(out)
    content = out.read_text(encoding="utf-8-sig")
    assert "record_key" in content
    assert "ETHUSDT" in content
    # 6 записей + заголовок
    assert len(content.strip().splitlines()) == 7