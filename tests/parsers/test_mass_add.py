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
    @pytest.mark.parametrize("tok,expected", [
        ("up", "up"),
        ("UP", "up"),
        ("long", "up"),
        ("LONG", "up"),
        ("вверх", "up"),
        ("ВВЕРХ", "up"),
        ("down", "down"),
        ("DOWN", "down"),
        ("short", "down"),
        ("вниз", "down"),
        ("any", "any"),
        ("ANY", "any"),
    ])
    def test_alias_maps(self, tok, expected):
        r = parse_mass_add(f"BTCUSDT 100 {tok}")
        assert len(r.valid) == 1
        assert r.valid[0].direction == expected
        assert r.valid[0].direction_raw == tok

    def test_unknown_direction_skipped(self):
        r = parse_mass_add("BTCUSDT 100 sideways")
        assert r.valid == []
        assert len(r.skipped) == 1
        assert r.skipped[0].reason == "direction"


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
        ("мусор", "формат"),
        ("BTCUSDT", "формат"),
        ("BTCUSDT 65000", "формат"),
        ("BTCUSDT abc up", "цена"),
        ("BTCUSDT -5 up", "цена"),
        ("BTCUSDT 0 up", "цена"),
        ("BTCUSDT 65000 sideways", "direction"),
        ("BTC 65000", "формат"),       # нет direction → "формат" (len(parts)<3)
        ("BTCUSDC 65000 up", "не USDT"),
        ("ETHBTC 65000 up", "не USDT"),
        ("B 65000 up", "не USDT"),      # нормализация не проходит
    ])
    def test_bad_lines_go_to_skipped(self, bad, expected_reason):
        r = parse_mass_add(bad)
        assert r.valid == []
        assert len(r.skipped) == 1
        s = r.skipped[0]
        assert s.line_no == 1
        assert s.raw == bad
        assert s.reason == expected_reason


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

    def test_note_does_not_contain_newlines(self):
        """\n всегда режется splitlines() — setup_note не может содержать перевод строки."""
        text = "BTCUSDT 65000 up line1"
        r = parse_mass_add(text)
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