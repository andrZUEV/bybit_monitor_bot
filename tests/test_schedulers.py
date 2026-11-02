"""Тесты планировщиков (Этап 11): LevelScan / ReversalDiary / VirtualTrader.

Потоки не запускаем «по-настоящему»: подменяем событие остановки и проверяем
логику одного прохода. Это быстрее и не зависит от таймингов CI.
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from src.core import level_scan_scheduler as lss_mod
from src.core.level_scan_scheduler import LevelScanScheduler
from src.core.reversal_diary_scheduler import ReversalDiaryScheduler
from src.core.settings import RuntimeSettings
from src.core.virtual_trader_scheduler import VirtualTraderScheduler


class _StopEvent:
    """Событие-заглушка: первый wait() сразу просит выйти из цикла."""

    def __init__(self) -> None:
        self.waits = 0
        self._set = False

    def wait(self, timeout: float | None = None) -> bool:
        self.waits += 1
        return True

    def is_set(self) -> bool:
        return self._set

    def set(self) -> None:
        self._set = True

    def clear(self) -> None:
        self._set = False



# ==================== LEVEL SCAN ====================

def _level_scan(settings: RuntimeSettings, runner=None) -> LevelScanScheduler:
    return LevelScanScheduler(
        settings=settings,
        symbols_provider=lambda: ["BTCUSDT", "ETHUSDT"],
        category_provider=lambda symbol: "linear",
        scan_runner=runner or (lambda symbols, cat, score: None),
    )


class TestLevelScanScheduler:
    def test_safe_run_calls_runner(self):
        calls: list[tuple] = []
        sched = _level_scan(
            RuntimeSettings(),
            runner=lambda symbols, cat, score: calls.append((symbols, cat, score)),
        )

        sched._safe_run()

        assert calls == [(["BTCUSDT", "ETHUSDT"], sched.category_provider, 5.0)]
        assert sched._last_run_ts > 0

    def test_safe_run_skips_when_disabled(self):
        runner = MagicMock()
        sched = _level_scan(
            RuntimeSettings(level_scan_enabled=False), runner=runner,
        )

        sched._safe_run()

        runner.assert_not_called()

    def test_safe_run_skips_without_symbols(self):
        runner = MagicMock()
        sched = _level_scan(RuntimeSettings(), runner=runner)
        sched.symbols_provider = lambda: []

        sched._safe_run()

        runner.assert_not_called()

    def test_safe_run_swallows_runner_error(self):
        def boom(*args):
            raise RuntimeError("сеть недоступна")

        sched = _level_scan(RuntimeSettings(), runner=boom)

        # не должно бросить
        sched._safe_run()

    def test_loop_runs_on_scheduled_hour(self, monkeypatch):
        calls: list[str] = []
        sched = _level_scan(
            RuntimeSettings(level_scan_enabled=True, level_scan_hours_utc="0"),
            runner=lambda *args: calls.append("scan"),
        )
        sched._stop_event = _StopEvent()

        class _FixedDatetime:
            @staticmethod
            def now(tz=None):
                return datetime(2026, 11, 2, 0, 30, tzinfo=UTC)

        monkeypatch.setattr(lss_mod, "datetime", _FixedDatetime)
        monkeypatch.setattr(lss_mod, "_IDLE_POLL_SEC", 0.0)

        sched._loop()

        assert calls == ["scan"]
        assert sched._last_run_date_hour == (datetime(2026, 11, 2).date(), 0)

    def test_loop_does_not_repeat_in_same_hour(self, monkeypatch):
        calls: list[str] = []
        sched = _level_scan(
            RuntimeSettings(level_scan_enabled=True, level_scan_hours_utc="0"),
            runner=lambda *args: calls.append("scan"),
        )
        sched._last_run_date_hour = (datetime(2026, 11, 2).date(), 0)
        sched._stop_event = _StopEvent()

        class _FixedDatetime:
            @staticmethod
            def now(tz=None):
                return datetime(2026, 11, 2, 0, 5, tzinfo=UTC)

        monkeypatch.setattr(lss_mod, "datetime", _FixedDatetime)
        monkeypatch.setattr(lss_mod, "_IDLE_POLL_SEC", 0.0)

        sched._loop()

        assert calls == []

    def test_loop_skips_when_disabled(self, monkeypatch):
        calls: list[str] = []
        sched = _level_scan(
            RuntimeSettings(level_scan_enabled=False),
            runner=lambda *args: calls.append("scan"),
        )
        sched._stop_event = _StopEvent()
        monkeypatch.setattr(lss_mod, "_IDLE_POLL_SEC", 0.0)

        sched._loop()

        assert calls == []

    def test_loop_survives_settings_error(self, monkeypatch):
        sched = _level_scan(RuntimeSettings(level_scan_enabled=True))
        sched._stop_event = _StopEvent()

        def boom():
            raise ValueError("часы сломаны")

        sched.settings.parsed_hours_utc = boom
        monkeypatch.setattr(lss_mod, "_IDLE_POLL_SEC", 0.0)

        sched._loop()  # не должно бросить

    def test_start_stop_and_double_start(self):
        sched = _level_scan(RuntimeSettings())
        loop_started = []

        def fake_loop():
            loop_started.append(True)
            while not sched._stop_event.wait(0.01):
                pass

        sched._loop = fake_loop
        sched.start()
        assert sched._thread is not None and sched._thread.is_alive()
        assert loop_started

        sched.start()  # второй запуск — warning, поток тот же
        sched.stop()

        assert not sched._thread.is_alive()

    def test_trigger_now_runs_runner(self):
        calls: list[str] = []
        sched = _level_scan(
            RuntimeSettings(), runner=lambda *args: calls.append("scan"),
        )
        done = __import__("threading").Event()

        def fake_safe_run():
            calls.append("scan")
            done.set()

        sched._safe_run = fake_safe_run
        sched.trigger_now()
        assert done.wait(2.0)
        assert calls == ["scan"]


# ==================== REVERSAL DIARY ====================

def _diary_scheduler(diary=None, enabled=None) -> ReversalDiaryScheduler:
    return ReversalDiaryScheduler(
        diary=diary or MagicMock(),
        klines_provider=lambda symbol, category: None,
        atr_1h_provider=lambda symbol, category: None,
        category_provider=lambda symbol: "linear",
        interval_min=1,
        enabled_provider=enabled,
    )


class TestReversalDiaryScheduler:
    def test_interval_min_is_clamped(self):
        sched = _diary_scheduler()
        assert sched.interval_sec == 60

    def test_safe_run_calls_resolve(self):
        diary = MagicMock()
        diary.resolve_all_pending.return_value = 2
        diary.count_by_status.return_value = 5
        sched = _diary_scheduler(diary)

        sched._safe_run()

        diary.resolve_all_pending.assert_called_once()
        diary.count_by_status.assert_called_with("pending")

    def test_safe_run_skips_when_disabled(self):
        diary = MagicMock()
        sched = _diary_scheduler(diary, enabled=lambda: False)

        sched._safe_run()

        diary.resolve_all_pending.assert_not_called()

    def test_safe_run_swallows_error(self):
        diary = MagicMock()
        diary.resolve_all_pending.side_effect = RuntimeError("нет свечей")
        sched = _diary_scheduler(diary)

        sched._safe_run()  # не бросает

    def test_loop_runs_once_then_stops(self):
        diary = MagicMock()
        diary.resolve_all_pending.return_value = 0
        sched = _diary_scheduler(diary)
        sched._stop_event = _StopEvent()

        sched._loop()

        diary.resolve_all_pending.assert_called_once()


    def test_start_stop(self):
        sched = _diary_scheduler()
        sched._loop = lambda: sched._stop_event.wait(0.01)
        sched.start()
        assert sched._thread.is_alive()
        sched.stop()
        assert not sched._thread.is_alive()


# ==================== VIRTUAL TRADER ====================

def _vt_scheduler(trader=None, enabled=None) -> VirtualTraderScheduler:
    return VirtualTraderScheduler(
        trader=trader or MagicMock(),
        klines_provider=lambda symbol, category: None,
        category_provider=lambda symbol: "linear",
        interval_min=1,
        enabled_provider=enabled,
    )


class TestVirtualTraderScheduler:
    def test_interval_min_is_clamped(self):
        assert _vt_scheduler().interval_sec == 60

    def test_safe_run_calls_resolve(self):
        trader = MagicMock()
        trader.resolve_all.return_value = 1
        sched = _vt_scheduler(trader)

        sched._safe_run()

        trader.resolve_all.assert_called_once()

    def test_safe_run_skips_when_disabled(self):
        trader = MagicMock()
        sched = _vt_scheduler(trader, enabled=lambda: False)

        sched._safe_run()

        trader.resolve_all.assert_not_called()

    def test_safe_run_swallows_error(self):
        trader = MagicMock()
        trader.resolve_all.side_effect = RuntimeError("битый jsonl")
        sched = _vt_scheduler(trader)

        sched._safe_run()

    def test_loop_runs_once_then_stops(self):
        trader = MagicMock()
        trader.resolve_all.return_value = 0
        sched = _vt_scheduler(trader)
        sched._stop_event = _StopEvent()

        sched._loop()

        trader.resolve_all.assert_called_once()

    def test_start_stop(self):
        sched = _vt_scheduler()
        sched._loop = lambda: sched._stop_event.wait(0.01)
        sched.start()
        assert sched._thread.is_alive()
        sched.stop()
        assert not sched._thread.is_alive()


def test_schedulers_have_no_thread_leaks():
    """После stop() ни один из планировщиков не оставляет живых потоков."""
    import threading

    before = {t.name for t in threading.enumerate()}
    schedulers = [_level_scan(RuntimeSettings()), _diary_scheduler(), _vt_scheduler()]
    for sched in schedulers:
        sched._loop = lambda s=sched: s._stop_event.wait(0.01)
        sched.start()
    for sched in schedulers:
        sched.stop()

    after = {t.name for t in threading.enumerate()}
    leaked = after - before
    assert not any("Scheduler" in name or "Manual" in name for name in leaked)


@pytest.mark.parametrize("name", ["LevelScanScheduler", "ReversalDiaryScheduler"])
def test_scheduler_logs_do_not_crash(name):
    """Логирование запуска/остановки не падает на нестандартных настройках."""
    sched = _level_scan(RuntimeSettings()) if name.startswith("Level") else _diary_scheduler()
    sched._loop = lambda: None
    sched.start()
    sched.stop()
    assert sched._thread is not None
