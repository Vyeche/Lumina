"""__init__.py — Factory registration entry point for the Lumina plugin.

Krita loads this package and calls :func:`create_plugin_widget` to obtain the
main docker widget.  Everything else lives in ``sphere_docker.py`` so this file
stays a thin, dependency-light entry point (no Qt imports at module top level).
"""

from .sphere_docker import SphereDocker


def create_plugin_widget():
    """Public entry point Krita invokes to build the plugin's main widget."""
    return SphereDocker()
