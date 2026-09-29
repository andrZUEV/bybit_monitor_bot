"""
Telegram бот — запуск и отправка уведомлений.

Версия 2.0:
- Тип bybit_client: Optional[BybitClient] (был Optional[Any])
- Убран дублирующий импорт BybitClient внутри __init__
- Явный Optional[BybitClient] в сигнатуре
"""

import json
import sys
import os
import logging
import threading
import requests
import asyncio
from typing import Optional

from telegram import Update, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from src.core.alerts import AlertsManager
from src.api.bybit_client import BybitClient
from src.telegram.handlers import TelegramHandlers

logger = logging.getLogger(__name__)


class TelegramBot:
    """
    Telegram бот для управления алертами и отправки уведомлений.
    """

    def __init__(
        self,
        token: str,
        allowed_chat_id: str,
        alerts_manager: AlertsManager,
        bybit_client: Optional[BybitClient] = None,
    ):
        self.token = token
        self.allowed_chat_id = allowed_chat_id
        self.alerts_manager = alerts_manager

        # Если клиент не передан — создаём свой (для обратной совместимости)
        self.bybit_client = bybit_client or BybitClient()

        self.handlers = TelegramHandlers(
            alerts_manager=alerts_manager,
            allowed_chat_id=allowed_chat_id,
            bybit_client=self.bybit_client,
        )

        self._application: Optional[Application] = None
        self._thread: Optional[threading.Thread] = None
        self._running = True

    # ==================== СБОРКА ====================

    def _build_application(self) -> Application:
        """Создаёт и настраивает Application."""
        app = Application.builder().token(self.token).build()

        # Команды
        app.add_handler(CommandHandler("start", self.handlers.start_cmd))
        app.add_handler(CommandHandler("menu", self.handlers.menu_cmd))
        app.add_handler(CommandHandler("list", self.handlers.list_cmd))
        app.add_handler(CommandHandler("help", self.handlers.help_cmd))
        app.add_handler(CommandHandler("data", self.handlers.data_cmd))

        # Callback'и inline-кнопок
        app.add_handler(CallbackQueryHandler(self.handlers.handle_callback))

        # Текстовые сообщения (не команды)
        app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handlers.handle_text)
        )

        return app

    # ==================== ЗАПУСК / ОСТАНОВКА ====================

    def start(self):
        """Запускает бота (блокирующий вызов)."""
        logger.info("🤖 Telegram бот запускается...")
        self._application = self._build_application()

        # Python 3.12+: создаём event loop явно
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            loop.run_until_complete(self._run_async())
        except KeyboardInterrupt:
            logger.info("🛑 Бот остановлен пользователем (KeyboardInterrupt)")
        except Exception as e:
            logger.error(f"Ошибка бота: {e}", exc_info=True)
            raise
        finally:
            try:
                loop.close()
            except Exception:
                pass

    async def _run_async(self):
        """Async-обёртка запуска и остановки."""
        await self._application.initialize()
        await self._application.start()
        await self._application.updater.start_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )

        try:
            while self._running:
                await asyncio.sleep(1)
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        finally:
            if self._application.updater.running:
                await self._application.updater.stop()
            await self._application.stop()
            await self._application.shutdown()

    def start_async(self):
        """Запускает бота в отдельном потоке (неблокирующий)."""
        self._thread = threading.Thread(
            target=self.start, daemon=True, name="TelegramBot"
        )
        self._thread.start()
        logger.info("🤖 Telegram бот запущен в фоновом потоке")

    def stop(self):
        """Останавливает бота."""
        self._running = False
        logger.info("🛑 Сигнал остановки бота отправлен...")

    # ==================== ОТПРАВКА СООБЩЕНИЙ ====================

    def send_alert(self, message: str, reply_markup=None) -> bool:
        """
        Отправляет сообщение в Telegram (можно вызывать из любого потока).

        Использует прямой HTTP-запрос, а не Application, чтобы не зависеть
        от event loop. Это позволяет вызывать метод из Monitor без проблем.
        """
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.allowed_chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        # Telegram ожидает JSON-строку для клавиатуры
        if reply_markup:
            if isinstance(reply_markup, InlineKeyboardMarkup):
                payload["reply_markup"] = json.dumps(reply_markup.to_dict())
            else:
                payload["reply_markup"] = json.dumps(reply_markup)

        try:
            response = requests.post(url, data=payload, timeout=10)
            data = response.json()

            if data.get("ok"):
                logger.debug("Сообщение успешно отправлено в Telegram")
                return True

            logger.error(f"Ошибка отправки: {data.get('description')}")
            return False

        except Exception as e:
            logger.error(f"Ошибка отправки алерта: {e}")
            return False


# ==================== ТЕСТ ====================

if __name__ == "__main__":
    from src.utils.config import Config

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    ok, msg = Config.validate()
    if not ok:
        print(f"❌ {msg}")
        sys.exit(1)

    manager = AlertsManager(str(Config.ALERTS_FILE))

    bot = TelegramBot(
        token=Config.TELEGRAM_BOT_TOKEN,
        allowed_chat_id=Config.TELEGRAM_CHAT_ID,
        alerts_manager=manager,
    )

    print("📤 Отправляем тестовое сообщение...")
    bot.send_alert("🧪 <b>Тест</b>\nБот работает!")

    print("🤖 Запускаем бота. Нажмите Ctrl+C для остановки.\n")
    bot.start()