"""Этап 10: зоны в Telegram-слое — /list, клавиатура и однострочное добавление."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.alerts import AlertRule, Asset
from src.telegram import keyboards
from src.telegram.handlers import TelegramHandlers

pytestmark = pytest.mark.asyncio

CHAT_ID = 12345


def _update_with_query() -> SimpleNamespace:
    query = SimpleNamespace(edit_message_text=AsyncMock(), answer=AsyncMock())
    return SimpleNamespace(
        callback_query=query,
        effective_chat=SimpleNamespace(id=CHAT_ID),
    )


def _update_with_text(text: str) -> SimpleNamespace:
    reply = AsyncMock()
    message = SimpleNamespace(text=text, reply_text=reply)
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=CHAT_ID),
        message=message,
    )


@pytest.fixture
def handlers() -> TelegramHandlers:
    manager = MagicMock()
    manager.get_all_alerts.return_value = []
    manager.add_alert.return_value = (True, False)
    return TelegramHandlers(
        alerts_manager=manager,
        allowed_chat_id=str(CHAT_ID),
        bybit_client=MagicMock(),
    )


class TestListShowsZone:
    async def test_text_contains_range(self, handlers):
        handlers.alerts_manager.get_all_alerts.return_value = [
            Asset(symbol="BTCUSDT", category="linear", alerts=[
                AlertRule(
                    price=102.5, direction="up",
                    price_low=100.0, price_high=105.0, setup_note="зона",
                ),
                AlertRule(price=200.0, direction="down", setup_note="точка"),
            ]),
        ]
        update = _update_with_query()

        await handlers._show_list(update, None, page=0)

        text = update.callback_query.edit_message_text.await_args.args[0]
        assert "100.0000–105.0000" in text
        assert "200.0000" in text
        assert "зона" in text

    async def test_keyboard_label_contains_range(self, handlers):
        handlers.alerts_manager.get_all_alerts.return_value = [
            Asset(symbol="BTCUSDT", category="linear", alerts=[
                AlertRule(
                    price=102.5, direction="up",
                    price_low=100.0, price_high=105.0,
                ),
            ]),
        ]
        update = _update_with_query()

        await handlers._show_list(update, None, page=0)

        markup = update.callback_query.edit_message_text.await_args.kwargs["reply_markup"]
        labels = [
            button.text
            for row in markup.inline_keyboard
            for button in row
        ]
        assert any("100.0000–105.0000" in label for label in labels)

    async def test_delete_callback_uses_center_price(self, handlers):
        handlers.alerts_manager.get_all_alerts.return_value = [
            Asset(symbol="BTCUSDT", category="linear", alerts=[
                AlertRule(
                    price=102.5, direction="up",
                    price_low=100.0, price_high=105.0,
                ),
            ]),
        ]
        update = _update_with_query()

        await handlers._show_list(update, None, page=0)

        markup = update.callback_query.edit_message_text.await_args.kwargs["reply_markup"]
        callbacks = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
        ]
        assert "del|BTCUSDT|102.5|up|0" in callbacks


class TestKeyboardBackwardCompatibility:
    async def test_three_tuple_rows_still_work(self):
        markup = keyboards.alerts_list_keyboard(
            [("BTCUSDT", 65000.0, "up")], page=0, pages=1,
        )
        labels = [b.text for row in markup.inline_keyboard for b in row]
        assert any("65,000.00" in label for label in labels)


class TestSingleLineZoneAdd:
    async def test_zone_passed_to_manager(self, handlers):
        update = _update_with_text("BTC 64000-66500 up зона")

        await handlers.handle_text(update, None)

        handlers.alerts_manager.add_alert.assert_called_once_with(
            "BTCUSDT", 65250.0, "up", "linear", "зона",
            price_low=64000.0, price_high=66500.0,
        )

    async def test_reply_shows_range(self, handlers):
        update = _update_with_text("BTC 64000-66500 up зона")

        await handlers.handle_text(update, None)

        text_sent = update.message.reply_text.await_args.args[0]
        assert "64,000.00–66,500.00" in text_sent
        assert "Добавлено" in text_sent

    async def test_point_line_unchanged(self, handlers):
        update = _update_with_text("BTC 65000 up пробой")

        await handlers.handle_text(update, None)

        handlers.alerts_manager.add_alert.assert_called_once_with(
            "BTCUSDT", 65000.0, "up", "linear", "пробой",
            price_low=None, price_high=None,
        )
