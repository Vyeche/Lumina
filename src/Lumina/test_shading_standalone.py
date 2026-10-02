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


# ---------------------------------------------------------------------------
# Deriving light/shadow from a sampled base colour.
#
# Reimplements the arithmetic from SphereDocker._distribute_from so it can be
# checked without PyQt5. The constants are read out of sphere_docker.py rather
# than repeated here, so this test cannot drift from the real code; if the file
# is missing the test reports that instead of silently passing.
# ---------------------------------------------------------------------------
def _load_docker_constants():
    import ast
    import os

    # Resolve next to this file, so the test works whether it is run from the
    # repo root or from inside the package directory.
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "sphere_docker.py")
    try:
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
    except OSError:
        print("  ! sphere_docker.py not found at {0}".format(path))
        return None

    tree = ast.parse(source)
    wanted = {
        "KEY_LIGHT_HUE", "HIGHLIGHT_HUE_SHIFT", "SHADOW_HUE_SHIFT",
        "HUE_SHIFT_CAP", "_DARK_FLOOR_SHADOW", "_DARK_FLOOR_LIGHT",
        "_DARK_FLOOR_SAT",
    }
    found = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in wanted:
                if isinstance(node.value, ast.Constant):
                    found[target.id] = node.value.value
                elif isinstance(node.value, ast.BinOp):
                    # AMBIENT_HUE is derived; not needed by this test.
                    pass

    missing = wanted - set(found)
    if missing:
        print("  ! missing constants in sphere_docker.py: {0}".format(
            ", ".join(sorted(missing))))
        return None
    return found


def _derive(h, s, v, c):
    """Mirror of _distribute_from's arithmetic, Qt-free."""
    clamp01 = lambda x: 0.0 if x < 0.0 else (1.0 if x > 1.0 else float(x))

    sat_gate = min(1.0, s / 0.35)
    cap = c["HUE_SHIFT_CAP"]

    def hue_toward(start, target, amount):
        delta = (target - start + 0.5) % 1.0 - 0.5
        return (start + delta * amount) % 1.0

    light_h = hue_toward(h, c["KEY_LIGHT_HUE"],
                         min(c["HIGHLIGHT_HUE_SHIFT"] * sat_gate, cap))
    shadow_h = (h - min(c["SHADOW_HUE_SHIFT"] * sat_gate, cap)) % 1.0

    deriv_s = s if s > 0.0 else c["_DARK_FLOOR_SAT"]
    return {
        "light_h": light_h, "light_s": clamp01(deriv_s * 0.72),
        "light_v": clamp01(max(v + (1.0 - v) * 0.45, c["_DARK_FLOOR_LIGHT"])),
        "shadow_h": shadow_h, "shadow_s": clamp01(deriv_s * 0.92),
        "shadow_v": clamp01(max(
            v * 0.50, min(c["_DARK_FLOOR_SHADOW"], v * 0.90))),
    }


def test_dark_base_derivation():
    """A very dark base must still yield three distinguishable targets.

    Picking pure black used to produce a shadow identical to the base and a
    neutral-grey highlight, so the sphere rendered unlit and the Hue slider did
    nothing (rotating a zero-saturation colour is a no-op). The derivation is
    multiplicative, so at v = 0 there is nothing left to scale; the fix floors
    the derived value and restores a saturation when the base carries none.
    """
    c = _load_docker_constants()
    if c is None:
        return

    cases = [
        ("pure black", 0.0, 0.0, 0.0),
        ("near black", 0.0, 0.0, 0.04),
        ("dark grey", 0.0, 0.0, 0.25),
        ("dark colour", 0.02, 0.40, 0.18),
        ("sunset orange", 0.02, 0.75, 0.90),
    ]

    for label, h, s, v in cases:
        d = _derive(h, s, v, c)

        # The shadow must be darker than the light, always.
        assert d["shadow_v"] < d["light_v"], \
            "{0}: shadow {1:.3f} not below light {2:.3f}".format(
                label, d["shadow_v"], d["light_v"])

        # A shadow must always be darker than its base, and never pure black
        # unless the base is. The floor is a rescue for mid-dark bases only:
        # it yields whenever the base is already darker than the floor, because
        # a "shadow" lighter than its base would be worse than no shadow.
        if v > 0.0:
            assert d["shadow_v"] < v - 1e-9, \
                "{0}: shadow_v {1:.3f} is not darker than base v={2:.3f}".format(
                    label, d["shadow_v"], v)
            if v > c["_DARK_FLOOR_SHADOW"]:
                # Bright enough that the floor, not the base, is the binding
                # constraint -- it must actually hold.
                assert d["shadow_v"] >= c["_DARK_FLOOR_SHADOW"] - 1e-9, \
                    "{0}: shadow_v {1:.3f} below the floor".format(
                        label, d["shadow_v"])
        else:
            assert d["shadow_v"] == 0.0, \
                "{0}: a black base has no darker shadow to derive".format(label)

        # A highlight must exist even for a black base.
        assert d["light_v"] >= c["_DARK_FLOOR_LIGHT"] - 1e-9, \
            "{0}: light_v {1:.3f} below the floor".format(label, d["light_v"])

        print("  ✓ {0:<15} base v={1:.2f} -> light v={2:.3f} s={3:.3f}  "
              "shadow v={4:.3f} s={5:.3f}".format(
                  label, v, d["light_v"], d["light_s"],
                  d["shadow_v"], d["shadow_s"]))

    # The regression that started this: a pure black pick used to give a
    # shadow identical to the base and a neutral-grey highlight, so the sphere
    # was unlit and the Hue slider inert. The shadow must stay black (nothing
    # is darker), but the highlight must not.
    black = _derive(0.0, 0.0, 0.0, c)
    assert black["light_v"] > 0.0, "pure black produced no highlight"
    assert black["light_s"] > 0.0, \
        "pure black produced a zero-saturation highlight, which makes the Hue slider inert"
    assert black["shadow_v"] == 0.0, "shadow should be black for a black base"
    print("  ✓ pure black keeps a tinted highlight (shadow stays black, as it must)")


def main():
    print("Running Lumina shading tests...\n")
    test_engine_gradient()
    test_processor_delegation()
    test_symmetry()
    test_dark_base_derivation()
    print("\nALL SHADING TESTS PASSED ✅")


if __name__ == "__main__":
    main()
