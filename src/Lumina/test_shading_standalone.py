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
    img = p.render((0.5, 0.5, 0.5), 64, 64)
    w = 64
    for row in range(10, 54):          # interior rows (stay inside hemisphere)
        for col in range(8, 24):       # interior columns whose mirror is also inside
            mirror = w - 1 - col
            assert abs(img[row][col][0] - img[row][mirror][0]) < 1e-6, \
                f"asymmetry at row {row}, col {col} vs {mirror}"
    print("  ✓ shading is symmetric about the light meridian (interior points)")


def main():
    print("Running Lumina shading tests...\n")
    test_engine_gradient()
    test_processor_delegation()
    test_symmetry()
    print("\nALL SHADING TESTS PASSED ✅")


if __name__ == "__main__":
    main()
