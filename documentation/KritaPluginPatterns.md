# Krita Python Plugin Development - Key Patterns

## Official Documentation Pattern

### Creating a Docker (from official docs)

```python
from PyQt5.QtWidgets import *
from krita import *

class MyDocker(DockWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("My Docker")
        
        # IMPORTANT: Set up main widget
        mainWidget = QWidget(self)
        self.setWidget(mainWidget)
        mainWidget.setLayout(QVBoxLayout())
        
        # Add widgets to mainWidget
        button = QPushButton("Export Document", mainWidget)
        mainWidget.layout().addWidget(button)
        
        # Required method
    def canvasChanged(self, canvas):
        pass

# Register with Krita
Krita.instance().addDockWidgetFactory(
    DockWidgetFactory("myDocker", DockWidgetFactoryBase.DockRight, MyDocker)
)
```

### Key Points from Official Docs

1. **Use `setWidget()` with a `QWidget` child** - The DockWidget expects a child widget
2. **Call `setLayout()` on the child widget** - Not on the DockWidget itself
3. **`canvasChanged()` method is required** - Even if you don't use it
4. **Use `DockWidgetFactory`, not `DockWidgetFactoryBase`** - The base class is abstract
5. **Factory function takes the class itself** - Not a function that returns an instance

### Desktop File Pattern

```ini
[Desktop Entry]
Type=Service
ServiceTypes=Krita/PythonPlugin
X-KDE-Library=pluginname
X-Python-2-Compatible=false
Name=Plugin Name
Comment=Description
X-KDE-PluginInfo-Author=Your Name
X-KDE-PluginInfo-Version=1.0.0
X-Krita-Manual=MANUAL.html
```

## Issues Found in Lumina

1. ❌ Layout set on SphereDocker instead of main widget
2. ❌ Missing `setWidget()` call
3. ❌ Using `show()` on child widget (may work but not standard)
4. ❌ Using `setVisible()` instead of proper visibility management

## Correct Pattern for Lumina

```python
class SphereDocker(DockWidget):
    def __init__(self):
        super().__init__()
        
        # Create main widget
        mainWidget = QWidget()
        mainWidget.setStyleSheet("QWidget { background-color: #141419; }")
        self.setWidget(mainWidget)
        
        # Create layout on mainWidget
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(4, 4, 4, 4)
        
        # Add sphere container
        self._sphere_container = SpherePaintWidget(self)
        self._sphere_container.setFixedSize(200, 200)
        
        # Build control panel
        # ...
        
        main_layout.addLayout(sphere_layout, 1)
        main_layout.addLayout(ctrl_layout)
        
        mainWidget.setLayout(main_layout)
        
        # Required method
    def canvasChanged(self, canvas):
        pass
```

# Official Krita Documentation

Full documentation available at: https://docs.krita.org/en/user_manual/python_scripting/krita_python_plugin_howto.html
