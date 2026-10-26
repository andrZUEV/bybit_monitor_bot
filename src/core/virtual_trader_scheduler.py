"""
Планировщик резолва виртуальных сделок.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from src.core.virtual_trader import VirtualTrader

logger = logging.getLogger(__name__)


class VirtualTraderScheduler:
    def __init__(
        self,
        trader: VirtualTrader,
        klines_provider: Callable[[str, str], list[list] | None],
        category_provider: Callable[[str], str],
        *,
        interval_min: int = 5,
        enabled_provider: Callable[[], bool] | None = None,
    ):
        self.trader = trader
        self.klines_provider = klines_provider
        self.category_provider = category_provider
        self.interval_sec = max(60, int(interval_min) * 60)
        self.enabled_provider = enabled_provider or (lambda: True)

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="VirtualTraderScheduler", daemon=True,
        )
        self._thread.start()
        logger.info(
            f"⏰ VirtualTraderScheduler запущен (interval={self.interval_sec // 60} мин)"
        )

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        logger.info("🛑 VirtualTraderScheduler остановлен")

    def _loop(self) -> None:
        self._safe_run()
        while not self._stop_event.is_set():
            if self._stop_event.wait(self.interval_sec):
                break
            if not self.enabled_provider():
                continue
            self._safe_run()

    def _safe_run(self) -> None:
        with self._lock:
            if not self.enabled_provider():
                return
            try:
                n = self.trader.resolve_all(
                    klines_provider=self.klines_provider,
                    category_provider=self.category_provider,
                )
                if n > 0:
                    logger.info(f"💼 Резолв сделок: {n} шт.")
            except Exception as e:
                logger.error(f"❌ VirtualTraderScheduler: {e}", exc_info=True)