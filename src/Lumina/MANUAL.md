# Lumina — Manual

A 3D color sphere for studying how light, shadow and base color interact, and
for picking colors out of your artwork.

Enable it in Krita: **Settings → Configure Krita → Python Plugin Manager**, tick
**Lumina**, then open it via **View → Dock Widgets → Lumina**.

---

## The idea

Real objects are never lit by "white light onto a flat color". A sphere shows you
what happens when a light color, a shadow color and a base color are combined,
so you can choose a palette that reads as a lit object rather than three unrelated
swatches.

The core rule of this tool:

> **The sphere's colors change only from the sliders, the three target dots, or
> the eyedropper. Clicking the sphere does not change the sphere.**

---

## The panel, top to bottom

### Header row

| Control | What it does |
|---|---|
| **Gear** (left) | Opens **Settings**: light direction, render quality, sampler toggle, and *Reset all*. |
| **Left color block** | The **original** color. Click it to revert the active color back to it. |
| **Right color block** | The **active** color — what you are editing or what you last picked. Click it to commit it as the new original. |
| **Eyedropper** (right) | Activates Krita's **Color Sampler** tool so you can click any pixel on the canvas. |

The two color blocks are a *split color preview*: the left never changes on its
own, and the right always shows the color currently in play. They only diverge
while you are editing.

### The orb

A sphere lit by the three target colors. **Drag the color sampler** — the small
ring that follows your pointer — to read the color under it; its centre always
shows the exact color being sampled, and the RGB value appears underneath.

- **Hover** to preview a color.
- **Click or drag** to choose that color **for drawing**. This sets Krita's
  foreground (brush) color so you can paint with it. The sphere itself does not
  change.
- Picks only register **on the sphere** — the corners outside the disc are ignored.

### Target dots

Three small icons pick which color the Hue and Saturation sliders edit:

| Dot | Meaning |
|---|---|
| **Crescent moon** | **Shadow** — the hue of the darkest areas. |
| **Droplet** | **Base** — the color of the object. |
| **Sun** | **Light** — the highlight color; also drives how bright the lit side gets. |

The selected dot has a light ring around it.

### Sliders

| Slider | Range | What it does |
|---|---|---|
| **Hue** | 0–359 | Changes the hue of the **selected target**. Fixed rainbow track. |
| **Saturation** | 0–100 | Changes the saturation of the selected target. The track re-renders as grey → full color at the current hue. |
| **Contrast** | 0–200, default 100 (middle) | Controls how sharply the light falls off into shadow on the orb. 100 is neutral -- below or above that, tone mapping reshapes the falloff. It does **not** change a color. |

### Advanced (collapsed)

Open it for the lighting controls. The panel scrolls when the open section does
not fit, so nothing spills out over the dockers below it:

| Control | What it does |
|---|---|
| **Base Level** | Overall brightness of the base color, preserving hue and saturation. |
| **Intensity** | Brightness of the main light. |
| **Ambient** | Fill light in the shadowed areas; higher values soften the shadow. |
| **Specular** | Width of the highlight. Low is a broad diffuse wash, high a tight bright spot. |
| **Diffuse** | Softness of the highlight's falloff. High spreads it into a wide, gentle sheen; low keeps it a compact bright spot. Pairs with **Specular**: that sets how wide the highlight is, this sets how softly it fades. |
| **Glow** | Bloom on the lit areas. |
| **Tone** | Global saturation of the rendered result. |
| **Mixer** | How the light components blend: **Blended**, **Additive** or **Multiplicative**. |

### Presets

One-click looks, above the Advanced section. Each sets the highlight color,
lighting sliders and mixer mode together:

| Button | What it does |
|---|---|
| **Artistic** | Bright white highlights, high contrast. |
| **Real** | Warm highlights, soft natural contrast. |
| **Nocturne** | Low key, cool moonlight, deep shadow. |
| **Gloss** | Tight bright highlight, slick and punchy. |
| **Matte** | Even clay-like falloff, no specular hotspot. |
| **Neon** | Saturated and blooming, coloured light. |

---

## Picking a color from your artwork

Press the **eyedropper**, then click any pixel on the canvas. The sampled color
becomes the **base**, and the rest of the lighting is derived around it so the
three always blend:

- **Light** — the base lifted **45% of the way toward white**, less saturated, and
  its hue moved slightly **toward the key light's hue** (a warm 29°).
- **Shadow** — the base taken down to **42% of its value**, slightly *muted*, and
  its hue moved slightly **toward the ambient's hue** — the complement of the key
  light, a cool 209°.

This is the standard hue-shifting rule: lit surfaces take the hue of the light,
shadowed ones the hue of the ambient fill. Both are expressed as a *target hue*
rather than a fixed offset, because "warmer" has no single direction around the
color wheel — from a blue base, warmer is a *decreasing* hue, while from a red
base it is an *increasing* one. A fixed sign therefore inverts the pair for
everything between cyan and magenta, giving a cool highlight and a warm shadow.
Targeting the light and its complement keeps the direction right from anywhere
on the wheel. Both shifts are small and scale with the base's saturation, because
a large shift is not just a bigger version of a small one: a base sitting near the
ambient's antipode sends even a modest shift clean across the wheel, which turned
an orange into an olive-green shadow. Real shadows mostly darken and desaturate
rather than rotate in hue.

Value and saturation shifts are proportional to the headroom for the same reason:
a flat "+20% value" clipped outright on any color already brighter than 80%, and a
flat "−30%" drove dark colors to black.

Your tool is returned to whatever you were using, and the eyedropper stays armed,
so you can keep sampling. This also works if you pick with Krita's own Color
Sampler, the palette, or the Color Selector docker.

---

## Settings (gear)

| Setting | What it does |
|---|---|
| **Light azimuth** | Rotates the main light around the sphere, 0–359°. |
| **Light height** | Raises and lowers the light, 0–90°. |
| **Light azimuth size** | Highlight size on the orb, 0–100. Up means a bigger, softer highlight. Drives the same sharpness as the Advanced **Specular** row, so the two stay in sync. |
| **Quality** | Render grid size. *Low* is smoothest while dragging, *High* is finest. |
| **Show color cursor** | Show or hide the ring that follows the pointer over the orb. It marks the exact pixel being read and previews that color; it does not magnify. |
| **Use base color as brush** | Sends the **base color** to Krita's foreground. Use it after editing with the sliders, since slider edits do not touch the brush. |
| **Reset all** | Restores every color and lighting value to its default, and saves that reset. |
| **Save settings** | Writes your current setup to disk. Changes already save as you make them, so this is a checkpoint — it confirms the write rather than being the only chance to keep your work. |

### Saved settings

Your setup is written to a plain-text file and restored automatically the next
time Krita opens: the three target colors, the light direction, highlight
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
