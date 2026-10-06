"""Тесты дедупликации AlertHistory."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.core.alert_history import AlertHistory, AlertRecord


def _make_rec(
    symbol: str = "BTCUSDT",
    level: float = 100.0,
    direction: str = "up",
    candle_ts: int = 1_700_000_000_000,
    was_sent: bool = False,
    score: float = 3.0,
) -> AlertRecord:
    return AlertRecord(
        timestamp=1_700_000_000.0,
        symbol=symbol, level=level, direction=direction,
        alert_age_hours=1.0, final_score=score,
        verdict="⚠️ Weak", was_sent=was_sent, skip_reason=None,
        candle_ts=candle_ts,
        candle={"open": 99.0, "high": 101.0, "low": 98.5, "close": 100.5, "volume": 1000.0},
        prev_candle={"open": 98.0, "high": 99.5, "low": 97.5, "close": 99.0},
        volume_ratio=1.0, rsi=50.0,
        body_size=1.5, upper_wick=0.5, lower_wick=1.0,
        body_to_range_ratio=0.6, close_position="upper", close_pos_ratio=0.8,
        detected_pattern=None, pattern_score=0.0, pattern_reason="",
        wick_beyond_level=False, close_in_correct_third=False,
        volume_score=0.0, rsi_score=0.0,
        worn_level=False, touches_count=1,
        htf_trend="side", htf_against=False,
        timeframe_used="15m", atr_value=0.5, hard_filter=None,
        strategy_pass=False,
    )


@pytest.fixture
def history(tmp_path: Path) -> AlertHistory:
    return AlertHistory(tmp_path / "h.jsonl", max_records=100)


class TestDedup:
    def test_same_key_overwrites(self, history):
        history.append(_make_rec(score=3.0))
        history.append(_make_rec(score=5.0))

        assert history.count() == 1
        rec = history.load_all()[0]
        assert rec.final_score == 5.0   # последняя запись победила

    def test_same_key_keeps_was_sent_true(self, history):
        history.append(_make_rec(was_sent=True, score=3.0))
        history.append(_make_rec(was_sent=False, score=5.0))

        assert history.count() == 1
        rec = history.load_all()[0]
        assert rec.was_sent is True
        assert rec.final_score == 3.0

    def test_different_candle_ts_keeps_both(self, history):
        history.append(_make_rec(candle_ts=1_700_000_000_000, score=3.0))
        history.append(_make_rec(candle_ts=1_700_000_100_000, score=5.0))

        assert history.count() == 2

    def test_different_symbol_keeps_both(self, history):
        history.append(_make_rec(symbol="BTCUSDT"))
        history.append(_make_rec(symbol="ETHUSDT"))

        assert history.count() == 2

    def test_different_level_keeps_both(self, history):
        history.append(_make_rec(level=100.0))
        history.append(_make_rec(level=200.0))

        assert history.count() == 2

    def test_different_direction_keeps_both(self, history):
        history.append(_make_rec(direction="up"))
        history.append(_make_rec(direction="down"))

        assert history.count() == 2

    def test_dedup_survives_reload(self, tmp_path: Path):
        path = tmp_path / "h.jsonl"
        h1 = AlertHistory(path, max_records=100)
        h1.append(_make_rec(score=3.0))
        h1.append(_make_rec(score=5.0))   # дедуп → одна

        h2 = AlertHistory(path, max_records=100)
        assert h2.count() == 1
        assert h2.load_all()[0].final_score == 5.0

    def test_dedup_over_limit_prunes(self, tmp_path: Path):
        h = AlertHistory(tmp_path / "h.jsonl", max_records=3)
        # 5 разных свечей
        for i in range(5):
            h.append(_make_rec(candle_ts=1_700_000_000_000 + i * 1000))

        assert h.count() == 3
        # Оставлены последние (по порядку добавления)
        recs = h.load_all()
        assert [r.candle_ts for r in recs] == [
            1_700_000_002_000, 1_700_000_003_000, 1_700_000_004_000,
        ]