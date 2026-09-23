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
    """Структура данных тикера"""
    symbol: str
    price: float
    volume_24h: float
    timestamp: int


@dataclass
class ScreenerAsset:
    """Актив для скринера с расширенными данными"""
    symbol: str
    price: float
    price_change_24h: float  # в процентах
    volume_24h: float         # объем в USDT
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
            
            # <-- ВАЖНО: этот лог показывает получение данных
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
    
    def get_screener_data(
        self,
        category: str = "linear",
        sort_by: str = "volume_desc",
        min_volume_usd: float = 1_000_000,
        min_change_abs: float = 0.0,  # <-- НОВОЕ: минимальное изменение по модулю (%)
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
                    
                    volume = float(item.get("turnover24h", 0))  # Оборот в USDT
                    if volume < min_volume_usd:
                        continue
                    
                    price_change = float(item.get("price24hPcnt", 0)) * 100
                    
                    # <-- НОВОЕ: Фильтр по абсолютному изменению (не важно + или -)
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
            
            # Сортировка
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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    client = BybitClient()
    
    print("🧪 Тест скринера:\n")
    assets = client.get_screener_data(category="linear", sort_by="price_change_desc", limit=5)
    
    for a in assets:
        print(f"{a.symbol}: {a.price_change_24h:+.2f}% | Volume: ${a.volume_24h/1e6:.2f}M")