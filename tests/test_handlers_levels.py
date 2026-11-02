"""Тесты UI сканера уровней (Этап 11): меню, кэш, добавление уровней."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.alerts import AlertRule, Asset
from src.core.settings import RuntimeSettings
from src.telegram import handlers_levels as lvl_mod
from src.telegram.handlers import TelegramHandlers

pytestmark = pytest.mark.asyncio

CHAT_ID = 12345


def _candidate(price: float, direction: str = "up", note: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        price=price, direction_for_alert=direction, note=note,
    )


def _query() -> SimpleNamespace:
    return SimpleNamespace(
        edit_message_text=AsyncMock(),
        answer=AsyncMock(),
        message=SimpleNamespace(reply_text=AsyncMock()),
    )


@pytest.fixture
def handlers() -> TelegramHandlers:
    manager = MagicMock()
    manager.get_all_alerts.return_value = []
    manager.add_alert.return_value = (True, False)
    h = TelegramHandlers(
        alerts_manager=manager,
        allowed_chat_id=str(CHAT_ID),
        bybit_client=MagicMock(),
    )
    h._level_scan_cache = {}
    return h


@pytest.fixture
def settings(monkeypatch) -> RuntimeSettings:
    s = RuntimeSettings()
    monkeypatch.setattr(lvl_mod, "get_settings", lambda: s)
    return s


class TestMenu:
    async def test_menu_shows_state(self, handlers, settings):
        query = _query()

        await handlers._show_level_scan_menu(query)

        text = query.edit_message_text.await_args.args[0]
        markup = query.edit_message_text.await_args.kwargs["reply_markup"]
        callbacks = [
            b.callback_data for row in markup.inline_keyboard for b in row
        ]
        assert "Сканер уровней" in text
        assert "ls_scan_now" in callbacks


class TestRunScan:
    async def test_uses_cache(self, handlers, settings):
        handlers._level_scan_cache = {
            "data": ("summary", "full", {}, {}, "compact_kb", "full_kb"),
            "ts": __import__("time").time(),
        }
        query = _query()

        await handlers._run_level_scan(query)

        query.edit_message_text.assert_awaited_once_with(
            "summary", parse_mode="HTML", reply_markup="compact_kb",
        )

    async def test_force_refresh_bypasses_cache(self, handlers, settings, monkeypatch):
        handlers._level_scan_cache = {
            "data": ("old", "old", {}, {}, "kb", "kb"),
            "ts": __import__("time").time(),
        }
        handlers.alerts_manager.get_all_alerts.return_value = [
            Asset(symbol="BTCUSDT", category="linear", alerts=[]),
        ]
        monkeypatch.setattr(
            lvl_mod.level_scanner, "scan_and_format",
            lambda *a, **k: ("new", "full", {"BTCUSDT": []}, {}, "kb", "kb"),
        )
        query = _query()

        await handlers._run_level_scan(query, force_refresh=True)

        assert query.edit_message_text.await_args.args[0] == "new"

    async def test_no_symbols(self, handlers, settings):
        query = _query()

        await handlers._run_level_scan(query)

        text = query.edit_message_text.await_args.args[0]
        assert "Нет отслеживаемых активов" in text

    async def test_scan_error_is_reported(self, handlers, settings, monkeypatch):
        handlers.alerts_manager.get_all_alerts.return_value = [
            Asset(symbol="BTCUSDT", category="linear", alerts=[]),
        ]

        def boom(*args, **kwargs):
            raise RuntimeError("api недоступен")

        monkeypatch.setattr(lvl_mod.level_scanner, "scan_and_format", boom)
        query = _query()

        await handlers._run_level_scan(query)

        text = query.edit_message_text.await_args.args[0]
        assert "Ошибка скана" in text
        assert "api недоступен" in text

    async def test_scan_caches_result(self, handlers, settings, monkeypatch):
        handlers.alerts_manager.get_all_alerts.return_value = [
            Asset(symbol="BTCUSDT", category="linear", alerts=[]),
        ]
        results = {"BTCUSDT": [_candidate(65000.0)]}
        monkeypatch.setattr(
            lvl_mod.level_scanner, "scan_and_format",
            lambda *a, **k: ("summary", "full", results, {}, "kb", "kb"),
        )
        query = _query()

        await handlers._run_level_scan(query)

        assert handlers._level_scan_cache["data"][2] == results
        assert handlers._last_level_scan_results == results


class TestShowAllCompact:
    async def test_all_without_cache(self, handlers):
        query = _query()
        await handlers._show_level_scan_all(query)
        query.answer.assert_awaited_with("Сначала запустите скан", show_alert=True)

    async def test_all_without_results(self, handlers):
        handlers._level_scan_cache = {"data": ("s", "f", {}, {}, "kb", "fkb")}
        query = _query()

        await handlers._show_level_scan_all(query)

        query.answer.assert_awaited_with("Нет уровней", show_alert=True)

    async def test_all_shows_full(self, handlers):
        handlers._level_scan_cache = {
            "data": ("s", "full text", {"BTCUSDT": []}, {}, "kb", "full_kb"),
        }
        query = _query()

        await handlers._show_level_scan_all(query)

        query.edit_message_text.assert_awaited_once_with(
            "full text", parse_mode="HTML", reply_markup="full_kb",
        )

    async def test_compact_without_cache(self, handlers):
        query = _query()
        await handlers._show_level_scan_compact(query)
        query.answer.assert_awaited_with("Сначала запустите скан", show_alert=True)

    async def test_compact_shows_summary(self, handlers):
        handlers._level_scan_cache = {
            "data": ("summary", "full", {}, {}, "compact_kb", "fkb"),
        }
        query = _query()

        await handlers._show_level_scan_compact(query)

        query.edit_message_text.assert_awaited_once_with(
            "summary", parse_mode="HTML", reply_markup="compact_kb",
        )


class TestAddSingle:
    async def test_bad_format(self, handlers):
        query = _query()
        await handlers._handle_level_scan_add(query, "ls_add|BTCUSDT")
        query.answer.assert_awaited_with("Ошибка формата", show_alert=True)

    async def test_bad_price(self, handlers):
        query = _query()
        await handlers._handle_level_scan_add(query, "ls_add|BTCUSDT|abc|up")
        query.answer.assert_awaited_with("Ошибка цены", show_alert=True)

    async def test_existing_level_warns(self, handlers):
        handlers.alerts_manager.get_all_alerts.return_value = [
            Asset(symbol="BTCUSDT", category="linear", alerts=[
                AlertRule(price=65000.0, direction="up"),
            ]),
        ]
        query = _query()

        await handlers._handle_level_scan_add(
            query, "ls_add|BTCUSDT|65000|up",
        )

        query.message.reply_text.assert_awaited_once()
        handlers.alerts_manager.add_alert.assert_not_called()

    async def test_adds_with_note_from_scan(self, handlers):
        handlers._level_scan_cache = {
            "data": ("s", "f", {"BTCUSDT": [_candidate(65000.0, "up", "сильный")]},
                     {}, "kb", "fkb"),
        }
        query = _query()

        await handlers._handle_level_scan_add(query, "ls_add|BTCUSDT|65000|up")

        handlers.alerts_manager.add_alert.assert_called_once_with(
            "BTCUSDT", 65000.0, "up", "linear", "сильный",
        )
        query.answer.assert_awaited()

    async def test_reports_replaced(self, handlers):
        handlers.alerts_manager.add_alert.return_value = (True, True)
        query = _query()

        await handlers._handle_level_scan_add(query, "ls_add|BTCUSDT|65000|up")

        query.answer.assert_awaited_with(
            "🔄 Обновлён: BTCUSDT @ 65000.0 up", show_alert=False,
        )

    async def test_reports_failure(self, handlers):
        handlers.alerts_manager.add_alert.return_value = (False, False)
        query = _query()

        await handlers._handle_level_scan_add(query, "ls_add|BTCUSDT|65000|up")

        query.answer.assert_awaited_with("❌ Не удалось сохранить", show_alert=True)


class TestAddSymbol:
    async def test_without_cache(self, handlers):
        query = _query()
        await handlers._handle_level_scan_add_symbol(query, "BTCUSDT")
        query.answer.assert_awaited_with("Сначала запустите скан", show_alert=True)

    async def test_symbol_without_levels(self, handlers):
        handlers._level_scan_cache = {"data": ("s", "f", {}, {}, "kb", "fkb")}
        query = _query()

        await handlers._handle_level_scan_add_symbol(query, "BTCUSDT")

        query.answer.assert_awaited_with(
            "Нет уровней для BTCUSDT", show_alert=True,
        )

    async def test_adds_and_skips_existing(self, handlers):
        handlers._level_scan_cache = {
            "data": ("s", "f", {"BTCUSDT": [
                _candidate(65000.0), _candidate(66000.0),
            ]}, {}, "kb", "fkb"),
        }
        handlers.alerts_manager.get_all_alerts.return_value = [
            Asset(symbol="BTCUSDT", category="linear", alerts=[
                AlertRule(price=65000.0, direction="up"),
            ]),
        ]
        query = _query()

        await handlers._handle_level_scan_add_symbol(query, "BTCUSDT")

        text = query.message.reply_text.await_args.args[0]
        assert "добавлено 1" in text
        assert "пропущено 1" in text


class TestAddAll:
    async def test_without_cache(self, handlers):
        query = _query()
        await handlers._handle_level_scan_add_all(query)
        query.answer.assert_awaited_with("Сначала запустите скан", show_alert=True)

    async def test_without_results(self, handlers):
        handlers._level_scan_cache = {"data": ("s", "f", {}, {}, "kb", "fkb")}
        query = _query()

        await handlers._handle_level_scan_add_all(query)

        query.answer.assert_awaited_with("Нет уровней", show_alert=True)

    async def test_adds_all(self, handlers):
        handlers._level_scan_cache = {
            "data": ("s", "f", {
                "BTCUSDT": [_candidate(65000.0)],
                "ETHUSDT": [_candidate(3000.0, "down")],
            }, {}, "kb", "fkb"),
        }
        query = _query()

        await handlers._handle_level_scan_add_all(query)

        assert handlers.alerts_manager.add_alert.call_count == 2
        text = query.message.reply_text.await_args.args[0]
        assert "Добавлено уровней: <b>2</b>" in text


class TestLevelExists:
    async def test_exact_match(self, handlers):
        handlers.alerts_manager.get_all_alerts.return_value = [
            Asset(symbol="BTCUSDT", category="linear", alerts=[
                AlertRule(price=65000.0, direction="up"),
            ]),
        ]
        assert handlers._level_exists("BTCUSDT", 65000.0, "up") is True
        assert handlers._level_exists("BTCUSDT", 65000.0, "down") is False
        assert handlers._level_exists("ETHUSDT", 65000.0, "up") is False

    async def test_no_alerts(self, handlers):
        assert handlers._level_exists("BTCUSDT", 1.0, "up") is False


class TestScheduledScan:
    async def test_scan_all_and_send(self, handlers, monkeypatch):
        sent: list[tuple] = []
        handlers.set_telegram_sender(lambda text, kb: sent.append((text, kb)))
        handlers.bybit_client.get_ticker.return_value = SimpleNamespace(price=65000.0)
        monkeypatch.setattr(
            lvl_mod.level_scanner, "scan_all",
            lambda *a, **k: {"BTCUSDT": [_candidate(65000.0)]},
        )
        monkeypatch.setattr(lvl_mod.level_scanner, "format_summary", lambda *a: "summary")
        monkeypatch.setattr(lvl_mod.level_scanner, "build_keyboard", lambda *a: "kb")
        monkeypatch.setattr(lvl_mod.level_scanner, "format_full_list", lambda *a: "full")
        monkeypatch.setattr(lvl_mod.level_scanner, "build_full_keyboard", lambda *a: "fkb")

        handlers.run_scheduled_level_scan(
            ["BTCUSDT"], lambda symbol: "linear", 5.0,
        )

        assert sent == [("summary", "kb")]
        assert handlers._level_scan_cache["data"][0] == "summary"

    async def test_scan_without_results_sends_empty(self, handlers, monkeypatch):
        sent: list[tuple] = []
        handlers.set_telegram_sender(lambda text, kb: sent.append((text, kb)))
        monkeypatch.setattr(lvl_mod.level_scanner, "scan_all", lambda *a, **k: {})
        monkeypatch.setattr(lvl_mod.level_scanner, "format_summary", lambda *a: "s")
        monkeypatch.setattr(lvl_mod.level_scanner, "build_keyboard", lambda *a: "kb")
        monkeypatch.setattr(lvl_mod.level_scanner, "format_full_list", lambda *a: "f")
        monkeypatch.setattr(lvl_mod.level_scanner, "build_full_keyboard", lambda *a: "fkb")

        handlers.run_scheduled_level_scan([], lambda s: "linear", 5.0)

        assert "Пусто" in sent[0][0]

    async def test_scan_without_sender_logs(self, handlers, monkeypatch):
        handlers._telegram_send = None
        monkeypatch.setattr(lvl_mod.level_scanner, "scan_all", lambda *a, **k: {})
        monkeypatch.setattr(lvl_mod.level_scanner, "format_summary", lambda *a: "s")
        monkeypatch.setattr(lvl_mod.level_scanner, "build_keyboard", lambda *a: "kb")
        monkeypatch.setattr(lvl_mod.level_scanner, "format_full_list", lambda *a: "f")
        monkeypatch.setattr(lvl_mod.level_scanner, "build_full_keyboard", lambda *a: "fkb")

        handlers.run_scheduled_level_scan([], lambda s: "linear", 5.0)  # не бросает
