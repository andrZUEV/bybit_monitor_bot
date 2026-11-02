"""Тесты: отправка в Telegram с двумя попытками и без утечки токена в лог."""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
import requests

from src.telegram.bot import TelegramBot

TOKEN = "123456:SECRET-TOKEN"


def _make_bot() -> TelegramBot:
    """TelegramBot без запуска Application: send_alert нужны только token/chat_id."""
    bot = object.__new__(TelegramBot)
    bot.token = TOKEN
    bot.allowed_chat_id = "42"
    return bot


def _ok_response() -> SimpleNamespace:
    return SimpleNamespace(status_code=200, json=lambda: {"ok": True})


def _error_response(status: int = 400, description: str = "Bad Request") -> SimpleNamespace:
    return SimpleNamespace(
        status_code=status,
        json=lambda: {"ok": False, "description": description},
    )


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Не ждём реальные 1–2 секунды между попытками."""
    sleeps: list[float] = []
    monkeypatch.setattr(
        "src.telegram.bot.time.sleep", lambda s: sleeps.append(s)
    )
    return sleeps


class TestSendAlertRetries:
    def test_success_on_second_attempt(self, monkeypatch):
        calls = []

        def fake_post(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise requests.ConnectionError("network down")
            return _ok_response()

        monkeypatch.setattr("src.telegram.bot.requests.post", fake_post)
        assert _make_bot().send_alert("hi") is True
        assert len(calls) == 2

    def test_both_attempts_fail(self, monkeypatch):
        calls = []

        def fake_post(*args, **kwargs):
            calls.append(1)
            raise requests.ConnectionError("network down")

        monkeypatch.setattr("src.telegram.bot.requests.post", fake_post)
        assert _make_bot().send_alert("hi") is False
        assert len(calls) == 2

    def test_400_is_not_retried(self, monkeypatch):
        calls = []

        def fake_post(*args, **kwargs):
            calls.append(1)
            return _error_response(400, "can't parse entities")

        monkeypatch.setattr("src.telegram.bot.requests.post", fake_post)
        assert _make_bot().send_alert("hi") is False
        assert len(calls) == 1

    def test_429_then_success(self, monkeypatch, no_sleep):
        calls = []

        def fake_post(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                return SimpleNamespace(
                    status_code=429,
                    json=lambda: {
                        "ok": False,
                        "description": "Too Many Requests",
                        "parameters": {"retry_after": 1},
                    },
                )
            return _ok_response()

        monkeypatch.setattr("src.telegram.bot.requests.post", fake_post)
        assert _make_bot().send_alert("hi") is True
        assert len(calls) == 2
        assert no_sleep, "должна быть пауза перед повтором"

    def test_token_not_logged_on_network_error(self, monkeypatch, caplog):
        def fake_post(*args, **kwargs):
            raise requests.ConnectionError(f"failed for {TOKEN}")

        monkeypatch.setattr("src.telegram.bot.requests.post", fake_post)
        with caplog.at_level(logging.ERROR):
            _make_bot().send_alert("hi")

        assert TOKEN not in caplog.text
