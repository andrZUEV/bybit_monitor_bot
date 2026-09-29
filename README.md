# Bybit Monitor Bot

Мониторинг цен и объёмов Bybit + Telegram-алерты.
Стратегия — «уровневый отбой», см. [`docs/STRATEGY.md`](docs/STRATEGY.md).

## Возможности

- **WebSocket-push** цен (`tickers.{symbol}`) — без REST-поллинга.
- **REST-fallback** при `USE_WEBSOCKET=false`.
- Пошаговый диалог добавления алерта: тикер → цена → направление → заметка.
- Кнопки у алерта: «📊 Выгрузить данные» (CSV по 15m/1H/4H), «🗑 Удалить».
- Антиспам: кулдаун на повторные срабатывания (`data/cooldowns.json`).
- Анализ: RSI, EMA, ATR, HTF-тренд, паттерны, касания уровня.
- Нормализация тикеров: `sol` → `SOLUSDT` (через `resolve_symbol`).

## Требования

- Python 3.12+
- Telegram-бот (токен от [@BotFather](https://t.me/BotFather))
- Доступ к публичному API Bybit v5 (ключи не нужны — только public endpoints)

## Установка

```bash
git clone <repo-url>
cd bybit-monitor-bot
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"