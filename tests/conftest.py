"""Общие фикстуры. Без сетевых вызовов."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Изолированный data/-каталог.

    Патчит модуль-уровневые константы путей в src.core.alerts,
    src.core.cooldown, src.utils.config — чтобы менеджеры писали в tmp.

    ВАЖНО: конкретные имена констант зависят от репо. Проверь
    через `grep -R "ALERTS_FILE\\|COOLDOWNS_FILE\\|EXPORTS_DIR" src/`
    и поправь monkeypatch.setattr ниже.
    """
    from src.core import alerts as alerts_mod
    from src.core import cooldown as cooldown_mod
    from src.utils import config as cfg_mod

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    exports_dir = data_dir / "exports"
    exports_dir.mkdir()

    alerts_file = data_dir / "alerts.json"
    cooldowns_file = data_dir / "cooldowns.json"
    alerts_file.write_text(json.dumps({"alerts": []}), encoding="utf-8")

    # Патчим и Config, и модуль-уровневые константы, если они есть.
    for mod in (cfg_mod, alerts_mod, cooldown_mod):
        for name, value in [
            ("PROJECT_ROOT", tmp_path),
            ("DATA_DIR", data_dir),
            ("ALERTS_FILE", alerts_file),
            ("COOLDOWNS_FILE", cooldowns_file),
            ("EXPORTS_DIR", exports_dir),
        ]:
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name, value, raising=False)

    return data_dir


@pytest.fixture
def monitor_factory(monkeypatch: pytest.MonkeyPatch):
    """Фабрика Monitor с замоканными зависимостями.

    Не стартует WS, не ходит в сеть. Все пути — через tmp_data_dir
    (передай его как аргумент, если нужно).
    """
    from src.core.monitor import Monitor

    def _make(
        alerts: Any = None,
        client: Any = None,
        use_websocket: bool = True,
    ) -> Monitor:
        if client is None:
            client = MagicMock()
            client.get_klines.return_value = None
            client.get_ticker.return_value = None
        if alerts is None:
            alerts = MagicMock()
            alerts.get_all_alerts.return_value = []

        # on_alert_callback — обязательный. Передаём no-op.
        m = Monitor(
            bybit_client=client,
            alerts_manager=alerts,
            on_alert_callback=MagicMock(),
            use_websocket=use_websocket,
        )
        # Отключаем реальную отправку.
        m._check_price_cross = MagicMock()
        return m

    return _make