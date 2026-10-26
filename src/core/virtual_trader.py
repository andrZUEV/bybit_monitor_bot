"""
Виртуальные сделки — симуляция реальных лимитных сделок без риска.

Трейдер записывает сделку (entry/SL/TP/риск/плечо), бот следит за
ценой и определяет: сработал вход, дошёл до TP (успех), выбило по
SL (провал). Поддерживаются частичные TP (50/50 или 33/33/34).

PnL считается как: риск × (движение / стоп_дистанция) × portion.
Плечо — только метаданные (в PnL не участвует).

Хранение: data/virtual_trades.jsonl (append-only, перезапись по id).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ==================== КОНСТАНТЫ ====================

DEFAULT_RISK_USDT: float = 1.0
DEFAULT_LEVERAGE: int = 10

# Таймауты
PENDING_ENTRY_TIMEOUT_DAYS: int = 0   # 0 = бесконечно


# ==================== DTO ====================

@dataclass
class VirtualTrade:
    """Одна виртуальная сделка."""
    id: str
    symbol: str
    direction: str              # "up" (long) | "down" (short)
    entry: float
    sl: float
    tps: list[float]            # [tp1, tp2, ...]
    portions: list[float]       # [0.5, 0.5] или [0.33, 0.33, 0.34]

    risk_usdt: float            # 1.00
    leverage: int               # 10 (метаданные)

    # Связь с алертом (опционально)
    alert_level: float | None = None
    setup_key: str | None = None
    alert_id: str | None = None

    # Статус
    status: str = "pending_entry"
    # "pending_entry" | "open" | "closed" | "cancelled" | "expired"

    created_at: float = field(default_factory=time.time)
    entry_ts: float | None = None
    closed_at: float | None = None

    # Реализованные TP: [{"tp": float, "portion": float, "pnl": float, "ts": float}]
    filled_tps: list[dict[str, Any]] = field(default_factory=list)
    realized_pnl: float = 0.0

    # Закрытие (при SL или ручном)
    close_reason: str | None = None   # "tp" | "sl" | "manual" | None
    close_price: float | None = None

    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VirtualTrade:
        # Гарантируем наличие обязательных полей.
        return cls(
            id=str(data["id"]),
            symbol=str(data["symbol"]),
            direction=str(data["direction"]),
            entry=float(data["entry"]),
            sl=float(data["sl"]),
            tps=[float(x) for x in data.get("tps", [])],
            portions=[float(x) for x in data.get("portions", [])],
            risk_usdt=float(data.get("risk_usdt", DEFAULT_RISK_USDT)),
            leverage=int(data.get("leverage", DEFAULT_LEVERAGE)),
            alert_level=(
                float(data["alert_level"])
                if data.get("alert_level") is not None else None
            ),
            setup_key=data.get("setup_key"),
            alert_id=data.get("alert_id"),
            status=str(data.get("status", "pending_entry")),
            created_at=float(data.get("created_at", time.time())),
            entry_ts=(
                float(data["entry_ts"])
                if data.get("entry_ts") is not None else None
            ),
            closed_at=(
                float(data["closed_at"])
                if data.get("closed_at") is not None else None
            ),
            filled_tps=list(data.get("filled_tps", [])),
            realized_pnl=float(data.get("realized_pnl", 0.0)),
            close_reason=data.get("close_reason"),
            close_price=(
                float(data["close_price"])
                if data.get("close_price") is not None else None
            ),
            notes=str(data.get("notes", "")),
        )

    @property
    def is_long(self) -> bool:
        return self.direction == "up"

    @property
    def total_risk(self) -> float:
        """Общий риск в USDT."""
        return self.risk_usdt


# ==================== ХЕЛПЕРЫ ====================

def split_portions(n: int) -> list[float]:
    """
    Делит 1.0 на n частей.
    1 → [1.0]
    2 → [0.5, 0.5]
    3 → [0.333, 0.333, 0.334]
    """
    if n <= 0:
        return [1.0]
    if n == 1:
        return [1.0]
    base = 1.0 / n
    portions = [base] * n
    portions[-1] = 1.0 - sum(portions[:-1])
    return portions


def calc_pnl(
    *,
    direction: str,
    entry: float,
    exit_price: float,
    sl: float,
    risk_usdt: float,
    portion: float,
) -> float:
    """
    PnL для порции позиции (portion = 0..1).

    Для long: pnl_ratio = (exit - entry) / (entry - sl).
    Для short: pnl_ratio = (entry - exit) / (sl - entry).
    pnl = pnl_ratio * risk_usdt * portion.

    При exit == SL: pnl = -risk_usdt * portion.
    При exit == TP: pnl = R:R * risk_usdt * portion.
    """
    if portion <= 0:
        return 0.0

    if direction == "up":
        denom = entry - sl
        if denom <= 0:
            return 0.0
        pnl_ratio = (exit_price - entry) / denom
    else:  # down
        denom = sl - entry
        if denom <= 0:
            return 0.0
        pnl_ratio = (entry - exit_price) / denom

    return round(pnl_ratio * risk_usdt * portion, 6)


def calc_rr(
    *,
    direction: str,
    entry: float,
    tp: float,
    sl: float,
) -> float:
    """R:R для конкретного TP."""
    if direction == "up":
        risk = entry - sl
        reward = tp - entry
    else:
        risk = sl - entry
        reward = entry - tp
    if risk <= 0:
        return 0.0
    return round(reward / risk, 2)

# ==================== РЕЗОЛВЕР ====================

def _check_open(
    trade: VirtualTrade,
    candle: dict[str, float],
) -> bool:
    """
    Проверяет, сработал ли вход в сделку на данной свече.
    Возвращает True, если сработал.
    """
    if trade.is_long:
        return candle["low"] <= trade.entry
    return candle["high"] >= trade.entry


def _check_sl(
    trade: VirtualTrade,
    candle: dict[str, float],
) -> bool:
    """Проверяет, сработал ли SL."""
    if trade.is_long:
        return candle["low"] <= trade.sl
    return candle["high"] >= trade.sl


def _check_tp(
    trade: VirtualTrade,
    candle: dict[str, float],
    tp: float,
) -> bool:
    """Проверяет, сработал ли конкретный TP."""
    if trade.is_long:
        return candle["high"] >= tp
    return candle["low"] <= tp


def _apply_tp(
    trade: VirtualTrade,
    tp: float,
    tp_index: int,
    candle_ts: int,
    now: float,
) -> None:
    """Применяет частичное закрытие по TP."""
    portion = trade.portions[tp_index] if tp_index < len(trade.portions) else 0.0
    if portion <= 0:
        return

    pnl = calc_pnl(
        direction=trade.direction,
        entry=trade.entry,
        exit_price=tp,
        sl=trade.sl,
        risk_usdt=trade.risk_usdt,
        portion=portion,
    )
    trade.filled_tps.append({
        "tp": tp,
        "portion": portion,
        "pnl": pnl,
        "ts": candle_ts,
    })
    trade.realized_pnl = round(trade.realized_pnl + pnl, 6)


def _close_trade(
    trade: VirtualTrade,
    reason: str,
    close_price: float,
    candle_ts: int,
    now: float,
) -> None:
    """Полностью закрывает сделку (по SL или вручную)."""
    trade.status = "closed"
    trade.close_reason = reason
    trade.close_price = close_price
    trade.closed_at = now

    # Если закрыто по SL — все оставшиеся portion идут в убыток.
    if reason == "sl":
        filled_sum = sum(x["portion"] for x in trade.filled_tps)
        remaining = 1.0 - filled_sum
        if remaining > 0:
            pnl = calc_pnl(
                direction=trade.direction,
                entry=trade.entry,
                exit_price=close_price,
                sl=trade.sl,
                risk_usdt=trade.risk_usdt,
                portion=remaining,
            )
            trade.filled_tps.append({
                "tp": None,
                "portion": remaining,
                "pnl": pnl,
                "ts": candle_ts,
                "reason": "sl",
            })
            trade.realized_pnl = round(trade.realized_pnl + pnl, 6)


def resolve_trade(
    trade: VirtualTrade,
    candles_after: list[dict[str, float]],
    now: float | None = None,
) -> bool:
    """
    Резолвит одну сделку по свечам после её создания.

    candles_after — свечи в хронологическом порядке (старые первыми),
    только те, что ПОСЛЕ created_at сделки.

    Возвращает True, если статус изменился (был pending/open, стал closed/cancelled).
    """
    if now is None:
        now = time.time()

    if trade.status in ("closed", "cancelled"):
        return False

    if not candles_after:
        return False

    # Pending → open
    if trade.status == "pending_entry":
        for c in candles_after:
            # Пессимистично: SL проверяем первым.
            if _check_sl(trade, c):
                # Вход и SL в одной свече → сделка вообще не открылась.
                # Если в этой свече сначала был вход, а потом SL —
                # по пессимистичной логике, всё равно SL.
                _close_trade(trade, "sl", trade.sl, int(c.get("ts", 0)), now)
                return True
            if _check_open(trade, c):
                trade.status = "open"
                trade.entry_ts = now
                break

    # Open → проверяем TPs и SL
    if trade.status == "open":
        # Индекс первого невыполненного TP.
        next_tp_idx = len(trade.filled_tps)

        for c in candles_after:
            # Если свеча была ДО открытия — пропускаем.
            if (
                trade.entry_ts is not None
                and c.get("ts", 0) < trade.entry_ts * 1000
            ):
                continue

            # Пессимистично: SL проверяем первым.
            if _check_sl(trade, c):
                _close_trade(trade, "sl", trade.sl, int(c.get("ts", 0)), now)
                return True

            # Проверяем TPs по порядку.
            while next_tp_idx < len(trade.tps):
                tp = trade.tps[next_tp_idx]
                if _check_tp(trade, c, tp):
                    _apply_tp(trade, tp, next_tp_idx, int(c.get("ts", 0)), now)
                    next_tp_idx += 1
                else:
                    break

            # Если все TP выполнены — закрываем сделку.
            if next_tp_idx >= len(trade.tps):
                trade.status = "closed"
                trade.close_reason = "tp"
                trade.close_price = trade.tps[-1] if trade.tps else trade.entry
                trade.closed_at = now
                return True

    return False

# ==================== ХРАНИЛИЩЕ ====================

class VirtualTrader:
    """Потокобезопасное хранилище виртуальных сделок (JSONL)."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        self._trades: dict[str, VirtualTrade] = {}
        self._load_into_memory()

    def _load_into_memory(self) -> None:
        self._trades.clear()
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
                        t = VirtualTrade.from_dict(data)
                    except (json.JSONDecodeError, TypeError, KeyError) as e:
                        logger.warning(
                            f"⚠️ Пропущена битая сделка (строка {i}): {e}"
                        )
                        continue
                    self._trades[t.id] = t
        except FileNotFoundError:
            pass

    def _rewrite_unlocked(self) -> None:
        """Атомарная перезапись файла."""
        if not self._trades:
            if self.path.exists():
                self.path.unlink()
            return
        fd, tmp = tempfile.mkstemp(
            prefix=self.path.name + ".", suffix=".tmp",
            dir=str(self.path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                for t in self._trades.values():
                    f.write(json.dumps(t.to_dict(), ensure_ascii=False))
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

    def create(
        self,
        *,
        symbol: str,
        direction: str,
        entry: float,
        sl: float,
        tps: list[float],
        risk_usdt: float = DEFAULT_RISK_USDT,
        leverage: int = DEFAULT_LEVERAGE,
        alert_level: float | None = None,
        setup_key: str | None = None,
        alert_id: str | None = None,
        notes: str = "",
    ) -> VirtualTrade:
        """Создаёт виртуальную сделку со статусом pending_entry."""
        if direction not in ("up", "down"):
            raise ValueError(f"Неверное направление: {direction}")
        if not tps:
            raise ValueError("Нужен хотя бы один TP")
        if entry <= 0 or sl <= 0 or any(tp <= 0 for tp in tps):
            raise ValueError("Цены должны быть > 0")

        # Проверка направления цен.
        if direction == "up":
            if sl >= entry:
                raise ValueError("Для long SL должен быть < entry")
            if any(tp <= entry for tp in tps):
                raise ValueError("Для long все TP должны быть > entry")
        else:
            if sl <= entry:
                raise ValueError("Для short SL должен быть > entry")
            if any(tp >= entry for tp in tps):
                raise ValueError("Для short все TP должны быть < entry")

        trade = VirtualTrade(
            id=str(uuid.uuid4()),
            symbol=symbol.upper(),
            direction=direction,
            entry=float(entry),
            sl=float(sl),
            tps=[float(x) for x in tps],
            portions=split_portions(len(tps)),
            risk_usdt=float(risk_usdt),
            leverage=int(leverage),
            alert_level=alert_level,
            setup_key=setup_key,
            alert_id=alert_id,
            status="pending_entry",
            created_at=time.time(),
            notes=notes,
        )
        with self._lock:
            self._trades[trade.id] = trade
            self._rewrite_unlocked()
        logger.info(
            f"📝 Виртуальная сделка: {trade.symbol} {direction} "
            f"entry={entry} sl={sl} tps={tps}"
        )
        return trade

    def get(self, trade_id: str) -> VirtualTrade | None:
        with self._lock:
            return self._trades.get(trade_id)

    def all_trades(self) -> list[VirtualTrade]:
        with self._lock:
            return list(self._trades.values())

    def active_trades(self) -> list[VirtualTrade]:
        with self._lock:
            return [
                t for t in self._trades.values()
                if t.status in ("pending_entry", "open")
            ]

    def count(self) -> int:
        with self._lock:
            return len(self._trades)

    def count_by_status(self, status: str) -> int:
        with self._lock:
            return sum(1 for t in self._trades.values() if t.status == status)

    def cancel(self, trade_id: str) -> bool:
        with self._lock:
            t = self._trades.get(trade_id)
            if not t or t.status in ("closed", "cancelled"):
                return False
            t.status = "cancelled"
            t.closed_at = time.time()
            self._rewrite_unlocked()
            return True

    def update(self, trade: VirtualTrade) -> None:
        with self._lock:
            self._trades[trade.id] = trade
            self._rewrite_unlocked()

    def clear(self) -> None:
        with self._lock:
            self._trades.clear()
            if self.path.exists():
                self.path.unlink()

    # ---------- Резолв ----------

    def resolve_all(
        self,
        klines_provider,
        category_provider,
        *,
        now: float | None = None,
    ) -> int:
        """
        Резолвит все pending/open сделки.
        Возвращает количество, которые изменили статус.
        """
        if now is None:
            now = time.time()

        active = self.active_trades()
        if not active:
            return 0

        resolved = 0
        for trade in active:
            category = category_provider(trade.symbol)
            klines = klines_provider(trade.symbol, category)
            if not klines:
                continue

            # Парсим klines в хронологическом порядке (старые первыми).
            parsed = sorted(
                [
                    {
                        "ts": int(k[0]),
                        "open": float(k[1]),
                        "high": float(k[2]),
                        "low": float(k[3]),
                        "close": float(k[4]),
                        "volume": float(k[5]),
                    }
                    for k in klines
                ],
                key=lambda x: x["ts"],
            )

            # Оставляем только свечи после созданной сделки.
            created_ms = int(trade.created_at * 1000)
            after = [c for c in parsed if c["ts"] >= created_ms]

            if not after:
                continue

            changed = resolve_trade(trade, after, now=now)
            if changed:
                self.update(trade)
                resolved += 1

        if resolved:
            logger.info(f"💼 VirtualTrader: резолвнуто {resolved} сделок")
        return resolved

def format_stats_message(trader: VirtualTrader) -> str:
    """Формирует текстовый отчёт по статистике виртуальных сделок."""
    all_trades = trader.all_trades()
    if not all_trades:
        return "💼 <b>Виртуальные сделки</b>\n\nПока нет данных."

    closed = [t for t in all_trades if t.status == "closed"]
    active = [t for t in all_trades if t.status in ("pending_entry", "open")]
    
    total_closed = len(closed)
    wins = sum(1 for t in closed if t.realized_pnl > 0)
    losses = sum(1 for t in closed if t.realized_pnl <= 0)
    
    winrate = (wins / total_closed * 100) if total_closed > 0 else 0.0
    total_pnl = sum(t.realized_pnl for t in closed)
    
    # Группировка по символам
    by_symbol: dict[str, dict] = {}
    for t in closed:
        if t.symbol not in by_symbol:
            by_symbol[t.symbol] = {"wins": 0, "losses": 0, "pnl": 0.0}
        by_symbol[t.symbol]["pnl"] += t.realized_pnl
        if t.realized_pnl > 0:
            by_symbol[t.symbol]["wins"] += 1
        else:
            by_symbol[t.symbol]["losses"] += 1

    lines = [
        "💼 <b>Статистика виртуальных сделок</b>",
        "",
        f"Всего закрыто: <b>{total_closed}</b>",
        f"✅ Прибыльных: <b>{wins}</b>",
        f"❌ Убыточных: <b>{losses}</b>",
        f"🎯 Winrate: <b>{winrate:.1f}%</b>",
        f"💰 Общий PnL: <b>{total_pnl:+.2f} USDT</b>",
        "",
        f"🟢 Активных сейчас: <b>{len(active)}</b>",
    ]

    if by_symbol:
        lines.extend(["", "<b>По символам:</b>"])
        # Сортируем по PnL (лучшие сверху)
        sorted_symbols = sorted(
            by_symbol.items(),
            key=lambda x: x[1]["pnl"],
            reverse=True,
        )
        for sym, data in sorted_symbols:
            n = data["wins"] + data["losses"]
            wr = (data["wins"] / n * 100) if n > 0 else 0
            lines.append(
                f"  <b>{sym}</b>: {n} сделок, "
                f"{wr:.0f}% WR, PnL: {data['pnl']:+.2f} USDT"
            )

    return "\n".join(lines)

# ==================== СИНГЛТОН ====================

_trader_instance: VirtualTrader | None = None
_trader_lock = threading.RLock()


def init_virtual_trader(path: Path | str) -> VirtualTrader:
    global _trader_instance
    with _trader_lock:
        _trader_instance = VirtualTrader(path)
        return _trader_instance


def get_virtual_trader() -> VirtualTrader:
    global _trader_instance
    with _trader_lock:
        if _trader_instance is None:
            from src.utils.config import Config
            _trader_instance = VirtualTrader(Config.VIRTUAL_TRADER_FILE)
        return _trader_instance


def set_virtual_trader(trader: VirtualTrader | None) -> None:
    global _trader_instance
    with _trader_lock:
        _trader_instance = trader