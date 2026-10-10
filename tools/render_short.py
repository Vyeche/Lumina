#!/usr/bin/env python3
"""Render the Lumina YouTube Short (issue #54): 1080x1920, 30 fps, captions only.

A beach still life at sunset in the style of the feature image (flat poster
bands), painted with colours read off the real Lumina panel: each object's
base colour goes into Lumina, the sphere is sampled in each named form zone
(Highlight, Light, Halftone, Terminator, Core shadow, Reflected light), and
the object is painted in those bands using the same zone map the readout
uses. The panel at the bottom is the actual SphereDocker, run headless like
tools/render_docs_images.py, with its own settings in a throwaway folder.

    python3 tools/render_short.py                      # -> ~/Videos/L/Lumina/
    python3 tools/render_short.py --out PATH.mp4 [--icon KRITA_SAMPLER_PNG]
    python3 tools/render_short.py --docs-gif           # also images/lumina_paint_zones.gif

Needs ffmpeg on PATH.
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
_CONFIG = tempfile.mkdtemp(prefix="lumina-short-config-")
os.environ["XDG_CONFIG_HOME"] = _CONFIG
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, HERE)

from PyQt5.QtCore import QEvent, QPoint, QPointF, QRectF, Qt  # noqa: E402
from PyQt5.QtGui import (QColor, QFont, QImage, QMouseEvent, QPainter,  # noqa: E402
                         QPainterPath, QPen, QPolygonF)
from PyQt5.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

import Lumina.sphere_docker as SD  # noqa: E402

W, H = 1080, 1920
FPS = 30
CAP_H = 230                       # caption band
CANVAS = QRectF(0, CAP_H, 1080, 1000)
PANEL_TOP = CAP_H + 1000
BG = QColor(24, 26, 31)
ZONES = ("Light", "Halftone", "Terminator", "Core shadow", "Reflected light", "Highlight")

# Base colours, as an artist would pick them.
SKY_BASE = "#ff7a45"
DUSK_BASE = "#a2507e"             # the purple at the top of the sky
SEA_BASE = "#2c6b74"
SAND_BASE = "#efc994"
OBJECTS = {
    "ball": "#e8473c",
    "coconut": "#8a5a3c",
    "bucket": "#2a9d8f",
    "crate": "#c98b4f",
}


def pump(seconds=0.0):
    end = time.monotonic() + seconds
    while True:
        app.processEvents()
        if time.monotonic() >= end:
            break
        time.sleep(0.003)


# --------------------------------------------------------------- panel
class Panel:
    """The real docker, headless, with helpers to drive it like a user."""

    def __init__(self, icon=None):
        if icon and os.path.exists(icon):
            import render_docs_images as RD
            RD.use_krita_icon(icon)
        d = SD.SphereDocker()
        d._send_to_krita = lambda c: None
        d._save_settings = lambda: None
        d.resize(430, 980)
        d.show()
        pump(0.2)
        d._sphere_render_size = 480          # sharp when scaled up for 1080 wide
        d._rebuild_sphere_now()
        pump(0.1)
        self.d = d
        self.sw = d._sphere

    def set_base(self, hexcol):
        d = self.d
        d._on_target_changed("base")
        d._assign_target(QColor(hexcol))
        pump(0.05)
        d._rebuild_sphere_now()
        pump(0.05)

    def preset(self, key):
        self.d._on_preset_clicked(key, True)
        pump(0.1)
        self.d._rebuild_sphere_now()
        pump(0.05)

    def sphere_point(self, u, v):
        ox, oy, dia = self.sw._sphere_geometry()
        return QPointF(ox + (u + 1) * dia / 2.0, oy + (v + 1) * dia / 2.0)

    def hover(self, u, v):
        self.sw._sample(self.sphere_point(u, v))

    def leave(self):
        self.sw._sample(QPointF(-500.0, -500.0))

    def click(self, u, v):
        pt = self.sphere_point(u, v)
        self.sw._sample(pt)
        for kind, ev in (("press", QEvent.MouseButtonPress), ("release", QEvent.MouseButtonRelease)):
            btn = Qt.LeftButton
            held = Qt.LeftButton if kind == "press" else Qt.NoButton
            e = QMouseEvent(ev, pt, btn, held, Qt.NoModifier)
            (self.sw.mousePressEvent if kind == "press" else self.sw.mouseReleaseEvent)(e)

    def zone_colours(self):
        """Median colour of each form zone on the rendered sphere, and one
        representative sphere point per zone (for the lemon to visit)."""
        d = self.d
        img = d._render_sphere()
        eng = d.processor.engine
        n = img.width()
        acc = {z: [] for z in ZONES}
        pts = {z: [] for z in ZONES}
        for y in range(0, n, 2):
            for x in range(0, n, 2):
                u, v = -1 + 2 * (x + 0.5) / n, -1 + 2 * (y + 0.5) / n
                if u * u + v * v > 0.97:
                    continue
                z = eng.form_zone(u, v)
                c = QColor(img.pixel(x, y))
                acc[z].append((c.red(), c.green(), c.blue()))
                pts[z].append((u, v))
        out, where = {}, {}
        for z in ZONES:
            if not acc[z]:
                continue
            if z == "Highlight":
                # the highlight is the bright peak, not the zone's middle
                by_luma = sorted(acc[z], key=lambda c: 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2])
                out[z] = QColor(*by_luma[int(len(by_luma) * 0.8)])
            else:
                rs = sorted(a[0] for a in acc[z]); gs = sorted(a[1] for a in acc[z]); bs = sorted(a[2] for a in acc[z])
                m = len(rs) // 2
                out[z] = QColor(rs[m], gs[m], bs[m])
            # the zone point nearest the zone's middle
            cu = sum(p[0] for p in pts[z]) / len(pts[z]); cv = sum(p[1] for p in pts[z]) / len(pts[z])
            where[z] = min(pts[z], key=lambda p: (p[0] - cu) ** 2 + (p[1] - cv) ** 2)
        # Fill zones a dim light leaves out (Nocturne lights no "Light" or
        # "Highlight"). A missing highlight is the sphere's brightest pixels;
        # the others borrow from their nearest neighbour in lightness.
        everything = [c for z in ZONES for c in acc[z]]
        if "Highlight" not in out and everything:
            by_luma = sorted(everything, key=lambda c: 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2])
            out["Highlight"] = QColor(*by_luma[int(len(by_luma) * 0.97)])
            where["Highlight"] = where.get("Light") or where.get("Halftone") or (0.3, -0.4)
        fallback = {"Light": ("Halftone", "Highlight"), "Halftone": ("Light", "Terminator"),
                    "Terminator": ("Halftone", "Core shadow"), "Core shadow": ("Terminator", "Reflected light"),
                    "Reflected light": ("Core shadow", "Terminator"), "Highlight": ("Light", "Halftone")}
        for z in ZONES:
            if z not in out:
                for alt in fallback[z]:
                    if alt in out:
                        out[z] = out[alt]; where[z] = where[alt]
                        break
        return out, where

    def grab(self):
        d = self.d
        top = d.light_type_row.mapTo(d, QPoint(0, 0)).y() - 8
        cap = d._target_caps["base"]
        bottom = cap.mapTo(d, QPoint(0, cap.height())).y() + 12
        return d.grab().toImage().copy(0, top, d.width(), bottom - top)

    def engine(self):
        return self.d.processor.engine


# --------------------------------------------------------------- scene
class Scene:
    """The still life, painted in Lumina's zone colours, object by object."""

    def __init__(self, panel):
        self.p = panel
        self.palette = {}           # name -> zone colours
        self.where = {}
        self.engine_state = None

    def read(self, name, hexcol):
        self.p.set_base(hexcol)
        cols, where = self.p.zone_colours()
        self.palette[name] = cols
        self.where[name] = where
        return cols, where

    # ---- background
    def background(self, night=False):
        img = QImage(1080, 1000, QImage.Format_ARGB32_Premultiplied)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        sky, sea, sand = self.palette["sky"], self.palette["sea"], self.palette["sand"]
        dusk = self.palette["dusk"]
        horizon = 560
        # banded sky, as in the feature image: dusk purple at the top through
        # pink and orange to cream at the horizon
        bands = [dusk["Halftone"], dusk["Light"], sky["Halftone"], sky["Light"], sky["Highlight"]]
        if night:
            bands.sort(key=lambda c: c.lightnessF())      # darkest at the top
        bh = horizon / len(bands)
        for i, c in enumerate(bands):
            p.fillRect(QRectF(0, i * bh, 1080, bh + 1), c)
        # the sun, upper right, where Lumina's light comes from
        sun = QPointF(820, 230)
        for r, a in ((150, 40), (115, 70), (85, 255)):
            col = QColor(sky["Highlight"]).lighter(118 if night else 112)
            col.setAlpha(a)
            p.setPen(Qt.NoPen); p.setBrush(col)
            p.drawEllipse(sun, r, r)
        p.setBrush(QColor("#fff6dc") if not night else QColor("#e8eefc"))
        p.drawEllipse(sun, 70, 70)
        # birds
        p.setPen(QPen(QColor(sky["Core shadow"]).darker(150), 4, Qt.SolidLine, Qt.RoundCap))
        for bx, by, s in ((610, 150, 1.0), (680, 110, 0.8), (560, 200, 0.7), (735, 165, 0.6)):
            path = QPainterPath(QPointF(bx - 16 * s, by))
            path.quadTo(bx - 8 * s, by - 10 * s, bx, by)
            path.quadTo(bx + 8 * s, by - 10 * s, bx + 16 * s, by)
            p.setBrush(Qt.NoBrush); p.drawPath(path)
        # sea bands with light dashes
        sea_bands = [sea["Light"], sea["Halftone"], sea["Terminator"]]
        for i, c in enumerate(sea_bands):
            p.fillRect(QRectF(0, horizon + i * 30, 1080, 31), c)
        p.setPen(QPen(sea["Highlight"], 4))
        for y, xs in ((575, (140, 330, 600)), (605, (60, 450, 900)), (632, (250, 700))):
            for x in xs:
                p.drawLine(QPointF(x, y), QPointF(x + 60, y))
        # sand
        p.setPen(Qt.NoPen)
        p.fillRect(QRectF(0, horizon + 90, 1080, 1000 - horizon - 90), sand["Light"])
        p.fillRect(QRectF(0, horizon + 90, 1080, 14), sand["Highlight"])
        # a soft dune line in the sand
        p.setBrush(sand["Highlight"])
        p.drawEllipse(QPointF(760, 1010), 560, 70)
        # palm silhouette, left, like the feature image
        dark = QColor(sky["Core shadow"]).darker(170)
        p.setBrush(dark)
        trunk = QPainterPath(QPointF(40, 1000))
        trunk.cubicTo(90, 820, 150, 560, 230, 330)
        trunk.lineTo(250, 338)
        trunk.cubicTo(180, 570, 125, 820, 85, 1000)
        trunk.closeSubpath()
        p.drawPath(trunk)
        for ang, ln in ((-20, 230), (10, 210), (40, 190), (-60, 200), (-100, 170), (80, 160), (150, 190), (190, 150)):
            a = math.radians(ang)
            tip = QPointF(240 + math.cos(a) * ln, 335 + math.sin(a) * ln * 0.75)
            base_l = QPointF(240 + math.cos(a + 1.4) * 14, 335 + math.sin(a + 1.4) * 14)
            base_r = QPointF(240 + math.cos(a - 1.4) * 14, 335 + math.sin(a - 1.4) * 14)
            p.drawPolygon(QPolygonF([base_l, tip, base_r]))
        p.end()
        return img

    # ---- objects: one layer per zone, built once per palette
    _zone_maps = {}

    def _zone_map(self, r, eng):
        """Zone name per pixel of a sphere of radius r, at 2x (cached)."""
        key = (r, id(eng))
        m = Scene._zone_maps.get(key)
        if m is None:
            S = 2
            size = int(2 * r * S) + 4
            m = []
            for y in range(size):
                row = []
                for x in range(size):
                    u = ((x + 0.5) / S - r - 1) / r
                    v = ((y + 0.5) / S - r - 1) / r
                    row.append(eng.form_zone(u, v) if u * u + v * v <= 1.0 else None)
                m.append(row)
            Scene._zone_maps[key] = m
        return m

    def _recoloured(self, layer, colour):
        """A zone layer filled flat with another colour (same coverage)."""
        key = (id(layer), colour.rgb())
        cache = self.__dict__.setdefault("_recolour_cache", {})
        img = cache.get(key)
        if img is None:
            img = QImage(layer.size(), QImage.Format_ARGB32_Premultiplied)
            img.fill(Qt.transparent)
            p = QPainter(img)
            p.drawImage(0, 0, layer)
            p.setCompositionMode(QPainter.CompositionMode_SourceIn)
            p.fillRect(img.rect(), colour)
            p.end()
            cache[key] = img
        return img

    def _sphere_layers(self, name, cols, r, eng, texture=None):
        key = (name, id(cols))
        cache = self.__dict__.setdefault("_layer_cache", {})
        if key in cache:
            return cache[key]
        m = self._zone_map(r, eng)
        S = 2
        size = len(m)
        layers = {}
        for z in ZONES:
            img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
            img.fill(Qt.transparent)
            any_px = False
            for y in range(size):
                row = m[y]
                for x in range(size):
                    if row[x] != z:
                        continue
                    u = ((x + 0.5) / S - r - 1) / r
                    v = ((y + 0.5) / S - r - 1) / r
                    c = QColor(cols[z])
                    if texture is not None:
                        c = texture(u, v, c)
                    img.setPixelColor(x, y, c)
                    any_px = True
            if any_px:
                layers[z] = img.scaled(size // S, size // S, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        cache[key] = layers
        return layers

    def paint_objects(self, base_img, progress, eng):
        """progress: name -> {zone: alpha 0..1}; returns the composed canvas."""
        out = QImage(base_img)
        p = QPainter(out)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        order = ("crate", "bucket", "coconut", "ball")
        for name in order:
            prog = progress.get(name)
            if not prog:
                continue
            cols = self.palette[name]
            sand = self.palette["sand"]
            shadow_a = prog.get("shadow", 0.0)
            geo = LAYOUT[name]
            if shadow_a > 0:
                sc = QColor(sand["Core shadow"]); sc.setAlphaF(0.75 * shadow_a)
                p.setPen(Qt.NoPen); p.setBrush(sc)
                sx, sy, sw_, sh_ = geo["shadow"]
                p.drawEllipse(QRectF(sx, sy, sw_, sh_))
            alpha = lambda z: prog.get(z, 0.0)
            if geo["kind"] == "sphere":
                tex = None
                layers = self._sphere_layers(name, cols, geo["r"], eng, tex)
                at = QPointF(geo["cx"] - geo["r"] - 1, geo["cy"] - geo["r"] - 1)
                base_a = prog.get("Light", 0.0)
                if base_a > 0:
                    # blocked in first in the light colour, as a painter would
                    p.setOpacity(base_a)
                    p.setPen(Qt.NoPen); p.setBrush(cols["Light"])
                    p.drawEllipse(QPointF(geo["cx"], geo["cy"]), geo["r"] - 0.3, geo["r"] - 0.3)
                for z in ZONES:
                    a = alpha(z)
                    if a > 0 and z in layers:
                        p.setOpacity(a)
                        p.drawImage(at, layers[z])
                p.setOpacity(1.0)
            elif geo["kind"] == "cylinder":
                self._cylinder(p, cols, geo, alpha, eng)
            elif geo["kind"] == "cube":
                self._cube(p, cols, geo, alpha, eng)
        p.end()
        return out

    def _cylinder(self, p, cols, g, alpha, eng):
        x0, x1, top, bot, eh = g["x0"], g["x1"], g["top"], g["bot"], g["eh"]
        cx, hw = (x0 + x1) / 2.0, (x1 - x0) / 2.0
        strip = 3
        base_a = alpha("Light")
        for x in range(int(x0), int(x1), strip):
            s = (x + strip / 2.0 - cx) / hw
            s = max(-0.999, min(0.999, s))
            z = eng.form_zone(s, 0.0)
            a = alpha(z)
            if base_a > 0 and z != "Light":
                # blocked in first in the light colour
                yb = bot + eh * math.sqrt(max(0.0, 1 - s * s))
                yt = top + eh * math.sqrt(max(0.0, 1 - s * s))
                c0 = QColor(cols["Light"]); c0.setAlphaF(base_a)
                p.setPen(Qt.NoPen); p.setBrush(c0)
                p.drawRect(QRectF(x, yt, strip + 0.6, yb - yt))
            if a <= 0:
                continue
            c = QColor(cols[z]); c.setAlphaF(a)
            yb = bot + eh * math.sqrt(max(0.0, 1 - s * s))
            yt = top + eh * math.sqrt(max(0.0, 1 - s * s))
            p.setPen(Qt.NoPen); p.setBrush(c)
            p.drawRect(QRectF(x, yt, strip + 0.6, yb - yt))
        # the open top: rim in the light, inside in shadow
        ra = alpha("Light")
        if ra > 0:
            c = QColor(cols["Light"]); c.setAlphaF(ra)
            p.setBrush(c); p.drawEllipse(QRectF(x0, top - eh, x1 - x0, 2 * eh))
            c2 = QColor(cols["Core shadow"]); c2.setAlphaF(alpha("Core shadow") or ra)
            p.setBrush(c2); p.drawEllipse(QRectF(x0 + 10, top - eh + 7, x1 - x0 - 20, 2 * eh - 14))
        # handle
        ha = alpha("Terminator")
        if ha > 0:
            c = QColor(cols["Terminator"]); c.setAlphaF(ha)
            p.setPen(QPen(c, 6)); p.setBrush(Qt.NoBrush)
            p.drawArc(QRectF(x0 + 8, top - eh - 70, x1 - x0 - 16, 130), 15 * 16, 150 * 16)

    def _cube(self, p, cols, g, alpha, eng):
        x, y, s, d = g["x"], g["y"], g["s"], g["d"]
        # faces: top (facing up and to us), right (toward the light), left (away)
        top = QPolygonF([QPointF(x, y), QPointF(x + s, y), QPointF(x + s + d, y - d * 0.55), QPointF(x + d, y - d * 0.55)])
        front = QPolygonF([QPointF(x, y), QPointF(x + s, y), QPointF(x + s, y + s), QPointF(x, y + s)])
        side = QPolygonF([QPointF(x + s, y), QPointF(x + s + d, y - d * 0.55), QPointF(x + s + d, y + s - d * 0.55), QPointF(x + s, y + s)])
        faces = ((top, eng.form_zone(0.25, -0.8)), (side, eng.form_zone(0.85, -0.1)), (front, eng.form_zone(-0.35, 0.15)))
        base_a = alpha("Light")
        if base_a > 0:
            c0 = QColor(cols["Light"]); c0.setAlphaF(base_a)
            p.setPen(Qt.NoPen); p.setBrush(c0)
            for poly, _z in faces:
                p.drawPolygon(poly)
        for poly, z in faces:
            a = alpha(z)
            if a <= 0:
                continue
            c = QColor(cols[z]); c.setAlphaF(a)
            p.setPen(Qt.NoPen); p.setBrush(c); p.drawPolygon(poly)
            # planks
            lc = QColor(cols["Core shadow"]); lc.setAlphaF(0.55 * a)
            p.setPen(QPen(lc, 3))
            if poly is front:
                for k in (1, 2):
                    p.drawLine(QPointF(x + 6, y + s * k / 3.0), QPointF(x + s - 6, y + s * k / 3.0))
            elif poly is side:
                for k in (1, 2):
                    p.drawLine(QPointF(x + s + 4, y + s * k / 3.0 - 2), QPointF(x + s + d - 4, y + s * k / 3.0 - d * 0.55 + 2))


LAYOUT = {
    "crate": {"kind": "cube", "x": 640, "y": 690, "s": 170, "d": 80,
              "shadow": (520, 830, 260, 70)},
    "bucket": {"kind": "cylinder", "x0": 830, "x1": 960, "top": 760, "bot": 880, "eh": 20,
               "shadow": (720, 880, 220, 60)},
    "coconut": {"kind": "sphere", "cx": 300, "cy": 880, "r": 62,
                "shadow": (180, 905, 170, 48)},
    "ball": {"kind": "sphere", "cx": 480, "cy": 800, "r": 120,
             "shadow": (270, 880, 290, 80)},
}


# --------------------------------------------------------------- video
class Video:
    def __init__(self, path):
        self.path = path
        self.proc = subprocess.Popen(
            ["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
             "-s", "%dx%d" % (W, H), "-r", str(FPS), "-i", "-",
             "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", path], stdin=subprocess.PIPE)
        self.frames = 0

    def put(self, img, hold=1):
        img = img.convertToFormat(QImage.Format_RGB888)
        data = img.constBits().asstring(img.sizeInBytes())
        for _ in range(hold):
            self.proc.stdin.write(data)
            self.frames += 1

    def close(self):
        self.proc.stdin.close()
        self.proc.wait()


def blend(a, b, t):
    """b over a at opacity t (a cross-fade between two canvases)."""
    out = QImage(a)
    p = QPainter(out)
    p.setOpacity(t)
    p.drawImage(0, 0, b)
    p.end()
    return out


def caption_font(px, bold=True):
    f = QFont("Sans")
    f.setPixelSize(px)
    f.setBold(bold)
    return f


def compose(canvas, panel_img, caption, sub=None, caption_alpha=1.0, end=False):
    frame = QImage(W, H, QImage.Format_RGB32)
    frame.fill(BG)
    p = QPainter(frame)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)
    p.drawImage(CANVAS, canvas)
    if panel_img is not None:
        avail_h = H - PANEL_TOP - 20
        scale = min(1000.0 / panel_img.width(), avail_h / panel_img.height())
        pw, ph = panel_img.width() * scale, panel_img.height() * scale
        p.drawImage(QRectF((W - pw) / 2.0, PANEL_TOP + 10, pw, ph), panel_img)
    if caption:
        p.setOpacity(caption_alpha)
        p.setPen(QColor(255, 255, 255))
        p.setFont(caption_font(66))
        p.drawText(QRectF(40, 40, W - 80, 120), Qt.AlignCenter, caption)
        if sub:
            p.setPen(QColor(200, 206, 216))
            p.setFont(caption_font(36, bold=False))
            p.drawText(QRectF(40, 150, W - 80, 60), Qt.AlignCenter, sub)
        p.setOpacity(1.0)
    p.end()
    return frame


def zone_steps(zones=ZONES):
    return list(zones)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=os.path.expanduser("~/Videos/L/Lumina/lumina_short.mp4"))
    ap.add_argument("--icon", default=os.path.expanduser("~/krita-pilot-swatches/krita_sampler_icon.png"))
    ap.add_argument("--docs-gif", action="store_true",
                    help="also cut the read-and-paint stretch into images/lumina_paint_zones.gif")
    args = ap.parse_args(argv)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    panel = Panel(args.icon)
    scene = Scene(panel)
    eng = panel.engine()

    # read every colour off Lumina, day
    for name, hexcol in (("sky", SKY_BASE), ("dusk", DUSK_BASE), ("sea", SEA_BASE), ("sand", SAND_BASE)):
        scene.read(name, hexcol)
    for name, hexcol in OBJECTS.items():
        scene.read(name, hexcol)
    day_bg = scene.background()
    full = {n: dict({z: 1.0 for z in ZONES}, shadow=1.0) for n in OBJECTS}
    day_final = scene.paint_objects(day_bg, full, eng)

    # and at night (Nocturne), for the mood beat
    panel.preset("nocturne")
    night_palette = {}
    for name, hexcol in list({"sky": SKY_BASE, "dusk": DUSK_BASE, "sea": SEA_BASE, "sand": SAND_BASE}.items()) + list(OBJECTS.items()):
        panel.set_base(hexcol)
        night_palette[name] = panel.zone_colours()[0]
    day_palette = dict(scene.palette)
    scene.palette = night_palette
    night_bg = scene.background(night=True)
    night_final = scene.paint_objects(night_bg, full, eng)
    scene.palette = day_palette
    panel.preset("nocturne")                     # off again
    panel.set_base(OBJECTS["ball"])

    video = Video(args.out)

    # 1. the finished piece
    panel.leave()
    pimg = panel.grab()
    for i in range(int(2.6 * FPS)):
        video.put(compose(day_final, pimg, "One light. Every object.", "Lumina for Krita",
                          caption_alpha=min(1.0, i / 8.0)))
    # 2. pick a colour: the ball's red into Lumina
    panel.set_base("#9aa0aa")
    start = panel.grab()
    for i in range(int(0.8 * FPS)):
        video.put(compose(day_bg, start, "Pick a colour", "it becomes the sphere's base"))
    panel.set_base(OBJECTS["ball"])
    pimg = panel.grab()
    for i in range(int(1.8 * FPS)):
        video.put(compose(day_bg, pimg, "Pick a colour", "it becomes the sphere's base"))
    # 3. read the light off the sphere: the lemon visits each zone
    where = scene.where["ball"]
    for z in ZONES:
        u, v = where[z]
        panel.hover(u, v)
        pump(0.02)
        pimg = panel.grab()
        video.put(compose(day_bg, pimg, "Read the light off the sphere", z), hold=int(1.0 * FPS))
    # 4. paint it zone by zone, the lemon on the zone being laid in. Each
    #    stage is painted whole and cross-faded in, so translucent layers
    #    never sit on each other.
    prog = {"ball": {}}
    canvas = day_bg
    for z in ZONES:
        u, v = where[z]
        panel.click(u, v)
        pump(0.05)
        pimg = panel.grab()
        prog["ball"][z] = 1.0
        if z == "Light":
            prog["ball"]["shadow"] = 1.0
        nxt = scene.paint_objects(day_bg, prog, eng)
        steps = 12
        for k in range(1, steps + 1):
            video.put(compose(blend(canvas, nxt, k / float(steps)), pimg, "Paint it zone by zone", z))
        canvas = nxt
        video.put(compose(canvas, pimg, "Paint it zone by zone", z), hold=12)
    # 5. the same light on every shape
    for name in ("coconut", "crate", "bucket"):
        panel.set_base(OBJECTS[name])
        w = scene.where[name]
        prog[name] = {}
        for z in ZONES:
            u, v = w[z]
            panel.hover(u, v)
            pump(0.01)
            pimg = panel.grab()
            prog[name][z] = 1.0
            prog[name]["shadow"] = 1.0
            nxt = scene.paint_objects(day_bg, prog, eng)
            for k in (1, 2, 3, 4, 5):
                video.put(compose(blend(canvas, nxt, k / 5.0), pimg, "Same light, any shape", name.capitalize()))
            canvas = nxt
        video.put(compose(canvas, pimg, "Same light, any shape", name.capitalize()), hold=14)
    panel.leave()
    pimg = panel.grab()
    video.put(compose(day_final, pimg, "Same light, any shape"), hold=int(0.8 * FPS))
    # 6. change the mood in one click
    panel.set_base(OBJECTS["ball"])
    panel.preset("nocturne")
    pimg = panel.grab()
    for i in range(int(1.6 * FPS)):
        t = min(1.0, i / (1.1 * FPS))
        video.put(compose(blend(day_final, night_final, t), pimg, "Change the mood in one click", "Nocturne preset"))
    video.put(compose(night_final, pimg, "Change the mood in one click", "Nocturne preset"), hold=int(1.0 * FPS))
    panel.preset("nocturne")
    pimg = panel.grab()
    for i in range(int(0.8 * FPS)):
        t = min(1.0, i / (0.6 * FPS))
        video.put(compose(blend(night_final, day_final, t), pimg, "Change the mood in one click", "and back"))
    # 7. end card
    for i in range(int(3.0 * FPS)):
        video.put(compose(day_final, pimg, "Lumina", "a free lighting plugin for Krita"))
    video.close()

    # a poster frame next to the video
    compose(day_final, pimg, "One light. Every object.", "Lumina for Krita").save(
        os.path.splitext(args.out)[0] + "_poster.png")
    print("wrote", args.out, "%d frames, %.1f s" % (video.frames, video.frames / float(FPS)))
    if args.docs_gif:
        # "Read the light off the sphere" through "Paint it zone by zone"
        gif = os.path.join(ROOT, "images", "lumina_paint_zones.gif")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", "5.2", "-t", "11.0", "-i", args.out,
                        "-vf", "fps=12,scale=360:-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=full:max_colors=256[p];"
                        "[b][p]paletteuse=dither=sierra2_4a", "-loop", "0", gif], check=True)
        print("wrote", os.path.relpath(gif, ROOT))
    shutil.rmtree(_CONFIG, ignore_errors=True)


if __name__ == "__main__":
    main(sys.argv[1:])
