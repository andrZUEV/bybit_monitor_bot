"""
RuntimeSettings — настройки бота, меняемые на лету.

Хранятся в data/settings.json. Атомарная запись (mkstemp + os.replace),
потокобезопасно (threading.RLock). При отсутствии/битом файле — дефолты.

Главное поле:
    alert_min_score: float
        Порог score, ниже которого алерт НЕ отправляется.
        -inf → слать всё (режим тестирования).
         0.0 → слать только с score ≥ 0.
         3.0 → «железобетонные» (Strong).
"""
from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


DEFAULT_MIN_SCORE: float = float("-inf")


@dataclass
class RuntimeSettings:
    """Настройки, которые можно менять без рестарта бота."""
    alert_min_score: float = DEFAULT_MIN_SCORE

    # Путь хранилища (не сериализуется в JSON)
    _path: Path | None = None
    _lock: threading.RLock = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self._lock is None:
            self._lock = threading.RLock()

    # ---------- фабрики ----------

    @classmethod
    def load(cls, path: Path | str) -> RuntimeSettings:
        """
        Загружает настройки из JSON. Если файла нет или он битый — дефолты.

        Не падает никогда: любая проблема → дефолт + warning в лог.
        """
        p = Path(path)
        with open(p, encoding="utf-8") as f:
            data = json.load(f)

        min_score_raw = data.get("alert_min_score", DEFAULT_MIN_SCORE)
        min_score = cls._parse_score(min_score_raw, default=DEFAULT_MIN_SCORE)

        s = cls(alert_min_score=min_score)
        s._path = p
        return s

    @classmethod
    def load_or_default(cls, path: Path | str) -> RuntimeSettings:
        """
        Мягкая загрузка: если файла нет / он битый — создаём дефолт
        и записываем его на диск.
        """
        p = Path(path)
        if not p.exists():
            s = cls(alert_min_score=DEFAULT_MIN_SCORE)
            s._path = p
            s.save()
            return s

        try:
            return cls.load(p)
        except Exception as e:
            logger.warning(
                f"⚠️ settings.json битый или нечитаемый ({e}), "
                f"использую дефолты"
            )
            s = cls(alert_min_score=DEFAULT_MIN_SCORE)
            s._path = p
            return s

    # ---------- запись ----------

    def save(self) -> None:
        """Атомарно пишет JSON. Не падает, если path не задан."""
        if self._path is None:
            logger.warning("RuntimeSettings.save(): _path не задан, пропускаю")
            return

        path = self._path
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {"alert_min_score": self.alert_min_score}

            fd, tmp_name = tempfile.mkstemp(
                prefix=path.name + ".", suffix=".tmp", dir=str(path.parent),
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_name, path)
            except Exception:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise

    # ---------- хелперы ----------

    @staticmethod
    def _parse_score(raw: object, *, default: float) -> float:
        """
        Принимает float | int | str | "-inf" | "inf" | "-5" | None.
        На любой ошибке возвращает default.
        """
        if raw is None:
            return default
        if isinstance(raw, (int, float)):
            v = float(raw)
            if math.isnan(v):
                return default
            return v
        s = str(raw).strip().lower()
        if s in ("-inf", "-infinity", "все", "all"):
            return float("-inf")
        if s in ("inf", "+inf", "infinity"):
            return float("inf")
        try:
            return float(s)
        except ValueError:
            return default

    # ---------- представление для UI ----------

    def is_all(self) -> bool:
        """True, если порог = -inf (слать всё)."""
        return self.alert_min_score == float("-inf")

    def describe(self) -> str:
        """Короткое человекочитаемое описание порога."""
        if self.is_all():
            return "ВСЕ"
        return f"≥ {self.alert_min_score:g}"

# ==================== СИНГЛТОН ====================
#
# Один инстанс на процесс. main.py вызывает init_settings(path) один раз,
# все остальные модули (handlers, monitor) дёргают get_settings().
# В тестах можно переопределить через set_settings(RuntimeSettings(...)).

_settings_instance: RuntimeSettings | None = None
_settings_init_lock = threading.RLock()


def init_settings(path: Path | str) -> RuntimeSettings:
    """
    Инициализирует глобальный инстанс из файла. Вызывается один раз в main.py.

    Повторный вызов — перечитывает файл (полезно для hot-reload).
    """
    global _settings_instance
    with _settings_init_lock:
        _settings_instance = RuntimeSettings.load_or_default(path)
        return _settings_instance


def get_settings() -> RuntimeSettings:
    """
    Возвращает глобальный инстанс. Если init_settings не вызывался —
    создаёт дефолтный (без пути, в память).

    Такое поведение удобно для тестов: не нужно инициализировать
    глобальный инстанс, если он не нужен.
    """
    global _settings_instance
    with _settings_init_lock:
        if _settings_instance is None:
            _settings_instance = RuntimeSettings()
        return _settings_instance


def set_settings(settings: RuntimeSettings | None) -> None:
    """
    Переопределяет глобальный инстанс. Для тестов.

    set_settings(None) — сбрасывает, следующий get_settings() создаст дефолт.
    """
    global _settings_instance
    with _settings_init_lock:
        _settings_instance = settings