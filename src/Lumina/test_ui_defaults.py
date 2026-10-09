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
    d._rebuild_orb = lambda reason="live_update": applied.append(tuple(d._targets["base"].getRgb()[:3]))
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
    d._rebuild_orb = lambda reason="live_update": None
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
    d._rebuild_orb = lambda reason="live_update": None  # scaling + sync logic is what is tested
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
    d._rebuild_orb = lambda reason="live_update": None
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
    d._rebuild_orb = lambda reason="live_update": None
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
    """_rebuild_orb(reason=...) survives coalescing into RENDER_DONE."""
    _app_or_skip()
    from PyQt5.QtGui import QImage as _QI
    from Lumina.sphere_docker import SphereDocker
    d = SphereDocker()
    d._rebuild_timer = _NoopTimer()
    seen = []
    d._orb.set_image = lambda img: seen.append(img)
    d._render_orb = lambda: (
        setattr(d, "_last_render_size", 96),
        setattr(d, "_last_render_smooth", 0.0),
        _QI(96, 96, _QI.Format_ARGB32),
    )[-1]
    d._rebuild_orb(reason="slider_release")
    assert d._pending_reason == "slider_release"
    d._rebuild_orb_now()
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
    d._rebuild_orb = lambda reason="live_update": None
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
    assert "version=2.6.0" in stamps[0], stamps[0]
    assert "target=shadow" in stamps[0], stamps[0]
    assert "hsv=(268,90,31)" in stamps[0], stamps[0]
    assert "sliders=(268,90,31)" in stamps[0], stamps[0]
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
    d._rebuild_orb = lambda reason="live_update": None
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
    assert d._hex_lock_btn.text() == "EDIT"


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
    # The sticky band reaches ~30px past the rim: 10px out still projects...
    # (measured on the diagonal, where the widget bounds leave room; on the
    # axes the 20px orb margin is the tighter limit).
    sx, sy = w._to_sphere_coords(_QP(172.0, 172.0))
    assert (sx, sy) != (None, None), "10px past rim should still pick"
    # ...while well outside stays a dead zone.
    sx, sy = w._to_sphere_coords(_QP(184.0, 184.0))
    assert (sx, sy) == (None, None), (sx, sy)


@requires_qt
def test_magenta_zone_hits_reference_triple():
    """#743356 derives approx #2b0850 / #c25f42 (hue-conditioned, not global)."""
    _app_or_skip()
    from PyQt5.QtGui import QColor as _QColor
    from Lumina.sphere_docker import SphereDocker
    d = SphereDocker()
    d._rebuild_timer = _NoopTimer()
    d._rebuild_orb = lambda reason="live_update": None
    d._distribute_from(_QColor("#743356"))
    shadow = d._targets["shadow"]
    light = d._targets["light"]
    assert d._targets["base"].name() == "#743356"

    def _hsv(name, color):
        h, s, v = color.getHsvF()[0], color.getHsvF()[1], color.getHsvF()[2]
        got = (round(h * 359) % 360, round(s * 100), round(v * 100))
        ref = {"shadow": (269, 90, 31), "light": (14, 66, 76)}[name]
        assert abs(got[0] - ref[0]) <= 3, (name, got, ref)
        assert abs(got[1] - ref[1]) <= 4, (name, got, ref)
        assert abs(got[2] - ref[2]) <= 2, (name, got, ref)

    _hsv("shadow", shadow)
    _hsv("light", light)


@requires_qt
def test_derivation_zone_leaves_primaries_sane():
    """Outside magenta, derivation keeps highlights near the base family."""
    _app_or_skip()
    from PyQt5.QtGui import QColor as _QColor
    from Lumina.sphere_docker import SphereDocker
    d = SphereDocker()
    d._rebuild_timer = _NoopTimer()
    d._rebuild_orb = lambda reason="live_update": None
    for hexv, h_lo, h_hi in (("#d93636", 350, 80),   # red: warm light, no magenta jump
                             ("#36d936", 80, 160),   # green: stays green-yellow
                             ("#3636d9", 230, 280)):  # blue: stays blue-violet
        base = _QColor(hexv)
        d._distribute_from(base)
        lh = d._targets["light"].getHsvF()[0]
        ldeg = round(lh * 359) % 360
        if h_lo > h_hi:  # wraps through 0
            assert ldeg >= h_lo or ldeg <= h_hi, (hexv, ldeg)
        else:
            assert h_lo <= ldeg <= h_hi, (hexv, ldeg)
        # Shadow always strictly darker than its base.
        sv = d._targets["shadow"].getHsvF()[2]
        assert sv < base.getHsvF()[2], (hexv, sv)
    # Grey stays achromatic: the zone never invents hue.
    d._distribute_from(_QColor(140, 140, 140))
    assert d._targets["light"].getHsvF()[1] == 0.0
    assert d._targets["shadow"].getHsvF()[1] == 0.0


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
def test_red_falls_outside_magenta_zone():
    """Pure red derives from the base rule only (zone weight exactly 0)."""
    _app_or_skip()
    from PyQt5.QtGui import QColor as _QColor
    from Lumina.sphere_docker import SphereDocker
    d = SphereDocker()
    d._rebuild_timer = _NoopTimer()
    d._rebuild_orb = lambda reason="live_update": None
    assert d._magenta_weight(0.0) == 0.0
    d._distribute_from(_QColor("#ff0000"))
    sh = d._targets["shadow"].getHsvF()
    li = d._targets["light"].getHsvF()
    assert (round(sh[0] * 359) % 360, round(sh[1] * 100), round(sh[2] * 100)) == (334, 92, 50)
    assert (round(li[0] * 359) % 360, round(li[1] * 100), round(li[2] * 100)) == (2, 72, 100)


@requires_qt
def test_rim_tint_interlock_keeps_stored_value():
    """Rim Tint greys out at Rim 0 without losing its value (Q11)."""
    _app_or_skip()
    from Lumina.sphere_docker import SphereDocker
    d = SphereDocker()
    d._rebuild_timer = _NoopTimer()
    d._rebuild_orb = lambda reason="live_update": None
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
    d._rebuild_orb = lambda reason="live_update": None
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
    d._rebuild_orb = lambda reason="live_update": None
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
