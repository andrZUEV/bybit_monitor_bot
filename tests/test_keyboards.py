"""Тесты клавиатур (Этап 11): все меню собираются и содержат нужные callback'и."""
from __future__ import annotations

from src.telegram import keyboards


def _callbacks(markup) -> list[str]:
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
    ]


def _labels(markup) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]


def _rows(markup) -> int:
    return len(markup.inline_keyboard)


class TestMainMenus:
    def test_main_menu(self):
        markup = keyboards.main_menu_keyboard()
        assert _callbacks(markup) == [
            "menu_add", "menu_screener", "menu_prices",
            "menu_list", "menu_export", "menu_help",
        ]

    def test_main_menu_with_settings_shows_threshold(self):
        markup = keyboards.main_menu_keyboard_with_settings("-inf")
        callbacks = _callbacks(markup)
        labels = " ".join(_labels(markup))

        assert "settings_menu" in callbacks
        assert "ls_menu" in callbacks
        assert "rd_menu" in callbacks
        assert "vt_menu" in callbacks
        assert "-inf" in labels

    def test_export_options(self):
        callbacks = _callbacks(keyboards.export_options_keyboard())
        assert callbacks == [
            "export_tracked", "export_manual", "history_menu", "menu_main",
        ]

    def test_direction_keyboard(self):
        callbacks = _callbacks(keyboards.direction_keyboard())
        assert callbacks[:3] == ["dir_up", "dir_down", "dir_any"]

    def test_cancel_keyboard(self):
        assert _callbacks(keyboards.cancel_keyboard()) == ["menu_main"]

    def test_skip_note_keyboard(self):
        callbacks = _callbacks(keyboards.skip_note_keyboard())
        assert callbacks == ["skip_note", "menu_main"]

    def test_screener_add_alert(self):
        callbacks = _callbacks(keyboards.screener_add_alert_keyboard("BTCUSDT"))
        assert "scr_add_BTCUSDT" in callbacks
        assert "export_screener" in callbacks


class TestAlertsList:
    def test_pagination_buttons(self):
        markup = keyboards.alerts_list_keyboard(
            [("BTCUSDT", 100.0, "up")], page=1, pages=3,
        )
        callbacks = _callbacks(markup)

        assert "list_page|0" in callbacks
        assert "list_page|2" in callbacks
        assert "del|BTCUSDT|100.0|up|1" in callbacks
        assert "del_all_BTCUSDT" in callbacks

    def test_single_page_has_no_nav(self):
        markup = keyboards.alerts_list_keyboard(
            [("BTCUSDT", 100.0, "up")], page=0, pages=1,
        )
        assert not any(c.startswith("list_page|") for c in _callbacks(markup))

    def test_zone_label_in_button(self):
        markup = keyboards.alerts_list_keyboard(
            [("BTCUSDT", 102.5, "up", "100.00–105.00")],
        )
        assert any("100.00–105.00" in label for label in _labels(markup))


class TestSettingsKeyboards:
    def test_settings_menu(self):
        markup = keyboards.settings_menu_keyboard("-inf", "ВЫКЛ", "10000")
        callbacks = _callbacks(markup)

        assert "settings_threshold" in callbacks
        assert "settings_toggle_invalid" in callbacks
        assert "settings_history_depth" in callbacks
        assert "menu_main" in callbacks
        assert any("ВЫКЛ" in label for label in _labels(markup))

    def test_toggle_invalid_on(self):
        callbacks = _callbacks(keyboards.toggle_invalid_alerts_keyboard(True))
        assert "set_invalid|0" in callbacks

    def test_toggle_invalid_off(self):
        callbacks = _callbacks(keyboards.toggle_invalid_alerts_keyboard(False))
        assert "set_invalid|1" in callbacks

    def test_threshold_presets_marks_current(self):
        markup = keyboards.threshold_presets_keyboard((float("-inf"), 0.0, 3.0), 3.0)
        callbacks = _callbacks(markup)

        assert "set_score|-inf" in callbacks
        assert "set_score|3.0" in callbacks
        assert "set_score_custom" in callbacks

    def test_custom_score_keyboard(self):
        callbacks = _callbacks(keyboards.settings_custom_score_keyboard())
        assert "settings_threshold" in callbacks

    def test_history_depth_keyboard(self):
        markup = keyboards.history_depth_keyboard((1000, 5000), 5000)
        callbacks = _callbacks(markup)

        assert "set_depth|1000" in callbacks
        assert "set_depth|5000" in callbacks
        assert "set_depth_custom" in callbacks

    def test_custom_depth_keyboard(self):
        callbacks = _callbacks(keyboards.history_custom_depth_keyboard())
        assert "settings_history_depth" in callbacks


class TestAlertHistoryKeyboards:
    def test_history_menu(self):
        markup = keyboards.alert_history_menu_keyboard(42)
        callbacks = _callbacks(markup)

        assert "history_export_csv" in callbacks
        assert "history_clear_confirm" in callbacks
        assert any("42" in label for label in _labels(markup))

    def test_clear_confirm(self):
        callbacks = _callbacks(keyboards.alert_history_clear_confirm_keyboard())
        assert callbacks == ["history_clear_yes", "history_menu"]


class TestLevelScanKeyboards:
    def test_menu_enabled(self):
        markup = keyboards.level_scan_menu_keyboard(True, "6,18", 5.0)
        callbacks = _callbacks(markup)

        assert "ls_scan_now" in callbacks
        assert "ls_toggle_enabled" in callbacks
        assert "ls_set_min_score" in callbacks
        assert "ls_set_hours" in callbacks

    def test_menu_disabled(self):
        markup = keyboards.level_scan_menu_keyboard(False, "6,18", 5.0)
        assert "ls_toggle_enabled" in _callbacks(markup)

    def test_hours_presets(self):
        markup = keyboards.level_scan_hours_presets_keyboard(("6,18", "8"), "8")
        callbacks = _callbacks(markup)

        assert "ls_set_hours|6,18" in callbacks
        assert "ls_set_hours|8" in callbacks
        assert "ls_menu" in callbacks

    def test_score_presets(self):
        markup = keyboards.level_scan_score_presets_keyboard((3.0, 5.0), 5.0)
        callbacks = _callbacks(markup)

        assert "ls_set_min_score|3.0" in callbacks
        assert "ls_set_min_score|5.0" in callbacks


class TestReversalDiaryKeyboards:
    def test_menu(self):
        markup = keyboards.rd_menu_keyboard(True, 15, 120)
        callbacks = _callbacks(markup)

        assert "rd_show_stats" in callbacks
        assert "rd_toggle_enabled" in callbacks
        assert "rd_set_min_n" in callbacks
        assert "rd_export_csv" in callbacks
        assert "rd_clear_confirm" in callbacks

    def test_min_n_presets(self):
        markup = keyboards.rd_min_n_presets_keyboard((5, 15, 30), 15)
        callbacks = _callbacks(markup)

        assert "rd_set_min_n|5" in callbacks
        assert "rd_set_min_n|15" in callbacks

    def test_clear_confirm(self):
        callbacks = _callbacks(keyboards.rd_clear_confirm_keyboard())
        assert callbacks == ["rd_clear_yes", "rd_menu"]


class TestVirtualTradeKeyboards:
    def test_menu_with_data(self):
        markup = keyboards.vt_menu_keyboard(True, total=5, active=2)
        callbacks = _callbacks(markup)

        assert "vt_list_active" in callbacks
        assert "vt_list_closed" in callbacks
        assert "vt_stats" in callbacks
        assert "vt_export" in callbacks
        assert "vt_toggle_enabled" in callbacks

    def test_menu_empty(self):
        markup = keyboards.vt_menu_keyboard(False, total=0, active=0)
        callbacks = _callbacks(markup)

        assert "vt_list_active" not in callbacks
        assert "vt_list_closed" not in callbacks
        assert callbacks == ["vt_toggle_enabled", "menu_main"]

    def test_list_keyboard(self):
        callbacks = _callbacks(keyboards.vt_list_keyboard("active"))
        assert callbacks == ["vt_menu", "menu_main"]

    def test_trade_card(self):
        callbacks = _callbacks(keyboards.vt_trade_card_keyboard("abc123"))
        assert callbacks == ["vt_cancel|abc123", "menu_main"]


class TestStructure:
    def test_all_buttons_have_single_callback(self):
        """Каждая кнопка должна иметь непустой callback_data."""
        markups = [
            keyboards.main_menu_keyboard(),
            keyboards.main_menu_keyboard_with_settings("0"),
            keyboards.export_options_keyboard(),
            keyboards.direction_keyboard(),
            keyboards.cancel_keyboard(),
            keyboards.skip_note_keyboard(),
            keyboards.alerts_list_keyboard([("BTCUSDT", 1.0, "up")], 0, 3),
            keyboards.screener_add_alert_keyboard("BTCUSDT"),
            keyboards.settings_menu_keyboard("0"),
            keyboards.toggle_invalid_alerts_keyboard(True),
            keyboards.threshold_presets_keyboard((0.0,), 0.0),
            keyboards.settings_custom_score_keyboard(),
            keyboards.history_depth_keyboard((1000,), 1000),
            keyboards.history_custom_depth_keyboard(),
            keyboards.alert_history_menu_keyboard(1),
            keyboards.alert_history_clear_confirm_keyboard(),
            keyboards.level_scan_menu_keyboard(True, "6,18", 5.0),
            keyboards.level_scan_hours_presets_keyboard(("6",), "6"),
            keyboards.level_scan_score_presets_keyboard((3.0,), 3.0),
            keyboards.rd_menu_keyboard(True, 15, 10),
            keyboards.rd_min_n_presets_keyboard((5,), 5),
            keyboards.rd_clear_confirm_keyboard(),
            keyboards.vt_menu_keyboard(True, 1, 1),
            keyboards.vt_list_keyboard("closed"),
            keyboards.vt_trade_card_keyboard("id1"),
        ]

        for markup in markups:
            callbacks = _callbacks(markup)
            assert callbacks
            assert all(isinstance(c, str) and c for c in callbacks)
            assert _rows(markup) >= 1
