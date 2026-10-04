"""Release-calibration regression tests (stdlib only, no Krita/Qt).

Locks the renderer contract:
  A. shadow/base/light target roles via _tonal_color with extreme RGB.
  B. Spot visibly distinct from Point with 35deg/0.25 defaults.
  C. Contrast 135 preserves low shadow luma (no hard crush to black).
  D. Contrast 135 shadow zone renders non-black with shadow hue dominant.
"""

import math
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


def test_contrast_135_preserves_low_shadow_luma():
    engine_mod = load_engine()
    engine = engine_mod.ColorEngine(64)
    engine.contrast = 1.35

    for color in ((0.20, 0.0, 0.0), (0.05, 0.0, 0.0)):
        result = engine._apply_contrast(color)
        assert result[0] > 0.0, (color, result)
        assert result[0] > result[1] * 4.0, (color, result)
        assert result[0] > result[2] * 4.0, (color, result)
    print("  PASS contrast-135 display")


def test_contrast_135_shadow_zone_is_not_black():
    engine_mod = load_engine()
    engine = engine_mod.ColorEngine(96)

    engine.ambient = 0.0
    engine.smooth = 0.0
    engine.grain = 0.0
    engine.glow_intensity = 0.0
    engine.saturation = 1.0
    engine.brightness = 1.0
    engine.contrast = 1.35

    engine.rim_light = 0.0
    engine.spec_max = 0.0

    engine.set_shadow_color((1.0, 0.0, 0.0))
    engine.set_light_color((0.0, 0.0, 1.0))
    engine.set_highlight_color((1.0, 1.0, 1.0))

    res = 96
    pixels = engine.render((0.0, 1.0, 0.0), res, res)

    best = None
    for row in range(res):
        for col in range(res):
            if not engine._mask_grid[row][col]:
                continue
            x = -1.0 + 2.0 * col / (res - 1.0)
            y = -1.0 + 2.0 * row / (res - 1.0)
            nx, ny, nz = engine._normal_grid[row][col]
            ldir, _ = engine._light_for_surface(x, y, math.sqrt(max(0.0, 1.0 - x * x - y * y)))
            ndl = max(0.0, nx * ldir[0] + ny * ldir[1] + nz * ldir[2])
            if best is None or ndl < best[0]:
                best = (ndl, pixels[row][col])

    assert best is not None
    ndl, pixel = best
    assert ndl < 0.05, (ndl, pixel)

    red, green, blue = pixel
    assert red >= 5, (ndl, pixel)
    assert red > green * 2, (ndl, pixel)
    assert red > blue * 2, (ndl, pixel)
    print(f"  PASS contrast-135 shadow ndl={ndl:.3f} rgb={pixel}")


def _accent_probe(engine):
    # Fixed surface/light sample in linear space for accent components.
    nv, ndl, illuminance = 0.35, 0.55, 1.1
    light = (0.9, 0.8, 0.7)
    rim = engine._compute_rim(nv, ndl, illuminance, light)
    engine.glow_intensity = 0.28
    glow = engine._compute_glow(nv, ndl, light)
    engine.glow_intensity = 0.0
    return rim, glow


def test_tone_does_not_modify_rim_or_glow():
    engine_mod = load_engine()
    engine = engine_mod.ColorEngine(32)
    engine.set_saturation(1.0)
    rim_a, glow_a = _accent_probe(engine)
    engine.set_saturation(1.18)
    rim_b, glow_b = _accent_probe(engine)
    assert rim_a == rim_b, (rim_a, rim_b)
    assert glow_a == glow_b, (glow_a, glow_b)
    assert any(v > 0.0 for v in rim_a), rim_a
    assert any(v > 0.0 for v in glow_a), glow_a
    print("  PASS tone-invariant accents")


def test_grain_amplitude_is_contrast_independent():
    # Grain delta measured in linear space, after contrast, before accents.
    engine_mod = load_engine()
    deltas = []
    for contrast in (0.8, 1.4):
        engine = engine_mod.ColorEngine(32)
        engine.set_contrast(contrast)
        engine.set_grain(20)
        ds = []
        for row in range(0, 32, 3):
            for col in range(0, 32, 3):
                base = (0.30, 0.25, 0.22)
                graded = engine._apply_brightness(
                    engine._apply_saturation(engine._apply_contrast(base)))
                grained = engine._apply_grain(graded, row, col, 0.7)
                ds.append(max(abs(g - p) for g, p in zip(grained, graded)))
        deltas.append(sum(ds) / len(ds))
    ratio = deltas[1] / max(deltas[0], 1e-12)
    assert 0.85 <= ratio <= 1.15, deltas
    assert deltas[0] > 0.0, deltas
    print(f"  PASS grain ratio={ratio:.3f}")


def _load_pure_helpers():
    """Exec the real parse/format helpers without importing Qt."""
    import ast
    src = open("src/Lumina/sphere_docker.py").read()
    tree = ast.parse(src)
    wanted = {"parse_typed_slider_value", "format_slider_value"}
    nodes = [n for n in tree.body
             if isinstance(n, ast.FunctionDef) and n.name in wanted]
    assert {n.name for n in nodes} == wanted, "helpers missing"
    ns = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]),
                 "<slider-helpers>", "exec"), ns)
    return ns["parse_typed_slider_value"], ns["format_slider_value"]


def test_engine_defaults():
    engine_mod = load_engine()
    engine = engine_mod.ColorEngine(resolution=32)
    assert engine.light_azimuth == 287.0
    assert engine.light_elevation == 45.0
    assert engine.light_type == "Sun"


def test_parse_typed_slider_value():
    parse, _ = _load_pure_helpers()
    assert parse("86%", 0, 200) == 172
    assert parse("90", 0, 200) == 90
    assert parse("180", 0, 359) == 180
    assert parse("9999", 0, 200) == 200
    assert parse("-5", 0, 200) == 0
    assert parse("junk", 0, 200) is None
    assert parse("", 0, 200) is None


def test_format_slider_value():
    _, fmt = _load_pure_helpers()
    assert fmt(100, True) == "100%"
    assert fmt(200, True) == "200%"
    assert fmt(180, False) == "180"
    assert fmt(8, False) == "8"
