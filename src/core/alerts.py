"""
Модуль управления алертами
"""

import json
import logging
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass, asdict
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class AlertRule:
    """Правило алерта для конкретного уровня"""
    price: float
    direction: str  # 'up', 'down', 'any'
    setup_note: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AlertRule':
        return cls(
            price=data['price'], 
            direction=data['direction'], 
            setup_note=data.get('setup_note', '')
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
    def __init__(self, storage_path: str = "data/alerts.json"):
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.assets: List[Asset] = []
        self.load()
    
    def load(self):
        if not self.storage_path.exists():
            logger.info(f"Файл {self.storage_path} не найден, начинаем с пустого списка")
            self.assets = []
            return
        try:
            with open(self.storage_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.assets = [Asset.from_dict(a) for a in data.get('assets', [])]
        except Exception as e:
            logger.error(f"Ошибка загрузки алертов: {e}")
            self.assets = []
    
    def save(self):
        try:
            data = {'assets': [a.to_dict() for a in self.assets]}
            with open(self.storage_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Ошибка сохранения алертов: {e}")
    
    def add_alert(
        self,
        symbol: str,
        price: float,
        direction: str,
        category: str = "linear",
        setup_note: str = ""
    ) -> Tuple[bool, bool]:
        """
        Добавляет алерт. Если алерт с такой ценой уже существует, заменяет его.
        
        Returns:
            (success, replaced) - успешно ли добавлен и был ли заменен старый
        """
        symbol = symbol.upper()
        if direction not in ['up', 'down', 'any']:
            return False, False
        
        new_rule = AlertRule(price=price, direction=direction, setup_note=setup_note.strip())
        
        for asset in self.assets:
            if asset.symbol == symbol:
                # Проверяем, есть ли алерт с такой же ценой
                existing_idx = None
                for i, rule in enumerate(asset.alerts):
                    if rule.price == price:
                        existing_idx = i
                        break
                
                if existing_idx is not None:
                    # Заменяем старый алерт новым
                    old_rule = asset.alerts[existing_idx]
                    asset.alerts[existing_idx] = new_rule
                    self.save()
                    logger.info(f"🔄 Алерт {symbol} @ {price} заменен: {old_rule.direction} -> {direction}")
                    return True, True
                
                # Если цены разные, просто добавляем
                asset.alerts.append(new_rule)
                self.save()
                return True, False
        
        # Актив не найден, создаем новый
        new_asset = Asset(symbol=symbol, category=category, alerts=[new_rule])
        self.assets.append(new_asset)
        self.save()
        return True, False
    
    def remove_alert(self, symbol: str, price: float, direction: str) -> bool:
        symbol = symbol.upper()
        target_rule = AlertRule(price=price, direction=direction, setup_note="")
        
        for asset in self.assets:
            if asset.symbol == symbol:
                if target_rule in asset.alerts:
                    asset.alerts.remove(target_rule)
                    if not asset.alerts:
                        self.assets.remove(asset)
                    self.save()
                    return True
        return False
    
    def remove_all_alerts_for_symbol(self, symbol: str) -> bool:
        """Удаляет ВСЕ алерты для указанного символа"""
        symbol = symbol.upper()
        initial_len = len(self.assets)
        self.assets = [a for a in self.assets if a.symbol != symbol]
        
        if len(self.assets) != initial_len:
            self.save()
            return True
        return False
    
    def get_all_alerts(self) -> List[Asset]:
        return self.assets.copy()
    
    def clear_all(self):
        """Очищает все алерты (для тестирования)"""
        self.assets = []
        self.save()