"""Standalone shading engine test (no Krita/PyQt required for logic).

Verifies the pure-Python ColorEngine produces a valid gradient, that the
Krita-facing SphereColorProcessor adapter delegates correctly, and that the
shading math matches expectations.  Runs on ANY Python 3 interpreter — host,
CI, or inside the Krita flatpak.
"""

import sys
import math

sys.path.insert(0, ".")


def load_module(name, path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Minimal Qt stubs so color_processor.py can be exercised without PyQt5.
# (Inside Krita the real PyQt5 is used; here we just need redF()/greenF() etc.)
# ---------------------------------------------------------------------------
class FakeQColor:
    def __init__(self, r, g, b, a=255):
        self._r, self._g, self._b, self._a = r, g, b, a

    def redF(self):
        return self._r / 255.0

    def greenF(self):
        return self._g / 255.0

    def blueF(self):
        return self._b / 255.0

    def name(self):
        return f"rgb:{self._r},{self._g},{self._b}"


class FakeQImage:
    def __init__(self, w, h, fmt):
        self.w, self.h, self.fmt = w, h, fmt
        self._pixels = []

    def setPixelColor(self, x, y, color):
        self._pixels.append((x, y, color.name()))

    def pixelColor(self, x, y):
        return FakeQColor(100, 100, 100)

    def copy(self):
        return self


def _brightness(rgb):
    return sum(rgb) / 3.0


def test_engine_gradient():
    engine = load_module("color_engine", "src/Lumina/color_engine.py")
    p = engine.ColorEngine(resolution=64)
    # Use a mid-tone base color so diffuse variation is visible (not clamped).
    img = p.render((0.5, 0.3, 0.2), 64, 64)
    left = img[16][8]   # left side of the visible hemisphere
    right = img[16][56]  # right side (lit by azimuth=300 deg light)
    # Hyphen, not an em-dash: the flatpak's stdout is latin-1, so a non-ASCII
    # character in a print() raises UnicodeEncodeError there.
    print(f"Engine gradient - left : {left}   right: {right}")
    assert _brightness(left) < _brightness(right), \
        f"left should be darker, got {left} vs {right}"
    print("  ✓ gradient direction verified (light from right)")


def test_processor_delegation():
    engine = load_module("color_engine", "src/Lumina/color_engine.py")
    proc = load_module("color_processor", "src/Lumina/color_processor.py")
    # Force the non-Qt path so we can run on the host.
    proc._HAS_QT = False

    p = proc.SphereColorProcessor(resolution=64)
    buffer = p.render_image((200 / 255, 60 / 255, 60 / 255), 64, 64)
    assert len(buffer) == 64 * 64 * 4, "render_image should return packed ARGB bytes"
    print("  ✓ SphereColorProcessor.render_image returns packed pixels")

    # Verify setters funnel into the engine.
    p.set_ambient(0.5)
    p.set_light_intensity(1.5)
    p.set_contrast(1.3)
    assert abs(p.engine.ambient - 0.5) < 1e-9
    assert abs(p.engine.light_intensity - 1.5) < 1e-9
    print("  ✓ processor setters delegate to the pure engine")


def test_symmetry():
    """Shading must be symmetric about the light's meridian (the plane x=0).

    We pick an angled light so a real left-right gradient exists, then check
    that a point and its horizontal mirror (width-1-col) have equal diffuse —
    but only where both pixels are inside the visible hemisphere.  Restricting
    to interior columns guarantees the mirror is also valid.
    """
    engine = load_module("color_engine", "src/Lumina/color_engine.py")
    p = engine.ColorEngine(resolution=128)
    # Angled light -> a real left-right gradient to test symmetry on.
    p.set_light_angle(90.0, math.pi / 4)
    # Grain off: the limb breakup is per-pixel noise and is asymmetric by
    # design. This test covers the underlying lighting gradient, which must
    # stay symmetric; the grain overlay has its own determinism checks.
    p.set_grain(0.0)
    img = p.render((0.5, 0.5, 0.5), 64, 64)
    w = 64
    for row in range(10, 54):          # interior rows (stay inside hemisphere)
        for col in range(8, 24):       # interior columns whose mirror is also inside
            mirror = w - 1 - col
            assert abs(img[row][col][0] - img[row][mirror][0]) < 1e-6, \
                f"asymmetry at row {row}, col {col} vs {mirror}"
    print("  ✓ shading is symmetric about the light meridian (interior points)")


# ---------------------------------------------------------------------------
# Deriving light/shadow from a sampled base colour.
#
# derivation.py is stdlib-only, so the real model is exercised here directly,
# not a mirror of it. Loaded next to this file, so the test runs from the repo
# root or from inside the package directory.
# ---------------------------------------------------------------------------
def _derivation():
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    return load_module("lumina_derivation", os.path.join(here, "derivation.py"))


def _derive(h, s, v, model):
    """HSV base in, the model's light/shadow out as HSV floats."""
    import colorsys
    rgb = tuple(int(round(c * 255)) for c in colorsys.hsv_to_rgb(h, s, v))
    shadow, light = model.derive(rgb)
    sh = colorsys.rgb_to_hsv(*(c / 255.0 for c in shadow))
    li = colorsys.rgb_to_hsv(*(c / 255.0 for c in light))
    return {"base_rgb": rgb, "shadow_rgb": shadow, "light_rgb": light,
            "light_h": li[0], "light_s": li[1], "light_v": li[2],
            "shadow_h": sh[0], "shadow_s": sh[1], "shadow_v": sh[2]}


def test_dark_base_derivation():
    """A very dark base must still yield three distinguishable targets.

    Picking pure black once produced a shadow identical to the base and a
    neutral-grey highlight, so the sphere rendered unlit and the Hue slider did
    nothing. These invariants carried over from the old HSV rule to the OKLCH
    model in derivation.py.
    """
    model = _derivation()
    cases = [
        ("pure black", 0.0, 0.0, 0.0),
        ("near black", 0.0, 0.0, 0.04),
        ("dark grey", 0.0, 0.0, 0.25),
        ("dark colour", 0.02, 0.40, 0.18),
        ("sunset orange", 0.02, 0.75, 0.90),
    ]
    for label, h, s, v in cases:
        d = _derive(h, s, v, model)
        if d["base_rgb"] == (0, 0, 0):
            continue
        # The shadow is darker than the light, and darker than its base...
        assert d["shadow_v"] < d["light_v"], \
            "{0}: shadow {1:.3f} not below light {2:.3f}".format(
                label, d["shadow_v"], d["light_v"])
        assert d["shadow_v"] < v - 1e-9, \
            "{0}: shadow_v {1:.3f} is not darker than base v={2:.3f}".format(
                label, d["shadow_v"], v)
        # ...but never pure black unless the base is: a shadow that collapses
        # to black cannot be told apart from it.
        assert d["shadow_rgb"] != (0, 0, 0), \
            "{0}: a non-black base derived a black shadow".format(label)
        print("  \u2713 {0:<15} base v={1:.2f} -> light v={2:.3f} s={3:.3f}  "
              "shadow v={4:.3f} s={5:.3f}".format(
                  label, v, d["light_v"], d["light_s"],
                  d["shadow_v"], d["shadow_s"]))

    # A tint of black is black: picking #000000 leaves light and shadow at 0
    # rather than inventing a highlight.
    black = _derive(0.0, 0.0, 0.0, model)
    assert black["light_v"] == 0.0, \
        "pure black produced a highlight at v={0:.3f}".format(black["light_v"])
    assert black["shadow_v"] == 0.0, "shadow should be black for a black base"
    print("  \u2713 pure black stays black all the way through")

    # A dark grey's saturation is carried through, not boosted: the reference
    # reports #3F3C3C as S=5 and #151513 as S=10, the pick's own saturation.
    grey = _derive(0.0, 0.05, 0.25, model)
    assert grey["light_s"] < 0.06, \
        "a low-saturation grey produced a boosted highlight ({0:.3f})".format(
            grey["light_s"])
    print("  \u2713 low-saturation greys stay low-saturation, as the pick had them")


def test_no_complex_crash():
    """Bright pick + contrast must never produce complex pixels (Matte crash).

    The tone-mapping curve raises (1 - luma) to a fractional power; once the
    specular core pushes luma past 1 that base goes negative, which is complex
    in Python and crashed round() in _rgb_from_floats. The bases are clamped
    at zero now -- over-bright maps to over-bright. Sweeps the full slider
    extremes, not just the reported combo.
    """
    engine = load_module("color_engine", "src/Lumina/color_engine.py")
    n = 0
    for base in [(1.0, 0.15, 0.70), (1.0, 1.0, 1.0), (0.9, 0.45, 0.15)]:
        for contrast in [0.0, 0.5, 0.82, 1.5, 3.0]:
            for intensity in [0.0, 1.0, 2.0]:
                for shininess in [1, 8, 128]:
                    p = engine.ColorEngine(resolution=32)
                    p.set_contrast(contrast)
                    p.set_light_intensity(intensity)
                    p.set_shininess(shininess)
                    img = p.render(base, 32, 32)
                    for row in img:
                        for px in row:
                            assert all(isinstance(c, int) for c in px), \
                                "non-int pixel %r (base=%s c=%s i=%s s=%s)" % (
                                    px, base, contrast, intensity, shininess)
                    n += 1
    print("  ✓ %d bright/contrast/intensity combos render real pixels" % n)


def main():
    print("Running Lumina shading tests...\n")
    test_engine_gradient()
    test_processor_delegation()
    test_symmetry()
    test_dark_base_derivation()
    test_no_complex_crash()
    print("\nALL SHADING TESTS PASSED ✅")


if __name__ == "__main__":
    main()
