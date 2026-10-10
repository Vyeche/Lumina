#!/usr/bin/env python3
"""Compose the Lumina Short from frames captured in Krita (short_krita.py).

1080x1920, 30 fps, captions only. The real Krita window (Lumina docker on the
left, the canvas on the right) sits in the middle; a close-up of the objects
below; the brush cursor drawn where each stroke is being laid.
"""
import json
import os
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PyQt5.QtGui import QColor, QFont, QImage, QPainter, QPen  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])
WORK = os.path.expanduser("~/Videos/L/Lumina/work")
FRAMES = os.path.join(WORK, "frames")
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/Videos/L/Lumina/lumina_short.mp4")
W, H, FPS = 1080, 1920, 30
CAP_H = 300
WIN_Y = CAP_H
CLOSE_SRC = QRectF(466, 640, 600, 330)       # the objects, in window pixels
PANEL_SRC = QRectF(0, 205, 453, 410)         # Lumina's sphere and readout
PANEL_BEATS = ("Pick a colour", "Read the light off the sphere")
CLOSE_DST = QRectF(0, WIN_Y + 1068, 1080, H - WIN_Y - 1068)
BG = QColor(24, 26, 31)


def font(px, bold=True):
    f = QFont("Sans")
    f.setPixelSize(px)
    f.setBold(bold)
    return f


def cursor(p, x, y, r, dx=0.0, dy=0.0, scale=1.0):
    if r <= 0:
        return
    cx, cy, rr = dx + x * scale, dy + y * scale, max(4.0, r * scale)
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QColor(0, 0, 0, 170), 3))
    p.drawEllipse(QPointF(cx, cy), rr, rr)
    p.setPen(QPen(QColor(255, 255, 255, 230), 1.4))
    p.drawEllipse(QPointF(cx, cy), rr, rr)


def compose(win_img, caption, sub, cur):
    f = QImage(W, H, QImage.Format_RGB32)
    f.fill(BG)
    p = QPainter(f)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)
    p.drawImage(QPointF(0, WIN_Y), win_img)
    # close-up: Lumina while picking and reading, the objects while painting
    if caption in PANEL_BEATS:
        p.fillRect(CLOSE_DST, QColor(46, 50, 58))
        sc = CLOSE_DST.height() / PANEL_SRC.height()
        w = PANEL_SRC.width() * sc
        p.drawImage(QRectF((W - w) / 2.0, CLOSE_DST.y(), w, CLOSE_DST.height()), win_img, PANEL_SRC)
    else:
        p.drawImage(CLOSE_DST, win_img, CLOSE_SRC)
    p.setPen(QPen(QColor(255, 255, 255, 40), 2))
    p.setBrush(Qt.NoBrush)
    p.drawRect(CLOSE_DST.adjusted(1, 1, -1, -1))
    if cur:
        x, y, r = cur
        cursor(p, x, y, r, 0, WIN_Y)
        sx = CLOSE_DST.width() / CLOSE_SRC.width()
        if caption not in PANEL_BEATS and CLOSE_SRC.contains(QPointF(x, y)):
            cursor(p, x - CLOSE_SRC.x(), y - CLOSE_SRC.y(), r, CLOSE_DST.x(), CLOSE_DST.y(), sx)
    if caption:
        p.setPen(QColor(255, 255, 255))
        p.setFont(font(64))
        p.drawText(QRectF(30, 50, W - 60, 120), Qt.AlignCenter, caption)
    if sub:
        p.setPen(QColor(205, 211, 222))
        p.setFont(font(38, bold=False))
        p.drawText(QRectF(30, 175, W - 60, 70), Qt.AlignCenter, sub)
    p.end()
    return f


def main():
    meta = json.load(open(os.path.join(FRAMES, "meta.json")))
    proc = subprocess.Popen(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", "%dx%d" % (W, H), "-r", str(FPS), "-i", "-",
         "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p",
         "-movflags", "+faststart", OUT], stdin=subprocess.PIPE)
    count = [0]

    def put(img, n):
        data = img.convertToFormat(QImage.Format_RGB888)
        b = data.constBits().asstring(data.sizeInBytes())
        for _ in range(max(1, int(n))):
            proc.stdin.write(b)
            count[0] += 1

    final = QImage(os.path.join(FRAMES, meta[-1]["file"]))
    # 1. the finished piece
    put(compose(final, "One light. Every object.", "painted in Krita with Lumina", None), 2.8 * FPS)
    # 2. the process, as captured
    for m in meta[:-1]:
        img = QImage(os.path.join(FRAMES, m["file"]))
        per = 2 if m["caption"] == "Set the scene" else 3
        n = per + (m.get("hold", 1) - 1)
        put(compose(img, m["caption"], m["sub"], m.get("cursor")), n)
    # 3. the end card
    put(compose(final, "Lumina", "a free lighting plugin for Krita", None), 3.2 * FPS)
    proc.stdin.close()
    proc.wait()
    compose(final, "One light. Every object.", "painted in Krita with Lumina", None).save(
        os.path.splitext(OUT)[0] + "_poster.png")
    print("wrote %s: %d frames, %.1f s" % (OUT, count[0], count[0] / float(FPS)))


if __name__ == "__main__":
    main()
