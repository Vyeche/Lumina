"""color_processor.py — Krita-facing shading bridge over the pure engine.

``color_engine.py`` is framework-agnostic (pure tuples, no Qt). This module is
the *adapter* that lets the rest of the Krita plugin talk to it using the
historical ``QColor`` / ``QImage`` API:

    * Accepts ``QColor`` values from Krita.
    * Exposes the same ``set_*`` methods as the old engine.
    * Returns a ``QImage`` (inside Krita) with an identical shading result.

Why the split?  ``color_engine.py`` can be unit-tested on any machine, while
this adapter only needs to run inside Krita (where PyQt5 exists).  The two are
kept in sync by construction: every color is converted to normalized floats and
fed straight into :class:`ColorEngine`.

This module is import-safe both inside Krita AND standalone (it guards the
``QColor`` import so the shading math can still be exercised on the host via
``color_engine.py``).
"""

import math
from typing import Optional, Sequence

# Only import Qt if it is actually available.  Inside the Krita flatpak PyQt5
# is installed; on the host (and in CI) it is not — and we must not crash here.
try:  # pragma: no cover - depends on environment
    from PyQt5.QtGui import QColor, QImage

    _HAS_QT = True
except Exception:  # noqa: BLE001 - intentionally catch all import failures
    _HAS_QT = False

# ``color_engine`` is a pure-Python submodule. Inside the Krita package it is a
# relative import (``color_processor`` lives in the ``LuminaPlugin`` package);
# on the host (tests) it is a top-level module in the working directory. The
# absolute-import fallback keeps the standalone test runnable from the package
# directory; the relative import is what works inside Krita (avoids
# ``ModuleNotFoundError: No module named 'color_engine'``).
try:  # pragma: no cover - depends on packaging
    from .color_engine import ColorEngine
except ImportError:  # noqa: BLE001
    from color_engine import ColorEngine


# ---------------------------------------------------------------------------
# Color conversion helpers (QColor <-> normalized floats)
# ---------------------------------------------------------------------------
def _qcolor_to_floats(qcolor) -> Sequence[float]:
    """Convert a ``QColor`` to a normalized ``(r, g, b)`` tuple in 0..1."""
    return (qcolor.redF(), qcolor.greenF(), qcolor.blueF())


def _floats_to_qcolor(r: float, g: float, b: float) -> "QColor":
    """Convert normalized floats to a ``QColor`` (Krita context)."""
    return QColor.fromRgbF(max(0.0, min(1.0, r)), max(0.0, min(1.0, g)), max(0.0, min(1.0, b)))


# ---------------------------------------------------------------------------
# SphereColorProcessor — Krita-facing shading facade
# ---------------------------------------------------------------------------
class SphereColorProcessor:
    """Krita-facing shading facade over :class:`ColorEngine`.

    All color parameters are stored internally as normalized ``(r, g, b)``
    tuples (matching :class:`ColorEngine`), and converted to/from ``QColor``
    only at the boundary with Krita code.  The public ``set_*`` methods mirror
    the historical plugin API so existing widget handlers work unchanged.
    """

    def __init__(self, resolution: int = 256):
        self.resolution = max(8, min(resolution, 512))
        # Public (normalized) color state, mirrored by ColorEngine.
        self.ambient_color: Sequence[float] = (0.8627, 0.8627, 0.9412)
        self.shadow_color: Sequence[float] = (0.1569, 0.1569, 0.2353)
        self.highlight_color: Sequence[float] = (1.0, 1.0, 1.0)

        # Delegate all geometry + shading to the pure engine.
        self.engine = ColorEngine(resolution=self.resolution)

    # ------------------------------------------------------------------
    # Setters — accept QColor (Krita) or normalized floats (tests)
    # ------------------------------------------------------------------
    def set_ambient_color(self, color) -> None:
        """Set ambient color. Accepts a ``QColor`` or an ``(r,g,b)`` sequence."""
        self.engine.set_ambient_color(self._coerce_color(color))

    def set_shadow_color(self, color) -> None:
        self.engine.set_shadow_color(self._coerce_color(color))

    def set_highlight_color(self, color) -> None:
        self.engine.set_highlight_color(self._coerce_color(color))

    def set_light_color(self, color) -> None:
        """Tint for the lit side. Accepts a ``QColor`` or an ``(r,g,b)`` sequence."""
        self.engine.set_light_color(self._coerce_color(color))


    # --- Lighting / intensity (unchanged signatures) ---
    def set_light_angle(self, azimuth, elevation=None) -> None:
        self.engine.set_light_angle(azimuth, elevation)

    def set_ambient(self, value) -> None:
        self.engine.set_ambient(value)

    def set_light_intensity(self, value) -> None:
        self.engine.set_light_intensity(value)

    def set_shininess(self, value) -> None:
        self.engine.set_shininess(value)

    def set_spec_knee(self, value) -> None:
        self.engine.set_spec_knee(value)

    def set_contrast(self, value) -> None:
        self.engine.set_contrast(value)

    def set_light_elevation(self, elevation) -> None:
        self.engine.set_light_elevation(elevation)

    def set_brightness(self, value) -> None:
        self.engine.set_brightness(value)

    def set_saturation(self, value) -> None:
        self.engine.set_saturation(value)

    def set_mixer_mode(self, mode) -> None:
        self.engine.set_mixer_mode(mode)

    def set_glow_intensity(self, value) -> None:
        self.engine.set_glow_intensity(value)

    def set_glow_radius(self, value) -> None:
        self.engine.set_glow_radius(value)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def render(self, base_color, width, height):
        """Render the sphere to a list of ``(r, g, b)`` triples (0..255)."""
        return self.engine.render(base_color, width, height)

    def render_image(self, base_color, width=None, height=None):
        """Render the sphere and return a ``QImage`` (Krita context only).

        Pixels outside the sphere silhouette are fully transparent (alpha 0),
        and the one-pixel band straddling the rim carries fractional alpha so
        the edge is antialiased rather than a hard staircase.
        Falls back to a plain ``bytes`` buffer of packed ARGB pixels when Qt is
        unavailable, so callers can still inspect raw pixel data in tests.
        """
        w = width or self.resolution
        h = height or self.resolution
        pixels = self.engine.render(self._coerce_color(base_color), w, h)
        raw = self._build_buffer(pixels, w, h)
        if _HAS_QT:
            return QImage(bytes(raw), w, h, w * 4, QImage.Format_ARGB32).copy()
        return raw

    def _build_buffer(self, pixels, w, h):
        """Pack the render into a BGRA byte buffer with antialiased edges.

        Built once and shared by the QImage and raw-bytes paths, which were
        previously identical copies of the same loop.

        Two things produce the soft rim:

        * **Fractional alpha** from the engine's coverage grid. The binary mask
          is still used to decide *what is coloured*, but the alpha follows how
          much of each pixel the circle actually covers.
        * **Edge colour borrowing.** A pixel just outside the circle is still
          partly visible, and the engine returns black there. Compositing
          partially-transparent black over the panel would leave a dark
          one-pixel halo, so the outside band copies the colour of its nearest
          shaded neighbour instead. Without this the antialiasing trades jaggies
          for a dark fringe.
        """
        mask = self.engine.mask_grid()
        cov = self.engine.coverage_grid()
        raw = bytearray(w * h * 4)
        i = 0
        for row in range(h):
            prow = pixels[row]
            mrow = mask[row]
            crow = cov[row]
            for col in range(w):
                c = crow[col]
                if mrow[col]:
                    r, g, b = prow[col]
                    a = c
                elif c > 0.0:
                    # Outside the mask but still partly covered: borrow the
                    # nearest shaded neighbour so the edge does not darken.
                    src = self._nearest_shade(pixels, mask, row, col, w, h)
                    r, g, b = src
                    a = c
                else:
                    r = g = b = 0
                    a = 0.0
                raw[i] = b
                raw[i + 1] = g
                raw[i + 2] = r
                raw[i + 3] = int(a * 255.0 + 0.5) if a > 0.0 else 0
                i += 4
        return raw

    @staticmethod
    def _nearest_shade(pixels, mask, row, col, w, h):
        """Colour of the closest masked-in pixel, searched 4-way then outward.

        The outside band is one pixel wide, so the first ring almost always
        answers immediately; the wider sweep is only a fallback for the grid
        corners, which no inward direction can reach.
        """
        for radius in range(1, max(w, h)):
            for dr in range(-radius, radius + 1):
                for dc in (-radius, radius) if abs(dr) != radius else range(-radius, radius + 1):
                    r2, c2 = row + dr, col + dc
                    if 0 <= r2 < h and 0 <= c2 < w and mask[r2][c2]:
                        return pixels[r2][c2]
        return (0, 0, 0)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _coerce_color(self, color):
        """Normalize a QColor / sequence into an ``(r, g, b)`` float tuple."""
        if _HAS_QT and isinstance(color, QColor):
            return (color.redF(), color.greenF(), color.blueF())
        # Assume a sequence of ints 0..255 (or normalized floats).
        return tuple(float(c) for c in color)
