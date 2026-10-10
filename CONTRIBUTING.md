# Contributing to Lumina

Thanks for stopping by. Lumina is a Krita plugin (Python + PyQt5): an
interactive 3D color sphere used as a lighting reference while painting.

## Setup

No build step and no third-party dependencies — just Python 3 and PyQt5
(for the widget tests; the logic and engine suites are stdlib-only).

```bash
git clone https://github.com/Vyeche/Lumina.git
cd Lumina
python3 -m pytest src/Lumina/ -q
```

All 78 tests should pass: the stdlib suite (parser, engine, calibration)
plus the offscreen-Qt suite (readouts, sliders, docker state machine).

## Trying changes in Krita (flatpak)

Automated tests don't cover running inside Krita. To test there:

1. Copy `src/Lumina/*` to
   `~/.var/app/org.kde.krita/data/krita/pykrita/Lumina/`
2. Delete `__pycache__` in that directory.
3. **Fully restart Krita** (it caches the plugin list; reloading is not enough).

Runtime diagnostics land in `lumina_log.txt` next to the plugin —
useful, but never commit it.

## Conventions

- **Keep pure logic importable without Qt.** Parsing (`typed_entry.py`),
  the engine (`color_engine.py`), and calibration stay stdlib-only with
  stdlib tests. Qt lives in the docker, widget, and control layers.
- **`typed_entry.py` is ASCII-only.** The degree mark must stay escaped
  (`"\u00b0"`); some toolchains truncate the file otherwise.
- **Every fix ships with a regression test.** Offscreen Qt is fine
  (`QT_QPA_PLATFORM=offscreen`); see `test_ui_defaults.py` for the pattern.
- **Log through the `Lumina` logger** (`logging.getLogger("Lumina")`),
  which owns the file handler. Never call `logging.basicConfig` — it is a
  no-op under flatpak and silently eats the log.
- **`MANUAL.md` and `MANUAL.html` are edited together.** There is no
  generator; keep both copies in sync.
- **Release zips** contain `Lumina/` + `Lumina.desktop` from `src/`,
  excluding logs and caches (see the `v2.4.0`/`v2.5.0` assets for the layout).

## Pull requests

Small, focused PRs against `main`, with tests for behavior changes and a
note in the PR description about how it was verified (suite + in-Krita if
it touches the UI). Issues are equally welcome — a screenshot plus the
relevant `lumina_log.txt` lines is the fastest way to a fix.
