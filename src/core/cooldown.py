"""
Менеджер кулдаунов для предотвращения спама алертов
"""

import json
import time
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

class CooldownManager:
    def __init__(self, storage_path: str = "data/cooldowns.json", cooldown_minutes: int = 25):
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.cooldown_seconds = cooldown_minutes * 60
        self.alerts = {}
        self.load()

    def load(self):
        if self.storage_path.exists():
            try:
                with open(self.storage_path, 'r', encoding='utf-8') as f:
                    self.alerts = json.load(f)
            except Exception as e:
                logger.error(f"Ошибка загрузки кулдаунов: {e}")
                self.alerts = {}

    def save(self):
        try:
            with open(self.storage_path, 'w', encoding='utf-8') as f:
                json.dump(self.alerts, f, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения кулдаунов: {e}")

    def can_send(self, symbol: str, level: float, direction: str) -> bool:
        """Проверяет, можно ли отправить алерт"""
        key = f"{symbol}_{level}_{direction}"
        now = time.time()
        last_time = self.alerts.get(key, 0.0)
        
        time_passed = now - last_time
        if time_passed >= self.cooldown_seconds:
            return True
        
        # Для отладки можно раскомментировать:
        # remaining = int(self.cooldown_seconds - time_passed)
        # logger.debug(f"⏳ Кулдаун для {key}: осталось {remaining} сек")
        return False

    def mark_sent(self, symbol: str, level: float, direction: str):
        """Фиксирует факт отправки алерта"""
        key = f"{symbol}_{level}_{direction}"
        self.alerts[key] = time.time()
        self.save()
        logger.info(f"🔒 Кулдаун установлен для {key} на {self.cooldown_seconds // 60} мин")