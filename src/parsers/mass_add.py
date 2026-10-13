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

# ==================== ГРУППИРОВКА СТРОК В БЛОКИ ====================

def _is_continuation(raw: str) -> bool:
    """
    True, если строка — продолжение предыдущего блока (не новая тикерная строка).

    Признаки continuation:
      - начинается с пробела/таба (отступ);
      - начинается с известного префикса трейд-плана:
        "Entry:", "SL:", "TP1:", "вход:", "стоп:", "тейк:", "|", "-", "—", "•" и т.п.

    Иначе — новая тикерная строка (или мусор, который парсер попробует
    разобрать отдельным блоком).
    """
    if not raw:
        return False
    if raw[0] in (" ", "\t"):
        return True

    lowered = raw.lstrip().lower()
    prefixes = (
        "entry:", "sl:", "tp1:", "tp2:", "tp3:",
        "stop:", "stop-loss:", "take:", "take-profit:",
        "вход:", "стоп:", "тейк:", "цель:", "отмена:",
        "|", "-", "—", "–", "•", "*", "→",
    )
    return any(lowered.startswith(p) for p in prefixes)


def _looks_like_ticker_start(raw: str) -> bool:
    """
    True, если строка похожа на начало тикерного алерта: первое слово —
    буквенно-цифровое (тикер), второе — число (цена).

    Это НЕ проверка валидности алерта — только «похоже ли на тикерную».
    Финальный вердикт (valid / skipped) выносит _parse_block.
    """
    parts = raw.strip().split(maxsplit=2)
    if len(parts) < 2:
        return False
    if not re.match(r"^[A-Za-z][A-Za-z0-9]{1,14}$", parts[0]):
        return False
    try:
        float(parts[1].replace(",", "."))
    except ValueError:
        return False
    return True


def _group_into_blocks(text: str) -> list[list[tuple[int, str]]]:
    """
    Группирует строки в блоки.

    Логика:
      - пустые строки — игнорируются;
      - строка-продолжение (отступ / "Entry:" / "|" и т.п.) при
        наличии текущего блока — добавляется в текущий блок;
      - строка, похожая на тикерную, — начинает новый блок;
      - всё прочее ДО первого валидного тикера — тихо игнорируется (Q2=A);
      - всё прочее ПОСЛЕ первого тикера — становится новым блоком
        (парсер сам решит, valid это или skipped).

    Возвращает list[list[(line_no, raw_line)]], блоки в порядке появления.
    """
    blocks: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    seen_ticker = False   # уже встречали ли тикерную строку

    for i, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue

        # Continuation — только если есть к чему продолжать.
        if current and _is_continuation(raw):
            current.append((i, raw))
            continue

        # Новая тикерная строка — начинает блок.
        if _looks_like_ticker_start(raw):
            if current:
                blocks.append(current)
            current = [(i, raw)]
            seen_ticker = True
            continue

        # Не тикерная, не continuation.
        if not seen_ticker:
            # Ещё не было ни одного тикера — тихо игнорируем (Q2=A).
            continue

        # Уже видели тикер ранее — это «новый блок», парсер решит,
        # что с ним делать (скорее всего skipped).
        if current:
            blocks.append(current)
        current = [(i, raw)]

    if current:
        blocks.append(current)
    return blocks


def _parse_block(
    block: list[tuple[int, str]],
) -> tuple[ParsedLine | None, SkippedLine | None]:
    """
    Парсит блок строк как один алерт.

    Первая строка блока — SYMBOL PRICE DIR [NOTE].
    Последующие строки — продолжение setup_note (склеиваются через \\n).

    Возвращает:
      (ParsedLine, None)  — успех.
      (None, SkippedLine) — ошибка в первой строке блока.
    """
    line_no_first, first_raw = block[0]

    stripped = first_raw.strip()
    parts = stripped.split(maxsplit=3)
    if len(parts) < 3:
        return None, SkippedLine(
            line_no=line_no_first, raw=first_raw, reason="формат",
        )

    symbol_raw, price_raw, direction_raw = parts[0], parts[1], parts[2]

    symbol = normalize_symbol(symbol_raw)
    if symbol is None:
        return None, SkippedLine(
            line_no=line_no_first, raw=first_raw, reason="не USDT",
        )

    try:
        price = float(price_raw.replace(",", "."))
    except ValueError:
        return None, SkippedLine(
            line_no=line_no_first, raw=first_raw, reason="цена",
        )
    if price <= 0:
        return None, SkippedLine(
            line_no=line_no_first, raw=first_raw, reason="цена",
        )

    direction = normalize_direction(direction_raw)
    if direction is None:
        return None, SkippedLine(
            line_no=line_no_first, raw=first_raw, reason="direction",
        )

    # --- Сборка setup_note ---
    # Часть 1: остаток первой строки после DIR.
    first_note_raw = parts[3] if len(parts) == 4 else ""
    first_note_clean = _clean_note(first_note_raw) if first_note_raw else None

    # Часть 2: continuation-строки блока (без пустых, strip по краям).
    continuation_lines: list[str] = []
    for _, raw_line in block[1:]:
        s = raw_line.strip()
        if s:
            continuation_lines.append(s)

    note_parts: list[str] = []
    if first_note_clean:
        note_parts.append(first_note_clean)
    note_parts.extend(continuation_lines)

    setup_note: str | None = "\n".join(note_parts) if note_parts else None

    return (
        ParsedLine(
            raw=first_raw,
            line_no=line_no_first,
            symbol=symbol,
            price=price,
            direction_raw=direction_raw,
            direction=direction,
            setup_note=setup_note,
        ),
        None,
    )


# ==================== ПУБЛИЧНОЕ API ====================

def parse_mass_add(text: str) -> ParseResult:
    """
    Парсит многострочный (или однострочный) ввод.

    Группирует строки в блоки: тикерная строка + последующие
    continuation-строки (отступ / "Entry:", "|" и т.п.) образуют один
    алерт. Continuation-строки склеиваются в setup_note через \\n.

    Пустые строки — игнорируются. Строки, не ставшие частью ни одного
    валидного алерта (битые тикерные строки) — попадают в skipped.
    """
    result = ParseResult()
    for block in _group_into_blocks(text):
        parsed, skipped = _parse_block(block)
        if parsed is not None:
            result.valid.append(parsed)
        elif skipped is not None:
            result.skipped.append(skipped)
    return result