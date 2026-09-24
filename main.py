"""
Bybit Monitor Bot - Главная точка входа
Объединяет мониторинг цен/объемов и Telegram-бота
"""

import sys
import os
import logging
import time

# Добавляем корень проекта в путь (для запуска python main.py)
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from src.utils.config import Config
from src.core.alerts import AlertsManager
from src.telegram.bot import TelegramBot
from src.core.monitor import Monitor, AlertEvent

# ==================== НАСТРОЙКА ЛОГИРОВАНИЯ ====================

def setup_logging():
    log_format = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"
    
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(Config.LOGS_DIR / "bot.log", encoding="utf-8")
    ]
    
    logging.basicConfig(
        level=getattr(logging, Config.LOG_LEVEL.upper(), logging.INFO),
        format=log_format,
        datefmt=date_format,
        handlers=handlers
    )
    
    # Затыкаем ТОЛЬКО шумные внешние библиотеки, НЕ наш код
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

# ==================== ГЛАВНАЯ ЛОГИКА ====================

def on_alert_callback(event: AlertEvent):
    logger = logging.getLogger(__name__)
    logger.info(f"🔔 Сработал алерт: {event.symbol} ({event.event_type})")
    
    success = telegram_bot.send_alert(event.message, reply_markup=event.reply_markup)
    if not success:
        logger.error("❌ Не удалось отправить алерт в Telegram")

def main():
    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info("🚀 Запуск Bybit Monitor Bot v1.0")
    logger.info("=" * 60)

    # 1. Проверка конфигурации
    ok, msg = Config.validate()
    if not ok:
        logger.error(f"❌ Ошибка конфигурации: {msg}")
        logger.error("Проверьте файл .env и убедитесь, что заполнены TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID")
        sys.exit(1)
    
    logger.info("✅ Конфигурация загружена успешно")

    # 2. Инициализация менеджера алертов
    alerts_manager = AlertsManager(str(Config.ALERTS_FILE))
    total_alerts = sum(len(asset.alerts) for asset in alerts_manager.get_all_alerts())
    logger.info(f"📋 Загружено алертов: {total_alerts}")

    # 3. Инициализация Telegram бота
    global telegram_bot  # Делаем доступным для callback-функции
    telegram_bot = TelegramBot(
        token=Config.TELEGRAM_BOT_TOKEN,
        allowed_chat_id=Config.TELEGRAM_CHAT_ID,
        alerts_manager=alerts_manager
    )
    logger.info("✅ Telegram бот инициализирован")

    # 4. Инициализация Монитора
    monitor = Monitor(
        alerts_manager=alerts_manager,
        on_alert_callback=on_alert_callback,
        poll_interval=Config.POLL_INTERVAL,
        volume_threshold=Config.VOLUME_THRESHOLD,
        volume_cooldown=Config.VOLUME_COOLDOWN,
        price_reset_threshold=0.005,
        candle_interval=Config.CANDLE_INTERVAL,
        candle_periods=Config.CANDLE_PERIODS,
        candle_volume_multiplier=Config.CANDLE_VOLUME_MULTIPLIER,
        alert_cooldown_minutes=Config.ALERT_COOLDOWN_MINUTES
    )
    logger.info("✅ Монитор инициализирован")

    # 5. Запуск компонентов
    logger.info("🔄 Запуск фоновых служб...")
    
    # Запускаем бота в отдельном потоке (неблокирующий)
    telegram_bot.start_async()
    
    # Даём боту 1 секунду на инициализацию
    time.sleep(1)
    
    # Отправляем приветственное сообщение в Telegram С КНОПКАМИ
    welcome_msg = (
        "✅ <b>Bybit Monitor Bot запущен!</b>\n\n"
        f" Отслеживается алертов: <b>{total_alerts}</b>\n"
        f"⏱ Интервал опроса: <b>{Config.POLL_INTERVAL} сек</b>\n"
        f"📈 Порог объема: <b>{Config.VOLUME_THRESHOLD}%</b>\n"
        f"⏳ Кулдаун алертов: <b>{Config.ALERT_COOLDOWN_MINUTES} мин</b>\n\n"
        "Используйте меню для управления:"
    )
    
    # Импортируем клавиатуру
    from src.telegram.keyboards import main_menu_keyboard
    telegram_bot.send_alert(welcome_msg, reply_markup=main_menu_keyboard())

    # 6. Основной цикл (блокирующий)
    try:
        logger.info("▶️ Мониторинг начался. Нажмите Ctrl+C для остановки.")
        monitor.start()
        
    except KeyboardInterrupt:
        logger.info("🛑 Получен сигнал остановки (Ctrl+C)")
    except Exception as e:
        logger.critical(f"💥 Критическая ошибка: {e}", exc_info=True)
    finally:
        # 7. Корректное завершение работы
        logger.info("🧹 Завершение работы и очистка ресурсов...")
        monitor.stop()
        telegram_bot.stop()
        
        # Даём потокам время на корректное завершение
        time.sleep(1.5)
        
        logger.info("✅ Приложение успешно остановлено. До встречи!")
        print("=" * 60)


if __name__ == "__main__":
    setup_logging()
    main()