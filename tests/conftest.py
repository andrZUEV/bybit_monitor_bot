"""Общие фикстуры. Без сетевых вызовов."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """Изолированный data/-каталог с пустыми alerts/cooldowns JSON."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "exports").mkdir()

    (data_dir / "alerts.json").write_text(
        json.dumps({"alerts": []}), encoding="utf-8",
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


@pytest.fixture
def sample_klines_newest_first() -> list[list[Any]]:
    """Синтетические свечи Bybit-формата (новые первыми).

    [start, open, high, low, close, volume, turnover]
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