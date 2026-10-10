"""derivation.py -- light and shadow targets derived from one base colour.

Pure Python: no Qt, no Krita. The docker calls :func:`derive` when a pick
lands on the base; the stdlib test suite and ``tools/reference_calibration.py``
call it directly.

The model works in OKLCH (perceptual lightness, chroma, hue), because that is
where the reference lighting app's behaviour turned out to be regular. Measured
on its own screens (the Ref Tester document), for seven base colours:

* the **light** sits a fixed ~0.18 *lighter* (OK lightness) than the base, and
  its hue swings toward a warm key light near 35 degrees by up to ~55 degrees,
  so blue lights turn magenta, greens turn cream and reds turn peach;
* the **shadow** sits a fixed ~0.18 *darker*, and its hue leans toward a cool
  ambient near 265 degrees -- strongly for greens and magentas, not at all for
  blues (already there) or for reds and oranges, whose shadows stay warm.

A fixed lightness step is what the old HSV rule could not express: it scaled
value, so a bright pick barely moved and a dark one collapsed.

Colours are 8-bit (r, g, b) in whatever RGB space the caller works in -- Lumina
passes the active layer's numbers -- and the OKLCH conversion uses the sRGB
transfer curve, which Display P3 shares.

Invariants kept from the earlier rule, and tested:

* the shadow is darker than the base and the light lighter;
* a grey derives greys (no chroma is invented);
* a tint of black is black: pure black derives black light and black shadow;
* a dark base still gets a separable shadow, never pure black.

Results leave the RGB gamut only by reducing chroma at fixed lightness and hue,
never by clipping channels, which would shift the hue.
"""

import math

# --- Tuning constants (fitted by tools/reference_calibration.py) ------------
# Light: lightness lift, the warm key hue it swings toward and the cap on the
# swing, and chroma as gain * C + offset (the offset fades out for greys).
LIGHT_LIFT = 0.174
KEY_HUE = 37.3
LIGHT_HUE_CAP = 56.7
LIGHT_C_GAIN = 0.516
LIGHT_C_ADD = 0.073

# Shadow: lightness drop, never below this share of the base (so a dark base
# keeps a separable shadow), the cool ambient hue and the strength of the lean
# toward it, and chroma as gain * C + offset.
SHADOW_DROP = 0.179
SHADOW_MIN_RATIO = 0.45
AMBIENT_HUE = 258.9
SHADOW_HUE_SWING = 47.6
SHADOW_C_GAIN = 0.428
SHADOW_C_ADD = 0.076

# Warm hues whose shadows do not lean toward the ambient (red, orange,
# yellow): full exclusion between the inner edges, a smooth fade to the outer.
WARM_INNER = (15.0, 85.0)
WARM_OUTER = (0.0, 110.0)

# Chroma band over which a colour stops counting as grey: below the low edge
# no chroma offset or hue swing applies (greys derive greys, and near-greys are
# never made more colourful than the pick); above the high edge, all of it.
GREY_CHROMA = (0.03, 0.08)
# Below this lightness the lift fades to zero: a tint of black is black.
BLACK_KNEE = 0.15


# --- OKLab -------------------------------------------------------------------
def _to_linear(c8):
    c = c8 / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _to_encoded(c):
    c = max(0.0, min(1.0, c))
    v = 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055
    return int(round(v * 255.0))


def _rgb_to_oklab(rgb):
    r, g, b = (_to_linear(v) for v in rgb)
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l, m, s = (math.copysign(abs(v) ** (1.0 / 3.0), v) for v in (l, m, s))
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def _oklab_to_linear(L, a, b):
    l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
            -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
            -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)


def to_oklch(rgb):
    """8-bit (r, g, b) -> (L, C, h degrees)."""
    L, a, b = _rgb_to_oklab(rgb)
    return L, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360.0


def from_oklch(L, C, h):
    """(L, C, h) -> 8-bit (r, g, b), reducing chroma until it is in gamut."""
    L = max(0.0, min(1.0, L))
    rad = math.radians(h)

    def linear(c):
        return _oklab_to_linear(L, c * math.cos(rad), c * math.sin(rad))

    def inside(rgb):
        return all(-1e-6 <= v <= 1.0 + 1e-6 for v in rgb)

    if not inside(linear(C)):
        lo, hi = 0.0, C
        for _ in range(24):
            mid = (lo + hi) / 2.0
            if inside(linear(mid)):
                lo = mid
            else:
                hi = mid
        C = lo
    return tuple(_to_encoded(v) for v in linear(C))


# --- The model ---------------------------------------------------------------
def _short(delta):
    """Signed shortest angle, degrees in [-180, 180)."""
    return (delta + 180.0) % 360.0 - 180.0


def _smooth(edge0, edge1, x):
    t = max(0.0, min(1.0, (x - edge0) / (edge1 - edge0)))
    return t * t * (3.0 - 2.0 * t)


def warm_exclusion(h):
    """1 where a shadow may lean toward the ambient, 0 for warm hues."""
    if WARM_INNER[0] <= h <= WARM_INNER[1]:
        return 0.0
    if WARM_OUTER[0] <= h < WARM_INNER[0]:
        return 1.0 - _smooth(WARM_OUTER[0], WARM_INNER[0], h)
    if WARM_INNER[1] < h <= WARM_OUTER[1]:
        return _smooth(WARM_INNER[1], WARM_OUTER[1], h)
    return 1.0


def derive_lch(L, C, h):
    """Derived (shadow, light) as (L, C, h) triples, before gamut mapping."""
    grey = _smooth(GREY_CHROMA[0], GREY_CHROMA[1], C)

    lift = LIGHT_LIFT * min(1.0, L / BLACK_KNEE)
    light_l = min(1.0, L + lift)
    swing = max(-LIGHT_HUE_CAP, min(LIGHT_HUE_CAP, _short(KEY_HUE - h)))
    light_h = (h + swing * grey) % 360.0
    light_c = LIGHT_C_GAIN * C + LIGHT_C_ADD * grey

    shadow_l = max(L - SHADOW_DROP, L * SHADOW_MIN_RATIO)
    lean = SHADOW_HUE_SWING * math.sin(math.radians(_short(AMBIENT_HUE - h)))
    shadow_h = (h + lean * warm_exclusion(h) * grey) % 360.0
    shadow_c = SHADOW_C_GAIN * C + SHADOW_C_ADD * grey

    return (shadow_l, shadow_c, shadow_h), (light_l, light_c, light_h)


def derive(rgb):
    """8-bit base (r, g, b) -> (shadow, light) as 8-bit (r, g, b) tuples."""
    rgb = tuple(int(v) for v in rgb[:3])
    if rgb == (0, 0, 0):
        return (0, 0, 0), (0, 0, 0)
    shadow, light = derive_lch(*to_oklch(rgb))
    shadow_rgb = from_oklch(*shadow)
    if shadow_rgb == (0, 0, 0):
        # A near-black pick (#050505) has a shadow that rounds to pure black
        # in 8 bits. Keep it one step down from the base instead, so the
        # shadow stays separable: darker, and still not black.
        shadow_rgb = tuple(max(0, c - max(1, c // 2)) if c else 0 for c in rgb)
    return shadow_rgb, from_oklch(*light)


# --- Perceptual slider model -------------------------------------------------
# Lumina's Hue / Sat / Light sliders use OKLCH, like the reference app's: hue
# in degrees, lightness 0..1, and chroma *relative* to the most vivid colour
# the RGB gamut holds at that lightness and hue (so 100% is "fully vivid"
# whatever the hue -- raw chroma would top out at different values per hue).

def _in_gamut(L, C, h):
    rad = math.radians(h)
    rgb = _oklab_to_linear(L, C * math.cos(rad), C * math.sin(rad))
    return all(-1e-6 <= v <= 1.0 + 1e-6 for v in rgb)


def max_chroma(L, h):
    """Largest in-gamut OKLCH chroma at lightness ``L`` and hue ``h``."""
    if L <= 0.0 or L >= 1.0:
        return 0.0
    lo, hi = 0.0, 0.5
    for _ in range(22):
        mid = (lo + hi) / 2.0
        if _in_gamut(L, mid, h):
            lo = mid
        else:
            hi = mid
    return lo


_CUSPS = {}


def cusp(h):
    """(L, C) of the most saturated in-gamut colour at hue ``h``: pure red,
    yellow, blue... Used to paint the hue track at full strength; a fixed
    lightness washes out the hues whose vivid colour sits far from it."""
    key = round(h % 360.0, 1)
    hit = _CUSPS.get(key)
    if hit is None:
        lo, hi = 0.05, 0.999
        for _ in range(40):                 # max_chroma(L) peaks once per hue
            m1 = lo + (hi - lo) / 3.0
            m2 = hi - (hi - lo) / 3.0
            if max_chroma(m1, key) < max_chroma(m2, key):
                lo = m1
            else:
                hi = m2
        L = (lo + hi) / 2.0
        hit = _CUSPS[key] = (L, max_chroma(L, key))
    return hit


def to_slider(rgb):
    """8-bit (r, g, b) -> (hue degrees, relative chroma 0..1, lightness 0..1)."""
    L, C, h = to_oklch(rgb)
    cmax = max_chroma(L, h)
    rel = min(1.0, C / cmax) if cmax > 1e-6 else 0.0
    return h, rel, L


def from_slider(h, rel, L):
    """(hue degrees, relative chroma 0..1, lightness 0..1) -> 8-bit (r, g, b)."""
    L = max(0.0, min(1.0, L))
    rel = max(0.0, min(1.0, rel))
    return from_oklch(L, rel * max_chroma(L, h), h % 360.0)
