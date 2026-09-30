"""
Модуль управления кулдаунами для алертов.
Потокобезопасный, с автоматической очисткой старых записей.
"""

import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class CooldownManager:
    """
    Менеджер кулдаунов с потокобезопасностью и автоочисткой.
    
    Ключ кулдауна: f"{symbol}_{price}_{direction}"
    Значение: timestamp последней отправки
    """
    
    # Максимальный возраст записи (7 дней) — после этого удаляется
    MAX_AGE_SECONDS = 7 * 24 * 3600
    
    def __init__(
        self, 
        cooldown_minutes: int = 25,
        storage_path: str = "data/cooldowns.json"
    ):
        self.cooldown_seconds = cooldown_minutes * 60
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.cooldowns: dict[str, float] = {}
        self._lock = threading.RLock()
        self.load()
    
    def _make_key(self, symbol: str, price: float, direction: str) -> str:
        """Формирует уникальный ключ для кулдауна"""
        return f"{symbol}_{price}_{direction}"
    
    def load(self):
        """Загружает кулдауны из файла (потокобезопасно)"""
        with self._lock:
            if not self.storage_path.exists():
                self.cooldowns = {}
                return
            try:
                with open(self.storage_path, encoding='utf-8') as f:
                    data = json.load(f)
                self.cooldowns = data.get('cooldowns', {})
                # Сразу чистим старые записи при загрузке
                self._cleanup_locked()
            except Exception as e:
                logger.error(f"Ошибка загрузки кулдаунов: {e}")
                self.cooldowns = {}
    
    def save(self) -> None:
        """Атомарное сохранение кулдаунов через mkstemp + os.replace.

        На Windows os.replace падает, если tmp уже существует —
        используем уникальные имена. Потокобезопасно, чистит старые
        записи перед сохранением.
        """
        with self._lock:
            try:
                self._cleanup_locked()

                path = Path(self.storage_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                data = {"cooldowns": self.cooldowns}

                fd, tmp_name = tempfile.mkstemp(
                    prefix=path.name + ".", suffix=".tmp", dir=str(path.parent),
                )
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp_name, path)
                except Exception:
                    try:
                        os.unlink(tmp_name)
                    except OSError:
                        pass
                    raise
            except Exception as e:
                logger.error(f"Ошибка сохранения кулдаунов: {e}")
    
    def _cleanup_locked(self):
        """
        Удаляет записи старше MAX_AGE_SECONDS.
        ВАЖНО: должен вызываться внутри with self._lock!
        """
        now = time.time()
        old_keys = [
            key for key, ts in self.cooldowns.items()
            if (now - ts) > self.MAX_AGE_SECONDS
        ]
        for key in old_keys:
            del self.cooldowns[key]
        
        if old_keys:
            logger.debug(f"Удалено {len(old_keys)} старых записей кулдаунов")
    
    def can_send(self, symbol: str, price: float, direction: str) -> bool:
        """
        Проверяет, можно ли отправить алерт (не в кулдауне).
        Потокобезопасно.
        """
        key = self._make_key(symbol, price, direction)
        with self._lock:
            last_sent = self.cooldowns.get(key, 0)
            return (time.time() - last_sent) >= self.cooldown_seconds
    
    def mark_sent(self, symbol: str, price: float, direction: str):
        """
        Помечает алерт как отправленный (запускает кулдаун).
        Потокобезопасно.
        """
        key = self._make_key(symbol, price, direction)
        with self._lock:
            self.cooldowns[key] = time.time()
        self.save()
    
    def reset(self, symbol: str, price: float, direction: str):
        """Сбрасывает кулдаун для конкретного алерта"""
        key = self._make_key(symbol, price, direction)
        with self._lock:
            if key in self.cooldowns:
                del self.cooldowns[key]
        self.save()
    
    def clear_all(self):
        """Очищает все кулдауны"""
        with self._lock:
            self.cooldowns = {}
        self.save()
    
    def get_stats(self) -> dict[str, Any]:
        """Возвращает статистику по кулдаунам"""
        with self._lock:
            now = time.time()
            active = sum(
                1 for ts in self.cooldowns.values()
                if (now - ts) < self.cooldown_seconds
            )
            return {
                'total': len(self.cooldowns),
                'active': active,
                'cooldown_seconds': self.cooldown_seconds
            }