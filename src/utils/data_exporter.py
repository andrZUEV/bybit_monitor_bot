"""
Модуль выгрузки свечных данных в текстовый файл
"""

import sys
import os
import time
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Dict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from src.api.bybit_client import BybitClient

logger = logging.getLogger(__name__)


def calculate_rsi(closes: List[float], period: int = 14) -> List[Optional[float]]:
    """
    Расчёт RSI(14) по формуле Wilder для списка цен закрытия
    
    Returns:
        Список RSI значений (None для первых period свечей)
    """
    if len(closes) < period + 1:
        return [None] * len(closes)
    
    rsi_values = [None] * period
    
    # Считаем изменения цен
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(c, 0) for c in changes]
    losses = [max(-c, 0) for c in changes]
    
    # Первое среднее
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    # Первый RSI
    if avg_loss == 0:
        rsi_values.append(100.0)
    else:
        rs = avg_gain / avg_loss
        rsi_values.append(100 - (100 / (1 + rs)))
    
    # Остальные RSI (сглаживание Wilder)
    for i in range(period, len(changes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        
        if avg_loss == 0:
            rsi_values.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_values.append(round(100 - (100 / (1 + rs)), 1))
    
    return rsi_values


class DataExporter:
    """Экспорт свечных данных в текстовый файл"""
    
    def __init__(self, alerts_manager: AlertsManager, allowed_chat_id: str, bybit_client: BybitClient):
        self.alerts_manager = alerts_manager
        self.allowed_chat_id = str(allowed_chat_id)
        self.bybit_client = bybit_client
        self.data_exporter = DataExporter(bybit_client)  # <-- ДОБАВИТЬ
        self.user_state: Dict[int, Dict[str, Any]] = {}
        self._screener_cache: Dict[str, tuple] = {}
        self._screener_cache_ttl = 120
        self._last_screener_query: Dict[int, Dict[str, Any]] = {}
        self._last_screener_results: List = []
    
    def can_export(self, symbol: str) -> bool:
        """Проверяет, можно ли сделать экспорт (кулдаун)"""
        symbol = symbol.upper()
        last_time = self._last_export_time.get(symbol, 0)
        return (time.time() - last_time) >= self._export_cooldown
    
    def mark_exported(self, symbol: str):
        """Фиксирует время последнего экспорта"""
        self._last_export_time[symbol.upper()] = time.time()
    
    def export_symbol_data(
        self,
        symbol: str,
        category: str = "linear"
    ) -> Optional[Path]:
        """
        Выгружает данные по символу на 3 таймфреймах
        
        Returns:
            Путь к файлу или None при ошибке
        """
        symbol = symbol.upper()
        
        # Проверяем кулдаун
        if not self.can_export(symbol):
            logger.warning(f"Кулдаун для {symbol}, подождите {self._export_cooldown} сек")
            return None
        
        try:
            # Конфигурация таймфреймов
            timeframes = [
                {"interval": "15", "limit": 150, "label": "15m"},
                {"interval": "60", "limit": 100, "label": "1H"},
                {"interval": "240", "limit": 70, "label": "4H"}
            ]
            
            # Создаём файл
            timestamp = int(time.time())
            filename = f"{symbol}_data_{timestamp}.txt"
            filepath = self.data_dir / filename
            
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(f"=== {symbol} | Bybit Data Export ===\n")
                f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Category: {category}\n\n")
                
                for tf in timeframes:
                    f.write(f"=== {symbol} | {tf['label']} | last {tf['limit']} candles ===\n")
                    f.write("time;open;high;low;close;volume;rsi\n")
                    
                    # Получаем свечи
                    klines = self.client.get_klines(
                        symbol, category, tf["interval"], tf["limit"]
                    )
                    
                    if not klines:
                        f.write("# No data available\n\n")
                        continue
                    
                    # Извлекаем цены закрытия для RSI
                    closes = [float(k[4]) for k in reversed(klines)]
                    rsi_values = calculate_rsi(closes, period=14)
                    
                    # Записываем свечи (от старых к новым)
                    for i, kline in enumerate(reversed(klines)):
                        timestamp_ms = int(kline[0])
                        dt = datetime.fromtimestamp(timestamp_ms / 1000)
                        time_str = dt.strftime('%Y-%m-%d %H:%M')
                        
                        open_price = float(kline[1])
                        high = float(kline[2])
                        low = float(kline[3])
                        close = float(kline[4])
                        volume = float(kline[5])
                        
                        # RSI (берём соответствующее значение)
                        rsi = rsi_values[i] if i < len(rsi_values) else None
                        rsi_str = f"{rsi:.1f}" if rsi is not None else "N/A"
                        
                        f.write(f"{time_str};{open_price};{high};{low};{close};{volume};{rsi_str}\n")
                    
                    f.write("\n")
            
            self.mark_exported(symbol)
            logger.info(f"✅ Экспортированы данные {symbol} в {filepath}")
            return filepath
            
        except Exception as e:
            logger.error(f"Ошибка экспорта данных {symbol}: {e}")
            return None
    
    def export_multiple_symbols(
        self,
        symbols: List[str],
        category: str = "linear"
    ) -> Optional[Path]:
        """Выгружает данные по нескольким символам в один файл"""
        if not symbols:
            return None
        
        try:
            timestamp = int(time.time())
            filename = f"multi_export_{timestamp}.txt"
            filepath = self.data_dir / filename
            
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(f"=== Multi-Symbol Export ===\n")
                f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Symbols: {', '.join(symbols)}\n")
                f.write(f"Category: {category}\n\n")
                
                for symbol in symbols:
                    symbol = symbol.upper()
                    f.write(f"\n{'='*60}\n")
                    f.write(f"SYMBOL: {symbol}\n")
                    f.write(f"{'='*60}\n\n")
                    
                    timeframes = [
                        {"interval": "15", "limit": 150, "label": "15m"},
                        {"interval": "60", "limit": 100, "label": "1H"},
                        {"interval": "240", "limit": 70, "label": "4H"}
                    ]
                    
                    for tf in timeframes:
                        f.write(f"=== {symbol} | {tf['label']} | last {tf['limit']} candles ===\n")
                        f.write("time;open;high;low;close;volume;rsi\n")
                        
                        klines = self.client.get_klines(
                            symbol, category, tf["interval"], tf["limit"]
                        )
                        
                        if not klines:
                            f.write("# No data available\n\n")
                            continue
                        
                        closes = [float(k[4]) for k in reversed(klines)]
                        rsi_values = calculate_rsi(closes, period=14)
                        
                        for i, kline in enumerate(reversed(klines)):
                            timestamp_ms = int(kline[0])
                            dt = datetime.fromtimestamp(timestamp_ms / 1000)
                            time_str = dt.strftime('%Y-%m-%d %H:%M')
                            
                            open_price = float(kline[1])
                            high = float(kline[2])
                            low = float(kline[3])
                            close = float(kline[4])
                            volume = float(kline[5])
                            
                            rsi = rsi_values[i] if i < len(rsi_values) else None
                            rsi_str = f"{rsi:.1f}" if rsi is not None else "N/A"
                            
                            f.write(f"{time_str};{open_price};{high};{low};{close};{volume};{rsi_str}\n")
                        
                        f.write("\n")
                    
                    # Кулдаун между символами
                    time.sleep(0.5)
            
            logger.info(f"✅ Экспортированы данные для {len(symbols)} символов в {filepath}")
            return filepath
            
        except Exception as e:
            logger.error(f"Ошибка массового экспорта: {e}")
            return None
    
    def cleanup_old_files(self, max_age_hours: int = 1):
        """Удаляет старые файлы экспорта"""
        try:
            now = time.time()
            max_age_seconds = max_age_hours * 3600
            
            for filepath in self.data_dir.glob("*.txt"):
                if (now - filepath.stat().st_mtime) > max_age_seconds:
                    filepath.unlink()
                    logger.info(f"🗑 Удален старый файл: {filepath.name}")
        except Exception as e:
            logger.error(f"Ошибка очистки старых файлов: {e}")


# ==================== ТЕСТ ====================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    client = BybitClient()
    exporter = DataExporter(client)
    
    print("📊 Тест выгрузки данных BTCUSDT\n")
    
    filepath = exporter.export_symbol_data("BTCUSDT", "linear")
    
    if filepath:
        print(f"✅ Файл создан: {filepath}")
        print(f"📏 Размер: {filepath.stat().st_size / 1024:.2f} KB")
        
        # Показываем первые 10 строк
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()[:10]
            print("\n📄 Первые 10 строк:")
            for line in lines:
                print(line.rstrip())
    else:
        print("❌ Ошибка экспорта")