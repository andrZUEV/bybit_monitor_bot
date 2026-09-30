"""
Тесты analyzer.format_alert_message — версия 4.5.

Проверяем секции и обратную совместимость.
"""
from __future__ import annotations

from src.core.analyzer import format_alert_message
from src.core.levels import Level, TrendStructure
from src.core.risk import RiskResult

# ==================== helpers ====================

def _legacy_evaluation() -> dict:
    """Минимальный evaluation без новых ключей — как в 3.x."""
    return {
        "score": 5.5,
        "verdict": "💪 Strong",
        "signals": ["Паттерн: Пин-бар +2.0", "Объём: 3.2x +2.0"],
        "filters": ["Уровень свежий (2 касаний)"],
        "vol_ratio": 3.2,
        "rsi": 30.0,
        "touches": 2,
        "htf_trend": "up",
        "age_hours": 1.0,
        "pattern": "bullish_pinbar",
        "strength_score": 5,
        "details": [],
    }


def _risk(valid: bool = True, reason: str = "") -> RiskResult:
    return RiskResult(
        entry=100.0, stop=95.0, tp1=110.0, tp2=120.0,
        rr=4.0, rr_tp1=2.0,
        size=20.0, risk_amount=100.0, runway_atr=5.0,
        valid=valid, reason=reason, soft_reasons=(),
    )


def _structure(direction: str = "up") -> TrendStructure:
    return TrendStructure(
        direction=direction,
        last_high=120.0, prev_high=110.0,
        last_low=115.0, prev_low=105.0,
        hh=True, hl=True, lh=False, ll=False,
    )


def _levels() -> list[Level]:
    tol = 100.0 * 0.0015
    return [
        Level(price=110.0, price_low=110 - tol, price_high=110 + tol,
              touches=4, is_mirror=True, is_worn=False,
              source_tf="240", strength=6),
        Level(price=120.0, price_low=120 - tol, price_high=120 + tol,
              touches=3, is_mirror=False, is_worn=True,
              source_tf="240", strength=2),
    ]


# ==================== legacy ====================

def test_legacy_format_no_new_sections():
    """Без risk/structure/levels — формат прежний."""
    msg = format_alert_message(
        symbol="BTCUSDT", level=100.0, direction="up",
        current_price=101.0, evaluation=_legacy_evaluation(),
    )
    assert "🚨 <b>Price Alert: BTCUSDT</b>" in msg
    assert "План сделки" not in msg
    assert "4H структура" not in msg
    assert "Уровни 4H" not in msg
    assert "СДЕЛКА НЕ ПО СТРАТЕГИИ" not in msg
    assert "Итого: 5.5" in msg


# ==================== risk ====================

def test_with_risk_section():
    ev = _legacy_evaluation()
    ev["risk"] = _risk(valid=True)
    msg = format_alert_message(
        symbol="BTCUSDT", level=100.0, direction="up",
        current_price=101.0, evaluation=ev,
    )
    assert "📐 <b>План сделки:</b>" in msg
    assert "Entry: <code>100.00</code>" in msg
    assert "SL:    <code>95.00</code>" in msg
    assert "TP1:   <code>110.00</code>  (RR 2.00)" in msg
    assert "TP2:   <code>120.00</code>  (RR 4.00)" in msg
    assert "Size:  <code>20.0000</code>" in msg
    assert "риск $100.00" in msg
    assert "valid: ✅" in msg


def test_with_risk_invalid_shows_cross():
    ev = _legacy_evaluation()
    ev["risk"] = _risk(valid=False, reason="rr_below_min")
    msg = format_alert_message(
        symbol="BTCUSDT", level=100.0, direction="up",
        current_price=101.0, evaluation=ev,
    )
    assert "valid: ⛔" in msg


# ==================== hard filter ====================

def test_hard_filter_banner_at_top():
    ev = _legacy_evaluation()
    ev["risk"] = _risk(valid=False, reason="trend_conflict")
    ev["hard_filter"] = "trend_conflict"
    ev["hard_filter_ru"] = "4H-тренд против направления входа"
    msg = format_alert_message(
        symbol="BTCUSDT", level=100.0, direction="up",
        current_price=101.0, evaluation=ev,
    )
    assert "⛔ <b>СДЕЛКА НЕ ПО СТРАТЕГИИ:</b>" in msg
    assert "4H-тренд против направления входа" in msg
    # Плашка идёт в самом начале
    assert msg.startswith("⛔")


def test_hard_filter_absent_when_not_set():
    ev = _legacy_evaluation()
    ev["hard_filter"] = None
    ev["hard_filter_ru"] = None
    msg = format_alert_message(
        symbol="BTCUSDT", level=100.0, direction="up",
        current_price=101.0, evaluation=ev,
    )
    assert "СДЕЛКА НЕ ПО СТРАТЕГИИ" not in msg


# ==================== structure ====================

def test_structure_section_up():
    ev = _legacy_evaluation()
    ev["structure"] = _structure("up")
    msg = format_alert_message(
        symbol="BTCUSDT", level=100.0, direction="up",
        current_price=101.0, evaluation=ev,
    )
    assert "📈 4H структура: <b>up</b> (HH+HL)" in msg


def test_structure_section_side_no_marks():
    ev = _legacy_evaluation()
    ev["structure"] = TrendStructure(
        direction="side",
        last_high=110.0, prev_high=109.0,
        last_low=100.0, prev_low=101.0,
        hh=False, hl=False, lh=False, ll=False,
    )
    msg = format_alert_message(
        symbol="BTCUSDT", level=100.0, direction="up",
        current_price=101.0, evaluation=ev,
    )
    assert "4H структура: <b>side</b> (—)" in msg


# ==================== levels ====================

def test_levels_section_top3():
    ev = _legacy_evaluation()
    ev["levels_4h"] = _levels()
    msg = format_alert_message(
        symbol="BTCUSDT", level=100.0, direction="up",
        current_price=101.0, evaluation=ev,
    )
    assert "🎯 <b>Уровни 4H (топ-3):</b>" in msg
    assert "<code>110.00</code>" in msg
    assert "(4 кас.) 🔄" in msg          # mirror
    assert "(3 кас.) ⚠️" in msg          # worn


def test_levels_absent_when_empty():
    ev = _legacy_evaluation()
    ev["levels_4h"] = []
    msg = format_alert_message(
        symbol="BTCUSDT", level=100.0, direction="up",
        current_price=101.0, evaluation=ev,
    )
    assert "Уровни 4H" not in msg


# ==================== setup_note ====================

def test_setup_note_present():
    msg = format_alert_message(
        symbol="BTCUSDT", level=100.0, direction="up",
        current_price=101.0, evaluation=_legacy_evaluation(),
        setup_note="reversal at 100",
    )
    assert "📝 <b>Сетап:</b> <code>reversal at 100</code>" in msg

def test_html_escape_in_hard_filter():
    ev = _legacy_evaluation()
    ev["hard_filter_ru"] = "RR < 1:3"
    msg = format_alert_message(
        symbol="BTCUSDT", level=100.0, direction="up",
        current_price=101.0, evaluation=ev,
    )
    assert "RR &lt; 1:3" in msg
    # И «сырой» < не остался
    assert "RR < 1:3" not in msg


def test_html_escape_in_setup_note():
    msg = format_alert_message(
        symbol="BTCUSDT", level=100.0, direction="up",
        current_price=101.0, evaluation=_legacy_evaluation(),
        setup_note="risk < 1% & watch > 121",
    )
    assert "&lt; 1% &amp; watch &gt; 121" in msg
    # Сырой < не должен остаться
    assert "< 1%" not in msg


def test_html_escape_in_signals():
    ev = _legacy_evaluation()
    ev["signals"] = ["Volume > 3x", "wick < level"]
    msg = format_alert_message(
        symbol="BTCUSDT", level=100.0, direction="up",
        current_price=101.0, evaluation=ev,
    )
    assert "Volume &gt; 3x" in msg
    assert "wick &lt; level" in msg