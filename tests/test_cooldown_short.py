"""Тесты короткого кулдауна после сбоя отправки в Telegram."""
from __future__ import annotations

import time

from src.core.cooldown import CooldownManager


class TestShortCooldown:
    def test_short_cooldown_blocks_and_expires(self, cooldowns_path, monkeypatch):
        cd = CooldownManager(cooldown_minutes=25, storage_path=cooldowns_path)
        assert cd.can_send("BTCUSDT", 100.0, "up") is True

        cd.mark_sent("BTCUSDT", 100.0, "up", seconds=60)
        assert cd.can_send("BTCUSDT", 100.0, "up") is False
        assert cd.short_cooldown_active("BTCUSDT", 100.0, "up") is True

        # Сдвигаем время на 61 секунду вперёд.
        now = time.time()
        monkeypatch.setattr(
            "src.core.cooldown.time.time", lambda: now + 61
        )
        assert cd.can_send("BTCUSDT", 100.0, "up") is True
        assert cd.short_cooldown_active("BTCUSDT", 100.0, "up") is False

    def test_normal_mark_clears_short(self, cooldowns_path):
        cd = CooldownManager(cooldown_minutes=25, storage_path=cooldowns_path)
        cd.mark_sent("BTCUSDT", 100.0, "up", seconds=60)
        assert cd.short_cooldown_active("BTCUSDT", 100.0, "up") is True

        cd.mark_sent("BTCUSDT", 100.0, "up")
        assert cd.short_cooldown_active("BTCUSDT", 100.0, "up") is False
        assert cd.can_send("BTCUSDT", 100.0, "up") is False

    def test_reset_clears_short(self, cooldowns_path):
        cd = CooldownManager(cooldown_minutes=25, storage_path=cooldowns_path)
        cd.mark_sent("BTCUSDT", 100.0, "up", seconds=60)

        cd.reset("BTCUSDT", 100.0, "up")
        assert cd.short_cooldown_active("BTCUSDT", 100.0, "up") is False
        assert cd.can_send("BTCUSDT", 100.0, "up") is True
