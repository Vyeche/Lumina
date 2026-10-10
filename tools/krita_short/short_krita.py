"""Paint the Lumina Short's still life in the running Krita, with real brushes
and real Lumina picks, and save a window frame after each brush step.

Loaded into Krita with exec() by the KritaPilot bridge; each step function is
called in its own bridge call so Krita never blocks for long.
"""
import json
import math
import os

from PyQt5.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt
from PyQt5.QtGui import QColor, QMouseEvent, QPainterPath, QPolygonF
from PyQt5.QtWidgets import QApplication
from krita import Krita, ManagedColor

WORK = os.path.expanduser("~/Videos/L/Lumina/work")
FRAMES = os.path.join(WORK, "frames")
CROP = QRect(0, 0, 1080, 1068)
ORIGIN = (466.0, 96.0)          # doc (0, 0) in window pixels
SCALE = 0.6689                  # doc px -> window px
LIGHT = (math.cos(math.radians(41)) * math.cos(math.radians(304)),
         math.cos(math.radians(41)) * math.sin(math.radians(304)),
         math.sin(math.radians(41)))


def norm(v):
    l = math.sqrt(sum(c * c for c in v)) or 1.0
    return tuple(c / l for c in v)


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


E1 = norm(cross(LIGHT, (0.0, 0.0, 1.0)))
E2 = norm(cross(LIGHT, E1))


class Short:
    def __init__(self):
        self.k = Krita.instance()
        self.win = self.k.activeWindow()
        self.view = self.win.activeView()
        self.doc = self.view.document()
        self.presets = self.k.resources("preset")
        self.dock = [w for w in QApplication.allWidgets() if type(w).__name__ == "SphereDocker"][0]
        self.sw = self.dock._sphere
        os.makedirs(FRAMES, exist_ok=True)
        self.meta_path = os.path.join(FRAMES, "meta.json")
        self.meta = json.load(open(self.meta_path)) if os.path.exists(self.meta_path) else []
        self.caption = ("", "")
        self.layers = {}

    # ------------------------------------------------------------ frames
    def frame(self, cursor=None, size=0, hold=1):
        # Krita refreshes the canvas asynchronously: wait for it, or the
        # grab shows the stroke before last.
        self.doc.waitForDone()
        for _ in range(4):
            QApplication.processEvents()
        n = len(self.meta)
        name = "f%05d.png" % n
        self.win.qwindow().grab(CROP).save(os.path.join(FRAMES, name))
        cur = None
        if cursor is not None:
            cur = [ORIGIN[0] + cursor[0] * SCALE, ORIGIN[1] + cursor[1] * SCALE, size * SCALE / 2.0]
        self.meta.append({"file": name, "hold": hold, "caption": self.caption[0],
                          "sub": self.caption[1], "cursor": cur})

    def save(self):
        json.dump(self.meta, open(self.meta_path, "w"))

    def say(self, caption, sub=""):
        self.caption = (caption, sub)

    # ------------------------------------------------------------ Lumina
    def wait_render(self, timeout=3.0):
        """Inside Krita the full-quality sphere renders on a worker; a pick
        before it lands would sample the previous colour."""
        import time as _t
        d = self.dock
        end = _t.monotonic() + timeout
        while _t.monotonic() < end:
            QApplication.processEvents()
            if getattr(d, "_full_running", None) is None and getattr(d, "_full_wanted", None) is None \
                    and not d._rebuild_timer.isActive():
                break
            _t.sleep(0.01)
        for _ in range(3):
            QApplication.processEvents()

    def lumina_base(self, hexcol):
        d = self.dock
        d._on_target_changed("base")
        d._assign_target(QColor(hexcol))
        for _ in range(5):
            QApplication.processEvents()
        d._rebuild_sphere_now()
        self.wait_render()

    def sphere_point(self, u, v):
        ox, oy, dia = self.sw._sphere_geometry()
        return QPointF(ox + (u + 1) * dia / 2.0, oy + (v + 1) * dia / 2.0)

    def zone_points(self):
        eng = self.dock.processor.engine
        pts = {}
        for j in range(-20, 21):
            for i in range(-20, 21):
                u, v = i / 20.0, j / 20.0
                if u * u + v * v > 0.92:
                    continue
                pts.setdefault(eng.form_zone(u, v), []).append((u, v))
        out = {}
        for z, ps in pts.items():
            cu = sum(p[0] for p in ps) / len(ps)
            cv = sum(p[1] for p in ps) / len(ps)
            out[z] = min(ps, key=lambda p: (p[0] - cu) ** 2 + (p[1] - cv) ** 2)
        return out

    def hover(self, u, v):
        self.sw._sample(self.sphere_point(u, v))

    def leave(self):
        self.sw._sample(QPointF(-500.0, -500.0))

    def pick(self, u, v):
        """Click Lumina's sphere: the colour goes to Krita's brush."""
        pt = self.sphere_point(u, v)
        self.sw._sample(pt)
        for ev, held in ((QEvent.MouseButtonPress, Qt.LeftButton), (QEvent.MouseButtonRelease, Qt.NoButton)):
            e = QMouseEvent(ev, pt, Qt.LeftButton, held, Qt.NoModifier)
            (self.sw.mousePressEvent if ev == QEvent.MouseButtonPress else self.sw.mouseReleaseEvent)(e)
        for _ in range(5):
            QApplication.processEvents()

    def set_colour(self, hexcol):
        c = QColor(hexcol)
        mc = ManagedColor("RGBA", "U8", "")
        mc.setComponents([c.blueF(), c.greenF(), c.redF(), 1.0])
        self.view.setForeGroundColor(mc)

    # ------------------------------------------------------------ layers
    def layer(self, name, parent=None, inherit=False, above=None):
        if name in self.layers:
            return self.layers[name]
        node = self.doc.createNode(name, "paintlayer")
        (parent or self.doc.rootNode()).addChildNode(node, above)
        if inherit:
            node.setInheritAlpha(True)
        self.layers[name] = node
        return node

    def group(self, name, above=None):
        if name in self.layers:
            return self.layers[name]
        g = self.doc.createGroupLayer(name)
        self.doc.rootNode().addChildNode(g, above)
        self.layers[name] = g
        return g

    def brush(self, preset, size):
        self.view.setCurrentBrushPreset(self.presets[preset])
        self.view.setBrushSize(size)
        self._size = size

    # ------------------------------------------------------------ strokes
    def stroke(self, node, pts, segments=4, hold_end=0):
        """Paint a stroke through pts in a few segments, a frame after each,
        with the brush cursor at the stroke's head."""
        n = len(pts)
        step = max(1, (n - 1) // segments)
        i = 0
        while i < n - 1:
            j = min(n - 1, i + step)
            path = QPainterPath(QPointF(*pts[i]))
            for k in range(i + 1, j + 1):
                path.lineTo(QPointF(*pts[k]))
            node.paintPath(path, "ForegroundColor", "None")
            self.doc.refreshProjection()
            self.frame(cursor=pts[j], size=self._size)
            i = j
        if hold_end:
            self.frame(cursor=pts[-1], size=self._size, hold=hold_end)

    def fill_ellipse(self, node, cx, cy, rx, ry):
        node.paintEllipse(QRectF(cx - rx, cy - ry, 2 * rx, 2 * ry), "None", "ForegroundColor")
        self.doc.refreshProjection()

    def fill_polygon(self, node, pts):
        node.paintPolygon([QPointF(*p) for p in pts], "None", "ForegroundColor")
        self.doc.refreshProjection()

    @staticmethod
    def contour(cx, cy, r, c, wobble=0.0, seed=0):
        """Points of the visible part of the N.L = c curve on a sphere."""
        s = math.sqrt(max(0.0, 1 - c * c))
        vis = []
        for i in range(181):
            th = 2 * math.pi * i / 180.0
            n = tuple(c * LIGHT[q] + s * (math.cos(th) * E1[q] + math.sin(th) * E2[q]) for q in range(3))
            if n[2] > 0.05:
                w = 1.0 + wobble * math.sin(i * 0.7 + seed)
                vis.append((cx + r * n[0] * w, cy + r * n[1] * w, i))
        if not vis:
            return []
        # the longest contiguous run (the visible arc may wrap past 0)
        runs, cur = [], [vis[0]]
        for a, b in zip(vis, vis[1:]):
            if b[2] == a[2] + 1:
                cur.append(b)
            else:
                runs.append(cur); cur = [b]
        runs.append(cur)
        if len(runs) > 1 and runs[0][0][2] == 0 and runs[-1][-1][2] == 180:
            runs = [runs[-1] + runs[0]] + runs[1:-1]
        best = max(runs, key=len)
        return [(x, y) for x, y, _ in best[::3]]


# ================================================================ the scene
BASES = {"sky": "#ff7a45", "dusk": "#a2507e", "sea": "#2c6b74", "sand": "#efc994",
         "ball": "#e8473c", "coconut": "#8a5a3c", "bucket": "#2a9d8f", "crate": "#c98b4f"}
BALL = (430.0, 1010.0, 150.0)
COCONUT = (190.0, 1185.0, 70.0)
CRATE = (520.0, 830.0, 180.0, 75.0)          # front x, y, size, depth
BUCKET = (735.0, 860.0, 905.0, 1045.0, 18.0)  # x0, x1, top, bottom, ellipse h
BRISTLE_BIG = "f) Bristles-3 Large Smooth"
BRISTLE = "f) Bristles-2 Flat Rough"
ROUND = "b) Basic-1"


def pick_zone(s, base, zone, zp):
    if s.__dict__.get("_base") != base:
        s.lumina_base(BASES[base])
        s._base = base
    u, v = zp[zone]
    s.pick(u, v)


def step_background(s):
    """Set the scene: sky, sun, sea, sand, palm, cast shadows. Fast."""
    s.say("Set the scene", "every colour picked off Lumina")
    zp = s.zone_points()
    bg = s.layer("Background")
    W = 900.0
    bands = [("dusk", "Halftone"), ("dusk", "Light"), ("sky", "Halftone"), ("sky", "Light"), ("sky", "Highlight")]
    bh = 620.0 / len(bands)
    for i, (base, zone) in enumerate(bands):
        pick_zone(s, base, zone, zp)
        bg.paintRectangle(QRectF(0, i * bh, W, bh + 2), "None", "ForegroundColor")
        s.frame()
        if i:
            # soften the edge into the band above, as a painter would
            s.brush(BRISTLE, 46)
            y = i * bh
            s.stroke(bg, [(-40 + k * 70, y - 4 + 9 * math.sin(k * 1.1 + i)) for k in range(15)], segments=2)
    # the sun, where Lumina's light comes from
    pick_zone(s, "sky", "Highlight", zp)
    s.fill_ellipse(bg, 690, 250, 118, 118)
    s.set_colour("#fff4d8")
    s.fill_ellipse(bg, 690, 250, 78, 78)
    s.frame()
    # sea
    for i, zone in enumerate(("Light", "Halftone", "Terminator")):
        pick_zone(s, "sea", zone, zp)
        bg.paintRectangle(QRectF(0, 620 + i * 47, W, 49), "None", "ForegroundColor")
        s.brush(BRISTLE, 30)
        s.stroke(bg, [(-20 + k * 80, 620 + i * 47 + 5 * math.sin(k * 1.3)) for k in range(13)], segments=2)
    pick_zone(s, "sea", "Highlight", zp)
    s.brush(ROUND, 6)
    for x0, y0 in ((90, 640), (330, 655), (560, 640), (190, 690), (650, 700), (420, 730)):
        s.stroke(bg, [(x0, y0), (x0 + 60, y0)], segments=1)
    # sand
    pick_zone(s, "sand", "Light", zp)
    bg.paintRectangle(QRectF(0, 761, W, 640), "None", "ForegroundColor")
    pick_zone(s, "sand", "Halftone", zp)
    s.brush(BRISTLE, 22)
    for x0, y0, ln in ((520, 1290, 330), (640, 1350, 260)):
        s.stroke(bg, [(x0 + k * ln / 6.0, y0 - 18 * math.sin(k / 6.0 * math.pi)) for k in range(7)], segments=2)
    pick_zone(s, "sand", "Highlight", zp)
    s.brush(BRISTLE, 26)
    s.stroke(bg, [(-20 + k * 80, 768 + 3 * math.sin(k)) for k in range(13)], segments=2)
    # palm silhouette
    s.set_colour("#2a1620")
    s.brush("b) Basic-5 Size", 46)
    trunk = [(70 - 10 * t ** 2 + 160 * t, 1420 - 1000 * t) for t in [k / 20.0 for k in range(21)]]
    s.stroke(bg, trunk, segments=4)
    crown = trunk[-1]
    for ang, ln in ((-25, 250), (5, 230), (35, 190), (-60, 200), (-110, 180), (75, 160), (150, 210), (195, 175), (-150, 150)):
        a = math.radians(ang)
        tip = (crown[0] + math.cos(a) * ln, crown[1] + math.sin(a) * ln * 0.75 + 30)
        mid = (crown[0] + math.cos(a) * ln * 0.5, crown[1] + math.sin(a) * ln * 0.5 * 0.75 - 10)
        side = (math.cos(a + math.pi / 2) * 22, math.sin(a + math.pi / 2) * 22)
        s.fill_polygon(bg, [(crown[0] + side[0] * 0.4, crown[1] + side[1] * 0.4),
                            (mid[0] + side[0], mid[1] + side[1]), tip,
                            (mid[0] - side[0] * 0.3, mid[1] - side[1] * 0.3)])
        s.frame(cursor=tip, size=26)
    # birds
    s.brush(ROUND, 5)
    for bx, by in ((470, 140), (530, 110), (420, 185)):
        s.stroke(bg, [(bx - 16, by), (bx - 8, by - 8), (bx, by), (bx + 8, by - 8), (bx + 16, by)], segments=1)
    # cast shadows, away from the light (lower left)
    sh = s.layer("Shadows")
    sh.setOpacity(165)
    pick_zone(s, "sand", "Core shadow", zp)
    for cx, cy, rx, ry in ((320, 1148, 175, 42), (125, 1250, 92, 22), (470, 1012, 125, 30), (690, 1052, 110, 26)):
        s.fill_ellipse(sh, cx, cy, rx, ry)
    s.frame(hold=4)
    s.save()


def sphere_group(s, name, above=None):
    g = s.group(name, above)
    base = s.layer(name + " base", g)
    paint = s.layer(name + " paint", g, inherit=True)
    return base, paint


def paint_sphere(s, name, sphere, zp, slow=True):
    cx, cy, r = sphere
    base, paint = sphere_group(s, name)
    seg = 4 if slow else 2
    # (zone, contours, brush, size as a share of the radius)
    plan = (("Light", (0.7,), BRISTLE_BIG, 0.5),
            ("Halftone", (0.42, 0.27, 0.15), BRISTLE_BIG, 0.5),
            ("Terminator", (0.05,), BRISTLE, 0.24),
            ("Core shadow", (-0.14, -0.32, -0.52, -0.72), BRISTLE_BIG, 0.52))
    for zone, cs, preset, share in plan:
        s.say(s.caption[0], zone)
        pick_zone(s, name.lower(), zone, zp)
        if zone == "Light":
            s.fill_ellipse(base, cx, cy, r, r)
            s.frame(cursor=(cx, cy), size=r * 0.5)
        s.brush(preset, max(10, int(r * share)))
        for i, c in enumerate(cs):
            pts = s.contour(cx, cy, r, c, wobble=0.015, seed=i * 1.7)
            if len(pts) > 2:
                s.stroke(paint, pts, segments=seg)
        if slow:
            s.frame(hold=8)
    # reflected light: a band along the rim facing away from the light
    s.say(s.caption[0], "Reflected light")
    pick_zone(s, name.lower(), "Reflected light", zp)
    s.brush(BRISTLE, max(10, int(r * 0.16)))
    away = math.atan2(-LIGHT[1], -LIGHT[0])
    pts = [(cx + 0.93 * r * math.cos(away + a), cy + 0.93 * r * math.sin(away + a))
           for a in [math.radians(d) for d in range(-55, 56, 8)]]
    s.stroke(paint, pts, segments=seg)
    if slow:
        s.frame(hold=8)
    # the highlight last
    s.say(s.caption[0], "Highlight")
    pick_zone(s, name.lower(), "Highlight", zp)
    hx, hy = cx + r * LIGHT[0] * 0.62, cy + r * LIGHT[1] * 0.62
    s.fill_ellipse(paint, hx, hy, r * 0.2, r * 0.15)
    s.frame(cursor=(hx, hy), size=r * 0.3)
    s.brush(BRISTLE, max(6, int(r * 0.12)))
    s.stroke(paint, [(hx - r * 0.12, hy + r * 0.05), (hx + r * 0.1, hy - r * 0.06)], segments=1)
    s.frame(hold=10 if slow else 4)


def step_ball_intro(s):
    s._base = None
    s.say("Pick a colour", "it becomes the sphere's base")
    s.leave()
    s.lumina_base("#9aa0aa")
    s.frame(hold=20)
    s.lumina_base(BASES["ball"]); s._base = "ball"
    s.frame(hold=40)
    s.say("Read the light off the sphere", "")
    zp = s.zone_points()
    for z in ("Light", "Halftone", "Terminator", "Core shadow", "Reflected light", "Highlight"):
        s.say("Read the light off the sphere", z)
        s.hover(*zp[z])
        s.frame(hold=26)
    s.save()


def step_ball_paint(s):
    s.say("Paint it zone by zone", "")
    zp = s.zone_points()
    paint_sphere(s, "Ball", BALL, zp, slow=True)
    s.save()


def step_others(s):
    s.say("Same light, any shape", "Coconut")
    zp = s.zone_points()
    paint_sphere(s, "Coconut", COCONUT, zp, slow=False)
    # crate: three faces, each the zone its facing gets
    s.say("Same light, any shape", "Crate")
    x, y, size, d = CRATE
    # behind the ball: just above the shadows
    g = s.group("Crate", above=s.layers["Shadows"])
    eng = s.dock.processor.engine
    faces = (("top", [(x, y), (x + size, y), (x + size + d, y - d * 0.55), (x + d, y - d * 0.55)], eng.form_zone(0.25, -0.8)),
             ("side", [(x + size, y), (x + size + d, y - d * 0.55), (x + size + d, y + size - d * 0.55), (x + size, y + size)], eng.form_zone(0.85, -0.1)),
             ("front", [(x, y), (x + size, y), (x + size, y + size), (x, y + size)], eng.form_zone(-0.35, 0.15)))
    lay = s.layer("Crate faces", g)
    for fname, poly, zone in faces:
        pick_zone(s, "crate", zone, zp)
        s.fill_polygon(lay, poly)
        cxp = sum(p[0] for p in poly) / 4.0; cyp = sum(p[1] for p in poly) / 4.0
        s.frame(cursor=(cxp, cyp), size=40)
    pick_zone(s, "crate", "Core shadow", zp)
    s.brush(ROUND, 4)
    for k in (1, 2):
        s.stroke(lay, [(x + 8, y + size * k / 3.0), (x + size - 8, y + size * k / 3.0)], segments=1)
        s.stroke(lay, [(x + size + 6, y + size * k / 3.0 - 3), (x + size + d - 6, y + size * k / 3.0 - d * 0.55 + 3)], segments=1)
    s.frame(hold=6)
    # bucket: vertical bands across the cylinder
    s.say("Same light, any shape", "Bucket")
    x0, x1, top, bot, eh = BUCKET
    base, paint = sphere_group(s, "Bucket", above=s.layers["Crate"])
    pick_zone(s, "bucket", "Light", zp)
    poly = [(x0, top)] + [(x0 + (x1 - x0) * t, bot + eh * math.sqrt(max(0, 1 - (2 * t - 1) ** 2))) for t in [k / 16.0 for k in range(17)]] + [(x1, top)]
    s.fill_polygon(base, poly)
    s.frame()
    cxm, hw = (x0 + x1) / 2.0, (x1 - x0) / 2.0
    for zone, (s0, s1) in (("Halftone", (-0.35, 0.15)), ("Terminator", (-0.62, -0.42)), ("Core shadow", (-1.0, -0.66)), ("Highlight", (0.5, 0.62))):
        pick_zone(s, "bucket", zone, zp)
        s.brush(BRISTLE, max(8, int(hw * (s1 - s0) * 1.1)))
        xm = cxm + hw * (s0 + s1) / 2.0
        s.stroke(paint, [(xm, top - 6), (xm, bot + eh + 6)], segments=2)
    pick_zone(s, "bucket", "Light", zp)
    s.fill_ellipse(base, cxm, top, hw, eh)
    pick_zone(s, "bucket", "Core shadow", zp)
    s.fill_ellipse(paint, cxm, top + 2, hw - 9, eh - 6)
    pick_zone(s, "bucket", "Terminator", zp)
    s.brush(ROUND, 6)
    handle = [(cxm + hw * 0.92 * math.cos(math.radians(a)), top - 10 - 70 * math.sin(math.radians(a))) for a in range(0, 181, 15)]
    s.stroke(s.layer("Bucket handle", above=s.layers["Bucket"]), handle, segments=2)
    s.leave()
    s.say("Same light, any shape", "")
    s.frame(hold=30)
    s.save()



def reset(s):
    """Remove everything this script painted (its own document only)."""
    for n in list(s.doc.rootNode().childNodes()):
        n.remove()
    s.layers = {}
    s.meta = []
    s.doc.refreshProjection()



def rollback(s, keep_frames, groups=()):
    """Undo a step: drop its frames and the groups it painted."""
    import glob
    for f in s.meta[keep_frames:]:
        try:
            os.remove(os.path.join(FRAMES, f["file"]))
        except OSError:
            pass
    s.meta = s.meta[:keep_frames]
    for g in groups:
        node = s.layers.pop(g, None)
        if node is not None:
            node.remove()
        for k in [k for k in s.layers if k.startswith(g + " ")]:
            s.layers.pop(k)
    s.doc.refreshProjection()
    s.save()
