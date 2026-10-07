"""typed_entry.py -- shared typed-value parsing for slider readouts.

Pure Python: no Qt, no Krita, no docker imports. Both the main/Advanced
slider rows (``sphere_docker.LabeledSliderRow``) and the gear popup
(``color_controls.SettingsPanel``) parse typed values through exactly one
implementation, so the two can never drift apart again.

Unit model: every field declares one of ``"percent"`` / ``"degree"`` /
``"none"``. Display is always canonical while input stays permissive.
``%`` is accepted on percent *and* unitless fields (percent-of-range);
the degree mark (and ``deg``) only on degree fields.
"""

import math
from dataclasses import dataclass
from typing import Optional

DEGREE_MARK = "\u00b0"

PERCENT = "percent"
DEGREE = "degree"
NONE = "none"

_VALID_UNITS = (PERCENT, DEGREE, NONE)


@dataclass(frozen=True)
class TypedParseResult:
    """Outcome of parsing one typed readout string against [lo, hi]."""

    value: Optional[float]
    valid: bool
    was_percent: bool
    was_clamped: bool


def _strip_suffix(raw: str):
    """Split trailing unit markers. Returns (numeric_text, suffix)."""
    text = raw.strip()
    lowered = text.lower()
    for suffix in ("deg", DEGREE_MARK):
        if lowered.endswith(suffix):
            return text[: -len(suffix)].strip(), suffix
    if text.endswith("%"):
        return text[:-1].strip(), "%"
    return text, ""


def parse_typed_entry(text, lo: int, hi: int,
                      unit: str = PERCENT) -> TypedParseResult:
    """Parse ``text`` as a raw number, percent, or degree angle.

    Accepted forms depend on ``unit``:

    - ``"percent"``: ``"86"``, ``"86%"`` (percent-of-range).
    - ``"degree"``: ``"86"``, ``"86°"``, ``"86deg"`` (raw angle);
      ``"86%"`` is still percent-of-range.
    - ``"none"``: ``"64"`` raw, ``"64%"`` percent-of-range; any other
      suffix (degree mark, ``deg``) is invalid.

    Empty, lone-suffix, non-numeric, and non-finite (``nan``/``inf``) input
    is invalid -- the caller reverts to the last committed value. Numerics
    (including scientific notation) clamp into range with ``was_clamped``.
    """
    if unit not in _VALID_UNITS:
        raise ValueError("unknown unit: %r" % (unit,))
    try:
        raw = text.strip()
    except (AttributeError, TypeError):
        return TypedParseResult(None, False, False, False)

    if not raw:
        return TypedParseResult(None, False, False, False)

    numeric, suffix = _strip_suffix(raw)
    if suffix == "%":
        is_percent = True
    elif suffix in (DEGREE_MARK, "deg"):
        if unit != DEGREE:
            return TypedParseResult(None, False, False, False)
        is_percent = False
    elif suffix:
        return TypedParseResult(None, False, False, False)
    else:
        is_percent = False

    if not numeric:
        return TypedParseResult(None, False, is_percent, False)

    try:
        value = float(numeric)
    except (TypeError, ValueError):
        return TypedParseResult(None, False, is_percent, False)

    if not math.isfinite(value):
        return TypedParseResult(None, False, is_percent, False)

    if is_percent:
        value = lo + (hi - lo) * (value / 100.0)

    clamped = max(lo, min(hi, value))
    return TypedParseResult(
        value=clamped,
        valid=True,
        was_percent=is_percent,
        was_clamped=(clamped != value),
    )


def format_slider_value(v, unit: str = PERCENT) -> str:
    """Canonical display text: ``86%``, ``323°``, or ``64``."""
    if unit is True:  # legacy bool callers
        unit = PERCENT
    elif unit is False:
        unit = NONE
    if unit == DEGREE:
        return "%d%s" % (int(v), DEGREE_MARK)
    if unit == PERCENT:
        return "%d%%" % int(v)
    return str(int(v))
