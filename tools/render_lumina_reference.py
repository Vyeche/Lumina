#!/usr/bin/env python3
"""Developer-only visual calibration: render reference spheres to PPM/PNG.

Not part of the automated suite (writes files). Run:
    python3 tools/render_lumina_reference.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "Lumina"))

from color_engine import ColorEngine


def write_ppm(path: Path, pixels):
    h, w = len(pixels), len(pixels[0])
    with path.open("wb") as f:
        f.write(f"P6\n{w} {h}\n255\n".encode("ascii"))
        for row in pixels:
            for r, g, b in row:
                f.write(bytes((r, g, b)))


def main():
    res = 200
    base = (205 / 255, 92 / 255, 92 / 255)
    out = Path(__file__).resolve().parent / "lumina_reference"
    out.mkdir(parents=True, exist_ok=True)
    for lt in ("Point", "Sun", "Spot", "Area"):
        e = ColorEngine(resolution=res)
        e.set_shadow_color((62 / 255, 66 / 255, 96 / 255))
        e.set_light_color((248 / 255, 206 / 255, 132 / 255))
        e.set_highlight_color((1.0, 1.0, 1.0))
        e.set_light_type(lt)
        e.set_smooth(0)
        px = e.render(base, res, res)
        p = out / f"sphere_{lt.lower()}.ppm"
        write_ppm(p, px)
        print(f"{lt}: wrote {p}")


if __name__ == "__main__":
    main()
