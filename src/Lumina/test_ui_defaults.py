"""Widget readout tests (needs PyQt5; skipped without it).

Qt-dependent half of the PR-31 review coverage: readout formatting,
typed entry, and settings-panel defaults, run offscreen. Pure logic
(parse/format) and engine defaults live in the stdlib-only suite
(test_release_calibration.py) so they run everywhere.
"""

import os
import sys

sys.path.insert(0, ".")

import pytest

try:
    import PyQt5.QtWidgets as QtWidgets
    _HAS_QT = True
except ImportError:
    QtWidgets = None
    _HAS_QT = False

requires_qt = pytest.mark.skipif(not _HAS_QT, reason="PyQt5 not installed")

if _HAS_QT:
    if not os.environ.get("DISPLAY") and sys.platform.startswith("linux"):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, "src")

_app = None


def _app_or_skip():
    global _app
    if _app is None:
        try:
            _app = QtWidgets.QApplication([])
        except Exception as exc:
            pytest.skip(f"no Qt application: {exc}")
    return _app


@requires_qt
def test_settings_panel_defaults():
    _app_or_skip()
    from Lumina.color_controls import SettingsPanel
    p = SettingsPanel()
    assert p.azimuth.value() == 287
    assert p.elevation.value() == 45
    assert p.highlight_size.value() == 80


@requires_qt
def test_readout_format_percent_and_raw():
    _app_or_skip()
    from Lumina.sphere_docker import LabeledSliderRow, Accent
    pct = LabeledSliderRow(Accent.NEUTRAL, "Tone", 100, hi=200)
    assert pct._value.text() == "100%"
    hue = LabeledSliderRow(Accent.NEUTRAL, "Hue", 0, hi=359, unit="degree")
    hue.slider.setValue(180)
    assert hue._value.text() == "180°"


@requires_qt
def test_typed_percent_and_raw():
    _app_or_skip()
    from Lumina.sphere_docker import LabeledSliderRow, Accent
    r = LabeledSliderRow(Accent.NEUTRAL, "Tone", 100, hi=200)
    r._value.setText("86%")
    r._value.returnPressed.emit()
    assert r.slider.value() == 172
    r._value.setText("90")
    r._value.returnPressed.emit()
    assert r.slider.value() == 90


@requires_qt
def test_typed_invalid_reverts():
    _app_or_skip()
    from Lumina.sphere_docker import LabeledSliderRow, Accent
    r = LabeledSliderRow(Accent.NEUTRAL, "Tone", 100, hi=200)
    r._value.setText("junk")
    r._value.returnPressed.emit()
    assert r.slider.value() == 100
    assert r._value.text() == "100%"
    r._value.setText("9999")
    r._value.returnPressed.emit()
    assert r.slider.value() == 200


@requires_qt
def test_focus_out_reverts_uncommitted():
    _app_or_skip()
    from PyQt5.QtGui import QFocusEvent
    from PyQt5.QtCore import Qt, QEvent
    from Lumina.sphere_docker import LabeledSliderRow, Accent
    r = LabeledSliderRow(Accent.NEUTRAL, "Tone", 100, hi=200)
    r._value.setText("40")
    # Simulate typing without Enter: textEdited fires on real keystrokes.
    r._value.textEdited.emit("40")
    assert r.slider.value() == 100
    r._value.focusOutEvent(QFocusEvent(QEvent.FocusOut, Qt.OtherFocusReason))
    assert r.slider.value() == 100
    assert r._value.text() == "100%"


@requires_qt
def test_enter_then_blur_does_not_double_commit():
    _app_or_skip()
    from PyQt5.QtGui import QFocusEvent
    from PyQt5.QtCore import Qt, QEvent
    from Lumina.sphere_docker import LabeledSliderRow, Accent
    r = LabeledSliderRow(Accent.NEUTRAL, "Tone", 100, hi=200)
    r._value.setText("40")
    r._value.textEdited.emit("40")
    r._value.returnPressed.emit()
    assert r.slider.value() == 40
    assert r._value.text() == "40%"
    r._value.focusOutEvent(QFocusEvent(QEvent.FocusOut, Qt.OtherFocusReason))
    assert r.slider.value() == 40
    assert r._value.text() == "40%"


@requires_qt
def test_track_click_changes_nothing():
    _app_or_skip()
    from PyQt5.QtCore import Qt, QEvent, QPointF
    from PyQt5.QtGui import QMouseEvent
    from Lumina.sphere_docker import LabeledSliderRow, Accent
    r = LabeledSliderRow(Accent.NEUTRAL, "Tone", 100, hi=200)
    r.show()
    r.slider.resize(240, 26)
    r.slider.setValue(100)
    hx = r.slider._handle_x()
    assert hx > 40, hx
    ev = QMouseEvent(QEvent.MouseButtonPress, QPointF(5.0, 13.0),
                     Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    r.slider.mousePressEvent(ev)
    assert r.slider.value() == 100
    # Keyboard adjustment stays available: strong focus policy retained.
    from PyQt5.QtCore import Qt as _Qt
    assert r.slider.focusPolicy() == _Qt.StrongFocus


class _NoopTimer:
    def start(self, *a):
        pass

    def stop(self):
        pass


@requires_qt
def test_external_color_throttle_and_end_detector():
    _app_or_skip()
    from Lumina.sphere_docker import SphereDocker
    d = SphereDocker()
    d._rebuild_timer = _NoopTimer()
    applied = []
    d._rebuild_orb = lambda: applied.append(tuple(d._targets["base"].getRgb()[:3]))
    for i in range(5):
        d._note_external_color((10 + i, 20, 30), False)
    # Nothing applied synchronously; only the newest is retained.
    assert applied == []
    assert d._latest_external == ((14, 20, 30), False)
    d._apply_external_color()
    assert applied == [(14, 20, 30)], applied
    # Unchanged ticks count up but render nothing on their own. Fewer than
    # EXT_END_POLLS ticks must never end the stream: natural drag pauses
    # reach ~230 ms, and a mid-drag full render is the hitch.
    from Lumina.sphere_docker import EXT_END_POLLS
    assert EXT_END_POLLS >= 8, EXT_END_POLLS
    assert d._external_preview is True
    for _ in range(EXT_END_POLLS - 1):
        d._note_external_unchanged()
    assert applied == [(14, 20, 30)], applied
    assert d._external_preview is True
    # ...until the end detector declares the stream over. The final pass
    # always schedules one render: the throttle tick left the orb on the
    # reduced drag profile, and the image the user ends up looking at has
    # to be the full-quality one.
    d._note_external_unchanged()  # EXT_END_POLLS-th: queues final
    d._apply_external_final()     # queued call executes (no event loop here)
    assert d._external_preview is False
    assert applied == [(14, 20, 30), (14, 20, 30)], applied
    # A second final is a no-op: the stream already ended.
    d._apply_external_final()
    assert applied == [(14, 20, 30), (14, 20, 30)], applied
    # ...and a color that arrives later starts a new stream, not a reopen.
    d._note_external_color((99, 20, 30), False)
    assert d._external_preview is True
    d._apply_external_color()
    assert applied[-1] == (99, 20, 30)
    d._apply_external_final()
    assert applied[-1] == (99, 20, 30)


@requires_qt
def test_post_stream_save_armed_and_cancelled():
    """The save flush waits past stream end; a new color cancels it."""
    _app_or_skip()
    from Lumina.sphere_docker import SphereDocker
    d = SphereDocker()
    d._rebuild_timer = _NoopTimer()
    d._rebuild_orb = lambda: None
    d._note_external_color((10, 20, 30), False)
    d._apply_external_color()
    assert d._settings_dirty is True
    # No disk timer may run mid-stream.
    assert not d._save_timer.isActive()
    d._apply_external_final()
    assert d._external_preview is False
    assert d._save_timer.isActive()
    assert d._save_timer.interval() == 500
    # Resume: the pending flush is cancelled, dirtiness retained.
    d._note_external_color((11, 20, 30), False)
    assert not d._save_timer.isActive()
    assert d._settings_dirty is True


@requires_qt
def test_external_stream_uses_drag_render_profile():
    """A native picker drag must render like a slider drag, not at full size."""
    _app_or_skip()
    from Lumina.sphere_docker import SphereDocker
    d = SphereDocker()
    sizes = []

    def _fake_render(base, w, h):
        sizes.append((w, h))

    d.processor.render_image = _fake_render
    d._orb_render_size = 200
    saved_smooth = d.processor.engine.smooth

    d._note_external_color((10, 20, 30), False)
    assert d._external_preview is True
    assert d.processor.engine.smooth == 0.0
    d._render_orb()
    assert sizes[-1] == (96, 96), sizes

    d._apply_external_final()
    assert d._external_preview is False
    assert d.processor.engine.smooth == saved_smooth
    d._render_orb()
    assert sizes[-1] == (200, 200), sizes


@requires_qt
def test_settings_save_is_debounced_not_direct():
    _app_or_skip()
    from Lumina.sphere_docker import SphereDocker
    d = SphereDocker()
    d._rebuild_timer = _NoopTimer()
    saved = []
    d._save_settings = lambda: saved.append(1)
    d._rebuild_orb()
    d._rebuild_orb()
    d._rebuild_orb()
    # Marked dirty, nothing written yet.
    assert saved == []
    assert d._settings_dirty is True
    d._flush_settings()
    assert saved == [1]
    assert d._settings_dirty is False
    # Second flush with nothing pending writes nothing.
    d._flush_settings()
    assert saved == [1]


@requires_qt
def test_base_level_drag_telescopes_to_exact_factor():
    """A 100->70 drag must leave base at exactly x0.70, and the row enabled.

    Regression: the handler multiplied the live base by value/100 on every
    event, compounding to near-black after a few dozen ticks. That tripped
    the near-black guard, disabled the row mid-drag, and froze the slider
    around 70%.
    """
    _app_or_skip()
    from PyQt5.QtGui import QColor as _QColor
    from Lumina.sphere_docker import SphereDocker
    d = SphereDocker()
    d._rebuild_timer = _NoopTimer()
    d._rebuild_orb = lambda: None  # scaling + sync logic is what is tested
    d._set_base_color(_QColor(255, 255, 255))
    d._base_level_last = 100
    for v in (95, 85, 75, 70):
        d.color_row.set_value(v)
    got = d._targets["base"].getRgb()[:3]
    # 70% of white is ~178, not the ~108 the compounding bug produced.
    assert all(abs(c - 178) <= 2 for c in got), got
    # The row must still be interactive, not greyed out by the guard.
    assert d.color_row.isEnabled()
    assert d._base_level_last == 70


@requires_qt
def test_base_level_reset_anchor_is_noop():
    """Reset's set_value(100) after a direct base restore must not rescale."""
    _app_or_skip()
    from PyQt5.QtGui import QColor as _QColor
    from Lumina.sphere_docker import SphereDocker
    d = SphereDocker()
    d._rebuild_timer = _NoopTimer()
    d._rebuild_orb = lambda: None
    d._set_base_color(_QColor(200, 100, 50))
    d._base_level_last = 100  # reset path anchors before set_value(100)
    before = d._targets["base"].getRgb()[:3]
    d.color_row.set_value(100)
    after = d._targets["base"].getRgb()[:3]
    assert before == after == (200, 100, 50)


@requires_qt
def test_lumina_logger_has_file_handler():
    """The Lumina logger must own a lumina_log.txt FileHandler.

    Regression: logging.basicConfig() is a no-op when the host already
    configured root logging, which left the log file empty under flatpak.
    """
    _app_or_skip()
    import logging
    import os as _os
    from Lumina import sphere_docker as _sd
    paths = [ _os.path.abspath(getattr(h, "baseFilename", "") or "")
              for h in _sd.LOG.handlers
              if isinstance(h, logging.FileHandler) ]
    assert any(p.endswith("lumina_log.txt") for p in paths), paths


@requires_qt
def test_base_level_low_end_never_freezes():
    """Dragging to 2% must leave the row enabled and draggable back up."""
    _app_or_skip()
    from PyQt5.QtGui import QColor as _QColor
    from Lumina.sphere_docker import SphereDocker
    d = SphereDocker()
    d._rebuild_timer = _NoopTimer()
    d._rebuild_orb = lambda: None
    d._set_base_color(_QColor(255, 255, 255))
    d._base_level_last = 100
    for v in (50, 10, 2):
        d.color_row.set_value(v)
    got = d._targets["base"].getRgb()[:3]
    assert all(abs(c - 5) <= 2 for c in got), got
    assert d.color_row.isEnabled()
    # ...and dragging back up recovers brightness in place.
    d.color_row.set_value(50)
    got = d._targets["base"].getRgb()[:3]
    assert all(abs(c - 128) <= 3 for c in got), got
    assert d.color_row.isEnabled()


@requires_qt
def test_base_level_recovers_hue_from_exact_black():
    """0 then back up must restore the hue, not stay black or go grey."""
    _app_or_skip()
    from PyQt5.QtGui import QColor as _QColor
    from Lumina.sphere_docker import SphereDocker
    d = SphereDocker()
    d._rebuild_timer = _NoopTimer()
    d._rebuild_orb = lambda: None
    warm = _QColor(200, 100, 50)
    d._set_base_color(_QColor(warm))
    d._base_level_last = 100
    d.color_row.set_value(0)
    assert d._targets["base"].getRgb()[:3] == (0, 0, 0)
    assert d.color_row.isEnabled()
    # Slider 78 means V=0.78 with the remembered hue/saturation.
    d.color_row.set_value(78)
    h0, s0 = warm.getHsvF()[0], warm.getHsvF()[1]
    h1, s1, v1 = d._targets["base"].getHsvF()[0], d._targets["base"].getHsvF()[1], d._targets["base"].getHsvF()[2]
    assert abs(h1 - h0) <= 0.02, (h1, h0)
    assert abs(s1 - s0) <= 0.02, (s1, s0)
    assert abs(v1 - 0.78) <= 0.02, v1


@requires_qt
def test_render_stats_reveals_single_hitch():
    """avg must not be the only number: one 240 ms spike hides in avg 51."""
    _app_or_skip()
    from Lumina.sphere_docker import SphereDocker
    avg, p95, peak = SphereDocker._render_stats([30.0] * 9 + [240.0])
    assert abs(avg - 51.0) < 0.01, avg
    assert p95 == 30.0, p95
    assert peak == 240.0, peak
    assert SphereDocker._render_stats([]) == (0.0, 0.0, 0.0)
