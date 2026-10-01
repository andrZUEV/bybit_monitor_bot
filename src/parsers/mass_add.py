"""
Парсер массового и однострочного добавления алертов.

Формат строки:
    SYMBOL PRICE DIR [ОПИСАНИЕ]

Где:
    SYMBOL — тикер в любом регистре: BTC, btc, BTCUSDT, btcusdt, sOl.
             Нормализуется: upper + "USDT" если USDT-суффикса ещё нет.
             Только USDT-пары, только линейные фьючерсы.
             Если заканчивается на USDC/BTC/ETH (не USDT) → skip.
    PRICE  — число > 0. Запятая как десятичный разделитель допустима.
    DIR    — up | down | any | long | short | вверх | вниз (регистр не важен).
             long/вверх → up, short/вниз → down.
    ОПИСАНИЕ — всё, что идёт после DIR, как есть (включая "|", ":", "—", "#").
               Стрипается по краям. Пусто → None.

Пример:
    ETHUSDT 3200 down ретест | Стоп: 3400 | Тейк: 2900

Пустая строка — разделитель, НЕ попадает в skipped.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ==================== РЕГУЛЯРКИ ====================

# USDT-пара: 2–15 заглавных букв (без цифр в начале — как в реальности Bybit).
_SYMBOL_RE = re.compile(r"^[A-Z]{2,15}USDT$")

# Квоты, которые мы НЕ поддерживаем — если встретили суффикс, скипаем с "не USDT".
_UNSUPPORTED_QUOTES = ("USDC", "BTC", "ETH", "BUSD", "TUSD", "DAI")


# ==================== МАППИНГИ ====================

_DIRECTION_ALIASES: dict[str, str] = {
    "up": "up",
    "long": "up",
    "вверх": "up",
    "down": "down",
    "short": "down",
    "вниз": "down",
    "any": "any",
}


# ==================== DATACLASSES ====================

@dataclass(frozen=True)
class ParsedLine:
    """Одна успешно распарсенная строка."""
    raw: str                    # исходная строка (как пришла, до strip)
    line_no: int                # 1-based номер во входном тексте
    symbol: str                 # "BTCUSDT"
    price: float
    direction_raw: str          # как ввёл пользователь: "up", "вверх", "LONG"
    direction: str              # нормализовано: "up" | "down" | "any"
    setup_note: str | None   # всё после direction или None


@dataclass(frozen=True)
class SkippedLine:
    """Одна пропущенная строка с короткой причиной."""
    line_no: int
    raw: str
    reason: str                 # "формат" | "не USDT" | "цена" | "direction"


@dataclass
class ParseResult:
    valid: list[ParsedLine] = field(default_factory=list)
    skipped: list[SkippedLine] = field(default_factory=list)


# ==================== НОРМАЛИЗАЦИЯ ====================

def normalize_symbol(raw: str) -> str | None:
    """
    "sol" → "SOLUSDT", "sOl" → "SOLUSDT", "BTCUSDT" → "BTCUSDT".
    Если уже есть USDT (в любом регистре) — не дублируем.
    Если суффикс — неподдерживаемая квота (USDC и т.п.) → None.
    Если не проходит regex ^[A-Z]{2,15}USDT$ → None.
    """
    if not raw:
        return None

    s = raw.strip().upper()
    if not s:
        return None

    # Уже USDT-пара
    if s.endswith("USDT"):
        return s if _SYMBOL_RE.match(s) else None

    # Неподдерживаемые квоты — явный отказ
    for quote in _UNSUPPORTED_QUOTES:
        if s.endswith(quote) and len(s) > len(quote):
            return None

    # Дописываем USDT
    candidate = s + "USDT"
    return candidate if _SYMBOL_RE.match(candidate) else None


def normalize_direction(raw: str) -> str | None:
    """
    "up"/"LONG"/"вверх" → "up"; "down"/"short"/"вниз" → "down"; "any" → "any".
    Неизвестное → None.
    """
    if not raw:
        return None
    return _DIRECTION_ALIASES.get(raw.strip().lower())


# Символы-разделители, которые могут «прилипнуть» к началу setup_note
# сразу после direction и должны быть срезаны.
_LEADING_SEPARATORS = ("|", "-", "—", ":", "–")


def _clean_note(note: str) -> str | None:
    """
    Убирает пробелы по краям и срезает ведущие разделители (|, -, —, :),
    оставшиеся от разделения direction и описания.

    '| a' → 'a'
    '| Стоп: 3400 | Тейк: 2900' → 'Стоп: 3400 | Тейк: 2900'
    'ретест | Стоп: 3400' → 'ретест | Стоп: 3400'   (не начинается с разделителя)
    """
    s = note.strip()
    while s and s[0] in _LEADING_SEPARATORS:
        s = s[1:].lstrip()
    return s if s else None

# ==================== ПАРСИНГ ОДНОЙ СТРОКИ ====================

def _parse_line(raw: str, line_no: int) -> tuple[ParsedLine | None, str]:
    """
    Возвращает (ParsedLine | None, reason).

    reason используется только когда ParsedLine is None:
        "формат" | "не USDT" | "цена" | "direction"
    Пустая строка → (None, "") — вызывающий сам решит, что это разделитель.
    """
    stripped = raw.strip()
    if not stripped:
        return None, ""  # разделитель

    # maxsplit=3, чтобы setup_note остался одним куском
    parts = stripped.split(maxsplit=3)
    if len(parts) < 3:
        return None, "формат"

    symbol_raw, price_raw, direction_raw = parts[0], parts[1], parts[2]

    symbol = normalize_symbol(symbol_raw)
    if symbol is None:
        return None, "не USDT"

    try:
        price = float(price_raw.replace(",", "."))
    except ValueError:
        return None, "цена"
    if price <= 0:
        return None, "цена"

    direction = normalize_direction(direction_raw)
    if direction is None:
        return None, "direction"

    if len(parts) == 4:
        setup_note = _clean_note(parts[3])
    else:
        setup_note = None

    return (
        ParsedLine(
            raw=raw,
            line_no=line_no,
            symbol=symbol,
            price=price,
            direction_raw=direction_raw,
            direction=direction,
            setup_note=setup_note,
        ),
        "",
    )


# ==================== ПУБЛИЧНОЕ API ====================

def parse_mass_add(text: str) -> ParseResult:
    """
    Парсит многострочный (или однострочный) ввод.

    Пустые строки — разделители, НЕ попадают в skipped.
    Валидные строки — в valid (в порядке появления).
    Невалидные — в skipped с короткой причиной.
    """
    result = ParseResult()
    for i, raw in enumerate(text.splitlines(), start=1):
        parsed, reason = _parse_line(raw, i)
        if parsed is not None:
            result.valid.append(parsed)
        elif reason:
            result.skipped.append(SkippedLine(line_no=i, raw=raw, reason=reason))
        # reason == "" → пустая строка-разделитель, молча пропускаем
    return result