"""Тесты src/core/monitor.py — _on_ticker_update.

Семантика (подтверждено эмпирически):
- Работает только для символов, по которым есть Asset: либо
  в _assets_by_symbol, либо в alerts_manager.get_all_alerts()
  (fallback).
- Для неизвестных символов — no-op: AssetState НЕ создаётся,
  _check_price_cross НЕ вызывается.
- Первый тик по известному символу создаёт AssetState и НЕ
  вызывает _check_price_cross (только запоминает prev_price).
- После удаления Asset состояние в states[symbol] остаётся
  (защита от гонки WS-потока и refresh-потока), но больше
  не обновляется.

AssetState (dataclass):
- prev_price: float
- prev_volume: float
- last_volume_alert_time: float
- triggered_alerts: dict
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.core.alerts import AlertsManager
from src.core.monitor import Monitor


@pytest.fixture
def monitor(tmp_data_dir):
    alerts = AlertsManager(storage_path=str(tmp_data_dir / "alerts.json"))
    m = Monitor(
        alerts_manager=alerts,
        on_alert_callback=MagicMock(),
        use_websocket=True,
    )
    m._check_price_cross = MagicMock()
    return m


def _populate_asset(m: Monitor, symbol: str, price: float,
                    direction: str = "up") -> None:
    """Кладёт Asset в _assets_by_symbol через публичный API."""
    m.alerts_manager.add_alert(
        symbol=symbol, price=price, direction=direction,
        category="linear", setup_note="",
    )
    for a in m.alerts_manager.get_all_alerts():
        if a.symbol == symbol:
            m._assets_by_symbol[symbol] = a
            break


class TestOnTickerUpdate:
    def test_unknown_symbol_does_not_create_state(self, monitor):
        """Без Asset — no-op."""
        monitor._on_ticker_update("UNKNOWNUSDT", 1.0, 1.0)
        assert "UNKNOWNUSDT" not in monitor.states
        monitor._check_price_cross.assert_not_called()

    def test_first_snapshot_creates_state_for_known_symbol(self, monitor):
        _populate_asset(monitor, "SOLUSDT", 100.0)
        monitor._on_ticker_update("SOLUSDT", 100.0, 1_000_000.0)

        state = monitor.states["SOLUSDT"]
        assert state.prev_price == 100.0
        assert state.prev_volume == 1_000_000.0

    def test_first_snapshot_calls_check_but_no_alert(self, monitor):
        """_check_price_cross вызывается на первом тике,
        но алерт не уходит (prev_price == None)."""
        _populate_asset(monitor, "SOLUSDT", 100.0)
        monitor._on_ticker_update("SOLUSDT", 100.0, 1_000_000.0)
        monitor._check_price_cross.assert_called_once()
        monitor.on_alert.assert_not_called()

    def test_second_snapshot_triggers_check(self, monitor):
        _populate_asset(monitor, "SOLUSDT", 100.0)
        monitor._on_ticker_update("SOLUSDT", 100.0, 1_000_000.0)
        monitor._on_ticker_update("SOLUSDT", 101.0, 1_000_000.0)
        monitor._check_price_cross.assert_called()

    def test_prev_price_updated(self, monitor):
        _populate_asset(monitor, "SOLUSDT", 100.0)
        monitor._on_ticker_update("SOLUSDT", 100.0, 1_000_000.0)
        monitor._on_ticker_update("SOLUSDT", 101.0, 1_000_000.0)
        assert monitor.states["SOLUSDT"].prev_price == 101.0

    def test_prev_volume_updated(self, monitor):
        _populate_asset(monitor, "SOLUSDT", 100.0)
        monitor._on_ticker_update("SOLUSDT", 100.0, 1_000_000.0)
        monitor._on_ticker_update("SOLUSDT", 100.0, 2_000_000.0)
        assert monitor.states["SOLUSDT"].prev_volume == 2_000_000.0

    def test_fallback_to_alerts_manager_on_race(self, monitor):
        """Кэш Monitor пуст, но алерт есть в alerts_manager.

        Fallback работает: _on_ticker_update находит Asset через
        alerts_manager.get_all_alerts().
        """
        monitor._assets_by_symbol.clear()
        monitor.alerts_manager.add_alert(
            symbol="SOLUSDT", price=100.0, direction="up",
            category="linear", setup_note="",
        )
        monitor._on_ticker_update("SOLUSDT", 99.0, 1_000_000.0)
        monitor._on_ticker_update("SOLUSDT", 101.0, 1_000_000.0)
        monitor._check_price_cross.assert_called()

    def test_states_preserved_on_symbol_removal(self, monitor):
        """После удаления Asset состояние остаётся как было."""
        _populate_asset(monitor, "SOLUSDT", 100.0)
        monitor._on_ticker_update("SOLUSDT", 100.0, 1_000_000.0)
        monitor._on_ticker_update("SOLUSDT", 101.0, 1_000_000.0)
        assert monitor.states["SOLUSDT"].prev_price == 101.0

        state_before = monitor.states["SOLUSDT"]

        monitor._assets_by_symbol.clear()
        monitor.alerts_manager.remove_all_alerts_for_symbol("SOLUSDT")

        monitor._on_ticker_update("SOLUSDT", 102.0, 1_000_000.0)

        state_after = monitor.states["SOLUSDT"]
        assert state_after is state_before
        assert state_after.prev_price == 101.0