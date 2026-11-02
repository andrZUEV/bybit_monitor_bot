"""
Короткие идентификаторы для кнопки «Взять в дневник».

Telegram ограничивает callback_data 64 байтами. Цены с плавающей точкой
(например, 0.022999999999999998) легко превышают лимит, поэтому в callback
уходит только короткий id, а параметры сделки хранятся здесь, в памяти.

Ограничения:
  - id живёт до нажатия (pop) или до вытеснения самыми старыми записями;
  - после рестарта бота старые кнопки недействительны — это ожидаемо.
"""
from __future__ import annotations

import threading
from collections import OrderedDict

MAX_PENDING = 500

_lock = threading.RLock()
_items: OrderedDict[str, tuple] = OrderedDict()
_counter = 0


def register_vt_take(
    symbol: str,
    entry: float,
    stop: float,
    tp1: float,
    tp2: float | None,
    direction: str,
) -> str:
    """Регистрирует параметры сделки и возвращает короткий id для callback."""
    global _counter
    with _lock:
        _counter += 1
        key = f"{_counter:x}"
        _items[key] = (symbol, entry, stop, tp1, tp2, direction)
        while len(_items) > MAX_PENDING:
            _items.popitem(last=False)
    return key


def pop_vt_take(key: str) -> tuple | None:
    """Возвращает параметры по id и удаляет запись (одноразовая кнопка)."""
    with _lock:
        return _items.pop(key, None)


def clear_vt_take() -> None:
    """Сбрасывает все ожидающие сделки (для тестов)."""
    with _lock:
        _items.clear()
