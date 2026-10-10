# Lumina — Manual

A 3D color sphere for studying how light, shadow and base color interact, and
for picking colors out of your artwork.

## Installing

Download the release `.zip` file. In Krita, go to **Tools → Scripts → Import
Python Plugin from File** and select the `.zip` — import it directly, no need
to extract anything (manual extraction into the plugins folder also works).
Then restart Krita.

Enable it in Krita: **Settings → Configure Krita → Python Plugin Manager**, tick
**Lumina**, then open it via **View → Dock Widgets → Lumina**.

---

## The idea

Real objects are never lit by "white light onto a flat color". A sphere shows you
what happens when a light color, a shadow color and a base color are combined,
so you can choose a palette that reads as a lit object rather than three unrelated
swatches.

Renderer model: Shadow → Base → Light sets the surface tone by light angle,
then specular/highlight, rim and glow are added, then display transforms
(contrast, saturation/Tone, brightness) and tone mapping produce the final color.
The highlight color feeds the specular hotspot only, not the light-side surface.

The core rule of this tool:

> **The sphere's colors change only from the sliders, the three target dots, or
> the eyedropper. Clicking the sphere does not change the sphere.**

---

## The panel, top to bottom

### Header row

| Control | What it does |
|---|---|
| **Gear** (left) | Opens **Settings**: light direction, render quality, sampler toggle, and *Reset all*. Lights up while the popup is open. |
| **Left color block** (*Before*) | The **original** color. Click it to revert the active color back to it. |
| **Right color block** (*Now*) | The **active** color — what you are editing or what you last picked. Click it to commit it as the new original. |
| **Eyedropper** (right) | Activates Krita's **Color Sampler** tool so you can click any pixel on the canvas. Lights up while the sampler is active. |

The two color blocks are a *split color preview*: the left never changes on its
own, and the right always shows the selected target (shade, base or highlight), the same color as the hex box under it. They only diverge
while you are editing.

Under each block sits its **hex value**. Click a hex value to copy it to the
clipboard. The current box shows the **selected target** (shadow, base or
highlight), and the toggle beside it names it — **EDIT SHADE**, **EDIT BASE** or
**EDIT HIGH**. It unlocks typing (green **DONE** while open): type a hex color and
press Enter. For the base, the light and shadow are derived from it, like
eyedropping; a shadow or highlight takes the color exactly as typed.
The previous box is display plus copy only. The
lock state is saved with your settings.

### What changes the sphere's colours

**A target changes** (and with it the sphere) in three ways:

- **Typing a hex** into the box under the active swatch: it edits the selected
  target — the toggle reads **EDIT SHADE**, **EDIT BASE** or **EDIT HIGH**.
- **Krita's colour** — an eyedropper pick or any change in Krita's colour
  selectors replaces the selected target, like a switch.
- **The Hue and Saturation sliders** edit the selected target (Light edits the
  base only).

**After a target changes**, the Hue and Saturation sliders move to the new colour
(and Light, when the base is selected), the hex box shows it, and the target's
dot and the sphere redraw.

When Krita's colour replaces a target, the line under the target dots says so
for a moment (for example *Base ← Krita colour #ab9b7c*). While you are pressing
or dragging on the sphere, Krita's colour changes are Lumina's own picks coming
back and are never taken as a new target.

**Nothing else changes a target.** Clicking or dragging the sphere only sets
Krita's brush colour for painting, whichever target is selected — except a
**Shift-click**, which makes the color under it the selected target's, like typing
its hex. Presets and the Advanced lighting sliders change how the sphere is lit,
never its three colours.

### The sphere

A sphere lit by the three target colors. It grows with the panel, from 200 up
to 340 px. **The color sampler** is a lemon that rests on the sphere under your
pointer (the pointer itself hides while it is there). It lies along the curve,
is filled with the exact color being read, and shows that color's hex in a pill
beneath it; under the target dots it reads as **H / S / L** in the sliders' own units. A click makes it pop;
while you hold the button it stays a little larger, and if you hold on long
enough it starts to sweat (a faint ring fills round it while you wait).

- **Hover** to preview a color.
- **Click or drag** to choose that color **for drawing**. This sets Krita's
  foreground (brush) color so you can paint with it. The sphere itself does not
  change.
- **Shift-click** to make the color the **selected target's** instead (once — a Shift-drag only moves the sampler). A base derives light and shadow around it; a shade or highlight takes it exactly.
- **Arrow keys** nudge the sampler 1 px (Shift: 5 px) for a precise pick; **Enter** or **Space** picks there. Click the sphere first so it has the keyboard.
- Picks register **on the sphere**, and keep going a little past its edge (the **Sticky distance** in Settings, 40 px by default), gliding round the rim. Further out they stop.

### The lamp

Directly above the sphere, four icons choose the lamp model driving the light:

| Icon | Meaning |
|---|---|
| **Haloed bulb** | **Point** — nearby lamp; brightness falls off with distance. |
| **Ring with rays** | **Sun** — distant parallel light; no falloff. |
| **Cone** | **Spot** — cone beam with a soft edge. |
| **Panel** | **Area** — broad panel; soft wrap. |

### Target dots

Below the sphere, three small icons pick which color the Hue and Saturation sliders edit. They share one visual language — a dark-to-bright progression on the same circle:

| Dot | Meaning |
|---|---|
| **Half-dark circle** | **Shadow** — the hue of the darkest areas. |
| **Plain ring** | **Base** — the color of the object. |
| **Ring with rays** | **Light** — the highlight color; also drives how bright the lit side gets. |

Each pair is captioned **Shade**, **Base** or **High**. The selected target shows on its **color swatch** beside the icon, which grows and takes a white rim, and its caption lights up; selecting one sparkles. The icons themselves never change.

The arrows at the two ends of the row **undo** and **redo** changes to the three
target colors (up to 50 steps; a whole slider drag counts as one). Undo never
touches the brush color.

### Sliders

Every slider shows its value at the right end. Clicking a slider's track
jumps the knob to the click (then keep holding to drag); grabbing the knob
itself drags relatively from its current value. Letting go sends a soft ripple
out from the knob, in the colour under it; hold one too long and the knob
starts to sweat. The mouse wheel and arrow keys step a slider quietly, without
either.

#### Typing values

Click a value and type to set it directly. Display is always
canonical-with-unit: percent rows show `86%`, angle rows `323°`, unitless
rows `64`. Typing stays permissive: `86` and `86%` both work on percent
rows, `86`/`86°`/`86deg` on angle rows; anything outside the range is
clamped with a brief amber flash, and non-numeric input is ignored (the
readout snaps back). Press Enter to commit; clicking away reverts
half-typed text.

| Slider | Range | What it does |
|---|---|---|
| **Hue** | 0–359 | Perceptual hue (OKLCH degrees) of the **selected target**, like the reference lighting app's: equal steps look equally different. Fixed full-strength rainbow track: each hue at its most vivid color, like Krita's hue strip. |
| **Saturation** | 0–100 | Vividness of the selected target: 100 is the most vivid color the gamut holds at that hue and lightness. The track re-renders as grey → full color at the current hue. |
| **Light** | 0–100 | Perceived lightness of the **base** target only; hidden for shadow and light, which are derived from the base. |
| **Contrast** | 0–200, default 100 (middle) | Tonal separation around the midtone. Above 100 deepens it, below 100 softens it; pure black and white are always preserved. It does **not** change a color. |
| **Intensity** (primary) | 0–200, default 100 | Scene light level: scales Sun, Point, Spot and Area together. Mirrors the Advanced **Intensity** row — one value, two handles. |

### Recent picks

Under the sliders, the last 10 colors you picked on the sphere, newest first.
**Click** one to make it the brush color again, **double-click** to make it the
selected target's color, **right-click** to **pin** or **remove** it. Pinned
colors sit first with a small white dot and are never pushed out by new picks;
**Clear** empties the rest. Saved with your settings.

### Advanced (collapsed)

Open it for the lighting controls. The panel scrolls when the open section does
not fit, so nothing spills out over the dockers below it:

| Control | What it does |
|---|---|
| **Base Level** | Overall brightness of the base color, preserving hue and saturation. |
| **Intensity** | Brightness of the main light. Normalized relative scale: 100% = reference illuminance 1.0 at light distance 3; 200% = 2.0 (linear). Relative rendering units, not lux/lumens. |
| **Ambient** | Fill light in the shadowed areas; higher values soften the shadow. |
| **Specular** | Width of the highlight. Low is a broad diffuse wash, high a tight bright spot. |
| **Diffuse** | Softness of the highlight's falloff. High spreads it into a wide, gentle sheen; low keeps it a compact bright spot. Pairs with **Specular**: that sets how wide the highlight is, this sets how softly it fades. |
| **Glow** | Bloom on the lit areas. |
| **Tone** | Global saturation of the rendered result. |
| **Rim** | 0–30%, default 10%. Edge accent strength — not a second key light. The glow on the shadow-side edge opposite the light is reflected light, built in and in the surface's own colour. |
| **Rim Tint** | 0–100%, default 60%. Blends the rim from the key color toward sky color. |
| **Sky** | 0–10%, default 2%. Cool bounce from above in the shadows. |
| **Ground** | 0–5%, default 1%. Warm bounce from below in the shadows. |
| **Mixer** | How the light components blend: **Blended**, **Additive** or **Multiplicative**. |

### Presets

One-click looks flanking the sphere, three per side. Each sets the highlight color,
lighting sliders and mixer mode together (never the target colors):

| Button | What it does |
|---|---|
| **Artistic** | Bright white highlights, high contrast. |
| **Real** | Warm highlights, soft natural contrast. |
| **Nocturne** | Low key, cool moonlight, deep shadow. |
| **Gloss** | Tight bright highlight, slick and punchy. |
| **Matte** | Even clay-like falloff, no specular hotspot. |
| **Neon** | Saturated and blooming, coloured light. |

A preset stays lit while the lighting still matches it — picking or editing
colors does not switch it off. **Click it again to turn it off**: the lighting
you had before switching it on comes back (hopping between presets keeps that
original). Moving a lighting slider makes your edit the new starting point. The
lit preset, and what it restores, are remembered across restarts.

---

## Picking a color from your artwork

Press the **eyedropper**, then click any pixel on the canvas. What happens next
depends on which **target dot** is selected:

- **Base selected** — the sampled color becomes the base, and the light and
  shadow are derived around it (below) so the three always blend.
- **Shadow or light selected** — the sampled color replaces *only that target*,
  exactly as picked. The other two are left alone, so you can sample a real
  highlight and a real shadow straight off a reference.

When the base is selected, the derivation is:

- **Light** — a fixed step **lighter** than the base, with its hue swung toward a
  **warm key light** (about 37°, a peach-orange): blues turn magenta-lilac, greens
  turn cream, reds turn peach.
- **Shadow** — a fixed step **darker** than the base, leaning toward a **cool
  ambient** (about 260°): greens go teal, magentas go violet. Reds, oranges and
  yellows keep **warm shadows**, and blues are already there.

The steps are measured in *perceived* lightness (OKLCH), so a bright pick and a
dark one move by the same visible amount — the old rule scaled brightness, so
bright picks barely moved and dark ones collapsed. The model was fitted to a
reference lighting app's own shadow and light for several bases, and is checked
against them by `tools/reference_calibration.py`.

A grey derives greys, black derives black, and a very dark pick still gets a
shadow you can tell apart from it. Colors that would fall outside the RGB range
lose chroma rather than shifting hue.

Your tool is returned to whatever you were using, and the eyedropper stays armed,
so you can keep sampling. This also works if you pick with Krita's own Color
Sampler, the palette, or the Color Selector docker.

### Colors match Krita's, on every layer

Whatever sets Krita's color — the eyedropper, the Specific or Advanced Color
Selector, a palette — Lumina takes **exactly that color** and never changes
Krita's color back.

Lumina shows numbers the same way Krita's **Specific Color Selector** does: in
the **active layer's color space**. That matters for imported photos and
screenshots, which often keep their own profile (iPhone and Mac screenshots are
*Display P3*). The same blue reads `#0102bd` on an sRGB layer and `#0102b5` on a
Display P3 layer — in Krita's selector *and* in Lumina. Switching layers changes
the numbers, never the colors, and is not treated as a new pick.

The sphere and swatches are always drawn in the right colors on screen, whatever
the layer.

> If the numbers in Krita's selector and Lumina ever disagree, check the
> selector's **Lock to current layer colorspace** button (on by default). With it
> off, the selector shows a space of its own choosing instead of the layer's.

---

## Settings (gear)

| Setting | What it does |
|---|---|
| **Light azimuth** | Rotates the main light around the sphere, 0–359° (default 304°, where the reference lighting app puts its light). |
| **Light height** | Raises and lowers the light, 0–90° (default 41°). |
| **Highlight size** | Highlight size on the sphere, 0–100 (default 99, the broad sheen the reference lighting app shows). Up means a bigger, softer highlight. Drives the same sharpness as the Advanced **Specular** row, so the two stay in sync. |
| **Quality** | Render grid size. *Low* is smoothest while dragging, *High* is finest. |
| **Show color cursor** | Show or hide the lemon sampler that rests under the pointer on the sphere. It marks the exact pixel being read and previews that color; it does not magnify. With it off the normal pointer stays. |
| **Sticky distance** | How far past the sphere's edge picking keeps hold, 0–100 px (default 40). |
| **Compact panel** | Hides the captions (presets, lamps, targets, Before / Now) and Recent picks; the icons and their tooltips stay. |
| **Use base color as brush** | Sends the **base color** to Krita's foreground. Use it after editing with the sliders, since slider edits do not touch the brush. |
| **Reset all** | Restores every color and lighting value to its default, and saves that reset. |
| **Save settings** | Writes your current setup to disk. Changes already save as you make them, so this is a checkpoint — it confirms the write rather than being the only chance to keep your work. |

### Defaults

On first launch (and on *Reset all*) the lamp is **Sun**, and the shadow and
light targets are harmonized from the base color — slightly darker/cooler and
lighter/warmer versions of it — instead of three fixed swatches. Saved settings
always win over these defaults.

### Saved settings

Your setup is written to a plain-text file and restored automatically the next
time Krita opens: the three target colors, the lamp model, the light direction, highlight
size, quality, sampler visibility, and every slider (contrast, intensity,
ambient, specular, diffuse, glow, tone, base level and the mixer mode). Nothing is lost if Krita
crashes, because each change is saved as you make it.

Two things are deliberately *not* saved: the colour you last picked for
painting (that is a per-session brush choice) and which target the sliders are
currently editing. **Reset all** clears the saved file's contents too, so the
next launch starts clean.

---

## Tips

- **Shadows are rarely black.** Give the shadow dot a real color — a cool
  bounce from the environment — rather than letting it go to black.
- **Change the base freely.** The base color is independent of the light and
  shadow, so you can restyle an object without disturbing its lighting.
- **Use the left block to A/B.** Pick a color, edit it, click the left block to
  go back to where you started, then click the right block to keep the new one.
- **Drag the Advanced section open** when you want the physical controls, and
  leave it closed while you are choosing colors.
- **Drop Quality to Low** on large documents if adjusting a slider feels laggy.
