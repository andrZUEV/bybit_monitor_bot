"""Тесты RuntimeSettings: load/save round-trip, дефолты, битые данные."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core.settings import RuntimeSettings


class TestDefaults:
    def test_default_min_score_is_neg_inf(self):
        s = RuntimeSettings()
        assert s.alert_min_score == float("-inf")
        assert s.is_all() is True

    def test_describe_all(self):
        assert RuntimeSettings().describe() == "ВСЕ"

    def test_describe_number(self):
        assert RuntimeSettings(alert_min_score=-5.0).describe() == "≥ -5"
        assert RuntimeSettings(alert_min_score=0.0).describe() == "≥ 0"
        assert RuntimeSettings(alert_min_score=3.0).describe() == "≥ 3"


class TestSaveLoad:
    def test_round_trip(self, tmp_path: Path):
        p = tmp_path / "settings.json"
        s = RuntimeSettings(alert_min_score=-2.5)
        s._path = p
        s.save()

        loaded = RuntimeSettings.load(p)
        assert loaded.alert_min_score == -2.5

    def test_round_trip_all(self, tmp_path: Path):
        p = tmp_path / "settings.json"
        s = RuntimeSettings(alert_min_score=float("-inf"))
        s._path = p
        s.save()

        loaded = RuntimeSettings.load(p)
        assert loaded.alert_min_score == float("-inf")

    def test_file_written_as_json(self, tmp_path: Path):
        p = tmp_path / "settings.json"
        s = RuntimeSettings(alert_min_score=1.5)
        s._path = p
        s.save()

        data = json.loads(p.read_text(encoding="utf-8"))
        assert data == {
            "alert_min_score": 1.5,
            "send_invalid_alerts": True,
            "alert_history_depth": 10000,
        }

    def test_save_without_path_is_noop(self, tmp_path: Path):
        s = RuntimeSettings(alert_min_score=2.0)
        # Не задан _path — не должно падать
        s.save()


class TestLoadOrDefault:
    def test_missing_file_creates_default(self, tmp_path: Path):
        p = tmp_path / "missing.json"
        s = RuntimeSettings.load_or_default(p)

        assert s.alert_min_score == float("-inf")
        assert p.exists()  # файл создан

        # JSON не умеет -inf — Python сохранит как -Infinity (не по стандарту,
        # но сам же читает обратно). Проверим через повторный load.
        s2 = RuntimeSettings.load_or_default(p)
        assert s2.alert_min_score == float("-inf")

        # Файл после save не пустой
        assert p.read_text(encoding="utf-8").strip() != ""

    def test_broken_json_falls_back_to_default(self, tmp_path: Path):
        p = tmp_path / "broken.json"
        p.write_text("not json {{{", encoding="utf-8")

        s = RuntimeSettings.load_or_default(p)
        assert s.alert_min_score == float("-inf")


class TestParseScore:
    @pytest.mark.parametrize("raw,expected", [
        (0, 0.0),
        (-5, -5.0),
        (3.5, 3.5),
        ("-5", -5.0),
        ("0", 0.0),
        ("-inf", float("-inf")),
        ("-Infinity", float("-inf")),
        ("-infinity", float("-inf")),
        ("все", float("-inf")),
        ("all", float("-inf")),
        ("inf", float("inf")),
        ("+inf", float("inf")),
    ])
    def test_parse(self, raw, expected):
        assert RuntimeSettings._parse_score(raw, default=0.0) == expected

    @pytest.mark.parametrize("bad", [None, "abc", "1.2.3", []])
    def test_parse_bad_returns_default(self, bad):
        assert RuntimeSettings._parse_score(bad, default=-7.0) == -7.0

    def test_load_reads_string_min_score(self, tmp_path: Path):
        """На случай, если руками впишут '-5' строкой."""
        p = tmp_path / "settings.json"
        p.write_text(json.dumps({"alert_min_score": "-5"}), encoding="utf-8")
        s = RuntimeSettings.load(p)
        assert s.alert_min_score == -5.0

    def test_load_reads_inf_string(self, tmp_path: Path):
        p = tmp_path / "settings.json"
        p.write_text(json.dumps({"alert_min_score": "-inf"}), encoding="utf-8")
        s = RuntimeSettings.load(p)
        assert s.alert_min_score == float("-inf")


class TestLockIsSet:
    def test_lock_created_in_post_init(self):
        s = RuntimeSettings()
        assert s._lock is not None

class TestSendInvalidAlerts:
    def test_default_true(self):
        s = RuntimeSettings()
        assert s.send_invalid_alerts is True
        assert s.describe_invalid() == "ВКЛ"

    def test_describe_off(self):
        s = RuntimeSettings(send_invalid_alerts=False)
        assert s.describe_invalid() == "ВЫКЛ"

    def test_round_trip_true(self, tmp_path: Path):
        p = tmp_path / "settings.json"
        s = RuntimeSettings(alert_min_score=0.0, send_invalid_alerts=True)
        s._path = p
        s.save()

        loaded = RuntimeSettings.load(p)
        assert loaded.send_invalid_alerts is True

    def test_round_trip_false(self, tmp_path: Path):
        p = tmp_path / "settings.json"
        s = RuntimeSettings(alert_min_score=0.0, send_invalid_alerts=False)
        s._path = p
        s.save()

        loaded = RuntimeSettings.load(p)
        assert loaded.send_invalid_alerts is False

    def test_load_missing_field_defaults_true(self, tmp_path: Path):
        """Старый файл без send_invalid_alerts — читается как True."""
        p = tmp_path / "settings.json"
        p.write_text(json.dumps({"alert_min_score": -5.0}), encoding="utf-8")

        loaded = RuntimeSettings.load(p)
        assert loaded.send_invalid_alerts is True

    @pytest.mark.parametrize("raw,expected", [
        (True, True),
        (False, False),
        (1, True),
        (0, False),
        ("true", True),
        ("false", False),
        ("1", True),
        ("0", False),
        ("yes", True),
        ("no", False),
        ("on", True),
        ("off", False),
        ("вкл", True),
        ("выкл", False),
    ])
    def test_parse_bool(self, raw, expected):
        assert RuntimeSettings._parse_bool(raw, default=None) is expected

    @pytest.mark.parametrize("bad", [None, "abc", [], "1.5"])
    def test_parse_bool_bad_returns_default(self, bad):
        assert RuntimeSettings._parse_bool(bad, default=True) is True
        assert RuntimeSettings._parse_bool(bad, default=False) is False