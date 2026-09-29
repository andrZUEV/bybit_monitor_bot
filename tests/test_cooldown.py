# test_cooldown.py
"""Тесты src/core/cooldown.py."""
from __future__ import annotations

import time
from pathlib import Path

from src.core.cooldown import CooldownManager


class TestCooldownManager:
    def test_first_send_allowed(self, tmp_data_dir: Path):
        cd = CooldownManager()
        assert cd.can_send("SOLUSDT:100:up") is True

    def test_second_send_blocked(self, tmp_data_dir: Path):
        cd = CooldownManager()
        key = "SOLUSDT:100:up"
        cd.mark_sent(key)
        assert cd.can_send(key) is False

    def test_different_keys_independent(self, tmp_data_dir: Path):
        cd = CooldownManager()
        cd.mark_sent("A")
        assert cd.can_send("B") is True

    def test_expired_cooldown_allowed(self, tmp_data_dir: Path, monkeypatch):
        # Патчим cooldown_minutes через Config, если он там.
        from src.utils.config import Config
        monkeypatch.setattr(Config, "ALERT_COOLDOWN_MINUTES", 0.001, raising=False)
        cd = CooldownManager()
        key = "SOLUSDT:100:up"
        cd.mark_sent(key)
        time.sleep(0.1)  # 0.001 мин = 0.06 сек
        assert cd.can_send(key) is True

    def test_persists_across_instances(self, tmp_data_dir: Path):
        cd1 = CooldownManager()
        cd1.mark_sent("KEY")

        cd2 = CooldownManager()
        assert cd2.can_send("KEY") is False