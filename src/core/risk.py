"""
Risk-модуль стратегии «Уровневый отбой».

Чистая математика: RR, размер позиции, ATR-стоп, hard filters.
Без обращений к API/БД/Telegram. Всё — детерминированные функции.

Соответствие STRATEGY.md:
- Риск на сделку ≤ 1%        → validate_setup (risk_above_max)
- RR минимум 1:3              → validate_setup (rr_below_min)
- Стоп за структурой + ATR×1.5 → build_setup / calc_atr_stop / choose_stop
- 4H-тренд против направления → hard block (trend_conflict)
- «Запас хода» до TP1 ≥ 2×ATR → validate_setup (insufficient_runway)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

Direction = Literal["long", "short"]

# Алиасы analyzer.py → risk.py
_ANALYZER_TO_RISK: dict[str, Direction] = {
    "up": "long",
    "down": "short",
}
_RISK_TO_ANALYZER: dict[Direction, str] = {v: k for k, v in _ANALYZER_TO_RISK.items()}


def analyzer_dir_to_risk(direction: str) -> Direction:
    """'up'|'down' → 'long'|'short'. Бросает ValueError на мусор."""
    try:
        return _ANALYZER_TO_RISK[direction]
    except KeyError as e:
        raise ValueError(f"Unknown analyzer direction: {direction!r}") from e


def risk_dir_to_analyzer(direction: Direction) -> str:
    """'long'|'short' → 'up'|'down'."""
    try:
        return _RISK_TO_ANALYZER[direction]
    except KeyError as e:
        raise ValueError(f"Unknown risk direction: {direction!r}") from e


def _check_direction(direction: str) -> None:
    if direction not in ("long", "short"):
        raise ValueError(f"direction must be 'long'|'short', got {direction!r}")


# ==================== КОНФИГ ====================

# Жёсткие лимиты стратегии — не переопределяются из .env
HARD_MAX_RISK_PCT = 0.01      # ≤ 1% депозита
HARD_MIN_RR = 3.0             # минимум 1:3
SOFT_MIN_RR = 2.5             # < 1:2.5 — точно пропуск
MIN_RUNWAY_ATR = 2.0          # расстояние до TP1 ≥ 2×ATR


@dataclass(frozen=True)
class RiskConfig:
    """Параметры риск-менеджмента. Заполняется из Config + defaults."""
    equity: float
    risk_pct: float = 0.01
    min_rr: float = HARD_MIN_RR
    atr_multiplier: float = 1.5
    min_runway_atr: float = MIN_RUNWAY_ATR
    lot_step: float = 0.0
    min_qty: float = 0.0
    max_qty: float = float("inf")

    def __post_init__(self) -> None:
        if self.equity <= 0:
            raise ValueError("equity must be > 0")
        if not (0 < self.risk_pct <= HARD_MAX_RISK_PCT):
            # Не блокируем создание — validate_setup отдельно проверит
            # и вернёт risk_above_max. Но < 0 — это явная ошибка.
            if self.risk_pct <= 0:
                raise ValueError("risk_pct must be > 0")
        if self.atr_multiplier <= 0:
            raise ValueError("atr_multiplier must be > 0")


@dataclass(frozen=True)
class RiskResult:
    entry: float
    stop: float
    tp1: float
    tp2: float
    rr: float               # RR по TP2 (финальная цель)
    rr_tp1: float           # RR по TP1 (частичная фиксация)
    size: float             # объём в базовом активе
    risk_amount: float      # $ риск
    runway_atr: float       # |tp1 - entry| / atr
    valid: bool
    reason: str = ""
    soft_reasons: tuple[str, ...] = ()


# ==================== ПРИМИТИВЫ ====================

def calc_rr(entry: float, stop: float, target: float, direction: Direction) -> float:
    """RR по модулю. При нулевом риске → 0.0."""
    _check_direction(direction)
    risk = abs(entry - stop)
    if risk == 0:
        return 0.0
    return abs(target - entry) / risk


def calc_atr_stop(
    entry: float, atr: float, direction: Direction, multiplier: float = 1.5
) -> float:
    """Стоп от entry на расстоянии ATR × multiplier."""
    _check_direction(direction)
    if atr <= 0:
        raise ValueError("atr must be > 0")
    delta = atr * multiplier
    return entry - delta if direction == "long" else entry + delta


def apply_structure_buffer(
    structure_stop: float, atr: float, direction: Direction,
    multiplier: float = 1.5,
) -> float:
    """
    «Технический стоп + ATR×1.5» — буфер складывается в сторону риска.
    long:  structure_stop - atr*mult
    short: structure_stop + atr*mult
    """
    _check_direction(direction)
    if atr <= 0:
        raise ValueError("atr must be > 0")
    delta = atr * multiplier
    return structure_stop - delta if direction == "long" else structure_stop + delta


def calc_position_size(
    equity: float,
    risk_pct: float,
    entry: float,
    stop: float,
    direction: Direction,
    lot_step: float = 0.0,
    min_qty: float = 0.0,
    max_qty: float = float("inf"),
) -> tuple[float, float]:
    """
    Возвращает (size, risk_amount).
    size округляется вниз по lot_step, обрезается по min_qty/max_qty.
    При size < min_qty → (0.0, 0.0).
    """
    _check_direction(direction)
    risk_per_unit = abs(entry - stop)
    if risk_per_unit == 0:
        raise ValueError("zero risk per unit")

    risk_amount = equity * risk_pct
    size = risk_amount / risk_per_unit

    if lot_step > 0:
        size = math.floor(size / lot_step) * lot_step
        # защита от float-мусора
        size = round(size, 12)

    if size > max_qty:
        size = max_qty
        risk_amount = size * risk_per_unit
    if size < min_qty or size <= 0:
        return 0.0, 0.0

    return size, risk_amount


# ==================== HARD FILTERS ====================

def validate_setup(
    entry: float,
    stop: float,
    tp1: float,
    tp2: float,
    direction: Direction,
    cfg: RiskConfig,
    atr: float,
    trend_4h: Direction | None = None,
) -> RiskResult:
    """
    Полная проверка сетапа. Возвращает RiskResult с valid и reason
    (первое нарушение) + soft_reasons (RR в серой зоне и т.п.).
    """
    _check_direction(direction)

    rr_tp2 = calc_rr(entry, stop, tp2, direction)
    rr_tp1 = calc_rr(entry, stop, tp1, direction)
    runway_atr = abs(tp1 - entry) / atr if atr > 0 else 0.0

    size, risk_amount = calc_position_size(
        cfg.equity, cfg.risk_pct, entry, stop, direction,
        cfg.lot_step, cfg.min_qty, cfg.max_qty,
    )

    soft: list[str] = []
    if SOFT_MIN_RR <= rr_tp2 < cfg.min_rr:
        soft.append("rr_in_grey_zone")

    # --- hard filters по порядку приоритета ---
    reason = ""

    if cfg.risk_pct > HARD_MAX_RISK_PCT:
        reason = "risk_above_max"
    elif trend_4h is not None and trend_4h != direction:
        reason = "trend_conflict"
    elif rr_tp2 < cfg.min_rr:
        reason = "rr_below_min"
    elif atr <= 0:
        reason = "atr_invalid"
    elif runway_atr < cfg.min_runway_atr:
        reason = "insufficient_runway"
    elif size == 0.0:
        reason = "size_below_min"

    return RiskResult(
        entry=entry, stop=stop, tp1=tp1, tp2=tp2,
        rr=rr_tp2, rr_tp1=rr_tp1,
        size=size, risk_amount=risk_amount, runway_atr=runway_atr,
        valid=(reason == ""),
        reason=reason,
        soft_reasons=tuple(soft),
    )


# ==================== СБОРНАЯ ФУНКЦИЯ ====================

def build_setup(
    entry: float,
    structure_stop: float | None,
    atr: float,
    direction: Direction,
    tp1: float,
    tp2: float,
    cfg: RiskConfig,
    trend_4h: Direction | None = None,
) -> RiskResult:
    """
    Основной вход для analyzer.py.

    Стоп:
      - structure_stop задан → structure_stop ± ATR×mult (в сторону риска)
      - structure_stop = None → entry ± ATR×mult
    Дальше — validate_setup.
    """
    _check_direction(direction)
    if atr <= 0:
        # Раньше validate_setup уже вернёт atr_invalid, но здесь
        # calc_atr_stop бросит ValueError — гасим вручную.
        return RiskResult(
            entry=entry, stop=0.0, tp1=tp1, tp2=tp2,
            rr=0.0, rr_tp1=0.0, size=0.0, risk_amount=0.0,
            runway_atr=0.0, valid=False, reason="atr_invalid",
        )

    if structure_stop is None:
        stop = calc_atr_stop(entry, atr, direction, cfg.atr_multiplier)
    else:
        stop = apply_structure_buffer(
            structure_stop, atr, direction, cfg.atr_multiplier
        )

    return validate_setup(
        entry=entry, stop=stop, tp1=tp1, tp2=tp2,
        direction=direction, cfg=cfg, atr=atr, trend_4h=trend_4h,
    )

    # ---------- маппинг reason → русский (используется в Telegram) ----------

REASON_RU: dict[str, str] = {
    "risk_above_max":      "Риск на сделку > 1%",
    "trend_conflict":      "4H-тренд против направления входа",
    "rr_below_min":        "RR < 1:3",              # ← оставить как есть
    "atr_invalid":         "ATR недоступен/невалиден",
    "insufficient_runway": "Запас хода до TP1 < 2×ATR",  # ← оставить
    "size_below_min":      "Размер позиции < минимального лота",
    "":                    "",
}

SOFT_REASON_RU: dict[str, str] = {
    "rr_in_grey_zone": "RR в серой зоне (2.5–3.0)",
}


def format_reason_ru(reason: str) -> str:
    return REASON_RU.get(reason, reason)

# ==================== ФАБРИКА ИЗ CONFIG ====================   ← блок 2 (тот самый)

def risk_config_from_env(
    equity: float,
    risk_pct: float = 0.01,
    lot_step: float = 0.0,
    min_qty: float = 0.0,
    max_qty: float = float("inf"),
    atr_multiplier: float = 1.5,
) -> RiskConfig:
    """
    Тонкая обёртка: вызывающий сам читает Config и передаёт сюда.
    risk.py не импортирует src.utils.config — остаётся чистым.
    """
    return RiskConfig(
        equity=equity,
        risk_pct=risk_pct,
        lot_step=lot_step,
        min_qty=min_qty,
        max_qty=max_qty,
        atr_multiplier=atr_multiplier,
    )