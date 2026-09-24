import os, logging
from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
_env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(_env_path)

class Config:
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
    
    POLL_INTERVAL: float = float(os.getenv("POLL_INTERVAL", "4"))
    VOLUME_THRESHOLD: float = float(os.getenv("VOLUME_THRESHOLD", "5.0"))
    VOLUME_COOLDOWN: float = float(os.getenv("VOLUME_COOLDOWN", "300"))
    
    CANDLE_INTERVAL: str = os.getenv("CANDLE_INTERVAL", "15")
    CANDLE_PERIODS: int = int(os.getenv("CANDLE_PERIODS", "20"))
    CANDLE_VOLUME_MULTIPLIER: float = float(os.getenv("CANDLE_VOLUME_MULTIPLIER", "3.0"))
    
    # НОВАЯ НАСТРОЙКА
    ALERT_COOLDOWN_MINUTES: int = int(os.getenv("ALERT_COOLDOWN_MINUTES", "25"))
    
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    DATA_DIR: Path = Path(__file__).parent.parent.parent / "data"
    LOGS_DIR: Path = Path(__file__).parent.parent.parent / "logs"
    ALERTS_FILE: Path = DATA_DIR / "alerts.json"
    
    @classmethod
    def validate(cls) -> tuple[bool, str]:
        if not cls.TELEGRAM_BOT_TOKEN: return False, "TELEGRAM_BOT_TOKEN не заполнен"
        if not cls.TELEGRAM_CHAT_ID: return False, "TELEGRAM_CHAT_ID не заполнен"
        return True, "OK"

Config.DATA_DIR.mkdir(parents=True, exist_ok=True)
Config.LOGS_DIR.mkdir(parents=True, exist_ok=True)