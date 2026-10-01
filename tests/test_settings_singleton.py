"""Тесты синглтон-обёртки RuntimeSettings: init/get/set."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core.settings import (
    RuntimeSettings,
    get_settings,
    init_settings,
    set_settings,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Каждый тест начинается и заканчивается с чистого синглтона."""
    set_settings(None)
    yield
    set_settings(None)


class TestInitSettings:
    def test_init_creates_default_when_missing(self, tmp_path: Path):
        p = tmp_path / "settings.json"
        s = init_settings(p)

        assert s.alert_min_score == float("-inf")
        assert p.exists()  # load_or_default создал файл
        assert get_settings() is s  # тот же объект

    def test_init_reads_existing(self, tmp_path: Path):
        p = tmp_path / "settings.json"
        p.write_text(json.dumps({"alert_min_score": -3.0}), encoding="utf-8")

        s = init_settings(p)
        assert s.alert_min_score == -3.0
        assert get_settings() is s

    def test_init_twice_reloads(self, tmp_path: Path):
        p = tmp_path / "settings.json"
        init_settings(p)
        # Изменяем файл извне
        p.write_text(json.dumps({"alert_min_score": 2.5}), encoding="utf-8")
        s2 = init_settings(p)
        assert s2.alert_min_score == 2.5


class TestGetSettings:
    def test_get_without_init_returns_default(self):
        s = get_settings()
        assert s.alert_min_score == float("-inf")

    def test_get_returns_same_instance(self):
        a = get_settings()
        b = get_settings()
        assert a is b

    def test_get_after_set(self):
        custom = RuntimeSettings(alert_min_score=1.0)
        set_settings(custom)
        assert get_settings() is custom


class TestSetSettings:
    def test_set_none_resets(self):
        custom = RuntimeSettings(alert_min_score=1.0)
        set_settings(custom)
        set_settings(None)

        # Следующий get создаст дефолт
        s = get_settings()
        assert s is not custom
        assert s.alert_min_score == float("-inf")

    def test_set_replaces(self):
        a = RuntimeSettings(alert_min_score=1.0)
        b = RuntimeSettings(alert_min_score=2.0)
        set_settings(a)
        set_settings(b)
        assert get_settings() is b


class TestMutationPersists:
    def test_mutate_and_save(self, tmp_path: Path):
        p = tmp_path / "settings.json"
        init_settings(p)

        s = get_settings()
        s.alert_min_score = -2.0
        s.save()

        # Перечитываем с диска
        loaded = RuntimeSettings.load(p)
        assert loaded.alert_min_score == -2.0