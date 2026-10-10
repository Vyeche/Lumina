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
│   ├── SphereWidget (QWidget) → Renders the small circular sphere preview
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
| `ColorControls` | Icon-driven, color-coded widgets; `ColorSampler` (lemon), `RecentColors`, `SparkleBurst`, `SettingsPanel` | `color_controls.py` | Yes (Krita only) |
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

1. Tonal path shadow → base → light by N·L (`TONAL_SHADOW_START`, `TONAL_BASE`,
   `TONAL_LIGHT_POWER`), times the diffuse response (`DIFFUSE_FLOOR`)
2. Shadow-colour blend, weight `(1 - N·L)^SHADOW_FALLOFF`
3. Fresnel rim at the silhouette (`RIM_POWER`, `RIM_LIGHT`)
4. Ambient fill
5. Brightness / saturation
6. **Specular highlight** — the subject of the next section
7. Mixer mode, contrast, glow
8. Display roll-off: identity up to `TONE_SHOULDER`, exponential ease above

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

### Calibrated against a reference lighting app

The derivation (`derivation.py`) and the tonal path and roll-off above were
fitted to a reference app's own spheres, captured in the Ref Tester document
(`images/`, not committed). `tools/reference_calibration.py` holds the
derivation data, fit and leave-one-out check. Each sphere was compared with its
own shadow/base/light and a matched light (304°/41°, since adopted as the
default).

Mean error (OKLab ×100) before → after the engine change: highlight 11.0 → 6.3,
lit 12.3 → 6.8, terminator 7.4 → 5.7, core shadow 6.7 → 4.4, rim 8.3 → 4.9,
sky side 10.9 → 6.0, ground side 6.6 → 4.5. Two things drove it: Reinhard
compressed every value (a lit `#ff0000` came out `#bb0000`), and the light
colour washed over the whole lit half instead of gathering in the highlight.
Slider defaults were not changed.

A second pass fixed what a side-by-side in Krita showed was still wrong: the
reference uses the three targets as *ingredients*, not paint. Its unlit side is
a base/shadow mix (`TONAL_SHADOW_MIX` 0.53) darkened by the shading, its
highlight only reaches `TONAL_LIGHT_MAX` (0.83) of the light target, and its
specular is warm on saturated colours but neutral on greys (`SPEC_WARMTH`,
scaled by the base's saturation; a preset's highlight colour is left alone).
Region error after the second pass: highlight 4.7, lit 5.8, terminator 3.9,
core shadow 2.8, rim 3.9, sky side 5.5, ground side 2.7 (overall 9.4 → 4.4).
These figures are in-sample, fitted and scored on the same screenshots.

A third pass (issue #45) went after depth: the shadow side read paler and
greyer than the reference, and dark colours had almost no highlight. Measured
along N·L on every reference sphere, lightness already matched; chroma and the
highlight did not.

- **Environment light takes the surface's colour** (`ENV_ALBEDO` 0.7). The
  sky-tinted ambient and the sky / ground bounce were added as plain light, a
  grey film that dominated dark saturated shadows: the reference's blue keeps
  chroma in step with lightness (C/L 0.68 at the core, base 0.69), Lumina's
  fell to 0.49.
- **Shadow hue by signed N·L** (`SHADOW_HUE_MAX` 0.59 from `SHADOW_HUE_START`
  0.57 to `SHADOW_HUE_CORE` −0.26). The surface keeps the base's lightness and
  moves its OKLab chroma direction toward the shadow target's, building from
  the lit side of the terminator into the core; the lighting does the
  darkening. It replaces the linear `TONAL_SHADOW_MIX` blend, which put the
  shadow colour's hue at the terminator and stopped at N·L 0.
- **The highlight sits near the light** (`SPEC_TOWARD_LIGHT` 0.875). The
  reference's peaks about two thirds of the way toward the light and stays
  bright to the lit silhouette; a Blinn lobe sat nearer the centre and died
  before the edge. With the lobe moved, it is broader and stronger (shininess
  8.9, `spec_max` 0.61; the presets' `spec_max` scaled by the same 61/27), and
  the light colour's share of the highlight drops (`TONAL_LIGHT_MAX` 0.4).
  `SPEC_WARMTH` 0.75.

Whole-sphere error (OKLab ×100 per N·L band and limb sector, 15 reference
spheres): 3.16 → 1.86. The lit limb on dark colours went from 17–23 to 1–5,
the highlight band on black from 9.4 to 3.8.

A fourth pass (issue #45, round 2) went after the *structure* of the shadow
side, which ring averages hide: base, shade, core and rim sit next to each
other, and the gradation between them is what reads as depth. It was scored
in cells of signed N·L × radius, in a wedge facing away from the light, and on
the shape of lightness against N·L.

- **The shade grades longer.** The diffuse response is now
  `DIFFUSE_LEVEL · (1 + g)E / (1 + gE)`: `DIFFUSE_GAIN` (g, 0.275) sets how
  soon it bends over and `DIFFUSE_LEVEL` (1.32) how high it goes. It was
  2gE/(1+gE) with g 1.155, which tied the two together; the shade was nearly
  at full brightness by N·L 0.3 and squeezed into a narrow band. The
  reference brightens almost linearly from the terminator to N·L 0.6.
- **The rim is a band.** Reflected light used `edge ** REFLECT_POWER`, nearly
  all of it in the outermost few percent. It now rises as a smoothstep from
  `REFLECT_START` 0.38 to `REFLECT_END` 0.82 (in 1 − N.z) and holds, at
  `REFLECT_STRENGTH` 0.065, so it lifts out of the core and levels off at the
  silhouette. `REFLECT_POWER` applies only when `REFLECT_END` is 0.
- Refitted with them: `FLOOR_SOFTNESS` 0.193, `REFLECT_SPREAD` 3.88,
  `SHADOW_HUE_MAX` 0.575, `SHADOW_HUE_CORE` −0.32. Matte's intensity went
  from 96 to 80 to keep its peak where it was (the higher curve top lifts a
  surface with no specular).

Whole-sphere error 1.86 → 1.65; shadow-structure error 1.74 → 1.54; mean
lightness-curve error from the terminator to N·L 0.6, 0.031 → 0.015.
`test_shade_grades_into_the_shadow_and_the_rim_is_a_band` holds both.

Then the highlight. Lumina's peak was 0.02–0.10 OKLab L brighter than the
reference's on every sphere (0.06 on average) and sat further out, about 0.74
of the radius toward the light against the reference's 0.66. Fitted on the
high-N·L bands and the peak's brightness and position, with the whole-sphere
error held where it was:

- `SPEC_GAIN` 0.748, a new engine-side scale on the specular sheen under the
  Highlight strength slider, so nobody's saved `spec_max` moves.
- `SPEC_TOWARD_LIGHT` 0.875 → 0.845, which brings the peak in.
- `TONAL_LIGHT_MAX` 0.4 → 0.16 and `TONAL_LIGHT_POWER` 8 → 2.39: less of the
  light colour, spread over the lit side instead of packed at the peak.
- `TONE_SHOULDER` 0.646 → 0.556, `SPEC_WARMTH` 0.75 → 0.51, and
  `DIFFUSE_GAIN` 0.275 → 0.329.

Highlight-band error 3.36 → 2.24; peak brightness-and-position error
6.79 → 1.79; whole-sphere 1.65 → 1.66; shadow structure 1.54 → 1.55. Peaks
now: black 0.784 (reference 0.76), blue 0.778 (0.76), green 0.912 (0.92).
`test_highlight_peak_matches_the_reference_brightness` holds them.

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


## Krita's color and Lumina's

Krita's color is the source of truth: it replaces Lumina's selected target and is
never written back on an external change. Lumina only writes Krita's foreground
when the user acts in Lumina (sphere click, *Use base color as brush*).

- **Working space.** Target numbers are in the *active layer's* profile
  (`_space_profile`), matching Krita's Specific Color Selector, which is locked to
  the current layer's space by default. `_sync_working_space` re-expresses every
  target when that changes and re-baselines the foreground watcher so a layer
  switch is never read as a pick. Each target keeps its origin (`_space_memo`):
  conversions start there, because 8-bit round trips drift.
- **Reading.** `_managed_color_to_rgb` converts a copy of Krita's foreground into
  the working space with `ManagedColor.setColorSpace` -- not `colorForCanvas`,
  which is the monitor conversion.
- **Writing.** `_send_to_krita` builds the color in the working profile (native
  B,G,R,A order), so Krita stores exactly what Lumina shows.
- **Drawing.** The engine and the swatches paint sRGB for Qt, so targets pass
  through `_to_display` before rendering and sphere picks through `_from_display`
  on the way back.
- **Routing.** A pick replaces whichever target is selected; with the base
  selected it is distributed into light and shadow as well. The readout under
  the targets says so for 2.5 s ("Base <- Krita colour #hex").
- **Echo guard.** A sphere pick writes Krita's foreground, and the 50 ms watcher
  must not read that back as a Krita pick. `_send_to_krita` raises `_sync_guard`
  for the write and re-baselines the watcher on what Krita stored; on top of
  that, `_note_external_color` ignores every foreground change while the sphere
  is pressed and for `SPHERE_ECHO_MS` (350) after its last pick (logged
  `EXT_IGNORED`). Each adopted stream logs `EXT_SOURCE` with its context
  (sphere pressed, ms since the last sphere pick, Krita's active tool).

---

## Panel interaction

- **Sampler.** `ColorSampler` (in `color_controls.py`) is the lemon that rests on
  the sphere under the pointer: turned along the curve, foreshortened toward the
  silhouette, filled with the picked pixel, edged in its own colour moved in
  OKLab lightness (never black or white). It pops on press, holds at 1.1x,
  shows a countdown ring and then sweats (`_drops`) while held. The mouse pointer
  is hidden while it shows.
- **Sticky edge.** Picks project onto the rim up to `SphereWidget.edge_glide`
  (Settings > Sticky distance) past the silhouette. A drag keeps the mouse; a
  hover past the widget bounds is followed by `_OutsideTracker`, an application
  event filter installed only while needed.
- **Picks into targets.** Sphere clicks set the brush. The exceptions --
  Shift-click, a double-click on a recent pick, the hex box -- all go through
  `_assign_target`.
- **History.** `_update_preview` is the funnel for every target change; it feeds
  `_hist_note`, which records one undo step once the targets have held still
  for 0.6 s (so a slider drag is one step).
- **Presets.** A lit preset stays lit while `_preset_matches` holds (lighting
  only; colour changes keep it lit). The lit preset and the lighting it restores
  (`_preset_before`) are saved. A preset click renders the drag-size preview at
  once and the full render a beat later; `SphereColorProcessor` keeps the last
  `FULL_CACHE_SIZE` (8) full-quality renders, so toggling back is instant.
  The six presets were retuned against the calibrated shading so each reads as
  its name on any base (`test_presets_look_like_their_names` keeps Nocturne low
  key, Gloss's peak above Matte's, Neon more saturated); Neon's bloom needed the
  glow to show at all, so `GLOW_GAIN` went from 0.18 to 0.5.
- **Settings popup.** `_settings_position` places it beside the docker on the
  canvas side, inside Krita's window on the docker's monitor (`_settings_bounds`).
- **Full renders off the UI thread.** A full-quality render is ~330 ms of pure
  Python at the usual 288 px, which froze the panel after every slider release
  (the knob ripple and sweat stopped mid-air) and queued wheel notches behind
  it. Inside Krita, `_submit_full_render` hands it to one worker thread with an
  engine of its own (`SphereColorProcessor.full_render_job` snapshots the
  settings, `run_full_render_job` renders, `store_full_render` caches it back on
  the UI thread). Only the newest waiting job is kept, and a result is shown
  only if nothing newer was drawn meanwhile (`_render_gen`). Previews stay in
  step, and a preview or a slider starting to move cancels the render under
  way: `ColorEngine._cancel` is checked once per row, and the stage caches
  commit only when a stage completes. An identical request adopts the running
  render. While a worker runs, Python's switch interval drops from 5 ms to 0.2 ms
  (`BG_SWITCH_INTERVAL`): at 5 ms the UI thread waited that long to win the
  interpreter lock back each time it returned from Qt, and the panel stalled
  almost as badly as before. Measured in Krita, a 10 ms timer stays within
  ~14 ms of schedule through a render. Tests and the docs renderer keep
  rendering in step (`_async_full_render` is on only inside Krita).
- **Wheel and arrow keys.** A burst of wheel notches or arrow-key steps on a
  slider is one drag (`ColorSlider.WHEEL_SETTLE_MS`, 250 ms after the last
  step), so it previews at drag size instead of a full render per notch.

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
