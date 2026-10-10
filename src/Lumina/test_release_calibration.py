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

    # Targets are ingredients, not paint (fitted to the reference app): the
    # unlit side is the shadow target mixed with the base by
    # TONAL_SHADOW_MIX, the highlight reaches TONAL_LIGHT_MAX of the light
    # (since #45 the specular sheen carries most of the highlight, so the
    # light colour is a minority share even at the peak). Shadow and base
    # lead their zones, the light rises only toward the top, and it never
    # leaks into the shadow.
    mix, cap = engine_mod.ColorEngine.TONAL_SHADOW_MIX, engine_mod.ColorEngine.TONAL_LIGHT_MAX
    assert abs(c0[0] - mix) < 0.02 and abs(c0[1] - (1.0 - mix)) < 0.02, c0
    assert c0[0] > c0[1] and c0[2] <= 0.02, c0

    assert c5[1] >= 0.75, c5
    assert c5[1] > c5[0] + 0.40, c5
    assert c5[1] > c5[2] + 0.40, c5

    assert abs(c1[2] - cap) < 0.02 and c1[2] > c5[2] + cap - 0.05, (c1, c5)
    assert c1[0] <= 0.02, c1
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
    # Isolate the shadow zone from the edge accents: reflected light adds the
    # base colour at the silhouette, which is where the darkest N.L sits.
    engine.REFLECT_STRENGTH = 0.0

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
    # The shadow target leads the zone; the base is mixed in by
    # TONAL_SHADOW_MIX, so green is present but never ahead.
    assert red > green, (ndl, pixel)
    assert red > blue * 2, (ndl, pixel)
    print(f"  PASS contrast-135 shadow ndl={ndl:.3f} rgb={pixel}")


def _accent_probe(engine):
    # Fixed surface/light sample in linear space for accent components.
    nv, ndl, illuminance = 0.35, 0.55, 1.1
    light = (0.9, 0.8, 0.7)
    sky = (0.2, 0.3, 0.5)
    rim = engine._compute_rim(nv, ndl, illuminance, light, sky)
    engine.glow_intensity = 0.28
    glow = engine._compute_glow(nv, ndl, light)
    engine.glow_intensity = 0.0
    return rim, glow


def test_rim_tint_blends_toward_sky():
    # The rim must separate chromatically from a warm body: with a warm
    # light and cool sky, the rim's blue share exceeds the light's own.
    engine_mod = load_engine()
    engine = engine_mod.ColorEngine(32)
    rim = engine._compute_rim(0.35, 0.55, 1.1, (0.9, 0.8, 0.7), (0.2, 0.3, 0.5))
    total = sum(rim)
    assert total > 0.0, rim
    assert rim[2] / total > 0.7 / (0.9 + 0.8 + 0.7), rim
    print("  PASS rim sky tint")


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
    """Load the real typed_entry module without importing Qt."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "typed_entry", "src/Lumina/typed_entry.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.parse_typed_entry, mod.format_slider_value


def test_engine_defaults():
    engine_mod = load_engine()
    engine = engine_mod.ColorEngine(resolution=32)
    # The reference lighting app's light direction, fitted (was 287/45).
    assert engine.light_azimuth == 304.0
    assert engine.light_elevation == 41.0
    assert engine.light_type == "Sun"


def test_parse_typed_slider_value():
    parse, _ = _load_pure_helpers()
    cases = [
        # (text, lo, hi, value, valid, was_percent, was_clamped)
        ("90", 0, 200, 90, True, False, False),
        ("  90  ", 0, 200, 90, True, False, False),
        ("90.0", 0, 200, 90, True, False, False),
        ("86%", 0, 200, 172, True, True, False),
        (" 90 % ", 0, 200, 180, True, True, False),
        ("0%", 0, 200, 0, True, True, False),
        ("100%", 0, 200, 200, True, True, False),
        ("150%", 0, 200, 200, True, True, True),
        ("-10%", 0, 200, 0, True, True, True),
        ("90%", 0, 359, 323.1, True, True, False),
        ("9999", 0, 200, 200, True, False, True),
        ("-5", 0, 200, 0, True, False, True),
        ("-0", 0, 200, 0, True, False, False),
        ("-0%", 0, 200, 0, True, True, False),
        ("1e3", 0, 200, 200, True, False, True),
        ("1e2", 0, 200, 100, True, False, False),
        ("", 0, 200, None, False, False, False),
        ("   ", 0, 200, None, False, False, False),
        ("junk", 0, 200, None, False, False, False),
        ("%", 0, 200, None, False, True, False),
        ("%%", 0, 200, None, False, True, False),
        ("1%", 0, 200, 2, True, True, False),
        ("nan", 0, 200, None, False, False, False),
        ("+nan", 0, 200, None, False, False, False),
        ("inf", 0, 200, None, False, False, False),
        ("+inf", 0, 200, None, False, False, False),
        ("-inf", 0, 200, None, False, False, False),
    ]
    for text, lo, hi, value, valid, was_percent, was_clamped in cases:
        r = parse(text, lo, hi)
        assert (r.value, r.valid, r.was_percent, r.was_clamped) == \
            (value, valid, was_percent, was_clamped), text
    # Degree-unit forms (azimuth/hue fields).
    assert parse("86°", 0, 359, "degree").value == 86
    assert parse("86deg", 0, 359, "degree").value == 86
    assert parse("86", 0, 359, "degree").value == 86
    assert parse("86%", 0, 359, "degree").value == 308.74
    assert parse("64°", 0, 100, "none").valid is False
    assert parse("64%", 0, 100, "none").value == 64
    print("  PASS typed-entry parse table")


def test_format_slider_value():
    _, fmt = _load_pure_helpers()
    assert fmt(100, True) == "100%"
    assert fmt(200, True) == "200%"
    assert fmt(180, False) == "180"
    assert fmt(8, False) == "8"
    assert fmt(180, "degree") == "180°"
    assert fmt(86, "percent") == "86%"
    assert fmt(64, "none") == "64"


def _quiet_sphere(engine_mod, res=96):
    engine = engine_mod.ColorEngine(resolution=res)
    engine.ambient = 0.0
    engine.smooth = 0.0
    engine.grain = 0.0
    engine.glow_intensity = 0.0
    engine.saturation = 1.0
    engine.brightness = 1.0
    engine.contrast = 1.0
    engine.spec_max = 0.0
    engine.set_shadow_color((1.0, 0.0, 0.0))
    engine.set_light_color((0.0, 0.0, 1.0))
    engine.set_highlight_color((1.0, 1.0, 1.0))
    engine.set_light_type("Sun")
    engine.set_light_angle(287.0, 45.0)
    return engine


def test_rim_toggle_moves_limb_pixels():
    # A/B: identical renders except rim_light 0 vs 0.14. At the strongest
    # theoretical rim pixel the 8-bit max-channel delta must clear 5.
    engine_mod = load_engine()
    res = 96
    best = None
    probe = engine_mod.ColorEngine(resolution=res)
    probe.set_light_type("Sun")
    probe.set_light_angle(287.0, 45.0)
    for row in range(res):
        for col in range(res):
            if not probe._mask_grid[row][col]:
                continue
            x = -1.0 + 2.0 * col / (res - 1.0)
            y = -1.0 + 2.0 * row / (res - 1.0)
            nx, ny, nz = probe._normal_grid[row][col]
            ldir, _ = probe._light_for_surface(
                x, y, __import__("math").sqrt(max(0.0, 1.0 - x * x - y * y)))
            ndl = max(0.0, nx * ldir[0] + ny * ldir[1] + nz * ldir[2])
            score = ((1.0 - max(0.0, nz)) ** 5) * ndl
            if best is None or score > best[0]:
                best = (score, row, col)
    assert best is not None and best[0] > 0.0
    _, row, col = best

    off = _quiet_sphere(engine_mod, res)
    off.rim_light = 0.0
    img_off = off.render((0.8, 0.4, 0.15), res, res)
    on = _quiet_sphere(engine_mod, res)
    on.rim_light = 0.14
    img_on = on.render((0.8, 0.4, 0.15), res, res)

    delta = max(abs(a - b) for a, b in zip(img_on[row][col], img_off[row][col]))
    assert delta >= 5, ((row, col), img_off[row][col], img_on[row][col])
    print(f"  PASS rim A/B delta={delta} at ({row},{col})")


def test_hemisphere_bounce_lifts_shadow_form():
    # In the unlit zone the sky-facing limb must read brighter than the
    # ground-facing limb: directional bounce, not a flat floor.
    engine_mod = load_engine()
    res = 96
    engine = _quiet_sphere(engine_mod, res)
    # Reflected light lifts the limb opposite the light (here the lower
    # one); switch it off so only the sky/ground bounce is measured.
    engine.REFLECT_STRENGTH = 0.0
    pixels = engine.render((0.5, 0.5, 0.5), res, res)
    upper, lower = [], []
    for row in range(res):
        for col in range(res):
            if not engine._mask_grid[row][col]:
                continue
            x = -1.0 + 2.0 * col / (res - 1.0)
            y = -1.0 + 2.0 * row / (res - 1.0)
            nx, ny, nz = engine._normal_grid[row][col]
            ldir, _ = engine._light_for_surface(x, y, 0.0)
            ndl = max(0.0, nx * ldir[0] + ny * ldir[1] + nz * ldir[2])
            if ndl < 0.05:
                (upper if ny < 0.0 else lower).append(sum(pixels[row][col]) / 3.0)
    assert upper and lower
    mu = sum(upper) / len(upper)
    ml = sum(lower) / len(lower)
    assert mu > ml, (mu, ml)
    print(f"  PASS hemisphere upper={mu:.1f} lower={ml:.1f}")


def test_gear_highlight_size_domain():
    # Gear owns shininess 64-8 (never the pathological wash at 1);
    # Advanced Specular keeps the full 1-64 range.
    import sys
    sys.path.insert(0, "src")
    from Lumina.sphere_docker import SphereDocker
    to_shin = SphereDocker._size_to_shininess
    to_size = SphereDocker._shininess_to_size
    assert to_shin(0) == 64.0
    assert to_shin(100) == 8.0
    assert abs(to_shin(50) - 50.0) < 1.0
    for level in (0, 25, 50, 75, 100):
        assert to_size(to_shin(level)) == level, level
    print("  PASS gear 64-8 domain")


def test_clear_timing_drops_stale_mode_samples():
    """Rolling timing averages must not leak across render-profile switches.

    Regression for the ghosts that sent an investigation after a 22 ms
    geometry average and a 45 ms smooth average during 96 px previews:
    both stages record samples in only one mode, so entering/exiting the
    external-drag preview clears them.
    """
    mod = load_engine()
    engine_cls = getattr(mod, "LightingEngine", None) or getattr(mod, "ColorEngine")
    engine = engine_cls()
    engine._record_timing("smooth", 45.0)
    engine._record_timing("geometry", 22.0)
    engine._record_timing("material", 12.0)
    engine.clear_timing("smooth", "geometry")
    assert "smooth" not in engine._timings
    assert "geometry" not in engine._timings
    assert engine._timings["material"] == [12.0]
    # Clearing a name with no samples is a no-op, not a KeyError.
    engine.clear_timing("smooth", "missing-stage")


def test_geometry_cache_survives_size_switch():
    """Switching 200 -> 96 -> 200 must reuse grids, not regenerate them.

    Run-2: the first preview after every full-quality final paid ~40 ms of
    geometry + shading rebuild. Restored pixels must be identical to a
    fresh render, and the caches must stay bounded.
    """
    mod = load_engine()
    engine_cls = getattr(mod, "LightingEngine", None) or getattr(mod, "ColorEngine")
    base = (0.8, 0.4, 0.2)
    fresh = engine_cls()
    ref200 = fresh.render(base, 200, 200)
    switched = engine_cls()
    switched.render(base, 200, 200)
    switched.render(base, 96, 96)
    again200 = switched.render(base, 200, 200)
    assert again200 == ref200
    # The init-time default size (256) is also cached; what matters is our
    # two sizes are warm and the caches stay bounded.
    assert {(200, 200), (96, 96)} <= set(switched._geo_cache)
    assert len(switched._geo_cache) <= 6
    assert len(switched._shading_cache) <= 12
    # Same size twice in a row is a pure cache hit: grids identical objects.
    before = switched._normal_grid
    switched.render(base, 200, 200)
    assert switched._normal_grid is before


def test_tuning_gate_deterministic_and_sane():
    """Q9 harness: identical runs give identical metrics; all metrics sane.

    The gate is the objective referee for the defaults rebalance. If the
    harness itself wobbles, no A/B comparison means anything.
    """
    import importlib.util as _ilu
    spec = _ilu.spec_from_file_location(
        "tuning_gate", "tools/tuning_gate.py")
    gate = _ilu.module_from_spec(spec)
    spec.loader.exec_module(gate)
    first = gate.measure()
    second = gate.measure()
    assert first == second
    assert set(first) == {"red", "green", "blue", "gray"}
    for name, m in first.items():
        assert set(m) == {"terminator_px", "shadow_luma", "highlight_dia",
                          "range", "crease"}, (name, m)
        assert m["range"] > 0.05, (name, m)
        assert m["terminator_px"] >= 0.0, (name, m)
        assert 0.0 <= m["shadow_luma"] <= 1.0, (name, m)
        assert m["highlight_dia"] >= 0.0, (name, m)
        assert m["crease"] >= 0.0, (name, m)


def test_reflected_light_lifts_the_shadow_side_edge():
    """Bounce light: the unlit side is darkest behind the terminator and
    lighter again at the silhouette, in the base colour (as the reference)."""
    engine_mod = load_engine()
    res = 81

    def profile(strength):
        e = engine_mod.ColorEngine(res)
        e.REFLECT_STRENGTH = strength
        e.set_shadow_color((0.0, 0.65, 0.58))
        e.set_light_color((1.0, 1.0, 1.0))
        e.light_azimuth, e.light_elevation = 304.0, 41.0
        e._compute_shading()
        px = e.render((0.0, 0.9, 0.0), res, res)
        # lower-left diagonal: inner shadow band vs the silhouette
        band = px[int(res * 0.75)][int(res * 0.25)]
        edge = px[int(res * 0.83)][int(res * 0.17)]
        return band, edge

    band, edge = profile(0.15)
    off_band, off_edge = profile(0.0)
    assert edge[1] > off_edge[1], (edge, off_edge)      # the edge lifts
    assert edge[1] >= band[1], (band, edge)             # lighter than the band
    print(f"  PASS reflected light band={band} edge={edge}")


def test_reflected_light_comes_from_the_side_opposite_the_light():
    """The reference lifts the limb opposite the light (lower left for a
    light at 304/41) but not the lower-right limb beside the terminator."""
    engine_mod = load_engine()
    res = 81

    def lift(row_f, col_f):
        out = []
        for strength in (engine_mod.ColorEngine.REFLECT_STRENGTH, 0.0):
            e = engine_mod.ColorEngine(res)
            e.REFLECT_STRENGTH = strength
            e.set_shadow_color((0.0, 0.65, 0.58))
            e.set_light_color((1.0, 1.0, 1.0))
            e.light_azimuth, e.light_elevation = 304.0, 41.0
            e._compute_shading()
            out.append(e.render((0.0, 0.9, 0.0), res, res)[int(res * row_f)][int(res * col_f)][1])
        return out[0] - out[1]

    opposite = lift(0.83, 0.17)      # lower-left limb
    beside = lift(0.85, 0.80)        # lower-right limb
    assert opposite > 0, opposite
    assert opposite > 2 * beside, (opposite, beside)
    print(f"  PASS directional reflect opposite={opposite} beside={beside}")


# --- Issue #45: shadow depth and the highlight -------------------------------
def _oklab(rgb8):
    lin = [((v / 255.0 + 0.055) / 1.055) ** 2.4 if v / 255.0 > 0.04045 else v / 255.0 / 12.92 for v in rgb8]
    l = 0.4122214708 * lin[0] + 0.5363325363 * lin[1] + 0.0514459929 * lin[2]
    m = 0.2119034982 * lin[0] + 0.6806995451 * lin[1] + 0.1073969566 * lin[2]
    s = 0.0883024619 * lin[0] + 0.2817188376 * lin[1] + 0.6299787005 * lin[2]
    l, m, s = (v ** (1.0 / 3.0) for v in (l, m, s))
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def _ref_sphere(engine_mod, trio, res=81, **overrides):
    """Render a reference-style sphere (light 304/41) and return a sampler
    by sphere coordinates u, v in [-1, 1]."""
    e = engine_mod.ColorEngine(res)
    for k, v in overrides.items():
        setattr(e, k, v)
    shadow, base, light = trio
    e.set_shadow_color(shadow)
    e.set_light_color(light)
    e.light_azimuth, e.light_elevation = 304.0, 41.0
    e._compute_shading()
    px = e.render(base, res, res)
    return lambda u, v: px[int(round((v + 1) / 2 * (res - 1)))][int(round((u + 1) / 2 * (res - 1)))]


BLUE = ((1 / 255, 0.0, 43 / 255), (0.0, 0.0, 181 / 255), (155 / 255, 49 / 255, 176 / 255))
BLACK = ((5 / 255, 5 / 255, 6 / 255), (36 / 255, 34 / 255, 37 / 255), (80 / 255, 79 / 255, 81 / 255))


def test_dark_shadow_keeps_its_chroma():
    """The reference's shadow is the same rich colour, darker: on its blue
    sphere chroma falls in step with lightness (C/L 0.68 at the core, the
    base's own 0.69). Environment light added as plain light washed a grey
    film over it (C/L 0.49)."""
    engine_mod = load_engine()
    f = _ref_sphere(engine_mod, BLUE)
    L, a, b = _oklab(f(-0.55, 0.55))                  # shadow core, lower left
    bl, ba, bb = _oklab(tuple(round(c * 255) for c in BLUE[1]))
    ratio, base_ratio = math.hypot(a, b) / L, math.hypot(ba, bb) / bl
    assert ratio > 0.85 * base_ratio, (ratio, base_ratio)
    print(f"  PASS shadow chroma C/L {ratio:.2f} (base {base_ratio:.2f})")


def test_dark_base_gets_a_real_highlight_reaching_the_lit_edge():
    """The reference's highlight on black #242225 peaks near L 0.75 about two
    thirds of the way toward the light and stays bright out to the lit
    silhouette (L 0.62 at r 0.95, brighter than at r 0.3)."""
    engine_mod = load_engine()
    f = _ref_sphere(engine_mod, BLACK, res=121)
    ang = math.radians(-54.0)                          # toward the light
    along = {r: _oklab(f(r * math.cos(ang), r * math.sin(ang)))[0] for r in (0.3, 0.65, 0.95)}
    assert along[0.65] > 0.68, along
    assert along[0.95] > along[0.3], along
    print("  PASS highlight " + " ".join(f"r{r}:{v:.2f}" for r, v in along.items()))


def test_shadow_hue_builds_into_the_core():
    """The hue moves toward the shadow target steadily, from the lit side of
    the terminator on into the core -- past N.L 0, which a ramp on clamped
    N.L could not do -- while the surface keeps the base's lightness (the
    lighting does the darkening)."""
    engine_mod = load_engine()
    E = engine_mod.ColorEngine
    e = E(16)
    shadow, base = (0.0, 167 / 255, 147 / 255), (0.0, 231 / 255, 0.0)
    lut = e._shadow_hue_lut(e._to_linear(shadow), e._to_linear(base))
    at = lambda sn: E._linear_to_oklab(lut[int((sn + 1.0) * 0.5 * (len(lut) - 1) + 0.5)])
    hue = lambda lab: math.degrees(math.atan2(lab[2], lab[1]))
    base_lab = E._linear_to_oklab(e._to_linear(base))
    assert abs(hue(at(0.9)) - hue(base_lab)) < 0.5            # lit side: the base
    steps = [hue(at(sn)) for sn in (E.SHADOW_HUE_START, 0.3, 0.0, -0.1, E.SHADOW_HUE_CORE)]
    assert all(b > a for a, b in zip(steps, steps[1:])), steps  # rising into the core
    assert steps[-1] - steps[2] > 3.0, steps                   # still moving past N.L 0
    assert all(abs(at(sn)[0] - base_lab[0]) < 0.01 for sn in (0.3, 0.0, -0.3))  # base lightness
    print("  PASS shadow hue " + " ".join("%.0f" % h for h in steps))


GREEN_REF = ((0.0, 0xa7 / 255, 0x93 / 255), (0.0, 0xe7 / 255, 0.0), (1.0, 1.0, 1.0))


def test_shade_grades_into_the_shadow_and_the_rim_is_a_band():
    """Issue #45, round 2. The reference brightens almost linearly from the
    terminator to N.L 0.6, a long grade into the shadow; Lumina's shade used
    to be nearly at full brightness by N.L 0.3. And its reflected rim rises
    out of the core as a band and levels off, where Lumina's was a thin line
    at the very edge (a quarter of the lift by 0.91 of the radius)."""
    engine_mod = load_engine()
    res = 121
    f = _ref_sphere(engine_mod, GREEN_REF, res=res)
    el, az = math.radians(41.0), math.radians(304.0)
    la = (math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el))

    def lightness_at(ndl):
        acc = []
        for j in range(res):
            for i in range(res):
                u, v = i / (res - 1) * 2 - 1, j / (res - 1) * 2 - 1
                if u * u + v * v > 0.7:
                    continue
                nz = math.sqrt(1 - u * u - v * v)
                if abs(u * la[0] + v * la[1] + nz * la[2] - ndl) < 0.03:
                    acc.append(_oklab(f(u, v))[0])
        return sum(acc) / len(acc)

    l1, l3, l6 = lightness_at(0.1), lightness_at(0.3), lightness_at(0.6)
    assert l3 / l6 <= 0.835, (l1, l3, l6)          # still grading at 0.3
    assert l1 / l6 >= 0.55, (l1, l3, l6)           # not crushed past it

    dx, dy = -math.cos(az), -math.sin(az)          # away from the light
    prof = [_oklab(f(dx * r, dy * r))[0] for r in (0.8, 0.84, 0.88, 0.91, 0.94, 0.98)]
    core = min(prof)
    assert prof[-1] > core, prof                   # the rim lifts
    assert (prof[3] - core) >= 0.4 * (prof[-1] - core), prof   # as a band
    print(f"  PASS shade grade {l3 / l6:.3f}, rim band {prof}")


def test_highlight_peak_matches_the_reference_brightness():
    """Issue #45: Lumina's highlight peaked 0.02-0.10 OKLab L brighter than
    the reference on every sphere (black 0.855 vs 0.76, blue 0.832 vs 0.76).
    The peak comes down to the reference's without dulling a bright base
    (green's reference peak is 0.92)."""
    engine_mod = load_engine()
    res = 121

    def peak(trio):
        f = _ref_sphere(engine_mod, trio, res=res)
        best = 0.0
        for j in range(res):
            for i in range(res):
                u, v = i / (res - 1) * 2 - 1, j / (res - 1) * 2 - 1
                if u * u + v * v <= 0.92:
                    best = max(best, _oklab(f(u, v))[0])
        return best

    black, blue, green = peak(BLACK), peak(BLUE), peak(GREEN_REF)
    assert 0.70 <= black <= 0.80, black
    assert 0.70 <= blue <= 0.80, blue
    assert green >= 0.88, green
    print(f"  PASS highlight peaks black={black:.3f} blue={blue:.3f} green={green:.3f}")
