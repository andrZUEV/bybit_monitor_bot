"""
Централизованная конфигурация проекта.

Все значения читаются из .env (или берутся дефолты).
Пути к файлам данных строятся от корня проекта, чтобы не зависеть
от текущей рабочей директории запуска.
"""

import os
import logging
from pathlib import Path
from dotenv import load_dotenv

from typing import Dict

logger = logging.getLogger(__name__)

# Корень проекта: .../bybit-monitor-bot/
PROJECT_ROOT = Path(__file__).parent.parent.parent

# Загружаем .env из корня проекта
_env_path = PROJECT_ROOT / ".env"
load_dotenv(_env_path)


class Config:
    # ==================== Telegram ====================
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # ==================== Мониторинг ====================
    # Интервал опроса в секундах (при REST). Для WS не используется.
    POLL_INTERVAL: float = float(os.getenv("POLL_INTERVAL", "4"))

    # Порог объёма (в %) для volume-алертов — legacy, не используется в текущем Monitor
    VOLUME_THRESHOLD: float = float(os.getenv("VOLUME_THRESHOLD", "5.0"))
    VOLUME_COOLDOWN: float = float(os.getenv("VOLUME_COOLDOWN", "300"))

    # ==================== WebSocket ====================
    # Использовать WS для получения цен. False = fallback на REST-поллинг.
    USE_WEBSOCKET: bool = os.getenv("USE_WEBSOCKET", "true").strip().lower() in (
        "1", "true", "yes", "on"
    )
    # URL публичного WS Bybit v5 (linear)
    WS_URL: str = os.getenv(
        "BYBIT_WS_URL", "wss://stream.bybit.com/v5/public/linear"
    )
    # Базовая задержка реконнекта (сек), далее — экспоненциальный backoff
    WS_RECONNECT_DELAY: float = float(os.getenv("WS_RECONNECT_DELAY", "5"))
    # Период ping'а (Bybit требует не реже 20 сек)
    WS_PING_INTERVAL: float = float(os.getenv("WS_PING_INTERVAL", "20"))
    # Период сверки списка подписок с alerts.json
    WS_SYMBOL_REFRESH: float = float(os.getenv("WS_SYMBOL_REFRESH", "10"))

    # ==================== Анализ свечей ====================
    # Таймфрейм для анализа паттернов/RSI (в минутах, формат Bybit: "15", "60", "240")
    CANDLE_INTERVAL: str = os.getenv("CANDLE_INTERVAL", "15")

    # Сколько свечей усреднять для расчёта объёма
    CANDLE_PERIODS: int = int(os.getenv("CANDLE_PERIODS", "20"))

    # Во сколько раз объём должен превысить средний, чтобы считаться всплеском
    CANDLE_VOLUME_MULTIPLIER: float = float(os.getenv("CANDLE_VOLUME_MULTIPLIER", "3.0"))

    # ==================== Кулдауны алертов ====================
    # Сколько минут не повторять один и тот же алерт
    ALERT_COOLDOWN_MINUTES: int = int(os.getenv("ALERT_COOLDOWN_MINUTES", "25"))

    # ==================== Логирование ====================
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # ==================== Лимиты свечей ====================
    # Сколько свечей запрашивать при экспорте по каждому ТФ.
    # Больше свечей = качественнее анализ, но больше CSV.
    # Bybit отдаёт максимум 1000 свечей за один запрос.
    EXPORT_LIMITS: Dict[str, int] = {
        "15":  300,   # ~75ч     — покрывает 48ч lookback для count_touches
        "60":  300,   # ~12.5д   — EMA50/EMA200 + локальные уровни
        "240": 400,   # ~66д     — глобальный тренд + «бетонные» уровни
    }

    # ==================== Пути ====================
    DATA_DIR: Path = PROJECT_ROOT / "data"
    EXPORTS_DIR: Path = DATA_DIR / "exports"
    LOGS_DIR: Path = PROJECT_ROOT / "logs"

    ALERTS_FILE: Path = DATA_DIR / "alerts.json"
    COOLDOWNS_FILE: Path = DATA_DIR / "cooldowns.json"

    # ==================== Валидация ====================
    @classmethod
    def validate(cls) -> tuple[bool, str]:
        """Проверяет обязательные переменные окружения."""
        if not cls.TELEGRAM_BOT_TOKEN:
            return False, "TELEGRAM_BOT_TOKEN не заполнен в .env"
        if not cls.TELEGRAM_CHAT_ID:
            return False, "TELEGRAM_CHAT_ID не заполнен в .env"
        return True, "OK"


# Создаём директории на импорте (чтобы не забывать)
Config.DATA_DIR.mkdir(parents=True, exist_ok=True)
Config.EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
Config.LOGS_DIR.mkdir(parents=True, exist_ok=True)