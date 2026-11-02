#!/usr/bin/env python
"""
Живой smoke-тест Telegram-доставки (Этап 11).

Что проверяет (по реальному API, без запуска бота и без getUpdates):
  1. Токен валиден (`getMe`).
  2. HTML-сообщение с спецсимволами доходит и не ломает parse_mode.
  3. Инлайн-кнопка с callback_data отправляется.
  4. Сообщение редактируется (как это делает бот в меню).

Запуск (из корня проекта, где лежит .env):
    .venv\\Scripts\\python.exe scripts/smoke_telegram.py
    .venv\\Scripts\\python.exe scripts/smoke_telegram.py --chat 123456789

Бот при этом НЕ запускается: getUpdates не вызывается, чтобы не мешать
работающему экземпляру. Отправляется 1–2 сообщения в указанный чат.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:  # dotenv не обязателен, если переменные заданы в окружении
    pass

API = "https://api.telegram.org/bot{token}/{method}"


def _call(token: str, method: str, **params):
    response = requests.post(
        API.format(token=token, method=method), data=params, timeout=20,
    )
    try:
        payload = response.json()
    except ValueError:
        return False, {"http": response.status_code, "raw": response.text[:200]}
    return bool(payload.get("ok")), payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke-тест Telegram-доставки")
    parser.add_argument("--chat", help="chat_id (по умолчанию из .env)")
    parser.add_argument(
        "--keep", action="store_true",
        help="не удалять отправленные сообщения после проверки",
    )
    args = parser.parse_args(argv)

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = (args.chat or os.getenv("TELEGRAM_CHAT_ID", "")).strip()

    if not token or not chat_id:
        print("FAIL: Нет TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (проверьте .env)")
        return 1

    failures: list[str] = []

    # 1. Токен
    ok, me = _call(token, "getMe")
    if ok:
        print(f"OK: getMe: @{me['result'].get('username')} (id={me['result'].get('id')})")
    else:
        print(f"FAIL: getMe: {me}")
        return 1

    sent_ids: list[int] = []

    # 2. HTML + экранирование
    html_text = (
        "🧪 <b>Smoke-тест</b>\n"
        "Экранирование: &lt;b&gt; &amp; &lt;script&gt;\n"
        "Цена: <code>66,000.00 &lt; 66,500.00</code>"
    )
    ok, resp = _call(
        token, "sendMessage",
        chat_id=chat_id, text=html_text, parse_mode="HTML",
        disable_web_page_preview="true",
    )
    if ok:
        sent_ids.append(resp["result"]["message_id"])
        print("OK: sendMessage (HTML): доставлено")
    else:
        failures.append(f"sendMessage HTML: {resp.get('description')}")
        print(f"FAIL: sendMessage (HTML): {resp.get('description')}")

    # 3. Инлайн-кнопка
    markup = {
        "inline_keyboard": [[
            {"text": "🗑 Удалить (тест)", "callback_data": "del|BTCUSDT|66000.0|down|0"},
        ]],
    }
    ok, resp = _call(
        token, "sendMessage",
        chat_id=chat_id,
        text="🧪 <b>Smoke-тест:</b> кнопка ниже должна быть кликабельной",
        parse_mode="HTML",
        reply_markup=json.dumps(markup),
    )
    if ok:
        message_id = resp["result"]["message_id"]
        sent_ids.append(message_id)
        print("OK: sendMessage (клавиатура): доставлено")

        # 4. Редактирование — как делают меню бота
        ok, resp = _call(
            token, "editMessageText",
            chat_id=chat_id, message_id=message_id,
            text="🧪 <b>Smoke-тест:</b> сообщение отредактировано",
            parse_mode="HTML",
        )
        if ok:
            print("OK: editMessageText: работает")
        else:
            failures.append(f"editMessageText: {resp.get('description')}")
            print(f"FAIL: editMessageText: {resp.get('description')}")
    else:
        failures.append(f"sendMessage клавиатура: {resp.get('description')}")
        print(f"FAIL: sendMessage (клавиатура): {resp.get('description')}")

    # Уборка
    if not args.keep:
        for message_id in sent_ids:
            _call(token, "deleteMessage", chat_id=chat_id, message_id=message_id)
        if sent_ids:
            print(f"cleanup: Удалено тестовых сообщений: {len(sent_ids)}")

    if failures:
        print("\nFAIL: Провалы:")
        for item in failures:
            print(f"  • {item}")
        return 1

    print("\nOK: Smoke-тест пройден: доставка, HTML и кнопки работают.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
