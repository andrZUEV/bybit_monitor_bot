"""Тесты целей: один ATR, TP только по уровням, no_target, RR по TP1."""
from __future__ import annotations

import time

from src.core import analyzer
from src.core.levels import Level
from src.core.risk import RiskConfig


def _make_klines(closes: list[float]) -> list[list[str]]:
    out: list[list[str]] = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i > 0 else c
        h = max(o, c) * 1.001
        low = min(o, c) * 0.999
        out.append([
            str(1_700_000_000_000 + i * 60_000),
            f"{o}", f"{h}", f"{low}", f"{c}", "1000", "100000",
        ])
    out.reverse()
    return out


def _level(price: float, touches: int = 3, worn: bool = False) -> Level:
    return Level(
        price=price,
        price_low=price * 0.999,
        price_high=price * 1.001,
        touches=touches,
        is_mirror=False,
        is_worn=worn,
        source_tf="240",
        strength=touches,
    )


CFG = RiskConfig(equity=10_000.0, risk_pct=0.01, atr_multiplier=1.5)


def _evaluate(monkeypatch, levels: list[Level]):
    monkeypatch.setattr(analyzer, "_merge_levels", lambda *args: levels)
    return analyzer.evaluate_alert(
        symbol="BTCUSDT",
        level=50.0,          # далеко от свечей: уровень не «изношен»
        direction="up",
        current_price=100.0,
        alert_created_at=time.time(),
        klines_15m=_make_klines([100.0] * 60),
        klines_4h=_make_klines([100.0] * 60),
        risk_cfg=CFG,
    )


class TestNoTarget:
    def test_no_levels_gives_no_target(self, monkeypatch):
        res = _evaluate(monkeypatch, [])
        risk = res["risk"]
        assert risk is not None
        assert risk.valid is False
        assert risk.reason == "no_target"
        assert risk.tp1 == 0.0
        assert risk.tp2 is None
        assert res["hard_filter"] == "no_target"


class TestTargetsFromLevels:
    def test_single_level_gives_tp1_only(self, monkeypatch):
        res = _evaluate(monkeypatch, [_level(120.0)])
        risk = res["risk"]
        assert risk.tp1 == 120.0
        assert risk.tp2 is None
        # RR считается по TP1
        assert risk.rr == risk.rr_tp1

    def test_two_levels_give_tp1_and_tp2(self, monkeypatch):
        res = _evaluate(monkeypatch, [_level(110.0), _level(130.0)])
        risk = res["risk"]
        assert risk.tp1 == 110.0
        assert risk.tp2 == 130.0
        assert risk.rr > risk.rr_tp1


class TestSingleAtr:
    def test_one_hour_atr_used_for_stop(self, monkeypatch):
        """SL = structure_stop − ATR_MULTIPLIER × atr_1h, а не 15m."""
        monkeypatch.setattr(analyzer, "_calc_atr_15m", lambda candles: 1.0)
        monkeypatch.setattr(analyzer, "_calc_atr_1h", lambda candles: 5.0)

        res = _evaluate(monkeypatch, [_level(130.0)])
        risk = res["risk"]
        # structure_stop = 99.9 (low плоских свечей), 99.9 - 1.5*5 = 92.4
        assert risk.stop == 92.4
