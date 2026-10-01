# Lumina

An interactive lighting study tool for Krita. A sphere is shaded from three
colours you control — **shadow**, **base** and **light** — so you can read how a
key light, an ambient bounce and an object's local colour interact, then sample
the resulting values straight into your brush.

The useful question it answers is never "what is the local colour" but "what
does this surface look like under this light" — which is why it earns its keep
on flat and cel-shaded illustration *and* on 3D shading and rendering. Pick the
cel-shaded bands, pick the smooth falloff; the three targets and the shading
controls work either way, and the sphere shows you which one you are actually
looking at.

![Lumina at work: a flat poster-style sunset beach, the lighting sets the plugin derived from it painted as a palette strip, and the panel on the right](images/Lumina_feature.png)

That shot is the two worlds side by side. The beach is flat graphic work —
banded sky, hard-edged sun, silhouette palm and dog, birds, a fish shoal. The
panel beside it is a smoothly shaded 3D sphere lit from the same key. Three
colours sampled off the artwork — a sunset orange, a sea teal and a sand cream —
and Lumina derived a coherent shadow / base / light set from each; the strip
along the bottom of the canvas is its output, and the sphere on the right is
what those same values look like rendered rather than banded.

**New here? Start with [Getting started](documentation/GettingStarted.md).**

## Features

- **Phong shading** — ambient, diffuse, specular, Fresnel rim and a mixer mode,
  rendered in pure Python with no third-party dependencies.
- **Three linked targets** — shadow, base and light. Each has its own colour
  swatch beside its glyph, and the active one is edited by the Hue / Saturation /
  Light sliders.
- **Six lighting presets** — Artistic, Real, Nocturne, Gloss, Matte and Neon.
  Each fully defines the look, so switching presets never leaves the previous
  one's glow or ambient behind.
- **Direct colour sampling** — drag across the sphere to read highlight,
  midtone and shadow values; Krita's foreground colour updates live.
- **Settings persistence** — your setup is saved as you work and restored next
  time Krita opens.
- **Adjustable light direction and render quality**, plus a headless colour
  sampler ring that follows the pointer.

## Installation

The plugin runs inside the Krita flatpak. From the repository root:

```bash
DEPLOY="$HOME/.var/app/org.kde.krita/data/krita/pykrita"
rsync -a --delete --exclude='__pycache__' src/Lumina/ "$DEPLOY/Lumina/"
cp src/Lumina.desktop "$DEPLOY/"
```

Then restart Krita — it caches its plugin list at startup, so closing the window
is not enough. The dock appears under **Settings → Dockers → Lumina**.

See [documentation/KritaFlatpak.md](documentation/KritaFlatpak.md) for the full
deploy, cache-clear and verification steps, including a headless check that runs
the plugin under Krita's own Python.

## Usage

1. Open the **Lumina** docker.
2. Pick a target — click either the glyph or its colour swatch.
3. Adjust **Hue**, **Saturation** and **Light** for that target. *Light* is the
   base target only; shadow and light are derived from it.
4. **Click and drag** on the sphere to sample a colour. Krita's foreground
   (brush) colour updates immediately; the sphere itself is never changed by
   picking.
5. Use the **gear** for light direction, render quality, the sampler toggle and
   **Save settings**.

## How it works

The sphere is a unit hemisphere. Each pixel maps to a surface normal, and the
shading is evaluated per pixel:

```
C = base × diffuse × light_tint      # light-tinted diffuse
  + shadow_colour × (1 - N·L)^1.35   # shadow blend
  + Fresnel rim at the silhouette     # (1 - z)^5
  + ambient fill
  + specular highlight                # soft-shouldered Phong lobe
  then contrast, saturation, mixer and glow
```

The shading engine (`color_engine.py`) is deliberately free of Qt and Krita
imports, so the whole render can be unit-tested on any machine. See
[documentation/ARCHITECTURE.md](documentation/ARCHITECTURE.md) for the design
decisions and the reasoning behind each tuning constant.

## File structure

```
src/Lumina/
  __init__.py              Registers the dock widget factory with Krita
  sphere_docker.py         The docker: layout, controls, presets, persistence
  sphere_widget.py         The orb: painting, picking, hover sampler
  color_engine.py          Pure-Python shading engine (no Qt, no Krita)
  color_processor.py       Adapter between the engine and Krita/Qt
  color_controls.py        Sliders, buttons, settings panel
  test_shading.py          Smoke tests (needs PyQt5, no Krita)
  test_shading_standalone.py  Engine tests, run from the repository root
```

## Dependencies

- Krita 5.x (bundles PyQt5 and Python 3.13)
- Nothing else. The shading is pure Python; there is no numpy.

## Running the tests

```bash
python3 src/Lumina/test_shading_standalone.py   # from the repo root
python3 src/Lumina/test_shading.py              # needs PyQt5
```

`test_shading_standalone.py` hardcodes repo-relative paths, so it must be run
from the repository root — from inside `src/Lumina/` it fails with a
`FileNotFoundError` on a doubled path.

## Documentation

| File | Description |
|------|-------------|
| [documentation/GettingStarted.md](documentation/GettingStarted.md) | Install, enable, and a walkthrough with screenshots |
| [documentation/ARCHITECTURE.md](documentation/ARCHITECTURE.md) | Module layout, data flow, design decisions |
| [documentation/KritaFlatpak.md](documentation/KritaFlatpak.md) | Deployment — the single source of truth |
| [documentation/TestingGuide.md](documentation/TestingGuide.md) | The test suites and how to run them |
| [documentation/Bugs.md](documentation/Bugs.md) | Bugs hit, with the real crash logs and the fixes |
| [documentation/CommonIssues.md](documentation/CommonIssues.md) | Wider troubleshooting |
| [src/Lumina/MANUAL.md](src/Lumina/MANUAL.md) | The user-facing manual Krita shows under Help → Plugin Help |
