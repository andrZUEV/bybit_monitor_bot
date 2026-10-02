"""
РўРµСЃС‚С‹ РіРµР№С‚Р° РІ _check_price_cross: С„РёР»СЊС‚СЂР°С†РёСЏ РїРѕ score + send_invalid_alerts.

Monitor СЃРѕР·РґР°С‘Рј СЃ РјРѕРєР°РјРё, klines С‚РѕР¶Рµ РјРѕРєР°РµРј. РџСЂРѕРІРµСЂСЏРµРј, С‡С‚Рѕ on_alert РІС‹Р·РІР°РЅ
РёР»Рё РќР• РІС‹Р·РІР°РЅ РІ Р·Р°РІРёСЃРёРјРѕСЃС‚Рё РѕС‚ evaluation['score'] / hard_filter / РЅР°СЃС‚СЂРѕРµРє.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from src.core.alerts import AlertRule, Asset
from src.core.monitor import AssetState, Monitor
from src.core.settings import RuntimeSettings

# ==================== helpers ====================

def _make_evaluation(score: float, hard_filter: str | None = None) -> dict:
    """РњРёРЅРёРјР°Р»СЊРЅС‹Р№ dict РѕС†РµРЅРєРё вЂ” РєР°Рє РІРѕР·РІСЂР°С‰Р°РµС‚ evaluate_alert."""
    return {
        "score": score,
        "verdict": "вљ пёЏ Weak" if score < 5.5 else "рџ’Є Strong",
        "signals": [],
        "filters": [],
        "vol_ratio": 1.0,
        "rsi": 50.0,
        "touches": 1,
        "htf_trend": "side",
        "age_hours": 1.0,
        "pattern": None,
        "strength_score": int(score),
        "details": [],
        "structure": None,
        "levels_4h": [],
        "levels_1d": [],
        "divergence": [],
        "risk": None,
        "hard_filter": hard_filter,
        "hard_filter_ru": hard_filter,
    }


def _make_asset(direction: str = "down", price: float = 100.0) -> Asset:
    return Asset(
        symbol="BTCUSDT",
        category="linear",
        alerts=[
            AlertRule(price=price, direction=direction, setup_note=""),
        ],
    )


def _make_monitor(settings: RuntimeSettings) -> tuple[Monitor, MagicMock]:
    """Monitor СЃ Р·Р°РјРѕРєР°РЅРЅС‹Рј client, cooldown Рё on_alert."""
    alerts = MagicMock()
    alerts.get_all_alerts.return_value = []

    on_alert = MagicMock()
    client = MagicMock()

    # Р”РѕСЃС‚Р°С‚РѕС‡РЅРѕ СЃРІРµС‡РµР№, С‡С‚РѕР±С‹ РєРѕРґ РїСЂРѕС€С‘Р» РІСЃРµ РїСЂРѕРІРµСЂРєРё
    client.get_klines.return_value = [
        [1, "100", "101", "99", "100", "1000"] for _ in range(5)
    ]
    client.get_candle_volume_ratio.return_value = {"ratio": 1.0}

    m = Monitor(
        alerts_manager=alerts,
        on_alert_callback=on_alert,
        bybit_client=client,
        use_websocket=False,  # Р±РµР· WS
        settings=settings,
    )

    # РљР›Р®Р§Р•Р’РћР™ РњРћРљ: cooldown РІСЃРµРіРґР° СЂР°Р·СЂРµС€Р°РµС‚ РѕС‚РїСЂР°РІРєСѓ.
    # Р‘РµР· СЌС‚РѕРіРѕ СЂРµР°Р»СЊРЅС‹Р№ data/cooldowns.json РјРѕР¶РµС‚ Р±Р»РѕРєРёСЂРѕРІР°С‚СЊ С‚РµСЃС‚.
    m.cooldown_manager = MagicMock()
    m.cooldown_manager.can_send.return_value = True

    return m, on_alert


def _run_cross(
    monitor: Monitor,
    evaluation: dict,
    direction: str = "down",
    price: float = 100.0,
) -> None:
    """
    РџСЂРѕРіРѕРЅСЏРµС‚ РѕРґРЅСѓ РёС‚РµСЂР°С†РёСЋ _check_price_cross СЃ РїРѕРґРјРµРЅС‘РЅРЅС‹Рј evaluate_alert.

    РњС‹ РїР°С‚С‡РёРј evaluate_alert С‡РµСЂРµР· РјРѕРґСѓР»СЊ src.core.monitor вЂ” РЅРѕ РѕРЅ РІС‹Р·С‹РІР°РµС‚СЃСЏ
    РєР°Рє `from src.core.analyzer import evaluate_alert` РІРЅСѓС‚СЂРё С„СѓРЅРєС†РёРё. РџРѕСЌС‚РѕРјСѓ
    РёСЃРїРѕР»СЊР·СѓРµРј monkeypatch РІ С‚РµСЃС‚Рµ.
    """
    asset = _make_asset(direction=direction, price=price)

    # prev < curr >= target в†’ crossed_up; prev > curr <= target в†’ crossed_down
    ticker = MagicMock()
    ticker.price = price + 1.0  # РґР»СЏ down РЅСѓР¶РЅРѕ prev > target Рё curr <= target

    state = AssetState()
    # prev_price = 105, target = 100, curr = 101 в†’ crossed_down (105 > 100, 101 <= 100?) вЂ” РќР•Рў
    # РќР°СЃС‚СЂРѕРёРј СЏРІРЅРѕ: prev = 100.5, curr = 99.5, target = 100
    state.prev_price = 100.5
    ticker.price = 99.5

    monitor._check_price_cross(asset, ticker, state)


# ==================== С‚РµСЃС‚С‹ ====================

class TestScoreGate:
    """
    РџСЂРѕРІРµСЂСЏРµРј, С‡С‚Рѕ РїСЂРё score < РїРѕСЂРѕРіР° Р°Р»РµСЂС‚ РќР• РѕС‚РїСЂР°РІР»СЏРµС‚СЃСЏ.
    """

    def test_below_threshold_normal_alert_skipped(self, monkeypatch):
        settings = RuntimeSettings(alert_min_score=0.0)
        m, on_alert = _make_monitor(settings)

        eval_low = _make_evaluation(score=-4.5, hard_filter=None)
        monkeypatch.setattr(
            "src.core.monitor.evaluate_alert",
            lambda **kwargs: eval_low,
            raising=False,
        )
        # РўР°Рє РєР°Рє evaluate_alert РёРјРїРѕСЂС‚РёСЂСѓРµС‚СЃСЏ РІРЅСѓС‚СЂРё С„СѓРЅРєС†РёРё, РїР°С‚С‡РёРј РјРѕРґСѓР»СЊ analyzer
        monkeypatch.setattr(
            "src.core.monitor.evaluate_alert",
            lambda **kwargs: eval_low,
        )

        asset = _make_asset(direction="down", price=100.0)
        ticker = MagicMock()
        ticker.price = 99.5
        ticker.volume_24h = 1000.0
        state = AssetState()
        state.prev_price = 100.5

        m._check_price_cross(asset, ticker, state)

        on_alert.assert_not_called()

    def test_above_threshold_normal_alert_sent(self, monkeypatch):
        settings = RuntimeSettings(alert_min_score=0.0)
        m, on_alert = _make_monitor(settings)

        eval_high = _make_evaluation(score=3.5, hard_filter=None)
        monkeypatch.setattr(
            "src.core.monitor.evaluate_alert",
            lambda **kwargs: eval_high,
        )

        asset = _make_asset(direction="down", price=100.0)
        ticker = MagicMock()
        ticker.price = 99.5
        ticker.volume_24h = 1000.0
        state = AssetState()
        state.prev_price = 100.5

        m._check_price_cross(asset, ticker, state)

        on_alert.assert_called_once()


class TestInvalidAlertsFlag:
    """РџСЂРѕРІРµСЂСЏРµРј, С‡С‚Рѕ send_invalid_alerts СѓРїСЂР°РІР»СЏРµС‚ hard_filter-Р°Р»РµСЂС‚Р°РјРё."""

    def test_hard_filter_sent_when_flag_true(self, monkeypatch):
        settings = RuntimeSettings(alert_min_score=0.0, send_invalid_alerts=True)
        m, on_alert = _make_monitor(settings)

        eval_hard = _make_evaluation(
            score=-1.5, hard_filter="trend_conflict",
        )
        monkeypatch.setattr(
            "src.core.monitor.evaluate_alert",
            lambda **kwargs: eval_hard,
        )

        asset = _make_asset(direction="down", price=100.0)
        ticker = MagicMock()
        ticker.price = 99.5
        ticker.volume_24h = 1000.0
        state = AssetState()
        state.prev_price = 100.5

        m._check_price_cross(asset, ticker, state)

        on_alert.assert_called_once()

    def test_hard_filter_skipped_when_flag_false(self, monkeypatch):
        settings = RuntimeSettings(alert_min_score=0.0, send_invalid_alerts=False)
        m, on_alert = _make_monitor(settings)

        eval_hard = _make_evaluation(
            score=-1.5, hard_filter="trend_conflict",
        )
        monkeypatch.setattr(
            "src.core.monitor.evaluate_alert",
            lambda **kwargs: eval_hard,
        )

        asset = _make_asset(direction="down", price=100.0)
        ticker = MagicMock()
        ticker.price = 99.5
        ticker.volume_24h = 1000.0
        state = AssetState()
        state.prev_price = 100.5

        m._check_price_cross(asset, ticker, state)

        on_alert.assert_not_called()
