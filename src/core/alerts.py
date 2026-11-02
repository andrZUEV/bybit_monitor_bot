"""
Модуль управления алертами с поддержкой времени создания и потокобезопасности
"""

import json
import logging
import os
import shutil
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Максимальная длина заметки к алерту: защита alerts.json от раздувания
# и от вставки мегабайтного текста через Telegram.
MAX_NOTE_LENGTH = 500


@dataclass
class AlertRule:
    """Правило алерта для конкретного уровня"""
    price: float
    direction: str  # 'up', 'down', 'any'
    setup_note: str = ""
    created_at: float = field(default_factory=time.time)
    
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Optional['AlertRule']:
        """Безопасное создание из словаря с игнорированием битых записей"""
        try:
            # Если data - это строка (например, случайно записалось "up" вместо dict), игнорируем
            if isinstance(data, str):
                return None
            
            price = float(data.get('price', 0))
            direction = str(data.get('direction', 'any'))
            setup_note = str(data.get('setup_note', ''))
            created_at = float(data.get('created_at', time.time()))
            
            return cls(
                price=price,
                direction=direction,
                setup_note=setup_note,
                created_at=created_at
            )
        except (ValueError, TypeError, AttributeError) as e:
            logger.warning(f"⚠️ Пропущен некорректный алерт: {data}. Ошибка: {e}")
            return None
    
    def __eq__(self, other):
        if not isinstance(other, AlertRule):
            return False
        return self.price == other.price and self.direction == other.direction


@dataclass
class Asset:
    """Актив с набором правил алертов"""
    symbol: str
    category: str
    alerts: list[AlertRule]
    
    def to_dict(self) -> dict[str, Any]:
        return {
            'symbol': self.symbol,
            'category': self.category,
            'alerts': [a.to_dict() for a in self.alerts]
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'Asset':
        """Безопасное создание актива с фильтрацией битых алертов"""
        alerts = []
        raw_alerts = data.get('alerts', [])
        
        if isinstance(raw_alerts, list):
            for a in raw_alerts:
                rule = AlertRule.from_dict(a)
                if rule:  # Добавляем только валидные правила
                    alerts.append(rule)
        
        return cls(
            symbol=str(data.get('symbol', 'UNKNOWN')),
            category=str(data.get('category', 'linear')),
            alerts=alerts
        )


class AlertsManager:
    """
    Потокобезопасный менеджер алертов.
    Все операции чтения/записи защищены threading.Lock.
    """
    
    _global_lock = threading.RLock()
    
    def __init__(self, storage_path: str = "data/alerts.json"):
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.assets: list[Asset] = []
        self._lock = threading.RLock()
        self.load()
    
    @staticmethod
    def _read_assets(path: Path) -> list[Asset] | None:
        """
        Читает файл алертов и возвращает список Asset.

        None — файл отсутствует/битый/не того формата. Вызывающий решает,
        что делать: восстановить из .bak или оставить текущее состояние.
        """
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return None
            assets: list[Asset] = []
            for a in data.get("assets", []):
                if isinstance(a, dict):
                    asset = Asset.from_dict(a)
                    if asset.symbol != "UNKNOWN" or asset.alerts:
                        assets.append(asset)
            return assets
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            return None

    def load(self):
        """
        Загружает алерты из файла (потокобезопасно).

        При ошибке чтения НЕ обнуляет текущее состояние: сначала пробует
        .bak, иначе оставляет то, что уже в памяти. Иначе транзиентная
        ошибка I/O + следующее сохранение затирали бы все алерты.
        """
        with self._lock:
            assets = self._read_assets(self.storage_path)
            if assets is not None:
                self.assets = assets
                return

            if not self.storage_path.exists():
                # Файла нет (первый запуск или его удалили) — не трогаем
                # память: при старте она и так пустая.
                return

            logger.error(
                "⚠️ Не удалось прочитать alerts.json — пробую .bak"
            )
            backup_path = self.storage_path.with_suffix(".json.bak")
            backup = self._read_assets(backup_path)
            if backup is not None:
                self.assets = backup
                logger.warning(
                    f"✅ Восстановлено из .bak: {len(self.assets)} активов"
                )
                return

            logger.error(
                "❌ .bak тоже недоступен — оставляю алерты в памяти как есть"
            )

    def save(self) -> None:
        """Атомарная запись через mkstemp + os.replace.

        Весь блок под self._lock — иначе при параллельных add_alert
        два потока одновременно зовут os.replace на одном dst, и на
        Windows это даёт WinError 5.

        .bak обновляется КОПИЕЙ только если текущий файл валиден: битый
        файл не должен вытеснять последнюю хорошую копию.
        """
        with self._lock:
            path = Path(self.storage_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {"assets": [a.to_dict() for a in self.assets]}
            fd, tmp_name = tempfile.mkstemp(
                prefix=path.name + ".", suffix=".tmp", dir=str(path.parent),
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                if path.exists() and self._read_assets(path) is not None:
                    backup_path = path.with_suffix(".json.bak")
                    try:
                        shutil.copy2(path, backup_path)
                    except OSError as e:
                        logger.warning(f"⚠️ Не удалось обновить .bak: {e}")
                os.replace(tmp_name, path)
            except Exception:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
    
    def get_all_alerts(self) -> list[Asset]:
        """Возвращает копию списка активов (потокобезопасно)"""
        with self._lock:
            return list(self.assets)
    
    def add_alert(
        self,
        symbol: str,
        price: float,
        direction: str,
        category: str = "linear",
        setup_note: str = "",
    ) -> tuple:

        """Добавляет алерт. Возвращает (success, replaced)"""
        symbol = symbol.upper()
        if direction not in ['up', 'down', 'any']:
            return False, False
        
        new_rule = AlertRule(
            price=float(price),
            direction=direction,
            setup_note=(setup_note or "").strip()[:MAX_NOTE_LENGTH],
        )
        
        with self._lock:
            for asset in self.assets:
                if asset.symbol == symbol:
                    existing_idx = None
                    for i, rule in enumerate(asset.alerts):
                        if rule.price == new_rule.price:
                            existing_idx = i
                            break
                    
                    if existing_idx is not None:
                        old_rule = asset.alerts[existing_idx]
                        new_rule.created_at = old_rule.created_at  # Сохраняем время создания
                        asset.alerts[existing_idx] = new_rule
                        self.save()
                        return True, True
                    
                    asset.alerts.append(new_rule)
                    self.save()
                    return True, False
            
            new_asset = Asset(symbol=symbol, category=category, alerts=[new_rule])
            self.assets.append(new_asset)
        
        self.save()
        return True, False
    
    def remove_alert(self, symbol: str, price: float, direction: str) -> bool:
        """Удаляет конкретный алерт"""
        symbol = symbol.upper()
        with self._lock:
            for asset in self.assets:
                if asset.symbol == symbol:
                    initial_len = len(asset.alerts)
                    asset.alerts = [
                        a for a in asset.alerts 
                        if not (a.price == float(price) and a.direction == direction)
                    ]
                    if len(asset.alerts) < initial_len:
                        if not asset.alerts:
                            self.assets.remove(asset)
                        self.save()
                        return True
        return False
    
    def remove_all_alerts_for_symbol(self, symbol: str) -> bool:
        """Удаляет все алерты для символа"""
        symbol = symbol.upper()
        with self._lock:
            initial_len = len(self.assets)
            self.assets = [a for a in self.assets if a.symbol != symbol]
            removed = len(self.assets) < initial_len
        
        if removed:
            self.save()
        return removed
    
    def clear_all(self):
        """Очищает все алерты"""
        with self._lock:
            self.assets = []
        self.save()