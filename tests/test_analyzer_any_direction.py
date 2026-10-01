"""Тесты evaluate_alert с direction='any' и format_alert_message."""
from __future__ import annotations

import time

from src.core.analyzer import evaluate_alert, format_alert_message


class TestEvaluateAnyDirection:
    def test_any_does_not_crash(self, klines_15m, klines_4h):
        result = evaluate_alert(
            symbol="ETHUSDT",
            level=3000.0,
            direction="any",
            current_price=3000.0,
            alert_created_at=time.time(),
            klines_15m=klines_15m,
            klines_4h=klines_4h,
        )
        assert isinstance(result, dict)
        assert result["score"] is not None
        assert isinstance(result["divergence"], list)

    def test_any_pattern_is_none(self, klines_15m, klines_4h):
        result = evaluate_alert(
            symbol="ETHUSDT", level=3000.0, direction="any",
            current_price=3000.0, alert_created_at=time.time(),
            klines_15m=klines_15m, klines_4h=klines_4h,
        )
        assert result["pattern"] is None

    def test_any_risk_is_none(self, klines_15m, klines_4h, risk_cfg):
        result = evaluate_alert(
            symbol="ETHUSDT", level=3000.0, direction="any",
            current_price=3000.0, alert_created_at=time.time(),
            klines_15m=klines_15m, klines_4h=klines_4h,
            risk_cfg=risk_cfg,
        )
        assert result["risk"] is None
        assert any("any" in f.lower() for f in result["filters"])

    def test_any_divergence_is_list_of_dicts(self, klines_15m, klines_4h):
        result = evaluate_alert(
            symbol="ETHUSDT", level=3000.0, direction="any",
            current_price=3000.0, alert_created_at=time.time(),
            klines_15m=klines_15m, klines_4h=klines_4h,
        )
        for d in result["divergence"]:
            assert set(d.keys()) == {"kind", "rsi_gap", "price_gap_pct", "age_bars"}
            assert d["kind"] in ("bullish", "bearish")


class TestUpDownRegression:
    """direction='up'/'down' работает как раньше."""

    def test_up_returns_dict(self, klines_15m, klines_4h):
        result = evaluate_alert(
            symbol="ETHUSDT", level=3000.0, direction="up",
            current_price=3000.0, alert_created_at=time.time(),
            klines_15m=klines_15m, klines_4h=klines_4h,
        )
        assert "score" in result
        assert isinstance(result["divergence"], list)

    def test_down_returns_dict(self, klines_15m, klines_4h):
        result = evaluate_alert(
            symbol="ETHUSDT", level=3000.0, direction="down",
            current_price=3000.0, alert_created_at=time.time(),
            klines_15m=klines_15m, klines_4h=klines_4h,
        )
        assert "score" in result


class TestFormatAlertMessageAny:
    def test_cross_text_any(self, base_evaluation):
        msg = format_alert_message(
            symbol="ETHUSDT", level=3000.0, direction="any",
            current_price=3000.0, evaluation=base_evaluation,
        )
        assert "ПЕРЕСЕЧЕНИЕ" in msg

    def test_cross_text_up(self, base_evaluation):
        msg = format_alert_message(
            symbol="ETHUSDT", level=3000.0, direction="up",
            current_price=3000.0, evaluation=base_evaluation,
        )
        assert "СНИЗУ ВВЕРХ" in msg

    def test_cross_text_down(self, base_evaluation):
        msg = format_alert_message(
            symbol="ETHUSDT", level=3000.0, direction="down",
            current_price=3000.0, evaluation=base_evaluation,
        )
        assert "СВЕРХУ ВНИЗ" in msg

    def test_divergence_section_rendered(self, base_evaluation):
        base_evaluation["divergence"] = [
            {"kind": "bullish", "rsi_gap": 7.5, "price_gap_pct": 1.2, "age_bars": 4},
            {"kind": "bearish", "rsi_gap": 6.0, "price_gap_pct": 0.9, "age_bars": 12},
        ]
        msg = format_alert_message(
            symbol="ETHUSDT", level=3000.0, direction="any",
            current_price=3000.0, evaluation=base_evaluation,
        )
        assert "🧭 <b>Дивергенции:</b>" in msg
        assert "бычья" in msg
        assert "медвежья" in msg

    def test_divergence_section_absent_when_empty(self, base_evaluation):
        base_evaluation["divergence"] = []
        msg = format_alert_message(
            symbol="ETHUSDT", level=3000.0, direction="any",
            current_price=3000.0, evaluation=base_evaluation,
        )
        assert "🧭 <b>Дивергенции:</b>" not in msg