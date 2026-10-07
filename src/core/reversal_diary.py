"""
Дневник разворотов (reversal_diary).

Цель:
  Фиксировать исход каждого price-alert'а (success / fail по движению
  цены после кросса уровня) и агрегировать статистику по «сетапам» —
  комбинациям признаков (паттерн свечи, объём, тень, HTF-тренд, RSI).

  По накопленным данным в алерте показывается строка:
      📈 История: pinbar·vol≥2x·wick·htf+·RSI<40 → 65% (n=24)

Что НЕ делает:
  - не влияет на score и hard-фильтры (только текстовая информация),
  - не ставит сделки,
  - не учитывает direction='any' (нет направления — нет «разворота»).

Хранение:
  data/reversal_diary.jsonl — append-only.
  Одна строка = один резолв (не дублируем 30 полей AlertRecord).
  Для stats() достаточно этого файла — setup_key хранится в записи.

Интеграция:
  monitor.py вызывает create_pending() после успешной отправки алерта
  (was_sent=True, hard_filter is None, direction in up|down).
  ReversalDiaryScheduler раз в ~20 мин вызывает resolve_all_pending().

Ресурсы: 1 CPU / 2 GB. Stats — один проход по файлу, кэш в памяти.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ==================== КОНСТАНТЫ ====================

# Сколько свечей рабочего ТФ ждём до резолва (8 × 15m = 2ч).
LOOKAHEAD_BARS: int = 8

# Ход в сторону алерта ≥ N × ATR_1h — success.
MIN_MOVE_ATR: float = 1.0

# Стоп-зона: long — level - SL×ATR, short — level + SL×ATR.
SL_BUFFER_ATR: float = 0.5

# Pending без резолва дольше N часов → expired (не идёт в rate).
PENDING_TIMEOUT_HOURS: int = 48

# Порог для показа строки «📈 История...» в алерте.
MIN_N_DISPLAY: int = 15

# Порог n для попадания в stats() (по умолчанию — 1, всё).
MIN_N_STATS: int = 1


# ==================== DTO ====================

@dataclass
class ReversalResolution:
    """
    Резолв одного алерта: success/fail/expired.

    Не дублирует AlertRecord. Чтобы stats() не читал два файла,
    хранит снимок setup_key и минимальный набор для агрегации.
    """
    record_key: str             # "SYMBOL|level|direction|candle_ts"
    symbol: str
    direction: str              # "up" | "down"
    setup_key: str              # "pinbar|vol_high|wick_1|htf_with|rsi_low"

    status: str                 # "pending" | "success" | "fail" | "expired"
    created_at: float           # когда pending создан

    # Поля, заполняемые при резолве:
    outcome_ts: int | None = None     # timestamp_ms свечи-исхода
    move_atr: float | None = None     # движение в ATR_1h на момент исхода
    bars_to_target: int | None = None # через сколько свечей достигли цели
    hit_stop: bool | None = None      # был ли пробой стоп-зоны
    resolved_at: float | None = None  # когда резолв записан

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReversalResolution:
        return cls(
            record_key=str(data["record_key"]),
            symbol=str(data["symbol"]),
            direction=str(data["direction"]),
            setup_key=str(data["setup_key"]),
            status=str(data.get("status", "pending")),
            created_at=float(data.get("created_at", 0.0)),
            outcome_ts=(
                int(data["outcome_ts"])
                if data.get("outcome_ts") is not None else None
            ),
            move_atr=(
                float(data["move_atr"])
                if data.get("move_atr") is not None else None
            ),
            bars_to_target=(
                int(data["bars_to_target"])
                if data.get("bars_to_target") is not None else None
            ),
            hit_stop=(
                bool(data["hit_stop"])
                if data.get("hit_stop") is not None else None
            ),
            resolved_at=(
                float(data["resolved_at"])
                if data.get("resolved_at") is not None else None
            ),
        )


@dataclass
class SetupStat:
    """
    Статистика по одному сетап-ключу (в разрезе symbol или ALL).

    rate = success / n, n = success + fail (pending/expired не считаем).
    """
    symbol: str                 # конкретный символ или "ALL"
    setup_key: str
    success: int
    fail: int
    n: int
    rate: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ==================== СЕТАП-КЛЮЧ ====================

# Обобщение паттерна: bullish_pinbar / bearish_pinbar → pinbar.
def _pattern_bucket(pattern: str | None) -> str:
    if not pattern:
        return "none"
    if "pinbar" in pattern:
        return "pinbar"
    if "engulfing" in pattern:
        return "engulfing"
    return "other"


def _vol_bucket(ratio: float) -> str:
    if ratio < 1.0:
        return "low"
    if ratio < 2.0:
        return "mid"
    return "high"


def _htf_align(htf_trend: str, htf_against: bool, direction: str) -> str:
    """
    htf_trend — "up"|"down"|"side".
    htf_against — флаг «HTF против направления алерта».
    direction — "up"|"down".

    Возвращает "with" | "against" | "side".
    """
    if htf_trend == "side":
        return "side"
    return "against" if htf_against else "with"


def _rsi_bucket(rsi: float | None, direction: str) -> str:
    """
    Для long (direction=up): rsi < 40 → low, 40..60 → mid, > 60 → high.
    Для short (direction=down): зеркально: > 60 → high, 40..60 → mid, < 40 → low.
    Смысл: «low» = RSI в зоне, благоприятной для входа.
    Для None — "na".
    """
    if rsi is None:
        return "na"
    if direction == "up":
        if rsi < 40:
            return "low"
        if rsi <= 60:
            return "mid"
        return "high"
    # direction == "down"
    if rsi > 60:
        return "high"
    if rsi >= 40:
        return "mid"
    return "low"


def build_setup_key(
    *,
    pattern: str | None,
    volume_ratio: float,
    wick_beyond: bool,
    htf_trend: str,
    htf_against: bool,
    rsi: float | None,
    direction: str,
) -> str:
    """
    Собирает сетап-ключ для агрегации.

    Формат: "<pattern>|vol_<bucket>|wick_<0|1>|htf_<align>|rsi_<bucket>"
    Пример: "pinbar|vol_high|wick_1|htf_with|rsi_low"
    """
    p = _pattern_bucket(pattern)
    v = _vol_bucket(volume_ratio)
    w = 1 if wick_beyond else 0
    h = _htf_align(htf_trend, htf_against, direction)
    r = _rsi_bucket(rsi, direction)
    return f"{p}|vol_{v}|wick_{w}|htf_{h}|rsi_{r}"


# ==================== ЧЕЛОВЕКОЧИТАЕМЫЙ ТЕКСТ ====================

_PATTERN_RU = {
    "pinbar": "pinbar",
    "engulfing": "engulfing",
    "none": "",
    "other": "pat",
}

_VOL_RU = {
    "low": "vol<1x",
    "mid": "",          # mid не показываем (нейтрально)
    "high": "vol≥2x",
}

_HTF_RU = {
    "with": "htf+",
    "against": "htf−",
    "side": "",
}

_RSI_RU = {
    "low": "RSI<40",
    "high": "RSI>60",
    "mid": "",          # нейтрально — не показываем
    "na": "",
}


def humanize_setup_key(setup_key: str) -> str:
    """
    Превращает машинный ключ в компактную строку для Telegram.

    "pinbar|vol_high|wick_1|htf_with|rsi_low" →
      "pinbar·vol≥2x·wick·htf+·RSI<40"
    """
    parts = setup_key.split("|")
    if len(parts) != 5:
        return setup_key  # неизвестный формат — как есть

    pattern, vol, wick, htf, rsi = parts

    chunks: list[str] = []

    p_name = _PATTERN_RU.get(pattern, pattern)
    if p_name:
        chunks.append(p_name)

    v_name = _VOL_RU.get(vol.replace("vol_", ""), "")
    if v_name:
        chunks.append(v_name)

    if wick == "wick_1":
        chunks.append("wick")

    h_name = _HTF_RU.get(htf.replace("htf_", ""), "")
    if h_name:
        chunks.append(h_name)

    r_name = _RSI_RU.get(rsi.replace("rsi_", ""), "")
    if r_name:
        chunks.append(r_name)

    return "·".join(chunks) if chunks else "базовый"


# ==================== РЕЗОЛВ (success / fail) ====================

def resolve_outcome(
    *,
    direction: str,
    level: float,
    atr_1h: float,
    candles_after: list[dict[str, float]],
    lookahead_bars: int = LOOKAHEAD_BARS,
    min_move_atr: float = MIN_MOVE_ATR,
    sl_buffer_atr: float = SL_BUFFER_ATR,
) -> tuple[str, dict[str, Any]]:
    """
    Определяет исход: success / fail / pending.

    candles_after — свечи ПОСЛЕ candle_ts (в порядке Bybit: свежие первыми).
                    Внутри индексы 0..N-1 соответствуют свечам,
                    появившимся после алерта.

    Логика:
      - Порядок проверки внутри свечи: СНАЧАЛА стоп, потом цель
        (консервативно — плохой исход побеждает).
      - Для "up":  цель = level + min_move, стоп = level - sl_buffer
      - Для "down": цель = level - min_move, стоп = level + sl_buffer

    Возвращает (status, details):
      - ("pending", {}) — если свечей меньше lookahead_bars
      - ("success", {move_atr, bars_to_target, outcome_ts, hit_stop})
      - ("fail",    {move_atr, bars_to_target, outcome_ts, hit_stop})
    """
    if len(candles_after) < lookahead_bars:
        return "pending", {}

    min_move = min_move_atr * atr_1h
    sl_buffer = sl_buffer_atr * atr_1h

    if direction == "up":
        target = level + min_move
        stop = level - sl_buffer
    else:  # down
        target = level - min_move
        stop = level + sl_buffer

    candles = candles_after[:lookahead_bars]

    for i, c in enumerate(candles):
        # Проверяем стоп ПЕРВЫМ.
        if direction == "up" and c["low"] <= stop:
            return "fail", {
                "hit_stop": True,
                "bars_to_target": i + 1,
                "outcome_ts": c.get("ts"),
                "move_atr": None,
            }
        if direction == "down" and c["high"] >= stop:
            return "fail", {
                "hit_stop": True,
                "bars_to_target": i + 1,
                "outcome_ts": c.get("ts"),
                "move_atr": None,
            }

        # Цель.
        if direction == "up" and c["high"] >= target:
            move = (c["high"] - level) / atr_1h
            return "success", {
                "hit_stop": False,
                "bars_to_target": i + 1,
                "outcome_ts": c.get("ts"),
                "move_atr": move,
            }
        if direction == "down" and c["low"] <= target:
            move = (level - c["low"]) / atr_1h
            return "success", {
                "hit_stop": False,
                "bars_to_target": i + 1,
                "outcome_ts": c.get("ts"),
                "move_atr": move,
            }

    # Все N свечей прошли, цели не достигли, стопа не было — fail (timeout).
    return "fail", {
        "hit_stop": False,
        "bars_to_target": None,
        "outcome_ts": candles[-1].get("ts") if candles else None,
        "move_atr": None,
    }


# ==================== ДНЕВНИК ====================

class ReversalDiary:
    """
    Потокобезопасное хранилище резолвов (JSONL).

    - append только через self._lock.
    - stats() кэшируется в памяти (пересчёт после каждого append/update).
    - update: перезапись файла целиком (атомарно через mkstemp).
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        # record_key -> ReversalResolution
        self._resolutions: dict[str, ReversalResolution] = {}
        self._load_into_memory()

    # ---------- хранилище ----------

    def _load_into_memory(self) -> None:
        self._resolutions.clear()
        if not self.path.exists():
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                for i, line in enumerate(f, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        res = ReversalResolution.from_dict(data)
                    except (json.JSONDecodeError, TypeError, KeyError) as e:
                        logger.warning(
                            f"⚠️ Пропущена битая запись (строка {i}): {e}"
                        )
                        continue
                    self._resolutions[res.record_key] = res
        except FileNotFoundError:
            pass

    def _rewrite_unlocked(self) -> None:
        """Атомарная перезапись файла из self._resolutions."""
        if not self._resolutions:
            if self.path.exists():
                self.path.unlink()
            return

        fd, tmp = tempfile.mkstemp(
            prefix=self.path.name + ".", suffix=".tmp",
            dir=str(self.path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                for r in self._resolutions.values():
                    f.write(json.dumps(
                        r.to_dict(), ensure_ascii=False,
                    ))
                    f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ---------- CRUD ----------

    def create_pending(self, res: ReversalResolution) -> None:
        """Сохраняет pending-запись. Идемпотентно по record_key."""
        with self._lock:
            if res.record_key in self._resolutions:
                return
            self._resolutions[res.record_key] = res
            self._rewrite_unlocked()
        logger.debug(f"📔 Pending: {res.record_key} ({res.setup_key})")

    def update_resolution(self, res: ReversalResolution) -> None:
        """Обновляет запись (после резолва)."""
        with self._lock:
            self._resolutions[res.record_key] = res
            self._rewrite_unlocked()

    def get(self, record_key: str) -> ReversalResolution | None:
        with self._lock:
            return self._resolutions.get(record_key)

    def all_records(self) -> list[ReversalResolution]:
        with self._lock:
            return list(self._resolutions.values())

    def count(self) -> int:
        with self._lock:
            return len(self._resolutions)

    def count_by_status(self, status: str) -> int:
        with self._lock:
            return sum(1 for r in self._resolutions.values() if r.status == status)

    def clear(self) -> None:
        with self._lock:
            self._resolutions.clear()
            if self.path.exists():
                self.path.unlink()
            logger.info("🗑 Дневник разворотов очищен")

    # ---------- резолв ----------

    def resolve_all_pending(
        self,
        klines_provider,   # (symbol, category) -> list[list] | None
        atr_1h_provider,   # (symbol, category) -> float | None
        category_provider, # (symbol) -> "linear"|"spot"
        *,
        lookahead_bars: int = LOOKAHEAD_BARS,
        min_move_atr: float = MIN_MOVE_ATR,
        sl_buffer_atr: float = SL_BUFFER_ATR,
        pending_timeout_hours: int = PENDING_TIMEOUT_HOURS,
        now: float | None = None,
    ) -> int:
        """
        Резолвит все pending-записи, для которых уже достаточно klines.

        Возвращает количество резолвнутых.
        """
        if now is None:
            now = time.time()
        timeout_sec = pending_timeout_hours * 3600

        with self._lock:
            pending = [
                r for r in self._resolutions.values()
                if r.status == "pending"
            ]

        resolved = 0
        for res in pending:
            # Таймаут — expired.
            if now - res.created_at > timeout_sec:
                res.status = "expired"
                res.resolved_at = now
                self.update_resolution(res)
                resolved += 1
                continue

            category = category_provider(res.symbol)
            klines = klines_provider(res.symbol, category)
            atr_1h = atr_1h_provider(res.symbol, category)
            if not klines or not atr_1h or atr_1h <= 0:
                continue

            # Находим свечу с ts == candle_ts (из record_key).
            # record_key = "SYM|level|dir|candle_ts" — распарсим.
            try:
                candle_ts = int(res.record_key.rsplit("|", 1)[1])
            except (ValueError, IndexError):
                res.status = "expired"
                res.resolved_at = now
                self.update_resolution(res)
                resolved += 1
                continue

            # Парсим klines в список dict с ts.
            parsed = [
                {
                    "ts": int(k[0]),
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                }
                for k in klines
            ]

            # Ищем свечу с ts == candle_ts. В Bybit-порядке новые первыми.
            idx = None
            for i, c in enumerate(parsed):
                if c["ts"] == candle_ts:
                    idx = i
                    break
            if idx is None:
                # Свеча уже выпала из окна klines — expired.
                if now - res.created_at > 24 * 3600:
                    res.status = "expired"
                    res.resolved_at = now
                    self.update_resolution(res)
                    resolved += 1
                continue

            # Свечи ПОСЛЕ candle_ts — это элементы с меньшим индексом
            # (новые первые). Берём срез [0:idx] и реверсим, чтобы получить
            # хронологический порядок (старые первыми).
            after = parsed[:idx]
            if not after:
                # Ещё ни одной свечи не закрылось после алерта.
                continue
            # Свежесть не важна — важен порядок; резолв делает проход
            # по «следующим N свечам» от момента алерта. Реверсим в
            # хронологию: старые первыми (самая старая = ближе к candle_ts).
            after_chrono = list(reversed(after))

            status, details = resolve_outcome(
                direction=res.direction,
                level=_extract_level(res.record_key),
                atr_1h=atr_1h,
                candles_after=after_chrono,
                lookahead_bars=lookahead_bars,
                min_move_atr=min_move_atr,
                sl_buffer_atr=sl_buffer_atr,
            )

            if status == "pending":
                continue

            res.status = status
            res.hit_stop = details.get("hit_stop")
            res.bars_to_target = details.get("bars_to_target")
            res.outcome_ts = details.get("outcome_ts")
            res.move_atr = details.get("move_atr")
            res.resolved_at = now
            self.update_resolution(res)
            resolved += 1

        if resolved:
            logger.info(f"📔 Резолвнуто записей: {resolved}")
        return resolved

    # ---------- статистика ----------

    def stats(
        self,
        symbol: str | None = None,
        min_n: int = MIN_N_STATS,
        include_all: bool = True,
    ) -> list[SetupStat]:
        """
        Статистика по сетап-ключам.

        symbol=None → по каждому символу + агрегат "ALL" (если include_all).
        symbol="ETHUSDT" → только по этому символу.
        Возвращает список SetupStat, отсортированный по n DESC.
        """
        with self._lock:
            records = [
                r for r in self._resolutions.values()
                if r.status in ("success", "fail")
                and r.direction in ("up", "down")
            ]

        # symbol -> setup_key -> [success, fail]
        buckets: dict[tuple[str, str], list[int]] = {}

        for r in records:
            if symbol is not None and r.symbol != symbol:
                continue
            key = (r.symbol, r.setup_key)
            if key not in buckets:
                buckets[key] = [0, 0]
            if r.status == "success":
                buckets[key][0] += 1
            else:
                buckets[key][1] += 1

        out: list[SetupStat] = []
        for (sym, sk), (s, f) in buckets.items():
            n = s + f
            if n < min_n:
                continue
            out.append(SetupStat(
                symbol=sym, setup_key=sk,
                success=s, fail=f, n=n,
                rate=s / n if n > 0 else 0.0,
            ))

        # Агрегат ALL — только если не смотрели конкретный символ.
        if symbol is None and include_all:
            all_buckets: dict[str, list[int]] = {}
            for r in records:
                if r.setup_key not in all_buckets:
                    all_buckets[r.setup_key] = [0, 0]
                if r.status == "success":
                    all_buckets[r.setup_key][0] += 1
                else:
                    all_buckets[r.setup_key][1] += 1
            for sk, (s, f) in all_buckets.items():
                n = s + f
                if n < min_n:
                    continue
                out.append(SetupStat(
                    symbol="ALL", setup_key=sk,
                    success=s, fail=f, n=n,
                    rate=s / n if n > 0 else 0.0,
                ))

        out.sort(key=lambda st: (-st.n, -st.rate))
        return out

    def probability_for(
        self,
        symbol: str,
        setup_key: str,
        min_n: int = MIN_N_DISPLAY,
    ) -> tuple[float, int] | None:
        """
        Возвращает (rate, n) для сетап-ключа по символу.
        Если по символу n < min_n — fallback на ALL.
        Если и там n < min_n — None.
        """
        sym_stats = self.stats(symbol=symbol, min_n=1, include_all=False)
        for st in sym_stats:
            if st.setup_key == setup_key and st.n >= min_n:
                return st.rate, st.n

        all_stats = self.stats(symbol=None, min_n=1, include_all=True)
        for st in all_stats:
            if st.symbol == "ALL" and st.setup_key == setup_key and st.n >= min_n:
                return st.rate, st.n

        return None

    # ---------- форматирование ----------

    def format_stats_message(
        self,
        symbol: str | None = None,
        limit: int = 10,
    ) -> str:
        """
        HTML-сообщение со статистикой (для Telegram).

        symbol=None → по всем символам + ALL.
        symbol="ETHUSDT" → по ETH + ALL.
        """
        stats_list = self.stats(symbol=symbol, min_n=1, include_all=True)
        if not stats_list:
            return "📈 <b>Дневник разворотов</b>\n\nПока нет данных."

        header = f"📈 <b>Дневник разворотов"
        if symbol:
            header += f" · {symbol}"
        header += "</b>\n"
        header += (
            f"Всего записей: {self.count()}  |  "
            f"pending: {self.count_by_status('pending')}  |  "
            f"success: {self.count_by_status('success')}  |  "
            f"fail: {self.count_by_status('fail')}  |  "
            f"expired: {self.count_by_status('expired')}"
        )

        # Группируем по символу.
        by_symbol: dict[str, list[SetupStat]] = {}
        for st in stats_list[:limit * 3]:
            by_symbol.setdefault(st.symbol, []).append(st)

        lines = [header, ""]
        for sym, items in by_symbol.items():
            lines.append(f"<b>{sym}</b>")
            for st in items[:limit]:
                human = humanize_setup_key(st.setup_key)
                lines.append(
                    f"  {human} → {st.rate:.0%} "
                    f"(n={st.n}: ✅{st.success} / ❌{st.fail})"
                )
            lines.append("")
        return "\n".join(lines).rstrip()

    # ---------- экспорт ----------

    def export_csv(self, out_path: Path | str) -> Path:
        """Экспорт всех записей в CSV (utf-8-sig для Excel)."""
        import csv
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        cols = [
            "record_key", "symbol", "direction", "setup_key",
            "status", "outcome_ts", "move_atr", "bars_to_target",
            "hit_stop", "created_at", "resolved_at",
        ]
        with open(out, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for r in self.all_records():
                d = r.to_dict()
                w.writerow([d[c] if d[c] is not None else "" for c in cols])

        logger.info(f"📄 Дневник выгружен: {out} ({self.count()} записей)")
        return out


# ==================== ВСПОМОГАТЕЛЬНОЕ ====================

def _extract_level(record_key: str) -> float:
    """
    record_key = "SYM|level|dir|candle_ts" → float level.
    """
    try:
        return float(record_key.split("|")[1])
    except (ValueError, IndexError):
        return 0.0


def build_record_key(
    symbol: str, level: float, direction: str, candle_ts: int,
) -> str:
    """Собирает record_key. Формат: SYM|level|dir|candle_ts."""
    return f"{symbol}|{level}|{direction}|{candle_ts}"


# ==================== СИНГЛТОН ====================

_diary_instance: ReversalDiary | None = None
_diary_lock = threading.RLock()


def init_reversal_diary(path: Path | str) -> ReversalDiary:
    """Инициализирует глобальный инстанс. Вызывается один раз в main.py."""
    global _diary_instance
    with _diary_lock:
        _diary_instance = ReversalDiary(path)
        return _diary_instance


def get_reversal_diary() -> ReversalDiary:
    """Возвращает глобальный инстанс. Для тестов — set_reversal_diary()."""
    global _diary_instance
    with _diary_lock:
        if _diary_instance is None:
            from src.utils.config import Config
            _diary_instance = ReversalDiary(Config.REVERSAL_DIARY_FILE)
        return _diary_instance


def set_reversal_diary(diary: ReversalDiary | None) -> None:
    """Переопределяет глобальный инстанс. Для тестов."""
    global _diary_instance
    with _diary_lock:
        _diary_instance = diary