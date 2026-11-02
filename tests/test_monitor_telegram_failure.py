"""Тесты: сбой отправки в Telegram не ставит обычный кулдаун."""
from __future__ import annotations

import time
from unittest.mock import MagicMock

from src.core.alert_history import AlertHistory
from src.core.alerts import AlertRule, Asset
from src.core.cooldown import CooldownManager
from src.core.monitor import AssetState, Monitor
from src.core.settings import RuntimeSettings


def _make_evaluation(score: float = 3.5) -> dict:
    return {
        "score": score,
        "verdict": "⚠️ Weak",
        "signals": [],
        "filters": [],
        "vol_ratio": 1.0,
        "rsi": 50.0,
        "touches": 1,
        "htf_trend": "side",
        "age_hours": 1.0,
        "pattern": None,
        "strength_score": int(score),
        "details": [],
        "structure": None,
        "levels_4h": [],
        "levels_1d": [],
        "divergence": [],
        "risk": None,
        "hard_filter": None,
        "hard_filter_ru": None,
        "candle_ts": 1_700_000_000_000,
        "candle": {"open": 99.0, "high": 101.0, "low": 98.5, "close": 100.5, "volume": 1.0},
        "prev_candle": {"open": 98.0, "high": 99.5, "low": 97.5, "close": 99.0},
        "body_size": 1.5, "upper_wick": 0.5, "lower_wick": 1.0,
        "body_to_range_ratio": 0.6, "close_position": "upper", "close_pos_ratio": 0.8,
        "pattern_score": 0.0, "pattern_reason": "no pattern",
        "volume_score": 0.0, "rsi_score": 0.0,
        "wick_beyond_level": True, "close_in_correct_third": True,
        "htf_against": False, "atr_value": 0.5,
    }


def _make_monitor(tmp_path, on_alert: MagicMock) -> tuple[Monitor, AlertHistory]:
    alerts = MagicMock()
    alerts.get_all_alerts.return_value = []

    client = MagicMock()
    client.get_klines.return_value = [
        [1, "100", "101", "99", "100", "1000"] for _ in range(5)
    ]
    client.get_candle_volume_ratio.return_value = {"ratio": 1.0}

    history = AlertHistory(tmp_path / "history.jsonl", max_records=100)
    m = Monitor(
        alerts_manager=alerts,
        on_alert_callback=on_alert,
        bybit_client=client,
        use_websocket=False,
        settings=RuntimeSettings(alert_min_score=0.0),
        alert_history=history,
    )
    m.cooldown_manager = CooldownManager(
        cooldown_minutes=25, storage_path=str(tmp_path / "cooldowns.json"),
    )
    return m, history


def _run_cross(m: Monitor, monkeypatch) -> AssetState:
    monkeypatch.setattr(
        "src.core.monitor.evaluate_alert",
        lambda **kwargs: _make_evaluation(),
    )
    asset = Asset(
        symbol="BTCUSDT",
        category="linear",
        alerts=[AlertRule(price=100.0, direction="down", setup_note="")],
    )
    ticker = MagicMock()
    ticker.price = 99.5
    ticker.volume_24h = 1000.0

    state = AssetState()
    state.prev_price = 100.5
    m._check_price_cross(asset, ticker, state)
    return state


class TestTelegramFailure:
    def test_failed_send_sets_short_cooldown_and_records_error(
        self, tmp_path, monkeypatch,
    ):
        on_alert = MagicMock(return_value=False)
        m, history = _make_monitor(tmp_path, on_alert)
        state = _run_cross(m, monkeypatch)

        on_alert.assert_called_once()
        rec = history.load_all()[0]
        assert rec.was_sent is False
        assert rec.skip_reason == "telegram_error"

        # Обычный кулдаун не поставлен, но короткий — да.
        assert m.cooldown_manager.short_cooldown_active("BTCUSDT", 100.0, "down")
        assert m.cooldown_manager.can_send("BTCUSDT", 100.0, "down") is False

        # Флаг не поднят: после короткого кулдауна будет новая попытка.
        assert state.triggered_alerts.get("100.0_down", False) is False

    def test_after_short_cooldown_retry_is_allowed(
        self, tmp_path, monkeypatch,
    ):
        on_alert = MagicMock(return_value=False)
        m, _ = _make_monitor(tmp_path, on_alert)
        _run_cross(m, monkeypatch)

        now = time.time()
        monkeypatch.setattr(
            "src.core.cooldown.time.time", lambda: now + 61
        )
        assert m.cooldown_manager.can_send("BTCUSDT", 100.0, "down") is True
        assert m.cooldown_manager.short_cooldown_active(
            "BTCUSDT", 100.0, "down"
        ) is False

    def test_successful_send_keeps_normal_cooldown(self, tmp_path, monkeypatch):
        on_alert = MagicMock(return_value=True)
        m, history = _make_monitor(tmp_path, on_alert)
        _run_cross(m, monkeypatch)

        rec = history.load_all()[0]
        assert rec.was_sent is True
        assert rec.skip_reason is None
        assert m.cooldown_manager.short_cooldown_active(
            "BTCUSDT", 100.0, "down"
        ) is False
        assert m.cooldown_manager.can_send("BTCUSDT", 100.0, "down") is False
