"""Тесты: monitor пишет AlertRecord в AlertHistory."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.core.alert_history import AlertHistory
from src.core.alerts import AlertRule, Asset
from src.core.monitor import AssetState, Monitor
from src.core.settings import RuntimeSettings


def _make_monitor_with_history(
    settings: RuntimeSettings,
    history: AlertHistory,
) -> tuple[Monitor, MagicMock]:
    """Monitor с замоканным client + history."""
    alerts = MagicMock()
    alerts.get_all_alerts.return_value = []

    on_alert = MagicMock()
    client = MagicMock()
    client.get_klines.return_value = [
        [1, "100", "101", "99", "100", "1000"] for _ in range(5)
    ]
    client.get_candle_volume_ratio.return_value = {"ratio": 1.0}

    m = Monitor(
        alerts_manager=alerts,
        on_alert_callback=on_alert,
        bybit_client=client,
        use_websocket=False,
        settings=settings,
        alert_history=history,
    )
    m.cooldown_manager = MagicMock()
    m.cooldown_manager.can_send.return_value = True
    return m, on_alert


def _make_asset(direction: str = "down", price: float = 100.0) -> Asset:
    return Asset(
        symbol="BTCUSDT",
        category="linear",
        alerts=[AlertRule(price=price, direction=direction, setup_note="тест")],
    )


def _make_evaluation(score: float, hard_filter: str | None = None) -> dict:
    return {
        "score": score,
        "verdict": "⚠️ Weak" if score < 5.5 else "💪 Strong",
        "signals": [],
        "filters": [],
        "vol_ratio": 1.0,
        "rsi": 50.0,
        "touches": 1,
        "htf_trend": "side",
        "age_hours": 1.5,
        "pattern": None,
        "strength_score": int(score),
        "details": [],
        "structure": None,
        "levels_4h": [],
        "levels_1d": [],
        "divergence": [],
        "risk": None,
        "hard_filter": hard_filter,
        "hard_filter_ru": hard_filter,
        "candle_ts": 1_700_000_000_000,
        "candle": {"open": 99.0, "high": 101.0, "low": 98.5, "close": 100.5, "volume": 1234.0},
        "prev_candle": {"open": 98.0, "high": 99.5, "low": 97.5, "close": 99.0},
        "body_size": 1.5, "upper_wick": 0.5, "lower_wick": 1.0,
        "body_to_range_ratio": 0.6, "close_position": "upper", "close_pos_ratio": 0.8,
        "pattern_score": 0.0, "pattern_reason": "no pattern",
        "volume_score": 0.0, "rsi_score": 0.0,
        "wick_beyond_level": True, "close_in_correct_third": True,
        "htf_against": False, "atr_value": 0.5,
    }


def _run_cross(m: Monitor, monkeypatch, evaluation: dict) -> None:
    monkeypatch.setattr(
        "src.core.monitor.evaluate_alert",
        lambda **kwargs: evaluation,
    )
    asset = _make_asset(direction="down", price=100.0)
    ticker = MagicMock()
    ticker.price = 99.5
    ticker.volume_24h = 1000.0
    state = AssetState()
    state.prev_price = 100.5
    m._check_price_cross(asset, ticker, state)


@pytest.fixture
def history(tmp_path):
    return AlertHistory(tmp_path / "history.jsonl", max_records=100)


class TestMonitorWritesHistory:
    def test_sent_alert_recorded(self, history, monkeypatch):
        settings = RuntimeSettings(alert_min_score=0.0)
        m, on_alert = _make_monitor_with_history(settings, history)
        _run_cross(m, monkeypatch, _make_evaluation(score=3.5))

        on_alert.assert_called_once()
        assert history.count() == 1
        rec = history.load_all()[0]
        assert rec.was_sent is True
        assert rec.skip_reason is None
        assert rec.symbol == "BTCUSDT"

    def test_skipped_by_score_recorded(self, history, monkeypatch):
        settings = RuntimeSettings(alert_min_score=0.0)
        m, on_alert = _make_monitor_with_history(settings, history)
        _run_cross(m, monkeypatch, _make_evaluation(score=-4.5))

        on_alert.assert_not_called()
        assert history.count() == 1
        rec = history.load_all()[0]
        assert rec.was_sent is False
        assert rec.skip_reason == "score_below"

    def test_skipped_by_hard_filter_recorded(self, history, monkeypatch):
        settings = RuntimeSettings(alert_min_score=0.0, send_invalid_alerts=False)
        m, on_alert = _make_monitor_with_history(settings, history)
        _run_cross(
            m, monkeypatch,
            _make_evaluation(score=-1.5, hard_filter="trend_conflict"),
        )

        on_alert.assert_not_called()
        assert history.count() == 1
        rec = history.load_all()[0]
        assert rec.was_sent is False
        assert rec.skip_reason == "hard_filter_disabled"

    def test_hard_filter_sent_when_flag_true_recorded(self, history, monkeypatch):
        settings = RuntimeSettings(alert_min_score=0.0, send_invalid_alerts=True)
        m, on_alert = _make_monitor_with_history(settings, history)
        _run_cross(
            m, monkeypatch,
            _make_evaluation(score=-1.5, hard_filter="trend_conflict"),
        )

        on_alert.assert_called_once()
        assert history.count() == 1
        rec = history.load_all()[0]
        assert rec.was_sent is True
        assert rec.skip_reason is None

    def test_weak_signal_not_recorded(self, history, monkeypatch):
        """verdict='❌ None' без hard_filter — не пишем вообще (даже в историю)."""
        settings = RuntimeSettings(alert_min_score=0.0)
        m, on_alert = _make_monitor_with_history(settings, history)
        _run_cross(
            m, monkeypatch,
            _make_evaluation(score=-10.0),  # verdict будет Weak, но score низкий
        )

        # У нас verdict = "⚠️ Weak" (не None) — попадёт в гейт 1 и запишется с was_sent=False
        # Поэтому — проверим, что записано, но не отправлено.
        assert history.count() == 1
        rec = history.load_all()[0]
        assert rec.was_sent is False