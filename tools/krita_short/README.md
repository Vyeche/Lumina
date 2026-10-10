# The Krita-painted Short

The README's feature video (`images/lumina_short.gif`, from a 1080×1920 MP4) was painted in a running Krita with real brushes, not rendered offscreen.

- `short_krita.py` runs **inside Krita**. It is loaded through the KritaPilot bridge (`run_python`, `exec` of the file), and each `step_*` function is called in its own bridge call so Krita never blocks for long. It:
  - paints a beach still life on its own document, with Krita's brush engine (`Node.paintPath` / `paintEllipse` / `paintPolygon` with the Bristles presets);
  - picks every colour off the live Lumina docker, where a click on the sphere sets Krita's brush colour;
  - saves a frame of the window (Lumina docked on the left, the canvas on the right) after each brush step, with captions and the brush position in `frames/meta.json`.
- `compose_short.py` runs **outside Krita**. It turns those frames into the vertical video:
  - captions on top;
  - the Krita window in the middle;
  - a close-up below, showing Lumina's sphere while picking and the stroke while painting, with the brush cursor drawn in.

The order of steps is `reset`, `step_background`, `step_ball_intro`, `step_ball_paint`, `step_others`, then a final `frame()`.

## Before you run it

The script changes live state. It sets Krita's brush preset, size and colour, and Lumina's targets follow Krita's colour. So before you run it:
- note the preset, size and foreground colour, and restore them afterwards;
- copy `~/.var/app/org.kde.krita/config/Lumina/Lumina.ini` first, and restore Lumina's recent picks from that copy afterwards.

Work files go to `~/Videos/L/Lumina/work/`, outside the repository (Krita's flatpak cannot write to `/tmp`). The scene `.kra` stays there too.

## Making the README GIF from the MP4

```bash
ffmpeg -i lumina_short_krita.mp4 -vf "fps=10,scale=400:-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=full[p];[b][p]paletteuse=dither=sierra2_4a" -loop 0 images/lumina_short.gif
```
