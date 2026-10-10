"""One-entry full-quality output cache (issue #40 §1).

Headless-safe: byte-level tests force the processor's non-Qt return path.
The docker release/reuse test needs PyQt5 and is skipped without it.
"""

import sys

sys.path.insert(0, ".")
sys.path.insert(0, "src")

import pytest

from Lumina.color_engine import ColorEngine
from Lumina.color_processor import SphereColorProcessor
import Lumina.color_processor as _cp

try:
    import PyQt5.QtWidgets as QtWidgets  # noqa: F401
    _HAS_QT = True
except ImportError:
    QtWidgets = None
    _HAS_QT = False

requires_qt = pytest.mark.skipif(not _HAS_QT, reason="PyQt5 not installed")

_app = None


def _app_or_skip():
    global _app
    if _app is None:
        try:
            _app = QtWidgets.QApplication([])
        except Exception as exc:
            pytest.skip(f"no Qt application: {exc}")
    return _app

SIZE = 48
BASE = (0.8, 0.36, 0.36)

# One mutator per render-affecting setting family. Each must change the
# signature (and therefore force a cache miss) on its own.
FAMILIES = [
    ("light_type", lambda e: e.set_light_type("Point")),
    ("azimuth", lambda e: e.set_light_angle(10, 45)),
    ("elevation", lambda e: e.set_light_elevation(20)),
    ("intensity", lambda e: e.set_light_intensity(0.5)),
    ("light_color", lambda e: e.set_light_color((0.5, 0.6, 0.7))),
    ("shadow_color", lambda e: e.set_shadow_color((0.1, 0.2, 0.3))),
    ("highlight_color", lambda e: e.set_highlight_color((0.9, 0.8, 0.7))),
    ("ambient_color", lambda e: e.set_ambient_color((0.5, 0.5, 0.5))),
    ("ambient", lambda e: e.set_ambient(0.3)),
    ("shininess", lambda e: e.set_shininess(8.0)),
    ("spec_knee", lambda e: e.set_spec_knee(0.5)),
    ("spec_max", lambda e: e.set_spec_max(0.1)),
    ("rim_light", lambda e: e.set_rim_light(0.3)),
    ("rim_sky_mix", lambda e: e.set_rim_sky_mix(0.2)),
    ("sky_bounce", lambda e: e.set_sky_bounce(0.2)),
    ("ground_bounce", lambda e: e.set_ground_bounce(0.2)),
    ("contrast", lambda e: e.set_contrast(1.2)),
    ("brightness", lambda e: e.set_brightness(1.2)),
    ("saturation", lambda e: e.set_saturation(0.5)),
    ("mixer_mode", lambda e: e.set_mixer_mode("Additive")),
    ("glow_intensity", lambda e: e.set_glow_intensity(0.5)),
    ("glow_radius", lambda e: e.set_glow_radius(20.0)),
    ("grain", lambda e: e.set_grain(0.5)),
    ("smooth", lambda e: e.set_smooth(0.0)),
]


def _bytes_proc(monkeypatch):
    """Processor whose render_image returns raw bytes (no Qt)."""
    monkeypatch.setattr(_cp, "_HAS_QT", False)
    return SphereColorProcessor(resolution=SIZE)


def _count_engine_renders(proc):
    calls = []
    orig = proc.engine.render

    def counting(base, w, h):
        calls.append((tuple(base), w, h))
        return orig(base, w, h)

    proc.engine.render = counting
    return calls


def test_signature_stable_for_same_state():
    assert ColorEngine().render_signature() == ColorEngine().render_signature()


@pytest.mark.parametrize("name,mutate", FAMILIES)
def test_signature_covers_every_family(name, mutate):
    before = ColorEngine().render_signature()
    eng = ColorEngine()
    mutate(eng)
    assert eng.render_signature() != before, name


def test_full_repeat_reuses_pixels_without_rerender(monkeypatch):
    proc = _bytes_proc(monkeypatch)
    calls = _count_engine_renders(proc)
    first = proc.render_image(BASE, SIZE, SIZE, full_quality=True)
    assert proc.last_cache_hit is False
    second = proc.render_image(BASE, SIZE, SIZE, full_quality=True)
    assert proc.last_cache_hit is True
    assert bytes(second) == bytes(first)
    assert len(calls) == 1


@pytest.mark.parametrize("name,mutate", FAMILIES)
def test_each_family_forces_a_miss(monkeypatch, name, mutate):
    proc = _bytes_proc(monkeypatch)
    proc.render_image(BASE, SIZE, SIZE, full_quality=True)
    assert proc.last_cache_hit is False
    mutate(proc.engine)
    proc.render_image(BASE, SIZE, SIZE, full_quality=True)
    assert proc.last_cache_hit is False, name


def test_base_color_change_forces_a_miss(monkeypatch):
    proc = _bytes_proc(monkeypatch)
    proc.render_image(BASE, SIZE, SIZE, full_quality=True)
    proc.render_image((0.1, 0.2, 0.9), SIZE, SIZE, full_quality=True)
    assert proc.last_cache_hit is False


def test_size_change_forces_a_miss(monkeypatch):
    proc = _bytes_proc(monkeypatch)
    proc.render_image(BASE, SIZE, SIZE, full_quality=True)
    proc.render_image(BASE, SIZE + 8, SIZE + 8, full_quality=True)
    assert proc.last_cache_hit is False


def test_preview_never_populates_the_cache(monkeypatch):
    proc = _bytes_proc(monkeypatch)
    calls = _count_engine_renders(proc)
    proc.render_image(BASE, SIZE, SIZE)  # preview: no opt-in
    proc.render_image(BASE, SIZE, SIZE)  # preview again: must re-render
    assert len(calls) == 2
    assert proc.last_cache_hit is False
    proc.render_image(BASE, SIZE, SIZE, full_quality=True)
    assert proc.last_cache_hit is False  # previews left nothing behind
    proc.render_image(BASE, SIZE, SIZE, full_quality=True)
    assert proc.last_cache_hit is True


def test_cached_value_is_immutable(monkeypatch):
    proc = _bytes_proc(monkeypatch)
    first = proc.render_image(BASE, SIZE, SIZE, full_quality=True)
    first[0] = 0xAB  # mutate the caller's buffer
    second = proc.render_image(BASE, SIZE, SIZE, full_quality=True)
    assert proc.last_cache_hit is True
    assert second[0] != 0xAB


@requires_qt
def test_release_reuses_cache_across_drag_cycle():
    """Full → drag preview → release: release hits, preview never cached."""
    import os
    if not os.environ.get("DISPLAY") and sys.platform.startswith("linux"):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _app_or_skip()
    from Lumina.sphere_docker import SphereDocker
    d = SphereDocker()
    seen = []
    d._sphere.set_image = lambda img: seen.append(img)

    d._slider_dragging = False
    d._external_preview = False
    d._render_sphere()  # full: populates
    assert d.processor.last_cache_hit is False
    d._render_sphere()  # identical full: reuses
    assert d.processor.last_cache_hit is True

    d._on_slider_drag_begin()  # smooth 0, drag size
    d._render_sphere()  # preview: must not hit or populate
    assert d.processor.last_cache_hit is False

    d._on_slider_drag_end()  # restores pre-drag settings
    d._pending_reason = None
    d._render_sphere()  # release: same key as before the drag
    assert d.processor.last_cache_hit is True


def test_cache_keeps_recent_looks_so_toggling_back_is_instant():
    """Several full-quality results are kept: going back to a recent look
    (a preset switched off again) is a cache hit, not a re-render."""
    p = SphereColorProcessor(resolution=32)
    base = (0.8, 0.3, 0.2)
    p.engine.ambient = 0.05
    p.render_image(base, 32, 32, full_quality=True)          # look A
    assert p.has_full_render(base, 32, 32)
    p.engine.ambient = 0.3
    assert not p.has_full_render(base, 32, 32)
    p.render_image(base, 32, 32, full_quality=True)          # look B
    p.engine.ambient = 0.05
    assert p.has_full_render(base, 32, 32)
    p.render_image(base, 32, 32, full_quality=True)          # back to A
    assert p.last_cache_hit
    for k in range(p.FULL_CACHE_SIZE + 3):                    # bounded
        p.engine.ambient = 0.1 + k * 0.01
        p.render_image(base, 32, 32, full_quality=True)
    assert len(p._full_cache) == p.FULL_CACHE_SIZE
