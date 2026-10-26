"""
Миксин для UI и логики сканера уровней (Этап 6).
"""
from __future__ import annotations

import asyncio
import logging
import time

from src.core import level_scanner
from src.core.level_scanner import _round_price_for_callback
from src.core.settings import get_settings
from src.telegram import keyboards
from src.utils.formatting import html_escape

logger = logging.getLogger(__name__)


class LevelScanMixin:
    """
    Ожидает, что self содержит:
      - self.bybit_client
      - self.alerts_manager
      - self._level_scan_cache
      - self._level_scan_cache_ttl
      - self._last_level_scan_results
      - self._telegram_send
      - self._main_menu_kb()
      - self._show_rd_menu() (если нужно)
    """

    def set_telegram_sender(self, sender) -> None:
        """Устанавливает функцию отправки в Telegram (обычно bot.send_alert)."""
        self._telegram_send = sender

    def _collect_symbols_and_categories(self) -> tuple[list[str], dict[str, str]]:
        """Собирает уникальные символы из alerts.json и категории для них."""
        assets = self.alerts_manager.get_all_alerts()
        categories: dict[str, str] = {}
        for a in assets:
            if a.symbol not in categories:
                categories[a.symbol] = a.category or "linear"
        return list(categories.keys()), categories

    def _category_provider(self, symbol: str) -> str:
        """Callback для планировщика: category по символу (из alerts.json)."""
        for a in self.alerts_manager.get_all_alerts():
            if a.symbol == symbol:
                return a.category or "linear"
        return "linear"

    async def _show_level_scan_menu(self, query):
        s = get_settings()
        await query.edit_message_text(
            f"📊 <b>Сканер уровней</b>\n\n"
            f"Находит сильные уровни на 15m/4H/1D и предлагает добавить "
            f"их в алерты. Не влияет на текущие алерты.\n\n"
            f"🔔 Автоскан: <b>{s.describe_level_scan()}</b>\n"
            f"🎯 Порог score: <b>{s.describe_level_scan_score()}</b>\n\n"
            f"Запуск вручную или по расписанию.",
            parse_mode="HTML",
            reply_markup=keyboards.level_scan_menu_keyboard(
                s.level_scan_enabled,
                s.level_scan_hours_utc,
                s.level_scan_min_score,
            ),
        )

    async def _run_level_scan(self, query, force_refresh: bool = False):
        """Запускает скан (или отдаёт из кэша, если < 30 мин)."""
        now = time.time()
        cached = self._level_scan_cache.get("data")
        cached_ts = self._level_scan_cache.get("ts", 0.0)
        cache_age = now - cached_ts

        if (
            not force_refresh
            and cached is not None
            and cache_age < self._level_scan_cache_ttl
        ):
            logger.info(
                f"📥 Сканер уровней: из кэша (возраст {cache_age:.0f}с)"
            )
            summary, full, results, prices, compact_kb, full_kb = cached
            await query.edit_message_text(
                summary, parse_mode="HTML", reply_markup=compact_kb,
            )
            return

        await query.edit_message_text(
            "⏳ <b>Сканирую уровни...</b>\n"
            "<i>Это займёт 1–2 минуты, не закрывайте чат.</i>",
            parse_mode="HTML",
        )

        symbols, _ = self._collect_symbols_and_categories()
        if not symbols:
            await query.edit_message_text(
                "📋 Нет отслеживаемых активов — нечего сканировать.",
                reply_markup=self._main_menu_kb(),
            )
            return

        s = get_settings()
        try:
            (
                summary, full, results, prices, compact_kb, full_kb
            ) = await asyncio.to_thread(
                level_scanner.scan_and_format,
                symbols,
                self.bybit_client,
                category_provider=self._category_provider,
                min_score=s.level_scan_min_score,
            )
        except Exception as e:
            logger.error(f"❌ Ошибка скана уровней: {e}", exc_info=True)
            await query.edit_message_text(
                f"❌ Ошибка скана: <code>{html_escape(str(e))[:200]}</code>",
                parse_mode="HTML",
                reply_markup=self._main_menu_kb(),
            )
            return

        self._level_scan_cache = {
            "data": (summary, full, results, prices, compact_kb, full_kb),
            "ts": now,
        }
        self._last_level_scan_results = results

        await query.edit_message_text(
            summary, parse_mode="HTML", reply_markup=compact_kb,
        )

    async def _show_level_scan_all(self, query):
        """Показывает полный список уровней (все, не топ-1)."""
        cached = self._level_scan_cache.get("data")
        if not cached:
            await query.answer("Сначала запустите скан", show_alert=True)
            return
        _, full, results, prices, compact_kb, full_kb = cached
        if not results:
            await query.answer("Нет уровней", show_alert=True)
            return
        await query.edit_message_text(
            full, parse_mode="HTML", reply_markup=full_kb,
        )

    async def _show_level_scan_compact(self, query):
        """Возврат к компактному виду."""
        cached = self._level_scan_cache.get("data")
        if not cached:
            await query.answer("Сначала запустите скан", show_alert=True)
            return
        summary, full, results, prices, compact_kb, full_kb = cached
        await query.edit_message_text(
            summary, parse_mode="HTML", reply_markup=compact_kb,
        )

    async def _handle_level_scan_add(self, query, data: str):
        """Добавление одного уровня из сканера."""
        parts = data.split("|")
        if len(parts) != 4:
            await query.answer("Ошибка формата", show_alert=True)
            return
        _, symbol, price_str, direction = parts
        try:
            price = float(price_str)
        except ValueError:
            await query.answer("Ошибка цены", show_alert=True)
            return

        if self._level_exists(symbol, price, direction):
            await query.answer()
            await query.message.reply_text(
                f"⚠️ Уже есть: <b>{symbol}</b> @ "
                f"<code>{price}</code> {direction}",
                parse_mode="HTML",
            )
            return

        note = "сканер уровней"
        cached = self._level_scan_cache.get("data")
        if cached:
            _, _, results, _, _, _ = cached
            for c in results.get(symbol, []):
                if (
                    abs(c.price - price) / max(c.price, 1e-9) < 0.001
                    and c.direction_for_alert == direction
                ):
                    note = c.note or note
                    break

        category = self._category_provider(symbol)
        success, replaced = self.alerts_manager.add_alert(
            symbol, price, direction, category, note,
        )
        if success:
            verb = "🔄 Обновлён" if replaced else "✅ Добавлен"
            await query.answer(
                f"{verb}: {symbol} @ {price} {direction}", show_alert=False,
            )
        else:
            await query.answer(
                "❌ Не удалось сохранить", show_alert=True,
            )

    async def _handle_level_scan_add_symbol(self, query, symbol: str):
        """Добавляет ВСЕ уровни одного символа из последнего скана."""
        cached = self._level_scan_cache.get("data")
        if not cached:
            await query.answer("Сначала запустите скан", show_alert=True)
            return
        _, _, results, _, _, _ = cached
        cands = results.get(symbol) or []
        if not cands:
            await query.answer(f"Нет уровней для {symbol}", show_alert=True)
            return

        added = 0
        skipped = 0
        category = self._category_provider(symbol)

        for c in cands:
            if self._level_exists(symbol, c.price, c.direction_for_alert):
                skipped += 1
                continue
            price = float(_round_price_for_callback(c.price))
            success, _ = self.alerts_manager.add_alert(
                symbol, price, c.direction_for_alert, category,
                c.note or "сканер уровней",
            )
            if success:
                added += 1
            else:
                skipped += 1

        await query.answer()
        text = f"✅ <b>{symbol}</b>: добавлено {added}"
        if skipped:
            text += f", пропущено {skipped}"
        await query.message.reply_text(text, parse_mode="HTML")

    async def _handle_level_scan_add_all(self, query):
        """Добавляет ВСЕ уровни всех символов из последнего скана."""
        cached = self._level_scan_cache.get("data")
        if not cached:
            await query.answer("Сначала запустите скан", show_alert=True)
            return
        _, _, results, _, _, _ = cached
        if not results:
            await query.answer("Нет уровней", show_alert=True)
            return

        added = 0
        skipped = 0
        for sym, cands in results.items():
            category = self._category_provider(sym)
            for c in cands:
                if self._level_exists(sym, c.price, c.direction_for_alert):
                    skipped += 1
                    continue
                price = float(_round_price_for_callback(c.price))
                success, _ = self.alerts_manager.add_alert(
                    sym, price, c.direction_for_alert, category,
                    c.note or "сканер уровней",
                )
                if success:
                    added += 1
                else:
                    skipped += 1

        await query.answer()
        text = f"✅ Добавлено уровней: <b>{added}</b>"
        if skipped:
            text += f"\n⚠️ Пропущено (уже есть): <b>{skipped}</b>"
        await query.message.reply_text(text, parse_mode="HTML")

    def _level_exists(self, symbol: str, price: float, direction: str) -> bool:
        """Проверяет, есть ли уже такой (symbol, price, direction) в alerts.json."""
        for asset in self.alerts_manager.get_all_alerts():
            if asset.symbol != symbol:
                continue
            for rule in asset.alerts:
                if rule.direction != direction:
                    continue
                if abs(rule.price - price) / max(abs(price), 1e-9) < 1e-5:
                    return True
        return False

    def run_scheduled_level_scan(
        self,
        symbols: list[str],
        category_provider,
        min_score: float,
    ) -> None:
        """Синхронная точка входа для планировщика."""
        results = level_scanner.scan_all(
            symbols, self.bybit_client,
            category_provider=category_provider,
            min_score=min_score,
        )
        prices: dict[str, float] = {}
        for sym in results.keys():
            cat = category_provider(sym)
            ticker = self.bybit_client.get_ticker(sym, cat)
            if ticker:
                prices[sym] = ticker.price

        summary = level_scanner.format_summary(results, prices)
        compact_kb = level_scanner.build_keyboard(results)
        full = level_scanner.format_full_list(results, prices)
        full_kb = level_scanner.build_full_keyboard(results)

        self._level_scan_cache = {
            "data": (summary, full, results, prices, compact_kb, full_kb),
            "ts": time.time(),
        }
        self._last_level_scan_results = results

        if self._telegram_send is None:
            logger.warning("⚠️ Telegram sender не задан, скан не отправлен")
            return
        if not results:
            self._telegram_send(
                "📊 <b>СВОДКА УРОВНЕЙ</b>\n\nПусто — фильтры ничего не пропустили.",
                compact_kb,
            )
            return
        self._telegram_send(summary, compact_kb)