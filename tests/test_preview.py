from types import SimpleNamespace

import pytest

from speak.windows import preview_geometry
from speak.ui import App


@pytest.mark.parametrize("anchor,work", [
    ((2226, 292, 2512, 370), (0, 0, 2560, 1400)),  # Actual 150% DPI edge case.
    ((80, 140, 366, 218), (0, 0, 1920, 1040)),
    ((1634, 962, 1920, 1040), (0, 0, 1920, 1040)),
    ((0, 0, 286, 78), (0, 0, 600, 300)),  # Fit vertically when neither side fits.
    ((0, 0, 286, 78), (0, 0, 320, 200)),  # Shrink into remaining space.
    ((-286, 100, 0, 178), (-1920, 0, 0, 1040)),
    ((-1920, -200, -1634, -122), (-1920, -1080, 0, 0)),
])
def test_preview_remains_inside_monitor_without_covering_button(anchor, work):
    x, y, width, height = preview_geometry(anchor, work)
    assert work[0] <= x < x + width <= work[2]
    assert work[1] <= y < y + height <= work[3]
    assert x + width <= anchor[0] or x >= anchor[2] or y + height <= anchor[1] or y >= anchor[3]


def test_preview_hidden_if_button_fills_entire_work_area():
    assert preview_geometry((0, 0, 286, 78), (0, 0, 286, 78)) is None


def test_preview_stop_never_restarts_after_a_second_click():
    calls = []
    app = SimpleNamespace(controller=SimpleNamespace(state="recording"))
    def toggle():
        calls.append(1)
        app.controller.state = "processing"
    app.toggle = toggle
    App.stop_preview_recording(app)
    App.stop_preview_recording(app)
    app.controller.state = "idle"
    App.stop_preview_recording(app)
    assert calls == [1]
