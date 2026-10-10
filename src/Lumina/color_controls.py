"""color_controls.py — Color-coded control widgets for the Lumina docker.

* :class:`Accent` — named accent palette shared by the color-coded controls.
* :class:`ColorSlider` — a thin slider whose track is either a solid accent
  fill or an arbitrary multi-stop gradient (hue / saturation / value ramps),
  with a white round handle.

All widgets are pure PyQt5 (no shading math), so they can be assembled by the
sphere widget without pulling in ``color_engine``.
"""

from typing import List, Optional, Sequence, Tuple

import math
import time

from PyQt5.QtCore import (Qt, QEasingCurve, QPoint, QPointF, QRect, QRectF, QSize,
                         QTimer, QVariantAnimation, pyqtSignal)
from PyQt5.QtGui import (QBrush, QColor, QConicalGradient, QFont, QLinearGradient,
                         QPainter, QPainterPath, QPen, QPolygonF)
from PyQt5.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy,
                             QSlider, QStyle, QStyleOption, QVBoxLayout, QWidget)

from .derivation import from_oklch, to_oklch
from .tooltip import set_tooltip
from .typed_entry import format_slider_value as format_value


TYPED_READOUT_STYLE = (
    "QLineEdit { color: #e2e6ef; font-size: 10px; "
    "background: #262b35; border-radius: 3px; padding-right: 2px; }"
    "QLineEdit:hover { background: #2e3440; }"
    "QLineEdit:focus { background: #333a48; color: #ffffff; }"
)


def _flash_clamped(widget) -> None:
    """Brief amber outline marking a clamped commit. Purely visual."""
    try:
        original = widget.styleSheet()
        widget.setStyleSheet(
            original + "QLineEdit { border: 1px solid #c98a2e; }")
        QTimer.singleShot(
            300,
            lambda: widget.setStyleSheet(original),
        )
    except Exception:  # pragma: no cover - cosmetic only
        pass


class TypedReadout(QLineEdit):
    """Editable numeric readout: Enter commits, blur reverts half-typed text.

    Tracks dirtiness itself so owners never double-commit: ``returnPressed``
    commits, and ``focusOutEvent`` restores the last committed text only when
    an edit is still dirty. The owner supplies commit/revert by connecting
    ``returnPressed`` and calling :meth:`mark_committed`.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._typed_dirty = False
        self._canonical_text = ""
        self.setFixedWidth(40)
        self.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.setFrame(False)
        self.setStyleSheet(TYPED_READOUT_STYLE)
        self.textEdited.connect(self._mark_typed_dirty)

    def _mark_typed_dirty(self, _text: str) -> None:
        self._typed_dirty = True

    def mark_committed(self, canonical_text: str) -> None:
        """Record committed text; clears the dirty flag."""
        self._typed_dirty = False
        self._canonical_text = canonical_text
        self.setText(canonical_text)

    def focusOutEvent(self, event) -> None:
        if self._typed_dirty:
            self.setText(self._canonical_text)
            self._typed_dirty = False
        super().focusOutEvent(event)


# ---------------------------------------------------------------------------
# Accent color palette — one tint per control family (Blender/Substance look)
# ---------------------------------------------------------------------------
class CheckBox(QCheckBox):
    """A QCheckBox that draws its own tick box.

    Qt paints the native indicator using the widget's own background, and the
    settings popup stylesheet sets a background on *every* QWidget. The box was
    therefore painted the same color as the popup and disappeared, leaving the
    bare tick floating there -- which reads as a stray glyph, not a checkbox.
    Painting the indicator here keeps the box, the tick and the accent fill.
    """

    BOX = 13
    GAP = 7

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)

    def sizeHint(self):
        hint = super().sizeHint()
        return QSize(hint.width() + self.GAP, max(hint.height(), self.BOX + 4))

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        top = (self.height() - self.BOX) / 2.0
        box = QRectF(0.5, top + 0.5, self.BOX - 1, self.BOX - 1)
        if self.isChecked():
            p.setBrush(QColor(Accent.BLUE))
            p.setPen(QPen(QColor(Accent.BLUE).darker(125), 1.0))
        else:
            p.setBrush(QColor("#2b3038"))
            p.setPen(QPen(QColor("#5a6270"), 1.0))
        p.drawRoundedRect(box, 3.0, 3.0)
        if self.isChecked():
            pen = QPen(QColor("#ffffff"), 1.8)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            p.drawPolyline(QPolygonF([
                QPointF(3.3, top + 6.9), QPointF(5.6, top + 9.2),
                QPointF(9.7, top + 4.1)]))
        # Draw the label ourselves: the native text is positioned for the
        # indicator Qt drew, which no longer exists.
        p.setPen(QColor("#9aa3b4"))
        p.drawText(QRectF(self.BOX + self.GAP, 0,
                          max(0, self.width() - self.BOX - self.GAP), self.height()),
                   Qt.AlignVCenter | Qt.AlignLeft, self.text())
        p.end()


class Accent:
    """Named accent colors for the color-coded controls."""

    RED = QColor(231, 72, 80)       # base / diffuse channel
    GREEN = QColor(77, 210, 129)    # green channel
    BLUE = QColor(74, 153, 246)     # blue channel
    AMBER = QColor(251, 188, 54)    # intensity / highlight
    PURPLE = QColor(167, 139, 250)  # ambient / environment
    CYAN = QColor(34, 211, 238)     # glow / specular
    NEUTRAL = QColor(160, 174, 193) # generic


# ---------------------------------------------------------------------------
# ColorSlider — thin slider with solid-accent or gradient track
# ---------------------------------------------------------------------------
class ColorSlider(QSlider):
    """A QSlider custom-painted (not QSS) for the V2 industrial look.

    The track is either:

    * a **solid accent fill** from the left edge to the handle over a dark
      remainder (default), or
    * an arbitrary **multi-stop gradient** set via :meth:`set_gradient` —
      used for the hue / saturation / value ramps where the track itself
      encodes the parameter.

    The handle is always a white disc with a subtle dark ring so it reads
    clearly on any track color.

    It also emits :attr:`beganDrag` / :attr:`endedDrag` around a mouse drag, so
    the docker can render at reduced quality *while* the value is changing and
    restore full quality once the drag finishes. Without that cue the only way
    to know a drag ended is to guess from timing, and every value change would
    otherwise pay for a full-resolution render.
    """

    beganDrag = pyqtSignal()
    endedDrag = pyqtSignal()

    # Grab radius around the knob, in pixels. The knob itself is only ~11px
    # across, so with Qt's default hit area you had to land almost exactly on
    # the circle to start a drag -- and near either end of the track the knob is
    # partly clipped, making it worse. Pressing anywhere within this radius
    # grabs the handle instead.
    GRAB_RADIUS = 18.0

    def __init__(self, accent: Accent = Accent.NEUTRAL, orientation: Qt.Orientation = Qt.Horizontal, parent=None):
        super().__init__(orientation, parent)
        self.accent = accent
        self._gradient_stops: Optional[List[Tuple[float, QColor]]] = None
        self._dragging = False
        self._drag_from_x = 0.0
        self._drag_from_value = 0
        self.setMinimumHeight(26)
        self.setFixedHeight(26)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.valueChanged.connect(self.update)

    def _travel_span(self) -> float:
        """Width the handle actually travels, matching :meth:`_handle_x`."""
        r = self._handle_radius()
        return max(1.0, float(self.width()) - 2.0 * r)

    def _value_from_x(self, x: float) -> int:
        """Slider value for a track x, matching :meth:`_handle_x` geometry."""
        r = self._handle_radius()
        lo, hi = r, float(self.width()) - r
        t = 0.0 if hi <= lo else (x - lo) / (hi - lo)
        t = max(0.0, min(1.0, t))
        return int(round(self.minimum() + t * (self.maximum() - self.minimum())))

    def mousePressEvent(self, event):
        self.by_mouse = True
        timer = getattr(self, "_wheel_timer", None)
        if timer is not None and timer.isActive():
            timer.stop()                # the burst's drag carries on as this one
        else:
            self.beganDrag.emit()
        if event.button() == Qt.LeftButton and \
                abs(event.pos().x() - self._handle_x()) <= self.GRAB_RADIUS:
            # Grabbed the knob: track the pointer *relative* to where the drag
            # started. QSlider's own handling is absolute, so the value snapped
            # to whatever x the pointer was over -- which is why a drag started
            # on the edge of the knob would jump the value the instant you
            # moved a pixel.
            self._dragging = True
            self._drag_from_x = event.pos().x()
            self._drag_from_value = self.value()
            event.accept()
            return
        if event.button() == Qt.LeftButton:
            # Pressed the track: jump the knob to the click (Krita convention),
            # then drag relatively from there so the press flows into a drag.
            self.setValue(self._value_from_x(event.pos().x()))
            self._dragging = True
            self._drag_from_x = event.pos().x()
            self._drag_from_value = self.value()
            self.setFocus(Qt.MouseFocusReason)
            event.accept()
            return
        # Non-left press: focus for keyboard arrows, never moves the value.
        self._dragging = False
        self.setFocus(Qt.MouseFocusReason)
        event.accept()

    def mouseMoveEvent(self, event):
        if self._dragging and event.buttons() & Qt.LeftButton:
            per_px = (self.maximum() - self.minimum()) / self._travel_span()
            delta = (event.pos().x() - self._drag_from_x) * per_px
            self.setValue(int(round(self._drag_from_value + delta)))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._dragging = False
        super().mouseReleaseEvent(event)
        # Emitted after the base handler so the final value is already set.
        self.endedDrag.emit()

    # A burst of wheel notches (or held arrow keys) counts as one drag, ending
    # this long after the last step. Without it every notch paid for a
    # full-quality render (~140 ms), and a quick scroll queued seconds of them.
    WHEEL_SETTLE_MS = 250
    STEP_KEYS = (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down,
                 Qt.Key_PageUp, Qt.Key_PageDown, Qt.Key_Home, Qt.Key_End)

    @property
    def wheeling(self) -> bool:
        """True during a wheel or key burst (not a mouse drag)."""
        timer = getattr(self, "_wheel_timer", None)
        return timer is not None and timer.isActive()

    # Whether the current (or last) drag is a mouse press, not a wheel or
    # key burst. Only a mouse drag gets the knob's animations.
    by_mouse = True

    def _step_burst(self) -> None:
        """Open (or extend) a wheel / key burst."""
        self.by_mouse = False
        timer = getattr(self, "_wheel_timer", None)
        if timer is None:
            timer = self._wheel_timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self.endedDrag.emit)
        if not timer.isActive():
            timer.start(self.WHEEL_SETTLE_MS)  # active first: begin sees it
            self.beganDrag.emit()
        timer.start(self.WHEEL_SETTLE_MS)

    def wheelEvent(self, event):
        if not self._dragging:                  # a mouse drag already owns it
            self._step_burst()
        super().wheelEvent(event)

    def keyPressEvent(self, event):
        if not self._dragging and event.key() in self.STEP_KEYS:
            self._step_burst()
        super().keyPressEvent(event)

    def knob_color(self) -> QColor:
        """The colour under the knob: the track gradient at the current
        value where there is one, otherwise the accent."""
        stops = getattr(self, "_gradient_stops", None)
        if not stops:
            return QColor(self.accent)
        span = max(1, self.maximum() - self.minimum())
        t = (self.value() - self.minimum()) / float(span)
        stops = sorted(stops, key=lambda st: st[0])
        for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
            if t0 <= t <= t1:
                k = 0.0 if t1 <= t0 else (t - t0) / (t1 - t0)
                return QColor(int(round(c0.red() + (c1.red() - c0.red()) * k)),
                              int(round(c0.green() + (c1.green() - c0.green()) * k)),
                              int(round(c0.blue() + (c1.blue() - c0.blue()) * k)))
        return QColor(stops[-1][1] if t > stops[-1][0] else stops[0][1])

    def knob_center(self) -> QPoint:
        """The knob's centre, in this slider's coordinates."""
        return QPoint(int(round(self._handle_x())), self.height() // 2)

    def set_gradient(self, stops: Sequence[Tuple[float, QColor]]) -> None:
        """Paint the track as a gradient. ``stops`` = [(pos 0..1, QColor), ...]."""
        self._gradient_stops = list(stops) if stops else None
        self.update()

    def _handle_radius(self):
        # Fixed 10 px visual knob inside the 26 px row: precise-looking,
        # with generous vertical slop around it.
        return 10.0

    def _handle_x(self):
        """Centre x of the handle, kept inside the widget.

        Qt maps the value across the full widget width, so at either extreme the
        handle centre sits on the edge and the circle is clipped in half. The
        travel is therefore inset by the handle radius.
        """
        r = self._handle_radius()
        w = float(self.width())
        lo, hi = r, w - r
        if hi <= lo:
            return w / 2.0
        pos = self.style().sliderPositionFromValue(
            self.minimum(), self.maximum(), self.value(), self.width())
        return lo + (max(0.0, min(float(pos), w)) / max(1.0, w)) * (hi - lo)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        h = self.height()
        cy = h / 2.0
        track_w = 10.0
        # The track spans the same inset range as the handle's travel, so the
        # fill always lines up with the handle and neither is clipped.
        r = self._handle_radius()
        x0 = r
        x1 = max(r + 1.0, self.width() - r)
        hx = max(x0, min(x1, self._handle_x()))

        if self._gradient_stops:
            # Track itself encodes the parameter (hue / saturation / value).
            grad = QLinearGradient(x0, 0.0, x1, 0.0)
            for pos, color in self._gradient_stops:
                grad.setColorAt(self.clamp01(pos), QColor(color))
            painter.setPen(QPen(QBrush(grad), track_w))
            # drawLine only accepts ints for the 4-arg form, so go via QPointF.
            painter.drawLine(QPointF(x0, cy), QPointF(x1, cy))
        else:
            # Dark remainder across the full width...
            painter.setPen(QPen(QColor(58, 64, 78), track_w))
            painter.drawLine(QPointF(x0, cy), QPointF(x1, cy))
            # ...with an accent fill from the left edge up to the handle.
            # (This draw was previously missing: the accent pen was set but
            # never used, so solid sliders showed only the dark remainder.)
            if hx > x0:
                painter.setPen(QPen(self.accent, track_w))
                painter.drawLine(QPointF(x0, cy), QPointF(hx, cy))

        # White round handle with a subtle dark ring (reads on any track).
        radius = self._handle_radius()
        painter.setPen(QPen(QColor(30, 34, 44), 1.5))
        painter.setBrush(QColor(255, 255, 255))
        painter.drawEllipse(QPointF(hx, cy), radius, radius)
        painter.end()

    def setAccent(self, accent: Accent) -> None:
        self.accent = accent
        self.update()

    @staticmethod
    def clamp01(value: float) -> float:
        return 0.0 if value < 0.0 else (1.0 if value > 1.0 else float(value))

    def hue_gradient(self, steps: int = 12) -> None:
        """Rainbow track: full hue circle, so position == hue."""
        stops = []
        n = max(2, int(steps))
        for i in range(n):
            stops.append((self.clamp01(i / float(n - 1)), QColor.fromHsvF(
                self.clamp01(i / float(n - 1)), 0.92, 0.95)))
        self.set_gradient(stops)

    def sat_gradient(self, hue: float, val: float) -> None:
        """Dynamic saturation track for the active target: grey -> fully saturated.

        Per the reference the background re-renders to
        ``[HSL(h, 0%, v) -> HSL(h, 100%, v)]`` whenever the hue or value moves.
        """
        h = self.clamp01(hue) % 1.0
        v = max(0.0, min(1.0, float(val)))
        stops = []
        n = 8
        for i in range(n):
            t = i / float(n - 1)
            c = QColor.fromHsvF(h, t, v)
            # Keep the desaturated end readable against the dark panel.
            c = QColor(
                int(c.red() * (1 - t)) + int(20 * t),
                int(c.green() * (1 - t)) + int(22 * t),
                int(c.blue() * (1 - t)) + int(28 * t),
            )
            stops.append((t, c))
        self.set_gradient(stops)

    def value_gradient(self, hue: float, sat: float) -> None:
        """Dark -> light track for the active target's value."""
        h = self.clamp01(hue) % 1.0
        s = max(0.0, min(1.0, float(sat)))
        stops = []
        n = 8
        for i in range(n):
            t = i / float(n - 1)
            stops.append((t, QColor.fromHsvF(h, s, t * 0.96 + 0.04)))
        self.set_gradient(stops)


# ---------------------------------------------------------------------------
# CollapsibleSection — disclosure group for the advanced controls
# ---------------------------------------------------------------------------
def step_sweat_drops(drops, dt_ms: float, life_ms: float):
    """Advance sweat drops ([x, y, vx, vy, age_ms]) by ``dt_ms``: they drift,
    fall under gravity and age; the ones past ``life_ms`` are dropped."""
    dt = dt_ms / 1000.0
    for d in drops:
        d[0] += d[2] * dt
        d[1] += d[3] * dt
        d[3] += 300.0 * dt
        d[4] += dt_ms
    return [d for d in drops if d[4] < life_ms]


def paint_sweat_drops(p: QPainter, drops, life_ms: float) -> None:
    """Cartoon sweat: light-blue teardrops, tip up, fading as they fall.
    Shared by the lemon and the sliders, so both sweat alike."""
    for x, y, vx, vy, age in drops:
        fade = max(0.0, 1.0 - age / life_ms)
        r = 4.3
        drop = QPainterPath(QPointF(0.0, -r * 2.3))
        drop.cubicTo(QPointF(r * 0.55, -r * 1.3), QPointF(r * 1.05, -r * 0.45), QPointF(r, 0.15 * r))
        drop.arcTo(QRectF(-r, -r * 0.85, 2 * r, 2 * r), 0.0, -180.0)
        drop.cubicTo(QPointF(-r * 1.05, -r * 0.45), QPointF(-r * 0.55, -r * 1.3), QPointF(0.0, -r * 2.3))
        p.save()
        p.translate(x, y)
        p.rotate(18.0 if vx > 0 else -18.0)
        p.setPen(QPen(QColor(40, 120, 190, int(230 * fade)), 1.1))
        p.setBrush(QColor(150, 210, 255, int(235 * fade)))
        p.drawPath(drop)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, int(200 * fade)))
        p.drawEllipse(QPointF(-r * 0.35, -r * 0.1), r * 0.28, r * 0.38)
        p.restore()


class SweatEmitter(QWidget):
    """Sweat drops popping off a point that may move (a slider's knob held
    too long). Covers its parent, transparent to the mouse; drops live in
    the parent's coordinates, so ones already falling stay where they are
    while the knob moves on."""

    EVERY_MS = 300
    LIFE_MS = 650
    TICK_MS = 30

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._drops = []
        self._source = None           # callable -> QPoint in parent coords
        self._since = 0.0
        self._side = 1.0
        self._tick = QTimer(self)
        self._tick.setInterval(self.TICK_MS)
        self._tick.timeout.connect(self._step)
        self.hide()

    @staticmethod
    def _drops_rect(drops) -> QRect:
        """The area the drops cover. Only that is repainted each tick: the
        overlay spans the panel, and a whole-panel update redrew everything
        under it, the sphere included, 33 times a second."""
        rect = QRect()
        for d in drops:
            rect = rect.united(QRect(int(d[0]) - 14, int(d[1]) - 16, 28, 32))
        return rect

    @property
    def active(self) -> bool:
        return self._source is not None

    def start(self, source) -> None:
        """Start sweating from ``source()`` (re-read every tick)."""
        self._source = source
        self._since = self.EVERY_MS                    # first drop at once
        # Shown once and left up (it paints nothing when dry): showing or
        # hiding a panel-sized overlay redraws the whole panel under it,
        # which was a hitch each time the sweat started.
        parent = self.parentWidget()
        if parent is not None and self.geometry() != parent.rect():
            self.setGeometry(parent.rect())
        if not self.isVisible():
            self.show()
            self.raise_()
        self._tick.start()

    def stop(self) -> None:
        """No new drops; the ones in the air finish falling."""
        self._source = None

    def _step(self) -> None:
        dirty = self._drops_rect(self._drops)
        self._drops = step_sweat_drops(self._drops, self.TICK_MS, self.LIFE_MS)
        if self._source is not None:
            self._since += self.TICK_MS
            if self._since >= self.EVERY_MS:
                self._since = 0.0
                try:
                    at = self._source()
                except Exception:
                    at = None
                if at is not None:
                    side = self._side
                    self._side = -side
                    self._drops.append([at.x() + side * 9.0, at.y() - 8.0, side * 42.0, -46.0, 0.0])
        elif not self._drops:
            self._tick.stop()
        dirty = dirty.united(self._drops_rect(self._drops))
        if not dirty.isEmpty():
            self.update(dirty)

    def paintEvent(self, event):
        if not self._drops:
            return
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            paint_sweat_drops(p, self._drops, self.LIFE_MS)
        finally:
            p.end()


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    """Linear blend of two colours, alpha included (t = 0 -> a, 1 -> b)."""
    return QColor(int(round(a.red() + (b.red() - a.red()) * t)),
                  int(round(a.green() + (b.green() - a.green()) * t)),
                  int(round(a.blue() + (b.blue() - a.blue()) * t)),
                  int(round(a.alpha() + (b.alpha() - a.alpha()) * t)))


class ColorSampler(QWidget):
    """The colour sampler: a lemon (Lumina's own) resting on the sphere.

    It lies along the surface under the pointer: its long axis follows the
    curve and it is foreshortened by the surface's tilt, round-bodied at the
    centre and an ever slimmer lemon toward the silhouette. Near the centre,
    where "along the curve" has no direction, it eases to a natural tilt so it
    does not spin as the pointer crosses the middle.

    The lemon is the readout: it is filled with the exact colour of the pixel
    underneath. Its only edge is that same colour a step darker on a light
    colour and a step lighter on a dark one -- in lightness only, keeping the
    colour's own saturation, so it never fades toward white or black -- and so
    it stands out from the sphere around it in its own hue. A click pops it:
    it swells briefly and settles a little larger while the button is held,
    back to its size on release. Hold it too long and it starts to sweat:
    drops pop off its upper sides and fall away until it is let go.

    It is a sampler, not a magnifier: it enlarges nothing.
    """

    HALF_LENGTH = 22.0     # tip to centre along the long axis, in pixels
    HALF_WIDTH = 16.0      # body half-width seen head-on
    MIN_SQUASH = 0.62      # slimmest it gets at the rim, still a lemon
    REST_ANGLE = -35.0     # its tilt near the centre, in degrees
    EDGE_STEP = 0.24       # OKLab lightness between the fill and its edge
    EDGE_BLEND = 0.12      # around EDGE_SPLIT the step fades through zero
    EDGE_CHROMA = 1.35     # the edge keeps (and lifts) the fill's chroma
    EDGE_WIDTH = 2.0
    POP_SCALE = 1.25       # how far a click swells it
    HOLD_SCALE = 1.10      # where it settles while the button is held
    POP_MS = 240           # swell and settle, in milliseconds
    RELEASE_MS = 140       # back to rest on release
    SWEAT_AFTER_MS = 1200  # held this long, it starts to sweat
    SWEAT_EVERY_MS = 300   # a drop this often while sweating
    DROP_LIFE_MS = 650     # each drop arcs out, falls and fades
    TICK_MS = 30
    EDGE_SPLIT = 0.55      # perceived lightness where the edge flips

    def __init__(self, parent=None):
        super().__init__(parent)
        self._color = QColor(200, 60, 60)
        self._active = False          # True while the pointer is held down
        self._pop = 1.0               # size scale: 1 at rest
        self._pop_end = 1.0           # where the running animation ends
        self._pop_anim = QVariantAnimation(self)
        self._pop_anim.setEasingCurve(QEasingCurve.OutQuad)
        self._pop_anim.valueChanged.connect(self._on_pop)
        # The last animated frame can land just short; end exactly there.
        self._pop_anim.finished.connect(lambda: self._on_pop(self._pop_end))
        # Sweat: starts after SWEAT_AFTER_MS held; drops are
        # [x, y, vx, vy, age_ms] around the widget centre, in pixels.
        self._sweating = False
        self._drops = []
        self._since_drop = 0.0
        self._next_side = 1.0
        self._sweat_timer = QTimer(self)
        self._sweat_timer.setSingleShot(True)
        self._sweat_timer.timeout.connect(self._start_sweat)
        self._tick = QTimer(self)
        self._tick.setInterval(self.TICK_MS)
        self._tick.timeout.connect(self._step_drops)
        self._u = 0.0                 # surface position under the pointer,
        self._v = 0.0                 # sphere coordinates in [-1, 1]
        self._label = ""              # hex of the colour under it, shown below
        # Room for the drops to arc out around the lemon.
        self.setFixedSize(112, 112)
        # Never intercept input: the sphere underneath must keep getting the events.
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def set_color(self, color: Optional[QColor]) -> None:
        if color is not None and color.isValid():
            self._color = QColor(color)
        self.update()

    def set_label(self, text: str) -> None:
        """The hex of the colour under the lemon, shown in a pill below it."""
        text = str(text or "")
        if text != self._label:
            self._label = text
            self.update()

    def set_active(self, active: bool) -> None:
        """Pointer pressed (pop, then hold a little larger; start the sweat
        clock) or released (back to rest; no new drops)."""
        active = bool(active)
        if active == self._active:
            return
        self._active = active
        if active:
            self._animate(((0.0, self._pop), (0.35, self.POP_SCALE),
                           (1.0, self.HOLD_SCALE)), self.POP_MS)
            self._sweat_timer.start(self.SWEAT_AFTER_MS)
            # The countdown ring fills while held; the tick repaints it.
            self._held_at = time.monotonic()
            self._tick.start()
        else:
            self._animate(((0.0, self._pop), (1.0, 1.0)), self.RELEASE_MS)
            self._sweat_timer.stop()
            self._sweating = False

    def _animate(self, keys, ms: int) -> None:
        self._pop_anim.stop()
        self._pop_anim.setDuration(int(ms))
        self._pop_anim.setKeyValues([(float(t), float(v)) for t, v in keys])
        self._pop_end = float(keys[-1][1])
        self._pop_anim.start()

    def _on_pop(self, value) -> None:
        self._pop = float(value)
        self.update()

    def _start_sweat(self) -> None:
        if self._active:
            self._sweating = True
            self._since_drop = self.SWEAT_EVERY_MS      # first drop right away
            self._tick.start()

    def _spawn_drop(self) -> None:
        """A drop just off the lemon's upper outline, flung away from it."""
        turn, squash = self._shape()
        side = self._next_side
        self._next_side = -side
        a = self.HALF_LENGTH * self._pop
        b = self.HALF_WIDTH * self._pop * squash
        c, s_ = math.cos(math.radians(turn)), math.sin(math.radians(turn))
        best = None
        for k in (1.0, -1.0):                          # the side that is up
            lx, ly = side * 0.58 * a, -k * 1.05 * b
            sx, sy = lx * c - ly * s_, lx * s_ + ly * c
            if best is None or sy < best[1]:
                best = (sx, sy)
        x, y = best
        n = math.hypot(x, y) or 1.0
        x, y = x + 4.0 * x / n, y + 4.0 * y / n        # just outside the edge
        self._drops.append([x, y, 46.0 * x / n, 34.0 * y / n - 40.0, 0.0])

    def _step_drops(self) -> None:
        self._drops = step_sweat_drops(self._drops, self.TICK_MS, self.DROP_LIFE_MS)
        if self._sweating:
            self._since_drop += self.TICK_MS
            if self._since_drop >= self.SWEAT_EVERY_MS:
                self._since_drop = 0.0
                self._spawn_drop()
        elif not self._drops and not self._active:
            self._tick.stop()
        self.update()

    def _hold_progress(self) -> float:
        """0..1 through the wait before sweating, while held; else 0."""
        if not self._active or self._sweating:
            return 0.0
        held = (time.monotonic() - getattr(self, "_held_at", time.monotonic())) * 1000.0
        return max(0.0, min(1.0, held / self.SWEAT_AFTER_MS))

    def _paint_label(self, p: QPainter) -> None:
        """The hex in a small dark pill under the lemon."""
        font = QFont(self.font())
        font.setPixelSize(10)
        font.setBold(True)
        p.setFont(font)
        width = p.fontMetrics().horizontalAdvance(self._label) + 10
        rect = QRectF(-width / 2.0, self.HALF_LENGTH * self._pop + 10.0, width, 14.0)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(20, 22, 28, 205))
        p.drawRoundedRect(rect, 7.0, 7.0)
        p.setPen(QColor(236, 240, 246))
        p.drawText(rect, Qt.AlignCenter, self._label)

    def _paint_drops(self, p: QPainter) -> None:
        paint_sweat_drops(p, self._drops, self.DROP_LIFE_MS)

    def set_surface(self, u: float, v: float) -> None:
        """Where on the sphere the lemon rests, in sphere coordinates (centre
        0, 0; silhouette at radius 1). Sets its turn and foreshortening."""
        r = math.hypot(u, v)
        if r > 1.0:
            u, v = u / r, v / r
        if (u, v) != (self._u, self._v):
            self._u, self._v = u, v
            self.update()

    def _shape(self):
        """(turn of the long axis in degrees, squash of the short axis)."""
        u, v = self._u, self._v
        r = math.hypot(u, v)
        nz = math.sqrt(max(0.0, 1.0 - r * r))
        squash = self.MIN_SQUASH + (1.0 - self.MIN_SQUASH) * nz
        rest = math.radians(self.REST_ANGLE)
        dx, dy = math.cos(rest), math.sin(rest)
        if r > 1e-4:
            tx, ty = -v / r, u / r                 # along the curve
            if tx * dx + ty * dy < 0.0:            # a lemon reads the same
                tx, ty = -tx, -ty                  # either way round
            t = min(1.0, max(0.0, (r - 0.15) / 0.45))
            w = t * t * (3.0 - 2.0 * t)
            dx, dy = dx + (tx - dx) * w, dy + (ty - dy) * w
        return math.degrees(math.atan2(dy, dx)), squash

    @classmethod
    def edge_color(cls, color: QColor) -> QColor:
        """The lemon's edge: its own colour, darker if light, lighter if dark,
        moved in OKLab lightness only so it keeps its hue and saturation
        (blending toward white turned dark colours' edges pale grey)."""
        L, C, h = to_oklch((color.red(), color.green(), color.blue()))
        # Darker on light colours, lighter on dark ones. Near the split the
        # step eases through zero, so the edge passes through the picked
        # colour itself as it turns instead of jumping from one to the other.
        t = max(-1.0, min(1.0, (L - cls.EDGE_SPLIT) / cls.EDGE_BLEND))
        ease = t * (1.5 - 0.5 * t * t)            # smooth, +-1 at the ends
        lift = 1.0 + (cls.EDGE_CHROMA - 1.0) * abs(ease)
        return QColor(*from_oklch(L - cls.EDGE_STEP * ease, C * lift, h))

    @staticmethod
    def _lemon_path(a: float, b: float) -> QPainterPath:
        """A lemon along x: a full oval body (half-width ``b``) with a short,
        rounded nub at each end; ``a`` is centre to nub tip."""
        body = a * 0.80                            # where each nub begins
        path = QPainterPath(QPointF(-a, 0.0))
        for sign in (-1.0, 1.0):                   # top half, then bottom
            ends = ((-a, -body), (body, a)) if sign < 0 else ((a, body), (-body, -a))
            (tip0, flank0), (flank1, tip1) = ends
            # Nub out of the first tip into the body.
            path.cubicTo(QPointF(tip0, sign * 0.13 * b), QPointF(flank0 + (0.03 * a if flank0 < 0 else -0.03 * a), sign * 0.24 * b),
                         QPointF(flank0, sign * 0.34 * b))
            # The body's full curve to the other side.
            path.cubicTo(QPointF(flank0 * 0.72, sign * 1.14 * b), QPointF(flank1 * 0.72, sign * 1.14 * b),
                         QPointF(flank1, sign * 0.34 * b))
            # Into the far nub's rounded tip.
            path.cubicTo(QPointF(flank1 + (0.03 * a if flank1 > 0 else -0.03 * a), sign * 0.24 * b), QPointF(tip1, sign * 0.13 * b),
                         QPointF(tip1, 0.0))
        path.closeSubpath()
        return path

    def paintEvent(self, event):
        try:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing, True)
            turn, squash = self._shape()
            grow = self._pop
            a, b = self.HALF_LENGTH * grow, self.HALF_WIDTH * grow * squash
            p.translate(self.width() / 2.0, self.height() / 2.0)
            p.rotate(turn)
            lemon = self._lemon_path(a, b)

            colour = QColor(self._color)

            # Body: exactly the picked colour, flat so it reads true, edged in
            # its own colour shifted well apart for contrast (no black outline).
            p.setBrush(QBrush(colour))
            p.setPen(QPen(self.edge_color(colour), self.EDGE_WIDTH))
            p.drawPath(lemon)

            # A small gloss.
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 80))
            p.drawEllipse(QPointF(-a * 0.28, -b * 0.42), a * 0.26, b * 0.20)

            # Sweat, in screen orientation around the lemon's centre.
            p.resetTransform()
            p.translate(self.width() / 2.0, self.height() / 2.0)
            if self._drops:
                self._paint_drops(p)
            progress = self._hold_progress()
            if progress > 0.0:
                # Countdown to sweating: a faint ring fills round the lemon.
                ring = self.HALF_LENGTH * self._pop + 6.0
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(QColor(255, 255, 255, 60), 2.0))
                p.drawEllipse(QPointF(0.0, 0.0), ring, ring)
                p.setPen(QPen(QColor(255, 255, 255, 190), 2.0, Qt.SolidLine, Qt.RoundCap))
                p.drawArc(QRectF(-ring, -ring, 2 * ring, 2 * ring), 90 * 16,
                          -int(progress * 360 * 16))
            if self._label:
                self._paint_label(p)
            p.end()
        except Exception as exc:  # pragma: no cover - cosmetic only
            print("ColorSampler: paintEvent failed - %s" % exc)


class SparkleBurst(QWidget):
    """A one-shot burst of sparkles, played around the target dot when the
    user selects Shade, Base or High. Transparent to the mouse; parented to
    the panel and centred on the dot so the sparkles are not clipped."""

    SIZE = 96
    COUNT = 9
    MS = 620

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._t = 1.0
        self._color = QColor(255, 255, 255)
        self._sparks = []             # (angle, distance, size, twinkle phase)
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(self.MS)
        self._anim.valueChanged.connect(self._on_step)
        self._anim.finished.connect(self.hide)
        self.hide()

    def play(self, color: QColor, centre: QPoint) -> None:
        """Burst from ``centre`` (in the parent's coordinates) in ``color``."""
        import random
        self._color = QColor(color)
        step = 360.0 / self.COUNT
        self._sparks = [(math.radians(i * step + random.uniform(-12.0, 12.0)),
                         random.uniform(26.0, 40.0), random.uniform(4.6, 7.4),
                         random.uniform(0.0, math.pi))
                        for i in range(self.COUNT)]
        self.move(centre.x() - self.SIZE // 2, centre.y() - self.SIZE // 2)
        self._t = 0.0
        self.show()
        self.raise_()
        self._anim.stop()
        self._anim.start()

    def _on_step(self, value) -> None:
        self._t = float(value)
        self.update()

    @staticmethod
    def _star(size: float) -> QPainterPath:
        """A four-point sparkle."""
        w = size * 0.28
        path = QPainterPath(QPointF(0.0, -size))
        path.quadTo(QPointF(w, -w), QPointF(size, 0.0))
        path.quadTo(QPointF(w, w), QPointF(0.0, size))
        path.quadTo(QPointF(-w, w), QPointF(-size, 0.0))
        path.quadTo(QPointF(-w, -w), QPointF(0.0, -size))
        path.closeSubpath()
        return path

    def paintEvent(self, event):
        if self._t >= 1.0 or not self._sparks:
            return
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            p.translate(self.SIZE / 2.0, self.SIZE / 2.0)
            t = self._t
            ease = 1.0 - (1.0 - t) ** 3               # fast out, settling
            fade = 1.0 - t
            tint = self._color.lighter(150)
            for i, (ang, dist, size, phase) in enumerate(self._sparks):
                r = 10.0 + dist * ease
                twinkle = 0.75 + 0.25 * math.sin(phase + t * 18.0)
                sz = size * (1.0 - 0.4 * t) * twinkle
                fill = QColor(255, 255, 255) if i % 3 == 0 else QColor(tint)
                fill.setAlpha(int(255 * fade))
                p.save()
                p.translate(math.cos(ang) * r, math.sin(ang) * r)
                p.rotate(t * 90.0)
                p.setPen(Qt.NoPen)
                p.setBrush(fill)
                p.drawPath(self._star(sz))
                p.restore()
        finally:
            p.end()


class KnobRipple(QWidget):
    """A soft ripple from a slider's knob when it is let go: two rings in the
    knob's colour spread out and fade, with a brief glow on the knob.
    Transparent to the mouse, parented to the panel and centred on the knob."""

    SIZE = 72
    MS = 520

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._t = 1.0
        self._color = QColor(255, 255, 255)
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(self.MS)
        self._anim.valueChanged.connect(self._on_step)
        self._anim.finished.connect(self.hide)
        self.hide()

    def play(self, color: QColor, centre: QPoint) -> None:
        self._color = QColor(color)
        self.move(centre.x() - self.SIZE // 2, centre.y() - self.SIZE // 2)
        self._t = 0.0
        self.show()
        self.raise_()
        self._anim.stop()
        self._anim.start()

    def _on_step(self, value) -> None:
        self._t = float(value)
        self.update()

    def paintEvent(self, event):
        if self._t >= 1.0:
            return
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            c = QPointF(self.SIZE / 2.0, self.SIZE / 2.0)
            ring = QColor(self._color).lighter(135)
            for delay in (0.0, 0.28):                       # two rings, staggered
                t = (self._t - delay) / (1.0 - delay)
                if t <= 0.0:
                    continue
                ease = 1.0 - (1.0 - t) ** 3
                col = QColor(ring)
                col.setAlpha(int(220 * (1.0 - t)))
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(col, 2.2 * (1.0 - 0.5 * t)))
                radius = 10.0 + 22.0 * ease
                p.drawEllipse(c, radius, radius)
            halo = QColor(255, 255, 255, int(120 * max(0.0, 1.0 - self._t * 2.5)))
            p.setPen(Qt.NoPen)
            p.setBrush(halo)
            p.drawEllipse(c, 11.0, 11.0)
        finally:
            p.end()


class RecentColors(QWidget):
    """A strip of the colours most recently picked from the sphere, newest
    first. Click one to paint with it again, double-click to make it the
    selected target's colour, right-click to pin or remove it; *Clear*
    empties it (pins stay). Pinned colours sit first and are never pushed
    out by new picks."""

    LIMIT = 10
    CHIP = 18
    TIP = "click: paint with it  -  double-click: set the selected target  -  right-click: pin / remove"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._colors = []             # QColor: pinned first, then newest first
        self._pinned = set()          # names of pinned colours
        self._on_pick = None
        self._on_assign = None
        self._on_change = None
        self._hover = -1
        self.setMouseTracking(True)
        self.setFixedHeight(self.CHIP + 22)
        self.setCursor(Qt.PointingHandCursor)

    def bind(self, on_pick, on_assign=None, on_change=None) -> None:
        self._on_pick = on_pick
        self._on_assign = on_assign
        self._on_change = on_change

    def colors(self):
        return [QColor(c) for c in self._colors]

    def pinned(self):
        return [QColor(c) for c in self._colors if c.name() in self._pinned]

    def is_pinned(self, color) -> bool:
        return QColor(color).name() in self._pinned

    def set_colors(self, colors) -> None:
        """Restore a list; an entry may be a QColor, a name, or a name
        prefixed with ``*`` for a pinned colour."""
        out, pins = [], set()
        for c in colors:
            name = c if isinstance(c, str) else QColor(c).name()
            pin = name.startswith("*")
            q = QColor(name.lstrip("*"))
            if q.isValid() and q.name() not in [o.name() for o in out]:
                out.append(q)
                if pin:
                    pins.add(q.name())
        self._pinned = pins
        self._colors = out
        self._order()
        self.update()

    def saved(self) -> str:
        """The list as saved: names, pinned ones prefixed with ``*``."""
        return ",".join(("*" if c.name() in self._pinned else "") + c.name()
                        for c in self._colors)

    def _order(self) -> None:
        """Pinned first (in their order), then the rest; trim the rest."""
        pins = [c for c in self._colors if c.name() in self._pinned]
        rest = [c for c in self._colors if c.name() not in self._pinned]
        self._colors = pins + rest[:max(0, self.LIMIT - len(pins))]

    def add(self, color: QColor) -> None:
        """Put ``color`` first among the unpinned, dropping an earlier copy."""
        color = QColor(color)
        if not color.isValid():
            return
        if color.name() in self._pinned:
            return
        self._colors = [c for c in self._colors if c.name() != color.name()]
        pins = sum(1 for c in self._colors if c.name() in self._pinned)
        self._colors.insert(pins, color)
        self._order()
        self.update()

    def toggle_pin(self, index: int) -> None:
        if 0 <= index < len(self._colors):
            name = self._colors[index].name()
            if name in self._pinned:
                self._pinned.discard(name)
            else:
                self._pinned.add(name)
            self._order()
            self._changed()

    def remove(self, index: int) -> None:
        if 0 <= index < len(self._colors):
            self._pinned.discard(self._colors[index].name())
            del self._colors[index]
            self._hover = -1
            self._changed()

    def clear(self) -> None:
        """Empty the strip; pinned colours stay."""
        self._colors = [c for c in self._colors if c.name() in self._pinned]
        self._hover = -1
        self._changed()

    def _show_menu(self, index: int, global_pos) -> None:
        from PyQt5.QtWidgets import QMenu
        menu = QMenu(self)
        pin = menu.addAction("Unpin" if self._colors[index].name() in self._pinned else "Pin")
        remove = menu.addAction("Remove")
        chosen = menu.exec_(global_pos)
        if chosen is pin:
            self.toggle_pin(index)
        elif chosen is remove:
            self.remove(index)

    def _changed(self) -> None:
        self.update()
        if self._on_change is not None:
            self._on_change()

    def _chip_rects(self):
        gap = 4.0
        top = 18.0
        return [QRectF(i * (self.CHIP + gap) + 1.0, top, self.CHIP, self.CHIP)
                for i in range(len(self._colors))]

    def _clear_rect(self) -> QRectF:
        return QRectF(self.width() - 40.0, 0.0, 40.0, 14.0)

    def _index_at(self, pos) -> int:
        for i, r in enumerate(self._chip_rects()):
            if r.contains(QPointF(pos)):
                return i
        return -1

    def mouseMoveEvent(self, event):
        i = self._index_at(event.pos())
        if i != self._hover:
            self._hover = i
            set_tooltip(self, "%s  -  %s" % (self._colors[i].name(), self.TIP) if i >= 0 else "")
            self.update()

    def leaveEvent(self, event):
        self._hover = -1
        self.update()

    def mousePressEvent(self, event):
        if self._colors and self._clear_rect().contains(QPointF(event.pos())):
            self.clear()
            event.accept()
            return
        i = self._index_at(event.pos())
        if i >= 0:
            if event.button() == Qt.RightButton:
                self._show_menu(i, event.globalPos())
            elif self._on_pick is not None:
                self._on_pick(QColor(self._colors[i]))
        event.accept()

    def mouseDoubleClickEvent(self, event):
        i = self._index_at(event.pos())
        if i >= 0 and event.button() == Qt.LeftButton and self._on_assign is not None:
            self._on_assign(QColor(self._colors[i]))
        event.accept()

    def paintEvent(self, event):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            font = QFont(self.font())
            font.setPixelSize(10)
            p.setFont(font)
            p.setPen(QColor(200, 208, 222))
            p.drawText(QRectF(0, 0, self.width(), 14), Qt.AlignLeft | Qt.AlignVCenter,
                       "Recent picks" if self._colors else
                       "Recent picks -- click the sphere to collect colours")
            if self._colors:
                p.setPen(QColor(150, 160, 178))
                p.drawText(self._clear_rect(), Qt.AlignRight | Qt.AlignVCenter, "Clear")
            show = getattr(self, "display", None)    # working -> screen colours
            for i, (r, c) in enumerate(zip(self._chip_rects(), self._colors)):
                p.setBrush(show(c) if show is not None else c)
                hover = i == self._hover
                p.setPen(QPen(QColor(255, 255, 255, 230 if hover else 70), 2.0 if hover else 1.0))
                p.drawRoundedRect(r, 4.0, 4.0)
                if c.name() in self._pinned:
                    # Pin marker: a small white dot in the top-right corner.
                    p.setPen(QPen(QColor(20, 22, 28, 220), 1.0))
                    p.setBrush(QColor(255, 255, 255))
                    p.drawEllipse(QPointF(r.right() - 3.0, r.top() + 3.0), 2.6, 2.6)
        finally:
            p.end()


class SettingsPanel(QWidget):
    """Compact settings popup shown by the gear button.

    Deliberately holds the things that have no home on the main panel:

    * **Main light direction** - azimuth and elevation. The engine has always
      supported these but no control ever exposed them, so the light could not
      be moved without editing code.
    * **Render quality** - the sphere is shaded in pure Python, so the grid
      resolution trades smoothness against fidelity while dragging.
    * **Picker pointer** - show/hide the sampling ring on the sphere.
    * **Hex values** - show/hide the copyable hex labels under the swatches.
    * **Reset** - restore every target and lighting parameter to its default.
    """

    QUALITY = (("Low", 128), ("Med", 200), ("High", 288))

    STICKY_MAX = 100       # px past the sphere's edge

    def __init__(self, azimuth: int = 304, elevation: int = 41,
                 quality: int = 200, highlight_size: int = 80, sticky: int = 40,
                 parent=None):
        super().__init__(parent, Qt.Popup)
        self.setStyleSheet(
            "QWidget { background-color: #343941; color: #e2e6ef; }"
            "QLabel { color: #9aa3b4; font-size: 10px; }")
        self._quality_buttons = {}
        self._quality = quality
        self._on_change = None
        self._on_reset = None
        self._on_save = None
        # A shiny pulse round the border each time the popup opens: a bright
        # sweep travels once round the edge while a soft glow fades.
        self._pulse = 1.0                    # 0 -> 1 over the animation; 1 = settled
        self._pulse_anim = QVariantAnimation(self)
        self._pulse_anim.setStartValue(0.0)
        self._pulse_anim.setEndValue(1.0)
        self._pulse_anim.setDuration(self.PULSE_MS)
        self._pulse_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._pulse_anim.valueChanged.connect(self._on_pulse)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        title = QLabel("Settings")
        title.setStyleSheet("color: #e7eaf2; font-size: 11px; font-weight: bold;")
        root.addWidget(title)

        self.azimuth = self._add_slider(root, "Light azimuth", 0, 359,
                                          azimuth, unit="degree")
        self.elevation = self._add_slider(root, "Light height", 0, 90,
                                          elevation, unit="degree")
        # Highlight size, 0-100, driving the same specular sharpness as the
        # Advanced "Specular" row but in the direction a painter thinks: up
        # means a bigger, softer highlight. Kept in sync with that row by the
        # docker, since two sliders own one engine value.
        self.highlight_size = self._add_slider(root, "Highlight size",
                                                0, 100, highlight_size,
                                                unit="none")
        set_tooltip(self.highlight_size, "Highlight size on the sphere")
        # How far past the sphere's edge the color cursor keeps picking
        # before it lets go, in pixels.
        self.sticky = self._add_slider(root, "Sticky distance", 0,
                                       self.STICKY_MAX, sticky, unit="none")
        set_tooltip(self.sticky, "How far past the sphere's edge picking holds on (px)")

        qrow = QHBoxLayout()
        qrow.setSpacing(4)
        qlab = QLabel("Quality")
        qlab.setFixedWidth(74)
        qrow.addWidget(qlab)
        for name, res in self.QUALITY:
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setChecked(res == quality)
            btn.setFocusPolicy(Qt.StrongFocus)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(self._btn_style(res == quality))
            btn.clicked.connect(lambda _c, r=res, n=name: self._set_quality(r, n))
            self._quality_buttons[name] = btn
            qrow.addWidget(btn, 1)
        root.addLayout(qrow)

        # The tick box is drawn by hand -- see CheckBox for why.
        self.pointer_cb = CheckBox("Show color cursor")
        self.pointer_cb.setChecked(True)
        root.addWidget(self.pointer_cb)

        self.hex_cb = CheckBox("Show hex values")
        self.hex_cb.setChecked(True)
        root.addWidget(self.hex_cb)

        # Compact: hide the captions and Recent picks once the panel is known.
        self.compact_cb = CheckBox("Compact panel")
        self.compact_cb.setChecked(False)
        root.addWidget(self.compact_cb)

        # Sends the base color to Krita's foreground so the brush can use it.
        # Moved here from the main panel, where it sat below the presets as a
        # full-width button: it is an action, not a setting, and the panel reads
        # better without it. Kept short-tooltipped -- see the main panel notes
        # on Qt rendering long tooltips as screen-wide banners.
        self.apply_btn = QPushButton("Use base color as brush")
        self.apply_btn.setCursor(Qt.PointingHandCursor)
        set_tooltip(self.apply_btn, "Send the base color to Krita's brush color")
        self.apply_btn.setStyleSheet(
            "QPushButton { background-color: #3b414a; color: #ccd5e2; "
            "border: 1px solid rgba(255,255,255,60); border-radius: 5px; "
            "font-size: 10px; padding: 4px; }"
            "QPushButton:hover { background-color: #464d57; }"
            "QPushButton:pressed { background-color: #2e333a; }")
        root.addWidget(self.apply_btn)

        reset = QPushButton("Reset all")
        reset.setCursor(Qt.PointingHandCursor)
        reset.setStyleSheet(
            "QPushButton { background-color: #2a2f3c; color: #e2e6ef; "
            "border: 1px solid rgba(255,255,255,60); border-radius: 5px; "
            "font-size: 10px; padding: 4px; }")
        reset.clicked.connect(lambda: self._on_reset and self._on_reset())
        root.addWidget(reset)

        # Explicit checkpoint. Changes already autosave, so this is about
        # reassurance rather than necessity -- hence the confirmation label.
        self._saved_label = QLabel("")
        self._saved_label.setFixedHeight(12)
        self._saved_label.setStyleSheet("color: #6f7a8d; font-size: 9px;")
        self._saved_timer = QTimer(self)
        self._saved_timer.setSingleShot(True)
        self._saved_timer.timeout.connect(self._saved_label.clear)
        root.addWidget(self._saved_label)

        save = QPushButton("Save settings")
        save.setCursor(Qt.PointingHandCursor)
        save.setStyleSheet(
            "QPushButton { background-color: #2a4a3c; color: #dcefe4; "
            "border: 1px solid rgba(120,220,170,90); border-radius: 5px; "
            "font-size: 10px; padding: 4px; }")
        save.clicked.connect(lambda: self._on_save and self._on_save())
        root.addWidget(save)

        self.azimuth.valueChanged.connect(self._emit)
        self.elevation.valueChanged.connect(self._emit)
        self.highlight_size.valueChanged.connect(self._emit)
        self.sticky.valueChanged.connect(self._emit)
        self.pointer_cb.stateChanged.connect(self._emit)
        self.hex_cb.stateChanged.connect(self._emit)
        self.compact_cb.stateChanged.connect(self._emit)

    @staticmethod
    def _btn_style(on: bool) -> str:
        if on:
            return ("QPushButton { background-color: #33404f; color: #eef1f6; "
                    "border: 1px solid rgba(255,255,255,110); border-radius: 4px; "
                    "font-size: 9px; padding: 3px 0; }")
        return ("QPushButton { background-color: #21252e; color: #8e97a8; "
                "border: 1px solid rgba(255,255,255,35); border-radius: 4px; "
                "font-size: 9px; padding: 3px 0; }")

    def _add_slider(self, root, label, lo, hi, value, unit="percent"):
        # Label column is wide enough for the longest caption ("Light azimuth
        # size"): at 74px it was truncated mid-word. The popup grows to fit,
        # which is the requested "slightly bigger" -- no fixed popup width to
        # update.
        row = QWidget()
        rh = QHBoxLayout(row)
        rh.setContentsMargins(0, 0, 0, 0)
        rh.setSpacing(6)
        lab = QLabel(label)
        lab.setFixedWidth(110)
        rh.addWidget(lab)
        sl = ColorSlider(Accent.NEUTRAL, Qt.Horizontal)
        sl.setRange(lo, hi)
        sl.setValue(value)
        sl.setProperty("typed_unit", unit)
        rh.addWidget(sl, 1)
        # Canonical-with-unit readout (86% / 309° / 64): self-describing,
        # permissive on input via the shared parser. Enter commits; moving
        # focus away reverts.
        val = TypedReadout()
        val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        rh.addWidget(val)
        sl.valueChanged.connect(
            lambda v, s=sl, w=val: w.mark_committed(
                format_value(s.value(), s.property("typed_unit") or "percent")))
        val.mark_committed(format_value(sl.value(), unit or "percent"))
        val.returnPressed.connect(
            lambda s=sl, w=val: self._commit_typed(s, w))
        if not hasattr(self, "_readouts"):
            self._readouts = {}
        self._readouts[sl] = val
        root.addWidget(row)
        return sl

    @staticmethod
    def _commit_typed(sl, w) -> None:
        """Commit a typed readout via the shared parser; flash on clamp."""
        from .typed_entry import parse_typed_entry
        unit = sl.property("typed_unit") or "percent"
        result = parse_typed_entry(w.text(), sl.minimum(), sl.maximum(),
                                   unit=unit)
        if not result.valid:
            w.mark_committed(format_value(sl.value(), unit or "percent"))
            return
        sl.setValue(int(round(result.value)))
        w.mark_committed(format_value(sl.value(), unit or "percent"))
        if result.was_clamped:
            _flash_clamped(w)

    def _refresh_readouts(self) -> None:
        """Repaint every readout from its slider's current value.

        Needed after programmatic changes (sync_from blocks signals, so the
        live valueChanged handler never fires and the numbers go stale).
        """
        for sl, val in getattr(self, "_readouts", {}).items():
            val.mark_committed(
                format_value(sl.value(), sl.property("typed_unit") or "percent"))

    def _set_quality(self, res, name):
        self._quality = res
        for n, b in self._quality_buttons.items():
            b.setChecked(n == name)
            b.setStyleSheet(self._btn_style(n == name))
        self._emit()

    def _emit(self, *_a):
        if self._on_change is not None:
            self._on_change({
                "azimuth": self.azimuth.value(),
                "elevation": self.elevation.value(),
                "highlight_size": self.highlight_size.value(),
                "sticky": self.sticky.value(),
                "quality": self._quality,
                "pointer": self.pointer_cb.isChecked(),
                "hex": self.hex_cb.isChecked(),
                "compact": self.compact_cb.isChecked(),
            })

    PULSE_MS = 1100

    def showEvent(self, event):
        super().showEvent(event)
        self._pulse_anim.stop()
        self._pulse = 0.0
        self._pulse_anim.start()

    def _on_pulse(self, value) -> None:
        self._pulse = float(value)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        try:
            # The stylesheet background first: a custom paintEvent replaces it.
            opt = QStyleOption()
            opt.initFrom(self)
            self.style().drawPrimitive(QStyle.PE_Widget, opt, p, self)
            p.setRenderHint(QPainter.Antialiasing, True)
            rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
            # A faint outline always; the pulse brightens it as it opens.
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255, 40), 1.0))
            p.drawRoundedRect(rect, 6.0, 6.0)
            t = self._pulse
            if t < 1.0:
                glow = 1.0 - t
                p.setPen(QPen(QColor(150, 220, 255, int(110 * glow)), 3.0))
                p.drawRoundedRect(rect, 6.0, 6.0)
                sweep = QConicalGradient(rect.center(), 90.0 - t * 360.0)
                bright = QColor(255, 255, 255, int(255 * min(1.0, glow * 1.6)))
                clear = QColor(255, 255, 255, 0)
                soft = QColor(bright)
                soft.setAlpha(int(bright.alpha() * 0.55))
                sweep.setColorAt(0.0, bright)
                sweep.setColorAt(0.08, soft)
                sweep.setColorAt(0.22, clear)
                sweep.setColorAt(0.9, clear)
                sweep.setColorAt(1.0, bright)
                p.setPen(QPen(QBrush(sweep), 3.0))
                p.drawRoundedRect(rect, 6.0, 6.0)
        finally:
            p.end()

    def bind(self, on_change, on_reset, on_save=None):
        self._on_change = on_change
        self._on_reset = on_reset
        self._on_save = on_save

    def show_saved(self) -> None:
        """Flash a confirmation that the settings reached disk."""
        self._saved_label.setText("Saved")
        self._saved_timer.start(1600)

    def sync_from(self, azimuth, elevation, highlight_size, quality,
                    pointer, hex_labels=True, sticky=None, compact=None) -> None:
        """Mirror engine state into the widgets without emitting.

        Every one of these widgets reports the panel's *entire* state on
        change, so updating them one at a time would push a half-applied mix of
        new and still-default values back at the owner. Blocked, so restoring
        state is a single silent step.
        """
        widgets = (self.azimuth, self.elevation, self.highlight_size,
                   self.sticky, self.pointer_cb, self.hex_cb, self.compact_cb)
        for w in widgets:
            w.blockSignals(True)
        try:
            self.azimuth.setValue(int(azimuth))
            self.elevation.setValue(int(elevation))
            self.highlight_size.setValue(int(highlight_size))
            if sticky is not None:
                self.sticky.setValue(int(round(sticky)))
            self.pointer_cb.setChecked(bool(pointer))
            self.hex_cb.setChecked(bool(hex_labels))
            if compact is not None:
                self.compact_cb.setChecked(bool(compact))
            for name, res in self.QUALITY:
                self._quality_buttons[name].setChecked(res == int(quality))
                self._quality_buttons[name].setStyleSheet(
                    self._btn_style(res == int(quality)))
            self._quality = int(quality)
            self._refresh_readouts()
        finally:
            for w in widgets:
                w.blockSignals(False)


class CollapsibleSection(QWidget):
    """A titled group that can be expanded/collapsed by clicking its header.

    Layout ownership note (Qt/PyQt pitfall, see Bugs.md): this widget installs
    its ``QVBoxLayout`` exactly once in ``__init__`` and callers append children
    via :meth:`add_row`. Re-wrapping with ``QVBoxLayout(self)`` would *detach*
    the new layout rather than replace it, leaving children with ``parent=None``
    so they never render.
    """

    def __init__(self, title: str, expanded: bool = False, accent: QColor = None, parent=None):
        super().__init__(parent)
        self._title = title
        self._expanded = bool(expanded)
        self._accent = QColor(accent) if accent is not None else QColor(160, 174, 193)

        # The single outer layout. Created once; the header is a nested
        # sub-layout, never a bare QHBoxLayout(self).
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 2, 0, 0)
        self._outer.setSpacing(2)

        self._header = _SectionHeader(title, self._accent, self._expanded)
        self._outer.addWidget(self._header)

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self._body = QVBoxLayout()
        self._body.setContentsMargins(0, 0, 0, 0)
        self._body.setSpacing(0)
        self._outer.addLayout(self._body)
        self._rows: List[QWidget] = []
        self.set_expanded(self._expanded)

    def add_row(self, widget: QWidget) -> None:
        """Append a control row to the section body.

        Rows are hidden individually rather than by hiding the layout, because
        ``QLayout`` has no ``setVisible`` and a sub-layout cannot collapse on its
        own. The current expanded state is applied to each new row so a section
        created collapsed really does start collapsed.
        """
        self._body.addWidget(widget)
        self._rows.append(widget)
        widget.setVisible(self._expanded)
        if self._expanded:
            self.set_expanded(True)

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        for row in self._rows:
            row.setVisible(self._expanded)
        self._header.set_expanded(self._expanded)
        # The section hugs its content: the header, plus the rows only when
        # open. The header is a fixed 20px tall, but its sizeHint() is not
        # reliable before the first layout pass, so use the known height.
        rows = self._rows if self._expanded else []
        extra = sum(max(r.sizeHint().height(), 1) for r in rows)
        self.setFixedHeight(HEADER_HEIGHT + extra + 6)

    def is_expanded(self) -> bool:
        return self._expanded


HEADER_HEIGHT = 30      # height of a CollapsibleSection header bar


class _SectionHeader(QWidget):
    """Clickable title bar with a rotating chevron."""

    def __init__(self, title: str, accent: QColor, expanded: bool, parent=None):
        super().__init__(parent)
        self._title = title
        self._accent = QColor(accent)
        self._expanded = bool(expanded)
        self._last_toggle_ms = 0.0
        self.setFixedHeight(HEADER_HEIGHT)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_Hover, True)

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        self.update()

    def mousePressEvent(self, event):
        try:
            if event.button() == Qt.LeftButton:
                # Debounce: a double-click otherwise toggles open-then-shut,
                # which reads as "sometimes doesn't open".
                now_ms = time.monotonic() * 1000.0
                if now_ms - self._last_toggle_ms < 400.0:
                    event.accept()
                    return
                self._last_toggle_ms = now_ms
                self._expanded = not self._expanded
                self.update()
                window = self.window()
                if window is not None:
                    # Let the owning CollapsibleSection mirror the state.
                    parent = self.parent()
                    if isinstance(parent, CollapsibleSection):
                        parent.set_expanded(self._expanded)
        except Exception:  # pragma: no cover - cosmetic only
            pass

    def paintEvent(self, event):
        try:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing, True)
            w, h = self.width(), self.height()
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(34, 37, 45))
            p.drawRoundedRect(QRectF(0, 0, w, h), 5, 5)

            cy = h / 2.0
            # Chevron: points right when collapsed, down when expanded.
            p.setPen(QPen(self._accent, 1.4))
            p.setBrush(Qt.NoBrush)
            if self._expanded:
                p.drawPolyline(QPointF(8, cy - 2), QPointF(11, cy + 2), QPointF(14, cy - 2))
            else:
                p.drawPolyline(QPointF(9, cy - 3), QPointF(13, cy), QPointF(9, cy + 3))

            p.setPen(QPen(QColor(168, 178, 196), 1.0))
            p.drawText(QRectF(20, 0, w - 26, h), int(Qt.AlignVCenter | Qt.AlignLeft), self._title)
            p.end()
        except Exception:  # pragma: no cover - cosmetic only
            pass
