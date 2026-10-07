"""
Планировщик резолва дневника разворотов.

Daemon-thread, который раз в REVERSAL_RESOLVE_INTERVAL_MIN минут
проходит по всем pending-записям и пытается их резолвить.

Не использует APScheduler — как и LevelScanScheduler, простая логика.
Ошибки резолва не валят поток: логируются, идём дальше.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from src.core.reversal_diary import ReversalDiary

logger = logging.getLogger(__name__)


# Как часто проверять, не пора ли резолвить (секунды).
# Внутри — основной интервал из настроек, здесь — минимальный шаг.
_IDLE_POLL_SEC: float = 60.0


class ReversalDiaryScheduler:
    """
    Планировщик резолва pending-записей.

    Параметры:
        diary: ReversalDiary (резолвит внутри себя).
        klines_provider: (symbol, category) -> list[list] | None.
        atr_1h_provider: (symbol, category) -> float | None.
        category_provider: (symbol) -> "linear" | "spot".
        interval_min: интервал между проходами (минуты).
        enabled_provider: () -> bool — включать ли (для hot-reload).
    """

    
    def __init__(
        self,
        diary: ReversalDiary,
        klines_provider: Callable[[str, str], list[list] | None],
        atr_1h_provider: Callable[[str, str], float | None],
        category_provider: Callable[[str], str],
        *,
        interval_min: int = 20,
        enabled_provider: Callable[[], bool] | None = None,
    ):
        self.diary = diary
        self.klines_provider = klines_provider
        self.atr_1h_provider = atr_1h_provider
        self.category_provider = category_provider
        self.interval_sec = max(60, int(interval_min) * 60)
        self.enabled_provider = enabled_provider or (lambda: True)

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    # ==================== ПУБЛИЧНЫЙ API ====================

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            logger.warning("⚠️ ReversalDiaryScheduler уже запущен")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="ReversalDiaryScheduler", daemon=True,
        )
        self._thread.start()
        logger.info(
            f"⏰ ReversalDiaryScheduler запущен "
            f"(interval={self.interval_sec // 60} мин)"
        )

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        logger.info("🛑 ReversalDiaryScheduler остановлен")

    def trigger_now(self) -> None:
        """Принудительный запуск резолва (для кнопки)."""
        t = threading.Thread(
            target=self._safe_run, name="ReversalDiaryResolve", daemon=True,
        )
        t.start()

    # ==================== ВНУТРЕННИЕ ====================

    def _loop(self) -> None:
        logger.debug("ReversalDiaryScheduler: loop started")
        # Первый проход — сразу после старта.
        self._safe_run()

        while not self._stop_event.is_set():
            if self._stop_event.wait(self.interval_sec):
                break
            if not self.enabled_provider():
                continue
            self._safe_run()

        logger.debug("ReversalDiaryScheduler: loop ended")

    def _safe_run(self) -> None:
        """Обёртка над diary.resolve_all_pending с логированием."""
        with self._lock:
            if not self.enabled_provider():
                return
            try:
                n = self.diary.resolve_all_pending(
                    klines_provider=self.klines_provider,
                    atr_1h_provider=self.atr_1h_provider,
                    category_provider=self.category_provider,
                )
                if n > 0:
                    logger.info(
                        f"📔 ReversalDiary: резолвнуто {n} записей, "
                        f"pending: {self.diary.count_by_status('pending')}"
                    )
            except Exception as e:
                logger.error(
                    f"❌ ReversalDiaryScheduler: ошибка резолва: {e}",
                    exc_info=True,
                )