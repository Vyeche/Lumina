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
    hue = LabeledSliderRow(Accent.NEUTRAL, "Hue", 0, hi=359, percent=False)
    hue.slider.setValue(180)
    assert hue._value.text() == "180"


@requires_qt
def test_typed_percent_and_raw():
    _app_or_skip()
    from Lumina.sphere_docker import LabeledSliderRow, Accent
    r = LabeledSliderRow(Accent.NEUTRAL, "Tone", 100, hi=200)
    r._value.setText("86%")
    r._value.editingFinished.emit()
    assert r.slider.value() == 172
    r._value.setText("90")
    r._value.editingFinished.emit()
    assert r.slider.value() == 90


@requires_qt
def test_typed_invalid_reverts():
    _app_or_skip()
    from Lumina.sphere_docker import LabeledSliderRow, Accent
    r = LabeledSliderRow(Accent.NEUTRAL, "Tone", 100, hi=200)
    r._value.setText("junk")
    r._value.editingFinished.emit()
    assert r.slider.value() == 100
    assert r._value.text() == "100%"
    r._value.setText("9999")
    r._value.editingFinished.emit()
    assert r.slider.value() == 200
