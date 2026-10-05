"""Тесты расширенных полей evaluate_alert для AlertHistory (5.2.2)."""
from __future__ import annotations

import time

from src.core.analyzer import evaluate_alert


class TestExtendedFieldsPresent:
    def test_all_new_keys_in_result(self, klines_15m, klines_4h):
        r = evaluate_alert(
            symbol="ETHUSDT", level=3000.0, direction="up",
            current_price=3000.0, alert_created_at=time.time(),
            klines_15m=klines_15m, klines_4h=klines_4h,
        )
        for key in [
            "candle_ts", "candle", "prev_candle",
            "body_size", "upper_wick", "lower_wick",
            "body_to_range_ratio", "close_position", "close_pos_ratio",
            "pattern_score", "pattern_reason",
            "volume_score", "rsi_score",
            "wick_beyond_level", "close_in_correct_third",
            "htf_against", "atr_value",
        ]:
            assert key in r, f"Нет ключа {key}"

    def test_empty_result_also_has_new_keys(self):
        """При недостатке данных возвращается empty — но с новыми ключами."""
        r = evaluate_alert(
            symbol="XRPUSDT", level=1.0, direction="up",
            current_price=1.0, alert_created_at=time.time(),
            klines_15m=[], klines_4h=[],
        )
        assert r["candle_ts"] == 0
        assert r["candle"] == {}
        assert r["pattern_score"] == 0.0
        assert r["wick_beyond_level"] is False


class TestExtendedFieldsValues:
    def test_candle_dict_shape(self, klines_15m, klines_4h):
        r = evaluate_alert(
            symbol="ETHUSDT", level=3000.0, direction="up",
            current_price=3000.0, alert_created_at=time.time(),
            klines_15m=klines_15m, klines_4h=klines_4h,
        )
        assert set(r["candle"].keys()) == {"open", "high", "low", "close", "volume"}
        assert set(r["prev_candle"].keys()) == {"open", "high", "low", "close"}
        assert r["candle_ts"] > 0

    def test_body_size_and_wicks_non_negative(self, klines_15m, klines_4h):
        r = evaluate_alert(
            symbol="ETHUSDT", level=3000.0, direction="up",
            current_price=3000.0, alert_created_at=time.time(),
            klines_15m=klines_15m, klines_4h=klines_4h,
        )
        assert r["body_size"] >= 0
        assert r["upper_wick"] >= 0
        assert r["lower_wick"] >= 0

    def test_close_position_values(self, klines_15m, klines_4h):
        r = evaluate_alert(
            symbol="ETHUSDT", level=3000.0, direction="up",
            current_price=3000.0, alert_created_at=time.time(),
            klines_15m=klines_15m, klines_4h=klines_4h,
        )
        assert r["close_position"] in ("upper", "middle", "lower")
        assert 0.0 <= r["close_pos_ratio"] <= 1.0

    def test_pattern_reason_string(self, klines_15m, klines_4h):
        r = evaluate_alert(
            symbol="ETHUSDT", level=3000.0, direction="up",
            current_price=3000.0, alert_created_at=time.time(),
            klines_15m=klines_15m, klines_4h=klines_4h,
        )
        assert isinstance(r["pattern_reason"], str)
        # "no pattern" или что-то осмысленное
        assert len(r["pattern_reason"]) > 0


class TestBuildRecordFromEvaluation:
    def test_build_alert_record_ok(self, klines_15m, klines_4h):
        from src.core.alert_history import build_alert_record

        ev = evaluate_alert(
            symbol="ETHUSDT", level=3000.0, direction="up",
            current_price=3000.0, alert_created_at=time.time(),
            klines_15m=klines_15m, klines_4h=klines_4h,
        )
        rec = build_alert_record(
            evaluation=ev,
            symbol="ETHUSDT",
            level=3000.0,
            direction="up",
            current_price=3000.0,
            alert_age_hours=1.0,
            volume_ratio=1.5,
            was_sent=True,
        )
        assert rec.symbol == "ETHUSDT"
        assert rec.direction == "up"
        assert rec.was_sent is True
        assert rec.skip_reason is None
        assert rec.final_score == ev["score"]
        assert rec.candle == ev["candle"]
        assert rec.candle_ts == ev["candle_ts"]

    def test_build_alert_record_empty_evaluation(self):
        """Не падает, если evaluation — пустой словарь."""
        from src.core.alert_history import build_alert_record

        rec = build_alert_record(
            evaluation={},
            symbol="XRPUSDT",
            level=1.0,
            direction="down",
            current_price=1.0,
            alert_age_hours=0.5,
            volume_ratio=1.0,
            was_sent=False,
            skip_reason="score_below",
        )
        assert rec.final_score == 0.0
        assert rec.verdict == "❌ None"
        assert rec.was_sent is False
        assert rec.skip_reason == "score_below"
        assert rec.candle == {}