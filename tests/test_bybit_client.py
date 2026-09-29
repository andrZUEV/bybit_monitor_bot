"""Тесты src/api/bybit_client.py — resolve_symbol на мок get_ticker."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.api.bybit_client import BybitClient


@pytest.fixture
def client():
    c = BybitClient()
    c.get_ticker = MagicMock()
    return c


class TestResolveSymbol:
    def test_lowercase_adds_usdt(self, client):
        client.get_ticker.side_effect = lambda s, category="linear": (
            MagicMock() if s == "SOLUSDT" else None
        )
        assert client.resolve_symbol("sol") == "SOLUSDT"

    def test_uppercase_adds_usdt(self, client):
        client.get_ticker.side_effect = lambda s, category="linear": (
            MagicMock() if s == "SOLUSDT" else None
        )
        assert client.resolve_symbol("SOL") == "SOLUSDT"

    def test_already_usdt(self, client):
        client.get_ticker.side_effect = lambda s, category="linear": (
            MagicMock() if s == "SOLUSDT" else None
        )
        assert client.resolve_symbol("SOLUSDT") == "SOLUSDT"

    def test_lowercase_usdt(self, client):
        client.get_ticker.side_effect = lambda s, category="linear": (
            MagicMock() if s == "SOLUSDT" else None
        )
        assert client.resolve_symbol("solusdt") == "SOLUSDT"

    def test_spaces_stripped(self, client):
        client.get_ticker.side_effect = lambda s, category="linear": (
            MagicMock() if s == "SOLUSDT" else None
        )
        assert client.resolve_symbol("  sol usdt  ") == "SOLUSDT"

    def test_unknown_returns_none(self, client):
        client.get_ticker.return_value = None
        assert client.resolve_symbol("XPR") is None

    def test_typo_xrp_vs_xpr(self, client):
        # XRP существует, XPR — нет.
        client.get_ticker.side_effect = lambda s, category="linear": (
            MagicMock() if s == "XRPUSDT" else None
        )
        assert client.resolve_symbol("xrp") == "XRPUSDT"
        assert client.resolve_symbol("xpr") is None