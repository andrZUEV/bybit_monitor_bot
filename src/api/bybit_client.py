"""
Клиент для взаимодействия с Bybit API v5.

Все методы возвращают типизированные объекты (dataclass'ы) или None.
Поддерживаются умные ретраи для временных ошибок (лимиты, 5xx).
"""

import logging
import random
import threading
import time
from dataclasses import dataclass
from typing import Any

import requests

logger = logging.getLogger(__name__)


# ==================== DTO ====================

@dataclass
class TickerData:
    """Данные тикера (цена + объём)."""
    price: float
    volume_24h: float


@dataclass
class ScreenerAsset:
    """Актив для скринера."""
    symbol: str
    price: float
    price_change_24h: float  # в процентах, например -5.3 или +7.1
    volume_24h: float        # в USDT


# ==================== КЛИЕНТ ====================

class BybitClient:
    """
    Клиент Bybit v5 (public endpoints).
    Не требует API-ключей — работает только с публичными данными.
    """

    BASE_URL = "https://api.bybit.com"

    # Коды Bybit, при которых имеет смысл повторить запрос
    RETRYABLE_CODES = {-1001, -1000, 10002, 10003,10006, 429}

    def __init__(self, max_retries: int = 3, base_delay: float = 1.0):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "BybitMonitorBot/1.0",
        })

        # Rate limiter: не чаще, чем _min_request_interval секунд
        # между запросами. Работает для всех, кто ходит через
        # self.session — Monitor, handlers, data_exporter.
        self._rate_lock = threading.Lock()
        self._last_request_ts = 0.0
        # ~8 req/sec с запасом от 10 (лимит Bybit public).
        self._min_request_interval = 0.12

    # ==================== ВНУТРЕННИЕ ====================

    def _make_request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        
        """ HTTP-запрос с ретраями (экспоненциальная задержка).

        - Retry на сетевые ошибки / не-JSON.
        - Retry на RETRYABLE_CODES. Для 10006 (rate limit) —
          отдельный, более длинный backoff.
        - Rate limiter перед запросом.

        Возвращает result-часть ответа или None.
        """
       
        url = f"{self.BASE_URL}{endpoint}"

        # Rate limiter.
        self._rate_limit()

        for attempt in range(self.max_retries):
            try:
                response = self.session.request(
                    method, url, params=params, json=json_data, timeout=10
                )
                response.raise_for_status()

                # Bybit иногда отдаёт HTML на 5xx — парсим аккуратно
                try:
                    data = response.json()
                except ValueError:
                    logger.warning(
                        f"Bybit вернул не-JSON (HTTP {response.status_code}): "
                        f"{response.text[:200]}"
                    )
                    time.sleep(self._retry_delay(attempt))
                    continue

                ret_code = data.get("retCode")
                if ret_code != 0:
                    ret_msg = data.get("retMsg", "Unknown error")

                    if ret_code in self.RETRYABLE_CODES:
                        if ret_code == 10006:
                            delay = self._rate_limit_backoff(attempt)
                        else:
                            delay = self._retry_delay(attempt)
                        logger.warning(
                            f"Bybit API (код {ret_code}): {ret_msg}. "
                            f"Повтор через {delay:.1f}с..."
                        )
                        time.sleep(delay)
                        continue

                    logger.error(f"Bybit API ошибка (код {ret_code}): {ret_msg}")
                    return None

                return data.get("result")

            except requests.exceptions.RequestException as e:
                delay = self._retry_delay(attempt)
                logger.warning(f"Сетевая ошибка: {e}. Повтор через {delay:.1f}с...")
                time.sleep(delay)
            except Exception as e:
                logger.error(
                    f"Неожиданная ошибка в _make_request: {e}", exc_info=True
                )
                return None

        logger.error("Превышено максимальное количество попыток запроса к Bybit API.")
        return None

    def _rate_limit(self) -> None:
        """Простой token-bucket: не чаще, чем _min_request_interval.

        Потокобезопасно. Гарантирует, что даже при всплеске
        запросов из разных потоков не упираемся в rate limit Bybit.
        """
        with self._rate_lock:
            now = time.monotonic()
            delta = now - self._last_request_ts
            if delta < self._min_request_interval:
                time.sleep(self._min_request_interval - delta)
            self._last_request_ts = time.monotonic()

    def _retry_delay(self, attempt: int) -> float:
        """Экспоненциальный backoff с джиттером: base_delay * 2^n + [0..0.5]."""
        base = self.base_delay * (2 ** attempt)
        return base + random.uniform(0, 0.5)

    def _rate_limit_backoff(self, attempt: int) -> float:
        """Backoff специально для 10006 — ждём дольше, чем обычно.

        Bybit рекомендует паузу не меньше 1 секунды; на практике
        окно сбрасывается за 2–5 секунд. Формула: 3 * (attempt+1)
        с джиттером до 1 сек, максимум 10 сек.
        """
        base = min(3.0 * (attempt + 1), 10.0)
        return base + random.uniform(0, 1.0)

    # ==================== ПУБЛИЧНЫЕ МЕТОДЫ ====================

    def get_ticker(
        self, symbol: str, category: str = "linear"
    ) -> TickerData | None:
        """Возвращает текущую цену и 24h-объём по символу."""
        params = {"category": category, "symbol": symbol}
        result = self._make_request("GET", "/v5/market/tickers", params=params)

        if result and "list" in result and result["list"]:
            t = result["list"][0]
            try:
                return TickerData(
                    price=float(t["lastPrice"]),
                    volume_24h=float(t["volume24h"]),
                )
            except (KeyError, ValueError) as e:
                logger.warning(f"Не удалось распарсить тикер {symbol}: {e}")
                return None
        return None

    def resolve_symbol(
        self, user_input: str, category: str = "linear"
    ) -> str | None:
        
        """Приводит пользовательский ввод к валидному символу Bybit.

        Примеры:
            "sol"      -> "SOLUSDT"
            "SOL"      -> "SOLUSDT"
            "Sol"      -> "SOLUSDT"
            "solusdt"  -> "SOLUSDT"
            "SOLUSDT"  -> "SOLUSDT"
            "BTC"      -> "BTCUSDT"
            "btcusdt"  -> "BTCUSDT"
            "random"   -> None

        Возвращает None, если символ не найден на Bybit."""
        
        if not user_input:
            return None

        s = user_input.strip().upper().replace(" ", "")
        if not s:
            return None

        # Кандидаты для проверки
        if s.endswith("USDT"):
            candidates = [s]
        else:
            candidates = [s + "USDT"]

        for cand in candidates:
            if self.get_ticker(cand, category) is not None:
                return cand

        return None

    def get_klines(
        self,
        symbol: str,
        category: str,
        interval: str,
        limit: int = 200,
    ) -> list[list] | None:
        """
        Возвращает сырые klines от Bybit.

        ВАЖНО: порядок — от НОВЫХ к СТАРЫМ (k[0] = самая свежая свеча).
        Формат элемента: [start_ms, open, high, low, close, volume, turnover].
        """
        params = {
            "category": category,
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
        result = self._make_request("GET", "/v5/market/kline", params=params)
        if result and "list" in result:
            return result["list"]
        return None

    def get_candle_volume_ratio(
        self,
        symbol: str,
        category: str,
        interval: str,
        periods: int = 20,
    ) -> dict[str, float] | None:
        """
        Отношение объёма ТЕКУЩЕЙ свечи к среднему за `periods` предыдущих.

        Returns:
            {"ratio": float, "current": float, "avg": float} или None.
        """
        klines = self.get_klines(symbol, category, interval, limit=periods + 1)
        if not klines or len(klines) < periods + 1:
            return None

        current_volume = float(klines[0][5])
        avg_volume = sum(float(k[5]) for k in klines[1:periods + 1]) / periods

        if avg_volume == 0:
            return {"ratio": 1.0, "current": current_volume, "avg": avg_volume}

        return {
            "ratio": current_volume / avg_volume,
            "current": current_volume,
            "avg": avg_volume,
        }

    def get_screener_data(
        self,
        category: str = "linear",
        sort_by: str = "volume_desc",
        min_volume_usd: float = 1_000_000,
        min_change_abs: float = 3.0,
        limit: int = 50,
    ) -> list[ScreenerAsset]:
        """
        Список активов, у которых за 24ч:
          - объём >= min_volume_usd
          - |изменение цены| >= min_change_abs

        Отсортировано по sort_by (Bybit: "volume_desc", "change_desc", ...).
        """
        params = {"category": category, "sort": sort_by, "limit": limit}
        result = self._make_request("GET", "/v5/market/tickers", params=params)

        assets: list[ScreenerAsset] = []
        if not result or "list" not in result:
            return assets

        for ticker in result["list"]:
            try:
                price = float(ticker.get("lastPrice", 0))
                change_24h = float(ticker.get("price24hPcnt", 0)) * 100
                volume_24h = float(ticker.get("turnover24h", 0))

                if volume_24h >= min_volume_usd and abs(change_24h) >= min_change_abs:
                    assets.append(ScreenerAsset(
                        symbol=ticker["symbol"],
                        price=price,
                        price_change_24h=change_24h,
                        volume_24h=volume_24h,
                    ))
            except (ValueError, KeyError):
                continue

        return assets