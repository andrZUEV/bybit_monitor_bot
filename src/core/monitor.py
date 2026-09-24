"""
Модуль мониторинга цен и объёмов
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

import time
import logging
from typing import Callable, Optional, Dict, Any
from dataclasses import dataclass, field
from enum import Enum

from src.api.bybit_client import BybitClient, TickerData
from src.core.alerts import AlertsManager, Asset, AlertRule

logger = logging.getLogger(__name__)


class CrossDirection(Enum):
    UP = "up"
    DOWN = "down"


@dataclass
class AlertEvent:
    event_type: str
    symbol: str
    category: str
    current_price: float
    message: str
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AssetState:
    prev_price: Optional[float] = None
    prev_volume: Optional[float] = None
    last_volume_alert_time: float = 0
    triggered_alerts: Dict[str, bool] = field(default_factory=dict)


class Monitor:
    def __init__(
        self,
        alerts_manager: AlertsManager,
        on_alert_callback: Callable[[AlertEvent], None],
        poll_interval: float = 4.0,
        volume_threshold: float = 5.0,
        volume_cooldown: float = 300.0,
        price_reset_threshold: float = 0.005,
        candle_interval: str = "15",
        candle_periods: int = 20,
        candle_volume_multiplier: float = 3.0
    ):
        self.alerts_manager = alerts_manager
        self.on_alert = on_alert_callback
        self.poll_interval = poll_interval
        self.volume_threshold = volume_threshold
        self.volume_cooldown = volume_cooldown
        self.price_reset_threshold = price_reset_threshold
        
        # Новые параметры для анализа свечей
        self.candle_interval = candle_interval
        self.candle_periods = candle_periods
        self.candle_volume_multiplier = candle_volume_multiplier
        
        self.client = BybitClient()
        self.states: Dict[str, AssetState] = {}
        self._running = False
        self._poll_count = 0
    
    def _check_price_cross(self, asset: Asset, ticker: TickerData, state: AssetState):
        if state.prev_price is None:
            return
        
        prev = state.prev_price
        curr = ticker.price
        
        for rule in asset.alerts:
            target = rule.price
            direction = rule.direction
            alert_key = f"{target}_{direction}"
            
            crossed_up = (prev < target and curr >= target)
            crossed_down = (prev > target and curr <= target)
            
            if not (crossed_up or crossed_down):
                if direction == 'up' and curr < target:
                    state.triggered_alerts[alert_key] = False
                elif direction == 'down' and curr > target:
                    state.triggered_alerts[alert_key] = False
                elif direction == 'any':
                    distance = abs(curr - target) / target if target > 0 else 0
                    if distance > self.price_reset_threshold:
                        state.triggered_alerts[alert_key] = False
                continue
            
            cross_type = CrossDirection.UP if crossed_up else CrossDirection.DOWN
            should_alert = (direction == 'any') or (direction == cross_type.value)
            is_triggered = state.triggered_alerts.get(alert_key, False)
            
            if should_alert and not is_triggered:
                cross_text = "🟢 СНИЗУ ВВЕРХ" if cross_type == CrossDirection.UP else "🔴 СВЕРХУ ВНИЗ"
                
                # Получаем свечи для анализа
                klines = self.client.get_klines(
                    asset.symbol, asset.category,
                    self.candle_interval, 30
                )
                
                # Получаем отношение объема
                vol_data = self.client.get_candle_volume_ratio(
                    asset.symbol, asset.category,
                    self.candle_interval, self.candle_periods
                )
                volume_ratio = vol_data["ratio"] if vol_data else 0.0
                
                # Вызываем анализатор
                from src.core.analyzer import analyze_candle_confirmation, format_confirmation_message
                
                if klines:
                    analysis = analyze_candle_confirmation(
                        klines=klines,
                        level=target,
                        direction=direction,
                        volume_ratio=volume_ratio
                    )
                    confirmation_block = format_confirmation_message(analysis)
                else:
                    confirmation_block = "\n⚠️ Не удалось получить данные свечей для анализа"
                
                note_text = f"\n📝 <b>Сетап:</b> <code>{rule.setup_note}</code>" if rule.setup_note else ""
                
                message = (
                    f"🚨 <b>Price Alert: {asset.symbol}</b>\n"
                    f"Уровень: <code>{target:,.2f}</code>\n"
                    f"Направление: {cross_text}\n"
                    f" Цена: <code>{curr:,.2f}</code>"
                    f"{confirmation_block}"
                    f"{note_text}"
                )
                
                event = AlertEvent(
                    event_type='price_cross',
                    symbol=asset.symbol,
                    category=asset.category,
                    current_price=curr,
                    message=message,
                    extra={
                        'target': target, 
                        'direction': direction, 
                        'volume_ratio': volume_ratio,
                        'analysis': analysis if klines else None
                    }
                )
                
                self.on_alert(event)
                state.triggered_alerts[alert_key] = True
                logger.info(f"🔔 Алерт сработал: {asset.symbol} @ {target} {direction}")
    
    def _check_candle_volume(self, asset: Asset, ticker: TickerData, state: AssetState):
        """Проверяет аномальный объем текущей свечи"""
        vol_data = self.client.get_candle_volume_ratio(
            asset.symbol, asset.category,
            self.candle_interval, self.candle_periods
        )
        
        if not vol_data:
            return
        
        ratio = vol_data["ratio"]
        
        # Проверяем, превышает ли объем порог
        if ratio < self.candle_volume_multiplier:
            return
        
        # Кулдаун
        now = time.time()
        if now - state.last_volume_alert_time < self.volume_cooldown:
            return
        
        message = (
            f"⚡️ <b>Candle Volume Alert: {asset.symbol}</b>\n"
            f"Объём свечи: <b>{ratio:.1f}x</b> от среднего\n"
            f"Таймфрейм: {self.candle_interval}m\n"
            f"💰 Цена: <code>{ticker.price:,.2f}</code>"
        )
        
        event = AlertEvent(
            event_type='candle_volume',
            symbol=asset.symbol,
            category=asset.category,
            current_price=ticker.price,
            message=message,
            extra={'volume_ratio': ratio, 'interval': self.candle_interval}
        )
        
        self.on_alert(event)
        state.last_volume_alert_time = now
        logger.info(f"⚡️ Candle volume alert: {asset.symbol} {ratio:.1f}x")
    
    def _poll_once(self):
        self.alerts_manager.load()
        assets = self.alerts_manager.get_all_alerts()
        
        if not assets:
            return
        
        self._poll_count += 1
        if self._poll_count % 15 == 0:
            logger.info(f"📡 Опрос #{self._poll_count} | Активов: {len(assets)}")
        
        for asset in assets:
            symbol = asset.symbol
            
            if symbol not in self.states:
                self.states[symbol] = AssetState()
            
            state = self.states[symbol]
            
            ticker = self.client.get_ticker(symbol, asset.category)
            if not ticker:
                continue
            
            self._check_price_cross(asset, ticker, state)
            self._check_candle_volume(asset, ticker, state)
            
            state.prev_price = ticker.price
            state.prev_volume = ticker.volume_24h
    
    def start(self):
        self._running = True
        logger.info(
            f"🚀 Мониторинг запущен. "
            f"Интервал: {self.poll_interval}s, "
            f"Свечи: {self.candle_interval}m, "
            f"Порог объема: {self.candle_volume_multiplier}x"
        )
        
        try:
            while self._running:
                try:
                    self._poll_once()
                except Exception as e:
                    logger.error(f"Ошибка в цикле опроса: {e}", exc_info=True)
                
                time.sleep(self.poll_interval)
        
        except KeyboardInterrupt:
            logger.info(" Мониторинг остановлен пользователем")
        finally:
            self._running = False
    
    def stop(self):
        self._running = False
        logger.info("Мониторинг останавливается...")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    print("🧪 Тестирование Monitor\n")
    
    test_file = "data/test_monitor_alerts.json"
    manager = AlertsManager(test_file)
    manager.clear_all()
    manager.add_alert("BTCUSDT", 85000, "up")
    
    def on_alert(event: AlertEvent):
        print(f"\n🔔 ПОЛУЧЕН АЛЕРТ:\n{event.message}\n")
    
    monitor = Monitor(
        alerts_manager=manager,
        on_alert_callback=on_alert,
        poll_interval=3.0,
        candle_interval="15",
        candle_periods=20,
        candle_volume_multiplier=3.0
    )
    
    print("📡 Запускаем мониторинг на 15 секунд...\n")
    
    import threading
    thread = threading.Thread(target=monitor.start, daemon=True)
    thread.start()
    
    time.sleep(15)
    monitor.stop()
    thread.join(timeout=2)
    
    manager.clear_all()
    from pathlib import Path
    Path(test_file).unlink(missing_ok=True)
    
    print("🎉 Тест завершён!")