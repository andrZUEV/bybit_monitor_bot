"""
Inline-клавиатуры для Telegram бота.
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Главное меню."""
    keyboard = [
        [InlineKeyboardButton("➕ Добавить алерт", callback_data="menu_add")],
        [InlineKeyboardButton("🔍 Скринер", callback_data="menu_screener")],
        [InlineKeyboardButton("💰 Текущие цены", callback_data="menu_prices")],
        [InlineKeyboardButton("📋 Мои алерты", callback_data="menu_list")],
        [InlineKeyboardButton("📊 Выгрузить данные", callback_data="menu_export")],
        [InlineKeyboardButton("ℹ️ Помощь", callback_data="menu_help")],
    ]
    return InlineKeyboardMarkup(keyboard)


def export_options_keyboard() -> InlineKeyboardMarkup:
    """Подменю выгрузки данных."""
    keyboard = [
        [InlineKeyboardButton("📋 Отслеживаемые алерты", callback_data="export_tracked")],
        [InlineKeyboardButton("✏️ Ввести тикеры вручную", callback_data="export_manual")],
        [InlineKeyboardButton("🔙 Назад в меню", callback_data="menu_main")],
    ]
    return InlineKeyboardMarkup(keyboard)


def direction_keyboard() -> InlineKeyboardMarkup:
    """Выбор направления для алерта (шаг 3 пошагового диалога)."""
    keyboard = [
        [InlineKeyboardButton("🟢 Снизу вверх (long)", callback_data="dir_up")],
        [InlineKeyboardButton("🔴 Сверху вниз (short)", callback_data="dir_down")],
        [InlineKeyboardButton("⚪️ Любое пересечение", callback_data="dir_any")],
        [InlineKeyboardButton("🏠 Отмена", callback_data="menu_main")],
    ]
    return InlineKeyboardMarkup(keyboard)


def cancel_keyboard() -> InlineKeyboardMarkup:
    """Кнопка отмены (возврат в главное меню)."""
    keyboard = [[InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")]]
    return InlineKeyboardMarkup(keyboard)


def alerts_list_keyboard(assets: list) -> InlineKeyboardMarkup:
    """
    Клавиатура со списком алертов для удаления.
    Для каждого актива:
      - кнопка «удалить все»
      - отдельные кнопки на каждый алерт
    """
    keyboard = []
    dir_emoji = {"up": "🟢", "down": "🔴", "any": "⚪️"}

    for asset in assets:
        # Кнопка «удалить все» — компактнее, чем список всех
        keyboard.append([
            InlineKeyboardButton(
                f"🗑 Удалить все алерты {asset.symbol}",
                callback_data=f"del_all_{asset.symbol}",
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
    """
    Кнопки под результатами скринера.
    Основное действие — добавить алерт на топ-1.
    """
    keyboard = [
        [InlineKeyboardButton(f"➕ Алерт на {symbol}", callback_data=f"scr_add_{symbol}")],
        [InlineKeyboardButton("🔄 Обновить", callback_data="scr_refresh")],
        [InlineKeyboardButton("📊 Выгрузить данные скринера", callback_data="export_screener")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
    ]
    return InlineKeyboardMarkup(keyboard)


def skip_note_keyboard() -> InlineKeyboardMarkup:
    """
    Клавиатура для шага ввода заметки: кнопка «пропустить».
    Используется в пошаговом диалоге добавления алерта.
    """
    keyboard = [
        [InlineKeyboardButton("⏭ Пропустить заметку", callback_data="skip_note")],
        [InlineKeyboardButton("🏠 Отмена", callback_data="menu_main")],
    ]
    return InlineKeyboardMarkup(keyboard)