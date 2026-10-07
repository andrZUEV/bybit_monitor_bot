"""
Централизованная конфигурация проекта.

Все значения читаются из .env (или берутся дефолты).
Пути к файлам данных строятся от корня проекта, чтобы не зависеть
от текущей рабочей директории запуска.
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

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

    # ==================== Порог алертов ====================
    # Пресеты для кнопки «⚙️ Настройки» в боте.
    # -inf = слать всё (режим тестов). Чем больше — тем «жёстче» фильтр.
    ALERT_SCORE_PRESETS: tuple[float, ...] = (
        float("-inf"),  # ВСЕ
        -5.0,
        0.0,
        3.0,
    )

    HISTORY_DEPTH_PRESETS: tuple[int, ...] = (1000, 5000, 10000, 50000)

    # Пресеты часов UTC для сканера уровней.
    LEVEL_SCAN_HOURS_PRESETS: tuple[str, ...] = (
        "6,18", "8", "0,12", "6,12,18",
    )

    # Пресеты порога score для сканера уровней.
    LEVEL_SCAN_SCORE_PRESETS: tuple[float, ...] = (
        3.0, 5.0, 6.0, 7.0,
    )
    # ==================== Дневник разворотов (Этап 7) ====================
    # Фиксация исхода каждого price-alert'а и статистика по сетапам.

    # Включён ли дневник (пишем pending, резолвим, показываем статистику).
    REVERSAL_DIARY_ENABLED: bool = os.getenv(
        "REVERSAL_DIARY_ENABLED", "true"
    ).strip().lower() in ("1", "true", "yes", "on")

    # Сколько свечей 15m ждём до резолва (8 × 15m = 2 часа).
    REVERSAL_LOOKAHEAD_BARS: int = int(os.getenv("REVERSAL_LOOKAHEAD_BARS", "8"))

    # Ход в сторону алерта ≥ N × ATR_1h → success.
    REVERSAL_MIN_MOVE_ATR: float = float(os.getenv("REVERSAL_MIN_MOVE_ATR", "1.0"))

    # Стоп-зона: level ∓ SL × ATR_1h.
    REVERSAL_SL_BUFFER_ATR: float = float(os.getenv("REVERSAL_SL_BUFFER_ATR", "0.5"))

    # Порог n для показа строки «📈 История...» в алерте.
    REVERSAL_MIN_N_DISPLAY: int = int(os.getenv("REVERSAL_MIN_N_DISPLAY", "15"))

    # Pending без резолва дольше N часов → expired.
    REVERSAL_PENDING_TIMEOUT_HOURS: int = int(
        os.getenv("REVERSAL_PENDING_TIMEOUT_HOURS", "48")
    )

    # Писать ли pending для алертов с hard_filter / низким score.
    # false = только «чистые» алерты (рекомендуется).
    REVERSAL_RECORD_WEAK: bool = os.getenv(
        "REVERSAL_RECORD_WEAK", "false"
    ).strip().lower() in ("1", "true", "yes", "on")

    # Интервал между проходами resolve_all_pending (минуты).
    REVERSAL_RESOLVE_INTERVAL_MIN: int = int(
        os.getenv("REVERSAL_RESOLVE_INTERVAL_MIN", "20")
    )

    # Слать ли hard-filter-алерты (⛔ СДЕЛКА НЕ ПО СТРАТЕГИИ).
    # True — шлём всё (включая hard_filter), False — только валидные сделки.
    # По умолчанию True — обратная совместимость.
    SEND_INVALID_ALERTS_DEFAULT: bool = os.getenv(
        "SEND_INVALID_ALERTS", "true"
    ).strip().lower() in ("1", "true", "yes", "on")

    # ==================== Лимиты свечей ====================
    # Сколько свечей запрашивать при экспорте по каждому ТФ.
    # Больше свечей = качественнее анализ, но больше CSV.
    # Bybit отдаёт максимум 1000 свечей за один запрос.
    EXPORT_LIMITS: dict[str, int] = {
        "15":  300,   # ~75ч     — покрывает 48ч lookback для count_touches
        "60":  300,   # ~12.5д   — EMA50/EMA200 + локальные уровни
        "240": 400,   # ~66д     — глобальный тренд + «бетонные» уровни
        "D":   200,   # ~200 дней — для зеркальных уровней и HH/HL
    }

    # ==================== Риск-менеджмент (Этап 4) ====================
    # Депозит в USDT. Обязателен для расчёта размера позиции.
    # Если 0 — risk-модуль вернёт size=0 (см. validate_setup).
    EQUITY: float = float(os.getenv("EQUITY", "0"))

    # Риск на сделку (доля). Жёсткий предел стратегии — 0.01 (1%).
    # Если в .env больше — validate_setup вернёт risk_above_max.
    RISK_PCT: float = float(os.getenv("RISK_PCT", "0.01"))

    # Шаг лота для округления размера позиции (0 = не округлять).
    # Для BTCUSDT на Bybit linear обычно 0.001.
    LOT_STEP: float = float(os.getenv("LOT_STEP", "0"))

    # Минимальный размер позиции. Если расчётный < MIN_QTY → алерт
    # помечается size_below_min.
    MIN_QTY: float = float(os.getenv("MIN_QTY", "0"))

    # Максимальный размер позиции (защита от ошибок в EQUITY).
    _max_qty_raw: str = os.getenv("MAX_QTY", "").strip()
    MAX_QTY: float = float(_max_qty_raw) if _max_qty_raw else float("inf")

    # Множитель ATR для стопа (стратегия: 1.5).
    ATR_MULTIPLIER: float = float(os.getenv("ATR_MULTIPLIER", "1.5"))

    # Минимальный «запас хода» до TP1 в ATR (стратегия: 2.0).
    MIN_RUNWAY_ATR: float = float(os.getenv("MIN_RUNWAY_ATR", "2.0"))

    # ==================== Пути ====================
    DATA_DIR: Path = PROJECT_ROOT / "data"
    EXPORTS_DIR: Path = DATA_DIR / "exports"
    LOGS_DIR: Path = PROJECT_ROOT / "logs"
    
    ALERTS_FILE: Path = DATA_DIR / "alerts.json"
    COOLDOWNS_FILE: Path = DATA_DIR / "cooldowns.json"
    SETTINGS_FILE: Path = DATA_DIR / "settings.json"
    ALERT_HISTORY_FILE: Path = DATA_DIR / "alert_history.jsonl"
    REVERSAL_DIARY_FILE: Path = DATA_DIR / "reversal_diary.jsonl"

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