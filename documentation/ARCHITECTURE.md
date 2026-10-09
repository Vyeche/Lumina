# Lumina Plugin — v2 (Lumina) Architecture

## Overview

Lumina is a Krita Python plugin providing an interactive 3D color sphere
for intuitive color picking. It renders a shaded sphere using Phong shading with
configurable ambient, diffuse, and specular lighting. Users pick colors by
dragging across the sphere surface, which updates Krita's foreground color in
real-time.

**Version 2 ("Lumina")** reworks the UI into a compact, icon-driven,
color-coded vertical sidebar (Blender/Substance Painter aesthetic), replacing
the previous wide, text-labeled panel.

---

## Architecture & Data Flow

### High-Level Structure

```
Lumina (Plugin)
├── SphereDocker (DockWidget) → Main UI container (V2 icon-driven sidebar)
│   ├── SphereWidget (QWidget) → Renders small circular sphere image
│   │   └── PaintEvent → Draws QImage onto widget; mouse picking
│   ├── Controls (ColorSlider, ToolButton) → User input
│   └── SphereColorProcessor → Shading engine adapter
└── Lumina.desktop → Plugin manifest
```

### Data Flow

1. **User Action** → Slider/BTN update → `set_light_intensity()`, etc.
2. **Engine Update** → `SphereColorProcessor` delegates to pure `ColorEngine`
3. **Image Generation** → `render_image()` → QImage with shading
4. **Render** → `SphereWidget.paintEvent()` → displays image
5. **Pick Color** → Mouse drag → `pixelColor()` → set Krita foreground

### Key Modules

| Module | Purpose | Location | Qt-Dependent? |
|--------|---------|----------|---------------|
| `SphereDocker` | Main docker widget (V2 UI) | `sphere_docker.py` | Yes (Krita only) |
| `SphereWidget` | Sphere rendering + picking | `sphere_widget.py` | Yes (Krita only) |
| `ColorEngine` | Pure-Python Phong shading | `color_engine.py` | **No** — testable anywhere |
| `SphereColorProcessor` | Krita-facing adapter | `color_processor.py` | Partial (guarded Qt import) |
| `ColorControls` | Icon-driven, color-coded widgets | `color_controls.py` | Yes (Krita only) |
| factory | Plugin registration | `__init__.py` | No |

---

## Design Decisions (v2)

### Why split the shading engine from Qt?

PyQt5 is **only available inside the Krita flatpak at runtime** — not on the
host machine or in CI. By making `color_engine.py` pure Python (tuples, no Qt),
the shading math can be unit-tested anywhere:

```bash
python3 test_shading_standalone.py   # runs on host, CI, or in Krita
```

### Why icon-driven, color-coded UI?

The V2 design shows a professional, compact sidebar. Key changes from v1:

| v1 (old) | v2 (new) |
|----------|----------|
| Wide text-labeled panel | Narrow vertical sidebar |
| Plain QSliders with labels | Color-coded thin sliders (RGB-style accent tints) |
| 200px square-ish sphere | Small circular sphere (120px) |
| Text buttons | Icon-driven toggles with accent dots |

### Accent color palette

Each control family has a distinct accent color so you can read channel values
at a glance without labels:

```python
Accent.RED     = (231, 72, 80)    # base / diffuse
Accent.GREEN   = (77, 210, 129)   # green channel
Accent.BLUE    = (74, 153, 246)   # blue channel
Accent.AMBER   = (251, 188, 54)   # intensity / highlight
Accent.PURPLE  = (167, 139, 250)  # ambient / environment
Accent.CYAN    = (34, 211, 238)   # glow / specular
Accent.NEUTRAL = (160, 174, 193)  # generic
```

---

## File Layout

| Path | Purpose |
|------|---------|
| `color_engine.py` | Pure-Python shading engine (no Qt) |
| `color_processor.py` | Krita-facing adapter over `ColorEngine` |
| `color_controls.py` | Icon-driven, color-coded control widgets |
| `sphere_widget.py` | Interactive sphere surface with picking |
| `sphere_docker.py` | Main V2 docker widget |
| `__init__.py` | Factory registration entry point |
| `Lumina.desktop` | Plugin manifest |
| `test_shading_standalone.py` | Shading engine tests (no Qt) |

---

## Development Commands

### Test (host / CI — no Krita needed)

`test_shading_standalone.py` hardcodes repo-relative paths, so it must be run
**from the repository root** — not from inside `src/Lumina/`, where it
fails with `FileNotFoundError` on the doubled path.

```bash
python3 src/Lumina/test_shading_standalone.py
```

### Install into Krita (flatpak)

See **[KritaFlatpak.md](KritaFlatpak.md)** for the deploy, cache-clear, restart
and verification commands. That document is the single source of truth for
deployment; it is not duplicated here.

---

## Shading Model

`color_engine.py` implements an extended Phong model, in this order per pixel:

1. Base × diffuse (`DIFFUSE_FLOOR`, `DIFFUSE_GAMMA`), tinted toward the light
2. Shadow-colour blend, weight `(1 - N·L)^SHADOW_FALLOFF`
3. Fresnel rim at the silhouette (`RIM_POWER`, `RIM_LIGHT`)
4. Ambient fill
5. Brightness / saturation
6. **Specular highlight** — the subject of the next section
7. Mixer mode, contrast, glow

Tuning constants live at the top of the module with the reasoning for each value
in comments, so the render can be adjusted without reading the loop.

### The specular highlight is a soft shoulder, not a clamp

The highlight is a Phong lobe `((N·R)·L)^(2·shininess)`, but its ceiling is
applied as a **rational curve**, not a clamp:

```python
spec = SPEC_MAX * s * (SPEC_KNEE + 1.0) / (s + SPEC_KNEE)   # color_engine.py
```

**Why.** Clamping at `SPEC_MAX` pinned every pixel above the ceiling to exactly
the ceiling, producing a flat-topped plateau ~4,400 px across at the default
shininess, with a visible step at its edge. The highlight read as a grey disc
*laid on* the sphere rather than as a sheen. The curve above passes through the
same origin and the same peak (`s=1 → SPEC_MAX`) but never saturates, so there
is no plateau and no discontinuity in the gradient — and it redistributes the
plateau's energy outward, widening the sheen without making it brighter.

Measured on the 256 px render (`shininess` 1.6 / 2.0 / 5.0):

| Metric | Before | After |
|---|---|---|
| Flat core (px) | 4356 / 3567 / 1499 | 264 / 212 / 85 |
| Area ≥50% of peak | 15.2% / 12.7% / 5.6% | 16.9% / 14.2% / 6.3% |
| Max per-pixel step | 0.0265 / 0.0278 / 0.0376 | 0.0199 / 0.0190 / 0.0211 |
| Peak | 0.5000 | 0.5000 (unchanged) |

`SPEC_KNEE = 0.35` was chosen by sweeping knee strength. Lower values spread the
sheen further but steepen the core, reintroducing a hotspot.

**Tuning.** The knee is exposed as `ColorEngine.spec_knee` and driven by the
**Diffuse** slider, which maps 0–100 onto `SPEC_KNEE_MAX`–`SPEC_KNEE_MIN`
(inverted, since a low knee is the diffuse look). It sits next to **Specular**
in the Advanced section because the two are halves of one control: Specular sets
the highlight's *width*, Diffuse its *softness*. The curve is monotonic over
`[0, 1]`, so it cannot ring or invert at any `shininess`.

## Persistence

User settings are stored with `QSettings` in `IniFormat` under the flatpak's
config dir — a plain-text file, readable and editable without Krita running.

Persisted: the three target colors, light azimuth/elevation, render quality,
sampler visibility, and every slider (contrast, intensity, ambient, specular,
diffuse, glow, tone, base level, mixer mode). Deliberately *not* persisted: the
last-picked brush colour and the active target, both per-session choices.

Two design points worth keeping:

- **Autosave is a single choke point**, in `_rebuild_sphere`, not per-handler. Every
  control already routes through it, so a control added later cannot be silently
  left unsaved — which is exactly what happened during development when each
  handler saved individually.
- **Restoring is guarded by `_syncing`**, held across the whole of
  `_load_settings` including the widget updates. The settings popup reports its
  *entire* state on every emission, so restoring its widgets one at a time pushes
  a half-applied mix of restored and still-default values back into the engine;
  `SettingsPanel.sync_from` blocks signals for the same reason.

---

## Testing & QA

### Standalone tests (no Krita)

| Test | Purpose |
|------|---------|
| `test_engine_gradient()` | Verifies diffuse gradient direction |
| `test_processor_delegation()` | Confirms adapter funnels into engine |
| `test_symmetry()` | Verifies left-right symmetry about light meridian |

### Krita Integration Tests

**Must verify in Krita:**
1. ✅ Sphere displays as full circle (not clipped)
2. ✅ Colors vary from dark (shaded) to bright (lit)
3. ✅ Base color updates the sphere
4. ✅ Color-coded sliders change the sphere appearance
5. ✅ Icon toggles (Artistic/Real-World) respond to clicks
6. ✅ Picking a color on the sphere updates Krita foreground
7. ✅ No errors in terminal output

**Run Krita for 30 seconds** to allow the user to open an image and see the panel.

---

## References

- **Official Krita Python Plugin Docs:** https://docs.krita.org/en/user_manual/python_scripting/krita_python_plugin_howto.html
- **Qt Widgets Documentation:** https://doc.qt.io/qt-5/widgets.html
- **Phong Shading Model:** Ambient + Diffuse + Specular lighting
