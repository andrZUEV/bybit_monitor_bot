"""Тесты src/core/alerts.py.

Структура данных:
- Asset(symbol, category, alerts: list[AlertRule])
- AlertRule(price, direction, setup_note, created_at)

Методы:
- add_alert(symbol, price, direction, category, setup_note) -> (success, replaced)
- get_all_alerts() -> list[Asset]
- remove_alert(symbol, price, direction) -> bool
- remove_all_alerts_for_symbol(symbol) -> bool
- clear_all()
- load() / save()
"""
from __future__ import annotations

import threading

from src.core.alerts import AlertsManager


class TestAlertsManager:

    def test_add_and_get(self, alerts_path: str):
        mgr = AlertsManager(storage_path=alerts_path)
        ok, replaced = mgr.add_alert(
            symbol="SOLUSDT", price=100.0, direction="up",
            category="linear", setup_note="test",
        )
        assert ok is True
        assert replaced is False

        all_alerts = mgr.get_all_alerts()
        assert len(all_alerts) == 1
        asset = all_alerts[0]
        assert asset.symbol == "SOLUSDT"
        assert asset.category == "linear"
        assert len(asset.alerts) == 1
        rule = asset.alerts[0]
        assert rule.price == 100.0
        assert rule.direction == "up"
        assert rule.setup_note == "test"

    def test_assets_attribute_matches_get_all(self, alerts_path: str):
        mgr = AlertsManager(storage_path=alerts_path)
        mgr.add_alert(symbol="SOLUSDT", price=100.0, direction="up",
                      category="linear", setup_note="")
        assert len(mgr.assets) == len(mgr.get_all_alerts()) == 1

    def test_add_same_symbol_second_alert(self, alerts_path: str):
        """Два алерта на один символ — две AlertRule в одном Asset."""
        mgr = AlertsManager(storage_path=alerts_path)
        mgr.add_alert(symbol="SOLUSDT", price=100.0, direction="up",
                      category="linear", setup_note="")
        mgr.add_alert(symbol="SOLUSDT", price=200.0, direction="down",
                      category="linear", setup_note="")

        all_alerts = mgr.get_all_alerts()
        assert len(all_alerts) == 1
        assert len(all_alerts[0].alerts) == 2

    def test_remove_alert_by_triple(self, alerts_path: str):
        mgr = AlertsManager(storage_path=alerts_path)
        mgr.add_alert(symbol="SOLUSDT", price=100.0, direction="up",
                      category="linear", setup_note="")
        mgr.add_alert(symbol="SOLUSDT", price=200.0, direction="down",
                      category="linear", setup_note="")

        ok = mgr.remove_alert("SOLUSDT", 100.0, "up")
        assert ok is True

        remaining = mgr.get_all_alerts()
        assert len(remaining) == 1
        assert len(remaining[0].alerts) == 1
        assert remaining[0].alerts[0].price == 200.0

    def test_remove_alert_unknown_returns_false(self, alerts_path: str):
        mgr = AlertsManager(storage_path=alerts_path)
        mgr.add_alert(symbol="SOLUSDT", price=100.0, direction="up",
                      category="linear", setup_note="")
        assert mgr.remove_alert("SOLUSDT", 999.0, "up") is False
        assert mgr.remove_alert("NOPEUSDT", 100.0, "up") is False

    def test_remove_all_for_symbol(self, alerts_path: str):
        mgr = AlertsManager(storage_path=alerts_path)
        mgr.add_alert(symbol="SOLUSDT", price=100.0, direction="up",
                      category="linear", setup_note="")
        mgr.add_alert(symbol="SOLUSDT", price=101.0, direction="up",
                      category="linear", setup_note="")
        mgr.add_alert(symbol="BTCUSDT", price=50000.0, direction="down",
                      category="linear", setup_note="")
        assert len(mgr.get_all_alerts()) == 2

        removed = mgr.remove_all_alerts_for_symbol("SOLUSDT")
        assert removed is True

        remaining = mgr.get_all_alerts()
        assert len(remaining) == 1
        assert remaining[0].symbol == "BTCUSDT"

    def test_remove_unknown_symbol_returns_false(self, alerts_path: str):
        mgr = AlertsManager(storage_path=alerts_path)
        assert mgr.remove_all_alerts_for_symbol("NOPEUSDT") is False

    def test_persists_to_disk(self, alerts_path: str):
        mgr = AlertsManager(storage_path=alerts_path)
        mgr.add_alert(symbol="BTCUSDT", price=50000.0, direction="down",
                      category="linear", setup_note="x")

        mgr2 = AlertsManager(storage_path=alerts_path)
        mgr2.load()
        assert len(mgr2.get_all_alerts()) == 1
        assert mgr2.get_all_alerts()[0].symbol == "BTCUSDT"

    def test_load_corrupt_json_does_not_crash(self, alerts_path: str):
        with open(alerts_path, "w", encoding="utf-8") as f:
            f.write("{not valid json")
        mgr = AlertsManager(storage_path=alerts_path)
        mgr.load()
        assert isinstance(mgr.get_all_alerts(), list)

    def test_clear_all(self, alerts_path: str):
        mgr = AlertsManager(storage_path=alerts_path)
        mgr.add_alert(symbol="SOLUSDT", price=100.0, direction="up",
                      category="linear", setup_note="")
        mgr.add_alert(symbol="BTCUSDT", price=50000.0, direction="down",
                      category="linear", setup_note="")
        assert len(mgr.get_all_alerts()) == 2

        mgr.clear_all()
        assert mgr.get_all_alerts() == []

    def test_add_100_unique_symbols(self, alerts_path: str):
        """Один поток, 100 уникальных символов → 100 Asset."""
        mgr = AlertsManager(storage_path=alerts_path)
        for i in range(100):
            mgr.add_alert(
                symbol=f"U{i}USDT", price=float(i), direction="up",
                category="linear", setup_note="",
            )
        assert len(mgr.get_all_alerts()) == 100

    def test_concurrent_add_and_read_no_crash(self, alerts_path: str):
        """Параллельные add и read не падают."""
        mgr = AlertsManager(storage_path=alerts_path)

        def adder(offset: int) -> None:
            for i in range(10):
                mgr.add_alert(
                    symbol=f"A{offset}_{i}USDT",
                    price=float(offset * 100 + i),
                    direction="up",
                    category="linear",
                    setup_note="",
                )

        def reader() -> None:
            for _ in range(10):
                mgr.get_all_alerts()

        threads = (
            [threading.Thread(target=adder, args=(k,)) for k in range(3)]
            + [threading.Thread(target=reader) for _ in range(2)]
        )
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(mgr.get_all_alerts()) > 0