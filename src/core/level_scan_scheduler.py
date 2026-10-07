"""
Планировщик сканера уровней.

Daemon-thread, который раз в сутки (по часам UTC из RuntimeSettings)
запускает скан и отправляет сводку в Telegram.

Особенности:
  - Не использует APScheduler — своя простая логика (1 CPU, 2 GB).
  - Спит «до следующего часа из списка». Если прошли все часы суток —
    ждёт первый час следующих суток.
  - При level_scan_enabled=False просто спит и периодически проверяет
    флаг (на случай, если пользователь включил из бота).
  - Ошибки скана не валят поток: логируются и продолжаем.
  - Вызывается из main.py через .start() / .stop().
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from src.core.settings import RuntimeSettings

logger = logging.getLogger(__name__)


# Как часто проверять флаг enabled, пока ждём часа X (секунды).
_IDLE_POLL_SEC: float = 60.0

# За сколько секунд до целевого часа просыпаться.
_WAKE_MARGIN_SEC: float = 5.0


class LevelScanScheduler:
    """
    Планировщик сканера уровней.

    Параметры:
        settings: RuntimeSettings (флаги enabled / hours_utc).
        symbols_provider: () -> list[str] — откуда брать символы.
            Обычно AlertsManager.get_all_alerts() → уникальные symbol.
        category_provider: (symbol) -> str — категория для символа
            (из alerts.json, fallback 'linear').
        scan_runner: (symbols, category_provider, min_score) -> None
            Функция, которая делает всё: скан, форматирование, отправку.
            Инжектится извне, чтобы не тащить сюда Telegram и хендлеры.
    """

    def __init__(
        self,
        settings: RuntimeSettings,
        symbols_provider: Callable[[], list[str]],
        category_provider: Callable[[str], str],
        scan_runner: Callable[[list[str], Callable[[str], str], float], None],
    ):
        self.settings = settings
        self.symbols_provider = symbols_provider
        self.category_provider = category_provider
        self.scan_runner = scan_runner

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._last_run_ts: float = 0.0
        self._lock = threading.Lock()

    # ==================== ПУБЛИЧНЫЙ API ====================

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            logger.warning("⚠️ LevelScanScheduler уже запущен")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="LevelScanScheduler", daemon=True,
        )
        self._thread.start()
        logger.info(
            f"⏰ LevelScanScheduler запущен "
            f"(enabled={self.settings.level_scan_enabled}, "
            f"hours={self.settings.level_scan_hours_utc} UTC)"
        )

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        logger.info("🛑 LevelScanScheduler остановлен")

    def trigger_now(self) -> None:
        """Принудительный запуск скана в фоновом потоке (для кнопки)."""
        t = threading.Thread(
            target=self._safe_run, name="LevelScanManual", daemon=True,
        )
        t.start()

    # ==================== ВНУТРЕННИЕ ====================

    def _loop(self) -> None:
        logger.debug("LevelScanScheduler: loop started")
        while not self._stop_event.is_set():
            try:
                if not self.settings.level_scan_enabled:
                    if self._stop_event.wait(_IDLE_POLL_SEC):
                        break
                    continue

                next_run = self._compute_next_run()
                if next_run is None:
                    if self._stop_event.wait(_IDLE_POLL_SEC):
                        break
                    continue

                now = datetime.now(UTC)
                sleep_sec = (next_run - now).total_seconds()
                if sleep_sec > 0:
                    # Просыпаемся не позже чем через час, чтобы
                    # перепроверить флаг enabled.
                    if self._stop_event.wait(min(sleep_sec, 3600.0)):
                        break
                    continue

                # Время пришло
                self._safe_run()

                # Защита от двойного запуска в ту же минуту
                time.sleep(2.0)

            except Exception as e:
                logger.error(
                    f"❌ LevelScanScheduler: ошибка в loop: {e}", exc_info=True,
                )
                if self._stop_event.wait(30.0):
                    break

        logger.debug("LevelScanScheduler: loop ended")

    def _safe_run(self) -> None:
        """Обёртка над scan_runner с логированием и защитой от исключений."""
        with self._lock:
            if not self.settings.level_scan_enabled:
                logger.info("⏭️ LevelScanScheduler: скан выключен, пропуск")
                return
            try:
                symbols = self.symbols_provider()
                if not symbols:
                    logger.info("⏭️ LevelScanScheduler: нет символов, пропуск")
                    return
                min_score = self.settings.level_scan_min_score
                logger.info(
                    f"🔍 LevelScanScheduler: старт скана "
                    f"({len(symbols)} символов, min_score={min_score})"
                )
                self.scan_runner(symbols, self.category_provider, min_score)
                self._last_run_ts = time.time()
                logger.info("✅ LevelScanScheduler: скан завершён")
            except Exception as e:
                logger.error(
                    f"❌ LevelScanScheduler: ошибка скана: {e}", exc_info=True,
                )

    def _compute_next_run(self) -> datetime | None:
        """
        Ближайшее время (UTC) из self.settings.parsed_hours_utc(),
        которое ещё не наступило. Если все часы в сегодняшних сутках
        прошли — берём первый час завтра.
        """
        hours = self.settings.parsed_hours_utc()
        if not hours:
            return None

        now = datetime.now(UTC)
        today = now.date()

        for h in hours:
            candidate = datetime(
                today.year, today.month, today.day,
                h, 0, 0, tzinfo=UTC,
            )
            if candidate > now + timedelta(seconds=_WAKE_MARGIN_SEC):
                return candidate

        tomorrow = today + timedelta(days=1)
        first_h = hours[0]
        return datetime(
            tomorrow.year, tomorrow.month, tomorrow.day,
            first_h, 0, 0, tzinfo=UTC,
        )