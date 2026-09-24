"""
Модуль для работы с Bybit API v5
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

import requests
import time
import logging
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class TickerData:
    symbol: str
    price: float
    volume_24h: float
    timestamp: int


@dataclass
class ScreenerAsset:
    symbol: str
    price: float
    price_change_24h: float
    volume_24h: float
    high_24h: float
    low_24h: float
    category: str


class BybitClient:
    BASE_URL = "https://api.bybit.com"
    
    def __init__(self, timeout: int = 5, max_retries: int = 3):
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "BybitMonitorBot/1.0",
            "Accept": "application/json"
        })
        self._last_request_time = 0
        self._min_request_interval = 0.1
        
        # Кэш для объема свечей (чтобы не долбить API каждый опрос)
        self._volume_cache: Dict[str, tuple] = {}
        self._volume_cache_ttl = 60  # 1 минута
    
    def _rate_limit(self):
        current_time = time.time()
        time_since_last = current_time - self._last_request_time
        if time_since_last < self._min_request_interval:
            time.sleep(self._min_request_interval - time_since_last)
        self._last_request_time = time.time()
    
    def _make_request(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.BASE_URL}{endpoint}"
        for attempt in range(self.max_retries):
            try:
                self._rate_limit()
                response = self.session.get(url, params=params, timeout=self.timeout)
                response.raise_for_status()
                data = response.json()
                if data.get("retCode") != 0:
                    raise Exception(f"Bybit API error: {data.get('retMsg')}")
                return data
            except Exception as e:
                if attempt == self.max_retries - 1:
                    raise
        raise Exception("Все попытки запроса неудачны")
    
    def get_ticker(self, symbol: str, category: str = "linear") -> Optional[TickerData]:
        try:
            params = {"category": category, "symbol": symbol.upper()}
            data = self._make_request("/v5/market/tickers", params)
            result_list = data.get("result", {}).get("list", [])
            if not result_list:
                logger.warning(f"Нет данных для {symbol}")
                return None
            item = result_list[0]
            
            price = float(item.get("lastPrice", 0))
            volume = float(item.get("volume24h", 0))
            timestamp = int(item.get("time", 0))
            
            logger.info(f"📥 {symbol}: 💰 {price:,.4f} | 📊 Vol: {volume:,.2f}")
            
            return TickerData(
                symbol=symbol.upper(),
                price=price,
                volume_24h=volume,
                timestamp=timestamp
            )
        except Exception as e:
            logger.error(f"Ошибка получения тикера {symbol}: {e}")
            return None
    
    def get_candle_volume_ratio(
        self,
        symbol: str,
        category: str = "linear",
        interval: str = "15",
        periods: int = 20
    ) -> Optional[Dict[str, Any]]:
        """
        Возвращает отношение объема текущей свечи к среднему объему предыдущих свечей
        
        Args:
            symbol: Тикер
            category: Категория (linear/spot)
            interval: Таймфрейм свечи в минутах (1, 3, 5, 15, 30, 60, 120, 240)
            periods: Количество предыдущих свечей для расчета среднего
            
        Returns:
            Dict с ratio, current_volume, avg_volume или None при ошибке
        """
        try:
            # Проверяем кэш
            cache_key = f"{symbol}_{category}_{interval}_{periods}"
            cached = self._volume_cache.get(cache_key)
            if cached and (time.time() - cached[0]) < self._volume_cache_ttl:
                return cached[1]
            
            params = {
                "category": category,
                "symbol": symbol.upper(),
                "interval": interval,
                "limit": periods + 1  # +1 для текущей свечи
            }
            data = self._make_request("/v5/market/kline", params)
            
            candles = data.get("result", {}).get("list", [])
            if len(candles) < periods + 1:
                logger.warning(f"Недостаточно свечей для {symbol}")
                return None
            
            # Bybit возвращает свечи от новых к старым
            # candles[0] - текущая (незавершенная) свеча
            # candles[1:] - предыдущие завершенные свечи
            
            current_candle = candles[0]
            previous_candles = candles[1:periods+1]
            
            # index 6 = turnover (объем в USDT), index 5 = volume (в базовой валюте)
            current_volume = float(current_candle[6])
            previous_volumes = [float(c[6]) for c in previous_candles]
            
            avg_volume = sum(previous_volumes) / len(previous_volumes)
            
            if avg_volume == 0:
                ratio = 0
            else:
                ratio = current_volume / avg_volume
            
            result = {
                "ratio": ratio,
                "current_volume": current_volume,
                "avg_volume": avg_volume,
                "interval": interval
            }
            
            # Сохраняем в кэш
            self._volume_cache[cache_key] = (time.time(), result)
            
            logger.debug(f" {symbol} volume ratio: {ratio:.2f}x (interval: {interval}m)")
            
            return result
            
        except Exception as e:
            logger.error(f"Ошибка получения объема свечи {symbol}: {e}")
            return None
    
    def get_screener_data(
        self,
        category: str = "linear",
        sort_by: str = "volume_desc",
        min_volume_usd: float = 1_000_000,
        min_change_abs: float = 0.0,
        limit: int = 15
    ) -> List[ScreenerAsset]:
        try:
            params = {"category": category}
            data = self._make_request("/v5/market/tickers", params)
            
            assets = []
            for item in data.get("result", {}).get("list", []):
                try:
                    symbol = item.get("symbol", "")
                    if not symbol.endswith("USDT"):
                        continue
                    
                    volume = float(item.get("turnover24h", 0))
                    if volume < min_volume_usd:
                        continue
                    
                    price_change = float(item.get("price24hPcnt", 0)) * 100
                    
                    if abs(price_change) < min_change_abs:
                        continue
                    
                    assets.append(ScreenerAsset(
                        symbol=symbol,
                        price=float(item.get("lastPrice", 0)),
                        price_change_24h=price_change,
                        volume_24h=volume,
                        high_24h=float(item.get("highPrice24h", 0)),
                        low_24h=float(item.get("lowPrice24h", 0)),
                        category=category
                    ))
                except (ValueError, TypeError):
                    continue
            
            if sort_by == "price_change_desc":
                assets.sort(key=lambda x: x.price_change_24h, reverse=True)
            elif sort_by == "price_change_asc":
                assets.sort(key=lambda x: x.price_change_24h)
            elif sort_by == "volume_desc":
                assets.sort(key=lambda x: x.volume_24h, reverse=True)
            
            return assets[:limit]
            
        except Exception as e:
            logger.error(f"Ошибка скринера: {e}")
            return []
    
    def test_connection(self) -> bool:
        try:
            self._make_request("/v5/market/time")
            return True
        except Exception:
            return False

    def get_klines(
        self,
        symbol: str,
        category: str = "linear",
        interval: str = "15",
        limit: int = 30
    ) -> Optional[List[List]]:
        """
        Получает свечи (klines) с Bybit API
        
        Args:
            symbol: Тикер
            category: Категория (linear/spot)
            interval: Таймфрейм в минутах
            limit: Количество свечей (макс 200)
        
        Returns:
            Список свечей от новых к старым или None при ошибке
            Формат свечи: [startTime, open, high, low, close, volume, turnover]
        """
        try:
            params = {
                "category": category,
                "symbol": symbol.upper(),
                "interval": interval,
                "limit": limit
            }
            data = self._make_request("/v5/market/kline", params)
            return data.get("result", {}).get("list", [])
        except Exception as e:
            logger.error(f"Ошибка получения свечей {symbol}: {e}")
            return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    client = BybitClient()
    
    print(" Тест объема свечей:\n")
    result = client.get_candle_volume_ratio("BTCUSDT", "linear", "15", 20)
    
    if result:
        print(f"Ratio: {result['ratio']:.2f}x")
        print(f"Current volume: ${result['current_volume']:,.2f}")
        print(f"Avg volume: ${result['avg_volume']:,.2f}")
    else:
        print("❌ Ошибка")