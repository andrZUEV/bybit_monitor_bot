"""Тесты WS-клиента Bybit (Этап 11): подписки, сообщения, реконнект.

Сеть не трогаем: websocket.WebSocket подменяется фейком, потоки — управляемые.
"""
from __future__ import annotations

import json
import threading

import pytest

from src.api import bybit_ws as ws_mod
from src.api.bybit_ws import BybitWebSocketClient


class FakeWS:
    """Минимальный двойник websocket.WebSocket."""

    def __init__(self, messages=None, connect_error: Exception | None = None):
        self.sent: list[str] = []
        self.messages = list(messages or [])
        self.connect_error = connect_error
        self.closed = False
        self.timeout = None
        self.connect_args = None

    def settimeout(self, timeout):
        self.timeout = timeout

    def connect(self, url, timeout=None):
        self.connect_args = (url, timeout)
        if self.connect_error:
            raise self.connect_error

    def send(self, payload: str):
        self.sent.append(payload)

    def recv(self):
        if self.messages:
            item = self.messages.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        raise ws_mod.websocket.WebSocketTimeoutException()

    def close(self):
        self.closed = True

    def payloads(self) -> list[dict]:
        return [json.loads(p) for p in self.sent]


@pytest.fixture
def client() -> BybitWebSocketClient:
    received: list[tuple] = []
    c = BybitWebSocketClient(
        url="wss://example.test/ws",
        on_ticker=lambda symbol, price, volume: received.append((symbol, price, volume)),
        ping_interval=20.0,
        reconnect_delay=0.01,
    )
    c.received = received
    return c


class TestSetSymbols:
    def test_normalizes_and_dedups(self, client):
        client.set_symbols(["btcusdt", " BTCUSDT ", "", "ethusdt"])

        assert client._desired == {"BTCUSDT", "ETHUSDT"}

    def test_empty_iterable(self, client):
        client.set_symbols([])
        assert client._desired == set()


class TestApplyDiff:
    def test_initial_subscribes_desired(self, client):
        client.set_symbols(["BTCUSDT"])
        ws = FakeWS()

        client._apply_diff(ws, initial=True)

        assert client._subscribed == {"BTCUSDT"}
        assert ws.payloads() == [
            {"op": "subscribe", "args": ["tickers.BTCUSDT"]},
        ]

    def test_initial_restores_previous_subscriptions(self, client):
        client._subscribed = {"ETHUSDT"}
        ws = FakeWS()

        client._apply_diff(ws, initial=True)

        assert client._subscribed == {"ETHUSDT"}

    def test_nothing_to_do(self, client):
        client.set_symbols(["BTCUSDT"])
        client._subscribed = {"BTCUSDT"}
        ws = FakeWS()

        client._apply_diff(ws)

        assert ws.sent == []

    def test_removes_missing_symbols(self, client):
        client.set_symbols(["ETHUSDT"])
        client._subscribed = {"BTCUSDT", "ETHUSDT"}
        ws = FakeWS()

        client._apply_diff(ws)

        assert client._subscribed == {"ETHUSDT"}
        assert ws.payloads()[0]["op"] == "unsubscribe"

    def test_chunks_ten_at_a_time(self, client):
        symbols = [f"S{i}USDT" for i in range(12)]
        client.set_symbols(symbols)
        ws = FakeWS()

        client._apply_diff(ws)

        ops = ws.payloads()
        assert [len(p["args"]) for p in ops] == [10, 2]
        assert all(p["op"] == "subscribe" for p in ops)

    def test_chunks_helper(self, client):
        assert list(client._chunks([1, 2, 3, 4, 5], 2)) == [[1, 2], [3, 4], [5]]


class TestSendJson:
    def test_send_error_is_logged_not_raised(self, client):
        class Broken(FakeWS):
            def send(self, payload):
                raise OSError("сокет закрыт")

        client._send_json(Broken(), {"op": "ping"})  # не бросает

    def test_ping_payload(self, client):
        ws = FakeWS()
        client._send_ping(ws)
        assert ws.payloads() == [{"op": "ping"}]


class TestHandleMessage:
    def test_not_json_is_ignored(self, client):
        client._handle_message("не json")
        assert client.received == []

    def test_pong_is_ignored(self, client):
        client._handle_message(json.dumps({"op": "pong"}))
        assert client.received == []

    def test_subscribe_failure_is_logged(self, client):
        client._handle_message(json.dumps({
            "op": "subscribe", "success": False, "ret_msg": "bad arg",
        }))
        assert client.received == []

    def test_server_ping_answered_with_pong(self, client):
        ws = FakeWS()
        client._ws = ws

        client._handle_message(json.dumps({"op": "ping"}))

        assert ws.payloads() == [{"op": "pong"}]
        client._ws = None

    def test_unknown_op_ignored(self, client):
        client._handle_message(json.dumps({"op": "something"}))

    def test_non_ticker_topic_ignored(self, client):
        client._handle_message(json.dumps({
            "topic": "orderbook.1.BTCUSDT", "data": {"lastPrice": "100"},
        }))
        assert client.received == []

    def test_ticker_parsed(self, client):
        client._handle_message(json.dumps({
            "topic": "tickers.BTCUSDT",
            "data": {"symbol": "BTCUSDT", "lastPrice": "65000.5", "volume24h": "123.4"},
        }))

        assert client.received == [("BTCUSDT", 65000.5, 123.4)]

    def test_ticker_without_symbol_uses_topic(self, client):
        client._handle_message(json.dumps({
            "topic": "tickers.ETHUSDT",
            "data": {"lastPrice": "3000"},
        }))

        assert client.received == [("ETHUSDT", 3000.0, 0.0)]

    def test_ticker_without_price_ignored(self, client):
        client._handle_message(json.dumps({
            "topic": "tickers.BTCUSDT", "data": {"volume24h": "1"},
        }))
        assert client.received == []

    def test_broken_numbers_ignored(self, client):
        client._handle_message(json.dumps({
            "topic": "tickers.BTCUSDT",
            "data": {"lastPrice": "abc", "volume24h": "def"},
        }))
        assert client.received == []

    def test_callback_error_does_not_raise(self, client):
        def boom(*args):
            raise RuntimeError("колбэк упал")

        client.on_ticker = boom

        client._handle_message(json.dumps({
            "topic": "tickers.BTCUSDT",
            "data": {"lastPrice": "1", "volume24h": "1"},
        }))


class TestConnectAndListen:
    def test_session_subscribes_and_handles_messages(self, client, monkeypatch):
        client.set_symbols(["BTCUSDT"])
        ws = FakeWS(messages=[json.dumps({
            "topic": "tickers.BTCUSDT",
            "data": {"lastPrice": "100", "volume24h": "5"},
        })])
        monkeypatch.setattr(ws_mod.websocket, "WebSocket", lambda: ws)

        # Останавливаем цикл после первого сообщения.
        original_handle = client._handle_message

        def handle_then_stop(raw):
            original_handle(raw)
            client._stop_event.set()

        client._handle_message = handle_then_stop

        client._connect_and_listen()

        assert client.received == [("BTCUSDT", 100.0, 5.0)]
        assert ws.closed is True
        assert client._ws is None

    def test_connection_closed_breaks_loop(self, client, monkeypatch):
        ws = FakeWS(messages=[ws_mod.websocket.WebSocketConnectionClosedException()])
        monkeypatch.setattr(ws_mod.websocket, "WebSocket", lambda: ws)

        client._connect_and_listen()

        assert ws.closed is True

    def test_ping_sent_on_interval(self, client, monkeypatch):
        ws = FakeWS()
        monkeypatch.setattr(ws_mod.websocket, "WebSocket", lambda: ws)
        client.ping_interval = 0.0

        ticks = {"n": 0}

        def handle(raw):
            ticks["n"] += 1
            client._stop_event.set()

        client._handle_message = handle

        # Первый recv даёт TimeoutException → continue; на следующем круге
        # ping_interval=0 → пинг уходит. Ограничим цикл вторым таймаутом.
        original_recv = ws.recv
        state = {"calls": 0}

        def recv():
            state["calls"] += 1
            if state["calls"] > 3:
                client._stop_event.set()
            return original_recv()

        ws.recv = recv

        client._connect_and_listen()

        ops = [p["op"] for p in ws.payloads()]
        assert "ping" in ops


class TestRunForever:
    def test_reconnects_with_backoff(self, client, monkeypatch):
        attempts = {"n": 0}

        def connect():
            attempts["n"] += 1
            if attempts["n"] >= 2:
                client._stop_event.set()
            raise OSError("сеть недоступна")

        monkeypatch.setattr(client, "_connect_and_listen", connect)

        client._run_forever()

        assert attempts["n"] >= 2

    def test_stop_breaks_loop(self, client, monkeypatch):
        def connect():
            client._stop_event.set()

        monkeypatch.setattr(client, "_connect_and_listen", connect)
        client._run_forever()
        assert client._stop_event.is_set()


class TestStartStop:
    def test_start_stop_thread(self, client, monkeypatch):
        started = threading.Event()

        def run_forever():
            started.set()
            client._stop_event.wait(0.05)

        monkeypatch.setattr(client, "_run_forever", run_forever)

        client.start()
        assert started.wait(2.0)
        assert client._thread.is_alive()

        client.start()  # второй запуск — предупреждение, поток тот же
        client.stop()
        assert not client._thread.is_alive()

    def test_stop_closes_socket(self, client):
        ws = FakeWS()
        client._ws = ws

        client.stop()

        assert ws.closed is True
        client._ws = None

    def test_stop_survives_broken_socket(self, client):
        class Broken(FakeWS):
            def close(self):
                raise OSError("уже закрыт")

        client._ws = Broken()
        client.stop()  # не бросает
        client._ws = None
