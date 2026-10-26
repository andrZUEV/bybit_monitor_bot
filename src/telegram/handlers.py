"""
Обработчики команд и callback'ов Telegram бота.

Версия 2.1:
- Все методы внутри класса TelegramHandlers (исправлен развал структуры).
- resolve_symbol для интерактивного ввода тикеров (диалог добавления,
  /data, ручная выгрузка).
- Быстрое однострочное и массовое добавление — просто +USDT без REST (не тормозим).
"""

import asyncio
import logging
import math
import os
import time
from typing import Any

from telegram.ext import ContextTypes

from src.api.bybit_client import BybitClient
from src.core.alert_history import get_alert_history
from src.core.alerts import AlertsManager
from src.core.settings import get_settings
from src.core.virtual_trader import get_virtual_trader
from src.parsers.mass_add import parse_mass_add
from src.telegram import keyboards
from src.telegram.handlers_common import CommonMixin, safe_edit_to_main_menu
from src.telegram.handlers_diary import DiaryMixin
from src.telegram.handlers_export import ExportMixin
from src.telegram.handlers_levels import LevelScanMixin
from src.telegram.handlers_screener import ScreenerMixin
from src.telegram.handlers_vt import VirtualTradeMixin
from src.utils.config import Config
from src.utils.formatting import html_escape
from telegram import InputFile, Update

logger = logging.getLogger(__name__)


class TelegramHandlers(
    CommonMixin,
    ScreenerMixin,
    ExportMixin,
    LevelScanMixin,
    DiaryMixin,
    VirtualTradeMixin,
):
    def __init__(
        self,
        alerts_manager: AlertsManager,
        allowed_chat_id: str,
        bybit_client: BybitClient,
    ):
        self.alerts_manager = alerts_manager
        self.allowed_chat_id = str(allowed_chat_id)
        self.bybit_client = bybit_client
        self.user_state: dict[int, dict[str, Any]] = {}
        self._screener_cache: dict[str, tuple] = {}
        self._screener_cache_ttl = 120
        self._last_screener_results: list = []

        # Кэш сканера уровней (Этап 6): {"data": (...), "ts": float}
        self._level_scan_cache: dict[str, Any] = {}
        self._level_scan_cache_ttl = 30 * 60   # 30 минут
        self._last_level_scan_results: dict[str, Any] = {}
        self._telegram_send: Any = None


    # ==================== КОМАНДЫ ====================

    async def start_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update):
            return
        self._reset_state(update.effective_chat.id)
        total_alerts = sum(len(a.alerts) for a in self.alerts_manager.get_all_alerts())
        await update.message.reply_text(
            "👋 <b>Bybit Monitor Bot</b>\n\n"
            "🔍 <b>Скринер:</b> ищите монеты с сильным движением!\n"
            "⚡ <b>Алерты:</b> получайте уведомления о пробоях.\n\n"
            f"📊 <b>Статус:</b>\n"
            f"• Отслеживается алертов: <b>{total_alerts}</b>\n"
            f"• Интервал опроса: <b>{Config.POLL_INTERVAL:g} сек</b>\n"
            f"• Кулдаун: <b>{Config.ALERT_COOLDOWN_MINUTES} мин</b>\n\n"
            "Выберите действие:",
            parse_mode="HTML",
            reply_markup=self._main_menu_kb(),
        )

    async def menu_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update):
            return
        self._reset_state(update.effective_chat.id)
        await update.message.reply_text(
            "🏠 <b>Главное меню</b>",
            parse_mode="HTML",
            reply_markup=self._main_menu_kb(),
        )

    async def settings_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update):
            return
        s = get_settings()
        await update.message.reply_text(
            f"⚙️ <b>Настройки</b>\n\n"
            f"Порог алертов: <b>{s.describe()}</b>\n"
            f"Hard-filter алерты: <b>{s.describe_invalid()}</b>",
            parse_mode="HTML",
            reply_markup=keyboards.settings_menu_keyboard(
                s.describe(), s.describe_invalid(), s.describe_depth(),
            ),
        )

    async def list_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update):
            return
        await self._show_list(update, context)

    async def help_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update):
            return
        await self._show_help(update)

    async def data_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update):
            return
        if not context.args:
            await update.message.reply_text(
                "❌ Укажите символ\n\n"
                "Пример: <code>/data ETH</code> или <code>/data ETHUSDT</code>",
                parse_mode="HTML",
            )
            return

        user_input = context.args[0].strip()
        symbol = self.bybit_client.resolve_symbol(user_input, "linear")
        if not symbol:
            await update.message.reply_text(
                f"❌ Символ <code>{user_input}</code> не найден на Bybit.\n"
                "Можно писать сокращённо: "
                "<code>eth</code>, <code>ETH</code>, <code>ETHUSDT</code>.",
                parse_mode="HTML",
            )
            return

        await update.message.reply_text(f"⏳ Выгружаю данные {symbol}...")

    # ==================== ТЕКСТОВЫЕ СООБЩЕНИЯ ====================

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update):
            return

        chat_id = update.effective_chat.id
        text = update.message.text.strip()
        state = self.user_state.get(chat_id, {})
        step = state.get("step")

        # --- Экспорт: ожидание тикеров ---
        if step == "waiting_for_export_tickers":
            await self._handle_export_tickers(update, text, chat_id)
            return

        # --- Добавление алерта: ожидание тикера ---
        if step == "waiting_symbol":
            user_input = text.strip()
            symbol = self.bybit_client.resolve_symbol(user_input, "linear")
            if not symbol:
                await update.message.reply_text(
                    f"❌ Тикер <code>{user_input}</code> не найден на Bybit.\n"
                    f"Попробуйте: <code>BTC</code>, <code>BTCUSDT</code>, <code>sol</code>.",
                    parse_mode="HTML",
                    reply_markup=keyboards.cancel_keyboard(),
                )
                return
            self.user_state[chat_id] = {"step": "waiting_price", "symbol": symbol}
            await update.message.reply_text(
                f"✅ Тикер: <b>{symbol}</b>\nВведите цену:",
                parse_mode="HTML",
                reply_markup=keyboards.cancel_keyboard(),
            )
            return

        # --- Добавление алерта: ожидание цены ---
        if step == "waiting_price":
            try:
                price = float(text.replace(",", "."))
                if price <= 0:
                    raise ValueError("Цена должна быть > 0")
            except ValueError:
                await update.message.reply_text(
                    "❌ Введите корректное число. Например: <code>85000.5</code>",
                    parse_mode="HTML",
                    reply_markup=keyboards.cancel_keyboard(),
                )
                return

            symbol = state["symbol"]
            self.user_state[chat_id] = {
                "step": "waiting_direction",
                "symbol": symbol,
                "price": price,
            }
            await update.message.reply_text(
                f"✅ Цена: <b>{price}</b>\n\n"
                f"Выберите направление для <b>{symbol}</b>:",
                parse_mode="HTML",
                reply_markup=keyboards.direction_keyboard(),
            )
            return

        # --- Добавление алерта: ожидание заметки (после выбора направления) ---
        if step == "waiting_note":
            note = text.strip()
            if note in ("-", "—", ""):
                note = ""
            await self._finalize_add_alert(update, state, note, chat_id)
            return

        # --- Настройки: ввод своего порога ---
        if step == "waiting_custom_score":
            from src.core.settings import RuntimeSettings as _RS
            new_score = _RS._parse_score(text.strip(), default=float("nan"))
            if math.isnan(new_score):
                await update.message.reply_text(
                    "❌ Не понял число. Введите, например, "
                    "<code>-2.5</code> или <code>all</code>.",
                    parse_mode="HTML",
                    reply_markup=keyboards.settings_custom_score_keyboard(),
                )
                return
            s = get_settings()
            s.alert_min_score = new_score
            s.save()
            self._reset_state(chat_id)
            logger.info(f"⚙️ Порог алертов изменён: {s.describe()}")
            await update.message.reply_text(
                f"✅ Порог установлен: <b>{s.describe()}</b>",
                parse_mode="HTML",
                reply_markup=keyboards.settings_menu_keyboard(
                    s.describe(), s.describe_invalid(), s.describe_depth(),
                ),
            )
            return

        # --- Настройки: ввод своей глубины истории ---
        if step == "waiting_custom_depth":
            try:
                new_depth = int(text.strip())
            except ValueError:
                await update.message.reply_text(
                    "❌ Не понял число. Введите, например, <code>2000</code>.",
                    parse_mode="HTML",
                    reply_markup=keyboards.history_custom_depth_keyboard(),
                )
                return
            new_depth = max(100, min(100_000, new_depth))
            s = get_settings()
            s.alert_history_depth = new_depth
            s.save()
            history = get_alert_history()
            history.set_max_records(new_depth)
            self._reset_state(chat_id)
            logger.info(f"📜 Глубина истории: {s.describe_depth()}")
            await update.message.reply_text(
                f"✅ Глубина истории: <b>{s.describe_depth()}</b>",
                parse_mode="HTML",
                reply_markup=keyboards.settings_menu_keyboard(
                    s.describe(), s.describe_invalid(), s.describe_depth(),
                ),
            )
            return

        # --- Сканер уровней: ввод своих часов UTC ---
        if step == "waiting_level_scan_hours":
            s = get_settings()
            normalized = s._parse_hours_utc(text.strip())
            if not normalized:
                await update.message.reply_text(
                    "❌ Не понял часы. Введите, например, "
                    "<code>6,18</code> (числа 0-23 через запятую).",
                    parse_mode="HTML",
                    reply_markup=keyboards.cancel_keyboard(),
                )
                return
            s.level_scan_hours_utc = normalized
            s.save()
            self._reset_state(chat_id)
            logger.info(f"⏰ Сканер уровней: часы {s.level_scan_hours_utc}")
            await update.message.reply_text(
                f"✅ Часы сканера: <b>{s.level_scan_hours_utc}</b> UTC",
                parse_mode="HTML",
                reply_markup=self._main_menu_kb(),
            )
            return

        # --- Дневник разворотов: ввод своего min_n ---
        if step == "waiting_rd_min_n":
            try:
                n = int(text.strip())
            except ValueError:
                await update.message.reply_text(
                    "❌ Не понял число. Введите, например, <code>20</code>.",
                    parse_mode="HTML",
                    reply_markup=keyboards.cancel_keyboard(),
                )
                return
            n = max(1, min(1000, n))
            s = get_settings()
            s.reversal_min_n_display = n
            s.save()
            self._reset_state(chat_id)
            logger.info(f"🎯 Дневник: min_n = {s.reversal_min_n_display}")
            await update.message.reply_text(
                f"✅ Мин. n: <b>{s.reversal_min_n_display}</b>",
                parse_mode="HTML",
                reply_markup=self._main_menu_kb(),
            )
            return

        # --- Массовое добавление (текстом, без диалога) ---
        if "\n" in text.strip():
            await self._process_bulk_add(update, text)
            return

        # --- Однострочное добавление: SYMBOL PRICE DIR [NOTE] ---
        result = parse_mass_add(text)
        if result.valid:
            if len(result.valid) > 1:
                logger.warning(
                    f"⚠️ handle_text: неожиданно >1 valid без '\\n' в text={text!r}; "
                    f"ухожу в bulk"
                )
                await self._process_bulk_add(update, text)
                return
            p = result.valid[0]
            note_lower = (p.setup_note or "").lower()
            category = (
                "spot"
                if "spot" in note_lower or "спот" in note_lower
                else "linear"
            )
            clean_note = p.setup_note or ""
            success, replaced = self.alerts_manager.add_alert(
                p.symbol, p.price, p.direction, category, clean_note
            )
            dir_text = {
                "up": "снизу вверх 🟢",
                "down": "сверху вниз 🔴",
                "any": "любое ⚪️",
            }
            note_display = f"\n📝 Сетап: <code>{clean_note}</code>" if clean_note else ""
            if success:
                verb = "Обновлено" if replaced else "Добавлено"
                await update.message.reply_text(
                    f"✅ <b>{verb}:</b>\n"
                    f"🪙 {p.symbol}\n💰 {p.price:,.2f}\n"
                    f"🎯 {dir_text[p.direction]}{note_display}",
                    parse_mode="HTML",
                    reply_markup=self._main_menu_kb(),
                )
            else:
                await update.message.reply_text(
                    "⚠️ Уже существует",
                    reply_markup=self._main_menu_kb(),
                )
            return

        # Если не распарсили — общий «не понял»
        await update.message.reply_text(
            "❓ Не понял команду. Проверьте формат:\n"
            "<code>TICKER PRICE DIR [ОПИСАНИЕ]</code>\n"
            "Например: <code>BTC 65000 up пробой хая</code>",
            parse_mode="HTML",
            reply_markup=self._main_menu_kb(),
        )

    async def _finalize_add_alert(
        self, update: Update, state: dict[str, Any], note: str, chat_id: int
    ):
        """Завершает добавление алерта после выбора направления и заметки."""
        symbol = state["symbol"]
        price = state["price"]
        direction = state["direction"]

        success, replaced = self.alerts_manager.add_alert(
            symbol, price, direction, "linear", note
        )

        dir_text = {"up": "снизу вверх 🟢", "down": "сверху вниз 🔴", "any": "любое ⚪️"}
        note_display = f"\n📝 Сетап: <code>{note}</code>" if note else ""

        if success:
            verb = "Обновлено" if replaced else "Добавлено"
            await update.message.reply_text(
                f"✅ <b>{verb}:</b>\n"
                f"🪙 {symbol}\n💰 {price:,.2f}\n"
                f"🎯 {dir_text.get(direction, direction)}{note_display}",
                parse_mode="HTML",
                reply_markup=self._main_menu_kb(),
            )
        else:
            await update.message.reply_text(
                "⚠️ Не удалось сохранить алерт",
                reply_markup=self._main_menu_kb(),
            )

        self._reset_state(chat_id)

    async def _process_bulk_add(self, update: Update, text: str):
        result = parse_mass_add(text)

        success_count = 0
        replaced_count = 0
        failed_details: list[str] = []

        for p in result.valid:
            note_lower = (p.setup_note or "").lower()
            category = (
                "spot"
                if "spot" in note_lower or "спот" in note_lower
                else "linear"
            )
            clean_note = p.setup_note or ""
            success, replaced = self.alerts_manager.add_alert(
                p.symbol, p.price, p.direction, category, clean_note
            )
            if success:
                if replaced:
                    replaced_count += 1
                else:
                    success_count += 1
            else:
                failed_details.append(f"• {p.raw} (не сохранилось)")

        for s in result.skipped:
            failed_details.append(f"• строка {s.line_no}: {s.raw} — {s.reason}")

        report = "📊 <b>Результат:</b>\n"
        report += f"✅ Добавлено: <b>{success_count}</b>\n"
        if replaced_count:
            report += f"🔄 Обновлено: <b>{replaced_count}</b>\n"
        if result.skipped or failed_details:
            report += f"❌ Пропущено: <b>{len(result.skipped)}</b>\n"
            if failed_details:
                report += "\n".join(failed_details[:5]) + "\n"
        else:
            report += "\n🎉 Всё обработано!"

        await update.message.reply_text(
            report, parse_mode="HTML", reply_markup=self._main_menu_kb()
        )

    # ==================== CALLBACK'И ====================

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        data = query.data
        logger.info(
            f"🔘 CALLBACK: data={data!r} | chat_id={update.effective_chat.id}"
        )

        await query.answer()

        if not self._is_allowed(update):
            await query.edit_message_text("⛔️ Доступ запрещён")
            return

        chat_id = query.from_user.id

        # ========== Меню ==========
        if data == "menu_main":
            self._reset_state(chat_id)
            await safe_edit_to_main_menu(
                query,
                "🏠 <b>Главное меню</b>",
                parse_mode="HTML",
                reply_markup=self._main_menu_kb(),
            )

        elif data == "menu_add":
            self.user_state[chat_id] = {"step": "waiting_symbol"}
            await query.edit_message_text(
                "➕ <b>Добавить алерт</b>\n\n"
                "Введите тикер (например, <code>BTCUSDT</code> или <code>BTC</code>):",
                parse_mode="HTML",
                reply_markup=keyboards.cancel_keyboard(),
            )

        elif data == "menu_prices":
            await self._handle_current_prices(update)

        elif data == "menu_list":
            await self._show_list(update, context)

        elif data == "menu_help":
            await self._show_help(update)

        elif data == "menu_screener":
            await self._run_screener_simple(update, chat_id)

        elif data == "scr_refresh":
            self._screener_cache.clear()
            await self._run_screener_simple(update, chat_id)

        # ========== Экспорт ==========
        elif data == "menu_export":
            await query.edit_message_text(
                "📊 <b>Выгрузка данных</b>\n\nВыберите способ выгрузки:",
                parse_mode="HTML",
                reply_markup=keyboards.export_options_keyboard(),
            )

        elif data == "export_tracked":
            assets = self.alerts_manager.get_all_alerts()
            if not assets:
                await query.edit_message_text(
                    "📋 Нет отслеживаемых активов.",
                    reply_markup=self._main_menu_kb(),
                )
                return
            symbols = list({a.symbol for a in assets})
            await query.edit_message_text(f"⏳ Выгружаю данные для {len(symbols)} активов...")
            filepath = await asyncio.to_thread(
                self._generate_export_file, symbols
            )
            if filepath and os.path.exists(filepath):
                with open(filepath, "rb") as f:
                    await update.callback_query.message.reply_document(
                        document=InputFile(f, filename=os.path.basename(filepath)),
                        caption=f"✅ Данные выгружены\nАктивов: {len(symbols)}",
                    )
                os.remove(filepath)
                await query.edit_message_text(
                    "✅ Файл отправлен!", reply_markup=self._main_menu_kb()
                )
            else:
                await query.edit_message_text(
                    "❌ Ошибка экспорта", reply_markup=self._main_menu_kb()
                )

        elif data == "export_manual":
            self.user_state[chat_id] = {"step": "waiting_for_export_tickers"}
            await query.edit_message_text(
                "✏️ <b>Ручная выгрузка</b>\n\nВведите тикеры через пробел:\n"
                "Например: <code>BTC ETH</code> или <code>BTCUSDT ETHUSDT</code>",
                parse_mode="HTML",
                reply_markup=keyboards.cancel_keyboard(),
            )

        elif data == "export_screener":
            if not self._last_screener_results:
                await query.edit_message_text(
                    "⚠️ Сначала запустите скринер.",
                    reply_markup=self._main_menu_kb(),
                )
                return
            symbols = [asset.symbol for asset in self._last_screener_results]
            await query.edit_message_text(
                f"⏳ Выгружаю данные для {len(symbols)} активов из скринера..."
            )
            filepath = await asyncio.to_thread(
                self._generate_export_file, symbols
            )
            if filepath and os.path.exists(filepath):
                with open(filepath, "rb") as f:
                    await update.callback_query.message.reply_document(
                        document=InputFile(f, filename=os.path.basename(filepath)),
                        caption=f"✅ Данные скринера выгружены\nАктивов: {len(symbols)}",
                    )
                os.remove(filepath)
                await query.edit_message_text(
                    "✅ Файл отправлен!", reply_markup=self._main_menu_kb()
                )
            else:
                await query.edit_message_text(
                    "❌ Ошибка экспорта", reply_markup=self._main_menu_kb()
                )

        elif data.startswith("export_alert_"):
            await self._handle_export_alert(update, data)

        # ========== Удаление алертов ==========
        elif data.startswith("del_all_"):
            symbol = data.replace("del_all_", "").upper()
            removed = self.alerts_manager.remove_all_alerts_for_symbol(symbol)
            if removed:
                await query.edit_message_text(
                    f"🗑 Все алерты для <b>{symbol}</b> удалены",
                    parse_mode="HTML",
                    reply_markup=self._main_menu_kb(),
                )
            else:
                await query.edit_message_text(
                    f"⚠️ Алертов для <b>{symbol}</b> не найдено",
                    parse_mode="HTML",
                    reply_markup=self._main_menu_kb(),
                )

        elif data.startswith("del|"):
            await self._handle_delete_alert(update, data)

        # ========== Направление при добавлении ==========
        elif data in ("dir_up", "dir_down", "dir_any"):
            direction = data.replace("dir_", "")
            state = self.user_state.get(chat_id, {})
            if state.get("step") != "waiting_direction":
                await query.answer("Сессия истекла, начните заново", show_alert=True)
                return
            state["direction"] = direction
            state["step"] = "waiting_note"
            self.user_state[chat_id] = state
            await query.edit_message_text(
                f"✅ Направление: <b>{direction}</b>\n\n"
                f"Введите заметку (например, «пробой сопротивления»)\n"
                f"Или отправьте <code>-</code>, чтобы пропустить.",
                parse_mode="HTML",
                reply_markup=keyboards.cancel_keyboard(),
            )

        # ========== Скринер: добавить алерт ==========
        elif data.startswith("scr_add_"):
            symbol = data.replace("scr_add_", "").upper()
            self.user_state[chat_id] = {"step": "waiting_price", "symbol": symbol}
            await query.edit_message_text(
                f"➕ <b>Алерт на {symbol}</b>\n\nВведите цену:",
                parse_mode="HTML",
                reply_markup=keyboards.cancel_keyboard(),
            )

        # ========== Настройки ==========
        elif data == "settings_menu":
            s = get_settings()
            await query.edit_message_text(
                f"⚙️ <b>Настройки</b>\n\n"
                f"Порог алертов: <b>{s.describe()}</b>\n"
                f"Hard-filter алерты: <b>{s.describe_invalid()}</b>",
                parse_mode="HTML",
                reply_markup=keyboards.settings_menu_keyboard(
                    s.describe(), s.describe_invalid(), s.describe_depth(),
                ),
            )

        elif data == "settings_threshold":
            s = get_settings()
            await query.edit_message_text(
                f"⚙️ <b>Порог алертов</b>\n\n"
                f"Текущий: <b>{s.describe()}</b>\n\n"
                f"Выберите пресет или введите своё значение.",
                parse_mode="HTML",
                reply_markup=keyboards.threshold_presets_keyboard(
                    Config.ALERT_SCORE_PRESETS, s.alert_min_score,
                ),
            )

        elif data.startswith("set_score|"):
            raw = data.split("|", 1)[1]
            from src.core.settings import RuntimeSettings as _RS
            new_score = _RS._parse_score(raw, default=float("nan"))
            if math.isnan(new_score):
                await query.answer("Ошибка парсинга порога", show_alert=True)
                return
            s = get_settings()
            s.alert_min_score = new_score
            s.save()
            logger.info(f"⚙️ Порог алертов изменён: {s.describe()}")
            await query.edit_message_text(
                f"✅ Порог установлен: <b>{s.describe()}</b>",
                parse_mode="HTML",
                reply_markup=keyboards.settings_menu_keyboard(
                    s.describe(), s.describe_invalid(), s.describe_depth(),
                ),
            )

        elif data == "set_score_custom":
            self.user_state[chat_id] = {"step": "waiting_custom_score"}
            await query.edit_message_text(
                "✏️ <b>Свой порог</b>\n\n"
                "Введите число (например, <code>-2.5</code> или <code>1</code>).\n\n"
                "Что это значит:\n"
                "• Чем больше — тем строже фильтр.\n"
                "• Отрицательные значения — слать почти всё.\n"
                "• Введите <code>all</code>, чтобы слать всё (без порога).",
                parse_mode="HTML",
                reply_markup=keyboards.settings_custom_score_keyboard(),
            )

        # ========== Настройки: hard-filter алерты ==========
        elif data == "settings_toggle_invalid":
            s = get_settings()
            await query.edit_message_text(
                f"⛔ <b>Hard-filter алерты</b>\n\n"
                f"Текущее: <b>{s.describe_invalid()}</b>\n\n"
                f"Hard-filter — это алерты, помеченные «СДЕЛКА НЕ ПО СТРАТЕГИИ» "
                f"(например, 4H-тренд против направления входа).\n\n"
                f"• <b>ВКЛ</b> — такие алерты приходят всегда, независимо от score.\n"
                f"• <b>ВЫКЛ</b> — такие алерты не отправляются.",
                parse_mode="HTML",
                reply_markup=keyboards.toggle_invalid_alerts_keyboard(
                    s.send_invalid_alerts,
                ),
            )

        elif data.startswith("set_invalid|"):
            raw = data.split("|", 1)[1]
            new_value = raw == "1"
            s = get_settings()
            s.send_invalid_alerts = new_value
            s.save()
            logger.info(
                f"⛔ Hard-filter алерты: {s.describe_invalid()}"
            )
            await query.edit_message_text(
                f"✅ Hard-filter алерты: <b>{s.describe_invalid()}</b>",
                parse_mode="HTML",
                reply_markup=keyboards.settings_menu_keyboard(
                    s.describe(), s.describe_invalid(), s.describe_depth(),
                ),
            )

        # ========== Глубина истории ==========
        elif data == "settings_history_depth":
            s = get_settings()
            await query.edit_message_text(
                f"📜 <b>Глубина истории алертов</b>\n\n"
                f"Текущая: <b>{s.describe_depth()}</b>\n\n"
                f"Сколько последних алертов хранить для анализа.\n"
                f"Файл: <code>{Config.ALERT_HISTORY_FILE.name}</code>",
                parse_mode="HTML",
                reply_markup=keyboards.history_depth_keyboard(
                    Config.HISTORY_DEPTH_PRESETS,
                    s.alert_history_depth,
                ),
            )

        elif data.startswith("set_depth|"):
            raw = data.split("|", 1)[1]
            try:
                new_depth = int(raw)
            except ValueError:
                await query.answer("Ошибка парсинга", show_alert=True)
                return
            s = get_settings()
            s.alert_history_depth = max(100, min(100_000, new_depth))
            s.save()
            history = get_alert_history()
            history.set_max_records(s.alert_history_depth)
            logger.info(f"📜 Глубина истории: {s.describe_depth()}")
            await query.edit_message_text(
                f"✅ Глубина истории: <b>{s.describe_depth()}</b>",
                parse_mode="HTML",
                reply_markup=keyboards.settings_menu_keyboard(
                    s.describe(), s.describe_invalid(), s.describe_depth(),
                ),
            )

        elif data == "set_depth_custom":
            self.user_state[chat_id] = {"step": "waiting_custom_depth"}
            await query.edit_message_text(
                "✏️ <b>Своя глубина истории</b>\n\n"
                "Введите число от <b>100</b> до <b>100000</b>.\n"
                "Например: <code>2000</code>",
                parse_mode="HTML",
                reply_markup=keyboards.history_custom_depth_keyboard(),
            )

        # ========== История алертов ==========
        elif data == "history_menu":
            history = get_alert_history()
            count = history.count()
            await query.edit_message_text(
                f"📜 <b>История алертов</b>\n\n"
                f"Записей: <b>{count}</b>\n"
                f"Глубина: <b>{history.max_records}</b>\n"
                f"Файл: <code>{history.path.name}</code>\n\n"
                f"Выгрузить в CSV или очистить:",
                parse_mode="HTML",
                reply_markup=keyboards.alert_history_menu_keyboard(count),
            )

        elif data == "history_export_csv":
            history = get_alert_history()
            if history.is_empty():
                await query.answer("История пуста", show_alert=True)
                return
            await query.edit_message_text("⏳ Генерирую CSV...")
            from src.utils.config import Config as _C
            out_path = _C.EXPORTS_DIR / f"alert_history_{int(time.time())}.csv"
            csv_path = await asyncio.to_thread(history.export_csv, out_path)
            with open(csv_path, "rb") as f:
                await query.message.reply_document(
                    document=InputFile(f, filename=csv_path.name),
                    caption=f"📜 История алертов: {history.count()} записей",
                )
            try:
                os.remove(csv_path)
            except Exception:
                pass
            await query.edit_message_text(
                "✅ CSV отправлен!",
                reply_markup=keyboards.alert_history_menu_keyboard(history.count()),
            )

        elif data == "history_clear_confirm":
            await query.edit_message_text(
                "🗑 <b>Очистить всю историю алертов?</b>\n\n"
                "Действие необратимо.",
                parse_mode="HTML",
                reply_markup=keyboards.alert_history_clear_confirm_keyboard(),
            )

        elif data == "history_clear_yes":
            history = get_alert_history()
            history.clear()
            await query.edit_message_text(
                "✅ История очищена",
                reply_markup=keyboards.alert_history_menu_keyboard(0),
            )

        # ========== Сканер уровней (Этап 6) ==========
        elif data == "ls_menu":
            await self._show_level_scan_menu(query)

        elif data == "ls_scan_now":
            await self._run_level_scan(query, force_refresh=False)

        elif data == "ls_show_all":
            await self._show_level_scan_all(query)

        elif data == "ls_show_compact":
            await self._show_level_scan_compact(query)

        elif data == "ls_add_all":
            await self._handle_level_scan_add_all(query)

        elif data.startswith("ls_add_sym|"):
            symbol = data.split("|", 1)[1]
            await self._handle_level_scan_add_symbol(query, symbol)

        elif data.startswith("ls_add|"):
            await self._handle_level_scan_add(query, data)

        elif data == "ls_toggle_enabled":
            s = get_settings()
            s.level_scan_enabled = not s.level_scan_enabled
            s.save()
            logger.info(f"🔔 Сканер уровней: {s.describe_level_scan()}")
            await self._show_level_scan_menu(query)

        elif data == "ls_set_min_score":
            s = get_settings()
            await query.edit_message_text(
                f"🎯 <b>Порог score для сканера</b>\n\n"
                f"Текущий: <b>≥ {s.level_scan_min_score:g}</b>\n\n"
                f"Уровни с меньшим score в сводку не попадают.",
                parse_mode="HTML",
                reply_markup=keyboards.level_scan_score_presets_keyboard(
                    Config.LEVEL_SCAN_SCORE_PRESETS, s.level_scan_min_score,
                ),
            )

        elif data.startswith("ls_set_min_score|"):
            raw = data.split("|", 1)[1]
            from src.core.settings import RuntimeSettings as _RS
            new_score = _RS._parse_score(raw, default=float("nan"))
            if math.isnan(new_score):
                await query.answer("Ошибка парсинга", show_alert=True)
                return
            s = get_settings()
            s.level_scan_min_score = new_score
            s.save()
            logger.info(f"🎯 Сканер уровней: порог {s.describe_level_scan_score()}")
            await self._show_level_scan_menu(query)

        elif data == "ls_set_hours":
            s = get_settings()
            await query.edit_message_text(
                f"⏰ <b>Часы автоскана (UTC)</b>\n\n"
                f"Текущие: <b>{s.level_scan_hours_utc}</b>\n\n"
                f"Скан запускается в указанные часы. "
                f"Можно задать свои через запятую, например <code>6,18</code>.",
                parse_mode="HTML",
                reply_markup=keyboards.level_scan_hours_presets_keyboard(
                    Config.LEVEL_SCAN_HOURS_PRESETS, s.level_scan_hours_utc,
                ),
            )

        elif data.startswith("ls_set_hours|"):
            raw = data.split("|", 1)[1]
            s = get_settings()
            s.level_scan_hours_utc = s._parse_hours_utc(raw)
            s.save()
            logger.info(f"⏰ Сканер уровней: часы {s.level_scan_hours_utc}")
            await self._show_level_scan_menu(query)

        elif data == "ls_set_hours_custom":
            self.user_state[chat_id] = {"step": "waiting_level_scan_hours"}
            await query.edit_message_text(
                "✏️ <b>Свои часы UTC</b>\n\n"
                "Введите часы через запятую (0-23).\n"
                "Например: <code>6,18</code> или <code>0,8,16</code>",
                parse_mode="HTML",
                reply_markup=keyboards.cancel_keyboard(),
            )

        # ========== Дневник разворотов (Этап 7) ==========
        elif data == "rd_menu":
            await self._show_rd_menu(query)

        elif data == "rd_show_stats":
            await self._show_rd_stats(query)

        elif data == "rd_toggle_enabled":
            s = get_settings()
            s.reversal_diary_enabled = not s.reversal_diary_enabled
            s.save()
            logger.info(f"📈 Дневник разворотов: {s.describe_reversal_diary()}")
            await self._show_rd_menu(query)

        elif data == "rd_set_min_n":
            s = get_settings()
            await query.edit_message_text(
                f"🎯 <b>Минимальное n для показа</b>\n\n"
                f"Текущее: <b>{s.reversal_min_n_display}</b>\n\n"
                f"Строка «📈 История ...» появляется в алерте только "
                f"если накоплено ≥ N наблюдений по сетапу.",
                parse_mode="HTML",
                reply_markup=keyboards.rd_min_n_presets_keyboard(
                    Config.REVERSAL_MIN_N_PRESETS,
                    s.reversal_min_n_display,
                ),
            )

        elif data.startswith("rd_set_min_n|"):
            raw = data.split("|", 1)[1]
            try:
                n = int(raw)
            except ValueError:
                await query.answer("Ошибка парсинга", show_alert=True)
                return
            n = max(1, min(1000, n))
            s = get_settings()
            s.reversal_min_n_display = n
            s.save()
            logger.info(f"🎯 Дневник: min_n = {s.reversal_min_n_display}")
            await self._show_rd_menu(query)

        elif data == "rd_set_min_n_custom":
            self.user_state[chat_id] = {"step": "waiting_rd_min_n"}
            await query.edit_message_text(
                "✏️ <b>Своё значение min_n</b>\n\n"
                "Введите целое число от <b>1</b> до <b>1000</b>.\n"
                "Например: <code>20</code>",
                parse_mode="HTML",
                reply_markup=keyboards.cancel_keyboard(),
            )

        elif data == "rd_export_csv":
            await self._handle_rd_export_csv(query)

        elif data == "rd_clear_confirm":
            await query.edit_message_text(
                "🗑 <b>Очистить весь дневник разворотов?</b>\n\n"
                "Все записи (pending / success / fail / expired) будут удалены.\n"
                "Действие необратимо.",
                parse_mode="HTML",
                reply_markup=keyboards.rd_clear_confirm_keyboard(),
            )

        elif data == "rd_clear_yes":
            from src.core.reversal_diary import get_reversal_diary
            diary = get_reversal_diary()
            diary.clear()
            logger.info("🗑 Дневник разворотов очищен")
            await self._show_rd_menu(query)

        # ========== Виртуальные сделки ==========
        elif data == "vt_menu":
            await self._show_vt_menu(query)
            
        elif data == "vt_list_active":
            await self._show_vt_list(query, "active")
            
        elif data == "vt_list_closed":
            await self._show_vt_list(query, "closed")
            
        elif data == "vt_stats":
            await self._show_vt_stats(query)
            
        elif data == "vt_toggle_enabled":
            s = get_settings()
            s.virtual_trader_enabled = not s.virtual_trader_enabled
            s.save()
            await self._show_vt_menu(query)

        elif data.startswith("vt_take|"):
            await self._handle_take_to_diary(query, data)
            
        elif data.startswith("vt_cancel|"):
            trade_id = data.split("|", 1)[1]
            trader = get_virtual_trader()
            if trader.cancel(trade_id):
                await query.answer("✅ Сделка отменена", show_alert=False)
                await self._show_vt_menu(query)
            else:
                await query.answer("Не удалось отменить", show_alert=True)

        # ========== Необработанный callback ==========
        else:
            logger.warning(f"⚠️ Необработанный callback: {data!r}")
            await query.answer("Команда в разработке", show_alert=False)



    async def _handle_delete_alert(self, update: Update, data: str):
        """
        Удаление одного алерта. Формат: del|SYMBOL|PRICE|DIRECTION.

        Исходное сообщение алерта не редактируется по тексту,
        но у него убирается клавиатура (чтобы кнопки нельзя было нажать повторно).
        Статус — отдельным сообщением.
        """
        query = update.callback_query
        parts = data.split("|")
        if len(parts) != 4:
            await query.answer("Ошибка формата", show_alert=True)
            return
        _, symbol, price_str, direction = parts
        try:
            price = float(price_str)
        except ValueError:
            await query.answer("Ошибка цены", show_alert=True)
            return

        removed = self.alerts_manager.remove_alert(symbol, price, direction)
        if removed:
            # Убираем клавиатуру у алерта — кнопки уже неактуальны.
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass

            # Статус — новым сообщением, чтобы алерт остался в чате.
            await query.message.reply_text(
                f"🗑 Удалён алерт <b>{symbol}</b> @ {price}",
                parse_mode="HTML",
                reply_markup=self._main_menu_kb(),
            )
        else:
            await query.answer("Алерт не найден", show_alert=True)



    # ==================== ЭКРАНЫ ====================

    async def _show_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Список всех алертов."""
        assets = self.alerts_manager.get_all_alerts()

        if not assets:
            text = "📋 <b>Пусто</b>"
            keyboard = self._main_menu_kb()
        else:
            dir_text = {"up": "🟢", "down": "🔴", "any": "⚪️"}
            total = sum(len(a.alerts) for a in assets)
            text = f"📋 <b>Алерты</b> ({total} шт.)\n\n"
            keyboard = keyboards.alerts_list_keyboard(assets)

            for asset in assets:
                text += f"🪙 <b>{asset.symbol}</b>\n"
                for alert in asset.alerts:
                    note_escaped = html_escape(alert.setup_note or "")
                    note_display = f" | 📝 <i>{note_escaped}</i>" if note_escaped else ""
                    text += (
                        f"   • {alert.price:,.4f} "
                        f"{dir_text.get(alert.direction, '')}{note_display}\n"
                    )
                text += "\n"

        try:
            if update.callback_query:
                await update.callback_query.edit_message_text(
                    text, parse_mode="HTML", reply_markup=keyboard
                )
            else:
                await update.message.reply_text(
                    text, parse_mode="HTML", reply_markup=keyboard
                )
        except Exception as e:
            if "Message is not modified" not in str(e):
                logger.error(f"Ошибка в _show_list: {e}")

    async def _show_help(self, update: Update):
        """Справка."""
        text = (
            "ℹ️ <b>Справка</b>\n\n"
            "<b>Формат быстрого добавления:</b>\n"
            "<code>TICKER PRICE DIR [NOTE]</code>\n"
            "Пример: <code>BTC 85000 up пробой</code> или <code>BTCUSDT 85000 up пробой</code>\n\n"
            "<b>DIR:</b> up | down | any\n\n"
            "<b>Массовое добавление:</b> отправьте несколько строк:\n"
            "<code>BTC 85000 up пробой\n"
            "ETH 3200 down ретест</code>"
            "\n\n<b>Настройки:</b>\n"
            "⚙️ Кнопка в меню или команда <code>/settings</code> — "
            "изменить порог score для отправки алертов."
            "\n\n<b>История алертов:</b>\n"
            "📜 Выгрузить данные → 📜 История алертов — CSV последних алертов."
        )
        try:
            if update.callback_query:
                await update.callback_query.edit_message_text(
                    text, parse_mode="HTML", reply_markup=self._main_menu_kb()
                )
            else:
                await update.message.reply_text(
                    text, parse_mode="HTML", reply_markup=self._main_menu_kb()
                )
        except Exception as e:
            if "Message is not modified" not in str(e):
                logger.error(f"Ошибка в _show_help: {e}")