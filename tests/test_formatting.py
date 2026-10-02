"""Тесты src.utils.formatting.html_escape."""
from __future__ import annotations

from src.utils.formatting import html_escape


class TestHtmlEscape:
    def test_ampersand(self):
        assert html_escape("a & b") == "a &amp; b"

    def test_lt(self):
        assert html_escape("a < b") == "a &lt; b"

    def test_gt(self):
        assert html_escape("a > b") == "a &gt; b"

    def test_all_three(self):
        assert html_escape("<a> & <b>") == "&lt;a&gt; &amp; &lt;b&gt;"

    def test_order_amp_first(self):
        """'<' должен стать '&lt;', а не '&amp;lt;'."""
        assert html_escape("<") == "&lt;"
        assert "&amp;lt;" not in html_escape("<")

    def test_empty_string(self):
        assert html_escape("") == ""

    def test_none_returns_empty(self):
        # Хелпер принимает None — вернёт ""
        assert html_escape(None) == ""    # type: ignore[arg-type]

    def test_no_special(self):
        assert html_escape("BTC 65000 up") == "BTC 65000 up"

    def test_note_from_setup(self):
        assert html_escape("пробой & откат < 1%") == "пробой &amp; откат &lt; 1%"

    def test_cyrillic_untouched(self):
        assert html_escape("пробой уровня") == "пробой уровня"

    def test_emojis_untouched(self):
        assert html_escape("🚀 пробой 💥") == "🚀 пробой 💥"