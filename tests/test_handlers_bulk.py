"""
Тесты TelegramHandlers: однострочное и массовое добавление алертов.

Проверяем, что:
  - handle_text вызывает parse_mass_add и alerts_manager.add_alert с правильными аргументами;
  - однострочный формат SYMBOL PRICE DIR [NOTE] работает;
  - многострочный формат уходит в _process_bulk_add и собирает отчёт;
  - невалидные строки не вызывают add_alert и попадают в "Пропущено".

AlertsManager и keyboards замоканы. Реального Telegram и JSON-файлов нет.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.telegram.handlers import TelegramHandlers

pytestmark = pytest.mark.asyncio


# ==================== ФИКСТУРЫ ====================

@pytest.fixture
def alerts_manager_mock() -> MagicMock:
    """AlertsManager-заглушка. add_alert по умолчанию → (True, False)."""
    m = MagicMock()
    m.add_alert.return_value = (True, False)
    m.get_all_alerts.return_value = []
    return m


@pytest.fixture
def bybit_client_mock() -> MagicMock:
    return MagicMock()


@pytest.fixture
def handlers(alerts_manager_mock, bybit_client_mock) -> TelegramHandlers:
    """Хендлер с замоканными зависимостями."""
    h = TelegramHandlers(
        alerts_manager=alerts_manager_mock,
        allowed_chat_id="12345",
        bybit_client=bybit_client_mock,
    )
    return h


def _make_update(text: str, chat_id: int = 12345) -> SimpleNamespace:
    """
    Минимальный Update-подобный объект для handle_text.

    Что нужно хендлеру:
      - update.effective_chat.id
      - update.message.text
      - await update.message.reply_text(...) — AsyncMock
    """
    reply = AsyncMock()
    message = SimpleNamespace(text=text, reply_text=reply)
    chat = SimpleNamespace(id=chat_id)
    return SimpleNamespace(
        effective_chat=chat,
        message=message,
    )


# ==================== A. ОДНОСТРОЧНОЕ ДОБАВЛЕНИЕ ====================

class TestSingleLineAdd:
    async def test_minimal_line(self, handlers, alerts_manager_mock):
        update = _make_update("BTC 65000 up пробой")
        await handlers.handle_text(update, None)

        alerts_manager_mock.add_alert.assert_called_once_with(
            "BTCUSDT", 65000.0, "up", "linear", "пробой"
        )
        reply_text = update.message.reply_text
        assert reply_text.await_count == 1
        text_sent = reply_text.await_args.args[0]
        assert "✅" in text_sent and "Добавлено" in text_sent
        assert "BTCUSDT" in text_sent

    async def test_no_note(self, handlers, alerts_manager_mock):
        update = _make_update("btcusdt 3200 down")
        await handlers.handle_text(update, None)

        alerts_manager_mock.add_alert.assert_called_once_with(
            "BTCUSDT", 3200.0, "down", "linear", ""
        )

    async def test_symbol_normalized_from_short(self, handlers, alerts_manager_mock):
        """btc → BTCUSDT."""
        update = _make_update("btc 100 up")
        await handlers.handle_text(update, None)

        args = alerts_manager_mock.add_alert.call_args.args
        assert args[0] == "BTCUSDT"

    async def test_spot_in_note_sets_category_spot(
        self, handlers, alerts_manager_mock
    ):
        update = _make_update("BTC 65000 up spot пробой")
        await handlers.handle_text(update, None)

        args = alerts_manager_mock.add_alert.call_args.args
        assert args[3] == "spot"
        assert args[4] == "spot пробой"

    async def test_spоt_cyrillic(self, handlers, alerts_manager_mock):
        update = _make_update("BTC 65000 up спот пробой")
        await handlers.handle_text(update, None)

        args = alerts_manager_mock.add_alert.call_args.args
        assert args[3] == "spot"

    async def test_invalid_line_does_not_call_add_alert(
        self, handlers, alerts_manager_mock
    ):
        update = _make_update("мусор")
        await handlers.handle_text(update, None)

        alerts_manager_mock.add_alert.assert_not_called()
        text_sent = update.message.reply_text.await_args.args[0]
        assert "❓" in text_sent or "Не понял" in text_sent

    async def test_usdc_symbol_does_not_call_add_alert(
        self, handlers, alerts_manager_mock
    ):
        update = _make_update("BTCUSDC 65000 up пробой")
        await handlers.handle_text(update, None)

        alerts_manager_mock.add_alert.assert_not_called()

    async def test_already_exists_message(
        self, handlers, alerts_manager_mock
    ):
        alerts_manager_mock.add_alert.return_value = (False, False)
        update = _make_update("BTC 65000 up")
        await handlers.handle_text(update, None)

        text_sent = update.message.reply_text.await_args.args[0]
        assert "Уже существует" in text_sent or "⚠️" in text_sent

    async def test_update_replaced_shows_updated(
        self, handlers, alerts_manager_mock
    ):
        alerts_manager_mock.add_alert.return_value = (True, True)
        update = _make_update("BTC 65000 up")
        await handlers.handle_text(update, None)

        text_sent = update.message.reply_text.await_args.args[0]
        assert "Обновлено" in text_sent

    async def test_any_direction_accepted(self, handlers, alerts_manager_mock):
        update = _make_update("BTC 65000 any")
        await handlers.handle_text(update, None)

        args = alerts_manager_mock.add_alert.call_args.args
        assert args[2] == "any"


# ==================== B. МАССОВОЕ ДОБАВЛЕНИЕ ====================

class TestBulkAdd:
    async def test_three_valid_lines(
        self, handlers, alerts_manager_mock
    ):
        text = "\n".join([
            "BTC 65000 up пробой",
            "ETH 3200 down ретест",
            "SOL 150 up",
        ])
        update = _make_update(text)
        await handlers.handle_text(update, None)

        assert alerts_manager_mock.add_alert.call_count == 3
        calls = [c.args for c in alerts_manager_mock.add_alert.call_args_list]
        assert calls[0] == ("BTCUSDT", 65000.0, "up", "linear", "пробой")
        assert calls[1] == ("ETHUSDT", 3200.0, "down", "linear", "ретест")
        assert calls[2] == ("SOLUSDT", 150.0, "up", "linear", "")

        text_sent = update.message.reply_text.await_args.args[0]
        assert "Добавлено: <b>3</b>" in text_sent
        assert "Пропущено" not in text_sent

    async def test_mixed_valid_and_invalid(
        self, handlers, alerts_manager_mock
    ):
        text = "\n".join([
            "BTC 65000 up пробой",
            "мусор",
            "ETH 3200 down ретест",
            "BTCUSDC 100 up",
        ])
        update = _make_update(text)
        await handlers.handle_text(update, None)

        assert alerts_manager_mock.add_alert.call_count == 2
        text_sent = update.message.reply_text.await_args.args[0]
        assert "Добавлено: <b>2</b>" in text_sent
        assert "Пропущено: <b>2</b>" in text_sent
        # В отчёте — короткие причины
        assert "формат" in text_sent
        assert "не USDT" in text_sent

    async def test_all_invalid(self, handlers, alerts_manager_mock):
        text = "\n".join(["мусор", "ещё мусор"])
        update = _make_update(text)
        await handlers.handle_text(update, None)

        alerts_manager_mock.add_alert.assert_not_called()
        text_sent = update.message.reply_text.await_args.args[0]
        assert "Добавлено: <b>0</b>" in text_sent
        assert "Пропущено: <b>2</b>" in text_sent

    async def test_replaced_line_reports_updated(
        self, handlers, alerts_manager_mock
    ):
        # Первый add_alert → заменено, второй → новое
        alerts_manager_mock.add_alert.side_effect = [
            (True, True),
            (True, False),
        ]
        text = "\n".join([
            "BTC 65000 up",
            "ETH 3200 down",
        ])
        update = _make_update(text)
        await handlers.handle_text(update, None)

        text_sent = update.message.reply_text.await_args.args[0]
        assert "Добавлено: <b>1</b>" in text_sent
        assert "Обновлено: <b>1</b>" in text_sent

    async def test_save_failure_in_failed_details(
        self, handlers, alerts_manager_mock
    ):
        alerts_manager_mock.add_alert.return_value = (False, False)
        text = "\n".join(["BTC 65000 up", "ETH 3200 down"])
        update = _make_update(text)
        await handlers.handle_text(update, None)

        text_sent = update.message.reply_text.await_args.args[0]
        assert "Добавлено: <b>0</b>" in text_sent
        assert "не сохранилось" in text_sent

    async def test_more_than_five_skips_shows_only_five(
        self, handlers, alerts_manager_mock
    ):
        """failed_details[:5] — в отчёте максимум 5 строк пропусков."""
        text = "\n".join(["мусор"] * 7)
        update = _make_update(text)
        await handlers.handle_text(update, None)

        text_sent = update.message.reply_text.await_args.args[0]
        assert "Пропущено: <b>7</b>" in text_sent
        # Проверяем, что упомянуто ровно 5 строк (по "строка N:")
        assert text_sent.count("• строка ") == 5

    async def test_blank_lines_are_separators(
        self, handlers, alerts_manager_mock
    ):
        text = "\n".join([
            "BTC 65000 up",
            "",
            "ETH 3200 down",
        ])
        update = _make_update(text)
        await handlers.handle_text(update, None)

        assert alerts_manager_mock.add_alert.call_count == 2
        text_sent = update.message.reply_text.await_args.args[0]
        assert "Добавлено: <b>2</b>" in text_sent
        assert "Пропущено" not in text_sent


# ==================== C. РЕГРЕССИЯ ФОРМАТА ====================

class TestRealFormatRegression:
    async def test_full_aave_line_from_user(
        self, handlers, alerts_manager_mock
    ):
        line = (
            "AAVEUSDT 160.00 up отбой от поддержки | "
            "Вход: 160.20–160.80 | Стоп: 158.00 | Тейк: 166.50 (RR ~1:3)"
        )
        update = _make_update(line)
        await handlers.handle_text(update, None)

        args = alerts_manager_mock.add_alert.call_args.args
        assert args[0] == "AAVEUSDT"
        assert args[1] == 160.0
        assert args[2] == "up"
        assert args[3] == "linear"
        assert args[4] == (
            "отбой от поддержки | Вход: 160.20–160.80 | "
            "Стоп: 158.00 | Тейк: 166.50 (RR ~1:3)"
        )

    async def test_leading_pipe_stripped(self, handlers, alerts_manager_mock):
        update = _make_update("BTC 65000 up | пробой")
        await handlers.handle_text(update, None)

        args = alerts_manager_mock.add_alert.call_args.args
        assert args[4] == "пробой"


# ==================== D. ИЗОЛЯЦИЯ ====================

class TestIsolation:
    async def test_wrong_chat_id_ignored(
        self, handlers, alerts_manager_mock
    ):
        update = _make_update("BTC 65000 up", chat_id=99999)
        await handlers.handle_text(update, None)

        alerts_manager_mock.add_alert.assert_not_called()
        update.message.reply_text.assert_not_awaited()