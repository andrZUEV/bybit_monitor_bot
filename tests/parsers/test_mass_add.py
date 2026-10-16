"""Тесты src.parsers.mass_add — актуальный формат (up/down/any, алиасы, USDT)."""
from __future__ import annotations

import pytest

from src.parsers.mass_add import (
    ParsedLine,
    normalize_direction,
    normalize_symbol,
    parse_mass_add,
)

# ==================== ВАЛИДНЫЕ СТРОКИ ====================

class TestValidLines:
    def test_minimal_line_no_note(self):
        r = parse_mass_add("BTCUSDT 65000 up")
        assert r.skipped == []
        assert r.valid == [
            ParsedLine(
                raw="BTCUSDT 65000 up",
                line_no=1,
                symbol="BTCUSDT",
                price=65000.0,
                direction_raw="up",
                direction="up",
                setup_note=None,
            )
        ]

    def test_real_world_line(self):
        """Пример из согласованного формата."""
        line = "ETHUSDT 3200 down ретест | Стоп: 3400 | Тейк: 2900"
        r = parse_mass_add(line)
        assert len(r.valid) == 1
        p = r.valid[0]
        assert p.symbol == "ETHUSDT"
        assert p.price == 3200.0
        assert p.direction == "down"
        assert p.direction_raw == "down"
        assert p.setup_note == "ретест | Стоп: 3400 | Тейк: 2900"

    def test_full_aave_line_from_user(self):
        line = (
            "AAVEUSDT 160.00 up отбой от поддержки | Вход: 160.20–160.80 | "
            "Стоп: 158.00 | Тейк: 166.50 (RR ~1:3)"
        )
        r = parse_mass_add(line)
        assert len(r.valid) == 1
        p = r.valid[0]
        assert p.symbol == "AAVEUSDT"
        assert p.price == 160.0
        assert p.direction == "up"
        assert p.setup_note == (
            "отбой от поддержки | Вход: 160.20–160.80 | "
            "Стоп: 158.00 | Тейк: 166.50 (RR ~1:3)"
        )


# ==================== АЛИАСЫ НАПРАВЛЕНИЯ ====================

class TestDirectionAliases:
    @pytest.mark.parametrize("bad,expected_reason", [
        # Строки, которые парсер ВИДИТ как «попытку алерта»
        # (первое слово — тикер, второе — число) → skipped с причиной.
        ("BTCUSDT 65000", "формат"),
        ("BTCUSDT -5 up", "цена"),
        ("BTCUSDT 0 up", "цена"),
        ("BTCUSDT 65000 sideways", "direction"),
        ("BTC 65000", "формат"),
        ("BTCUSDC 65000 up", "не USDT"),
        ("ETHBTC 65000 up", "не USDT"),
    ])
    def test_bad_lines_go_to_skipped(self, bad, expected_reason):
        r = parse_mass_add(bad)
        assert r.valid == []
        assert len(r.skipped) == 1
        s = r.skipped[0]
        assert s.line_no == 1
        assert s.raw == bad
        assert s.reason == expected_reason

    @pytest.mark.parametrize("junk", [
        "мусор",
        "BTCUSDT",              # одно слово — не тикерная строка
        "BTCUSDT abc up",       # второе слово не число
        "B 65000 up",           # тикер слишком короткий для _looks_like_ticker_start
        "какие-то мои комментарии",
    ])
    def test_junk_before_any_alert_is_ignored(self, junk):
        """Строки, не похожие на тикерные, до первого алерта — игнор (Q2=A)."""
        r = parse_mass_add(junk)
        assert r.valid == []
        assert r.skipped == []


    def test_junk_after_first_alert_is_skipped(self):
        """Мусор ПОСЛЕ первого алерта — skipped (парсер уже видел тикер)."""
        text = "\n".join([
            "BTCUSDT 82800 up",
            "мусор",
        ])
        r = parse_mass_add(text)
        assert len(r.valid) == 1
        assert len(r.skipped) == 1
        assert r.skipped[0].raw == "мусор"
        # `_parse_block("мусор")` → parts=["мусор"] → len<3 → "формат".
        # (Раньше я ждал "не USDT" — но проверка длины идёт первой.)
        assert r.skipped[0].reason == "формат"

# ==================== НОРМАЛИЗАЦИЯ ТИКЕРА ====================

class TestNormalizeSymbol:
    @pytest.mark.parametrize("raw,expected", [
        ("BTC", "BTCUSDT"),
        ("btc", "BTCUSDT"),
        ("bTc", "BTCUSDT"),
        ("SOL", "SOLUSDT"),
        ("sOl", "SOLUSDT"),
        ("eth", "ETHUSDT"),
        ("aave", "AAVEUSDT"),
        ("BTCUSDT", "BTCUSDT"),
        ("btcusdt", "BTCUSDT"),
        ("EthUsdt", "ETHUSDT"),
    ])
    def test_ok(self, raw, expected):
        assert normalize_symbol(raw) == expected

    @pytest.mark.parametrize("raw", [
        "",
        "   ",
        "BTCUSDC",       # не USDT
        "btcusdc",
        "ETHBTC",        # не USDT
        "B",             # слишком короткий
        "1INCH",         # начинается с цифры
        "BTC USDT",      # с пробелом — это уже не один токен
    ])
    def test_reject(self, raw):
        assert normalize_symbol(raw) is None


class TestNormalizeDirection:
    @pytest.mark.parametrize("raw,expected", [
        ("up", "up"), ("LONG", "up"), ("вверх", "up"),
        ("down", "down"), ("SHORT", "down"), ("вниз", "down"),
        ("any", "any"), ("ANY", "any"),
    ])
    def test_ok(self, raw, expected):
        assert normalize_direction(raw) == expected

    @pytest.mark.parametrize("raw", ["", "sideways", "xyz", "123"])
    def test_reject(self, raw):
        assert normalize_direction(raw) is None


# ==================== СКИПЫ ====================

class TestSkippedLines:
    @pytest.mark.parametrize("bad,expected_reason", [
        # Строки, которые парсер ВИДИТ как «попытку алерта»
        # (первое слово — тикер, второе — число) → skipped с причиной.
        ("BTCUSDT 65000", "формат"),
        ("BTCUSDT -5 up", "цена"),
        ("BTCUSDT 0 up", "цена"),
        ("BTCUSDT 65000 sideways", "direction"),
        ("BTC 65000", "формат"),
        ("BTCUSDC 65000 up", "не USDT"),
        ("ETHBTC 65000 up", "не USDT"),
    ])
    def test_bad_lines_go_to_skipped(self, bad, expected_reason):
        r = parse_mass_add(bad)
        assert r.valid == []
        assert len(r.skipped) == 1
        s = r.skipped[0]
        assert s.line_no == 1
        assert s.raw == bad
        assert s.reason == expected_reason

    @pytest.mark.parametrize("junk", [
        "мусор",
        "BTCUSDT",              # одно слово — не тикерная строка
        "BTCUSDT abc up",       # второе слово не число → не тикерная
        "B 65000 up",           # тикер слишком короткий для _looks_like_ticker_start
        "какие-то мои комментарии",
    ])
    def test_junk_before_any_alert_is_ignored(self, junk):
        """
        Строки, не похожие на тикерные, до первого алерта — игнор (Q2=A).
        Они не попадают ни в valid, ни в skipped.
        """
        r = parse_mass_add(junk)
        assert r.valid == []
        assert r.skipped == []


# ==================== ПУСТЫЕ СТРОКИ ====================

class TestBlankLines:
    def test_blank_lines_are_separators_not_skipped(self):
        text = "\n\nBTCUSDT 65000 up\n\n\nETHUSDT 3200 down\n\n"
        r = parse_mass_add(text)
        assert len(r.valid) == 2
        assert r.skipped == []

    def test_whitespace_only_is_separator(self):
        r = parse_mass_add("   \n\t\n   ")
        assert r.valid == []
        assert r.skipped == []

    def test_line_numbers_count_blanks(self):
        r = parse_mass_add("\n\nBTCUSDT 65000 up")
        assert r.valid[0].line_no == 3


# ==================== МИКС ====================

class TestMixedBatch:
    def test_order_and_line_numbers(self):
        text = "\n".join([
            "BTCUSDT 65000 up | a",
            "мусор",
            "ETHUSDT 3200 down - b",
            "BTCUSDT 65000",              # формат
            "SOL 150",                    # формат
            "AAVE 160 up",
        ])
        r = parse_mass_add(text)
        assert [p.symbol for p in r.valid] == ["BTCUSDT", "ETHUSDT", "AAVEUSDT"]
        assert [p.line_no for p in r.valid] == [1, 3, 6]
        assert [s.line_no for s in r.skipped] == [2, 4, 5]
        assert [s.reason for s in r.skipped] == ["формат", "формат", "формат"]
        assert r.valid[0].setup_note == "a"
        assert r.valid[1].setup_note == "b"
        assert r.valid[2].setup_note is None


# ==================== setup_note ====================

class TestSetupNote:
    def test_leading_pipe_is_stripped(self):
        """Ведущий '|' сразу после direction — это разделитель, а не часть note."""
        r = parse_mass_add("BTCUSDT 65000 up | Вход: 65000 | Стоп: 64000")
        assert r.valid[0].setup_note == "Вход: 65000 | Стоп: 64000"

    def test_inner_pipes_preserved(self):
        """Пайпы ВНУТРИ описания (после первого) — часть note."""
        r = parse_mass_add("BTCUSDT 65000 up ретест | Вход: 65000 | Стоп: 64000")
        assert r.valid[0].setup_note == "ретест | Вход: 65000 | Стоп: 64000"
    def test_hash_inside_note_preserved(self):
        r = parse_mass_add("BTCUSDT 65000 up # пробой хая")
        assert r.valid[0].setup_note == "# пробой хая"

    def test_dash_inside_note_preserved(self):
        r = parse_mass_add("BTCUSDT 65000 up вход на откате - после импульса")
        assert r.valid[0].setup_note == "вход на откате - после импульса"

    def test_note_with_only_spaces_is_none(self):
        r = parse_mass_add("BTCUSDT 65000 up     ")
        assert r.valid[0].setup_note is None

    def test_note_is_stripped(self):
        r = parse_mass_add("BTCUSDT 65000 up    note   ")
        assert r.valid[0].setup_note == "note"

    def test_leading_pipe_stripped_single_word(self):
        r = parse_mass_add("BTCUSDT 65000 up | line1")
        assert r.valid[0].setup_note == "line1"

    def test_single_line_note_has_no_newline(self):
        """Одиночная строка — setup_note без \\n."""
        text = "BTCUSDT 65000 up line1"
        r = parse_mass_add(text)
        assert r.valid[0].setup_note == "line1"
        assert "\n" not in (r.valid[0].setup_note or "")

    def test_leading_dash_is_stripped(self):
        r = parse_mass_add("BTCUSDT 65000 up - пробой хая")
        assert r.valid[0].setup_note == "пробой хая"

    def test_leading_emdash_is_stripped(self):
        r = parse_mass_add("BTCUSDT 65000 up — пробой хая")
        assert r.valid[0].setup_note == "пробой хая"

    def test_leading_colon_is_stripped(self):
        r = parse_mass_add("BTCUSDT 65000 up : пробой хая")
        assert len(r.valid) == 1
        assert r.valid[0].setup_note == "пробой хая"

    def test_direction_with_attached_colon_is_invalid(self):
        """`up:` без пробела — это один токен, direction не распознан."""
        r = parse_mass_add("BTCUSDT 65000 up: пробой хая")
        assert r.valid == []
        assert len(r.skipped) == 1
        assert r.skipped[0].reason == "direction"

    def test_leading_multiple_separators_stripped(self):
        r = parse_mass_add("BTCUSDT 65000 up || — пробой хая")
        assert r.valid[0].setup_note == "пробой хая"

    def test_hash_is_not_a_separator(self):
        """'#' НЕ входит в _LEADING_SEPARATORS — должен сохраниться."""
        r = parse_mass_add("BTCUSDT 65000 up # пробой хая")
        assert r.valid[0].setup_note == "# пробой хая"


# ==================== РЕГРЕССИЯ ФОРМАТА ====================

class TestRealUserExamples:
    """4 реальные строки, которые ты скидывал в самом начале."""

    REAL_1 = ("AAVEUSDT 160.00 up отбой от поддержки | Вход: 160.20–160.80 | "
              "Стоп: 158.00 | Тейк: 166.50 (RR ~1:3)")
    REAL_2 = ("AAVEUSDT 158.50 up отбой от поддержки | Вход: 158.70–159.20 | "
              "Стоп: 156.50 | Тейк: 165.00 (RR ~1:3.1)")
    REAL_3 = ("AAVEUSDT 157.00 down пробой поддержки | Вход: 156.80 | "
              "Стоп: 159.50 | Тейк: 152.00 (RR ~1:2.8)")
    REAL_4 = ("AAVEUSDT 166.00 down ретест сопротивления | Вход: 165.70–166.00 | "
              "Стоп: 168.50 | Тейк: 160.00 (RR ~1:3)")

    def test_four_real_lines_parse_ok(self):
        text = "\n".join([self.REAL_1, self.REAL_2, self.REAL_3, self.REAL_4])
        r = parse_mass_add(text)
        assert len(r.valid) == 4
        assert r.skipped == []
        assert [p.direction for p in r.valid] == ["up", "up", "down", "down"]
        assert [p.price for p in r.valid] == [160.0, 158.5, 157.0, 166.0]
        for p in r.valid:
            assert p.setup_note is not None
            assert "|" in p.setup_note

# ==================== МНОГОСТРОЧНЫЕ БЛОКИ ====================

class TestMultilineBlocks:
    """Continuation-строки склеиваются в setup_note предыдущего алерта."""

    def test_two_alerts_with_continuation_indent(self):
        """Continuation с отступом (пробел в начале)."""
        text = "\n".join([
            "BTCUSDT 82800 up отбой",
            "  Entry: 82800–82850 | SL: 82200 | TP1: 83600",
            "",
            "BTCUSDT 84300 down ретест",
            "  Entry: 84300–84350 | SL: 84850 | TP1: 83600",
        ])
        r = parse_mass_add(text)
        assert len(r.valid) == 2
        assert r.skipped == []
        assert r.valid[0].setup_note == (
            "отбой\nEntry: 82800–82850 | SL: 82200 | TP1: 83600"
        )
        assert r.valid[1].setup_note == (
            "ретест\nEntry: 84300–84350 | SL: 84850 | TP1: 83600"
        )

    def test_continuation_without_indent_prefix(self):
        """Continuation без отступа, но с префиксом 'Entry:'."""
        text = "\n".join([
            "BTCUSDT 82800 up отбой",
            "Entry: 82800 | SL: 82200",
        ])
        r = parse_mass_add(text)
        assert len(r.valid) == 1
        assert r.valid[0].setup_note == "отбой\nEntry: 82800 | SL: 82200"

    def test_real_user_example_5_alerts(self):
        """Реальный ввод: 5 алертов с Entry-строками."""
        text = "\n".join([
            "BTCUSDT 82800 up отбой от 1H/4H поддержки",
            "  Entry: 82800–82850 | SL: 82200 | TP1: 83600",
            "",
            "BTCUSDT 84300 down ретест 4H сопротивления",
            "  Entry: 84300–84350 | SL: 84850",
            "",
            "ETHUSDT 2500 up отбой",
            "  Entry: 2498–2502",
            "",
            "SOLUSDT 107.80 up отбой",
            "  Entry: 107.70–107.90",
            "",
            "AVAXUSDT 10.46 up отбой",
            "  Entry: 10.44–10.48",
        ])
        r = parse_mass_add(text)
        assert len(r.valid) == 5
        assert r.skipped == []
        assert [p.symbol for p in r.valid] == [
            "BTCUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT",
        ]
        for p in r.valid:
            assert "\n" in (p.setup_note or "")
            assert "Entry:" in (p.setup_note or "")

    def test_no_note_but_continuation(self):
        """В тикерной строке нет note, но есть continuation-строка."""
        text = "\n".join([
            "BTCUSDT 82800 up",
            "  Entry: 82800–82850",
        ])
        r = parse_mass_add(text)
        assert len(r.valid) == 1
        assert r.valid[0].setup_note == "Entry: 82800–82850"

    def test_continuation_before_first_alert_ignored(self):
        """Строки до первого тикера — игнор (Q2=A)."""
        text = "\n".join([
            "какие-то мои комментарии",
            "  и ещё одна строка",
            "BTCUSDT 82800 up отбой",
            "  Entry: 82800",
        ])
        r = parse_mass_add(text)
        assert len(r.valid) == 1
        assert r.skipped == []
        assert r.valid[0].setup_note == "отбой\nEntry: 82800"

    def test_multiple_continuation_lines(self):
        """Несколько continuation-строк — все в note через \\n."""
        text = "\n".join([
            "BTCUSDT 82800 up отбой",
            "  Entry: 82800–82850",
            "  SL: 82200",
            "  TP1: 83600",
        ])
        r = parse_mass_add(text)
        assert len(r.valid) == 1
        assert r.valid[0].setup_note == (
            "отбой\nEntry: 82800–82850\nSL: 82200\nTP1: 83600"
        )

    def test_blank_lines_inside_block_ignored(self):
        """Пустые строки внутри блока не ломают склейку."""
        text = "\n".join([
            "BTCUSDT 82800 up отбой",
            "",
            "  Entry: 82800",
            "",
            "  SL: 82200",
        ])
        r = parse_mass_add(text)
        assert len(r.valid) == 1
        assert r.valid[0].setup_note == "отбой\nEntry: 82800\nSL: 82200"

    def test_broken_first_line_block_goes_to_skipped(self):
        """Битый тикер — весь блок в skipped, continuation не выносится."""
        text = "\n".join([
            "BTCUSDC 82800 up отбой",
            "  Entry: 82800",
        ])
        r = parse_mass_add(text)
        assert len(r.valid) == 0
        assert len(r.skipped) == 1
        assert r.skipped[0].reason == "не USDT"
        assert r.skipped[0].line_no == 1

    def test_multiline_returns_correct_line_numbers(self):
        """line_no — номер ТИКЕРНОЙ строки в исходном тексте."""
        text = "\n".join([
            "BTCUSDT 82800 up",
            "  Entry: 82800",
            "",
            "ETHUSDT 2500 up",
            "  Entry: 2500",
        ])
        r = parse_mass_add(text)
        assert [p.line_no for p in r.valid] == [1, 4]

    def test_mixed_valid_and_broken(self):
        """Порядок: valid и skipped, каждый со своим line_no."""
        text = "\n".join([
            "BTCUSDT 82800 up отбой",
            "  Entry: 82800",
            "мусор",
            "ETHUSDT 2500 up",
            "  Entry: 2500",
        ])
        r = parse_mass_add(text)
        assert [p.symbol for p in r.valid] == ["BTCUSDT", "ETHUSDT"]
        assert len(r.skipped) == 1
        assert r.skipped[0].raw == "мусор"
        assert r.skipped[0].reason == "формат"