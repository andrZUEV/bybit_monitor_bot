"""Тесты TelegramBot (Этап 11): сборка приложения, запуск, отправка."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.error import TimedOut

from src.telegram import bot as bot_mod
from src.telegram.bot import TelegramBot


def _make_bot() -> TelegramBot:
    return TelegramBot(
        token="123:TEST",
        allowed_chat_id="42",
        alerts_manager=MagicMock(),
        bybit_client=MagicMock(),
    )


class _FakeResponse:
    def __init__(self, status_code: int, payload, json_error: bool = False):
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("not json")
        return self._payload


@pytest.fixture
def no_sleep(monkeypatch):
    calls: list[float] = []
    monkeypatch.setattr(bot_mod.time, "sleep", lambda seconds: calls.append(seconds))
    return calls


class TestBuildApplication:
    def test_registers_handlers(self):
        bot = _make_bot()
        app = MagicMock()

        with patch.object(bot_mod.Application, "builder") as builder:
            builder.return_value.token.return_value.build.return_value = app
            built = bot._build_application()

        assert built is app
        # 5 команд + callback + текст = 7 хендлеров
        assert app.add_handler.call_count == 7


@pytest.mark.asyncio
class TestRunAsync:
    async def test_initializes_and_stops(self):
        bot = _make_bot()
        app = MagicMock()
        app.initialize = AsyncMock()
        app.start = AsyncMock()
        app.stop = AsyncMock()
        app.shutdown = AsyncMock()
        app.updater = MagicMock()
        app.updater.start_polling = AsyncMock()
        app.updater.running = False
        app.updater.stop = AsyncMock()
        bot._application = app
        bot._running = False

        await bot._run_async()

        app.initialize.assert_awaited_once()
        app.start.assert_awaited_once()
        app.updater.start_polling.assert_awaited_once()
        app.stop.assert_awaited_once()
        app.shutdown.assert_awaited_once()

    async def test_retries_on_timed_out(self, monkeypatch):
        sleep_mock = AsyncMock()
        monkeypatch.setattr("src.telegram.bot.asyncio.sleep", sleep_mock)

        bot = _make_bot()
        app = MagicMock()
        app.initialize = AsyncMock(side_effect=[TimedOut(), TimedOut(), None])
        app.start = AsyncMock()
        app.stop = AsyncMock()
        app.shutdown = AsyncMock()
        app.updater = MagicMock()
        app.updater.start_polling = AsyncMock()
        app.updater.running = False
        app.updater.stop = AsyncMock()
        bot._application = app
        bot._running = False

        await bot._run_async()

        assert app.initialize.await_count == 3
        assert sleep_mock.await_count == 2

    async def test_raises_after_three_timed_out(self, monkeypatch):
        monkeypatch.setattr("src.telegram.bot.asyncio.sleep", AsyncMock())

        bot = _make_bot()
        app = MagicMock()
        app.initialize = AsyncMock(side_effect=[TimedOut(), TimedOut(), TimedOut()])
        bot._application = app
        bot._running = False

        with pytest.raises(TimedOut):
            await bot._run_async()

    async def test_stops_updater_when_running(self, monkeypatch):
        monkeypatch.setattr("src.telegram.bot.asyncio.sleep", AsyncMock())

        bot = _make_bot()
        app = MagicMock()
        app.initialize = AsyncMock()
        app.start = AsyncMock()
        app.stop = AsyncMock()
        app.shutdown = AsyncMock()
        app.updater = MagicMock()
        app.updater.start_polling = AsyncMock()
        app.updater.running = True
        app.updater.stop = AsyncMock()
        bot._application = app
        bot._running = False

        await bot._run_async()

        app.updater.stop.assert_awaited_once()



class TestStartAsyncStop:
    def test_start_async_starts_thread(self):
        bot = _make_bot()
        started = []

        def fake_start():
            started.append(True)

        bot.start = fake_start
        bot.start_async()
        bot._thread.join(timeout=2.0)

        assert started == [True]

    def test_stop_sets_running_false(self):
        bot = _make_bot()
        bot.stop()
        assert bot._running is False


class TestSendAlert:
    def test_success_first_try(self, monkeypatch):
        bot = _make_bot()
        monkeypatch.setattr(
            bot_mod.requests, "post",
            lambda *a, **k: _FakeResponse(200, {"ok": True}),
        )

        assert bot.send_alert("привет") is True

    def test_non_json_then_success(self, monkeypatch, no_sleep):
        bot = _make_bot()
        responses = [
            _FakeResponse(502, None, json_error=True),
            _FakeResponse(200, {"ok": True}),
        ]
        monkeypatch.setattr(bot_mod.requests, "post", lambda *a, **k: responses.pop(0))

        assert bot.send_alert("текст") is True
        assert no_sleep == [1.0]

    def test_non_json_twice_returns_false(self, monkeypatch, no_sleep):
        bot = _make_bot()
        responses = [
            _FakeResponse(502, None, json_error=True),
            _FakeResponse(502, None, json_error=True),
        ]
        monkeypatch.setattr(bot_mod.requests, "post", lambda *a, **k: responses.pop(0))

        assert bot.send_alert("текст") is False

    def test_429_twice_returns_false(self, monkeypatch, no_sleep):
        bot = _make_bot()
        payload = {"ok": False, "parameters": {"retry_after": 5}}
        responses = [
            _FakeResponse(429, payload),
            _FakeResponse(429, payload),
        ]
        monkeypatch.setattr(bot_mod.requests, "post", lambda *a, **k: responses.pop(0))

        assert bot.send_alert("текст") is False
        # ждём не больше 2 секунд даже при retry_after=5
        assert no_sleep == [2]

    def test_429_with_broken_retry_after(self, monkeypatch, no_sleep):
        bot = _make_bot()
        payload = {"ok": False, "parameters": {"retry_after": "abc"}}
        responses = [
            _FakeResponse(429, payload),
            _FakeResponse(200, {"ok": True}),
        ]
        monkeypatch.setattr(bot_mod.requests, "post", lambda *a, **k: responses.pop(0))

        assert bot.send_alert("текст") is True
        assert no_sleep == [1]

    def test_400_not_retried(self, monkeypatch, no_sleep):
        bot = _make_bot()
        calls = []

        def post(*args, **kwargs):
            calls.append(1)
            return _FakeResponse(400, {"ok": False, "description": "Bad Request"})

        monkeypatch.setattr(bot_mod.requests, "post", post)

        assert bot.send_alert("текст") is False
        assert len(calls) == 1
        assert no_sleep == []

    def test_network_error_twice(self, monkeypatch, no_sleep):
        bot = _make_bot()

        def post(*args, **kwargs):
            raise bot_mod.requests.exceptions.ConnectionError("нет сети")

        monkeypatch.setattr(bot_mod.requests, "post", post)

        assert bot.send_alert("текст") is False
        assert no_sleep == [1.0]

    def test_unexpected_exception_returns_false(self, monkeypatch, no_sleep):
        bot = _make_bot()

        def post(*args, **kwargs):
            raise RuntimeError("что-то странное")

        monkeypatch.setattr(bot_mod.requests, "post", post)

        assert bot.send_alert("текст") is False

    def test_reply_markup_inline_serialized(self, monkeypatch):
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        bot = _make_bot()
        captured = {}

        def post(url, data=None, timeout=None):
            captured.update(data)
            return _FakeResponse(200, {"ok": True})

        monkeypatch.setattr(bot_mod.requests, "post", post)
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("кнопка", callback_data="x")]])

        assert bot.send_alert("текст", reply_markup=markup) is True
        payload = json.loads(captured["reply_markup"])
        assert payload["inline_keyboard"][0][0]["callback_data"] == "x"

    def test_reply_markup_dict_serialized(self, monkeypatch):
        bot = _make_bot()
        captured = {}

        def post(url, data=None, timeout=None):
            captured.update(data)
            return _FakeResponse(200, {"ok": True})

        monkeypatch.setattr(bot_mod.requests, "post", post)
        raw = {"inline_keyboard": []}

        assert bot.send_alert("текст", reply_markup=raw) is True
        assert json.loads(captured["reply_markup"]) == raw

    def test_payload_fields(self, monkeypatch):
        bot = _make_bot()
        captured = {}

        def post(url, data=None, timeout=None):
            captured.update(data)
            captured["url"] = url
            return _FakeResponse(200, {"ok": True})

        monkeypatch.setattr(bot_mod.requests, "post", post)

        bot.send_alert("текст")

        assert captured["chat_id"] == "42"
        assert captured["parse_mode"] == "HTML"
        assert captured["disable_web_page_preview"] is True
        assert captured["url"].endswith("/bot123:TEST/sendMessage")
