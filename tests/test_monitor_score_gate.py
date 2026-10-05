"""
Тесты гейта в _check_price_cross: фильтрация по score + send_invalid_alerts.

Monitor создаём с моками, klines тоже мокаем. Проверяем, что on_alert вызван
или НЕ вызван в зависимости от evaluation['score'] / hard_filter / настроек.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from src.core.alerts import AlertRule, Asset
from src.core.monitor import AssetState, Monitor
from src.core.settings import RuntimeSettings

# ==================== helpers ====================

def _make_evaluation(score: float, hard_filter: str | None = None) -> dict:
    # Воспроизводим логику verdict из evaluate_alert:
    #   score >= strong_min(5.5) → "💪 Strong"
    #   score >= weak_min(3.5)   → "⚠️ Weak"
    #   иначе                    → "❌ None"
    if score >= 5.5:
        verdict = "💪 Strong"
    elif score >= 3.5:
        verdict = "⚠️ Weak"
    else:
        verdict = "❌ None"
    return {
        "score": score,
        "verdict": verdict,   # ← переменная из if/elif/else,
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
    """Monitor с замоканным client, cooldown и on_alert."""
    alerts = MagicMock()
    alerts.get_all_alerts.return_value = []

    on_alert = MagicMock()
    client = MagicMock()

    # Достаточно свечей, чтобы код прошёл все проверки
    client.get_klines.return_value = [
        [1, "100", "101", "99", "100", "1000"] for _ in range(5)
    ]
    client.get_candle_volume_ratio.return_value = {"ratio": 1.0}

    m = Monitor(
        alerts_manager=alerts,
        on_alert_callback=on_alert,
        bybit_client=client,
        use_websocket=False,  # без WS
        settings=settings,
    )

    # КЛЮЧЕВОЙ МОК: cooldown всегда разрешает отправку.
    # Без этого реальный data/cooldowns.json может блокировать тест.
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
    Прогоняет одну итерацию _check_price_cross с подменённым evaluate_alert.

    Мы патчим evaluate_alert через модуль src.core.monitor — но он вызывается
    как `from src.core.analyzer import evaluate_alert` внутри функции. Поэтому
    используем monkeypatch в тесте.
    """
    asset = _make_asset(direction=direction, price=price)

    # prev < curr >= target → crossed_up; prev > curr <= target → crossed_down
    ticker = MagicMock()
    ticker.price = price + 1.0  # для down нужно prev > target и curr <= target

    state = AssetState()
    # prev_price = 105, target = 100, curr = 101 → crossed_down (105 > 100, 101 <= 100?) — НЕТ
    # Настроим явно: prev = 100.5, curr = 99.5, target = 100
    state.prev_price = 100.5
    ticker.price = 99.5

    monitor._check_price_cross(asset, ticker, state)


# ==================== тесты ====================

class TestScoreGate:
    """
    Проверяем, что при score < порога алерт НЕ отправляется.
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
        # Так как evaluate_alert импортируется внутри функции, патчим модуль analyzer
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
    """Проверяем, что send_invalid_alerts управляет hard_filter-алертами."""

    def test_hard_filter_sent_when_flag_true(self, monkeypatch):
        settings = RuntimeSettings(alert_min_score=0.0, send_invalid_alerts=True)
        m, on_alert = _make_monitor(settings)

        eval_hard = _make_evaluation(
            score=3.5, hard_filter="trend_conflict",
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
            score=3.5, hard_filter="trend_conflict",
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