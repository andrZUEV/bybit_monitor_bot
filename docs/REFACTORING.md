# REFACTORING.md

Журнал рефакторинга проекта **bybit-monitor-bot**.
Файл нужен, чтобы:
- любой новый чат/разработчик мог быстро войти в контекст;
- видеть, что уже сделано, а что — в плане;
- не терять решения и обоснования.

---

## Общая информация

- **Проект:** Bybit Monitor Bot — мониторинг цен/объёмов Bybit + Telegram-алерты.
- **Стек:** Python 3.12, `python-telegram-bot==21.6`, `requests`, `python-dotenv`, `colorlog`.
- **Точка входа:** `main.py`.
- **Стратегия торговли:** см. `docs/STRATEGY.md` («уровневый отбой»).
- **Архитектура:**
  - `src/api/bybit_client.py` — REST-клиент Bybit v5 (public endpoints).
  - `src/core/monitor.py` — цикл опроса цен и проверка кроссов.
  - `src/core/alerts.py` — потокобезопасный менеджер алертов (JSON + .bak).
  - `src/core/cooldown.py` — антиспам (файл `data/cooldowns.json`).
  - `src/core/analyzer.py` — веса/штрафы, паттерны, RSI, EMA, HTF-тренд.
  - `src/telegram/bot.py` — запуск и отправка сообщений.
  - `src/telegram/handlers.py` — команды и callback'и.
  - `src/telegram/keyboards.py` — inline-клавиатуры.
  - `src/utils/config.py` — централизованная конфигурация из `.env`.
  - `src/utils/indicators.py` — единый RSI, EMA, ATR (Wilder).
  - `src/utils/data_exporter.py` — экспорт CSV.

---

## Дорожная карта

| Этап | Тема | Статус |
|------|------|--------|
| 1 | Чистка багов, заставить работать то, что есть | ✅ ЗАКРЫТ |
| 2 | WebSocket + рефакторинг цикла мониторинга | ⏳ НЕ НАЧАТ |
| 3 | Порядок в репо: `.gitignore`, README, тесты, CI | ⏳ |
| 4 | Расширение: RSI-дивергенция, ATR-стопы, RR, размер позиции | ⏳ |

---

## Этап 1 — что было исправлено

### Инфраструктура и конфиги

1. **`pyproject.toml`** — невалидный `build-backend` (`setuptools.backends._legacy:_Backend`) заменён на `setuptools.build_meta`. Добавлен `colorlog`, `dev`-зависимости (pytest, ruff), конфиг ruff.

2. **`.env.example`** — синхронизирован с `Config`. `ALERT_COOLDOWN_MINUTES` унифицировано на 25 (было 15 в .env.example, 25 в Config, 25 хардкодом в handlers).

3. **`.gitignore`** — добавлены `__pycache__/`, `*.egg-info/`, `.venv/`, `.env`, `data/*.json`, `data/exports/`, `logs/`, `structure.txt`, кэши pytest/ruff. Из индекса убран `bybit_monitor_bot.egg-info/`.

4. **`src/utils/config.py`** — добавлены:
   - `PROJECT_ROOT` (единая точка расчёта путей).
   - `COOLDOWNS_FILE` (был захардкожен в Monitor).
   - `EXPORTS_DIR` (был захардкожен в handlers).
   - `EXPORT_LIMITS` — лимиты свечей по ТФ: `15 → 300`, `60 → 300`, `240 → 400`.

### Единый источник индикаторов

5. **`src/utils/indicators.py`** — единая точка входа для индикаторов:
   - `calculate_rsi` (одно число) — раньше было **3 копии** в `indicators.py`, `analyzer.py`, `handlers.py`.
   - `calculate_rsi_series` (список, для CSV) — раньше была локально в `handlers.py`.
   - `calculate_ema` / `calculate_ema_from_bybit` — обёртка для массивов от Bybit (новые первые).
   - `calculate_atr` / `calculate_atr_series` — новый индикатор (для Этапа 4).
   - `calculate_bollinger` — опционально.

### API-клиент Bybit

6. **`src/api/bybit_client.py`**:
   - `get_ticker` возвращает `Optional[TickerData]` (было `Optional[Any]`).
   - `ScreenerAsset` — полноценный `@dataclass` (было через `type('ScreenerAsset', ...)`).
   - `_make_request`: `response.json()` обёрнут в `try/except ValueError` — теперь **есть ретрай** при не-JSON ответе (раньше Bybit 5xx с HTML не ретраился).
   - `RETRYABLE_CODES` — class-level константа.

### Анализ

7. **`src/core/analyzer.py`**:
   - Убран дубль `calculate_rsi` — импортируется из `indicators`.
   - **Исправлен баг `get_htf_trend`:** раньше `ema50[-1]` трактовался как «текущее», хотя после разворота это самое **старое** значение → тренд определялся неверно. Теперь через `calculate_ema_from_bybit` + `ema50[0]`.
   - `count_touches` логирует warning, если свечей меньше, чем нужно для `lookback_hours`.
   - `worn_touches = 5` (по стратегии: 5+ касаний = изношен). Было 4.
   - `touches_lookback_hours = 48` вынесено в `THRESHOLDS`.
   - Вердикт `💪 Strong` (был ` Strong` с пробелом — терялся эмодзи).
   - Type hints для `signals`, `filters`.

### Мониторинг

8. **`src/core/monitor.py`**:
   - Лимиты свечей: `KLINES_15M_LIMIT = 250 → 300`, `KLINES_4H_LIMIT = 100 → 400` (через Config.EXPORT_LIMITS).
   - `CooldownManager` получает путь из `Config.COOLDOWNS_FILE`.
   - `bybit_client: Optional[BybitClient]` вместо `Optional[Any]`.

### Telegram

9. **`src/telegram/handlers.py`**:
   - **Все callback'и обрабатываются.** Были мёртвые:
     - `menu_prices`, `menu_list`, `menu_help`.
     - `del_all_{symbol}`, `del|symbol|price|direction`.
     - `dir_up / dir_down / dir_any`.
     - `scr_add_{symbol}`.
   - Добавлен `else` для логирования необработанных callback'ов.
   - **Пошаговый диалог добавления алерта (Вариант Б):** тикер → цена → направление (клавиатура) → заметка.
   - Убран локальный дубль `calculate_rsi` → `calculate_rsi_series` из `indicators.py`.
   - Путь экспорта через `Config.EXPORTS_DIR`.
   - Хардкод «Интервал 4 сек / Кулдаун 25 мин» в `start_cmd` заменён на `Config.POLL_INTERVAL` / `Config.ALERT_COOLDOWN_MINUTES`.
   - `_handle_current_prices` — убран двойной `query.answer()`.
   - Разбит большой `handle_callback` на `_handle_delete_alert`, `_handle_export_alert`, `_handle_current_prices`.
   - `_finalize_add_alert` — отдельный метод для завершения диалога.
   - **`_handle_export_tickers`** — предварительная валидация тикеров через `get_ticker`. Невалидные не попадают в CSV, в caption показывается «⚠️ Пропущены: ...». Если все невалидны — подсказка про опечатки (XRP vs XPR).
   - **`data_cmd`** — проверка существования символа перед экспортом.

10. **`src/telegram/keyboards.py`**:
    - `direction_keyboard` теперь используется (раньше — мёртвый код).
    - Добавлена `skip_note_keyboard` (пока не подключена в handlers).
    - `cancel_keyboard` — кнопка «Главное меню» вместо «Отмена».

11. **`src/telegram/bot.py`**:
    - `bybit_client: Optional[BybitClient]` вместо `Optional[Any]`.
    - Убран дублирующий импорт `BybitClient` внутри `__init__`.
    - Комментарий, почему `send_alert` использует прямой HTTP, а не `Application.send_message` (вызывается из потока Monitor, не зависит от event loop).

---

## Известные проблемы / технический долг

### Критичные (в Этап 2)
- **REST-поллинг каждые 4 сек** — при 30+ активах упрёмся в лимит Bybit (10 req/s на IP). Решение: WebSocket `wss://stream.bybit.com/v5/public/linear`, подписка на `tickers.{symbol}` для всех символов.
- **`triggered_alerts` живёт только в памяти** — после рестарта бота prev_price на первом тике = None, кроссы не проверяются (прогрев). Спама нет (cooldown в файле), но логика стоит перепроверить.

### Средние
- **`_handle_current_prices`** — двойной `query.answer()` был убран, но стоит проверить `Message is not modified` при повторном нажатии.
- **`skip_note_keyboard`** — добавлена, но не подключена. Пользователь вводит «-» для пропуска заметки. Можно сделать кнопкой.
- **Параметры `volume_threshold`, `volume_cooldown`** в `Monitor.__init__` — legacy, не используются. Стоит удалить или задокументировать.

### Мелкие
- `cross_text` в `monitor._check_price_cross` — создаётся, но не используется. Удалить.
- `VOLUME_THRESHOLD`, `VOLUME_COOLDOWN` в `.env.example` — legacy-флаги, помечены комментариями.
- `data_exporter.py` — используется в `main.py` для очистки старых файлов, но не подключён к `Config.EXPORTS_DIR`. Стоит привести к единому источнику.

---

## Этап 2 — план (WebSocket + рефакторинг цикла)

**Цель:** уйти от REST-поллинга на WebSocket-push.

**Ключевые изменения:**
1. `src/api/bybit_ws.py` — новый модуль: WS-клиент Bybit v5.
2. Подписка на `tickers.{symbol}` для всех активов в `alerts.json`.
3. `monitor._poll_once` → `monitor._on_ticker_update(symbol, price)`.
4. Klines по-прежнему через REST, **только при срабатывании алерта**.
5. Авто-переподключение WS при обрыве.
6. Heartbeat + ping/pong (Bybit требует `{"op":"ping"}` каждые 20 сек).
7. Fallback: если WS недоступен — вернуться на REST-поллинг (опционально).

**Файлы, которые будут изменены:**
- `src/core/monitor.py` — переписать под push.
- `src/api/bybit_ws.py` — новый.
- `main.py` — запуск WS вместо polling.
- `src/utils/config.py` — добавить `USE_WEBSOCKET: bool = True`.

---

## Этап 3 — план (порядок в репо)

1. **README.md** — описание, установка, запуск.
2. **`.gitignore`** — уже расширен в Этапе 1.
3. **Тесты (`pytest`):**
   - `tests/test_indicators.py` — RSI, EMA, ATR на заранее посчитанных значениях.
   - `tests/test_analyzer.py` — evaluate_alert на мок-свечах.
   - `tests/test_alerts.py` — add/remove/save/load.
   - `tests/test_cooldown.py` — can_send/mark_sent.
4. **CI (GitHub Actions):** ruff + pytest на push/PR.
5. **`docs/`** — REFACTORING.md (этот файл) + STRATEGY.md.

---

## Этап 4 — план (расширение по стратегии)

См. `docs/STRATEGY.md`.

**Ключевые модули:**
- `src/core/risk.py` — RR-калькулятор, размер позиции, ATR-стоп.
- `src/core/divergence.py` — поиск RSI-дивергенций.
- `src/core/levels.py` — детект зеркальных уровней, HH/HL на 4H.
- Расширение `analyzer.py` — hard filters (RR ≥ 1:3, риск ≤ 1%, 4H-тренд блокирует вход).
- Расширение алертов: `entry / SL / TP1 / TP2 / RR / size` в сообщении.
- Добавление ТФ `D` (1D) в экспорт и анализ.

---

## Как продолжать работу в новом чате

1. Открыть новый чат.
2. Первое сообщение — шаблон из этого файла (см. раздел «Как продолжать»).
3. Приложить: `docs/REFACTORING.md`, `docs/STRATEGY.md`, `src/core/monitor.py`, `src/api/bybit_client.py`.
4. Указать текущий этап (2) и конкретную задачу.

---

## Версия

- **v1.0** — Этап 1 закрыт (2026-09-29).
- Следующий этап — WebSocket.