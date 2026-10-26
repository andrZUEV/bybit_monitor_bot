"""
Миксин для экспорта данных (CSV), алертов и ручного ввода тикеров.
"""
from __future__ import annotations

import asyncio
import csv
import logging
import os
from datetime import datetime

from src.telegram import keyboards
from src.utils.config import Config
from src.utils.indicators import calculate_rsi_series
from telegram import InputFile, Update

logger = logging.getLogger(__name__)


class ExportMixin:
    """
    Ожидает, что self содержит:
      - self.bybit_client
      - self.alerts_manager
      - self.user_state
      - self._reset_state()
      - self._main_menu_kb()
    """

    async def _handle_export_tickers(self, update: Update, text: str, chat_id: int):
        """Обработка ввода тикеров для экспорта."""
        raw_tickers = text.replace(",", " ").split()
        checked_tickers: list[str] = []
        invalid_tickers: list[str] = []

        for t in raw_tickers:
            t = t.strip()
            if not t:
                continue
            resolved = self.bybit_client.resolve_symbol(t, "linear")
            if resolved:
                if resolved not in checked_tickers:
                    checked_tickers.append(resolved)
            else:
                invalid_tickers.append(t.upper())

        if not checked_tickers:
            invalid_msg = ", ".join(invalid_tickers) if invalid_tickers else "—"
            await update.message.reply_text(
                f"❌ Не найдено ни одного валидного тикера на Bybit:\n"
                f"<code>{invalid_msg}</code>\n\n"
                f"Можно писать как <code>BTC ETH</code>, так и <code>BTCUSDT ETHUSDT</code>.\n"
                f"Проверьте написание (например, <code>XRP</code>, а не <code>XPR</code>).",
                parse_mode="HTML",
                reply_markup=self._main_menu_kb(),
            )
            self._reset_state(chat_id)
            return

        await update.message.reply_text(
            f"⏳ Генерирую файл для {len(checked_tickers)} тикеров..."
        )
        filepath = await asyncio.to_thread(
            self._generate_export_file, checked_tickers
        )

        if filepath and os.path.exists(filepath):
            caption = f"✅ Данные выгружены для: {', '.join(checked_tickers)}"
            if invalid_tickers:
                caption += (
                    f"\n\n⚠️ Пропущены (не найдены на Bybit): "
                    f"{', '.join(invalid_tickers)}"
                )
            with open(filepath, "rb") as f:
                await update.message.reply_document(
                    document=InputFile(f, filename=os.path.basename(filepath)),
                    caption=caption,
                    reply_markup=keyboards.cancel_keyboard(),
                )
            os.remove(filepath)
        else:
            await update.message.reply_text("❌ Ошибка при генерации файла.")

        self._reset_state(chat_id)

    async def _handle_export_alert(self, update: Update, data: str):
        """
        Экспорт данных по одному активу.
        ВАЖНО: исходное сообщение алерта НЕ редактируется.
        """
        query = update.callback_query
        symbol = data.replace("export_alert_", "").upper()

        try:
            status_msg = await query.message.reply_text(
                f"⏳ Выгружаю данные <b>{symbol}</b>...", parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Не удалось отправить статусное сообщение: {e}")
            status_msg = None

        filepath = await asyncio.to_thread(
            self._generate_export_file, [symbol]
        )

        if filepath and os.path.exists(filepath):
            try:
                with open(filepath, "rb") as f:
                    await query.message.reply_document(
                        document=InputFile(f, filename=os.path.basename(filepath)),
                        caption=f"✅ Данные по <b>{symbol}</b>\n15m / 1H / 4H / 1D + RSI",
                    )
                if status_msg is not None:
                    try:
                        await status_msg.edit_text(
                            f"✅ Файл по <b>{symbol}</b> отправлен",
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass
            except Exception as e:
                logger.error(f"Ошибка отправки файла {symbol}: {e}", exc_info=True)
                if status_msg is not None:
                    try:
                        await status_msg.edit_text(
                            f"❌ Не удалось отправить файл по {symbol}"
                        )
                    except Exception:
                        pass
            finally:
                try:
                    os.remove(filepath)
                except Exception:
                    pass
        else:
            if status_msg is not None:
                try:
                    await status_msg.edit_text(
                        f"❌ Не удалось получить данные по <b>{symbol}</b>",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
            else:
                await query.message.reply_text(
                    f"❌ Не удалось получить данные по <b>{symbol}</b>",
                    parse_mode="HTML",
                )

    def _generate_export_file(self, tickers: list[str]) -> str | None:
        """Генерирует CSV с 15m/1H/4H/1D свечами и RSI."""
        try:
            Config.EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
            filename = f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            filepath = Config.EXPORTS_DIR / filename

            with open(filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(
                    ["Ticker", "Timeframe", "Time", "Open", "High", "Low", "Close", "Volume", "RSI"]
                )

                for ticker in tickers:
                    for tf, tf_name in [
                        ("15", "15m"),
                        ("60", "1H"),
                        ("240", "4H"),
                        ("D", "1D"),
                    ]:
                        try:
                            limit = Config.EXPORT_LIMITS.get(tf, 150)
                            klines = self.bybit_client.get_klines(ticker, "linear", tf, limit)
                            if not klines:
                                continue

                            closes = [float(k[4]) for k in klines]
                            rsi_values = calculate_rsi_series(closes, period=14)

                            for i, k in enumerate(reversed(klines)):
                                timestamp_ms = int(k[0])
                                dt = datetime.fromtimestamp(timestamp_ms / 1000)
                                time_str = dt.strftime("%Y-%m-%d %H:%M")
                                rsi_idx = len(klines) - 1 - i
                                rsi = (
                                    rsi_values[rsi_idx]
                                    if rsi_idx < len(rsi_values)
                                    else None
                                )
                                rsi_str = f"{rsi:.1f}" if rsi is not None else "N/A"

                                writer.writerow([
                                    ticker,
                                    tf_name,
                                    time_str,
                                    float(k[1]),
                                    float(k[2]),
                                    float(k[3]),
                                    float(k[4]),
                                    float(k[5]),
                                    rsi_str,
                                ])
                        except Exception as e:
                            logger.error(f"Ошибка данных для {ticker} {tf}: {e}")
                            continue

            logger.info(f"✅ Файл экспорта создан: {filepath}")
            return str(filepath)

        except Exception as e:
            logger.error(f"Ошибка генерации файла экспорта: {e}", exc_info=True)
            return None