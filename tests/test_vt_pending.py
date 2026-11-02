"""Тесты коротких id для кнопки «Взять в дневник»."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core import vt_pending
from src.telegram.handlers_vt import VirtualTradeMixin


@pytest.fixture(autouse=True)
def clean_registry():
    vt_pending.clear_vt_take()
    yield
    vt_pending.clear_vt_take()


class TestRegistry:
    def test_register_and_pop(self):
        key = vt_pending.register_vt_take(
            "BTCUSDT", 100.0, 95.0, 110.0, None, "up",
        )
        assert len(key) < 16

        payload = vt_pending.pop_vt_take(key)
        assert payload == ("BTCUSDT", 100.0, 95.0, 110.0, None, "up")
        assert vt_pending.pop_vt_take(key) is None

    def test_ids_are_unique(self):
        keys = {
            vt_pending.register_vt_take("BTCUSDT", 100.0 + i, 95.0, 110.0, None, "up")
            for i in range(50)
        }
        assert len(keys) == 50

    def test_old_entries_are_evicted(self):
        first = vt_pending.register_vt_take(
            "BTCUSDT", 1.0, 0.9, 1.2, None, "up",
        )
        for i in range(vt_pending.MAX_PENDING):
            vt_pending.register_vt_take(
                "ETHUSDT", 2.0 + i, 1.9, 2.2, None, "up",
            )
        assert vt_pending.pop_vt_take(first) is None


def _trade() -> SimpleNamespace:
    return SimpleNamespace(
        symbol="BTCUSDT", direction="long", entry=100.0, sl=95.0,
        tps=[110.0], portions=[1.0], risk_usdt=1.0, id="t1",
    )


class TestHandler:
    pytestmark = pytest.mark.asyncio

    async def test_new_short_id_format(self, monkeypatch):
        trader = MagicMock()
        trader.create.return_value = _trade()
        monkeypatch.setattr(
            "src.telegram.handlers_vt.get_virtual_trader", lambda: trader,
        )
        monkeypatch.setattr(
            "src.telegram.handlers_vt.get_settings",
            lambda: SimpleNamespace(virtual_trader_default_risk=1.0),
        )

        key = vt_pending.register_vt_take(
            "BTCUSDT", 100.0, 95.0, 110.0, None, "up",
        )
        query = SimpleNamespace(
            answer=AsyncMock(),
            message=SimpleNamespace(reply_text=AsyncMock()),
        )

        await VirtualTradeMixin()._handle_take_to_diary(query, f"vt_take|{key}")

        trader.create.assert_called_once()
        assert trader.create.call_args.kwargs["tps"] == [110.0]
        query.message.reply_text.assert_awaited_once()

    async def test_expired_id_is_reported(self):
        query = SimpleNamespace(answer=AsyncMock())
        await VirtualTradeMixin()._handle_take_to_diary(
            query, "vt_take|deadbeef",
        )
        query.answer.assert_awaited_once()
        assert "устарела" in query.answer.await_args.args[0]
