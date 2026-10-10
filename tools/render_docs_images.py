#!/usr/bin/env python3
"""Render the documentation images and the demo GIF from the real panel.

Runs the actual SphereDocker headless (Qt offscreen) with its settings in a
throwaway folder, so it never reads or writes your own Lumina settings, and
drives it the way a user would. Everything it writes is reproducible.

    python3 tools/render_docs_images.py                  # all images + GIF
    python3 tools/render_docs_images.py --icon PATH      # Krita's Color Sampler
                                                         # icon for the header

Outputs (in images/): step1_panel.png, step2_edited.png, step3_preset.png,
step4_sampled.png, Lumina_preset_{nocturne,gloss,neon}.png, Lumina_lamps.png
(the four lamps over their form-zone maps), lumina_demo.gif and
lumina_zones.gif (the zone readout under each lamp). The GIFs need ffmpeg.

The feature image (Lumina_feature.png) is a screenshot of Krita itself and is
taken in Krita, not here.
"""
import argparse
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "images")

# Isolate the settings before Qt is imported: QSettings reads XDG_CONFIG_HOME.
_CONFIG = tempfile.mkdtemp(prefix="lumina-docs-config-")
os.environ["XDG_CONFIG_HOME"] = _CONFIG
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(ROOT, "src"))

from PyQt5.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PyQt5.QtGui import (QColor, QImage, QMouseEvent, QPainter, QPainterPath,  # noqa: E402
                         QPen)
from PyQt5.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

import Lumina.sphere_docker as SD  # noqa: E402

PANEL_W, PANEL_H = 380, 900
BG = QColor(48, 52, 58)


def use_krita_icon(path):
    """Paint the eyedropper with Krita's own icon, as it is inside Krita."""
    if not path or not os.path.exists(path):
        return
    src = QImage(path)

    def glyph(cls, name, size, col):
        if name != "eyedropper":
            return None
        img = src.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        img = img.convertToFormat(QImage.Format_ARGB32_Premultiplied)
        p = QPainter(img)
        p.setCompositionMode(QPainter.CompositionMode_SourceIn)
        p.fillRect(img.rect(), col)
        p.end()
        return img
    SD.ToolButton._krita_glyph = classmethod(glyph)


def pump(seconds=0.0):
    end = time.monotonic() + seconds
    while True:
        app.processEvents()
        if time.monotonic() >= end:
            break
        time.sleep(0.004)


def new_panel():
    d = SD.SphereDocker()
    d._send_to_krita = lambda c: None      # no Krita here
    d._save_settings = lambda: None
    d.resize(PANEL_W, PANEL_H)
    d.show()
    pump(0.2)
    d._rebuild_sphere_now()
    pump(0.1)
    return d


def content_height(d):
    """Grab down to the bottom of the Advanced bar (no empty panel below)."""
    adv = d.advanced
    return adv.mapTo(d, QPoint(0, adv.height())).y() + 10


def grab(d, height=None):
    img = d.grab().toImage()
    if height:
        img = img.copy(0, 0, img.width(), min(img.height(), height))
    return img


def save(img, name):
    path = os.path.join(OUT, name)
    img.save(path)
    print("wrote", os.path.relpath(path, ROOT), "%dx%d" % (img.width(), img.height()))


def sphere_shot(d, name):
    """The sphere alone on a dark ground, as in the preset strip."""
    d._rebuild_sphere_now()
    pump(0.05)
    img = d._render_sphere()
    out = QImage(img.width(), img.height(), QImage.Format_RGB32)
    out.fill(QColor(20, 22, 26))
    p = QPainter(out)
    p.drawImage(0, 0, img)
    p.end()
    save(out, name)


# -------------------------------------------------------------- stills
def stills():
    d = new_panel()
    h = content_height(d)
    save(grab(d, h), "step1_panel.png")                      # defaults

    d._on_target_changed("base")
    d._assign_target(QColor("#3d6fc8"))                      # a cool blue
    pump(0.3)
    save(grab(d, h), "step2_edited.png")

    d._on_preset_clicked("nocturne", True)
    pump(0.3)
    d._rebuild_sphere_now()
    pump(0.1)
    save(grab(d, h), "step3_preset.png")

    d._on_preset_clicked("nocturne", True)                   # off again
    pump(0.3)
    d._apply_sampled_color((0x2C, 0x6B, 0x74))               # sample the sea
    pump(0.3)
    d._rebuild_sphere_now()
    pump(0.1)
    save(grab(d, h), "step4_sampled.png")

    d._assign_target(QColor("#ff8a4e"))                      # sunset orange
    for key in ("nocturne", "gloss", "neon"):
        d._on_preset_clicked(key, True)
        pump(0.3)
        sphere_shot(d, "Lumina_preset_%s.png" % key)
        d._on_preset_clicked(key, True)
        pump(0.2)
    d.close()


# ------------------------------------------------------ lamps and zones
ZONE_COLOURS = (("Highlight", (255, 255, 255)), ("Light", (240, 200, 90)),
                ("Halftone", (200, 120, 60)), ("Terminator", (150, 40, 40)),
                ("Core shadow", (52, 42, 78)), ("Reflected light", (70, 140, 200)))
LAMPS = ("Sun", "Point", "Spot", "Area")
LAMP_NOTES = {"Sun": "even, distant", "Point": "a nearby bulb",
              "Spot": "a soft-edged beam", "Area": "a broad panel"}


def set_lamp(d, mode):
    d._on_light_type_changed(mode)
    d.light_type_row.set_mode(mode)
    d.processor.clear_full_cache()
    d._rebuild_sphere_now()
    pump(0.05)


def lamps_still():
    """The same colour under the four lamps, each over its form-zone map."""
    from PyQt5.QtGui import QFont
    d = new_panel()
    d._on_target_changed("base")
    d._assign_target(QColor("#ea6218"))
    pump(0.2)
    S, gap, top, label_h = 200, 16, 30, 34
    colours = dict(ZONE_COLOURS)
    out = QImage(4 * S + 5 * gap, top + 2 * S + gap + label_h + 46, QImage.Format_RGB32)
    out.fill(QColor(24, 26, 31))
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing, True)
    for i, mode in enumerate(LAMPS):
        set_lamp(d, mode)
        x = gap + i * (S + gap)
        img = d._render_sphere().scaled(S, S, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        p.drawImage(x, top, img)
        zone = QImage(S, S, QImage.Format_ARGB32)
        zone.fill(QColor(0, 0, 0, 0))
        eng = d.processor.engine
        for yy in range(S):
            for xx in range(S):
                u, v = -1 + 2 * (xx + 0.5) / S, -1 + 2 * (yy + 0.5) / S
                if u * u + v * v <= 1.0:
                    zone.setPixelColor(xx, yy, QColor(*colours[eng.form_zone(u, v)]))
        p.drawImage(x, top + S + gap, zone)
        p.setPen(QColor(236, 240, 246))
        f = QFont("Sans"); f.setPixelSize(15); f.setBold(True); p.setFont(f)
        p.drawText(x, 0, S, top - 6, Qt.AlignHCenter | Qt.AlignBottom, mode)
        f.setBold(False); f.setPixelSize(12); p.setFont(f); p.setPen(QColor(170, 178, 192))
        p.drawText(x, top + 2 * S + gap, S, label_h, Qt.AlignHCenter | Qt.AlignVCenter, LAMP_NOTES[mode])
    # legend
    f = QFont("Sans"); f.setPixelSize(12); p.setFont(f)
    lx, ly = gap, top + 2 * S + gap + label_h + 12
    for name, rgb in ZONE_COLOURS:
        p.setPen(QColor(90, 96, 108)); p.setBrush(QColor(*rgb)); p.drawRect(lx, ly, 12, 12)
        p.setPen(QColor(200, 206, 216))
        w = p.fontMetrics().horizontalAdvance(name)
        p.drawText(lx + 18, ly + 11, name)
        lx += 18 + w + 22
    p.end()
    set_lamp(d, "Sun")
    save(out, "Lumina_lamps.png")
    d.close()


def zones_gif(fps=12):
    """The lemon sweeps from the highlight to the far rim under each lamp,
    and the line under the sphere names each zone it crosses."""
    d = new_panel()
    d._on_target_changed("base")
    d._assign_target(QColor("#ea6218"))
    d._recent.set_colors([])
    pump(0.2)
    sw = d._sphere
    frames_dir = tempfile.mkdtemp(prefix="lumina-zones-")
    frames = []
    top = d.light_type_row.mapTo(d, QPoint(0, 0)).y() - 6
    bottom = d._readout.mapTo(d, QPoint(0, d._readout.height())).y() + 8

    def shot(hold=1):
        img = grab(d).copy(0, top, d.width(), bottom - top)
        for _ in range(hold):
            path = os.path.join(frames_dir, "f%04d.png" % len(frames))
            img.save(path)
            frames.append(path)

    def sphere_point(u, v):
        ox, oy, dia = sw._sphere_geometry()
        return QPointF(ox + (u + 1) * dia / 2.0, oy + (v + 1) * dia / 2.0)

    # From the highlight (toward the light, upper right) across the form to
    # the rim facing away from it (lower left).
    az = math.radians(304.0)
    lx, ly = math.cos(az), math.sin(az)
    path = [(lx * t, ly * t) for t in [0.55 - i * 0.07 for i in range(22)]]
    for mode in LAMPS:
        set_lamp(d, mode)
        sw._sample(QPointF(-500.0, -500.0))
        pump(0.05)
        shot(hold=5)                                         # the lamp clicks over
        for u, v in path:
            sw._sample(sphere_point(u, v))
            pump(1.0 / fps)
            shot()
        shot(hold=6)
    sw._sample(QPointF(-500.0, -500.0))
    set_lamp(d, "Sun")

    out = os.path.join(OUT, "lumina_zones.gif")
    pattern = os.path.join(frames_dir, "f%04d.png")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-framerate", str(fps), "-i", pattern,
                    "-lavfi", "scale=%d:-1:flags=lanczos,split [a][b]; [a] palettegen=stats_mode=full:max_colors=256 [p];"
                    " [b][p] paletteuse=dither=sierra2_4a" % 360,
                    "-loop", "0", out], check=True)
    print("wrote", os.path.relpath(out, ROOT), "%d frames, %.1f s, %d KB" % (
        len(frames), len(frames) / float(fps), os.path.getsize(out) // 1024))
    shutil.rmtree(frames_dir, ignore_errors=True)
    d.close()


# ---------------------------------------------------------------- GIF
def arrow(p, pos):
    """A plain arrow pointer, drawn where a click lands off the sphere."""
    x, y = pos.x(), pos.y()
    path = QPainterPath(QPointF(x, y))
    for dx, dy in ((0, 16), (4, 12), (7, 19), (10, 18), (7, 11), (12, 11)):
        path.lineTo(x + dx, y + dy)
    path.closeSubpath()
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(QPen(QColor(0, 0, 0), 1.2))
    p.setBrush(QColor(255, 255, 255))
    p.drawPath(path)


def demo_gif(fps=12):
    d = new_panel()
    d._on_target_changed("base")
    d._assign_target(QColor("#e8743c"))
    d._recent.set_colors([])
    pump(0.3)
    d._rebuild_sphere_now()
    pump(0.1)
    sw = d._sphere
    h = content_height(d)
    frames_dir = tempfile.mkdtemp(prefix="lumina-gif-")
    frames = []
    cursor = {"pos": None}

    def at(widget, fx, fy):
        return widget.mapTo(d, QPoint(int(widget.width() * fx), int(widget.height() * fy)))

    def shot(hold=1):
        img = grab(d, h)
        if cursor["pos"] is not None:
            p = QPainter(img)
            arrow(p, cursor["pos"])
            p.end()
        for _ in range(hold):
            path = os.path.join(frames_dir, "f%04d.png" % len(frames))
            img.save(path)
            frames.append(path)

    def tick(n=1, hold=1):
        for _ in range(n):
            pump(1.0 / fps)
            shot(hold)

    def sphere_point(u, v):
        ox, oy, dia = sw._sphere_geometry()
        return QPointF(ox + (u + 1) * dia / 2.0, oy + (v + 1) * dia / 2.0)

    def mouse(kind, pt, buttons=Qt.LeftButton, mods=Qt.NoModifier):
        ev = {"press": QEvent.MouseButtonPress, "move": QEvent.MouseMove,
              "release": QEvent.MouseButtonRelease}[kind]
        btn = Qt.LeftButton if kind != "move" else Qt.NoButton
        held = buttons if kind != "release" else Qt.NoButton
        e = QMouseEvent(ev, pt, btn, held, mods)
        {"press": sw.mousePressEvent, "move": sw.mouseMoveEvent,
         "release": sw.mouseReleaseEvent}[kind](e)

    tick(4)
    # 1. Hover: the lemon glides over the sphere, showing the hex under it.
    for i in range(30):
        a = math.radians(-40 + i * 7)
        r = 0.55 - 0.25 * math.sin(i / 30.0 * math.pi)
        sw._sample(sphere_point(r * math.cos(a), r * math.sin(a)))
        tick()
    # 2. Click a few spots: the lemon pops, the colour goes to the brush and
    #    collects in Recent picks.
    for u, v in ((0.35, -0.45), (-0.1, 0.1), (-0.5, 0.45), (0.6, 0.2)):
        pt = sphere_point(u, v)
        sw._sample(pt)
        tick(2)
        mouse("press", pt)
        tick(3)
        mouse("release", pt)
        tick(4)
    tick(3)
    sw._sample(QPointF(-500.0, -500.0))                     # off the sphere
    # 3. Select Shade, then back to Base: sparkles.
    for key in ("shadow", "base"):
        dot = d._target_dots[key]
        cursor["pos"] = at(dot, 0.6, 0.7)
        tick(3)
        d._select_target(key)
        tick(9)
    # 4. Drag the Hue slider: the base and the whole set follow. Kept to the
    #    warm family (orange -> red -> pink): a sweep through greens needs
    #    colours the GIF's one shared palette does not have, and dithers.
    slider = d.hue_row.slider
    start = slider.value()
    for i in range(18):
        slider.setValue((start - i * 4) % 360)
        frac = (slider.value() - slider.minimum()) / max(1, slider.maximum() - slider.minimum())
        cursor["pos"] = at(slider, frac, 0.6)
        tick()
    tick(4)
    # 5. A preset on, and a second click off again.
    btn = next(b for b in d._preset_btns if b.key == "nocturne")
    cursor["pos"] = at(btn, 0.6, 0.6)
    tick(3)
    d._on_preset_clicked("nocturne", True)
    tick(14)
    d._on_preset_clicked("nocturne", True)
    tick(10)
    cursor["pos"] = None
    tick(1, hold=12)                                         # rest on the last frame

    out = os.path.join(OUT, "lumina_demo.gif")
    pattern = os.path.join(frames_dir, "f%04d.png")
    scale = "scale=%d:-1:flags=lanczos" % 340
    # One shared 256-colour palette, error-diffused. Ordered (bayer)
    # dithering banded the sphere's smooth shading into rings; a palette per
    # frame looked best but tripled the file (~6.5 MB) because every frame is
    # then stored whole. This keeps the demo near 2.5 MB for a README.
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-framerate", str(fps), "-i", pattern,
                    "-lavfi", scale + ",split [a][b]; [a] palettegen=stats_mode=full:max_colors=256 [p];"
                    " [b][p] paletteuse=dither=sierra2_4a",
                    "-loop", "0", out], check=True)
    print("wrote", os.path.relpath(out, ROOT), "%d frames, %.1f s, %d KB" % (
        len(frames), len(frames) / float(fps), os.path.getsize(out) // 1024))
    shutil.rmtree(frames_dir, ignore_errors=True)
    d.close()


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--icon", default=os.path.expanduser("~/krita-pilot-swatches/krita_sampler_icon.png"),
                    help="Krita's Color Sampler icon (PNG) for the eyedropper button")
    ap.add_argument("--no-gif", action="store_true")
    args = ap.parse_args(argv)
    use_krita_icon(args.icon)
    try:
        stills()
        lamps_still()
        if not args.no_gif:
            demo_gif()
            zones_gif()
    finally:
        shutil.rmtree(_CONFIG, ignore_errors=True)


if __name__ == "__main__":
    main(sys.argv[1:])
