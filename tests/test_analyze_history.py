"""Тесты скрипта scripts/analyze_history.py (Этап 9)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import analyze_history as ah  # noqa: E402


def _record(
    score: float = 3.5,
    verdict: str = "⚠️ Weak",
    was_sent: bool = True,
    skip_reason: str | None = None,
    symbol: str = "BTCUSDT",
    hard_filter: str | None = None,
    pattern: str | None = None,
    pattern_strict: bool | None = None,
    volume_ratio: float = 1.0,
    ts: float = 1_762_000_000.0,
    strategy_pass: bool = True,
) -> dict:
    return {
        "timestamp": ts,
        "symbol": symbol,
        "final_score": score,
        "verdict": verdict,
        "was_sent": was_sent,
        "skip_reason": skip_reason,
        "strategy_pass": strategy_pass,
        "hard_filter": hard_filter,
        "detected_pattern": pattern,
        "pattern_strict": pattern_strict,
        "volume_ratio": volume_ratio,
    }


def _write_jsonl(path: Path, records: list[dict]) -> Path:
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


class TestLoadRecords:
    def test_reads_records_and_skips_broken_lines(self, tmp_path):
        path = tmp_path / "history.jsonl"
        lines = [
            json.dumps(_record(score=3.5), ensure_ascii=False),
            "{битый json",
            "",
            json.dumps(_record(score=5.0), ensure_ascii=False),
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        records, broken = ah.load_records(path)

        assert len(records) == 2
        assert broken == 1

    def test_missing_file_gives_empty(self, tmp_path):
        records, broken = ah.load_records(tmp_path / "nope.jsonl")
        assert records == []
        assert broken == 0

    def test_last_keeps_tail(self, tmp_path):
        path = _write_jsonl(
            tmp_path / "history.jsonl",
            [_record(score=float(i)) for i in range(10)],
        )
        records, _ = ah.load_records(path, last=3)
        assert [r["final_score"] for r in records] == [7.0, 8.0, 9.0]


class TestSummarize:
    def test_counts_sent_and_skipped(self):
        records = [
            _record(score=5.5, verdict="💪 Strong"),
            _record(score=3.0, verdict="🟡 Marginal"),
            _record(
                score=-4.0, verdict="❌ None", was_sent=False,
                skip_reason="verdict_none", strategy_pass=False,
            ),
        ]
        stats = ah.summarize(records)

        assert stats["total"] == 3
        assert stats["sent"] == 2
        assert stats["skipped"] == 1
        assert stats["verdicts"]["💪 Strong"] == 1
        assert stats["verdicts"]["🟡 Marginal"] == 1
        assert stats["verdicts"]["❌ None"] == 1
        assert stats["skip_reasons"] == {"verdict_none": 1}

    def test_hard_filters_and_patterns(self):
        records = [
            _record(hard_filter="trend_conflict", was_sent=False, strategy_pass=False),
            _record(hard_filter="worn_level", was_sent=False, strategy_pass=False),
            _record(hard_filter="trend_conflict", was_sent=False, strategy_pass=False),
            _record(
                pattern="bullish_engulfing", pattern_strict=True,
            ),
            _record(
                pattern="bullish_engulfing", pattern_strict=False,
            ),
            _record(pattern="bullish_pinbar"),
        ]
        stats = ah.summarize(records)

        assert stats["hard_filters"]["trend_conflict"] == 2
        assert stats["hard_filters"]["worn_level"] == 1
        assert stats["engulfing_total"] == 2
        assert stats["engulfing_strict"] == 1
        assert stats["patterns"]["bullish_pinbar"] == 1

    def test_volume_stats(self):
        records = [
            _record(volume_ratio=1.0),
            _record(volume_ratio=2.0),
            _record(volume_ratio=3.0),
        ]
        stats = ah.summarize(records)

        assert stats["vol_avg"] == 2.0
        assert stats["vol_median"] == 2.0
        # 2 из 3 ≥ 1.8
        assert stats["vol_good_share"] == pytest.approx(66.7, abs=0.1)

    def test_empty_records_do_not_crash(self):
        stats = ah.summarize([])
        assert stats["total"] == 0
        assert stats["score_avg"] is None
        assert stats["vol_avg"] is None


class TestFormatReport:
    def test_report_contains_sections(self):
        records = [
            _record(score=5.5, verdict="💪 Strong"),
            _record(
                score=-4.0, verdict="❌ None", was_sent=False,
                skip_reason="verdict_none", strategy_pass=False,
            ),
        ]
        stats = ah.summarize(records)
        report = ah.format_report(stats, broken=1, path=Path("data/alert_history.jsonl"))

        assert "Записей: 2" in report
        assert "битых строк: 1" in report
        assert "== Отправка ==" in report
        assert "== Вердикты ==" in report
        # Эмодзи в отчёте заменены на ASCII-метки (cp1251-совместимость).
        assert "strong" in report
        assert "none" in report
        assert "💪" not in report

    def test_marginal_verdict_has_label(self):
        assert ah._verdict_label("🟡 Marginal") == "marginal"
        assert ah._verdict_label("❓ Странный") == "❓ Странный"


class TestMain:
    def test_json_output(self, tmp_path, capsys):
        path = _write_jsonl(tmp_path / "history.jsonl", [_record(score=3.5)])
        rc = ah.main([str(path), "--json"])

        assert rc == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["total"] == 1

    def test_symbol_filter(self, tmp_path, capsys):
        path = _write_jsonl(
            tmp_path / "history.jsonl",
            [_record(symbol="BTCUSDT"), _record(symbol="ETHUSDT")],
        )
        rc = ah.main([str(path), "--symbol", "ethusdt", "--json"])

        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["total"] == 1
        assert data["symbols_top"] == {"ETHUSDT": 1}

    def test_missing_file_returns_error(self, tmp_path):
        rc = ah.main([str(tmp_path / "nope.jsonl")])
        assert rc == 1
