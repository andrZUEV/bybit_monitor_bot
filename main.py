"""
Главный файл запуска бота.
Настройка логирования с ротацией, запуск бота и монитора.
"""

import os
import sys
import logging
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Добавляем корень проекта в путь
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from dotenv import load_dotenv
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters

from src.core.alerts import AlertsManager
from src.core.monitor import Monitor, AlertEvent
from src.core.alerts import AlertsManager
from src.core.monitor import Monitor, AlertEvent
from src.telegram.bot import TelegramBot
from src.utils.data_exporter import DataExporter


def setup_logging():
    """
    Настраивает логирование с ротацией файлов.
    - Консоль: INFO
    - Файл: DEBUG, ротация 5 МБ × 3 backup
    """
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "bot.log"
    
    # Формат логов
    log_format = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    
    # Очищаем старые handlers (на случай перезапуска)
    root_logger.handlers.clear()
    
    # === Консольный handler (INFO) ===
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(log_format)
    root_logger.addHandler(console_handler)
    
    # === Файловый handler с ротацией (DEBUG) ===
    # 5 МБ × 3 backup = максимум 20 МБ логов
    file_handler = RotatingFileHandler(
        filename=log_file,
        maxBytes=5 * 1024 * 1024,  # 5 МБ
        backupCount=3,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(log_format)
    root_logger.addHandler(file_handler)
    
    # === Отключаем шумные логи ===
    # telegram-library слишком многословна
    logging.getLogger('telegram').setLevel(logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    
    logging.info(f"📝 Логирование настроено: {log_file}")


def load_config():
    """Загружает конфигурацию из .env"""
    load_dotenv()
    
    config = {
        'TELEGRAM_TOKEN': os.getenv('TELEGRAM_TOKEN'),
        'ADMIN_CHAT_ID': os.getenv('ADMIN_CHAT_ID'),
        'ALERT_COOLDOWN_MINUTES': int(os.getenv('ALERT_COOLDOWN_MINUTES', '25')),
        'POLL_INTERVAL': float(os.getenv('POLL_INTERVAL', '4.0')),
        'PRICE_RESET_THRESHOLD': float(os.getenv('PRICE_RESET_THRESHOLD', '0.005')),
    }
    
    if not config['TELEGRAM_TOKEN']:
        raise ValueError("❌ TELEGRAM_TOKEN не задан в .env")
    
    if not config['ADMIN_CHAT_ID']:
        raise ValueError("❌ ADMIN_CHAT_ID не задан в .env")
    
    config['ADMIN_CHAT_ID'] = int(config['ADMIN_CHAT_ID'])
    return config


def send_alert_sync(bot_token: str, chat_id: int, event: AlertEvent):
    """
    Отправляет алерт в Telegram через raw requests.
    Запускается в отдельном потоке из монитора.
    """
    import requests
    
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': event.message,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True,
    }
    
    if event.reply_markup:
        # Преобразуем InlineKeyboardMarkup в JSON-совместимый формат
        payload['reply_markup'] = event.reply_markup.to_dict()
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            logging.getLogger(__name__).info(
                f"✅ Алерт отправлен: {event.symbol} @ {event.extra.get('target')}"
            )
        else:
            logging.getLogger(__name__).error(
                f"❌ Ошибка отправки: {response.status_code} {response.text}"
            )
    except Exception as e:
        logging.getLogger(__name__).error(f"❌ Исключение при отправке: {e}")


def on_alert_callback(bot_token: str, chat_id: int):
    """Возвращает callback для обработки событий алерта"""
    def callback(event: AlertEvent):
        # Запускаем отправку в отдельном потоке, чтобы не блокировать монитор
        thread = threading.Thread(
            target=send_alert_sync,
            args=(bot_token, chat_id, event),
            daemon=True
        )
        thread.start()
    return callback


def main():
    """Главная функция запуска"""
    # 1. Настраиваем логирование
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info("🚀 Запуск Bybit Monitor Bot")
    logger.info("=" * 60)
    
    # 2. Загружаем конфиг
    try:
        config = load_config()
    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)
    
    logger.info(f"⚙️  Кулдаун: {config['ALERT_COOLDOWN_MINUTES']} мин")
    logger.info(f"⚙️  Интервал опроса: {config['POLL_INTERVAL']} сек")
    
    # 3. Инициализируем менеджеры
    alerts_manager = AlertsManager(storage_path="data/alerts.json")
    
    # 4. Создаём callback для алертов
    alert_callback = on_alert_callback(
        bot_token=config['TELEGRAM_TOKEN'],
        chat_id=config['ADMIN_CHAT_ID']
    )
    
    # 5. Запускаем монитор в отдельном потоке
    monitor = Monitor(
        alerts_manager=alerts_manager,
        on_alert_callback=alert_callback,
        poll_interval=config['POLL_INTERVAL'],
        price_reset_threshold=config['PRICE_RESET_THRESHOLD'],
        alert_cooldown_minutes=config['ALERT_COOLDOWN_MINUTES']
    )
    
    monitor_thread = threading.Thread(target=monitor.start, daemon=True)
    monitor_thread.start()
    logger.info("📡 Монитор запущен в отдельном потоке")
    
    # 6. Запускаем Telegram-бота
    try:
        app = ApplicationBuilder().token(config['TELEGRAM_TOKEN']).build()
        
        # Команды
        app.add_handler(CommandHandler("start", start_cmd))
        app.add_handler(CommandHandler("menu", menu_main))
        app.add_handler(CommandHandler("export", export_cmd))
        app.add_handler(CommandHandler("screener", screener_cmd))
        app.add_handler(CommandHandler("status", status_cmd))
        
        # Callback-кнопки
        app.add_handler(CallbackQueryHandler(handle_callback))
        
        # Обработка текстовых сообщений (добавление алертов)
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        logger.info("🤖 Telegram-бот запущен. Polling...")
        app.run_polling(drop_pending_updates=True)
        
    except KeyboardInterrupt:
        logger.info("🛑 Получен сигнал остановки")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}", exc_info=True)
    finally:
        logger.info("🛑 Остановка монитора...")
        monitor.stop()
        logger.info("👋 Бот остановлен")


if __name__ == "__main__":
    main()