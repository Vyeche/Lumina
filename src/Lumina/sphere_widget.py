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

from PyQt5.QtCore import Qt, QEvent, QObject, QPoint, QPointF, QRectF, QTimer
from PyQt5.QtGui import QColor, QCursor, QPainter, QFont, QPixmap, QImage, QPen
from PyQt5.QtWidgets import QApplication, QWidget

# How far past the silhouette a pick still works, in widget pixels. Inside this
# band the sample point is projected onto the circle, letting the cursor glide
# around the rim; past it, picking stops. 40 (was 32): the pick held on so
# briefly that a drag along the edge kept letting go. This is the default;
# Settings > Sticky distance sets it per user (SphereWidget.edge_glide). The band reaches
# past the widget's own bounds -- a drag keeps the mouse, and a hover is
# followed by _OutsideTracker -- so it is the same width all the way round.
EDGE_GLIDE = 40.0


class _OutsideTracker(QObject):
    """Follows the pointer after it leaves the sphere widget while the sampler
    is still in the edge band. Hover moves are only delivered to the widget
    under the pointer, and the widget ends 20 px past the sphere at the top,
    bottom and sides, so without this the sampler vanished there however wide
    EDGE_GLIDE was. Watches application mouse moves only while it is needed
    (never consumes them) and hands back to the widget on re-entry."""

    def __init__(self, sphere):
        super().__init__(sphere)
        self._sphere = sphere
        self.active = False

    def start(self) -> None:
        if not self.active:
            self.active = True
            QApplication.instance().installEventFilter(self)
            QApplication.setOverrideCursor(Qt.BlankCursor)

    def stop(self) -> None:
        if self.active:
            self.active = False
            QApplication.instance().removeEventFilter(self)
            QApplication.restoreOverrideCursor()

    def eventFilter(self, obj, event):
        try:
            if event.type() == QEvent.MouseMove and self.active:
                self._sphere._follow_outside(self._sphere._cursor_local())
        except Exception:  # pragma: no cover - never break input
            LOG.exception("_OutsideTracker failed")
            self.stop()
        return False

# Inset between the widget edge and the drawn sphere, in pixels. Hovering
# (no button) only reaches this far past the silhouette at the four points
# where the circle is nearest the widget bounds; a drag reaches EDGE_GLIDE.
SPHERE_MARGIN = 20.0

# ---------------------------------------------------------------------------
# Logging — shared setup in lumina_logging (file handler never holds the
# log open, so Windows reinstalls can delete the plugin directory).
# ---------------------------------------------------------------------------
from .lumina_logging import LOG


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
        self._mouse_hidden = False    # mouse pointer hidden while the sampler shows
        self._outside = _OutsideTracker(self)   # hover past the widget's bounds
        self.edge_glide = EDGE_GLIDE  # how far past the rim a pick holds, px
        self._target_cb = None        # Shift-click: pick into the selected target
        self._shift_pick = False      # this press was a Shift-click
        self._key_pos = None          # arrow-key nudge position, widget coords
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

    def set_target_callback(self, callback: Optional[Callable[[QColor], None]]):
        """Register a callback for Shift-click: the one way a sphere click
        sets the selected target instead of the brush."""
        self._target_cb = callback

    def set_hover_callback(self, callback: Optional[Callable[[QColor], None]]):
        """Register a callback fired when the cursor moves over the sphere."""
        self._hover_cb = callback

    def set_edge_glide(self, pixels: float) -> None:
        """How far past the sphere's edge a pick holds on (Settings >
        Sticky distance), in widget pixels. 0 lets go at the silhouette."""
        self.edge_glide = max(0.0, float(pixels))

    def set_show_pointer(self, show: bool) -> None:
        """Toggle the on-sphere sampling markers.

        This governs both markers, because they are one feature: the small
        pointer ring painted by :meth:`paintEvent` and the larger sampling ring
        that tracks the cursor. Hiding only the small one left the prominent
        ring on screen, so the settings toggle appeared to do nothing.
        """
        self._show_pointer = bool(show)
        if self._preview is not None:
            if not show:
                # Otherwise the ring lingers on screen until the pointer next
                # leaves the sphere, which reads as the toggle having failed.
                self._preview.hide()
                self._hide_mouse(False)
            elif self._pointer is not None:
                # Re-show straight away if the pointer is already over the sphere.
                # Showing is otherwise driven by pointer motion, so switching it
                # back on appeared to do nothing until the mouse moved.
                self._reposition_preview()
        self.update()

    def set_preview_widget(self, widget) -> None:
        """Attach a floating preview (the colour sampler ring) tracking the cursor.

        The preview follows the pointer across the sphere and is hidden again when
        the cursor leaves the sphere, instead of sitting in a fixed corner.
        """
        self._preview = widget
        if widget is not None:
            widget.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            widget.hide()

    def _hide_mouse(self, hide: bool) -> None:
        """Hide the mouse pointer while the sampler is on the sphere -- the
        sampler marks the spot, and the arrow on top of it only hid the colour
        -- and bring it back whenever the sampler is not showing (off the
        sphere, or switched off in settings, when nothing else would mark it)."""
        hide = bool(hide)
        if hide == self._mouse_hidden:
            return
        self._mouse_hidden = hide
        if hide:
            self.setCursor(Qt.BlankCursor)
        else:
            self.unsetCursor()

    def _reposition_preview(self) -> None:
        """Centre the sampler on the pointer, in its parent's coordinates.

        The sampler is parented to the panel rather than the sphere so it is not
        clipped by the sphere's bounds. Clamping it to stay inside the sphere made it
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
            self._hide_mouse(False)
            return
        # Tell the sampler where on the surface it sits, so it lies on the curve.
        if hasattr(preview, "set_surface"):
            off_x, off_y, dia = self._sphere_geometry()
            preview.set_surface((self._pointer.x() - off_x) / dia * 2.0 - 1.0,
                                (self._pointer.y() - off_y) / dia * 2.0 - 1.0)
        if hasattr(preview, "set_active"):
            preview.set_active(self._pointer_down)
        parent = preview.parentWidget() or self
        top_left = self.mapTo(parent, QPoint(int(self._pointer.x()) - preview.width() // 2,
                                             int(self._pointer.y()) - preview.height() // 2))
        preview.move(top_left)
        preview.raise_()
        preview.show()
        self._hide_mouse(True)


    def hovered_color(self) -> Optional[QColor]:
        return self._hover_color

    # ------------------------------------------------------------------
    def paintEvent(self, event):
        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing, True)
            # Also smooth the *image* scaling. The sphere is drawn at whatever size
            # the render resolution allows, which is not always the widget size:
            # the drag path renders at 96px (DRAG_RENDER) and upscales into a
            # 200px widget, and
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
            off_x, off_y, dia = self._sphere_geometry()

            # Scale the *whole* image into a centered square: a complete circle is
            # always shown regardless of widget/image size mismatch or HiDPI,
            # instead of clipping an unscaled sub-rectangle (Issue: flat bottom +
            # hard right edge when the widget was smaller than the image).
            painter.drawImage(QRectF(off_x, off_y, dia, dia), self._image)

            # No rim stroke: the coverage-grid alpha already antialiases the
            # silhouette, and a painted ring read as a light outline drawn
            # round dark spheres.

            # Picker pointer: a small ring marking the last sampled/picked point
            # on the sphere surface (the reference shows this draggable marker).
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
            self._key_pos = None
            # Shift-click picks into the selected target, once. A plain click
            # (press and release with no movement) chooses the colour under
            # the pointer for drawing, so it goes to the brush on press as
            # well as during the drag.
            self._shift_pick = bool(event.modifiers() & Qt.ShiftModifier) and self._target_cb is not None
            self._sample(event.pos(), "target" if self._shift_pick else "brush")
        except Exception as exc:
            LOG.exception("mousePressEvent failed")

    def mouseMoveEvent(self, event):
        try:
            self._last_pos = event.pos()
            self._key_pos = None
            dragging = bool(event.buttons() & Qt.LeftButton)
            self._pointer_down = dragging
            if dragging and self._shift_pick:
                # A Shift-drag only moves the sampler: re-targeting on every
                # move would re-render the sphere under the pointer and the
                # target would run away.
                self._sample(event.pos(), "none")
                return
            # While dragging, stream the color under the cursor to the brush.
            # The sphere's target colors are NOT touched here: doing so would
            # re-render the sphere mid-drag, so every move would resample a
            # different color and the target would run away.
            self._sample(event.pos(), "brush" if dragging else "none")
        except Exception as exc:
            LOG.exception("mouseMoveEvent failed")

    def mouseReleaseEvent(self, event):
        try:
            self._pointer_down = False
            self._shift_pick = False
            # Let the sampler settle (and stop sweating) on release.
            if self._preview is not None and hasattr(self._preview, "set_active"):
                self._preview.set_active(False)
            # No-op: the sphere's own colours are never changed from the sphere.
            # The brush was already set on press and on every move.
            self.update()
        except Exception as exc:
            LOG.exception("mouseReleaseEvent failed")

    def _cursor_local(self) -> QPoint:
        """Where the pointer is now, in this widget's coordinates."""
        return self.mapFromGlobal(QCursor.pos())

    def _follow_outside(self, local: QPoint) -> None:
        """A pointer move outside the widget while the tracker runs."""
        if self.rect().contains(local):
            self._outside.stop()                    # back over the widget
            return
        self._sample(QPointF(local), "none")
        if self._pointer is None:                   # past the band: let go
            self._outside.stop()

    NUDGE_KEYS = {Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0),
                  Qt.Key_Up: (0, -1), Qt.Key_Down: (0, 1)}

    def keyPressEvent(self, event):
        """Arrow keys nudge the sampler 1 px (Shift: 5 px) for a precise
        pick; Enter or Space picks there, as a click would."""
        try:
            key = event.key()
            if key in self.NUDGE_KEYS:
                if self._key_pos is None:
                    self._key_pos = QPointF(self._pointer) if self._pointer is not None \
                        else QPointF(self.width() / 2.0, self.height() / 2.0)
                step = 5.0 if event.modifiers() & Qt.ShiftModifier else 1.0
                dx, dy = self.NUDGE_KEYS[key]
                self._key_pos = QPointF(self._key_pos.x() + dx * step,
                                        self._key_pos.y() + dy * step)
                self._sample(self._key_pos, "none")
                event.accept()
                return
            if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space) and self._pointer is not None:
                where = self._key_pos if self._key_pos is not None else QPointF(self._pointer)
                if self._preview is not None and hasattr(self._preview, "set_active"):
                    preview = self._preview
                    preview.set_active(True)            # the click pop...
                    QTimer.singleShot(160, lambda: preview.set_active(False))
                self._sample(where, "brush")
                event.accept()
                return
        except Exception as exc:  # pragma: no cover - defensive
            LOG.exception("keyPressEvent failed")
        super().keyPressEvent(event)

    def leaveEvent(self, event):
        try:
            # Still within the edge band: keep the sampler and follow the
            # pointer outside the widget instead of dropping it at the bounds.
            if (self._preview is not None and self._preview.isVisible()
                    and self._show_pointer and not self._pointer_down):
                local = self._cursor_local()
                self._outside.start()
                self._sample(QPointF(local), "none")
                if self._pointer is not None:
                    return
                self._outside.stop()
            self._pointer_down = False
            if self._preview is not None and hasattr(self._preview, "set_active"):
                self._preview.set_active(False)
            self._hover_color = None
            self._pointer = None
            if self._preview is not None:
                self._preview.hide()
            self._hide_mouse(False)
            if self._hover_cb is not None:
                try:
                    self._hover_cb(None)
                except Exception:  # pragma: no cover - defensive
                    pass
            self.update()
        except Exception as exc:  # pragma: no cover - cosmetic only
            LOG.exception("leaveEvent failed")

    def _sample(self, point: QPointF, mode: str = "none"):
        """Sample the sphere under ``point``.

        The sphere is a *brush* picker only: it never assigns a color to the
        sphere's own targets. Those change from the sliders, the target dots or
        the document eyedropper -- not from clicking the sphere.

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
                self._hide_mouse(False)
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
        # Use the same inset sphere rectangle as paintEvent, not the full widget
        # side: the image is drawn inside side - 2*SPHERE_MARGIN.
        off_x, off_y, dia = self._sphere_geometry()
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
        if mode == "target" and self._target_cb is not None:
            try:
                self._target_cb(pixel)
            except Exception as exc:  # pragma: no cover - defensive
                LOG.exception("target callback failed")
        if mode == "brush" and self._brush_cb is not None:
            try:
                self._brush_cb(pixel)
            except Exception as exc:  # pragma: no cover - defensive
                print(f"Lumina: brush callback failed - {exc}")

    def _sphere_geometry(self):
        """Top-left and diameter of the drawn sphere, in widget coordinates.

        Single source of truth shared by :meth:`paintEvent` and
        :meth:`_to_sphere_coords`. They previously each recomputed the centring
        separately, which is exactly the kind of duplication that lets the thing
        you see drift away from the thing you click.

        The sphere is inset by :data:`SPHERE_MARGIN` rather than filling the widget.
        A circle drawn edge-to-edge has no room outside itself, so the glide
        band in :meth:`_to_sphere_coords` could never be reached: the widget
        bounds rejected the point first, at the four points where the circle
        touches the square. The margin gives that band somewhere to live, so
        picking behaves the same all the way round.
        """
        w, h = self.width(), self.height()
        side = min(w, h)
        dia = max(1.0, side - 2.0 * SPHERE_MARGIN)
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
        off_x, off_y, dia = self._sphere_geometry()
        # Test against the widget, not the sphere rect: the glide band deliberately
        # reaches outside the circle, so it must not be clipped to it. A drag
        # keeps the mouse, so while the button is held the band may also reach
        # past the widget; the EDGE_GLIDE test below still bounds it.
        if (not self._pointer_down and not self._outside.active
                and not (0 <= point.x() < w and 0 <= point.y() < h)):
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
            if r - 1.0 > self.edge_glide / (dia * 0.5):
                return None, None
            k = 1.0 / r
            u *= k
            v *= k
        # Derived from the (possibly projected) u/v rather than the raw point,
        # so the projected case samples the rim instead of a masked-out pixel.
        r = math.sqrt(u * u + v * v)
        if r > 0.0:
            # Erode the pickable disc ~2 image pixels. The outermost ring
            # carries coverage-grid antialiasing blended with the background,
            # so sampling it reports a darkened mix instead of the sphere
            # colour -- hovering the rim read the backdrop, not the sphere.
            # Only the fringe band is pulled inward; interior samples are
            # untouched, and the glide projection above still lands the
            # pointer on the rim.
            inset = 4.0 / float(res)
            band = 12.0 / float(res)
            if r > 1.0 - band:
                k = max(0.0, (r - inset) / r)
                u *= k
                v *= k
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
