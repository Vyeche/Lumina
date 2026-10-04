"""Krita-facing shading bridge for the Lumina sphere renderer.

This module keeps Qt and Krita details outside the pure-Python lighting engine.
The engine works in normalized color values and performs the actual sphere
geometry, light transport, artist-controlled tonal mapping, and final color
conversion. This adapter accepts QColor values and returns a QImage when Qt is
available.
"""

from typing import Sequence

try:
    from PyQt5.QtGui import QColor, QImage
    _HAS_QT = True
except Exception:
    _HAS_QT = False

try:
    from .color_engine import ColorEngine
except ImportError:
    try:
        from color_engine import ColorEngine
    except ImportError:
        import importlib.util as _ilu
        import os as _os
        _ce_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "color_engine.py")
        _spec = _ilu.spec_from_file_location("color_engine", _ce_path)
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        ColorEngine = _mod.ColorEngine


def _qcolor_to_floats(qcolor) -> Sequence[float]:
    """Convert a QColor to normalized red, green, blue values in the range 0..1."""
    return (qcolor.redF(), qcolor.greenF(), qcolor.blueF())


def _floats_to_qcolor(r: float, g: float, b: float):
    """Convert normalized red, green, blue values in the range 0..1 to QColor."""
    if not _HAS_QT:
        raise RuntimeError("PyQt5 is required to create QColor values")
    return QColor.fromRgbF(
        max(0.0, min(1.0, float(r))),
        max(0.0, min(1.0, float(g))),
        max(0.0, min(1.0, float(b))),
    )


class SphereColorProcessor:
    """Krita-facing facade over ColorEngine.

    The public color attributes are kept synchronized with the underlying
    engine so callers that inspect processor state do not see stale values.
    """

    def __init__(self, resolution: int = 256):
        self.resolution = max(8, min(int(resolution), 512))

        self.ambient_color = (0.88, 0.88, 0.88)
        self.shadow_color = (0.1569, 0.1569, 0.2353)
        self.highlight_color = (1.0, 1.0, 1.0)
        self.light_color = (1.0, 1.0, 1.0)

        self.engine = ColorEngine(resolution=self.resolution)

        self.ambient_color = tuple(self.engine.ambient_color)
        self.shadow_color = tuple(self.engine.shadow_color)
        self.highlight_color = tuple(self.engine.highlight_color)
        self.light_color = tuple(self.engine.light_color)

    # ------------------------------------------------------------------
    # Color setters
    # ------------------------------------------------------------------
    def set_ambient_color(self, color) -> None:
        value = self._coerce_color(color)
        self.ambient_color = value
        self.engine.set_ambient_color(value)

    def set_shadow_color(self, color) -> None:
        value = self._coerce_color(color)
        self.shadow_color = value
        self.engine.set_shadow_color(value)

    def set_highlight_color(self, color) -> None:
        value = self._coerce_color(color)
        self.highlight_color = value
        self.engine.set_highlight_color(value)

    def set_light_color(self, color) -> None:
        value = self._coerce_color(color)
        self.light_color = value
        self.engine.set_light_color(value)

    # ------------------------------------------------------------------
    # Lighting and display setters
    # ------------------------------------------------------------------
    def set_light_angle(self, azimuth, elevation=None) -> None:
        self.engine.set_light_angle(azimuth, elevation)

    def set_light_elevation(self, elevation) -> None:
        self.engine.set_light_elevation(elevation)

    def set_ambient(self, value) -> None:
        self.engine.set_ambient(value)

    def set_light_intensity(self, value) -> None:
        self.engine.set_light_intensity(value)

    def set_shininess(self, value) -> None:
        self.engine.set_shininess(value)

    def set_spec_knee(self, value) -> None:
        self.engine.set_spec_knee(value)

    def set_spec_max(self, value) -> None:
        self.engine.set_spec_max(value)

    def set_rim_light(self, value) -> None:
        self.engine.set_rim_light(value)

    def set_contrast(self, value) -> None:
        self.engine.set_contrast(value)

    def set_brightness(self, value) -> None:
        self.engine.set_brightness(value)

    def set_saturation(self, value) -> None:
        self.engine.set_saturation(value)

    def set_light_type(self, value) -> None:
        self.engine.set_light_type(value)

    def set_mixer_mode(self, mode) -> None:
        self.engine.set_mixer_mode(mode)

    def set_glow_intensity(self, value) -> None:
        self.engine.set_glow_intensity(value)

    def set_glow_radius(self, value) -> None:
        self.engine.set_glow_radius(value)

    def set_grain(self, value) -> None:
        self.engine.set_grain(value)

    def set_smooth(self, value) -> None:
        self.engine.set_smooth(value)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def render(self, base_color, width, height):
        """Render the sphere as a list of red, green, blue integer triples."""
        return self.engine.render(self._coerce_color(base_color), width, height)

    def render_image(self, base_color, width=None, height=None):
        """Render the sphere and return a QImage, or raw BGRA bytes without Qt."""
        w = self.resolution if width is None else max(2, int(width))
        h = self.resolution if height is None else max(2, int(height))

        pixels = self.engine.render(self._coerce_color(base_color), w, h)
        raw = self._build_buffer(pixels, w, h)

        if _HAS_QT:
            return QImage(
                bytes(raw),
                w,
                h,
                w * 4,
                QImage.Format_ARGB32,
            ).copy()

        return raw

    def _build_buffer(self, pixels, w, h):
        """Pack RGB pixels into BGRA with the engine's analytic circle coverage."""
        mask = self.engine.mask_grid()
        coverage = self.engine.coverage_grid()
        raw = bytearray(w * h * 4)
        index = 0

        for row in range(h):
            pixel_row = pixels[row]
            mask_row = mask[row]
            coverage_row = coverage[row]

            for col in range(w):
                alpha = coverage_row[col]

                if mask_row[col]:
                    red, green, blue = pixel_row[col]

                elif alpha > 0.0:
                    red, green, blue = self._nearest_shade(
                        pixels,
                        mask,
                        row,
                        col,
                        w,
                        h,
                    )

                else:
                    red = green = blue = 0
                    alpha = 0.0

                raw[index] = int(max(0.0, min(255.0, blue)))
                raw[index + 1] = int(max(0.0, min(255.0, green)))
                raw[index + 2] = int(max(0.0, min(255.0, red)))
                raw[index + 3] = int(max(0.0, min(1.0, alpha)) * 255.0 + 0.5)
                index += 4

        return raw

    @staticmethod
    def _nearest_shade(pixels, mask, row, col, width, height):
        """Find the nearest rendered interior pixel for an antialiased edge pixel."""
        max_radius = max(width, height)

        for radius in range(1, max_radius):
            for delta_row in range(-radius, radius + 1):
                if abs(delta_row) == radius:
                    delta_columns = range(-radius, radius + 1)
                else:
                    delta_columns = (-radius, radius)

                for delta_col in delta_columns:
                    candidate_row = row + delta_row
                    candidate_col = col + delta_col

                    if not (0 <= candidate_row < height and 0 <= candidate_col < width):
                        continue

                    if mask[candidate_row][candidate_col]:
                        return pixels[candidate_row][candidate_col]

        return (0, 0, 0)

    # ------------------------------------------------------------------
    # Input color normalization
    # ------------------------------------------------------------------
    def _coerce_color(self, color):
        """Normalize QColor or RGB sequence to a clamped normalized RGB tuple."""
        if _HAS_QT and isinstance(color, QColor):
            return _qcolor_to_floats(color)

        values = tuple(float(component) for component in color)
        if len(values) != 3:
            raise ValueError("Color must contain exactly three channels")

        if any(abs(component) > 1.0 for component in values):
            values = tuple(component / 255.0 for component in values)

        return tuple(max(0.0, min(1.0, value)) for value in values)
