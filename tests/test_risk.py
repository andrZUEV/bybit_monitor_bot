"""Тесты src/core/risk.py — чистые, без сети и моков."""
import pytest

from src.core.risk import (
    HARD_MAX_RISK_PCT,
    RiskConfig,
    analyzer_dir_to_risk,
    apply_structure_buffer,
    build_setup,
    calc_atr_stop,
    calc_position_size,
    calc_rr,
    risk_dir_to_analyzer,
    validate_setup,
)

CFG = RiskConfig(equity=10_000.0, risk_pct=0.01, min_rr=3.0,
                 atr_multiplier=1.5, min_runway_atr=2.0,
                 lot_step=0.0, min_qty=0.0, max_qty=float("inf"))


# ==================== direction helpers ====================

@pytest.mark.parametrize("src,dst", [("up", "long"), ("down", "short")])
def test_analyzer_dir_to_risk(src, dst):
    assert analyzer_dir_to_risk(src) == dst

def test_analyzer_dir_to_risk_invalid():
    with pytest.raises(ValueError):
        analyzer_dir_to_risk("side")

@pytest.mark.parametrize("src,dst", [("long", "up"), ("short", "down")])
def test_risk_dir_to_analyzer(src, dst):
    assert risk_dir_to_analyzer(src) == dst


# ==================== RiskConfig ====================

def test_cfg_rejects_zero_equity():
    with pytest.raises(ValueError):
        RiskConfig(equity=0.0)

def test_cfg_allows_risk_above_hard_cap_but_flags_later():
    # Создание не бросает — валидация ловит в validate_setup
    cfg = RiskConfig(equity=1000.0, risk_pct=0.02)
    assert cfg.risk_pct > HARD_MAX_RISK_PCT

def test_cfg_rejects_nonpositive_risk_pct():
    with pytest.raises(ValueError):
        RiskConfig(equity=1000.0, risk_pct=0.0)


# ==================== calc_rr ====================

@pytest.mark.parametrize("entry,stop,target,dir,expected", [
    (100.0, 95.0, 115.0, "long",  3.0),
    (100.0, 95.0, 110.0, "long",  2.0),
    (100.0, 105.0, 85.0, "short", 3.0),
    (100.0, 105.0, 90.0, "short", 2.0),
    (100.0, 100.0, 110.0, "long", 0.0),
])
def test_calc_rr(entry, stop, target, dir, expected):
    assert calc_rr(entry, stop, target, dir) == pytest.approx(expected)

def test_calc_rr_bad_direction():
    with pytest.raises(ValueError):
        calc_rr(100.0, 95.0, 110.0, "up")  # analyzer-нотация сюда не проходит


# ==================== calc_atr_stop ====================

def test_atr_stop_long():
    assert calc_atr_stop(100.0, 2.0, "long", 1.5) == pytest.approx(97.0)

def test_atr_stop_short():
    assert calc_atr_stop(100.0, 2.0, "short", 1.5) == pytest.approx(103.0)

def test_atr_stop_zero_atr_raises():
    with pytest.raises(ValueError):
        calc_atr_stop(100.0, 0.0, "long", 1.5)


# ==================== apply_structure_buffer ====================

def test_structure_buffer_long_sums_atr():
    # structure=97, atr=2, mult=1.5 → 97 - 3 = 94
    assert apply_structure_buffer(97.0, 2.0, "long", 1.5) == pytest.approx(94.0)

def test_structure_buffer_short_sums_atr():
    assert apply_structure_buffer(103.0, 2.0, "short", 1.5) == pytest.approx(106.0)


# ==================== calc_position_size ====================

def test_size_basic():
    # equity=10000, risk=1% → 100; |100-95|=5 → size=20
    size, risk_amount = calc_position_size(10_000.0, 0.01, 100.0, 95.0, "long")
    assert size == pytest.approx(20.0)
    assert risk_amount == pytest.approx(100.0)

def test_size_lot_step_floors():
    # 20 / 0.3 = 66.66 → 66 * 0.3 = 19.8
    size, _ = calc_position_size(10_000.0, 0.01, 100.0, 95.0, "long", lot_step=0.3)
    assert size == pytest.approx(19.8)

def test_size_min_qty_violation_returns_zero():
    size, risk_amount = calc_position_size(
        1000.0, 0.01, 100.0, 99.0, "long", min_qty=100.0
    )  # risk=10, |1|=1 → size=10 < 100
    assert size == 0.0
    assert risk_amount == 0.0

def test_size_max_qty_caps_and_recomputes_risk():
    size, risk_amount = calc_position_size(
        1_000_000.0, 0.01, 100.0, 99.0, "long", max_qty=50.0
    )  # хотели 10000, cap 50 → risk = 50*1 = 50
    assert size == pytest.approx(50.0)
    assert risk_amount == pytest.approx(50.0)

def test_size_zero_risk_raises():
    with pytest.raises(ValueError):
        calc_position_size(10_000.0, 0.01, 100.0, 100.0, "long")


# ==================== validate_setup ====================

def test_validate_ok_long():
    # entry=100, stop=95, tp1=110 (RR=2), tp2=115 (RR=3), atr=2
    # runway = |110-100|/2 = 5 ≥ 2 OK
    r = validate_setup(100.0, 95.0, 110.0, 115.0, "long", CFG, atr=2.0)
    assert r.valid is True
    assert r.reason == ""
    assert r.rr == pytest.approx(3.0)
    assert r.rr_tp1 == pytest.approx(2.0)
    assert r.runway_atr == pytest.approx(5.0)

def test_validate_rr_below_soft_zone():
    # RR=2.0 < 2.5 → в серую зону не попадает, soft_reasons пуст
    r = validate_setup(100.0, 95.0, 105.0, 110.0, "long", CFG, atr=2.0)
    assert r.valid is False
    assert r.reason == "rr_below_min"
    assert r.soft_reasons == ()


def test_validate_soft_zone_2_5_to_3():
    # RR=2.7 → в серой зоне, soft_reasons содержит метку, valid=False
    r = validate_setup(100.0, 95.0, 110.0, 113.5, "long", CFG, atr=2.0)
    assert r.valid is False
    assert r.reason == "rr_below_min"
    assert "rr_in_grey_zone" in r.soft_reasons

def test_validate_risk_above_max():
    cfg = RiskConfig(equity=10_000.0, risk_pct=0.02, atr_multiplier=1.5)
    r = validate_setup(100.0, 95.0, 110.0, 115.0, "long", cfg, atr=2.0)
    assert r.valid is False
    assert r.reason == "risk_above_max"

def test_validate_trend_conflict_long():
    r = validate_setup(100.0, 95.0, 110.0, 115.0, "long", CFG, atr=2.0,
                       trend_4h="short")
    assert r.valid is False
    assert r.reason == "trend_conflict"

def test_validate_trend_neutral_ok():
    r = validate_setup(100.0, 95.0, 110.0, 115.0, "long", CFG, atr=2.0,
                       trend_4h=None)
    assert r.valid is True

def test_validate_short_ok():
    r = validate_setup(100.0, 105.0, 90.0, 85.0, "short", CFG, atr=2.0)
    assert r.valid is True
    assert r.rr == pytest.approx(3.0)

def test_validate_insufficient_runway():
    # entry=100, tp1=102 (RR=0.4), tp2=115 (RR=3), atr=2 → runway=1 < 2
    r = validate_setup(100.0, 95.0, 102.0, 115.0, "long", CFG, atr=2.0)
    assert r.valid is False
    assert r.reason == "insufficient_runway"

def test_validate_atr_invalid():
    r = validate_setup(100.0, 95.0, 110.0, 115.0, "long", CFG, atr=0.0)
    assert r.valid is False
    assert r.reason == "atr_invalid"

def test_validate_size_below_min():
    cfg = RiskConfig(equity=100.0, risk_pct=0.01, atr_multiplier=1.5,
                     min_qty=100.0)  # risk=$1, |100-95|=5 → size=0.2 < 100
    r = validate_setup(100.0, 95.0, 110.0, 115.0, "long", cfg, atr=2.0)
    assert r.valid is False
    assert r.reason == "size_below_min"


# ==================== build_setup ====================

def test_build_long_uses_structure_plus_atr():
    # structure=97, atr=2, mult=1.5 → stop = 97 - 3 = 94
    r = build_setup(entry=100.0, structure_stop=97.0, atr=2.0,
                    direction="long", tp1=110.0, tp2=115.0, cfg=CFG)
    assert r.stop == pytest.approx(94.0)
    # RR по tp2 = 15/6 = 2.5 < 3 → invalid
    assert r.valid is False
    assert r.reason == "rr_below_min"

def test_build_long_without_structure():
    # structure=None → stop = 100 - 3 = 97
    r = build_setup(entry=100.0, structure_stop=None, atr=2.0,
                    direction="long", tp1=110.0, tp2=118.0, cfg=CFG)
    assert r.stop == pytest.approx(97.0)
    # RR по tp2 = 18/3 = 6 → OK; runway = 10/2 = 5 ≥ 2 OK
    assert r.valid is True

def test_build_short_with_trend_conflict():
    r = build_setup(entry=100.0, structure_stop=103.0, atr=2.0,
                    direction="short", tp1=90.0, tp2=85.0, cfg=CFG,
                    trend_4h="long")
    assert r.valid is False
    assert r.reason == "trend_conflict"

def test_build_atr_invalid_short_circuits():
    r = build_setup(entry=100.0, structure_stop=None, atr=0.0,
                    direction="long", tp1=110.0, tp2=120.0, cfg=CFG)
    assert r.valid is False
    assert r.reason == "atr_invalid"
    assert r.stop == 0.0