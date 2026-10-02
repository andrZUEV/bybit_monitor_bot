"""
Хелперы форматирования текста.

Пока только html_escape — единый источник правды для экранирования
символов &<> при вставке в Telegram HTML. Используется в analyzer.py,
handlers.py и других местах, где мы собираем HTML-сообщения.
"""


def html_escape(text: str) -> str:
    """
    Экранирует &<> для безопасной вставки в Telegram HTML.

    ВАЖНО: & экранируется ПЕРВЫМ, иначе получится &amp;lt;
    вместо &lt; для '<'.

    Пустая строка или None → "".
    """
    if not text:
        return ""
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )