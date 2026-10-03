"""tooltip.py -- Opaque rich-text tooltips for the Lumina docker (issue #20).

Plain ``setToolTip("text")`` renders as white text with no background in
Krita (flatpak / Breeze): the tooltip is its own top-level window, so the
docker's stylesheet and palette never reach it, and Krita's style paints the
``QTipLabel`` with translucency that neither the ``ToolTipBase``/``ToolTipText``
palette roles nor an appended ``QToolTip`` app-stylesheet rule overrides
(all five approaches tried in #20 failed).

What survives the style is an *inline* background inside the tooltip's own
document: Qt renders tooltip text through ``QTextDocument``, so a ``<div>``
with ``background-color`` paints opaquely whatever the outer ``QTipLabel``
box does. Every tooltip therefore goes through :func:`set_tooltip`, which
wraps the text in such a div. The palette/stylesheet passes in
``sphere_docker._install_tooltip_style`` are kept as a fallback only.
"""

import html

TOOLTIP_BG = "#000000"
TOOLTIP_FG = "#ffffff"
TOOLTIP_BORDER = "#9aa3b4"


def tooltip_html(text: str) -> str:
    """Wrap plain tooltip text in an opaque rich-text div.

    The text is HTML-escaped first, so ``<``, ``&`` etc. in a caption can
    never break the markup or inject tags. The ``<div>`` prefix is what
    flips Qt into rich-text mode; without an HTML tag the tooltip stays
    plain text and the transparent box comes back.

    ``margin: 0`` matters: ``QTextDocument`` adds a default document margin
    around the div, and the outer ``QTipLabel`` background (white under
    Breeze when the style ignores the palette) showed through that gap as
    a white ring between the border and the black div. Zeroing the div
    margin and keeping the outer ``QToolTip`` background black removes it;
    the visual breathing room comes from the div's own padding instead.

    Padding rides on a single-cell ``<table>`` with ``cellpadding`` rather
    than the div alone: Qt's rich-text engine honors table cell padding
    reliably, while ``div`` padding was silently ignored (bumping it from
    8px to 10px changed nothing on screen). The ``td`` keeps an inline
    padding style as a backup; the background is set on both table and
    cell so no outer white shows through.
    """
    escaped = html.escape(str(text), quote=True)
    return (
        "<table style='background-color: %s; color: %s; margin: 0;' "
        "cellpadding='10' cellspacing='0' border='0'><tr>"
        "<td style='background-color: %s; color: %s; "
        "padding: 10px 12px; white-space: pre-wrap;'>%s</td>"
        "</tr></table>"
        % (TOOLTIP_BG, TOOLTIP_FG, TOOLTIP_BG, TOOLTIP_FG, escaped)
    )


def set_tooltip(widget, text: str) -> None:
    """Set an opaque tooltip on any widget (issue #20 fix)."""
    widget.setToolTip(tooltip_html(text))
