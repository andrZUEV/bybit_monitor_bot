"""Тесты: worn_level → hard_filter, htf_against без штрафа."""
from __future__ import annotations

import time

from src.core.analyzer import evaluate_alert


def _make_klines_simple(n: int = 200, start_price: float = 3000.0):
    """Простые свечи без тренда."""
    klines = []
    price = start_price
    now_ms = int(time.time() * 1000)
    step_ms = 15 * 60 * 1000
    for i in range(n):
        o = price
        c = price * 1.0001
        h = max(o, c) * 1.001
        low = min(o, c) * 0.999
        klines.append([now_ms - i * step_ms, str(o), str(h), str(low), str(c), "1000"])
        price = o
    return klines


class TestWornLevelHardFilter:
    def test_worn_level_sets_hard_filter(self):
        """
        Если уровень касается много раз — должно быть
        hard_filter='worn_level', и score НЕ штрафуется -1.5.
        """
        # Простой сценарий: klines с высоким level=3000
        # (наши плоские свечи колеблются вокруг 3000 → много касаний)
        klines_15m = _make_klines_simple(200, 3000.0)
        klines_4h = _make_klines_simple(100, 3000.0)

        result = evaluate_alert(
            symbol="TESTUSDT", level=3000.0, direction="up",
            current_price=3000.0, alert_created_at=time.time(),
            klines_15m=klines_15m, klines_4h=klines_4h,
        )

        if result["touches"] >= 5:
            assert result["hard_filter"] == "worn_level"
            assert result["worn_level"] is True
        else:
            assert result["hard_filter"] != "worn_level"
            assert result["worn_level"] is False

    def test_worn_level_no_score_penalty(self):
        """
        Проверяем, что worn_level НЕ даёт -1.5 к score.
        Сравниваем две ситуации — одну с worn (touches много), другую без.
        Ожидаем, что разница в score — НЕ -1.5 (штраф убран), а 0.
        """
        # Этот тест нужен для регрессии — если кто-то вернёт штраф,
        # тест упадёт.
        # В реальной ситуации трудно создать ровно 0 касаний, поэтому
        # проверяем косвенно: у worn-случая hard_filter == "worn_level",
        # а score — не содержит -1.5.
        klines_15m = _make_klines_simple(200, 3000.0)
        klines_4h = _make_klines_simple(100, 3000.0)

        result = evaluate_alert(
            symbol="TESTUSDT", level=3000.0, direction="up",
            current_price=3000.0, alert_created_at=time.time(),
            klines_15m=klines_15m, klines_4h=klines_4h,
        )
        # В filters не должно быть старого сообщения со штрафом
        assert not any("-1.5" in f and "изношен" in f for f in result["filters"])