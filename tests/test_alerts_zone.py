"""Этап 10: зоны в AlertRule — миграция, дедуп, границы."""
from __future__ import annotations

import json

from src.core.alerts import AlertRule, AlertsManager


def _legacy_file(path, alerts: list[dict]) -> str:
    path.write_text(
        json.dumps(
            {"assets": [{"symbol": "BTCUSDT", "category": "linear", "alerts": alerts}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return str(path)


class TestMigration:
    """Старые файлы без price_low/price_high читаются как точечные алерты."""

    def test_old_record_without_bounds(self, tmp_path):
        path = _legacy_file(tmp_path / "alerts.json", [{
            "price": 65000.0, "direction": "up",
            "setup_note": "old", "created_at": 1.0,
        }])

        mgr = AlertsManager(storage_path=path)
        rule = mgr.get_all_alerts()[0].alerts[0]

        assert rule.is_zone is False
        assert rule.price == 65000.0
        assert rule.low == rule.high == 65000.0

    def test_reversed_bounds_are_normalized(self):
        rule = AlertRule.from_dict({
            "price": 0, "direction": "up",
            "price_low": 120.0, "price_high": 100.0,
        })

        assert rule.is_zone is True
        assert (rule.price_low, rule.price_high) == (100.0, 120.0)
        # price отсутствовал — центром стала середина зоны
        assert rule.price == 110.0

    def test_single_bound_is_ignored(self):
        rule = AlertRule.from_dict({
            "price": 100.0, "direction": "up", "price_low": 95.0,
        })

        assert rule.is_zone is False
        assert rule.price_low is None
        assert rule.price == 100.0

    def test_equal_bounds_are_point(self):
        rule = AlertRule.from_dict({
            "price": 100.0, "direction": "up",
            "price_low": 100.0, "price_high": 100.0,
        })

        assert rule.is_zone is False

    def test_negative_low_is_ignored(self):
        rule = AlertRule.from_dict({
            "price": 100.0, "direction": "up",
            "price_low": -5.0, "price_high": 120.0,
        })

        assert rule.is_zone is False


class TestRoundtrip:
    def test_zone_saved_and_loaded(self, tmp_path):
        path = str(tmp_path / "alerts.json")
        mgr = AlertsManager(storage_path=path)
        mgr.add_alert(
            symbol="BTCUSDT", price=102.5, direction="down",
            setup_note="зона", price_low=100.0, price_high=105.0,
        )

        reloaded = AlertsManager(storage_path=path)
        rule = reloaded.get_all_alerts()[0].alerts[0]

        assert rule.is_zone is True
        assert (rule.price_low, rule.price_high) == (100.0, 105.0)
        assert rule.price == 102.5
        assert rule.setup_note == "зона"

    def test_json_contains_bounds(self, tmp_path):
        path = tmp_path / "alerts.json"
        mgr = AlertsManager(storage_path=str(path))
        mgr.add_alert(
            symbol="ETHUSDT", price=0, direction="up",
            price_low=3000.0, price_high=3100.0,
        )

        data = json.loads(path.read_text(encoding="utf-8"))
        rule = data["assets"][0]["alerts"][0]

        assert rule["price_low"] == 3000.0
        assert rule["price_high"] == 3100.0
        # price не передавали — вычислился как середина зоны
        assert rule["price"] == 3050.0

    def test_zone_label(self):
        zone = AlertRule(price=102.5, direction="up", price_low=100.0, price_high=105.0)
        point = AlertRule(price=100.0, direction="up")

        assert zone.zone_label(",.2f") == "100.00–105.00"
        assert point.zone_label(",.2f") == "100.00"


class TestDedup:
    """Дедуп: замена при пересечении диапазонов и совпадении направления."""

    def test_same_zone_replaced(self, tmp_path):
        mgr = AlertsManager(storage_path=str(tmp_path / "a.json"))
        ok, replaced = mgr.add_alert(
            "BTCUSDT", 102.5, "up", price_low=100.0, price_high=105.0,
        )
        assert (ok, replaced) == (True, False)

        created = mgr.get_all_alerts()[0].alerts[0].created_at

        ok, replaced = mgr.add_alert(
            "BTCUSDT", 102.5, "up", price_low=100.0, price_high=105.0,
            setup_note="обновлено",
        )
        assert (ok, replaced) == (True, True)

        rules = mgr.get_all_alerts()[0].alerts
        assert len(rules) == 1
        assert rules[0].setup_note == "обновлено"
        # created_at старого алерта сохраняется
        assert rules[0].created_at == created

    def test_overlapping_zone_replaced(self, tmp_path):
        mgr = AlertsManager(storage_path=str(tmp_path / "a.json"))
        mgr.add_alert("BTCUSDT", 102.5, "up", price_low=100.0, price_high=105.0)

        ok, replaced = mgr.add_alert(
            "BTCUSDT", 108.0, "up", price_low=104.0, price_high=112.0,
        )

        assert (ok, replaced) == (True, True)
        assert len(mgr.get_all_alerts()[0].alerts) == 1
        assert mgr.get_all_alerts()[0].alerts[0].price_high == 112.0

    def test_adjacent_zones_are_separate(self, tmp_path):
        mgr = AlertsManager(storage_path=str(tmp_path / "a.json"))
        mgr.add_alert("BTCUSDT", 102.5, "up", price_low=100.0, price_high=105.0)
        ok, replaced = mgr.add_alert(
            "BTCUSDT", 107.5, "up", price_low=105.0, price_high=110.0,
        )

        # 105.0 — общая точка: диапазоны «касаются», это пересечение → замена
        assert (ok, replaced) == (True, True)

        # А 111–115 уже не пересекается с 105–110 — это новый алерт
        ok, replaced = mgr.add_alert(
            "BTCUSDT", 113.0, "up", price_low=111.0, price_high=115.0,
        )
        assert (ok, replaced) == (True, False)
        assert len(mgr.get_all_alerts()[0].alerts) == 2

    def test_same_price_other_direction_is_separate(self, tmp_path):
        """Раньше «down» затирал «up» на той же цене — теперь это разные алерты."""
        mgr = AlertsManager(storage_path=str(tmp_path / "a.json"))
        mgr.add_alert("BTCUSDT", 100.0, "up")
        ok, replaced = mgr.add_alert("BTCUSDT", 100.0, "down")

        assert (ok, replaced) == (True, False)
        assert len(mgr.get_all_alerts()[0].alerts) == 2

    def test_point_on_zone_is_replaced(self, tmp_path):
        mgr = AlertsManager(storage_path=str(tmp_path / "a.json"))
        mgr.add_alert("BTCUSDT", 102.5, "up", price_low=100.0, price_high=105.0)
        ok, replaced = mgr.add_alert("BTCUSDT", 103.0, "up")

        assert (ok, replaced) == (True, True)
        assert mgr.get_all_alerts()[0].alerts[0].is_zone is False

    def test_bad_direction_and_price_rejected(self, tmp_path):
        mgr = AlertsManager(storage_path=str(tmp_path / "a.json"))
        assert mgr.add_alert("BTCUSDT", 100.0, "sideways") == (False, False)
        assert mgr.add_alert("BTCUSDT", 0, "up") == (False, False)

    def test_remove_zone_by_center(self, tmp_path):
        mgr = AlertsManager(storage_path=str(tmp_path / "a.json"))
        mgr.add_alert("BTCUSDT", 102.5, "up", price_low=100.0, price_high=105.0)

        assert mgr.remove_alert("BTCUSDT", 102.5, "up") is True
        assert mgr.get_all_alerts() == []
