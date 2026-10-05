"""Тесты колбэков настроек ГЛУБИНЫ истории и меню истории в TelegramHandlers."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.alert_history import (
    AlertHistory,
    get_alert_history,
    set_alert_history,
)
from src.core.settings import RuntimeSettings, get_settings, set_settings
from src.telegram.handlers import TelegramHandlers

pytestmark = pytest.mark.asyncio


# ==================== ФИКСТУРЫ ====================

@pytest.fixture(autouse=True)
def _reset_singletons(tmp_path):
    """Каждый тест — свежие синглтоны settings и alert_history."""
    set_settings(RuntimeSettings())
    set_alert_history(AlertHistory(tmp_path / "history.jsonl", max_records=1000))
    yield
    set_settings(None)
    set_alert_history(None)


@pytest.fixture
def handlers() -> TelegramHandlers:
    alerts = MagicMock()
    alerts.add_alert.return_value = (True, False)
    alerts.get_all_alerts.return_value = []

    bybit = MagicMock()

    return TelegramHandlers(
        alerts_manager=alerts,
        allowed_chat_id="12345",
        bybit_client=bybit,
    )


def _make_callback_update(data: str, chat_id: int = 12345) -> SimpleNamespace:
    query = SimpleNamespace(
        data=data,
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
        edit_message_reply_markup=AsyncMock(),
        message=SimpleNamespace(reply_text=AsyncMock()),
        from_user=SimpleNamespace(id=chat_id),
    )
    chat = SimpleNamespace(id=chat_id)
    return SimpleNamespace(callback_query=query, effective_chat=chat)


def _make_message_update(text: str, chat_id: int = 12345) -> SimpleNamespace:
    reply = AsyncMock()
    message = SimpleNamespace(text=text, reply_text=reply)
    chat = SimpleNamespace(id=chat_id)
    return SimpleNamespace(effective_chat=chat, message=message)


# ==================== settings_history_depth ====================

class TestHistoryDepthMenu:
    async def test_settings_history_depth_opens(self, handlers):
        update = _make_callback_update("settings_history_depth")
        await handlers.handle_callback(update, None)

        query = update.callback_query
        assert query.edit_message_text.await_count == 1
        text = query.edit_message_text.await_args.args[0]
        assert "Глубина истории" in text
        assert "1000" in text


class TestSetDepthPreset:
    async def test_set_depth_500(self, handlers, tmp_path):
        s = get_settings()
        s._path = tmp_path / "settings.json"

        update = _make_callback_update("set_depth|500")
        await handlers.handle_callback(update, None)

        assert get_settings().alert_history_depth == 500
        text = update.callback_query.edit_message_text.await_args.args[0]
        assert "500" in text

    async def test_set_depth_updates_history_max_records(self, handlers, tmp_path):
        s = get_settings()
        s._path = tmp_path / "settings.json"

        update = _make_callback_update("set_depth|200")
        await handlers.handle_callback(update, None)

        # Глобальный AlertHistory получил новый max_records
        assert get_alert_history().max_records == 200

    async def test_set_depth_persists_to_disk(self, handlers, tmp_path):
        s = get_settings()
        s._path = tmp_path / "settings.json"

        update = _make_callback_update("set_depth|300")
        await handlers.handle_callback(update, None)

        loaded = RuntimeSettings.load(tmp_path / "settings.json")
        assert loaded.alert_history_depth == 300

    async def test_set_depth_clamps_low(self, handlers, tmp_path):
        s = get_settings()
        s._path = tmp_path / "settings.json"

        # 50 → обрезается до 100 (минимум)
        update = _make_callback_update("set_depth|50")
        await handlers.handle_callback(update, None)

        assert get_settings().alert_history_depth == 100


class TestSetDepthCustom:
    async def test_custom_opens_input_state(self, handlers):
        update = _make_callback_update("set_depth_custom")
        await handlers.handle_callback(update, None)

        state = handlers.user_state.get(12345, {})
        assert state.get("step") == "waiting_custom_depth"

    async def test_custom_input_parsed(self, handlers, tmp_path):
        s = get_settings()
        s._path = tmp_path / "settings.json"

        # 1. Пользователь нажал «✏️ Свой»
        await handlers.handle_callback(
            _make_callback_update("set_depth_custom"), None,
        )

        # 2. Ввёл значение текстом
        msg_update = _make_message_update("2000")
        await handlers.handle_text(msg_update, None)

        assert get_settings().alert_history_depth == 2000
        text = msg_update.message.reply_text.await_args.args[0]
        assert "2000" in text

    async def test_custom_input_clamps_high(self, handlers, tmp_path):
        s = get_settings()
        s._path = tmp_path / "settings.json"

        await handlers.handle_callback(
            _make_callback_update("set_depth_custom"), None,
        )
        msg_update = _make_message_update("999999")
        await handlers.handle_text(msg_update, None)

        # 999999 → обрезается до 100_000 (максимум)
        assert get_settings().alert_history_depth == 100_000

    async def test_custom_input_invalid(self, handlers, tmp_path):
        s = get_settings()
        s._path = tmp_path / "settings.json"
        old_depth = s.alert_history_depth

        await handlers.handle_callback(
            _make_callback_update("set_depth_custom"), None,
        )
        msg_update = _make_message_update("abc")
        await handlers.handle_text(msg_update, None)

        # Значение не изменилось
        assert get_settings().alert_history_depth == old_depth
        text = msg_update.message.reply_text.await_args.args[0]
        assert "❌" in text

    async def test_custom_input_updates_history_max_records(self, handlers, tmp_path):
        s = get_settings()
        s._path = tmp_path / "settings.json"

        await handlers.handle_callback(
            _make_callback_update("set_depth_custom"), None,
        )
        msg_update = _make_message_update("700")
        await handlers.handle_text(msg_update, None)

        assert get_alert_history().max_records == 700


# ==================== history_menu ====================

class TestHistoryMenu:
    async def test_history_menu_shows_count(self, handlers):
        # Наполним историю
        from tests.test_alert_history import _make_record
        for _ in range(3):
            get_alert_history().append(_make_record())

        update = _make_callback_update("history_menu")
        await handlers.handle_callback(update, None)

        text = update.callback_query.edit_message_text.await_args.args[0]
        assert "История алертов" in text
        assert "Записей" in text
        assert "3" in text

    async def test_history_menu_empty(self, handlers):
        update = _make_callback_update("history_menu")
        await handlers.handle_callback(update, None)

        text = update.callback_query.edit_message_text.await_args.args[0]
        assert "0" in text

    async def test_history_clear_confirm(self, handlers):
        update = _make_callback_update("history_clear_confirm")
        await handlers.handle_callback(update, None)

        text = update.callback_query.edit_message_text.await_args.args[0]
        assert "Очистить" in text

    async def test_history_clear_yes(self, handlers):
        from tests.test_alert_history import _make_record
        history = get_alert_history()
        history.append(_make_record())
        history.append(_make_record())
        assert history.count() == 2

        update = _make_callback_update("history_clear_yes")
        await handlers.handle_callback(update, None)

        assert history.count() == 0
        text = update.callback_query.edit_message_text.await_args.args[0]
        assert "очищена" in text.lower()

    async def test_history_export_empty_shows_alert(self, handlers):
        update = _make_callback_update("history_export_csv")
        await handlers.handle_callback(update, None)

        # Пустая история → query.answer с show_alert
        update.callback_query.answer.assert_awaited()
        # Не должен показывать прогресс
        # (edit_message_text либо не вызван, либо вызван до answer)