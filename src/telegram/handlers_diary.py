"""
Миксин для UI дневника разворотов (reversal_diary, Этап 7).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

from src.core.reversal_diary import get_reversal_diary
from src.core.settings import get_settings
from src.telegram import keyboards
from src.utils.config import Config
from telegram import InputFile

logger = logging.getLogger(__name__)


class DiaryMixin:
    """
    Ожидает, что self содержит:
      - self._main_menu_kb()
    """

    async def _show_rd_menu(self, query):
        """Меню дневника разворотов."""
        diary = get_reversal_diary()
        s = get_settings()

        total = diary.count()
        pending = diary.count_by_status("pending")
        success = diary.count_by_status("success")
        fail = diary.count_by_status("fail")
        expired = diary.count_by_status("expired")

        await query.edit_message_text(
            f"📈 <b>Дневник разворотов</b>\n\n"
            f"Записей: <b>{total}</b>\n"
            f"⏳ pending: <b>{pending}</b>\n"
            f"✅ success: <b>{success}</b>\n"
            f"❌ fail: <b>{fail}</b>\n"
            f"⌛ expired: <b>{expired}</b>\n\n"
            f"🔔 Запись: <b>{s.describe_reversal_diary()}</b>\n"
            f"🎯 Мин. n для показа: <b>{s.reversal_min_n_display}</b>\n\n"
            f"Дневник пишет исход каждого price-alert'а (success/fail по "
            f"ATR) и агрегирует статистику по сетапам.\n"
            f"Не влияет на score и hard-фильтры.",
            parse_mode="HTML",
            reply_markup=keyboards.rd_menu_keyboard(
                s.reversal_diary_enabled,
                s.reversal_min_n_display,
                total,
            ),
        )

    async def _show_rd_stats(self, query):
        """Экран статистики: топ-комбинации по всем символам + ALL."""
        diary = get_reversal_diary()

        if diary.count() == 0:
            await query.answer("Дневник пуст", show_alert=True)
            return

        await query.edit_message_text("⏳ Считаю статистику...")

        text = diary.format_stats_message(symbol=None, limit=10)
        s = get_settings()

        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=keyboards.rd_menu_keyboard(
                s.reversal_diary_enabled,
                s.reversal_min_n_display,
                diary.count(),
            ),
        )

    async def _handle_rd_export_csv(self, query):
        """Экспорт дневника в CSV."""
        diary = get_reversal_diary()
        if diary.count() == 0:
            await query.answer("Дневник пуст", show_alert=True)
            return

        await query.edit_message_text("⏳ Генерирую CSV...")

        out_path = Config.EXPORTS_DIR / f"reversal_diary_{int(time.time())}.csv"
        csv_path = await asyncio.to_thread(diary.export_csv, out_path)

        with open(csv_path, "rb") as f:
            await query.message.reply_document(
                document=InputFile(f, filename=csv_path.name),
                caption=(
                    f"📈 Дневник разворотов: {diary.count()} записей"
                ),
            )
        try:
            os.remove(csv_path)
        except Exception:
            pass

        await self._show_rd_menu(query)