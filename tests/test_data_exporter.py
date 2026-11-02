"""Тесты DataExporter (Этап 11): выгрузка, RSI-колонка, кулдаун, очистка."""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.utils.data_exporter import DataExporter


def _klines(n: int = 30) -> list[list[str]]:
    """Свечи в формате Bybit: новые первыми."""
    out: list[list[str]] = []
    for i in range(n):
        price = 100.0 + i
        out.append([
            str(1_700_000_000_000 + i * 60_000),
            f"{price}", f"{price + 1}", f"{price - 1}", f"{price + 0.5}", "10",
        ])
    out.reverse()
    return out


@pytest.fixture
def exporter(tmp_path, monkeypatch) -> DataExporter:
    client = MagicMock()
    client.get_klines.return_value = _klines()
    exp = DataExporter(client)
    # Не пишем в data/exports репозитория
    exp.data_dir = tmp_path / "exports"
    exp.data_dir.mkdir(parents=True, exist_ok=True)
    return exp


class TestCanExport:
    def test_allows_first_time(self, exporter):
        assert exporter.can_export("btcusdt") is True

    def test_blocks_after_mark(self, exporter):
        exporter.mark_exported("BTCUSDT")
        assert exporter.can_export("BTCUSDT") is False

    def test_cooldown_expires(self, exporter):
        exporter.mark_exported("BTCUSDT")
        exporter._last_export_time["BTCUSDT"] = time.time() - 31
        assert exporter.can_export("BTCUSDT") is True


class TestExportSymbolData:
    def test_creates_file_with_header(self, exporter):
        path = exporter.export_symbol_data("btcusdt", "linear")

        assert path is not None and path.exists()
        text = Path(path).read_text(encoding="utf-8")
        assert "BTCUSDT | Bybit Data Export" in text
        assert "Category: linear" in text
        assert "15m" in text and "1H" in text and "4H" in text
        assert "time;open;high;low;close;volume;rsi" in text

    def test_rsi_column_filled(self, exporter):
        path = exporter.export_symbol_data("BTCUSDT")

        rows = [
            line for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line and line[0].isdigit()
        ]
        assert rows
        rsi_values = [row.split(";")[-1] for row in rows]
        assert any(value != "N/A" for value in rsi_values)

    def test_no_data_branch(self, exporter):
        exporter.bybit_client.get_klines.return_value = []
        path = exporter.export_symbol_data("BTCUSDT")

        assert path is not None
        assert "# No data available" in Path(path).read_text(encoding="utf-8")

    def test_cooldown_returns_none(self, exporter):
        exporter.mark_exported("BTCUSDT")

        assert exporter.export_symbol_data("BTCUSDT") is None

    def test_client_error_returns_none(self, exporter):
        exporter.bybit_client.get_klines.side_effect = RuntimeError("api down")

        assert exporter.export_symbol_data("BTCUSDT") is None

    def test_client_not_called_after_cooldown(self, exporter):
        exporter.mark_exported("BTCUSDT")
        exporter.bybit_client.get_klines.reset_mock()

        exporter.export_symbol_data("BTCUSDT")

        exporter.bybit_client.get_klines.assert_not_called()


class TestExportMultiple:
    def test_empty_list_returns_none(self, exporter):
        assert exporter.export_multiple_symbols([]) is None

    def test_writes_all_symbols(self, exporter, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda *_: None)

        path = exporter.export_multiple_symbols(["btc", "ETH"], "linear")

        assert path is not None
        text = Path(path).read_text(encoding="utf-8")
        assert "Multi-Symbol Export" in text
        assert "SYMBOL: BTC" in text
        assert "SYMBOL: ETH" in text

    def test_error_returns_none(self, exporter, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda *_: None)
        exporter.bybit_client.get_klines.side_effect = RuntimeError("boom")

        assert exporter.export_multiple_symbols(["BTC"]) is None


class TestCleanup:
    def test_removes_old_files(self, exporter):
        old = exporter.data_dir / "old.txt"
        new = exporter.data_dir / "new.txt"
        old.write_text("x", encoding="utf-8")
        new.write_text("x", encoding="utf-8")
        old_time = time.time() - 7200
        import os
        os.utime(old, (old_time, old_time))

        exporter.cleanup_old_files(max_age_hours=1)

        assert not old.exists()
        assert new.exists()

    def test_keeps_fresh_files(self, exporter):
        fresh = exporter.data_dir / "fresh.txt"
        fresh.write_text("x", encoding="utf-8")

        exporter.cleanup_old_files(max_age_hours=1)

        assert fresh.exists()
