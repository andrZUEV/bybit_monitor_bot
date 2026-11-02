"""Тесты пагинации /list (по 10 алертов) и экранирования заметок."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.alerts import AlertRule, Asset
from src.telegram.handlers import TelegramHandlers

pytestmark = pytest.mark.asyncio

CHAT_ID = 12345


def _make_assets(count: int, note: str = "") -> list[Asset]:
    """count алертов, разложенных по активам по 3 штуки."""
    assets: list[Asset] = []
    per_asset = 3
    for i in range(count):
        if i % per_asset == 0:
            assets.append(Asset(
                symbol=f"SYM{i // per_asset}USDT",
                category="linear",
                alerts=[],
            ))
        assets[-1].alerts.append(AlertRule(
            price=100.0 + i,
            direction="up",
            setup_note=note,
        ))
    return assets


def _make_update() -> SimpleNamespace:
    query = SimpleNamespace(
        edit_message_text=AsyncMock(),
        answer=AsyncMock(),
    )
    return SimpleNamespace(
        callback_query=query,
        effective_chat=SimpleNamespace(id=CHAT_ID),
    )


@pytest.fixture
def handlers() -> TelegramHandlers:
    manager = MagicMock()
    manager.get_all_alerts.return_value = []
    return TelegramHandlers(
        alerts_manager=manager,
        allowed_chat_id=str(CHAT_ID),
        bybit_client=MagicMock(),
    )


def _sent_text(update: SimpleNamespace) -> str:
    return update.callback_query.edit_message_text.await_args.args[0]


class TestListPagination:
    async def test_first_page_header_and_size(self, handlers):
        handlers.alerts_manager.get_all_alerts.return_value = _make_assets(25)
        update = _make_update()

        await handlers._show_list(update, None, page=0)

        text = _sent_text(update)
        assert "стр. 1/3" in text
        assert "всего 25" in text
        # 10 строк-алертов на странице
        assert text.count("   • ") == 10

    async def test_second_page_starts_from_eleventh(self, handlers):
        handlers.alerts_manager.get_all_alerts.return_value = _make_assets(25)
        update = _make_update()

        await handlers._show_list(update, None, page=1)

        text = _sent_text(update)
        assert "стр. 2/3" in text
        assert "110.0000" in text  # 100 + 10

    async def test_last_page_partial(self, handlers):
        handlers.alerts_manager.get_all_alerts.return_value = _make_assets(25)
        update = _make_update()

        await handlers._show_list(update, None, page=2)

        text = _sent_text(update)
        assert "стр. 3/3" in text
        assert text.count("   • ") == 5

    async def test_out_of_range_page_is_clamped(self, handlers):
        handlers.alerts_manager.get_all_alerts.return_value = _make_assets(25)
        update = _make_update()

        await handlers._show_list(update, None, page=99)

        assert "стр. 3/3" in _sent_text(update)

    async def test_keyboard_has_navigation(self, handlers):
        handlers.alerts_manager.get_all_alerts.return_value = _make_assets(25)
        update = _make_update()

        await handlers._show_list(update, None, page=1)

        keyboard = update.callback_query.edit_message_text.await_args.kwargs[
            "reply_markup"
        ]
        callbacks = [
            button.callback_data
            for row in keyboard.inline_keyboard
            for button in row
        ]
        assert "list_page|0" in callbacks
        assert "list_page|2" in callbacks

    async def test_note_is_html_escaped(self, handlers):
        handlers.alerts_manager.get_all_alerts.return_value = _make_assets(
            1, note="<b>x</b> & y"
        )
        update = _make_update()

        await handlers._show_list(update, None, page=0)

        text = _sent_text(update)
        assert "&lt;b&gt;x&lt;/b&gt; &amp; y" in text
        assert "<b>x</b>" not in text


class TestDeleteFromList:
    async def test_delete_rerenders_same_page(self, handlers):
        handlers.alerts_manager.get_all_alerts.return_value = _make_assets(25)
        handlers.alerts_manager.remove_alert.return_value = True
        update = _make_update()

        await handlers._handle_delete_alert(
            update, "del|SYM3USDT|109.0|up|1"
        )

        text = _sent_text(update)
        assert "стр. 2/3" in text
        update.callback_query.answer.assert_awaited()
