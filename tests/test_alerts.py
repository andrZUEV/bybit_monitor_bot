# test_alerts.py
"""Тесты src/core/alerts.py — add/remove/save/load + потокобезопасность."""
from __future__ import annotations

import threading
from pathlib import Path

from src.core.alerts import AlertsManager


class TestAlertsManager:
    def test_add_and_get(self, tmp_data_dir: Path):
        mgr = AlertsManager()
        mgr.add(symbol="SOLUSDT", price=100.0, direction="up", note="test")
        all_alerts = mgr.get_all_alerts()
        assert len(all_alerts) == 1
        a = all_alerts[0]
        assert a["symbol"] == "SOLUSDT"
        assert a["price"] == 100.0
        assert a["direction"] == "up"

    def test_remove(self, tmp_data_dir: Path):
        mgr = AlertsManager()
        mgr.add(symbol="SOLUSDT", price=100.0, direction="up", note="")
        alert_id = mgr.get_all_alerts()[0]["id"]
        mgr.remove(alert_id)
        assert mgr.get_all_alerts() == []

    def test_persists_to_disk(self, tmp_data_dir: Path):
        mgr = AlertsManager()
        mgr.add(symbol="BTCUSDT", price=50000.0, direction="down", note="x")

        mgr2 = AlertsManager()
        mgr2.load()
        assert len(mgr2.get_all_alerts()) == 1
        assert mgr2.get_all_alerts()[0]["symbol"] == "BTCUSDT"

    def test_load_corrupt_json_does_not_crash(self, tmp_data_dir: Path):
        # Пишем мусор в файл, который читает менеджер.
        from src.core import alerts as alerts_mod
        path = getattr(alerts_mod, "ALERTS_FILE", None)
        if path is None:
            # Если путь внутри Config — достаём через Config.
            from src.utils.config import Config
            path = Config.ALERTS_FILE
        Path(path).write_text("{not valid json", encoding="utf-8")

        mgr = AlertsManager()
        mgr.load()
        assert isinstance(mgr.get_all_alerts(), list)

    def test_concurrent_adds_are_thread_safe(self, tmp_data_dir: Path):
        mgr = AlertsManager()

        def worker(n: int) -> None:
            for i in range(n):
                mgr.add(symbol=f"T{i}USDT", price=float(i), direction="up", note="")

        threads = [threading.Thread(target=worker, args=(20,)) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(mgr.get_all_alerts()) == 100