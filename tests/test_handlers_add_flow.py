"""Тесты диалога добавления алерта (Этап 11): кнопка «Пропустить заметку»."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.telegram.handlers import TelegramHandlers

pytestmark = pytest.mark.asyncio

CHAT_ID = 12345


def _callback(data: str) -> SimpleNamespace:
    query = SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=CHAT_ID),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
        edit_message_reply_markup=AsyncMock(),
        message=SimpleNamespace(reply_text=AsyncMock()),
    )
    return SimpleNamespace(
        callback_query=query,
        effective_chat=SimpleNamespace(id=CHAT_ID),
    )


@pytest.fixture
def handlers() -> TelegramHandlers:
    manager = MagicMock()
    manager.add_alert.return_value = (True, False)
    return TelegramHandlers(
        alerts_manager=manager,
        allowed_chat_id=str(CHAT_ID),
        bybit_client=MagicMock(),
    )


class TestDirectionStep:
    async def test_direction_moves_to_note_step(self, handlers):
        handlers.user_state[CHAT_ID] = {
            "step": "waiting_direction", "symbol": "BTCUSDT", "price": 65000.0,
        }
        update = _callback("dir_up")

        await handlers.handle_callback(update, None)

        state = handlers.user_state[CHAT_ID]
        assert state["step"] == "waiting_note"
        assert state["direction"] == "up"

    async def test_note_step_shows_skip_button(self, handlers):
        handlers.user_state[CHAT_ID] = {
            "step": "waiting_direction", "symbol": "BTCUSDT", "price": 65000.0,
        }
        update = _callback("dir_down")

        await handlers.handle_callback(update, None)

        markup = update.callback_query.edit_message_text.await_args.kwargs["reply_markup"]
        callbacks = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
        ]
        assert "skip_note" in callbacks

    async def test_stale_session_warns(self, handlers):
        update = _callback("dir_up")

        await handlers.handle_callback(update, None)

        update.callback_query.answer.assert_awaited_with(
            "Сессия истекла, начните заново", show_alert=True,
        )


class TestSkipNote:
    async def test_skip_finalizes_with_empty_note(self, handlers):
        handlers.user_state[CHAT_ID] = {
            "step": "waiting_note",
            "symbol": "BTCUSDT",
            "price": 65000.0,
            "direction": "up",
        }
        update = _callback("skip_note")

        await handlers.handle_callback(update, None)

        handlers.alerts_manager.add_alert.assert_called_once_with(
            "BTCUSDT", 65000.0, "up", "linear", "",
        )
        text = update.callback_query.message.reply_text.await_args.args[0]
        assert "Добавлено" in text
        assert CHAT_ID not in handlers.user_state

    async def test_skip_removes_keyboard(self, handlers):
        handlers.user_state[CHAT_ID] = {
            "step": "waiting_note", "symbol": "ETHUSDT",
            "price": 3000.0, "direction": "down",
        }
        update = _callback("skip_note")

        await handlers.handle_callback(update, None)

        update.callback_query.edit_message_reply_markup.assert_awaited_with(
            reply_markup=None,
        )

    async def test_skip_without_session_warns(self, handlers):
        update = _callback("skip_note")

        await handlers.handle_callback(update, None)

        update.callback_query.answer.assert_awaited_with(
            "Сессия истекла, начните заново", show_alert=True,
        )
        handlers.alerts_manager.add_alert.assert_not_called()

    async def test_skip_reports_replace(self, handlers):
        handlers.alerts_manager.add_alert.return_value = (True, True)
        handlers.user_state[CHAT_ID] = {
            "step": "waiting_note", "symbol": "BTCUSDT",
            "price": 65000.0, "direction": "up",
        }
        update = _callback("skip_note")

        await handlers.handle_callback(update, None)

        text = update.callback_query.message.reply_text.await_args.args[0]
        assert "Обновлено" in text

    async def test_skip_reports_failure(self, handlers):
        handlers.alerts_manager.add_alert.return_value = (False, False)
        handlers.user_state[CHAT_ID] = {
            "step": "waiting_note", "symbol": "BTCUSDT",
            "price": 65000.0, "direction": "up",
        }
        update = _callback("skip_note")

        await handlers.handle_callback(update, None)

        text = update.callback_query.message.reply_text.await_args.args[0]
        assert "Не удалось сохранить" in text
