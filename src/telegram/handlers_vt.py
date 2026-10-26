"""
Миксин для UI виртуальных сделок.
"""
from __future__ import annotations

import logging

from src.core.settings import get_settings
from src.core.virtual_trader import get_virtual_trader
from src.telegram import keyboards

logger = logging.getLogger(__name__)


class VirtualTradeMixin:
    """
    Ожидает, что self содержит:
      - self._main_menu_kb()
    """

    async def _show_vt_menu(self, query):
        """Меню виртуальных сделок."""
        trader = get_virtual_trader()
        s = get_settings()

        total = trader.count()
        pending = trader.count_by_status("pending_entry")
        open_ = trader.count_by_status("open")
        closed = trader.count_by_status("closed")
        cancelled = trader.count_by_status("cancelled")

        await query.edit_message_text(
            f"💼 <b>Виртуальные сделки</b>\n\n"
            f"Всего: <b>{total}</b>\n"
            f"⏳ Ждут входа: <b>{pending}</b>\n"
            f"🟢 В работе: <b>{open_}</b>\n"
            f"✅ Закрыто: <b>{closed}</b>\n"
            f"🗑 Отменено: <b>{cancelled}</b>\n\n"
            f"🔔 Запись: <b>{s.describe_virtual_trader()}</b>\n"
            f"💰 Риск по умолчанию: <b>{s.virtual_trader_default_risk:.2f} USDT</b>",
            parse_mode="HTML",
            reply_markup=keyboards.vt_menu_keyboard(
                enabled=s.virtual_trader_enabled,
                total=total,
                active=pending + open_,
            ),
        )

    async def _show_vt_list(self, query, status_filter: str = "active"):
        """Список сделок (active / closed / all)."""
        trader = get_virtual_trader()
        
        if status_filter == "active":
            trades = trader.active_trades()
            title = "🟢 <b>Активные сделки</b>"
        elif status_filter == "closed":
            trades = [t for t in trader.all_trades() if t.status in ("closed", "cancelled")]
            title = "📜 <b>Закрытые сделки</b>"
        else:
            trades = trader.all_trades()
            title = "📋 <b>Все сделки</b>"

        if not trades:
            await query.edit_message_text(
                f"{title}\n\nПусто.",
                parse_mode="HTML",
                reply_markup=keyboards.vt_list_keyboard(status_filter),
            )
            return

        # Сортируем по created_at (новые первыми)
        trades.sort(key=lambda t: t.created_at, reverse=True)
        
        lines = [title, ""]
        for t in trades[:20]:  # максимум 20 на экран
            emoji = "🟢" if t.status in ("open", "pending_entry") else "⚪️"
            lines.append(f"{emoji} <b>{t.symbol}</b> {t.direction.upper()}")
            lines.append(f"   Entry: {t.entry:.2f} | SL: {t.sl:.2f}")
            lines.append(f"   PnL: {t.realized_pnl:+.2f} USDT | {t.status}")
            lines.append("")

        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=keyboards.vt_list_keyboard(status_filter),
        )

    async def _show_vt_stats(self, query):
        """Статистика виртуальных сделок."""
        from src.core.virtual_trader import format_stats_message
        trader = get_virtual_trader()
        
        if trader.count() == 0:
            await query.answer("Пока нет сделок", show_alert=True)
            return

        text = format_stats_message(trader)
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=keyboards.vt_list_keyboard("stats"),
        )

    async def _handle_take_to_diary(self, query, data: str):
        """
        Кнопка [📝 Взять в дневник] под алертом.
        data = "vt_take|SYMBOL|ENTRY|SL|TP1|TP2|DIRECTION"
        """
        parts = data.split("|")
        if len(parts) != 7:
            await query.answer("Ошибка формата", show_alert=True)
            return
            
        _, symbol, entry_s, sl_s, tp1_s, tp2_s, direction = parts
        try:
            entry = float(entry_s)
            sl = float(sl_s)
            tp1 = float(tp1_s)
            tp2 = float(tp2_s) if tp2_s else 0.0
        except ValueError:
            await query.answer("Ошибка парсинга", show_alert=True)
            return

        # Проверяем, была ли метка о риске. 
        # В мониторе мы ставим "⚠️" если risk.valid == False
        # Но здесь мы не знаем, нажал ли он на ⚠️ или на обычную.
        # Проще: мы всегда создаем сделку, но если entry == sl (аномалия), отсеиваем.
        
        tps = [tp1]
        if tp2 > 0:
            tps.append(tp2)

        s = get_settings()
        risk_usdt = s.virtual_trader_default_risk
        
        trader = get_virtual_trader()
        try:
            trade = trader.create(
                symbol=symbol,
                direction=direction,
                entry=entry,
                sl=sl,
                tps=tps,
                risk_usdt=risk_usdt,
                leverage=10,
            )
        except Exception as e:
            await query.answer(f"Ошибка: {e}", show_alert=True)
            return

        # Показываем карточку
        text = (
            f"✅ <b>Виртуальная сделка создана</b>\n\n"
            f"🪙 <b>{trade.symbol}</b> · {trade.direction.upper()}\n"
            f"📌 Entry: <code>{trade.entry:.2f}</code>\n"
            f"🛑 SL: <code>{trade.sl:.2f}</code>\n"
        )
        for i, tp in enumerate(trade.tps, 1):
            text += f"🎯 TP{i}: <code>{tp:.2f}</code> ({trade.portions[i-1]*100:.0f}%)\n"
            
        text += f"\n💰 Риск: {trade.risk_usdt:.2f} USDT\n"
        text += "⏳ Статус: Ждём входа"

        await query.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=keyboards.vt_trade_card_keyboard(trade.id),
        )