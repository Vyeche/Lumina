New bugs

### Contrast did nothing, then crashed at the slider's minimum (fixed 2026-09-29)

**Symptom:** dragging the Contrast slider changed nothing until the very end,
where it took the docker down with `ZeroDivisionError: float division by zero`.

**Two independent defects in the same block:**

1. **The tone map was a no-op.** The code computed `mapped = luma ** inv_contrast`
   and then `scale = mapped / (mapped if mapped > 1e-6 else 1e-6)`. That
   denominator is `mapped` itself, so `scale` is exactly 1.0 for every input.
   Measured: **0 differing pixels** across the whole 0.25–3.0 range. The gamma
   exponent was being computed and then thrown away.
2. **`contrast = 0` divided by zero.** `inv_contrast = 1.0 / contrast`, and the
   Contrast row's minimum is 0, so dragging the handle to the far left raised
   `ZeroDivisionError` inside `render` — which is on the docker's paint path,
   so it took the whole panel down.

**Fix:** replaced the gamma with a mid-grey-anchored exponent curve that has no
division in it:

```
below mid:  0.5 * (2*luma) ** contrast
above mid:  1 - 0.5 * (2*(1-luma)) ** contrast
```

Identity at contrast 1, every pixel collapsing to flat mid-grey as contrast
approaches 0. Measured after the fix: ~7,000 pixels change per setting, tonal
spread rises monotonically from 0.7 (contrast 0) to 175.7 (contrast 2.0), and
hue is preserved exactly (0.0° shift) because the curve is luma-only.

### Slider rows displayed values the engine was not using (fixed 2026-09-29)

**Symptom:** the panel opened showing Contrast 50, Intensity 70 and Tone 50 while
the engine actually sat at 1.0 for all three. The handle position did not
describe the state being rendered, so the first drag produced a large jump from
an unrelated starting point.

**Cause:** the row defaults were hardcoded literals that had drifted from the
engine's real defaults.

**Fix:** the rows are now constructed from the engine's actual state
(`_eng.contrast * 100`, etc.). Verified all six advanced rows match.

### Sliders silently clamped values above 99 (fixed 2026-09-29)

**Symptom:** asking a slider for 100 gave 99.

**Cause:** `LabeledSliderRow` set the value *before* the range, so against Qt's
default 0–99 range anything above 99 was clamped and left the handle short of
where it was asked to be. Four rows (Intensity, Ambient, Glow, Specular) were
never given an explicit range at all, and Contrast and Tone were capped at 100
even though the engine allows 2.0 for both.

**Fix:** `LabeledSliderRow` now takes `lo`/`hi` and applies the range before the
value; the rows pass their ranges through the constructor. Contrast and Tone now
span 0–200, Specular 1–128 (matching `set_shininess`'s clamp).

### Knob grab area was the knob itself (fixed 2026-09-29)

**Symptom:** you could not start a drag from the edge of the round handle on any
slider.

**Cause:** Qt's default hit area is the widget's own geometry, and the value
mapped absolutely to the pointer's x. So a press on the rim of the knob either
missed it or snapped the value to wherever the pointer happened to be.

**Fix:** `ColorSlider` gained a `GRAB_RADIUS` of 18px (the knob is ~11px) and
*relative* dragging — when the press lands within that radius the value follows
the pointer's delta from where the drag started instead of jumping to the
pointer's position. Presses outside the radius still jump, as before. Verified:
edge grabs work, extremes are reachable, motion is linear, and `beganDrag` /
`endedDrag` still fire so the docker's drag render cap is unaffected.

### Startup warning: `QWidget::setLayout ... already has a layout` (fixed 2026-09-29)

**Cause:** `_build_ui` installed `outer = QVBoxLayout(main)` and then, at the
end, called `main.setLayout(layout)` — where `layout` belongs to `content`, the
widget inside the scroll area. Qt does not replace an existing layout; it
detaches the new one. So the call achieved nothing beyond the warning, and it
fired once per docker construction.

**Fix:** removed the call. Verified 0 warnings from a `qInstallMessageHandler`
probe, and the panel still lays out identically.

### "Reset all" left most of the panel untouched (fixed 2026-09-29)

**Symptom:** *Reset all* restored the target colors, light angle, mixer and
contrast — and left ambient, intensity, specular, diffuse, glow, tone and base
level exactly where the user had put them.

Not visible on its own. It became a real bug once the whole panel was being
persisted: the values reset *did* save, but the leftovers were written back too,
so the next launch restored a half-reset setup and the reset looked like it had
silently failed.

**Fix:** `_on_settings_reset` now resets every value the settings file persists,
and writes the result.

### Sphere edge was jagged, and picking died at the rim (fixed 2026-09-29)

Two related defects on the silhouette.

**Symptom:** the orb's edge was visibly choppy, and colours right at the rim were
effectively unpickable.

**1. The edge had no antialiasing at all.** `render_image` wrote a binary alpha
(`255 if mask else 0`). The mask is a hard in/out test, so the rim was a
staircase of full-on/full-off steps — the classic jaggies.

**Fix** (`color_engine.py`, `color_processor.py`): added `_build_coverage_grid`,
which returns the fraction of each pixel the circle covers. Because the
silhouette is a circle centred on the origin, the exact distance from a pixel
centre at radius `r` to the boundary is `1 - r` regardless of angle, so
dividing by the pixel pitch gives the coverage directly. `render_image` now
writes that as fractional alpha.

The one-pixel band *outside* the circle is also partly visible but is returned
black by the engine, so it needed its colour borrowed from the nearest shaded
neighbour — otherwise antialiasing just trades jaggies for a dark one-pixel
halo. (The two byte-buffer builders, one for QImage and one for the non-Qt
fallback, were identical copies; they are now one shared `_build_buffer`.)

**2. Picking stopped dead at the silhouette.** `_to_sphere_coords` rejected
anything with `u² + v² > 1`. Near the edge a pixel of cursor movement spans a
large arc, so the terminator was practically unreachable.

**Fix** (`sphere_widget.py`): a narrow band just outside the circle
(`EDGE_GLIDE`, 6px) now projects the point radially back onto the silhouette,
so dragging past the edge glides the sample around the rim.

This needed a second fix to actually work: the orb was drawn to fill its
widget edge-to-edge, so the widget bounds rejected the point *before* the glide
band was ever reached — at the four points where circle and square touch, and
nowhere else. The orb is inset by `ORB_MARGIN` (8px) to give the band room.
Drawing and picking now both read a single `_orb_geometry()` helper, since they
previously recomputed the centring separately and could drift apart.

Also set `QPainter.SmoothPixmapTransform` in `paintEvent`: the orb is drawn at
whatever resolution it was rendered, and the drag path renders at 128px and
upscales into a 200px widget, which was blocky.

**Verify (offscreen, 2026-09-29):** 636 partial-alpha pixels against a ~622px
circumference, no dark fringe, continuous picking across the whole band with no
dead spots at any of 720 angles around the rim, and corners still unpickable.
15/15 checks pass.

### Sliders moved in slow motion (fixed 2026-09-29)

**Symptom:** dragging a slider in the settings popup felt sluggish and the
handle lagged the cursor.

**Cause:** the shading loop is pure Python and scales with pixel count. Measured
per-update cost: 15.6 ms at 128px, 35.6 ms at 200px, **76.0 ms at 288px** (the
*High* preset) — 13 Hz. The existing 0 ms coalescer cannot help, because a
blocking 76 ms render stalls the event loop itself, so the *handle* falls behind
rather than just the orb.

**Fix:** while any slider is held, the orb renders at 128px; full quality is
restored on release. `ColorSlider` gained `beganDrag` / `endedDrag` signals, and
the docker wires all 12 sliders in a single `findChildren` pass so a slider
added later — in the main panel or the settings popup — is covered for free.
**4.1x faster** while dragging (57.9 ms → 14.3 ms).

Note the flag is deliberately *not* `_dragging`: that name already tracked the
floating title bar's window drag. Sharing it would let a title-bar drag drop the
orb to 128px, and a slider release would cancel an in-progress window drag.

### "Show color sampler" toggle did nothing (fixed 2026-09-29)

**Symptom:** unchecking *Show color sampler* in the gear popup left the ring
following the cursor on the orb. The toggle appeared completely inert.

**Two independent defects, both required for the symptom:**

1. **It gated the wrong marker.** The orb draws *two* separate markers, and the
   flag only covered the smaller one:

   | Marker | Drawn by | Gated by `_show_pointer`? |
   |---|---|---|
   | 56px colour ring that follows the cursor | `ColorSampler` (`self.cylinder`), shown via `_reposition_preview` | **No** |
   | 6px ring marking the last sampled point | `SphereWidget.paintEvent` | Yes |

   So switching the toggle off removed a 6px ring — easy to miss — while the
   prominent 56px ring the label named stayed exactly where it was.

2. **Nothing could have kept it hidden anyway.** `_reposition_preview` runs on
   every pointer move and called `preview.show()` unconditionally, so even
   gating it at that point would have been undone on the next mouse movement.

**Fix** (`sphere_widget.py`):
- `set_show_pointer` now also hides the sampling ring immediately when switched
  off. Without this the ring lingered until the pointer next left the orb,
  which itself reads as the toggle having failed.
- `_reposition_preview` returns early and hides the ring when `_show_pointer` is
  false, so a subsequent pointer move cannot resurrect it.

**Verify (offscreen, 2026-09-29):** toggling off while hovering hides the ring
immediately; it stays hidden across repeated pointer moves and fresh hover
events; toggling back on restores it; and leaving the orb still hides it. 7/7
checks pass. `test_shading_standalone.py` and `test_shading.py` unaffected.

### Section controls never render (fixed 2026-09-15)

Symptom: the "Lumina" docker instantiated without a traceback, but every section showed only its colored header pill + title label — the Color slider, all 6 Lighting sliders, and the 4 Tool buttons were absent.

**Diagnosis (verified 2026-09-15 in the flatpak offscreen probe):** two independent defects in the section builder.

1. `AccentGroup.__init__` installed a `QHBoxLayout` on the group (`QHBoxLayout(self)`). `_add_section` then called `QVBoxLayout(group)` to stack the header + content vertically. In the flatpak's Qt/PyQt (Python 3.13), **installing a layout on a widget that already has one does not replace it** — it emits `QLayout: Attempting to add QLayout "" to ... which already has a layout` and leaves the new layout *detached* (`group.layout()` still returns the old `QHBoxLayout`). Widgets added to that orphaned layout get `parent=None`, so they never render. Confirmed empirically: `isinstance(row, QWidget)` is `True`, yet after `_add_section` the row's `parent()` is `None`.
2. `_add_section`'s `isinstance(child, QWidget)` filter dropped `QLayout` children. The Lighting and Tools sections pass a `QVBoxLayout`/`QHBoxLayout` (not bare widgets), so they were silently skipped even with fix #1.

**Fix:**
- `color_controls.py` — `AccentGroup` now owns an outer `QVBoxLayout` (header row kept as a *nested* `QHBoxLayout` sub-layout) instead of a bare `QHBoxLayout(self)`. The group keeps this layout so callers add children to it rather than replacing it. Dropped the dead `self._spacer` (set once, never read).
- `sphere_docker.py` — `_add_section` now uses `layout = group.layout()` (never `QVBoxLayout(group)`) and accepts both `QLayout` (`addLayout`) and `QWidget` (`addWidget`) children. Also removed the redundant `layout.addLayout(tool_layout)` in `_build_ui`, which double-added the Tools layout.

**Verify (flatpak offscreen, 2026-09-15):** `QT_QPA_PLATFORM=offscreen flatpak run --command=python3 org.kde.krita <_probe>` instantiates `SphereDocker()` and the layout-tree walk finds **7 slider rows** `[30,70,10,50,64,0,50]` and **4 tool buttons** (amber/purple/cyan/red). Tree dump shows clean nesting: each `AccentGroup` -> `[header pill, title, <content>]`.
Traceback (most recent call last):

  File "/app/lib/krita-python-libs/krita/dockwidgetfactory.py", line 23, in createDockWidget
    return self.klass()
           ~~~~~~~~~~^^

  File "$HOME/.var/app/org.kde.krita/data/krita/pykrita/LuminaPlugin/sphere_docker.py", line 161, in __init__
    self.color_row = LabeledSliderRow(Accent.RED, "Base", 30)
                     ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^

  File "$HOME/.var/app/org.kde.krita/data/krita/pykrita/LuminaPlugin/sphere_docker.py", line 66, in __init__
    super().__init__(parent)
    ~~~~~~~~~~~~~~~~^^^^^^^^

TypeError: QWidget(parent: Optional[QWidget] = None, flags: Union[Qt.WindowFlags, Qt.WindowType] = Qt.WindowFlags()): argument 1 has unexpected type 'int'

old

Traceback (most recent call last):

  File "/app/lib/krita-python-libs/krita/dockwidgetfactory.py", line 23, in createDockWidget
    return self.klass()
           ~~~~~~~~~~^^

  File "$HOME/.var/app/org.kde.krita/data/krita/pykrita/LuminaPlugin/sphere_docker.py", line 157, in __init__
    self._orb = SphereWidget()
                ~~~~~~~~~~~~^^

  File "$HOME/.var/app/org.kde.krita/data/krita/pykrita/LuminaPlugin/sphere_widget.py", line 32, in __init__
    self.setAutoFocusEnabled(True)
    ^^^^^^^^^^^^^^^^^^^^^^^^

AttributeError: 'SphereWidget' object has no attribute 'setAutoFocusEnabled'. Did you mean: 'setShortcutEnabled'?


Traceback (most recent call last):

  File "/app/lib/krita-python-libs/krita/dockwidgetfactory.py", line 23, in createDockWidget
    return self.klass()
           ~~~~~~~~~~^^

  File "$HOME/.var/app/org.kde.krita/data/krita/pykrita/LuminaPlugin/sphere_docker.py", line 153, in __init__
    self.processor = SphereColorProcessor(resolution=200)
                     ~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^

  File "$HOME/.var/app/org.kde.krita/data/krita/pykrita/LuminaPlugin/color_processor.py", line 67, in __init__
    self.engine = __import__("color_engine", fromlist=["ColorEngine"]).ColorEngine(resolution=self.resolution)
                  ~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

  File "/app/lib/krita-python-libs/krita/__init__.py", line 38, in importAvoidWrongPyQtHack
    return import_real(name, globals, locals, fromlist, level)

ModuleNotFoundError: No module named 'color_engine'

**Diagnosis (verified 2026-09-15):** The flatpak docker crashes on load because ``color_processor.py`` imported the engine with a bare ``__import__("color_engine", ...)``. That absolute import only resolves when run from inside the package directory (the standalone test), but inside Krita the module is ``LuminaPlugin.color_engine``, so it raises ``ModuleNotFoundError: No module named 'color_engine'`` and the docker dies hard (dockers have no graceful fallback — the whole dock crashes).

**Fix:** replace the bare import with a relative import (``from .color_engine import ColorEngine``) guarded by an absolute-import fallback for the standalone host test — mirroring the existing Qt-guard pattern already in that file. Verified: ``py_compile`` clean and ``test_shading_standalone.py`` passes (exercises the fallback path); the relative path is exercised inside Krita's ``LuminaPlugin`` package context.



vye@pop-os:~$ flatpak run org.kde.krita
QIBusPlatformInputContext: invalid portal bus.
QIBusPlatformInputContext: invalid portal bus.
QIBusPlatformInputContext: invalid portal bus.
QIBusPlatformInputContext: invalid portal bus.
QObject::startTimer: Timers cannot have negative intervals
/app/lib/krita-python-libs/krita added to PYTHONPATH
krita.scripting: "Traceback (most recent call last):"
krita.scripting: "  File \"/app/lib/krita-python-libs/krita/__init__.py\", line 38, in importAvoidWrongPyQtHack"
krita.scripting: "    return import_real(name, globals, locals, fromlist, level)"
krita.scripting: "  File \"$HOME/.var/app/org.kde.krita/data/krita/pykrita/LuminaPlugin/__init__.py\", line 8, in <module>"
krita.scripting: "    from .sphere_docker import SphereDocker"
krita.scripting: "  File \"/app/lib/krita-python-libs/krita/__init__.py\", line 38, in importAvoidWrongPyQtHack"
krita.scripting: "    return import_real(name, globals, locals, fromlist, level)"
krita.scripting: "  File \"$HOME/.var/app/org.kde.krita/data/krita/pykrita/LuminaPlugin/sphere_docker.py\", line 31, in <module>"
krita.scripting: "    from PyQt5.QtWidgets import ("
krita.scripting: "    ...<2 lines>..."
krita.scripting: "    )"
krita.scripting: "ImportError: cannot import name 'DockWidget' from 'PyQt5.QtWidgets' (/app/lib/python3.13/site-packages/PyQt5/QtWidgets.abi3.so)"
krita.scripting: "Could not import LuminaPlugin"
krita.scripting: Error loading plugin "LuminaPlugin"
qrc:/OpenTypeFeatureDelegate.qml:113:5: QML ToolTipBase: cannot find any window to open popup in.
qrc:/OpenTypeFeatureDelegate.qml:113:5: QML ToolTipBase: cannot find any window to open popup in.
## Diagnosis (verified 2026-09-15)

**Root cause: import name, not a logic bug.** The Krita flatpak bundles PyQt5 built on Python 3.13, and that build does NOT export the `DockWidget` alias from `PyQt5.QtWidgets`. Importing it raises the `ImportError` above, so the plugin never loads. The shading logic itself is healthy (`test_shading_standalone.py` passes).

**Evidence (run inside the real flatpak runtime):**
- `from PyQt5.QtWidgets import DockWidget` -> `ImportError` (same as Bugs.md log)
- `from PyQt5.QtWidgets import QDockWidget` -> OK
- `from PyQt5.QtWidgets import DockWidgetFactory` -> `ImportError` (these classes live in the `krita` module, not QtWidgets)

**Applied fix (2026-09-15) in `src/Lumina/sphere_docker.py`:**
1. Import the canonical class name: `DockWidget` -> `QDockWidget` (line 32) and `class SphereDocker(DockWidget):` -> `class SphereDocker(QDockWidget):`. The flatpak's PyQt5 (Python 3.13) exports only `QDockWidget`.
2. Dropped the dead, unimportable `DockWidgetFactory, DockWidgetFactoryBase` from the QtWidgets import tuple — those classes live in the `krita` module, not QtWidgets.
3. Added a guarded `from krita import Krita, DockWidgetFactory, DockWidgetFactoryBase` and a module-level `Krita.instance().addDockWidgetFactory("color_sphere_factory", DockWidgetFactoryBase.DockRight, SphereDocker)` at the end of the file. **This registration step was the second root cause of "docker never comes up":** without a registered factory the docker does not appear, even with the import fixed. Both the import and registration are guarded so the module still imports outside Krita (static inspection / host tests).

**Verify:** copy the updated package contents into the deployed folder (renamed) and clear the cache, then restart Krita:
```bash
cp -r src/Lumina/. $HOME/.var/app/org.kde.krita/data/krita/pykrita/LuminaPlugin/
rm -rf "$HOME"/.var/app/org.kde.krita/data/krita/pykrita/LuminaPlugin/__pycache__
```
Confirm (a) no `ImportError` in the Krita terminal and (b) the "Lumina" dock appears. `X-KDE-Library=LuminaPlugin` matches the deployed folder name (source package `src/Lumina/` is renamed to `LuminaPlugin` on deploy).