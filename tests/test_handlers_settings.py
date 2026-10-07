"""Тесты колбэков настроек в TelegramHandlers."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.settings import RuntimeSettings, get_settings, set_settings
from src.telegram.handlers import TelegramHandlers

pytestmark = pytest.mark.asyncio


# ==================== ФИКСТУРЫ ====================

@pytest.fixture(autouse=True)
def _reset_singleton():
    """Каждый тест — свежий синглтон."""
    set_settings(RuntimeSettings())
    yield
    set_settings(None)


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
    """
    Минимальный Update для handle_callback.

    Что нужно:
      - update.callback_query.data
      - await update.callback_query.answer()
      - await update.callback_query.edit_message_text(...)
      - update.callback_query.from_user.id
      - update.effective_chat.id
    """
    query = SimpleNamespace(
        data=data,
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
        edit_message_reply_markup=AsyncMock(),
        message=SimpleNamespace(reply_text=AsyncMock()),
        from_user=SimpleNamespace(id=chat_id),
    )
    chat = SimpleNamespace(id=chat_id)
    return SimpleNamespace(
        callback_query=query,
        effective_chat=chat,
    )


def _make_message_update(text: str, chat_id: int = 12345) -> SimpleNamespace:
    reply = AsyncMock()
    message = SimpleNamespace(text=text, reply_text=reply)
    chat = SimpleNamespace(id=chat_id)
    return SimpleNamespace(effective_chat=chat, message=message)




# ==================== settings_menu ====================

class TestSettingsMenu:
    async def test_settings_menu_opens(self, handlers):
        update = _make_callback_update("settings_menu")
        await handlers.handle_callback(update, None)

        query = update.callback_query
        assert query.edit_message_text.await_count == 1
        text = query.edit_message_text.await_args.args[0]
        assert "⚙️" in text and "Настройки" in text
        assert "Порог алертов" in text

    async def test_settings_threshold_opens(self, handlers):
        update = _make_callback_update("settings_threshold")
        await handlers.handle_callback(update, None)

        query = update.callback_query
        text = query.edit_message_text.await_args.args[0]
        assert "Порог алертов" in text


# ==================== set_score|... ====================

class TestSetScorePreset:
    async def test_set_score_to_zero(self, handlers, tmp_path):
        s = get_settings()
        s._path = tmp_path / "settings.json"

        update = _make_callback_update("set_score|0.0")
        await handlers.handle_callback(update, None)

        assert get_settings().alert_min_score == 0.0
        text = update.callback_query.edit_message_text.await_args.args[0]
        assert "≥ 0" in text

    async def test_set_score_to_neg5(self, handlers, tmp_path):
        s = get_settings()
        s._path = tmp_path / "settings.json"

        update = _make_callback_update("set_score|-5.0")
        await handlers.handle_callback(update, None)

        assert get_settings().alert_min_score == -5.0

    async def test_set_score_to_neg_inf(self, handlers, tmp_path):
        s = get_settings()
        s._path = tmp_path / "settings.json"
        s.alert_min_score = 0.0

        update = _make_callback_update("set_score|-inf")
        await handlers.handle_callback(update, None)

        assert get_settings().alert_min_score == float("-inf")
        text = update.callback_query.edit_message_text.await_args.args[0]
        assert "ВСЕ" in text

    async def test_set_score_persists_to_disk(self, handlers, tmp_path):
        s = get_settings()
        s._path = tmp_path / "settings.json"

        update = _make_callback_update("set_score|3.0")
        await handlers.handle_callback(update, None)

        loaded = RuntimeSettings.load(tmp_path / "settings.json")
        assert loaded.alert_min_score == 3.0


# ==================== set_score_custom ====================

class TestSetScoreCustom:
    async def test_custom_opens_input_state(self, handlers):
        update = _make_callback_update("set_score_custom")
        await handlers.handle_callback(update, None)

        state = handlers.user_state.get(12345, {})
        assert state.get("step") == "waiting_custom_score"

    async def test_custom_input_parsed(self, handlers, tmp_path):
        s = get_settings()
        s._path = tmp_path / "settings.json"

        # 1. Пользователь нажал «✏️ Свой порог»
        cb_update = _make_callback_update("set_score_custom")
        await handlers.handle_callback(cb_update, None)

        # 2. Ввёл значение текстом
        msg_update = _make_message_update("-2.5")
        await handlers.handle_text(msg_update, None)

        assert get_settings().alert_min_score == -2.5
        text = msg_update.message.reply_text.await_args.args[0]
        assert "≥ -2.5" in text

    async def test_custom_input_all_keyword(self, handlers, tmp_path):
        s = get_settings()
        s._path = tmp_path / "settings.json"
        s.alert_min_score = 0.0

        await handlers.handle_callback(_make_callback_update("set_score_custom"), None)
        msg_update = _make_message_update("all")
        await handlers.handle_text(msg_update, None)

        assert get_settings().alert_min_score == float("-inf")

    async def test_custom_input_invalid(self, handlers, tmp_path):
        s = get_settings()
        s._path = tmp_path / "settings.json"
        old_score = s.alert_min_score

        await handlers.handle_callback(_make_callback_update("set_score_custom"), None)
        msg_update = _make_message_update("abc")
        await handlers.handle_text(msg_update, None)

        # Значение не изменилось
        assert get_settings().alert_min_score == old_score
        text = msg_update.message.reply_text.await_args.args[0]
        assert "❌" in text


# ==================== /settings ====================

class TestSettingsCommand:
    async def test_settings_cmd(self, handlers):
        """settings_cmd — метод класса, не колбэк."""
        update = _make_message_update("/settings")  # text не важен
        # В _make_message_update у нас message.reply_text — но settings_cmd
        # использует update.message.reply_text — ок.
        await handlers.settings_cmd(update, None)

        text = update.message.reply_text.await_args.args[0]
        assert "⚙️" in text and "Порог алертов" in text