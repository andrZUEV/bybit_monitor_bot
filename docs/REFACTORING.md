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
| 2 | WebSocket + рефакторинг цикла мониторинга | ✅ ЗАКРЫТ |
| 3 | Порядок в репо: `.gitignore`, README, тесты, CI | ⏳ НЕ НАЧАТ |
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
4. Указать текущий этап (3) и конкретную задачу.

---

## Этап 3 — что было сделано (README, тесты, CI)

### Цель

Порядок в репо: документация, тесты, автоматическая проверка.

### Артефакты

1. **`README.md`** — описание, установка, запуск, переменные
   окружения, команды бота, структура, разработка.

2. **`tests/`** — pytest, без сетевых вызовов (~52 теста):
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

### Технический долг (перенесён в Этап 4)

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

  ## Версия

- **v1.0** — Этап 1 закрыт (2026-09-29).
- **v1.1** — Этап 2 закрыт (2026-09-29). WebSocket, `resolve_symbol`, кнопки алерта не затирают сообщение.
- **v1.2** — Этап 3 закрыт (2026-09-29). README, ~52 pytest-теста, CI (ruff + pytest).
- Следующий этап — 4 (risk, divergence, levels).