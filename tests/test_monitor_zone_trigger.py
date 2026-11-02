"""Этап 10: монитор срабатывает на вход в зону, а не на центр диапазона."""
from __future__ import annotations

from unittest.mock import MagicMock

from src.core.alerts import AlertRule, Asset
from src.core.monitor import AssetState, Monitor
from src.core.settings import RuntimeSettings


def _make_evaluation(score: float = 4.0) -> dict:
    verdict = "💪 Strong" if score >= 5.5 else "⚠️ Weak" if score >= 3.5 else "🟡 Marginal"
    return {
        "score": score,
        "verdict": verdict,
        "signals": [],
        "filters": [],
        "vol_ratio": 1.0,
        "rsi": 50.0,
        "touches": 1,
        "htf_trend": "side",
        "age_hours": 1.0,
        "pattern": None,
        "pattern_strict": None,
        "strength_score": int(score),
        "details": [],
        "structure": None,
        "levels_4h": [],
        "levels_1d": [],
        "divergence": [],
        "risk": None,
        "hard_filter": None,
        "hard_filter_ru": None,
    }


def _make_monitor() -> tuple[Monitor, MagicMock]:
    alerts = MagicMock()
    alerts.get_all_alerts.return_value = []
    on_alert = MagicMock()
    client = MagicMock()
    client.get_klines.return_value = [
        [1, "100", "101", "99", "100", "1000"] for _ in range(5)
    ]

    m = Monitor(
        alerts_manager=alerts,
        on_alert_callback=on_alert,
        bybit_client=client,
        use_websocket=False,
        settings=RuntimeSettings(alert_min_score=float("-inf")),
    )
    m.cooldown_manager = MagicMock()
    m.cooldown_manager.can_send.return_value = True
    m.cooldown_manager.short_cooldown_active.return_value = False
    return m, on_alert


def _asset(
    price: float = 102.5,
    direction: str = "down",
    price_low: float | None = 100.0,
    price_high: float | None = 105.0,
) -> Asset:
    return Asset(
        symbol="BTCUSDT",
        category="linear",
        alerts=[AlertRule(
            price=price, direction=direction,
            price_low=price_low, price_high=price_high,
        )],
    )


def _run(
    monitor: Monitor,
    asset: Asset,
    prev: float,
    curr: float,
    state: AssetState | None = None,
) -> AssetState:
    state = state or AssetState()
    state.prev_price = prev

    ticker = MagicMock()
    ticker.price = curr
    ticker.volume_24h = 1000.0

    monitor._check_price_cross(asset, ticker, state)
    return state


def _patch_eval(monkeypatch, evaluation: dict) -> None:
    monkeypatch.setattr(
        "src.core.monitor.evaluate_alert", lambda **kwargs: evaluation,
    )


class TestZoneEntry:
    def test_down_entry_from_above(self, monkeypatch):
        """prev выше зоны, curr внутри → алерт (сверху вниз)."""
        m, on_alert = _make_monitor()
        _patch_eval(monkeypatch, _make_evaluation())

        _run(m, _asset(direction="down"), prev=106.0, curr=104.0)

        on_alert.assert_called_once()

    def test_up_entry_from_below(self, monkeypatch):
        """prev ниже зоны, curr внутри → алерт (снизу вверх)."""
        m, on_alert = _make_monitor()
        _patch_eval(monkeypatch, _make_evaluation())

        _run(m, _asset(direction="up"), prev=98.0, curr=101.0)

        on_alert.assert_called_once()

    def test_no_alert_when_above_zone(self, monkeypatch):
        m, on_alert = _make_monitor()
        _patch_eval(monkeypatch, _make_evaluation())

        _run(m, _asset(direction="down"), prev=110.0, curr=107.0)

        on_alert.assert_not_called()

    def test_no_alert_when_already_inside_and_staying(self, monkeypatch):
        """Цена уже в зоне и не выходила — повторного алерта нет."""
        m, on_alert = _make_monitor()
        _patch_eval(monkeypatch, _make_evaluation())

        state = AssetState()
        _run(m, _asset(direction="down"), prev=106.0, curr=104.0, state=state)
        on_alert.assert_called_once()

        _run(m, _asset(direction="down"), prev=104.0, curr=102.0, state=state)
        assert on_alert.call_count == 1

    def test_any_direction_triggers_from_both_sides(self, monkeypatch):
        m, on_alert = _make_monitor()
        _patch_eval(monkeypatch, _make_evaluation())

        _run(m, _asset(direction="any"), prev=98.0, curr=101.0)
        assert on_alert.call_count == 1

        m2, on_alert2 = _make_monitor()
        _run(m2, _asset(direction="any"), prev=107.0, curr=104.0)
        assert on_alert2.call_count == 1

    def test_point_alert_behaves_as_before(self, monkeypatch):
        """Точечный алерт: low == high == price, старое поведение сохранено."""
        m, on_alert = _make_monitor()
        _patch_eval(monkeypatch, _make_evaluation())

        _run(
            m,
            _asset(price=100.0, direction="down", price_low=None, price_high=None),
            prev=101.0,
            curr=99.5,
        )

        on_alert.assert_called_once()

    def test_zone_center_used_for_cooldown_key(self, monkeypatch):
        """Кулдаун спрашивается по центру зоны — ключ стабилен."""
        m, on_alert = _make_monitor()
        _patch_eval(monkeypatch, _make_evaluation())

        _run(m, _asset(direction="down"), prev=106.0, curr=104.0)

        args = m.cooldown_manager.can_send.call_args.args
        assert args == ("BTCUSDT", 102.5, "down")

    def test_alert_message_shows_range(self, monkeypatch):
        m, on_alert = _make_monitor()
        _patch_eval(monkeypatch, _make_evaluation())

        _run(m, _asset(direction="down"), prev=106.0, curr=104.0)

        event = on_alert.call_args.args[0]
        assert "100.0000–105.0000" in event.message
