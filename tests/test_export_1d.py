"""
Тест: _generate_export_file включает 1D.

Bybit API не дёргаем — используем фейковый клиент.
"""
from __future__ import annotations

import csv
from pathlib import Path

from src.telegram.handlers import TelegramHandlers

# ==================== helpers ====================

class _FakeBybitClient:
    """Возвращает синтетические klines для любого ТФ."""

    def get_klines(self, symbol, category, interval, limit):
        # 100 свечей с простым трендом
        # Формат Bybit: [ts, o, h, l, c, vol, turnover]
        base = 100.0 if interval != "D" else 90.0
        rows = []
        for i in range(min(limit, 100)):
            price = base + i * 0.1
            rows.append([
                (1_000_000_000 + i * 60_000) * 1000,   # ts ms
                str(price),
                str(price + 0.5),
                str(price - 0.5),
                str(price + 0.1),
                "10.0",
                "1000.0",
            ])
        # Bybit-порядок: новые первые
        return list(reversed(rows))

    def resolve_symbol(self, user_input, category):
        return user_input.upper()

    def get_screener_data(self, **kwargs):
        return []

    def get_ticker(self, symbol, category):
        return None

    def get_candle_volume_ratio(self, symbol, category, interval, periods):
        return {"ratio": 1.0}


class _FakeAlerts:
    def get_all_alerts(self):
        return []

    def load(self):
        pass


def _make_handlers():
    return TelegramHandlers(
        alerts_manager=_FakeAlerts(),
        allowed_chat_id="123",
        bybit_client=_FakeBybitClient(),
    )


# ==================== tests ====================

def test_export_contains_1d_rows(tmp_path, monkeypatch):
    from src.utils.config import Config
    monkeypatch.setattr(Config, "EXPORTS_DIR", tmp_path)

    h = _make_handlers()
    filepath = h._generate_export_file(["BTCUSDT"])

    assert filepath is not None
    path = Path(filepath)
    assert path.exists()

    # Читаем CSV и ищем строки с Timeframe == "1D"
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    timeframes = {r["Timeframe"] for r in rows}
    assert "1D" in timeframes
    # Остальные ТФ тоже на месте (обратная совместимость)
    assert {"15m", "1H", "4H"} <= timeframes


def test_export_1d_has_rsi(tmp_path, monkeypatch):
    from src.utils.config import Config
    monkeypatch.setattr(Config, "EXPORTS_DIR", tmp_path)

    h = _make_handlers()
    filepath = h._generate_export_file(["BTCUSDT"])

    with open(filepath, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows_1d = [r for r in reader if r["Timeframe"] == "1D"]

    assert len(rows_1d) > 0
    # Хотя бы где-то есть валидный RSI (не N/A), если свечей >= 15
    rsi_values = [r["RSI"] for r in rows_1d if r["RSI"] != "N/A"]
    assert len(rsi_values) > 0


def test_export_1d_uses_configured_limit(tmp_path, monkeypatch):
    from src.utils.config import Config
    monkeypatch.setattr(Config, "EXPORTS_DIR", tmp_path)

    # Ограничим до 30 свечей, чтобы точно влезло
    monkeypatch.setattr(Config, "EXPORT_LIMITS", {
        "15": 300, "60": 300, "240": 400, "D": 30,
    })

    h = _make_handlers()
    filepath = h._generate_export_file(["BTCUSDT"])

    with open(filepath, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows_1d = [r for r in reader if r["Timeframe"] == "1D"]

    # Фейковый клиент отдаёт min(limit, 100) свечей → ожидаем 30
    assert len(rows_1d) == 30