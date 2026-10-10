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
    assert p.azimuth.value() == 304
    assert p.elevation.value() == 41
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
def test_track_click_jumps_to_position():
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
    # Track press far from the knob jumps to the click (Krita convention)...
    ev = QMouseEvent(QEvent.MouseButtonPress, QPointF(5.0, 13.0),
                     Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    r.slider.mousePressEvent(ev)
    assert r.slider.value() == 0, r.slider.value()
    assert r.slider._dragging is True
    # ...while a press on the knob grabs relatively with no jump.
    r.slider.setValue(100)
    hx = r.slider._handle_x()
    ev2 = QMouseEvent(QEvent.MouseButtonPress, QPointF(hx, 13.0),
                      Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    r.slider.mousePressEvent(ev2)
    assert r.slider.value() == 100, r.slider.value()
    assert r.slider._dragging is True
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
    d._rebuild_sphere = lambda reason="live_update": applied.append(tuple(d._targets["base"].getRgb()[:3]))
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
    # always schedules one render: the throttle tick left the sphere on the
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
    d._rebuild_sphere = lambda reason="live_update": None
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
    full_flags = []

    def _fake_render(base, w, h, full_quality=False):
        sizes.append((w, h))
        full_flags.append(full_quality)

    d.processor.render_image = _fake_render
    d._sphere_render_size = 200
    saved_smooth = d.processor.engine.smooth

    d._note_external_color((10, 20, 30), False)
    assert d._external_preview is True
    assert d.processor.engine.smooth == 0.0
    d._render_sphere()
    assert sizes[-1] == (96, 96), sizes
    assert full_flags[-1] is False, full_flags

    d._apply_external_final()
    assert d._external_preview is False
    assert d.processor.engine.smooth == saved_smooth
    d._render_sphere()
    assert sizes[-1] == (200, 200), sizes
    assert full_flags[-1] is True, full_flags


@requires_qt
def test_settings_save_is_debounced_not_direct():
    _app_or_skip()
    from Lumina.sphere_docker import SphereDocker
    d = SphereDocker()
    d._rebuild_timer = _NoopTimer()
    saved = []
    d._save_settings = lambda: saved.append(1)
    d._rebuild_sphere()
    d._rebuild_sphere()
    d._rebuild_sphere()
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
    d._rebuild_sphere = lambda reason="live_update": None  # scaling + sync logic is what is tested
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
    d._rebuild_sphere = lambda reason="live_update": None
    d._set_base_color(_QColor(200, 100, 50))
    d._base_level_last = 100  # reset path anchors before set_value(100)
    before = d._targets["base"].getRgb()[:3]
    d.color_row.set_value(100)
    after = d._targets["base"].getRgb()[:3]
    assert before == after == (200, 100, 50)


@requires_qt
def test_lumina_logger_has_file_handler():
    """The Lumina logger must own a lumina_log.txt file handler.

    Regression: logging.basicConfig() is a no-op when the host already
    configured root logging, which left the log file empty under flatpak.
    """
    _app_or_skip()
    import os as _os
    from Lumina import lumina_logging as _ll
    from Lumina import sphere_docker as _sd
    paths = [ _os.path.abspath(getattr(h, "baseFilename", "") or "")
              for h in _sd.LOG.handlers
              if isinstance(h, _ll.ReopenFileHandler) ]
    assert any(p.endswith("lumina_log.txt") for p in paths), paths


@requires_qt
def test_log_handler_never_holds_file_open():
    """Emitting must not leave an open handle on lumina_log.txt.

    Regression (Windows): a permanently-open FileHandler locks the file,
    so Krita's plugin importer fails with PermissionError [WinError 32]
    when deleting the old plugin directory on reinstall.
    """
    _app_or_skip()
    import logging as _logging
    from Lumina import lumina_logging as _ll
    from Lumina import sphere_docker as _sd
    _sd.LOG.info("handler-hold-probe")
    for h in _sd.LOG.handlers:
        if isinstance(h, _ll.ReopenFileHandler):
            assert not hasattr(h, "stream"), "handler holds file open"
    # No duplicate handlers after re-import (reload-safe guard).
    import importlib as _il
    _il.reload(_ll)
    count = sum(isinstance(h, _ll.ReopenFileHandler) for h in _sd.LOG.handlers)
    assert count == 1, count


@requires_qt
def test_hex_labels_copy_and_toggle():
    """Hex readouts track the swatches and toggle via the persisted setting."""
    _app_or_skip()
    from PyQt5.QtGui import QColor as _QColor
    from Lumina.sphere_docker import SphereDocker
    d = SphereDocker()
    d._rebuild_timer = _NoopTimer()
    d._rebuild_sphere = lambda reason="live_update": None
    # Labels exist under their swatches and follow the colors.
    d._set_base_color(_QColor(255, 128, 0))
    d._original_color = _QColor(16, 32, 48)
    d._update_preview()
    assert d._hex_current.text() == "#ff8000", d._hex_current.text()
    assert d._hex_prev.text() == "#102030", d._hex_prev.text()
    assert not d._hex_wrap.isHidden()
    # Click-to-copy writes the hex to the clipboard.
    from PyQt5.QtWidgets import QApplication as _QA
    d._on_hex_pressed("current")
    assert _QA.clipboard().text() == "#ff8000"
    assert d._hex_current.text() == "copied"
    # Setting off hides the prev label and the current pair wrapper.
    d._set_hex_visible(False)
    assert d._hex_wrap.isHidden()
    assert d._hex_prev.isHidden()
    assert d._collect_settings()["show_hex"] is False
    # Back on restores them with current values.
    d._set_hex_visible(True)
    d._update_preview()
    assert not d._hex_wrap.isHidden()
    assert d._hex_current.text() == "#ff8000"
    # Panel checkbox round-trips through the settings state dict.
    d._settings_panel.hex_cb.setChecked(False)
    assert d._settings_panel.hex_cb.isChecked() is False
    d._on_settings_changed({"azimuth": 287, "elevation": 45,
                            "highlight_size": 80, "quality": 200,
                            "pointer": True, "hex": False})
    assert d._hex_wrap.isHidden()
    d._on_settings_changed({"azimuth": 287, "elevation": 45,
                            "highlight_size": 80, "quality": 200,
                            "pointer": True, "hex": True})
    assert not d._hex_wrap.isHidden()


@requires_qt
def test_render_done_carries_trigger_reason():
    """_rebuild_sphere(reason=...) survives coalescing into RENDER_DONE."""
    _app_or_skip()
    from PyQt5.QtGui import QImage as _QI
    from Lumina.sphere_docker import SphereDocker
    d = SphereDocker()
    d._rebuild_timer = _NoopTimer()
    seen = []
    d._sphere.set_image = lambda img: seen.append(img)
    d._render_sphere = lambda: (
        setattr(d, "_last_render_size", 96),
        setattr(d, "_last_render_smooth", 0.0),
        _QI(96, 96, _QI.Format_ARGB32),
    )[-1]
    d._rebuild_sphere(reason="slider_release")
    assert d._pending_reason == "slider_release"
    d._rebuild_sphere_now()
    assert len(seen) == 1
    assert d._pending_reason is None
    assert d._last_render_size == 96


@requires_qt
def test_target_switch_logs_sync_stamp():
    """Target switches stamp version + module + hsv + rows (Q3)."""
    _app_or_skip()
    import logging as _logging
    from PyQt5.QtGui import QColor as _QColor
    from Lumina.sphere_docker import SphereDocker

    class _Handler(_logging.Handler):
        def __init__(self):
            super().__init__()
            self.lines = []

        def emit(self, record):
            self.lines.append(record.getMessage())

    d = SphereDocker()
    d._rebuild_timer = _NoopTimer()
    d._rebuild_sphere = lambda reason="live_update": None
    probe = _Handler()
    from Lumina import sphere_docker as _sd
    _sd.LOG.addHandler(probe)
    try:
        d._targets["shadow"] = _QColor("#2b0850")
        d._on_target_changed("shadow")
    finally:
        _sd.LOG.removeHandler(probe)
    stamps = [ln for ln in probe.lines if ln.startswith("TARGET_SYNC")]
    assert len(stamps) == 1, probe.lines
    assert "version=%s" % _sd.PLUGIN_VERSION in stamps[0], stamps[0]
    assert "target=shadow" in stamps[0], stamps[0]
    # OKLCH stamp; the rows must show exactly what the stamp says.
    import re as _re
    lch = _re.search(r"lch=\((\d+,\d+,\d+)\)", stamps[0]).group(1)
    assert "sliders=(%s)" % lch in stamps[0], stamps[0]
    assert "sphere_docker.py" in stamps[0], stamps[0]


@requires_qt
def test_handle_x_tracks_value_proportionally():
    """Painted knob position must agree with the numeric value (Q2 gap).

    Value sync was proven exact, but nothing checked the value -> pixel
    mapping the user actually sees. A knob painted away from its value
    would look exactly like the photographed mismatch with correct rows.
    """
    _app_or_skip()
    from Lumina.color_controls import ColorSlider
    sl = ColorSlider()
    sl.setMinimum(0)
    sl.setMaximum(359)
    sl.resize(240, 26)
    sl.show()
    w = sl.width()
    r = sl._handle_radius()
    for value, lo_frac, hi_frac in ((0, 0.0, 0.08), (179, 0.42, 0.58),
                                    (268, 0.66, 0.82), (327, 0.83, 0.97),
                                    (14, 0.0, 0.12), (359, 0.92, 1.0)):
        sl.setValue(value)
        x = sl._handle_x()
        frac = (x - r) / max(1.0, (w - 2 * r))
        assert lo_frac <= frac <= hi_frac, (value, x, frac)
    # Click mapping is the exact inverse of the paint mapping: a click at
    # the painted knob must read back (nearly) the same value.
    sl.setValue(268)
    assert abs(sl._value_from_x(sl._handle_x()) - 268) <= 2


@requires_qt
def test_track_press_jumps_knob_to_click():
    """Track presses move the knob (Krita convention); knob grabs stay relative."""
    _app_or_skip()
    from PyQt5.QtCore import QEvent, QPointF, Qt as _Qt
    from PyQt5.QtGui import QMouseEvent as _QME
    from Lumina.color_controls import ColorSlider
    sl = ColorSlider()
    sl.setMinimum(0)
    sl.setMaximum(100)
    sl.setValue(20)
    sl.resize(200, 26)
    sl.show()
    # Far from the knob: jumps to the click, then drags relatively.
    far_x = sl.width() - 11  # near max end
    sl.mousePressEvent(_QME(QEvent.MouseButtonPress, QPointF(far_x, 13),
                            _Qt.LeftButton, _Qt.LeftButton, _Qt.NoModifier))
    assert sl.value() > 90, sl.value()
    assert sl._dragging is True
    sl.mouseReleaseEvent(_QME(QEvent.MouseButtonRelease, QPointF(far_x, 13),
                              _Qt.LeftButton, _Qt.NoButton, _Qt.NoModifier))
    assert sl._dragging is False


@requires_qt
def test_hex_edit_commit_and_lock():
    """Unlocked hex fields accept typed colors; locked copies; lock persists."""
    _app_or_skip()
    from PyQt5.QtGui import QColor as _QColor
    from Lumina.sphere_docker import SphereDocker
    d = SphereDocker()
    d._rebuild_timer = _NoopTimer()
    d._rebuild_sphere = lambda reason="live_update": None
    # Locked by default: fields read-only.
    assert d._hex_locked is True
    assert d._hex_current.isReadOnly()
    assert d._hex_prev.isReadOnly()
    # Unlock: green DONE, current editable, previous stays display-only.
    d._set_hex_locked(False)
    assert d._hex_locked is False
    assert not d._hex_current.isReadOnly()
    assert d._hex_prev.isReadOnly()
    assert d._hex_lock_btn.text() == "DONE"
    # Valid edit on current derives the full lighting set (eyedropper
    # behavior); editing previous is a no-op that just refreshes.
    d._hex_current.setText("#743356")
    d._commit_hex("current")
    assert d._targets["base"].name() == "#743356"
    assert d._targets["light"].name() != "#743356"
    assert d._targets["shadow"].name() != "#743356"
    before = d._original_color
    d._hex_prev.setText("#ffffff")
    d._commit_hex("prev")
    assert d._original_color == before
    # Invalid input reverts to the shown color instead of sticking.
    d._hex_current.setText("not-a-color")
    d._commit_hex("current")
    assert d._hex_current.text() == "#743356"
    # Lock state persists through collect; re-lock restores read-only.
    d._set_hex_locked(True)
    assert d._collect_settings()["hex_locked"] is True
    assert d._hex_current.isReadOnly()
    assert d._hex_lock_btn.text() == "EDIT BASE"


@requires_qt
def test_edge_samples_erode_inward_not_background():
    """Rim samples must read sphere color, not the AA fringe mix."""
    _app_or_skip()
    from PyQt5.QtCore import QPointF as _QP
    from PyQt5.QtGui import QImage as _QI, QColor as _QC
    from Lumina.sphere_widget import SphereWidget
    w = SphereWidget()
    img = _QI(200, 200, _QI.Format_ARGB32)
    img.fill(_QC(200, 100, 50))
    w.set_image(img)
    w.resize(200, 200)
    w.show()
    import math as _math
    # Dead-center sample is untouched.
    sx, sy = w._to_sphere_coords(_QP(100.0, 100.0))
    assert (sx, sy) == (100, 100), (sx, sy)
    # Exact rim (r=1.0) pulls ~2px inward instead of reading the edge pixel.
    sx, sy = w._to_sphere_coords(_QP(191.0, 100.0))
    u = (sx / 199.0) * 2.0 - 1.0
    assert _math.sqrt(u * u) < 0.99, (sx, sy)
    assert 0 <= sx < 200 and 0 <= sy < 200
    # The sticky band reaches EDGE_GLIDE (40px) past the rim: ~33px out on
    # the diagonal still projects onto the rim...
    sx, sy = w._to_sphere_coords(_QP(180.0, 180.0))
    assert (sx, sy) != (None, None), "33px past rim should still pick"
    # ...hovering stops at the widget's own bounds...
    assert w._to_sphere_coords(_QP(100.0, 240.0)) == (None, None)
    # ...but a drag keeps the mouse, so it holds on past them, up to the band.
    w._pointer_down = True
    sx, sy = w._to_sphere_coords(_QP(100.0, 215.0))   # 35px below the rim
    assert (sx, sy) != (None, None), "a drag 35px out should still pick"
    assert sy > 190, (sx, sy)                          # on the bottom rim
    assert w._to_sphere_coords(_QP(100.0, 225.0)) == (None, None)   # 45px: let go
    w._pointer_down = False


@requires_qt
def test_accordion_debounce_double_press():
    """A double-click must open, not open-then-shut (reads as 'won't open')."""
    _app_or_skip()
    from PyQt5.QtCore import QEvent, QPointF, Qt as _Qt
    from PyQt5.QtGui import QMouseEvent as _QME
    from PyQt5.QtWidgets import QLabel as _QL
    from Lumina.color_controls import CollapsibleSection
    s = CollapsibleSection("Advanced", expanded=False)
    s.add_row(_QL("row"))
    s.show()
    assert s.is_expanded() is False
    ev = lambda: _QME(QEvent.MouseButtonPress, QPointF(10.0, 10.0),
                      _Qt.LeftButton, _Qt.LeftButton, _Qt.NoModifier)
    s._header.mousePressEvent(ev())
    assert s.is_expanded() is True
    s._header.mousePressEvent(ev())  # immediate second press: ignored
    assert s.is_expanded() is True


@requires_qt
def test_rim_tint_interlock_keeps_stored_value():
    """Rim Tint greys out at Rim 0 without losing its value (Q11)."""
    _app_or_skip()
    from Lumina.sphere_docker import SphereDocker
    d = SphereDocker()
    d._rebuild_timer = _NoopTimer()
    d._rebuild_sphere = lambda reason="live_update": None
    d.rim_mix_row.set_value(60)
    assert d.rim_mix_row.isEnabled()
    d.rim_row.set_value(0)
    assert not d.rim_mix_row.isEnabled()
    assert d.rim_mix_row.value() == 60
    assert d.processor.engine.rim_light == 0.0
    d.rim_row.set_value(14)
    assert d.rim_mix_row.isEnabled()
    assert d.rim_mix_row.value() == 60


@requires_qt
def test_base_level_low_end_never_freezes():
    """Dragging to 2% must leave the row enabled and draggable back up."""
    _app_or_skip()
    from PyQt5.QtGui import QColor as _QColor
    from Lumina.sphere_docker import SphereDocker
    d = SphereDocker()
    d._rebuild_timer = _NoopTimer()
    d._rebuild_sphere = lambda reason="live_update": None
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
    d._rebuild_sphere = lambda reason="live_update": None
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


def _switch_docker():
    from Lumina.sphere_docker import SphereDocker
    d = SphereDocker()
    d._rebuild_timer = _NoopTimer()
    d._rebuild_sphere = lambda reason="live_update": None
    return d


def _hexes(d):
    return {k: d._targets[k].name() for k in ("shadow", "base", "light")}


@requires_qt
def test_krita_pick_replaces_active_shadow_only():
    """The active target is a switch: a Krita pick with the shadow selected
    replaces the shadow and leaves base and light untouched."""
    _app_or_skip()
    d = _switch_docker()
    d._on_target_changed("shadow")
    before = _hexes(d)
    d._apply_sampled_color((10, 200, 30))   # poll path (eyedropper, selectors)
    after = _hexes(d)
    assert after["shadow"] == "#0ac81e", after
    assert after["base"] == before["base"], (before, after)
    assert after["light"] == before["light"], (before, after)
    # The sliders now describe the replaced shadow, not the base.
    assert d.hue_row.slider.value() == int(round(d._lch_of("shadow")[0])) % 360


@requires_qt
def test_krita_pick_replaces_active_light_on_signal_path():
    """The non-derived (signal) path follows the switch too."""
    _app_or_skip()
    d = _switch_docker()
    d._on_target_changed("light")
    before = _hexes(d)
    d._note_external_color((240, 220, 90), False)
    d._apply_external_color()
    after = _hexes(d)
    assert after["light"] == "#f0dc5a", after
    assert after["base"] == before["base"], (before, after)
    assert after["shadow"] == before["shadow"], (before, after)


@requires_qt
def test_krita_pick_with_base_active_still_derives():
    """Base selected keeps the existing behaviour: base takes the pick and
    light/shadow are re-derived from it."""
    _app_or_skip()
    d = _switch_docker()
    d._on_target_changed("base")
    d._apply_sampled_color((200, 60, 40))
    after = _hexes(d)
    assert after["base"] == "#c83c28", after
    assert after["light"] != after["base"] and after["shadow"] != after["base"], after
    assert d._targets["light"].valueF() > d._targets["base"].valueF()
    assert d._targets["shadow"].valueF() < d._targets["base"].valueF()


@requires_qt
def test_change_detection_compares_against_active_target():
    """With the shadow selected, a foreground equal to the base is still new."""
    _app_or_skip()
    d = _switch_docker()
    d._on_target_changed("shadow")
    noted = []
    d._note_external_color = lambda cur, full: noted.append(cur)
    base_rgb = tuple(d._targets["base"].getRgb()[:3])
    d._current_krita_rgb = lambda: base_rgb
    d._on_krita_color_changed()
    assert noted == [base_rgb], noted
    # ...and a foreground equal to the shadow itself is not.
    noted.clear()
    shadow_rgb = tuple(d._targets["shadow"].getRgb()[:3])
    d._current_krita_rgb = lambda: shadow_rgb
    d._on_krita_color_changed()
    assert noted == [], noted




# --- Krita colour in Lumina's working space ---------------------------------
# A stand-in for libkis ManagedColor: native B,G,R,A storage like the real
# one, and profile conversion by lookup table. Only the conversions a test
# lists exist, so an unexpected conversion fails loudly instead of passing.
_SRGB = "sRGB-elle-V2-srgbtrc.icc"
_P3 = "Display P3 Gamut with sRGB Transfer"


class _FakeManagedColor:
    # Pairs measured with Krita 5.3.3's own colour management.
    _PAIRS = [((0x74, 0x33, 0x56), (0x7d, 0x2e, 0x57)),   # P3 -> sRGB
              ((0x01, 0x02, 0xb5), (0x01, 0x02, 0xbd))]
    CONVERSIONS = {(_P3, _SRGB): {p: s for p, s in _PAIRS},
                   (_SRGB, _P3): {s: p for p, s in _PAIRS}}
    # An 8-bit round trip that drifts, as measured in Krita: red near zero.
    CONVERSIONS[(_SRGB, _P3)][(0x00, 0xa8, 0x93)] = (0x4b, 0xa5, 0x93)
    CONVERSIONS[(_P3, _SRGB)][(0x4b, 0xa5, 0x93)] = (0x05, 0xa8, 0x92)
    conversions = []

    def __init__(self, model, depth, profile):
        self._space = (model, depth, profile)
        self._native = [0.0, 0.0, 0.0, 1.0]

    def colorModel(self):
        return self._space[0]

    def colorDepth(self):
        return self._space[1]

    def colorProfile(self):
        return self._space[2]

    def setComponents(self, values):
        self._native = list(values)

    def components(self):
        return list(self._native)

    def componentsOrdered(self):
        b, g, r, a = self._native
        return [r, g, b, a]

    def setColorSpace(self, model, depth, profile):
        rgb = tuple(int(round(v * 255)) for v in self.componentsOrdered()[:3])
        r, g, b = self.CONVERSIONS[(self._space[2], profile)][rgb]
        _FakeManagedColor.conversions.append((self._space[2], profile))
        self._native = [b / 255.0, g / 255.0, r / 255.0, 1.0]
        self._space = (model, depth, profile)
        return True

    def colorForCanvas(self, _canvas):
        raise AssertionError("colorForCanvas converts for the monitor")

    @staticmethod
    def fromQColor(_qcolor, _canvas):
        raise AssertionError("fromQColor reads through the display converter")


def _managed(hex_rgb, profile):
    m = _FakeManagedColor("RGBA", "U8", profile)
    r, g, b = (int(hex_rgb[i:i + 2], 16) for i in (1, 3, 5))
    m.setComponents([b / 255.0, g / 255.0, r / 255.0, 1.0])
    return m


class _FakeView:
    def __init__(self, fg):
        self.fg = fg
        self.writes = []

    def foregroundColor(self):
        return self.fg

    def backgroundColor(self):
        return self.fg

    def setForeGroundColor(self, managed):
        self.writes.append(managed)
        self.fg = managed

    def canvas(self):
        return object()


def _fake_krita(monkeypatch, view, layer_profile=_SRGB, doc_profile=_SRGB):
    """Install a `krita` module for the duration of one test only.

    Lumina imports krita inside the methods that talk to Krita, so swapping
    the module here never changes the docker's base class for other tests.
    Returns the active layer so a test can switch its profile.
    """
    import types
    layer = types.SimpleNamespace(profile=layer_profile)
    layer.colorModel = lambda: "RGBA"
    layer.colorProfile = lambda: layer.profile
    doc = types.SimpleNamespace(colorModel=lambda: "RGBA",
                                colorProfile=lambda: doc_profile,
                                activeNode=lambda: layer)
    window = types.SimpleNamespace(activeView=lambda: view)
    app = types.SimpleNamespace(activeDocument=lambda: doc,
                                activeWindow=lambda: window,
                                actions=lambda: [])
    fake = types.ModuleType("krita")
    fake.Krita = types.SimpleNamespace(instance=lambda: app)
    fake.ManagedColor = _FakeManagedColor
    monkeypatch.setitem(sys.modules, "krita", fake)
    _FakeManagedColor.conversions = []
    from Lumina.sphere_docker import SphereDocker
    SphereDocker._CONVERT_CACHE.clear()
    return layer


def _space_docker():
    d = _switch_docker()
    d._ext_render_timer = _NoopTimer()
    d._save_timer = _NoopTimer()
    d._space_profile = _SRGB
    d._sync_guard = False
    return d


@requires_qt
def test_krita_colour_is_read_in_working_space(monkeypatch):
    """Lumina reads the colour Krita paints, not a foreign profile's numbers.

    Typing into the Specific Color Selector over a Display P3 layer stores a
    P3 foreground; on an sRGB layer that colour is sRGB #7d2e57, not the raw
    #743356 Lumina used to adopt.
    """
    _app_or_skip()
    d = _space_docker()
    view = _FakeView(_managed("#743356", _SRGB))
    _fake_krita(monkeypatch, view)
    # Already in the working profile: used as stored, with no identity
    # conversion that could round by one and look like a new pick.
    assert d._current_krita_rgb() == (0x74, 0x33, 0x56)
    assert _FakeManagedColor.conversions == []
    view.fg = _managed("#743356", _P3)
    assert d._current_krita_rgb() == (0x7d, 0x2e, 0x57)
    assert _FakeManagedColor.conversions == [(_P3, _SRGB)]
    # Converted on a copy: Krita's own colour is untouched.
    assert view.fg.colorProfile() == _P3


@requires_qt
def test_krita_pick_replaces_base_and_never_writes_back(monkeypatch):
    """Krita's colour replaces the base; Lumina must not rewrite Krita's.

    The earlier colour-space patch re-sent every foreign-profile pick to
    Krita as sRGB, which changed the user's brush to a different colour.
    """
    _app_or_skip()
    d = _space_docker()
    d._on_target_changed("base")
    view = _FakeView(_managed("#743356", _P3))
    _fake_krita(monkeypatch, view)
    d._sampler_baseline = (0, 0, 0)
    d._poll_sampler()               # what Krita 5 actually fires on
    d._apply_external_color()       # the throttled apply
    d._apply_external_final()       # stream end
    assert d._targets["base"].name() == "#7d2e57"
    assert view.writes == [], "Lumina wrote to Krita's foreground"
    assert view.fg.colorProfile() == _P3


@requires_qt
def test_lumina_follows_the_active_layer_space(monkeypatch):
    """On a Display P3 layer Lumina shows the numbers Krita's selector shows.

    The selector is locked to the current layer's colour space by default:
    sRGB #0102bd reads #0102b5 over a P3 layer. Switching layers rewrites
    Lumina's numbers -- same colour -- and is not mistaken for a pick.
    """
    _app_or_skip()
    d = _space_docker()
    from PyQt5.QtGui import QColor
    d._targets["base"] = QColor("#7d2e57")
    view = _FakeView(_managed("#0102bd", _SRGB))
    layer = _fake_krita(monkeypatch, view)
    noted = []
    d._note_external_color = lambda cur, full: noted.append(cur)
    d._sampler_baseline = d._current_krita_rgb()
    assert d._sampler_baseline == (0x01, 0x02, 0xbd)

    layer.profile = _P3              # the user selects a P3 reference layer
    d._poll_sampler()
    assert d._space_profile == _P3
    assert d._targets["base"].name() == "#743356"      # same colour, P3 numbers
    assert d._current_krita_rgb() == (0x01, 0x02, 0xb5)  # matches the selector
    assert noted == [], "a layer switch was read as a new pick"
    assert view.writes == []

    layer.profile = _SRGB            # and back again
    d._poll_sampler()
    assert d._targets["base"].name() == "#7d2e57"
    assert noted == []


@requires_qt
def test_display_and_sphere_picks_convert_between_spaces(monkeypatch):
    """Swatches and the sphere are painted in sRGB for the screen, and a
    pick off the sphere goes back to Krita in the layer's numbers."""
    _app_or_skip()
    from PyQt5.QtGui import QColor
    d = _space_docker()
    view = _FakeView(_managed("#000000", _P3))
    _fake_krita(monkeypatch, view, layer_profile=_P3)
    d._space_profile = _P3
    d._targets["base"] = QColor("#0102b5")
    assert d._to_display(d._targets["base"]).name() == "#0102bd"
    d._sync_target_dots()
    assert d._target_dots["base"]._color.name() == "#0102bd"
    # A click on the sphere samples a screen (sRGB) pixel.
    d._on_sphere_brush(QColor("#0102bd"))
    assert d._chosen_color.name() == "#0102b5"
    sent = view.writes[-1]
    assert sent.colorProfile() == _P3
    assert [round(v * 255) for v in sent.components()[:3]] == [0xb5, 0x02, 0x01]


@requires_qt
def test_send_to_krita_builds_colour_in_working_profile(monkeypatch):
    """A Lumina colour reaches Krita exactly, independent of the monitor."""
    _app_or_skip()
    from PyQt5.QtGui import QColor
    d = _space_docker()
    view = _FakeView(_managed("#000000", _SRGB))
    _fake_krita(monkeypatch, view)
    d._send_to_krita(QColor("#e94f37"))
    assert len(view.writes) == 1
    sent = view.writes[0]
    assert sent.colorProfile() == _SRGB
    # Native order: B, G, R.
    assert [round(v * 255) for v in sent.components()[:3]] == [0x37, 0x4f, 0xe9]
    # The echo is baselined, so the poll does not re-derive from it.
    assert d._sampler_baseline == (0xe9, 0x4f, 0x37)


@requires_qt
def test_layer_switch_after_pick_keeps_the_brush_colour(monkeypatch):
    """A pick made over a P3 layer still equals the brush on an sRGB layer.

    Converting Lumina's 8-bit copy back drifted (#4ba593 -> #05a892); the
    pick is re-expressed from Krita's own copy of the colour instead.
    """
    _app_or_skip()
    d = _space_docker()
    d._on_target_changed("base")
    view = _FakeView(_managed("#0102bd", _SRGB))
    layer = _fake_krita(monkeypatch, view, layer_profile=_P3)
    d._poll_sampler()                 # the layer switch: no pick
    view.fg = _managed("#00a893", _SRGB)
    d._poll_sampler()                 # the eyedropper pick
    d._apply_external_color()
    assert d._targets["base"].name() == "#4ba593"   # matches the selector
    layer.profile = _SRGB
    d._poll_sampler()
    assert d._targets["base"].name() == "#00a893"   # equals the brush, no drift


# --- Derivation against the reference lighting app ---------------------------
# Shadow / light the reference app produced for each base, from the Ref Tester
# document (tools/reference_calibration.py holds the full data). Values are the
# screenshots' Display P3 numbers, which is what Lumina works in on those layers.
_REFERENCE_TRIPLES = [
    ("purple 743356", "#743356", "#2b0850", "#c25f42"),
    ("red ff0000", "#ff0000", "#a20003", "#ff8566"),
    ("green 00e700", "#00e700", "#00a793", "#ffffff"),
    ("blue dots", "#0000b5", "#01002b", "#9b31b0"),
    ("blue 1a50b2", "#1a50b2", "#01255f", "#b56ac7"),
    ("green 9fbd24", "#9fbd24", "#018c48", "#ffe2c2"),
]


def _ok_distance(a_hex, b_hex):
    """OKLab distance x100 (2 = just noticeable, 10 = clearly different)."""
    import math
    from Lumina import derivation
    rgb = lambda h: tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))
    return 100.0 * math.dist(derivation._rgb_to_oklab(rgb(a_hex)),
                             derivation._rgb_to_oklab(rgb(b_hex)))


@requires_qt
def test_pick_derives_the_reference_lighting():
    """A base pick derives light and shadow close to the reference app's.

    Driven through the docker, so the wiring is covered, not only the model.
    The old HSV rule matched only the purple it was fitted to (blue light
    #6747ff for #a32bae, green shadow #367409 for #00a793).
    """
    _app_or_skip()
    from PyQt5.QtGui import QColor
    d = _switch_docker()
    errors = []
    for label, base, shadow, light in _REFERENCE_TRIPLES:
        d._distribute_from(QColor(base))
        assert d._targets["base"].name() == base
        es = _ok_distance(d._targets["shadow"].name(), shadow)
        el = _ok_distance(d._targets["light"].name(), light)
        assert es < 7.0 and el < 7.0, (label, d._targets["shadow"].name(),
                                       d._targets["light"].name(), es, el)
        errors += [es, el]
    assert sum(errors) / len(errors) < 3.0, errors


@requires_qt
def test_derived_hues_follow_the_reference_directions():
    """Lights swing warm, shadows lean cool -- except warm shadows stay warm."""
    _app_or_skip()
    from PyQt5.QtGui import QColor
    from Lumina import derivation
    d = _switch_docker()

    def hue(c):
        return derivation.to_oklch((c.red(), c.green(), c.blue()))[2]

    def turn(a, b):
        return (b - a + 180.0) % 360.0 - 180.0

    d._distribute_from(QColor("#1a50b2"))           # blue: light toward magenta
    assert turn(hue(QColor("#1a50b2")), hue(d._targets["light"])) > 40
    d._distribute_from(QColor("#00e700"))           # green: shadow toward teal
    assert turn(hue(QColor("#00e700")), hue(d._targets["shadow"])) > 20
    d._distribute_from(QColor("#ea6218"))           # orange: shadow stays warm
    assert abs(turn(hue(QColor("#ea6218")), hue(d._targets["shadow"]))) < 5
    d._distribute_from(QColor("#808080"))           # grey derives greys
    for key in ("light", "shadow"):
        assert d._targets[key].getHsvF()[1] < 0.02, (key, d._targets[key].name())


@requires_qt
def test_reset_restarts_from_krita_colour(monkeypatch):
    """Reset keeps Lumina in step with Krita instead of the default base.

    Resetting to #cd5c5c while the brush stayed green meant eyedropping that
    same green again changed nothing in Krita, so Lumina never saw a pick.
    """
    _app_or_skip()
    d = _space_docker()
    view = _FakeView(_managed("#0102bd", _SRGB))
    _fake_krita(monkeypatch, view)
    d._save_settings = lambda: None
    d._on_settings_reset()
    assert d._targets["base"].name() == "#0102bd"
    assert d._sampler_baseline == (0x01, 0x02, 0xbd)
    assert view.writes == []


@requires_qt
def test_conversion_is_not_cached_before_the_profile_loads(monkeypatch):
    """A profile Krita has not loaded yet must not poison the cache.

    At startup Lumina draws its saved P3 targets before the document that
    embeds Display P3 has loaded; Krita then falls back to its default
    profile and the conversion is a no-op. Caching that drew the sphere from
    raw P3 numbers -- visibly paler -- until Krita restarted.
    """
    _app_or_skip()
    from PyQt5.QtGui import QColor
    from Lumina.sphere_docker import SphereDocker
    view = _FakeView(_managed("#000000", _SRGB))
    _fake_krita(monkeypatch, view)
    loaded = {"p3": False}
    real_init = _FakeManagedColor.__init__

    def init(self, model, depth, profile):
        if profile == _P3 and not loaded["p3"]:
            profile = _SRGB          # Krita's fallback for an unknown profile
        real_init(self, model, depth, profile)
    monkeypatch.setattr(_FakeManagedColor, "__init__", init)

    before = SphereDocker._convert_qcolor(QColor("#0102b5"), _P3, _SRGB)
    assert before.name() == "#0102b5"           # cannot convert yet...
    assert SphereDocker._CONVERT_CACHE == {}    # ...and nothing was cached
    loaded["p3"] = True                         # the document loads
    after = SphereDocker._convert_qcolor(QColor("#0102b5"), _P3, _SRGB)
    assert after.name() == "#0102bd"


@requires_qt
def test_hex_box_edits_the_selected_target():
    """EDIT SHADE / EDIT BASE / EDIT LIGHT: the hex box follows the target.

    It shows the selected target, its label names it, and typing a colour
    changes that target: base derives light and shadow, shadow or highlight
    take the colour as typed. A sphere click never changes the box.
    """
    _app_or_skip()
    from PyQt5.QtGui import QColor
    d = _switch_docker()
    d._send_to_krita = lambda color: None
    d._on_target_changed("base")
    assert d._hex_lock_btn.text() == "EDIT BASE"
    assert d._hex_current.text() == d._targets["base"].name()

    d._on_target_changed("shadow")
    assert d._hex_lock_btn.text() == "EDIT SHADE"
    assert d._hex_current.text() == d._targets["shadow"].name()
    base_before = d._targets["base"].name()
    d._set_hex_locked(False)
    assert d._hex_lock_btn.text() == "DONE"
    d._hex_current.setText("#123456")
    d._commit_hex("current")
    assert d._targets["shadow"].name() == "#123456"
    assert d._targets["base"].name() == base_before
    d._set_hex_locked(True)

    d._on_target_changed("light")
    assert d._hex_lock_btn.text() == "EDIT LIGHT"
    assert d._hex_current.text() == d._targets["light"].name()
    shown = d._hex_current.text()
    d._on_sphere_brush(QColor("#ff00ff"))     # a sphere click: brush only
    d._update_preview()
    assert d._hex_current.text() == shown


@requires_qt
def test_active_swatch_matches_the_selected_target():
    """The swatch above the hex box shows the same colour as the box.

    It used to show the base (or the last sphere click) while the box showed
    the selected shade or highlight, so the two disagreed.
    """
    _app_or_skip()
    from PyQt5.QtGui import QColor
    d = _switch_docker()
    d._send_to_krita = lambda color: None
    for key in ("shadow", "base", "light"):
        d._on_target_changed(key)
        want = d._targets[key].name()
        assert d._hex_current.text() == want
        assert want in d._sw_current.styleSheet(), (key, d._sw_current.styleSheet())
    d._on_sphere_brush(QColor("#ff00ff"))          # sphere click: brush only
    d._update_preview()
    assert d._targets["light"].name() in d._sw_current.styleSheet()


@requires_qt
def test_canvas_pick_of_unchanged_colour_still_lands(monkeypatch):
    """Re-picking the colour Krita already holds still reaches the target.

    Pick green into the base, switch to the shade, pick the same green: Krita's
    colour does not change, so the colour watcher sees nothing. The release of
    the Color Sampler click is the pick, and that is what lands it now.
    """
    _app_or_skip()
    from PyQt5.QtCore import QPointF, Qt, QEvent
    from PyQt5.QtGui import QMouseEvent
    from PyQt5.QtWidgets import QWidget
    from Lumina.sphere_docker import _CanvasPickFilter
    d = _space_docker()
    view = _FakeView(_managed("#00eb00", _SRGB))
    _fake_krita(monkeypatch, view)
    d._on_target_changed("base")
    d._sampler_baseline = (0, 0xeb, 0)        # the watcher already saw green
    d._on_target_changed("shadow")
    shade_before = d._targets["shadow"].name()

    class KisOpenGLCanvas2(QWidget):          # stands in for Krita's canvas
        pass
    host = QWidget(); canvas = KisOpenGLCanvas2(host)
    pick = _CanvasPickFilter.instance(); pick.attach(d)
    assert pick.watch(host) == 1 and pick.watch(host) == 0   # once only

    queued = []
    monkeypatch.setattr("Lumina.sphere_docker.QTimer.singleShot",
                        lambda ms, fn: queued.append(fn))
    release = QMouseEvent(QEvent.MouseButtonRelease, QPointF(5, 5),
                          Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
    d._color_sampler_active = lambda: False
    pick.eventFilter(canvas, release)         # a brush stroke: ignored
    assert queued == []
    d._color_sampler_active = lambda: True
    assert pick.eventFilter(canvas, release) is False   # observed, never eaten
    for fn in queued:
        fn()
    d._apply_external_color()
    assert shade_before != "#00eb00"
    assert d._targets["shadow"].name() == "#00eb00"
    assert view.writes == []
    # Ctrl quick-sample counts too, whatever the tool.
    queued.clear(); d._color_sampler_active = lambda: False
    ctrl = QMouseEvent(QEvent.MouseButtonRelease, QPointF(5, 5),
                       Qt.LeftButton, Qt.NoButton, Qt.ControlModifier)
    pick.eventFilter(canvas, ctrl)
    assert len(queued) == 1
    pick.detach()


@requires_qt
def test_sliders_are_perceptual_like_the_reference():
    """Hue / Sat / Light read OKLCH: #00e700 sits at 142 / 100 / 80, where the
    reference app's thumbs sit; moving one slider leaves the other two."""
    _app_or_skip()
    from PyQt5.QtGui import QColor
    d = _switch_docker()
    d._on_target_changed("base")
    d._targets["base"] = QColor("#00e700")
    d._sync_sliders_from_state()
    assert (d.hue_row.slider.value(), d.saturation_row.slider.value(),
            d.light_row.slider.value()) == (142, 100, 80)
    d._set_active_lch(L=0.6)                  # darker, same hue, still vivid
    h, c, L = d._lch_of("base")
    assert abs(L - 0.6) < 0.01 and abs(h - 142) < 2 and c > 0.97
    d._set_active_lch(c=0.0)                  # to grey: hue is remembered
    assert d._lch_of("base")[0] == h


@requires_qt
def test_hue_track_is_a_full_strength_rainbow():
    """The hue track paints each hue at its most saturated colour, like
    Krita's hue strip, whatever the base's own lightness: a fixed lightness
    left red and blue pastel."""
    _app_or_skip()
    from PyQt5.QtGui import QColor
    d = _switch_docker()
    d._on_target_changed("base")
    d._targets["base"] = QColor("#00e700")
    d._sync_sliders_from_state()
    d._sync_gradient_tracks()
    stops = d.hue_row.slider._gradient_stops
    assert len(stops) >= 13
    for _, c in stops:
        assert max(c.red(), c.green(), c.blue()) >= 240, c.name()
        assert min(c.red(), c.green(), c.blue()) <= 20, c.name()


@requires_qt
def test_sampler_lemon_rests_on_the_surface():
    """The sampler is a lemon resting on the sphere: at its natural tilt and
    full width at the centre, turned along the curve and foreshortened toward
    the silhouette, filled with the colour under it, its edges leaning toward
    that colour."""
    _app_or_skip()
    from PyQt5.QtGui import QColor
    from Lumina.color_controls import ColorSampler
    lemon = ColorSampler()
    lemon.set_surface(0.0, 0.0)
    turn, squash = lemon._shape()
    assert squash == 1.0 and abs(turn - lemon.REST_ANGLE) < 1e-6
    lemon.set_surface(0.0, 0.9)                    # near the bottom edge
    turn, squash = lemon._shape()
    assert min(abs(turn), abs(abs(turn) - 180.0)) < 1e-6   # lies along the rim
    assert lemon.MIN_SQUASH < squash < 0.95
    lemon.set_surface(3.0, 0.0)                    # outside: clamped to the rim
    assert lemon._shape()[1] == lemon.MIN_SQUASH
    lemon.set_color(QColor("#00e700"))
    assert lemon._color.name() == "#00e700"
    # Its edge is its own colour shifted for contrast, never a black outline:
    # darker on a light colour, lighter on a dark one, same hue family.
    light, dark = QColor("#9be86a"), QColor("#0b3d1c")
    e_light, e_dark = ColorSampler.edge_color(light), ColorSampler.edge_color(dark)
    assert e_light.lightness() < light.lightness() and e_light.green() > e_light.red()
    assert e_dark.lightness() > dark.lightness() and e_dark.green() > e_dark.red()
    for e in (e_light, e_dark, ColorSampler.edge_color(QColor("#202020"))):
        assert e.lightness() > 25, e.name()        # never black


@requires_qt
def test_mouse_pointer_hides_while_the_sampler_shows():
    """Over the sphere the lemon marks the spot, so the arrow is hidden; it
    comes back off the sphere, on leaving, and when the sampler is off."""
    _app_or_skip()
    from PyQt5.QtCore import QPointF as _QP, Qt
    from PyQt5.QtGui import QImage as _QI, QColor as _QC
    from Lumina.sphere_widget import SphereWidget
    from Lumina.color_controls import ColorSampler
    w = SphereWidget()
    img = _QI(200, 200, _QI.Format_ARGB32)
    img.fill(_QC(200, 100, 50))
    w.set_image(img)
    w.set_preview_widget(ColorSampler())
    w.resize(200, 200)
    w._sample(_QP(100.0, 100.0))
    assert w.cursor().shape() == Qt.BlankCursor
    w._sample(_QP(260.0, 100.0))                    # off the sphere (hovering)
    assert w.cursor().shape() != Qt.BlankCursor
    w._sample(_QP(100.0, 100.0))
    from PyQt5.QtCore import QPoint as _P
    w._cursor_local = lambda: _P(100, 400)          # left, far from the sphere
    w.leaveEvent(None)
    assert w.cursor().shape() != Qt.BlankCursor and not w._outside.active
    w.set_show_pointer(False)
    w._sample(_QP(100.0, 100.0))
    assert w.cursor().shape() != Qt.BlankCursor     # nothing else would mark it


@requires_qt
def test_lemon_pops_on_click_holds_a_little_larger_and_settles_on_release():
    """A press swells the lemon, it stays a little larger while held, and it
    settles back to its size on release."""
    _app_or_skip()
    import time
    from PyQt5.QtCore import QAbstractAnimation
    from PyQt5.QtWidgets import QApplication
    from Lumina.color_controls import ColorSampler

    def run(lemon):
        peak, end = lemon._pop, time.time() + 2.0
        while time.time() < end:
            QApplication.processEvents()
            peak = max(peak, lemon._pop)
            if lemon._pop_anim.state() == QAbstractAnimation.Stopped:
                break
            time.sleep(0.005)
        return peak

    lemon = ColorSampler()
    assert lemon._pop == 1.0
    lemon.set_active(True)                          # press
    assert run(lemon) > 1.15
    assert lemon._pop == lemon.HOLD_SCALE           # held: a little larger
    lemon.set_active(False)                         # release
    run(lemon)
    assert lemon._pop == 1.0


@requires_qt
def test_lemon_sweats_when_held_too_long_and_stops_on_release():
    _app_or_skip()
    from Lumina.color_controls import ColorSampler
    lemon = ColorSampler()
    lemon.set_active(True)
    assert lemon._sweat_timer.isActive() and not lemon._sweating
    assert lemon._sweat_timer.interval() == lemon.SWEAT_AFTER_MS
    lemon._start_sweat()                            # held long enough
    for _ in range(20):
        lemon._step_drops()
    assert len(lemon._drops) >= 1
    lemon.set_active(False)                         # let go: no new drops
    assert not lemon._sweating and not lemon._sweat_timer.isActive()
    for _ in range(40):
        lemon._step_drops()
    assert lemon._drops == [] and not lemon._tick.isActive()


@requires_qt
def test_hover_keeps_the_lemon_past_the_widget_bounds():
    """Hover moves stop at the widget, which ends 20px past the sphere on the
    axes; the tracker keeps the lemon until the pointer is EDGE_GLIDE out."""
    _app_or_skip()
    from PyQt5.QtCore import QPoint as _P
    from PyQt5.QtGui import QImage as _QI, QColor as _QC
    from Lumina.sphere_widget import SphereWidget
    from Lumina.color_controls import ColorSampler
    w = SphereWidget()
    img = _QI(200, 200, _QI.Format_ARGB32)
    img.fill(_QC(200, 100, 50))
    w.set_image(img)
    w.set_preview_widget(ColorSampler())
    w.resize(200, 200)
    w._outside.start()
    try:
        w._follow_outside(_P(100, 212))             # 32px below the rim
        assert w._pointer is not None and w._outside.active
        w._follow_outside(_P(100, 100))             # back over the widget
        assert not w._outside.active
        w._outside.start()
        w._follow_outside(_P(100, 280))             # 100px out: let go
        assert w._pointer is None and not w._outside.active
    finally:
        w._outside.stop()


@requires_qt
def test_sticky_distance_setting_drives_the_edge_and_is_saved():
    _app_or_skip()
    from Lumina.sphere_widget import EDGE_GLIDE
    d = _switch_docker()
    assert d._sphere.edge_glide == EDGE_GLIDE
    panel = d._settings_panel
    seen = []
    panel.bind(lambda st: (seen.append(st), d._on_settings_changed(st)), None)
    panel.sticky.setValue(70)
    assert seen and seen[-1]["sticky"] == 70
    assert d._sphere.edge_glide == 70.0
    assert d._collect_settings()["sticky"] == 70
    d._sphere.set_edge_glide(25)
    d._sync_settings_panel()
    assert panel.sticky.value() == 25


@requires_qt
def test_presets_toggle_off_and_restore_the_lighting_before_them():
    """A second click on the lit preset switches it off and restores the
    lighting from before; hopping between presets keeps that original."""
    _app_or_skip()
    d = _switch_docker()
    d._save_settings = lambda: None                 # keep the real settings file out
    eng = d.processor.engine
    before = d._preset_snapshot()
    assert not any(b.isChecked() for b in d._preset_btns)
    d._on_preset_clicked("gloss", True)
    assert d._active_preset == "gloss"
    assert [b.key for b in d._preset_btns if b.isChecked()] == ["gloss"]
    assert d._preset_snapshot() != before
    d._on_preset_clicked("matte", True)             # hop to another preset
    assert [b.key for b in d._preset_btns if b.isChecked()] == ["matte"]
    d._on_preset_clicked("matte", True)             # and switch it off
    assert d._active_preset is None
    assert not any(b.isChecked() for b in d._preset_btns)
    after = d._preset_snapshot()
    assert after == before, {k: (before[k], after[k]) for k in before if before[k] != after[k]}


@requires_qt
def test_settings_open_beside_the_docker_on_the_canvas_side():
    """The popup sits right next to the docker, on the side facing the
    canvas, inside Krita's window on the docker's own monitor."""
    _app_or_skip()
    from PyQt5.QtCore import QPoint, QRect
    d = _switch_docker()
    d.resize(300, 900)
    d._settings_bounds = lambda: QRect(1920, 0, 1920, 1080)     # second monitor
    d._gear_btn.mapToGlobal = lambda p: QPoint(p.x() + 1930, p.y() + 40)
    # Docker on the left of Krita's window: popup to its right.
    d.mapToGlobal = lambda p: QPoint(p.x() + 1920, p.y())
    pos = d._settings_position(260, 400)
    assert pos == QPoint(1920 + 300 + 4, 40), pos
    # Docker on the right: popup to its left, still on that monitor.
    d.mapToGlobal = lambda p: QPoint(p.x() + 1920 + 1620, p.y())
    pos = d._settings_position(260, 400)
    assert pos == QPoint(1920 + 1620 - 4 - 260, 40), pos
    # Tall popup near the bottom is pulled up to fit.
    d._gear_btn.mapToGlobal = lambda p: QPoint(p.x() + 1930, p.y() + 900)
    assert d._settings_position(260, 400).y() == 1080 - 400


@requires_qt
def test_target_row_has_captions_with_the_selected_one_lit():
    _app_or_skip()
    d = _switch_docker()
    caps = d._target_caps
    assert {k: c.text() for k, c in caps.items()} == {"shadow": "Shade", "base": "Base", "light": "Light"}
    d._on_target_changed("light")
    assert "bold" in caps["light"].styleSheet() and "bold" not in caps["base"].styleSheet()


@requires_qt
def test_header_swatches_say_before_and_now_on_a_pill():
    """White text on a dark translucent pill, readable on any swatch."""
    _app_or_skip()
    d = _switch_docker()
    assert d._cap_prev.text() == "Before" and d._cap_now.text() == "Now"
    qss = d._cap_now.styleSheet()
    assert "color: #ffffff" in qss and "background-color: rgba(10, 12, 18" in qss
    assert "border-radius" in qss


@requires_qt
def test_recent_picks_collect_sphere_picks_and_paint_with_them_again():
    _app_or_skip()
    from PyQt5.QtGui import QColor
    d = _switch_docker()
    d._save_settings = lambda: None
    sent = []
    d._send_to_krita = lambda c: sent.append(QColor(c).name())
    d._recent.set_colors([])
    for c in ("#112233", "#445566", "#778899"):      # one drag: many moves
        d._on_sphere_brush(QColor(c))
    d._commit_recent()                                 # the drag settles
    assert [c.name() for c in d._recent.colors()] == [d._chosen_color.name()]
    d._on_sphere_brush(QColor("#aa0000")); d._commit_recent()
    first = d._recent.colors()[1]
    d._on_recent_pick(first)                           # click an older chip
    assert sent[-1] == first.name()
    assert d._recent.colors()[0].name() == first.name()   # moved to the front
    assert len(d._recent.colors()) == 2                    # no duplicate
    assert d._collect_settings()["recent"].split(",")[0] == first.name()


@requires_qt
def test_lemon_shows_the_hovered_hex():
    _app_or_skip()
    from PyQt5.QtGui import QColor
    d = _switch_docker()
    d._on_sphere_hover(QColor("#336699"))
    assert d.cylinder._label == d._from_display(QColor("#336699")).name()
    d._on_sphere_hover(None)
    assert d.cylinder._label == ""


@requires_qt
def test_sphere_grows_with_the_panel_and_stays_square():
    """The sphere's holder takes the width between the preset columns; its
    height (and the sphere's) follows that width, clamped to the range."""
    _app_or_skip()
    from PyQt5.QtCore import QSize
    from PyQt5.QtGui import QResizeEvent
    from Lumina.sphere_docker import SPHERE_SIZE, SPHERE_MAX
    d = _switch_docker()
    holder, sphere = d._sphere_holder, d._sphere
    seen = []
    for width in (232, 520, 120, 232):
        holder.resize(width, holder.height())
        d.eventFilter(holder, QResizeEvent(QSize(width, holder.height()), QSize()))
        seen.append((holder.height(), sphere.height()))
    clamp = lambda w: max(SPHERE_SIZE, min(SPHERE_MAX, w))
    assert seen == [(clamp(w), clamp(w)) for w in (232, 520, 120, 232)], seen
    assert holder.minimumWidth() == SPHERE_SIZE and holder.maximumWidth() == SPHERE_MAX


@requires_qt
def test_selecting_a_target_plays_sparkles_round_its_dot():
    _app_or_skip()
    d = _switch_docker()
    played = []
    d._sparkle.play = lambda color, centre: played.append((color.name(), centre))
    d._select_target("light")
    assert d._active_target == "light"
    assert played and played[-1][0] == d._to_display(d._targets["light"]).name()
    d._on_target_changed("shadow")                  # selection from code: no burst
    assert len(played) == 1


@requires_qt
def test_recent_picks_menu_pin_remove_clear_and_double_click_to_set_the_target():
    _app_or_skip()
    from PyQt5.QtCore import Qt, QEvent
    from PyQt5.QtGui import QColor, QMouseEvent
    d = _switch_docker()
    d._save_settings = lambda: None
    d._send_to_krita = lambda c: None
    r = d._recent
    r.resize(260, r.height())
    r.set_colors(["#112233", "#445566", "#778899"])
    menus = []
    r._show_menu = lambda i, pos: menus.append(i)
    chip = r._chip_rects()[1].center()
    r.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, chip, Qt.RightButton, Qt.RightButton, Qt.NoModifier))
    assert menus == [1]                                                  # right-click: the menu
    r.toggle_pin(2)                                                      # pin #778899
    assert [c.name() for c in r.colors()] == ["#778899", "#112233", "#445566"]
    r.remove(2)
    assert [c.name() for c in r.colors()] == ["#778899", "#112233"]
    for i in range(r.LIMIT + 2):                                         # pins never pushed out
        r.add(QColor(i * 20, 40, 60))
    assert r.colors()[0].name() == "#778899" and len(r.colors()) == r.LIMIT
    assert r.saved().startswith("*#778899,")
    d._on_target_changed("light")
    chip = r._chip_rects()[0].center()
    r.mouseDoubleClickEvent(QMouseEvent(QEvent.MouseButtonDblClick, chip, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    assert d._targets["light"].name() == "#778899"                       # double-click sets target
    r.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, r._clear_rect().center(), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    assert [c.name() for c in r.colors()] == ["#778899"]                 # Clear keeps pins
    r2 = type(r)()
    r2.set_colors(r.saved().split(","))
    assert r2.is_pinned("#778899")                                       # pins survive save/load


@requires_qt
def test_lemon_countdown_ring_fills_while_held_then_gives_way_to_sweat():
    _app_or_skip()
    import time
    from Lumina.color_controls import ColorSampler
    lemon = ColorSampler()
    assert lemon._hold_progress() == 0.0
    lemon.set_active(True)
    lemon._held_at = time.monotonic() - lemon.SWEAT_AFTER_MS / 2000.0      # half way
    assert 0.4 < lemon._hold_progress() < 0.6
    lemon._start_sweat()
    assert lemon._hold_progress() == 0.0                                  # sweating now
    lemon.set_active(False)
    assert lemon._hold_progress() == 0.0


@requires_qt
def test_arrow_keys_nudge_the_sampler_and_enter_picks():
    _app_or_skip()
    from PyQt5.QtCore import Qt, QEvent, QPointF
    from PyQt5.QtGui import QImage as _QI, QColor as _QC, QKeyEvent
    from Lumina.sphere_widget import SphereWidget
    w = SphereWidget()
    img = _QI(200, 200, _QI.Format_ARGB32)
    img.fill(_QC(200, 100, 50))
    w.set_image(img)
    w.resize(200, 200)
    picked = []
    w.set_brush_callback(lambda c: picked.append(c.name()))
    w._sample(QPointF(100.0, 100.0))
    start = QPointF(w._pointer)                    # snapped to the pixel centre
    key = lambda k, mod=Qt.NoModifier: w.keyPressEvent(QKeyEvent(QEvent.KeyPress, k, mod))
    key(Qt.Key_Right)                              # 1 px
    key(Qt.Key_Down, Qt.ShiftModifier)             # 5 px
    assert abs(w._key_pos.x() - start.x() - 1.0) < 1e-6
    assert abs(w._key_pos.y() - start.y() - 5.0) < 1e-6
    key(Qt.Key_Return)
    assert picked == ["#c86432"]


@requires_qt
def test_shift_click_on_the_sphere_sets_the_selected_target():
    """Shift-click is the one sphere click that sets a target (once; a
    Shift-drag does not keep re-targeting); a plain click only sets the brush."""
    _app_or_skip()
    from PyQt5.QtCore import Qt, QEvent, QPoint
    from PyQt5.QtGui import QImage as _QI, QColor as _QC, QMouseEvent
    from Lumina.sphere_widget import SphereWidget
    w = SphereWidget()
    img = _QI(200, 200, _QI.Format_ARGB32)
    img.fill(_QC(10, 200, 90))
    w.set_image(img)
    w.resize(200, 200)
    brush, target = [], []
    w.set_brush_callback(lambda c: brush.append(c.name()))
    w.set_target_callback(lambda c: target.append(c.name()))
    press = lambda mod: w.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, QPoint(100, 100), Qt.LeftButton, Qt.LeftButton, mod))
    move = lambda: w.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, QPoint(110, 100), Qt.NoButton, Qt.LeftButton, Qt.ShiftModifier))
    release = lambda: w.mouseReleaseEvent(QMouseEvent(QEvent.MouseButtonRelease, QPoint(110, 100), Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
    press(Qt.ShiftModifier); move(); release()
    assert target == ["#0ac85a"] and brush == []
    press(Qt.NoModifier); release()
    assert target == ["#0ac85a"] and brush == ["#0ac85a"]


@requires_qt
def test_compact_panel_hides_captions_and_recent_picks():
    _app_or_skip()
    d = _switch_docker()
    d._save_settings = lambda: None
    d.show()
    d._set_compact(True)
    assert not d._recent.isVisible() and not d._cap_now.isVisible()
    assert not any(c.isVisible() for c in d._target_caps.values())
    assert not any(c.isVisible() for c in d._preset_caps + d.light_type_row.captions)
    assert d._collect_settings()["compact"] is True
    d._set_compact(False)
    assert d._recent.isVisible() and all(c.isVisible() for c in d._target_caps.values())
    d.hide()


@requires_qt
def test_target_undo_and_redo_step_through_settled_changes():
    _app_or_skip()
    from PyQt5.QtGui import QColor
    d = _switch_docker()
    d._save_settings = lambda: None
    d._send_to_krita = lambda c: None
    start = d._hist_state()
    assert not d._undo_btn.isEnabled()
    d._on_target_changed("light")
    d._assign_target(QColor("#ff8800")); d._hist_commit()           # step 1
    d._assign_target(QColor("#00ff88"))                              # step 2, still settling
    after2 = d._hist_state()
    d._undo_targets()                                                # settles, then undoes step 2
    assert d._targets["light"].name() == "#ff8800"
    d._undo_targets()
    assert d._hist_state() == start
    assert not d._undo_btn.isEnabled() and d._redo_btn.isEnabled()
    d._redo_targets(); d._redo_targets()
    assert d._hist_state() == after2
    d._undo_targets()
    d._assign_target(QColor("#123456")); d._hist_commit()            # a new change drops redo
    assert not d._redo_btn.isEnabled()


@requires_qt
def test_readout_shows_the_hovered_colour_in_slider_units():
    _app_or_skip()
    from PyQt5.QtGui import QColor
    d = _switch_docker()
    d._on_sphere_hover(QColor("#00e700"))
    text = d._readout.text()
    assert text.startswith("H ") and "S " in text and "V " in text and "," not in text


@requires_qt
def test_every_preset_matches_itself_and_colour_changes_keep_it_lit():
    """A lit preset stays lit through colour changes (a pick, a target
    edit) and goes off only when a lighting value it sets moves."""
    _app_or_skip()
    from PyQt5.QtGui import QColor
    from Lumina.sphere_docker import _PRESETS, SphereDocker
    d = _switch_docker()
    d._save_settings = lambda: None
    d._send_to_krita = lambda c: None
    real_rebuild = SphereDocker._rebuild_sphere.__get__(d)
    for preset in _PRESETS:
        d._on_preset_clicked(preset["key"], True)
        assert d._preset_matches(preset["key"]), preset["key"]
    d._on_preset_clicked("gloss", True)
    d._assign_target(QColor("#3366cc"))              # a colour change
    real_rebuild()
    assert d._active_preset == "gloss"
    d.processor.set_contrast(0.5)                    # a lighting change
    real_rebuild()
    assert d._active_preset is None and not any(b.isChecked() for b in d._preset_btns)


@requires_qt
def test_lit_preset_and_its_before_lighting_survive_a_restart(tmp_path, monkeypatch):
    _app_or_skip()
    from PyQt5.QtCore import QSettings
    import Lumina.sphere_docker as SD
    store = QSettings(str(tmp_path / "Lumina.ini"), QSettings.IniFormat)
    monkeypatch.setattr(SD, "_settings", lambda: store)
    d = _switch_docker()
    before = d._preset_snapshot()
    d._on_preset_clicked("matte", True)
    d._save_settings()
    assert store.value("active_preset") == "matte"
    d2 = _switch_docker()                            # a fresh panel loads it
    assert d2._active_preset == "matte"
    assert [b.key for b in d2._preset_btns if b.isChecked()] == ["matte"]
    d2._save_settings = lambda: None
    d2._on_preset_clicked("matte", True)             # switch it off
    assert d2._preset_snapshot() == before


@requires_qt
def test_krita_colour_changes_during_a_sphere_pick_are_never_adopted():
    """While the sphere is pressed, and briefly after a pick, a change of
    Krita's colour is Lumina's own pick coming back: it must not re-derive
    the targets (that changed the sphere under the pointer)."""
    _app_or_skip()
    import time
    from PyQt5.QtGui import QColor
    d = _switch_docker()
    d._send_to_krita = lambda c: None
    applied = []
    d._enter_external_preview = lambda: applied.append("preview")
    before = {k: v.name() for k, v in d._targets.items()}
    d._on_sphere_brush(QColor("#c08060"))            # a sphere pick
    d._note_external_color((192, 128, 96), True)     # its echo
    assert applied == []
    d._sphere._pointer_down = True                    # mid-drag, any time later
    d._last_sphere_pick = time.monotonic() - 5.0
    d._note_external_color((10, 20, 30), True)
    assert applied == []
    d._sphere._pointer_down = False                   # a real Krita change later
    d._note_external_color((10, 20, 30), True)
    assert applied == ["preview"]
    assert {k: v.name() for k, v in d._targets.items()} == before


@requires_qt
def test_a_krita_colour_replacing_a_target_says_so():
    _app_or_skip()
    d = _switch_docker()
    d._send_to_krita = lambda c: None
    d._on_target_changed("light")
    d._apply_sampled_color((171, 155, 124))
    assert d._readout.text() == "Light ← Krita colour #ab9b7c"
    d._clear_krita_cue()
    assert d._readout.text() == ""


@requires_qt
def test_presets_look_like_their_names():
    """Each preset reads as its description on an ordinary colour: Nocturne
    low key, Gloss a hot tight peak over Matte's none, Neon saturated."""
    _app_or_skip()
    from PyQt5.QtGui import QColor
    from Lumina.sphere_docker import _PRESETS
    d = _switch_docker()
    d._save_settings = lambda: None
    d._send_to_krita = lambda c: None
    d._sphere_render_size = 48
    d._on_target_changed("base")
    d._assign_target(QColor("#ff8a4e"))
    stats = {}
    for preset in _PRESETS:
        d._applying_preset = True
        d._apply_preset_values(preset["values"])
        d._applying_preset = False
        img = d._render_sphere()
        lum, sat, peak, n = 0.0, 0.0, 0.0, 0
        for y in range(48):
            for x in range(48):
                c = QColor(img.pixelColor(x, y))
                if c.alpha() < 200:
                    continue
                l = 0.2126 * c.redF() + 0.7152 * c.greenF() + 0.0722 * c.blueF()
                lum += l
                sat += c.hsvSaturationF()
                peak = max(peak, l)
                n += 1
        stats[preset["key"]] = (lum / n, sat / n, peak)
    assert stats["nocturne"][0] < 0.75 * stats["artistic"][0], stats
    assert stats["gloss"][2] > stats["matte"][2] + 0.1, stats
    assert stats["neon"][1] > stats["matte"][1] + 0.1, stats


@requires_qt
def test_gear_opens_on_every_real_click_fast_or_slow():
    """Only the press that closed the popup (Qt closes it on the press and
    hands that press straight to the gear) is kept from reopening it; any
    other click opens it, however slowly it is made."""
    _app_or_skip()
    import time
    from PyQt5.QtCore import QEvent, QPointF, Qt
    from PyQt5.QtGui import QMouseEvent
    from PyQt5.QtWidgets import QApplication
    d = _switch_docker()
    d.show()
    panel, gear = d._settings_panel, d._gear_btn
    c = QPointF(gear.width() / 2.0, gear.height() / 2.0)

    def click(hold=0.0):
        QApplication.sendEvent(gear, QMouseEvent(QEvent.MouseButtonPress, c, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
        if hold:
            time.sleep(hold)
        QApplication.sendEvent(gear, QMouseEvent(QEvent.MouseButtonRelease, c, Qt.LeftButton, Qt.NoButton, Qt.NoModifier))

    d._press_on_gear = lambda: False
    click()
    assert panel.isVisible()                         # opens
    d._press_on_gear = lambda: True                  # Qt closes it on a gear press...
    panel.hide()
    click(hold=0.8)                                  # ...and that press, released slowly
    assert not panel.isVisible()                     # does not reopen it
    d._press_on_gear = lambda: False
    time.sleep(0.2)
    click(hold=0.8)                                  # a separate slow click
    assert panel.isVisible()                         # opens
    click()                                          # closing with the gear itself
    assert not panel.isVisible()
    click()                                          # and straight back open
    assert panel.isVisible()
    panel.hide()
    d.hide()


@requires_qt
def test_letting_go_of_a_slider_ripples_from_its_knob():
    _app_or_skip()
    d = _switch_docker()
    d.show()
    played = []
    d._knob_ripple.play = lambda color, centre: played.append((color.name(), centre))
    slider = d.hue_row.slider
    slider.beganDrag.emit()
    slider.endedDrag.emit()
    assert len(played) == 1
    name, centre = played[0]
    assert name == slider.knob_color().name()
    assert centre == slider.mapTo(d._knob_ripple.parentWidget(), slider.knob_center())
    d._settings_panel.azimuth.endedDrag.emit()   # a slider in the popup: none
    assert len(played) == 1
    d.hide()


@requires_qt
def test_holding_a_slider_too_long_makes_its_knob_sweat():
    _app_or_skip()
    d = _switch_docker()
    d.show()
    slider = d.hue_row.slider
    em = d._slider_sweat
    slider.beganDrag.emit()
    assert d._slider_sweat_timer.isActive()
    assert d._slider_sweat_timer.interval() == d.SLIDER_SWEAT_AFTER_MS
    d._start_slider_sweat()                          # held long enough
    assert em.active and em.isVisible()
    for _ in range(25):
        em._step()
    knob = slider.mapTo(em.parentWidget(), slider.knob_center())
    assert em._drops and all(abs(dx[0] - knob.x()) < 60 for dx in em._drops)
    slider.endedDrag.emit()                          # let go: no new drops
    assert not em.active and not d._slider_sweat_timer.isActive()
    for _ in range(40):
        em._step()
    assert em._drops == [] and not em._tick.isActive()
    d.hide()


@requires_qt
def test_a_wheel_burst_on_a_slider_is_one_drag():
    """Wheel notches render at drag size: one burst is one drag, ending a
    moment after the last notch. Each notch used to pay for a full render
    (~140 ms), so a quick scroll queued seconds of them."""
    app = _app_or_skip()
    from PyQt5.QtCore import Qt, QPoint, QPointF
    from PyQt5.QtGui import QWheelEvent
    from PyQt5.QtTest import QTest
    d = _switch_docker()
    d.show()
    slider = d.hue_row.slider
    begins, ends = [], []
    slider.beganDrag.connect(lambda: begins.append(1))
    slider.endedDrag.connect(lambda: ends.append(1))
    smooth = d.processor.engine.smooth
    pos = QPointF(slider.width() / 2.0, 13.0)

    def notch():
        app.sendEvent(slider, QWheelEvent(pos, slider.mapToGlobal(pos.toPoint()), QPoint(0, 0),
                                          QPoint(0, 120), Qt.NoButton, Qt.NoModifier,
                                          Qt.NoScrollPhase, False))
    start = slider.value()
    for _ in range(5):
        notch()
    assert slider.value() != start
    assert len(begins) == 1 and not ends and d._slider_dragging and slider.wheeling
    assert not d._slider_sweat_timer.isActive()      # a wheel does not sweat
    played = []
    d._knob_ripple.play = lambda *a: played.append(a)
    slider._wheel_timer.stop()
    slider._wheel_timer.timeout.emit()               # the burst settles
    assert ends == [1] and not d._slider_dragging
    assert played == []                              # no ripple for the wheel
    assert d.processor.engine.smooth == smooth
    # A press during a burst carries the drag on: smoothing survives.
    notch()
    QTest.mousePress(slider, Qt.LeftButton, Qt.NoModifier, slider.knob_center())
    assert not slider.wheeling and len(begins) == 2
    QTest.mouseRelease(slider, Qt.LeftButton, Qt.NoModifier, slider.knob_center())
    assert not d._slider_dragging and d.processor.engine.smooth == smooth
    assert len(played) == 1                          # a mouse drag still ripples
    d.hide()


@requires_qt
def test_sweat_repaints_only_round_the_drops():
    """The sweat overlay spans the panel; a whole-panel update redrew the
    sphere under it every tick. Only the drops' area is repainted, and the
    overlay is not hidden and re-shown (a whole-panel redraw each time)."""
    app = _app_or_skip()
    from PyQt5.QtCore import QEvent, QObject
    d = _switch_docker()
    d.resize(380, 900)
    d.show()
    app.processEvents()

    class Paints(QObject):
        rects = []

        def eventFilter(self, obj, ev):
            if ev.type() == QEvent.Paint:
                Paints.rects.append(ev.rect())
            return False
    counter = Paints()
    d._sphere.installEventFilter(counter)
    slider = d.hue_row.slider
    em = d._slider_sweat
    slider.beganDrag.emit()
    d._start_slider_sweat()
    for _ in range(3):
        em._step()
        app.processEvents()
    Paints.rects = []
    for _ in range(20):
        em._step()
        app.processEvents()
    # Nothing, or only drop-sized patches where a drop crosses the sphere.
    whole = d._sphere.width() * d._sphere.height()
    assert em._drops
    assert all(r.width() * r.height() < whole // 20 for r in Paints.rects)
    slider.endedDrag.emit()
    for _ in range(40):
        em._step()
        app.processEvents()
    assert em._drops == [] and em.isVisible() and not em._tick.isActive()
    d.hide()


def _wait_full_render(app, d, timeout=10.0):
    import time as _t
    end = _t.monotonic() + timeout
    while getattr(d, "_full_running", None) is not None and _t.monotonic() < end:
        app.processEvents()
        _t.sleep(0.005)
    app.processEvents()


@requires_qt
def test_full_render_runs_off_the_ui_thread_and_matches():
    """Inside Krita a full-quality render (~330 ms) runs on a worker: the
    rebuild returns at once, and the image that lands is the same one the UI
    thread would have drawn, cached for next time."""
    app = _app_or_skip()
    import time as _t
    from Lumina.sphere_docker import SphereDocker
    d = SphereDocker()
    d._rebuild_timer.timeout.disconnect()          # only the test draws
    d._async_full_render = True
    d.processor.clear_full_cache()
    interval = __import__("sys").getswitchinterval()
    t0 = _t.monotonic()
    d._rebuild_sphere_now()
    assert d._full_running is not None
    assert (_t.monotonic() - t0) < 0.1               # did not render in step
    import sys as _sys
    assert abs(_sys.getswitchinterval() - d.BG_SWITCH_INTERVAL) < 1e-6
    _wait_full_render(app, d)
    assert d._full_running is None and not d._full_poll.isActive()
    assert _sys.getswitchinterval() == interval     # restored once idle
    got = d._sphere._image
    base, size, _fq = d._prepare_render()
    assert d.processor.has_full_render(base, size, size)
    d.processor.clear_full_cache()
    want = d.processor.render_image(base, size, size, full_quality=True)
    assert got.size() == want.size() and got == want
    d.close()


@requires_qt
def test_a_stale_background_render_never_replaces_a_newer_image():
    """A drag preview drawn while a full render is under way stays up; the
    late result only goes into the cache. A newer full request replaces any
    waiting one rather than queueing behind it."""
    app = _app_or_skip()
    from Lumina.sphere_docker import SphereDocker
    d = SphereDocker()
    d._rebuild_timer.timeout.disconnect()          # only the test draws
    d._async_full_render = True
    d.processor.clear_full_cache()
    d._rebuild_sphere_now()                           # full: to the worker
    d._slider_dragging = True
    d._last_sphere_ms = 0.0
    d._rebuild_sphere_now()                           # preview, in step
    preview = d._sphere._image
    d._slider_dragging = False
    _wait_full_render(app, d)
    assert d._sphere._image is preview               # the old result is not shown
    # Two requests while one runs: only the newest is kept waiting.
    d._on_hue_changed(10)
    d._rebuild_sphere_now()
    d._on_hue_changed(20)
    d._rebuild_sphere_now()
    d._on_hue_changed(30)
    d._rebuild_sphere_now()
    assert d._full_wanted is not None and d._full_wanted[0] == d._render_gen
    _wait_full_render(app, d)
    _wait_full_render(app, d)
    base, size, _fq = d._prepare_render()
    assert d.processor.has_full_render(base, size, size)
    d.close()


@requires_qt
def test_a_preview_cancels_the_background_render_it_replaces():
    """Left running, an out-of-date full render halved the previews' speed
    while scrolling (they share the interpreter). A preview, or a slider
    starting to move, stops it within a row; an identical full request
    adopts the render already under way instead."""
    app = _app_or_skip()
    from Lumina.sphere_docker import SphereDocker
    d = SphereDocker()
    d._rebuild_timer.timeout.disconnect()          # only the test draws
    d._async_full_render = True
    d.processor.clear_full_cache()
    d._rebuild_sphere_now()                           # full: to the worker
    first = d._full_running
    d._rebuild_sphere_now()                           # the same render again
    assert d._full_running[4] is first[4] and d._full_running[0] == d._render_gen
    assert not first[5].is_set() and d._full_wanted is None
    d._slider_dragging = True
    d._last_sphere_ms = 0.0
    d._rebuild_sphere_now()                           # a preview
    preview = d._sphere._image
    d._slider_dragging = False
    assert first[5].is_set()                          # told to stop
    _wait_full_render(app, d)
    assert first[4].result() is None                  # it stopped
    base, size, _fq = d._prepare_render()
    assert not d.processor.has_full_render(base, size, size)
    assert d._sphere._image is preview
    # A slider starting to move stops one too.
    d._rebuild_sphere_now()
    running = d._full_running
    d.hue_row.slider.beganDrag.emit()
    assert running[5].is_set()
    d.hue_row.slider.endedDrag.emit()
    _wait_full_render(app, d)
    _wait_full_render(app, d)
    d.close()


@requires_qt
def test_sphere_sits_centred_between_equal_preset_columns():
    """Both preset columns take the same width, so the sphere is centred
    with the same gap each side ("Nocturne" is wider than "Gloss" and used to
    push it off centre), and the row fits a narrow panel."""
    app = _app_or_skip()
    import time
    from PyQt5.QtCore import QPoint
    from Lumina.sphere_docker import SphereDocker
    for width in (340, 450, 620):
        d = SphereDocker()
        d.resize(width, 950)
        d.show()
        for _ in range(20):
            app.processEvents()
            time.sleep(0.005)
        # Centred in the scroll content: when the panel scrolls, its
        # scrollbar takes the right-hand edge.
        main = d._scroll.widget()
        left, right = d._preset_cols
        assert left.width() == right.width()
        ox, _oy, dia = d._sphere._sphere_geometry()
        x0 = d._sphere.mapTo(main, QPoint(0, 0)).x() + ox
        lx = left.mapTo(main, QPoint(0, 0)).x() + left.width()
        rx = right.mapTo(main, QPoint(0, 0)).x()
        assert abs((x0 - lx) - (rx - (x0 + dia))) <= 1, (width, x0, dia, lx, rx)
        assert abs((x0 + dia / 2.0) - main.width() / 2.0) <= 1.5, (width, x0, dia, main.width())
        assert rx + right.width() <= main.width(), (width, rx, right.width(), main.width())
        d.close()
    # The row alone (two columns, the smallest sphere, two gaps) fits 300 px.
    from Lumina.sphere_docker import SPHERE_SIZE
    assert 2 * left.width() + SPHERE_SIZE + 12 <= 300, left.width()


@requires_qt
def test_targets_are_their_colour_and_name_only():
    """The Shade / Base / Light row shows each target's colour dot and its
    caption, no glyph icons; clicking either the dot or the caption selects it."""
    _app_or_skip()
    from PyQt5.QtCore import QPointF, Qt
    from PyQt5.QtGui import QMouseEvent
    from PyQt5.QtCore import QEvent
    d = _switch_docker()
    assert not hasattr(d, "_target_btns")
    for key, dot in d._target_dots.items():
        cell = dot.parentWidget()
        kinds = {type(w).__name__ for w in cell.children() if getattr(w, "isWidgetType", lambda: False)()}
        assert kinds == {"TargetDot", "QLabel"}, (key, kinds)
    d._target_caps["shadow"].mousePressEvent(
        QMouseEvent(QEvent.MouseButtonPress, QPointF(2, 2), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    assert d._active_target == "shadow"
    d._target_dots["light"].mousePressEvent(
        QMouseEvent(QEvent.MouseButtonPress, QPointF(2, 2), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    assert d._active_target == "light"


@requires_qt
def test_selected_dot_keeps_clear_of_its_caption_and_sliders_say_value():
    """The grown (selected) disc leaves room above its caption; the lightness
    slider is "Value", so captions read "Hue (light)", never "Light (light)";
    the undo / redo arrows are a comfortable size."""
    app = _app_or_skip()
    from PyQt5.QtCore import QPoint
    d = _switch_docker()
    d.resize(450, 950)
    d.show()
    app.processEvents()
    dot, cap = d._target_dots["base"], d._target_caps["base"]
    d._on_target_changed("base")
    disc_bottom = dot.mapTo(d, QPoint(0, 0)).y() + (dot.BOX + dot.LARGE) / 2.0
    assert cap.mapTo(d, QPoint(0, 0)).y() - disc_bottom >= 6, (disc_bottom, cap.geometry())
    assert d.light_row._label.toolTip().startswith("Value (") or "Value" in d.light_row._label.text()
    d._on_target_changed("light")
    assert d.hue_row._label.toolTip() == "Hue (light)" or d.hue_row._label.text() == "Hue (light)"
    assert d._undo_btn.width() >= 34 and d._redo_btn.width() >= 34
    d.hide()


@requires_qt
def test_every_slider_track_starts_and_ends_at_the_same_x():
    """One label column for all rows: Hue / Sat / Value used to start
    further right than Contrast, Intensity and the Advanced rows."""
    app = _app_or_skip()
    from PyQt5.QtCore import QPoint
    from Lumina.sphere_docker import LabeledSliderRow
    d = _switch_docker()
    d.resize(450, 1400)
    d.show()
    for _ in range(10):
        app.processEvents()
    rows = [r for r in d.findChildren(LabeledSliderRow) if r.isVisible()]
    spans = {(r.slider.mapTo(d, QPoint(0, 0)).x(), r.slider.width()) for r in rows}
    assert len(rows) >= 5 and len(spans) == 1, spans
    d.hide()


@requires_qt
def test_arrows_sit_under_the_presets_and_the_readout_under_the_sphere():
    """Undo / Redo are captioned and centred under the preset columns; the
    hover readout has its own line under the sphere, clear of the target
    row, so it never clashes with the Shade / Base / Light captions."""
    app = _app_or_skip()
    from PyQt5.QtCore import QPoint
    from PyQt5.QtGui import QColor
    d = _switch_docker()
    d.resize(451, 950)
    d.show()
    for _ in range(10):
        app.processEvents()
    c = d._scroll.widget()
    cx = lambda w: w.mapTo(c, QPoint(0, 0)).x() + w.width() / 2.0
    top = lambda w: w.mapTo(c, QPoint(0, 0)).y()
    assert abs(cx(d._undo_btn) - cx(d._preset_cols[0])) <= 1
    assert abs(cx(d._redo_btn) - cx(d._preset_cols[1])) <= 1
    assert d._undo_cap.text() == "Undo" and d._redo_cap.text() == "Redo"
    d._on_sphere_hover(QColor("#16da12"))
    sphere_bottom = top(d._sphere) + d._sphere.height()
    assert sphere_bottom <= top(d._readout)
    assert top(d._readout) + d._readout.height() + 6 <= top(d._target_dots["base"])
    d.hide()


@requires_qt
def test_recent_picks_line_up_with_the_slider_rows():
    app = _app_or_skip()
    from PyQt5.QtCore import QPoint
    d = _switch_docker()
    d.resize(451, 950)
    d.show()
    for _ in range(10):
        app.processEvents()
    c = d._scroll.widget()
    row_x = d.hue_row._dot.mapTo(c, QPoint(0, 0)).x()
    assert abs(d._recent.mapTo(c, QPoint(0, 0)).x() - row_x) <= 1
    d.hide()


@requires_qt
def test_hover_readout_names_the_form_zone():
    _app_or_skip()
    from PyQt5.QtGui import QColor
    d = _switch_docker()
    d._sphere.hover_uv = (0.0, 0.0)
    d._on_sphere_hover(QColor("#00e700"))
    zone = d._readout.text().split("·")[-1].strip()
    assert zone in ("Highlight", "Light", "Halftone", "Terminator",
                    "Core shadow", "Reflected light"), d._readout.text()
    d._sphere.hover_uv = (-0.6, 0.75)          # lower left: away from the light
    d._on_sphere_hover(QColor("#00e700"))
    assert d._readout.text().split("·")[-1].strip() in ("Core shadow", "Reflected light")


@requires_qt
def test_canvas_watch_searches_the_central_area_only():
    """Scanning the whole Krita window made PyQt wrap the objects inside its
    QML panels, which printed "No such signal QQuickPalette::destroyed" and
    three more each time. The canvases live in the central area; the docks
    (where the QML panels are) are never walked."""
    _app_or_skip()
    from PyQt5.QtWidgets import QMainWindow, QDockWidget, QWidget, QVBoxLayout
    import Lumina.sphere_docker as SD

    class KisOpenGLCanvas2(QWidget):        # named like Krita's canvas class
        pass

    win = QMainWindow()
    central = QWidget()
    QVBoxLayout(central).addWidget(KisOpenGLCanvas2())
    win.setCentralWidget(central)
    dock = QDockWidget("Text Properties")
    in_dock = KisOpenGLCanvas2()
    dock.setWidget(in_dock)
    win.addDockWidget(1, dock)
    watch = SD._CanvasPickFilter()
    assert watch.watch(win) == 1                      # the central canvas
    assert in_dock not in watch._watched              # the dock was not searched
def test_migration_moves_only_values_still_at_an_old_default():
    """Issue #57: settings saved before v6 move from an old default to the
    current one; values the user chose stay exactly as they were; v6 settings
    are left alone."""
    from Lumina.sphere_docker import migrate_settings_defaults, SHININESS_REF
    old = {"ambient": 10, "ground": 2, "sky": 5, "rim": 14, "spec_max": 24,
           "specular": 3, "azimuth": 287, "elevation": 45}
    new, changes = migrate_settings_defaults(old, 5)
    assert new == {"ambient": 5, "ground": 1, "sky": 2, "rim": 10, "spec_max": 61,
                   "specular": SHININESS_REF, "azimuth": 304, "elevation": 41}
    assert {c[0] for c in changes} == set(old)
    mine = {"ambient": 12, "ground": 3, "sky": 4, "rim": 20, "spec_max": 40,
            "specular": 16, "azimuth": 287, "elevation": 30}
    kept, changes = migrate_settings_defaults(mine, 5)
    assert kept == mine and changes == []           # the light moves only as a pair
    mixed = dict(mine, rim=14)
    out, changes = migrate_settings_defaults(mixed, 5)
    assert out == dict(mine, rim=10) and [c[0] for c in changes] == ["rim"]
    assert migrate_settings_defaults(old, 6) == (old, [])
    # the truncated 8.9 (saved as an int before v6) counts as the default
    assert migrate_settings_defaults({"specular": 8}, 5)[0]["specular"] == SHININESS_REF


@requires_qt
def test_old_settings_file_migrates_once_and_keeps_custom_values(tmp_path, monkeypatch):
    """A 2.6.0-style v5 file: untouched defaults load as today's, a value the
    user set stays, the version is written as 6, and a second load changes
    nothing. Shininess survives a restart exactly (it was saved as an int)."""
    _app_or_skip()
    from PyQt5.QtCore import QSettings
    import Lumina.sphere_docker as SD
    store = QSettings(str(tmp_path / "Lumina.ini"), QSettings.IniFormat)
    for k, v in {"version": 5, "ambient": 10, "rim": 14, "sky": 5, "ground": 2,
                 "spec_max": 24, "specular": 3, "azimuth": 287, "elevation": 45,
                 "contrast": 130}.items():
        store.setValue(k, v)
    store.sync()
    monkeypatch.setattr(SD, "_settings", lambda: store)
    d = _switch_docker()
    eng = d.processor.engine
    assert round(eng.ambient * 100) == 5 and round(eng.rim_light * 100) == 10
    assert round(eng.sky_bounce * 100) == 2 and round(eng.ground_bounce * 100) == 1
    assert round(eng.spec_max * 100) == 61 and abs(eng.shininess - SD.SHININESS_REF) < 1e-6
    assert (round(eng.light_azimuth), round(eng.light_elevation)) == (304, 41)
    assert abs(eng.contrast - 1.30) < 1e-6           # the user's own value stays
    d._save_settings()
    assert int(store.value("version")) == 6
    assert abs(float(store.value("specular")) - SD.SHININESS_REF) < 1e-6
    # A user who moves Rim back to 14 on v6 keeps it.
    store.setValue("rim", 14)
    store.sync()
    d2 = _switch_docker()
    assert round(d2.processor.engine.rim_light * 100) == 14
    assert abs(d2.processor.engine.shininess - SD.SHININESS_REF) < 1e-6


@requires_qt
def test_a_v5_file_written_by_2_7_is_not_migrated(tmp_path, monkeypatch):
    """2.7.0 also saved v5, already with today's defaults. A Rim of 14 there
    came from the Artistic preset, not the old default, and must stay (this
    happened to the author: the migration moved it to 10)."""
    _app_or_skip()
    from PyQt5.QtCore import QSettings
    import Lumina.sphere_docker as SD
    store = QSettings(str(tmp_path / "Lumina.ini"), QSettings.IniFormat)
    for k, v in {"version": 5, "rim": 14, "ambient": 6, "spec_max": 80, "specular": 12,
                 "sticky": 40, "targets_profile": "sRGB-elle-V2-srgbtrc.icc",
                 "highlight": "1.0000,0.9850,0.9700"}.items():
        store.setValue(k, v)
    store.sync()
    monkeypatch.setattr(SD, "_settings", lambda: store)
    d = _switch_docker()
    eng = d.processor.engine
    assert round(eng.rim_light * 100) == 14
    assert round(eng.spec_max * 100) == 80 and abs(eng.shininess - 12) < 1e-6
    d._save_settings()
    assert int(store.value("version")) == 6


@requires_qt
def test_a_2_6_preset_migrates_whole_to_todays_preset(tmp_path, monkeypatch):
    """The old presets share values with the old defaults (2.6.0's Gloss has
    ground 2 and spec_max 24). A file holding one whole gets today's version
    of that preset, not a per-value mix of old preset and new defaults."""
    _app_or_skip()
    from PyQt5.QtCore import QSettings
    import Lumina.sphere_docker as SD
    store = QSettings(str(tmp_path / "Lumina.ini"), QSettings.IniFormat)
    store.setValue("version", 5)
    for k, v in SD.V26_PRESETS["gloss"].items():
        store.setValue(k, v)
    store.sync()
    monkeypatch.setattr(SD, "_settings", lambda: store)
    d = _switch_docker()
    eng = d.processor.engine
    gloss = next(p for p in SD._PRESETS if p["key"] == "gloss")["values"]
    assert round(eng.spec_max * 100) == gloss["spec_max"]
    assert round(eng.rim_light * 100) == gloss["rim"]
    assert abs(eng.shininess - gloss["specular"]) < 1e-6
    assert SD.match_v26_preset(dict(SD.V26_PRESETS["gloss"], rim=17)) is None   # tweaked: not whole
