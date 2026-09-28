"""
Модуль управления алертами с поддержкой времени создания и потокобезопасности
"""

import json
import logging
import time
import threading
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, asdict, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class AlertRule:
    """Правило алерта для конкретного уровня"""
    price: float
    direction: str  # 'up', 'down', 'any'
    setup_note: str = ""
    created_at: float = field(default_factory=time.time)
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AlertRule':
        return cls(
            price=float(data['price']),  # <-- ЯВНОЕ ПРЕОБРАЗОВАНИЕ В FLOAT
            direction=data['direction'], 
            setup_note=data.get('setup_note', ''),
            created_at=float(data.get('created_at', time.time())) # <-- И ЗДЕСЬ ТОЖЕ
        )
    
    def __eq__(self, other):
        if not isinstance(other, AlertRule):
            return False
        return self.price == other.price and self.direction == other.direction


@dataclass
class Asset:
    """Актив с набором правил алертов"""
    symbol: str
    category: str
    alerts: List[AlertRule]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'symbol': self.symbol,
            'category': self.category,
            'alerts': [a.to_dict() for a in self.alerts]
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Asset':
        return cls(
            symbol=data['symbol'],
            category=data.get('category', 'linear'),
            alerts=[AlertRule.from_dict(a) for a in data.get('alerts', [])]
        )


class AlertsManager:
    """
    Потокобезопасный менеджер алертов.
    Все операции чтения/записи защищены threading.Lock.
    """
    
    # Глобальный lock для всех инстансов (на случай если создаётся несколько раз)
    _global_lock = threading.Lock()
    
    def __init__(self, storage_path: str = "data/alerts.json"):
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.assets: List[Asset] = []
        self._lock = threading.Lock()
        self.load()
    
    def load(self):
        """Загружает алерты из файла (потокобезопасно)"""
        with self._lock:
            if not self.storage_path.exists():
                logger.info(f"Файл {self.storage_path} не найден, начинаем с пустого списка")
                self.assets = []
                return
            try:
                with open(self.storage_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.assets = [Asset.from_dict(a) for a in data.get('assets', [])]
                logger.debug(f"Загружено {len(self.assets)} активов")
            except json.JSONDecodeError as e:
                logger.error(f"Ошибка парсинга JSON: {e}. Пробуем .bak")
                self._try_restore_backup()
            except Exception as e:
                logger.error(f"Ошибка загрузки алертов: {e}")
                self.assets = []
    
    def _try_restore_backup(self):
        """Пытается восстановить из .bak файла"""
        backup_path = self.storage_path.with_suffix('.json.bak')
        if backup_path.exists():
            try:
                with open(backup_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.assets = [Asset.from_dict(a) for a in data.get('assets', [])]
                logger.warning(f"Восстановлено из .bak: {len(self.assets)} активов")
                return
            except Exception as e:
                logger.error(f"Не удалось восстановить из .bak: {e}")
        self.assets = []
    
    def save(self):
        """
        Атомарное сохранение с .bak бэкапом.
        Потокобезопасно через threading.Lock.
        """
        with self._lock:
            try:
                data = {'assets': [a.to_dict() for a in self.assets]}
                
                # Сначала пишем в .tmp
                tmp_path = self.storage_path.with_suffix('.json.tmp')
                with open(tmp_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                    f.flush()
                    import os
                    os.fsync(f.fileno())
                
                # Создаём .bak из текущего файла
                if self.storage_path.exists():
                    backup_path = self.storage_path.with_suffix('.json.bak')
                    try:
                        if backup_path.exists():
                            backup_path.unlink()
                        self.storage_path.rename(backup_path)
                    except Exception as e:
                        logger.warning(f"Не удалось создать .bak: {e}")
                
                # Атомарно переименовываем .tmp -> .json
                tmp_path.rename(self.storage_path)
                
            except Exception as e:
                logger.error(f"Ошибка сохранения алертов: {e}")
                # Пытаемся восстановить из .bak
                self._try_restore_backup()
    
    def get_all_alerts(self) -> List[Asset]:
        """Возвращает копию списка активов (потокобезопасно)"""
        with self._lock:
            return list(self.assets)
    
    def get_asset(self, symbol: str) -> Optional[Asset]:
        """Находит актив по символу"""
        with self._lock:
            for asset in self.assets:
                if asset.symbol == symbol:
                    return asset
            return None
    
    def add_asset(self, asset: Asset):
        """Добавляет новый актив"""
        with self._lock:
            # Проверяем дубликаты
            for existing in self.assets:
                if existing.symbol == asset.symbol:
                    return False
            self.assets.append(asset)
        self.save()
        return True
    
    def remove_asset(self, symbol: str) -> bool:
        """Удаляет актив по символу"""
        with self._lock:
            initial_len = len(self.assets)
            self.assets = [a for a in self.assets if a.symbol != symbol]
            removed = len(self.assets) < initial_len
        if removed:
            self.save()
        return removed
    
    def add_alert(self, symbol: str, category: str, price: float, direction: str, setup_note: str = "") -> bool:
        """Добавляет алерт к существующему или новому активу"""
        with self._lock:
            asset = None
            for a in self.assets:
                if a.symbol == symbol:
                    asset = a
                    break
            
            if asset is None:
                asset = Asset(symbol=symbol, category=category, alerts=[])
                self.assets.append(asset)
            
            new_rule = AlertRule(
                price=price,
                direction=direction,
                setup_note=setup_note,
                created_at=time.time()
            )
            
            # Проверяем дубликаты
            if new_rule in asset.alerts:
                return False
            
            asset.alerts.append(new_rule)
        
        self.save()
        return True
    
    def remove_alert(self, symbol: str, price: float, direction: str) -> bool:
        """Удаляет конкретный алерт"""
        with self._lock:
            asset = self.get_asset(symbol)
            if asset is None:
                return False
            
            initial_len = len(asset.alerts)
            asset.alerts = [
                a for a in asset.alerts 
                if not (a.price == price and a.direction == direction)
            ]
            removed = len(asset.alerts) < initial_len
            
            # Удаляем актив, если алертов не осталось
            if not asset.alerts:
                self.assets = [a for a in self.assets if a.symbol != symbol]
        
        if removed:
            self.save()
        return removed
    
    def clear_all(self):
        """Очищает все алерты"""
        with self._lock:
            self.assets = []
        self.save()