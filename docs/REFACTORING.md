# REFACTORING.md

Журнал рефакторинга проекта **bybit-monitor-bot**.
Файл нужен, чтобы:
- любой новый чат/разработчик мог быстро войти в контекст;
- видеть, что уже сделано, а что — в плане;
- не терять решения и обоснования.

---

## Общая информация

- **Проект:** Bybit Monitor Bot — мониторинг цен/объёмов Bybit + Telegram-алерты.
- **Стек:** Python 3.12, `python-telegram-bot==21.6`, `requests`, `python-dotenv`, `colorlog`, `websocket-client>=1.7`.
- **Точка входа:** `main.py`.
- **Стратегия торговли:** см. `docs/STRATEGY.md` («уровневый отбой»).
- **Архитектура:**
  - `src/api/bybit_client.py` — REST-клиент Bybit v5 (public endpoints). Плюс `resolve_symbol()`.
  - `src/api/bybit_ws.py` — WS-клиент Bybit v5 (`tickers.{symbol}`). Push-цены.
  - `src/core/monitor.py` — мониторинг (WS + REST-fallback), проверка кроссов.
  - `src/core/alerts.py` — потокобезопасный менеджер алертов (JSON + .bak).
  - `src/core/cooldown.py` — антиспам (файл `data/cooldowns.json`).
  - `src/core/analyzer.py` — веса/штрафы, паттерны, RSI, EMA, HTF-тренд, hard filters.
  - `src/core/risk.py` — RR-калькулятор, размер позиции, ATR-стоп, hard filters.
  - `src/core/divergence.py` — поиск RSI-дивергенций на локальных пивотах.
  - `src/core/levels.py` — поиск уровней на 4H/1D, зеркальность, HH/HL-структура тренда.
  - `src/telegram/bot.py` — запуск и отправка сообщений.
  - `src/telegram/handlers.py` — команды и callback'и.
  - `src/telegram/keyboards.py` — inline-клавиатуры.
  - `src/utils/config.py` — централизованная конфигурация из `.env`.
  - `src/utils/indicators.py` — единый RSI, EMA, ATR (Wilder).
  - `src/utils/data_exporter.py` — экспорт CSV.

---

## Дорожная карта

| Этап | Тема                                                       | Статус   |
|------|------------------------------------------------------------|----------|
| 1    | Чистка багов, заставить работать то, что есть              | ✅ ЗАКРЫТ |
| 2    | WebSocket + рефакторинг цикла мониторинга                  | ✅ ЗАКРЫТ |
| 3    | Порядок в репо: `.gitignore`, README, тесты, CI            | ✅ ЗАКРЫТ |
| 4    | Расширение: RSI-дивергенция, ATR-стопы, RR, размер позиции | ✅ ЗАКРЫТ |
| 5    | Рефакторинг + буфер алертов                                  | ✅ ЗАКРЫТ |
| 6    | Сканер уровней                                               | ✅ ЗАКРЫТ |
| 7    | Дневник разворотов                                           | ✅ ЗАКРЫТ |
| —    | Виртуальный трейдер (вне нумерации плана)                    | ✅ СДЕЛАНО |
| HF   | Hotfix 1–3: ретраи Telegram, `/list`, один ATR, воркер        | ✅ ЗАКРЫТ |
| 8    | Синхронизация документации с кодом                           | ✅ ЗАКРЫТ |
| 9    | Гейтинг сигналов и точность паттернов                        | ✅ ЗАКРЫТ |
| 10   | Уровни как зоны (`price_low`/`price_high`)                   | ✅ ЗАКРЫТ |
| 11   | Покрытие тестами (Telegram-слой) + живой smoke-тест           | ✅ ЗАКРЫТ |

Актуальные детали — в разделах «Этап 6», «Этап 7», «Виртуальный
трейдер», «Hotfix 1–3», «Этап 8», «Этап 9», «Этап 10», «Этап 11» в конце документа.

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

## Этап 2 — что было сделано (WebSocket)

### Цель

Уйти от REST-поллинга (`_poll_once` каждые 4 сек) на WS-push. Klines оставить через REST — только при срабатывании алерта.

### Решения

1. **Библиотека:** `websocket-client>=1.7` (синхронный, отдельный поток). Не `websockets` (async) — чтобы не мостить asyncio-мост с `python-telegram-bot`.
2. **Мониторинг живёт в WS-потоке**, главный поток джойнит.
3. **Без REST-прогрева** при старте: первый snapshot от Bybit запоминает `prev_price`, кросс не проверяется. Прогрев происходит автоматически за ~1–2 сек после подписки.
4. **Refresh подписок раз в 10 сек** (`WS_SYMBOL_REFRESH`) — сравнение символов из `alerts.json` с текущими подписками.
5. **`resolve_symbol()`** — единая точка нормализации тикеров (`sol` → `SOLUSDT`), в интерактивных местах. Быстрое и массовое добавление — только `+USDT` без REST (не тормозим).
6. **`states` не чистится** при исчезновении символа — защита от гонки WS-потока и refresh-потока + сохранение `prev_price` между удалением/повторным добавлением уровня.
7. **Сообщения алертов не затираются** кнопками «Выгрузить» / «Удалить» — исходное сообщение остаётся в чате.

### Новый модуль

12. **`src/api/bybit_ws.py`** — `BybitWebSocketClient`:
    - Синхронный клиент, `websocket-client`, отдельный поток.
    - Публичный API: `start()`, `stop()`, `set_symbols(iterable[str])`.
    - Callback: `on_ticker(symbol, price, volume_24h)`.
    - Ping каждые `WS_PING_INTERVAL` (20с) — требование Bybit.
    - Авто-reconnect с экспоненциальным backoff до 30с.
    - `_apply_diff(initial=True)` после реконнекта подписывается на `_desired | _subscribed` — страховка от гонки при старте.
    - Подписки — чанками по 10 args (лимит Bybit).
    - Парсинг `{"topic": "tickers.SOLUSDT", "data": {...}}`; `lastPrice`/`volume24h` — строки → float.
    - Служебные `{"op": "pong"}`, `{"op": "subscribe", "success": ...}` — игнорируются.

### Изменённые модули

13. **`src/utils/config.py`** — добавлены (внутри класса `Config`):
    - `USE_WEBSOCKET: bool` (из `USE_WEBSOCKET`, дефолт `true`).
    - `WS_URL: str` (из `BYBIT_WS_URL`, дефолт `wss://stream.bybit.com/v5/public/linear`).
    - `WS_RECONNECT_DELAY: float` (5с).
    - `WS_PING_INTERVAL: float` (20с).
    - `WS_SYMBOL_REFRESH: float` (10с).

14. **`src/core/monitor.py`** (v3.0):
    - Новые атрибуты: `_assets_by_symbol` (кэш под `_cache_lock`), `_stop_event`, `_refresh_thread`, `_ws_client`, `use_websocket`.
    - Новый метод `_on_ticker_update(symbol, price, volume_24h)` — колбэк из WS. Ищет `Asset` в кэше, fallback на `alerts_manager.get_all_alerts()` при гонке. Вызывает `_check_price_cross`, обновляет `prev_price`/`prev_volume`.
    - Новый метод `_refresh_subscriptions()` — `alerts_manager.load()`, обновление кэша, `ws_client.set_symbols(symbols)`. **`states` не чистит.**
    - Новый метод `_refresh_loop()` — фоновый поток: refresh сразу + каждые `WS_SYMBOL_REFRESH` сек.
    - `start()` — диспетчер: WS (`_start_websocket`) или REST (`_start_rest_polling`).
    - `_start_websocket()` — старт refresh-потока + `ws_client.start()`, блокирующее ожидание `_stop_event`. При ошибке запуска WS → fallback на REST.
    - `_start_rest_polling()` — старый цикл `_poll_once` с `_stop_event.wait()` вместо `time.sleep()`.
    - `stop()` — остановка WS + join refresh-потока.
    - `_poll_once()` — сохранён для fallback.
    - `_check_price_cross()` — сигнатура `ticker: TickerData`, удалён `cross_text` (мёртвый код).
    - `BybitWebSocketClient` импортируется лениво (не тянем `websocket-client`, если WS выключен).

15. **`src/api/bybit_client.py`** — добавлен `resolve_symbol(user_input, category)`:
    - `strip().upper().replace(" ", "")`.
    - Если заканчивается на `USDT` — проверяет как есть.
    - Иначе пробует `+USDT`.
    - Возвращает валидный символ или `None`.
    - Проверка — через `get_ticker`.

16. **`src/telegram/handlers.py`** (v2.1):
    - `resolve_symbol` подключён в 3 местах:
      - `/data <тикер>`,
      - диалог добавления алерта (`waiting_symbol`),
      - ручная выгрузка (`_handle_export_tickers`).
    - Быстрое однострочное добавление (`BTC 85000 up пробой`) и массовое — только `+USDT` без REST.
    - **Починен развал структуры класса:** в одной из итераций правок методы `_handle_export_tickers` и ниже выпали из `TelegramHandlers` (нулевой отступ вместо 4 пробелов) → `AttributeError: 'TelegramHandlers' object has no attribute 'handle_callback'`. Все методы возвращены в класс.
    - `_handle_export_alert` — **не затирает** исходное сообщение алерта. Статус («⏳ Выгружаю...» → «✅ Файл отправлен») — отдельным сообщением.
    - `_handle_delete_alert` — **не затирает** алерт. Убирает у него клавиатуру (`edit_message_reply_markup(reply_markup=None)`), статус «🗑 Удалён ...» — отдельным сообщением.
    - `menu_main` — по-прежнему затирает исходное сообщение меню (стандартное поведение).

17. **`main.py`** — `Monitor(..., use_websocket=Config.USE_WEBSOCKET)`.

18. **`pyproject.toml`** — добавлена зависимость `websocket-client>=1.7.0`.

19. **`.env.example`** — добавлен блок:
    - `USE_WEBSOCKET=true`
    - `BYBIT_WS_URL=wss://stream.bybit.com/v5/public/linear`
    - `WS_RECONNECT_DELAY=5`
    - `WS_PING_INTERVAL=20`
    - `WS_SYMBOL_REFRESH=10`

### Проверено

- Бот стартует, WS коннектится, подписки уходят.
- Тикеры приходят, кроссы детектятся, `evaluate_alert` вызывается.
- Алерт-сообщение с клавиатурой отправляется в Telegram.
- Кнопки «📊 Выгрузить данные» и «🗑 Удалить» работают, исходное сообщение алерта остаётся в чате.
- `resolve_symbol`: `sol` / `SOL` / `SOLUSDT` / `solusdt` — все варианты дают `SOLUSDT`.
- REST-fallback (`USE_WEBSOCKET=false`) — сохранён, не проверялся вживую.

---

## Известные проблемы / технический долг

### Средние

- **`_handle_export_alert` делает 3 REST-запроса (15m / 1H / 4H) в event loop бота.** При массовой выгрузке или двух пользователях одновременно — блокирует обработку других команд на 2–5 сек. Решение: `run_in_executor` или `asyncio.to_thread`. **Не в Этапе 2.**
- **`_handle_current_prices`** — двойной `query.answer()` убран, `Message is not modified` обрабатывается. Ок.
- **`skip_note_keyboard`** — добавлена в `keyboards.py`, но не подключена. Пользователь вводит «-» для пропуска заметки. Можно сделать кнопкой.
- **Параметры `volume_threshold`, `volume_cooldown`** в `Monitor.__init__` — legacy, не используются. Стоит удалить или задокументировать. **Не в Этапе 2.**
- **`get_candle_volume_ratio`** внутри `_check_price_cross` делает отдельный REST-запрос `get_klines` — при срабатывании алерта это лишний запрос (можно было бы вычислить из уже полученных `klines_15m`). Микрооптимизация.

### Мелкие

- `VOLUME_THRESHOLD`, `VOLUME_COOLDOWN` в `.env.example` — legacy-флаги, помечены комментариями.
- `data_exporter.py` — используется в `main.py` для очистки старых файлов, но не подключён к `Config.EXPORTS_DIR`. Стоит привести к единому источнику.
- `del_all_{symbol}` — callback в `handlers.py` есть, но из клавиатуры алертов (в `keyboards.alerts_list_keyboard`) не вызывается? Стоит проверить актуальность.

### Решённые в Этапе 2

- ~~REST-поллинг каждые 4 сек — упрёмся в лимит Bybit при 30+ активах.~~ → WS-push.
- ~~`triggered_alerts` живёт только в памяти — после рестарта prev_price = None.~~ → То же поведение, но прогревается автоматически из WS-snapshot за ~1–2 сек. Ложных кроссов нет. Cooldown по-прежнему в файле.

---

## Этап 3 — план (порядок в репо)

1. **README.md** — описание, установка, запуск.
2. **`.gitignore`** — уже расширен в Этапе 1.
3. **Тесты (`pytest`):**
   - `tests/test_indicators.py` — RSI, EMA, ATR на заранее посчитанных значениях.
   - `tests/test_analyzer.py` — evaluate_alert на мок-свечах.
   - `tests/test_alerts.py` — add/remove/save/load.
   - `tests/test_cooldown.py` — can_send/mark_sent.
   - `tests/test_bybit_client.py` — `resolve_symbol` на мок-`get_ticker`.
   - `tests/test_monitor_ws.py` — `_on_ticker_update` с мок-WS-клиентом и мок-AlertsManager.
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
3. Приложить: `docs/REFACTORING.md`, `docs/STRATEGY.md`, `src/core/monitor.py`, `src/api/bybit_client.py`, `src/api/bybit_ws.py`.
4. Указать текущий этап (5) и конкретную задачу.

---

## Этап 3 — что было сделано (README, тесты, CI)

### Цель

Порядок в репо: документация, тесты, автоматическая проверка.

### Артефакты

1. **`README.md`** — описание, установка, запуск, переменные
   окружения, команды бота, структура, разработка.

2. **`tests/`** — pytest, без сетевых вызовов (~550 тестов):
   - `conftest.py` — фикстуры (`tmp_data_dir`, `alerts_path`,
     `cooldowns_path`, `make_monitor`, `sample_klines_newest_first`).
   - `test_indicators.py` — RSI, EMA, ATR. Зафиксировано:
     - `calculate_rsi` / `calculate_rsi_series` ожидают
       **Bybit-порядок** (новые первыми).
     - `calculate_ema` — хронологический (старые первыми),
       возвращает серию.
     - `calculate_ema_from_bybit` — серия, `[0]` самое свежее.
     - `calculate_atr_series` возвращает `len(klines) + 1`.
   - `test_analyzer.py` — `evaluate_alert` на мок-свечах
     (сигнатура: `symbol, level, direction, current_price,
     alert_created_at, klines_15m, klines_4h`).
   - `test_alerts.py` — `add_alert` / `remove_alert(symbol, price,
     direction)` / `remove_all_alerts_for_symbol` / `clear_all`.
     Структура: `Asset(symbol, category, alerts: list[AlertRule])`,
     `AlertRule(price, direction, setup_note, created_at)`.
     Нет `remove_alert(id)` — удаление по тройке.
   - `test_cooldown.py` — `can_send` / `mark_sent` / `reset`
     (сигнатуры `(symbol, price, direction)`). Поле внутри —
     `cooldown_seconds`.
   - `test_bybit_client.py` — `resolve_symbol` на мок-`get_ticker`.
   - `test_monitor_ws.py` — семантика `_on_ticker_update`:
     - Работает только для символов с Asset (в `_assets_by_symbol`
       или через fallback на `alerts_manager.get_all_alerts()`).
     - Для неизвестных — no-op, `AssetState` не создаётся.
     - `_check_price_cross` вызывается всегда (когда есть Asset),
       но алерт уходит только при наличии `prev_price`.
     - После удаления Asset состояние в `states[symbol]`
       сохраняется (защита от гонки WS-потока и refresh-потока).

3. **`.github/workflows/ci.yml`** — ruff + pytest на push/PR,
   Python 3.12, кэш pip.

### Фиксы production-кода

4. **`src/core/alerts.py::save()`** — переписан через
   `tempfile.mkstemp` + `os.replace`. Восстановлены:
   - `.bak`-бэкап перед записью,
   - `to_dict()` при сериализации (был потерян при рефакторинге
     на заглушку `data = {...}`, что давало
     `TypeError: Object of type set is not JSON serializable`),
   - `import time` в шапке.

5. **`src/core/cooldown.py::save()`** — то же:
   - `mkstemp` вместо `with open(tmp_path, 'w')` —
     `WinError 183` на Windows при повторной записи.
   - Восстановлены `with self._lock:` и `self._cleanup_locked()`,
     потерянные при рефакторинге.
   - `import os` / `import tempfile` в шапке.

6. **`src/utils/data_exporter.py`** — удалён локальный дубль
   `calculate_rsi` (F811). Используется `calculate_rsi_series`
   из `indicators`.

7. **`src/core/analyzer.py`** — `l` → `low` в `detect_pattern`
   (E741, F821: после переименования часть использований
   осталась со старым именем).

8. **`main.py`**, **`src/telegram/handlers.py`**,
   **`src/core/alerts.py`** — разбиты длинные строки (E501).

9. **`pyproject.toml`**:
   - `[tool.ruff]`: `select = ["E", "F", "I", "UP", "B"]`,
     `ignore` для PLR* / PLC0415 / PLW* — стилевые придирки,
     не баги.
   - `[tool.pytest.ini_options]`: `testpaths = ["tests"]`,
     `python_files = ["test_*.py"]`.
   - `[tool.ruff.lint.per-file-ignores]`: `tests/**` — E501,
     PLR2004, S101.

### Проверено

- `ruff check .` → `All checks passed!`.
- `pytest -q` → 52 passed.
- CI-конфиг локально воспроизводится.

### Технический долг (перенесён в Этап 4 / 5+)

- **Гонка при параллельных `add_alert`** в `AlertsManager`:
  `os.replace` на Windows может давать `WinError 5`, если
  `save()` вызывается без `self._lock`. Warning в pytest
  (`PytestUnhandledThreadExceptionWarning`) фиксирует это.
  Решение: обернуть `save()` в `with self._lock:` **или**
  заменить `Lock` на `RLock` и убрать lock из `add_alert`.
- Параметры `volume_threshold`, `volume_cooldown` в `Monitor` —
  legacy, не используются.
- `_handle_export_alert` делает 3 REST-запроса в event loop —
  стоит вынести в `asyncio.to_thread`.
- `skip_note_keyboard` — не подключена.
- Магические числа в `analyzer.py` (пороги RSI 40/60,
  пин-бары 0.66/0.33) — вынести в `THRESHOLDS`.
- `get_candle_volume_ratio` в `_check_price_cross` делает
  отдельный REST-запрос — можно вычислить из `klines_15m`.

---

## Этап 4 — Стратегия «Уровневый отбой» (ЗАКРЫТ)

**Дата:** 2026-09-30
**Цель:** расширить бот с «просто алерта по уровню» до полноценного
сетап-фильтра по стратегии «Уровневый отбой» (см. `docs/STRATEGY.md`).

### Добавленные модули

| Модуль                   | Назначение                                                                                            |
| ------------------------ | ----------------------------------------------------------------------------------------------------- |
| `src/core/risk.py`       | RR-калькулятор, размер позиции, ATR-стоп, hard filters (RR ≥ 1:3, риск ≤ 1%, 4H-тренд блокирует вход) |
| `src/core/divergence.py` | Поиск RSI-дивергенций на локальных пивотах (бычья/медвежья)                                           |
| `src/core/levels.py`     | Поиск уровней на 4H/1D, зеркальность, HH/HL-структура тренда                                          |

### Изменённые модули

| Модуль                     | Что изменилось                                                                                                                                                                                                                                            |
| -------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/core/analyzer.py`     | `evaluate_alert` принимает `klines_1h`/`klines_1d`/`risk_cfg`, считает `RiskResult`, `TrendStructure`, `Level` (4H/1D), RSI-дивергенцию. `format_alert_message` рисует секции «📐 План сделки», «📈 4H структура», «🎯 Уровни 4H», плашку «⛔ Hard filter» |
| `src/core/monitor.py`      | Прокидывает `klines_1h`/`klines_1d`/`risk_cfg` в `evaluate_alert`. Кэш klines (1H TTL=3600, 1D TTL=86400). Отправляет weak-сигналы с непройденным hard-фильтром — с плашкой                                                                               |
| `src/utils/config.py`      | Добавлены: `EQUITY`, `RISK_PCT`, `LOT_STEP`, `MIN_QTY`, `MAX_QTY`, `ATR_MULTIPLIER`, `MIN_RUNWAY_ATR`, `EXPORT_LIMITS["D"] = 200`                                                                                                                         |
| `src/telegram/handlers.py` | Экспорт CSV теперь включает 1D                                                                                                                                                                                                                            |

### Hard filters (по стратегии)

Проверяются через `risk.validate_setup`:

| Фильтр                        | Reason                                                | Действие         |
| ----------------------------- | ----------------------------------------------------- | ---------------- |
| Риск на сделку > 1%           | `risk_above_max`                                      | Сделка запрещена |
| RR < 1:3 (серая зона 2.5–3.0) | `rr_below_min` (+ `soft_reasons=["rr_in_grey_zone"]`) | Сделка запрещена |
| 4H-тренд против направления   | `trend_conflict`                                      | Сделка запрещена |
| Запас хода до TP1 < 2×ATR     | `insufficient_runway`                                 | Сделка запрещена |
| Размер позиции < min_qty      | `size_below_min`                                      | Сделка запрещена |
| ATR недоступен                | `atr_invalid`                                         | Сделка запрещена |

**Поведение при срабатывании hard filter:** алерт **отправляется** с плашкой
`⛔ СДЕЛКА НЕ ПО СТРАТЕГИИ: <причина>`. Это временное решение — убедиться,
что фильтр работает, а не завис. Позже возможно переключение в режим
«не отправлять» через `Config.SEND_INVALID_ALERTS`.

### Тесты

Прирост к базовым 52:

| Файл                                 | Тестов |
| ------------------------------------ | ------ |
| `tests/test_risk.py`                 | 38     |
| `tests/test_divergence.py`           | 18     |
| `tests/test_levels.py`               | 22     |
| `tests/test_analyzer_integration.py` | 8      |
| `tests/test_format_alert_message.py` | 10     |
| `tests/test_monitor_klines_cache.py` | 6      |
| `tests/test_export_1d.py`            | 3      |

**Итого:** ~550 тестов, все зелёные, `ruff check .` → `All checks passed`.

### Известный техдолг (Этап 5+)

- Уровень в `alerts.json` сейчас хранится как **точка** (`price: float`),
  а стратегия требует **зону** (`price_low`/`price_high`). Рефакторинг
  схемы данных — отдельный этап.
- ATR: один ATR на весь план (1H, иначе 15m) — используется для стопа,
  runway и RR. Цели TP1/TP2 берутся только из уровней 4H/1D.
- Дивергенция RSI ищется на 15m. Для 4H/1D — потенциально полезно,
  но требует переработки `evaluate_alert` (сейчас работает только на
  рабочем ТФ).
- Параметры `volume_threshold`, `volume_cooldown` в `Monitor` — legacy, не используются.
- `skip_note_keyboard` — не подключена.
- Магические числа в `analyzer.py` (пороги RSI 40/60, пин-бары 0.66/0.33) — вынести в `THRESHOLDS`.

---
## Этап 5 — Рефакторинг и буфер алертов (ЗАКРЫТ)

**Дата:** 2026-10-07
**Цель:** закрыть накопившийся техдолг + собрать статистику для анализа
качества оценки.

### Этап 5.1 — Быстрые фиксы

**5.1.0 — Дивергенции при `direction='any'`**
- `src/core/divergence.py`: `find_divergences` принимает `'any'`,
  возвращает до 2 (bullish + bearish), сортирует по свежести.
- `src/core/analyzer.py`: `pattern` / `wick` / `rsi` / `close_third` /
  `structure` корректно обрабатывают `'any'`; risk не считается при `'any'`.
- Новая секция `🧭 Дивергенции` в отчёте алерта.

**5.1.1 — Парсер массового добавления**
- Новый `src/parsers/mass_add.py`: `parse_mass_add`, `ParsedLine`,
  `SkippedLine`.
- Формат: `SYMBOL PRICE DIR [ОПИСАНИЕ]`, где DIR ∈
  `{up, down, any, long, short, вверх, вниз}`.
- Нормализация тикера (`sol` → `SOLUSDT`, `BTCUSDC` → skip).
- `setup_note` = всё после direction, включая `|`, `Вход:`, `Стоп:`.
- Ведущие разделители (`|`, `-`, `—`, `:`) срезаются.
- Интеграция в `handlers.py`: отчёт «Добавлено N, Пропущено M».

**5.1.2 — Порог `alert_min_score`**
- Новый `src/core/settings.py`: `RuntimeSettings` (JSON, синглтон,
  `init_settings` / `get_settings` / `set_settings`).
- Кнопка `⚙️ Настройки` в боте, пресеты `[-inf, -5, 0, 3]` + свой.
- Гейт в `Monitor._check_price_cross` — score ниже порога → пропуск.

**5.1.3 — Флаг `send_invalid_alerts`**
- `RuntimeSettings.send_invalid_alerts: bool = True` — **исторический
  дефолт**. В hotfix 1 изменён на `False` (в чат идут только алерты,
  прошедшие hard-фильтры; остальные пишутся в историю) — см. раздел
  «Hotfix 1–3» ниже.
- Кнопка `⛔ Hard-filter алерты: ВКЛ/ВЫКЛ` в боте.
- Гейт 2 в `Monitor._check_price_cross` — hard_filter-алерты режутся
  отдельно от score-гейта.

**5.1.4 — Единый `html_escape`**
- Новый `src/utils/formatting.py`: `html_escape` (`&` экранируется
  первым).
- `analyzer.py` использует общий хелпер.
- `handlers.py::_show_list` экранирует `&` в `setup_note` (был баг).

### Этап 5.2 — Буфер последних алертов

**5.2.1 — `AlertRecord` + `AlertHistory`**
- Новый `src/core/alert_history.py`: `AlertRecord` (40+ полей),
  `AlertHistory` (JSONL, RLock, атомарная запись через
  `mkstemp + os.replace`).
- `build_alert_record` — фабрика из `evaluation` + метаданных.
- Синглтон: `init_alert_history` / `get_alert_history` /
  `set_alert_history`.

**5.2.2 — Расширение `evaluate_alert`**
- 17 новых полей в возвращаемом dict: `candle`, `prev_candle`,
  `candle_ts`, `body_size`, `upper_wick`, `lower_wick`,
  `body_to_range_ratio`, `close_position`, `close_pos_ratio`,
  `pattern_score`, `pattern_reason`, `volume_score`, `rsi_score`,
  `wick_beyond_level`, `close_in_correct_third`, `htf_against`,
  `atr_value`.

**5.2.3 — Интеграция в Monitor**
- `Monitor` принимает `alert_history`, пишет `AlertRecord` на каждом
  алерте (включая пропущенные).
- `Config.ALERT_HISTORY_FILE`, `RuntimeSettings.alert_history_depth`.
- Пишем ВСЕ алерты, включая `verdict="❌ None"`.

**5.2.4 — UI истории**
- Кнопка `📜 История алертов` в главном меню.
- `/settings` → `📜 Глубина истории` (пресеты + свой).
- Колбэки `settings_history_depth`, `set_depth|N`, `set_depth_custom`.
- Выгрузка CSV, очистка с подтверждением.

**5.2.6 — `strategy_pass`**
- Пишем все алерты в историю, включая слабые сигналы.
- `AlertRecord.strategy_pass = (verdict != "❌ None") AND
  (score >= alert_min_score) AND
  (hard_filter is None or send_invalid_alerts)`.
- `skip_reason`: `verdict_none` / `score_below` /
  `hard_filter_disabled`.

**5.2.7 — Дедупликация**
- `AlertHistory._add_to_memory`: ключ
  `(symbol, level, direction, candle_ts)`.
- `old.was_sent=True` НЕ перезаписывается `new.was_sent=False`.
- Rewrite JSONL при каждом append (файл маленький, после дедупа).
- `alert_history_depth = 10000` по умолчанию.

**5.2.8 — `worn_level` и `htf_against`**
- `analyzer.py`: `worn_level > 5` → `hard_filter = "worn_level"`
  (убран штраф `-1.5` — устранён двойной учёт).
- `analyzer.py`: `htf_against` — убран штраф `-1.5`, оставлен только
  `hard_filter = "trend_conflict"` в `risk` (устранён двойной штраф).
- `AlertRecord.worn_level` берётся из `evaluation`, не хардкод.
- `format_reason_ru("worn_level")` → «Уровень изношен».

### Итоги Этапа 5

- **~350+ тестов**, все зелёные, `ruff check` чистый.
- **Новые модули:** `src/parsers/`, `src/core/settings.py`,
  `src/core/alert_history.py`, `src/utils/formatting.py`.
- **Расширенные:** `analyzer`, `monitor`, `handlers`, `keyboards`,
  `config`, `main.py`.
- **Собрана статистика:** после дедупликации ~120 записей в сутки
  (было 300+ дублей/сутки).
- **Анализ выявил:**
  - Отслеживаемые уровни были почти все worn (`touches=46-72`) —
    пользователь удалил их, будут добавлены свежие.
  - `bullish_engulfing` — 1–2 срабатывания, формально корректные,
    но `upper_wick > lower_wick` — **отложено на 5.4** (после набора
    данных).
  - `weak_min = 3.5` — возможно, завышен, но **не трогали**.
  - `worn_level` hard block — **сделано** в 5.2.8.
  - Двойной штраф `htf_against` — **устранён** в 5.2.8.

### Техдолг (перенесён в Этап 6+)

- `bullish_engulfing` — усилить условия (строгое поглощение + проверка
  тени).
- `weak_min = 3.5` — пересмотреть после данных.
- Уровни как **зоны** (`price_low`/`price_high`) вместо точек.
- CSV `utf-8-sig` для Excel (мелко, 1 строка).
- `get_candle_volume_ratio` в `_check_price_cross` — лишний REST-запрос
  (можно из `klines_15m`).
- Гонка при параллельных `add_alert` в `AlertsManager` (см. Этап 3).
- `_handle_export_alert` — 3 REST-запроса в event loop, вынести в
  `asyncio.to_thread`.
- `skip_note_keyboard` — не подключена.

### Статус техдолга (перепроверено по коду, 2026-11-02)

| Пункт | Статус | Где смотреть |
|-------|--------|--------------|
| `bullish_engulfing` — усилить условия | ✅ закрыт (Этап 9) | `analyzer.is_strict_engulfing`: тело ≥ 1.5×, тень ≤ 0.5 тела |
| `weak_min = 3.5` — пересмотреть | ✅ закрыт (Этап 9) | порог доставки вынесен в `THRESHOLDS["deliver_min"] = 2.5` |
| Уровни как зоны (`price_low`/`price_high`) | ✅ закрыт (Этап 10) | `AlertRule.price_low/price_high`, дедуп по пересечению |
| CSV `utf-8-sig` для Excel | ✅ закрыт | `alert_history.py`, `reversal_diary.py`, `virtual_trader.py` |
| `get_candle_volume_ratio` — лишний REST | ✅ закрыт (Этап 9) | вызов убран из `monitor.py`, метод удалён из клиента |
| Гонка при параллельных `add_alert` | ⏳ открыт | `alerts.py::add_alert` вне критической секции |
| `_handle_export_alert` — REST в event loop | ✅ закрыт | `handlers_export.py` → `asyncio.to_thread` |
| `skip_note_keyboard` не подключена | ✅ закрыт (Этап 11) | кнопка «Пропустить заметку» в шаге ввода заметки |

Итого: 7 закрыто, 1 открыт. Закрытые пункты оставлены в списке как
история — не удалять.

## Этап 6 — сканер уровней (ЗАКРЫТ)

**Коммит:** `afb6c89` (`feat(scanner): add level scanner`).

Уровни больше не приходится искать глазами: бот сканирует 4H/1D и
предлагает готовые уровни с оценкой силы.

- **Новые модули:** `src/core/level_scanner.py`,
  `src/core/level_scan_scheduler.py`,
  `src/telegram/handlers_levels.py`.
- **Пайплайн:** свинги → кластеризация близких цен (допуск зависит от
  ATR 15m и процента от цены) → `count_touches` → `is_visible_on_tf` →
  `score_candidate` (касания, свежесть, объём, запас хода до
  следующего уровня).
- **Автоскан:** `LevelScanScheduler` по `level_scan_hours_utc`
  (по умолчанию `6,18` UTC), порог показа `level_scan_min_score`
  (по умолчанию `5.0`).
- **UI:** главное меню → `📊 Сканер уровней`, кнопки «▶️ Запустить
  сейчас», «🔔 Автоскан: ВКЛ/ВЫКЛ», «🎯 Порог score», «⏰ Часы UTC».
- **Результаты:** «➕ SYMBOL · N уровней» (добавить все уровни
  символа), «➕ ➕ Добавить все уровни», «📋 Все уровни текстом»
  (индивидуальные кнопки), «🔄 Обновить».
- **Дедупликация:** перед добавлением — `_level_exists`.
- **Тесты:** `test_level_scanner.py`; пагинация списков —
  `test_handlers_list_pagination.py`.

## Этап 7 — дневник разворотов (ЗАКРЫТ)

**Финальная строка вероятности в алерте:** `408d4c0`.

Цель — понимать, какие сетапы реально отрабатывают, и показывать это
прямо в алерте, а не в теории.

- **Новые модули:** `src/core/reversal_diary.py`,
  `src/core/reversal_diary_scheduler.py`,
  `src/telegram/handlers_diary.py`.
- **Запись:** каждый отправленный алерт (`was_sent=True`,
  `hard_filter is None`, `direction in up|down`) создаёт `pending` с
  `setup_key` — набором признаков: паттерн, объём, тень, HTF, RSI.
- **Резолв** (`ReversalDiaryScheduler`, по умолчанию раз в 20 мин):
  `success` — ход в сторону алерта ≥ `1.0 × ATR_1h` в пределах 8 свечей
  15m; `fail` — раньше пробита стоп-зона `level ± 0.5 × ATR_1h`;
  `expired` — через 48 ч (`pending`/`expired` в процент не идут).
- **Показ:** `probability_for()` → строка
  «📈 История: pinbar·vol≥2x·wick·htf+·RSI<40 → 65% (n=24)» при
  `n ≥ reversal_min_n_display` (по умолчанию 15).
- **UI:** `📈 Статистика разворотов`: запись ВКЛ/ВЫКЛ, мин. n, показ
  статистики, экспорт CSV, очистка.
- **Важно:** дневник **не влияет** на score и hard-фильтры, не ставит
  сделки — только информационная строка.
- **Тесты:** `test_reversal_diary.py`.

## Виртуальный трейдер (вне нумерации плана)

**Коммит:** `d96371e`.

- **Модули:** `src/core/virtual_trader.py`,
  `src/core/virtual_trader_scheduler.py`,
  `src/telegram/handlers_vt.py`.
- **Вход:** кнопка `📝 Взять в дневник` под алертом (если есть `risk`
  с `stop > 0` и `tp1 > 0`) создаёт виртуальную сделку: entry / SL /
  TP1 / TP2, риск в USDT и плечо — метаданные (в PnL не участвуют).
- **Статусы:** `pending_entry` → `open` → `closed` / `cancelled` /
  `expired`. Поддержаны частичные TP (`50/50` или `33/33/34`),
  `realized_pnl` считается по закрытым порциям.
- **Хранение:** `data/virtual_trades.jsonl`, перезапись по `id`.
- **callback_data:** короткий id через `src/core/vt_pending.py` — цены
  с плавающей точкой не влезают в лимит Telegram (64 байта). После
  рестарта бота старые кнопки недействительны, это ожидаемо.
- **UI:** `💼 Виртуальные сделки`: активные, история, статистика,
  экспорт CSV, отмена сделки.
- **Бот по-прежнему не торгует** (см. STRATEGY §6): это симуляция на
  бумаге, заявки никуда не уходят.

## Hotfix 1–3 (2026-11-02)

Три этапа правок по итогам аудита проекта. Коммиты `d1066ad`
(hotfix 1), `0e4ce13` (hotfix 2), `51b654d` (hotfix 3).

### Hotfix 1 — путь отправки, лимиты, пагинация (`d1066ad`)

- **Ретраи Telegram:** 2 попытки, пауза 1–2 с. При неудаче — кулдаун
  30–60 с по символу+уровню, `was_sent=false`,
  `skip_reason=telegram_error`; алерт не теряется.
- **`SEND_INVALID_ALERTS=false` по умолчанию:** в чат идут только
  алерты, прошедшие hard-фильтры; невалидные (`hard_filter is not
  None`) и `verdict == "❌ None"` пишутся в `alert_history` без спама
  в чат.
- **`/list`:** пагинация по 10, заголовок `стр. 2/5 · всего 47`,
  кнопки ←/→.
- **HTML:** экранирование пользовательских заметок и ограничение
  длины сообщений.

### Hotfix 2 — один ATR, TP по уровням (`0e4ce13`)

- **Один ATR на сделку:** `atr_1h`, при отсутствии — `atr_15m`.
  SL = структура ± `ATR_MULTIPLIER × atr`.
- **TP1/TP2 — следующие сильные уровни 4H/1D**, а не N × ATR. Нет
  TP1 → сетап отклоняется (`no_target`); нет TP2 → RR считается по
  TP1. RR — следствие уровней и стопа, а не наоборот.
- **Защита `alerts.json`:** восстановление из `.bak`, атомарная запись
  (`mkstemp` + `os.replace` + `fsync`).

### Hotfix 3 — развязка WS, короткие callback'и, чистка (`51b654d`)

- **Воркер алертов:** отдельный поток + `queue.Queue`, коалесинг по
  символу; WS-поток больше не блокируется отправкой в Telegram.
- **`vt_take`** — короткий id (`vt_pending`), влезает в 64 байта.
- **Чистка:** удалён мёртвый код, актуализированы README/доки.
- **Тесты:** 553, зелёные; `ruff check` чистый; CI (ruff + pytest).

## Этап 8 — синхронизация документации (ЗАКРЫТ)

**Дата:** 2026-11-02.

- Дорожная карта дополнена этапами 5–7, виртуальным трейдером и
  hotfix 1–3.
- Исправлен устаревший дефолт `send_invalid_alerts` (`True` →
  `False`, см. hotfix 1).
- Техдолг перепроверен по коду: 2 пункта закрыты, 6 открыты
  (таблица выше).
- `README.md`: разделы «Сканер уровней», «Дневник разворотов»,
  «Виртуальные сделки», актуальный список настроек, 553 теста.
- `docs/STRATEGY.md`: §4 приведён к коду (worn level — hard block,
  один ATR на сделку), зафиксировано решение по ATR/TP, помечены
  устаревшие пункты v1.1.

## Этап 9 — гейтинг сигналов и точность паттернов (ЗАКРЫТ)

**Дата:** 2026-11-02. **Тесты:** 553 → 580.

### 9.1 Порог доставки отделён от вердикта

Проблема: гейт отправки требовал `verdict != "❌ None"`, а вердикт
выставлялся от `weak_min = 3.5`. При `SEND_INVALID_ALERTS=false` сигналы
со score 2.5–3.5 **не уходили вообще** и даже не логировались как
«почти прошедшие».

- В `THRESHOLDS` добавлен `deliver_min = 2.5`, вердиктов стало четыре:
  `💪 Strong` (≥ 5.5), `⚠️ Weak` (≥ 3.5), `🟡 Marginal` (≥ 2.5),
  `❌ None` (< 2.5).
- Гейт `monitor.py` не менялся: он и так проверяет вердикт, поэтому
  диапазон 2.5–3.5 начал доставляться сам.
- `alert_min_score` из `data/settings.json` остался ручным порогом сверху
  (`-inf` по умолчанию — не режет).
- Тесты: `tests/test_monitor_score_gate.py` — новый класс
  `TestDeliveryThreshold` (marginal доставляется, граница 2.5
  включительная, 2.4 не доставляется, `alert_min_score` режет marginal).

### 9.2 Строгое поглощение

- Новый `analyzer.is_strict_engulfing`: тело ≥ `engulfing_body_mult`
  (1.5) от тела предыдущей свечи и «мешающая» тень ≤
  `engulfing_wick_ratio` (0.5) собственного тела.
- Строгое поглощение даёт полный `WEIGHTS["pattern"] = 2.0`, слабое —
  `engulfing_loose_mult` (0.5) от веса, с пометкой «слабое» в сигналах.
- Флаг `pattern_strict` пишется в `evaluate_alert` и в
  `AlertRecord.pattern_strict` (опциональное поле, старые записи
  читаются без миграции).
- Тесты: `tests/test_engulfing_strict.py` (детектор, предикат строгости,
  веса паттерна, пин-бар не помечается).

### 9.3 Объём без лишнего REST

`monitor._check_price_cross` больше не зовёт
`bybit_client.get_candle_volume_ratio`: `analyzer` уже считает
`vol_ratio` из тех же `klines_15m`. Метод клиента удалён, константа
`VOLUME_PERIODS` из `monitor.py` убрана.

### 9.4 Скрипт разбора истории

`scripts/analyze_history.py` — read-only разбор
`data/alert_history.jsonl`: отправлено/отброшено, вердикты, гистограмма
score, `skip_reason`, hard filters, паттерны (доля строгих поглощений),
статистика `vol_ratio`, топ символов. Флаги `--last`, `--symbol`,
`--json`. Вывод устойчив к cp1251 (эмодзи заменяются ASCII-метками).
Тесты: `tests/test_analyze_history.py`.

### 9.5 Документация окружения

`.env.example` дополнен реальными env-переменными дневника разворотов и
виртуального трейдера; отдельно оговорено, что пресеты кнопок
(`ALERT_SCORE_PRESETS`, `LEVEL_SCAN_*_PRESETS`, `REVERSAL_MIN_N_PRESETS`)
заданы в `src/utils/config.py`, а не в env. Настройки из чата живут в
`data/settings.json`.

### Что осталось открытым

- Уровни как зоны (`price_low`/`price_high`) — Этап 10.
- Гонка `add_alert`, `skip_note_keyboard` — мелкий техдолг.
- Пороги строгого поглощения (1.5× и 0.5) — на наблюдении: менять
  после 100+ записей в истории (`scripts/analyze_history.py`).

## Этап 10 — уровни как зоны (ЗАКРЫТ)

**Дата:** 2026-11-02. **Тесты:** 580 → 620.

По стратегии уровень — это диапазон, а не линия (ШАГ 2). До этапа
`alerts.json` хранил только `price`, и алерт срабатывал от одной точки.

### 10.1 Модель данных

- `AlertRule` получил `price_low` / `price_high` (по умолчанию `None` —
  точечный алерт) и свойства `is_zone`, `low`, `high`, `zone_label()`,
  `overlaps()`.
- **Миграция не нужна:** старые записи читаются как точки. При чтении
  вывернутая зона (`high < low`) разворачивается, одиночная граница
  игнорируется, `price <= 0` при наличии границ заменяется центром зоны.
- **Дедуп по пересечению:** заменяем алерт, если у символа совпадает
  направление и диапазоны пересекаются (касание границами считается
  пересечением). Побочно исправлен баг: раньше сравнение шло только по
  `price`, поэтому `down` на той же цене затирал `up`.

### 10.2 Монитор

- Вход в зону считается по границе: `down` — пробой верхней сверху,
  `up` — нижней снизу, `any` — любой из двух. Для точки
  `low == high == price`, поэтому прежнее поведение сохранено буквально.
- Центр зоны остаётся ключом: кулдаун, `alert_key`, запись в историю и
  callback'и удаления не изменились.
- Сброс флага «уже сработал» для `any` считает расстояние до зоны
  (`_distance_to_zone`), а не до центра.
- Сообщение алерта показывает `Уровень: 100.0000–105.0000`
  (`format_alert_message(..., level_label=...)`).

### 10.3 Ввод и отображение

- Парсер массового добавления принимает `SYMBOL LOW-HIGH DIR [NOTE]`,
  разделители `-`, `–`, `—`; вывернутые границы нормализуются.
  Битая или нулевая цена по-прежнему даёт `skipped` с причиной «цена».
- `add_alert(..., price_low=, price_high=)`; ответ бота показывает
  диапазон, справка дополнена примером зоны.
- `/list` и клавиатура показывают диапазон; callback удаления передаёт
  центр (совместимо со старыми сообщениями). `alerts_list_keyboard`
  принимает и старые 3-кортежи.

### 10.4 Тесты

- `tests/test_alerts_zone.py` — миграция, roundtrip, `zone_label`,
  дедуп (замена по пересечению, раздельные зоны, разные направления),
  отказ при неверных данных.
- `tests/test_monitor_zone_trigger.py` — вход в зону сверху и снизу,
  отсутствие повторного алерта, `any` с обеих сторон, точка как раньше,
  ключ кулдауна по центру, диапазон в тексте алерта.
- `tests/test_handlers_zone.py` — диапазон в `/list` и кнопках,
  callback с центром, обратная совместимость клавиатуры, зональная
  строка в `handle_text`.
- `tests/parsers/test_mass_add.py` — 10 кейсов зон (тире, длинное тире,
  вывернутый диапазон, битые границы, мультистрочный пакет).

### Что осталось

- Этап 11: покрытие Telegram-слоя и планировщиков (`handlers_levels` 12%,
  `virtual_trader` 23%, `bybit_ws` 19%, три планировщика 0%), живой
  smoke-тест бота.
- Мелкий долг: гонка `add_alert`, неиспользуемая `skip_note_keyboard`.

## Этап 11 — качество и проверка (ЗАКРЫТ)

**Дата:** 2026-11-02. **Тесты:** 620 → 807. **Покрытие:** 60% → 78%.

Этап про то, что не видно в фичах: тесты на непокрытые слои, подключение
готовой, но забытой кнопки, и проверка живой доставки.

### 11.1 Покрытие тестами

| Модуль | Было | Стало |
|---|---|---|
| `level_scan_scheduler` | 0% | 99% |
| `reversal_diary_scheduler` | 0% | 87% |
| `virtual_trader_scheduler` | 0% | 91% |
| `data_exporter` | 0% | 97% |
| `virtual_trader` | 23% | 94% |
| `bybit_ws` | 19% | 97% |
| `keyboards` | 50% | 100% |
| `bot.py` | 41% | 85% |
| `handlers_levels` | 12% | 97% |
| **Всего по `src`** | **60%** | **78%** |

Новые файлы тестов: `test_schedulers.py`, `test_data_exporter.py`,
`test_virtual_trader_core.py`, `test_bybit_ws.py`, `test_bot_core.py`,
`test_keyboards.py`, `test_handlers_levels.py`, `test_handlers_add_flow.py`.

Планировщики тестируются без реальных потоков: подменяется событие остановки,
проверяется один проход. WS — на фейковом сокете, без сети.

### 11.2 Найденный баг: экспорт с RSI не работал

`DataExporter.export_symbol_data` вызывал `calculate_rsi` (возвращает одно
число) и дальше делал `len(rsi_values)` и `rsi_values[i]`. Результат: любая
выгрузка падала в `except`, функция возвращала `None`. Исправлено на
`calculate_rsi_series`.

Это же объясняет 0% покрытия `data_exporter`: код не выполнялся в тестах, а в
бою — только через `cleanup_old_files` в `main.py`.

### 11.3 Подключена `skip_note_keyboard`

Кнопка «⏭ Пропустить заметку» существовала в `keyboards.py`, но не была
подключена: пропустить шаг можно было только вручную введя «-».

- На шаге ввода заметки теперь показывается `skip_note_keyboard`.
- Новый callback `skip_note` завершает диалог с пустой заметкой через
  `_finalize_add_alert(..., from_callback=True)`: у callback'а нет
  `update.message`, поэтому ответ уходит в сообщение-запрос, а клавиатура
  с него снимается.

### 11.4 Живой smoke-тест

Добавлен `scripts/smoke_telegram.py`: `getMe` → HTML-сообщение со
спецсимволами → сообщение с инлайн-кнопкой → `editMessageText` → удаление
тестовых сообщений. `getUpdates` не вызывается, чтобы не мешать работающему
боту.

Из среды разработки Telegram API недоступен (соединение обрывается), тогда как
Bybit REST отвечает — поэтому живой прогон делается локально:

```powershell
.venv\Scripts\python.exe scripts/smoke_telegram.py
```

### Что осталось

- Гонка `add_alert` (критическая секция) — единственный открытый пункт техдолга.
- `handlers_screener` 13%, `handlers_export` 37%, `handlers_vt` 38%,
  `handlers_diary` 33% — следующие кандидаты на тесты.
- Идея: включить в CI порог покрытия (например, `--cov-fail-under=75`).

## Версия

- **v1.0** — Этап 1 закрыт (2026-09-29).
- **v1.1** — Этап 2 закрыт (2026-09-29). WebSocket, `resolve_symbol`, кнопки алерта не затирают сообщение.
- **v1.2** — Этап 3 закрыт (2026-09-29). README, ~52 pytest-теста, CI (ruff + pytest).
- **v1.3** — Этап 4 закрыт (2026-09-30). risk / divergence / levels, hard filters, ~119 тестов.
- **v1.4** — Этап 5 закрыт (2026-10-07). Быстрые фиксы 5.1 + буфер алертов 5.2. ~350+ тестов.
- **v1.5** — Этап 6 закрыт. Сканер уровней 4H/1D, автоскан, пагинация списков.
- **v1.6** — Этап 7 закрыт. Дневник разворотов, статистика сетапов в алерте.
- **v1.7** — Виртуальный трейдер. Виртуальные сделки с частичными TP, короткие callback'и.
- **v1.8** — Hotfix 1–3 (2026-11-02). Ретраи Telegram, пагинация `/list`, один ATR, TP по уровням, воркер алертов. 553 теста.
- **v1.9** — Этап 8 закрыт (2026-11-02). Документация синхронизирована с кодом.
- **v2.0** — Этап 9 закрыт (2026-11-02). Порог доставки `deliver_min = 2.5` отделён от вердикта, строгое поглощение, объём из `klines_15m`, `scripts/analyze_history.py`, `.env.example` дополнен. 580 тестов.
- **v2.1** — Этап 10 закрыт (2026-11-02). Уровень как зона: `price_low`/`price_high`, вход в диапазон по границе, дедуп по пересечению, диапазон в алерте и `/list`, ввод `LOW-HIGH`. 620 тестов.
- **v2.2** — Этап 11 закрыт (2026-11-02). Покрытие 60% → 78%, 807 тестов, `scripts/smoke_telegram.py`, подключён `skip_note_keyboard`, исправлен экспорт с RSI.
- **Следующий этап не запланирован.** Идеи на будущее: гонка `add_alert` (критическая секция), порог coverage в CI, тесты `handlers_screener` / `handlers_export`.
- **Следующий этап — 9** (гейтинг сигналов и техдолг: `weak_min`, `bullish_engulfing`, зоны уровней).