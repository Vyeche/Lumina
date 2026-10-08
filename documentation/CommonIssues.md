# Common Issues and Troubleshooting

This document covers common issues encountered during Lumina plugin development and usage, with verified solutions.

## Table of Contents
1. [Widget Hierarchy Issues](#widget-hierarchy-issues)
2. [Factory Registration Errors](#factory-registration-errors)
3. [Display and Rendering Issues](#display-and-rendering-issues)
4. [Color Management Issues](#color-management-issues)
5. [Mouse Event Issues](#mouse-event-issues)
6. [Krita Integration Issues](#krita-integration-issues)
7. [Performance Issues](#performance-issues)

---

## Widget Hierarchy Issues

### Issue 1: TypeError on Widget Initialization

**Error Message**:
```
TypeError: LuminaWidget.__init__() takes 1 positional argument but 2 were given
```

**Location**: `sphere_widget.py:541`

**Root Cause**: 
`LuminaWidget` constructor signature changed but call site not updated.

**Solution**:
```python
# In sphere_widget.py __init__
def __init__(self, parent=None):  # ← Add optional parent parameter
    super().__init__(parent)
    # ... rest of initialization
```

**Verification**:
- ✅ No TypeError on docker creation
- ✅ LuminaWidget created successfully
- ✅ `self._sphere_container` initialized

---

### Issue 2: Missing setWidget() Call

**Error Message**:
```
krita.general: Could not create docker for "color_sphere_factory"
```

**Root Cause**:
`DockWidget` requires a child `QWidget` via `setWidget()`. Without it, Krita cannot create the widget.

**Solution**:
```python
class SphereDocker(DockWidget):
    def __init__(self):
        super().__init__()
        
        # Create main widget
        mainWidget = QWidget()
        self.setWidget(mainWidget)  # ← REQUIRED
        
        # Create layout on mainWidget, not self
        mainWidget.setLayout(QVBoxLayout())
```

**Verification**:
- ✅ No "could not create docker" error
- ✅ Panel appears in View → Dock Widgets
- ✅ No QML popup errors

---

### Issue 3: Layout on Wrong Widget

**Error Message**:
Various Qt errors about layout ownership

**Root Cause**:
Calling `setLayout()` on `DockWidget` instead of its child `QWidget`.

**Wrong**:
```python
class SphereDocker(DockWidget):
    def __init__(self):
        self.setLayout(QVBoxLayout())  # ❌ Wrong
```

**Correct**:
```python
class SphereDocker(DockWidget):
    def __init__(self):
        mainWidget = QWidget()
        self.setWidget(mainWidget)
        mainWidget.setLayout(QVBoxLayout())  # ✅ Correct
```

**Verification**:
- ✅ No Qt layout ownership errors
- ✅ Widgets display correctly

---

## Factory Registration Errors

### Issue 1: EndDockWidgetList Reference

**Error Message**:
```
AttributeError: module 'krita' has no attribute 'EndDockWidgetList'
```

**Root Cause**:
`DockWidgetFactoryBase.EndDockWidgetList` was removed in Krita 5.x.

**Solution**:
```python
# WRONG - Do NOT use this
factory = DockWidgetFactory("my_id", 
                            DockWidgetFactoryBase.EndDockWidgetList,
                            create_docker)

# CORRECT - Just use DockRight constant
from krita import DockWidgetFactory, DockWidgetFactoryBase

factory = DockWidgetFactory("my_id", 
                            DockWidgetFactoryBase.DockRight,  # ← Correct
                            create_docker)
```

**Note**: Do not reference `EndDockWidgetList` anywhere in your code.

---

### Issue 2: Factory Name Mismatch

**Error Message**:
Plugin doesn't appear in dock widget list

**Root Cause**:
`X-KDE-Library` in `.desktop` doesn't match factory name in code.

**Solution**:
Ensure these match exactly:
```desktop
# In Lumina.desktop
X-KDE-Library=Lumina
Name=Lumina
```

```python
# In __init__.py
factory = DockWidgetFactory("color_sphere_factory", ...)
```

**Note**: The `.desktop` name becomes the display name, factory name is internal.

---

### Issue 3: Plugin Cache Not Cleared

**Issue**: Plugin installed but doesn't appear after restart

**Root Cause**:
Krita caches plugin list at startup. Simply closing window is insufficient.

**Solution**:
```bash
# Force kill and restart
flatpak run --kill org.kde.krita
```

**Verification Steps**:
1. Close Krita completely
2. Run `flatpak run --kill org.kde.krita` from terminal
3. Launch with `flatpak run org.kde.krita`
4. Check **View → Dock Widgets → Lumina**

---

## Display and Rendering Issues

### Issue 1: Sphere Image Not Visible

**Symptoms**:
- Widget has size but no content
- Red rectangle shown (test code)
- No sphere image

**Root Cause**:
`_sphere_image` is None or not initialized before paintEvent.

**Solution**:
```python
class SphereDocker(DockWidget):
    def __init__(self):
        super().__init__()
        mainWidget = QWidget()
        self.setWidget(mainWidget)
        
        # Initialize processor first
        self.processor = SphereColorProcessor()
        
        # Create widget with processor
        self._sphere_container = LuminaWidget(self)
        
        # Generate image before showing
        self._sphere_image = self.processor.generate_sphere_image(
            QColor(200, 60, 60), 128, 128
        )
```

**Verification**:
- ✅ Sphere image appears (no red rectangle)
- ✅ Colors vary across sphere
- ✅ No "No docker or sphere image" warnings

---

### Issue 2: Sphere Clipped or Cut Off

**Symptoms**:
- Only quarter or half of sphere visible
- Elliptical shape instead of circle

**Root Cause**:
Widget size or image scaling calculations incorrect.

**Solution**:
```python
# Set fixed size on container widget
self._sphere_container.setFixedSize(200, 200)

# In paintEvent, proper scaling
scale = min(w, h) / res
sw = int(res * scale)
sh = int(res * scale)
off_x = int((w - sw) / 2)
off_y = int((h - sh) / 2)

# Draw image
painter.drawImage(off_x, off_y, self._sphere_image, 0, 0, sw, sh)
```

**Verification**:
- ✅ Sphere shows as full circle
- ✅ No clipping at edges
- ✅ Proper aspect ratio

---

### Issue 3: No Color Variation

**Symptoms**:
- Entire sphere is same color
- All gray or monochromatic

**Root Cause**:
Lighting calculations not applied or base color not set.

**Solution**:
```python
def generate_sphere_image(self, base_color, width, height):
    # Ensure base color is set
    self.base_color = base_color
    
    # Ensure light angle is set
    self.light_angle = 45  # Default
    
    # Generate with lighting
    return self._render_with_phong_shading(width, height)
```

**Verification**:
- ✅ Dark to bright gradient visible
- ✅ Highlight on lit side
- ✅ Shadows on dark side

---

## Color Management Issues

### Issue 1: Colors Shift in Documents

**Symptoms**:
- Color picked from sphere doesn't match Krita
- Different colors in different documents

**Root Cause**:
Using `QColor` directly without `ManagedColor` conversion.

**Solution**:
```python
def safe_apply_color(self, color):
    """Apply color respecting document colorspace."""
    try:
        view = self._get_view()
        if not view:
            print("Lumina: No active view")
            return
        
        # Convert QColor to ManagedColor
        canvas = view.canvas()
        managed_color = ManagedColor.fromQColor(color, canvas)
        
        # Set foreground color
        view.setForeGroundColor(managed_color)
        
        print(f"Lumina: Applied color {color}")
    except Exception as e:
        print(f"Lumina: Failed - {e}")
        import traceback
        traceback.print_exc()
```

**Verification**:
- ✅ Color matches in all documents
- ✅ No color shift between sRGB and other profiles

---

### Issue 2: Incorrect Color Components

**Symptoms**:
- Colors have wrong RGB values
- Alpha channel issues

**Root Cause**:
Using normalized values (0.0-1.0) instead of 0-255, or vice versa.

**Solution**:
```python
# When creating color from user input (0-255)
qcolor = QColor(200, 60, 60)  # 0-255

# When passing to ManagedColor.fromQColor
mc = ManagedColor.fromQColor(qcolor, canvas)

# When creating programmatically (0.0-1.0)
cs.createColorInstance((200/255, 60/255, 60/255))
```

**Verification**:
- ✅ Color picker shows correct values
- ✅ Hex values match RGB

---

## Mouse Event Issues

### Issue 1: Color Values Not Displayed

**Symptoms**:
- Dragging on sphere does nothing
- No RGB values shown

**Root Cause**:
Mouse tracking not enabled or coordinate conversion fails.

**Solution**:
```python
class LuminaWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)  # ← Enable mouse tracking
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAutoFillBackground(True)
    
    def mouseMoveEvent(self, event):
        """Handle drag for color picking."""
        self._handle_mouse_event(event, is_drag=True)
```

**Coordinate Conversion**:
```python
def _to_sphere_pixel(self, point):
    """Convert widget coordinates to image coordinates."""
    w = self.width()
    h = self.height()
    res = self._docker.processor.resolution
    
    # Calculate image bounds
    scale = min(w, h) / res
    sw = int(res * scale)
    sh = int(res * scale)
    off_x = int((w - sw) / 2)
    off_y = int((h - sh) / 2)
    
    # Check if point is within image bounds
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

**Verification**:
- ✅ RGB values appear on drag
- ✅ Values update continuously
- ✅ Click outside shows "None, None, None"

---

### Issue 2: Color Not Applied on Drag

**Symptoms**:
- Values show but color doesn't change in Krita

**Root Cause**:
`_send_to_krita` not called or view is None.

**Solution**:
```python
def _handle_mouse_event(self, event, is_press):
    """Handle mouse events for color picking."""
    if self._docker._sphere_image is None:
        print("Lumina: No sphere image")
        return
    
    point = event.pos()
    sx, sy = self._to_sphere_pixel_in_container(point)
    
    if sx is not None and sy is not None:
        try:
            pixel = self._docker._sphere_image.pixelColor(sx, sy)
            self._docker._hover_color = pixel
            
            if is_press:
                self._docker._is_dragging = True
                self._docker._send_to_krita(pixel)  # ← Call this
        except Exception as e:
            print(f"Lumina: Error getting pixel: {e}")
```

**Verification**:
- ✅ Krita foreground color changes on drag
- ✅ Color matches sphere pixel

---

## Krita Integration Issues

### Issue 1: Plugin Won't Load

**Symptoms**:
- No "Lumina" in dock widget list
- Terminal shows "Could not create docker"

**Root Cause**:
Plugin files not in correct location or corrupted.

**Solution**:
```bash
# Verify structure
ls -la ~/.var/app/org.kde.krita/data/krita/pykrita/

# Should show:
# Lumina.desktop          ← At root
# Lumina/                 ← Subdirectory
#   ├── __init__.py
#   ├── sphere_widget.py
#   └── color_processor.py
```

**Reinstall**:
```bash
# Remove old
rm -rf ~/.var/app/org.kde.krita/data/krita/pykrita/Lumina*

# Copy fresh
cp -r src/Lumina/* ~/.var/app/org.kde.krita/data/krita/pykrita/

# Clean cache
rm -rf ~/.var/app/org.kde.krita/data/krita/pykrita/Lumina/__pycache__

# Restart
flatpak run --kill org.kde.krita
flatpak run org.kde.krita
```

**Verification**:
- ✅ `Lumina.desktop` at pykrita root
- ✅ All `.py` files in `Lumina/` directory
- ✅ No `__pycache__` errors

---

### Issue 2: Reinstall Fails on Windows — `lumina_log.txt` Locked (WinError 32)

**Symptoms** (Krita 5.x, Windows):
```
PermissionError: [WinError 32] The process cannot access the file because
it is being used by another process:
'...\AppData\Roaming\krita\pykrita\Lumina\lumina_log.txt'
```
thrown from `plugin_importer.py` (`shutil.rmtree`) during
Tools → Scripts → Import Python Plugin from File.

**Root Cause**:
Versions ≤ 2.5.0 kept `lumina_log.txt` open with a permanent
`FileHandler` for the whole session. Windows locks open files, so the
importer's delete-the-old-directory step failed while Krita (running the
old version) still held the log. Linux never showed this — it allows
deleting open files.

**Immediate workaround** (any version):
1. Close Krita completely.
2. Delete `%AppData%\krita\pykrita\Lumina` (and `Lumina.desktop`).
3. Start Krita and import the zip again.

**Fixed after 2.5.0**: logging moved to a shared `lumina_logging` module
whose handler opens/appends/closes on every record and never holds the
file open, so the importer can always delete the directory.

---

### Issue 2: CanvasChange Not Called

**Symptoms**:
- Widget works on first canvas, fails after switching
- `canvasChanged` method shows in logs but doesn't execute

**Root Cause**:
Missing or incorrect `canvasChanged` implementation.

**Solution**:
```python
class SphereDocker(DockWidget):
    def __init__(self):
        super().__init__()
        mainWidget = QWidget()
        self.setWidget(mainWidget)
        
        # Initialize processor with default base color
        self.processor = SphereColorProcessor()
        
    def canvasChanged(self, canvas):
        """Called when user switches between canvases."""
        print(f"Lumina: canvasChanged called, canvas={canvas}")
        # Optionally re-initialize or update settings
```

**Note**: This method is required by Krita's Docker base class.

---

### Issue 3: Memory Leak on Widget Creation

**Symptoms**:
- Memory usage grows with each Krita restart
- Performance degrades over time

**Root Cause**:
Not cleaning up references or not using single-instance pattern.

**Solution**:
```python
# Ensure only one instance exists
class SphereDocker(DockWidget, object):
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not self._initialized:
            self._initialized = True
            # ... initialization
```

**Alternative**: Clean up in factory
```python
def create_docker():
    """Create or get singleton docker instance."""
    global _docker_instance
    if _docker_instance is None:
        _docker_instance = SphereDocker()
    return _docker_instance
```

**Verification**:
- ✅ Memory stable across restarts
- ✅ No "object at 0x..." accumulation in logs

---

## Performance Issues

### Issue 1: Slow Sphere Rendering

**Symptoms**:
- Lag when dragging on sphere
- UI unresponsive during rendering

**Root Cause**:
Generating image on every mouse move instead of caching.

**Solution**:
```python
class SphereColorProcessor:
    def __init__(self):
        self._sphere_image = None
        # Pre-compute normal and diffuse maps
        self._normal_map = self._generate_normal_map()
        self._diffuse_map = self._generate_diffuse_map()
    
    def generate_sphere_image(self, base_color, width, height):
        # Only generate if parameters changed
        if (self._sphere_image is None or 
            self._sphere_image.width() != width or
            self.base_color != base_color):
            self._sphere_image = self._render(width, height)
        
        return self._sphere_image
```

**Optimization**:
- Use lower resolution for testing (64x64)
- Pre-compute normal maps once
- Cache generated images

**Verification**:
- ✅ Smooth drag response
- ✅ No lag on mouse movement
- ✅ Consistent FPS

---

### Issue 2: High CPU Usage

**Symptoms**:
- High CPU during Krita idle
- Fan running constantly

**Root Cause**:
Widget timer or recursive updates.

**Solution**:
```python
# Remove unnecessary timers
# class LuminaWidget(QWidget):
#     def __init__(self):
#         super().__init__()
#         # self._timer = QTimer(self)  # ← Don't create timer
#         # self._timer.timeout.connect(self.update)
#         # self._timer.start(1000)
```

**Verify**:
- ✅ Check terminal for excessive update() calls
- ✅ Monitor with `top` or `htop`
- ✅ Disable auto-update if not needed

---

## Development Debugging

### Enable Verbose Logging

Add this to `__init__.py`:
```python
def register_factory():
    import logging
    logging.basicConfig(level=logging.DEBUG)
    
    # ... rest of code ...
    print("Lumina: DEBUG - Factory registration started")
```

### Add Console Logging Pattern

```python
def debug_log(self, message, level="INFO"):
    """Log debug message to console."""
    print(f"Lumina [DEBUG [{level}]]: {message}")

# Usage
self.debug_log(f"Image size: {self._sphere_image.width()}x{self._sphere_image.height()}")
```

### Common Debug Checks

```python
# Check widget visibility
print(f"Visible: {self.isVisible()}, Showed: {self.isShown()}")

# Check parent widget
print(f"Parent: {self.parent()}, Parent type: {type(self.parent())}")

# Check image data
if self._sphere_image:
    print(f"Image format: {self._sphere_image.format()}")
    print(f"Image size: {self._sphere_image.width()}x{self._sphere_image.height()}")
    print(f"Byte count: {self._sphere_image.byteCount()}")

# Check Krita instance
instance = Krita.instance()
print(f"Krita instance: {instance}")
print(f"Active document: {instance.activeDocument()}")
```

---

## Quick Troubleshooting Flow

1. **Plugin not appearing?**
   - Check `.desktop` at pykrita root
   - Run `flatpak run --kill org.kde.krita`
   - Restart Krita

2. **Factory registration error?**
   - Check terminal for full traceback
   - Verify `setWidget(mainWidget)` called
   - Check widget constructor signatures

3. **Widget not showing?**
   - Verify `self._sphere_image` is not None
   - Check widget size and geometry
   - Ensure `update()` called after drawing

4. **Color picking not working?**
   - Check `setMouseTracking(True)` enabled
   - Verify coordinate conversion returns valid values
   - Check `pixelColor()` call succeeds

5. **Errors in terminal?**
   - Copy full traceback
   - Search this document for error message
   - If not found, add to `Bugs.md`

---

## References

- [Krita Python Plugin Documentation](https://docs.krita.org/en/user_manual/python_scripting/krita_python_plugin_howto.html)
- [Krita API Reference](https://apidoc.krita.maou-maou.fr/)
- [Testing Guide](./TestingGuide.md)
- [Krita Plugin API](./KritaPluginAPI.md)