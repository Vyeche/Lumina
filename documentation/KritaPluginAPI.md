# Krita Plugin API - Best Practices

This guide covers Krita Python plugin development patterns, API usage, and common pitfalls for the Lumina plugin.

## Table of Contents
1. [Widget Hierarchy](#widget-hierarchy)
2. [Docker Registration](#docker-registration)
3. [Color Management](#color-management)
4. [Document Access](#document-access)
5. [Mouse Event Handling](#mouse-event-handling)
6. [Error Handling](#error-handling)
7. [Testing](#testing)

---

## Widget Hierarchy

### Critical Pattern: Docker → QWidget → Layouts

**WRONG** (causes crash):
```python
class SphereDocker(DockWidget):
    def __init__(self):
        self.setLayout(QVBoxLayout())  # ❌ Layout on Docker itself
```

**CORRECT**:
```python
class SphereDocker(DockWidget):
    def __init__(self):
        super().__init__()
        
        # Step 1: Create main widget child
        mainWidget = QWidget()
        mainWidget.setStyleSheet("QWidget { background-color: #141419; }")
        
        # Step 2: Set it as the docker's widget
        self.setWidget(mainWidget)
        
        # Step 3: Create layout on mainWidget, not self
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(4, 4, 4, 4)
        mainWidget.setLayout(main_layout)
        
        # Step 4: Add widgets to mainWidget or main_layout
        self._sphere_container = LuminaWidget(self)
        self._sphere_container.setFixedSize(200, 200)
        main_layout.addWidget(self._sphere_container)
```

### Why This Matters
- `DockWidget` expects a child `QWidget` to hold UI elements
- Layouts must be created on the child widget
- Missing `setWidget()` causes "cannot find window" errors

---

## Docker Registration

### Factory Function Pattern

```python
# __init__.py
def create_docker():
    """Factory function - creates new docker instance."""
    print("Lumina: create_docker() called")
    from .sphere_widget import SphereDocker
    docker = SphereDocker()
    print(f"Lumina: docker visible={docker.isVisible()}")
    return docker

def register_factory():
    """Register with Krita."""
    import krita
    from krita import DockWidgetFactory, DockWidgetFactoryBase
    
    factory = DockWidgetFactory("color_sphere_factory", 
                                DockWidgetFactoryBase.DockRight, 
                                create_docker)
    Krita.instance().addDockWidgetFactory(factory)
```

### Important Notes
- Factory function returns `DockWidget` instance
- Factory name must match `X-KDE-Library` in `.desktop` file
- Use integer position: `DockWidgetFactoryBase.DockRight` = right side
- DO NOT use `DockWidgetFactoryBase.EndDockWidgetList` (removed in Krita 5.x)

### Desktop File Location
The `.desktop` file must be at the **root** of the plugin directory in `pykrita/`:
```
pykrita/
├── Lumina.desktop        ← Root (Krita service registration)
└── Lumina/               ← Plugin package
    └── __init__.py
```

---

## Color Management

### ManagedColor vs QColor

**Key Concept**: All colors in Krita are color-managed. `QColor` is sRGB; `ManagedColor` respects document colorspace.

**Creating colors for Krita**:
```python
# Get document's color space
doc = Krita.instance().activeDocument()
cs = doc.colorSpace()

# Method 1: Create in document's colorspace
managed_color = cs.createColorInstance((r, g, b))  # r, g, b in 0.0-1.0

# Method 2: Convert from QColor
canvas = doc.activeView().canvas()
mc = ManagedColor.fromQColor(qcolor, canvas)

# Method 3: Set components
components = mc.components()  # Returns [r, g, b, a] in 0.0-1.0
components[0] = 1.0  # Red
mc.setComponents(components)
```

**Setting foreground color**:
```python
view = doc.activeView()
if view:
    view.setForeGroundColor(managed_color)
```

### Common Pitfall
Using `QColor` directly without conversion to `ManagedColor` can cause color shifts in documents with different colorspaces.

---

## Document Access

### Getting Active Document/View
```python
def safe_get_view():
    """Safely get the current view (may be None)."""
    instance = Krita.instance()
    doc = instance.activeDocument()
    if doc:
        view = doc.activeView()
        return view
    return None

# Usage
view = safe_get_view()
if view:
    color = view.foregroundColor()
    print(f"Current color: {color.toQColor()}")
```

### Document State
```python
doc = Krita.instance().activeDocument()

# Check if document exists
if doc:
    print(f"Document: {doc.name()}")
    print(f"Size: {doc.width()}x{doc.height()}")
    print(f"Modified: {doc.modified()}")
    
    # Get active canvas
    canvas = doc.activeCanvas()
    
    # Get view for color operations
    view = doc.activeView()
```

---

## Mouse Event Handling

### Widget Mouse Tracking

```python
class LuminaWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)  # Enable mouse tracking
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAutoFillBackground(True)
        
    def paintEvent(self, event):
        # Draw sphere image
        painter = QPainter(self)
        painter.drawPixmap(...)
        
    def mouseMoveEvent(self, event):
        # Handle drag for color picking
        self._handle_color_pick(event)
```

### Converting Widget Pixels to Image Coordinates

```python
def _to_image_coords(self, point):
    """Convert widget mouse position to image pixel coordinates."""
    w = self.width()
    h = self.height()
    res = self._processor.resolution  # e.g., 128
    
    # Scale image to widget size
    scale = min(w, h) / res
    sw = int(res * scale)
    sh = int(res * scale)
    
    # Offset to center
    off_x = int((w - sw) / 2)
    off_y = int((h - sh) / 2)
    
    # Check bounds
    if not QRectF(off_x, off_y, sw, sh).contains(point):
        return None
    
    # Convert to image coordinates
    x = int((point.x() - off_x) * res / sw)
    y = int((point.y() - off_y) * res / sh)
    
    # Clamp to valid range
    x = max(0, min(res - 1, x))
    y = max(0, min(res - 1, y))
    
    return x, y
```

### Getting Pixel Color from Image

```python
def _handle_color_pick(self, event):
    """Handle mouse drag for color picking."""
    image = self._docker._sphere_image
    if not image:
        return
    
    coords = self._to_image_coords(event.pos())
    if coords:
        x, y = coords
        pixel = image.pixelColor(x, y)
        self._docker._hover_color = pixel
        self._docker._update_button_style()
```

---

## Error Handling

### Global Error Catch

**Rule**: ALL errors MUST be caught and logged to terminal. Krita popups hide details.

```python
def safe_apply_color(color):
    """Safely apply color to Krita foreground."""
    try:
        view = safe_get_view()
        if not view:
            print("Lumina: No active view")
            return
            
        managed_color = ManagedColor.fromQColor(color, view.canvas())
        view.setForeGroundColor(managed_color)
        print(f"Lumina: Applied color {color}")
    except Exception as e:
        print(f"Lumina: ERROR - {e}")
        import traceback
        traceback.print_exc()
```

### Factory Registration Safety

```python
def register_factory():
    """Register the Lumina dock widget factory with Krita."""
    print("Lumina: Starting factory registration")
    try:
        import krita
        from krita import DockWidgetFactory, DockWidgetFactoryBase
        
        factory = DockWidgetFactory("color_sphere_factory", 
                                    DockWidgetFactoryBase.DockRight, 
                                    create_docker)
        Krita.instance().addDockWidgetFactory(factory)
        print("Lumina: Factory registered successfully")
    except ImportError as e:
        print(f"Lumina: ImportError - {e}")
    except Exception as e:
        print(f"Lumina: Factory registration failed - {e}")
        import traceback
        traceback.print_exc()
```

---

## Testing

### Standalone Tests (No Krita)

The test suite should verify logic without Krita runtime:

```python
# test_shading.py
import sys
import math

# Stub krita module
class _Krita:
    class instance:
        @staticmethod
        def addDockWidgetFactory(*a): pass

sys.modules['krita'] = _Krita()

# Import processor directly (bypassing __init__.py)
import importlib.util
spec = importlib.util.spec_from_file_location(
    "color_processor", 
    "src/Lumina/color_processor.py"
)
color_processor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(color_processor)

# Run tests
def test_sphere_generates_image():
    proc = color_processor.SphereColorProcessor(resolution=64)
    img = proc.generate_sphere_image(
        QColor(200, 60, 60), 
        64, 64
    )
    assert img.width() == 64
    assert img.height() == 64
```

### Running Tests

```bash
# Standalone (no Krita)
python3 src/Lumina/test_shading.py

# With pytest
python3 -m pytest src/Lumina/test_shading.py -v
```

### Krita Integration Tests

**Required verification checklist**:
1. ✅ Factory registration successful (check terminal logs)
2. ✅ Docker widget creation and display
3. ✅ Canvas change handling (no errors)
4. ✅ Sphere rendering and color generation
5. ✅ Mouse event handling (color picking works)
6. ✅ Light angle slider functionality
7. ✅ No errors in terminal output

### Testing Duration

**Run Krita for 30 seconds** to allow user to:
- Open an image
- Access the Lumina panel
- Test color picking functionality

```bash
flatpak run org.kde.krita
# Keep terminal open to view logs
```

---

## Common Pitfalls

### 1. Widget Hierarchy Issues
**Error**: "TypeError: __init__() takes 1 positional argument but 2 were given"
**Cause**: Passing `self` to `LuminaWidget(self)` after removing parent parameter

**Fix**: Update constructor signature:
```python
def __init__(self, parent=None):  # Add optional parent parameter
    super().__init__(parent)
```

### 2. Missing setWidget()
**Error**: "cannot find any window to open popup in"
**Cause**: Not calling `self.setWidget(mainWidget)`

**Fix**: Always set widget on Docker:
```python
self.setWidget(mainWidget)  # REQUIRED
```

### 3. Krita Startup Cache
**Issue**: Plugin doesn't appear after installation
**Cause**: Krita caches plugin list at startup

**Fix**: Force kill and restart:
```bash
flatpak run --kill org.kde.krita
```

### 4. Color Management
**Issue**: Colors shift in documents with different colorspaces
**Cause**: Using `QColor` directly without `ManagedColor` conversion

**Fix**: Always convert to `ManagedColor`:
```python
mc = ManagedColor.fromQColor(qcolor, canvas)
view.setForeGroundColor(mc)
```

---

## References

- **Official Krita Python Docs**: https://docs.krita.org/en/user_manual/python_scripting/krita_python_plugin_howto.html
- **API Reference**: https://apidoc.krita.maou-maou.fr/
- **Color Management**: https://docs.krita.org/en/reference_manual/color_management/index.html
- **Dockers**: https://docs.krita.org/en/reference_manual/dockers.html