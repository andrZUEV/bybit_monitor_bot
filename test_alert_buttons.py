# test_alert_buttons.py — временный скрипт в корне проекта
import logging
logging.basicConfig(level=logging.INFO)

from src.utils.config import Config
from src.core.alerts import AlertsManager
from src.telegram.bot import TelegramBot
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

am = AlertsManager(str(Config.ALERTS_FILE))
bot = TelegramBot(
    token=Config.TELEGRAM_BOT_TOKEN,
    allowed_chat_id=Config.TELEGRAM_CHAT_ID,
    alerts_manager=am,
)

symbol = "SOLUSDT"
target = 200.0
direction = "up"
keyboard = InlineKeyboardMarkup([
    [InlineKeyboardButton(
        f"🗑 Удалить {symbol} @ {target}",
        callback_data=f"del|{symbol}|{target}|{direction}",
    )],
    [InlineKeyboardButton(
        f"📊 Выгрузить данные {symbol}",
        callback_data=f"export_alert_{symbol}",
    )],
    [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
])

msg = (
    f"🧪 <b>Тест кнопок</b>\n"
    f"🪙 {symbol} @ {target}\n"
    f"Score: <b>TEST</b>"
)
bot.send_alert(msg, reply_markup=keyboard)
print("Отправлено. Нажмите кнопку в Telegram.")