"""Тесты виртуального трейдера (Этап 11): PnL, резолв, хранилище, статистика."""
from __future__ import annotations

import json
import time

import pytest

from src.core.virtual_trader import (
    VirtualTrade,
    VirtualTrader,
    calc_pnl,
    calc_rr,
    format_stats_message,
    get_virtual_trader,
    init_virtual_trader,
    resolve_trade,
    set_virtual_trader,
    split_portions,
)


def _long_trade(**kwargs) -> VirtualTrade:
    base = dict(
        id="t1", symbol="BTCUSDT", direction="up",
        entry=100.0, sl=95.0, tps=[110.0, 120.0], portions=[0.5, 0.5],
        risk_usdt=10.0, leverage=10,
    )
    base.update(kwargs)
    return VirtualTrade(**base)


def _candle(ts: int, high: float, low: float, close: float | None = None) -> dict:
    return {
        "ts": ts, "open": (high + low) / 2, "high": high, "low": low,
        "close": close if close is not None else (high + low) / 2,
        "volume": 1.0,
    }


class TestSplitPortions:
    def test_single(self):
        assert split_portions(1) == [1.0]

    def test_two(self):
        assert split_portions(2) == [0.5, 0.5]

    def test_three_sums_to_one(self):
        portions = split_portions(3)
        assert len(portions) == 3
        assert abs(sum(portions) - 1.0) < 1e-9
        assert portions[-1] > portions[0]

    def test_zero_and_negative(self):
        assert split_portions(0) == [1.0]
        assert split_portions(-3) == [1.0]


class TestCalcPnl:
    def test_long_profit(self):
        pnl = calc_pnl(
            direction="up", entry=100.0, exit_price=110.0, sl=95.0,
            risk_usdt=10.0, portion=0.5,
        )
        # (110-100)/(100-95)=2 → 2 * 10 * 0.5 = 10
        assert pnl == 10.0

    def test_long_loss_at_sl(self):
        pnl = calc_pnl(
            direction="up", entry=100.0, exit_price=95.0, sl=95.0,
            risk_usdt=10.0, portion=1.0,
        )
        assert pnl == -10.0

    def test_short_profit(self):
        pnl = calc_pnl(
            direction="down", entry=100.0, exit_price=90.0, sl=105.0,
            risk_usdt=10.0, portion=1.0,
        )
        # (100-90)/(105-100)=2 → 20
        assert pnl == 20.0

    def test_zero_portion(self):
        assert calc_pnl(
            direction="up", entry=100.0, exit_price=110.0, sl=95.0,
            risk_usdt=10.0, portion=0.0,
        ) == 0.0

    def test_invalid_denominator_returns_zero(self):
        assert calc_pnl(
            direction="up", entry=100.0, exit_price=110.0, sl=100.0,
            risk_usdt=10.0, portion=1.0,
        ) == 0.0


class TestCalcRr:
    def test_long(self):
        assert calc_rr(direction="up", entry=100.0, tp=110.0, sl=95.0) == 2.0

    def test_short(self):
        assert calc_rr(direction="down", entry=100.0, tp=90.0, sl=105.0) == 2.0

    def test_zero_risk(self):
        assert calc_rr(direction="up", entry=100.0, tp=110.0, sl=100.0) == 0.0


class TestResolveTrade:
    def test_pending_opens_on_entry_touch(self):
        trade = _long_trade()
        candles = [_candle(1_000_000, high=101.0, low=99.0)]

        changed = resolve_trade(trade, candles, now=1000.0)

        assert changed is False  # вход есть, но закрытия ещё нет
        assert trade.status == "open"

    def test_pending_closes_on_sl_before_entry(self):
        trade = _long_trade()
        candles = [_candle(1_000_000, high=99.0, low=94.0)]

        changed = resolve_trade(trade, candles, now=1000.0)

        assert changed is True
        assert trade.status == "closed"
        assert trade.close_reason == "sl"

    def test_open_partial_tp_then_full_close(self):
        trade = _long_trade()
        resolve_trade(trade, [_candle(1_000_000, high=101.0, low=99.0)], now=1000.0)
        entry_ts = trade.entry_ts

        # Первый TP сработал
        changed = resolve_trade(
            trade,
            [_candle(2_000_000, high=111.0, low=100.0)],
            now=2000.0,
        )
        assert changed is False
        assert len(trade.filled_tps) == 1
        assert trade.realized_pnl == 10.0
        assert trade.entry_ts == entry_ts

        # Второй TP закрывает сделку
        changed = resolve_trade(
            trade,
            [_candle(3_000_000, high=121.0, low=100.0)],
            now=3000.0,
        )
        assert changed is True
        assert trade.status == "closed"
        assert trade.close_reason == "tp"
        assert trade.close_price == 120.0
        assert len(trade.filled_tps) == 2

    def test_open_sl_closes_remaining_portion(self):
        trade = _long_trade()
        resolve_trade(trade, [_candle(1_000_000, high=101.0, low=99.0)], now=1000.0)
        resolve_trade(trade, [_candle(2_000_000, high=111.0, low=100.0)], now=2000.0)

        changed = resolve_trade(
            trade,
            [_candle(3_000_000, high=105.0, low=94.0)],
            now=3000.0,
        )

        assert changed is True
        assert trade.close_reason == "sl"
        # вторая половина ушла в убыток: -10 * 0.5
        assert len(trade.filled_tps) == 2
        assert trade.realized_pnl == pytest.approx(5.0)

    def test_candles_before_entry_are_skipped(self):
        trade = _long_trade()
        resolve_trade(trade, [_candle(1_000_000, high=101.0, low=99.0)], now=1000.0)
        trade.entry_ts = 10_000.0  # порог 10^7 мс: свеча 2*10^6 ещё до входа

        changed = resolve_trade(
            trade,
            [_candle(2_000_000, high=130.0, low=100.0)],
            now=2000.0,
        )

        assert changed is False
        assert trade.filled_tps == []

    def test_closed_trade_is_noop(self):
        trade = _long_trade(status="closed")

        assert resolve_trade(trade, [_candle(1_000, 200.0, 50.0)]) is False

    def test_empty_candles_is_noop(self):
        trade = _long_trade()
        assert resolve_trade(trade, []) is False
        assert trade.status == "pending_entry"


class TestStorage:
    def test_create_and_reload(self, tmp_path):
        path = tmp_path / "vt.jsonl"
        trader = VirtualTrader(path)
        trade = trader.create(
            symbol="btcusdt", direction="up", entry=100.0, sl=95.0,
            tps=[110.0, 120.0],
        )

        assert trade.symbol == "BTCUSDT"
        assert trade.portions == [0.5, 0.5]
        assert path.exists()

        reloaded = VirtualTrader(path)
        restored = reloaded.get(trade.id)
        assert restored is not None
        assert restored.entry == 100.0
        assert restored.portions == [0.5, 0.5]

    def test_create_validates(self, tmp_path):
        trader = VirtualTrader(tmp_path / "vt.jsonl")

        with pytest.raises(ValueError):
            trader.create(symbol="BTCUSDT", direction="side", entry=1, sl=2, tps=[3])
        with pytest.raises(ValueError):
            trader.create(symbol="BTCUSDT", direction="up", entry=100, sl=95, tps=[])
        with pytest.raises(ValueError):
            trader.create(symbol="BTCUSDT", direction="up", entry=0, sl=95, tps=[110])
        with pytest.raises(ValueError):
            trader.create(symbol="BTCUSDT", direction="up", entry=100, sl=105, tps=[110])
        with pytest.raises(ValueError):
            trader.create(symbol="BTCUSDT", direction="up", entry=100, sl=95, tps=[90])
        with pytest.raises(ValueError):
            trader.create(symbol="BTCUSDT", direction="down", entry=100, sl=95, tps=[90])
        with pytest.raises(ValueError):
            trader.create(symbol="BTCUSDT", direction="down", entry=100, sl=105, tps=[110])

    def test_counts_and_active(self, tmp_path):
        trader = VirtualTrader(tmp_path / "vt.jsonl")
        a = trader.create(symbol="BTCUSDT", direction="up", entry=100, sl=95, tps=[110])
        b = trader.create(symbol="ETHUSDT", direction="down", entry=100, sl=105, tps=[90])
        trader.cancel(a.id)

        assert trader.count() == 2
        assert trader.count_by_status("cancelled") == 1
        assert [t.id for t in trader.active_trades()] == [b.id]

    def test_cancel(self, tmp_path):
        trader = VirtualTrader(tmp_path / "vt.jsonl")
        trade = trader.create(symbol="BTCUSDT", direction="up", entry=100, sl=95, tps=[110])

        assert trader.cancel(trade.id) is True
        assert trader.cancel(trade.id) is False
        assert trader.cancel("нет-такого") is False

    def test_update_and_clear(self, tmp_path):
        path = tmp_path / "vt.jsonl"
        trader = VirtualTrader(path)
        trade = trader.create(symbol="BTCUSDT", direction="up", entry=100, sl=95, tps=[110])
        trade.notes = "правка"
        trader.update(trade)

        assert VirtualTrader(path).get(trade.id).notes == "правка"

        trader.clear()
        assert not path.exists()
        assert trader.count() == 0

    def test_clear_empty_does_not_crash(self, tmp_path):
        VirtualTrader(tmp_path / "vt.jsonl").clear()

    def test_broken_lines_are_skipped(self, tmp_path):
        path = tmp_path / "vt.jsonl"
        good = VirtualTrade(
            id="ok", symbol="BTCUSDT", direction="up", entry=100.0, sl=95.0,
            tps=[110.0], portions=[1.0], risk_usdt=1.0, leverage=10,
        )
        path.write_text(
            "{битый\n" + json.dumps(good.to_dict(), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        trader = VirtualTrader(path)

        assert trader.count() == 1
        assert trader.get("ok") is not None

    def test_from_dict_defaults(self):
        trade = VirtualTrade.from_dict({
            "id": 5, "symbol": "BTCUSDT", "direction": "up",
            "entry": "100", "sl": "95",
        })

        assert trade.id == "5"
        assert trade.tps == []
        assert trade.status == "pending_entry"
        assert trade.realized_pnl == 0.0


class TestResolveAll:
    def test_resolve_all_updates_storage(self, tmp_path):
        path = tmp_path / "vt.jsonl"
        trader = VirtualTrader(path)
        trade = trader.create(symbol="BTCUSDT", direction="up", entry=100, sl=95, tps=[110])
        now = time.time()

        def klines_provider(symbol, category):
            return [
                [str(int((trade.created_at + 60) * 1000)), "100", "101", "99", "100", "1"],
                [str(int((trade.created_at + 120) * 1000)), "100", "111", "100", "110", "1"],
            ]

        resolved = trader.resolve_all(
            klines_provider=klines_provider,
            category_provider=lambda symbol: "linear",
            now=now,
        )

        assert resolved == 1
        assert trader.get(trade.id).status == "closed"
        assert trader.get(trade.id).close_reason == "tp"

    def test_resolve_all_without_provider_data(self, tmp_path):
        trader = VirtualTrader(tmp_path / "vt.jsonl")
        trader.create(symbol="BTCUSDT", direction="up", entry=100, sl=95, tps=[110])

        resolved = trader.resolve_all(
            klines_provider=lambda symbol, category: None,
            category_provider=lambda symbol: "linear",
        )

        assert resolved == 0

    def test_resolve_all_without_active(self, tmp_path):
        trader = VirtualTrader(tmp_path / "vt.jsonl")

        assert trader.resolve_all(
            klines_provider=lambda *a: [], category_provider=lambda s: "linear",
        ) == 0


class TestStats:
    def test_empty_stats(self, tmp_path):
        trader = VirtualTrader(tmp_path / "vt.jsonl")

        assert "Пока нет данных" in format_stats_message(trader)

    def test_stats_with_closed_trades(self, tmp_path):
        trader = VirtualTrader(tmp_path / "vt.jsonl")
        win = trader.create(symbol="BTCUSDT", direction="up", entry=100, sl=95, tps=[110])
        win.status = "closed"
        win.realized_pnl = 10.0
        trader.update(win)

        loss = trader.create(symbol="ETHUSDT", direction="up", entry=100, sl=95, tps=[110])
        loss.status = "closed"
        loss.realized_pnl = -5.0
        trader.update(loss)

        active = trader.create(symbol="SOLUSDT", direction="up", entry=100, sl=95, tps=[110])

        text = format_stats_message(trader)

        assert "Всего закрыто: <b>2</b>" in text
        assert "Прибыльных: <b>1</b>" in text
        assert "Убыточных: <b>1</b>" in text
        assert "Winrate: <b>50.0%</b>" in text
        assert "PnL: <b>+5.00 USDT</b>" in text
        assert "BTCUSDT" in text and "ETHUSDT" in text
        assert active.id  # активная сделка не ломает отчёт


class TestSingleton:
    def test_init_and_get(self, tmp_path):
        trader = init_virtual_trader(tmp_path / "vt.jsonl")
        assert get_virtual_trader() is trader

    def test_set_none_then_get_creates_default(self, tmp_path, monkeypatch):
        set_virtual_trader(None)
        monkeypatch.setattr(
            "src.utils.config.Config.VIRTUAL_TRADER_FILE", tmp_path / "default.jsonl",
        )
        import src.core.virtual_trader as module
        module._trader_instance = None

        created = get_virtual_trader()

        assert created is not None
        set_virtual_trader(None)
