"""
Обработчики команд и callback'ов Telegram бота
"""

import logging
import time
from typing import Dict, Any, List

from telegram import Update
from telegram.ext import ContextTypes

from src.core.alerts import AlertsManager
from src.api.bybit_client import BybitClient, ScreenerAsset
from src.telegram import keyboards
from src.telegram.keyboards import main_menu_keyboard, reply_keyboard  

logger = logging.getLogger(__name__)


class TelegramHandlers:
    def __init__(self, alerts_manager: AlertsManager, allowed_chat_id: str, bybit_client: BybitClient):
        self.alerts_manager = alerts_manager
        self.allowed_chat_id = str(allowed_chat_id)
        self.bybit_client = bybit_client
        self.user_state: Dict[int, Dict[str, Any]] = {}
        
        self._screener_cache: Dict[str, tuple] = {}
        self._screener_cache_ttl = 120
    
    def _is_allowed(self, update: Update) -> bool:
        return str(update.effective_chat.id) == self.allowed_chat_id
    
    def _reset_state(self, chat_id: int):
        self.user_state.pop(chat_id, None)

    # ==================== КОМАНДЫ ====================
    async def start_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update): return
        self._reset_state(update.effective_chat.id)
        await update.message.reply_text(
            "👋 <b>Bybit Monitor Bot</b>\n\n"
            "🔍 <b>Скринер:</b> ищите монеты с сильным движением!\n"
            "⚡ <b>Алерты:</b> получайте уведомления о пробоях.",
            parse_mode='HTML',
            reply_markup=reply_keyboard()
        )

    async def menu_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update): return
        self._reset_state(update.effective_chat.id)
        await update.message.reply_text("🏠 <b>Главное меню</b>", parse_mode='HTML', reply_markup=reply_keyboard())

    async def list_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update): return
        await self._show_list(update, context)

    async def help_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update): return
        await self._show_help(update)

    # ==================== ТЕКСТОВЫЕ СООБЩЕНИЯ ====================
    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update): return
        
        chat_id = update.effective_chat.id
        text = update.message.text.strip()
        state = self.user_state.get(chat_id, {})
        
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
                    
                    success = self.alerts_manager.add_alert(symbol, price, direction, category, clean_note)
                    dir_text = {"up": "снизу вверх 🟢", "down": "сверху вниз 🔴", "any": "любое ⚪️"}
                    note_display = f"\n📝 Сетап: <code>{clean_note}</code>" if clean_note else ""
                    
                    if success:
                        await update.message.reply_text(f"✅ <b>Добавлено:</b>\n🪙 {symbol}\n💰 {price:,.2f}\n🎯 {dir_text[direction]}{note_display}", parse_mode='HTML', reply_markup=keyboards.main_menu_keyboard())
                    else:
                        await update.message.reply_text("⚠️ Уже существует", reply_markup=keyboards.main_menu_keyboard())
                    return
            except ValueError:
                pass
        
        await update.message.reply_text("❓ Не понял команду.", parse_mode='HTML', reply_markup=keyboards.main_menu_keyboard())

    async def _process_bulk_add(self, update: Update, lines: list[str]):
        success_count = fail_count = 0
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
                        if self.alerts_manager.add_alert(symbol, price, direction, category, clean_note): success_count += 1
                        else:
                            fail_count += 1; failed_details.append(f"• {line} (существует)")
                    else:
                        fail_count += 1; failed_details.append(f"• {line} (ошибка)")
                except ValueError:
                    fail_count += 1; failed_details.append(f"• {line} (ошибка цены)")
            else:
                fail_count += 1; failed_details.append(f"• {line} (формат)")
        
        report = f"📊 <b>Результат:</b>\n✅ Успешно: <b>{success_count}</b>\n"
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
            
        # ==================== СКРИНЕР (УПРОЩЕННЫЙ) ====================
        elif data == "menu_screener":
            await self._run_screener_simple(update, chat_id)
            
        elif data == "scr_refresh":
            self._screener_cache.clear()
            await self._run_screener_simple(update, chat_id)
                
        elif data.startswith("scr_add_"):
            symbol = data.replace("scr_add_", "")
            self.user_state[chat_id] = {'step': 'waiting_price', 'symbol': symbol}
            await query.edit_message_text(f"➕ <b>Алерт на {symbol}</b>\nВведите цену:", parse_mode='HTML', reply_markup=keyboards.cancel_keyboard())
        # ==================== КОНЕЦ СКРИНЕРА ====================
            
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
            note = state.get('note', '')
            
            success = self.alerts_manager.add_alert(symbol, price, direction, "linear", note)
            self._reset_state(chat_id)
            
            dir_text = {"up": "снизу вверх 🟢", "down": "сверху вниз 🔴", "any": "любое ⚪️"}
            note_display = f"\n📝 Сетап: <code>{note}</code>" if note else ""
            await query.edit_message_text(f"✅ <b>Добавлено:</b>\n🪙 {symbol}\n💰 {price:,.2f}\n🎯 {dir_text[direction]}{note_display}", parse_mode='HTML', reply_markup=keyboards.main_menu_keyboard())
            
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
        """Упрощенный скринер - сразу показывает топ по абсолютному изменению цены"""
        query = update.callback_query
        try:
            await query.edit_message_text("⏳ <b>Анализирую рынок...</b>", parse_mode='HTML')
        except Exception:
            pass
        
        cache_key = "screener_simple"
        cached = self._screener_cache.get(cache_key)
        
        if cached and (time.time() - cached[0]) < self._screener_cache_ttl:
            assets, fetch_time = cached
            from_cache = True
        else:
            # Получаем данные с фильтром: мин объем 1 млн $, мин изменение 3%
            assets = self.bybit_client.get_screener_data(
                category="linear",
                sort_by="volume_desc",  # Сортируем по объему для стабильности
                min_volume_usd=1_000_000,
                min_change_abs=3.0,  # Минимум 3% движения
                limit=20
            )
            
            # Сортируем по абсолютному изменению цены (не важно + или -)
            assets.sort(key=lambda x: abs(x.price_change_24h), reverse=True)
            
            fetch_time = time.time()
            self._screener_cache[cache_key] = (fetch_time, assets)
            from_cache = False
        
        if not assets:
            await query.edit_message_text(
                "❌ <b>Ничего не найдено</b>\n\n"
                "Попробуйте позже или уменьшите фильтры.",
                parse_mode='HTML',
                reply_markup=keyboards.main_menu_keyboard()
            )
            return
        
        text = self._format_screener_simple(assets, fetch_time, from_cache)
        top_symbol = assets[0].symbol
        
        await query.edit_message_text(text, parse_mode='HTML', reply_markup=keyboards.screener_add_alert_keyboard(top_symbol))
    
    def _format_screener_simple(self, assets: List[ScreenerAsset], fetch_time: float, from_cache: bool) -> str:
        """Форматирует результаты упрощенного скринера"""
        text = "🔍 <b>ТОП ПО ДВИЖЕНИЮ ЦЕНЫ</b> (24ч)\n"
        text += "<i>Отсортировано по абсолютному изменению (не важно + или -)</i>\n"
        text += "<i>Фильтры: >= 3% движения, >= 1M$ объема</i>\n\n"
        text += "━" * 30 + "\n\n"
        
        for i, asset in enumerate(assets[:15], 1):  # Показываем топ 15
            change_emoji = "🚀" if asset.price_change_24h > 5 else "💥" if asset.price_change_24h < -5 else "🟢" if asset.price_change_24h > 0 else "🔴"
            
            vol = asset.volume_24h
            vol_str = f"${vol/1_000_000_000:.2f}B" if vol >= 1e9 else f"${vol/1_000_000:.2f}M" if vol >= 1e6 else f"${vol/1_000:.1f}K"
            
            price_str = f"{asset.price:,.2f}" if asset.price >= 100 else f"{asset.price:,.4f}" if asset.price >= 1 else f"{asset.price:,.6f}"
            
            text += f"<b>{i:2d}.</b> <code>{asset.symbol}</code>\n"
            text += f"    💰 <b>{price_str}</b> $ | {change_emoji} <b>{asset.price_change_24h:+.2f}%</b>\n"
            text += f"    📊 Объем: <b>{vol_str}</b>\n\n"
        
        text += "━" * 30 + "\n"
        text += f"<i>{'📥 Из кэша' if from_cache else '🔄 Свежие данные'} ({int(time.time() - fetch_time)}с назад)</i>"
        return text
    # ==================== КОНЕЦ СКРИНЕРА ====================

    async def _handle_current_prices(self, update: Update):
        query = update.callback_query
        await query.edit_message_text("⏳ Загружаю цены...")
        assets = self.alerts_manager.get_all_alerts()
        if not assets:
            await query.edit_message_text("📋 Пусто.", reply_markup=keyboards.main_menu_keyboard())
            return
        
        text = "💰 <b>Текущие цены:</b>\n\n"
        for symbol in list(set(a.symbol for a in assets)):
            category = next((a.category for a in assets if a.symbol == symbol), "linear")
            ticker = self.bybit_client.get_ticker(symbol, category)
            text += f"🪙 <b>{symbol}</b>: <code>{ticker.price:,.4f}</code> $\n" if ticker else f"🪙 <b>{symbol}</b>: ❌\n"
        
        await query.edit_message_text(text, parse_mode='HTML', reply_markup=keyboards.main_menu_keyboard())

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
                
        if update.callback_query:
            await update.callback_query.edit_message_text(text, parse_mode='HTML', reply_markup=keyboard)
        else:
            await update.message.reply_text(text, parse_mode='HTML', reply_markup=keyboard)

    async def _show_help(self, update: Update):
        text = (
            "ℹ️ <b>Справка</b>\n\n"
            "🔍 <b>Скринер:</b> показывает топ монет по движению цены за 24ч\n"
            "⚡ <b>Алерт:</b> <code>TICKER PRICE DIR [NOTE]</code>\n"
            "Пример: <code>BTCUSDT 85000 up пробой</code>"
        )
        if update.callback_query:
            await update.callback_query.edit_message_text(text, parse_mode='HTML', reply_markup=keyboards.main_menu_keyboard())
        else:
            await update.message.reply_text(text, parse_mode='HTML', reply_markup=keyboards.main_menu_keyboard())