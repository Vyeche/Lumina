#!/usr/bin/env python3
"""Q9 tuning gate: headless A/B metrics for the lighting defaults.

Renders the canonical scene (Sun, 287deg/45deg, intensity 1.0, engine
defaults) for four swatches (red/green/blue at equal sat/value + neutral
gray) and reports five metrics per swatch:

  terminator_px  width of the 25%..75% falloff along the center row
  shadow_luma    mean luma of the darkest 10% of sphere pixels
  highlight_dia  equivalent diameter of pixels >= 90% of range
  range          max - min luma (modeling must survive smoothing)
  crease         max 2nd-difference along center row/col (kink detector)

Usage:
    python3 tools/tuning_gate.py            # print baseline table
    python3 tools/tuning_gate.py --json     # machine-readable for A/B diffs

A default set is better when, across all four swatches, the terminator
widens, shadow luma rises within its ceiling, highlight diameter holds or
shrinks, range is retained, and crease falls. No single metric may be
optimized at the others' expense.
"""

import colorsys
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "Lumina"))

from color_engine import ColorEngine

SIZE = 200
SWATCHES = {
    # Equal sat/value chromatics + one neutral gray (luminance-only read).
    "red": colorsys.hsv_to_rgb(0.0, 0.75, 0.85),
    "green": colorsys.hsv_to_rgb(1 / 3, 0.75, 0.85),
    "blue": colorsys.hsv_to_rgb(2 / 3, 0.75, 0.85),
    "gray": (0.55, 0.55, 0.55),
}


def canonical_derivation(base):
    """Fixed shadow/light for a base. NOT the docker's harmonization.

    Held constant across runs so that only engine defaults move the
    metrics. Light leans toward white, shadow toward black with a cool lift.
    """
    r, g, b = base
    light = (r + (1.0 - r) * 0.55, g + (1.0 - g) * 0.55, b + (1.0 - b) * 0.55)
    shadow = (r * 0.30, g * 0.30, min(1.0, b * 0.30 + 0.03))
    return light, shadow


def _luma(px):
    r, g, b = px[0] / 255.0, px[1] / 255.0, px[2] / 255.0
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def analyze(pixels):
    h = len(pixels)
    w = len(pixels[0])
    lum = [[_luma(p) for p in row] for row in pixels]
    masked = [v for row in lum for v in row
              if not (v == 0.0)]
    # Outside-mask pixels render exactly black; drop true-black interior
    # pixels from the floor estimate by using a low percentile instead.
    ordered = sorted(masked)
    n = len(ordered)
    lo = sum(ordered[: max(1, n // 10)]) / max(1, n // 10)
    hi = ordered[-1]
    rng = hi - lo if hi > lo else 1e-9

    # Terminator width along the center row, bright side -> dark side.
    mid = lum[h // 2]
    # Bright end is the max; walk outward from it in both directions and
    # take the widest 25%..75% span (robust to highlight placement).
    bt = [i for i, v in enumerate(mid) if v > 0.0]
    x25 = x75 = None
    if bt:
        seg = mid[min(bt): max(bt) + 1]
        peak = max(seg)
        floor = min(seg)
        span = peak - floor if peak > floor else 1e-9
        over25 = [i for i, v in enumerate(seg) if v >= floor + 0.25 * span]
        over75 = [i for i, v in enumerate(seg) if v >= floor + 0.75 * span]
        if over25 and over75:
            x25 = (min(over25), max(over25))
            x75 = (min(over75), max(over75))
    width = (max(x25) - min(x25)) - (max(x75) - min(x75)) if x25 else 0.0
    width = abs(width)

    # Highlight equivalent diameter.
    hot = sum(1 for v in masked if v >= lo + 0.90 * rng)
    dia = 2.0 * math.sqrt(hot / math.pi) if hot else 0.0

    # Crease detector: max 2nd difference along center row/col, skipping
    # the mask rim (first/last live pixel) where the edge itself spikes.
    crease = 0.0
    row = [v for v in mid if v > 0.0][1:-1]
    for i in range(1, len(row) - 1):
        crease = max(crease, abs(row[i - 1] - 2 * row[i] + row[i + 1]))
    col = [lum[y][w // 2] for y in range(h)]
    col = [v for v in col if v > 0.0][1:-1]
    for i in range(1, len(col) - 1):
        crease = max(crease, abs(col[i - 1] - 2 * col[i] + col[i + 1]))

    return {
        "terminator_px": round(width, 1),
        "shadow_luma": round(lo, 4),
        "highlight_dia": round(dia, 1),
        "range": round(rng, 4),
        "crease": round(crease, 5),
    }


def measure(engine=None):
    results = {}
    for name, base in SWATCHES.items():
        eng = engine or ColorEngine()
        light, shadow = canonical_derivation(base)
        eng.set_light_type("Sun")
        eng.set_light_angle(287, 45)
        eng.set_light_color(light)
        eng.set_shadow_color(shadow)
        pixels = eng.render(base, SIZE, SIZE)
        results[name] = analyze(pixels)
    return results


def main():
    results = measure()
    if "--json" in sys.argv:
        print(json.dumps(results, indent=1))
        return
    print("%-8s %12s %12s %13s %8s %8s" %
          ("swatch", "terminator", "shadow_luma", "highlight_dia", "range", "crease"))
    for name, m in results.items():
        print("%-8s %12.1f %12.4f %13.1f %8.4f %8.5f" % (
            name, m["terminator_px"], m["shadow_luma"],
            m["highlight_dia"], m["range"], m["crease"]))


if __name__ == "__main__":
    main()
