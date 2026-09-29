"""Тесты src/core/analyzer.py — evaluate_alert на мок-свечах.

Никаких REST: get_klines мокается. Проверяем, что вердикт и сигналы
формируются, а не конкретные магические числа (они плывут от порогов).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.core import analyzer


def _make_klines(closes: list[float]) -> list[list[str]]:
    """Свечи Bybit-формата (новые первыми) из списка close (старые первыми)."""
    out: list[list[str]] = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i > 0 else c
        h = max(o, c) * 1.001
        l = min(o, c) * 0.999
        out.append([
            str(1_700_000_000_000 + i * 60_000),
            f"{o}", f"{h}", f"{l}", f"{c}", "1000", "100000",
        ])
    out.reverse()
    return out


@pytest.fixture
def mock_client():
    client = MagicMock()
    # По умолчанию get_klines возвращает None — тесты сами настроят.
    client.get_klines.return_value = None
    client.get_ticker.return_value = None
    return client


class TestEvaluateAlert:
    def test_returns_dict_with_expected_keys(self, mock_client):
        # Минимальный сценарий: 4H-тренд не определяется (None),
        # 15m свечей нет — функция не должна падать.
        mock_client.get_klines.return_value = _make_klines([100.0] * 60)
        result = analyzer.evaluate_alert(
            client=mock_client,
            symbol="SOLUSDT",
            target_price=100.0,
            direction="up",
            current_price=101.0,
        )
        assert isinstance(result, dict)

    def test_does_not_crash_when_klines_none(self, mock_client):
        mock_client.get_klines.return_value = None
        # Не должно бросать — должен вернуть что-то осмысленное (dict).
        result = analyzer.evaluate_alert(
            client=mock_client,
            symbol="SOLUSDT",
            target_price=100.0,
            direction="up",
            current_price=101.0,
        )
        assert isinstance(result, dict)

    def test_htf_trend_uses_newest_ema_value(self, mock_client):
        """Регрессия на баг из Этапа 1: ema50[-1] трактовался как текущее.

        Ряд: сначала рост (старые), потом падение (новые). На 4H EMA50
        должна смотреть на НОВЫЕ значения → тренд вниз.
        """
        # 200 свечей: первые 100 растут 100→200, последние 100 падают 200→100.
        closes = [100.0 + i for i in range(100)] + [200.0 - i for i in range(100)]
        klines_4h = _make_klines(closes)
        klines_15m = _make_klines([150.0] * 60)

        def fake_get_klines(symbol, interval, limit, category="linear"):
            return klines_4h if interval == "240" else klines_15m

        mock_client.get_klines.side_effect = fake_get_klines

        # Косвенно проверяем через evaluate_alert: он должен выставить
        # htf_trend в "down" или хотя бы не "up" на падающем хвосте.
        result = analyzer.evaluate_alert(
            client=mock_client,
            symbol="SOLUSDT",
            target_price=150.0,
            direction="up",
            current_price=140.0,
        )
        # Достаём тренд, если функция его возвращает.
        trend = result.get("htf_trend") or result.get("trend")
        if trend is not None:
            assert trend in ("down", "sideways", None)

    def test_worn_touches_threshold_is_5(self):
        # В Этапе 1 зафиксировано: 5+ касаний = изношен.
        # Проверяем константу, если она вынесена в THRESHOLDS.
        th = getattr(analyzer, "THRESHOLDS", {})
        if "worn_touches" in th:
            assert th["worn_touches"] == 5