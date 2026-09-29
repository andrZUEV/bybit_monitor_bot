"""
WebSocket-клиент Bybit v5 (public linear).

Назначение:
    Получать push-обновления тикеров (lastPrice, volume24h) вместо REST-поллинга.
    Klines остаются через REST — их тянут только при срабатывании алерта.

Архитектура:
    - Синхронный клиент на `websocket-client`, работает в отдельном потоке.
    - Ping каждые `ping_interval` секунд (Bybit требует не реже 20с).
    - Авто-реконнект с экспоненциальной задержкой.
    - Динамические подписки: set_symbols() делает diff с текущим набором
      и шлёт subscribe/unsubscribe. Потокобезопасно.

Публичный API:
    - start()                    — запустить поток.
    - stop()                     — остановить поток.
    - set_symbols(iterable[str]) — обновить список подписок.

Callback:
    on_ticker(symbol: str, price: float, volume_24h: float) -> None
    вызывается из WS-потока при получении тикера.
"""

import json
import logging
import threading
import time
from typing import Callable, Iterable, Optional, Set

import websocket  # websocket-client

logger = logging.getLogger(__name__)


class BybitWebSocketClient:
    """
    Синхронный WS-клиент Bybit v5 для темы `tickers.{symbol}`.

    Один экземпляр — одно соединение. Живёт в отдельном потоке.
    """

    # Максимальный backoff между попытками реконнекта (сек).
    MAX_RECONNECT_DELAY = 30.0

    def __init__(
        self,
        url: str,
        on_ticker: Callable[[str, float, float], None],
        ping_interval: float = 20.0,
        reconnect_delay: float = 5.0,
    ):
        self.url = url
        self.on_ticker = on_ticker
        self.ping_interval = ping_interval
        self.reconnect_delay = reconnect_delay

        # Текущий набор подписок (символы в верхнем регистре).
        self._subscribed: Set[str] = set()
        # Множество, которое хотим иметь. Обновляется из set_symbols().
        self._desired: Set[str] = set()
        self._subs_lock = threading.Lock()

        # Управление потоком.
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._ws: Optional[websocket.WebSocket] = None

    # ==================== ПУБЛИЧНЫЙ API ====================

    def start(self) -> None:
        """Запускает WS-клиент в отдельном потоке (неблокирующий)."""
        if self._thread and self._thread.is_alive():
            logger.warning("BybitWebSocketClient уже запущен.")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_forever, name="BybitWebSocket", daemon=True
        )
        self._thread.start()
        logger.info(f"🔌 WS-клиент запущен: {self.url}")

    def stop(self, timeout: float = 5.0) -> None:
        """Останавливает клиент и ждёт завершения потока."""
        self._stop_event.set()
        # Закрываем сокет, чтобы разбудить recv-loop.
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        logger.info("🔌 WS-клиент остановлен.")

    def set_symbols(self, symbols: Iterable[str]) -> None:
        """
        Обновляет желаемый набор подписок.

        Реальный diff (subscribe/unsubscribe) выполняется в WS-потоке —
        здесь мы только фиксируем новое множество.
        """
        normalized = {s.strip().upper() for s in symbols if s and s.strip()}
        with self._subs_lock:
            self._desired = normalized

    # ==================== ПОТОК ====================

    def _run_forever(self) -> None:
        """Основной цикл: connect → subscribe → recv → reconnect."""
        attempt = 0
        while not self._stop_event.is_set():
            try:
                self._connect_and_listen()
                attempt = 0  # успешная сессия — сбрасываем backoff
            except Exception as e:
                logger.error(f"WS-сессия упала: {e}", exc_info=True)

            if self._stop_event.is_set():
                break

            # Экспоненциальный backoff с потолком.
            attempt += 1
            delay = min(
                self.reconnect_delay * (2 ** (attempt - 1)),
                self.MAX_RECONNECT_DELAY,
            )
            logger.info(f"🔄 Реконнект через {delay:.1f}с (попытка {attempt})...")
            # Ждём либо таймаут, либо stop_event.
            self._stop_event.wait(delay)

    def _connect_and_listen(self) -> None:
        """Одно соединение: коннект, подписка, recv-loop с ping."""
        ws = websocket.WebSocket()
        ws.settimeout(1.0)  # чтобы recv не блокировал stop()
        logger.info(f"🌐 Подключаюсь к {self.url}...")
        ws.connect(self.url, timeout=10)
        self._ws = ws
        logger.info("✅ WS подключён.")

        # Применяем начальный набор подписок.
        self._apply_diff(ws, initial=True)

        last_ping = time.monotonic()
        try:
            while not self._stop_event.is_set():
                # 1. Ping по таймеру.
                if time.monotonic() - last_ping >= self.ping_interval:
                    self._send_ping(ws)
                    last_ping = time.monotonic()

                # 2. Применяем diff подписок (если set_symbols() что-то изменил).
                self._apply_diff(ws)

                # 3. Читаем сообщение (settimeout = 1с, чтобы цикл был живым).
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                except websocket.WebSocketConnectionClosedException:
                    logger.warning("WS-соединение закрыто сервером.")
                    break

                if not raw:
                    continue
                self._handle_message(raw)
        finally:
            try:
                ws.close()
            except Exception:
                pass
            self._ws = None

    # ==================== ПОДПИСКИ ====================

    def _apply_diff(self, ws: websocket.WebSocket, initial: bool = False) -> None:
        """
        Сравнивает _subscribed с _desired и шлёт subscribe/unsubscribe.

        initial=True — первая подписка после коннекта. Подписываемся
        на объединение _desired | _subscribed, чтобы после обрыва связи
        восстановить прежний набор, даже если set_symbols() ещё не вызван.
        """
        with self._subs_lock:
            if initial:
                desired = set(self._desired) | set(self._subscribed)
                current = set()
                # Считаем, что после коннекта сервер «пуст».
                self._subscribed = set()
            else:
                desired = set(self._desired)
                current = set(self._subscribed)

            to_add = desired - current
            to_remove = current - desired

            if not to_add and not to_remove:
                return

            # Bybit принимает до 10 args за один subscribe, поэтому бьём на чанки.
            if to_add:
                for chunk in self._chunks(sorted(to_add), 10):
                    args = [f"tickers.{s}" for s in chunk]
                    self._send_json(ws, {"op": "subscribe", "args": args})
                    logger.info(f"📡 subscribe: {', '.join(chunk)}")
                    self._subscribed.update(chunk)

            if to_remove:
                for chunk in self._chunks(sorted(to_remove), 10):
                    args = [f"tickers.{s}" for s in chunk]
                    self._send_json(ws, {"op": "unsubscribe", "args": args})
                    logger.info(f"📴 unsubscribe: {', '.join(chunk)}")
                    self._subscribed.difference_update(chunk)

    @staticmethod
    def _chunks(seq, n):
        for i in range(0, len(seq), n):
            yield seq[i:i + n]

    def _send_json(self, ws: websocket.WebSocket, payload: dict) -> None:
        try:
            ws.send(json.dumps(payload))
        except Exception as e:
            logger.error(f"Ошибка отправки в WS: {e}")

    def _send_ping(self, ws: websocket.WebSocket) -> None:
        self._send_json(ws, {"op": "ping"})

    # ==================== ОБРАБОТКА СООБЩЕНИЙ ====================

    def _handle_message(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"WS: не-JSON сообщение: {raw[:200]}")
            return

        # Служебные ответы
        if "op" in msg:
            op = msg.get("op")
            if op == "pong":
                return
            if op in ("subscribe", "unsubscribe"):
                if not msg.get("success", True):
                    logger.warning(f"WS {op} failed: {msg.get('ret_msg', msg)}")
                return
            if op == "ping":
                # На всякий случай: если сервер сам пингует, отвечаем pong.
                # Bybit обычно отвечает pong на наш ping; это защита на будущее.
                try:
                    if self._ws is not None:
                        self._ws.send(json.dumps({"op": "pong"}))
                except Exception:
                    pass
                return
            logger.debug(f"WS op={op}: {msg}")
            return

        # Тикер
        topic = msg.get("topic", "")
        if not topic.startswith("tickers."):
            return

        data = msg.get("data") or {}
        symbol = data.get("symbol") or topic[len("tickers."):]
        price_str = data.get("lastPrice")
        vol_str = data.get("volume24h")

        if price_str is None:
            return

        try:
            price = float(price_str)
            volume_24h = float(vol_str) if vol_str is not None else 0.0
        except (TypeError, ValueError) as e:
            logger.warning(f"WS: не удалось распарсить тикер {symbol}: {e}")
            return

        try:
            self.on_ticker(symbol, price, volume_24h)
        except Exception as e:
            # Не даём упасть WS-потоку из-за ошибки в колбэке.
            logger.error(f"Ошибка в on_ticker({symbol}): {e}", exc_info=True)