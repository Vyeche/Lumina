# Testing Procedures for Lumina Plugin

This document outlines the complete testing strategy for the Lumina Krita plugin.

## Table of Contents
1. [The current suite](#the-current-suite)
2. [Standalone Tests](#standalone-tests)
2. [Krita Integration Tests](#krita-integration-tests)
3. [Test Automation](#test-automation)
4. [Debugging Checklist](#debugging-checklist)
5. [Version Compatibility](#version-compatibility)

---

## The current suite

Run everything from the repository root:

```bash
QT_QPA_PLATFORM=offscreen python3 -m pytest -q src/Lumina      # 317 tests, ~25 s
python3 src/Lumina/test_shading_standalone.py                  # engine-only smoke test
```

| File | What it holds |
|---|---|
| `test_release_calibration.py` | Shading contract, stdlib only:<br>• tonal roles, contrast, the #45 shade and rim fit<br>• the four lamp models (#53) and form zones<br>• the fast pipeline: bit-identical to `render_reference()` at an even and an odd size (`test_fast_pipeline_is_bit_identical_to_the_reference`), and at least 2× faster on a colour change on the same machine (#56) |
| `test_ui_defaults.py` | The panel, headless:<br>• layout (a centred sphere, aligned sliders, the Shade / Base / Light row, the readout under the sphere)<br>• presets, undo / redo, background renders and cancellation, wheel bursts<br>• settings round-trips and the v6 migration (#57), including a real 2.6.0-style file, a 2.7.0 file that must not migrate, and a whole old preset<br>• the canvas watch searching the central area only |
| `test_full_cache.py` | The full-quality output cache: keys, hits and misses. |
| `test_diagnostic_*.py` | The diagnostic engines, which run through `render_reference()`. |

Tools that regenerate artefacts:

- `tools/render_docs_images.py`: the docs screenshots, `Lumina_lamps.png`, `lumina_demo.gif` and `lumina_zones.gif`.
- `tools/render_perf_chart.py`: measures the old and new pipelines and draws `images/render_speed.png`.
- `tools/render_short.py`: the headless Short. `--docs-gif` also cuts `images/lumina_paint_zones.gif`.
- `tools/krita_short/`: the Krita-painted Short behind the README's feature video (`images/lumina_short.gif`). It runs inside Krita through KritaPilot; see its README.

For engine changes that must not alter output, capture renders before the change and compare bytes after. That is how #56 was held bit-identical across 72 renders: all four lamps, the mixer modes, glow, grain, the blur settings, and 200 / 288 px.

## Standalone Tests

### Purpose
Verify shading engine logic without requiring Krita runtime. This allows development and testing on any system.

### Test File Location
```
src/Lumina/test_shading.py
```

### Running Tests

```bash
# Direct execution
cd /path/to/Lumina
python3 src/Lumina/test_shading.py

# With pytest
python3 -m pytest src/Lumina/test_shading.py -v

# With coverage
python3 -m pytest src/Lumina/test_shading.py -v --cov=src/Lumina/color_processor
```

### Test Coverage

| Test Function | Purpose | Expected Result |
|--------------|---------|-----------------|
| `test_sphere_generates_image()` | Image generation | Returns 64x64 QImage with proper format |
| `test_ambient_darkens_sphere()` | Ambient lighting | Lower ambient = darker sphere |
| `test_light_angle_changes_shading()` | Light angle | Different angles = different highlight position |
| `test_base_color_applied()` | Base color | Sphere colors match base color channels |

### Test Assertions

```python
def test_sphere_generates_image():
    proc = SphereColorProcessor(resolution=64)
    img = proc.generate_sphere_image(QColor(200, 60, 60), 64, 64)
    
    # Check image format
    assert img.format() == QImage.Format_ARGB32
    # Check dimensions
    assert img.width() == 64
    assert img.height() == 64
    # Check pixel data exists
    assert img.byteCount() > 0

def test_light_angle_changes_shading():
    proc1 = SphereColorProcessor(resolution=64)
    proc1.set_light_angle(0)  # Light from left
    img1 = proc1.generate_sphere_image(QColor(200, 60, 60), 64, 64)
    
    proc2 = SphereColorProcessor(resolution=64)
    proc2.set_light_angle(90)  # Light from top
    img2 = proc2.generate_sphere_image(QColor(200, 60, 60), 64, 64)
    
    # Images should be different
    assert img1 != img2
```

---

## Krita Integration Tests

### Purpose
Verify complete plugin functionality with Krita runtime.

### Prerequisites
- Krita 5.x with Python bindings
- PyQt5 installed
- Plugin deployed to `~/.var/app/org.kde.krita/data/krita/pykrita/`

### Installation Steps

See **[KritaFlatpak.md](KritaFlatpak.md)** — it is the single source of truth for
deploying. In short: `rsync` the package, copy the `.desktop` to the pykrita
**root**, clear both `__pycache__` trees, then restart Krita.

It also documents a headless verify step that runs the plugin under Krita's own
Python 3.13, which catches import and version problems before you launch a GUI.

### Test Checklist

**Required Verification** (must all pass):

| # | Test | Expected Result | How to Verify |
|---|------|-----------------|---------------|
| 1 | Factory Registration | No errors in terminal | Check terminal for "Factory registered successfully" |
| 2 | Docker Visibility | Panel appears in dock | **View → Dock Widgets → Lumina** |
| 3 | Sphere Display | Full circular sphere visible | Sphere shows as complete circle, not clipped |
| 4 | Color Variation | Colors vary from dark to bright | Sphere shows gradient from shaded (dark) to lit (bright) |
| 5 | Base Color | Red (200, 60, 60) by default | Default sphere is reddish hue |
| 6 | Color Picking | Drag displays color values | Mouse drag shows RGB values at bottom |
| 7 | Light Slider | Sphere brightness changes | Light angle slider updates shading |
| 8 | Ambient Slider | Overall brightness changes | Ambient slider adjusts sphere darkness |
| 9 | Buttons Responsive | No errors on clicks | All buttons respond without errors |
| 10 | No Terminal Errors | Clean terminal output | No `TypeError` or `AttributeError` messages |

### Testing Procedure

**Step 1: Launch Krita**
```bash
flatpak run org.kde.krita
```
Keep terminal open to view logs.

**Step 2: Access Plugin Panel**
- Go to **View → Dock Widgets**
- Find "Lumina" in the list
- Click to show the panel

**Step 3: Verify Sphere Display**
- Sphere should appear as full circle
- Check for color gradients (dark to bright)
- Default should be reddish (200, 60, 60)

**Step 4: Test Color Picking**
- Click and drag on sphere surface
- Watch for RGB values at bottom right
- Values should change during drag

**Step 5: Test Controls**
- Drag **Light Angle** slider (0-180)
- Drag **Ambient** slider (0-100)
- Drag **Intensity** slider (0-200)
- All should update sphere appearance

**Step 6: Test Buttons**
- Click "Pick from Document" button
- Click "Apply to Selection" button
- No errors should appear

**Step 7: Monitor Terminal**
```bash
# Terminal should show logs like:
Lumina: create_docker() called
Lumina: create_docker() returned docker: ..., visible=True
Lumina: Factory registered successfully
```

### Duration
Run Krita for **30 seconds minimum** to allow user to open an image and fully test the panel.

---

## Test Automation

### Automated Test Script

Create `test_plugin.py` for automated testing:

```python
#!/usr/bin/env python3
"""Automated test script for Lumina plugin."""
import sys
import time
from pathlib import Path

def test_plugin_installation():
    """Verify plugin files are correctly installed."""
    plugin_root = Path.home() / ".var/app/org.krita/data/krita/pykrita"
    plugin_dir = plugin_root / "Lumina"
    
    required_files = [
        "__init__.py",
        "sphere_widget.py",
        "color_processor.py",
        "test_shading.py",
    ]
    
    print("Checking plugin files...")
    for filename in required_files:
        filepath = plugin_dir / filename
        if not filepath.exists():
            print(f"❌ Missing: {filename}")
            return False
        print(f"✅ Found: {filename}")
    
    # Check desktop file at root
    desktop_file = plugin_root / "Lumina.desktop"
    if desktop_file.exists():
        print(f"✅ Found: Lumina.desktop")
    else:
        print(f"❌ Missing: Lumina.desktop")
        return False
    
    return True

def test_standalone_tests():
    """Run standalone shading tests."""
    test_file = Path("src/Lumina/test_shading.py")
    
    if not test_file.exists():
        print("❌ Test file not found")
        return False
    
    print("Running standalone tests...")
    result = os.system("python3 " + str(test_file))
    return result == 0

def main():
    """Run all tests."""
    print("=" * 50)
    print("Lumina Plugin Test Suite")
    print("=" * 50)
    
    print("\n[1/3] Checking installation...")
    if not test_plugin_installation():
        print("\n❌ Installation check failed")
        return 1
    print("\n[1/3] ✅ Installation OK")
    
    print("\n[2/3] Running standalone tests...")
    if not test_standalone_tests():
        print("\n❌ Standalone tests failed")
        return 1
    print("\n[2/3] ✅ Standalone tests OK")
    
    print("\n[3/3] Krita integration tests...")
    print("Manual testing required:")
    print("  1. Run: flatpak run org.kde.krita")
    print("  2. View → Dock Widgets → Lumina")
    print("  3. Verify all features work")
    print("  4. Monitor terminal for errors")
    
    print("\n" + "=" * 50)
    print("✅ All automated tests passed!")
    print("⚠️  Manual Krita integration testing still required")
    print("=" * 50)
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

### Running Automated Tests

```bash
# Install pytest
pip3 install pytest pytest-cov

# Run all tests
python3 test_plugin.py

# With pytest
python3 -m pytest test_plugin.py -v
```

---

## Debugging Checklist

### Factory Registration Issues

**Problem**: "Could not create docker for 'color_sphere_factory'"

**Debug Steps**:
1. Check terminal for full error traceback
2. Verify `__init__.py` is being imported
3. Check `create_docker()` returns valid `DockWidget`
4. Verify `setWidget(mainWidget)` is called
5. Check widget hierarchy is correct

**Common Errors**:
```
TypeError: __init__() takes 1 positional argument but 2 were given
→ Fix: Add `parent=None` parameter to widget constructors

AttributeError: 'SphereDocker' object has no attribute 'base_btn'
→ Fix: Check attribute initialization order in __init__
```

### Widget Display Issues

**Problem**: Sphere not visible or clipped

**Debug Steps**:
1. Check widget size: `print(f"Size: {widget.size()}")`
2. Check geometry: `print(f"Geometry: {widget.geometry()}")`
3. Verify `setFixedSize()` or explicit size set
4. Check parent widget layout
5. Verify `update()` called after drawing

**Common Errors**:
```
Sphere image outside widget bounds
→ Fix: Check offset calculations in paintEvent

Widget not painting
→ Fix: Ensure setFixedSize() called before paintEvent
```

### Color Picking Issues

**Problem**: No color values displayed on drag

**Debug Steps**:
1. Check mouse tracking enabled: `setMouseTracking(True)`
2. Verify `_to_image_coords()` returns valid coordinates
3. Check `pixelColor()` call returns QColor
4. Verify hover color stored: `self._docker._hover_color = pixel`
5. Check button style update: `self._docker._update_button_style()`

**Common Errors**:
```
_sx, _sy are None
→ Fix: Check bounds calculation and QRectF containment test

pixelColor returns None
→ Fix: Verify image is loaded and coordinates are valid
```

### Krita Integration Issues

**Problem**: Plugin doesn't appear in dock

**Debug Steps**:
1. Verify `.desktop` file at correct location (pykrita root)
2. Check plugin name matches `X-KDE-Library`
3. Run `flatpak run --kill org.kde.krita` before restart
4. Check **Settings → Configure Krita → Python Plugin Manager**
5. Enable plugin if disabled

**Common Errors**:
```
Plugin won't appear after install
→ Fix: Use `flatpak run --kill` to force restart

Cannot find window to open popup
→ Fix: Add missing setWidget(mainWidget) call
```

### Memory and Performance

**Problem**: Slow rendering or memory issues

**Debug Steps**:
1. Check resolution: Lower resolution (64-128) for testing
2. Verify pre-computation happens in `__init__`
3. Check if grids are cached (not recomputed on every render)
4. Monitor memory with `top` or `htop`

---

## Version Compatibility

### Tested Versions
- ✅ Krita 5.3.3 (Flatpak)
- ✅ Krita 6.0+ (Desktop version)

### Minimum Requirements
- Krita 5.x with Python bindings
- PyQt5 5.x (bundled with Krita)
- Python 3.8+

### Breaking Changes by Version

| Feature | Krita 5.x | Krita 6.0+ | Notes |
|---------|-----------|------------|-------|
| `DockWidgetFactoryBase.EndDockWidgetList` | ❌ Removed | ❌ Removed | DO NOT USE |
| `ManagedColor` API | ✅ Supported | ✅ Enhanced | Use in both |
| `activeView()` | ✅ Supported | ✅ Enhanced | Available in both |
| `setWidget()` | ✅ Required | ✅ Required | Critical pattern |

### Version-Specific Features

**Krita 6.0+ Only**:
- HDR exposure/gamma controls
- Advanced blending modes
- Enhanced color management

**Compatible with All Versions**:
- Basic color picking
- Phong shading model
- Dock widget creation
- Mouse event handling

---

## Quick Reference Commands

### Installation
```bash
cp -r src/Lumina/* ~/.var/app/org.kde.krita/data/krita/pykrita/
rm -rf ~/.var/app/org.kde.krita/data/krita/pykrita/Lumina/__pycache__
flatpak run --kill org.kde.krita
flatpak run org.kde.krita
```

### Running Tests
```bash
# Standalone (no Krita)
python3 src/Lumina/test_shading.py

# With pytest
python3 -m pytest src/Lumina/test_shading.py -v

# Automated script
python3 test_plugin.py
```

### Debugging
```bash
# Run Krita with verbose logging
flatpak run org.kde.krita

# Monitor logs in terminal
# Keep terminal open to view Lumina logs
```

### Plugin Manager Check
```bash
# In Krita:
Settings → Configure Krita → Python Plugin Manager
# Verify "Lumina" is enabled
```

---

## Appendix: Test Result Templates

### Successful Test Report
```
✅ Installation Check: PASSED
   - All required files present
   - Desktop file at correct location

✅ Standalone Tests: PASSED
   - test_sphere_generates_image
   - test_ambient_darkens_sphere
   - test_light_angle_changes_shading
   - test_base_color_applied

✅ Krita Integration: PASSED
   - Factory registration successful
   - Docker widget visible
   - Sphere displays correctly
   - Color picking functional
   - All controls responsive
   - No terminal errors
```

### Failed Test Report Template
```
❌ Test Failed: [TEST NAME]

Error Location: [FILE:LINE]

Error Message:
[Full traceback]

Expected: [What should happen]
Actual: [What actually happened]

Resolution: [Steps to fix]
```