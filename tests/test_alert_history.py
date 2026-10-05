"""Тесты AlertHistory: append/load/prune/export."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core.alert_history import DEFAULT_MAX_RECORDS, AlertHistory, AlertRecord

# ==================== ФАБРИКА ====================

def _make_record(
    symbol: str = "BTCUSDT",
    final_score: float = 3.5,
    was_sent: bool = True,
    ts: float | None = None,
) -> AlertRecord:
    return AlertRecord(
        timestamp=ts if ts is not None else 1_700_000_000.0,
        symbol=symbol,
        level=100.0,
        direction="up",
        alert_age_hours=1.5,
        final_score=final_score,
        verdict="⚠️ Weak",
        was_sent=was_sent,
        skip_reason=None if was_sent else "score_below",
        candle_ts=1_700_000_000_000,
        candle={"open": 99.0, "high": 101.0, "low": 98.5, "close": 100.5, "volume": 1234.0},
        prev_candle={"open": 98.0, "high": 99.5, "low": 97.5, "close": 99.0},
        volume_ratio=1.5,
        rsi=45.0,
        body_size=1.5,
        upper_wick=0.5,
        lower_wick=1.0,
        body_to_range_ratio=0.6,
        close_position="upper",
        close_pos_ratio=0.8,
        detected_pattern="bullish_pinbar",
        pattern_score=2.0,
        pattern_reason="lower_wick >= 2*body, close in upper third",
        wick_beyond_level=True,
        close_in_correct_third=True,
        volume_score=2.0,
        rsi_score=1.0,
        worn_level=False,
        touches_count=2,
        htf_trend="up",
        htf_against=False,
        timeframe_used="15m",
        atr_value=0.5,
        hard_filter=None,
    )


@pytest.fixture
def history_path(tmp_path: Path) -> Path:
    return tmp_path / "alert_history.jsonl"


@pytest.fixture
def history(history_path: Path) -> AlertHistory:
    return AlertHistory(history_path, max_records=5)


# ==================== БАЗОВОЕ ====================

class TestAppendLoad:
    def test_append_one(self, history):
        r = _make_record()
        history.append(r)

        loaded = history.load_all()
        assert len(loaded) == 1
        assert loaded[0].symbol == "BTCUSDT"
        assert loaded[0].final_score == 3.5

    def test_append_many(self, history):
        for i in range(3):
            history.append(_make_record(symbol=f"SYM{i}USDT"))

        loaded = history.load_all()
        assert [r.symbol for r in loaded] == ["SYM0USDT", "SYM1USDT", "SYM2USDT"]

    def test_count_empty(self, history):
        assert history.count() == 0
        assert history.is_empty() is True

    def test_count_after_append(self, history):
        history.append(_make_record())
        history.append(_make_record())
        assert history.count() == 2

    def test_roundtrip_full_record(self, history):
        r = _make_record()
        history.append(r)

        loaded = history.load_all()[0]
        # Проверяем ключевые поля
        assert loaded.direction == "up"
        assert loaded.candle["open"] == 99.0
        assert loaded.candle["high"] == 101.0
        assert loaded.prev_candle["open"] == 98.0
        assert loaded.detected_pattern == "bullish_pinbar"
        assert loaded.pattern_score == 2.0
        assert loaded.close_position == "upper"
        assert loaded.close_pos_ratio == 0.8
        assert loaded.wick_beyond_level is True


# ==================== ОБРЕЗКА ====================

class TestPrune:
    def test_prune_kicks_in_on_overflow(self, history):
        # max_records=5, добавляем 7 → должны остаться последние 5
        for i in range(7):
            history.append(_make_record(symbol=f"SYM{i}USDT", ts=float(i)))

        loaded = history.load_all()
        assert len(loaded) == 5
        assert [r.symbol for r in loaded] == [
            "SYM2USDT", "SYM3USDT", "SYM4USDT", "SYM5USDT", "SYM6USDT",
        ]

    def test_prune_keeps_newest_by_position(self, history):
        for i in range(10):
            history.append(_make_record(symbol=f"S{i}", ts=float(i)))

        loaded = history.load_all()
        # последние 5 — S5..S9
        assert [r.symbol for r in loaded] == ["S5", "S6", "S7", "S8", "S9"]

    def test_set_max_records_triggers_prune(self, history):
        for i in range(5):
            history.append(_make_record(symbol=f"S{i}", ts=float(i)))

        history.set_max_records(2)
        loaded = history.load_all()
        assert len(loaded) == 2
        assert [r.symbol for r in loaded] == ["S3", "S4"]


# ==================== ОЧИСТКА ====================

class TestClear:
    def test_clear(self, history):
        history.append(_make_record())
        history.append(_make_record())
        assert history.count() == 2

        history.clear()
        assert history.count() == 0
        assert history.is_empty() is True


# ==================== БИТЫЕ ДАННЫЕ ====================

class TestBrokenData:
    def test_broken_line_skipped(self, history_path: Path):
        history_path.write_text(
            json.dumps({
                "timestamp": 1.0, "symbol": "BTCUSDT", "level": 100.0,
                "direction": "up", "alert_age_hours": 1.0, "final_score": 3.0,
                "verdict": "Weak", "was_sent": True, "skip_reason": None,
                "candle_ts": 1, "candle": {}, "prev_candle": {},
                "volume_ratio": 1.0, "rsi": None,
                "body_size": 0.0, "upper_wick": 0.0, "lower_wick": 0.0,
                "body_to_range_ratio": 0.0, "close_position": "middle",
                "close_pos_ratio": 0.5, "detected_pattern": None,
                "pattern_score": 0.0, "pattern_reason": "",
                "wick_beyond_level": False, "close_in_correct_third": False,
                "volume_score": 0.0, "rsi_score": 0.0,
                "worn_level": False, "touches_count": 0,
                "htf_trend": "side", "htf_against": False,
                "timeframe_used": "15m", "atr_value": None,
                "hard_filter": None, "recorded_at": 1.0,
            }) + "\n"
            + "не json\n"
            + json.dumps({
                "timestamp": 2.0, "symbol": "ETHUSDT", "level": 3000.0,
                "direction": "down", "alert_age_hours": 2.0, "final_score": -1.0,
                "verdict": "None", "was_sent": False, "skip_reason": "score_below",
                "candle_ts": 2, "candle": {}, "prev_candle": {},
                "volume_ratio": 0.5, "rsi": None,
                "body_size": 0.0, "upper_wick": 0.0, "lower_wick": 0.0,
                "body_to_range_ratio": 0.0, "close_position": "middle",
                "close_pos_ratio": 0.5, "detected_pattern": None,
                "pattern_score": 0.0, "pattern_reason": "",
                "wick_beyond_level": False, "close_in_correct_third": False,
                "volume_score": 0.0, "rsi_score": 0.0,
                "worn_level": False, "touches_count": 0,
                "htf_trend": "side", "htf_against": False,
                "timeframe_used": "15m", "atr_value": None,
                "hard_filter": None, "recorded_at": 2.0,
            }) + "\n",
            encoding="utf-8",
        )

        hist = AlertHistory(history_path, max_records=100)
        loaded = hist.load_all()
        # Битая строка пропущена, две валидных загружены
        assert len(loaded) == 2
        assert loaded[0].symbol == "BTCUSDT"
        assert loaded[1].symbol == "ETHUSDT"


# ==================== ЭКСПОРТ В CSV ====================

class TestExportCsv:
    def test_export_empty(self, history, tmp_path: Path):
        out = tmp_path / "history.csv"
        history.export_csv(out)
        assert out.exists()
        # Только заголовок
        lines = out.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1

    def test_export_with_records(self, history, tmp_path: Path):
        history.append(_make_record(symbol="BTCUSDT", final_score=3.5))
        history.append(_make_record(symbol="ETHUSDT", final_score=-1.0, was_sent=False))

        out = tmp_path / "history.csv"
        history.export_csv(out)

        content = out.read_text(encoding="utf-8")
        assert "BTCUSDT" in content
        assert "ETHUSDT" in content
        assert "final_score" in content
        assert "candle_open" in content
        assert "prev_candle_close" in content


# ==================== MAX_RECORDS DEFAULT ====================

def test_default_max_records():
    assert DEFAULT_MAX_RECORDS == 1000


def test_max_records_lower_bound():
    """max_records всегда >= 1, даже если передали 0 или отрицательное."""
    h = AlertHistory("data/test.jsonl", max_records=0)
    assert h.max_records == 1

    h2 = AlertHistory("data/test.jsonl", max_records=-100)
    assert h2.max_records == 1