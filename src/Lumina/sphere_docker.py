"""sphere_docker.py — The Lumina Krita docker (V2 icon-driven layout).

Assembles the compact, icon-driven control panel:

    +-----------------------------------------------------+
    |  [Orb]                                              |   <- SphereWidget (small circular orb)
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
    * Compact circular orb instead of a large square-ish one.
    * All shading math delegated to the pure ``color_engine`` / ``color_processor``.
"""

import math
import weakref
from typing import Callable, Optional

from PyQt5.QtCore import (Qt, QEvent, QObject, QPoint, QPointF, QRectF, QSettings,
                         QTimer, pyqtSlot)
from PyQt5.QtGui import (QColor, QPainter, QLinearGradient, QPixmap, QImage, QIcon,
                         QFont, QPen, QPainterPath, QPolygonF)
from PyQt5.QtWidgets import (
    QAbstractButton, QApplication, QDockWidget, QWidget, QVBoxLayout, QGridLayout,
    QHBoxLayout, QLabel, QPushButton, QFrame, QSizePolicy, QLayout, QScrollArea
)

# `krita` provides the docker-factory registration API (Krita, DockWidgetFactory,
# DockWidgetFactoryBase). These live in the `krita` module, NOT QtWidgets.
# The stale `DockWidget` reference here is exactly why the module failed to load.
try:
    from krita import Krita, DockWidgetFactory, DockWidgetFactoryBase
except ImportError:
    Krita = DockWidgetFactory = DockWidgetFactoryBase = None

from .color_processor import SphereColorProcessor
from .color_engine import SPEC_KNEE, SPEC_KNEE_MIN, SPEC_KNEE_MAX
from .sphere_widget import SphereWidget
from .color_controls import (Accent, ColorSampler, ColorSlider,
                             CollapsibleSection, SettingsPanel)

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




# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ORB_SIZE = 200          # diameter of the circular orb
ORB_RENDER = 200        # engine render resolution (square)
# Ceiling on the render resolution while a slider is held down. The shading loop
# is pure Python and costs ~0.9 us per pixel, so the full 288px "High" preset
# blocks the UI thread for ~76 ms per update -- long enough that the slider
# handle visibly lags the cursor. 128px costs ~15.6 ms (~64 Hz), which tracks the
# pointer smoothly, and the full-resolution orb is drawn again on release.
DRAG_RENDER = 128
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
# Values are the ones the corresponding slider rows display, so applying a
# preset can move the rows and stay in sync with the engine.
_PRESETS = (
    {"key": "artistic", "label": "Artistic", "glyph": "sparkle",
     "accent": Accent.AMBER,
     "tip": "Artistic: bright white highlights, high contrast",
     "values": {"highlight": (1.0, 1.0, 1.0), "ambient": 10, "intensity": 100,
                "contrast": 120, "specular": 2, "diffuse": 72, "glow": 0,
                "tone": 100, "mixer": "Blended"}},
    {"key": "real", "label": "Real", "glyph": "layers",
     "accent": Accent.PURPLE,
     "tip": "Real-world: warm highlights, soft natural contrast",
     "values": {"highlight": (0.98, 0.94, 0.86), "ambient": 10, "intensity": 100,
                "contrast": 90, "specular": 2, "diffuse": 72, "glow": 0,
                "tone": 100, "mixer": "Blended"}},
    {"key": "nocturne", "label": "Nocturne", "glyph": "moon",
     "accent": Accent.BLUE,
     "tip": "Nocturne: low key, cool moonlight, deep shadow",
     "values": {"highlight": (0.82, 0.88, 1.0), "ambient": 4, "intensity": 92,
                "contrast": 135, "specular": 3, "diffuse": 45, "glow": 0,
                "tone": 88, "mixer": "Blended"}},
    {"key": "gloss", "label": "Gloss", "glyph": "sun",
     "accent": Accent.CYAN,
     "tip": "Gloss: tight bright highlight, slick and punchy",
     "values": {"highlight": (1.0, 0.98, 0.94), "ambient": 8, "intensity": 100,
                "contrast": 118, "specular": 8, "diffuse": 30, "glow": 12,
                "tone": 105, "mixer": "Blended"}},
    {"key": "matte", "label": "Matte", "glyph": "disc",
     "accent": Accent.NEUTRAL,
     "tip": "Matte: even clay-like falloff, no specular hotspot",
     "values": {"highlight": (1.0, 1.0, 1.0), "ambient": 28, "intensity": 100,
                "contrast": 82, "specular": 1, "diffuse": 100, "glow": 0,
                "tone": 96, "mixer": "Blended"}},
    {"key": "neon", "label": "Neon", "glyph": "bolt",
     "accent": Accent.GREEN,
     "tip": "Neon: saturated and blooming, coloured light",
     "values": {"highlight": (0.72, 0.95, 1.0), "ambient": 12, "intensity": 100,
                "contrast": 125, "specular": 4, "diffuse": 55, "glow": 45,
                "tone": 118, "mixer": "Additive"}},
)

HEADER_BTN = 38         # gear / eyedropper size in the header row
PANEL_TOP_PAD = 13      # space above the header row (under the title bar)
PANEL_ROW_GAP = 14      # space between the header row and the orb
TITLEBAR_BAND = 34     # height of the floating window's draggable title band
# --- Deriving light and shadow from a sampled base color --------------------
# A lit surface takes on the hue of the key light; a shadowed one the hue of the
# ambient, which is the complement of that light. Stated as target hues because
# "warmer" is not a fixed direction around the color wheel.
#
# The shifts are deliberately small and scaled by the base's saturation. A large
# shift is not a bigger version of a small one: a base sitting near the
# ambient's antipode sends a 30% shift clean across the wheel, which turned an
# orange into an olive-green shadow. Real shadows barely rotate in hue -- they
# darken and desaturate -- and a near-grey base has no meaningful hue to rotate
# in the first place, so the shift is gated on how saturated the base actually is.
KEY_LIGHT_HUE = 0.08                    # warm key light, ~29 degrees
AMBIENT_HUE = (KEY_LIGHT_HUE + 0.5) % 1.0  # its complement, ~209 degrees (cool)
HIGHLIGHT_HUE_SHIFT = 0.10              # fraction of the way to the key light
SHADOW_HUE_SHIFT = 0.07                 # rotation deeper into red
HUE_SHIFT_CAP = 0.07                    # never rotate more than ~25 degrees

# Krita's own docker grey rather than near-black. At RGB(24,26,32) the panel
# read as a harsh black hole next to Krita's Layers and Tool Options panels,
# which sit around RGB(48,52,58) in the default dark theme.
PANEL_BG = QColor(48, 52, 58)
PANEL_BORDER = QColor(58, 64, 78)
TEXT_DIM = QColor(150, 160, 175)
TEXT_BRIGHT = QColor(225, 232, 245)


# ---------------------------------------------------------------------------
# Small helper widgets
# ---------------------------------------------------------------------------
class LabeledSliderRow(QWidget):
    """A single-row control: accent dot + icon label + color-coded slider."""

    def __init__(self, accent: Accent, label: str, value: int = 50, parent=None,
                 lo: int = 0, hi: int = 100):
        super().__init__(parent)
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

    def value(self) -> int:
        return self.slider.value()

    def set_value(self, value: int) -> None:
        self.slider.setValue(value)


class ToolButton(QPushButton):
    """A small icon button.

    Unlike the first cut of this control, which only ever painted a coloured
    dot, each button now draws a real glyph (``gear``, ``eyedropper``,
    ``sparkle``, ``layers``, ``grid``, ``target``) so the header reads as
    gear / swatch / colour-picker and the bottom row matches the reference's
    icon tab bar.
    """

    GLYPHS = ("gear", "eyedropper", "sparkle", "layers", "grid", "target")

    def __init__(self, accent: Accent = Accent.NEUTRAL, checked: bool = False,
                 glyph: str = "target", size: int = 30, checkable: bool = True,
                 parent=None):
        super().__init__(parent)
        self.setCheckable(checkable)
        self.setChecked(checked)
        self.accent = accent
        self.glyph = glyph
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

    def _draw_eyedropper(self, p, cx, cy, col):
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
        R, d, off = 5.4, 2.8, 1.3
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
            }.get(self.glyph, self._draw_target)
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


# ---------------------------------------------------------------------------
# SphereDocker — the main V2 docker
# ---------------------------------------------------------------------------
class MixerRow(QWidget):
    """Segmented control for the light mixer: Additive / Multiplicative / Blended.

    The engine has always supported these three modes but nothing in the UI ever
    exposed them, so the mixer was unreachable.
    """

    MODES = ("Blended", "Additive", "Multiplicative")

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

        cap = QLabel("Mixer")
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


class SphereDocker(QDockWidget):
    """The Lumina docker with a compact, icon-driven vertical sidebar.

    The panel follows the Infinite Painter "Lighting Orb" layout (see issue #9):

        header   [gear]  [base swatch, light|dark]  [eyedropper]
        orb      large full circle with a draggable picker pointer
        targets  shadow / base / light dots (active one ringed)
        sliders  Hue (rainbow) / Saturation (grey->hue) / Value (dark->light)
                 / Contrast (plain)
        advanced collapsible: base level, intensity, ambient, specular, glow, tone
        tools    artistic / real-world / pick-from-document / apply-to-foreground
    """

    # Editable lighting targets. The orb always renders base + light + shadow
    # together; the Hue / Saturation / Value sliders edit whichever target is
    # active, so the base color can be changed without disturbing the light and
    # shadow colors (per the reference manual).
    TARGET_ORDER = ("shadow", "base", "light")
    DEFAULT_TARGETS = {
        "shadow": QColor(62, 66, 96),     # cool bounce: shadows are rarely black
        "base":   QColor(205, 92, 92),    # the object itself
        "light":  QColor(248, 206, 132),  # key light
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

    def __init__(self):
        LOG.info("SphereDocker.__init__ START")
        super().__init__()
        self.setWindowTitle("Lumina")
        LOG.info("base widget created")

        # --- Editable lighting targets (shadow / base / light) ---
        self._targets = {k: QColor(v) for k, v in self.DEFAULT_TARGETS.items()}
        # Left block of the split swatch: the committed "original" colour.
        # Seeded from the default base so it is never an empty placeholder.
        self._original_color = QColor(self.DEFAULT_TARGETS["base"])
        # Colour chosen on the orb to draw with; shown in the active swatch.
        self._chosen_color = None
        self._chosen_color = None    # colour picked on the orb, shown in the active swatch
        # QColor.getHsvF() reports hue 0 for greys, so remember the last
        # meaningful hue per target and fall back to it when saturation is ~0.
        self._hue_memory = {k: 0.0 for k in self._targets}
        self._active_target = "base"
        self._syncing = False
        self._sync_guard = False       # blocks our own foreground echo
        self._awaiting_sampler = False # next canvas sample comes from the eyedropper
        self._sampler_timer = None
        self._sampler_baseline = None
        self._sampler_elapsed = 0
        self._sampler_prev_tool = None
        self._orb_render_size = ORB_RENDER   # changed by the settings panel
        self._slider_dragging = False        # True while a slider is held down

        # --- Shading engine (pure Python, no Qt) + Krita-facing processor ---
        self.processor = SphereColorProcessor(resolution=ORB_RENDER)
        LOG.info("processor ready")

        # --- Interactive orb surface ---
        self._orb = SphereWidget()
        self._orb.set_hover_callback(self._on_orb_hover)
        self._orb.set_brush_callback(self._on_orb_brush)
        LOG.info("orb created + pick/hover wired")

        # Colour sampling ring drawn on top of the orb. It is a child of the orb
        # so it can be positioned in the orb's own coordinates and follow the
        # pointer across the surface.
        self.cylinder = ColorSampler()
        self._orb.set_preview_widget(self.cylinder)
        self.cylinder.hide()

        # Coalescer for orb rebuilds. Created before the UI is built because
        # syncing slider values can fire valueChanged handlers that call
        # _rebuild_orb().
        self._rebuild_timer = QTimer(self)
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.setInterval(0)
        self._rebuild_timer.timeout.connect(self._rebuild_orb_now)

        # --- Primary sliders: edit the active target + contrast ---
        # Row defaults are read from the engine rather than hardcoded. Four of
        # them were lying: the panel opened showing Contrast 50, Intensity 70
        # and Tone 50 while the engine actually sat at 1.0 for all three, so the
        # handle position did not describe the state being rendered and the
        # first drag produced a large jump from an unrelated starting point.
        _eng = self.processor.engine
        self.hue_row = LabeledSliderRow(Accent.NEUTRAL, "Hue", 0, hi=359)
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
                                             int(round(_eng.shininess)), lo=1, hi=128)
        # Diffuse is the other half of the highlight: Specular sets how wide the
        # sheen is, Diffuse how softly it falls off. They sit together so the
        # relationship is obvious rather than split across two panels.
        self.diffuse_row = LabeledSliderRow(Accent.CYAN, "Diffuse",
                                            self._knee_to_level(SPEC_KNEE))
        self.glow_row = LabeledSliderRow(Accent.CYAN, "Glow", 0)
        self.tone_row = LabeledSliderRow(Accent.GREEN, "Tone",
                                         int(round(_eng.saturation * 100)),
                                         hi=200)
        # These four were never given an explicit range, so they sat on Qt's
        # default 0-99. Anything asking for 100 was silently clamped to 99 by
        # the widget, and the resulting valueChanged overwrote the engine value
        # the caller had just set -- so "Intensity 100" was unreachable and the
        # presets silently rendered at 0.99.

        self.mixer_row = MixerRow(self.processor.engine.mixer_mode)
        self.mixer_row._on_mode_changed = self._on_mixer_changed
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
        for i, preset in enumerate(_PRESETS):
            btn = ToolButton(preset["accent"], checked=(i == 0),
                             glyph=preset["glyph"])
            btn.key = preset["key"]
            btn.setToolTip(preset["tip"])
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
        self._rebuild_orb()
        LOG.info("SphereDocker.__init__ COMPLETE")

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
            self._rebuild_orb()
            self._sync_sliders_from_state()
            self._update_preview()
        except Exception as exc:  # pragma: no cover - UI only
            LOG.exception("_revert_to_original failed")
            print(f"Lumina: revert failed - {exc}")

    def _commit_current(self) -> None:
        """Right swatch: commit the active color as the new original."""
        try:
            shown = self._chosen_color if self._chosen_color is not None \
                else self._targets["base"]
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
            h, s, v = color.getHsvF()[0], color.getHsvF()[1], color.getHsvF()[2]
            base = QColor(color)

            # Auto-harmonised derivation, so the three always read as one
            # lighting scheme rather than three clashing colours.
            #
            # Hue follows the usual hue-shifting rule: a lit surface takes the
            # hue of the key light, a shadowed one the hue of the ambient, which
            # is the complement of that light. It has to be expressed as a
            # *target hue*, not a fixed offset, because "warmer" has no single
            # direction around the wheel -- from a blue base warmer is a
            # decreasing hue, from a red base an increasing one. The old fixed
            # offsets (light h-0.03, shadow h+0.05) were therefore inverted for
            # every base between cyan and magenta, producing a cool highlight
            # and a warm shadow. Targeting the light and its complement gives the
            # right direction from anywhere on the wheel, and keeps the gap
            # between light and shadow at 55-63 degrees whatever the base.
            # Scale the hue shift by the base's saturation and cap it, so a
            # saturated color shifts a little and a grey one does not swing.
            #
            # Direction: the highlight moves toward the warm key light (up the
            # wheel, toward yellow) and the shadow moves the other way, deeper
            # into red. Both are fixed small rotations rather than a move toward
            # the ambient's hue. The ambient sits at the key light's antipode, so
            # "toward the ambient" is ambiguous to within a degree for a base
            # anywhere near it, and it resolved the wrong way: an orange base had
            # its shadow pushed up into yellow, giving a muddy olive shadow
            # where the reference has a deep red-brown.
            sat_gate = min(1.0, s / 0.35)
            cap = HUE_SHIFT_CAP
            light_h = self._hue_toward(
                h, KEY_LIGHT_HUE, min(HIGHLIGHT_HUE_SHIFT * sat_gate, cap))
            shadow_h = (h - min(SHADOW_HUE_SHIFT * sat_gate, cap)) % 1.0
            light = QColor.fromHsvF(
                light_h,
                # Desaturate toward the light, but never so far that the
                # highlight stops reading as that colour.
                self._clamp01(s * 0.72),
                # 45% of the way to white: proportional to the headroom, so a
                # bright base is not pushed flat and a dark one is not crushed.
                self._clamp01(v + (1.0 - v) * 0.45),
            )
            shadow = QColor.fromHsvF(
                shadow_h,
                # Shadows read as more muted than the base, not more vivid. The
                # old *1.05 pushed saturation up on a darkened colour, which is
                # what turned shadows into muddy brown.
                self._clamp01(s * 0.92),
                # Down to 50% of the base. Darker than this and the shadow
                # collapsed toward black, which forced the rim light up to
                # compensate and produced a glowing outline; the reference keeps
                # a rich, saturated shadow that is still recognisably the base
                # color's family.
                self._clamp01(v * 0.50),
            )

            self._targets["base"] = base
            self._targets["light"] = light
            self._targets["shadow"] = shadow
            self._hue_memory["light"] = light_h
            self._hue_memory["shadow"] = shadow_h
            # Keep the per-target swatches honest immediately, rather than waiting
            # for whichever caller happens to refresh the preview next.
            self._sync_target_dots()

            LOG.info("distributed %s -> base=%s light=%s shadow=%s",
                     color.name(), base.name(), light.name(), shadow.name())
        except Exception as exc:
            LOG.exception("_distribute_from failed")
            print(f"Lumina: distribute failed - {exc}")

    @staticmethod
    def _clamp01(value: float) -> float:
        return 0.0 if value < 0.0 else (1.0 if value > 1.0 else float(value))

    @staticmethod
    def _hue_toward(h: float, target: float, amount: float) -> float:
        """Move hue ``h`` a fraction ``amount`` of the way to ``target``.

        Takes the short way round the wheel, so the direction is right no
        matter where ``h`` sits.
        """
        delta = (target - h + 0.5) % 1.0 - 0.5
        return (h + delta * amount) % 1.0

    @staticmethod
    def _rgb01(color: QColor):
        return (color.redF(), color.greenF(), color.blueF())

    def _hsv_of(self, key: str):
        """Return (h, s, v) floats 0..1 for a target, honouring grey-colour hue."""
        # getHsvF() returns a 4-tuple (h, s, v, a) -- take the first three.
        hsv = self._targets[key].getHsvF()
        h, s, v = hsv[0], hsv[1], hsv[2]
        if s < 0.005:
            h = self._hue_memory.get(key, h)
        else:
            self._hue_memory[key] = h
        return h, s, v

    def _set_active_hsv(self, h=None, s=None, v=None) -> None:
        """Update the active target's HSV components, leaving the rest intact."""
        ch, cs, cv = self._hsv_of(self._active_target)
        nh = ch if h is None else self._clamp01(h)
        ns = cs if s is None else self._clamp01(s)
        nv = cv if v is None else self._clamp01(v)
        self._targets[self._active_target] = QColor.fromHsvF(nh, ns, nv)
        self._hue_memory[self._active_target] = nh

    class TargetDot(QWidget):
        """The live colour swatch shown beside a target's glyph.

        The reference pairs every icon with the colour it controls, so the row
        reads at a glance as "shadow / base / highlight" with real values rather
        than three anonymous glyphs. Selection is shown on *both* halves of the
        pair -- the glyph fills solid white and this dot takes a full white rim
        -- while the dot's own fill stays the real target colour.
        """

        # All three swatches are the same size. Growing the selected one made the
        # row look like it contained a circle, a dot and a circle, and the
        # reference keeps them uniform; the selection is shown by the ring.
        SMALL = 20
        LARGE = 20

        def __init__(self, key: str, parent=None):
            super().__init__(parent)
            self.key = key
            self._color = QColor(120, 120, 120)
            self._active = False
            self.setFixedSize(self.LARGE, self.LARGE)
            self.setToolTip(SphereDocker.TARGET_LABELS.get(key, key))
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
                d = self.LARGE if self._active else self.SMALL
                cx = cy = self.width() / 2.0
                rect = QRectF(cx - d / 2.0, cy - d / 2.0, d, d)
                p.setBrush(self._color)
                # The rim is the selection mark, and it now matches the icon
                # beside it: solid white when active, faint when not. Only the
                # rim changes -- the fill always stays the target's real colour,
                # which is the whole point of showing it here.
                #
                # A hairline rim keeps a near-black shadow swatch legible against
                # the dark panel. The selected rim is deliberately drawn at the
                # swatch's own radius rather than outside it, so the pair keeps
                # its uniform size instead of the active one growing.
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
        """Build the Lighting Orb panel (see issue #9 for the reference layout)."""

        class TargetBtn(QPushButton):
            """Small selectable dot: crescent (shadow) / droplet (base) / sun (light).

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
                self.setToolTip(SphereDocker.TARGET_LABELS.get(key, key))
                # No QSS background: paintEvent draws everything itself.
                self.setStyleSheet("QPushButton { background: transparent; border: none; }")

            def set_active(self, active: bool) -> None:
                self.setChecked(bool(active))
                self.update()

            def paintEvent(self, event):
                """Draw a thin monochrome outline glyph: crescent, droplet, sun.

                The reference draws these as light, hairline outlines in a single
                ink colour. They were previously solid shapes in three different
                accent colours, and the "base" glyph was a filled disc with a
                white centre -- a target, not a droplet -- so none of the three
                read as the icon it stood for. Colour comes from the swatch
                beside the glyph, so the glyph itself only has to say *which
                role* it plays.

                Selection is carried by the *fill*: the active glyph is filled
                solid white, the inactive ones stay hollow. Filling is a much
                stronger signal than tinting the stroke, and it is the one thing
                the two can differ on without touching the palette.
                """
                try:
                    p = QPainter(self)
                    p.setRenderHint(QPainter.Antialiasing, True)
                    w, h = self.width(), self.height()
                    cx, cy = w / 2.0, h / 2.0
                    # One ink for all three; only the brush differs by state.
                    ink = QColor(216, 224, 236)
                    active = self.isChecked()
                    stroke = QPen(ink, 1.9, Qt.SolidLine, Qt.RoundCap,
                                  Qt.RoundJoin)
                    p.setPen(stroke)
                    p.setBrush(QColor(255, 255, 255) if active else Qt.NoBrush)

                    if self.icon == "shadow":
                        # Crescent: the sliver between two equal circles whose
                        # centres are offset horizontally. Built as a closed
                        # polygon from sampled arc points that meet exactly at
                        # the circles' real intersections -- drawing two arcs
                        # independently left them unjoined, which read as an
                        # open "C" rather than a crescent.
                        R, d, off = 9.0, 5.0, 2.0
                        ox, ix = cx - off, cx - off + d
                        h = math.sqrt(R * R - (d / 2.0) ** 2)
                        a = math.degrees(math.atan2(h, d / 2.0))
                        pts = []
                        steps = 30
                        # Outer circle, the long way round through its left side.
                        for k in range(steps + 1):
                            t = math.radians(-a + (-(360.0 - 2 * a)) * k / steps)
                            pts.append(QPointF(ox + R * math.cos(t), cy + R * math.sin(t)))
                        # Inner circle back again, also through its left side, so
                        # the two arcs meet at exactly the same two points.
                        for k in range(steps + 1):
                            t = math.radians((180.0 - a) + (2 * a) * k / steps)
                            pts.append(QPointF(ix + R * math.cos(t), cy + R * math.sin(t)))
                        poly = QPolygonF(pts)
                        # drawPolygon when selected so the crescent actually
                        # fills; drawPolyline when not, which is what a hairline
                        # outline wants. The point list is already a closed loop
                        # (the two arcs meet at the same two points), so the
                        # fill is the crescent sliver and not the whole disc.
                        if active:
                            p.drawPolygon(poly)
                        else:
                            p.drawPolyline(poly)
                    elif self.icon == "base":
                        # Droplet: pointed at the top, round at the bottom.
                        path = QPainterPath()
                        path.moveTo(cx, cy - 9.5)
                        path.cubicTo(cx + 5.2, cy - 3.0, cx + 7.4, cy + 8.0,
                                     cx, cy + 8.0)
                        path.cubicTo(cx - 7.4, cy + 8.0, cx - 5.2, cy - 3.0,
                                     cx, cy - 9.5)
                        path.closeSubpath()
                        p.drawPath(path)
                    else:
                        # Sun: a small disc with short rays around it. The disc
                        # takes the fill so the glyph can read as selected; the
                        # rays are strokes and stay strokes either way.
                        p.drawEllipse(QRectF(cx - 4.6, cy - 4.6, 9.2, 9.2))
                        for k in range(8):
                            a = math.radians(k * 45.0)
                            p.drawLine(
                                QPointF(cx + math.cos(a) * 7.6, cy + math.sin(a) * 7.6),
                                QPointF(cx + math.cos(a) * 10.4, cy + math.sin(a) * 10.4),
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

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)

        # 1. Settings
        # 1. Settings (gear). checkable=False: it is a momentary action that
        # opens the settings popup, so it must not latch its highlight on.
        gear = ToolButton(Accent.NEUTRAL, glyph="gear", size=HEADER_BTN,
                          checkable=False)
        gear.setToolTip("Settings: light direction, render quality, reset")
        gear.clicked.connect(self._toggle_settings)
        self._gear_btn = gear
        header.addWidget(gear)

        # 2. Previous color (click to step back)
        self._sw_prev = QFrame()
        self._sw_prev.setMinimumSize(64, HEADER_BTN)
        self._sw_prev.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._sw_prev.setCursor(Qt.PointingHandCursor)
        self._sw_prev.setToolTip("Previous color - click to step back")
        self._sw_prev.setStyleSheet(
            "QFrame { background-color: #3b414a; border-radius: 5px; }")
        self._sw_prev.mousePressEvent = self._on_prev_swatch_pressed
        header.addWidget(self._sw_prev, 1)

        # 3. Current (modified) color
        self._sw_current = QFrame()
        self._sw_current.setMinimumSize(64, HEADER_BTN)
        self._sw_current.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._sw_current.setStyleSheet(
            "QFrame { background-color: #3b414a; border-radius: 5px; }")
        self._sw_current.setToolTip("Active color")
        self._sw_current.mousePressEvent = self._on_current_swatch_pressed
        header.addWidget(self._sw_current, 1)

        # 4. Color picker: hands over to Krita's own Color Sampler tool so the
        # user can click any pixel on the canvas.
        pick = ToolButton(Accent.CYAN, glyph="eyedropper", size=HEADER_BTN,
                          checkable=False)
        pick.setToolTip("Eyedropper: click a color on the canvas to sample it")
        pick.clicked.connect(self._on_eyedropper_tool)
        self._eyedropper_btn = pick
        header.addWidget(pick)
        layout.addLayout(header)
        layout.addSpacing(PANEL_ROW_GAP)

        # ------------------------------------------------------------------
        # Orb + readout
        # ------------------------------------------------------------------
        orb_frame = QFrame()
        orb_layout = QVBoxLayout(orb_frame)
        # Extra space directly under the gear / swatch / eyedropper row so the
        # sphere is not crowded against the controls above it.
        orb_layout.setContentsMargins(0, 12, 0, 0)
        orb_layout.setSpacing(2)
        self._orb.setFixedSize(ORB_SIZE, ORB_SIZE)
        # Fixed-size holder so the orb sits at a predictable size.
        orb_holder = QWidget()
        orb_holder.setFixedSize(ORB_SIZE, ORB_SIZE)
        orb_holder.setStyleSheet("background: transparent;")
        orb_frame.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        orb_grid = QGridLayout(orb_holder)
        orb_grid.setContentsMargins(0, 0, 0, 0)
        orb_grid.addWidget(self._orb, 0, 0)
        orb_layout.addWidget(orb_holder, alignment=Qt.AlignCenter)

        self._readout = QLabel("")
        self._readout.setAlignment(Qt.AlignCenter)
        self._readout.setFixedHeight(14)
        self._readout.setStyleSheet(
            "QLabel { color: %s; font-size: 10px; }" % TEXT_DIM.name())
        orb_layout.addWidget(self._readout)
        layout.addWidget(orb_frame)

        # ------------------------------------------------------------------
        # Target selector
        # ------------------------------------------------------------------
        target_row = QHBoxLayout()
        target_row.setContentsMargins(0, 0, 0, 0)
        target_row.setSpacing(22)
        # Stretches at both ends keep the dot cluster tight and centered. Without
        # them a QHBoxLayout spreads the fixed-size buttons across the full panel
        # width, which left the three dots marooned at the far edges.
        target_row.addStretch(1)
        self._target_btns = []
        self._target_dots = {}
        for key in self.TARGET_ORDER:
            btn = TargetBtn(key)
            btn.clicked.connect(lambda _c, k=key: self._on_target_changed(k))
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
            dot.on_click = self._on_target_changed
            cv.addWidget(dot, alignment=Qt.AlignCenter)
            self._target_dots[key] = dot
            target_row.addWidget(cell, alignment=Qt.AlignCenter)
            self._target_btns.append(btn)
        target_row.addStretch(1)
        layout.addLayout(target_row)

        # ------------------------------------------------------------------
        # Primary sliders
        # ------------------------------------------------------------------
        layout.addWidget(self.hue_row)
        layout.addWidget(self.saturation_row)
        layout.addWidget(self.light_row)
        layout.addWidget(self.contrast_row)

        # ------------------------------------------------------------------
        # Advanced (collapsible)
        # ------------------------------------------------------------------
        self.advanced = CollapsibleSection("Advanced", expanded=False,
                                           accent=Accent.NEUTRAL)
        for row in (self.color_row, self.intensity_row, self.ambient_row,
                    self.specular_row, self.diffuse_row, self.glow_row, self.tone_row,
                    self.mixer_row):
            self.advanced.add_row(row)
        layout.addWidget(self.advanced)

        # ------------------------------------------------------------------
        # Lighting presets
        # ------------------------------------------------------------------
        # Labelled, because checkable glyph buttons on their own read as
        # generic toggles rather than as a named set of looks.
        #
        # Laid out 3x2 rather than as one row: six captioned glyphs side by side
        # needed ~430px against a panel that is ~230px wide, so the row either
        # overflowed or crushed the captions. A grid keeps every label legible
        # and adds no horizontal scroll.
        plab = QLabel("Presets")
        plab.setStyleSheet(
            "QLabel { color: %s; font-size: 11px; font-weight: 600; "
            "letter-spacing: 0.3px; }" % TEXT_DIM.name())
        plab.setContentsMargins(10, 0, 0, 0)
        layout.addWidget(plab)

        grid = QGridLayout()
        grid.setContentsMargins(6, 2, 6, 0)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(6)
        for i, (btn, preset) in enumerate(zip(self._preset_btns, _PRESETS)):
            cell = QWidget()
            cv = QVBoxLayout(cell)
            cv.setContentsMargins(0, 0, 0, 0)
            cv.setSpacing(2)
            cv.addWidget(btn, alignment=Qt.AlignCenter)
            cap = QLabel(preset["label"])
            cap.setAlignment(Qt.AlignCenter)
            cap.setStyleSheet("QLabel { color: #b9c3d2; font-size: 9px; }")
            cv.addWidget(cap)
            grid.addWidget(cell, i // 3, i % 3, alignment=Qt.AlignCenter)
        for col in range(3):
            grid.setColumnStretch(col, 1)
        layout.addLayout(grid)

        # ------------------------------------------------------------------
        # Send the base color to the brush
        # ------------------------------------------------------------------
        # A text button rather than another glyph: it is an action, not a
        # preset, and "Apply" gave no hint that it changes Krita's foreground.
        self.apply_btn = QPushButton("Use base color as brush")
        self.apply_btn.setCursor(Qt.PointingHandCursor)
        # Keep this to one short line. A long or multi-paragraph tooltip is not
        # wrapped by Qt, so an over-long line rendered as a banner stretching
        # most of the screen width and covering the canvas.
        self.apply_btn.setToolTip("Send the base color to Krita's brush color")
        self.apply_btn.setStyleSheet(
            "QPushButton { background-color: #3b414a; color: #ccd5e2; "
            "border: 1px solid rgba(255,255,255,60); border-radius: 5px; "
            "font-size: 10px; padding: 5px; }"
            "QPushButton:hover { background-color: #464d57; }"
            "QPushButton:pressed { background-color: #2e333a; }")
        layout.addWidget(self.apply_btn)

        # Anything left over goes to the bottom of the panel rather than being
        # distributed between the controls.
        layout.addStretch(1)

        # The sampler is parented to the panel, not the orb: a child of the orb is
        # clipped to the orb's bounds, which stopped the reticle reaching the
        # rim of the sphere.
        self.cylinder.setParent(main)

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
            azimuth=int(self.processor.engine.light_azimuth_deg),
            elevation=int(math.degrees(self.processor.engine.light_elevation_rad)),
            quality=self._orb_render_size,
            parent=main)
        self._settings_panel.bind(self._on_settings_changed, self._on_settings_reset,
                                  self._on_settings_save)
        self._settings_panel.hide()

        # Paint the initial state so the swatch / dots / gradients are correct
        # on the very first frame (previously _update_preview only ran on a
        # user action, leaving the swatch an empty grey box).
        # Restore the user's saved setup before the first paint, so the orb never
        # flashes at the defaults on the way to the saved state.
        self._load_settings()

        self._sync_target_buttons()
        self._sync_sliders_from_state()
        self._update_preview()

        # Drop the orb to a cheaper render size while any slider is being
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
    def _on_target_changed(self, key: str) -> None:
        """Select which lighting target the Hue/Sat/Value sliders edit."""
        try:
            if key not in self._targets:
                return
            self._active_target = key
            self._sync_target_buttons()
            self._sync_sliders_from_state()
            self._update_preview()
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
                dot.set_color(color)
            dot.set_active(key == self._active_target)

    def _sync_sliders_from_state(self) -> None:
        """Push the active target's HSV into the sliders without re-entering."""
        if self._syncing:
            return
        self._syncing = True
        try:
            h, s, v = self._hsv_of(self._active_target)
            self.hue_row.slider.setValue(int(round(h * 359.0)) % 360)
            self.saturation_row.slider.setValue(int(round(s * 100.0)))
            self.light_row.slider.setValue(int(round(v * 100.0)))
            # Light is base-only, so the row is hidden for the other two
            # targets rather than sitting there greyed out or, worse, still
            # live and quietly editing whichever target happened to be active.
            self.light_row.setVisible(self._active_target == "base")
        finally:
            self._syncing = False
        self._sync_gradient_tracks()

    def _sync_gradient_tracks(self) -> None:
        """Repaint the slider tracks so each encodes the active target."""
        try:
            h, s, v = self._hsv_of(self._active_target)
            self.hue_row.slider.hue_gradient()
            self.saturation_row.slider.sat_gradient(h, v)
            # The Light track was implemented in color_controls from the start
            # but never wired to a row, so it was dead code until now.
            self.light_row.slider.value_gradient(h, s)
        except Exception as exc:  # pragma: no cover - cosmetic only
            LOG.exception("_sync_gradient_tracks failed")

    def _update_preview(self) -> None:
        """Refresh the split swatch: original (left block) and active (right)."""
        # Every path that changes a target colour -- a slider, the eyedropper,
        # distributing a sampled colour, a reset -- funnels through here, so this
        # is where the per-target swatches stay in step.
        self._sync_target_dots()
        try:
            base = self._targets["base"]
            # A colour chosen on the orb (for drawing) takes precedence over the
            # sphere's base in the active swatch, so picking a colour to paint
            # with does not look like it was discarded.
            shown = self._chosen_color if self._chosen_color is not None else base
            self._set_swatch_color(self._sw_current, shown)
            self._sw_current.setToolTip(
                "Active color %s - click to make this the new original" % shown.name())
            orig = self._original_color
            if orig is not None:
                self._sw_prev.setStyleSheet(
                    "QFrame { background-color: %s; border-radius: 5px; "
                    "border: 1px solid rgba(255,255,255,70); }" % orig.name())
                self._sw_prev.setToolTip(
                    "Original color %s - click to revert" % orig.name())
            else:
                self._sw_prev.setStyleSheet(
                    "QFrame { background-color: #22262f; border-radius: 5px; "
                    "border: 1px dashed rgba(255,255,255,50); }")
                self._sw_prev.setToolTip("No original color yet")
        except Exception as exc:  # pragma: no cover - cosmetic only
            print(f"Lumina: update_preview failed - {exc}")

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
        """Open / close the settings panel under the gear button."""
        try:
            panel = getattr(self, "_settings_panel", None)
            if panel is None:
                return
            if panel.isVisible():
                panel.hide()
                return
            # Anchor just below the gear button.
            gear = self._gear_btn
            panel.adjustSize()
            # A Qt.Popup is positioned in global screen coordinates, so map the
            # gear button to global. mapTo() (widget-relative) put the panel in
            # the top-left corner of the screen.
            point = gear.mapToGlobal(QPoint(0, gear.height() + 4))
            # Keep it on screen if the dock is near the bottom edge.
            screen = QApplication.desktop().availableGeometry() if hasattr(
                QApplication, "desktop") else None
            if screen is not None:
                x = max(screen.left(), min(point.x(), screen.right() - panel.width()))
                y = max(screen.top(), min(point.y(), screen.bottom() - panel.height()))
                point = QPoint(x, y)
            panel.move(point)
            panel.show()
            panel.raise_()
        except Exception as exc:  # pragma: no cover - UI only
            LOG.exception("_toggle_settings failed")
            print(f"Lumina: toggle settings failed - {exc}")

    def _on_settings_changed(self, state) -> None:
        """Apply the settings panel's current values to the engine."""
        try:
            self.processor.set_light_angle(
                int(state["azimuth"]), math.radians(float(state["elevation"])))
            self._orb_render_size = int(state["quality"])
            self._orb.set_show_pointer(bool(state["pointer"]))
            self._rebuild_orb()
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
            "azimuth": int(eng.light_azimuth_deg),
            "elevation": int(round(math.degrees(eng.light_elevation_rad))),
            "quality": int(getattr(self, "_orb_render_size", ORB_RENDER)),
            "sampler": bool(self._orb._show_pointer),
            "diffuse": self._knee_to_level(eng.spec_knee),
            "contrast": int(eng.contrast * 100.0),
            "intensity": int(eng.light_intensity * 100.0),
            "ambient": int(eng.ambient * 100.0),
            "specular": int(eng.shininess),
            "glow": int(eng.glow_intensity * 100.0),
            "tone": int(eng.brightness * 100.0),
            "mixer": str(eng.mixer_mode),
            "base_level": int(self.color_row.value()),
        }
        for name, qc in self._targets.items():
            out["target_" + name] = "#%02X%02X%02X" % (qc.red(), qc.green(), qc.blue())
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
                eng.set_light_angle(int(num("azimuth", 300, 0, 359)),
                                    math.radians(num("elevation", 60, 0, 90)))
                self._orb_render_size = int(num("quality", ORB_RENDER, 64, 512))
                self._orb.set_show_pointer(str(s.value("sampler", "true")).lower()
                                           not in ("false", "0"))
                self.processor.set_spec_knee(self._level_to_knee(
                    int(num("diffuse", self._knee_to_level(SPEC_KNEE), 0, 100))))
                self.processor.set_contrast(num("contrast", 100, 0, 300) / 100.0)
                self.processor.set_light_intensity(
                    num("intensity", 100, 0, 200) / 100.0)
                self.processor.set_ambient(num("ambient", 10, 0, 100) / 100.0)
                self.processor.set_shininess(num("specular", 2, 1, 128))
                self.processor.set_glow_intensity(num("glow", 0, 0, 100) / 100.0)
                self.processor.set_brightness(num("tone", 100, 0, 200) / 100.0)
                self.processor.set_mixer_mode(str(s.value("mixer", "Blended")))
                self.color_row.set_value(int(num("base_level", 100, 0, 100)))

                # Push the restored numbers back into the widgets, still inside
                # the _syncing guard: the settings popup re-emits as its widgets
                # are set, and each of those emissions would otherwise apply a
                # half-updated mix of new and still-default values to the engine.
                self.diffuse_row.set_value(self._knee_to_level(eng.spec_knee))
                self.contrast_row.set_value(int(eng.contrast * 100.0))
                self.intensity_row.set_value(int(eng.light_intensity * 100.0))
                self.ambient_row.set_value(int(eng.ambient * 100.0))
                self.specular_row.set_value(int(eng.shininess))
                self.glow_row.set_value(int(eng.glow_intensity * 100.0))
                self.tone_row.set_value(int(eng.brightness * 100.0))
                self._sync_settings_panel()
                self._sync_target_buttons()
                self._sync_sliders_from_state()
            finally:
                self._syncing = False
            # First paint happens after the guard is released, so this rebuild
            # both shows the restored orb and commits it as the new saved state.
            self._rebuild_orb()
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
                int(eng.light_azimuth_deg),
                int(round(math.degrees(eng.light_elevation_rad))),
                int(getattr(self, "_orb_render_size", ORB_RENDER)),
                bool(self._orb._show_pointer),
            )
        except Exception as exc:  # pragma: no cover - cosmetic only
            LOG.exception("_sync_settings_panel failed")

    def _on_settings_reset(self) -> None:
        """Restore targets and lighting to their defaults."""
        try:
            self._original_color = QColor(self.DEFAULT_TARGETS["base"])
            self._chosen_color = None
            for key, col in self.DEFAULT_TARGETS.items():
                self._targets[key] = QColor(col)
                self._hue_memory[key] = QColor(col).getHsvF()[0]
            self._active_target = "base"
            self.processor.set_light_angle(300.0, math.radians(60.0))
            self.processor.set_mixer_mode("Blended")
            self.mixer_row.set_mode("Blended")
            self._on_contrast_changed(50)
            # Reset every value the setup persists, not just contrast and the
            # targets. It used to leave ambient, intensity, specular, diffuse,
            # glow, tone and base level untouched, so "Reset all" left most of
            # the advanced sliders exactly where the user had put them -- and now
            # that the whole panel is saved, those leftovers came back on the
            # next launch looking like the reset had silently failed.
            self._syncing = True
            try:
                eng = self.processor
                eng.set_ambient(0.10)
                eng.set_light_intensity(1.0)
                eng.set_shininess(2)
                eng.set_spec_knee(SPEC_KNEE)
                eng.set_glow_intensity(0.0)
                eng.set_brightness(1.0)
                eng.set_saturation(1.0)
                self._orb_render_size = ORB_RENDER
                self._orb.set_show_pointer(True)
                self.color_row.set_value(100)
                self.ambient_row.set_value(10)
                self.intensity_row.set_value(100)
                self.specular_row.set_value(2)
                self.diffuse_row.set_value(self._knee_to_level(SPEC_KNEE))
                self.glow_row.set_value(0)
                self.tone_row.set_value(100)
                self._sync_settings_panel()
            finally:
                self._syncing = False
            self._sync_target_buttons()
            self._sync_sliders_from_state()
            # Rebuilds and persists, since a reset is a change that must stick.
            self._rebuild_orb()
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
            self._rebuild_orb()
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
        self.ambient_row.slider.valueChanged.connect(self._on_ambient_changed)
        self.specular_row.slider.valueChanged.connect(self._on_specular_changed)
        self.diffuse_row.slider.valueChanged.connect(self._on_diffuse_changed)
        self.light_row.slider.valueChanged.connect(self._on_light_changed)
        self.glow_row.slider.valueChanged.connect(self._on_glow_changed)
        self.tone_row.slider.valueChanged.connect(self._on_tone_changed)

        # Preset buttons connect themselves at construction time, from the
        # _PRESETS table. Connecting them again here used to be the only thing
        # wiring them up, and left two dead references to handlers that no
        # longer exist.
        self.apply_btn.clicked.connect(self._on_apply_selection)

    # ------------------------------------------------------------------
    # Target color sliders -> active target
    # ------------------------------------------------------------------
    def _on_hue_changed(self, value):
        try:
            if self._syncing:
                return
            LOG.info("_on_hue_changed value=%d target=%s", value, self._active_target)
            self._set_active_hsv(h=value / 359.0)
            self._rebuild_orb()
            self._update_preview()
            self._sync_gradient_tracks()
        except Exception as exc:
            LOG.exception("_on_hue_changed failed")

    def _on_saturation_changed(self, value):
        try:
            if self._syncing:
                return
            LOG.info("_on_saturation_changed value=%d target=%s", value, self._active_target)
            self._set_active_hsv(s=value / 100.0)
            self._rebuild_orb()
            self._update_preview()
            self._sync_gradient_tracks()
        except Exception as exc:
            LOG.exception("_on_saturation_changed failed")

    def _on_light_changed(self, value):
        try:
            if self._syncing:
                return
            LOG.info("_on_light_changed value=%d target=%s", value, self._active_target)
            self._set_active_hsv(v=value / 100.0)
            self._rebuild_orb()
            self._update_preview()
            self._sync_gradient_tracks()
        except Exception as exc:
            LOG.exception("_on_light_changed failed")

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _render_orb(self) -> QImage:
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
        slow motion. Capping the drag puts the loop at ~64 Hz, and the orb is
        re-rendered at full size the moment the drag ends.
        """
        light = self._targets["light"]
        shadow = self._targets["shadow"]
        self.processor.set_light_color(self._rgb01(light))
        self.processor.set_shadow_color(self._rgb01(shadow))
        size = int(getattr(self, "_orb_render_size", ORB_RENDER))
        if getattr(self, "_slider_dragging", False) and size > DRAG_RENDER:
            size = DRAG_RENDER
        return self.processor.render_image(self._rgb01(self._targets["base"]),
                                           size, size)

    def _on_slider_drag_begin(self) -> None:
        """Drop to the drag render size for the duration of the drag."""
        self._slider_dragging = True

    def _on_slider_drag_end(self) -> None:
        """Restore full render quality once the drag is over.

        The value is already final by the time this fires, so scheduling a
        normal rebuild here produces the crisp orb the user is left looking at.

        Note this flag is deliberately *not* the same as ``_dragging``, which
        tracks the floating title bar; sharing the name would let a title-bar
        drag drop the orb to the drag resolution, and a slider release would
        cancel an in-progress window drag.
        """
        self._slider_dragging = False
        self._rebuild_orb()

    def _rebuild_orb(self):
        """Schedule a coalesced orb rebuild and persist the new state.

        Also the single choke point for autosaving. Every user-facing control
        ends up here, so persisting from one place guarantees no control can be
        added and quietly forgotten -- which is exactly what happened when each
        handler saved individually. Guarded by ``_syncing`` so that loading
        saved state, which also rebuilds, does not write the half-applied
        intermediate values straight back over the file.

        A single slider drag fires valueChanged dozens of times per second, and
        each render is a few tens of milliseconds of pure-Python shading. Queueing
        the work on a 0 ms single-shot timer collapses a burst of changes into one
        render per event-loop turn, so dragging stays responsive instead of
        backing up a queue of stale renders.
        """
        self._rebuild_timer.start(0)
        if getattr(self, "_applying_preset", False):
            # The preset that is mid-apply owns the state right now.
            return
        # A checked preset that no longer matches the controls would be a lie,
        # so moving any slider clears the selection. The engine and rows keep
        # whatever the preset left, which is what the user actually asked for.
        for btn in getattr(self, "_preset_btns", []):
            if btn.isChecked():
                btn.setChecked(False)
        if not getattr(self, "_syncing", False):
            self._save_settings()

    def _rebuild_orb_now(self):
        """Render and display the orb immediately (bypasses the coalescer)."""
        self._orb.set_image(self._render_orb())

    # ------------------------------------------------------------------
    # Row handlers -> engine setters + rebuild
    # ------------------------------------------------------------------
    def _on_color_changed(self, value):
        """Advanced: scale the base target's overall level, preserving hue/sat."""
        try:
            LOG.info("_on_color_changed value=%d", value)
            factor = max(0.0, min(1.0, value / 100.0))
            base = self._targets["base"]
            self._set_base_color(QColor.fromRgbF(
                self._clamp01(base.redF() * factor),
                self._clamp01(base.greenF() * factor),
                self._clamp01(base.blueF() * factor),
            ))
            self._rebuild_orb()
            self._sync_sliders_from_state()
            self._update_preview()
        except Exception as exc:
            LOG.exception("_on_color_changed failed")

    def _on_mixer_changed(self, mode: str) -> None:
        """Advanced: how base / shadow / highlight are blended together."""
        try:
            LOG.info("_on_mixer_changed mode=%s", mode)
            self.processor.set_mixer_mode(mode)
            self._rebuild_orb()
        except Exception as exc:
            LOG.exception("_on_mixer_changed failed")

    def _on_tone_changed(self, value):
        """Advanced: global render saturation (engine tone, not a target color)."""
        try:
            LOG.info("_on_tone_changed value=%d", value)
            self.processor.set_saturation(value / 100.0)
            self._rebuild_orb()
        except Exception as exc:
            LOG.exception("_on_tone_changed failed")

    def _on_intensity_changed(self, value):
        try:
            LOG.info(f"_on_intensity_changed value={value}")
            self.processor.set_light_intensity(value / 100.0)
            self._rebuild_orb()
        except Exception as exc:
            LOG.exception("_on_intensity_changed failed")

    def _on_ambient_changed(self, value):
        try:
            LOG.info(f"_on_ambient_changed value={value}")
            self.processor.set_ambient(value / 100.0)
            self._rebuild_orb()
        except Exception as exc:
            LOG.exception("_on_ambient_changed failed")

    def _on_contrast_changed(self, value):
        try:
            LOG.info(f"_on_contrast_changed value={value}")
            self.processor.set_contrast(value / 100.0)
            self._rebuild_orb()
        except Exception as exc:
            LOG.exception("_on_contrast_changed failed")

    def _on_specular_changed(self, value):
        try:
            LOG.info(f"_on_specular_changed value={value}")
            self.processor.set_shininess(value)
            self._rebuild_orb()
        except Exception as exc:
            LOG.exception("_on_specular_changed failed")

    def _on_diffuse_changed(self, value):
        try:
            LOG.info(f"_on_diffuse_changed value={value}")
            self.processor.set_spec_knee(self._level_to_knee(value))
            self._rebuild_orb()
        except Exception as exc:
            LOG.exception("_on_diffuse_changed failed")

    @staticmethod
    def _level_to_knee(level: int) -> float:
        """Map the Diffuse slider (0-100) onto the specular knee.

        Inverted, because the underlying parameter runs the other way: a *low*
        knee is the diffuse look, so slider 100 -> SPEC_KNEE_MIN.
        """
        t = max(0.0, min(1.0, level / 100.0))
        return SPEC_KNEE_MAX - t * (SPEC_KNEE_MAX - SPEC_KNEE_MIN)

    @staticmethod
    def _knee_to_level(knee: float) -> int:
        """Inverse of :meth:`_level_to_knee`, for restoring a saved value."""
        k = max(SPEC_KNEE_MIN, min(SPEC_KNEE_MAX, knee))
        return int(round((SPEC_KNEE_MAX - k) / (SPEC_KNEE_MAX - SPEC_KNEE_MIN) * 100))

    def _on_glow_changed(self, value):
        try:
            LOG.info(f"_on_glow_changed value={value}")
            self.processor.set_glow_intensity(value / 100.0)
            self._rebuild_orb()
        except Exception as exc:
            LOG.exception("_on_glow_changed failed")

    # NOTE: the old render-level _on_saturation_changed was removed. Saturation
    # is now a *target color* control (see _on_saturation_changed above); the
    # engine's global tone control lives on the advanced "Tone" row.

    # ------------------------------------------------------------------
    # Toggle handlers
    # ------------------------------------------------------------------
    def _on_preset_clicked(self, key: str, checked: bool) -> None:
        """Select a lighting preset.

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
            for btn in self._preset_btns:
                btn.setChecked(btn.key == key)
            # The guard has to stay raised across the *final* rebuild too. Every
            # row update below fires valueChanged -> _rebuild_orb, and if the
            # flag were already clear that rebuild would uncheck the very
            # button the user just pressed.
            self._applying_preset = True
            try:
                self._apply_preset_values(preset["values"])
                self._rebuild_orb()
            finally:
                self._applying_preset = False
        except Exception as exc:
            LOG.exception("_on_preset_clicked failed")

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
        if "specular" in values:
            p.set_shininess(values["specular"])
        if "diffuse" in values:
            p.set_spec_knee(self._level_to_knee(values["diffuse"]))
        if "mixer" in values:
            p.set_mixer_mode(values["mixer"])
            self.mixer_row.set_mode(values["mixer"])
        for name, row in (("ambient", self.ambient_row),
                          ("intensity", self.intensity_row),
                          ("contrast", self.contrast_row),
                          ("specular", self.specular_row),
                          ("diffuse", self.diffuse_row),
                          ("glow", self.glow_row),
                          ("tone", self.tone_row)):
            if name in values:
                row.set_value(int(values[name]))

    # ------------------------------------------------------------------
    # Krita integration
    # ------------------------------------------------------------------
    @pyqtSlot()
    def isCanvasObserver(self) -> bool:
        """Declare this dock observes the active canvas so Krita pushes updates to us.

        Decorated as a slot because Krita asks for this through the Qt
        meta-object, not by calling the Python method directly. An undecorated
        Python method is invisible to that lookup, so Krita saw the default
        (false) and logged "is not a canvas observer" on every startup even
        though this returned True.
        """

        return True
    def canvasChanged(self, canvas):
        """Called by Krita when the active canvas/document changes.

        Refreshes the orb from current state so switching documents keeps it in sync.
        The real external-color listener is the View color-change signal wired below;
        this covers document/canvas switches where no change signal fires per pick.
        """
        try:
            self._on_krita_color_changed()
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
        """Convert a ManagedColor/QColor to an (r,g,b) tuple; alpha ignored.

        Use ``componentsOrdered()``: it always returns R,G,B,A. Plain
        ``components()`` returns the channels in the *document's* native order,
        which for an RGBA document is B,G,R,A -- reading it as RGB swaps red and
        blue, so an orange sample came back blue.
        """
        try:
            if hasattr(managed, "componentsOrdered"):
                c = list(managed.componentsOrdered())
                if len(c) >= 3:
                    return (int(round(c[0] * 255)), int(round(c[1] * 255)),
                            int(round(c[2] * 255)))
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

    def _current_krita_rgb(self):
        """Best-effort current foreground/background RGB from the active View."""
        view = self._active_view()
        if view is None:
            return None
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
        """Mirror an external color change into the orb. NEVER re-send to Krita (avoids a loop)."""
        try:
            if getattr(self, "_sync_guard", False):
                # This change is the echo of our own _send_to_krita() call.
                # Adopting it here would clobber the base target when the user
                # picked a color for the light or shadow instead.
                return
            cur = self._current_krita_rgb()
            if cur is None:
                return
            stored = self._targets["base"].getRgb()[:3]
            LOG.info("_on_krita_color_changed -> %s vs stored %s", cur, stored)
            if cur != stored:
                if getattr(self, "_awaiting_sampler", False):
                    # A canvas sample from the eyedropper: build a full
                    # lighting set around it rather than only moving base.
                    self._apply_sampled_color(cur)
                    return
                self._set_base_color(QColor(*cur))  # int rgb
                self._rebuild_orb()
                self._sync_sliders_from_state()
                self._update_preview()
        except Exception as exc:  # pragma: no cover - Krita-only path
            LOG.exception("_on_krita_color_changed failed")

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
        """Send the picked color to Krita's foreground."""
        try:
            from krita import Krita, ManagedColor
            view = Krita.instance().activeWindow().activeView()
            if view is None:
                return
            canvas = view.canvas()
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

    # NOTE: the orb NEVER assigns colours to its own targets. Clicking or
    # dragging it only chooses a colour for drawing (Krita's foreground).
    # The sphere's base/light/shadow are changed by the sliders, the target
    # dots, or the eyedropper tool -- nothing else.
    # Clicking or dragging the orb only drives the brush (Krita foreground) and
    # the hover preview. The sphere's colors change from the sliders, the target
    # dots, or the document eyedropper -- never from clicking the orb, which used
    # to re-render the sphere mid-drag and run the picked color away.

    def _on_orb_brush(self, color):
        """Choose a colour to draw with.

        Fires on press and on every move during a drag. The colour is sent to
        Krita's foreground and kept in the right (active) swatch so it survives
        leaving the orb. The sphere's own target colours are never touched.
        """
        try:
            self._chosen_color = QColor(color)
            self._set_swatch_color(self._sw_current, color)
            self._send_to_krita(color)
        except Exception as exc:  # pragma: no cover - Krita-only path
            LOG.exception("_on_orb_brush failed")

    def _on_orb_hover(self, color):
        """Show the RGB readout and sampling ring while over the orb.

        The right (active) swatch previews the colour under the pointer. When the
        pointer leaves it falls back to the colour the user last *chose* by
        clicking, rather than reverting to the sphere's base -- otherwise a
        deliberately picked drawing colour appeared not to have been kept.
        """
        try:
            if color is None:
                self._readout.setText("")
                self.cylinder.hide()
                self._update_preview()
            else:
                self._readout.setText("%d, %d, %d" % (color.red(), color.green(),
                                                      color.blue()))
                self.cylinder.set_color(color)
                self._set_swatch_color(self._sw_current, color)
        except Exception as exc:  # pragma: no cover - cosmetic only
            LOG.exception("_on_orb_hover failed")

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
            LOG.info("color sampler tool activated (returning to %s afterwards)",
                     self._sampler_prev_tool)
        except Exception as exc:  # pragma: no cover - Krita-only path
            self._awaiting_sampler = False
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
        """Hand the previous tool back so picking can be repeated."""
        name = getattr(self, "_sampler_prev_tool", None)
        if not name:
            return
        try:
            from krita import Krita
            app = Krita.instance()
            for a in app.actions():
                if a.objectName() == name:
                    a.trigger()
                    LOG.info("returned to tool %s; eyedropper still armed", name)
                    # Re-arm so the next canvas click samples again.
                    self._start_sampler_watch()
                    return
        except Exception as exc:  # pragma: no cover - Krita-only path
            LOG.exception("_restore_previous_tool failed")

    # --- Foreground watcher -------------------------------------------------
    # A 200 ms QTimer that reads the brush colour and redistributes it across
    # base/light/shadow. Started from __init__, so it runs for as long as the
    # docker is loaded.
    #
    # It exists because ``View.foregroundColorChanged`` is exposed on Krita 5.x
    # but never actually emitted, so _watch_krita_colors connects signals that
    # never fire. Polling is the only thing that makes external colour picks
    # reach the orb.
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
    SAMPLER_POLL_MS = 200

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
        """Distribute whenever the foreground colour changes externally.

        Compares against the last-seen value and does nothing on a tick where
        the brush colour is unchanged, so an idle poll is three cheap property
        reads and an int-tuple compare. The colour itself is never persisted;
        only the derived lighting setup is, via the normal settings save.
        """
        try:
            if self._sync_guard:
                return          # this change is our own echo; ignore it
            cur = self._current_krita_rgb()
            if cur is None or cur == self._sampler_baseline:
                return
            self._sampler_baseline = cur
            self._apply_sampled_color(cur)
        except Exception as exc:  # pragma: no cover - Krita-only path
            LOG.exception("_poll_sampler failed")

    def _apply_sampled_color(self, rgb) -> None:
        """Distribute a canvas-sampled colour across base, light and shadow."""
        try:
            color = QColor(*rgb)
            LOG.info("eyedropper sampled %s", color.name())
            self._distribute_from(color)
            self._rebuild_orb()
            self._sync_sliders_from_state()
            self._update_preview()
        except Exception as exc:  # pragma: no cover - UI only
            LOG.exception("_apply_sampled_color failed")
            print(f"Lumina: apply sampled colour failed - {exc}")
        finally:
            # Give the user their tool back after a canvas sample.
            if getattr(self, "_sampler_prev_tool", None):
                QTimer.singleShot(0, self._restore_previous_tool)

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
