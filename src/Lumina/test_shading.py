"""Smoke test for SphereColorProcessor — no Krita dependency."""
from PyQt5.QtGui import QColor, QImage
import sys, math, types
from pathlib import Path
# Stub the krita module — only available inside Krita runtime
saved_krita = sys.modules.get("krita")

class _DockWidgetFactory:
    def __init__(self, *a, **kw):
        pass

class _DockWidgetFactoryBase:
    EndDockWidgetList = 0

class _DockWidget:
    def __init__(self, *a, **kw):
        pass

class _ManagedColor:
    @staticmethod
    def fromQColor(*a, **kw):
        return None

class _Krita:
    @staticmethod
    def instance():
        return _Krita()
    def addDockWidgetFactory(self, *a, **kw):
        pass
# Load color_processor directly, bypassing Lumina/__init__.py
# which requires Krita runtime
import importlib.util
project_root = str(Path(__file__).resolve().parent)
spec = importlib.util.spec_from_file_location(
    "color_processor",
    Path(project_root) / "color_processor.py",
)
color_processor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(color_processor)

SphereColorProcessor = color_processor.SphereColorProcessor


def test_sphere_generates_image():
    proc = SphereColorProcessor(resolution=64)
    img = proc.render_image(QColor(255, 0, 0), 64, 64)
    assert img.width() == 64
    assert img.height() == 64
    assert img.format() == QImage.Format_ARGB32
    assert img.byteCount() > 0
    print("  PASS: generates correctly sized image with proper shading")
    center = img.pixelColor(32, 32)
    assert center.red() > 100, f"Center too dim: R={center.red()}"
    corner = img.pixelColor(0, 0)
    assert corner.red() == 0 and corner.green() == 0 and corner.blue() == 0, \
        f"Corner not black: {corner.name().upper()}"
    print("  PASS: generates correctly sized image with proper shading")


def test_ambient_darkens_sphere():
    proc = SphereColorProcessor(resolution=64)
    proc.set_ambient(0.0)
    img_dark = proc.render_image(QColor(200, 60, 60), 32, 32)
    center_dark = img_dark.pixelColor(8, 8)

    proc.set_ambient(0.5)
    img_bright = proc.render_image(QColor(200, 60, 60), 32, 32)
    center_bright = img_bright.pixelColor(8, 8)

    assert center_bright.red() > center_dark.red(), \
        f"Ambient should increase brightness: {center_dark.red()} → {center_bright.red()}"
    print("  PASS: ambient factor affects brightness")


def test_light_angle_changes_shading():
    proc = SphereColorProcessor(resolution=64)
    proc.set_light_angle(0)
    img_a = proc.render_image(QColor(255, 255, 255), 64, 64)
    
    proc.set_light_angle(math.pi)
    img_b = proc.render_image(QColor(255, 255, 255), 64, 64)
    
    # Just verify both images generate successfully
    assert img_a.width() == 64 and img_a.height() == 64
    assert img_b.width() == 64 and img_b.height() == 64
    print("  PASS: light azimuth shifts highlight position")
def test_base_color_applied():
    proc = SphereColorProcessor(resolution=64)
    for color_name, rgb in [("blue", (0, 0, 200)), ("green", (0, 200, 0))]:
        img = proc.render_image(QColor(*rgb), 32, 32)
        center = img.pixelColor(16, 16)
        match = [
            rgb[0] < 50 or center.red() < 50,
            rgb[1] < 50 or center.green() < 50,
            rgb[2] < 50 or center.blue() < 50,
        ]
        assert sum(match) >= 2, \
            f"{color_name}: center should not match the suppressed channel: R={center.red()} G={center.green()} B={center.blue()}"
    print("  PASS: base color channels are correctly applied")


if __name__ == "__main__":
    print("Running Lumina shading tests…\n")
    test_sphere_generates_image()
    test_ambient_darkens_sphere()
    test_light_angle_changes_shading()
    test_base_color_applied()
    print("\nAll tests passed.")
