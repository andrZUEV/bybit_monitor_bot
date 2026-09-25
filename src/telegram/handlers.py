"""
Обработчики команд и callback'ов Telegram бота
"""

import os
import time
import logging
import csv
from datetime import datetime
from typing import Dict, Any, List, Optional
from pathlib import Path

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from telegram.ext import ContextTypes

from src.core.alerts import AlertsManager
from src.api.bybit_client import BybitClient
from src.telegram import keyboards
from src.utils.data_exporter import DataExporter

logger = logging.getLogger(__name__)


def calculate_rsi(closes: List[float], period: int = 14) -> List[Optional[float]]:
    """
    Расчёт RSI(14) по формуле Wilder
    Возвращает список значений RSI (None для первых period свечей)
    """
    if len(closes) < period + 1:
        return [None] * len(closes)
    
    # Bybit отдаёт свечи от новых к старым, разворачиваем
    closes_rev = list(reversed(closes))
    changes = [closes_rev[i] - closes_rev[i - 1] for i in range(1, len(closes_rev))]
    gains = [max(c, 0) for c in changes]
    losses = [max(-c, 0) for c in changes]
    
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    rsi_values_rev = [None] * period
    
    if avg_loss == 0:
        rsi_values_rev.append(100.0)
    else:
        rs = avg_gain / avg_loss
        rsi_values_rev.append(100 - (100 / (1 + rs)))
    
    for i in range(period, len(changes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        
        if avg_loss == 0:
            rsi_values_rev.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_values_rev.append(100 - (100 / (1 + rs)))
    
    # Разворачиваем обратно (чтобы порядок совпадал с исходными klines: от новых к старым)
    return list(reversed(rsi_values_rev))


class TelegramHandlers:
    def __init__(self, alerts_manager: AlertsManager, allowed_chat_id: str, bybit_client: BybitClient):
        self.alerts_manager = alerts_manager
        self.allowed_chat_id = str(allowed_chat_id)
        self.bybit_client = bybit_client
        self.data_exporter = DataExporter(bybit_client) 
        self.user_state: Dict[int, Dict[str, Any]] = {}
        
        self._screener_cache: Dict[str, tuple] = {}
        self._screener_cache_ttl = 120
        self._last_screener_query: Dict[int, Dict[str, Any]] = {}
        self._last_screener_results: List = [] # Для хранения результатов скринера
    
    def _is_allowed(self, update: Update) -> bool:
        return str(update.effective_chat.id) == self.allowed_chat_id
    
    def _reset_state(self, chat_id: int):
        self.user_state.pop(chat_id, None)

    # ==================== КОМАНДЫ ====================
    async def start_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update): return
        self._reset_state(update.effective_chat.id)
        total_alerts = sum(len(a.alerts) for a in self.alerts_manager.get_all_alerts())
        await update.message.reply_text(
            "👋 <b>Bybit Monitor Bot</b>\n\n"
            "🔍 <b>Скринер:</b> ищите монеты с сильным движением!\n"
            "⚡ <b>Алерты:</b> получайте уведомления о пробоях.\n\n"
            f"📊 <b>Статус:</b>\n"
            f"• Отслеживается алертов: <b>{total_alerts}</b>\n"
            f"• Интервал опроса: <b>4 сек</b>\n"
            f"• Кулдаун: <b>25 мин</b>\n\n"
            "Выберите действие:",
            parse_mode='HTML',
            reply_markup=keyboards.main_menu_keyboard()
        )

    async def menu_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update): return
        self._reset_state(update.effective_chat.id)
        await update.message.reply_text("🏠 <b>Главное меню</b>", parse_mode='HTML', reply_markup=keyboards.main_menu_keyboard())

    async def list_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update): return
        await self._show_list(update, context)

    async def help_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update): return
        await self._show_help(update)

    async def data_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /data SYMBOL - выгрузка свечных данных"""
        if not self._is_allowed(update): return
        
        if not context.args or len(context.args) == 0:
            await update.message.reply_text(
                "❌ Укажите символ\n\n"
                "Пример: <code>/data BTCUSDT</code>",
                parse_mode='HTML'
            )
            return
        
        symbol = context.args[0].upper()
        
        if not self.data_exporter.can_export(symbol):
            await update.message.reply_text(
                f"⏳ Подождите {self.data_exporter._export_cooldown} секунд перед следующим запросом"
            )
            return
        
        await update.message.reply_text(f"⏳ Выгружаю данные {symbol}...")
        filepath = self.data_exporter.export_symbol_data(symbol, "linear")
        
        if filepath and filepath.exists():
            filename = filepath.name
            with open(filepath, 'rb') as f:
                await update.message.reply_document(
                    document=InputFile(f, filename=filename),
                    caption=f"✅ {symbol} данные выгружены\n15m: 150 свечей\n1H: 100 свечей\n4H: 70 свечей"
                )
            filepath.unlink()  # Удаляем временный файл
        else:
            await update.message.reply_text("❌ Не удалось получить данные. Попробуйте позже.")

    # ==================== ТЕКСТОВЫЕ СООБЩЕНИЯ ====================
    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update): return
        
        chat_id = update.effective_chat.id
        text = update.message.text.strip()
        state = self.user_state.get(chat_id, {})
        
        # ==================== ДОБАВИТЬ ЭТОТ БЛОК ====================
        if state.get('step') == 'waiting_for_export_tickers':
            tickers_input = text.upper().split()
            valid_tickers = [t for t in tickers_input if t.endswith('USDT') and len(t) >= 6]
            
            if not valid_tickers:
                await update.message.reply_text(
                    "❌ Неверный формат. Введите тикеры, заканчивающиеся на USDT, через пробел.\n"
                    "Например: <code>BTCUSDT ETHUSDT</code>",
                    parse_mode='HTML',
                    reply_markup=keyboards.cancel_keyboard()
                )
                return
            
            await update.message.reply_text(f"⏳ Генерирую файл для {len(valid_tickers)} тикеров...")
            filepath = self._generate_export_file(valid_tickers)
            
            if filepath and os.path.exists(filepath):
                with open(filepath, 'rb') as f:
                    await update.message.reply_document(
                        document=InputFile(f, filename=os.path.basename(filepath)),
                        caption=f"✅ Данные выгружены для: {', '.join(valid_tickers)}"
                    )
                os.remove(filepath)
            else:
                await update.message.reply_text("❌ Ошибка при генерации файла.")
            
            self._reset_state(chat_id)
            return
        # ==================== КОНЕЦ БЛОКА ====================
        
        if state.get('step') == 'waiting_symbol':
            symbol = text.upper().replace(' ', '')
            if len(symbol) < 4 or not symbol.isalpha():
                await update.message.reply_text("❌ Некорректный тикер.", parse_mode='HTML', reply_markup=keyboards.cancel_keyboard())
                return
            self.user_state[chat_id] = {'step': 'waiting_price', 'symbol': symbol}
            await update.message.reply_text(f"✅ Тикер: <b>{symbol}</b>\nВведите цену:", parse_mode='HTML', reply_markup=keyboards.cancel_keyboard())
            return
            
        if state.get('step') == 'waiting_price':
            try:
                price = float(text.replace(',', '.'))
                if price <= 0: raise ValueError
            except ValueError:
                await update.message.reply_text("❌ Некорректная цена.", reply_markup=keyboards.cancel_keyboard())
                return
            self.user_state[chat_id] = {'step': 'waiting_direction', 'symbol': state['symbol'], 'price': price}
            await update.message.reply_text(f"✅ Цена: <code>{price:,.2f}</code>\nВыберите направление:", parse_mode='HTML', reply_markup=keyboards.direction_keyboard())
            return

        lines = [line.strip() for line in text.split('\n') if line.strip()]
        if len(lines) > 1:
            await self._process_bulk_add(update, lines)
            return
            
        parts = text.split(maxsplit=3)
        if len(parts) >= 3:
            symbol = parts[0].upper()
            try:
                price = float(parts[1].replace(',', '.'))
                direction = parts[2].lower()
                note = parts[3] if len(parts) > 3 else "" 
                
                if direction in ['up', 'down', 'any'] and len(symbol) >= 4:
                    category = 'spot' if 'spot' in note.lower() or 'спот' in note.lower() else 'linear'
                    clean_note = note.replace('spot', '').replace('спот', '').strip()
                    
                    # Безопасная распаковка результата (поддерживает и bool, и tuple)
                    add_result = self.alerts_manager.add_alert(symbol, price, direction, category, clean_note)
                    success = add_result[0] if isinstance(add_result, tuple) else add_result
                    replaced = add_result[1] if isinstance(add_result, tuple) else False
                    
                    dir_text = {"up": "снизу вверх 🟢", "down": "сверху вниз 🔴", "any": "любое ⚪️"}
                    note_display = f"\n📝 Сетап: <code>{clean_note}</code>" if clean_note else ""
                    
                    if success:
                        action_text = "🔄 <b>Заменено:</b>" if replaced else "✅ <b>Добавлено:</b>"
                        await update.message.reply_text(f"{action_text}\n🪙 {symbol}\n💰 {price:,.2f}\n🎯 {dir_text[direction]}{note_display}", parse_mode='HTML', reply_markup=keyboards.main_menu_keyboard())
                    else:
                        await update.message.reply_text("⚠️ Ошибка добавления", reply_markup=keyboards.main_menu_keyboard())
                    return
            except ValueError:
                pass
        
        await update.message.reply_text("❓ Не понял команду.", parse_mode='HTML', reply_markup=keyboards.main_menu_keyboard())

    async def _process_bulk_add(self, update: Update, lines: list[str]):
        success_count = fail_count = replaced_count = 0
        failed_details = []
        for line in lines:
            if line.startswith('#') or line.startswith('//'): continue
            parts = line.split(maxsplit=3)
            if len(parts) >= 3:
                symbol = parts[0].upper()
                try:
                    price = float(parts[1].replace(',', '.'))
                    direction = parts[2].lower()
                    note = parts[3] if len(parts) > 3 else ""
                    category = 'spot' if 'spot' in note.lower() or 'спот' in note.lower() else 'linear'
                    clean_note = note.replace('spot', '').replace('спот', '').strip()
                    if direction in ['up', 'down', 'any'] and len(symbol) >= 4:
                        add_result = self.alerts_manager.add_alert(symbol, price, direction, category, clean_note)
                        success = add_result[0] if isinstance(add_result, tuple) else add_result
                        replaced = add_result[1] if isinstance(add_result, tuple) else False
                        
                        if success:
                            success_count += 1
                            if replaced:
                                replaced_count += 1
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
        
        report = f"📊 <b>Результат:</b>\n✅ Успешно: <b>{success_count}</b>\n"
        if replaced_count > 0:
            report += f"🔄 Заменено: <b>{replaced_count}</b>\n"
        if fail_count > 0:
            report += f"❌ Ошибок: <b>{fail_count}</b>\n" + "\n".join(failed_details[:5])
        else:
            report += "\n🎉 Все добавлены!"
        await update.message.reply_text(report, parse_mode='HTML', reply_markup=keyboards.main_menu_keyboard())

    # ==================== CALLBACK'И ====================
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        if not self._is_allowed(update):
            await query.edit_message_text("⛔️ Доступ запрещён")
            return
        
        chat_id = query.from_user.id
        data = query.data
        
        if data == "menu_main":
            self._reset_state(chat_id)
            await query.edit_message_text("🏠 <b>Главное меню</b>", parse_mode='HTML', reply_markup=keyboards.main_menu_keyboard())
            
        elif data == "menu_add":
            self.user_state[chat_id] = {'step': 'waiting_symbol'}
            await query.edit_message_text("➕ <b>Алерт</b>\nВведите тикер:", parse_mode='HTML', reply_markup=keyboards.cancel_keyboard())
            
        elif data == "menu_screener":
            await self._run_screener_simple(update, chat_id)
            
        elif data == "scr_refresh":
            self._screener_cache.clear()
            await self._run_screener_simple(update, chat_id)

        # ==================== ДОБАВИТЬ ЭТОТ БЛОК ====================
        elif data == "menu_export":
            await query.edit_message_text(
                "📊 <b>Выгрузка данных</b>\n\nВыберите способ выгрузки:",
                parse_mode='HTML',
                reply_markup=keyboards.export_options_keyboard()
            )
            
        elif data == "export_tracked":
            assets = self.alerts_manager.get_all_alerts()
            if not assets:
                await query.edit_message_text(
                    "📋 Нет отслеживаемых активов. Добавьте алерты сначала.",
                    reply_markup=keyboards.main_menu_keyboard()
                )
                return
            
            symbols = list(set(a.symbol for a in assets))
            await query.edit_message_text(f"⏳ Выгружаю данные для {len(symbols)} активов...")
            
            filepath = self._generate_export_file(symbols)
            
            if filepath and os.path.exists(filepath):
                with open(filepath, 'rb') as f:
                    await update.callback_query.message.reply_document(
                        document=InputFile(f, filename=os.path.basename(filepath)),
                        caption=f"✅ Данные выгружены\nАктивов: {len(symbols)}\n📋 {', '.join(symbols)}"
                    )
                os.remove(filepath)
                await query.edit_message_text("✅ Файл отправлен!", reply_markup=keyboards.main_menu_keyboard())
            else:
                await query.edit_message_text("❌ Ошибка экспорта", reply_markup=keyboards.main_menu_keyboard())
                
        elif data == "export_manual":
            self.user_state[chat_id] = {'step': 'waiting_for_export_tickers'}
            await query.edit_message_text(
                "✏️ <b>Ручная выгрузка</b>\n\n"
                "Введите тикеры через пробел:\n"
                "Например: <code>BTCUSDT ETHUSDT</code>",
                parse_mode='HTML',
                reply_markup=keyboards.cancel_keyboard()
            )
        # ==================== КОНЕЦ БЛОКА ====================

        elif data == "export_screener":
            if not self._last_screener_results:
                await query.edit_message_text(
                    "⚠️ Сначала запустите скринер, чтобы были данные для выгрузки",
                    reply_markup=keyboards.main_menu_keyboard()
                )
                return
            
            symbols = [asset.symbol for asset in self._last_screener_results]
            await query.edit_message_text(f"⏳ Выгружаю данные для {len(symbols)} активов из скринера...")
            
            filepath = self._generate_export_file(symbols)
            
            if filepath and os.path.exists(filepath):
                filename = os.path.basename(filepath)
                with open(filepath, 'rb') as f:
                    await update.callback_query.message.reply_document(
                        document=InputFile(f, filename=filename),
                        caption=f"✅ Данные скринера выгружены\nАктивов: {len(symbols)}\n📋 {', '.join(symbols[:10])}{'...' if len(symbols) > 10 else ''}"
                    )
                os.remove(filepath)
                await query.edit_message_text("✅ Файл отправлен!", reply_markup=keyboards.main_menu_keyboard())
            else:
                await query.edit_message_text("❌ Ошибка экспорта", reply_markup=keyboards.main_menu_keyboard())
                
        elif data.startswith("scr_add_"):
            symbol = data.replace("scr_add_", "")
            self.user_state[chat_id] = {'step': 'waiting_price', 'symbol': symbol}
            await query.edit_message_text(f"➕ <b>Алерт на {symbol}</b>\nВведите цену:", parse_mode='HTML', reply_markup=keyboards.cancel_keyboard())
            
        elif data == "menu_prices":
            await self._handle_current_prices(update)
            
        elif data.startswith("dir_"):
            state = self.user_state.get(chat_id, {})
            if state.get('step') != 'waiting_direction':
                await query.edit_message_text("⚠️ Сессия устарела", reply_markup=keyboards.main_menu_keyboard())
                return
            direction = data.replace("dir_", "")
            symbol = state['symbol']
            price = state['price']
            
            add_result = self.alerts_manager.add_alert(symbol, price, direction, "linear", "")
            success = add_result[0] if isinstance(add_result, tuple) else add_result
            replaced = add_result[1] if isinstance(add_result, tuple) else False
            
            self._reset_state(chat_id)
            
            dir_text = {"up": "снизу вверх 🟢", "down": "сверху вниз 🔴", "any": "любое ⚪️"}
            action_text = "🔄 <b>Заменено:</b>" if replaced else "✅ <b>Добавлено:</b>"
            await query.edit_message_text(f"{action_text}\n🪙 {symbol}\n💰 {price:,.2f}\n🎯 {dir_text[direction]}", parse_mode='HTML', reply_markup=keyboards.main_menu_keyboard())
            
        elif data in ["menu_list", "menu_remove"]:
            await self._show_list(update, context)
            
        elif data.startswith("del_all_"):
            symbol = data.replace("del_all_", "")
            if self.alerts_manager.remove_all_alerts_for_symbol(symbol):
                await query.edit_message_text(f"🗑 <b>Удалено: {symbol}</b>", parse_mode='HTML', reply_markup=keyboards.main_menu_keyboard())
            else:
                await query.edit_message_text("❌ Нечего удалять", reply_markup=keyboards.main_menu_keyboard())
                
        elif data.startswith("del|"):
            parts = data.split("|")
            if len(parts) == 4:
                _, symbol, price_str, direction = parts
                try:
                    if self.alerts_manager.remove_alert(symbol, float(price_str), direction):
                        await query.edit_message_text(f"🗑 Удалено: <b>{symbol}</b>", parse_mode='HTML', reply_markup=keyboards.main_menu_keyboard())
                    else:
                        await query.edit_message_text("❌ Не найдено", reply_markup=keyboards.main_menu_keyboard())
                except ValueError:
                    await query.edit_message_text("❌ Ошибка", reply_markup=keyboards.main_menu_keyboard())
                    
        elif data == "menu_help":
            await self._show_help(update)
            
        elif data == "noop":
            await query.answer()

    # ==================== СКРИНЕР ====================
    async def _run_screener_simple(self, update: Update, chat_id: int):
        query = update.callback_query
        try:
            await query.edit_message_text("⏳ <b>Анализирую рынок...</b>", parse_mode='HTML')
        except Exception:
            pass
        
        cache_key = "screener_simple"
        cached = self._screener_cache.get(cache_key)
        
        if cached and (time.time() - cached[0]) < self._screener_cache_ttl:
            fetch_time, assets = cached
            from_cache = True
        else:
            assets = self.bybit_client.get_screener_data(
                category="linear", sort_by="volume_desc", min_volume_usd=1_000_000, min_change_abs=3.0, limit=20
            )
            if assets:
                assets.sort(key=lambda x: abs(x.price_change_24h), reverse=True)
            
            fetch_time = time.time()
            self._screener_cache[cache_key] = (fetch_time, assets)
            from_cache = False
        
        if not assets:
            await query.edit_message_text("❌ <b>Ничего не найдено</b>\n\nПопробуйте позже или уменьшите фильтры.", parse_mode='HTML', reply_markup=keyboards.main_menu_keyboard())
            return

        self._last_screener_results = assets
        
        text = self._format_screener_simple(assets, fetch_time, from_cache)
        top_symbol = assets[0].symbol
        
        await query.edit_message_text(text, parse_mode='HTML', reply_markup=keyboards.screener_add_alert_keyboard(top_symbol))
    
    def _format_screener_simple(self, assets: List, fetch_time: float, from_cache: bool) -> str:
        text = "🔍 <b>ТОП ПО ДВИЖЕНИЮ ЦЕНЫ</b> (24ч)\n"
        text += "<i>Отсортировано по абсолютному изменению (не важно + или -)</i>\n"
        text += "<i>Фильтры: >= 3% движения, >= 1M$ объема</i>\n\n"
        text += "━" * 30 + "\n\n"
        
        for i, asset in enumerate(assets[:15], 1):
            change_emoji = "🚀" if asset.price_change_24h > 5 else "💥" if asset.price_change_24h < -5 else "🟢" if asset.price_change_24h > 0 else "🔴"
            vol_str = f"${asset.volume_24h/1_000_000:.2f}M"
            price_str = f"{asset.price:,.2f}" if asset.price >= 100 else f"{asset.price:,.4f}" if asset.price >= 1 else f"{asset.price:,.6f}"
            text += f"<b>{i:2d}.</b> <code>{asset.symbol}</code>\n"
            text += f"    💰 <b>{price_str}</b> $ | {change_emoji} <b>{asset.price_change_24h:+.2f}%</b>\n"
            text += f"    📊 Объем: <b>{vol_str}</b>\n\n"
        
        text += "━" * 30 + "\n"
        text += f"<i>{'📥 Из кэша' if from_cache else '🔄 Свежие данные'} ({int(time.time() - fetch_time)}с назад)</i>"
        return text

    async def _handle_current_prices(self, update: Update):
        query = update.callback_query
        await query.answer()
        try:
            await query.edit_message_text("⏳ Загружаю цены...")
        except Exception:
            pass
            
        assets = self.alerts_manager.get_all_alerts()
        if not assets:
            await query.edit_message_text("📋 Пусто.", reply_markup=keyboards.main_menu_keyboard())
            return
        
        text = "💰 <b>Текущие цены:</b>\n\n"
        for symbol in list(set(a.symbol for a in assets)):
            category = next((a.category for a in assets if a.symbol == symbol), "linear")
            ticker = self.bybit_client.get_ticker(symbol, category)
            text += f"🪙 <b>{symbol}</b>: <code>{ticker.price:,.4f}</code> $\n" if ticker else f"🪙 <b>{symbol}</b>: ❌\n"
        
        try:
            await query.edit_message_text(text, parse_mode='HTML', reply_markup=keyboards.main_menu_keyboard())
        except Exception as e:
            if "Message is not modified" not in str(e):
                logger.error(f"Ошибка в _handle_current_prices: {e}")

    async def _show_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        assets = self.alerts_manager.get_all_alerts()
        if not assets:
            text, keyboard = "📋 <b>Пусто</b>", keyboards.main_menu_keyboard()
        else:
            dir_text = {"up": "🟢", "down": "🔴", "any": "⚪️"}
            text = f"📋 <b>Алерты</b> ({sum(len(a.alerts) for a in assets)} шт.)\n\n"
            keyboard = keyboards.alerts_list_keyboard(assets)
            for asset in assets:
                text += f"🪙 <b>{asset.symbol}</b>\n"
                for alert in asset.alerts:
                    note = f" | 📝 <i>{alert.setup_note}</i>" if alert.setup_note else ""
                    text += f"   • {alert.price:,.4f} {dir_text.get(alert.direction, '')}{note}\n"
                text += "\n"
                
        try:
            if update.callback_query:
                await update.callback_query.edit_message_text(text, parse_mode='HTML', reply_markup=keyboard)
            else:
                await update.message.reply_text(text, parse_mode='HTML', reply_markup=keyboard)
        except Exception as e:
            if "Message is not modified" not in str(e):
                logger.error(f"Ошибка в _show_list: {e}")
            if update.callback_query:
                await update.callback_query.answer()

    async def _show_help(self, update: Update):
        text = (
            "ℹ️ <b>Справка</b>\n\n"
            "🔍 <b>Скринер:</b> показывает топ монет по движению цены за 24ч\n"
            "⚡ <b>Алерт:</b> <code>TICKER PRICE DIR [NOTE]</code>\n"
            "Пример: <code>BTCUSDT 85000 up пробой</code>"
        )
        try:
            if update.callback_query:
                await update.callback_query.edit_message_text(text, parse_mode='HTML', reply_markup=keyboards.main_menu_keyboard())
            else:
                await update.message.reply_text(text, parse_mode='HTML', reply_markup=keyboards.main_menu_keyboard())
        except Exception as e:
            if "Message is not modified" not in str(e):
                logger.error(f"Ошибка в _show_help: {e}")
            if update.callback_query:
                await update.callback_query.answer()

    def _generate_export_file(self, tickers: List[str]) -> Optional[str]:
        """
        Генерирует CSV файл с данными свечей для списка тикеров
        """
        try:
            data_dir = Path(__file__).parent.parent.parent / "data" / "exports"
            data_dir.mkdir(parents=True, exist_ok=True)
            
            filename = f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            filepath = data_dir / filename
            
            with open(filepath, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(['Ticker', 'Timeframe', 'Time', 'Open', 'High', 'Low', 'Close', 'Volume', 'RSI'])
                
                for ticker in tickers:
                    for tf, tf_name in [('15', '15m'), ('60', '1H'), ('240', '4H')]:
                        try:
                            klines = self.bybit_client.get_klines(ticker, 'linear', tf, 150)
                            if not klines:
                                continue
                            
                            closes = [float(k[4]) for k in klines]
                            rsi_values = calculate_rsi(closes, period=14)
                            
                            for i, k in enumerate(reversed(klines)):
                                timestamp_ms = int(k[0])
                                dt = datetime.fromtimestamp(timestamp_ms / 1000)
                                time_str = dt.strftime('%Y-%m-%d %H:%M')
                                
                                open_price = float(k[1])
                                high = float(k[2])
                                low = float(k[3])
                                close = float(k[4])
                                volume = float(k[5])
                                
                                rsi_idx = len(klines) - 1 - i
                                rsi = rsi_values[rsi_idx] if rsi_idx < len(rsi_values) else None
                                rsi_str = f"{rsi:.1f}" if rsi is not None else "N/A"
                                
                                writer.writerow([ticker, tf_name, time_str, open_price, high, low, close, volume, rsi_str])
                                
                        except Exception as e:
                            logger.error(f"Ошибка получения данных для {ticker} {tf}: {e}")
                            continue
            
            logger.info(f"✅ Файл экспорта создан: {filepath}")
            return str(filepath)
            
        except Exception as e:
            logger.error(f"Ошибка генерации файла экспорта: {e}")
            return None