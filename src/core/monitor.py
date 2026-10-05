"""
Модуль мониторинга цен и объёмов.

Версия 3.0 (WebSocket):
- Основной режим — WS-push (BybitWebSocketClient). Подписки обновляются
  раз в Config.WS_SYMBOL_REFRESH секунд по alerts.json.
- Fallback — REST-поллинг (старый _poll_once). Включается, если
  Config.USE_WEBSOCKET=False или WS-клиент не передан.
- Klines по-прежнему через REST, только при срабатывании алерта.
- Прогрев prev_price: первый тик по символу только запоминает цену,
  кросс не проверяется (то же поведение, что и в REST-версии).
- Кэш _assets_by_symbol для быстрого поиска Asset по тикеру из WS.
- states[symbol] НЕ удаляются при исчезновении символа из alerts.json:
  это защищает от гонки с WS-потоком и сохраняет prev_price между
  удалением/повторным добавлением того же уровня.

Версия 2.0 (REST):
- Лимиты klines увеличены до 250 (для корректного count_touches за 48ч)
- Интервалы/периоды берутся из Config, если не переданы явно
- Прогрев состояния при старте: первый тик только запоминает prev_price
- CooldownManager получает путь из Config
"""

import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# Добавляем корень проекта в путь (для запуска python main.py)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.api.bybit_client import BybitClient, TickerData
from src.core.alert_history import AlertHistory, build_alert_record
from src.core.alerts import AlertsManager, Asset
from src.core.analyzer import evaluate_alert, format_alert_message
from src.core.cooldown import CooldownManager
from src.core.risk import RiskConfig, risk_config_from_env
from src.core.settings import RuntimeSettings
from src.utils.config import Config
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)


# ==================== КОНСТАНТЫ ====================

# Лимиты свечей для анализа.
# 15m: 300 свечей = ~75ч (хватает для count_touches за 48ч)
# 1H:  300 свечей = ~12.5д (для ATR-1h, runway)
# 4H:  400 свечей = ~66 дней (для EMA50 + наклон + уровни)
# 1D:  200 свечей = ~200 дней (для зеркальных уровней, HH/HL)
KLINES_15M_LIMIT = Config.EXPORT_LIMITS["15"]   # 300
KLINES_1H_LIMIT = Config.EXPORT_LIMITS["60"]    # 300
KLINES_4H_LIMIT = Config.EXPORT_LIMITS["240"]   # 400
KLINES_1D_LIMIT = Config.EXPORT_LIMITS["D"]     # 200
VOLUME_PERIODS = 20

# TTL кэша klines (в секундах). 1H меняется раз в час, 1D — раз в сутки.
KLINES_1H_TTL = 3600
KLINES_1D_TTL = 86400


# ==================== DTO ====================

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
    extra: dict[str, Any] = field(default_factory=dict)
    reply_markup: Any = None


@dataclass
class AssetState:
    prev_price: float | None = None
    prev_volume: float | None = None
    last_volume_alert_time: float = 0
    triggered_alerts: dict[str, bool] = field(default_factory=dict)


# ==================== МОНИТОР ====================

class Monitor:
    def __init__(
        self,
        alerts_manager: AlertsManager,
        on_alert_callback: Callable[[AlertEvent], None],
        bybit_client: BybitClient | None = None,
        # REST-fallback:
        poll_interval: float = 4.0,
        # legacy, не используется:
        volume_threshold: float = 5.0,
        volume_cooldown: float = 300.0,
        # общие:
        price_reset_threshold: float = 0.005,
        candle_interval: str = "15",
        candle_periods: int = 20,
        candle_volume_multiplier: float = 3.0,
        alert_cooldown_minutes: int = 25,
        # WS:
        ws_client: Any | None = None,   # BybitWebSocketClient (ленивый импорт)
        use_websocket: bool = True,
        settings: RuntimeSettings | None = None,
        alert_history: AlertHistory | None = None,
    ):
        self.alerts_manager = alerts_manager
        self.on_alert = on_alert_callback
        self.settings = settings or RuntimeSettings()
        self.alert_history = alert_history or AlertHistory(
            path=Config.ALERT_HISTORY_FILE,
            max_records=self.settings.alert_history_depth,
        )
        self.poll_interval = poll_interval
        self.volume_threshold = volume_threshold  # legacy, не используется
        self.volume_cooldown = volume_cooldown    # legacy, не используется
        self.price_reset_threshold = price_reset_threshold
        self.candle_interval = candle_interval
        self.candle_periods = candle_periods
        self.candle_volume_multiplier = candle_volume_multiplier

        # REST-клиент нужен для:
        #   - fallback-поллинга,
        #   - получения klines/volume при срабатывании алерта.
        self.client = bybit_client or BybitClient()

        # Путь к cooldowns.json — из Config
        self.cooldown_manager = CooldownManager(
            cooldown_minutes=alert_cooldown_minutes,
            storage_path=str(Config.COOLDOWNS_FILE),
        )

        self.states: dict[str, AssetState] = {}
        # Кэш klines для 1H/1D (не дёргаем Bybit на каждый алерт)
        # symbol -> (timestamp, klines)
        self._klines_1h_cache: dict[str, tuple[float, list[list]]] = {}
        self._klines_1d_cache: dict[str, tuple[float, list[list]]] = {}
        self._klines_cache_lock = threading.Lock()

        # Risk-конфиг (Этап 4). Строится один раз. Если EQUITY=0 — None,
        # тогда evaluate_alert не считает план сделки.
        self.risk_cfg: RiskConfig | None = None
        if Config.EQUITY > 0:
            self.risk_cfg = risk_config_from_env(
                equity=Config.EQUITY,
                risk_pct=Config.RISK_PCT,
                lot_step=Config.LOT_STEP,
                min_qty=Config.MIN_QTY,
                max_qty=Config.MAX_QTY,
                atr_multiplier=Config.ATR_MULTIPLIER,
            )
            logger.info(
                f"💰 Risk-модуль включён: equity={Config.EQUITY}, "
                f"risk_pct={Config.RISK_PCT * 100:.2f}%"
            )
        else:
            logger.warning(
                "⚠️ EQUITY=0 в .env → risk-модуль выключен, "
                "алерты без плана сделки"
            )
        # Кэш: symbol -> Asset. Обновляется в _refresh_subscriptions().
        self._assets_by_symbol: dict[str, Asset] = {}
        self._cache_lock = threading.Lock()

        # Управление жизненным циклом
        self._running = False
        self._stop_event = threading.Event()
        self._refresh_thread: threading.Thread | None = None

        # REST-polling счётчик (для fallback)
        self._poll_count = 0

        # WebSocket
        self.use_websocket = use_websocket
        self._ws_client = ws_client  # может быть None — создадим ниже
        if self.use_websocket and self._ws_client is None:
            # Ленивый импорт, чтобы не тянуть websocket-client, если WS выключен
            from src.api.bybit_ws import BybitWebSocketClient

            self._ws_client = BybitWebSocketClient(
                url=Config.WS_URL,
                on_ticker=self._on_ticker_update,
                ping_interval=Config.WS_PING_INTERVAL,
                reconnect_delay=Config.WS_RECONNECT_DELAY,
            )

    # ==================== ПРОВЕРКА ЦЕН ====================

    def _check_price_cross(
        self, asset: Asset, ticker: TickerData, state: AssetState
    ) -> None:
        """Проверяет пересечения цены с уровнями алертов."""
        if state.prev_price is None:
            return

        prev = state.prev_price
        curr = ticker.price

        for rule in asset.alerts:
            target = float(rule.price)
            direction = rule.direction
            alert_key = f"{target}_{direction}"

            crossed_up = prev < target and curr >= target
            crossed_down = prev > target and curr <= target

            if not (crossed_up or crossed_down):
                # Сброс флага, если цена отошла от уровня
                if direction == "up" and curr < target or direction == "down" and curr > target:
                    state.triggered_alerts[alert_key] = False
                elif direction == "any":
                    distance = abs(curr - target) / target if target > 0 else 0
                    if distance > self.price_reset_threshold:
                        state.triggered_alerts[alert_key] = False
                continue

            cross_type = CrossDirection.UP if crossed_up else CrossDirection.DOWN
            should_alert = (direction == "any") or (direction == cross_type.value)
            is_triggered = state.triggered_alerts.get(alert_key, False)

            if should_alert and not is_triggered:
                # 1. Кулдаун ДО запросов свечей (защита от спама API)
                if not self.cooldown_manager.can_send(asset.symbol, target, direction):
                    logger.debug(
                        f"⏭️ Алерт ПРОПУЩЕН (кулдаун): {asset.symbol} @ {target}"
                    )
                    state.triggered_alerts[alert_key] = True
                    continue

                # 2. Получаем свечи и объём ТОЛЬКО если не в кулдауне
                klines_15m = self.client.get_klines(
                    asset.symbol, asset.category, "15", KLINES_15M_LIMIT
                )
                klines_4h = self.client.get_klines(
                    asset.symbol, asset.category, "240", KLINES_4H_LIMIT
                )
                klines_1h = self._get_klines_cached(
                    asset.symbol, asset.category, "60",
                    KLINES_1H_LIMIT, KLINES_1H_TTL,
                )
                klines_1d = self._get_klines_cached(
                    asset.symbol, asset.category, "D",
                    KLINES_1D_LIMIT, KLINES_1D_TTL,
                )

                vol_data = self.client.get_candle_volume_ratio(
                    asset.symbol, asset.category, "15", VOLUME_PERIODS
                )
                volume_ratio = vol_data["ratio"] if vol_data else 0.0

                if klines_15m and klines_4h:
                    evaluation = evaluate_alert(
                        symbol=asset.symbol,
                        level=target,
                        direction=direction,
                        current_price=curr,
                        alert_created_at=rule.created_at,
                        klines_15m=klines_15m,
                        klines_4h=klines_4h,
                        klines_1h=klines_1h,
                        klines_1d=klines_1d,
                        risk_cfg=self.risk_cfg,
                    )

                    hard_filter = evaluation.get("hard_filter")
                    score_val = evaluation["score"]
                    should_send = True
                    skip_reason: str | None = None

                    # Гейты отправки
                    if not hard_filter and score_val < self.settings.alert_min_score:
                        should_send = False
                        skip_reason = "score_below"
                    elif hard_filter and not self.settings.send_invalid_alerts:
                        should_send = False
                        skip_reason = "hard_filter_disabled"

                    # Пишем в историю ВСЕГДА (и отправленные, и пропущенные)
                    try:
                        record = build_alert_record(
                            evaluation=evaluation,
                            symbol=asset.symbol,
                            level=target,
                            direction=direction,
                            current_price=curr,
                            alert_age_hours=evaluation.get("age_hours", 0.0),
                            volume_ratio=volume_ratio,
                            was_sent=should_send,
                            skip_reason=skip_reason,
                        )
                        self.alert_history.append(record)
                    except Exception as e:
                        logger.error(
                            f"⚠️ Не удалось записать AlertRecord: {e}",
                            exc_info=True,
                        )

                    if not should_send:
                        logger.info(
                            f"⏭️ Алерт ПРОПУЩЕН ({skip_reason}): "
                            f"{asset.symbol} @ {target} "
                            f"(score={score_val}, hard_filter={hard_filter})"
                        )
                        state.triggered_alerts[alert_key] = True
                        continue

                    # --- Отправка ---
                    message = format_alert_message(
                        symbol=asset.symbol,
                        level=target,
                        direction=direction,
                        current_price=curr,
                        evaluation=evaluation,
                        setup_note=rule.setup_note,
                    )

                    alert_keyboard = InlineKeyboardMarkup([
                        [InlineKeyboardButton(
                            f"🗑 Удалить {asset.symbol} @ {target}",
                            callback_data=f"del|{asset.symbol}|{target}|{direction}",
                        )],
                        [InlineKeyboardButton(
                            f"📊 Выгрузить данные {asset.symbol}",
                            callback_data=f"export_alert_{asset.symbol}",
                        )],
                        [InlineKeyboardButton(
                            "🏠 Главное меню", callback_data="menu_main"
                        )],
                    ])

                    event = AlertEvent(
                        event_type="price_cross",
                        symbol=asset.symbol,
                        category=asset.category,
                        current_price=curr,
                        message=message,
                        extra={
                            "target": target,
                            "direction": direction,
                            "volume_ratio": volume_ratio,
                            "evaluation": evaluation,
                        },
                        reply_markup=alert_keyboard,
                    )

                    self.on_alert(event)
                    self.cooldown_manager.mark_sent(asset.symbol, target, direction)
                    logger.info(
                        f"🔔 Алерт ОТПРАВЛЕН: {asset.symbol} @ {target} "
                        f"(Score: {evaluation['score']}"
                        + (f", hard_filter={hard_filter}" if hard_filter else "")
                        + ")"
                    )
                else:
                    logger.warning(
                        f"⚠️ Не удалось получить данные для анализа: {asset.symbol}"
                    )

                # Ставим флаг, чтобы не дёргать API повторно, пока цена не отойдёт
                state.triggered_alerts[alert_key] = True

    # ==================== КЭШ KLINES 1H/1D ====================

    def _get_klines_cached(
        self,
        symbol: str,
        category: str,
        interval: str,
        limit: int,
        ttl: float,
    ) -> list[list] | None:
        """
        Возвращает klines из кэша или запрашивает через REST.

        TTL: 1H — 3600с, 1D — 86400с. Если данных в кэше нет или они
        устарели — идём в REST. Ошибки REST не кэшируются.
        """
        if interval == "60":
            cache = self._klines_1h_cache
        elif interval == "D":
            cache = self._klines_1d_cache
        else:
            # Для нестандартных интервалов кэш не ведём — прямой запрос
            return self.client.get_klines(symbol, category, interval, limit)

        now = time.time()
        with self._klines_cache_lock:
            entry = cache.get(symbol)
            if entry is not None and (now - entry[0]) < ttl:
                return entry[1]

        data = self.client.get_klines(symbol, category, interval, limit)
        if data:
            with self._klines_cache_lock:
                cache[symbol] = (now, data)
        return data

    # ==================== WS-КОЛБЭК ====================

    def _on_ticker_update(self, symbol: str, price: float, volume_24h: float) -> None:
        """
        Колбэк из WS-потока: пришёл новый тикер по символу.

        Находит Asset (по кэшу или из alerts_manager — fallback на гонку),
        проверяет кроссы, обновляет prev_price.
        """
        # Ищем Asset
        with self._cache_lock:
            asset = self._assets_by_symbol.get(symbol)

        if asset is None:
            # Fallback: возможно, алерт только что добавили, а refresh ещё не прошёл.
            # Или символ есть в alerts.json, но WS подписался раньше, чем обновился кэш.
            for a in self.alerts_manager.get_all_alerts():
                if a.symbol == symbol:
                    asset = a
                    with self._cache_lock:
                        self._assets_by_symbol[symbol] = a
                    break

        if asset is None:
            # Нет алертов для этого символа — игнорируем.
            # Возможно, символ ещё в _subscribed (удаляют алерт), это нормально.
            return

        state = self.states.get(symbol)
        if state is None:
            state = AssetState()
            self.states[symbol] = state

        ticker = TickerData(price=price, volume_24h=volume_24h)
        try:
            self._check_price_cross(asset, ticker, state)
        except Exception as e:
            logger.error(
                f"Ошибка в _check_price_cross({symbol}): {e}", exc_info=True
            )

        state.prev_price = price
        state.prev_volume = volume_24h

    # ==================== ОБНОВЛЕНИЕ ПОДПИСОК ====================

    def _refresh_subscriptions(self) -> None:
        """
        Перечитывает alerts.json, обновляет:
          - набор подписок WS,
          - кэш _assets_by_symbol.

        states не трогаем — см. комментарий в шапке модуля.
        """
        self.alerts_manager.load()
        assets = self.alerts_manager.get_all_alerts()

        new_cache: dict[str, Asset] = {a.symbol: a for a in assets}
        symbols = set(new_cache.keys())

        with self._cache_lock:
            self._assets_by_symbol = new_cache

        if self._ws_client is not None:
            self._ws_client.set_symbols(symbols)

        logger.debug(
            f"🔄 Подписки обновлены: {len(symbols)} символов"
        )

    def _refresh_loop(self) -> None:
        """Фоновый поток: периодический refresh подписок."""
        # Первый refresh — сразу, до старта WS.
        try:
            self._refresh_subscriptions()
        except Exception as e:
            logger.error(f"Ошибка в _refresh_subscriptions: {e}", exc_info=True)

        while not self._stop_event.is_set():
            # Ждём либо таймаут, либо stop
            if self._stop_event.wait(Config.WS_SYMBOL_REFRESH):
                break
            try:
                self._refresh_subscriptions()
            except Exception as e:
                logger.error(f"Ошибка в _refresh_subscriptions: {e}", exc_info=True)

    # ==================== REST-FALLBACK ЦИКЛ ====================

    def _poll_once(self) -> None:
        """Один цикл опроса всех активов (REST-fallback)."""
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

            state = self.states[symbol]
            self._check_price_cross(asset, ticker, state)
            state.prev_price = ticker.price
            state.prev_volume = ticker.volume_24h

    # ==================== УПРАВЛЕНИЕ ====================

    def start(self) -> None:
        """
        Запускает мониторинг.

        В WS-режиме:
          - стартует фоновый поток refresh-подписок,
          - стартует WS-клиент,
          - блокирует главный поток до stop()/KeyboardInterrupt.

        В REST-режиме:
          - блокирующий цикл _poll_once() с паузой poll_interval.
        """
        self._running = True
        self._stop_event.clear()

        if self.use_websocket and self._ws_client is not None:
            self._start_websocket()
        else:
            self._start_rest_polling()

    def _start_websocket(self) -> None:
        logger.info(
            f"🚀 Мониторинг (WebSocket) запущен. "
            f"Кулдаун: {self.cooldown_manager.cooldown_seconds // 60}м, "
            f"Refresh подписок: {Config.WS_SYMBOL_REFRESH:.0f}с"
        )

        # Поток обновления подписок
        self._refresh_thread = threading.Thread(
            target=self._refresh_loop, name="MonitorRefresh", daemon=True
        )
        self._refresh_thread.start()

        # WS-клиент
        try:
            self._ws_client.start()
        except Exception as e:
            logger.error(f"Не удалось запустить WS-клиент: {e}", exc_info=True)
            logger.warning("⚠️ Переключаюсь на REST-fallback.")
            self.use_websocket = False
            self._start_rest_polling()
            return

        # Блокируем главный поток до остановки
        try:
            while self._running and not self._stop_event.is_set():
                self._stop_event.wait(1.0)
        except KeyboardInterrupt:
            logger.info("🛑 Мониторинг остановлен (KeyboardInterrupt)")
        finally:
            self._running = False

    def _start_rest_polling(self) -> None:
        logger.info(
            f"🚀 Мониторинг (REST) запущен. "
            f"Интервал: {self.poll_interval}s, "
            f"Кулдаун: {self.cooldown_manager.cooldown_seconds // 60}м"
        )
        try:
            while self._running and not self._stop_event.is_set():
                try:
                    self._poll_once()
                except Exception as e:
                    logger.error(f"Ошибка в цикле: {e}", exc_info=True)
                # Прерываемый sleep
                if self._stop_event.wait(self.poll_interval):
                    break
        except KeyboardInterrupt:
            logger.info("🛑 Мониторинг остановлен (KeyboardInterrupt)")
        finally:
            self._running = False

    def stop(self) -> None:
        """Останавливает мониторинг и все фоновые потоки."""
        logger.info("Мониторинг останавливается...")
        self._running = False
        self._stop_event.set()

        # Останавливаем WS-клиент
        if self._ws_client is not None:
            try:
                self._ws_client.stop()
            except Exception as e:
                logger.error(f"Ошибка остановки WS: {e}", exc_info=True)

        # Ждём refresh-поток
        if self._refresh_thread is not None and self._refresh_thread.is_alive():
            self._refresh_thread.join(timeout=5.0)

        logger.info("🛑 Мониторинг остановлен.")