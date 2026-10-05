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
        [InlineKeyboardButton("📜 История алертов", callback_data="history_menu")],
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

# ==================== НАСТРОЙКИ ====================

def main_menu_keyboard_with_settings(threshold_describe: str) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("➕ Добавить алерт", callback_data="menu_add")],
        [InlineKeyboardButton("🔍 Скринер", callback_data="menu_screener")],
        [InlineKeyboardButton("💰 Текущие цены", callback_data="menu_prices")],
        [InlineKeyboardButton("📋 Мои алерты", callback_data="menu_list")],
        [InlineKeyboardButton("📊 Выгрузить данные", callback_data="menu_export")],
        [InlineKeyboardButton("📜 История алертов", callback_data="history_menu")],
        [InlineKeyboardButton(
            f"⚙️ Настройки (порог: {threshold_describe})",
            callback_data="settings_menu",
        )],
        [InlineKeyboardButton("ℹ️ Помощь", callback_data="menu_help")],
    ]
    return InlineKeyboardMarkup(keyboard)


def settings_menu_keyboard(
    threshold_describe: str,
    invalid_describe: str = "ВКЛ",
    depth_describe: str = "1000",
) -> InlineKeyboardMarkup:
    """Меню настроек."""
    keyboard = [
        [InlineKeyboardButton(
            f"⚙️ Порог алертов: {threshold_describe}",
            callback_data="settings_threshold",
        )],
        [InlineKeyboardButton(
            f"⛔ Hard-filter алерты: {invalid_describe}",
            callback_data="settings_toggle_invalid",
        )],
        [InlineKeyboardButton(
            f"📜 Глубина истории: {depth_describe}",
            callback_data="settings_history_depth",
        )],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
    ]
    return InlineKeyboardMarkup(keyboard)

def toggle_invalid_alerts_keyboard(current_enabled: bool) -> InlineKeyboardMarkup:
    """
    Экран подтверждения переключения флага hard-filter-алертов.

    current_enabled — текущее состояние (True/False).
    """
    if current_enabled:
        # Сейчас ВКЛ — предлагаем ВЫКЛЮЧИТЬ
        action_label = "⛔ Выключить (не слать hard-filter)"
        next_value = "0"
    else:
        # Сейчас ВЫКЛ — предлагаем ВКЛЮЧИТЬ
        action_label = "✅ Включить (слать hard-filter)"
        next_value = "1"

    keyboard = [
        [InlineKeyboardButton(
            action_label,
            callback_data=f"set_invalid|{next_value}",
        )],
        [InlineKeyboardButton("◀ Назад", callback_data="settings_menu")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
    ]
    return InlineKeyboardMarkup(keyboard)

def threshold_presets_keyboard(
    presets: tuple[float, ...],
    current: float,
) -> InlineKeyboardMarkup:
    """
    Выбор порога из пресетов.

    presets — кортеж порогов из Config.ALERT_SCORE_PRESETS.
    current — текущий порог, чтобы отметить активный ✅.
    """
    keyboard: list[list[InlineKeyboardButton]] = []

    # Пресеты в один ряд (или два, если их много)
    row: list[InlineKeyboardButton] = []
    for p in presets:
        label = "ВСЕ" if p == float("-inf") else f"≥ {p:g}"
        if abs(p - current) < 1e-9:
            label = f"✅ {label}"
        # callback_data: set_score|<value>  (значение передаём строкой)
        cb = f"set_score|{p}"
        row.append(InlineKeyboardButton(label, callback_data=cb))

    # Разбиваем по 2 в ряд, чтобы не было слишком широко
    for i in range(0, len(row), 2):
        keyboard.append(row[i:i + 2])

    keyboard.append([
        InlineKeyboardButton("✏️ Свой порог", callback_data="set_score_custom")
    ])
    keyboard.append([
        InlineKeyboardButton("◀ Назад", callback_data="settings_menu")
    ])
    return InlineKeyboardMarkup(keyboard)


def settings_custom_score_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура при вводе своего порога вручную."""
    keyboard = [
        [InlineKeyboardButton("◀ Назад", callback_data="settings_threshold")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
    ]
    return InlineKeyboardMarkup(keyboard)

# ==================== ГЛУБИНА ИСТОРИИ ====================

def history_depth_keyboard(
    presets: tuple[int, ...],
    current: int,
) -> InlineKeyboardMarkup:
    """
    Выбор глубины истории алертов.

    presets — кортеж из Config.HISTORY_DEPTH_PRESETS.
    current — текущая глубина, чтобы отметить активный ✅.
    """
    keyboard: list[list[InlineKeyboardButton]] = []

    row: list[InlineKeyboardButton] = []
    for p in presets:
        label = f"{p}"
        if p == current:
            label = f"✅ {p}"
        row.append(InlineKeyboardButton(label, callback_data=f"set_depth|{p}"))

    # Разбиваем по 2 в ряд
    for i in range(0, len(row), 2):
        keyboard.append(row[i:i + 2])

    keyboard.append([
        InlineKeyboardButton("✏️ Свой", callback_data="set_depth_custom")
    ])
    keyboard.append([
        InlineKeyboardButton("◀ Назад", callback_data="settings_menu")
    ])
    return InlineKeyboardMarkup(keyboard)


def history_custom_depth_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура при вводе своего значения глубины."""
    keyboard = [
        [InlineKeyboardButton("◀ Назад", callback_data="settings_history_depth")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
    ]
    return InlineKeyboardMarkup(keyboard)


def alert_history_menu_keyboard(count: int) -> InlineKeyboardMarkup:
    """Меню «История алертов» (из главного меню)."""
    keyboard = [
        [InlineKeyboardButton(
            f"📄 Выгрузить CSV ({count} записей)",
            callback_data="history_export_csv",
        )],
        [InlineKeyboardButton(
            "🗑 Очистить историю",
            callback_data="history_clear_confirm",
        )],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")],
    ]
    return InlineKeyboardMarkup(keyboard)


def alert_history_clear_confirm_keyboard() -> InlineKeyboardMarkup:
    """Подтверждение очистки истории."""
    keyboard = [
        [InlineKeyboardButton(
            "🗑 Да, очистить всё",
            callback_data="history_clear_yes",
        )],
        [InlineKeyboardButton(
            "◀ Отмена",
            callback_data="history_menu",
        )],
    ]
    return InlineKeyboardMarkup(keyboard)