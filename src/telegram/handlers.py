"""
Обработчики команд и callback'ов Telegram бота.

Версия 2.1:
- Все методы внутри класса TelegramHandlers (исправлен развал структуры).
- resolve_symbol для интерактивного ввода тикеров (диалог добавления,
  /data, ручная выгрузка).
- Быстрое однострочное и массовое добавление — просто +USDT без REST (не тормозим).
"""

import os
import time
import logging
import csv
from datetime import datetime
from typing import Dict, Any, List, Optional

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from telegram.ext import ContextTypes

from src.core.alerts import AlertsManager
from src.api.bybit_client import BybitClient
from src.telegram import keyboards
from src.utils.config import Config
from src.utils.indicators import calculate_rsi_series

logger = logging.getLogger(__name__)


class TelegramHandlers:
    def __init__(
        self,
        alerts_manager: AlertsManager,
        allowed_chat_id: str,
        bybit_client: BybitClient,
    ):
        self.alerts_manager = alerts_manager
        self.allowed_chat_id = str(allowed_chat_id)
        self.bybit_client = bybit_client
        self.user_state: Dict[int, Dict[str, Any]] = {}
        self._screener_cache: Dict[str, tuple] = {}
        self._screener_cache_ttl = 120
        self._last_screener_results: List = []

    # ==================== ВСПОМОГАТЕЛЬНЫЕ ====================

    def _is_allowed(self, update: Update) -> bool:
        return str(update.effective_chat.id) == self.allowed_chat_id

    def _reset_state(self, chat_id: int) -> None:
        self.user_state.pop(chat_id, None)

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
            reply_markup=keyboards.main_menu_keyboard(),
        )

    async def menu_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update):
            return
        self._reset_state(update.effective_chat.id)
        await update.message.reply_text(
            "🏠 <b>Главное меню</b>",
            parse_mode="HTML",
            reply_markup=keyboards.main_menu_keyboard(),
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
                f"Можно писать сокращённо: <code>eth</code>, <code>ETH</code>, <code>ETHUSDT</code>.",
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

        # --- Массовое добавление (текстом, без диалога) ---
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        if len(lines) > 1:
            await self._process_bulk_add(update, lines)
            return

        # --- Однострочный формат: SYMBOL PRICE DIR [NOTE] ---
        parts = text.split(maxsplit=3)
        if len(parts) >= 3:
            symbol = parts[0].strip().upper()
            if not symbol.endswith("USDT"):
                symbol += "USDT"
            try:
                price = float(parts[1].replace(",", "."))
                direction = parts[2].lower()
                note = parts[3] if len(parts) > 3 else ""
                if direction in ["up", "down", "any"] and len(symbol) >= 4:
                    category = (
                        "spot"
                        if "spot" in note.lower() or "спот" in note.lower()
                        else "linear"
                    )
                    clean_note = note.replace("spot", "").replace("спот", "").strip()
                    success, replaced = self.alerts_manager.add_alert(
                        symbol, price, direction, category, clean_note
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
                            f"🪙 {symbol}\n💰 {price:,.2f}\n"
                            f"🎯 {dir_text[direction]}{note_display}",
                            parse_mode="HTML",
                            reply_markup=keyboards.main_menu_keyboard(),
                        )
                    else:
                        await update.message.reply_text(
                            "⚠️ Уже существует",
                            reply_markup=keyboards.main_menu_keyboard(),
                        )
                    return
            except ValueError:
                pass

        await update.message.reply_text(
            "❓ Не понял команду.",
            parse_mode="HTML",
            reply_markup=keyboards.main_menu_keyboard(),
        )

    async def _handle_export_tickers(self, update: Update, text: str, chat_id: int):
        """Обработка ввода тикеров для экспорта."""
        raw_tickers = text.replace(",", " ").split()
        checked_tickers: List[str] = []
        invalid_tickers: List[str] = []

        for t in raw_tickers:
            t = t.strip()
            if not t:
                continue
            resolved = self.bybit_client.resolve_symbol(t, "linear")
            if resolved:
                if resolved not in checked_tickers:
                    checked_tickers.append(resolved)
            else:
                invalid_tickers.append(t.upper())

        if not checked_tickers:
            invalid_msg = ", ".join(invalid_tickers) if invalid_tickers else "—"
            await update.message.reply_text(
                f"❌ Не найдено ни одного валидного тикера на Bybit:\n"
                f"<code>{invalid_msg}</code>\n\n"
                f"Можно писать как <code>BTC ETH</code>, так и <code>BTCUSDT ETHUSDT</code>.\n"
                f"Проверьте написание (например, <code>XRP</code>, а не <code>XPR</code>).",
                parse_mode="HTML",
                reply_markup=keyboards.main_menu_keyboard(),
            )
            self._reset_state(chat_id)
            return

        await update.message.reply_text(
            f"⏳ Генерирую файл для {len(checked_tickers)} тикеров..."
        )
        filepath = self._generate_export_file(checked_tickers)

        if filepath and os.path.exists(filepath):
            caption = f"✅ Данные выгружены для: {', '.join(checked_tickers)}"
            if invalid_tickers:
                caption += (
                    f"\n\n⚠️ Пропущены (не найдены на Bybit): "
                    f"{', '.join(invalid_tickers)}"
                )
            with open(filepath, "rb") as f:
                await update.message.reply_document(
                    document=InputFile(f, filename=os.path.basename(filepath)),
                    caption=caption,
                )
            os.remove(filepath)
        else:
            await update.message.reply_text("❌ Ошибка при генерации файла.")

        self._reset_state(chat_id)

    async def _finalize_add_alert(
        self, update: Update, state: Dict[str, Any], note: str, chat_id: int
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
                reply_markup=keyboards.main_menu_keyboard(),
            )
        else:
            await update.message.reply_text(
                "⚠️ Не удалось сохранить алерт",
                reply_markup=keyboards.main_menu_keyboard(),
            )

        self._reset_state(chat_id)

    async def _process_bulk_add(self, update: Update, lines: List[str]):
        success_count = replaced_count = fail_count = 0
        failed_details = []

        for line in lines:
            if line.startswith("#") or line.startswith("//"):
                continue
            parts = line.split(maxsplit=3)
            if len(parts) >= 3:
                symbol = parts[0].strip().upper()
                if not symbol.endswith("USDT"):
                    symbol += "USDT"
                try:
                    price = float(parts[1].replace(",", "."))
                    direction = parts[2].lower()
                    note = parts[3] if len(parts) > 3 else ""
                    category = (
                        "spot"
                        if "spot" in note.lower() or "спот" in note.lower()
                        else "linear"
                    )
                    clean_note = note.replace("spot", "").replace("спот", "").strip()
                    if direction in ["up", "down", "any"] and len(symbol) >= 4:
                        success, replaced = self.alerts_manager.add_alert(
                            symbol, price, direction, category, clean_note
                        )
                        if success:
                            if replaced:
                                replaced_count += 1
                            else:
                                success_count += 1
                        else:
                            fail_count += 1
                            failed_details.append(f"• {line} (ошибка)")
                    else:
                        fail_count += 1
                        failed_details.append(f"• {line} (ошибка направления)")
                except ValueError:
                    fail_count += 1
                    failed_details.append(f"• {line} (ошибка цены)")
            else:
                fail_count += 1
                failed_details.append(f"• {line} (формат)")

        report = f"📊 <b>Результат:</b>\n"
        report += f"✅ Добавлено: <b>{success_count}</b>\n"
        if replaced_count:
            report += f"🔄 Обновлено: <b>{replaced_count}</b>\n"
        if fail_count > 0:
            report += f"❌ Ошибок: <b>{fail_count}</b>\n" + "\n".join(failed_details[:5])
        else:
            report += "\n🎉 Всё обработано!"

        await update.message.reply_text(
            report, parse_mode="HTML", reply_markup=keyboards.main_menu_keyboard()
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
            await query.edit_message_text(
                "🏠 <b>Главное меню</b>",
                parse_mode="HTML",
                reply_markup=keyboards.main_menu_keyboard(),
            )

        elif data == "menu_add":
            self.user_state[chat_id] = {"step": "waiting_symbol"}
            await query.edit_message_text(
                "➕ <b>Добавить алерт</b>\n\nВведите тикер (например, <code>BTCUSDT</code> или <code>BTC</code>):",
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
                    reply_markup=keyboards.main_menu_keyboard(),
                )
                return
            symbols = list({a.symbol for a in assets})
            await query.edit_message_text(f"⏳ Выгружаю данные для {len(symbols)} активов...")
            filepath = self._generate_export_file(symbols)
            if filepath and os.path.exists(filepath):
                with open(filepath, "rb") as f:
                    await update.callback_query.message.reply_document(
                        document=InputFile(f, filename=os.path.basename(filepath)),
                        caption=f"✅ Данные выгружены\nАктивов: {len(symbols)}",
                    )
                os.remove(filepath)
                await query.edit_message_text(
                    "✅ Файл отправлен!", reply_markup=keyboards.main_menu_keyboard()
                )
            else:
                await query.edit_message_text(
                    "❌ Ошибка экспорта", reply_markup=keyboards.main_menu_keyboard()
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
                    reply_markup=keyboards.main_menu_keyboard(),
                )
                return
            symbols = [asset.symbol for asset in self._last_screener_results]
            await query.edit_message_text(
                f"⏳ Выгружаю данные для {len(symbols)} активов из скринера..."
            )
            filepath = self._generate_export_file(symbols)
            if filepath and os.path.exists(filepath):
                with open(filepath, "rb") as f:
                    await update.callback_query.message.reply_document(
                        document=InputFile(f, filename=os.path.basename(filepath)),
                        caption=f"✅ Данные скринера выгружены\nАктивов: {len(symbols)}",
                    )
                os.remove(filepath)
                await query.edit_message_text(
                    "✅ Файл отправлен!", reply_markup=keyboards.main_menu_keyboard()
                )
            else:
                await query.edit_message_text(
                    "❌ Ошибка экспорта", reply_markup=keyboards.main_menu_keyboard()
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
                    reply_markup=keyboards.main_menu_keyboard(),
                )
            else:
                await query.edit_message_text(
                    f"⚠️ Алертов для <b>{symbol}</b> не найдено",
                    parse_mode="HTML",
                    reply_markup=keyboards.main_menu_keyboard(),
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
                reply_markup=keyboards.main_menu_keyboard(),
            )
        else:
            await query.answer("Алерт не найден", show_alert=True)

    async def _handle_export_alert(self, update: Update, data: str):
        """
        Экспорт данных по одному активу.

        ВАЖНО: исходное сообщение алерта НЕ редактируется. Статус и файл
        отправляются новыми сообщениями, чтобы алерт остался в чате.
        """
        query = update.callback_query
        symbol = data.replace("export_alert_", "").upper()

        # Статус — отдельным сообщением (не редактируем алерт).
        try:
            status_msg = await query.message.reply_text(
                f"⏳ Выгружаю данные <b>{symbol}</b>...", parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Не удалось отправить статусное сообщение: {e}")
            status_msg = None

        filepath = self._generate_export_file([symbol])

        if filepath and os.path.exists(filepath):
            try:
                with open(filepath, "rb") as f:
                    await query.message.reply_document(
                        document=InputFile(f, filename=os.path.basename(filepath)),
                        caption=f"✅ Данные по <b>{symbol}</b>\n15m / 1H / 4H + RSI",
                    )
                if status_msg is not None:
                    try:
                        await status_msg.edit_text(
                            f"✅ Файл по <b>{symbol}</b> отправлен",
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass
            except Exception as e:
                logger.error(f"Ошибка отправки файла {symbol}: {e}", exc_info=True)
                if status_msg is not None:
                    try:
                        await status_msg.edit_text(
                            f"❌ Не удалось отправить файл по {symbol}"
                        )
                    except Exception:
                        pass
            finally:
                try:
                    os.remove(filepath)
                except Exception:
                    pass
        else:
            if status_msg is not None:
                try:
                    await status_msg.edit_text(
                        f"❌ Не удалось получить данные по <b>{symbol}</b>",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
            else:
                await query.message.reply_text(
                    f"❌ Не удалось получить данные по <b>{symbol}</b>",
                    parse_mode="HTML",
                )

    # ==================== ЭКРАНЫ ====================

    async def _handle_current_prices(self, update: Update):
        """Показывает текущие цены по всем отслеживаемым активам."""
        query = update.callback_query

        try:
            await query.edit_message_text("⏳ Загружаю цены...")
        except Exception:
            pass

        assets = self.alerts_manager.get_all_alerts()
        if not assets:
            await query.edit_message_text(
                "📋 Пусто.", reply_markup=keyboards.main_menu_keyboard()
            )
            return

        text = "💰 <b>Текущие цены:</b>\n\n"
        symbols = list({a.symbol for a in assets})

        for symbol in symbols:
            try:
                category = next(
                    (a.category for a in assets if a.symbol == symbol), "linear"
                )
                ticker = self.bybit_client.get_ticker(symbol, category)
                if ticker:
                    price_str = (
                        f"{ticker.price:,.4f}"
                        if ticker.price < 1000
                        else f"{ticker.price:,.2f}"
                    )
                    text += f"🪙 <b>{symbol}</b>: <code>{price_str}</code> $\n"
                else:
                    text += f"🪙 <b>{symbol}</b>: ❌ нет данных\n"
            except Exception as e:
                logger.error(f"Ошибка получения цены {symbol}: {e}")
                text += f"🪙 <b>{symbol}</b>: ⚠️ ошибка\n"

        try:
            await query.edit_message_text(
                text,
                parse_mode="HTML",
                reply_markup=keyboards.main_menu_keyboard(),
            )
        except Exception as e:
            if "Message is not modified" not in str(e):
                logger.error(f"Ошибка в _handle_current_prices: {e}", exc_info=True)

    async def _show_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Список всех алертов."""
        assets = self.alerts_manager.get_all_alerts()

        if not assets:
            text = "📋 <b>Пусто</b>"
            keyboard = keyboards.main_menu_keyboard()
        else:
            dir_text = {"up": "🟢", "down": "🔴", "any": "⚪️"}
            total = sum(len(a.alerts) for a in assets)
            text = f"📋 <b>Алерты</b> ({total} шт.)\n\n"
            keyboard = keyboards.alerts_list_keyboard(assets)

            for asset in assets:
                text += f"🪙 <b>{asset.symbol}</b>\n"
                for alert in asset.alerts:
                    note_escaped = (
                        alert.setup_note.replace("<", "&lt;").replace(">", "&gt;")
                        if alert.setup_note
                        else ""
                    )
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
        )
        try:
            if update.callback_query:
                await update.callback_query.edit_message_text(
                    text, parse_mode="HTML", reply_markup=keyboards.main_menu_keyboard()
                )
            else:
                await update.message.reply_text(
                    text, parse_mode="HTML", reply_markup=keyboards.main_menu_keyboard()
                )
        except Exception as e:
            if "Message is not modified" not in str(e):
                logger.error(f"Ошибка в _show_help: {e}")

    async def _run_screener_simple(self, update: Update, chat_id: int):
        """Скринер топ-активов по движению."""
        query = update.callback_query
        try:
            await query.edit_message_text("⏳ <b>Анализирую рынок...</b>", parse_mode="HTML")
        except Exception:
            pass

        cache_key = "screener_simple"
        cached = self._screener_cache.get(cache_key)

        if cached and (time.time() - cached[0]) < self._screener_cache_ttl:
            fetch_time, assets = cached
            from_cache = True
        else:
            assets = self.bybit_client.get_screener_data(
                category="linear",
                sort_by="volume_desc",
                min_volume_usd=1_000_000,
                min_change_abs=3.0,
                limit=20,
            )
            if assets:
                assets.sort(key=lambda x: abs(x.price_change_24h), reverse=True)
            fetch_time = time.time()
            self._screener_cache[cache_key] = (fetch_time, assets)
            from_cache = False

        if not assets:
            await query.edit_message_text(
                "❌ <b>Ничего не найдено</b>\n\nПопробуйте позже или уменьшите фильтры.",
                parse_mode="HTML",
                reply_markup=keyboards.main_menu_keyboard(),
            )
            return

        self._last_screener_results = assets
        text = self._format_screener_simple(assets, fetch_time, from_cache)
        top_symbol = assets[0].symbol
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=keyboards.screener_add_alert_keyboard(top_symbol),
        )

    def _format_screener_simple(
        self, assets: List, fetch_time: float, from_cache: bool
    ) -> str:
        text = "🔍 <b>ТОП ПО ДВИЖЕНИЮ ЦЕНЫ</b> (24ч)\n"
        text += "<i>Отсортировано по абсолютному изменению</i>\n"
        text += "<i>Фильтры: >= 3% движения, >= 1M$ объема</i>\n\n"
        text += "━" * 30 + "\n\n"

        for i, asset in enumerate(assets[:15], 1):
            if asset.price_change_24h > 5:
                change_emoji = "🚀"
            elif asset.price_change_24h < -5:
                change_emoji = "💥"
            elif asset.price_change_24h > 0:
                change_emoji = "🟢"
            else:
                change_emoji = "🔴"

            vol_str = f"${asset.volume_24h / 1_000_000:.2f}M"
            if asset.price >= 100:
                price_str = f"{asset.price:,.2f}"
            elif asset.price >= 1:
                price_str = f"{asset.price:,.4f}"
            else:
                price_str = f"{asset.price:,.6f}"

            text += f"<b>{i:2d}.</b> <code>{asset.symbol}</code>\n"
            text += f"    💰 <b>{price_str}</b> $ | {change_emoji} <b>{asset.price_change_24h:+.2f}%</b>\n"
            text += f"    📊 Объем: <b>{vol_str}</b>\n\n"

        text += "━" * 30 + "\n"
        cache_mark = "📥 Из кэша" if from_cache else "🔄 Свежие данные"
        text += f"<i>{cache_mark} ({int(time.time() - fetch_time)}с назад)</i>"
        return text

    # ==================== ЭКСПОРТ В CSV ====================

    def _generate_export_file(self, tickers: List[str]) -> Optional[str]:
        """
        Генерирует CSV с 15m/1H/4H свечами и RSI.
        Возвращает путь к файлу или None.
        """
        try:
            Config.EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
            filename = f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            filepath = Config.EXPORTS_DIR / filename

            with open(filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(
                    ["Ticker", "Timeframe", "Time", "Open", "High", "Low", "Close", "Volume", "RSI"]
                )

                for ticker in tickers:
                    for tf, tf_name in [("15", "15m"), ("60", "1H"), ("240", "4H")]:
                        try:
                            limit = Config.EXPORT_LIMITS.get(tf, 150)
                            klines = self.bybit_client.get_klines(ticker, "linear", tf, limit)
                            if not klines:
                                continue

                            closes = [float(k[4]) for k in klines]
                            rsi_values = calculate_rsi_series(closes, period=14)

                            for i, k in enumerate(reversed(klines)):
                                timestamp_ms = int(k[0])
                                dt = datetime.fromtimestamp(timestamp_ms / 1000)
                                time_str = dt.strftime("%Y-%m-%d %H:%M")
                                rsi_idx = len(klines) - 1 - i
                                rsi = (
                                    rsi_values[rsi_idx]
                                    if rsi_idx < len(rsi_values)
                                    else None
                                )
                                rsi_str = f"{rsi:.1f}" if rsi is not None else "N/A"

                                writer.writerow([
                                    ticker,
                                    tf_name,
                                    time_str,
                                    float(k[1]),
                                    float(k[2]),
                                    float(k[3]),
                                    float(k[4]),
                                    float(k[5]),
                                    rsi_str,
                                ])
                        except Exception as e:
                            logger.error(f"Ошибка данных для {ticker} {tf}: {e}")
                            continue

            logger.info(f"✅ Файл экспорта создан: {filepath}")
            return str(filepath)

        except Exception as e:
            logger.error(f"Ошибка генерации файла экспорта: {e}", exc_info=True)
            return None