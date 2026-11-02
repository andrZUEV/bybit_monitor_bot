#!/usr/bin/env python
"""
Разбор `data/alert_history.jsonl`: что бот видел, что отправил и что отбросил.

Зачем: решения по порогам (`deliver_min`, `weak_min`, `alert_min_score`)
нужно принимать по данным, а не на глаз.

Запуск:
    .venv\\Scripts\\python.exe scripts/analyze_history.py
    .venv\\Scripts\\python.exe scripts/analyze_history.py --last 500
    .venv\\Scripts\\python.exe scripts/analyze_history.py --symbol BTCUSDT
    .venv\\Scripts\\python.exe scripts/analyze_history.py --json

Скрипт ничего не меняет и не требует сети: только чтение файла истории.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "alert_history.jsonl"

VERDICTS = ("💪 Strong", "⚠️ Weak", "🟡 Marginal", "❌ None")

# Эмодзи в консоли Windows (cp1251) не печатаются — в отчёте используем
# короткие ASCII-метки, эмодзи остаются только в самих данных.
VERDICT_LABELS = {
    "💪 Strong": "strong",
    "⚠️ Weak": "weak",
    "🟡 Marginal": "marginal",
    "❌ None": "none",
}


def _verdict_label(raw: str) -> str:
    return VERDICT_LABELS.get(raw, raw)


def _emit(text: str = "") -> None:
    """Печать с защитой от cp1251: непереводимые символы заменяются, не падаем."""
    enc = sys.stdout.encoding or "utf-8"
    if isinstance(text, str):
        try:
            text.encode(enc)
        except (UnicodeEncodeError, LookupError):
            text = text.encode(enc, "replace").decode(enc, "replace")
    print(text)


# ==================== чтение ====================

def load_records(path: Path, last: int | None = None) -> tuple[list[dict], int]:
    """Читает JSONL. Возвращает (записи, число битых строк).

    Битые строки не роняют разбор: файл истории пишется из живого процесса,
    последняя строка может быть обрезана.
    """
    records: list[dict] = []
    broken = 0
    if not path.exists():
        return records, broken

    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                broken += 1
                continue
            if isinstance(obj, dict):
                records.append(obj)
            else:
                broken += 1

    if last is not None and last > 0:
        records = records[-last:]
    return records, broken


# ==================== статистика ====================

def _pct(part: int, total: int) -> str:
    if total <= 0:
        return "—"
    return f"{part / total * 100:.0f}%"


def _score_buckets(scores: list[float]) -> list[tuple[str, int]]:
    """Гистограмма score с шагом 0.5 от -5.0 до 8.0 (всё крайнее — в края)."""
    buckets = [(f"{x / 2:.1f}", 0) for x in range(-10, 16)]
    counts: Counter[str] = Counter()
    for s in scores:
        idx = min(max(int(round(s * 2)), -10), 15)
        counts[f"{idx / 2:.1f}"] += 1
    return [(label, counts.get(label, 0)) for label, _ in buckets]


def summarize(records: list[dict]) -> dict:
    total = len(records)
    scores = [
        float(r["final_score"]) for r in records if isinstance(r.get("final_score"), (int, float))
    ]
    sent = [r for r in records if r.get("was_sent")]
    strategy = [r for r in records if r.get("strategy_pass")]

    verdict_counts = Counter(str(r.get("verdict")) for r in records)
    skip_counts = Counter(
        str(r.get("skip_reason")) for r in records if not r.get("was_sent")
    )
    hard_counts = Counter(
        str(r.get("hard_filter")) for r in records if r.get("hard_filter")
    )
    pattern_counts = Counter(
        str(r.get("detected_pattern")) for r in records if r.get("detected_pattern")
    )
    engulfing = [r for r in records if str(r.get("detected_pattern", "")).endswith("_engulfing")]
    strict = [r for r in engulfing if r.get("pattern_strict") is True]
    vol = [
        float(r["volume_ratio"])
        for r in records
        if isinstance(r.get("volume_ratio"), (int, float))
    ]
    symbols = Counter(str(r.get("symbol")) for r in records if r.get("symbol"))

    timestamps = [
        float(r["timestamp"]) for r in records if isinstance(r.get("timestamp"), (int, float))
    ]

    return {
        "total": total,
        "sent": len(sent),
        "skipped": total - len(sent),
        "strategy_pass": len(strategy),
        "score_avg": round(statistics.fmean(scores), 2) if scores else None,
        "score_median": round(statistics.median(scores), 2) if scores else None,
        "score_min": min(scores) if scores else None,
        "score_max": max(scores) if scores else None,
        "score_buckets": _score_buckets(scores),
        "verdicts": {v: verdict_counts.get(v, 0) for v in VERDICTS},
        "verdicts_other": {
            k: v for k, v in verdict_counts.items() if k not in VERDICTS
        },
        "skip_reasons": dict(skip_counts.most_common()),
        "hard_filters": dict(hard_counts.most_common()),
        "patterns": dict(pattern_counts.most_common()),
        "engulfing_total": len(engulfing),
        "engulfing_strict": len(strict),
        "vol_avg": round(statistics.fmean(vol), 2) if vol else None,
        "vol_median": round(statistics.median(vol), 2) if vol else None,
        "vol_good_share": (
            round(sum(1 for v in vol if v >= 1.8) / len(vol) * 100, 1) if vol else None
        ),
        "symbols_top": dict(symbols.most_common(10)),
        "first_ts": min(timestamps) if timestamps else None,
        "last_ts": max(timestamps) if timestamps else None,
    }


# ==================== вывод ====================

def _fmt_ts(ts: float | None) -> str:
    if ts is None:
        return "—"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def format_report(stats: dict, broken: int, path: Path) -> str:
    total = stats["total"]
    lines: list[str] = []
    lines.append(f"Файл: {path}")
    lines.append(f"Записей: {total} (битых строк: {broken})")
    lines.append(f"Период: {_fmt_ts(stats['first_ts'])} … {_fmt_ts(stats['last_ts'])}")
    lines.append("")

    lines.append("== Отправка ==")
    lines.append(
        f"отправлено {stats['sent']} ({_pct(stats['sent'], total)}), "
        f"отброшено {stats['skipped']} ({_pct(stats['skipped'], total)}), "
        f"strategy_pass {stats['strategy_pass']} ({_pct(stats['strategy_pass'], total)})"
    )
    lines.append("")

    lines.append("== Вердикты ==")
    for name, count in stats["verdicts"].items():
        lines.append(f"{_verdict_label(name) or '—':<12} {count:>6}  {_pct(count, total)}")
    for name, count in stats["verdicts_other"].items():
        label = _verdict_label(name) or "—"
        lines.append(f"{label:<12} {count:>6}  {_pct(count, total)}  (нестандартный)")
    lines.append("")

    if stats["score_avg"] is not None:
        lines.append("== Score ==")
        lines.append(
            f"min {stats['score_min']}, median {stats['score_median']}, "
            f"avg {stats['score_avg']}, max {stats['score_max']}"
        )
        peak = max((c for _, c in stats["score_buckets"]), default=0) or 1
        for label, count in stats["score_buckets"]:
            if count == 0:
                continue
            bar = "#" * max(1, round(count / peak * 30))
            lines.append(f"{label:>5} {count:>5} {bar}")
        lines.append("")

    if stats["skip_reasons"]:
        lines.append("== Почему не отправлено ==")
        for name, count in stats["skip_reasons"].items():
            lines.append(f"{name:<22} {count:>6}")
        lines.append("")

    if stats["hard_filters"]:
        lines.append("== Hard filters ==")
        for name, count in stats["hard_filters"].items():
            lines.append(f"{name:<22} {count:>6}")
        lines.append("")

    if stats["patterns"]:
        lines.append("== Паттерны ==")
        for name, count in stats["patterns"].items():
            lines.append(f"{name:<22} {count:>6}")
        if stats["engulfing_total"]:
            share = stats["engulfing_strict"] / stats["engulfing_total"] * 100
            lines.append(
                f"поглощения: {stats['engulfing_total']}, "
                f"строгих {stats['engulfing_strict']} ({share:.0f}%)"
            )
        lines.append("")

    if stats["vol_avg"] is not None:
        lines.append("== Объём (vol_ratio) ==")
        lines.append(
            f"avg {stats['vol_avg']}, median {stats['vol_median']}, "
            f"доля ≥ 1.8×: {stats['vol_good_share']}%"
        )
        lines.append("")

    if stats["symbols_top"]:
        lines.append("== Топ символов ==")
        for name, count in stats["symbols_top"].items():
            lines.append(f"{name:<15} {count:>6}")
        lines.append("")

    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Распределение score/verdict/skip_reason по alert_history.jsonl",
    )
    parser.add_argument(
        "path", nargs="?", default=str(DEFAULT_PATH),
        help=f"путь к JSONL (по умолчанию {DEFAULT_PATH})",
    )
    parser.add_argument("--last", type=int, default=None, help="только последние N записей")
    parser.add_argument("--symbol", default=None, help="фильтр по символу, например BTCUSDT")
    parser.add_argument("--json", action="store_true", help="вывести статистику как JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # В консоли Windows (cp1251) эмодзи не печатаются. Если вывод
    # перенаправлен в файл/пайп — переключаемся на UTF-8; в живой консоли
    # оставляем системную кодировку и правим символы в _emit.
    if not sys.stdout.isatty():
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    path = Path(args.path)
    records, broken = load_records(path, last=args.last)

    if args.symbol:
        wanted = args.symbol.upper()
        records = [r for r in records if str(r.get("symbol", "")).upper() == wanted]

    if not records:
        print(f"Нет данных: {path} пуст или не найден.", file=sys.stderr)
        return 1

    stats = summarize(records)
    if args.json:
        _emit(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        _emit(format_report(stats, broken, path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
