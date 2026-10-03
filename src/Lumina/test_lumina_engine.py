"""Geometry and light-type regression tests (no file output).

Side-effect free: renders in memory and asserts. For visual calibration,
use tools/render_lumina_reference.py which writes PPM/PNG files on demand.
"""

import sys

sys.path.insert(0, ".")


def load_engine():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "color_engine", "src/Lumina/color_engine.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_center_normal_is_unit_and_positive_z():
    mod = load_engine()
    e = mod.ColorEngine(resolution=64)
    e.render((0.5, 0.3, 0.2), 64, 64)
    n = e._normal_grid[32][32]
    length = sum(c * c for c in n) ** 0.5
    assert n[2] > 0.999, n
    assert abs(length - 1.0) < 1.0e-6, n


def test_corners_are_outside_silhouette():
    mod = load_engine()
    e = mod.ColorEngine(resolution=64)
    px = e.render((0.5, 0.3, 0.2), 64, 64)
    assert px[0][0] == (0, 0, 0)
    assert px[-1][-1] == (0, 0, 0)
    assert not e.mask_grid()[0][0]


def test_all_light_types_render():
    mod = load_engine()
    base = (205.0 / 255.0, 92.0 / 255.0, 92.0 / 255.0)
    for lt in ("Point", "Sun", "Spot", "Area"):
        e = mod.ColorEngine(resolution=64)
        e.set_light_type(lt)
        e.set_smooth(0)
        px = e.render(base, 64, 64)
        assert px[32][32] != (0, 0, 0), lt


def test_shininess_is_clamped_to_64():
    mod = load_engine()
    e = mod.ColorEngine(resolution=32)
    e.set_shininess(90)
    assert e.shininess == 64.0
    e.set_shininess(0)
    assert e.shininess == 1.0


def test_spec_knee_is_clamped_to_0_02_0_95():
    mod = load_engine()
    e = mod.ColorEngine(resolution=32)
    e.set_spec_knee(5.0)
    assert e.spec_knee == 0.95
    e.set_spec_knee(-1.0)
    assert e.spec_knee == 0.02


def test_spec_max_default_is_0_24():
    mod = load_engine()
    e = mod.ColorEngine(resolution=32)
    assert abs(float(getattr(e, "spec_max", e.SPEC_MAX)) - 0.24) < 1e-9
    e.set_spec_max(0.28)
    assert abs(e.spec_max - 0.28) < 1e-9
