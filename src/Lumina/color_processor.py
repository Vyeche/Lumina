"""Krita-facing shading bridge for the Lumina sphere renderer.

This module keeps Qt and Krita details outside the pure-Python lighting engine.
The engine works in normalized color values and performs the actual sphere
geometry, light transport, artist-controlled tonal mapping, and final color
conversion. This adapter accepts QColor values and returns a QImage when Qt is
available.
"""

from collections import OrderedDict
import copy
from typing import Sequence
import time

try:
    from PyQt5.QtGui import QColor, QImage
    _HAS_QT = True
except Exception:
    _HAS_QT = False

try:
    from .color_engine import ColorEngine, RenderCancelled
except ImportError:
    try:
        from color_engine import ColorEngine, RenderCancelled
    except ImportError:
        import importlib.util as _ilu
        import os as _os
        _ce_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "color_engine.py")
        _spec = _ilu.spec_from_file_location("color_engine", _ce_path)
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        ColorEngine = _mod.ColorEngine
        RenderCancelled = _mod.RenderCancelled


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

    FULL_CACHE_SIZE = 8

    def __init__(self, resolution: int = 256):
        self.resolution = max(8, min(int(resolution), 512))

        self.ambient_color = (0.88, 0.88, 0.88)
        self.shadow_color = (0.1569, 0.1569, 0.2353)
        self.highlight_color = (1.0, 1.0, 1.0)
        self.light_color = (1.0, 1.0, 1.0)

        # Last render_image() stage timings in ms (buffer pack + QImage).
        self.last_ms = {"buffer": 0.0, "qimage": 0.0}

        # Full-quality output cache (issue #40 §1): the last FULL_CACHE_SIZE
        # full-quality results as immutable bytes, keyed exactly, least
        # recently used dropped first. Previews never populate it; a full
        # render reuses an entry only on exact key match. It holds several
        # so toggling a preset on and off, or stepping back to a recent
        # look, is instant (one entry only ever matched an unchanged state).
        # last_cache_hit reports the previous render_image.
        self._full_cache = OrderedDict()
        self.last_cache_hit = False

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

    def set_rim_sky_mix(self, value) -> None:
        self.engine.set_rim_sky_mix(value)

    def set_sky_bounce(self, value) -> None:
        self.engine.set_sky_bounce(value)

    def set_ground_bounce(self, value) -> None:
        self.engine.set_ground_bounce(value)

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

    def render_image(self, base_color, width=None, height=None,
                       full_quality=False):
        """Render the sphere and return a QImage, or raw BGRA bytes without Qt.

        full_quality marks a release/final render at the configured size:
        only those consult and populate the one-entry output cache, so a
        drag preview (smaller size, suspended smoothing) can never replace
        or satisfy a full-quality result. Callers that do not opt in render
        exactly as before.
        """
        w = self.resolution if width is None else max(2, int(width))
        h = self.resolution if height is None else max(2, int(height))
        base = self._coerce_color(base_color)

        key = None
        if full_quality:
            key = self._full_cache_key_for(base, w, h)
            cached = self._full_cache.get(key)
            if cached is not None:
                self._full_cache.move_to_end(key)
                self.last_cache_hit = True
                return self._image_from_bytes(cached, w, h)
            self.last_cache_hit = False
        else:
            self.last_cache_hit = False

        t_buf = time.perf_counter()
        raw = self.engine.render_bgra(base, w, h)
        if raw is None:                    # an engine without the fast path
            pixels = self.engine.render(base, w, h)
            t_buf = time.perf_counter()
            raw = self._build_buffer(pixels, w, h)
        buf_ms = (time.perf_counter() - t_buf) * 1000.0

        if full_quality:
            # Immutable snapshot: later renders must not observe mutation.
            self._full_cache[key] = bytes(raw)
            self._full_cache.move_to_end(key)
            while len(self._full_cache) > self.FULL_CACHE_SIZE:
                self._full_cache.popitem(last=False)

        if _HAS_QT:
            t_img = time.perf_counter()
            img = QImage(
                bytes(raw),
                w,
                h,
                w * 4,
                QImage.Format_ARGB32,
            ).copy()
            self.last_ms = {"buffer": buf_ms,
                            "qimage": (time.perf_counter() - t_img) * 1000.0}
            return img

        self.last_ms = {"buffer": buf_ms, "qimage": 0.0}
        return raw

    def _full_cache_key_for(self, base, w, h):
        """Complete cache key: versions, base, size, settings, format."""
        return (ColorEngine.RENDER_ALGORITHM_VERSION,
                ColorEngine.GEOMETRY_ALGORITHM_VERSION,
                tuple(base), w, h,
                self.engine.render_signature(),
                "argb32" if _HAS_QT else "bgra")

    @staticmethod
    def _image_from_bytes(cached, w, h):
        """Rebuild the return value from cached bytes without re-rendering."""
        if _HAS_QT:
            return QImage(
                bytes(cached),
                w,
                h,
                w * 4,
                QImage.Format_ARGB32,
            ).copy()
        return bytearray(cached)

    def clear_full_cache(self):
        """Drop the cached full-quality result (e.g. settings reset)."""
        self._full_cache.clear()
        self.last_cache_hit = False

    def has_full_render(self, base_color, width, height) -> bool:
        """Whether a full-quality render for the current state is cached."""
        key = self._full_cache_key_for(self._coerce_color(base_color),
                                       max(2, int(width)), max(2, int(height)))
        return key in self._full_cache

    # ColorEngine attributes that are caches of its own work rather than
    # settings. An off-thread render (see full_render_job) copies everything
    # else from the live engine and keeps these to itself: the caches are
    # filled in place, so sharing them across threads would race. Each stage
    # cache is keyed on the settings it depends on, so a copy's caches stay
    # right without any invalidation from the live engine's setters.
    ENGINE_CACHES = frozenset((
        "_normal_grid", "_mask_grid", "_coverage_grid", "_pos_grid",
        "_grid_width", "_grid_height", "_geo_cache", "_shading_cache",
        "_light_vector", "_diffuse_cache", "_shadow_cache", "_spec_cache",
        "_light_cache_key_value", "_light_ldir", "_light_illum", "_light_ndl",
        "_light_half", "_material_cache_key_value", "_material_body",
        "_timings", "_last_timing_log", "_cancel",
        "_fast_geo_cache", "_fast_light", "_fast_width"))

    def full_render_job(self, base_color, width, height):
        """Snapshot what a full-quality render needs, on the UI thread.

        Returns ``(key, w, h, base, settings)`` for :meth:`run_full_render_job`
        and :meth:`store_full_render`. The settings are plain values and LUTs
        the engine replaces rather than edits, so later changes to the live
        engine cannot reach a render already under way.
        """
        w = max(2, int(width))
        h = max(2, int(height))
        base = self._coerce_color(base_color)
        settings = {k: v for k, v in self.engine.__dict__.items()
                    if k not in self.ENGINE_CACHES}
        if getattr(self, "_job_engine", None) is None:
            engine = copy.copy(self.engine)
            for name in self.ENGINE_CACHES:
                engine.__dict__.pop(name, None)
            engine._geo_cache = {}
            engine._shading_cache = {}
            engine._timings = {}
            engine._grid_width = engine._grid_height = 0
            engine._light_cache_key_value = None
            engine._material_cache_key_value = None
            self._job_engine = engine
        return (self._full_cache_key_for(base, w, h), w, h, base, settings)

    def run_full_render_job(self, job, cancel=None):
        """Render a :meth:`full_render_job` and return its BGRA bytes.

        Safe off the UI thread: it uses an engine of its own (made by
        :meth:`full_render_job`, kept between jobs so its geometry stays
        warm) and touches nothing else. One job at a time. Returns None when
        ``cancel`` (a threading.Event) is set before it finishes.
        """
        _key, w, h, base, settings = job
        engine = self._job_engine
        engine.__dict__.update(settings)
        engine._cancel = cancel
        try:
            if engine._grid_width == w and engine._grid_height == h:
                engine._compute_shading()      # light may have moved since
            raw = engine.render_bgra(base, w, h)
            if raw is None:
                raw = self._build_buffer(engine.render(base, w, h), w, h, engine)
        except RenderCancelled:
            return None
        finally:
            engine._cancel = None
        return bytes(raw)

    def store_full_render(self, job, raw):
        """Back on the UI thread: cache a finished job, return its QImage."""
        key, w, h, _base, _settings = job
        self._full_cache[key] = raw
        self._full_cache.move_to_end(key)
        while len(self._full_cache) > self.FULL_CACHE_SIZE:
            self._full_cache.popitem(last=False)
        return self._image_from_bytes(raw, w, h)

    def _build_buffer(self, pixels, w, h, engine=None):
        """Pack RGB pixels into BGRA with the engine's analytic circle coverage."""
        engine = self.engine if engine is None else engine
        mask = engine.mask_grid()
        coverage = engine.coverage_grid()
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
