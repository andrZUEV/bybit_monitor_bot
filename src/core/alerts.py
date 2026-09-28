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
    def from_dict(cls, data: Dict[str, Any]) -> Optional['AlertRule']:
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
    alerts: List[AlertRule]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'symbol': self.symbol,
            'category': self.category,
            'alerts': [a.to_dict() for a in self.alerts]
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Asset':
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
                self.assets = []
                return
            try:
                with open(self.storage_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # Фильтруем только валидные активы
                valid_assets = []
                for a in data.get('assets', []):
                    if isinstance(a, dict):
                        asset = Asset.from_dict(a)
                        if asset.symbol != 'UNKNOWN' or asset.alerts:
                            valid_assets.append(asset)
                
                self.assets = valid_assets
                
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
                
                valid_assets = []
                for a in data.get('assets', []):
                    if isinstance(a, dict):
                        asset = Asset.from_dict(a)
                        if asset.symbol != 'UNKNOWN' or asset.alerts:
                            valid_assets.append(asset)
                
                self.assets = valid_assets
                logger.warning(f"✅ Восстановлено из .bak: {len(self.assets)} активов")
                return
            except Exception as e:
                logger.error(f"Не удалось восстановить из .bak: {e}")
        self.assets = []
    
    def save(self):
        """Атомарное сохранение с .bak бэкапом. Потокобезопасно."""
        with self._lock:
            try:
                data = {'assets': [a.to_dict() for a in self.assets]}
                
                tmp_path = self.storage_path.with_suffix('.json.tmp')
                with open(tmp_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                    f.flush()
                    import os
                    os.fsync(f.fileno())
                
                if self.storage_path.exists():
                    backup_path = self.storage_path.with_suffix('.json.bak')
                    try:
                        if backup_path.exists():
                            backup_path.unlink()
                        self.storage_path.rename(backup_path)
                    except Exception:
                        pass
                
                tmp_path.rename(self.storage_path)
                
            except Exception as e:
                logger.error(f"Ошибка сохранения алертов: {e}")
                self._try_restore_backup()
    
    def get_all_alerts(self) -> List[Asset]:
        """Возвращает копию списка активов (потокобезопасно)"""
        with self._lock:
            return list(self.assets)
    
    def add_alert(self, symbol: str, price: float, direction: str, category: str = "linear", setup_note: str = "") -> tuple:
        """Добавляет алерт. Возвращает (success, replaced)"""
        symbol = symbol.upper()
        if direction not in ['up', 'down', 'any']:
            return False, False
        
        new_rule = AlertRule(price=float(price), direction=direction, setup_note=setup_note.strip())
        
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