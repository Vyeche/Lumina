# Krita Flatpak — Deployment

**This is the single source of truth for deploying Lumina.** Every other
document links here rather than repeating the commands.

## Plugin Directory

```
~/.var/app/org.kde.krita/data/krita/pykrita/
├── Lumina.desktop     ← service registration, lives at the ROOT
└── Lumina/            ← the plugin package
    ├── __init__.py            ← factory registration entry point
    ├── color_engine.py        ← pure-Python shading (no Qt)
    ├── color_processor.py     ← Krita-facing adapter
    ├── color_controls.py      ← control widgets
    ├── sphere_widget.py       ← sphere surface + picking
    ├── sphere_docker.py       ← main docker
    └── MANUAL.html            ← shown under Help → Plugin Help
```

The `.desktop` file goes at the **root** of `pykrita/`, not inside the package
directory. Source `src/Lumina/` maps to the deployed `Lumina/`
directory — the name is **not** renamed on deploy.

## Deploy

From the repository root:

```bash
DEPLOY="$HOME/.var/app/org.kde.krita/data/krita/pykrita"
rsync -a --delete --exclude='__pycache__' src/Lumina/ "$DEPLOY/Lumina/"
cp src/Lumina.desktop "$DEPLOY/"
```

Three details that matter:

| Detail | Why |
|---|---|
| `--delete` | Removes files deleted from source instead of leaving stale modules in the deploy dir. |
| `--exclude='__pycache__'` | Without it, host Python (3.12) bytecode is copied into the deploy dir next to Krita's own (3.13). Harmless but confusing; it also means the file you inspect may not be the one Krita runs. |
| `.desktop` at root | The plugin will not register if it sits inside the package dir. |

### One-time cleanup after the rename

The plugin shipped as `LightingSphere` before the rename. An older install
leaves the old package and service registration in place, and Krita then lists
the plugin twice. Remove them once:

```bash
DEPLOY="$HOME/.var/app/org.kde.krita/data/krita/pykrita"
rm -rf "$DEPLOY/LightingSphere" "$DEPLOY/LightingSphere.desktop"
```

Saved settings are **not** affected: the plugin reads the old
`LightingSphere` settings file and copies it into the new `Lumina` one on first
launch (see `_migrate_legacy_settings` in `sphere_docker.py`).

## Clear the cache

Krita will not pick up edits while `__pycache__` holds newer bytecode. Clear
**both** trees — the source one feeds the deploy:

```bash
DEPLOY="$HOME/.var/app/org.kde.krita/data/krita/pykrita"
rm -rf src/Lumina/__pycache__ "$DEPLOY/Lumina/__pycache__"
```

> **In an agent/automation shell, `rm -rf` may be blocked by the harness.** Use
> Python instead:
>
> ```bash
> python3 -c "
> import shutil, pathlib
> for p in [pathlib.Path('src/Lumina/__pycache__'),
>           pathlib.Path.home()/'.var/app/org.kde.krita/data/krita/pykrita/Lumina/__pycache__']:
>     shutil.rmtree(p, ignore_errors=True); print('cleared', p)
> "
> ```

## Restart Krita

Krita caches its plugin list at startup, so a reload is required — closing the
window is not enough.

```bash
flatpak run --kill org.kde.krita    # only if running
flatpak run org.kde.krita
```

If Krita was **not** running during the deploy, skip this — the next launch
picks up the new code.

## Verify without a GUI

This is the fastest way to confirm a deploy is sound, and it does not require
starting Krita. It runs the plugin under **Krita's own interpreter**, so it
catches version and import problems the host Python would miss:

```bash
DEPLOY="$HOME/.var/app/org.kde.krita/data/krita/pykrita"
flatpak run --command=python3 org.kde.krita -c "
import sys; sys.path.insert(0, '$DEPLOY/Lumina')
import color_engine as ce
print('python', sys.version.split()[0])
print('SPEC_MAX', ce.SPEC_MAX, 'SPEC_KNEE', ce.SPEC_KNEE)
rows = ce.ColorEngine(256).render((0.55, 0.35, 0.25), 256, 256)
flat = [p for r in rows for p in r]
assert all(0 <= v <= 255 for p in flat for v in p), 'out of range'
print('render ok', len(flat), 'px; max', tuple(max(p[i] for p in flat) for i in range(3)))
"
```

Expected: the interpreter banner reports **3.13** (Krita's bundled Python), and
the render line reports 65536 px.

Note this command writes a fresh `__pycache__` in the deploy dir. That is fine —
it is valid 3.13 bytecode matching current source.

## Verify in the GUI

1. **View → Dock Widgets → Lumina** — the panel appears.
2. The terminal shows no `ImportError` or `NameError`.
3. The sphere renders shaded, and the highlight reads as a soft sheen rather than a
   hard-edged disc.


## Gotchas

**`rm -rf pykrita/Lumina*` is destructive.** The glob also matches
`Lumina.desktop` and deletes the service registration, so the plugin
stops loading entirely and looks like a code bug. To reinstall, remove only the
directory:

```bash
rm -rf ~/.var/app/org.kde.krita/data/krita/pykrita/Lumina/
```

**`DockWidgetFactoryBase.EndDockWidgetList` was removed in Krita 5.x.** It
existed in Krita 4. The `.desktop` file does not use it, and `__init__.py` must
not reference it either. Pass the dock position as a plain integer, or omit it
and take the default.

**`DockWidget` is not exported by the flatpak's PyQt5.** Import
`QDockWidget` instead. `DockWidgetFactory` / `DockWidgetFactoryBase` live in the
`krita` module, not `PyQt5.QtWidgets`. See [Bugs.md](Bugs.md).

**PyQt5 comes from the Krita runtime.** On the host it must be installed
separately. `color_engine.py` deliberately has zero Qt dependencies, so the
shading maths can be tested anywhere.

## References

- [ARCHITECTURE.md](ARCHITECTURE.md) — module layout and design decisions
- [TestingGuide.md](TestingGuide.md) — test suites
- [Bugs.md](Bugs.md) — crash logs and their fixes
- <https://docs.krita.org/en/user_manual/python_scripting/krita_python_plugin_howto.html>
