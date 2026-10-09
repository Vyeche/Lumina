#!/usr/bin/env python3
"""Calibration grid (peer Q1/Q3 follow-up): derivation across the wheel.

For each base: distribute like the eyedropper path and record the
shadow/base/light triples. Separates hue effects (spectral sweep at
fixed sat/value) from saturation/value effects (red/gray ramps), so a
candidate model can be judged on structure instead of four anecdotes.

Usage:
    python3 tools/calibration_grid.py
    python3 tools/calibration_grid.py --json
"""
import colorsys
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QColor

app = QApplication([])

from Lumina.sphere_docker import SphereDocker

GRID = {}
for deg in (0, 30, 60, 90, 120, 150, 180, 210, 240, 270, 300, 330):
    r, g, b = colorsys.hsv_to_rgb(deg / 360.0, 0.85, 0.90)
    GRID["h%03d" % deg] = (r, g, b)
GRID["red-sat1"] = colorsys.hsv_to_rgb(0.0, 1.0, 1.0)
GRID["red-sat25"] = colorsys.hsv_to_rgb(0.0, 0.25, 0.85)
GRID["gray-25"] = (0.25, 0.25, 0.25)
GRID["gray-55"] = (0.55, 0.55, 0.55)
GRID["gray-80"] = (0.80, 0.80, 0.80)
GRID["purple-ref"] = (0x74 / 255.0, 0x33 / 255.0, 0x56 / 255.0)


def _hsv(color):
    h, s, v = color.getHsvF()[0], color.getHsvF()[1], color.getHsvF()[2]
    return (round(h * 359) % 360 if h >= 0 else -1,
            round(s * 100), round(v * 100))


def main():
    d = SphereDocker()
    rows = {}
    for name, rgb in GRID.items():
        base = QColor.fromRgbF(*rgb)
        d._distribute_from(base)
        t = d._targets
        rows[name] = {
            "base": (t["base"].name(), _hsv(t["base"])),
            "shadow": (t["shadow"].name(), _hsv(t["shadow"])),
            "light": (t["light"].name(), _hsv(t["light"])),
        }
    if "--json" in sys.argv:
        print(json.dumps(rows, indent=1))
        return
    print("%-10s %-9s %-9s %-9s" % ("base", "shadow", "base", "light"))
    for name, r in rows.items():
        print("%-10s %-9s %-9s %-9s  s:(%d,%d,%d)" % (
            name, r["shadow"][0], r["base"][0], r["light"][0],
            r["shadow"][1][1], r["base"][1][1], r["light"][1][1]))


if __name__ == "__main__":
    main()
