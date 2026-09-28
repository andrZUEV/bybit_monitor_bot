"""
Клиент для взаимодействия с Bybit API
"""

import time
import logging
import requests
from typing import Optional, Dict, Any, List

from dataclasses import dataclass

@dataclass
class TickerData:
    price: float
    volume_24h: float

logger = logging.getLogger(__name__)


class BybitClient:
    def __init__(self, max_retries: int = 3, base_delay: float = 1.0):
        self.base_url = "https://api.bybit.com"
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "BybitMonitorBot/1.0"
        })

    def _make_request(
        self, 
        method: str, 
        endpoint: str, 
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Выполняет HTTP-запрос с умными ретраями.
        """
        url = f"{self.base_url}{endpoint}"
        
        for attempt in range(self.max_retries):
            try:
                response = self.session.request(
                    method, 
                    url, 
                    params=params, 
                    json=json_data, 
                    timeout=10
                )
                response.raise_for_status()
                data = response.json()
                
                # Проверяем логический код ответа Bybit
                ret_code = data.get("retCode")
                if ret_code != 0:
                    ret_msg = data.get("retMsg", "Unknown error")
                    
                    # Коды, при которых имеет смысл сделать ретрай (лимиты, временные сбои)
                    retryable_codes = [-1001, -1000, 10002, 10003, 429]
                    
                    if ret_code in retryable_codes:
                        delay = self.base_delay * (2 ** attempt)
                        logger.warning(f"Bybit API (код {ret_code}): {ret_msg}. Повтор через {delay}с...")
                        time.sleep(delay)
                        continue
                    else:
                        # Невосстанавливаемая логическая ошибка (например, неверный символ)
                        logger.error(f"Bybit API логическая ошибка (код {ret_code}): {ret_msg}")
                        return None
                
                return data.get("result")
                
            except requests.exceptions.RequestException as e:
                delay = self.base_delay * (2 ** attempt)
                logger.warning(f"Сетевая ошибка: {e}. Повтор через {delay}с...")
                time.sleep(delay)
            except Exception as e:
                logger.error(f"Неожиданная ошибка в _make_request: {e}")
                return None
                
        logger.error("Превышено максимальное количество попыток запроса к Bybit API.")
        return None

    def get_ticker(self, symbol: str, category: str = "linear") -> Optional[Any]:
        """Получает данные тикера (цена, объем)"""
        params = {"category": category, "symbol": symbol}
        result = self._make_request("GET", "/v5/market/tickers", params=params)
        if result and "list" in result and len(result["list"]) > 0:
            ticker = result["list"][0]
            return TickerData(
                price=float(ticker['lastPrice']),
                volume_24h=float(ticker['volume24h'])
            )
        return None

    def get_klines(
        self, 
        symbol: str, 
        category: str, 
        interval: str, 
        limit: int = 200
    ) -> Optional[List[List]]:
        """Получает свечи (klines). Возвращает от НОВЫХ к СТАРЫМ."""
        params = {
            "category": category,
            "symbol": symbol,
            "interval": interval,
            "limit": limit
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
        periods: int = 20
    ) -> Optional[Dict[str, float]]:
        """Получает отношение объема текущей свечи к среднему"""
        klines = self.get_klines(symbol, category, interval, limit=periods + 1)
        if not klines or len(klines) < periods + 1:
            return None
        
        current_volume = float(klines[0][5])
        avg_volume = sum(float(k[5]) for k in klines[1:periods+1]) / periods
        
        if avg_volume == 0:
            return {"ratio": 1.0, "current": current_volume, "avg": avg_volume}
            
        return {
            "ratio": current_volume / avg_volume,
            "current": current_volume,
            "avg": avg_volume
        }

    def get_screener_data(
        self, 
        category: str = "linear", 
        sort_by: str = "volume_desc", 
        min_volume_usd: float = 1_000_000, 
        min_change_abs: float = 3.0, 
        limit: int = 50
    ) -> List[Any]:
        """Получает данные для скринера (топ по объему/изменению)"""
        params = {
            "category": category,
            "sort": sort_by,
            "limit": limit
        }
        result = self._make_request("GET", "/v5/market/tickers", params=params)
        
        assets = []
        if result and "list" in result:
            for ticker in result["list"]:
                try:
                    price = float(ticker.get("lastPrice", 0))
                    change_24h = float(ticker.get("price24hPcnt", 0)) * 100
                    volume_24h = float(ticker.get("turnover24h", 0)) # turnover24h в USDT
                    
                    if volume_24h >= min_volume_usd and abs(change_24h) >= min_change_abs:
                        assets.append(type('ScreenerAsset', (object,), {
                            'symbol': ticker['symbol'],
                            'price': price,
                            'price_change_24h': change_24h,
                            'volume_24h': volume_24h
                        }))
                except (ValueError, KeyError):
                    continue
        return assets