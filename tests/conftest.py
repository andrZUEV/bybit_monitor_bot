"""Общие фикстуры для тестов. Без сетевых вызовов."""
from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.core.risk import RiskConfig

# ==================== Свечи ====================

def _make_klines(
    n: int,
    start_price: float = 3000.0,
    trend: float = 0.0,
    volatility: float = 0.005,
    seed: int = 42,
) -> list[list]:
    """
    Генерирует n свечей в формате Bybit:
    [start_time, open, high, low, close, volume, ...].

    klines[0] — самая свежая.
    trend > 0 → цена растёт с течением времени (в обратную сторону массива).
    """
    rng = random.Random(seed)
    candles: list[list] = []
    price = start_price
    now_ms = int(time.time() * 1000)
    step_ms = 15 * 60 * 1000

    for i in range(n):
        drift = trend * rng.uniform(0.3, 1.0)
        noise = rng.uniform(-volatility, volatility)
        o = price
        c = price * (1 + drift + noise)
        h = max(o, c) * (1 + abs(rng.uniform(0, volatility)))
        low = min(o, c) * (1 - abs(rng.uniform(0, volatility)))
        v = rng.uniform(100, 1000)
        # индекс 0 — самая свежая → время идёт назад
        ts = now_ms - i * step_ms
        candles.append([ts, str(o), str(h), str(low), str(c), str(v)])
        # для следующей (более старой) свечи price = open текущей
        price = o

    return candles


@pytest.fixture
def klines_15m() -> list[list]:
    """200 свечей 15m, слабый рост, средняя волатильность."""
    return _make_klines(n=200, start_price=3000.0, trend=0.0001, seed=42)


@pytest.fixture
def klines_4h() -> list[list]:
    """100 свечей 4H, без тренда."""
    return _make_klines(n=100, start_price=3000.0, trend=0.0, seed=43)


@pytest.fixture
def klines_1h() -> list[list]:
    return _make_klines(n=100, start_price=3000.0, trend=0.0, seed=44)


@pytest.fixture
def klines_1d() -> list[list]:
    return _make_klines(n=50, start_price=3000.0, trend=0.0, seed=45)


@pytest.fixture
def sample_klines_newest_first() -> list[list[Any]]:
    """Синтетические свечи Bybit-формата (новые первыми).

    [start, open, high, low, close, volume, turnover]
    Оставлен для обратной совместимости со старыми тестами.
    """
    klines: list[list[Any]] = []
    price = 100.0
    for i in range(60):
        price += 0.5
        klines.append([
            1_700_000_000_000 + i * 60_000,
            str(price - 0.2),
            str(price + 0.3),
            str(price - 0.4),
            str(price),
            "1000",
            "100000",
        ])
    klines.reverse()  # новые первыми
    return klines


# ==================== RiskConfig ====================

@pytest.fixture
def risk_cfg() -> RiskConfig:
    return RiskConfig(
        equity=10_000.0,
        risk_pct=0.01,
        lot_step=0.001,
        min_qty=0.001,
        max_qty=100.0,
        atr_multiplier=1.5,
        min_rr=2.0,
    )


# ==================== Base evaluation ====================

@pytest.fixture
def base_evaluation() -> dict:
    """Минимальный dict, совместимый с format_alert_message."""
    return {
        "score": 3.0,
        "verdict": "⚠️ Weak",
        "signals": [],
        "filters": [],
        "vol_ratio": 1.0,
        "rsi": 50.0,
        "touches": 1,
        "htf_trend": "side",
        "age_hours": 1.0,
        "pattern": None,
        "strength_score": 3,
        "details": [],
        "structure": None,
        "levels_4h": [],
        "levels_1d": [],
        "divergence": [],
        "risk": None,
        "hard_filter": None,
        "hard_filter_ru": None,
    }


# ==================== Data dir / Alerts / Cooldowns ====================

@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """Изолированный data/-каталог с пустыми alerts/cooldowns JSON."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "exports").mkdir()

    # Актуальный формат AlertsManager: {"assets": [...]}
    (data_dir / "alerts.json").write_text(
        json.dumps({"assets": []}), encoding="utf-8",
    )
    (data_dir / "cooldowns.json").write_text(
        json.dumps({}), encoding="utf-8",
    )
    return data_dir


@pytest.fixture
def alerts_path(tmp_data_dir: Path) -> str:
    """Путь к alerts.json для AlertsManager(storage_path=...)."""
    return str(tmp_data_dir / "alerts.json")


@pytest.fixture
def cooldowns_path(tmp_data_dir: Path) -> str:
    """Путь к cooldowns.json для CooldownManager(storage_path=...)."""
    return str(tmp_data_dir / "cooldowns.json")


@pytest.fixture
def make_alerts_manager(alerts_path: str):
    """Фабрика AlertsManager с путём в tmp."""
    from src.core.alerts import AlertsManager

    def _make() -> AlertsManager:
        return AlertsManager(storage_path=alerts_path)

    return _make


@pytest.fixture
def make_cooldown_manager(cooldowns_path: str):
    """Фабрика CooldownManager с путём в tmp."""
    from src.core.cooldown import CooldownManager

    def _make(cooldown_minutes: int = 25) -> CooldownManager:
        return CooldownManager(
            cooldown_minutes=cooldown_minutes,
            storage_path=cooldowns_path,
        )

    return _make


# ==================== Monitor ====================

@pytest.fixture
def make_monitor(tmp_data_dir: Path):
    """Фабрика Monitor с замоканными зависимостями.

    on_alert_callback — обязательный, передаём MagicMock.
    bybit_client — можно замокать, чтобы _check_price_cross
    не ходил в сеть.
    """
    from src.core.alerts import AlertsManager
    from src.core.monitor import Monitor

    def _make(
        use_websocket: bool = True,
        on_alert_callback: Any = None,
        bybit_client: Any = None,
    ) -> Monitor:
        alerts = AlertsManager(storage_path=str(tmp_data_dir / "alerts.json"))

        if on_alert_callback is None:
            on_alert_callback = MagicMock()

        if bybit_client is None:
            bybit_client = MagicMock()
            bybit_client.get_klines.return_value = None
            bybit_client.get_ticker.return_value = None

        m = Monitor(
            alerts_manager=alerts,
            on_alert_callback=on_alert_callback,
            bybit_client=bybit_client,
            use_websocket=use_websocket,
        )
        # Отключаем реальную проверку кросса — тестируем только
        # логику _on_ticker_update.
        m._check_price_cross = MagicMock()
        return m

    return _make