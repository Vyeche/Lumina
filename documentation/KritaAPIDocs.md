URLS: 
- https://apidoc.krita.maou-maou.fr/
- https://scripting.krita.org/lessons/introduction
- https://scripting.krita.org/lessons/reference-api-pyqt
- https://scripting.krita.org/lessons/reference-api-krita
- https://api.kde.org/legacy/krita/html/classKrita.html
- https://docs.krita.org/en/reference_manual/dockers.html
- https://docs.krita.org/en/user_manual/python_scripting/krita_python_plugin_howto.html
- Dont change URL above.
- Add any new links below.

## Add any missing interesting points below based on the above the documentation.

## API Indexes
- [Versions](https://apidoc.krita.maou-maou.fr/kapi-version.html)
- [Classes](https://apidoc.krita.maou-maou.fr/kapi-classes.html)

## Singleton Access
```python
from krita import *
app = Krita.instance()    # same as Application()
```
Builtins `Scripter` and `Application` are aliases for `Krita.instance()`.

## Key Classes for Lumina

### Krita (Application)
Root access to the Krita object hierarchy.

| Method | Notes |
|--------|-------|
| `activeDocument()` → `Document` | Currently active doc, may be `None` |
| `activeWindow()` → `Window` | Currently active window, may be `None` |
| `addDockWidgetFactory(factory)` | Registers docker factory (our plugin entry) |
| `documents()` → `list[Document]` | All open documents |
| `createDocument(w, h, name, model, depth, profile, res)` | Creates new doc |
| `addProfile(profilePath)` → `bool` | Register ICC profile |
| `filters()` → `list[str]` | All filter names |
| `icon(name)` → `QIcon` | Get internal icon by name |
| `readSetting(group, name, default)` → `str` | Read saved setting |
| `writeSetting(group, name, value)` | Save setting persistently |
| `resources(type)` → `dict` | Get resources by type (`'preset'`, `'gradient'`, etc.) |

### Document
Represents an open Krita document/image.

| Property/Method | Notes |
|-----------------|-------|
| `colorModel()` → `str` | `RGBA`, `XYZA`, `LABA`, `CMYKA`, `GRAYA`, `YCbCrA`, `A` |
| `colorDepth()` → `str` | `U8`, `U16`, `F16`, `F32` |
| `colorProfile()` → `str` | ICC profile name |
| `activeNode()` → `Node` | Currently selected node |
| `topLevelNodes()` → `list[Node]` | Root-level nodes |
| `width()` / `height()` → `int` | Image dimensions |
| `fileName()` → `str` | Path to saved file |
| `backgroundColor()` → `QColor` | Document background color |
| `setBackgroundColor(color)` → `bool` | Set background |
| `setColorSpace(model, depth, profile)` → `bool` | Convert document colorspace |
| `setColorProfile(profile)` → `bool` | Change profile only |
| `projection(x, y, w, h)` → `QImage` | Renders composited pixels |
| `pixelData(x, y, w, h)` → `QByteArray` | Raw pixel data |
| `lock()` / `unlock()` | Lock for safe concurrent access |
| `tryBarrierLock()` → `bool` | Non-blocking lock attempt |
| `waitForDone()` | Wait for pending operations |
| `modified()` → `bool` | Has unsaved changes |
| `save()` / `saveAs(path)` → `bool` | Save operations |
| `exportImage(path, config)` → `bool` | Export to file |
| `batchmode()` → `bool` | No dialogs/popups |
| `setBatchmode(val)` | Disable interactive UI |
| `bounds()` → `QRect` | Image bounds |
| `name()` / `setName()` | Document name |

### Canvas
Controls zoom, rotation, and view state for a document view.

| Method | Notes |
|--------|-------|
| `zoom()` → `float` | Current zoom level |
| `setZoom(value)` | Set zoom |
| `rotation()` → `float` | Rotation in degrees |
| `setRotation(degrees)` | Set rotation |
| `imageSize()` → `QSize` | Document size in pixels |
| `isDirty()` → `bool` | Canvas needs redraw |

### View
Represents one view on a document. Contains brush settings, colors, blending mode.

| Property | Getter | Setter | Notes |
|----------|--------|--------|-------|
- `foregroundColor` | `foregroundColor()` → `ManagedColor` | `setForeGroundColor(col: ManagedColor)` | Active brush color |
- `backgroundColor` | `backgroundColor()` → `ManagedColor` | `setBackGroundColor(col: ManagedColor)` | Canvas bg |
| `brushSize` | `brushSize()` → `float` | `setBrushSize(size: float)` | In pixels |
| `brushFade` | `brushFade()` → `float` | `setBrushFade(val: float)` | 0.00–1.00 |
| `brushRotation` | `brushRotation()` → `float` | `setBrushRotation(deg: float)` | Brush tip rotation |
| `paintingOpacity` | `paintingOpacity()` → `float` | `setPaintingOpacity(val: float)` | Brush opacity |
| `paintingFlow` | `paintingFlow()` → `float` | `setPaintingFlow(val: float)` | Brush flow |
| `currentBlendingMode` | `currentBlendingMode()` → `str` | `setCurrentBlendingMode(mode: str)` | Layer blending |
| `currentBrushPreset` | `currentBrushPreset()` → `Resource` | `setCurrentBrushPreset(res: Resource)` | Active brush |
| `currentGradient` | `currentGradient()` → `Resource` | `setCurrentGradient(res: Resource)` | Active gradient |
| `currentPattern` | `currentPattern()` → `Resource` | `setCurrentPattern(res: Resource)` | Active pattern |
| `eraserMode` | `eraserMode()` → `bool` | `setEraserMode(val: bool)` | Eraser active? |
| `disablePressure` | `disablePressure()` → `bool` | `setDisablePressure(val: bool)` | Disable tablet pressure |
| `globalAlphaLock` | `globalAlphaLock()` → `bool` | `setGlobalAlphaLock(val: bool)` | Alpha lock mode |
| `HDRExposure` | `HDRExposure()` → `float` | `setHDRExposure(val: float)` | Krita 6.0+ |
| `HDRGamma` | `HDRGamma()` → `float` | `setHDRGamma(val: float)` | Krita 6.0+ |
| `document()` | — | `setDocument(doc: Document)` | Associated doc |
| `canvas()` | — | — | Associated canvas |
| `visible()` | — | `setVisible()` | Show/hide view |
| `showFloatingMessage(msg, icon, timeout, priority)` | — | — | Toast notification |

**Signals:**
- `foregroundColorChanged()` — Emitted when foreground color changes (Krita 6.0.3+)
- `backgroundColorChanged()` — Emitted when background color changes (Krita 6.0.3+)
- `currentBrushPresetChanged()` — Brush preset changed (Krita 6.0.3+)
- `currentToolChanged(str)` — Tool changed, arg is tool ID (Krita 6.0.3+)


### ManagedColor
Color-managed color with full profile support. **All colors in Krita are color managed.** `QColor` is understood as sRGB.

| Method | Notes |
|--------|-------|
| `ManagedColor(model, depth, profile)` | Constructor |
| `fromQColor(qcolor, canvas)` → `ManagedColor` | Convert QColor → ManagedColor |
| `colorForCanvas(canvas)` → `QColor` | Convert to display QColor |
| `setComponents(values: list[float])` | Set channel values (normalized 0–1, includes alpha) |
| `components()` → `list[float]` | Normalized components + alpha |
| `componentsOrdered()` → `list[float]` | Components ordered for display |
| `colorModel()` → `str` | RGBA, XYZA, LABA, CMYKA, GRAYA, YCbCrA, A |
| `colorDepth()` → `str` | U8, U16, F16, F32 |
| `colorProfile()` → `str` | ICC profile name |
| `setColorSpace(model, depth, profile)` → `bool` | Change colorspace |
| `setColorProfile(profile)` → `bool` | Change profile only |
| `toXML()` → `str` | Serialize (swatch format) |
| `fromXML(xml)` → `ManagedColor` | Deserialize |
| `toQString()` → `str` | Human-readable string |

**Creating colors programmatically (our plugin approach):**
```python
# Get the document's color space
cs = doc.colorSpace()
# Create a color in the document's colorspace
# Accepts (r, g, b) with float values 0.0–1.0
managed_color = cs.createColorInstance((r, g, b))
# Or set alpha too: cs.createColorInstance((r, g, b, a))
```

**Alternative using fromQColor:**
```python
# Requires a canvas reference
canvas = view.canvas()
mc = ManagedColor.fromQColor(qcolor, canvas)
# Then set components and set as foreground
components = mc.components()
components[0] = 1.0
components[1] = 0.6
components[2] = 0.7
mc.setComponents(components)
view.setForeGroundColor(mc)
```

### DockWidget
Base class for custom docker panels.

```python
class MyDocker(DockWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("My Docker")
    
    def canvasChanged(self, canvas):
        # Called when user switches between canvases
        # One docker per window, not per canvas
        pass
```

| Method | Notes |
|--------|-------|
| `canvas()` → `Canvas` | Currently associated canvas |
| `canvasChanged(canvas)` | Override — called on canvas switch |

### DockWidgetFactoryBase
Factory for creating docker instances. Must be registered with `Application.addDockWidgetFactory()`.

```python
from krita import DockWidgetFactoryBase, DockWidget

Application.addDockWidgetFactory(
    DockWidgetFactory("my_docker_id", DockWidgetFactoryBase.DockRight, MyDocker)
)
```

**Dock position constants:**
- `DockWidgetFactoryBase.DockLeft`
- `DockWidgetFactoryBase.DockRight`
- `DockWidgetFactoryBase.DockTop`
- `DockWidgetFactoryBase.DockBottom`
- `DockWidgetFactoryBase.DockTornOff`
- `DockWidgetFactoryBase.DockMinimized`

**Note:** `DockWidgetFactoryBase.EndDockWidgetList` was removed in Krita 5.x. Do NOT use it.

### Node
Represents a layer or group in the document.

| Method | Notes |
|--------|-------|
| `name()` / `setName(name)` | Layer name |
| `visible()` / `setVisible(bool)` | Layer visibility |
| `opacity()` → `float` | Opacity 0–100 |
| `setOpacity(opacity)` | Set opacity |
| `blendingMode()` → `str` | Current blend mode |
| `setBlendingMode(mode)` | Set blend mode (Krita 6.0+) |
| `isBackground()` → `bool` | Background layer? |
| `isGroupLayer()` → `bool` | Group node? |
| `isInternal()` → `bool` | Internal-only (e.g., projection) |
| `locked()` / `setLocked(bool)` | Edit lock |
| `selected()` / `setSelected(bool)` | Selection state |
| `moveToBack()` / `moveToFront()` | Layer ordering |
| `moveToParent(parent)` | Move into group |
| `duplicate()` → `Node` | Create copy |
| `remove()` | Delete layer |
| `projection()` → `QImage` | Rendered layer pixels |
| `image()` → `QImage` | Current layer content |
| `maskSelected()` → `QImage` | Selection mask |
| `maskActive()` → `QImage` | Active mask |
| `width()` / `height()` → `int` | Node dimensions |
| `colorSpace()` → `str` | Color model + depth |
| `colorProfile()` → `str` | ICC profile |
| `pixel(x, y)` → `list[float]` | Pixel values at point |
| `setPixel(x, y, vals)` | Set pixel (normalized) |

### ColorSpace
Accesses the document's color space for creating ManagedColor instances.

| Method | Notes |
|--------|-------|
| `colorSpace()` → `ColorSpace` | From `Document.colorSpace()` |
| `createColorInstance(components)` → `ManagedColor` | Create color in this colorspace |
| `id()` → `str` | Color space ID (e.g., `RGBA_U8`) |
| `name()` → `str` | Human-readable name |

### Selection
Represents the active selection boundary on a node.

| Method | Notes |
|--------|-------|
| `xpos()` / `ypos()` → `int` | Selection offset |
| `width()` / `height()` → `int` | Selection bounds |
| `isEmpty()` → `bool` | No selection active |
| `isValid()` → `bool` | Selection is well-formed |
| `to QImage` | Convert to mask image |
| `setFromPath(path)` | Load selection from file |
| `saveToFile(path)` | Export selection |

### Filter
Applies Krita filters to nodes with configurable parameters.

| Method | Notes |
|--------|-------|
| `apply(node, x, y, w, h, config)` | Apply filter with config dict |
| `id()` → `str` | Filter ID |
| `name()` → `str` | Human-readable name |
| `hasConfig()` → `bool` | Filter accepts parameters |
| `apply(node, x, y, w, h)` | Apply with defaults |
| `apply(node, x, y, w, h, config)` | Apply with custom config (dict) |

**Config format:** `{param_name: value}` dict, e.g., `{intensity: 0.5, radius: 10}`.

### InfoObject
Per-document metadata stored by Krita.

| Method | Notes |
|--------|-------|
| `get(key)` → `str` | Read value (may be `None`) |
| `set(key, value)` | Set metadata |

### Resource
Any browsable Krita resource (brush preset, gradient, pattern, etc.).

| Method | Notes |
|--------|-------|
| `name()` → `str` | Resource name |
| `path()` → `str` | File system path |
| `description()` → `str` | Resource description |
| `data()` → `bytes` | Raw resource data |

### Window
Top-level window (subclass of `QMainWindow`).

| Method | Notes |
|--------|-------|
| `activeView()` → `View` | Current active view |
| `views()` → `list[View]` | All views in window |
| `activeWindow()` → `Window` | Active window (from `Krita.instance()`) |
| `showNotification(message, duration)` | Show brief toast |

### Canvas
Canvas controls (zoom, rotation, view state).

| Method | Notes |
|--------|-------|
| `zoom()` / `setZoom(val)` | Zoom level |
| `rotation()` / `setRotation(deg)` | Rotation in degrees |
| `mirrorImageX()` / `mirrorImageY()` | Horizontal/vertical flip |
| `setMirrorImageX(bool)` / `setMirrorImageY(bool)` | Set flip state |
| `imageSize()` → `QSize` | Document size in pixels |
| `wraparound()` / `setWraparound(bool)` | Wraparound mode |
| `isDirty()` → `bool` | Needs redraw |

### GridConfig
Document grid settings.

| Method | Notes |
|--------|-------|
| `originX()` / `originY()` → `float` | Grid origin |
| `cellSize()` → `float` | Grid cell size |
| `rotation()` → `float` | Grid rotation |
| `gridEnabled()` → `bool` | Grid visible? |

### GuidesConfig
Document guide settings.

| Method | Notes |
|--------|-------|
| `verticalGuides()` → `list[float]` | Vertical guide positions |
| `horizontalGuides()` → `list[float]` | Horizontal guide positions |
| `setVerticalGuides(positions)` | Set vertical guides |
| `setHorizontalGuides(positions)` | Set horizontal guides |

### Extension
Base class for dock widget plugins. Use `DockWidgetFactoryBase` for docker registration.

| Method | Notes |
|--------|-------|
| `createAction(id)` → `QAction` | Add menu/toolbar action |
| `createActionWithShortcut(id, shortcut)` | Action with keyboard shortcut |

### AppWindow
Application-level window access (Krita 6.0+).

| Method | Notes |
|--------|-------|
| `activeView()` → `View` | Current view |
| `views()` → `list[View]` | All views |
| `activeWindow()` → `Window` | Active window |
| `showNotification(message, duration)` | Toast notification |
## Color Workflow Example

```python
from krita import *

# Get active document and canvas
app = Krita.instance()
doc = app.activeDocument()
view = app.activeWindow().activeView()
canvas = view.canvas()

# Get document's color space
cs = doc.colorSpace()

# Set foreground color from RGB values (0.0–1.0)
r, g, b = 1.0, 0.5, 0.2
color = cs.createColorInstance((r, g, b))
view.setForeGroundColor(color)

# Read current foreground
current = view.foregroundColor()
comps = current.components()  # [r, g, b, a] normalized
```

## Batch Mode Detection
```python
# Scripts running in batch mode should not show dialogs
if Krita.instance().batchmode():
    # Silent mode — skip UI interactions
    pass
```

## Settings Persistence
```python
# Read saved setting
val = Krita.instance().readSetting("Lumina", "lightAngle", "45")

# Save setting
Krita.instance().writeSetting("Lumina", "lightAngle", str(angle))
```

## Resources (Brushes, Gradients, Patterns, etc.)
```python
# Get all brush presets
brushes = Krita.instance().resources('preset')

# Get a specific preset by name
for name, res in brushes.items():
    if "Round" in name:
        view.setCurrentBrushPreset(res)
        break
```
## Usage Examples

### Creating a Dock Widget Plugin
```python
from krita import DockWidget, DockWidgetFactoryBase, Krita
from PyQt5.QtWidgets import QWidget

class MyDocker(DockWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("My Docker")
        # Setup self.ui here

    def canvasChanged(self, canvas):
        # Called when user switches documents/canvases
        if canvas:
            doc = canvas.document()
            self.updateForDocument(doc)

    def closePlugin(self):
        # Cleanup when docker closes
        pass

class MyFactory(DockWidgetFactoryBase):
    def __init__(self):
        super().__init__()

# Register the factory
factory = MyFactory()
factory.setWidgetClassName("MyDocker")
Krita.instance().addDockWidgetFactory(factory, DockWidgetFactoryBase.DockRight)
```

### Setting Foreground Color from QColor
```python
from krita import Krita
from PyQt5.QtGui import QColor

def set_foreground(r, g, b):
    """Set Krita's foreground color from 0-255 RGB values."""
    doc = Krita.instance().activeDocument()
    if not doc:
        return
    cs = doc.colorSpace()
    # Convert to 0.0–1.0 float range
    managed = cs.createColorInstance((r/255.0, g/255.0, b/255.0))
    # Get the view from the active document
    win = Krita.instance().activeWindow()
    if win:
        win.view().setForeGroundColor(managed)
```

### Creating a New Layer
```python
from krita import Krita

def create_layer(name, above_active=True):
    doc = Krita.instance().activeDocument()
    if not doc:
        return None
    node = doc.createNode(name, doc.width(), doc.height())
    if above_active:
        active = doc.activeNode()
        if active:
            node.moveToParent(active)
            node.moveToParent(active.parent())
    doc.getRootNode().addChild(node)
    return node
```

### Reading Pixel Data from a Node
```python
from krita import Krita

def get_pixel(node, x, y):
    """Return normalized RGBA list [r, g, b, a] at (x, y)."""
    pixel = node.pixel(x, y)
    # pixel is already a list of floats, 0.0–1.0
    return list(pixel)

def set_pixel(node, x, y, r, g, b, a=1.0):
    """Set pixel to RGBA values in 0.0–1.0 range."""
    node.setPixel(x, y, [r, g, b, a])
```

### Using the Selection API
```python
from krita import Krita

def fill_selection(color_rgb):
    """Fill the active selection with a color."""
    doc = Krita.instance().activeDocument()
    if not doc:
        return
    node = doc.activeNode()
    sel = node.selection()
    if sel.isEmpty():
        return
    # Selection provides bounds
    x, y = sel.xpos(), sel.ypos()
    w, h = sel.width(), sel.height()
    # Lock, fill, unlock
    doc.lock()
    try:
        for py in range(h):
            for px in range(w):
                if sel.isSelected(x + px, y + py):
                    node.setPixel(x + px, y + py, color_rgb)
    finally:
        doc.unlock()
```

### Applying a Filter
```python
from krita import Krita

def apply_gaussian_blur(node, radius=5):
    """Apply Gaussian Blur to a node."""
    filters = Krita.instance().filters()
    if "gaussian-blur" not in filters:
        return
    config = {"radius": radius}
    node.applyFilter("gaussian-blur", config)
```

### Persisting Settings Across Sessions
```python
from krita import Krita

def save_light_angle(angle):
    """Save light angle to Krita settings."""
    Krita.instance().writeSetting("Lumina", "lightAngle", str(int(angle)))

def load_light_angle():
    """Load saved light angle, default 45."""
    return int(Krita.instance().readSetting("Lumina", "lightAngle", "45"))
```

### Accessing Available Resources
```python
from krita import Krita

# All brush presets: {name: Resource, ...}
brushes = Krita.instance().resources('preset')

# All gradients
gradients = Krita.instance().resources('gradient')

# All patterns
patterns = Krita.instance().resources('pattern')

# Use a resource
for name, res in brushes.items():
    if "Round" in name and "Hard" in name:
        Krita.instance().activeWindow().view().setCurrentBrushPreset(res)
        break
```

### Working with Node Groups
```python
from krita import Krita

def move_node_to_group(child_name, group_name):
    doc = Krita.instance().activeDocument()
    if not doc:
        return
    child = _find_node(doc, child_name)
    group = _find_node(doc, group_name)
    if child and group and group.isGroupLayer():
        child.moveToParent(group)

def _find_node(doc, name):
    for node in doc.topLevelNodes():
        if node.name() == name:
            return node
    return None
```

### Document Batch Mode
```python
from krita import Krita

def run_silent_operation():
    """Execute without dialogs or popups."""
    doc = Krita.instance().activeDocument()
    if not doc:
        return
    doc.setBatchmode(True)
    try:
        # ... heavy operations ...
        pass
    finally:
        doc.setBatchmode(False)
```
