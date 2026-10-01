"""Comprehensive structural smoke tests for the Lumina plugin (V2 modular layout).

Checks that each module compiles, defines its expected class, and keeps the
error-handling / widget-hierarchy invariants Krita requires. Pure text +
py_compile checks — no Qt or Krita needed, so it runs on host and CI.
"""
import os
import re
import py_compile
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _read(name):
    with open(os.path.join(HERE, name), 'r') as f:
        return f.read()


MODULES = [
    'color_engine.py',
    'color_processor.py',
    'color_controls.py',
    'sphere_widget.py',
    'sphere_docker.py',
]

# class -> file it must be defined in (V2 modular architecture)
EXPECTED_CLASSES = {
    'class ColorEngine': 'color_engine.py',
    'class SphereColorProcessor': 'color_processor.py',
    'class ColorSlider': 'color_controls.py',
    'class SphereWidget(QWidget)': 'sphere_widget.py',
    'class SphereDocker(QDockWidget)': 'sphere_docker.py',
}


def test_file_compiles():
    """Every plugin module compiles without syntax errors."""
    ok = True
    for name in MODULES:
        try:
            py_compile.compile(os.path.join(HERE, name), doraise=True)
            print(f"✓ {name} compiles")
        except py_compile.PyCompileError as e:
            print(f"✗ {name}: {e}")
            ok = False
    return ok


def test_imports_correct():
    """Qt modules import PyQt5; the engine stays Qt-free (pure Python)."""
    ok = True
    for name in ('sphere_widget.py', 'sphere_docker.py', 'color_controls.py'):
        content = _read(name)
        for imp in ('from PyQt5.QtWidgets import', 'from PyQt5.QtGui import'):
            if imp in content:
                print(f"✓ {name}: {imp}")
            else:
                print(f"✗ {name}: missing {imp}")
                ok = False
    engine = _read('color_engine.py')
    # Only real import statements count — docstrings/comments may mention PyQt5/krita.
    bad_imports = [
        l.strip() for l in engine.splitlines()
        if (l.strip().startswith(('import ', 'from ')) and ('PyQt5' in l or 'krita' in l))
    ]
    if not bad_imports:
        print("✓ color_engine.py: pure Python (no Qt/Krita imports)")
    else:
        print(f"✗ color_engine.py: must stay free of Qt/Krita imports: {bad_imports}")
        ok = False
    return ok


def test_class_definitions():
    """Each expected class is defined in its designated module."""
    ok = True
    for cls, fname in EXPECTED_CLASSES.items():
        if cls in _read(fname):
            print(f"✓ {cls} in {fname}")
        else:
            print(f"✗ missing {cls} in {fname}")
            ok = False
    return ok

def test_init_methods_exist():
    """Every widget/processor class defines __init__."""
    ok = True
    for fname in ('color_processor.py', 'color_controls.py', 'sphere_widget.py', 'sphere_docker.py'):
        content = _read(fname)
        # Only classes that subclass something (Qt widgets etc.) are required to define __init__;
        # plain helper/constant/data namespaces legitimately may not.
        widget_classes = sum(1 for line in content.splitlines() if re.match(r'^class \w+\(', line))
        inits = content.count('def __init__')
        if widget_classes == 0:
            print(f"✓ {fname}: no subclassed widgets requiring __init__")
        elif inits >= widget_classes:
            print(f"✓ {fname}: {inits} __init__ for {widget_classes} class(es)")
        else:
            print(f"✗ {fname}: {inits} __init__ < {widget_classes} classes")
            ok = False
    return ok


def test_try_except_structure():
    """The docker keeps its error-handling wrappers (Krita hides tracebacks)."""
    content = _read('sphere_docker.py')
    lines = content.split('\n')
    try_count = sum(1 for l in lines if 'try:' in l and not l.strip().startswith('#'))
    except_count = sum(1 for l in lines if 'except Exception' in l or 'except AttributeError' in l)
    print(f"\nFound {try_count} 'try:' blocks, {except_count} 'except' blocks")
    return try_count >= 2 and except_count >= 2


def test_super_init_present():
    """Widget subclasses call super().__init__() (Qt object setup)."""
    ok = True
    for fname in ('sphere_widget.py', 'sphere_docker.py'):
        # super().__init__(parent) is valid Qt practice — accept any argument list.
        if 'super().__init__' in _read(fname):
            print(f"✓ {fname}: super().__init__() present")
        else:
            print(f"✗ {fname}: super().__init__() missing")
            ok = False
    return ok


def test_dockwidget_import():
    """SphereDocker subclasses QDockWidget imported from PyQt5.QtWidgets."""
    content = _read('sphere_docker.py')
    if 'from PyQt5.QtWidgets import' in content and 'QDockWidget' in content:
        print("✓ QDockWidget imported for SphereDocker")
        return True
    print("✗ QDockWidget not properly imported in sphere_docker.py")
    return False


def main():
    """Run all tests."""
    print("=" * 60)
    print("LUMINA PLUGIN STRUCTURAL TESTS (V2 modular)")
    print("=" * 60)

    tests = [
        ("File Compiles", test_file_compiles),
        ("Imports Correct", test_imports_correct),
        ("Class Definitions", test_class_definitions),
        ("Init Methods Exist", test_init_methods_exist),
        ("Try/Except Structure", test_try_except_structure),
        ("Super Init Present", test_super_init_present),
        ("DockWidget Import", test_dockwidget_import),
    ]

    results = []
    for name, test_func in tests:
        print(f"\n--- {name} ---")
        result = test_func()
        results.append((name, result))
        print()

    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)

    passed = sum(1 for _, r in results if r)
    total = len(results)

    for name, result in results:
        status = "PASS" if result else "FAIL"
        symbol = "✓" if result else "✗"
        print(f"{symbol} {name}: {status}")

    print()
    print(f"Results: {passed}/{total} tests passed")

    if passed == total:
        print("\n🎉 All tests passed! Plugin is ready for Krita.")
        return 0
    print(f"\n⚠️  {total - passed} test(s) failed. Fix before running in Krita.")
    return 1


if __name__ == '__main__':
    sys.exit(main())
