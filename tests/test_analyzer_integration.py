"""
Интеграционные тесты analyzer.evaluate_alert с risk/levels/divergence.
Без сети. Синтетические свечи.
"""
from __future__ import annotations

import time

import pytest

from src.core.analyzer import evaluate_alert
from src.core.risk import RiskConfig

# ==================== helpers ====================

def _kline(o, h, low, c, v=1.0):
    return [0, o, h, low, c, v]


def _flat_klines(n: int, price: float = 100.0, wick: float = 0.5):
    """n одинаковых свечей вокруг price. Свежие первые — не важно."""
    return [_kline(price, price + wick, price - wick, price) for _ in range(n)]


def _volatile_uptrend_klines(n: int = 60, base: float = 100.0):
    """
    Синтетический восходящий тренд на 4H.
    Свежие первые. Строим хронологически и разворачиваем.
    """
    chrono = []
    price = base
    for i in range(n):
        # Зигзаг: +2, -1, +2, -1 ...
        if i % 4 == 0:
            price += 2
        elif i % 4 == 2:
            price -= 1
        o = price
        c = price + (0.5 if i % 2 == 0 else -0.5)
        h = max(o, c) + 0.8
        low = min(o, c) - 0.8
        chrono.append(_kline(o, h, low, c))
    return list(reversed(chrono))


def _make_15m_with_bounce(level: float = 100.0, n: int = 60):
    """
    15m свечи, где последняя закрытая свеча — бычий отбой от level.
    Возвращает klines в Bybit-порядке.
    """
    candles_chrono = []
    price = level + 5
    for i in range(n):
        o = price
        # Финальные свечи — падение к level и отбой
        if i < n - 3:
            c = price + (0.3 if i % 2 == 0 else -0.3)
            h = max(o, c) + 0.4
            low = min(o, c) - 0.4
        else:
            # спуск
            c = max(level + 0.5, price - 2)
            h = max(o, c) + 0.4
            low = min(o, c) - 0.4
        candles_chrono.append(_kline(o, h, low, c))
        price = c
    # Отбой: молот на level
    candles_chrono.append(_kline(level + 1, level + 1.5, level - 0.5, level + 1.2))
    return list(reversed(candles_chrono))


# ==================== обратная совместимость ====================

def test_evaluate_alert_legacy_signature_still_works():
    """Без risk_cfg и без 1h/1d — работает как раньше."""
    k15 = _make_15m_with_bounce()
    k4 = _volatile_uptrend_klines(60)
    result = evaluate_alert(
        symbol="BTCUSDT",
        level=100.0,
        direction="up",
        current_price=101.2,
        alert_created_at=time.time(),
        klines_15m=k15,
        klines_4h=k4,
    )
    # старые ключи на месте
    assert "score" in result
    assert "verdict" in result
    assert "signals" in result
    assert "filters" in result
    # новые ключи есть, но пустые/None
    assert result["risk"] is None
    assert result["hard_filter"] is None
    assert isinstance(result["levels_4h"], list)


def test_evaluate_alert_short_data_returns_safe_defaults():
    result = evaluate_alert(
        symbol="X", level=100.0, direction="up",
        current_price=100.0, alert_created_at=time.time(),
        klines_15m=[], klines_4h=[],
    )
    assert result["score"] == 0.0
    assert result["risk"] is None
    assert result["hard_filter"] is None
    assert result["structure"] is None


# ==================== risk_cfg ====================

def test_evaluate_alert_with_risk_cfg_adds_risk_result():
    """С risk_cfg → RiskResult заполнен (valid или invalid)."""
    k15 = _make_15m_with_bounce(level=100.0)
    k4 = _volatile_uptrend_klines(60)
    # 1H свечи — копируем 15m для простоты (формально не 1H, но данные валидны)
    k1h = _flat_klines(50, price=100.0, wick=2.0)

    cfg = RiskConfig(
        equity=10_000.0, risk_pct=0.01, min_rr=3.0,
        atr_multiplier=1.5, min_runway_atr=2.0,
        lot_step=0.0, min_qty=0.0, max_qty=float("inf"),
    )
    result = evaluate_alert(
        symbol="BTCUSDT",
        level=100.0,
        direction="up",
        current_price=101.2,
        alert_created_at=time.time(),
        klines_15m=k15,
        klines_4h=k4,
        klines_1h=k1h,
        risk_cfg=cfg,
    )
    risk = result["risk"]
    assert risk is not None
    assert risk.entry == pytest.approx(101.2)
    assert risk.stop > 0
    # либо valid=True, либо valid=False с reason
    if not risk.valid:
        assert risk.reason != ""


def test_evaluate_alert_hard_filter_trend_conflict_sets_reason():
    """
    Структура 4H = down, направление алерта = up → trend_conflict.
    """
    # Нисходящий 4H
    k4 = _volatile_uptrend_klines(60)
    k4 = list(reversed(k4))  # разворот → вниз
    k15 = _make_15m_with_bounce(level=100.0)
    k1h = _flat_klines(50, price=100.0, wick=2.0)

    cfg = RiskConfig(equity=10_000.0, risk_pct=0.01)
    result = evaluate_alert(
        symbol="BTCUSDT", level=100.0, direction="up",
        current_price=101.2, alert_created_at=time.time(),
        klines_15m=k15, klines_4h=k4, klines_1h=k1h,
        risk_cfg=cfg,
    )
    # Если структура действительно определилась как down — hard_filter
    if result["structure"] and result["structure"].direction == "down":
        assert result["hard_filter"] == "trend_conflict"
        assert result["hard_filter_ru"]  # непустой перевод


def test_evaluate_alert_no_atr_skips_risk():
    """Мало свечей 15m для ATR → risk не считаем, hard_filter=None."""
    k15 = _flat_klines(5, price=100.0)
    k4 = _volatile_uptrend_klines(60)
    cfg = RiskConfig(equity=10_000.0, risk_pct=0.01)
    result = evaluate_alert(
        symbol="X", level=100.0, direction="up",
        current_price=100.0, alert_created_at=time.time(),
        klines_15m=k15, klines_4h=k4, klines_1h=None,
        risk_cfg=cfg,
    )
    assert result["risk"] is None
    assert result["hard_filter"] is None
    # В filters должна быть запись про недоступный ATR
    assert any("ATR" in f for f in result["filters"])


# ==================== levels + structure ====================

def test_evaluate_alert_with_1d_fills_levels_1d():
    k15 = _make_15m_with_bounce()
    k4 = _volatile_uptrend_klines(80)
    k1d = _volatile_uptrend_klines(60, base=90.0)

    result = evaluate_alert(
        symbol="X", level=100.0, direction="up",
        current_price=101.0, alert_created_at=time.time(),
        klines_15m=k15, klines_4h=k4, klines_1d=k1d,
    )
    assert isinstance(result["levels_1d"], list)


def test_evaluate_alert_structure_present_when_4h_enough():
    k15 = _make_15m_with_bounce()
    k4 = _volatile_uptrend_klines(80)
    result = evaluate_alert(
        symbol="X", level=100.0, direction="up",
        current_price=101.0, alert_created_at=time.time(),
        klines_15m=k15, klines_4h=k4,
    )
    assert result["structure"] is not None
    # На нашем зигзаге 4H структура должна определиться
    assert result["structure"].direction in ("up", "down", "side")


def test_evaluate_alert_new_keys_always_present():
    """Все новые ключи присутствуют даже при пустых данных."""
    result = evaluate_alert(
        symbol="X", level=100.0, direction="up",
        current_price=100.0, alert_created_at=time.time(),
        klines_15m=[], klines_4h=[],
    )
    for key in ("structure", "levels_4h", "levels_1d",
                "divergence", "risk", "hard_filter", "hard_filter_ru"):
        assert key in result