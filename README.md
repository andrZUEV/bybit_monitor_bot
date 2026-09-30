# Bybit Monitor Bot

Мониторинг цен и объёмов Bybit + Telegram-алерты.
Стратегия — «уровневый отбой», см. [`docs/STRATEGY.md`](docs/STRATEGY.md).

## Возможности

- **WebSocket-push** цен (`tickers.{symbol}`) — без REST-поллинга.
- **REST-fallback** при `USE_WEBSOCKET=false`.
- Пошаговый диалог добавления алерта: тикер → цена → направление → заметка.
- Кнопки у алерта: «📊 Выгрузить данные» (CSV по 15m/1H/4H/1D), «🗑 Удалить».
- Антиспам: кулдаун на повторные срабатывания (`data/cooldowns.json`).
- Анализ: RSI, EMA, ATR, HTF-тренд, паттерны, касания уровня.
- **План сделки в алерте:** Entry / SL / TP1 / TP2 / RR / size + hard filters.
- Нормализация тикеров: `sol` → `SOLUSDT` (через `resolve_symbol`).

### Стратегия «Уровневый отбой» (Этап 4)

Алерт — это не просто «цена пересекла уровень», а полноценный **план
сделки** по стратегии:

- **Entry / SL / TP1 / TP2** — с расчётом от структуры и уровней 4H/1D.
- **RR** — по TP1 и TP2, с проверкой hard filter `RR ≥ 1:3`.
- **Size** — размер позиции под риск ≤ 1% от депозита.
- **Runway** — запас хода до TP1 ≥ 2×ATR.

Плюс валидация по стратегии:

- **RSI-дивергенция** (бычья/медвежья) на локальных пивотах.
- **Структура тренда 4H** — HH/HL (восходящий), LH/LL (нисходящий).
- **Уровни 4H/1D** — с зеркальностью и фильтром износа.

Если hard filter не пройден — алерт отправляется с плашкой
**⛔ СДЕЛКА НЕ ПО СТРАТЕГИИ** (режим наблюдения).

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