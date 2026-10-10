"""sphere_docker.py — The Lumina Krita docker (V2 icon-driven layout).

Assembles the compact, icon-driven control panel:

    +-----------------------------------------------------+
    |  [Sphere]                                              |   <- SphereWidget (small circular sphere)
    |            r,g,b                                     |
    +-----------------------------------------------------+
    | [Color] Base ▢                                       |
    | [Intensity] ◐                                         |
    | [Ambient] ◑                                           |
    | [Contrast] ◔                                         |
    | [Specular] ◕                                          |
    | [Glow] ✦                                              |
    | [Saturation] ◗                                        |
    +-----------------------------------------------------+

Key V2 design decisions implemented here:
    * Narrow vertical sidebar (not a wide text-labeled panel).
    * Color-coded thin sliders (RGB-style accent tints) instead of plain sliders.
    * Icon-driven toggles with accent dots, no text labels.
    * Compact circular sphere instead of a large square-ish one.
    * All shading math delegated to the pure ``color_engine`` / ``color_processor``.
"""

import math
import json
import sys
import time
import weakref
from typing import Callable, Optional

from PyQt5.QtCore import (Qt, QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSettings,
                         QTimer)
from PyQt5.QtGui import (QColor, QCursor, QPainter, QLinearGradient, QPixmap, QImage, QIcon,
                         QFont, QFontMetricsF, QPen, QPainterPath, QPolygonF,
                         QPalette)
from PyQt5.QtWidgets import (
    QAbstractButton, QApplication, QDockWidget, QWidget, QVBoxLayout, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QFrame, QSizePolicy, QLayout, QScrollArea
)

# `krita` provides the docker-factory registration API (Krita, DockWidgetFactory,
# DockWidgetFactoryBase) and the DockWidget base class. These live in the
# `krita` module, NOT QtWidgets.
# The stale `DockWidget` reference here is exactly why the module failed to load.
try:
    from krita import Krita, DockWidgetFactory, DockWidgetFactoryBase, DockWidget
except ImportError:
    Krita = DockWidgetFactory = DockWidgetFactoryBase = None
    # Off-host fallback (tests, standalone): plain QDockWidget. Inside Krita
    # the real base is required -- it carries the KoCanvasObserverBase
    # plumbing, so subclassing QDockWidget directly logs "is not a canvas
    # observer" and canvasChanged() never fires.
    DockWidget = QDockWidget

from .color_processor import SphereColorProcessor
from .color_engine import SPEC_KNEE, SPEC_KNEE_MIN, SPEC_KNEE_MAX
from .sphere_widget import EDGE_GLIDE, SphereWidget
from .color_controls import (Accent, ColorSampler, ColorSlider,
                             CollapsibleSection, RecentColors, SettingsPanel,
                             KnobRipple, SparkleBurst, SweatEmitter,
                             TypedReadout, _flash_clamped)
from .tooltip import set_tooltip as _set_tooltip
from .derivation import derive as derive_targets
from .derivation import cusp as _hue_cusp, from_slider as _lch_to_rgb, to_slider as _rgb_to_lch
from .tooltip import TOOLTIP_BG, TOOLTIP_FG, TOOLTIP_BORDER

# ---------------------------------------------------------------------------
# Logging — shared setup in lumina_logging (file handler never holds the
# log open, so Windows reinstalls can delete the plugin directory).
# ---------------------------------------------------------------------------
import os

from .lumina_logging import LOG




# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Highlight-size endpoints for the gear popup, in engine shininess. The
# Default sheen, fitted to the reference lighting app's highlight (it was
# 3.52, then 10.5): broad, and centred near the light (SPEC_TOWARD_LIGHT) so
# it reaches the lit silhouette as the reference's does. The gear's
# highlight size rests at 99.
SHININESS_REF = 8.9
# Default light direction, fitted to the reference app (was 287/45).
DEFAULT_AZIMUTH = 304
DEFAULT_ELEVATION = 41
SHININESS_MAX = 64.0
SPHERE_SIZE = 200          # smallest the sphere widget gets (narrow panel)
SPHERE_MAX = 340           # largest it grows to as the panel widens
# Captions under icons (presets, light type, targets): 9px dim grey was hard
# to read on the dark panel.
CAPTION_QSS = "QLabel { color: #d3dae6; font-size: 10px; background: transparent; }"
CAPTION_ON_QSS = ("QLabel { color: #ffffff; font-size: 10px; font-weight: bold; "
                  "background: transparent; }")
SPHERE_RENDER = 200        # engine render resolution (square)
# Ceiling on the render resolution while a slider is held down. Renders are
# throttled to ~30 ms during drags and the blur pass is suspended, so 96 px
# previews track the pointer; the full-resolution sphere is drawn again on
# release.
DRAG_RENDER = 96
DRAG_THROTTLE_MS = 30.0
# External foreground-color stream (native picker drags): render at most
# this often, with exactly one full-quality final at declared stream end.
# ~50 ms preview ~= 20 FPS; the end detector needs ~400 ms of confirmed
# inactivity (8 unchanged 50 ms polls, ~400 ms) before the blocking full
# render. Deliberately longer than the ~230 ms pauses inside a legitimate
# drag: a delayed final beats a mid-drag freeze. Lower once the full-render
# cost itself comes down.
EXT_RENDER_MS = 50
EXT_END_POLLS = 8
# Post-stream persistence delay: sync() waits this long past stream end so a
# pause-then-resume never eats disk latency mid-gesture.
STREAM_END_SAVE_MS = 500
# Disk persistence: ordinary changes wait this long of quiet for one write.
SAVE_DEBOUNCE_MS = 2000
# Plugin version, logged at startup with the loaded module path so stale or
# half-reinstalled copies are visible in bug reports. Keep in sync with
# src/Lumina.desktop (X-KDE-PluginInfo-Version); bump both per release.
PLUGIN_VERSION = "2.7.0"
# Settings schema version. v1 stored Tone as brightness (bug); v2 stores Tone
# as saturation. v3 persists spec_max. v4 migrates Diffuse/highlight-size
# slider levels to the perceptual curves (old linear positions reinterpreted
# as old physical values, then mapped to equivalent new positions).
# v5 persists the environment accents (rim, rim tint, sky, ground).
SETTINGS_VERSION = 5
# --- Persisted settings -----------------------------------------------------
# Stored via QSettings so they land somewhere the user can find and back up. The
# Inifile format is deliberate: it is a plain text file inside the flatpak's
# config dir, readable and editable without Krita running.
_SETTINGS_ORG = "Lumina"
_SETTINGS_APP = "Lumina"
# The plugin shipped as "LightingSphere" before it was renamed. QSettings keys
# its file on the org/app pair, so renaming would orphan every existing user's
# setup in a stale ini file. Keep the old pair around for one release and copy
# anything forward on first load.
_LEGACY_SETTINGS_ORG = "LightingSphere"
_LEGACY_SETTINGS_APP = "LightingSphere"
# Krita's built-in sRGB profile, used when the document is not RGB and the
# targets need some RGB space to be expressed in.
SRGB_PROFILE = "sRGB-elle-V2-srgbtrc.icc"
# Labels of the hex-entry toggle under the active swatch. The box edits
# whichever target is selected, so the label names it.
HEX_EDIT_LABELS = {"shadow": "EDIT SHADE", "base": "EDIT BASE", "light": "EDIT HIGH"}
HEX_EDIT_LABEL = HEX_EDIT_LABELS["base"]
HEX_DONE_LABEL = "DONE"


def _settings():
    return QSettings(QSettings.IniFormat, QSettings.UserScope,
                     _SETTINGS_ORG, _SETTINGS_APP)


def _settings_path() -> str:
    return _settings().fileName()


def _migrate_legacy_settings() -> bool:
    """Copy a pre-rename LightingSphere setup into the Lumina settings.

    Returns True when something was migrated. Only runs when the Lumina file is
    still empty, so it can never overwrite a setup the user has already made
    under the new name.
    """
    current = _settings()
    if current.allKeys():
        return False
    try:
        legacy = QSettings(QSettings.IniFormat, QSettings.UserScope,
                           _LEGACY_SETTINGS_ORG, _LEGACY_SETTINGS_APP)
        keys = legacy.allKeys()
        if not keys:
            return False
        for key in keys:
            current.setValue(key, legacy.value(key))
        current.sync()
        LOG.info("migrated %d settings from %s", len(keys), legacy.fileName())
        return True
    except Exception as exc:  # pragma: no cover - migration must never break load
        LOG.exception("_migrate_legacy_settings failed")
        print(f"Lumina: settings migration failed - {exc}")
        return False


# ---------------------------------------------------------------------------
# Lighting presets
# ---------------------------------------------------------------------------
# Each preset fully defines the *look* parameters. That completeness matters:
# a preset that only set contrast left the previous preset's glow and ambient
# behind, so switching from Neon to Matte still glowed. Anything not listed here
# (the target colours, the light direction, the mixer blend) is deliberately the
# user's to keep -- a preset that silently rotated the light or overwrote the
# colours they are painting from would be actively hostile.
#
# Preset calibration references (shadow/base/light relationships) are tuning
# references only, NOT target overrides: presets may change shininess, spec_max,
# ambient, glow, mixer, light type/intensity, but never shadow/base/light targets.
#
# Values are the ones the corresponding slider rows display, so applying a
# preset can move the rows and stay in sync with the engine.
_PRESETS = (
    {"key": "artistic", "label": "Artistic", "glyph": "sparkle",
     "accent": Accent.AMBER,
     "tip": "Artistic: bright white highlights, high contrast",
     "values": {"highlight": (1, 0.985, 0.97), "ambient": 6, "intensity": 105,
                "contrast": 128, "specular": 12, "diffuse": 60, "glow": 0,
                "tone": 112, "mixer": "Blended", "spec_max": 80, "rim": 14,
                "rim_mix": 50, "sky": 2, "ground": 1}},
    {"key": "real", "label": "Real", "glyph": "layers",
     "accent": Accent.PURPLE,
     "tip": "Real-world: warm highlights, soft natural contrast",
     "values": {"highlight": (1, 0.86, 0.7), "ambient": 12, "intensity": 100,
                "contrast": 88, "specular": 7, "diffuse": 80, "glow": 0,
                "tone": 96, "mixer": "Blended", "spec_max": 52, "rim": 8,
                "rim_mix": 40, "sky": 5, "ground": 5}},
    {"key": "nocturne", "label": "Nocturne", "glyph": "moon",
     "accent": Accent.BLUE,
     "tip": "Nocturne: low key, cool moonlight, deep shadow",
     "values": {"highlight": (0.6, 0.74, 1), "ambient": 1, "intensity": 20,
                "contrast": 118, "specular": 14, "diffuse": 55, "glow": 0,
                "tone": 78, "mixer": "Blended", "spec_max": 60, "rim": 28,
                "rim_mix": 90, "sky": 8, "ground": 0}},
    {"key": "gloss", "label": "Gloss", "glyph": "gloss",
     "accent": Accent.CYAN,
     "tip": "Gloss: tight bright highlight, slick and punchy",
     "values": {"highlight": (1, 0.995, 0.99), "ambient": 7, "intensity": 100,
                "contrast": 115, "specular": 48, "diffuse": 20, "glow": 0,
                "tone": 108, "mixer": "Blended", "spec_max": 100, "rim": 18,
                "rim_mix": 40, "sky": 3, "ground": 1}},
    {"key": "matte", "label": "Matte", "glyph": "disc",
     "accent": Accent.NEUTRAL,
     "tip": "Matte: even clay-like falloff, no specular hotspot",
     "values": {"highlight": (0.94, 0.94, 0.92), "ambient": 22, "intensity": 80,
                "contrast": 76, "specular": 2, "diffuse": 100, "glow": 0,
                "tone": 84, "mixer": "Blended", "spec_max": 6, "rim": 3,
                "rim_mix": 50, "sky": 8, "ground": 5}},
    {"key": "neon", "label": "Neon", "glyph": "bolt",
     "accent": Accent.GREEN,
     "tip": "Neon: saturated and blooming, coloured light",
     "values": {"highlight": (0.55, 1, 1), "ambient": 3, "intensity": 105,
                "contrast": 122, "specular": 16, "diffuse": 45, "glow": 85,
                "tone": 145, "mixer": "Additive", "spec_max": 75, "rim": 30,
                "rim_mix": 75, "sky": 2, "ground": 1}},
)

HEADER_BTN = 38         # gear / eyedropper size in the header row
PANEL_TOP_PAD = 13      # space above the header row (under the title bar)
PANEL_ROW_GAP = 14      # space between the header row and the sphere
TITLEBAR_BAND = 34     # height of the floating window's draggable title band
# --- Deriving light and shadow from a sampled base color --------------------
# See derivation.py: an OKLCH model fitted to the reference lighting app.

# Krita's own docker grey rather than near-black. At RGB(24,26,32) the panel
# read as a harsh black hole next to Krita's Layers and Tool Options panels,
# which sit around RGB(48,52,58) in the default dark theme.
PANEL_BG = QColor(48, 52, 58)
PANEL_BORDER = QColor(58, 64, 78)
TEXT_DIM = QColor(150, 160, 175)
TEXT_BRIGHT = QColor(225, 232, 245)

# Tooltips render in their own top-level window, so a stylesheet set on the
# docker does not reach them -- they were inheriting Krita's theme colours,
# which left dim tooltip text sitting on a background too close to its own
# value to read. An explicit style is applied application-wide (see
# _install_tooltip_style) because that is the only scope that covers a window
# that belongs to neither the docker nor its children.
#
# The text is TEXT_BRIGHT rather than TEXT_DIM: a tooltip is opt-in help, so
# it should read at full contrast, and dim text on a mid-grey tooltip is what
# made these hard to read in the first place.
TOOLTIP_STYLE = """
QToolTip {{
    background-color: {bg};
    color: {fg};
    border: 1px solid {border};
    padding: 0px;
}}
""".format(bg=TOOLTIP_BG, fg=TOOLTIP_FG, border=TOOLTIP_BORDER)


# ---------------------------------------------------------------------------
# Small helper widgets
# ---------------------------------------------------------------------------
def parse_typed_slider_value(text, lo, hi, unit="percent"):
    """Shared typed-entry parsing (see :mod:`typed_entry`).

    Kept as a thin wrapper so existing callers and tests keep working.
    Returns the clamped int, or None when the text is not a number.
    """
    from .typed_entry import parse_typed_entry
    result = parse_typed_entry(text, lo, hi, unit=unit)
    if not result.valid:
        return None
    return max(lo, min(hi, int(round(result.value))))


def format_slider_value(v, unit="percent"):
    """Display text for a slider value. Thin wrapper, see :mod:`typed_entry`."""
    from .typed_entry import format_slider_value as _fmt
    return _fmt(v, unit)


class LabeledSliderRow(QWidget):
    """A single-row control: accent dot + icon label + color-coded slider."""

    def __init__(self, accent: Accent, label: str, value: int = 50, parent=None,
                 lo: int = 0, hi: int = 100, unit: str = "percent"):
        super().__init__(parent)
        self._unit = unit
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)

        # Accent dot (tiny color indicator).
        self._dot = QFrame()
        self._dot.setFixedSize(5, 16)
        self._dot.setStyleSheet(f"background-color: {accent.name()}; border-radius: 2px;")
        layout.addWidget(self._dot)

        # Icon-style label (drawn as a small glyph + dim text).
        self._label = QLabel(label)
        self._label.setStyleSheet(
            f"QLabel {{ color: {TEXT_DIM.name()}; font-size: 11px; "
            f"font-weight: 600; letter-spacing: 0.3px; }}"
        )
        layout.addWidget(self._label)

        # Rows are exactly as tall as they need to be. With the default
        # Preferred policy the panel's leftover height was shared out between
        # the rows, leaving large gaps between the sliders.
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        # Color-coded thin slider.
        self.slider = ColorSlider(accent, orientation=Qt.Horizontal)
        self.slider.setMinimumWidth(120)
        # Range first, then value. Setting the value against Qt's default
        # 0-99 range and widening afterwards silently clamped anything above 99
        # and left the handle short of where it was asked to be.
        self.slider.setRange(lo, hi)
        self.slider.setValue(value)
        layout.addWidget(self.slider, 1)

        # Live value readout at the right end: an editable TypedReadout.
        # Display is canonical-with-unit (86% / 323° / 64); typing stays
        # permissive via the shared parser. Enter commits; moving focus
        # away reverts half-typed text.
        self._value = TypedReadout()
        self._value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self._value)
        self.slider.valueChanged.connect(
            lambda v: self._value.mark_committed(self._format_value(v)))
        self._value.mark_committed(self._format_value(value))
        self._value.returnPressed.connect(self._on_value_typed)

    def _format_value(self, v) -> str:
        return format_slider_value(v, self._unit)

    def _on_value_typed(self) -> None:
        """Commit a typed value through the shared parser."""
        from .typed_entry import parse_typed_entry
        result = parse_typed_entry(self._value.text(),
                                   self.slider.minimum(),
                                   self.slider.maximum(),
                                   unit=self._unit)
        if not result.valid:
            self._value.mark_committed(
                self._format_value(self.slider.value()))
            return
        canonical = max(self.slider.minimum(), min(
            self.slider.maximum(), int(round(result.value))))
        self.set_value(canonical)
        self._value.mark_committed(self._format_value(canonical))
        if result.was_clamped:
            _flash_clamped(self._value)

    def value(self) -> int:
        return self.slider.value()

    def set_value(self, value: int) -> None:
        self.slider.setValue(value)
        # setValue with blocked signals (reset/sync paths) skips
        # valueChanged, so refresh the readout explicitly.
        self._value.mark_committed(self._format_value(value))

    def set_caption(self, text: str) -> None:
        """Change the row's label, keeping the caption width stable.

        The label sits in the layout before the slider, so the caption is
        elided to a fixed width rather than allowed to grow: the rows already
        sum to more than the docker's natural width (dot + label + slider +
        margins), and widening the label widened the whole panel to 433px. A
        stable column means the slider handle does not shift when you switch
        targets.
        """
        metrics = QFontMetricsF(self._label.font())
        elided = metrics.elidedText(text, Qt.ElideRight, self.LABEL_WIDTH)
        self._label.setText(elided)
        _set_tooltip(self._label, text)
        self._label.setMinimumWidth(self.LABEL_WIDTH)

    #: Caption column width. The rows are dot(5) + label + slider(120 min) +
    #: margins(20) + spacing(20), and the whole thing has to fit inside the
    #: docker's natural 281px. Anything above ~100 here widens the panel, which
    #: is why the caption is short ("Hue (high)") and elided rather than spelled
    #: out. Verified: with no minimum at all the panel is 281px, so this value
    #: is what holds it there.
    LABEL_WIDTH = 96


class ToolButton(QPushButton):
    """A small icon button.

    Unlike the first cut of this control, which only ever painted a coloured
    dot, each button now draws a real glyph (``gear``, ``eyedropper``,
    ``sparkle``, ``layers``, ``grid``, ``target``) so the header reads as
    gear / swatch / colour-picker and the bottom row matches the reference's
    icon tab bar.
    """

    GLYPHS = ("gear", "eyedropper", "sparkle", "layers", "grid", "target",
              "point", "sun", "spot", "area", "gloss")

    def __init__(self, accent: Accent = Accent.NEUTRAL, checked: bool = False,
                 glyph: str = "target", size: int = 30, checkable: bool = True,
                 parent=None):
        super().__init__(parent)
        self.setCheckable(checkable)
        self.setChecked(checked)
        self.accent = accent
        self.glyph = glyph
        self.glyph_scale = 1.0      # draw the glyph larger within the button
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFlat(True)
        self.setStyleSheet("QPushButton { background: transparent; border: none; }")

    # -- glyph painters -------------------------------------------------
    def _draw_gear(self, p, cx, cy, col):
        pen = QPen(col, 1.5)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        for k in range(8):
            a = math.radians(k * 45.0)
            p.drawLine(
                QPointF(cx + math.cos(a) * 4.0, cy + math.sin(a) * 4.0),
                QPointF(cx + math.cos(a) * 6.6, cy + math.sin(a) * 6.6),
            )
        p.drawEllipse(QPointF(cx, cy), 3.6, 3.6)

    # Krita's own icon for a glyph, where it has one: the eyedropper is
    # Krita's Color Sampler tool icon, so the button looks like the tool it
    # switches to. Drawn glyphs remain the fallback outside Krita.
    KRITA_ICONS = {"eyedropper": "krita_tool_color_sampler"}
    _icon_cache = {}

    @classmethod
    def _krita_glyph(cls, glyph, size, col):
        """Krita's icon for ``glyph``, tinted ``col``, or None."""
        name = cls.KRITA_ICONS.get(glyph)
        if name is None:
            return None
        key = (name, size, col.rgba())
        if key not in cls._icon_cache:
            img = None
            try:
                from krita import Krita
                icon = Krita.instance().icon(name)
                if not icon.isNull():
                    img = icon.pixmap(size, size).toImage().convertToFormat(
                        QImage.Format_ARGB32_Premultiplied)
                    # One ink, like the drawn glyphs: keep the icon's shape
                    # (alpha), take the button's colour.
                    tint = QPainter(img)
                    tint.setCompositionMode(QPainter.CompositionMode_SourceIn)
                    tint.fillRect(img.rect(), col)
                    tint.end()
            except Exception:
                img = None
            cls._icon_cache[key] = img
        return cls._icon_cache[key]

    def _draw_eyedropper(self, p, cx, cy, col):
        img = self._krita_glyph("eyedropper", 18, col)
        if img is not None:
            p.drawImage(QPointF(cx - img.width() / 2.0, cy - img.height() / 2.0), img)
            return
        pen = QPen(col, 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        # Bulb
        p.drawEllipse(QPointF(cx + 2.6, cy - 3.2), 2.6, 2.6)
        # Tapered barrel down to the tip
        p.drawPolyline(QPointF(cx + 1.2, cy - 1.4), QPointF(cx - 1.4, cy + 2.0),
                       QPointF(cx - 4.4, cy + 5.2))

    def _draw_sparkle(self, p, cx, cy, col):
        pen = QPen(col, 1.5)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPolyline(QPointF(cx, cy - 5.5), QPointF(cx, cy + 5.5))
        p.drawPolyline(QPointF(cx - 5.5, cy), QPointF(cx + 5.5, cy))
        p.drawPolyline(QPointF(cx - 3.6, cy - 3.6), QPointF(cx + 3.6, cy + 3.6))
        p.drawPolyline(QPointF(cx - 3.6, cy + 3.6), QPointF(cx + 3.6, cy - 3.6))

    def _draw_layers(self, p, cx, cy, col):
        pen = QPen(col, 1.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPolyline(QPointF(cx - 5.0, cy + 1.4), QPointF(cx, cy - 1.6),
                       QPointF(cx + 5.0, cy + 1.4), QPointF(cx, cy + 4.4),
                       QPointF(cx - 5.0, cy + 1.4))
        p.drawPolyline(QPointF(cx - 5.0, cy - 1.6), QPointF(cx, cy - 4.6),
                       QPointF(cx + 5.0, cy - 1.6))

    def _draw_grid(self, p, cx, cy, col):
        pen = QPen(col, 1.3)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        for dx in (-2.6, 2.6):
            for dy in (-2.6, 2.6):
                p.drawRect(QRectF(cx + dx - 1.9, cy + dy - 1.9, 3.8, 3.8))

    def _draw_target(self, p, cx, cy, col):
        pen = QPen(col, 1.5)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), 5.4, 5.4)
        p.setBrush(col)
        p.drawEllipse(QPointF(cx, cy), 1.9, 1.9)

    def _draw_moon(self, p, cx, cy, col):
        """Crescent, for the low-key Nocturne preset."""
        pen = QPen(col, 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        # Shifted right so the crescent's painted mass sits on center: the
        # visible shape spans ox-R..ox+d/2, i.e. its middle is ~2px left of
        # the outer circle, so the outer circle is pushed right to compensate.
        R, d, off = 5.4, 2.8, -2.0
        ox, ix = cx - off, cx - off + d
        h = math.sqrt(R * R - (d / 2.0) ** 2)
        a = math.degrees(math.atan2(h, d / 2.0))
        pts = []
        steps = 24
        for k in range(steps + 1):
            t = math.radians(-a + (-(360.0 - 2 * a)) * k / steps)
            pts.append(QPointF(ox + R * math.cos(t), cy + R * math.sin(t)))
        for k in range(steps + 1):
            t = math.radians((180.0 - a) + (2 * a) * k / steps)
            pts.append(QPointF(ix + R * math.cos(t), cy + R * math.sin(t)))
        p.drawPolyline(QPolygonF(pts))

    def _draw_sun(self, p, cx, cy, col):
        """Ring with rays, for the tight high-energy Gloss preset."""
        pen = QPen(col, 1.4, Qt.SolidLine, Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), 3.2, 3.2)
        for k in range(8):
            a = math.radians(k * 45.0)
            p.drawLine(QPointF(cx + math.cos(a) * 5.0, cy + math.sin(a) * 5.0),
                       QPointF(cx + math.cos(a) * 6.6, cy + math.sin(a) * 6.6))

    def _draw_disc(self, p, cx, cy, col):
        """Plain even disc, for the flat Matte preset."""
        pen = QPen(col, 1.5)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), 5.2, 5.2)
        # Faint inner ring: a hint of a soft, structureless surface, as opposed
        # to the hard-edged single ring _draw_target uses.
        p.setPen(QPen(col, 1.0))
        p.drawEllipse(QPointF(cx, cy), 2.4, 2.4)

    def _draw_bolt(self, p, cx, cy, col):
        """Lightning bolt, for the blooming Neon preset."""
        pen = QPen(col, 1.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPolyline(QPolygonF([
            QPointF(cx + 1.4, cy - 6.0), QPointF(cx - 2.8, cy + 0.4),
            QPointF(cx + 0.2, cy + 0.4), QPointF(cx - 1.2, cy + 6.0),
            QPointF(cx + 3.0, cy - 0.6), QPointF(cx - 0.1, cy - 0.6),
        ]))

    def _draw_point(self, p, cx, cy, col):
        """Nearby omni lamp: filled bulb with a soft halo."""
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawEllipse(QPointF(cx, cy), 2.2, 2.2)
        p.setPen(QPen(col, 1.3))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), 5.4, 5.4)

    def _draw_spot(self, p, cx, cy, col):
        """Cone beam: apex dot widening into a soft-edged cone."""
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawEllipse(QPointF(cx, cy - 5.2), 1.6, 1.6)
        pen = QPen(col, 1.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPolyline(QPointF(cx, cy - 3.4), QPointF(cx - 4.6, cy + 5.4),
                       QPointF(cx + 4.6, cy + 5.4), QPointF(cx, cy - 3.4))

    def _draw_area(self, p, cx, cy, col):
        """Flat emitting panel with parallel rays below it."""
        pen = QPen(col, 1.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(cx - 5.5, cy - 5.5, 11.0, 4.2))
        for dx in (-3.6, 0.0, 3.6):
            p.drawLine(QPointF(cx + dx, cy + 0.6), QPointF(cx + dx, cy + 5.4))

    def _draw_undo(self, p, cx, cy, col, mirror=1.0):
        """A curved arrow turning back to the left (mirror=-1: to the right)."""
        p.setPen(QPen(col, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        m = mirror
        path = QPainterPath(QPointF(cx + m * 5.0, cy + 4.5))
        path.cubicTo(QPointF(cx + m * 5.5, cy - 3.0), QPointF(cx - m * 1.0, cy - 4.5),
                     QPointF(cx - m * 5.0, cy - 1.5))
        p.drawPath(path)
        p.drawLine(QPointF(cx - m * 5.0, cy - 1.5), QPointF(cx - m * 4.2, cy - 5.6))
        p.drawLine(QPointF(cx - m * 5.0, cy - 1.5), QPointF(cx - m * 1.0, cy - 0.6))

    def _draw_redo(self, p, cx, cy, col):
        self._draw_undo(p, cx, cy, col, mirror=-1.0)

    def _draw_gloss(self, p, cx, cy, col):
        """Specular streak: diagonal slash with a hotspot dot, for Gloss."""
        p.setPen(QPen(col, 2.6, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(cx - 4.5, cy + 4.5), QPointF(cx + 2.5, cy - 2.5))
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawEllipse(QPointF(cx + 4.2, cy - 4.2), 1.8, 1.8)

    def paintEvent(self, event):
        try:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing, True)
            w, h = self.width(), self.height()
            cx, cy = w / 2.0, h / 2.0
            on = self.isChecked()

            # The active tab gets a light rounded plate, as in the reference.
            if on:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(232, 236, 243))
                p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 7, 7)
                col = QColor(38, 42, 52)
            else:
                col = QColor(168, 178, 196)

            fn = {
                "gear": self._draw_gear,
                "eyedropper": self._draw_eyedropper,
                "sparkle": self._draw_sparkle,
                "layers": self._draw_layers,
                "grid": self._draw_grid,
                "target": self._draw_target,
                "moon": self._draw_moon,
                "sun": self._draw_sun,
                "disc": self._draw_disc,
                "bolt": self._draw_bolt,
                "point": self._draw_point,
                "spot": self._draw_spot,
                "area": self._draw_area,
                "gloss": self._draw_gloss,
                "undo": self._draw_undo,
                "redo": self._draw_redo,
            }.get(self.glyph, self._draw_target)
            if not self.isEnabled():
                col = QColor(col)
                col.setAlpha(70)
            if self.glyph_scale != 1.0:
                p.translate(cx, cy)
                p.scale(self.glyph_scale, self.glyph_scale)
                p.translate(-cx, -cy)
            fn(p, cx, cy, col)
            p.end()
        except Exception as exc:  # pragma: no cover - cosmetic paint only
            print(f"Lumina: ToolButton.paintEvent failed - {exc}")


# ---------------------------------------------------------------------------
# Title-bar drag filter
# ---------------------------------------------------------------------------
try:
    from PyQt5 import sip as _sip
except ImportError:  # pragma: no cover - very old PyQt
    _sip = None


def _is_dead(obj) -> bool:
    """True if ``obj``'s underlying C++ object has already been destroyed.

    A PyQt wrapper can outlive the C++ object it wraps: ``deleteLater()`` frees
    the widget while Python still holds a reference to the wrapper, and every
    method call on it then dereferences freed memory. ``sip.isdeleted`` is the
    only reliable way to tell the two apart.
    """
    if _sip is None:
        return False
    try:
        return bool(_sip.isdeleted(obj))
    except Exception:  # pragma: no cover - be permissive, never crash here
        return False


class _TitleBarDragFilter(QObject):
    """Long-lived event filter that reaches the docker through a weakref.

    The docker needs a filter on the *application*, so it can still see mouse
    events after the dock is torn off and the title-bar widget is replaced. That
    makes the docker itself the filter object, and Qt stores filters keyed on the
    C++ pointer with no way to notice the object died: if the docker is
    destroyed without the filter being removed, the next mouse event makes Qt
    call a vtable on freed memory. That is a segfault with no traceback and no
    Python exception, and it takes Krita with it.

    ``closeEvent`` and ``__del__`` are both too late to prevent it -- by the
    time either runs, Qt has already destroyed the C++ object. So the filter
    object is this singleton instead: it is parented to the QApplication, so it
    cannot die while the app does, and it holds only a weak reference to the
    docker.

    A weakref alone is not sufficient, though, and this is the subtle part: the
    Python wrapper can outlive the C++ object. ``deleteLater()`` destroys the
    C++ widget while Python still holds a reference to the wrapper, so the
    weakref resolves to a perfectly valid-looking object whose underlying widget
    no longer exists. Calling a method on it dereferences freed memory. So the
    filter checks :func:`sip.isdeleted` as well, and treats a deleted C++ object
    exactly like a collected one.
    """

    _instance = None

    @classmethod
    def instance(cls):
        if cls._instance is None:
            app = QApplication.instance()
            cls._instance = cls(app)   # parented to the app: outlives every docker
        return cls._instance

    def __init__(self, parent=None):
        super().__init__(parent)
        self._docker = None   # weakref, or None when detached

    def attach(self, docker) -> None:
        self._docker = weakref.ref(docker)

    def detach(self) -> None:
        self._docker = None

    def eventFilter(self, obj, event):
        ref = self._docker
        if ref is None:
            return False
        docker = ref()
        if docker is None or _is_dead(docker):
            # The docker is gone, or its C++ widget is gone while Python still
            # holds the wrapper. Returning False is the only safe answer.
            self._docker = None
            return False
        return docker._handle_titlebar_event(obj, event)


class _CanvasPickFilter(QObject):
    """Watches Krita's canvas for the release that ends a colour pick.

    Lumina otherwise only learns about a pick when Krita's colour *changes*.
    Picking the colour Krita already holds -- re-picking the base after
    switching to the shade, say -- changes nothing, so the pick was lost.
    The release of a Color Sampler (or Ctrl quick-sample) click is the pick
    itself, changed colour or not.

    Same lifetime rules as :class:`_TitleBarDragFilter`: a singleton parented
    to the application, reaching the docker only through a weakref, and only
    ever observing -- it never consumes an event, so painting is untouched.
    """

    _instance = None
    CANVAS_CLASSES = ("KisOpenGLCanvas2", "KisQPainterCanvas")

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls(QApplication.instance())
        return cls._instance

    def __init__(self, parent=None):
        super().__init__(parent)
        self._docker = None
        self._watched = weakref.WeakSet()

    def attach(self, docker) -> None:
        self._docker = weakref.ref(docker)

    def detach(self) -> None:
        self._docker = None

    def watch(self, root) -> int:
        """Install on every canvas widget under ``root``; returns how many
        were new. Canvases come and go with views, so this is re-run."""
        added = 0
        for widget in root.findChildren(QWidget):
            try:
                if widget.metaObject().className() in self.CANVAS_CLASSES \
                        and widget not in self._watched:
                    widget.installEventFilter(self)
                    self._watched.add(widget)
                    added += 1
            except Exception:  # pragma: no cover - never break the host
                continue
        return added

    def eventFilter(self, obj, event):
        try:
            if event.type() in (QEvent.MouseButtonRelease, QEvent.TabletRelease):
                ref = self._docker
                docker = ref() if ref is not None else None
                if docker is not None and not _is_dead(docker):
                    docker._on_canvas_release(event)
        except Exception:  # pragma: no cover - observing must never break input
            pass
        return False


# ---------------------------------------------------------------------------
# SphereDocker — the main V2 docker
# ---------------------------------------------------------------------------
class MixerRow(QWidget):
    """Segmented control for the light mixer: Additive / Multiplicative / Blended.

    The engine has always supported these three modes but nothing in the UI ever
    exposed them, so the mixer was unreachable.
    """

    MODES = ("Blended", "Additive", "Multiplicative")
    CAPTION = "Mixer"
    TIPS = {
        "Blended": "Natural balance of shadow, base and light",
        "Additive": "Brighter: adds light energy, lifts the shadows",
        "Multiplicative": "Deeper: multiplies light energy, richer darks",
    }

    def __init__(self, current: str = "Blended", parent=None):
        super().__init__(parent)
        self._buttons = {}
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(6)

        dot = QFrame()
        dot.setFixedSize(4, 12)
        dot.setStyleSheet("background-color: %s; border-radius: 2px;"
                          % Accent.NEUTRAL.name())
        row.addWidget(dot)

        cap = QLabel(self.CAPTION)
        cap.setFixedWidth(52)
        cap.setStyleSheet("QLabel { color: %s; font-size: 10px; }" % TEXT_DIM.name())
        row.addWidget(cap)

        for mode in self.MODES:
            btn = QPushButton(mode)
            btn.setCheckable(True)
            btn.setChecked(mode == current)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFocusPolicy(Qt.StrongFocus)
            btn.setStyleSheet(self._style(mode == current))
            _set_tooltip(btn, self.TIPS.get(mode, mode))
            btn.clicked.connect(lambda _c, m=mode: self._on_click(m))
            self._buttons[mode] = btn
            row.addWidget(btn, 1)
        self._current = current

    @staticmethod
    def _style(on: bool) -> str:
        if on:
            return ("QPushButton { background-color: #2f3b4d; color: #e7eaf2; "
                    "border: 1px solid rgba(255,255,255,120); border-radius: 5px; "
                    "font-size: 9px; padding: 2px 0; }")
        return ("QPushButton { background-color: #1b1f27; color: #8e97a8; "
                "border: 1px solid rgba(255,255,255,40); border-radius: 5px; "
                "font-size: 9px; padding: 2px 0; }")

    def mode(self) -> str:
        return self._current

    def set_mode(self, mode: str) -> None:
        self._current = mode
        for m, btn in self._buttons.items():
            btn.setChecked(m == mode)
            btn.setStyleSheet(self._style(m == mode))

    def _on_click(self, mode: str) -> None:
        self.set_mode(mode)
        cb = getattr(self, "_on_mode_changed", None)
        if cb is not None:
            cb(mode)


class LightTypeIconRow(QWidget):
    """Lamp model selector: Point / Sun / Spot / Area as icon buttons.

    Lives in a row directly above the sphere, built like the preset cells
    (glyph button + caption), since the lamp model visibly reshapes the sphere.
    Same interface as the old segmented row: ``mode()``, ``set_mode()`` and
    the ``_on_mode_changed`` callback, so persistence and reset keep working.
    """

    MODES = ("Point", "Sun", "Spot", "Area")
    GLYPHS = {"Point": "point", "Sun": "sun", "Spot": "spot", "Area": "area"}
    TIPS = {
        "Point": "Point: nearby lamp, brightness falls off with distance",
        "Sun": "Sun: distant parallel light, no falloff",
        "Spot": "Spot: cone beam with a soft edge",
        "Area": "Area: broad panel, soft wrap",
    }

    def __init__(self, current: str = "Point", parent=None):
        super().__init__(parent)
        self._buttons = {}
        self.captions = []          # hidden in compact mode
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        row.addStretch(1)
        for mode in self.MODES:
            cell = QWidget()
            cv = QVBoxLayout(cell)
            cv.setContentsMargins(0, 0, 0, 0)
            cv.setSpacing(1)
            btn = ToolButton(Accent.NEUTRAL, checked=(mode == current),
                             glyph=self.GLYPHS[mode])
            _set_tooltip(btn, self.TIPS[mode])
            btn.clicked.connect(lambda _c, m=mode: self._on_click(m))
            cv.addWidget(btn, alignment=Qt.AlignCenter)
            cap = QLabel(mode)
            cap.setAlignment(Qt.AlignCenter)
            cap.setStyleSheet(CAPTION_QSS)
            cv.addWidget(cap)
            self.captions.append(cap)
            row.addWidget(cell, alignment=Qt.AlignCenter)
            self._buttons[mode] = btn
        row.addStretch(1)
        self._current = current

    def mode(self) -> str:
        return self._current

    def set_mode(self, mode: str) -> None:
        if mode not in self.MODES:
            return
        self._current = mode
        for m, btn in self._buttons.items():
            btn.setChecked(m == mode)

    def _on_click(self, mode: str) -> None:
        self.set_mode(mode)
        cb = getattr(self, "_on_mode_changed", None)
        if cb is not None:
            cb(mode)


class SphereDocker(DockWidget):
    """The Lumina docker with a compact, icon-driven vertical sidebar.

    The panel follows the Infinite Painter "Lighting Sphere" layout (see issue #9):

        header   [gear]  [base swatch, light|dark]  [eyedropper]
        sphere      large full circle with a draggable picker pointer
        targets  shadow / base / light dots (active one ringed)
        sliders  Hue (rainbow) / Saturation (grey->hue) / Value (dark->light)
                 / Contrast (plain)
        advanced collapsible: base level, intensity, ambient, specular, glow, tone
        tools    artistic / real-world / pick-from-document / apply-to-foreground
    """

    # Editable lighting targets. The sphere always renders base + light + shadow
    # together; the Hue / Saturation / Value sliders edit whichever target is
    # active, so the base color can be changed without disturbing the light and
    # shadow colors (per the reference manual).
    TARGET_ORDER = ("shadow", "base", "light")
    DEFAULT_TARGETS = {
        "shadow": QColor(62, 66, 96),     # cool bounce: shadows are rarely black
        "base":   QColor(205, 92, 92),    # the object itself
        "light":  QColor(157, 65, 102),   # key light
    }
    TARGET_ACCENT = {
        "shadow": QColor(120, 140, 205),
        "base":   QColor(205, 92, 92),
        "light":  QColor(248, 196, 72),
    }
    TARGET_LABELS = {
        "shadow": "Shadow color (hue of the darkest areas)",
        "base":   "Base color (the color of the object)",
        "light":  "Light color (highlight; also controls brightness of light and shadow)",
    }
    # Short captions under the target row; the same words as EDIT SHADE /
    # EDIT BASE / EDIT HIGH.
    TARGET_CAPTIONS = {"shadow": "Shade", "base": "Base", "light": "High"}

    def _install_tooltip_style(self) -> None:
        """Give tooltips a readable background and text colour (issue #20).

        Five approaches all failed before, and the reason is worth writing
        down so none of them is retried:

        * ``QApplication.setStyleSheet()`` replaces Krita's *entire* app
          stylesheet. Invasive, and it still did not style these tooltips.
        * A ``QToolTip`` rule on the docker's own stylesheet had no visible
          effect either -- the rule was present on the widget and the tooltip
          rendered transparent regardless.
        * A palette set on the docker is likewise ignored, because a tooltip is
          its own top-level window and does not inherit the owner's palette.
        * Setting the two palette roles on QApplication *did* take -- they read
          back as #000000/#ffffff -- but the tooltip still rendered with no
          background. Krita's style paints tooltip windows itself rather than
          filling from those roles.
        * Appending a ``QToolTip`` rule to the application's *existing*
          stylesheet (marker ``/* Lumina tooltips */``) was verified present
          and still changed nothing.

        The fix that survives the style is per-tooltip rich text: every
        ``setToolTip`` call goes through ``tooltip.set_tooltip``, which wraps
        the text in a ``<div style='background-color:#000;color:#fff'>``. The
        div background paints inside whatever box Qt draws, so it stays opaque
        even when the outer ``QTipLabel`` does not. This method now only keeps
        the palette + appended-rule passes as a fallback for any plain-text
        tooltip that slips through.

        Timing matters more than the call itself: this runs from __init__,
        while Krita is still starting up, and Krita re-applies its own theme
        afterwards, wiping the two roles back to its values. So the style is
        applied twice -- once now, and once on a short single-shot timer that
        fires after startup has settled. Measured: without the deferred pass
        the roles read back as Krita's #363636/#9ca2ae.
        """
        self._apply_tooltip_palette()
        QTimer.singleShot(4000, self._apply_tooltip_palette)

    @staticmethod
    def _apply_tooltip_palette() -> None:
        """Black tooltip background, white text. See above for why."""
        app = QApplication.instance()
        if app is None:  # pragma: no cover - no application, nothing to do
            return
        pal = QPalette(app.palette())
        pal.setColor(QPalette.ToolTipBase, QColor(0, 0, 0))
        pal.setColor(QPalette.ToolTipText, QColor(255, 255, 255))
        app.setPalette(pal)
        # The palette alone does not render: Krita's style paints tooltip
        # windows itself. So a QToolTip rule is appended to the application's
        # existing stylesheet -- Krita's rules stay, ours wins by order.
        marker = "/* Lumina tooltips */"
        current = app.styleSheet() or ""
        if marker not in current:
            app.setStyleSheet(current + marker +
                "QToolTip { background-color: %s; color: %s; "
                "border: 1px solid %s; padding: 0px; }"
                % (TOOLTIP_BG, TOOLTIP_FG, TOOLTIP_BORDER))

    def __init__(self):
        LOG.info("SphereDocker.__init__ START")
        super().__init__()
        self._install_tooltip_style()
        self.setWindowTitle("Lumina")
        LOG.info("base widget created")

        # --- Editable lighting targets (shadow / base / light) ---
        self._targets = {k: QColor(v) for k, v in self.DEFAULT_TARGETS.items()}
        # Left block of the split swatch: the committed "original" colour.
        # Seeded from the default base so it is never an empty placeholder.
        self._original_color = QColor(self.DEFAULT_TARGETS["base"])
        # Colour chosen on the sphere to draw with; shown in the active swatch.
        self._chosen_color = None
        self._chosen_color = None    # colour picked on the sphere, shown in the active swatch
        # QColor.getHsvF() reports hue 0 for greys, so remember the last
        # meaningful hue per target and fall back to it when saturation is ~0.
        self._hue_memory = {k: 0.0 for k in self._targets}
        # Hue of the colour currently being distributed from. Black has no hue
        # of its own and Qt reports -1 for it, so _hsv_of borrows this instead
        # of resurrecting the previously picked colour's hue.
        self._picked_hue = None
        self._active_target = "base"
        self._syncing = False
        self._settings_restored = False  # set when _load_settings finds saved state
        self._sync_guard = False       # blocks our own foreground echo
        self._awaiting_sampler = False # next canvas sample comes from the eyedropper
        # Hex labels under the swatches: persisted display setting, on default.
        self._show_hex = True
        # Slider value that produced the current base color. The Base Level
        # row scales *relatively* (new/old), so this is the anchor that makes
        # a 100->70 drag telescope to exactly x0.70.
        self._base_level_last = 100
        # Hue/saturation of the last non-black base. Scaling to exactly zero
        # destroys hue information (black x factor = black), so raising the
        # slider again rebuilds from this memory instead of staying black.
        self._base_level_hs = None
        self._sampler_timer = None
        self._sampler_baseline = None
        # The colour space the target numbers are written in: the active
        # layer's, as in Krita's own selectors. See _sync_working_space.
        self._space_profile = SRGB_PROFILE
        self._sampler_elapsed = 0
        self._sampler_prev_tool = None
        self._sphere_render_size = SPHERE_RENDER   # changed by the settings panel
        self._slider_dragging = False        # True while a slider is held down

        # --- Shading engine (pure Python, no Qt) + Krita-facing processor ---
        self.processor = SphereColorProcessor(resolution=SPHERE_RENDER)
        LOG.info("processor ready")

        # --- Interactive sphere surface ---
        self._sphere = SphereWidget()
        self._sphere.set_hover_callback(self._on_sphere_hover)
        self._sphere.set_brush_callback(self._on_sphere_brush)
        self._sphere.set_target_callback(self._on_sphere_target)
        LOG.info("sphere created + pick/hover wired")

        # Colour sampling ring drawn on top of the sphere. It is a child of the sphere
        # so it can be positioned in the sphere's own coordinates and follow the
        # pointer across the surface.
        self.cylinder = ColorSampler()
        self._sphere.set_preview_widget(self.cylinder)
        self.cylinder.hide()

        # Coalescer for sphere rebuilds. Created before the UI is built because
        # syncing slider values can fire valueChanged handlers that call
        # _rebuild_sphere().
        # Target history: a step is recorded once the targets have held still
        # for HISTORY_SETTLE_MS, so one slider drag is one undo step.
        self._hist_undo, self._hist_redo = [], []
        self._hist_last = None
        self._hist_restoring = False
        self._hist_timer = QTimer(self)
        self._hist_timer.setSingleShot(True)
        self._hist_timer.setInterval(600)
        self._hist_timer.timeout.connect(self._hist_commit)
        # A sphere pick lands in Recent picks once the drag settles, so one
        # drag adds one colour, not every pixel crossed.
        self._recent_pending = None
        self._recent_timer = QTimer(self)
        self._recent_timer.setSingleShot(True)
        self._recent_timer.setInterval(400)
        self._recent_timer.timeout.connect(self._commit_recent)
        # Full renders off the UI thread inside Krita (see
        # _submit_full_render); tests and the docs renderer draw in step.
        self._async_full_render = Krita is not None
        self._rebuild_timer = QTimer(self)
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.setInterval(0)
        self._rebuild_timer.timeout.connect(self._rebuild_sphere_now)

        # External foreground-color pipeline (native picker drags, palette
        # picks): throttled previews while the stream is live, exactly one
        # full-quality final at declared stream end, so the sphere tracks at
        # ~20 FPS with zero blocking renders mid-drag (Run-2).
        self._ext_render_timer = QTimer(self)
        self._ext_render_timer.setSingleShot(True)
        self._ext_render_timer.setInterval(EXT_RENDER_MS)
        self._ext_render_timer.timeout.connect(self._apply_external_color)
        self._latest_external = None
        self._last_applied_external = None
        self._ext_render_pending = False
        self._ext_unchanged = 0
        # Render attribution (Q1): trigger/size/cost of the last executed
        # render, consumed by _rebuild_sphere_now for the RENDER_DONE line.
        self._pending_reason = None
        self._last_render_size = -1
        self._last_render_smooth = -1.0
        # While an external stream is in flight we render the sphere like a slider
        # drag: smaller grid, blur off, throttled. The end detector restores
        # full quality with a single final render.
        self._external_preview = False
        self._ext_smooth_saved = None
        # Instrumentation for one instrumented native-picker drag. Read back
        # from lumina_log.txt to see whether renders-per-apply is 1 or worse,
        # and whether any single render spikes (avg hides hitches).
        self._ext_applies = 0
        self._ext_renders = 0
        self._ext_render_ms = 0.0
        self._ext_render_samples = []
        self._ext_final_pending = False

        # Persistence debounce: a drag fires dozens of changes but needs one
        # disk write. Explicit actions save immediately (see callers).
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_DEBOUNCE_MS)
        self._save_timer.timeout.connect(self._flush_settings)
        self._settings_dirty = False

        # --- Primary sliders: edit the active target + contrast ---
        # Row defaults are read from the engine rather than hardcoded. Four of
        # them were lying: the panel opened showing Contrast 50, Intensity 70
        # and Tone 50 while the engine actually sat at 1.0 for all three, so the
        # handle position did not describe the state being rendered and the
        # first drag produced a large jump from an unrelated starting point.
        _eng = self.processor.engine
        self.hue_row = LabeledSliderRow(Accent.NEUTRAL, "Hue", 0, hi=359,
                                        unit="degree")
        self.saturation_row = LabeledSliderRow(Accent.NEUTRAL, "Saturation", 0)
        # Light (HSV value) belongs to the base target only. The shadow and
        # light targets deliberately keep just Hue and Saturation: they are
        # derived from the base, and giving them an independent brightness
        # control lets you dial a highlight down to nothing, which is not a
        # lighting decision worth exposing. Shown/hidden in _sync_sliders_from_state.
        self.light_row = LabeledSliderRow(Accent.NEUTRAL, "Light", 0)
        self.contrast_row = LabeledSliderRow(Accent.NEUTRAL, "Contrast",
                                             int(round(_eng.contrast * 100)),
                                             hi=200)
        # Primary Intensity: the scene light level (all lamp energy scales
        # with it), front and centre instead of buried in Advanced. It owns
        # the same engine value as the Advanced Intensity row; the two mirror
        # each other, so a preset, reset or load updates both and either
        # handle drives both. Both are called Intensity -- there is only one
        # light_intensity, not a separate photographic exposure stage.
        self.power_row = LabeledSliderRow(Accent.AMBER, "Intensity",
                                          int(round(_eng.light_intensity * 100)),
                                          hi=200)
        _set_tooltip(self.hue_row, "Hue of the active target color")
        _set_tooltip(self.saturation_row, "Saturation of the active target color")
        _set_tooltip(self.light_row, "Brightness of the base target color")
        _set_tooltip(self.contrast_row, "Sharpness of the light-to-shadow falloff (100 is neutral)")
        _set_tooltip(self.power_row, "Scene light level: scales Sun, Point, Spot and Area together (mirrors Advanced Intensity)")
        LOG.info("4 primary slider rows created")

        # --- Advanced rows (collapsed by default) ---
        self.color_row = LabeledSliderRow(Accent.RED, "Base Level", 100)
        self.intensity_row = LabeledSliderRow(Accent.AMBER, "Intensity",
                                              int(round(_eng.light_intensity * 100)))
        self.ambient_row = LabeledSliderRow(Accent.PURPLE, "Ambient",
                                            int(round(_eng.ambient * 100)))
        # Specular passes its value straight to set_shininess, which clamps to
        # 1..128, so the row spans exactly that.
        self.specular_row = LabeledSliderRow(Accent.CYAN, "Specular",
                                             int(round(_eng.shininess)), lo=1, hi=64,
                                             unit="none")
        # Diffuse is the other half of the highlight: Specular sets how wide the
        # sheen is, Diffuse how softly it falls off. They sit together so the
        # relationship is obvious rather than split across two panels.
        self.diffuse_row = LabeledSliderRow(Accent.CYAN, "Diffuse",
                                            self._knee_to_level(SPEC_KNEE))
        self.glow_row = LabeledSliderRow(Accent.CYAN, "Glow", 0)
        self.tone_row = LabeledSliderRow(Accent.GREEN, "Tone",
                                         int(round(_eng.saturation * 100)),
                                         hi=200)
        # Environment accents (rim strength/sky mix, sky/ground bounce).
        # Kept in Advanced, not the main panel: they shape the lighting
        # model rather than the color being picked.
        self.rim_row = LabeledSliderRow(Accent.PURPLE, "Rim", 10)
        self.rim_mix_row = LabeledSliderRow(Accent.PURPLE, "Rim Tint", 60)
        self.sky_row = LabeledSliderRow(Accent.BLUE, "Sky", 2)
        self.ground_row = LabeledSliderRow(Accent.AMBER, "Ground", 1)
        _set_tooltip(self.rim_row, "Rim edge strength (accent, not a second key light)")
        _set_tooltip(self.rim_mix_row, "Rim tint: key color to sky color")
        _set_tooltip(self.sky_row, "Sky bounce from above in the shadows")
        _set_tooltip(self.ground_row, "Ground bounce from below in the shadows")
        _set_tooltip(self.color_row, "Overall brightness of the base color, preserves hue and saturation")
        _set_tooltip(self.intensity_row, "Scene light level (mirrors the primary Intensity row)")
        _set_tooltip(self.ambient_row, "Fill light softening the shadowed areas")
        _set_tooltip(self.specular_row, "Highlight width: low is broad, high is tight")
        _set_tooltip(self.diffuse_row, "Highlight falloff softness: high spreads the sheen")
        _set_tooltip(self.glow_row, "Bloom on the lit areas")
        _set_tooltip(self.tone_row, "Global saturation of the rendered result")
        # These four were never given an explicit range, so they sat on Qt's
        # default 0-99. Anything asking for 100 was silently clamped to 99 by
        # the widget, and the resulting valueChanged overwrote the engine value
        # the caller had just set -- so "Intensity 100" was unreachable and the
        # presets silently rendered at 0.99.

        self.mixer_row = MixerRow(self.processor.engine.mixer_mode)
        self.mixer_row._on_mode_changed = self._on_mixer_changed
        self.light_type_row = LightTypeIconRow(self.processor.engine.light_type)
        self.light_type_row._on_mode_changed = self._on_light_type_changed
        LOG.info("6 advanced slider rows + mixer created")

        # --- Preset buttons -------------------------------------------------
        # Built from the _PRESETS table rather than declared one by one. The
        # engine params each one drives live in the same dict, so a preset can
        # never be added to the table and forgotten in the handler.
        #
        # "Apply" is deliberately not here: it pushes the base colour to
        # Krita's foreground so you can paint with it, which is a one-off action
        # and has no business sharing a row with the looks.
        self._preset_btns = []
        self._applying_preset = False
        # Presets toggle: the lit one turns off again on a second click and
        # restores the lighting from before any preset was switched on.
        self._active_preset = None
        self._preset_before = None
        for i, preset in enumerate(_PRESETS):
            btn = ToolButton(preset["accent"], checked=False,
                             glyph=preset["glyph"])
            btn.key = preset["key"]
            _set_tooltip(btn, preset["tip"] + " (click again to turn off)")
            btn.clicked.connect(
                lambda _c, k=preset["key"]: self._on_preset_clicked(k, True))
            self._preset_btns.append(btn)
        # Kept as attributes: the header/legacy code paths and tests reference
        # the first two by name.
        self.artistic_btn = self._preset_btns[0]
        self.realworld_btn = self._preset_btns[1]
        self._tool_labels = {p["key"]: p["label"] for p in _PRESETS}
        LOG.info("%d preset buttons created", len(self._preset_btns))

        self._build_ui()
        self._install_titlebar_drag()
        LOG.info("_build_ui done")
        self._connect_rows()
        LOG.info("_connect_rows done")
        self._krita_connections = []
        self._watchers_armed = False   # guards duplicate signal connections
        self._watch_krita_colors()
        LOG.info("external color sync armed (%d watcher(s))" % len(self._krita_connections))
        self._start_sampler_watch()
        if not getattr(self, "_settings_restored", False):
            # First run: harmonize the default trio from the base color
            # instead of three fixed swatches.
            self._distribute_from(QColor(self._targets["base"]))
        self._rebuild_sphere()
        LOG.info("SphereDocker.__init__ COMPLETE")
        # Build stamp for bug reports: version + the actual loaded file, so a
        # stale or half-reinstalled copy is visible in the log (Q3).
        try:
            LOG.info("Lumina plugin version=%s module=%s",
                     PLUGIN_VERSION, os.path.abspath(__file__))
        except Exception:  # pragma: no cover - UI only
            pass

    # ------------------------------------------------------------------
    # Target color state
    # ------------------------------------------------------------------
    @property
    def base_color(self) -> QColor:
        """Back-compat alias for the base target color."""
        return self._targets["base"]

    @base_color.setter
    def base_color(self, value: QColor) -> None:
        self._targets["base"] = QColor(value)

    # ------------------------------------------------------------------
    # Split color preview swatch: original (left) / active (right)
    # ------------------------------------------------------------------
    def _set_base_color(self, color: QColor) -> None:
        """Assign the base (active) color shown in the right swatch block."""
        self._targets["base"] = QColor(color)

    def _revert_to_original(self) -> None:
        """Left swatch: restore the committed original color."""
        try:
            if self._original_color is None:
                return
            self._set_base_color(self._original_color)
            self._rebuild_sphere()
            self._sync_sliders_from_state()
            self._update_preview()
        except Exception as exc:  # pragma: no cover - UI only
            LOG.exception("_revert_to_original failed")
            print(f"Lumina: revert failed - {exc}")

    def _commit_current(self) -> None:
        """Right swatch: commit the active color as the new original."""
        try:
            shown = self._targets.get(self._active_target, self._targets["base"])
            self._original_color = QColor(shown)
            self._update_preview()
        except Exception as exc:  # pragma: no cover - UI only
            LOG.exception("_commit_current failed")
            print(f"Lumina: commit failed - {exc}")

    # ------------------------------------------------------------------
    # Distributing a picked color across the three lighting targets
    # ------------------------------------------------------------------
    def _distribute_from(self, color: QColor) -> None:
        """Turn one sampled color into a coherent base / light / shadow set.

        This is the workflow the reference describes: you pick a color from
        your artwork and the tool works out the rest of the lighting around it
        instead of leaving you to set three swatches by hand.

        * **base**   - the sampled color itself (the object's local color).
        * **light**  - same hue, desaturated and pushed toward white, so the
          highlight reads as light rather than as a brighter paint.
        * **shadow** - hue rotated toward the cool side, saturated and darkened,
          because "the shadows in the real world are rarely black".
        """
        try:
            h = color.getHsvF()[0]
            base = QColor(color)

            # Light and shadow come from the OKLCH model in derivation.py,
            # fitted to the reference lighting app's own shadow / light for
            # seven bases (tools/reference_calibration.py): a fixed lightness
            # step either way, the light swinging toward a warm key and the
            # shadow leaning toward a cool ambient -- except for reds and
            # oranges, whose shadows stay warm. It keeps the invariants the
            # earlier HSV rule enforced: shadow darker and light lighter than
            # the base, greys derive greys, black derives black, and a dark
            # base still gets a separable shadow.
            shadow_rgb, light_rgb = derive_targets(
                (color.red(), color.green(), color.blue()))
            light = QColor(*light_rgb)
            shadow = QColor(*shadow_rgb)

            # Black has no hue and Qt reports -1 for it. Remember what we
            # distributed from so the sliders have something real to show
            # instead of the previous pick's hue.
            self._picked_hue = h if h >= 0.0 else 0.0

            def _hue_or_pick(c):
                ch, cs, cv, _a = c.getHsvF()
                return ch if (ch >= 0.0 and cs >= 0.005 and cv > 0.0) \
                    else self._picked_hue
            light_h = _hue_or_pick(light)
            shadow_h = _hue_or_pick(shadow)

            self._targets["base"] = base
            self._targets["light"] = light
            self._targets["shadow"] = shadow
            self._hue_memory["light"] = light_h
            self._hue_memory["shadow"] = shadow_h
            # Keep the per-target swatches honest immediately, rather than waiting
            # for whichever caller happens to refresh the preview next.
            #
            # All three syncs are needed, not just the dots. _sync_target_dots
            # updates the colour chips beside the glyphs, but the header swatch
            # comes from _update_preview and the Hue/Sat/Light positions from
            # _sync_sliders_from_state. Calling only the dots left the panel
            # showing the previous colour after a pick: the sphere went black
            # while the swatch and sliders still read the old pink. This mirrors
            # _on_target_changed, the other path that replaces all three.
            self._sync_target_dots()
            self._sync_sliders_from_state()
            self._update_preview()
            # And the sphere itself. This was missing, and it is the bug behind
            # "picking black makes everything go black": the swatch and the
            # sliders updated, but the sphere kept rendering the *previous*
            # colour, so a pick looked like it had turned the whole sphere black.
            # Measured: sampling the sphere after an orange pick showed 3% bright
            # pixels, the same as after a black pick, and 66% once this call
            # was added. Every other path that changes a target ends in
            # _rebuild_sphere for the same reason.
            self._rebuild_sphere()

            LOG.info("distributed %s -> base=%s light=%s shadow=%s",
                     color.name(), base.name(), light.name(), shadow.name())
        except Exception as exc:
            LOG.exception("_distribute_from failed")
            print(f"Lumina: distribute failed - {exc}")

    @staticmethod
    def _clamp01(value: float) -> float:
        return 0.0 if value < 0.0 else (1.0 if value > 1.0 else float(value))

    @staticmethod
    def _rgb01(color: QColor):
        return (color.redF(), color.greenF(), color.blueF())

    def _hsv_of(self, key: str):
        """Return (h, s, v) floats 0..1 for a target, honouring grey-colour hue.

        Two Qt quirks make this less trivial than reading the colour:

        * A fully desaturated colour carries no meaningful hue, and Qt reports
          the hue a grey was *built* from. Reading that back would make the Hue
          slider jump to an arbitrary colour on every grey, so the last real hue
          is remembered and reused instead.
        * A fully black colour reports hue **-1**, not 0. That is Qt saying
          "no hue", but -1 is not a position on the wheel: it puts the Hue
          slider at 287 and, worse, gets fed back through _set_active_hsv on the
          next edit, painting the target a colour the user never picked.

        The remembered hue is therefore only reused when the colour has some
        value to show. For black it is not, and the hue that the pick was
        distributed from is used instead, so the slider lands somewhere sensible
        rather than on a stale one.
        """
        hsv = self._targets[key].getHsvF()
        h, s, v = hsv[0], hsv[1], hsv[2]

        if v <= 0.0:
            # Pure black: no hue of its own, and Qt's -1 is not a real one.
            # Fall back to the hue this pick distributed from, not to whatever
            # was picked previously.
            h = self._picked_hue if self._picked_hue is not None else max(h, 0.0)
        elif s < 0.005:
            # Grey: Qt kept a hue we cannot trust, so reuse the remembered one.
            h = self._hue_memory.get(key, h)
        else:
            self._hue_memory[key] = h
        return h, s, v

    def _lch_of(self, key: str):
        """(hue degrees, relative chroma 0..1, lightness 0..1) of a target.

        A grey or black has no hue of its own, so the last real hue of that
        target is kept for the Hue slider instead of jumping to whatever the
        maths reports for zero chroma.
        """
        color = self._targets[key]
        h, c, L = _rgb_to_lch((color.red(), color.green(), color.blue()))
        memory = getattr(self, "_lch_hue_memory", None)
        if memory is None:
            memory = self._lch_hue_memory = {}
        if c < 0.01 or L <= 0.0:
            h = memory.get(key, memory.get("base", h))
        else:
            memory[key] = h
        return h, c, L

    def _set_active_lch(self, h=None, c=None, L=None) -> None:
        """Edit the active target in OKLCH, leaving the other parts intact.

        Lightness is honoured for the base only, as before: the highlight and
        shadow lightness come from the derivation, and a hidden row is a UI
        convention, not an invariant.
        """
        ch, cc, cl = self._lch_of(self._active_target)
        nh = ch if h is None else float(h) % 360.0
        nc = cc if c is None else max(0.0, min(1.0, float(c)))
        nl = cl
        if L is not None and self._active_target == "base":
            nl = max(0.0, min(1.0, float(L)))
        self._targets[self._active_target] = QColor(*_lch_to_rgb(nh, nc, nl))
        self._lch_hue_memory[self._active_target] = nh

    def _set_active_hsv(self, h=None, s=None, v=None) -> None:
        """Update the active target's HSV components, leaving the rest intact.

        ``v`` (brightness) is honoured for the base only. The highlight and
        shadow values are *derived* -- the highlight is always lighter than the
        base, the shadow always darker -- so a brightness edit on either would
        break exactly the relationship the distribution exists to produce,
        letting a shadow be dragged brighter than the object casting it.

        The Light row is hidden for those targets so the edit cannot be made by
        hand, but the guard is here as well: a hidden row is a UI convention,
        not an invariant, and a value loaded from disk, a preset, or any future
        caller would otherwise be able to write one the model calls invalid.
        Hue and Saturation remain editable on all three, since those describe a
        colour's character and every target legitimately has one.
        """
        ch, cs, cv = self._hsv_of(self._active_target)
        nh = ch if h is None else self._clamp01(h)
        ns = cs if s is None else self._clamp01(s)
        if v is not None and self._active_target != "base":
            nv = cv          # ignored: brightness belongs to the base alone
        else:
            nv = cv if v is None else self._clamp01(v)
        self._targets[self._active_target] = QColor.fromHsvF(nh, ns, nv)
        self._hue_memory[self._active_target] = nh

    class TargetDot(QWidget):
        """The live colour swatch shown beside a target's glyph.

        The reference pairs every icon with the colour it controls, so the row
        reads at a glance as "shadow / base / highlight" with real values rather
        than three anonymous glyphs. Selection is shown on *both* halves of the
        pair -- the glyph takes a smooth white ring and this dot a full white
        rim -- while the dot's own fill stays the real target colour.
        """

        # All three swatches share one box so the row never jumps. The disc
        # itself grows when selected: small at rest, larger with the white
        # rim when active.
        BOX = 28
        SMALL = 15
        LARGE = 25      # was 19: the selected target stands out clearly

        def __init__(self, key: str, parent=None):
            super().__init__(parent)
            self.key = key
            self._color = QColor(120, 120, 120)
            self._active = False
            self.setFixedSize(self.BOX, self.BOX)
            _set_tooltip(self, SphereDocker.TARGET_LABELS.get(key, key))
            # Clickable, like the glyph beside it. The swatch is half the width
            # of the pair and sits closest to the pointer when you aim for the
            # colour itself, so making only the icon a target made the obvious
            # thing to click the one thing that did nothing.
            self.setCursor(Qt.PointingHandCursor)
            self.setFocusPolicy(Qt.StrongFocus)

        def set_color(self, color: QColor) -> None:
            if color != self._color:
                self._color = QColor(color)
                self.update()

        def set_active(self, active: bool) -> None:
            if active != self._active:
                self._active = active
                self.update()

        def mousePressEvent(self, event):
            """Select this target, exactly as clicking its glyph does."""
            cb = getattr(self, "on_click", None)
            if cb is not None:
                cb(self.key)
            event.accept()

        def paintEvent(self, _event):
            p = QPainter(self)
            try:
                p.setRenderHint(QPainter.Antialiasing, True)
                # Inset by the rim half-width plus antialias margin: drawing
                # edge-to-edge clipped the rim against the widget bounds and
                # left flat, jagged edges. The active disc draws larger.
                d = self.LARGE if self._active else self.SMALL
                cx = cy = self.width() / 2.0
                rect = QRectF(cx - d / 2.0, cy - d / 2.0, d, d)
                p.setBrush(self._color)
                # The rim is the selection mark: solid white when active,
                # faint when not. Only the rim changes -- the fill always
                # stays the target's real colour, which is the whole point
                # of showing it here.
                #
                # A hairline rim keeps a near-black shadow swatch legible
                # against the dark panel.
                if self._active:
                    p.setPen(QPen(QColor(255, 255, 255, 255), 2.0))
                else:
                    p.setPen(QPen(QColor(255, 255, 255, 90), 1.2))
                p.drawEllipse(rect)
            except Exception as exc:  # pragma: no cover - cosmetic paint only
                print(f"Lumina: TargetDot.paintEvent failed - {exc}")
            finally:
                p.end()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        """Build the Lighting Sphere panel (see issue #9 for the reference layout)."""

        class TargetBtn(QPushButton):
            """Small selectable dot: terminator (shadow) / ring (base) / rays (light).

            One visual language for all three -- a dark-to-bright progression
            on the same circle -- instead of three unrelated pictograms.
            The active target gets a light outer ring. The previous version drew a
            flat amber square because the pen state leaked between branches and
            the glyphs were drawn with the QSS border-radius, so every branch now
            sets both pen and brush explicitly and draws ring-only geometry.
            """

            def __init__(self, key: str, parent=None):
                super().__init__(parent)
                self.key = key
                self.icon = key  # kept: existing code reads .icon
                self.color = QColor(SphereDocker.TARGET_ACCENT.get(key, QColor(160, 174, 193)))
                self.setFixedSize(34, 34)
                self.setCursor(Qt.PointingHandCursor)
                self.setFocusPolicy(Qt.StrongFocus)
                self.setCheckable(True)
                _set_tooltip(self, SphereDocker.TARGET_LABELS.get(key, key))
                # No QSS background: paintEvent draws everything itself.
                self.setStyleSheet("QPushButton { background: transparent; border: none; }")

            def set_active(self, active: bool) -> None:
                self.setChecked(bool(active))
                self.update()

            def paintEvent(self, event):
                """Draw the terminator-theme glyphs: half-dark / ring / rays.

                Shadow is a circle with its dark half filled (the terminator),
                base is the plain ring (the object itself), light is the ring
                with short rays (the lit side). Colour comes from the swatch
                beside the glyph, so the glyph itself only has to say *which
                role* it plays. Glyphs never take a selection mark -- that
                lives on the color swatch alone.
                """
                try:
                    p = QPainter(self)
                    p.setRenderHint(QPainter.Antialiasing, True)
                    w, h = self.width(), self.height()
                    cx, cy = w / 2.0, h / 2.0
                    # One ink for all three; glyphs stay hollow either way.
                    ink = QColor(216, 224, 236)
                    p.setPen(QPen(ink, 1.9, Qt.SolidLine, Qt.RoundCap,
                                  Qt.RoundJoin))
                    p.setBrush(Qt.NoBrush)

                    if self.icon == "shadow":
                        # Terminator: outline circle with the dark half filled.
                        p.drawEllipse(QRectF(cx - 5.5, cy - 5.5, 11.0, 11.0))
                        p.setPen(Qt.NoPen)
                        p.setBrush(ink)
                        p.drawPie(QRectF(cx - 5.5, cy - 5.5, 11.0, 11.0),
                                  90 * 16, 180 * 16)
                    elif self.icon == "base":
                        # The object itself: plain ring.
                        p.drawEllipse(QRectF(cx - 5.5, cy - 5.5, 11.0, 11.0))
                    else:
                        # Lit side: smaller ring with four short rays.
                        p.drawEllipse(QRectF(cx - 4.4, cy - 4.4, 8.8, 8.8))
                        for deg in (45.0, 135.0, 225.0, 315.0):
                            a = math.radians(deg)
                            dx, dy = math.cos(a), math.sin(a)
                            p.drawLine(
                                QPointF(cx + dx * 6.2, cy + dy * 6.2),
                                QPointF(cx + dx * 8.6, cy + dy * 8.6),
                            )
                    p.end()
                except Exception as exc:  # pragma: no cover - cosmetic paint only
                    print(f"Lumina: TargetBtn.paintEvent failed - {exc}")

        # ------------------------------------------------------------------
        # Header
        # ------------------------------------------------------------------
        main = QWidget()
        main.setStyleSheet("QWidget { background-color: %s; }" % PANEL_BG.name())
        self.setWidget(main)

        # The controls live in a scroll area. Expanding Advanced adds roughly ten
        # more rows, which is more than the panel is tall, and a plain widget
        # just overflowed: Qt let the panel spill past the bottom of the docker
        # and paint over whatever Krita had docked underneath it (the Layers
        # docker, the toolbox, the layers view). Scrolling keeps the panel inside
        # its own bounds no matter how far Advanced is opened.
        outer = QVBoxLayout(main)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll.setStyleSheet(
            "QScrollArea { background-color: %s; border: none; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
            "QScrollBar:vertical { background: transparent; width: 8px; margin: 0; }"
            "QScrollBar::handle:vertical { background: %s; border-radius: 4px; "
            "min-height: 24px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical "
            "{ height: 0px; }" % (PANEL_BG.name(), PANEL_BORDER.name()))
        outer.addWidget(self._scroll)

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        self._scroll.setWidget(content)

        layout = QVBoxLayout(content)

        header = QGridLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setHorizontalSpacing(6)
        header.setVerticalSpacing(2)
        # Gear and picker are fixed; the two swatches (and their hex labels
        # below) share the stretch so each label sits under its own box.
        header.setColumnStretch(0, 0)
        header.setColumnStretch(1, 1)
        header.setColumnStretch(2, 1)
        header.setColumnStretch(3, 0)

        # 1. Settings
        # 1. Settings (gear). Checkable so it lights up while the popup is
        # open; _toggle_settings and the panel Hide/Show events keep the
        # two in sync, including popup closes from outside clicks.
        gear = ToolButton(Accent.NEUTRAL, glyph="gear", size=HEADER_BTN)
        gear.glyph_scale = 1.6      # the gear read too small in its button
        _set_tooltip(gear, "Settings: light direction, render quality, reset")
        gear.clicked.connect(self._toggle_settings)
        self._gear_btn = gear
        gear.installEventFilter(self)       # sees its presses (popup close)
        header.addWidget(gear, 0, 0)

        # 2. Previous color (click to step back)
        self._sw_prev = QFrame()
        self._sw_prev.setMinimumSize(64, HEADER_BTN)
        self._sw_prev.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._sw_prev.setCursor(Qt.PointingHandCursor)
        _set_tooltip(self._sw_prev, "Previous color - click to step back")
        self._sw_prev.setStyleSheet(
            "QFrame { background-color: #3b414a; border-radius: 5px; }")
        self._sw_prev.mousePressEvent = self._on_prev_swatch_pressed
        header.addWidget(self._sw_prev, 0, 1)

        # 3. Current (modified) color
        self._sw_current = QFrame()
        self._sw_current.setMinimumSize(64, HEADER_BTN)
        self._sw_current.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._sw_current.setStyleSheet(
            "QFrame { background-color: #3b414a; border-radius: 5px; }")
        _set_tooltip(self._sw_current, "Active color")
        self._sw_current.mousePressEvent = self._on_current_swatch_pressed
        header.addWidget(self._sw_current, 0, 2)
        # Say what the two blocks are: the colour before (click to step back)
        # and the selected target now.
        self._cap_prev = self._swatch_caption(self._sw_prev, "Before")
        self._cap_now = self._swatch_caption(self._sw_current, "Now")

        # Copyable hex labels, one under each swatch. Click copies to the
        # clipboard with a brief confirmation; visibility is a persisted
        # setting (default on).
        self._hex_prev = self._make_hex_label("prev")
        self._hex_current = self._make_hex_label("current")
        header.addWidget(self._hex_prev, 1, 1)
        # Edit toggle glued to the current hash: the pair is centered as one
        # unit (fixed-width hash + tight button) so no gap opens up, and
        # both sit on the same 20px line.
        self._hex_locked = True
        # "EDIT SHADE" / "EDIT BASE" / "EDIT HIGH": typed hex values change
        # the selected target, and the label says which one.
        edit = QPushButton(HEX_EDIT_LABEL)
        # Wide enough for the longer of the two labels in Krita's own UI
        # font, which differs between Linux and Windows.
        bold = QFont(edit.font())
        bold.setPixelSize(10)
        bold.setBold(True)
        metrics = QFontMetricsF(bold)
        edit.setFixedSize(int(max(metrics.horizontalAdvance(label) for label in
                                  list(HEX_EDIT_LABELS.values()) + [HEX_DONE_LABEL])) + 6, 20)
        edit.setCursor(Qt.PointingHandCursor)
        edit.setFocusPolicy(Qt.NoFocus)
        edit.setFlat(True)
        edit.setStyleSheet(
            "QPushButton { color: #9aa3b4; font-size: 9px; "
            "background: transparent; border: none; padding: 0px; }"
            "QPushButton:hover { color: #ccd5e2; }")
        _set_tooltip(edit, "Allow typing new hex colors")
        edit.clicked.connect(self._toggle_hex_lock)
        self._hex_lock_btn = edit
        self._hex_current.setFixedWidth(56)
        hexbox = QHBoxLayout()
        hexbox.setContentsMargins(0, 0, 0, 0)
        hexbox.setSpacing(0)
        hexbox.addStretch(1)
        hexbox.addWidget(self._hex_current, 0)
        hexbox.addWidget(edit, 0)
        hexbox.addStretch(1)
        hexwrap = QWidget()
        hexwrap.setLayout(hexbox)
        self._hex_wrap = hexwrap
        header.addWidget(hexwrap, 1, 2)

        # 4. Color picker: hands over to Krita's own Color Sampler tool so the
        # user can click any pixel on the canvas. Checkable: lit while the
        # sampler tool is active, cleared when the previous tool returns.
        pick = ToolButton(Accent.CYAN, glyph="eyedropper", size=HEADER_BTN)
        _set_tooltip(pick, "Eyedropper: click a color on the canvas to sample it")
        pick.clicked.connect(self._on_eyedropper_tool)
        self._eyedropper_btn = pick
        header.addWidget(pick, 0, 3)
        layout.addLayout(header)
        layout.addSpacing(PANEL_ROW_GAP)

        # ------------------------------------------------------------------
        # Sphere + readout
        # ------------------------------------------------------------------
        sphere_frame = QFrame()
        sphere_layout = QVBoxLayout(sphere_frame)
        # Small gap under the gear / swatch / eyedropper row so the light
        # icons are not crowded against the controls above them.
        sphere_layout.setContentsMargins(0, 6, 0, 0)
        sphere_layout.setSpacing(2)
        # The sphere grows with the panel: it takes the width between the
        # preset columns, from SPHERE_SIZE up to SPHERE_MAX, and stays square
        # (the holder's height follows its width; see eventFilter). The
        # minimum is what the panel can shrink to, so narrowing it again works.
        self._sphere.setMinimumSize(SPHERE_SIZE, SPHERE_SIZE)
        self._sphere.setMaximumSize(SPHERE_MAX, SPHERE_MAX)
        self._sphere.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        sphere_holder = QWidget()
        sphere_holder.setMinimumSize(SPHERE_SIZE, SPHERE_SIZE)
        sphere_holder.setMaximumWidth(SPHERE_MAX)
        sphere_holder.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        sphere_holder.setFixedHeight(SPHERE_SIZE)
        sphere_holder.setStyleSheet("background: transparent;")
        self._sphere_holder = sphere_holder
        sphere_holder.installEventFilter(self)
        sphere_frame.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        sphere_grid = QGridLayout(sphere_holder)
        sphere_grid.setContentsMargins(0, 0, 0, 0)
        sphere_grid.addWidget(self._sphere, 0, 0)

        # Presets flank the sphere, three per side, instead of sitting in a grid
        # below it: at panel width a 3x2 grid pushed everything else down, and
        # the sphere is the visual anchor the presets modify, so they belong
        # beside it. Left column holds presets 0-2, right column 3-5.
        self._preset_caps = []

        def _preset_cell(btn, preset):
            cell = QWidget()
            cv = QVBoxLayout(cell)
            cv.setContentsMargins(0, 0, 0, 0)
            cv.setSpacing(1)
            cv.addWidget(btn, alignment=Qt.AlignCenter)
            cap = QLabel(preset["label"])
            cap.setAlignment(Qt.AlignCenter)
            cap.setStyleSheet(CAPTION_QSS)
            cv.addWidget(cap)
            self._preset_caps.append(cap)
            return cell

        def _preset_column(indexes):
            col = QVBoxLayout()
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(4)
            col.addStretch(1)
            for i in indexes:
                col.addWidget(_preset_cell(self._preset_btns[i], _PRESETS[i]),
                              alignment=Qt.AlignCenter)
            col.addStretch(1)
            return col

        # Lamp model sits directly above the sphere as icon buttons, like the
        # preset cells: it visibly reshapes the sphere, so it belongs with it
        # rather than buried in Advanced.
        sphere_layout.addWidget(self.light_type_row)
        sphere_layout.addSpacing(6)
        sphere_row = QHBoxLayout()
        sphere_row.setContentsMargins(0, 0, 0, 0)
        sphere_row.setSpacing(2)
        sphere_row.addLayout(_preset_column((0, 1, 2)), 0)
        sphere_row.addWidget(sphere_holder, 1)
        sphere_row.addLayout(_preset_column((3, 4, 5)), 0)
        sphere_layout.addLayout(sphere_row)

        self._readout = QLabel("")
        self._readout.setAlignment(Qt.AlignCenter)
        self._readout.setFixedHeight(14)
        self._readout.setStyleSheet(
            "QLabel { color: %s; font-size: 10px; }" % TEXT_DIM.name())
        layout.addWidget(sphere_frame)

        # ------------------------------------------------------------------
        # Target selector
        # ------------------------------------------------------------------
        target_row = QHBoxLayout()
        target_row.setContentsMargins(0, 0, 0, 0)
        target_row.setSpacing(22)
        # Stretches at both ends keep the dot cluster tight and centered. Without
        # them a QHBoxLayout spreads the fixed-size buttons across the full panel
        # width, which left the three dots marooned at the far edges.
        # Undo / redo of target colour changes, at the row's two ends.
        self._undo_btn = ToolButton(Accent.NEUTRAL, glyph="undo", size=26, checkable=False)
        self._redo_btn = ToolButton(Accent.NEUTRAL, glyph="redo", size=26, checkable=False)
        _set_tooltip(self._undo_btn, "Undo the last change to Shade / Base / High")
        _set_tooltip(self._redo_btn, "Redo")
        self._undo_btn.clicked.connect(self._undo_targets)
        self._redo_btn.clicked.connect(self._redo_targets)
        target_row.addWidget(self._undo_btn, 0, Qt.AlignVCenter)
        target_row.addStretch(1)
        self._target_btns = []
        self._target_dots = {}
        self._target_caps = {}
        for key in self.TARGET_ORDER:
            btn = TargetBtn(key)
            btn.clicked.connect(lambda _c, k=key: self._select_target(k))
            # Each target shows the colour it currently holds, so the row reads
            # as "shadow / base / highlight" with the actual values rather than
            # three unlabelled glyphs.
            cell = QWidget()
            cv = QHBoxLayout(cell)
            cv.setContentsMargins(0, 0, 0, 0)
            # Tight inside a pair, and the wider gap lives between pairs, so each
            # icon reads as belonging to the swatch beside it. With even spacing
            # throughout the six items looked like one undifferentiated row.
            cv.setSpacing(3)
            cv.addWidget(btn, alignment=Qt.AlignCenter)
            # TargetDot is a class attribute, so it needs qualifying here:
            # TargetBtn is declared inside this method and resolves by bare name,
            # but names in the class body are not in scope for its methods.
            dot = SphereDocker.TargetDot(key, parent=cell)
            dot.on_click = self._select_target
            cv.addWidget(dot, alignment=Qt.AlignCenter)
            self._target_dots[key] = dot
            # Caption under the pair: which target it is, the selected one lit.
            stack = QWidget()
            sv = QVBoxLayout(stack)
            sv.setContentsMargins(0, 0, 0, 0)
            sv.setSpacing(0)
            sv.addWidget(cell, alignment=Qt.AlignCenter)
            cap = QLabel(self.TARGET_CAPTIONS.get(key, key))
            cap.setAlignment(Qt.AlignCenter)
            cap.setStyleSheet(CAPTION_QSS)
            sv.addWidget(cap, alignment=Qt.AlignCenter)
            self._target_caps[key] = cap
            target_row.addWidget(stack, alignment=Qt.AlignCenter)
            self._target_btns.append(btn)
        target_row.addStretch(1)
        target_row.addWidget(self._redo_btn, 0, Qt.AlignVCenter)
        # Dots live inside the sphere frame, tucked under the sphere, with the
        # hover readout below them.
        sphere_layout.addLayout(target_row)
        sphere_layout.addWidget(self._readout)
        # Breathing room between the sphere frame and the Hue slider.
        layout.addSpacing(6)

        # ------------------------------------------------------------------
        # Primary sliders
        # ------------------------------------------------------------------
        layout.addWidget(self.hue_row)
        layout.addWidget(self.saturation_row)
        layout.addWidget(self.light_row)
        layout.addWidget(self.contrast_row)
        layout.addWidget(self.power_row)

        # Recent picks: the last colours chosen on the sphere, one click to
        # paint with again.
        self._recent = RecentColors()
        self._recent.display = self._to_display
        self._recent.bind(self._on_recent_pick, self._on_recent_assign,
                          self._mark_settings_dirty)
        layout.addSpacing(4)
        layout.addWidget(self._recent)

        # (Presets now flank the sphere above, three per side, instead of a grid
        # here: at panel width the 3x2 grid pushed everything else down, and the
        # sphere is the visual anchor the presets modify.)

        # ------------------------------------------------------------------
        # Advanced (collapsible)
        # ------------------------------------------------------------------
        self.advanced = CollapsibleSection("Advanced", expanded=False,
                                           accent=Accent.NEUTRAL)
        for row in (self.color_row, self.intensity_row, self.ambient_row,
                    self.specular_row, self.diffuse_row, self.glow_row, self.tone_row,
                    self.rim_row, self.rim_mix_row, self.sky_row, self.ground_row,
                    self.mixer_row):
            self.advanced.add_row(row)
        layout.addWidget(self.advanced)

        # Anything left over goes to the bottom of the panel rather than being
        # distributed between the controls.
        layout.addStretch(1)

        # The sampler is parented to the panel, not the sphere: a child of the sphere is
        # clipped to the sphere's bounds, which stopped the reticle reaching the
        # rim of the sphere.
        self.cylinder.setParent(main)
        # Sparkles on selecting a target, over the panel so nothing clips them.
        self._sparkle = SparkleBurst(main)
        # Letting go of a slider: a ripple from its knob.
        self._knob_ripple = KnobRipple(main)
        # Hold a slider too long and its knob sweats, like the lemon.
        self._slider_sweat = SweatEmitter(main)
        self._slider_sweat_timer = QTimer(self)
        self._slider_sweat_timer.setSingleShot(True)
        self._slider_sweat_timer.timeout.connect(self._start_slider_sweat)
        self._sweating_slider = None

        # `layout` belongs to `content`, the widget inside the scroll area, and
        # `main` already owns `outer`. Calling main.setLayout(layout) here looked
        # harmless but was the source of the startup warning
        # "QWidget::setLayout: Attempting to set QLayout ... which already has a
        # layout": Qt does not replace an existing layout, it detaches the new
        # one, so the call achieved nothing beyond the warning.
        #
        # Spacer items rather than contentsMargins: Krita re-applies the layout's
        # margins after the dock is created, which was wiping them back to the
        # 1px default and leaving the controls flush against the title bar.
        layout.setSpacing(6)
        layout.insertSpacing(0, PANEL_TOP_PAD)
        self._target_row_keys = list(self.TARGET_ORDER)

        # Settings popup lives on the main widget so it can position itself
        # against the gear button and close when clicking outside.
        self._settings_panel = SettingsPanel(
            azimuth=int(self.processor.engine.light_azimuth),
            elevation=int(round(float(self.processor.engine.light_elevation))),
            quality=self._sphere_render_size,
            highlight_size=self._shininess_to_size(
                self.processor.engine.shininess),
            parent=main)
        self._settings_panel.bind(self._on_settings_changed, self._on_settings_reset,
                                  self._on_settings_save)
        self._settings_panel.apply_btn.clicked.connect(self._on_apply_selection)
        self._settings_panel.hide()
        # Watch popup visibility so the gear highlight tracks outside-click
        # closes too, not just presses on the gear itself.
        self._settings_panel.installEventFilter(self)

        # Paint the initial state so the swatch / dots / gradients are correct
        # on the very first frame (previously _update_preview only ran on a
        # user action, leaving the swatch an empty grey box).
        # Restore the user's saved setup before the first paint, so the sphere never
        # flashes at the defaults on the way to the saved state.
        self._load_settings()

        self._sync_target_buttons()
        self._sync_sliders_from_state()
        self._update_preview()
        self._restore_lit_preset()
        # History starts from the restored targets, not the defaults they
        # replaced while loading.
        self._hist_timer.stop()
        self._hist_undo.clear()
        self._hist_redo.clear()
        self._hist_last = self._hist_state()
        self._sync_history_buttons()

        # Drop the sphere to a cheaper render size while any slider is being
        # dragged, and restore full quality on release. Done in one pass over
        # the widget tree rather than per-control so that sliders added later --
        # in the main panel or inside the settings popup -- pick it up for free.
        # The settings popup is parented to the main widget, so it is in range.
        for slider in self.findChildren(ColorSlider):
            slider.beganDrag.connect(self._on_slider_drag_begin)
            slider.endedDrag.connect(self._on_slider_drag_end)


    # ------------------------------------------------------------------
    # Floating-window dragging
    # ------------------------------------------------------------------
    # Krita's docker title bar does not start a window drag reliably once the
    # docker is torn off into its own window, so dragging the floating panel is
    # impossible. This adds drag handling on top of Krita's title bar rather
    # than replacing it, so the float/restore and close buttons keep working.
    _dragging = False
    _drag_offset = None

    def _install_titlebar_drag(self) -> None:
        """Watch for window drags on the floating panel's title bar.

        The filter is installed on the application rather than on the title bar
        widget, because tearing the docker off replaces that widget: a filter
        bound to the docked-time title bar never sees the floating window's
        events. A global filter keyed on "is this inside our own window, near its
        top edge" survives the reparenting.
        """
        try:
            filt = _TitleBarDragFilter.instance()
            filt.attach(self)
            app = QApplication.instance()
            if app is not None:
                app.installEventFilter(filt)
            tb = self.titleBarWidget()
            if tb is not None:
                tb.installEventFilter(filt)
        except Exception as exc:  # pragma: no cover - cosmetic only
            LOG.exception("_install_titlebar_drag failed")

    def _in_our_titlebar(self, obj, pos) -> bool:
        """True if ``pos`` lands on our own window's title bar area."""
        try:
            win = self.window()
            if win is None or win is self and not win.isWindow():
                return False
            # The event must belong to this dock's window (or its title bar).
            w = obj.window() if hasattr(obj, "window") else None
            if w is not None and w is not win and w is not self:
                return False
            # Prefer the title bar's real geometry: its height changes between
            # the docked and torn-off states, and a fixed band large enough for
            # the floating case would reach down over the gear button once the
            # panel's top padding is added, swallowing the press that opens
            # Settings.
            tb = self.titleBarWidget()
            if tb is not None and tb.window() is win:
                tl = tb.mapTo(win, tb.rect().topLeft())
                return tl.x() <= local.x() < tl.x() + tb.width() and \
                    tl.y() <= local.y() < tl.y() + tb.height()
            return 0 <= local.x() < win.width() and 0 <= local.y() <= TITLEBAR_BAND
        except Exception:  # pragma: no cover - defensive
            return False

    def _over_title_button(self, pos) -> bool:
        """True if the press landed on one of the title bar's own buttons."""
        try:
            tb = self.titleBarWidget()
            if tb is None:
                return False
            for b in tb.findChildren(QAbstractButton):
                # pos is global here; the button geometry is in title-bar space.
                if b.geometry().contains(tb.mapFromGlobal(pos)):
                    return True
            return False
        except Exception:  # pragma: no cover - defensive
            return True

    def eventFilter(self, obj, event):
        # Kept so the widget still behaves as a normal filter if something calls
        # it directly, but the real installation goes through the singleton
        # proxy -- see _TitleBarDragFilter for why it cannot be this object.
        if obj is getattr(self, "_sphere_holder", None):
            if event.type() == QEvent.Resize:
                side = max(SPHERE_SIZE, min(SPHERE_MAX, obj.width()))
                if obj.height() != side:
                    obj.setFixedHeight(side)
                    # The sphere follows at once; left to the holder's
                    # layout it lagged a pass behind on first show.
                    self._sphere.setFixedHeight(side)
            return False
        if obj is getattr(self, "_gear_btn", None):
            if event.type() == QEvent.MouseButtonPress:
                # The press that just closed the popup arrives within a few
                # ms of the close; any other press is a real click.
                gap = time.monotonic() - getattr(self, "_settings_hidden_by_gear_at", 0.0)
                self._gear_press_closes = gap < self.GEAR_REPLAY_S
            return False
        if obj is getattr(self, "_settings_panel", None):
            # Popup visibility drives the gear highlight, including closes
            # from outside clicks that never pass through _toggle_settings.
            et = event.type()
            if et == QEvent.Show:
                self._gear_btn.setChecked(True)
            elif et == QEvent.Hide:
                self._gear_btn.setChecked(False)
                # Was this close a press on the gear itself? Qt closes a popup
                # on the press and then hands that same press to the gear at
                # once; the click it becomes must not reopen the popup. Only
                # the moment is recorded here -- the gear's press handler
                # decides (see eventFilter), so the length of the click does
                # not matter (a release-time window broke slow clicks).
                self._settings_hidden_by_gear_at = (
                    time.monotonic() if self._press_on_gear() else 0.0)
            return False
        return self._handle_titlebar_event(obj, event)

    def _handle_titlebar_event(self, obj, event) -> bool:
        try:
            et = event.type()
            if et in (QEvent.MouseButtonPress, QEvent.MouseMove,
                      QEvent.MouseButtonRelease):
                gp = event.globalPos()
                if et == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                    if (self._in_our_titlebar(obj, gp)
                            and not self._over_title_button(gp)):
                        win = self.window()
                        self._drag_offset = gp - win.frameGeometry().topLeft()
                        self._dragging = True
                        return True
                elif et == QEvent.MouseMove and self._dragging:
                    self.window().move(gp - self._drag_offset)
                    return True
                elif et == QEvent.MouseButtonRelease and self._dragging:
                    self._dragging = False
                    self._drag_offset = None
                    return True
        except Exception as exc:  # pragma: no cover - cosmetic only
            LOG.exception("titlebar drag filter failed")
        return False

    # ------------------------------------------------------------------
    # State -> UI sync
    # ------------------------------------------------------------------
    def _select_target(self, key: str) -> None:
        """The user picked Shade, Base or High: select it, with sparkles."""
        self._on_target_changed(key)
        try:
            dot = self._target_dots.get(key)
            sparkle = getattr(self, "_sparkle", None)
            if dot is not None and sparkle is not None and key in self._targets:
                centre = dot.mapTo(sparkle.parentWidget(), dot.rect().center())
                sparkle.play(self._to_display(self._targets[key]), centre)
        except Exception as exc:  # pragma: no cover - cosmetic only
            LOG.exception("sparkle failed")

    def _on_target_changed(self, key: str) -> None:
        """Select which lighting target the Hue/Sat/Value sliders edit."""
        try:
            if key not in self._targets:
                return
            self._active_target = key
            self._sync_target_buttons()
            self._sync_sliders_from_state()
            self._update_preview()
            # The hex toggle names the target it edits (EDIT SHADE / BASE /
            # HIGH); re-apply the lock state to refresh that label.
            self._set_hex_locked(getattr(self, "_hex_locked", True))
            # Target-sync stamp (Q3): version + loaded module + target HSV +
            # row values. A stale install shows the wrong path; a bypassed
            # sync shows mismatched rows; matching values point at the UI.
            try:
                h, c, L = self._lch_of(key)
                LOG.info("TARGET_SYNC version=%s module=%s target=%s lch=(%d,%d,%d) sliders=(%d,%d,%d)",
                         PLUGIN_VERSION, os.path.abspath(__file__), key,
                         int(round(h)) % 360, int(round(c * 100.0)), int(round(L * 100.0)),
                         self.hue_row.slider.value(),
                         self.saturation_row.slider.value(),
                         self.light_row.slider.value())
            except Exception:  # pragma: no cover - UI only
                pass
        except Exception as exc:  # pragma: no cover - UI only
            LOG.exception("_on_target_changed failed")
            print(f"Lumina: on_target_changed failed - {exc}")

    def _sync_target_buttons(self) -> None:
        for btn in getattr(self, "_target_btns", []):
            btn.set_active(btn.key == self._active_target)
        self._sync_target_dots()

    def _sync_target_dots(self) -> None:
        """Show each target's current colour beside its glyph."""
        for key, dot in getattr(self, "_target_dots", {}).items():
            color = self._targets.get(key)
            if color is not None:
                dot.set_color(self._to_display(color))
            dot.set_active(key == self._active_target)
        for key, cap in getattr(self, "_target_caps", {}).items():
            cap.setStyleSheet(CAPTION_ON_QSS if key == self._active_target else CAPTION_QSS)

    def _sync_sliders_from_state(self) -> None:
        """Push the active target's HSV into the sliders without re-entering."""
        if self._syncing:
            return
        self._syncing = True
        try:
            # Perceptual (OKLCH) like the reference app: hue in degrees,
            # Sat as a share of the most vivid colour at that hue/lightness,
            # Light as perceived lightness. #00e700 reads 142 / 100 / 80.
            h, c, L = self._lch_of(self._active_target)
            self.hue_row.slider.setValue(int(round(h)) % 360)
            self.saturation_row.slider.setValue(int(round(c * 100.0)))
            self.light_row.slider.setValue(int(round(L * 100.0)))
            # Light (brightness) is only editable on the base. Hue and
            # Saturation apply to all three, because they describe a colour's
            # character and every target has one -- but value is different: the
            # highlight and shadow values are *derived* from the base, always
            # brighter and always darker than it respectively. A Light row on
            # either invites dragging a shadow brighter than the thing casting
            # it, or a highlight darker than its own base, which is exactly the
            # relationship the derivation exists to hold.
            self.light_row.setVisible(self._active_target == "base")
            # Base Level stays enabled at every level. It used to grey out
            # below ~3% max-channel, which trapped the slider: a disabled row
            # cannot be dragged back up, so the only recovery was Reset.
            # Near-black scales back up fine (0.02 x 25 = 0.5); only exact
            # zero loses hue, and that is recovered from hue/saturation memory
            # in _on_color_changed. The engine math is untouched.
            self.color_row.setEnabled(True)
            _set_tooltip(self.color_row, "Overall brightness of the base color")
            # Dependent interlocks (Q11): Rim Tint means nothing at Rim 0, so
            # it greys out -- the stored value is kept and returns when Rim
            # rises again. Gated on the engine's effective value, not the
            # rounded readout. (Glow has no dependent slider: intensity is
            # the only control.)
            self._sync_rim_interlock()
            # Name the target these rows are editing. Now that Light applies to
            # all three, "Light" alone is ambiguous: it could mean the base's
            # lightness or the highlight's.
            self._sync_slider_captions()
        finally:
            self._syncing = False
        self._sync_gradient_tracks()

    def _sync_rim_interlock(self) -> None:
        """Grey out Rim Tint while Rim Strength is at zero (and restore it).

        The stored tint value is never touched: dropping Rim to 0 and back
        returns the exact previous tint. Called from the rim handler (live
        drags) and the central slider sync (init/load/preset/reset).
        """
        try:
            rim_live = float(getattr(self.processor.engine, "rim_light", 0.0)) > 0.0
        except Exception:  # pragma: no cover - UI only
            rim_live = True
        try:
            self.rim_mix_row.setEnabled(rim_live)
            if rim_live:
                _set_tooltip(self.rim_mix_row, "Rim tint: key color to sky color")
            else:
                _set_tooltip(self.rim_mix_row,
                             "Rim Tint needs Rim Strength above 0: raise Rim "
                             "to adjust it (stored value kept)")
        except Exception:  # pragma: no cover - UI only
            LOG.exception("_sync_rim_interlock failed")

    def _sync_slider_captions(self) -> None:
        """Label the three colour sliders with the target they currently edit.

        "Hue / Saturation / Light" is ambiguous once Light applies to all three
        targets: it could read as "the base's lightness" or "the highlight's".

        The label names the target's *role* rather than its key, because
        "Light (Light)" is not a caption anyone should have to read. The value
        row is the base colour, and light/shadow are the highlight and the dark
        side of it, which is what the panel is actually modelling.
        """
        role = {
            "base": "base",
            "light": "high",
            "shadow": "shade",
        }.get(self._active_target, self._active_target)

        for row, base_name in ((self.hue_row, "Hue"),
                               (self.saturation_row, "Sat"),
                               (self.light_row, "Light")):
            try:
                row.set_caption("{0} ({1})".format(base_name, role))
            except Exception:  # pragma: no cover - cosmetic only
                LOG.exception("slider caption update failed")

    def _sync_gradient_tracks(self) -> None:
        """Repaint the slider tracks so each encodes the active target."""
        try:
            h, c, L = self._lch_of(self._active_target)
            show = lambda hh, cc, ll: self._to_display(QColor(*_lch_to_rgb(hh, cc, ll)))
            # Each hue at its most saturated (its cusp), like Krita's own
            # hue strip; one fixed lightness turned red and blue pastel.
            self.hue_row.slider.set_gradient(
                [(i / 36.0, show(i * 10.0, 1.0, _hue_cusp(i * 10.0)[0]))
                 for i in range(37)])
            self.saturation_row.slider.set_gradient(
                [(i / 6.0, show(h, i / 6.0, L)) for i in range(7)])
            self.light_row.slider.set_gradient(
                [(i / 8.0, show(h, c, i / 8.0)) for i in range(9)])
        except Exception as exc:  # pragma: no cover - cosmetic only
            LOG.exception("_sync_gradient_tracks failed")

    # ------------------------------------------------------------------
    # Target undo / redo
    # ------------------------------------------------------------------
    HISTORY_LIMIT = 50

    def _hist_state(self):
        return tuple(self._targets[k].name() for k in ("shadow", "base", "light"))

    def _hist_note(self) -> None:
        """Called on every target change (via _update_preview)."""
        if getattr(self, "_hist_timer", None) is None or self._hist_restoring:
            return
        if self._hist_last is None:
            self._hist_last = self._hist_state()
        elif self._hist_state() != self._hist_last:
            self._hist_timer.start()
        self._sync_history_buttons()

    def _hist_commit(self) -> None:
        """Record the settled change as one undo step."""
        self._hist_timer.stop()
        now = self._hist_state()
        if self._hist_last is not None and now != self._hist_last:
            self._hist_undo.append(self._hist_last)
            del self._hist_undo[:-self.HISTORY_LIMIT]
            self._hist_redo.clear()
        self._hist_last = now
        self._sync_history_buttons()

    def _hist_apply(self, state) -> None:
        self._hist_restoring = True
        try:
            for key, name in zip(("shadow", "base", "light"), state):
                self._targets[key] = QColor(name)
            self._sync_target_buttons()
            self._sync_sliders_from_state()
            self._rebuild_sphere()
            self._update_preview()
            self._mark_settings_dirty()
        finally:
            self._hist_restoring = False
        self._hist_last = tuple(state)
        self._sync_history_buttons()

    def _undo_targets(self) -> None:
        try:
            if self._hist_timer.isActive():
                self._hist_commit()             # a change still settling counts
            if not self._hist_undo:
                return
            self._hist_redo.append(self._hist_state())
            self._hist_apply(self._hist_undo.pop())
        except Exception:  # pragma: no cover - UI only
            LOG.exception("_undo_targets failed")

    def _redo_targets(self) -> None:
        try:
            if self._hist_timer.isActive():
                self._hist_commit()
            if not self._hist_redo:
                return
            self._hist_undo.append(self._hist_state())
            self._hist_apply(self._hist_redo.pop())
        except Exception:  # pragma: no cover - UI only
            LOG.exception("_redo_targets failed")

    def _sync_history_buttons(self) -> None:
        undo = getattr(self, "_undo_btn", None)
        if undo is not None:
            undo.setEnabled(bool(self._hist_undo) or self._hist_timer.isActive())
            self._redo_btn.setEnabled(bool(self._hist_redo))

    def _set_compact(self, compact: bool) -> None:
        """Compact panel: hide the captions (presets, lamps, targets,
        Before/Now) and Recent picks; the icons and tooltips stay."""
        self._compact = bool(compact)
        labels = (list(getattr(self, "_preset_caps", []))
                  + list(getattr(getattr(self, "light_type_row", None), "captions", []))
                  + list(getattr(self, "_target_caps", {}).values())
                  + [getattr(self, "_cap_prev", None), getattr(self, "_cap_now", None),
                     getattr(self, "_recent", None)])
        for w in labels:
            if w is not None:
                w.setVisible(not self._compact)

    @staticmethod
    def _swatch_caption(frame: QFrame, text: str) -> QLabel:
        """A small caption in the swatch's top-left corner."""
        box = QHBoxLayout(frame)
        box.setContentsMargins(6, 3, 6, 3)
        cap = QLabel(text)
        cap.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        box.addWidget(cap, 0, Qt.AlignLeft | Qt.AlignTop)
        SphereDocker._tint_caption(cap, None)
        return cap

    @staticmethod
    def _tint_caption(cap: QLabel, color) -> None:
        """Black or white caption, whichever reads on the swatch colour."""
        # White text on a small dark translucent pill: readable on any swatch
        # colour, light or dark (bare text vanished on mid tones).
        cap.setStyleSheet("QLabel { color: #ffffff; font-size: 9px; font-weight: bold; "
                          "background-color: rgba(10, 12, 18, 120); border: none; "
                          "border-radius: 6px; padding: 0px 5px; }")

    def _update_preview(self) -> None:
        """Refresh the split swatch: original (left block) and active (right)."""
        # Every path that changes a target colour -- a slider, the eyedropper,
        # distributing a sampled colour, a reset -- funnels through here, so this
        # is where the per-target swatches stay in step.
        self._sync_target_dots()
        self._hist_note()
        try:
            base = self._targets["base"]
            # The active swatch and the hex box under it both show the
            # selected target (shade, base or highlight): that is what the
            # box edits. A sphere click only sets Krita's brush and no longer
            # repaints the swatch, which made it disagree with the box.
            shown = self._targets.get(self._active_target, base)
            # Swatches are painted for the screen; the hex labels keep the
            # working-space numbers, which match Krita's selector.
            self._set_swatch_color(self._sw_current, self._to_display(shown))
            if getattr(self, "_cap_now", None) is not None:
                self._tint_caption(self._cap_now, self._to_display(shown))
            _set_tooltip(
                self._sw_current,
                "Active color %s - click to make this the new original" % shown.name())
            if getattr(self, "_hex_current", None) is not None:
                # The hex box shows the selected target: it is what typing
                # into it changes.
                self._hex_current.setText(
                    self._targets.get(self._active_target, base).name())
            orig = self._original_color
            if orig is not None:
                self._sw_prev.setStyleSheet(
                    "QFrame { background-color: %s; border-radius: 5px; "
                    "border: 1px solid rgba(255,255,255,70); }"
                    % self._to_display(orig).name())
                if getattr(self, "_cap_prev", None) is not None:
                    self._tint_caption(self._cap_prev, self._to_display(orig))
                _set_tooltip(
                    self._sw_prev,
                    "Original color %s - click to revert" % orig.name())
                if getattr(self, "_hex_prev", None) is not None:
                    self._hex_prev.setText(orig.name())
            else:
                self._sw_prev.setStyleSheet(
                    "QFrame { background-color: #22262f; border-radius: 5px; "
                    "border: 1px dashed rgba(255,255,255,50); }")
                if getattr(self, "_cap_prev", None) is not None:
                    self._tint_caption(self._cap_prev, None)
                _set_tooltip(self._sw_prev, "No original color yet")
                if getattr(self, "_hex_prev", None) is not None:
                    self._hex_prev.setText("--")
        except Exception as exc:  # pragma: no cover - cosmetic only
            print(f"Lumina: update_preview failed - {exc}")

    def _make_hex_label(self, which: str) -> QLineEdit:
        """Small hex readout under a swatch: click-to-copy when locked,
        typeable when the padlock is open (green)."""
        label = QLineEdit("--")
        label.setAlignment(Qt.AlignCenter)
        label.setCursor(Qt.PointingHandCursor)
        label.setReadOnly(True)
        label.setFrame(False)
        label.setStyleSheet(
            "QLineEdit { color: #9aa3b4; font-size: 10px; background: transparent; }"
            "QLineEdit:read-only { color: #9aa3b4; }"
            "QLineEdit:!read-only { color: #e7eaf2; }")
        label.setProperty("hex_which", which)
        _set_tooltip(label, "Click to copy the hex color")
        label.mousePressEvent = lambda event: self._on_hex_pressed(which, event)
        label.returnPressed.connect(lambda: self._commit_hex(which))
        label.editingFinished.connect(self._update_preview)
        return label

    @staticmethod
    def _parse_hex_entry(text: str):
        """Parse a typed hex color (#rrggbb, rrggbb, #rgb). None if invalid."""
        try:
            t = str(text).strip().lstrip("#")
            if len(t) == 3:
                t = "".join(ch * 2 for ch in t)
            if len(t) != 6:
                return None
            int(t, 16)
            color = QColor("#" + t.lower())
            return color if color.isValid() else None
        except Exception:
            return None

    def _on_hex_pressed(self, which: str, event=None) -> None:
        """Locked: copy the hex. Open: behave like a normal edit field."""
        try:
            if not getattr(self, "_hex_locked", True):
                QLineEdit.mousePressEvent(
                    self._hex_prev if which == "prev" else self._hex_current,
                    event)
                return
            label = self._hex_prev if which == "prev" else self._hex_current
            text = label.text().strip()
            if not text or text in ("--", "copied"):
                return
            clipboard = QApplication.clipboard()
            if clipboard is not None:
                clipboard.setText(text)
            label.setText("copied")
            # Only restore if nothing else overwrote the flash meanwhile.
            QTimer.singleShot(
                800,
                lambda: label.setText(text) if label.text() == "copied" else None)
        except Exception:  # pragma: no cover - UI only
            LOG.exception("_on_hex_pressed failed")

    def _commit_hex(self, which: str) -> None:
        """Apply a typed hex value: original swatch, or full lighting set.

        Only the current (right) box commits: the previous box is display
        plus copy, since editing it changed nothing on screen. The current
        box behaves like sampling the color with the eyedropper: the
        sphere's base/light/shadow are derived from it and the brush
        follows the new base. Setting only the brush left the sphere
        showing stale lighting for a color the user just chose.
        """
        try:
            if which != "current":
                self._update_preview()  # previous box never commits
                return
            label = self._hex_current
            color = self._parse_hex_entry(label.text())
            if color is None:
                self._update_preview()  # revert invalid input
                return
            self._assign_target(color)
        except Exception:  # pragma: no cover - UI only
            LOG.exception("_commit_hex failed")

    def _assign_target(self, color: QColor) -> None:
        """Make ``color`` (working-space numbers) the selected target's
        colour, as a typed hex does: the base derives light and shadow around
        it; a shadow or highlight is replaced exactly. The brush follows.
        Shared by the hex box, Shift-click on the sphere and a double-click
        on a recent pick."""
        self._chosen_color = None  # a chosen colour replaces any prior pick
        key = self._active_target
        if key == "base":
            self._distribute_from(QColor(color))
        else:
            self._replace_target(key, QColor(color))
        self._sync_target_buttons()
        self._sync_sliders_from_state()
        self._rebuild_sphere()
        self._update_preview()
        self._send_to_krita(self._targets[key])

    def _toggle_hex_lock(self) -> None:
        """Flip hex editing; wired to the EDIT/DONE toggle."""
        try:
            self._set_hex_locked(not getattr(self, "_hex_locked", True))
        except Exception:  # pragma: no cover - UI only
            LOG.exception("_toggle_hex_lock failed")

    def _set_hex_locked(self, locked: bool) -> None:
        """EDIT/DONE state: locked copies on click, open types new colors.

        Only the current (right) box is ever editable -- editing the
        previous box changed nothing visible, so it stays display + copy.
        """
        try:
            self._hex_locked = bool(locked)
            btn = getattr(self, "_hex_lock_btn", None)
            if btn is not None:
                btn.setText(HEX_DONE_LABEL if not self._hex_locked
                            else HEX_EDIT_LABELS.get(self._active_target, HEX_EDIT_LABEL))
                btn.setStyleSheet(
                    "QPushButton { color: #58b368; font-size: 10px; font-weight: bold; "
                    "background: transparent; border: none; padding: 0px; }"
                    "QPushButton:hover { color: #7bd88f; }"
                    if not self._hex_locked else
                    "QPushButton { color: #9aa3b4; font-size: 10px; font-weight: bold; "
                    "background: transparent; border: none; padding: 0px; }"
                    "QPushButton:hover { color: #ccd5e2; }")
                _set_tooltip(
                    btn, "Stop editing hex colors"
                    if not self._hex_locked else "Allow typing new hex colors")
            prev = getattr(self, "_hex_prev", None)
            if prev is not None:
                prev.setReadOnly(True)
                _set_tooltip(prev, "Click to copy the hex color")
            cur = getattr(self, "_hex_current", None)
            if cur is not None:
                cur.setReadOnly(self._hex_locked)
                _set_tooltip(
                    cur, "Type a hex color, Enter commits"
                    if not self._hex_locked else "Click to copy the hex color")
        except Exception:  # pragma: no cover - UI only
            LOG.exception("_set_hex_locked failed")

    def _set_hex_visible(self, visible: bool) -> None:
        """Show/hide the hex row; the persisted display setting."""
        try:
            self._show_hex = bool(visible)
            for label in (getattr(self, "_hex_prev", None),
                            getattr(self, "_hex_wrap", None)):
                if label is not None:
                    label.setVisible(self._show_hex)
        except Exception:  # pragma: no cover - UI only
            LOG.exception("_set_hex_visible failed")

    def _on_prev_swatch_pressed(self, event) -> None:
        """Left block: revert the active colour back to the original."""
        self._revert_to_original()

    def _on_current_swatch_pressed(self, event) -> None:
        """Right block: commit the active colour as the new original."""
        self._commit_current()

    # ------------------------------------------------------------------
    # Settings popup (gear button)
    # ------------------------------------------------------------------
    def _toggle_settings(self) -> None:
        """Open / close the settings panel right beside the docker.

        The popup sits against the docker's side that faces the canvas (its
        right when the docker is on the left of Krita's window, its left
        when it is on the right), level with the gear, so it never covers
        the sphere: settings re-render live and the sphere must stay
        visible while they change. It is kept inside Krita's main window
        and on the monitor Krita is on -- it used to be clamped to the
        *primary* screen, so with Krita on another monitor it opened out of
        sight.
        """
        try:
            panel = getattr(self, "_settings_panel", None)
            if panel is None:
                return
            if panel.isVisible():
                panel.hide()
                self._gear_btn.setChecked(False)
                return
            # A press on the gear while the popup is open closes the popup
            # first (Qt closes a popup on any outside press); the click that
            # follows must not reopen it straight away.
            if getattr(self, "_gear_press_closes", False):
                self._gear_press_closes = False   # this click's press closed it
                return
            panel.adjustSize()
            panel.move(self._settings_position(panel.width(), panel.height()))
            panel.show()
            panel.raise_()
            self._gear_btn.setChecked(True)
        except Exception as exc:  # pragma: no cover - UI only
            LOG.exception("_toggle_settings failed")
            print(f"Lumina: toggle settings failed - {exc}")

    GEAR_REPLAY_S = 0.15

    def _press_on_gear(self) -> bool:
        """Is the left button down over the gear right now?"""
        try:
            gear = self._gear_btn
            over = gear.rect().contains(gear.mapFromGlobal(QCursor.pos()))
            return bool(over and QApplication.mouseButtons() & Qt.LeftButton)
        except Exception:
            return False

    def _settings_bounds(self) -> QRect:
        """Where the popup may go, in global coordinates: Krita's main window
        on the monitor it is on (just that monitor for a floating docker)."""
        centre = self.mapToGlobal(QPoint(self.width() // 2, self.height() // 2))
        screen = QApplication.screenAt(centre) if hasattr(QApplication, "screenAt") else None
        if screen is None:
            handle = self.window().windowHandle()
            screen = handle.screen() if handle is not None else QApplication.primaryScreen()
        bounds = screen.availableGeometry()
        main = None
        try:
            from krita import Krita
            win = Krita.instance().activeWindow()
            main = win.qwindow() if win is not None else None
        except Exception:
            main = None
        if main is not None and main.isVisible():
            inside = bounds.intersected(main.frameGeometry())
            if inside.contains(centre):
                bounds = inside
        return bounds

    def _settings_position(self, width: int, height: int) -> QPoint:
        """Top-left for a ``width`` x ``height`` popup beside the docker."""
        gap = 4
        bounds = self._settings_bounds()
        left = self.mapToGlobal(QPoint(0, 0)).x()
        right = self.mapToGlobal(QPoint(self.width(), 0)).x()
        # The canvas side: toward the middle of the bounds.
        toward_right = (left + right) / 2.0 < bounds.center().x()
        spots = [right + gap, left - gap - width]
        if not toward_right:
            spots.reverse()
        x = next((sx for sx in spots
                  if sx >= bounds.left() and sx + width <= bounds.right() + 1),
                 spots[0])
        x = max(bounds.left(), min(x, bounds.right() + 1 - width))
        y = self._gear_btn.mapToGlobal(QPoint(0, 0)).y()
        y = max(bounds.top(), min(y, bounds.bottom() + 1 - height))
        return QPoint(int(x), int(y))

    def _on_settings_changed(self, state) -> None:
        """Apply the settings panel's current values to the engine."""
        try:
            self.processor.set_light_angle(
                int(state["azimuth"]), float(state["elevation"]))
            self._sphere_render_size = int(state["quality"])
            self._sphere.set_show_pointer(bool(state["pointer"]))
            if "sticky" in state:
                self._sphere.set_edge_glide(int(state["sticky"]))
            self._set_hex_visible(bool(state.get("hex", True)))
            if "compact" in state:
                self._set_compact(bool(state["compact"]))
            # Highlight size drives the same shininess as the Advanced
            # "Specular" row. The row is synced to match, so the two controls
            # never disagree about the engine value they both own.
            if "highlight_size" in state:
                self.processor.set_shininess(
                    self._size_to_shininess(int(state["highlight_size"])))
                self.specular_row.set_value(int(self.processor.engine.shininess))
            self._rebuild_sphere()
        except Exception as exc:  # pragma: no cover - UI only
            LOG.exception("_on_settings_changed failed")
            print(f"Lumina: settings change failed - {exc}")

    def _on_settings_save(self) -> None:
        """Explicit Save in the settings popup.

        Every change already autosaves, so this is a deliberate checkpoint
        rather than the only chance to keep the state -- it exists so the user
        can confirm the write happened and see where it went.
        """
        try:
            self._save_settings()
            self._settings_panel.show_saved()
            LOG.info("settings saved: %s", _settings_path())
        except Exception as exc:  # pragma: no cover - UI only
            LOG.exception("_on_settings_save failed")

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _collect_settings(self) -> dict:
        """Snapshot every user-facing value, for saving.

        Deliberately excludes transient state: the currently chosen brush
        colour and the active target are per-session, not part of a setup the
        user would expect to be restored.
        """
        eng = self.processor.engine
        out = {
            "version": int(SETTINGS_VERSION),
            "azimuth": int(eng.light_azimuth),
            "elevation": int(round(float(eng.light_elevation))),
            "highlight_size": self._shininess_to_size(eng.shininess),
            "quality": int(getattr(self, "_sphere_render_size", SPHERE_RENDER)),
            "sampler": bool(self._sphere._show_pointer),
            "sticky": int(round(self._sphere.edge_glide)),
            "compact": bool(getattr(self, "_compact", False)),
            # The specular colour a preset sets (Matte's warm white,
            # Nocturne's cool blue); it was not saved, so a restart lost it.
            "highlight": ",".join("%.4f" % c for c in eng.highlight_color),
            # The lit preset and the lighting it restores when switched off.
            "active_preset": str(getattr(self, "_active_preset", None) or ""),
            "preset_before": json.dumps(getattr(self, "_preset_before", None)),
            "show_hex": bool(getattr(self, "_show_hex", True)),
            "hex_locked": bool(getattr(self, "_hex_locked", True)),
            "diffuse": self._knee_to_level(eng.spec_knee),
            "contrast": int(eng.contrast * 100.0),
            "intensity": int(eng.light_intensity * 100.0),
            "ambient": int(eng.ambient * 100.0),
            "specular": int(eng.shininess),
            "spec_max": int(round(float(getattr(eng, "spec_max", 0.24)) * 100.0)),
            "rim": int(round(float(getattr(eng, "rim_light", 0.14)) * 100.0)),
            "rim_mix": int(round(float(getattr(eng, "rim_sky_mix", 0.60)) * 100.0)),
            "sky": int(round(float(getattr(eng, "sky_bounce", 0.05)) * 100.0)),
            "ground": int(round(float(getattr(eng, "ground_bounce", 0.02)) * 100.0)),
            "glow": int(eng.glow_intensity * 100.0),
            "tone": int(eng.saturation * 100.0),
            "mixer": str(eng.mixer_mode),
            "light_type": str(eng.light_type),
            "base_level": int(self.color_row.value()),
        }
        for name, qc in self._targets.items():
            out["target_" + name] = "#%02X%02X%02X" % (qc.red(), qc.green(), qc.blue())
        # The targets are numbers in this profile; without it a restart would
        # read P3 numbers as sRGB ones.
        out["targets_profile"] = str(getattr(self, "_space_profile", SRGB_PROFILE))
        if getattr(self, "_recent", None) is not None:
            out["recent"] = self._recent.saved()
        return out

    def _save_settings(self) -> None:
        """Write the current setup to disk.

        Called on every change, not just the Save button, so a crash or a
        forced quit cannot lose work. QSettings writes atomically, so a
        half-written file is not a risk.
        """
        try:
            s = _settings()
            for key, value in self._collect_settings().items():
                s.setValue(key, value)
            s.sync()
        except Exception as exc:  # pragma: no cover - disk/IO only
            LOG.exception("_save_settings failed")
            print(f"Lumina: could not save settings - {exc}")

    def _load_settings(self) -> None:
        """Restore a saved setup. Silently does nothing on a first run.

        Sets ``_syncing`` while applying so the slider updates it triggers do
        not bounce straight back through the change handlers and re-save a
        half-applied state.
        """
        try:
            _migrate_legacy_settings()
            s = _settings()
            if s.allKeys() == []:
                return
            self._settings_restored = True
            eng = self.processor.engine

            def num(key, default, lo=None, hi=None):
                v = s.value(key, default)
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    v = float(default)
                if lo is not None:
                    v = max(lo, v)
                if hi is not None:
                    v = min(hi, v)
                return v

            self._syncing = True
            try:
                for name in ("base", "light", "shadow"):
                    raw = s.value("target_" + name)
                    if raw:
                        qc = QColor(str(raw))
                        if qc.isValid():
                            self._targets[name] = qc
                # Settings older than this key were written in sRGB numbers.
                # The first poll converts them if the active layer differs.
                self._space_profile = str(s.value("targets_profile", SRGB_PROFILE))
                if getattr(self, "_recent", None) is not None:
                    names = [n for n in str(s.value("recent", "") or "").split(",") if n]
                    self._recent.set_colors(names)
                eng.set_light_angle(int(num("azimuth", DEFAULT_AZIMUTH, 0, 359)),
                                    num("elevation", DEFAULT_ELEVATION, 0, 90))
                self._sphere_render_size = int(num("quality", SPHERE_RENDER, 64, 512))
                self._sphere.set_show_pointer(str(s.value("sampler", "true")).lower()
                                           not in ("false", "0"))
                self._sphere.set_edge_glide(num("sticky", EDGE_GLIDE, 0, 100))
                self._set_compact(str(s.value("compact", "false")).lower()
                                  in ("true", "1"))
                hl = s.value("highlight", None)
                if hl:
                    try:
                        parts = hl if isinstance(hl, (list, tuple)) else str(hl).split(",")
                        rgb = tuple(max(0.0, min(1.0, float(v))) for v in parts)
                        if len(rgb) == 3:
                            self.processor.set_highlight_color(rgb)
                    except (TypeError, ValueError):
                        pass
                self._set_hex_visible(str(s.value("show_hex", "true")).lower()
                                      not in ("false", "0"))
                self._set_hex_locked(str(s.value("hex_locked", "true")).lower()
                                     not in ("false", "0"))
                saved_version = int(num("version", 1, 1, SETTINGS_VERSION))
                raw_diffuse = int(num("diffuse", self._knee_to_level(SPEC_KNEE), 0, 100))
                if saved_version < 4:
                    # Old linear slider meaning -> old physical knee ->
                    # equivalent new perceptual slider position.
                    raw_diffuse = self._migrate_diffuse_level(raw_diffuse)
                self.processor.set_spec_knee(self._level_to_knee(raw_diffuse))
                # Contrast defaults to 100 -- the middle of the 0-200 slider,
                # which is also the engine's no-op (1.0 = identity in the tone
                # curve). Below or above that, tone mapping reshapes the falloff.
                self.processor.set_contrast(num("contrast", 100, 0, 200) / 100.0)
                self.processor.set_light_intensity(
                    num("intensity", 100, 0, 200) / 100.0)
                self.processor.set_ambient(num("ambient", 5, 0, 100) / 100.0)
                # "specular" stays the canonical persisted shininess. The gear's
                # highlight-size slider drives the same engine value, but it is
                # mapped from it at sync time rather than read separately, so the
                # two controls can never disagree about what is saved.
                self.processor.set_shininess(num("specular", SHININESS_REF, 1, 128))
                self.processor.set_glow_intensity(num("glow", 0, 0, 100) / 100.0)
                # v1 stored Tone as brightness; do not reinterpret old
                # brightness as saturation. v2+ stores saturation correctly.
                if saved_version < 2:
                    self.processor.set_saturation(1.0)
                else:
                    self.processor.set_saturation(num("tone", 100, 0, 200) / 100.0)
                # v3+ persists spec_max; older settings keep baseline 0.24.
                if saved_version < 3:
                    self.processor.set_spec_max(0.24)
                else:
                    self.processor.set_spec_max(num("spec_max", 61, 0, 100) / 100.0)
                # v5+ persists the environment accents; older settings keep
                # the calibrated baselines.
                if saved_version < 5:
                    self.processor.set_rim_light(0.14)
                    self.processor.set_rim_sky_mix(0.60)
                    self.processor.set_sky_bounce(0.05)
                    self.processor.set_ground_bounce(0.02)
                else:
                    self.processor.set_rim_light(num("rim", 10, 0, 30) / 100.0)
                    self.processor.set_rim_sky_mix(num("rim_mix", 60, 0, 100) / 100.0)
                    self.processor.set_sky_bounce(num("sky", 2, 0, 10) / 100.0)
                    self.processor.set_ground_bounce(num("ground", 1, 0, 5) / 100.0)
                self.processor.set_mixer_mode(str(s.value("mixer", "Blended")))
                self.processor.set_light_type(str(s.value("light_type", "Sun")))
                self.light_type_row.set_mode(str(s.value("light_type", "Sun")))
                # Targets were restored as raw RGB above; anchor the relative
                # scaler at 100 so applying the saved level matches the old
                # absolute behavior (base x saved/100) on a fresh startup.
                self._base_level_last = 100
                self.color_row.set_value(int(num("base_level", 100, 0, 100)))

                # Push the restored numbers back into the widgets, still inside
                # the _syncing guard: the settings popup re-emits as its widgets
                # are set, and each of those emissions would otherwise apply a
                # half-updated mix of new and still-default values to the engine.
                self.diffuse_row.set_value(self._knee_to_level(eng.spec_knee))
                self.contrast_row.set_value(int(eng.contrast * 100.0))
                self.intensity_row.set_value(int(eng.light_intensity * 100.0))
                self.power_row.set_value(int(eng.light_intensity * 100.0))
                self.ambient_row.set_value(int(eng.ambient * 100.0))
                self.specular_row.set_value(int(eng.shininess))
                self.glow_row.set_value(int(eng.glow_intensity * 100.0))
                self.tone_row.set_value(int(eng.saturation * 100.0))
                self.rim_row.set_value(int(round(eng.rim_light * 100.0)))
                self.rim_mix_row.set_value(int(round(eng.rim_sky_mix * 100.0)))
                self.sky_row.set_value(int(round(eng.sky_bounce * 100.0)))
                self.ground_row.set_value(int(round(eng.ground_bounce * 100.0)))
                self._sync_settings_panel()
                self._sync_target_buttons()
                self._sync_sliders_from_state()
            finally:
                self._syncing = False
            # First paint happens after the guard is released, so this rebuild
            # both shows the restored sphere and commits it as the new saved state.
            self._rebuild_sphere()
            LOG.info("settings restored")
        except Exception as exc:  # pragma: no cover - startup path
            LOG.exception("_load_settings failed")
            print(f"Lumina: could not load settings - {exc}")

    def _sync_settings_panel(self) -> None:
        """Mirror the engine state back into the settings popup's widgets.

        Uses the panel's own signal-blocked setter. Setting the widgets
        individually re-emits on each one, and because the panel reports its
        whole state on every emission, the first three updates would push a
        mixture of restored and still-default values back into the engine.
        """
        try:
            eng = self.processor.engine
            self._settings_panel.sync_from(
                int(eng.light_azimuth),
                int(round(float(eng.light_elevation))),
                self._shininess_to_size(eng.shininess),
                int(getattr(self, "_sphere_render_size", SPHERE_RENDER)),
                bool(self._sphere._show_pointer),
                bool(getattr(self, "_show_hex", True)),
                sticky=self._sphere.edge_glide,
                compact=bool(getattr(self, "_compact", False)),
            )
        except Exception as exc:  # pragma: no cover - cosmetic only
            LOG.exception("_sync_settings_panel failed")

    def _on_settings_reset(self) -> None:
        """Restore targets and lighting to their defaults."""
        try:
            self._original_color = QColor(self.DEFAULT_TARGETS["base"])
            self._chosen_color = None
            # Base comes from the defaults; light and shadow are harmonized
            # from it below, so the reset trio always reads as one scheme.
            self._targets["base"] = QColor(self.DEFAULT_TARGETS["base"])
            self._hue_memory["base"] = QColor(self.DEFAULT_TARGETS["base"]).getHsvF()[0]
            self._active_target = "base"
            self.processor.set_light_angle(float(DEFAULT_AZIMUTH), float(DEFAULT_ELEVATION))
            self.processor.set_mixer_mode("Blended")
            self.mixer_row.set_mode("Blended")
            self.processor.set_light_type("Sun")
            self.light_type_row.set_mode("Sun")
            # Set the slider, not the engine: the row's valueChanged handler
            # applies it to the engine, so both end up at 100 together. Calling
            # _on_contrast_changed directly updated the engine but left the
            # slider showing the old position -- Reset then lied about the
            # state it had just restored.
            self.contrast_row.set_value(100)
            # Reset every value the setup persists, not just contrast and the
            # targets. It used to leave ambient, intensity, specular, diffuse,
            # glow, tone and base level untouched, so "Reset all" left most of
            # the advanced sliders exactly where the user had put them -- and now
            # that the whole panel is saved, those leftovers came back on the
            # next launch looking like the reset had silently failed.
            self._syncing = True
            try:
                eng = self.processor
                # A preset's specular colour goes too; Reset left it tinted.
                eng.set_highlight_color((1.0, 1.0, 1.0))
                eng.set_ambient(0.05)
                eng.set_light_intensity(1.0)
                eng.set_shininess(SHININESS_REF)
                eng.set_spec_knee(SPEC_KNEE)
                eng.set_spec_max(0.61)
                eng.set_glow_intensity(0.0)
                eng.set_rim_light(0.10)
                eng.set_rim_sky_mix(0.60)
                eng.set_sky_bounce(0.02)
                eng.set_ground_bounce(0.01)
                eng.set_brightness(1.0)
                eng.set_saturation(1.0)
                self._sphere_render_size = SPHERE_RENDER
                self._sphere.set_show_pointer(True)
                self._sphere.set_edge_glide(EDGE_GLIDE)
                self._set_compact(False)
                # The base was assigned directly above, so anchor the relative
                # scaler before moving the row: set_value(100) must be a no-op
                # (100/100), not a 100/last rescale of the fresh default.
                self._base_level_last = 100
                self.color_row.set_value(100)
                self.ambient_row.set_value(5)
                self.intensity_row.set_value(100)
                self.power_row.slider.blockSignals(True)
                try:
                    self.power_row.set_value(100)
                finally:
                    self.power_row.slider.blockSignals(False)
                # Blocked: _on_specular_changed has no _syncing guard, so
                # setting the row would push its value straight back into the
                # engine and undo SHININESS_REF restored above.
                self.specular_row.slider.blockSignals(True)
                try:
                    self.specular_row.set_value(int(SHININESS_REF))
                finally:
                    self.specular_row.slider.blockSignals(False)
                self.diffuse_row.set_value(self._knee_to_level(SPEC_KNEE))
                self.glow_row.set_value(0)
                self.tone_row.set_value(100)
                # Display setting back to its default (on).
                self._set_hex_visible(True)
                self._set_hex_locked(True)
                self.rim_row.set_value(10)
                self.rim_mix_row.set_value(60)
                self.sky_row.set_value(2)
                self.ground_row.set_value(1)
                self._sync_settings_panel()
            finally:
                self._syncing = False
            # The base restarts from Krita's current colour, not the default:
            # Krita's colour is the source of truth. Resetting to #cd5c5c
            # while the brush stayed green left the two disagreeing, and
            # eyedropping that same green again changed nothing in Krita, so
            # Lumina saw no pick at all. The default is the fallback for no
            # document.
            krita_rgb = self._current_krita_rgb()
            if krita_rgb is not None:
                self._targets["base"] = QColor(*krita_rgb)
                self._sampler_baseline = krita_rgb
            # Harmonize light/shadow from the base (same path as a canvas
            # pick), then sync and persist the coherent trio.
            self._distribute_from(QColor(self._targets["base"]))
            self._sync_target_buttons()
            self._sync_sliders_from_state()
            # Rebuilds and persists, since a reset is a change that must stick.
            # Explicit action: save now rather than waiting out the debounce.
            self._rebuild_sphere()
            self._save_settings()
            self._update_preview()
            LOG.info("settings reset to defaults")
        except Exception as exc:  # pragma: no cover - UI only
            LOG.exception("_on_settings_reset failed")
            print(f"Lumina: settings reset failed - {exc}")

    def _on_refresh(self) -> None:
        """Adopt the current Krita foreground color as the base target."""
        try:
            if hasattr(self, "_watch_krita_colors"):
                self._watch_krita_colors()
            rgb = self._current_krita_rgb()
            if rgb is not None:
                self._set_base_color(QColor(*rgb))
            self._rebuild_sphere()
            self._update_preview()
        except Exception as exc:  # pragma: no cover - UI only
            print(f"Lumina: on_refresh failed - {exc}")

    def _main_widget(self) -> QWidget:
        """Return the underlying QWidget (Krita pattern)."""
        return self.widget()

    # ------------------------------------------------------------------
    # Connections
    # ------------------------------------------------------------------
    def _connect_rows(self):
        # Primary sliders edit the *active target's* color.
        self.hue_row.slider.valueChanged.connect(self._on_hue_changed)
        self.saturation_row.slider.valueChanged.connect(self._on_saturation_changed)
        self.contrast_row.slider.valueChanged.connect(self._on_contrast_changed)

        # Advanced rows drive the engine directly.
        self.color_row.slider.valueChanged.connect(self._on_color_changed)
        self.intensity_row.slider.valueChanged.connect(self._on_intensity_changed)
        self.power_row.slider.valueChanged.connect(self._on_power_changed)
        self.ambient_row.slider.valueChanged.connect(self._on_ambient_changed)
        self.specular_row.slider.valueChanged.connect(self._on_specular_changed)
        self.diffuse_row.slider.valueChanged.connect(self._on_diffuse_changed)
        self.light_row.slider.valueChanged.connect(self._on_light_changed)
        self.glow_row.slider.valueChanged.connect(self._on_glow_changed)
        self.tone_row.slider.valueChanged.connect(self._on_tone_changed)
        self.rim_row.slider.valueChanged.connect(self._on_rim_changed)
        self.rim_mix_row.slider.valueChanged.connect(self._on_rim_mix_changed)
        self.sky_row.slider.valueChanged.connect(self._on_sky_changed)
        self.ground_row.slider.valueChanged.connect(self._on_ground_changed)

        # Preset buttons connect themselves at construction time, from the
        # _PRESETS table. Connecting them again here used to be the only thing
        # wiring them up, and left two dead references to handlers that no
        # longer exist.
        # (The "Use base color as brush" button lives in the gear popup now,
        # wired where the panel is constructed.)

    # ------------------------------------------------------------------
    # Target color sliders -> active target
    # ------------------------------------------------------------------
    def _on_hue_changed(self, value):
        try:
            if self._syncing:
                return
            LOG.info("_on_hue_changed value=%d target=%s", value, self._active_target)
            self._set_active_lch(h=float(value))
            self._rebuild_sphere()
            self._update_preview()
            self._sync_gradient_tracks()
        except Exception as exc:
            LOG.exception("_on_hue_changed failed")

    def _on_saturation_changed(self, value):
        try:
            if self._syncing:
                return
            LOG.info("_on_saturation_changed value=%d target=%s", value, self._active_target)
            self._set_active_lch(c=value / 100.0)
            self._rebuild_sphere()
            self._update_preview()
            self._sync_gradient_tracks()
        except Exception as exc:
            LOG.exception("_on_saturation_changed failed")

    def _on_light_changed(self, value):
        try:
            if self._syncing:
                return
            LOG.info("_on_light_changed value=%d target=%s", value, self._active_target)
            self._set_active_lch(L=value / 100.0)
            self._rebuild_sphere()
            self._update_preview()
            self._sync_gradient_tracks()
        except Exception as exc:
            LOG.exception("_on_light_changed failed")

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _render_sphere(self) -> QImage:
        """Render the sphere from the three target colors.

        The engine takes the base color as the albedo and the shadow tint as an
        ambient/shadow term, so the light and shadow targets are pushed in via
        the processor before rendering. Grid size follows the quality setting.

        While a slider is being dragged the grid is capped at
        ``DRAG_RENDER`` and the full size is restored on release. The shading
        loop is pure Python and scales with pixel count -- measured at 15.6 ms
        for 128px, 35.6 ms for 200px and 76.0 ms for 288px -- so a full-quality
        render holds the event loop long enough to stall the slider handle
        itself, which is what made the controls feel like they were moving in
        slow motion. Capping the drag puts the loop at ~64 Hz, and the sphere is
        re-rendered at full size the moment the drag ends.
        """
        base, size, full_quality = self._prepare_render()
        image = self.processor.render_image(base, size, size, full_quality=full_quality)
        self._last_render_cache_hit = bool(
            getattr(self.processor, "last_cache_hit", False))
        return image

    def _prepare_render(self):
        """Push the targets into the processor and settle the render size.
        Returns ``(base rgb01, size, full_quality)``."""
        # The engine renders sRGB for the screen, so working-space targets
        # (P3 numbers on a P3 layer) are converted first; picks off the
        # result are converted back in _on_sphere_brush.
        light = self._to_display(self._targets["light"])
        shadow = self._to_display(self._targets["shadow"])
        self.processor.set_light_color(self._rgb01(light))
        self.processor.set_shadow_color(self._rgb01(shadow))
        size = int(getattr(self, "_sphere_render_size", SPHERE_RENDER))
        if getattr(self, "_slider_dragging", False) and size > DRAG_RENDER:
            size = DRAG_RENDER
        if getattr(self, "_external_preview", False) and size > DRAG_RENDER:
            # Same reason as the slider drag above: a full-size render costs
            # ~120 ms, so a native picker drag would run the shading loop at
            # ~240% of one CPU slot and starve the event loop.
            size = DRAG_RENDER
        self._last_render_size = size
        try:
            self._last_render_smooth = float(self.processor.engine.smooth)
        except Exception:  # pragma: no cover - UI only
            self._last_render_smooth = -1.0
        # Only a non-drag, non-preview render at the configured size is a
        # full-quality result: previews (smaller size, suspended smoothing)
        # must never populate or satisfy the output cache (issue #40 §1).
        full_quality = not bool(getattr(self, "_slider_dragging", False)) \
            and not bool(getattr(self, "_external_preview", False))
        return self._rgb01(self._to_display(self._targets["base"])), size, full_quality

    SLIDER_SWEAT_AFTER_MS = 1200     # same wait as the lemon's

    def _on_slider_drag_begin(self) -> None:
        """Drop to the drag render size for the duration of the drag."""
        # A new change is coming: the full render under way is already stale.
        self._cancel_full_render()
        slider = self.sender()
        timer = getattr(self, "_slider_sweat_timer", None)
        # Only a held mouse sweats, not a wheel or key burst.
        if timer is not None and slider is not None and hasattr(slider, "knob_center") \
                and getattr(slider, "by_mouse", True):
            self._sweating_slider = slider
            timer.start(self.SLIDER_SWEAT_AFTER_MS)
        if self._slider_dragging:
            return              # already suspended: keep the saved smoothing
        self._slider_dragging = True
        # Suspend the blur pass while dragging: it costs more than the
        # shading itself at these sizes and returns on release.
        self._smooth_saved = self.processor.engine.smooth
        self.processor.set_smooth(0.0)

    def _on_slider_drag_end(self) -> None:
        """Restore full render quality once the drag is over.

        The value is already final by the time this fires, so scheduling a
        normal rebuild here produces the crisp sphere the user is left looking at.

        Note this flag is deliberately *not* the same as ``_dragging``, which
        tracks the floating title bar; sharing the name would let a title-bar
        drag drop the sphere to the drag resolution, and a slider release would
        cancel an in-progress window drag.
        """
        self._slider_dragging = False
        self.processor.set_smooth(getattr(self, "_smooth_saved", 2.0))
        self._rebuild_sphere(reason="slider_release")
        timer = getattr(self, "_slider_sweat_timer", None)
        if timer is not None:
            timer.stop()
            self._slider_sweat.stop()
        self._sweating_slider = None
        slider = self.sender()
        if getattr(slider, "by_mouse", True):     # no ripple for the wheel or keys
            self._ripple_slider(slider)

    def _start_slider_sweat(self) -> None:
        """Held too long: the dragged knob starts to sweat."""
        slider = getattr(self, "_sweating_slider", None)
        emitter = getattr(self, "_slider_sweat", None)
        if slider is None or emitter is None or not self._slider_dragging:
            return
        if slider.window() is not emitter.window() or not slider.isVisible():
            return                      # the settings popup is its own window
        parent = emitter.parentWidget()
        emitter.start(lambda: slider.mapTo(parent, slider.knob_center()))

    def _ripple_slider(self, slider) -> None:
        """A ripple from the knob of the slider just let go, in its colour.
        Only for the panel's own sliders: the settings popup is a window of
        its own, and the ripple overlay lives on the panel."""
        try:
            ripple = getattr(self, "_knob_ripple", None)
            if ripple is None or slider is None or not hasattr(slider, "knob_center"):
                return
            if slider.window() is not ripple.window() or not slider.isVisible():
                return
            centre = slider.mapTo(ripple.parentWidget(), slider.knob_center())
            ripple.play(slider.knob_color(), centre)
        except Exception:  # pragma: no cover - cosmetic only
            LOG.exception("slider ripple failed")

    def _rebuild_sphere(self, reason: str = "live_update"):
        """Schedule a coalesced sphere rebuild and mark settings dirty.

        ``reason`` names the requester for the RENDER_DONE log line
        (``slider_release`` / ``external_final`` / ``live_update``), so a UI
        freeze can be attributed to the render that caused it. Coalesced
        bursts keep the last reason: the executed render is the release or
        final, not an intermediate tick.

        Persisting moved to a debounced flush (see _mark_settings_dirty):
        every user-facing control still ends up saved exactly once, but a
        drag no longer syncs the settings file to disk per change. Explicit
        actions (Apply, Reset, preset, Save button, shutdown) save
        immediately. Guarded by ``_syncing`` so that loading saved state,
        which also rebuilds, does not write the half-applied intermediate
        values straight back over the file.

        A single slider drag fires valueChanged dozens of times per second, and
        each render is a few tens of milliseconds of pure-Python shading. Queueing
        the work on a 0 ms single-shot timer collapses a burst of changes into one
        render per event-loop turn, so dragging stays responsive instead of
        backing up a queue of stale renders.
        """
        self._pending_reason = reason
        self._rebuild_timer.start(0)
        if getattr(self, "_applying_preset", False):
            # The preset that is mid-apply owns the state right now.
            return
        # A lit preset that no longer matches the lighting would be a lie, so
        # it goes off when a lighting value it sets moves away from it. A
        # colour change (a pick, a target edit, Krita's colour) leaves the
        # lighting alone and the preset lit -- it used to switch off on any
        # redraw. The user's own lighting edit is then the state: a later
        # click switches the preset on afresh rather than undoing past it.
        active = getattr(self, "_active_preset", None)
        if active is not None and not self._preset_matches(active):
            for btn in getattr(self, "_preset_btns", []):
                if btn.isChecked():
                    btn.setChecked(False)
            self._active_preset = None
            self._preset_before = None
        if not getattr(self, "_syncing", False):
            self._mark_settings_dirty()

    def _mark_settings_dirty(self) -> None:
        """Defer the settings write: one disk sync per gesture, not per change.

        Ordinary control/slider changes land here; the 2 s single-shot timer
        collapses a whole drag into a single write. While an external stream
        is live (Run-2) this only marks: no disk I/O mid-drag. The end
        detector flushes after stream end (see _arm_post_stream_save).
        Explicit actions (Apply, Reset, preset choice, Save button) and
        shutdown call :meth:`_save_settings` directly instead.
        """
        try:
            self._settings_dirty = True
            if getattr(self, "_external_preview", False):
                return
            timer = getattr(self, "_save_timer", None)
            if timer is not None:
                timer.start(SAVE_DEBOUNCE_MS)
        except Exception:  # pragma: no cover - disk/IO only
            pass

    def _arm_post_stream_save(self) -> None:
        """Flush pending settings ~500 ms after stream end, not with the render.

        Gives the interaction breathing room: a pause-then-resume re-enters
        preview (cancelling this timer in _note_external_color) instead of
        eating disk latency. Persistence stays subordinate to the gesture.
        """
        try:
            if not getattr(self, "_settings_dirty", False):
                return
            timer = getattr(self, "_save_timer", None)
            if timer is not None:
                timer.stop()
                timer.start(STREAM_END_SAVE_MS)
        except Exception:  # pragma: no cover - disk/IO only
            pass

    def _flush_settings(self) -> None:
        """Write pending settings now; no-op when nothing changed."""
        try:
            if not getattr(self, "_settings_dirty", False):
                return
            self._settings_dirty = False
            self._save_settings()
        except Exception:  # pragma: no cover - disk/IO only
            LOG.exception("_flush_settings failed")

    def _rebuild_sphere_now(self):
        """Render and display the sphere immediately (bypasses the coalescer).

        While dragging, renders are throttled to ~30ms so the handle tracks
        the pointer; the release always schedules a full-quality final.
        """
        if getattr(self, "_slider_dragging", False) or \
                getattr(self, "_external_preview", False):
            now = time.monotonic()
            last = getattr(self, "_last_sphere_ms", 0.0)
            gap_ms = (now - last) * 1000.0
            if gap_ms < DRAG_THROTTLE_MS:
                QTimer.singleShot(
                    max(1, int(DRAG_THROTTLE_MS - gap_ms)),
                    self._rebuild_sphere_now)
                return
            self._last_sphere_ms = now
        # Anything drawn from here on is newer than a background render
        # still under way, which must then not replace it.
        self._render_gen = getattr(self, "_render_gen", 0) + 1
        if getattr(self, "_async_full_render", False) and self._submit_full_render():
            return
        # A preview: the full render under way is already out of date, and
        # left running it halves the previews' speed (they share the
        # interpreter). Stop it.
        self._cancel_full_render()
        started = time.monotonic()
        self._sphere.set_image(self._render_sphere())
        render_ms = (time.monotonic() - started) * 1000.0
        self._log_render_done(render_ms)

    # ------------------------------------------------------------------
    # Full-quality renders off the UI thread
    # ------------------------------------------------------------------
    # A full-quality render is ~330 ms of pure Python at the docker's usual
    # 288 px. On the UI thread it froze everything for that long after each
    # slider release or wheel pause: the knob ripple and falling sweat
    # stopped mid-air, and wheel notches queued up behind it. Inside Krita it
    # runs on a worker thread instead, with an engine of its own; the panel
    # keeps the drag-size image until the result is in. Previews stay on the
    # UI thread: at ~24 ms they are cheaper than the hand-off.
    #
    # The worker alone was not enough. Every time the UI thread comes back
    # from Qt into Python it must win the interpreter lock back from the
    # busy worker, which Python hands over only every switch interval
    # (5 ms); a panel repaint does that hundreds of times, so the panel
    # stalled nearly as long as before (a 10 ms timer ran 320 ms late).
    # While a render runs the interval drops to 0.2 ms: the timer is then at
    # most ~12 ms late and the render takes no longer. It is restored as
    # soon as the worker is idle.
    FULL_POLL_MS = 15
    BG_SWITCH_INTERVAL = 0.0002

    def _submit_full_render(self) -> bool:
        """Queue a full render off the UI thread. False when this render is
        a preview or already cached, which the caller draws itself."""
        base, size, full_quality = self._prepare_render()
        if not full_quality or self.processor.has_full_render(base, size, size):
            return False
        job = self.processor.full_render_job(base, size, size)
        reason = getattr(self, "_pending_reason", "live_update") or "live_update"
        self._pending_reason = None
        running = getattr(self, "_full_running", None)
        if running is not None and running[1][0] == job[0] and not running[5].is_set():
            # The one already under way is this very render: adopt it.
            self._full_running = (self._render_gen,) + running[1:]
            self._full_wanted = None
            return True
        # Only the newest waiting job is kept: renders for states already
        # passed would only hold up the one that matters.
        self._full_wanted = (self._render_gen, job, reason, time.monotonic())
        if running is None:
            self._launch_full_render()
        else:
            running[5].set()            # out of date: stop it, this one next
        return True

    def _cancel_full_render(self) -> None:
        """Stop the background render under way and drop any waiting one."""
        self._full_wanted = None
        running = getattr(self, "_full_running", None)
        if running is not None:
            running[5].set()

    def _launch_full_render(self) -> None:
        import concurrent.futures
        pool = getattr(self, "_full_pool", None)
        if pool is None:
            pool = self._full_pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="lumina-render")
        gen, job, reason, asked = self._full_wanted
        self._full_wanted = None
        if getattr(self, "_saved_switch_interval", None) is None:
            self._saved_switch_interval = sys.getswitchinterval()
            sys.setswitchinterval(self.BG_SWITCH_INTERVAL)
        import threading
        cancel = threading.Event()
        future = pool.submit(self.processor.run_full_render_job, job, cancel)
        self._full_running = (gen, job, reason, asked, future, cancel)
        poll = getattr(self, "_full_poll", None)
        if poll is None:
            poll = self._full_poll = QTimer(self)
            poll.setInterval(self.FULL_POLL_MS)
            poll.timeout.connect(self._poll_full_render)
        poll.start()

    def _poll_full_render(self) -> None:
        running = getattr(self, "_full_running", None)
        if running is None:
            self._full_poll.stop()
            return
        gen, job, reason, asked, future, _cancel = running
        if not future.done():
            return
        self._full_running = None
        try:
            raw = future.result()       # None: cancelled
        except Exception:
            LOG.exception("background render failed")
            raw = None
        if raw is None:
            LOG.info("RENDER_CANCELLED reason=%s after_ms=%.1f", reason,
                     (time.monotonic() - asked) * 1000.0)
        else:
            image = self.processor.store_full_render(job, raw)
            if gen == getattr(self, "_render_gen", 0):
                self._sphere.set_image(image)
                self._last_render_size = job[1]
                self._last_render_cache_hit = False
                self._pending_reason = reason
                self._log_render_done((time.monotonic() - asked) * 1000.0, background=True)
        if getattr(self, "_full_wanted", None) is not None:
            self._launch_full_render()
        else:
            self._full_poll.stop()
            self._restore_switch_interval()

    def _restore_switch_interval(self) -> None:
        saved = getattr(self, "_saved_switch_interval", None)
        if saved is not None:
            self._saved_switch_interval = None
            sys.setswitchinterval(saved)

    def _log_render_done(self, render_ms: float, background: bool = False) -> None:
        # Attribution line (Q1): every executed render reports its trigger,
        # resolution and cost, so a freeze maps to the render that caused it.
        # The throttle reschedule above returns early with the reason still
        # pending, so the retry logs the original trigger, not the retry.
        reason = getattr(self, "_pending_reason", "live_update") or "live_update"
        self._pending_reason = None
        LOG.info("RENDER_DONE reason=%s size=%d smooth=%.1f ms=%.1f drag_active=%d cache_hit=%d bg=%d",
                 reason, int(getattr(self, "_last_render_size", -1)),
                 float(getattr(self, "_last_render_smooth", -1.0)), render_ms,
                 int(bool(getattr(self, "_slider_dragging", False))),
                 int(bool(getattr(self, "_last_render_cache_hit", False))),
                 int(background))
        if getattr(self, "_external_preview", False):
            self._ext_renders += 1
            elapsed_ms = render_ms
            self._ext_render_ms += elapsed_ms
            samples = getattr(self, "_ext_render_samples", None)
            if samples is not None:
                samples.append(elapsed_ms)
        elif getattr(self, "_ext_final_pending", False):
            # The trailing full-quality final runs here, async, after the
            # burst summary already logged. Reported standalone so it is
            # never misattributed to the next burst: this render is the
            # prime hitch suspect for Run 1.
            self._ext_final_pending = False
            elapsed_ms = render_ms
            LOG.info("EXT_FINAL ms=%.1f", elapsed_ms)

    # ------------------------------------------------------------------
    # Row handlers -> engine setters + rebuild
    # ------------------------------------------------------------------
    def _on_color_changed(self, value):
        """Advanced: scale the base target's overall level, preserving hue/sat.

        Scaling is relative to the value that produced the current base
        (``_base_level_last``): each event multiplies by new/old, so a drag
        from 100 to 70 telescopes to exactly x0.70 however many valueChanged
        events fired in between. The old code multiplied the live base by
        value/100 on *every* event, compounding to near-black after a few
        dozen ticks -- which tripped the near-black guard, disabled the row
        mid-drag, and froze the slider around 70%.
        """
        try:
            LOG.info("_on_color_changed value=%d", value)
            value = max(0, min(100, int(value)))
            last = max(0, min(100, int(getattr(self, "_base_level_last", 100))))
            base = self._targets["base"]
            peak = max(base.redF(), base.greenF(), base.blueF())
            if peak <= 0.0 and value > 0 and getattr(self, "_base_level_hs", None):
                # Rising out of exact black: multiplication cannot recover
                # hue, so rebuild from the remembered hue/saturation at the
                # new level instead of staying black.
                h, s = self._base_level_hs
                self._set_base_color(QColor.fromHsvF(
                    h, s, value / 100.0,
                    max(0.0, min(1.0, base.alphaF()))))
                self._base_level_last = value
            else:
                if peak > 0.0:
                    h, s = base.getHsvF()[0], base.getHsvF()[1]
                    self._base_level_hs = (max(0.0, h), max(0.0, min(1.0, s)))
                if last > 0:
                    scale = value / last
                else:
                    # Anchored at black with no memory: nothing to recover.
                    scale = value / 100.0
                self._set_base_color(QColor.fromRgbF(
                    self._clamp01(base.redF() * scale),
                    self._clamp01(base.greenF() * scale),
                    self._clamp01(base.blueF() * scale),
                ))
                self._base_level_last = value
            self._rebuild_sphere()
            self._sync_sliders_from_state()
            self._update_preview()
        except Exception as exc:
            LOG.exception("_on_color_changed failed")

    def _on_mixer_changed(self, mode: str) -> None:
        """Advanced: how base / shadow / highlight are blended together."""
        try:
            LOG.info("_on_mixer_changed mode=%s", mode)
            self.processor.set_mixer_mode(mode)
            self._rebuild_sphere()
        except Exception as exc:
            LOG.exception("_on_mixer_changed failed")

    def _on_light_type_changed(self, mode: str) -> None:
        """Lamp model driving illuminance (Point/Sun/Spot/Area)."""
        try:
            LOG.info("_on_light_type_changed mode=%s", mode)
            self.processor.set_light_type(mode)
            self._rebuild_sphere()
        except Exception as exc:
            LOG.exception("_on_light_type_changed failed")

    def _on_tone_changed(self, value):
        """Advanced: global render saturation (engine tone, not a target color)."""
        try:
            LOG.info("_on_tone_changed value=%d", value)
            self.processor.set_saturation(value / 100.0)
            self._rebuild_sphere()
        except Exception as exc:
            LOG.exception("_on_tone_changed failed")

    def _set_light_power(self, percent: int) -> None:
        """Drive sun power from either slider; the other mirrors it silently."""
        try:
            self.processor.set_light_intensity(percent / 100.0)
            for row in (self.power_row, self.intensity_row):
                if row.slider.value() != percent:
                    row.slider.blockSignals(True)
                    try:
                        row.set_value(percent)
                    finally:
                        row.slider.blockSignals(False)
            self._rebuild_sphere()
        except Exception as exc:  # pragma: no cover - UI only
            LOG.exception("_set_light_power failed")
            print(f"Lumina: set light power failed - {exc}")

    def _on_power_changed(self, value):
        try:
            LOG.info(f"_on_power_changed value={value}")
            self._set_light_power(value)
        except Exception as exc:  # pragma: no cover - UI only
            LOG.exception("_on_power_changed failed")

    def _on_intensity_changed(self, value):
        try:
            LOG.info(f"_on_intensity_changed value={value}")
            self._set_light_power(value)
        except Exception as exc:
            LOG.exception("_on_intensity_changed failed")

    def _on_ambient_changed(self, value):
        try:
            LOG.info(f"_on_ambient_changed value={value}")
            self.processor.set_ambient(value / 100.0)
            self._rebuild_sphere()
        except Exception as exc:
            LOG.exception("_on_ambient_changed failed")

    def _on_contrast_changed(self, value):
        try:
            LOG.info(f"_on_contrast_changed value={value}")
            self.processor.set_contrast(value / 100.0)
            self._rebuild_sphere()
        except Exception as exc:
            LOG.exception("_on_contrast_changed failed")

    def _on_specular_changed(self, value):
        try:
            LOG.info(f"_on_specular_changed value={value}")
            self.processor.set_shininess(value)
            self._rebuild_sphere()
        except Exception as exc:
            LOG.exception("_on_specular_changed failed")

    def _on_diffuse_changed(self, value):
        try:
            LOG.info(f"_on_diffuse_changed value={value}")
            self.processor.set_spec_knee(self._level_to_knee(value))
            self._rebuild_sphere()
        except Exception as exc:
            LOG.exception("_on_diffuse_changed failed")

    @staticmethod
    def _level_to_knee(level: int) -> float:
        """Map the Diffuse slider (0-100) onto the specular knee.

        Inverted: low knee is the diffuse look, so slider 100 -> SPEC_KNEE_MIN.
        Perceptual (exponent 1.35) for the 0.02-0.95 domain: more resolution
        at low knee values. Starting calibration, validate visually at
        0/25/50/75/100 for dead ranges.
        """
        import math
        t = max(0.0, min(1.0, level / 100.0))
        return SPEC_KNEE_MIN + (SPEC_KNEE_MAX - SPEC_KNEE_MIN) * ((1.0 - t) ** 1.35)

    @staticmethod
    def _knee_to_level(knee: float) -> int:
        """Inverse of :meth:`_level_to_knee`, for restoring a saved value."""
        import math
        k = max(SPEC_KNEE_MIN, min(SPEC_KNEE_MAX, knee))
        t = 1.0 - ((k - SPEC_KNEE_MIN) / (SPEC_KNEE_MAX - SPEC_KNEE_MIN)) ** (1.0 / 1.35)
        return int(round(max(0.0, min(1.0, t)) * 100))

    @staticmethod
    def _migrate_diffuse_level(old_level: int) -> int:
        """Map a pre-v4 linear Diffuse slider position to the new curve.

        Old: knee = MAX - t*range (linear). New: perceptual exponent 1.35.
        Recover the old physical knee, then find the equivalent new position
        so existing users keep approximately their old look.
        """
        t_old = max(0.0, min(1.0, old_level / 100.0))
        old_knee = SPEC_KNEE_MAX - t_old * (SPEC_KNEE_MAX - SPEC_KNEE_MIN)
        return SphereDocker._knee_to_level(old_knee)

    @staticmethod
    def _size_to_shininess(level: int) -> float:
        """Map the gear's 0-100 highlight size onto engine shininess 64-8.

        The gear is a quick convenience control, not the full expert range:
        size 100 is softest (shininess 8), size 0 tightest (64). The
        pathological giant wash at shininess 1 stays exclusive to Advanced
        Specular. Perceptual (t^2) like before, remapped onto 64-8.
        """
        t = max(0.0, min(1.0, level / 100.0))
        return 64.0 - 56.0 * (t ** 2.0)

    @staticmethod
    def _shininess_to_size(shininess: float) -> int:
        """Inverse of :meth:`_size_to_shininess`, for restoring a saved value."""
        import math
        s = max(8.0, min(64.0, shininess))
        t = math.sqrt((64.0 - s) / 56.0)
        return int(round(max(0.0, min(1.0, t)) * 100))

    def _on_glow_changed(self, value):
        try:
            LOG.info(f"_on_glow_changed value={value}")
            self.processor.set_glow_intensity(value / 100.0)
            self._rebuild_sphere()
        except Exception as exc:
            LOG.exception("_on_glow_changed failed")

    def _on_rim_changed(self, value):
        try:
            self.processor.set_rim_light(value / 100.0)
            self._sync_rim_interlock()
            self._rebuild_sphere()
        except Exception as exc:
            LOG.exception("_on_rim_changed failed")

    def _on_rim_mix_changed(self, value):
        try:
            self.processor.set_rim_sky_mix(value / 100.0)
            self._rebuild_sphere()
        except Exception as exc:
            LOG.exception("_on_rim_mix_changed failed")

    def _on_sky_changed(self, value):
        try:
            self.processor.set_sky_bounce(value / 100.0)
            self._rebuild_sphere()
        except Exception as exc:
            LOG.exception("_on_sky_changed failed")

    def _on_ground_changed(self, value):
        try:
            self.processor.set_ground_bounce(value / 100.0)
            self._rebuild_sphere()
        except Exception as exc:
            LOG.exception("_on_ground_changed failed")

    # NOTE: the old render-level _on_saturation_changed was removed. Saturation
    # is now a *target color* control (see _on_saturation_changed above); the
    # engine's global tone control lives on the advanced "Tone" row.

    # ------------------------------------------------------------------
    # Toggle handlers
    # ------------------------------------------------------------------
    def _on_preset_clicked(self, key: str, checked: bool) -> None:
        """Toggle a lighting preset.

        A click on a preset switches it on; a click on the one already on
        switches it off and restores the lighting from before any preset was
        switched on (switching straight from one preset to another keeps that
        original state). Moving a slider in between makes the edit the new
        state.

        Driven entirely by the ``_PRESETS`` table, so adding a preset is one
        dict rather than a new button, a new caption, a new tooltip and a new
        copy-pasted handler -- which is how the two originals ended up being
        two near-identical methods that had already drifted apart.
        """
        try:
            LOG.info("_on_preset_clicked key=%s checked=%s", key, checked)
            if not checked:
                return
            preset = next((p for p in _PRESETS if p["key"] == key), None)
            if preset is None:
                return
            if self._active_preset == key:
                self._preset_off()
                return
            if self._active_preset is None:
                self._preset_before = self._preset_snapshot()
            self._active_preset = key
            for btn in self._preset_btns:
                btn.setChecked(btn.key == key)
            # The guard has to stay raised across the *final* rebuild too. Every
            # row update below fires valueChanged -> _rebuild_sphere, and if the
            # flag were already clear that rebuild would uncheck the very
            # button the user just pressed.
            self._applying_preset = True
            try:
                self._apply_preset_values(preset["values"])
                self._render_preset_change()
                # Explicit action: save now rather than waiting out the debounce.
                self._save_settings()
            finally:
                self._applying_preset = False
        except Exception as exc:
            LOG.exception("_on_preset_clicked failed")

    def _restore_lit_preset(self) -> None:
        """Light the preset that was on when the panel was last saved, with
        the lighting it restores, if the loaded lighting still matches it."""
        try:
            s = _settings()
            key = str(s.value("active_preset", "") or "")
            if not key or not self._preset_matches(key):
                return
            before = json.loads(str(s.value("preset_before", "null") or "null"))
            if isinstance(before, dict) and isinstance(before.get("highlight"), list):
                before["highlight"] = tuple(before["highlight"])
            self._active_preset = key
            self._preset_before = before if isinstance(before, dict) else None
            for btn in self._preset_btns:
                btn.setChecked(btn.key == key)
        except Exception:  # pragma: no cover - settings only
            LOG.exception("_restore_lit_preset failed")

    def _render_preset_change(self) -> None:
        """Show a preset's look without the full-render stall.

        A full-quality render is ~150 ms of pure Python and froze the panel
        on every preset click. If that render is cached (a preset toggled
        back off, a look revisited) it shows at once; otherwise the quick
        drag-size preview shows now and the full render follows a beat
        later, once the preview has painted.
        """
        # The rows the preset just moved each queued a full re-render.
        self._rebuild_timer.stop()
        size = int(getattr(self, "_sphere_render_size", SPHERE_RENDER))
        base = self._rgb01(self._to_display(self._targets["base"]))
        try:
            cached = self.processor.has_full_render(base, size, size)
        except Exception:
            cached = False
        if not cached:
            self._slider_dragging = True
            try:
                self._pending_reason = "preset_preview"
                self._rebuild_sphere_now()
            finally:
                self._slider_dragging = False
        QTimer.singleShot(0 if cached else 30, self._preset_final_render)

    def _preset_final_render(self) -> None:
        self._pending_reason = "preset"
        self._rebuild_sphere_now()

    def _preset_matches(self, key: str) -> bool:
        """Whether the lighting still holds preset ``key``'s values."""
        preset = next((p for p in _PRESETS if p["key"] == key), None)
        if preset is None:
            return False
        now = self._preset_snapshot()
        for name, want in preset["values"].items():
            have = now.get(name)
            if have is None:
                continue
            if isinstance(want, str) or isinstance(have, str):
                if str(want) != str(have):
                    return False
            elif isinstance(want, tuple):
                if any(abs(float(a) - float(b)) > 0.01 for a, b in zip(want, have)):
                    return False
            elif abs(float(want) - float(have)) > 1.0:
                return False
        return True

    def _preset_snapshot(self) -> dict:
        """The current lighting, in the same terms as a preset's values."""
        eng = self.processor.engine
        return {
            "highlight": tuple(eng.highlight_color),
            "ambient": int(round(eng.ambient * 100.0)),
            "intensity": int(round(eng.light_intensity * 100.0)),
            "contrast": int(round(eng.contrast * 100.0)),
            "specular": float(eng.shininess),
            "diffuse": self._knee_to_level(eng.spec_knee),
            "glow": int(round(eng.glow_intensity * 100.0)),
            "tone": int(round(eng.saturation * 100.0)),
            "mixer": str(eng.mixer_mode),
            "spec_max": int(round(float(eng.spec_max) * 100.0)),
            "rim": int(round(float(eng.rim_light) * 100.0)),
            "rim_mix": int(round(float(eng.rim_sky_mix) * 100.0)),
            "sky": int(round(float(eng.sky_bounce) * 100.0)),
            "ground": int(round(float(eng.ground_bounce) * 100.0)),
        }

    def _preset_off(self) -> None:
        """Switch the lit preset off: restore the lighting from before it."""
        before, self._preset_before = self._preset_before, None
        self._active_preset = None
        for btn in self._preset_btns:
            btn.setChecked(False)
        if before is None:
            return
        self._applying_preset = True
        try:
            self._apply_preset_values(before)
            self._render_preset_change()
            self._save_settings()
        finally:
            self._applying_preset = False

    def _apply_preset_values(self, values: dict) -> None:
        """Push a preset's values into the engine *and* the slider rows.

        Setting the rows matters as much as the engine: leaving them showing
        the previous preset's numbers made the panel lie about the state it was
        actually rendering.
        """
        p = self.processor
        if "highlight" in values:
            p.set_highlight_color(values["highlight"])
        for name, setter in (("ambient", p.set_ambient),
                             ("intensity", p.set_light_intensity),
                             ("glow", p.set_glow_intensity),
                             ("tone", p.set_saturation)):
            if name in values:
                setter(values[name] / 100.0)
        if "contrast" in values:
            p.set_contrast(values["contrast"] / 100.0)
        if "spec_max" in values:
            p.set_spec_max(values["spec_max"] / 100.0)
        for _name, _setter, _div in (
                ("rim", p.set_rim_light, 100.0),
                ("rim_mix", p.set_rim_sky_mix, 100.0),
                ("sky", p.set_sky_bounce, 100.0),
                ("ground", p.set_ground_bounce, 100.0)):
            if _name in values:
                _setter(values[_name] / _div)
        if "specular" in values:
            p.set_shininess(values["specular"])
            # The gear's highlight-size slider owns the same engine value.
            # Blocked while setting, since the panel reports its whole state on
            # every emission and an unblocked set would push a half-applied mix
            # back into the engine.
            self._settings_panel.highlight_size.blockSignals(True)
            try:
                self._settings_panel.highlight_size.setValue(
                    self._shininess_to_size(values["specular"]))
            finally:
                self._settings_panel.highlight_size.blockSignals(False)
        if "diffuse" in values:
            p.set_spec_knee(self._level_to_knee(values["diffuse"]))
        if "mixer" in values:
            p.set_mixer_mode(values["mixer"])
            self.mixer_row.set_mode(values["mixer"])
        for name, row in (("ambient", self.ambient_row),
                          ("intensity", self.intensity_row),
                          ("intensity", self.power_row),
                          ("contrast", self.contrast_row),
                          ("specular", self.specular_row),
                          ("diffuse", self.diffuse_row),
                          ("glow", self.glow_row),
                          ("tone", self.tone_row),
                          ("rim", self.rim_row),
                          ("rim_mix", self.rim_mix_row),
                          ("sky", self.sky_row),
                          ("ground", self.ground_row)):
            if name in values:
                row.set_value(int(values[name]))

    # ------------------------------------------------------------------
    # Krita integration
    # ------------------------------------------------------------------
    def canvasChanged(self, canvas):
        """Called by Krita when the active canvas/document changes.

        Refreshes the sphere from current state so switching documents keeps it in sync.
        The real external-color listener is the View color-change signal wired below;
        this covers document/canvas switches where no change signal fires per pick.
        """
        try:
            self._on_krita_color_changed()
            # A new view has a new canvas widget to watch for picks.
            self._watch_canvas_picks()
            # A newly loaded document can bring a colour profile Krita did not
            # know before (an embedded Display P3), so the display conversion
            # of the targets may only work now: redraw the swatches and the
            # sphere. Straight through the timer, so no preset is unchecked.
            self._update_preview()
            self._pending_reason = "canvas_changed"
            self._rebuild_timer.start(0)
        except Exception as exc:  # pragma: no cover - Krita-only path
            LOG.exception("canvasChanged sync failed")

    def _active_view(self):
        """Return the active View or None. Import kept local so off-host load can't crash."""
        try:
            from krita import Krita
            app = Krita.instance()
            if app is None:
                return None
            win = app.activeWindow()
            if win is None:
                return None
            return win.activeView()
        except Exception as exc:  # pragma: no cover - Krita-only path
            LOG.exception("_active_view failed")
            return None

    def _managed_color_to_rgb(self, managed):
        """Krita's colour as 8-bit (r, g, b) in Lumina's working space.

        The working space is the active layer's (see _sync_working_space), so
        the numbers match Krita's Specific Color Selector, which by default is
        locked to the current layer's colour space.

        This is what makes "Krita's colour replaces the base" true. A
        foreground keeps the profile it came from: the merged eyedropper gives
        the document's, while typing into the selector over a Display P3
        layer stores P3. Reading raw components without regard to their
        profile adopted a different colour from the one Krita paints -- P3
        ``#743356`` paints as sRGB ``#7d2e57``, yet the base became
        ``#743356`` in an sRGB space.

        So a colour in another profile or model is converted, on a copy,
        through Krita's own colour management. Two shortcuts are avoided:

        * ``colorForCanvas`` converts for the *monitor*. It equals the
          document space only when the monitor profile is the document's,
          which holds on a default Linux setup and not on Windows with a
          calibrated display.
        * A colour already in the working profile is not converted at all:
          an identity conversion can still round by one, and the watcher
          would read that as a fresh pick.

        ``componentsOrdered()`` always returns R,G,B,A; ``components()`` is
        in native order (B,G,R,A for RGBA), which once turned an orange
        sample blue.
        """
        try:
            if hasattr(managed, "componentsOrdered"):
                source = managed
                target = getattr(self, "_space_profile", None) or SRGB_PROFILE
                if str(managed.colorModel()) == "RGBA":
                    # Remember the colour as Krita holds it. A pick is later
                    # re-expressed from this original, not from Lumina's
                    # rounded 8-bit copy, when the layer space changes.
                    self._last_read_origin = (
                        str(managed.colorProfile()),
                        tuple(int(round(max(0.0, min(1.0, v)) * 255))
                              for v in list(managed.componentsOrdered())[:3]))
                if target and (str(managed.colorModel()) != "RGBA"
                               or str(managed.colorProfile()) != target):
                    converted = self._to_document_space(managed, target)
                    if converted is not None:
                        source = converted
                c = list(source.componentsOrdered())
                if len(c) >= 3:
                    return tuple(int(round(max(0.0, min(1.0, v)) * 255))
                                 for v in c[:3])
            if hasattr(managed, "getRgb"):      # plain QColor fallback -> rgba ints
                rgb = managed.getRgb()
                return (rgb[0], rgb[1], rgb[2])
            if hasattr(managed, "components"):
                c = list(managed.components())
                return (int(round(c[0] * 255)), int(round(c[1] * 255)),
                        int(round(c[2] * 255)))
        except Exception as exc:  # pragma: no cover - Krita-only path
            LOG.exception("_managed_color_to_rgb failed")
        return None

    @staticmethod
    def _working_profile():
        """The RGB profile Lumina's numbers should be written in, or None.

        The active layer's profile, because that is what Krita's Specific
        Color Selector shows by default ("Lock to current layer colorspace"):
        with an imported Display P3 screenshot active, Krita says ``#0102b5``
        for what an sRGB document calls ``#0102bd``, and Lumina should say the
        same. Lumina's targets are 8-bit RGB, so a non-RGB layer (a mask, a
        CMYK layer) falls back to the document's profile, and a non-RGB
        document to Krita's default sRGB. None when there is no document, so
        the caller keeps its current space.
        """
        try:
            from krita import Krita
            doc = Krita.instance().activeDocument()
            if doc is None:
                return None
            node = doc.activeNode() if hasattr(doc, "activeNode") else None
            if node is not None and str(node.colorModel()) == "RGBA":
                return str(node.colorProfile())
            if str(doc.colorModel()) == "RGBA":
                return str(doc.colorProfile())
            return SRGB_PROFILE
        except Exception:  # pragma: no cover - Krita-only path
            return None

    # Converted colours, keyed on (from, to, rgb). The display conversions
    # run on every render and swatch refresh; a pick only ever adds a few.
    _CONVERT_CACHE = {}

    @classmethod
    def _convert_qcolor(cls, color, src, dst):
        """``color`` re-expressed from profile ``src`` to ``dst``: same
        colour, different numbers. Identity, without Krita, or on failure."""
        if not src or not dst or src == dst:
            return QColor(color)
        key = (src, dst, color.rgb())
        hit = cls._CONVERT_CACHE.get(key)
        if hit is None:
            try:
                from krita import ManagedColor
                managed = ManagedColor("RGBA", "U8", src)
                # Krita substitutes its default profile for one it has not
                # loaded yet -- Display P3 arrives with the document that
                # embeds it, after Lumina has restored and drawn its saved
                # P3 targets at startup. That "conversion" is a silent no-op;
                # caching it left the sphere drawn from raw P3 numbers (pale)
                # until restart. Only a conversion between the profiles
                # actually asked for is trusted, and cached.
                if str(managed.colorProfile()) != src:
                    return QColor(color)
                managed.setComponents([color.blueF(), color.greenF(),
                                       color.redF(), 1.0])     # native BGRA
                if managed.setColorSpace("RGBA", "U8", dst) is False \
                        or str(managed.colorProfile()) != dst:
                    return QColor(color)
                hit = tuple(int(round(max(0.0, min(1.0, v)) * 255))
                            for v in list(managed.componentsOrdered())[:3])
            except Exception:
                return QColor(color)
            if len(cls._CONVERT_CACHE) > 4096:
                cls._CONVERT_CACHE.clear()
            cls._CONVERT_CACHE[key] = hit
        return QColor(*hit)

    def _to_display(self, color):
        """A working-space colour as the sRGB Qt paints it on screen.

        Lumina's widgets are not colour managed, so a P3 ``#6be845`` painted
        as-is would look like a muddier green than the ``#00eb00`` it is.
        """
        return self._convert_qcolor(color, getattr(self, "_space_profile", None),
                                    SRGB_PROFILE)

    def _from_display(self, color):
        """A colour sampled off Lumina's own widgets, in working-space numbers."""
        return self._convert_qcolor(color, SRGB_PROFILE,
                                    getattr(self, "_space_profile", None))

    def _sync_working_space(self) -> bool:
        """Follow the active layer's colour space, as Krita's selectors do.

        Switching from an sRGB layer to a Display P3 one changes the numbers
        Krita shows for the same colour, so every stored colour is re-written
        in the new space -- same colours, new numbers. The foreground is then
        re-baselined: its numbers changed too, and without that the poll would
        read the switch as a fresh pick and re-derive light and shadow.
        Returns True when the space changed.
        """
        new = self._working_profile()
        old = getattr(self, "_space_profile", None)
        if not new or new == old:
            return False
        # Re-express each target from where it came from, never from the
        # previous conversion: 8-bit numbers drift when converted back and
        # forth (sRGB #00a893 -> P3 #4ba593 -> sRGB #05a892, red near zero is
        # that sensitive). A target untouched since the last switch, or a
        # pick, still carries its origin; anything edited since originates
        # in the old space.
        memo = getattr(self, "_space_memo", None)
        if memo is None:
            memo = self._space_memo = {}
        for key, color in list(self._targets.items()):
            prior = memo.get(key)
            if prior is not None and prior[2] == color.rgb():
                origin_profile, origin_rgb = prior[0], prior[1]
            else:
                origin_profile, origin_rgb = old, (color.red(), color.green(),
                                                   color.blue())
            converted = self._convert_qcolor(QColor(*origin_rgb),
                                             origin_profile, new)
            self._targets[key] = converted
            memo[key] = (origin_profile, origin_rgb, converted.rgb())
        for name in ("_original_color", "_chosen_color"):
            color = getattr(self, name, None)
            if color is not None:
                setattr(self, name, self._convert_qcolor(color, old, new))
        self._space_profile = new
        LOG.info("SPACE %s -> %s", old, new)
        self._sampler_baseline = self._current_krita_rgb()
        self._sync_sliders_from_state()
        self._update_preview()
        # Re-render and persist, but not via _rebuild_sphere: that also clears a
        # checked preset, and switching layers changes no look parameter.
        self._pending_reason = "space_change"
        self._rebuild_timer.start(0)
        self._mark_settings_dirty()
        return True

    @staticmethod
    def _to_document_space(managed, profile):
        """A copy of ``managed`` converted to RGBA/U8 ``profile``, or None.

        Built from the full-precision native components, so nothing is lost
        before Krita's colour management does the conversion.
        """
        try:
            from krita import ManagedColor
            copy = ManagedColor(str(managed.colorModel()),
                                str(managed.colorDepth()),
                                str(managed.colorProfile()))
            copy.setComponents(list(managed.components()))
            if copy.setColorSpace("RGBA", "U8", profile) is False \
                    or str(copy.colorProfile()) != profile:
                return None     # profile not loaded yet: see _convert_qcolor
            return copy
        except Exception:  # pragma: no cover - Krita-only path
            LOG.exception("foreground conversion to %s failed", profile)
            return None

    def _current_krita_rgb(self):
        """Best-effort current foreground/background RGB from the active View,
        in Lumina's working space (the active layer's)."""
        view = self._active_view()
        if view is None:
            return None
        # Every reader goes through here, so the space is always current
        # before numbers are compared. Re-entrant: the sync's own baseline
        # read finds the space already switched.
        self._sync_working_space()
        # The eyedropper sets the FOREGROUND color; prefer it, fall back to background.
        for getter in ("foregroundColor", "backgroundColor"):
            fn = getattr(view, getter)
            try:
                managed = fn()  # ManagedColor (or QColor on some versions)
            except Exception as exc:  # pragma: no cover - Krita-only path
                LOG.exception(f"get {getter} failed")
                continue
            if managed is None:
                continue
            rgb = self._managed_color_to_rgb(managed)
            if rgb is not None:
                return rgb
        return None

    def _on_krita_color_changed(self, *args):
        """Mirror an external color change into the sphere. NEVER re-send to Krita (avoids a loop)."""
        try:
            if getattr(self, "_sync_guard", False):
                # This change is the echo of our own _send_to_krita() call.
                # Adopting it here would clobber the base target when the user
                # picked a color for the light or shadow instead.
                return
            cur = self._current_krita_rgb()
            if cur is None:
                return
            # Compare against the target the colour will land on: with the
            # shadow selected, a pick equal to the base is still a change.
            stored = self._targets[self._active_target].getRgb()[:3]
            if cur != stored:
                self._note_external_color(
                    cur, getattr(self, "_awaiting_sampler", False))
        except Exception as exc:  # pragma: no cover - Krita-only path
            LOG.exception("_on_krita_color_changed failed")

    def _enter_external_preview(self) -> None:
        """Drop to the drag render profile for the length of an external stream.

        A native picker drag is a continuous stream, so it gets exactly the
        treatment a slider drag already gets: the grid is capped at
        ``DRAG_RENDER`` (~15 ms instead of ~120 ms at 200px) and the blur pass
        is suspended. Without this the 50 ms throttle is meaningless -- a 120 ms
        render can never keep up with a 50 ms cadence, and the event loop stays
        saturated for the whole drag. :meth:`_apply_external_final` restores
        full quality, which is where the image the user is left looking at comes
        from.
        """
        if self._external_preview:
            return
        self._external_preview = True
        try:
            self._ext_smooth_saved = self.processor.engine.smooth
            self.processor.set_smooth(0.0)
            # The engine log is a rolling average; without this the preview
            # lines would keep reprinting the full-quality smooth/geometry
            # costs from before the stream started.
            self.processor.engine.clear_timing("smooth", "geometry")
        except Exception:  # pragma: no cover - UI only
            LOG.exception("external preview enter failed")

    def _exit_external_preview(self) -> None:
        """Restore full render quality after the stream goes quiet."""
        if not getattr(self, "_external_preview", False):
            return
        self._external_preview = False
        try:
            saved = getattr(self, "_ext_smooth_saved", None)
            if saved is not None:
                self.processor.set_smooth(saved)
            self._ext_smooth_saved = None
            # Symmetric with enter: the full-quality final must not average
            # in the preview's cheaper samples.
            self.processor.engine.clear_timing("smooth", "geometry")
        except Exception:  # pragma: no cover - UI only
            LOG.exception("external preview exit failed")

    def _note_external_color(self, cur, full: bool) -> None:
        """Single entry for the signal path and the poll path.

        Records the newest color immediately, then renders at most every
        EXT_RENDER_MS while the stream is live, so a native-picker drag
        tracks at ~20 FPS instead of re-rendering per event. ``full``
        selects full lighting derivation (sampler) versus base-only
        tracking. Stream end is declared by the poll end detector (see
        _note_external_unchanged), never by a quiet timer -- so no blocking
        full render can fire mid-drag.
        """
        try:
            # While the sphere is pressed, and briefly after its last pick, a
            # change of Krita's colour can only be Lumina's own pick coming
            # back (nobody uses Krita's selector mid-drag on the sphere). It
            # is never adopted: adopting it re-derived the targets from a
            # brush pick and changed the sphere under the pointer.
            since_pick = (time.monotonic() - getattr(self, "_last_sphere_pick", 0.0)) * 1000.0
            sphere_down = bool(getattr(self._sphere, "_pointer_down", False))
            if sphere_down or since_pick < self.SPHERE_ECHO_MS:
                self._sampler_baseline = tuple(cur)
                LOG.info("EXT_IGNORED %s: sphere pick echo (down=%d, %.0f ms after pick)",
                         "#%02x%02x%02x" % tuple(cur[:3]), int(sphere_down), since_pick)
                return
            was_preview = getattr(self, "_external_preview", False)
            if not was_preview:
                # Context for the first colour of each Krita stream, so an
                # unexpected change can be traced to its source.
                LOG.info("EXT_SOURCE %s full=%d sphere_down=%d since_sphere_pick=%.0fms tool=%s",
                         "#%02x%02x%02x" % tuple(cur[:3]), int(bool(full)), int(sphere_down),
                         since_pick, self._tool_for_log())
            self._latest_external = (tuple(cur), bool(full))
            # How Krita itself holds this colour (profile, numbers); see
            # _sync_working_space for why the original is kept.
            self._latest_origin = getattr(self, "_last_read_origin", None)
            self._enter_external_preview()
            self._ext_unchanged = 0
            if not was_preview:
                # Re-entering after a declared end: cancel the post-stream
                # save still waiting out its delay. The dirty flag stays set;
                # the next stream end flushes.
                timer = getattr(self, "_save_timer", None)
                if timer is not None:
                    timer.stop()
            if not getattr(self, "_ext_render_pending", False):
                self._ext_render_pending = True
                self._ext_render_timer.start(EXT_RENDER_MS)
        except Exception:  # pragma: no cover - UI only
            LOG.exception("_note_external_color failed")

    SPHERE_ECHO_MS = 350

    def _tool_for_log(self) -> str:
        """Krita's active tool, for the log (best effort)."""
        try:
            from PyQt5.QtWidgets import QToolButton
            win = self.window()
            for b in win.findChildren(QToolButton):
                name = b.objectName()
                if b.isCheckable() and b.isChecked() and name.startswith(("Kis", "Krita")):
                    return name
        except Exception:
            pass
        return "?"

    def _note_external_unchanged(self) -> None:
        """One poll tick with no foreground change: the end detector.

        After EXT_END_POLLS consecutive unchanged ticks inside a live
        preview, the drag is declared over and exactly one full-quality
        final is queued. The poll path only detects and transitions state;
        it never renders (see _apply_external_final). A new color resets the
        count in _note_external_color.
        """
        try:
            if not getattr(self, "_external_preview", False):
                return
            n = int(getattr(self, "_ext_unchanged", 0)) + 1
            self._ext_unchanged = n
            if n >= EXT_END_POLLS:
                self._ext_unchanged = 0  # queue once; re-armed by new color
                QTimer.singleShot(0, self._apply_external_final)
        except Exception:  # pragma: no cover - UI only
            LOG.exception("_note_external_unchanged failed")

    def _apply_external_color(self) -> None:
        """Render the newest pending external color, if any.

        One apply = one state update, one synchronization transaction, one
        scheduled rebuild. The UI sync runs under ``_syncing`` so no row handler
        fires a side effect of its own; the gradient tracks and preview are
        refreshed explicitly here instead.
        """
        try:
            if not getattr(self, "_ext_render_pending", False):
                return
            self._ext_render_pending = False
            latest = getattr(self, "_latest_external", None)
            if latest is None:
                return
            if getattr(self, "_syncing", False):
                # Settings are being applied right now. Leave the color pending
                # so the end-detector final picks it up rather than marking it
                # applied and silently dropping it.
                return
            cur, full = latest
            self._last_applied_external = latest
            self._ext_applies += 1
            LOG.info("EXT_APPLY seq=%d color=%s full=%s",
                     self._ext_applies, cur, full)
            if full:
                self._apply_sampled_color(cur)
            elif self._active_target != "base":
                # The active target is a switch: Krita's colour replaces
                # whichever of shadow / base / light is selected.
                self._replace_target(self._active_target, QColor(*cur))
            else:
                self._set_base_color(QColor(*cur))  # int rgb
                # _syncing already guards the setValue calls inside
                # _sync_sliders_from_state; the effects its row handlers would
                # have had are replayed once, here, after the sync.
                self._sync_sliders_from_state()
                self._rebuild_sphere()
                self._update_preview()
            # The picked target originates in Krita's own copy of the colour,
            # so switching layers re-expresses it from there and it keeps
            # matching the brush exactly.
            origin = getattr(self, "_latest_origin", None)
            key = self._active_target
            if origin is not None and key in self._targets:
                memo = getattr(self, "_space_memo", None)
                if memo is None:
                    memo = self._space_memo = {}
                memo[key] = (origin[0], origin[1], self._targets[key].rgb())
        except Exception:  # pragma: no cover - Krita-only path
            LOG.exception("_apply_external_color failed")

    @staticmethod
    def _render_stats(samples):
        """avg/p95/max of individual render durations.

        Avg alone hides a single 200 ms hitch inside eighteen 30 ms renders
        (avg 39 ms looks fine); max reveals it, p95 keeps one OS scheduling
        fluke from dominating the story.
        """
        samples = list(samples or [])
        if not samples:
            return (0.0, 0.0, 0.0)
        ordered = sorted(samples)
        avg = sum(samples) / len(samples)
        p95 = ordered[min(len(ordered) - 1, int(0.95 * (len(ordered) - 1)))]
        return (avg, p95, ordered[-1])

    def _apply_external_final(self) -> None:
        """Exactly one full-quality render at declared stream end, then save.

        Queued by the end detector -- never called from the poll tick, so
        the poll path stays detection-only. Applies any color newer than the
        last throttle tick, leaves preview mode (restores full quality),
        renders once, logs the burst summary, and arms the post-stream save
        flush. A new external color cancels the pending save and re-enters
        preview (see _note_external_color).

        Invariant: preview path renders cheap only, this path renders the
        one full image, the poll path renders nothing.
        """
        try:
            if not getattr(self, "_external_preview", False):
                return  # double-queued, or a color already re-armed preview
            if getattr(self, "_syncing", False):
                # Settings mid-apply: retry shortly instead of rendering a
                # half-applied state. Preview stays on; the end detector
                # re-queues if the stream is truly over.
                QTimer.singleShot(50, self._apply_external_final)
                return
            latest = getattr(self, "_latest_external", None)
            self._exit_external_preview()
            if latest is not None and \
                    latest != getattr(self, "_last_applied_external", None):
                self._ext_render_pending = True
                self._apply_external_color()
            # Always schedule one final: if the last throttle tick already
            # applied this color, the sphere is still sitting on the 96px blurred
            # preview and the full-quality image still has to be produced.
            # Flagged so _rebuild_sphere_now logs it standalone as EXT_FINAL.
            self._ext_final_pending = True
            self._rebuild_sphere(reason="external_final")
            avg, p95, peak = self._render_stats(
                getattr(self, "_ext_render_samples", None) or [])
            LOG.info("EXT_SUMMARY applies=%d renders=%d avg_ms=%.1f p95_ms=%.1f max_ms=%.1f (final)",
                     self._ext_applies, self._ext_renders, avg, p95, peak)
            self._ext_applies = 0
            self._ext_renders = 0
            self._ext_render_ms = 0.0
            self._ext_render_samples = []
            self._arm_post_stream_save()
        except Exception:  # pragma: no cover - Krita-only path
            LOG.exception("_apply_external_final failed")

    def _watch_krita_colors(self):
        """Attach to View color-change signals where available (Krita >=6.0.3)."""
        try:
            view = self._active_view()
            if view is None:
                return  # no active window yet; nothing safe to attach
            # This runs from __init__, showEvent and _on_refresh, so guard
            # against attaching the same signal twice.
            if getattr(self, "_watchers_armed", False):
                return
            for name in ("foreground", "background"):
                sig_name = f"{name}ColorChanged"
                signal = getattr(view, sig_name, None)
                if callable(signal):
                    try:
                        conn = signal.connect(self._on_krita_color_changed)
                        # keep (signal, connection) refs so PyQt won't GC the slot
                        self._krita_connections.append((sig_name, conn))
                        LOG.info(f"connected view.{sig_name}")
                    except Exception as exc:  # pragma: no cover - Krita-only path
                        LOG.exception(f"connect {sig_name} failed")
                else:
                    # Permanent record of poll-only mode: the View object has
                    # no such signal (not a connection bug on our side), so the
                    # native C++ provider signal is unreachable from Python.
                    LOG.info("view.%s unavailable (%r) - poll-only",
                             sig_name, signal)
            self._watchers_armed = True
        except Exception as exc:  # pragma: no cover - Krita-only path
            LOG.exception("_watch_krita_colors failed")
    def showEvent(self, event):
        """Re-arm external color sync when the docker becomes visible.

        __init__ only ran _watch_krita_colors() before any active window/view existed
        (Krita hadn't placed us yet), so it early-returned and no View signals were attached
        — leaving external picks / eye-droppers silent until a restart. Showing the dock re-arms
        the watchers now that a window exists; kept cheap + guarded, mirroring canvasChanged."""
        try:
            LOG.info("showEvent: docker being shown")
            super().showEvent(event)
            self._watch_krita_colors()
            self._start_sampler_watch()
            self._watch_canvas_picks()
            LOG.info("showEvent: re-sync armed OK")
        except Exception as exc:  # pragma: no cover
            LOG.exception("showEvent failed")

    def resizeEvent(self, event):
        try:
            LOG.info(f"resizeEvent size={self.width()}x{self.height()}")
            super().resizeEvent(event)
        except Exception as exc:
            LOG.exception("resizeEvent failed")

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------
    def _teardown(self) -> None:
        """Unregister what Qt still holds, and let go of the drag filter.

        The dangerous one is the event filter, and it is no longer this object:
        _TitleBarDragFilter is a singleton that survives us and no-ops on a dead
        weakref. Detaching it here is tidiness, not safety -- which is the whole
        point of the proxy, because neither closeEvent nor __del__ runs early
        enough to be relied on.

        The timer and the View signal connections are worth stopping explicitly:
        they keep firing (or keep our bound method alive from the Qt side) after
        the dock is gone.

        Every step is guarded: a teardown that raises would leave the remaining
        steps undone, which is the worse outcome.
        """
        try:
            _TitleBarDragFilter.instance().detach()
        except Exception:  # pragma: no cover - teardown must not raise
            pass
        try:
            _CanvasPickFilter.instance().detach()
        except Exception:  # pragma: no cover - teardown must not raise
            pass
        if _is_dead(self):
            # The C++ widget is already gone; touching it would fault. The
            # filter is detached, which is the part that mattered.
            return
        try:
            timer = getattr(self, "_sampler_timer", None)
            if timer is not None:
                timer.stop()
        except Exception:  # pragma: no cover
            pass
        pool = getattr(self, "_full_pool", None)
        if pool is not None:
            self._full_pool = None
            self._cancel_full_render()
            try:
                pool.shutdown(wait=False)
            except Exception:  # pragma: no cover
                pass
        self._restore_switch_interval()
        for _name in ("_ext_render_timer", "_full_poll",
                      "_save_timer", "_rebuild_timer"):
            try:
                timer = getattr(self, _name, None)
                if timer is not None:
                    timer.stop()
            except Exception:  # pragma: no cover
                pass
        try:
            view = self._active_view()
            for sig_name, _conn in getattr(self, "_krita_connections", []):
                signal = getattr(view, sig_name, None)
                if signal is not None:
                    try:
                        signal.disconnect(self._on_krita_color_changed)
                    except (TypeError, RuntimeError):
                        pass  # already gone
            self._krita_connections = []
            self._watchers_armed = False
        except Exception:  # pragma: no cover
            pass

    def closeEvent(self, event):
        try:
            LOG.info("closeEvent")
            # Flush any debounced settings write before teardown stops timers.
            self._flush_settings()
            self._teardown()
            super().closeEvent(event)
        except Exception as exc:
            LOG.exception("closeEvent failed")

    def __del__(self):
        # Belt and braces: stop the timer and drop the weakref even if the
        # widget is collected without closeEvent running.
        try:
            self._teardown()
        except Exception:  # pragma: no cover
            pass

    def _send_to_krita(self, color: QColor):
        """Send the picked color to Krita's foreground.

        ``color`` is in Lumina's working space, so it is tagged with that
        profile (the active layer's): Krita stores exactly these numbers and
        its selector shows them back unchanged. ``fromQColor(color, canvas)``
        is only the fallback: it reads the QColor through the canvas's
        *display* converter, which shifts it on any machine whose monitor
        profile is not the document's.
        """
        try:
            from krita import Krita, ManagedColor
            view = Krita.instance().activeWindow().activeView()
            if view is None:
                return
            canvas = view.canvas()
            managed = None
            profile = getattr(self, "_space_profile", None)
            if profile:
                managed = ManagedColor("RGBA", "U8", profile)
                # setComponents takes native order: B,G,R,A for RGBA.
                managed.setComponents([color.blueF(), color.greenF(),
                                       color.redF(), 1.0])
            else:
                managed = ManagedColor.fromQColor(color, canvas)
            if managed is not None:
                # Block the external-sync handler from adopting our own echo,
                # which would otherwise overwrite the base target whenever the
                # light or shadow target was the one being picked.
                self._sync_guard = True
                view.setForeGroundColor(managed)
                # Choosing a colour on the orb writes the foreground, and the
                # watcher would otherwise read that back as a canvas sample and
                # re-derive base/light/shadow from a mere click. Baseline it on
                # what Krita actually *stored* rather than on the value we sent:
                # the round trip through ManagedColor is not byte-exact, so
                # comparing against the requested colour still looked like a
                # change and turned the sphere black.
                self._sampler_baseline = self._current_krita_rgb()
                QTimer.singleShot(0, self._clear_sync_guard)
        except Exception as exc:  # pragma: no cover - Krita-only path
            print(f"Lumina: send-to-krita failed - {exc}")

    def _clear_sync_guard(self) -> None:
        self._sync_guard = False

    # NOTE: the sphere NEVER assigns colours to its own targets. Clicking or
    # dragging it only chooses a colour for drawing (Krita's foreground).
    # The sphere's base/light/shadow are changed by the sliders, the target
    # dots, or the eyedropper tool -- nothing else.
    # Clicking or dragging the sphere only drives the brush (Krita foreground) and
    # the hover preview. The sphere's colors change from the sliders, the target
    # dots, or the document eyedropper -- never from clicking the sphere, which used
    # to re-render the sphere mid-drag and run the picked color away.

    def _on_sphere_brush(self, color):
        """Choose a colour to draw with.

        Fires on press and on every move during a drag. The colour is sent to
        Krita's foreground and kept in the right (active) swatch so it survives
        leaving the sphere. The sphere's own target colours are never touched.
        """
        try:
            # The sphere is drawn in sRGB for the screen (see _to_display), so a
            # sampled pixel is converted back into working-space numbers
            # before it becomes the brush colour.
            chosen = self._from_display(color)
            self._chosen_color = chosen
            # The swatch and hex box keep showing the selected target: a
            # sphere click only chooses a brush colour, it never changes a
            # target (the sampler ring shows what is under the pointer).
            self._last_sphere_pick = time.monotonic()
            self._send_to_krita(chosen)
            self._recent_pending = QColor(chosen)
            self._recent_timer.start()
        except Exception as exc:  # pragma: no cover - Krita-only path
            LOG.exception("_on_sphere_brush failed")

    def _on_sphere_target(self, color) -> None:
        """Shift-click on the sphere: the colour becomes the selected
        target's, as typing its hex would make it (the one exception to
        sphere clicks only choosing the brush)."""
        try:
            self._assign_target(self._from_display(color))
        except Exception as exc:  # pragma: no cover - UI only
            LOG.exception("_on_sphere_target failed")

    def _commit_recent(self) -> None:
        """Add the settled sphere pick to Recent picks."""
        color, self._recent_pending = self._recent_pending, None
        if color is not None and getattr(self, "_recent", None) is not None:
            self._recent.add(color)
            self._mark_settings_dirty()

    def _on_recent_assign(self, color: QColor) -> None:
        """A recent pick double-clicked: it becomes the selected target's
        colour, as typing its hex would make it."""
        try:
            self._assign_target(QColor(color))
        except Exception as exc:  # pragma: no cover - UI only
            LOG.exception("_on_recent_assign failed")

    def _on_recent_pick(self, color: QColor) -> None:
        """A recent pick clicked: it becomes the brush colour again, exactly
        as a click on the sphere would make it."""
        try:
            self._chosen_color = QColor(color)
            self._send_to_krita(QColor(color))
            self._recent.add(color)
            self._mark_settings_dirty()
        except Exception as exc:  # pragma: no cover - Krita-only path
            LOG.exception("_on_recent_pick failed")

    def _on_sphere_hover(self, color):
        """Show the RGB readout and sampling ring while over the sphere.

        The right (active) swatch previews the colour under the pointer. When the
        pointer leaves it falls back to the colour the user last *chose* by
        clicking, rather than reverting to the sphere's base -- otherwise a
        deliberately picked drawing colour appeared not to have been kept.
        """
        try:
            if color is None:
                self._readout.setText("")
                self.cylinder.set_label("")
                self.cylinder.hide()
                self._update_preview()
            else:
                # Read out the numbers a click would send, not the screen's.
                value = self._from_display(color)
                # In the sliders' own terms (perceptual hue, saturation and
                # lightness); the hex is on the lemon's pill, so the RGB
                # numbers here only repeated it.
                h, c, L = _rgb_to_lch((value.red(), value.green(), value.blue()))
                self._readout.setText("H %d\u00b0   S %d%%   L %d%%" % (
                    int(round(h)) % 360, int(round(c * 100)), int(round(L * 100))))
                # The hex a click would send, under the lemon.
                self.cylinder.set_label(value.name())
                self.cylinder.set_color(color)
        except Exception as exc:  # pragma: no cover - cosmetic only
            LOG.exception("_on_sphere_hover failed")

    @staticmethod
    def _set_swatch_color(swatch, color: QColor) -> None:
        swatch.setStyleSheet(
            "QFrame { background-color: %s; border-radius: 5px; }" % color.name())

    def _on_eyedropper_tool(self) -> None:
        """Activate Krita's Color Sampler tool for a real canvas eyedropper.

        The next color the user samples on the canvas is routed through
        :meth:`_on_krita_color_changed`, which distributes it across base, light
        and shadow.
        """
        try:
            from krita import Krita
            app = Krita.instance()
            # Krita's Color Sampler *tool* is the real canvas eyedropper:
            # activate it, then the user clicks any pixel.
            #
            # Deliberately NOT falling back to the "sample_screen_color" action:
            # that is the screen-wide magnifier picker, which grabs the pointer
            # in a modal loop and presents as a frozen UI.
            action = None
            for candidate in app.actions():
                if candidate.objectName() == "KritaSelected/KisToolColorSampler":
                    action = candidate
                    break
            if action is None:
                raise RuntimeError("KisToolColorSampler action not found")
            # Remember the tool the user was on so it can be handed back after
            # the sample, and the eyedropper re-arms itself for the next pick.
            self._sampler_prev_tool = self._active_tool_name(app)
            self._start_sampler_watch()
            action.trigger()
            self._eyedropper_btn.setChecked(True)
            LOG.info("color sampler tool activated (returning to %s afterwards)",
                     self._sampler_prev_tool)
        except Exception as exc:  # pragma: no cover - Krita-only path
            self._awaiting_sampler = False
            self._eyedropper_btn.setChecked(False)
            LOG.exception("_on_eyedropper_tool failed")
            print(f"Lumina: eyedropper tool failed - {exc}")

    @staticmethod
    def _active_tool_name(app):
        """objectName of the currently active Krita tool action, if any."""
        try:
            for a in app.actions():
                if a.isCheckable() and a.isChecked() and "Tool" in a.objectName():
                    return a.objectName()
        except Exception:  # pragma: no cover - Krita-only path
            pass
        return None

    def _restore_previous_tool(self) -> None:
        """Hand the previous tool back so picking can be repeated.

        Only ever runs for a sample the eyedropper actually asked for. It used
        to run from ``_apply_sampled_color``'s finally-block for *every*
        foreground change, and ``_sampler_prev_tool`` was never cleared once
        set -- so after one eyedropper use, every native-picker drag event
        triggered a Krita tool action, re-armed the 200 ms poll and unset the
        button, on every tick. Triggering an action walks all of Krita's
        action list and switches tools, which is exactly the kind of thing that
        makes the whole application feel sluggish mid-drag.
        """
        name = getattr(self, "_sampler_prev_tool", None)
        if not name:
            return
        try:
            btn = getattr(self, "_eyedropper_btn", None)
            if btn is None or not btn.isChecked():
                # Not an eyedropper-armed sample (a native picker change, or the
                # user already cancelled). Leaving the tool alone is correct.
                return
            from krita import Krita
            app = Krita.instance()
            for a in app.actions():
                if a.objectName() == name:
                    a.trigger()
                    self._eyedropper_btn.setChecked(False)
                    LOG.info("returned to tool %s after sampler pick", name)
                    # Re-arm so the next canvas click samples again, and forget
                    # the tool so nothing else can fire this path.
                    self._sampler_prev_tool = None
                    self._start_sampler_watch()
                    return
        except Exception as exc:  # pragma: no cover - Krita-only path
            LOG.exception("_restore_previous_tool failed")

    # --- Foreground watcher -------------------------------------------------
    # A 50 ms QTimer that reads the brush colour and redistributes it across
    # base/light/shadow. Started from __init__, so it runs for as long as the
    # docker is loaded.
    #
    # It exists because ``View.foregroundColorChanged`` is exposed on Krita 5.x
    # but never actually emitted, so _watch_krita_colors connects signals that
    # never fire. Polling is the only thing that makes external colour picks
    # reach the sphere. The native C++ KisCanvasResourceProvider::sigFGColorChanged
    # exists but is not exposed through the public Python API, so no private
    # bridge -- just a fast poll. Each tick's read cost is self-measured (see
    # POLL_COST in the log); 50 ms keeps detection at the render throttle rate.
    #
    # What it touches, exhaustively:
    #   reads  View.foregroundColor() -> an (r,g,b) int tuple, kept in _sampler_baseline
    #   writes that tuple, plus the plugin's own QSettings file and log file
    #   never  reads the document, reads pixels, opens a file, or touches the network
    #
    # It is the plugin's only unconditional background activity. See
    # _poll_sampler for the change-detection, and note the side effect: any
    # foreground change from *any* source (palette, colour selector docker, a
    # script) is treated as a fresh sample and redistributed, not just the
    # eyedropper. That is the intended behaviour, not an accident.
    SAMPLER_POLL_MS = 50

    def _watch_canvas_picks(self) -> None:
        """Attach the canvas pick filter to the current window's canvases."""
        try:
            from krita import Krita
            window = Krita.instance().activeWindow()
            if window is None:
                return
            pick = _CanvasPickFilter.instance()
            pick.attach(self)
            added = pick.watch(window.qwindow())
            if added:
                LOG.info("canvas pick watch on %d canvas widget(s)", added)
        except Exception:  # pragma: no cover - Krita-only path
            LOG.exception("_watch_canvas_picks failed")

    def _on_canvas_release(self, event) -> None:
        """A release on the canvas: if it ended a colour pick, adopt it.

        A pick is a release while the Color Sampler tool is active, or with
        Ctrl held (Krita's quick-sample). The colour is read a moment later,
        once Krita has stored it.
        """
        try:
            ctrl = bool(event.modifiers() & Qt.ControlModifier)
            if not ctrl and not self._color_sampler_active():
                return
            QTimer.singleShot(60, self._adopt_canvas_pick)
        except Exception:  # pragma: no cover - UI only
            LOG.exception("_on_canvas_release failed")

    def _color_sampler_active(self) -> bool:
        try:
            from krita import Krita
            return self._active_tool_name(Krita.instance()) == \
                "KritaSelected/KisToolColorSampler"
        except Exception:  # pragma: no cover - Krita-only path
            return False

    def _adopt_canvas_pick(self) -> None:
        """Put Krita's (just picked) colour into the selected target, even
        when Krita's colour did not change."""
        try:
            cur = self._current_krita_rgb()
            if cur is None:
                return
            if tuple(self._targets[self._active_target].getRgb()[:3]) == tuple(cur):
                return              # already there: nothing to do
            LOG.info("CANVAS_PICK %s -> %s", "#%02x%02x%02x" % tuple(cur),
                     self._active_target)
            self._sampler_baseline = cur
            self._note_external_color(cur, True)
        except Exception:  # pragma: no cover - UI only
            LOG.exception("_adopt_canvas_pick failed")

    def _start_sampler_watch(self) -> None:
        """Begin/refresh the persistent foreground watcher.

        The baseline is reset to whatever the brush is now, so arming the
        eyedropper does not treat the existing colour as a fresh sample.
        """
        if getattr(self, "_sampler_timer", None) is None:
            self._sampler_timer = QTimer(self)
            self._sampler_timer.setInterval(self.SAMPLER_POLL_MS)
            self._sampler_timer.timeout.connect(self._poll_sampler)
        self._sampler_baseline = self._current_krita_rgb()
        self._sampler_timer.start()

    def _poll_sampler(self) -> None:
        """Feed the shared external-color pipeline on foreground change.

        Compares against the last-seen value and does nothing on a tick where
        the brush colour is unchanged, so an idle poll is one foreground read
        and an int-tuple compare. Rendering itself is throttled in
        _note_external_color; the poll never renders directly.

        The read cost is self-measured once per session (POLL_COST in the
        log): the first 100 ticks time ``_current_krita_rgb()`` and report
        avg/max. That line is the evidence the 50 ms interval is cheap.
        """
        try:
            if self._sync_guard:
                return          # this change is our own echo; ignore it
            t0 = time.monotonic()
            cur = self._current_krita_rgb()
            read_ms = (time.monotonic() - t0) * 1000.0
            if not getattr(self, "_poll_cost_logged", False):
                costs = getattr(self, "_poll_cost_ms", None)
                if costs is None:
                    costs = self._poll_cost_ms = []
                costs.append(read_ms)
                if len(costs) >= 100:
                    self._poll_cost_logged = True
                    LOG.info("POLL_COST n=%d avg=%.3fms max=%.3fms",
                             len(costs), sum(costs) / len(costs), max(costs))
            if cur is None:
                return
            if cur == self._sampler_baseline:
                self._note_external_unchanged()
                return
            self._sampler_baseline = cur
            self._note_external_color(cur, True)
        except Exception as exc:  # pragma: no cover - Krita-only path
            LOG.exception("_poll_sampler failed")

    def _apply_sampled_color(self, rgb) -> None:
        """Route a colour picked in Krita to the active target.

        The active target is a switch. With the base selected, the pick is
        distributed across base, light and shadow as before. With the shadow
        or light selected, the pick replaces that target alone and the other
        two are left as they are -- every Krita colour source (the
        eyedropper, the Specific and Advanced Color Selectors, a palette)
        arrives through here, so they all follow the switch.
        """
        try:
            color = QColor(*rgb)
            target = self._active_target
            LOG.info("eyedropper sampled %s -> %s", color.name(), target)
            # Say why the sphere just changed: Krita's colour (a selector, a
            # canvas sample, Ctrl-click) replaced the selected target.
            self._show_krita_cue(target, color)
            if target == "base":
                self._distribute_from(color)
                self._rebuild_sphere()
                self._sync_sliders_from_state()
                self._update_preview()
            else:
                self._replace_target(target, color)
        except Exception as exc:  # pragma: no cover - UI only
            LOG.exception("_apply_sampled_color failed")
            print(f"Lumina: apply sampled colour failed - {exc}")
        finally:
            # Give the user their tool back after a canvas sample.
            if getattr(self, "_sampler_prev_tool", None):
                QTimer.singleShot(0, self._restore_previous_tool)

    def _show_krita_cue(self, target: str, color: QColor) -> None:
        readout = getattr(self, "_readout", None)
        if readout is None:
            return
        self._cue_text = "%s \u2190 Krita colour %s" % (
            self.TARGET_CAPTIONS.get(target, target), color.name())
        readout.setText(self._cue_text)
        timer = getattr(self, "_cue_timer", None)
        if timer is None:
            timer = self._cue_timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._clear_krita_cue)
        timer.start(2500)

    def _clear_krita_cue(self) -> None:
        # Only the cue itself: a hover reading shown since stays.
        if self._readout.text() == getattr(self, "_cue_text", None):
            self._readout.setText("")

    def _replace_target(self, key: str, color: QColor) -> None:
        """Set one target to a colour chosen in Krita, outright.

        Deliberately bypasses the derived-value rule in _set_active_hsv (the
        shadow darker than the base, the light brighter): that rule guards
        slider edits, while a colour picked in Krita is the user's explicit
        choice for that target and is taken as given.
        """
        self._targets[key] = QColor(color)
        h, s, v, _a = color.getHsvF()
        if v > 0.0 and s >= 0.005:
            # Remember a real hue so the Hue slider has something to show if
            # this target is later desaturated to grey.
            self._hue_memory[key] = h
        self._sync_sliders_from_state()
        self._update_preview()
        self._rebuild_sphere()
        LOG.info("target %s replaced by %s", key, color.name())

    def _on_apply_selection(self, _checked=False):
        """Send the base color to Krita's foreground so the brush can use it.

        This is the one control in the panel that is an action rather than a
        setting, which is why it is a labelled button instead of sitting in the
        Presets group with Artistic and Real.
        """
        self._send_to_krita(self._targets["base"])


# Register this docker with Krita so it actually appears in the UI.
# This registration step was missing - without a registered factory the docker
# never loads (per the official Krita docker how-to). Guarded so the module can
# still be imported for static inspection outside Krita's runtime.
try:
    LOG.info("registering docker factory")
    Krita.instance().addDockWidgetFactory(
        DockWidgetFactory("lumina_docker", DockWidgetFactoryBase.DockRight, SphereDocker)
    )
    print("Lumina: registered 'Lumina' docker factory")
    LOG.info("docker factory registered OK")
except Exception as exc:  # pragma: no cover - only runs inside Krita
    print(f"Lumina: factory registration skipped (not inside Krita) - {exc}")
    LOG.info(f"factory registration skipped - {exc}")
