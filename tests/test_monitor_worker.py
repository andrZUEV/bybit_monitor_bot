"""Тесты воркера алертов: WS-поток не блокируется REST/Telegram."""
from __future__ import annotations

import time
from unittest.mock import MagicMock

from src.core.alert_history import AlertHistory
from src.core.alerts import AlertRule, Asset
from src.core.monitor import Monitor
from src.core.settings import RuntimeSettings


class _FakeAlerts:
    def __init__(self, assets: list[Asset]):
        self._assets = assets

    def get_all_alerts(self) -> list[Asset]:
        return list(self._assets)

    def load(self) -> None:
        pass


def _make_monitor(tmp_path) -> tuple[Monitor, list[float]]:
    asset = Asset(
        symbol="BTCUSDT",
        category="linear",
        alerts=[AlertRule(price=100.0, direction="up", setup_note="")],
    )
    history = AlertHistory(tmp_path / "history.jsonl", max_records=10)

    m = Monitor(
        alerts_manager=_FakeAlerts([asset]),
        on_alert_callback=MagicMock(return_value=True),
        bybit_client=MagicMock(),
        use_websocket=False,
        process_inline=False,
        settings=RuntimeSettings(alert_min_score=0.0),
        alert_history=history,
    )

    seen: list[float] = []

    def slow_check(asset, ticker, state, prev_price=None):
        seen.append(prev_price)
        time.sleep(0.3)

    m._check_price_cross = slow_check
    return m, seen


def _wait_for(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class TestAlertWorker:
    def test_ticker_update_returns_immediately(self, tmp_path):
        m, seen = _make_monitor(tmp_path)
        try:
            m._on_ticker_update("BTCUSDT", 100.0, 1.0)  # только prev

            started = time.monotonic()
            m._on_ticker_update("BTCUSDT", 101.0, 1.0)
            elapsed = time.monotonic() - started

            assert elapsed < 0.2, "WS-колбэк не должен ждать REST/Telegram"
            assert _wait_for(lambda: len(seen) == 1)
            assert seen[0] == 100.0
        finally:
            m.stop()

    def test_worker_processes_latest_price_with_first_prev(self, tmp_path):
        m, seen = _make_monitor(tmp_path)
        try:
            m._on_ticker_update("BTCUSDT", 100.0, 1.0)

            for price in (101.0, 102.0, 103.0):
                m._on_ticker_update("BTCUSDT", price, 1.0)

            assert _wait_for(lambda: len(seen) == 1)
            # Первый prev сохранён, чтобы не потерять пересечение.
            assert seen[0] == 100.0
        finally:
            m.stop()

    def test_stop_joins_worker(self, tmp_path):
        m, _ = _make_monitor(tmp_path)
        m._on_ticker_update("BTCUSDT", 100.0, 1.0)
        m._on_ticker_update("BTCUSDT", 101.0, 1.0)

        m.stop()
        assert m._worker_thread is not None
        assert not m._worker_thread.is_alive()
