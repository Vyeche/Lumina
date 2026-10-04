"""color_controls.py — Color-coded control widgets for the Lumina docker.

* :class:`Accent` — named accent palette shared by the color-coded controls.
* :class:`ColorSlider` — a thin slider whose track is either a solid accent
  fill or an arbitrary multi-stop gradient (hue / saturation / value ramps),
  with a white round handle.

All widgets are pure PyQt5 (no shading math), so they can be assembled by the
sphere widget without pulling in ``color_engine``.
"""

from typing import List, Optional, Sequence, Tuple

from PyQt5.QtCore import (Qt, QPoint, QPointF, QRectF, QSize, QTimer,
                         pyqtSignal)
from PyQt5.QtGui import (QBrush, QColor, QLinearGradient, QPainter, QPen,
                         QPolygonF)
from PyQt5.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy,
                             QSlider, QVBoxLayout, QWidget)

from .tooltip import set_tooltip


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
        self.setMinimumHeight(20)
        self.setFixedHeight(20)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.valueChanged.connect(self.update)

    def _travel_span(self) -> float:
        """Width the handle actually travels, matching :meth:`_handle_x`."""
        r = self._handle_radius()
        return max(1.0, float(self.width()) - 2.0 * r)

    def mousePressEvent(self, event):
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
        self._dragging = False
        super().mousePressEvent(event)

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

    def set_gradient(self, stops: Sequence[Tuple[float, QColor]]) -> None:
        """Paint the track as a gradient. ``stops`` = [(pos 0..1, QColor), ...]."""
        self._gradient_stops = list(stops) if stops else None
        self.update()

    def _handle_radius(self):
        return min(max(5.0, self.height() / 2.0), self.height() / 2.0 + 2.0)

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
        track_w = max(5.0, h / 2.0)
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
class ColorSampler(QWidget):
    """A colour sampling reticle drawn on top of the orb.

    This is a live-drawn object, not a static icon: a circular bezel centred on
    the pointer whose inner fill always shows the exact colour of the pixel
    underneath it, with a bevel and a dark outer ring so it stays readable over
    any part of the sphere.

    It is a *sampler*, not a magnifier: it enlarges nothing. It marks the exact
    pixel being read and previews that colour, which is all a reticle needs to
    do. It was previously called a "loupe" (a small magnifying glass, the
    eyepiece of an optical instrument), which is both jargon and wrong here --
    the control shows no enlarged view of the surrounding pixels, so the name
    promised a feature it does not have.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._color = QColor(200, 60, 60)
        self._active = False          # True while the pointer is held down
        self.setFixedSize(56, 56)
        # Never intercept input: the orb underneath must keep getting the events.
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def set_color(self, color: Optional[QColor]) -> None:
        if color is not None and color.isValid():
            self._color = QColor(color)
        self.update()

    def set_active(self, active: bool) -> None:
        self._active = bool(active)
        self.update()

    def paintEvent(self, event):
        try:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing, True)
            cx, cy = self.width() / 2.0, self.height() / 2.0
            outer = 17.0 if self._active else 15.0
            inner = outer - 4.0

            # Dark outer ring for contrast against a bright part of the sphere.
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(18, 20, 26, 215))
            p.drawEllipse(QPointF(cx, cy), outer + 1.6, outer + 1.6)

            # Inner fill: the sampled colour itself.
            p.setBrush(QBrush(self._color))
            p.setPen(QPen(QColor(255, 255, 255, 240), 2.0))
            p.drawEllipse(QPointF(cx, cy), inner, inner)

            # Bevel: a soft highlight on the upper-left, shadow lower-right.
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255, 90), 1.4))
            p.drawArc(QRectF(cx - inner + 1.2, cy - inner + 1.2,
                             (inner - 1.2) * 2, (inner - 1.2) * 2),
                      200 * 16, 140 * 16)
            p.setPen(QPen(QColor(0, 0, 0, 110), 1.4))
            p.drawArc(QRectF(cx - inner + 1.2, cy - inner + 1.2,
                             (inner - 1.2) * 2, (inner - 1.2) * 2),
                      20 * 16, 140 * 16)

            # Crosshair ticks mark the exact sampled coordinate.
            p.setPen(QPen(QColor(18, 20, 26, 190), 1.4, Qt.SolidLine, Qt.RoundCap))
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                p.drawLine(QPointF(cx + dx * (outer + 2.5), cy + dy * (outer + 2.5)),
                           QPointF(cx + dx * (outer + 5.5), cy + dy * (outer + 5.5)))
            p.end()
        except Exception as exc:  # pragma: no cover - cosmetic only
            print("ColorSampler: paintEvent failed - %s" % exc)


class SettingsPanel(QWidget):
    """Compact settings popup shown by the gear button.

    Deliberately holds the things that have no home on the main panel:

    * **Main light direction** - azimuth and elevation. The engine has always
      supported these but no control ever exposed them, so the light could not
      be moved without editing code.
    * **Render quality** - the orb is shaded in pure Python, so the grid
      resolution trades smoothness against fidelity while dragging.
    * **Picker pointer** - show/hide the sampling ring on the orb.
    * **Reset** - restore every target and lighting parameter to its default.
    """

    QUALITY = (("Low", 128), ("Med", 200), ("High", 288))

    def __init__(self, azimuth: int = 287, elevation: int = 45,
                 quality: int = 200, highlight_size: int = 80, parent=None):
        super().__init__(parent, Qt.Popup)
        self.setStyleSheet(
            "QWidget { background-color: #343941; color: #e2e6ef; }"
            "QLabel { color: #9aa3b4; font-size: 10px; }")
        self._quality_buttons = {}
        self._quality = quality
        self._on_change = None
        self._on_reset = None
        self._on_save = None

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        title = QLabel("Settings")
        title.setStyleSheet("color: #e7eaf2; font-size: 11px; font-weight: bold;")
        root.addWidget(title)

        self.azimuth = self._add_slider(root, "Light azimuth", 0, 359, azimuth)
        self.elevation = self._add_slider(root, "Light height", 0, 90, elevation)
        # Highlight size, 0-100, driving the same specular sharpness as the
        # Advanced "Specular" row but in the direction a painter thinks: up
        # means a bigger, softer highlight. Kept in sync with that row by the
        # docker, since two sliders own one engine value.
        self.highlight_size = self._add_slider(root, "Highlight size",
                                                0, 100, highlight_size)
        set_tooltip(self.highlight_size, "Highlight size on the orb")

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
        self.pointer_cb.stateChanged.connect(self._emit)

    @staticmethod
    def _btn_style(on: bool) -> str:
        if on:
            return ("QPushButton { background-color: #33404f; color: #eef1f6; "
                    "border: 1px solid rgba(255,255,255,110); border-radius: 4px; "
                    "font-size: 9px; padding: 3px 0; }")
        return ("QPushButton { background-color: #21252e; color: #8e97a8; "
                "border: 1px solid rgba(255,255,255,35); border-radius: 4px; "
                "font-size: 9px; padding: 3px 0; }")

    def _add_slider(self, root, label, lo, hi, value):
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
        rh.addWidget(sl, 1)
        # Percent readout: position as % of range. Type a value to set it:
        # a trailing % means percent-of-range, else the raw slider number.
        # Click to type: a trailing % means percent-of-range, else the raw
        # slider value.
        val = QLineEdit()
        val.setFixedWidth(40)
        val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        val.setFrame(False)
        val.setStyleSheet(
            "QLineEdit { color: #e2e6ef; font-size: 10px; "
            "background: #262b35; border-radius: 3px; padding-right: 2px; }"
            "QLineEdit:hover { background: #2e3440; }"
            "QLineEdit:focus { background: #333a48; color: #ffffff; }"
        )
        rh.addWidget(val)
        sl.valueChanged.connect(
            lambda v, s=sl, w=val: w.setText(self._track_pct(s)))
        val.setText(self._track_pct(sl))
        val.editingFinished.connect(
            lambda s=sl, w=val: self._commit_typed(s, w))
        if not hasattr(self, "_readouts"):
            self._readouts = {}
        self._readouts[sl] = val
        root.addWidget(row)
        return sl

    @staticmethod
    def _commit_typed(sl, w) -> None:
        """Commit a typed readout: '86%' is percent-of-range, else raw."""
        try:
            text = w.text().strip()
            lo, hi = sl.minimum(), sl.maximum()
            if text.endswith("%"):
                frac = max(0.0, min(100.0, float(text[:-1]))) / 100.0
                value = int(round(lo + frac * (hi - lo)))
            else:
                value = int(round(float(text)))
            sl.setValue(max(lo, min(hi, value)))
        except (TypeError, ValueError):
            pass
        finally:
            w.setText(SettingsPanel._track_pct(sl))

    @staticmethod
    def _track_pct(sl) -> str:
        """Handle position as a percentage of the slider's range."""
        lo, hi = sl.minimum(), sl.maximum()
        if hi <= lo:
            return "0%"
        return "%d%%" % round((sl.value() - lo) / (hi - lo) * 100)

    def _refresh_readouts(self) -> None:
        """Repaint every readout from its slider's current value.

        Needed after programmatic changes (sync_from blocks signals, so the
        live valueChanged handler never fires and the numbers go stale).
        """
        for sl, val in getattr(self, "_readouts", {}).items():
            val.setText(self._track_pct(sl))

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
                "quality": self._quality,
                "pointer": self.pointer_cb.isChecked(),
            })

    def bind(self, on_change, on_reset, on_save=None):
        self._on_change = on_change
        self._on_reset = on_reset
        self._on_save = on_save

    def show_saved(self) -> None:
        """Flash a confirmation that the settings reached disk."""
        self._saved_label.setText("Saved")
        self._saved_timer.start(1600)

    def sync_from(self, azimuth, elevation, highlight_size, quality,
                    pointer) -> None:
        """Mirror engine state into the widgets without emitting.

        Every one of these widgets reports the panel's *entire* state on
        change, so updating them one at a time would push a half-applied mix of
        new and still-default values back at the owner. Blocked, so restoring
        state is a single silent step.
        """
        widgets = (self.azimuth, self.elevation, self.highlight_size,
                   self.pointer_cb)
        for w in widgets:
            w.blockSignals(True)
        try:
            self.azimuth.setValue(int(azimuth))
            self.elevation.setValue(int(elevation))
            self.highlight_size.setValue(int(highlight_size))
            self.pointer_cb.setChecked(bool(pointer))
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


HEADER_HEIGHT = 22      # height of a CollapsibleSection header bar


class _SectionHeader(QWidget):
    """Clickable title bar with a rotating chevron."""

    def __init__(self, title: str, accent: QColor, expanded: bool, parent=None):
        super().__init__(parent)
        self._title = title
        self._accent = QColor(accent)
        self._expanded = bool(expanded)
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
