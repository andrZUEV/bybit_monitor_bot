"""Тесты восстановления alerts.json: битый файл, .bak, сохранение памяти."""
from __future__ import annotations

import json

from src.core.alerts import AlertsManager


def _add(m: AlertsManager, price: float, symbol: str = "BTCUSDT") -> None:
    ok, _ = m.add_alert(symbol, price, "up", "linear", "")
    assert ok


class TestBackupRecovery:
    def test_backup_created_on_second_save(self, tmp_path):
        path = tmp_path / "alerts.json"
        m = AlertsManager(str(path))
        _add(m, 100.0)
        _add(m, 101.0)

        backup = tmp_path / "alerts.json.bak"
        assert backup.exists()
        data = json.loads(backup.read_text(encoding="utf-8"))
        assert len(data["assets"][0]["alerts"]) == 1

    def test_corrupt_main_restores_from_backup(self, tmp_path):
        path = tmp_path / "alerts.json"
        m = AlertsManager(str(path))
        _add(m, 100.0)
        _add(m, 101.0)

        path.write_text("not json {{{", encoding="utf-8")

        restored = AlertsManager(str(path))
        assert len(restored.get_all_alerts()) == 1
        assert restored.get_all_alerts()[0].alerts[0].price == 100.0

    def test_corrupt_without_backup_keeps_memory(self, tmp_path):
        path = tmp_path / "alerts.json"
        m = AlertsManager(str(path))
        _add(m, 100.0)
        _add(m, 101.0)

        # Убираем .bak и портим основной файл: загрузка не должна
        # обнулять уже загруженные алерты.
        (tmp_path / "alerts.json.bak").unlink()
        path.write_text("not json", encoding="utf-8")
        m.load()

        assert len(m.get_all_alerts()) == 1
        assert len(m.get_all_alerts()[0].alerts) == 2

    def test_save_after_corruption_keeps_good_backup(self, tmp_path):
        path = tmp_path / "alerts.json"
        m = AlertsManager(str(path))
        _add(m, 100.0)
        _add(m, 101.0)

        path.write_text("broken", encoding="utf-8")
        m.load()          # восстановились из .bak (1 алерт)
        m.save()          # перезаписали битый файл

        new = AlertsManager(str(path))
        assert len(new.get_all_alerts()) == 1
        backup = tmp_path / "alerts.json.bak"
        assert backup.exists()
        json.loads(backup.read_text(encoding="utf-8"))
