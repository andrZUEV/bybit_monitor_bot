"""Тесты src/core/monitor.py — _on_ticker_update."""
from __future__ import annotations

import pytest


class TestOnTickerUpdate:
    def test_first_snapshot_does_not_trigger_cross(self, monitor_factory):
        monitor = monitor_factory()
        monitor._on_ticker_update("SOLUSDT", 100.0, 1_000_000.0)
        monitor._check_price_cross.assert_not_called()

    def test_second_snapshot_triggers_check(self, monitor_factory):
        monitor = monitor_factory()
        monitor._on_ticker_update("SOLUSDT", 100.0, 1_000_000.0)
        monitor._on_ticker_update("SOLUSDT", 101.0, 1_000_000.0)
        monitor._check_price_cross.assert_called()

    def test_prev_price_updated(self, monitor_factory):
        monitor = monitor_factory()
        monitor._on_ticker_update("SOLUSDT", 100.0, 1_000_000.0)
        monitor._on_ticker_update("SOLUSDT", 101.0, 1_000_000.0)
        # Имя атрибута может быть states или _states — уточни.
        states = getattr(monitor, "states", None) or getattr(monitor, "_states", {})
        state = states.get("SOLUSDT")
        assert state is not None
        assert state["prev_price"] == 101.0

    def test_prev_volume_updated(self, monitor_factory):
        monitor = monitor_factory()
        monitor._on_ticker_update("SOLUSDT", 100.0, 1_000_000.0)
        monitor._on_ticker_update("SOLUSDT", 100.0, 2_000_000.0)
        states = getattr(monitor, "states", None) or getattr(monitor, "_states", {})
        assert states["SOLUSDT"]["prev_volume"] == 2_000_000.0

    def test_unknown_symbol_no_alerts_does_not_crash(self, monitor_factory):
        monitor = monitor_factory()
        monitor._on_ticker_update("UNKNOWNUSDT", 1.0, 1.0)
        monitor._check_price_cross.assert_not_called()

    def test_fallback_to_alerts_manager_on_race(self, monitor_factory):
        monitor = monitor_factory()
        # Кэш пуст, но алерт есть в файле.
        monitor._assets_by_symbol = {}
        monitor.alerts_manager.get_all_alerts.return_value = [
            {"id": "a1", "symbol": "SOLUSDT", "price": 100.0,
             "direction": "up", "note": ""},
        ]
        monitor._on_ticker_update("SOLUSDT", 99.0, 1_000_000.0)
        monitor._on_ticker_update("SOLUSDT", 101.0, 1_000_000.0)
        monitor._check_price_cross.assert_called()

    def test_states_not_cleared_on_symbol_removal(self, monitor_factory):
        monitor = monitor_factory()
        monitor._on_ticker_update("SOLUSDT", 100.0, 1_000_000.0)
        monitor._on_ticker_update("SOLUSDT", 101.0, 1_000_000.0)
        monitor._assets_by_symbol = {}
        monitor.alerts_manager.get_all_alerts.return_value = []
        monitor._on_ticker_update("SOLUSDT", 102.0, 1_000_000.0)
        states = getattr(monitor, "states", None) or getattr(monitor, "_states", {})
        assert states["SOLUSDT"]["prev_price"] == 102.0