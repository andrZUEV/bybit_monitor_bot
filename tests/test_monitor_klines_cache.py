"""
Тесты кэша klines в Monitor (Этап 4.5.2).
Не дёргаем Bybit — используем фейковый BybitClient.
"""
from __future__ import annotations

import time

from src.core.monitor import Monitor

# ==================== helpers ====================

class _FakeBybitClient:
    """Считает вызовы get_klines. Возвращает предсказуемые данные."""

    def __init__(self):
        self.calls: list[tuple[str, str, str, int]] = []
        self.response: dict[str, list[list]] = {}

    def get_klines(self, symbol, category, interval, limit):
        self.calls.append((symbol, category, interval, limit))
        return self.response.get(interval)

    def get_candle_volume_ratio(self, symbol, category, interval, periods):
        return {"ratio": 1.0}

    def get_ticker(self, symbol, category):
        return None


def _make_monitor() -> Monitor:
    """Monitor с фейковым клиентом, без WS, без alerts_manager."""
    class _FakeAlerts:
        def get_all_alerts(self):
            return []

        def load(self):
            pass

    m = Monitor(
        alerts_manager=_FakeAlerts(),
        on_alert_callback=lambda e: None,
        bybit_client=_FakeBybitClient(),
        use_websocket=False,
    )
    return m


# ==================== tests ====================

def test_1h_cache_hit_within_ttl():
    m = _make_monitor()
    m.client.response["60"] = [[0, "100", "110", "90", "105", "1"]]

    r1 = m._get_klines_cached("BTCUSDT", "linear", "60", 300, ttl=3600)
    r2 = m._get_klines_cached("BTCUSDT", "linear", "60", 300, ttl=3600)

    assert r1 == r2
    # Два вызова → один REST-запрос (второй из кэша)
    calls = [c for c in m.client.calls if c[2] == "60"]
    assert len(calls) == 1


def test_1d_cache_separate_from_1h():
    m = _make_monitor()
    m.client.response["60"] = [[0, "1", "1", "1", "1", "1"]]
    m.client.response["D"] = [[0, "2", "2", "2", "2", "2"]]

    m._get_klines_cached("BTCUSDT", "linear", "60", 300, ttl=3600)
    m._get_klines_cached("BTCUSDT", "linear", "D", 200, ttl=86400)

    calls_60 = [c for c in m.client.calls if c[2] == "60"]
    calls_d = [c for c in m.client.calls if c[2] == "D"]
    assert len(calls_60) == 1
    assert len(calls_d) == 1


def test_cache_expires_after_ttl():
    m = _make_monitor()
    m.client.response["60"] = [[0, "100", "110", "90", "105", "1"]]

    m._get_klines_cached("BTCUSDT", "linear", "60", 300, ttl=0.05)
    time.sleep(0.1)
    m._get_klines_cached("BTCUSDT", "linear", "60", 300, ttl=0.05)

    calls = [c for c in m.client.calls if c[2] == "60"]
    assert len(calls) == 2


def test_empty_response_not_cached():
    m = _make_monitor()
    # response["60"] не задан → get_klines вернёт None
    m._get_klines_cached("BTCUSDT", "linear", "60", 300, ttl=3600)
    m._get_klines_cached("BTCUSDT", "linear", "60", 300, ttl=3600)

    calls = [c for c in m.client.calls if c[2] == "60"]
    # Оба раза идём в REST, потому что None не кэшируется
    assert len(calls) == 2


def test_different_symbols_separate_cache():
    m = _make_monitor()
    m.client.response["60"] = [[0, "1", "1", "1", "1", "1"]]

    m._get_klines_cached("BTCUSDT", "linear", "60", 300, ttl=3600)
    m._get_klines_cached("ETHUSDT", "linear", "60", 300, ttl=3600)

    btc_calls = [c for c in m.client.calls if c[0] == "BTCUSDT"]
    eth_calls = [c for c in m.client.calls if c[0] == "ETHUSDT"]
    assert len(btc_calls) == 1
    assert len(eth_calls) == 1


def test_unknown_interval_no_cache():
    m = _make_monitor()
    m.client.response["5"] = [[0, "1", "1", "1", "1", "1"]]

    m._get_klines_cached("BTCUSDT", "linear", "5", 100, ttl=3600)
    m._get_klines_cached("BTCUSDT", "linear", "5", 100, ttl=3600)

    calls = [c for c in m.client.calls if c[2] == "5"]
    # Без кэша — оба раза в REST
    assert len(calls) == 2