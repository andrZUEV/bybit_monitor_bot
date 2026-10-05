"""
История последних алертов для анализа качества оценки.

Формат хранения: JSONL (одна строка = одна запись). Дешёвый append,
устойчивость к обрывам, легко парсится потоково.

При превышении max_records файл обрезается до последних max_records.
max_records можно менять на лету через RuntimeSettings.

Запись атомарна внутри процесса (RLock). Между процессами не гарантируется —
но у нас один процесс на бота, этого достаточно.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_MAX_RECORDS = 1000


# ==================== DATACLASS ====================

@dataclass
class AlertRecord:
    """
    Запись об одном сработавшем (или пропущенном) алерте.

    Содержит сырые данные свечи, веса и итоговое решение системы.
    Используется для оффлайн-анализа: правильна ли оценка.
    """

    # ---------- Метаданные ----------
    timestamp: float              # unix-время срабатывания (когда evaluate_alert вызван)
    symbol: str
    level: float
    direction: str                # 'up' | 'down' | 'any'
    alert_age_hours: float        # возраст алерта на момент срабатывания
    final_score: float
    verdict: str                  # "💪 Strong" | "⚠️ Weak" | "❌ None"
    was_sent: bool                # отправлен ли в Telegram (с учётом гейтов)
    skip_reason: str | None       # если не отправлен: "score_below" | "hard_filter_disabled" | None

    # ---------- Свеча ----------
    candle_ts: int                # timestamp_ms закрытой свечи (klines[1][0])
    candle: dict[str, float]      # open, high, low, close, volume
    prev_candle: dict[str, float] # open, high, low, close
    volume_ratio: float
    rsi: float | None
    body_size: float
    upper_wick: float
    lower_wick: float
    body_to_range_ratio: float
    close_position: str           # "upper" | "middle" | "lower"
    close_pos_ratio: float        # (close - low) / (high - low), 0..1

    # ---------- Паттерн ----------
    detected_pattern: str | None
    pattern_score: float
    pattern_reason: str

    # ---------- Подтверждения ----------
    wick_beyond_level: bool
    close_in_correct_third: bool
    volume_score: float
    rsi_score: float
    worn_level: bool
    touches_count: int
    htf_trend: str                # "up" | "down" | "side"
    htf_against: bool

    # ---------- Контекст ----------
    timeframe_used: str           # "15m"
    atr_value: float | None
    hard_filter: str | None

    # ---------- Служебное ----------
    recorded_at: float = field(default_factory=time.time)  # когда запись создана


# ==================== ХРАНИЛИЩЕ ====================

class AlertHistory:
    """
    Хранилище последних AlertRecord в JSONL-файле.

    При превышении max_records обрезает файл до max_records.
    """

    def __init__(
        self,
        path: Path | str,
        max_records: int = DEFAULT_MAX_RECORDS,
    ):
        self.path = Path(path)
        self.max_records = max(1, int(max_records))
        self.path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()

    # ---------- запись ----------

    def append(self, record: AlertRecord) -> None:
        """Добавляет запись в файл. При превышении лимита — обрезает."""
        with self._lock:
            line = json.dumps(asdict(record), ensure_ascii=False)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())

            if self._count_unlocked() > self.max_records:
                self._prune_unlocked()

    # ---------- чтение ----------

    def load_all(self) -> list[AlertRecord]:
        """Возвращает все записи (может быть меньше max_records)."""
        with self._lock:
            return self._load_unlocked()

    def count(self) -> int:
        """Количество записей в файле."""
        with self._lock:
            return self._count_unlocked()

    def is_empty(self) -> bool:
        return self.count() == 0

    # ---------- управление ----------

    def clear(self) -> None:
        """Очищает файл."""
        with self._lock:
            if self.path.exists():
                self.path.unlink()
            logger.info("🗑 История алертов очищена")

    def set_max_records(self, n: int) -> None:
        """
        Меняет глубину хранения. Если записей больше нового лимита —
        сразу обрезает.
        """
        with self._lock:
            self.max_records = max(1, int(n))
            if self._count_unlocked() > self.max_records:
                self._prune_unlocked()
            logger.info(f"📜 Глубина истории: {self.max_records}")

    # ---------- экспорт ----------

    def export_csv(self, out_path: Path | str) -> Path:
        """
        Сохраняет историю в CSV (плоский формат). Возвращает путь.

        Вложенные dict (candle, prev_candle) превращаются в колонки
        candle_open, candle_high, ... и prev_candle_open, ...
        """
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        records = self.load_all()

        # Собираем все возможные колонки
        base_cols = [
            "timestamp", "symbol", "level", "direction",
            "alert_age_hours", "final_score", "verdict",
            "was_sent", "skip_reason",
            "candle_ts", "volume_ratio", "rsi",
            "body_size", "upper_wick", "lower_wick",
            "body_to_range_ratio", "close_position", "close_pos_ratio",
            "detected_pattern", "pattern_score", "pattern_reason",
            "wick_beyond_level", "close_in_correct_third",
            "volume_score", "rsi_score",
            "worn_level", "touches_count",
            "htf_trend", "htf_against",
            "timeframe_used", "atr_value", "hard_filter",
            "candle_open", "candle_high", "candle_low", "candle_close", "candle_volume",
            "prev_candle_open", "prev_candle_high", "prev_candle_low", "prev_candle_close",
            "recorded_at",
        ]

        with open(out, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(base_cols)

            for r in records:
                candle = r.candle or {}
                prev = r.prev_candle or {}
                row = [
                    r.timestamp, r.symbol, r.level, r.direction,
                    r.alert_age_hours, r.final_score, r.verdict,
                    r.was_sent, r.skip_reason or "",
                    r.candle_ts, r.volume_ratio, r.rsi if r.rsi is not None else "",
                    r.body_size, r.upper_wick, r.lower_wick,
                    r.body_to_range_ratio, r.close_position, r.close_pos_ratio,
                    r.detected_pattern or "", r.pattern_score, r.pattern_reason,
                    r.wick_beyond_level, r.close_in_correct_third,
                    r.volume_score, r.rsi_score,
                    r.worn_level, r.touches_count,
                    r.htf_trend, r.htf_against,
                    r.timeframe_used, r.atr_value if r.atr_value is not None else "",
                    r.hard_filter or "",
                    candle.get("open"), candle.get("high"), candle.get("low"),
                    candle.get("close"), candle.get("volume"),
                    prev.get("open"), prev.get("high"), prev.get("low"), prev.get("close"),
                    r.recorded_at,
                ]
                writer.writerow(row)

        logger.info(f"📄 История алертов выгружена: {out} ({len(records)} записей)")
        return out

    # ---------- внутренние ----------

    def _count_unlocked(self) -> int:
        if not self.path.exists():
            return 0
        with open(self.path, encoding="utf-8") as f:
            return sum(1 for _ in f)

    def _load_unlocked(self) -> list[AlertRecord]:
        if not self.path.exists():
            return []
        records: list[AlertRecord] = []
        with open(self.path, encoding="utf-8") as f:
            for i, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    records.append(AlertRecord(**data))
                except (json.JSONDecodeError, TypeError, KeyError) as e:
                    logger.warning(
                        f"⚠️ Пропущена битая запись истории (строка {i}): {e}"
                    )
        return records

    def _prune_unlocked(self) -> None:
        """
        Оставляет последние max_records записей. Атомарная перезапись
        через mkstemp + os.replace.
        """
        if not self.path.exists():
            return

        with open(self.path, encoding="utf-8") as f:
            lines = [ln for ln in f if ln.strip()]

        keep = lines[-self.max_records:]
        removed = len(lines) - len(keep)

        fd, tmp_name = tempfile.mkstemp(
            prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.writelines(keep)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, self.path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

        logger.info(
            f"✂️ История алертов обрезана: удалено {removed}, "
            f"осталось {len(keep)}"
        )

# ==================== ФАБРИКА ====================

def build_alert_record(
    evaluation: dict,
    symbol: str,
    level: float,
    direction: str,
    current_price: float,
    alert_age_hours: float,
    volume_ratio: float,
    was_sent: bool,
    skip_reason: str | None = None,
) -> AlertRecord:
    """
    Собирает AlertRecord из результата evaluate_alert + метаданных.

    Значения по умолчанию — на случай отсутствия ключа в evaluation
    (например, если evaluate_alert вернул empty-словарь).
    """
    return AlertRecord(
        timestamp=time.time(),
        symbol=symbol,
        level=float(level),
        direction=direction,
        alert_age_hours=float(alert_age_hours),
        final_score=float(evaluation.get("score", 0.0)),
        verdict=str(evaluation.get("verdict", "❌ None")),
        was_sent=was_sent,
        skip_reason=skip_reason,

        candle_ts=int(evaluation.get("candle_ts", 0)),
        candle=dict(evaluation.get("candle") or {}),
        prev_candle=dict(evaluation.get("prev_candle") or {}),
        volume_ratio=float(volume_ratio),
        rsi=(
            float(evaluation["rsi"])
            if evaluation.get("rsi") is not None
            else None
        ),
        body_size=float(evaluation.get("body_size", 0.0)),
        upper_wick=float(evaluation.get("upper_wick", 0.0)),
        lower_wick=float(evaluation.get("lower_wick", 0.0)),
        body_to_range_ratio=float(evaluation.get("body_to_range_ratio", 0.0)),
        close_position=str(evaluation.get("close_position", "middle")),
        close_pos_ratio=float(evaluation.get("close_pos_ratio", 0.5)),

        detected_pattern=evaluation.get("pattern"),
        pattern_score=float(evaluation.get("pattern_score", 0.0)),
        pattern_reason=str(evaluation.get("pattern_reason", "")),

        wick_beyond_level=bool(evaluation.get("wick_beyond_level", False)),
        close_in_correct_third=bool(evaluation.get("close_in_correct_third", False)),
        volume_score=float(evaluation.get("volume_score", 0.0)),
        rsi_score=float(evaluation.get("rsi_score", 0.0)),
        worn_level=(
            int(evaluation.get("touches", 0)) >= 5
        ),
        touches_count=int(evaluation.get("touches", 0)),
        htf_trend=str(evaluation.get("htf_trend", "side")),
        htf_against=bool(evaluation.get("htf_against", False)),

        timeframe_used="15m",
        atr_value=(
            float(evaluation["atr_value"])
            if evaluation.get("atr_value") is not None
            else None
        ),
        hard_filter=evaluation.get("hard_filter"),
    )

# ==================== СИНГЛТОН ====================
#
# Один инстанс на процесс. main.py вызывает init_alert_history() один раз.
# Monitor и хендлеры дёргают get_alert_history().

_history_instance: AlertHistory | None = None
_history_init_lock = threading.RLock()


def init_alert_history(
    path: Path | str,
    max_records: int = DEFAULT_MAX_RECORDS,
) -> AlertHistory:
    """Инициализирует глобальный инстанс. Вызывается один раз в main.py."""
    global _history_instance
    with _history_init_lock:
        _history_instance = AlertHistory(path, max_records=max_records)
        return _history_instance


def get_alert_history() -> AlertHistory:
    """
    Возвращает глобальный инстанс. Если init_alert_history не вызывался —
    создаёт с дефолтным путём.

    Для тестов можно переопределить через set_alert_history(AlertHistory(...)).
    """
    global _history_instance
    with _history_init_lock:
        if _history_instance is None:
            from src.utils.config import Config
            _history_instance = AlertHistory(
                path=Config.ALERT_HISTORY_FILE,
                max_records=DEFAULT_MAX_RECORDS,
            )
        return _history_instance


def set_alert_history(history: AlertHistory | None) -> None:
    """Переопределяет глобальный инстанс. Для тестов."""
    global _history_instance
    with _history_init_lock:
        _history_instance = history

# ==================== СИНГЛТОН ====================

_history_instance: AlertHistory | None = None
_history_init_lock = threading.RLock()

