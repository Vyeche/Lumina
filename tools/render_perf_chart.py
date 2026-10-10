#!/usr/bin/env python3
"""Measure the render pipeline before and after issue #56 and chart it.

Times the original pipeline (ColorEngine.render_reference + the processor's
packer) against the restructured one (render_bgra) at 288 px, the docker's
usual full-quality size, for the four kinds of edit, and draws
images/render_speed.png. Best of three runs each; numbers are this machine's.

    python3 tools/render_perf_chart.py
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "Lumina"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import color_engine as CE          # noqa: E402
import color_processor as CP       # noqa: E402

SIZE = 288
SURFACE = "#fcfcfb"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID = "#e4e3df"
SERIES = ("#2a78d6", "#eb6834")       # slot 1, slot 2 (validated pair)


def best_of(fn, runs=3):
    best = None
    for _ in range(runs):
        t = time.perf_counter()
        fn()
        ms = (time.perf_counter() - t) * 1000.0
        best = ms if best is None else min(best, ms)
    return best


def measure():
    """(label, reference ms, fast ms) per kind of edit."""
    packer = CP.SphereColorProcessor(resolution=SIZE)

    def reference(e, base):
        packer._build_buffer(e.render_reference(base, SIZE, SIZE), SIZE, SIZE, e)

    def fast(e, base):
        e.render_bgra(base, SIZE, SIZE)

    rows = []
    edits = (
        ("Colour change (Hue / Sat / Value)", lambda e, k: None, "Sun"),
        ("Lighting slider (Ambient)", lambda e, k: e.set_ambient(0.05 + 0.01 * k), "Sun"),
        ("Moving the Sun", lambda e, k: e.set_light_angle(250 + 7 * k, 35), "Sun"),
        ("Moving a Point / Area lamp", lambda e, k: e.set_light_angle(250 + 7 * k, 35), "Area"),
    )
    for label, change, lamp in edits:
        times = []
        for render in (reference, fast):
            e = CE.ColorEngine(SIZE)
            e.light_type = lamp
            render(e, (0.9, 0.4, 0.1))          # warm up at this size
            counter = {"k": 0}

            def one():
                counter["k"] += 1
                k = counter["k"]
                change(e, k)
                render(e, (0.2 + 0.05 * (k % 4), 0.5, 0.9))
            times.append(best_of(one))
        rows.append((label, times[0], times[1]))
        print("%-38s before %4.0f ms   after %4.0f ms" % (label, times[0], times[1]))
    return rows


def draw(rows, path):
    from PyQt5.QtCore import QRectF, Qt
    from PyQt5.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841 (fonts)

    W, H = 860, 120 + 64 * len(rows)
    left, right, top = 270, 70, 92
    img = QImage(W, H, QImage.Format_RGB32)
    img.fill(QColor(SURFACE))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)

    def font(px, bold=False):
        f = QFont("Sans")
        f.setPixelSize(px)
        f.setBold(bold)
        return f

    p.setPen(QColor(TEXT_PRIMARY))
    p.setFont(font(18, True))
    p.drawText(24, 34, "Render time at 288 px, before and after the restructure (#56)")
    p.setPen(QColor(TEXT_SECONDARY))
    p.setFont(font(13))
    p.drawText(24, 56, "Milliseconds, best of three, same machine. Lower is better; output is bit-identical.")

    # legend
    lx = left
    for name, colour in (("Before", SERIES[0]), ("After", SERIES[1])):
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(colour))
        p.drawRoundedRect(QRectF(lx, 70, 12, 12), 3, 3)
        p.setPen(QColor(TEXT_PRIMARY))
        p.setFont(font(13))
        p.drawText(lx + 18, 81, name)
        lx += 90

    top = 104
    plot_w = W - left - right
    vmax = max(max(r[1], r[2]) for r in rows)
    step = 100 if vmax > 300 else 50
    scale_max = ((int(vmax) // step) + 1) * step
    x_of = lambda v: left + plot_w * v / scale_max

    # recessive grid
    p.setFont(font(11))
    for v in range(0, scale_max + 1, step):
        x = x_of(v)
        p.setPen(QPen(QColor(GRID), 1))
        p.drawLine(int(x), top - 6, int(x), H - 30)
        p.setPen(QColor(TEXT_SECONDARY))
        p.drawText(QRectF(x - 30, H - 26, 60, 16), Qt.AlignCenter, "%d" % v)

    bar_h, gap = 16, 2
    for idx, (label, before, after) in enumerate(rows):
        y = top + idx * 64
        p.setPen(QColor(TEXT_PRIMARY))
        p.setFont(font(13))
        p.drawText(QRectF(16, y, left - 28, 2 * bar_h + gap), Qt.AlignRight | Qt.AlignVCenter, label)
        for k, (value, colour) in enumerate(((before, SERIES[0]), (after, SERIES[1]))):
            by = y + k * (bar_h + gap)
            x0, x1 = left, x_of(value)
            # Square at the baseline, 4 px rounded at the data end.
            bar = QPainterPath()
            r = min(4.0, (x1 - x0) / 2.0)
            bar.moveTo(x0, by)
            bar.lineTo(x1 - r, by)
            bar.quadTo(x1, by, x1, by + r)
            bar.lineTo(x1, by + bar_h - r)
            bar.quadTo(x1, by + bar_h, x1 - r, by + bar_h)
            bar.lineTo(x0, by + bar_h)
            bar.closeSubpath()
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(colour))
            p.drawPath(bar)
            p.setPen(QColor(TEXT_PRIMARY))
            p.setFont(font(12, k == 1))
            p.drawText(QRectF(x1 + 6, by - 1, 90, bar_h + 2), Qt.AlignLeft | Qt.AlignVCenter,
                       "%.0f ms" % value)
        speed = before / after if after > 0 else 0.0
        p.setPen(QColor(TEXT_SECONDARY))
        p.setFont(font(12))
        p.drawText(QRectF(16, y + 2 * bar_h + gap + 2, left - 28, 16),
                   Qt.AlignRight | Qt.AlignVCenter, "%.1f× faster" % speed)
    p.end()
    img.save(path)
    print("wrote", os.path.relpath(path, ROOT))


if __name__ == "__main__":
    draw(measure(), os.path.join(ROOT, "images", "render_speed.png"))
