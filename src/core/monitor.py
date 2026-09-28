"""
Модуль мониторинга цен и объёмов
"""

import sys
import os
import time
import logging
from typing import Callable, Optional, Dict, Any
from dataclasses import dataclass, field
from enum import Enum

# Добавляем корень проекта в путь
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from src.api.bybit_client import BybitClient, TickerData
from src.core.alerts import AlertsManager, Asset, AlertRule
from src.core.cooldown import CooldownManager
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

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
    reply_markup: Any = None


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
        candle_volume_multiplier: float = 3.0,
        alert_cooldown_minutes: int = 25
    ):
        self.alerts_manager = alerts_manager
        self.on_alert = on_alert_callback
        self.poll_interval = poll_interval
        self.volume_threshold = volume_threshold
        self.volume_cooldown = volume_cooldown
        self.price_reset_threshold = price_reset_threshold
        self.candle_interval = candle_interval
        self.candle_periods = candle_periods
        self.candle_volume_multiplier = candle_volume_multiplier
        
        # Если клиент не передан, создаем новый
        from src.api.bybit_client import BybitClient
        self.client = bybit_client or BybitClient()
        
        self.cooldown_manager = CooldownManager(cooldown_minutes=alert_cooldown_minutes)
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
                # 1. ПРОВЕРКА КУЛДАУНА ДО ЗАПРОСА СВЕЧЕЙ (защита от спама API)
                if not self.cooldown_manager.can_send(asset.symbol, target, direction):
                    logger.debug(f"⏭️ Алерт ПРОПУЩЕН (кулдаун): {asset.symbol} @ {target}")
                    state.triggered_alerts[alert_key] = True
                    continue
                
                cross_text = "🟢 СНИЗУ ВВЕРХ" if cross_type == CrossDirection.UP else "🔴 СВЕРХУ ВНИЗ"
                
                # 2. Получаем свечи и объем ТОЛЬКО если не в кулдауне
                klines_15m = self.client.get_klines(asset.symbol, asset.category, "15", 50)
                klines_4h = self.client.get_klines(asset.symbol, asset.category, "240", 30)
                
                vol_data = self.client.get_candle_volume_ratio(asset.symbol, asset.category, "15", 20)
                volume_ratio = vol_data["ratio"] if vol_data else 0.0
                
                from src.core.analyzer import evaluate_alert, format_alert_message
                
                if klines_15m and klines_4h:
                    evaluation = evaluate_alert(
                        symbol=asset.symbol,
                        level=target,
                        direction=direction,
                        current_price=curr,
                        alert_created_at=rule.created_at,
                        klines_15m=klines_15m,
                        klines_4h=klines_4h,
                    )
                    
                    # Отправляем только если вердикт не "❌ None"
                    if evaluation['verdict'] != '❌ None':
                        message = format_alert_message(
                            symbol=asset.symbol,
                            level=target,
                            direction=direction,
                            current_price=curr,
                            evaluation=evaluation,
                            setup_note=rule.setup_note
                        )
                        
                        # ИСПРАВЛЕНО: убрано :, из target, чтобы float() в handlers.py не падал с ValueError
                        alert_keyboard = InlineKeyboardMarkup([
                            [InlineKeyboardButton(f"🗑 Удалить {asset.symbol} @ {target}", 
                                                  callback_data=f"del|{asset.symbol}|{target}|{direction}")],
                            [InlineKeyboardButton(f"📊 Выгрузить данные {asset.symbol}", 
                                                  callback_data=f"export_alert_{asset.symbol}")],
                            [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")]
                        ])
                        
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
                                'evaluation': evaluation
                            },
                            reply_markup=alert_keyboard
                        )
                        
                        self.on_alert(event)
                        self.cooldown_manager.mark_sent(asset.symbol, target, direction)
                        logger.info(f"🔔 Алерт ОТПРАВЛЕН: {asset.symbol} @ {target} (Score: {evaluation['score']})")
                    else:
                        logger.info(f"⏭️ Алерт ПРОПУЩЕН (слабый сигнал): {asset.symbol} @ {target} (Score: {evaluation['score']})")
                else:
                    logger.warning(f"⚠️ Не удалось получить данные для анализа: {asset.symbol}")
                
                # В любом случае ставим флаг, чтобы не проверять этот уровень снова, 
                # пока цена не отойдет и не вернется (защита от спама API)
                state.triggered_alerts[alert_key] = True
    
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
            
            ticker = self.client.get_ticker(symbol, asset.category)
            if not ticker: 
                continue
            
            self._check_price_cross(asset, ticker, self.states[symbol])
            state = self.states[symbol]
            state.prev_price = ticker.price
            state.prev_volume = ticker.volume_24h
    
    def start(self):
        self._running = True
        logger.info(f"🚀 Мониторинг запущен. Интервал: {self.poll_interval}s, Кулдаун: {self.cooldown_manager.cooldown_seconds//60}м")
        try:
            while self._running:
                try: 
                    self._poll_once()
                except Exception as e: 
                    logger.error(f"Ошибка в цикле: {e}", exc_info=True)
                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            logger.info("🛑 Мониторинг остановлен")
        finally:
            self._running = False
    
    def stop(self):
        self._running = False
        logger.info("Мониторинг останавливается...")