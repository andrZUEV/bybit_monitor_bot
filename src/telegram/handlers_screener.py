"""
Миксин для скринера топ-активов и экрана «Текущие цены».
"""
from __future__ import annotations

import logging
import time

from src.telegram import keyboards
from telegram import Update

logger = logging.getLogger(__name__)


class ScreenerMixin:
    """
    Ожидает, что self содержит:
      - self.bybit_client
      - self.alerts_manager
      - self._screener_cache
      - self._screener_cache_ttl
      - self._last_screener_results
      - self._main_menu_kb()  (из CommonMixin)
    """

    async def _handle_current_prices(self, update: Update):
        """Показывает текущие цены по всем отслеживаемым активам."""
        query = update.callback_query

        try:
            await query.edit_message_text("⏳ Загружаю цены...")
        except Exception:
            pass

        assets = self.alerts_manager.get_all_alerts()
        if not assets:
            await query.edit_message_text(
                "📋 Пусто.", reply_markup=self._main_menu_kb()
            )
            return

        text = "💰 <b>Текущие цены:</b>\n\n"
        symbols = list({a.symbol for a in assets})

        for symbol in symbols:
            try:
                category = next(
                    (a.category for a in assets if a.symbol == symbol), "linear"
                )
                ticker = self.bybit_client.get_ticker(symbol, category)
                if ticker:
                    price_str = (
                        f"{ticker.price:,.4f}"
                        if ticker.price < 1000
                        else f"{ticker.price:,.2f}"
                    )
                    text += f"🪙 <b>{symbol}</b>: <code>{price_str}</code> $\n"
                else:
                    text += f"🪙 <b>{symbol}</b>: ❌ нет данных\n"
            except Exception as e:
                logger.error(f"Ошибка получения цены {symbol}: {e}")
                text += f"🪙 <b>{symbol}</b>: ⚠️ ошибка\n"

        try:
            await query.edit_message_text(
                text,
                parse_mode="HTML",
                reply_markup=self._main_menu_kb(),
            )
        except Exception as e:
            if "Message is not modified" not in str(e):
                logger.error(f"Ошибка в _handle_current_prices: {e}", exc_info=True)

    async def _run_screener_simple(self, update: Update, chat_id: int):
        """Скринер топ-активов по движению."""
        query = update.callback_query
        try:
            await query.edit_message_text("⏳ <b>Анализирую рынок...</b>", parse_mode="HTML")
        except Exception:
            pass

        cache_key = "screener_simple"
        cached = self._screener_cache.get(cache_key)

        if cached and (time.time() - cached[0]) < self._screener_cache_ttl:
            fetch_time, assets = cached
            from_cache = True
        else:
            assets = self.bybit_client.get_screener_data(
                category="linear",
                sort_by="volume_desc",
                min_volume_usd=1_000_000,
                min_change_abs=3.0,
                limit=20,
            )
            if assets:
                assets.sort(key=lambda x: abs(x.price_change_24h), reverse=True)
            fetch_time = time.time()
            self._screener_cache[cache_key] = (fetch_time, assets)
            from_cache = False

        if not assets:
            await query.edit_message_text(
                "❌ <b>Ничего не найдено</b>\n\nПопробуйте позже или уменьшите фильтры.",
                parse_mode="HTML",
                reply_markup=self._main_menu_kb(),
            )
            return

        self._last_screener_results = assets
        text = self._format_screener_simple(assets, fetch_time, from_cache)
        top_symbol = assets[0].symbol
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=keyboards.screener_add_alert_keyboard(top_symbol),
        )

    def _format_screener_simple(
        self, assets: list, fetch_time: float, from_cache: bool
    ) -> str:
        text = "🔍 <b>ТОП ПО ДВИЖЕНИЮ ЦЕНЫ</b> (24ч)\n"
        text += "<i>Отсортировано по абсолютному изменению</i>\n"
        text += "<i>Фильтры: >= 3% движения, >= 1M$ объема</i>\n\n"
        text += "━" * 30 + "\n\n"

        for i, asset in enumerate(assets[:15], 1):
            if asset.price_change_24h > 5:
                change_emoji = "🚀"
            elif asset.price_change_24h < -5:
                change_emoji = "💥"
            elif asset.price_change_24h > 0:
                change_emoji = "🟢"
            else:
                change_emoji = "🔴"

            vol_str = f"${asset.volume_24h / 1_000_000:.2f}M"
            if asset.price >= 100:
                price_str = f"{asset.price:,.2f}"
            elif asset.price >= 1:
                price_str = f"{asset.price:,.4f}"
            else:
                price_str = f"{asset.price:,.6f}"

            text += f"<b>{i:2d}.</b> <code>{asset.symbol}</code>\n"
            change_str = f"{asset.price_change_24h:+.2f}%"
            text += (
                f"    💰 <b>{price_str}</b> $ | "
                f"{change_emoji} <b>{change_str}</b>\n"
            )
            text += f"    📊 Объем: <b>{vol_str}</b>\n\n"

        text += "━" * 30 + "\n"
        cache_mark = "📥 Из кэша" if from_cache else "🔄 Свежие данные"
        text += f"<i>{cache_mark} ({int(time.time() - fetch_time)}с назад)</i>"
        return text