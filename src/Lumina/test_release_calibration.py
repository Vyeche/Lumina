"""Release-calibration regression tests (stdlib only, no Krita/Qt).

Locks the renderer contract:
  A. shadow/base/light target roles via _tonal_color with extreme RGB.
  B. Spot visibly distinct from Point with 35deg/0.25 defaults.
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


def test_tonal_color_preserves_shadow_base_light_roles():
    engine_mod = load_engine()
    engine = engine_mod.ColorEngine(64)

    shadow = (1.0, 0.0, 0.0)
    base = (0.0, 1.0, 0.0)
    light = (0.0, 0.0, 1.0)

    c0 = engine._tonal_color(shadow, base, light, 0.0)
    c5 = engine._tonal_color(shadow, base, light, 0.5)
    c1 = engine._tonal_color(shadow, base, light, 1.0)

    assert c0[0] >= 0.98, c0
    assert c0[1] <= 0.02, c0
    assert c0[2] <= 0.02, c0

    assert c5[1] >= 0.75, c5
    assert c5[1] > c5[0] + 0.40, c5
    assert c5[1] > c5[2] + 0.40, c5

    assert c1[2] >= 0.98, c1
    assert c1[0] <= 0.02, c1
    assert c1[1] <= 0.02, c1
    print("  PASS tonal roles")


def _make_engine(engine_mod):
    e = engine_mod.ColorEngine(resolution=128)
    e.set_ambient(0.10)
    e.set_light_intensity(1.0)
    e.set_smooth(0)
    e.set_glow_intensity(0.0)
    e.set_saturation(1.0)
    e.set_brightness(1.0)
    e.set_contrast(1.0)
    return e


def test_spot_is_visibly_distinct_from_point():
    engine_mod = load_engine()
    point = _make_engine(engine_mod)
    point.set_light_type("Point")
    spot = _make_engine(engine_mod)
    spot.set_light_type("Spot")

    base = (0.5, 0.5, 0.5)
    point_img = point.render(base, 128, 128)
    spot_img = spot.render(base, 128, 128)
    mask = point.mask_grid()

    deltas = []
    per_pixel_max = []
    for r in range(128):
        for c in range(128):
            if not mask[r][c]:
                continue
            a = point_img[r][c]
            b = spot_img[r][c]
            d = (abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2]))
            deltas.extend(d)
            per_pixel_max.append(max(d))

    assert max(per_pixel_max) >= 8, max(per_pixel_max)
    frac = sum(1 for v in per_pixel_max if v >= 5) / len(per_pixel_max)
    assert frac >= 0.05, frac
    assert sum(deltas) / len(deltas) >= 1.0, sum(deltas) / len(deltas)
    print(f"  PASS point!=spot max={max(per_pixel_max):.1f} frac={frac:.3f}")
