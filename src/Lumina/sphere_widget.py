"""sphere_widget.py — The interactive Lumina surface widget.

This custom ``QWidget`` renders the shaded sphere (from :class:`SphereColorProcessor`)
and lets the user *pick* colors by clicking/dragging on it.  Hovered and picked
colors are reported through callbacks; the docker renders the readout itself.

Responsibilities:
    * Paint the rendered QImage centered in the widget.
    * Map mouse events to sphere pixels and fire ``picked`` callbacks.
    * Track the hovered color and fire hover/pick callbacks.
"""

import math
from typing import Callable, Optional

from PyQt5.QtCore import Qt, QPoint, QPointF, QRectF
from PyQt5.QtGui import QColor, QPainter, QFont, QPixmap, QImage, QPen
from PyQt5.QtWidgets import QWidget

# How far past the silhouette a pick still works, in widget pixels. Inside this
# band the sample point is projected onto the circle, letting the cursor glide
# around the rim; past it, picking stops. Kept small so the transparent corners
# of the square around the orb stay genuinely unpickable.
EDGE_GLIDE = 6.0

# Inset between the widget edge and the drawn orb, in pixels. Must exceed
# EDGE_GLIDE, otherwise the circle touches the widget bounds and the glide band
# is unreachable at the four points where they meet.
ORB_MARGIN = 8.0

# ---------------------------------------------------------------------------
# Logging — write the full log to a file in the plugin directory so crashes are
# captured even when Krita hides the terminal / no traceback is visible.
# ---------------------------------------------------------------------------
import logging
import os

try:
    _LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lumina_log.txt")
    logging.basicConfig(
        filename=_LOG_PATH,
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    LOG = logging.getLogger("Lumina")
except Exception as exc:  # pragma: no cover - logging must never break the plugin
    print(f"Lumina: logging setup failed - {exc}")


class SphereWidget(QWidget):
    """Interactive sphere surface with color picking."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._image = None            # QImage rendered from the engine
        self._hover_color = None      # QColor currently under cursor
        self._brush_cb = None        # Callable[QColor, None] while dragging
        self._hover_cb = None          # Callable[QColor, None] on cursor move
        self._pointer = None          # QPointF of the picker marker (widget coords)
        self._pointer_down = False    # True while the pointer is being dragged
        self._show_pointer = True     # settings toggle for the marker
        self._last_pos = None         # last cursor position, for release-commit
        self._preview = None          # floating colour preview that tracks cursor
        self.setFixedSize(200, 200)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setStyleSheet("QWidget { background-color: rgba(20, 20, 25, 255); }")
    # ------------------------------------------------------------------
    # Public API used by SphereDocker
    # ------------------------------------------------------------------
    def set_image(self, image):
        """Set the QImage to display and repaint."""
        self._image = image
        self.update()

    def set_brush_callback(self, callback: Optional[Callable[[QColor], None]]):
        """Register a callback fired continuously while dragging over the sphere.

        This drives Krita's foreground (brush) color live. It deliberately does
        not touch the sphere's target colors -- see :meth:`_sample`.
        """
        self._brush_cb = callback

    def set_hover_callback(self, callback: Optional[Callable[[QColor], None]]):
        """Register a callback fired when the cursor moves over the sphere."""
        self._hover_cb = callback

    def set_show_pointer(self, show: bool) -> None:
        """Toggle the on-orb sampling markers.

        This governs both markers, because they are one feature: the small
        pointer ring painted by :meth:`paintEvent` and the larger sampling ring
        that tracks the cursor. Hiding only the small one left the prominent
        ring on screen, so the settings toggle appeared to do nothing.
        """
        self._show_pointer = bool(show)
        if self._preview is not None:
            if not show:
                # Otherwise the ring lingers on screen until the pointer next
                # leaves the orb, which reads as the toggle having failed.
                self._preview.hide()
            elif self._pointer is not None:
                # Re-show straight away if the pointer is already over the orb.
                # Showing is otherwise driven by pointer motion, so switching it
                # back on appeared to do nothing until the mouse moved.
                self._reposition_preview()
        self.update()

    def set_preview_widget(self, widget) -> None:
        """Attach a floating preview (the colour sampler ring) tracking the cursor.

        The preview follows the pointer across the orb and is hidden again when
        the cursor leaves the sphere, instead of sitting in a fixed corner.
        """
        self._preview = widget
        if widget is not None:
            widget.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            widget.hide()

    def _reposition_preview(self) -> None:
        """Centre the sampler on the pointer, in its parent's coordinates.

        The sampler is parented to the panel rather than the orb so it is not
        clipped by the orb's bounds. Clamping it to stay inside the orb made it
        visibly lag behind the pointer around the edges, so the reticle could
        never actually reach the rim of the sphere.

        Honours ``_show_pointer``. This runs on every pointer move, so it would
        otherwise re-show a ring the user had just switched off -- which is why
        the settings toggle looked inert.
        """
        preview = getattr(self, "_preview", None)
        if preview is None or self._pointer is None:
            return
        if not self._show_pointer:
            preview.hide()
            return
        parent = preview.parentWidget() or self
        top_left = self.mapTo(parent, QPoint(int(self._pointer.x()) - preview.width() // 2,
                                             int(self._pointer.y()) - preview.height() // 2))
        preview.move(top_left)
        preview.raise_()
        preview.show()


    def hovered_color(self) -> Optional[QColor]:
        return self._hover_color

    # ------------------------------------------------------------------
    def paintEvent(self, event):
        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing, True)
            # Also smooth the *image* scaling. The orb is drawn at whatever size
            # the render resolution allows, which is not always the widget size:
            # the drag path renders at 128px and upscales into a 200px widget, and
            # a HiDPI or resized docker scales it too. Without this hint the
            # upscale is nearest-neighbour and the rim turns blocky during drags.
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

            if self._image is None:
                painter.fillRect(self.rect(), QColor(20, 20, 25))
                painter.end()
                return

            w, h = self.width(), self.height()
            res = self._image.width() or self._image.height()
            if not res:
                painter.end()
                return
            off_x, off_y, dia = self._orb_geometry()

            # Scale the *whole* image into a centered square: a complete circle is
            # always shown regardless of widget/image size mismatch or HiDPI,
            # instead of clipping an unscaled sub-rectangle (Issue: flat bottom +
            # hard right edge when the widget was smaller than the image).
            painter.drawImage(QRectF(off_x, off_y, dia, dia), self._image)

            # No rim stroke: the coverage-grid alpha already antialiases the
            # silhouette, and a painted ring read as a light outline drawn
            # round dark spheres.

            # Picker pointer: a small ring marking the last sampled/picked point
            # on the orb surface (the reference shows this draggable marker).
            if self._pointer is not None and self._show_pointer:
                px, py = self._pointer.x(), self._pointer.y()
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(QColor(20, 22, 28, 210), 3.0))
                painter.drawEllipse(QPointF(px, py), 6.0, 6.0)
                if self._pointer_down:
                    pen = QPen(QColor(255, 255, 255, 245), 2.0)
                else:
                    pen = QPen(QColor(255, 255, 255, 190), 1.6)
                painter.setPen(pen)
                painter.drawEllipse(QPointF(px, py), 6.0, 6.0)

            painter.end()
        except Exception as exc:
            LOG.exception("paintEvent failed")


    # ------------------------------------------------------------------
    # Mouse handling
    # ------------------------------------------------------------------
    def mousePressEvent(self, event):
        try:
            LOG.info("mousePressEvent")
            self._pointer_down = True
            self._last_pos = event.pos()
            # A plain click (press and release with no movement) must still choose
            # the colour under the pointer for drawing, so send to the brush on
            # press as well as during the drag.
            self._sample(event.pos(), "brush")
        except Exception as exc:
            LOG.exception("mousePressEvent failed")

    def mouseMoveEvent(self, event):
        try:
            self._last_pos = event.pos()
            dragging = bool(event.buttons() & Qt.LeftButton)
            self._pointer_down = dragging
            # While dragging, stream the color under the cursor to the brush.
            # The sphere's target colors are NOT touched here: doing so would
            # re-render the orb mid-drag, so every move would resample a
            # different color and the target would run away.
            self._sample(event.pos(), "brush" if dragging else "none")
        except Exception as exc:
            LOG.exception("mouseMoveEvent failed")

    def mouseReleaseEvent(self, event):
        try:
            self._pointer_down = False
            # No-op: the sphere's own colours are never changed from the orb.
            # The brush was already set on press and on every move.
            self.update()
        except Exception as exc:
            LOG.exception("mouseReleaseEvent failed")

    def leaveEvent(self, event):
        try:
            self._pointer_down = False
            self._hover_color = None
            self._pointer = None
            if self._preview is not None:
                self._preview.hide()
            if self._hover_cb is not None:
                try:
                    self._hover_cb(None)
                except Exception:  # pragma: no cover - defensive
                    pass
            self.update()
        except Exception as exc:  # pragma: no cover - cosmetic only
            LOG.exception("leaveEvent failed")

    def _sample(self, point: QPointF, mode: str = "none"):
        """Sample the orb under ``point``.

        The orb is a *brush* picker only: it never assigns a color to the
        sphere's own targets. Those change from the sliders, the target dots or
        the document eyedropper -- not from clicking the orb.

        ``mode`` selects how far the sample propagates:

        * ``"none"``  - update the pointer and the hover readout only.
        * ``"brush"``  - additionally stream the colour to Krita's foreground,
          live while dragging.
        """
        sx, sy = self._to_sphere_coords(point)
        if sx is None or sy is None:
            if self._hover_color is not None or self._pointer is not None:
                self._hover_color = None
                self._pointer = None
                if self._preview is not None:
                    self._preview.hide()
                self.update()
                if self._hover_cb is not None:
                    try:
                        self._hover_cb(None)
                    except Exception:  # pragma: no cover - defensive
                        pass
            return
        try:
            pixel = self._image.pixelColor(sx, sy)
        except Exception:
            return
        self._hover_color = pixel
        # Remember where the pointer is, in widget coordinates.
        # Use the same inset orb rectangle as paintEvent, not the full widget
        # side: the image is drawn inside side - 2*ORB_MARGIN.
        off_x, off_y, dia = self._orb_geometry()
        res = ((self._image.width() or self._image.height())
               if self._image is not None else 0)
        if res > 0:
            pointer = QPointF(
                off_x + (sx + 0.5) * dia / float(res),
                off_y + (sy + 0.5) * dia / float(res),
            )
            self._pointer = pointer
        self._reposition_preview()
        if self._hover_cb is not None:
            try:
                self._hover_cb(pixel)
            except Exception as exc:  # pragma: no cover - defensive
                print(f"Lumina: hover callback failed - {exc}")
        self.update()
        if mode == "brush" and self._brush_cb is not None:
            try:
                self._brush_cb(pixel)
            except Exception as exc:  # pragma: no cover - defensive
                print(f"Lumina: brush callback failed - {exc}")

    def _orb_geometry(self):
        """Top-left and diameter of the drawn orb, in widget coordinates.

        Single source of truth shared by :meth:`paintEvent` and
        :meth:`_to_sphere_coords`. They previously each recomputed the centring
        separately, which is exactly the kind of duplication that lets the thing
        you see drift away from the thing you click.

        The orb is inset by :data:`ORB_MARGIN` rather than filling the widget.
        A circle drawn edge-to-edge has no room outside itself, so the glide
        band in :meth:`_to_sphere_coords` could never be reached: the widget
        bounds rejected the point first, at the four points where the circle
        touches the square. The margin gives that band somewhere to live, so
        picking behaves the same all the way round.
        """
        w, h = self.width(), self.height()
        side = min(w, h)
        dia = max(1.0, side - 2.0 * ORB_MARGIN)
        return (w - dia) / 2.0, (h - dia) / 2.0, dia

    def _to_sphere_coords(self, point: QPointF):
        """Map a widget point to a pixel of the rendered sphere.

        Returns ``(sx, sy)`` or ``(None, None)`` when the point is outside the
        pickable area.
        """
        if self._image is None or self.width() <= 0 or self.height() <= 0:
            return None, None
        res = self._image.width() or self._image.height()
        w, h = self.width(), self.height()
        off_x, off_y, dia = self._orb_geometry()
        # Test against the widget, not the orb rect: the glide band deliberately
        # reaches outside the circle, so it must not be clipped to it.
        if not (0 <= point.x() < w and 0 <= point.y() < h):
            return None, None
        u = (point.x() - off_x) / dia * 2.0 - 1.0
        v = (point.y() - off_y) / dia * 2.0 - 1.0
        r2 = u * u + v * v
        if r2 > 1.0:
            # Past the silhouette. A hard cutoff here made the pick die the
            # instant the cursor crossed the rim, and because a pixel of
            # movement near the edge spans a large arc, colours right at the
            # terminator were practically unreachable. Instead allow a narrow
            # band just outside the circle and project the point radially back
            # onto it, so dragging past the edge glides the sample around the rim
            # and picks up again on the way out. Beyond the band it is still a
            # dead zone, so the empty corners of the square stay unpickable.
            r = math.sqrt(r2)
            if r - 1.0 > EDGE_GLIDE / (dia * 0.5):
                return None, None
            k = 1.0 / r
            u *= k
            v *= k
        # Derived from the (possibly projected) u/v rather than the raw point,
        # so the projected case samples the rim instead of a masked-out pixel.
        sx = max(0, min(res - 1, int(round((u + 1.0) * 0.5 * (res - 1)))))
        sy = max(0, min(res - 1, int(round((v + 1.0) * 0.5 * (res - 1)))))
        return max(0, min(res - 1, sx)), max(0, min(res - 1, sy))

    def resizeEvent(self, event):
        try:
            LOG.info(f"resizeEvent size={self.width()}x{self.height()}")
            # Keep the sphere centered when the docker resizes.
            self.update()
        except Exception as exc:
            LOG.exception("resizeEvent failed")

    def closeEvent(self, event):
        try:
            LOG.info("closeEvent")
        except Exception as exc:
            LOG.exception("closeEvent failed")
