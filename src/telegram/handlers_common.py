"""
Общие утилиты для миксинов TelegramHandlers.
"""
from __future__ import annotations

from telegram.error import BadRequest

from telegram import Update


class CommonMixin:
    """
    Базовые методы, которые нужны всем остальным миксинам.
    Ожидает, что self содержит:
      - self.allowed_chat_id
      - self.user_state
    """

    def _is_allowed(self, update: Update) -> bool:
        return str(update.effective_chat.id) == self.allowed_chat_id

    def _reset_state(self, chat_id: int) -> None:
        self.user_state.pop(chat_id, None)

    def _main_menu_kb(self):
        """Главное меню с актуальным значением порога."""
        from src.core.settings import get_settings
        from src.telegram import keyboards
        s = get_settings()
        return keyboards.main_menu_keyboard_with_settings(s.describe())


async def safe_edit_to_main_menu(
    query,
    text: str,
    parse_mode: str | None = None,
    reply_markup=None,
) -> None:
    """
    Безопасно редактирует сообщение в главное меню.
    Работает и для текстовых сообщений, и для подписей к документам/фото.
    """
    try:
        await query.edit_message_text(
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )
        return
    except BadRequest as e:
        msg = str(e).lower()
        if "no text in the message" not in msg and "there is no text" not in msg:
            if "message is not modified" in msg:
                return
            if "no caption" not in msg and "media" not in msg:
                pass

    try:
        await query.edit_message_caption(
            caption=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )
        return
    except BadRequest as e:
        msg = str(e).lower()
        if "message is not modified" in msg:
            return
        try:
            await query.message.delete()
        except BadRequest:
            pass
        await query.message.chat.send_message(
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )