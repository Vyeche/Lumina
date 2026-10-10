#!/usr/bin/env python3
"""Render benchmark (issue #40 §5): reproducible stage timings + image diff.

Renders a fixed scene (engine defaults, Sun 287/45, fixed base) at 200x200:
one cold run on fresh instances, then N warm trials on the same instances.
Reports per-stage median/p95/max and the image diff against the first warm
trial (perf-only changes must be pixel-identical).

Usage:
    python3 tools/benchmark_render.py            # human-readable table
    python3 tools/benchmark_render.py --json     # machine-readable record
    python3 tools/benchmark_render.py --trials 10 --json
"""

import json
import os
import platform
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "Lumina"))

from color_engine import ColorEngine
from color_processor import SphereColorProcessor

SIZE = 200
BASE = (205 / 255, 92 / 255, 92 / 255)
TRIALS = 30
STAGES = ("geometry", "light", "material", "display", "smooth", "srgb",
          "total", "buffer", "qimage")


def _stage_sample(engine, processor, name):
    if name == "buffer":
        return processor.last_ms["buffer"]
    if name == "qimage":
        return processor.last_ms["qimage"]
    samples = engine._timings.get(name)
    return samples[-1] if samples else 0.0


def _run_trial(processor, base, size):
    engine = processor.engine
    started = time.perf_counter()
    image = processor.render_image(base, size, size)
    wall_ms = (time.perf_counter() - started) * 1000.0
    raw = _to_bytes(image, size)
    stages = {name: _stage_sample(engine, processor, name) for name in STAGES}
    stages["wall"] = wall_ms
    return raw, stages


def _to_bytes(image, size):
    try:
        return bytes(image)
    except TypeError:
        pass
    # QImage return path (Qt installed): read back the packed bytes.
    from PyQt5.QtGui import QImage as _QImage  # local: tool runs without Qt
    assert isinstance(image, _QImage)
    ptr = image.bits()
    ptr.setsize(image.byteCount())
    data = bytes(ptr)
    assert len(data) == size * size * 4, len(data)
    return data


def _dist(values):
    ordered = sorted(values)
    n = len(ordered)
    p95 = ordered[min(n - 1, int(n * 0.95))]
    return {"median": round(statistics.median(ordered), 2),
            "p95": round(p95, 2),
            "max": round(ordered[-1], 2),
            "n": n}


def _image_diff(first, other):
    diffs = [abs(a - b) for a, b in zip(first, other)]
    return {"mean_abs": round(sum(diffs) / len(diffs), 4),
            "max_abs": max(diffs),
            "nonzero": sum(1 for d in diffs if d)}


def measure(trials=TRIALS):
    cold_proc = SphereColorProcessor(resolution=SIZE)
    cold_raw, cold_stages = _run_trial(cold_proc, BASE, SIZE)

    proc = SphereColorProcessor(resolution=SIZE)
    raws, per_stage = [], {name: [] for name in STAGES + ("wall",)}
    for _ in range(trials):
        raw, stages = _run_trial(proc, BASE, SIZE)
        raws.append(raw)
        for name, ms in stages.items():
            per_stage[name].append(ms)

    diffs = [_image_diff(raws[0], raw) for raw in raws[1:]]
    return {
        "settings": {
            "base": [round(c, 4) for c in BASE],
            "size": SIZE,
            "light": "Sun 287/45 intensity 1.0 (engine defaults)",
            "smooth": proc.engine.smooth,
            "algorithm_version": ColorEngine.RENDER_ALGORITHM_VERSION,
        },
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "trials": trials,
        "cold_ms": {name: round(ms, 2) for name, ms in cold_stages.items()},
        "warm_ms": {name: _dist(per_stage[name]) for name in per_stage},
        "image_diff_vs_first": {
            "mean_abs_max": max(d["mean_abs"] for d in diffs),
            "max_abs": max(d["max_abs"] for d in diffs),
            "nonzero_max": max(d["nonzero"] for d in diffs),
        },
    }


def main():
    trials = TRIALS
    args = sys.argv[1:]
    if "--trials" in args:
        trials = max(1, int(args[args.index("--trials") + 1]))
    results = measure(trials)
    if "--json" in args:
        print(json.dumps(results, indent=1))
        return
    print("cold: " + " ".join("%s=%.1f" % (k, v)
                              for k, v in results["cold_ms"].items()))
    print("%-9s %8s %8s %8s" % ("stage", "median", "p95", "max"))
    for name, d in results["warm_ms"].items():
        print("%-9s %8.2f %8.2f %8.2f" % (name, d["median"], d["p95"], d["max"]))
    print("image diff vs first: %s" % (results["image_diff_vs_first"],))


if __name__ == "__main__":
    main()
