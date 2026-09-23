"""
Inline-клавиатуры для Telegram бота
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Главное меню"""
    keyboard = [
        [InlineKeyboardButton("➕ Добавить алерт", callback_data="menu_add")],
        [InlineKeyboardButton("🔍 Скринер", callback_data="menu_screener")],
        [InlineKeyboardButton("💰 Текущие цены", callback_data="menu_prices")],
        [InlineKeyboardButton("📋 Мои алерты", callback_data="menu_list")],
        [InlineKeyboardButton("ℹ️ Помощь", callback_data="menu_help")],
    ]
    return InlineKeyboardMarkup(keyboard)


def direction_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("🟢 Снизу вверх (long)", callback_data="dir_up")],
        [InlineKeyboardButton("🔴 Сверху вниз (short)", callback_data="dir_down")],
        [InlineKeyboardButton("⚪️ Любое пересечение", callback_data="dir_any")],
        [InlineKeyboardButton("❌ Отмена", callback_data="menu_main")],
    ]
    return InlineKeyboardMarkup(keyboard)


def cancel_keyboard() -> InlineKeyboardMarkup:
    keyboard = [[InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")]]
    return InlineKeyboardMarkup(keyboard)


def alerts_list_keyboard(assets: list) -> InlineKeyboardMarkup:
    keyboard = []
    dir_emoji = {"up": "🟢", "down": "🔴", "any": "⚪️"}
    
    for asset in assets:
        keyboard.append([
            InlineKeyboardButton(
                f"🗑 Удалить все алерты {asset.symbol}", 
                callback_data=f"del_all_{asset.symbol}"
            )
        ])
        
        for alert in asset.alerts:
            emoji = dir_emoji.get(alert.direction, "❓")
            label = f"   {emoji} {asset.symbol} @ {alert.price:,.2f}"
            callback = f"del|{asset.symbol}|{alert.price}|{alert.direction}"
            keyboard.append([InlineKeyboardButton(label, callback_data=callback)])
    
    keyboard.append([InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")])
    return InlineKeyboardMarkup(keyboard)


def screener_add_alert_keyboard(symbol: str) -> InlineKeyboardMarkup:
    """Кнопка для быстрого добавления алерта на актив из скринера"""
    keyboard = [
        [InlineKeyboardButton(f"➕ Алерт на {symbol}", callback_data=f"scr_add_{symbol}")],
        [InlineKeyboardButton("🔄 Обновить скринер", callback_data="scr_refresh")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
    ]
    return InlineKeyboardMarkup(keyboard)