"""Тесты src/core/cooldown.py.

Поля CooldownManager (из cd.__dict__):
- cooldown_seconds: int (25 мин → 1500)
- storage_path: Path
- cooldowns: dict
- _lock

API:
- can_send(symbol, price, direction) -> bool
- mark_sent(symbol, price, direction)
- reset(symbol, price, direction)
- clear_all()
- get_stats() -> dict
- load() / save()
"""
from __future__ import annotations

from src.core.cooldown import CooldownManager


class TestCooldownManager:
    def test_first_send_allowed(self, cooldowns_path: str):
        cd = CooldownManager(cooldown_minutes=25, storage_path=cooldowns_path)
        assert cd.can_send("SOLUSDT", 100.0, "up") is True

    def test_second_send_blocked(self, cooldowns_path: str):
        cd = CooldownManager(cooldown_minutes=25, storage_path=cooldowns_path)
        cd.mark_sent("SOLUSDT", 100.0, "up")
        assert cd.can_send("SOLUSDT", 100.0, "up") is False

    def test_different_keys_independent(self, cooldowns_path: str):
        cd = CooldownManager(cooldown_minutes=25, storage_path=cooldowns_path)
        cd.mark_sent("SOLUSDT", 100.0, "up")

        # Другой символ
        assert cd.can_send("ETHUSDT", 100.0, "up") is True
        # Другая цена
        assert cd.can_send("SOLUSDT", 101.0, "up") is True
        # Другое направление
        assert cd.can_send("SOLUSDT", 100.0, "down") is True

    def test_reset_allows_send_again(self, cooldowns_path: str):
        cd = CooldownManager(cooldown_minutes=25, storage_path=cooldowns_path)
        cd.mark_sent("SOLUSDT", 100.0, "up")
        assert cd.can_send("SOLUSDT", 100.0, "up") is False

        cd.reset("SOLUSDT", 100.0, "up")
        assert cd.can_send("SOLUSDT", 100.0, "up") is True

    def test_expired_cooldown_allowed(self, cooldowns_path: str, monkeypatch):
        """После истечения окна can_send снова True.

        Патчим cooldown_seconds=0, чтобы не ждать реальное окно.
        """
        cd = CooldownManager(cooldown_minutes=25, storage_path=cooldowns_path)
        monkeypatch.setattr(cd, "cooldown_seconds", 0, raising=False)

        cd.mark_sent("SOLUSDT", 100.0, "up")
        assert cd.can_send("SOLUSDT", 100.0, "up") is True

    def test_persists_across_instances(self, cooldowns_path: str):
        """Кулдаун сохраняется на диск и восстанавливается.

        Требует исправленного save() через mkstemp — иначе на
        Windows os.replace падает с WinError 183.
        """
        cd1 = CooldownManager(cooldown_minutes=25, storage_path=cooldowns_path)
        cd1.mark_sent("SOLUSDT", 100.0, "up")

        cd2 = CooldownManager(cooldown_minutes=25, storage_path=cooldowns_path)
        assert cd2.can_send("SOLUSDT", 100.0, "up") is False

    def test_get_stats(self, cooldowns_path: str):
        cd = CooldownManager(cooldown_minutes=25, storage_path=cooldowns_path)
        cd.mark_sent("SOLUSDT", 100.0, "up")
        stats = cd.get_stats()
        assert isinstance(stats, dict)