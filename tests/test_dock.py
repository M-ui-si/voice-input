import json
from types import SimpleNamespace

import pytest

from speak.storage import Settings
from speak.windows import dock_geometry, nearest_dock_side
from speak import windows


@pytest.mark.parametrize("work,y,side", [
    ((0, 0, 2560, 1400), 200, "left"),
    ((0, 0, 2560, 1400), 200, "right"),
    ((0, 0, 2560, 1400), 9999, "right"),
    ((-1920, -1080, 0, 0), -9999, "left"),
    ((-1920, 0, 0, 1040), 100, "right"),
    ((40, 0, 1920, 1040), 100, "left"),  # Left taskbar.
    ((0, 0, 16, 40), 100, "right"),  # Extremely small work area.
])
def test_edge_handle_and_expansion_stay_in_the_selected_work_area(work, y, side):
    for collapsed in (True, False):
        x, py, width, height = dock_geometry(work, y, side, collapsed)
        assert work[0] <= x < x + width <= work[2]
        assert work[1] <= py < py + height <= work[3]
        assert x == work[0] if side == "left" else x + width == work[2]


def test_expanded_strip_never_falls_below_bottom_edge_of_handle():
    work = (0, 0, 1920, 1040)
    _, y, w, h = dock_geometry(work, 1030, "right")
    assert (w, h) == (22, 56)
    assert dock_geometry(work, y, "right", False) == (1634, 962, 286, 78)


def test_nearest_edge_uses_monitor_coordinates_including_negative_origin():
    assert nearest_dock_side((80, 100, 366, 178), (0, 0, 1920, 1040)) == "left"
    assert nearest_dock_side((-286, 100, 0, 178), (-1920, 0, 0, 1040)) == "right"
    assert nearest_dock_side((-1920, 100, -1634, 178), (-1920, 0, 0, 1040)) == "left"


def test_old_settings_remain_expanded_and_new_settings_round_trip(tmp_path):
    (tmp_path / "settings.json").write_text('{"x": 80, "y": 140}', "utf-8")
    settings = Settings.load(tmp_path)
    assert not settings.collapsed
    settings.collapsed, settings.dock_side, settings.x, settings.y = True, "right", -22, 600
    settings.save(tmp_path)
    assert Settings.load(tmp_path) == settings


def test_invalid_docking_settings_do_not_hide_the_app(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"collapsed": "false", "dock_side": "bad"}), "utf-8")
    settings = Settings.load(tmp_path)
    assert settings.collapsed is False and settings.dock_side == "left"


def test_native_hotkey_callback_only_queues_and_tk_poll_dispatches(monkeypatch):
    scheduled, cancelled, toggles = [], [], []
    root = SimpleNamespace(update_idletasks=lambda: None, winfo_id=lambda: 10,
                           after=lambda delay, fn: scheduled.append(fn) or len(scheduled),
                           after_cancel=cancelled.append)
    monkeypatch.setattr(windows.win32gui, "GetAncestor", lambda *a: 10)
    monkeypatch.setattr(windows.win32gui, "GetWindowLong", lambda *a: 0)
    monkeypatch.setattr(windows.win32gui, "SetWindowLong", lambda *a: 1)
    monkeypatch.setattr(windows.win32gui, "IsWindow", lambda *a: False)
    monkeypatch.setattr(windows.user32, "UnregisterHotKey", lambda *a: True)
    hook = windows.WindowHooks(root, lambda: toggles.append(1))
    assert len(scheduled) == 1
    hook.proc(10, windows.win32con.WM_HOTKEY, hook.HOTKEY_ID, 0)
    assert not toggles and len(scheduled) == 1
    scheduled[0]()
    assert toggles == [1] and len(scheduled) == 2
    hook.close()
    scheduled[-1]()
    assert len(scheduled) == 2 and cancelled == [2]
