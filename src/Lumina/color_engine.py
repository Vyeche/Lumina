"""color_engine.py — Pure-Python lighting/shading engine (no Qt, no Krita).

This module is the *heart* of Lumina. It contains ZERO dependencies on
PyQt5 or the ``krita`` module, so it can be unit-tested anywhere: on the host,
in CI, inside the Krita flatpak, or in any Python 3 interpreter.

The engine models a single directional light striking a hemisphere and returns
per-pixel RGB triples (0-255). It implements an extended Phong model:

    C = base + diffuse*base*light_color*intensity     # light-tinted diffuse
      + ambient_color * ambient                       # ambient fill
      + specular_highlight                             # Phong specular
      then tone-mapped (contrast) + saturation-adjusted

Coordinate convention (matches the original engine exactly):
    The sphere is drawn in an image where the left edge is the fully shaded
    side and the right edge is the fully lit side. For each pixel we place it on
    a unit hemisphere:

        x = -1 + 2 * col / (n-1)     # horizontal, -1 .. +1
        y = -1 + 2 * row / (n-1)     # vertical,   -1 .. +1
        z = sqrt(max(0, 1 - x*x - y*y))

    Surface normal N = normalize(x, y, z)  (the hemisphere surface faces outward).
"""

import math
from typing import List, Sequence, Tuple

# A color is a plain (r, g, b) triple of ints in 0..255. Keeping it as tuples
# means this module never imports QImage/QColor and stays framework-agnostic.
RGB = Tuple[int, int, int]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _clamp(value: float, lo: int = 0, hi: int = 255) -> int:
    """Clamp ``value`` into the inclusive integer range [lo, hi]."""
    if value < lo:
        return lo
    if value > hi:
        return hi
    return int(round(value))


def _rgb_from_floats(r: float, g: float, b: float) -> RGB:
    """Convert normalized (0..1) floats to clamped 0..255 ints."""
    return (
        min(255, max(0, round(r * 255))),
        min(255, max(0, round(g * 255))),
        min(255, max(0, round(b * 255))),
    )


def _lerp(a: float, b: float, t: float) -> float:
    """Linear interpolation between ``a`` and ``b`` at parameter ``t``."""
    return a + (b - a) * t


# ---------------------------------------------------------------------------
# Shading engine
# ---------------------------------------------------------------------------
SHADOW_FALLOFF = 1.35    # shadow blend exponent; higher = shadow stays local
DIFFUSE_FLOOR = 0.12     # dimmest the unlit side gets from the diffuse term
DIFFUSE_GAMMA = 0.55     # <1 lifts the midtones so the base colour is reached
TINT_STRENGTH = 0.55     # how far the lit side is tinted toward the light colour
SPEC_MAX = 0.50          # ceiling on the specular: a broad wash, not a hotspot
# The knee is exposed per-engine (ColorEngine.spec_knee) so the UI can drive it.
# These are the two ends of the "Diffuse" slider's range: a low knee spreads the
# sheen, a high knee keeps it tight.
SPEC_KNEE_MIN = 0.10     # slider at 100 -- very diffuse
SPEC_KNEE_MAX = 1.00     # slider at 0   -- tight, close to the old clamp
# The ceiling is applied as a soft shoulder rather than a hard clamp. Clamping
# made every pixel whose raw sheen exceeded SPEC_MAX sit at exactly the ceiling,
# which produced a flat-topped plateau ~900px across at the default shininess
# and a hard step where the plateau ended -- the highlight read as a disc laid
# on the sphere rather than as a sheen. This rational curve
#
#     spec = SPEC_MAX * s * (k + 1) / (s + k)
#
# passes through the same origin and the same peak (s=1 -> SPEC_MAX) but never
# saturates, so there is no plateau and no discontinuity in the gradient. It
# also redistributes the plateau's energy outward, widening the sheen without
# changing overall brightness. k controls the shoulder: smaller k is a more
# aggressive rolloff. Swept against the 256px render, k=0.35 holds the peak at
# 0.50 while the flat core falls from ~900px to ~55px and the >=50% area grows
# from 12.7% to 14.1% of the disc, with mean sheen within 3% of the old value.
SPEC_KNEE = 0.35
# --- Rim / Fresnel ---------------------------------------------------------
# The Schlick approximation gives R = R0 + (1 - R0)(1 - cos(theta))^5. Viewed
# head-on, cos(theta) is the surface normal's z, and z runs from 1 at the centre
# of the disc to 0 at the silhouette, so the rim term is simply (1 - z)^5. The
# exponent of 5 is the standard Fresnel falloff, not a free parameter: it is
# what keeps the effect confined to a narrow band at the edge instead of
# washing across the whole sphere.
RIM_POWER = 5.0
# Measured: below about 0.60 the edge is still darker than the band just inside
# it, so the sphere keeps its dark outline. 0.65 is the first value at which the
# silhouette reads as lit and the shadow pools inside the form.
# 0.65 was enough to beat the interior but it read as a glowing outline drawn
# round the whole sphere, which the reference does not have -- there the edge
# brightness simply follows the light. Kept low, just enough to keep the
# silhouette from going darker than the shadow pooled inside it.
RIM_LIGHT = 0.22         # additive rim, tinted by the highlight colour
# Zero. Suppressing the shadow toward the silhouette to make the edge read as
# lit produced a dark band just inside a lighter rim, because the shadow weight
# is largest exactly at the silhouette and lifting it there only moved the
# minimum inward. The reference has no such band.
RIM_SHADOW_LIFT = 0.0


class ColorEngine:
    """Stateless-ish lighting engine.

    The constructor sets sane defaults; the widget layer mutates the public
    attributes (or calls the ``set_*`` helpers) and then calls
    :meth:`render` to get fresh pixel data.

    Attributes / set_* methods mirror the historical Krita plugin API so the
    widget code can be written once and work against either engine instance.
    """

    def __init__(self, resolution: int = 256):
        self.resolution = max(8, min(resolution, 512))

        # --- Lighting state -------------------------------------------------
        self.ambient = 0.1                      # 0.0 - 1.0
        self.light_azimuth_deg = 300.0          # degrees, 0..360
        self.light_elevation_rad = math.pi / 3  # radians, 0..pi/2
        # Specular sharpness. Measured on the rendered disc, the highlight's
        # area scales roughly as 1/shininess: 30 -> 223px, 14 -> 468px, 10 -> 651px
        # against a disc of ~26,500px. At 30 the highlight covered under 1% of the
        # sphere and read as a small white dot; the reference is a broad soft wash
        # with no distinct hotspot at all, which needs a far lower exponent still.
        # Squared after the power, so the effective exponent is 2 * this. 4 was
        # still rl**8 and read as a tight lobe; the reference spreads its
        # highlight across a large part of the upper hemisphere.
        self.shininess = 1.6                     # specular sharpness
        # Specular shoulder. See the SPEC_KNEE comment above; the UI's "Diffuse"
        # slider maps onto this. 0.35 is the measured sweet spot.
        self.spec_knee = SPEC_KNEE

        # --- Color state (normalized 0..1) ----------------------------------
        # The ambient fill is deliberately neutral. It is a flat lift added to
        # every pixel, so a blue-biased tint does not merely cool the shadows --
        # in the dark areas, where the base colour has almost no strength left,
        # that lift is most of what is there and it took over entirely: the
        # shadow of an orange sphere came out blue-violet, with blue-minus-red
        # reaching +0.11. Keeping it neutral lets the derived shadow colour show
        # through instead.
        self.ambient_color = (0.88, 0.88, 0.88)
        self.shadow_color = (0.1569, 0.1569, 0.2353)    # ~ (40,40,60)
        self.highlight_color = (1.0, 1.0, 1.0)           # white specular
        self.light_color = (1.0, 1.0, 1.0)             # tint of the lit side (white = neutral)

        # --- Intensity / effect state --------------------------------------
        self.light_intensity = 1.0               # 0.0 - 2.0
        self.contrast = 1.0                       # 1.0 = neutral
        self.brightness = 1.0                     # 0.0 - 2.0
        self.saturation = 1.0                     # 0.0 - 2.0

        # --- Mixer / glow ---------------------------------------------------
        self.mixer_mode = "Blended"               # Additive | Multiplicative | Blended
        self.glow_intensity = 0.0                 # 0.0 - 1.0
        self.glow_radius = 10.0                   # 1 - 30

        # Recompute the cached geometry + shading on construction.
        self._generate_normal_grid()
        self._compute_shading()

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------
    def _generate_normal_grid(self) -> None:
        """Pre-compute unit surface normals for the visible hemisphere."""
        n = self.resolution
        step = 2.0 / (n - 1) if n > 1 else 2.0
        normals: List[List[Tuple[float, float, float]]] = []
        masks: List[List[bool]] = []
        for row in range(n):
            nrow: List[Tuple[float, float, float]] = []
            mrow: List[bool] = []
            y = -1.0 + row * step
            for col in range(n):
                x = -1.0 + col * step
                r2 = x * x + y * y
                inside = r2 <= 1.0
                if inside and r2 > 0:
                    z = math.sqrt(1.0 - r2)
                    length = math.sqrt(r2 + z * z)
                    nrow.append((x / length, y / length, z / length))
                else:
                    nrow.append((0.0, 0.0, 0.0))
                mrow.append(inside)
            normals.append(nrow)
            masks.append(mrow)
        self._normal_grid = normals
        self._mask_grid = masks
        # Rim proximity: 0 at the centre of the disc, rising to 1 at the
        # silhouette. Depends only on geometry, so it is baked once here rather
        # than recomputed per pixel. z is 1 dead centre and 0 at the edge, so
        # 1 - z is exactly "how close to the outline is this".
        rim: List[List[float]] = []
        for row in range(n):
            rrow: List[float] = []
            for col in range(n):
                if not masks[row][col]:
                    rrow.append(0.0)
                else:
                    rrow.append((1.0 - normals[row][col][2]) ** RIM_POWER)
            rim.append(rrow)
        self._rim_grid = rim
        self._coverage_grid = self._build_coverage_grid()

    def _build_coverage_grid(self) -> List[List[float]]:
        """Per-pixel fraction of the pixel that lies inside the silhouette.

        The mask is a hard in/out test, so a sphere built from it has a 1-bit
        edge: every pixel is either fully opaque or fully gone, and the
        staircase of those steps is what read as a choppy, jagged rim.

        Because the silhouette is a circle centred on the origin, the exact
        perpendicular distance from a pixel centre at radius ``r`` to the
        boundary is simply ``1 - r``, independent of the angle. Dividing that by
        the pixel pitch gives the coverage, which is 1.0 well inside, 0.0 well
        outside, and fractional in a one-pixel band at the rim. Feeding that into
        the alpha channel lets Qt blend the edge instead of stepping it.
        """
        n = self.resolution
        step = 2.0 / (n - 1) if n > 1 else 2.0
        half = step * 0.5
        grid: List[List[float]] = []
        clamp = lambda v: 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)
        for row in range(n):
            rrow: List[float] = []
            y = -1.0 + row * step
            for col in range(n):
                x = -1.0 + col * step
                r = math.sqrt(x * x + y * y)
                rrow.append(clamp((1.0 - r + half) / step))
            grid.append(rrow)
        return grid

    def coverage_grid(self) -> List[List[float]]:
        """Per-pixel silhouette coverage in 0..1 (1 = fully inside)."""
        return self._coverage_grid

    def _light_direction(self) -> Tuple[float, float, float]:
        """Return the unit light direction vector."""
        le = self.light_elevation_rad
        la = math.radians(self.light_azimuth_deg)
        lx = math.cos(le) * math.cos(la)
        ly = math.cos(le) * math.sin(la)
        lz = math.sin(le)
        length = math.sqrt(lx * lx + ly * ly + lz * lz)
        if length == 0:
            return (0.0, 0.0, 1.0)
        return (lx / length, ly / length, lz / length)

    def _compute_shading(self) -> None:
        """Pre-compute every per-pixel term that depends only on the light.

        Three things are baked here instead of being recomputed 40k times per
        render: the diffuse term, the shadow blend weight ``(1-d)^0.6``, and the
        normalized reflection vector used by the specular highlight. The last
        two only depend on the light direction, so they stay valid until the
        light moves -- which keeps slider drags responsive.
        """
        lx, ly, lz = self._light_direction()
        n = self.resolution
        grid: List[List[float]] = []
        shadow_grid: List[List[float]] = []
        refl_grid: List[List[Tuple[float, float]]] = []
        max_ = max
        sqrt = math.sqrt
        for row in range(n):
            grow: List[float] = []
            srow: List[float] = []
            rrow: List[Tuple[float, float]] = []
            nrow = self._normal_grid[row]
            for col in range(n):
                nx, ny, nz = nrow[col]
                diffuse = max_(0.0, nx * lx + ny * ly + nz * lz)
                if diffuse > 1.0:
                    diffuse = 1.0
                grow.append(diffuse)
                srow.append((1.0 - diffuse) ** SHADOW_FALLOFF)
                # Reflection of the view vector (assumed to be the normal) about
                # the diffuse-scaled normal, then normalized.
                rx = 2.0 * nx * diffuse - lx
                ry = 2.0 * ny * diffuse - ly
                rz = 2.0 * nz * diffuse - lz
                rlen = sqrt(rx * rx + ry * ry + rz * rz)
                if rlen > 0:
                    inv = 1.0 / rlen
                    rl = (rx * inv) * lx + (ry * inv) * ly + (rz * inv) * lz
                    rrow.append(rl if rl > 0.0 else 0.0)
                else:
                    rrow.append(0.0)
            grid.append(grow)
            shadow_grid.append(srow)
            refl_grid.append(rrow)
        self._diffuse_grid = grid
        self._shadow_grid = shadow_grid
        self._refl_grid = refl_grid

    def mask(self, row: int, col: int) -> bool:
        """True if pixel ``(row, col)`` lies inside the sphere silhouette."""
        return self._mask_grid[row][col]

    def mask_grid(self) -> List[List[bool]]:
        """Return the whole silhouette mask.

        Callers that walk every pixel should take the grid once and index it
        directly rather than calling :meth:`mask` per pixel.
        """
        return self._mask_grid

    # ------------------------------------------------------------------
    # Setters (mirror the historical Krita plugin API)
    # ------------------------------------------------------------------
    def set_light_angle(self, azimuth_deg: float, elevation_rad=None) -> None:
        if elevation_rad is not None:
            self.light_elevation_rad = max(0.0, min(math.pi / 2, elevation_rad))
        self.light_azimuth_deg = max(0.0, min(360.0, azimuth_deg))
        self._compute_shading()

    def set_ambient(self, value: float) -> None:
        self.ambient = max(0.0, min(1.0, value))

    def set_ambient_color(self, color: Sequence[float]) -> None:
        self.ambient_color = tuple(float(c) for c in color)

    def set_shadow_color(self, color: Sequence[float]) -> None:
        self.shadow_color = tuple(float(c) for c in color)

    def set_highlight_color(self, color: Sequence[float]) -> None:
        self.highlight_color = tuple(float(c) for c in color)

    def set_light_color(self, color: Sequence[float]) -> None:
        """Tint applied to the diffuse (lit) side; white = neutral light."""
        self.light_color = tuple(float(c) for c in color)

    def set_light_intensity(self, value: float) -> None:
        self.light_intensity = max(0.0, min(2.0, value))

    def set_shininess(self, value: float) -> None:
        self.shininess = max(1.0, min(128.0, value))

    def set_spec_knee(self, value: float) -> None:
        """Set the specular shoulder strength (see :data:`SPEC_KNEE`).

        Smaller is more diffuse. Clamped to the range the Diffuse slider covers
        so a bad value can never produce a flat plateau or a blown-out hotspot.
        """
        self.spec_knee = max(SPEC_KNEE_MIN, min(SPEC_KNEE_MAX, value))

    def set_contrast(self, value: float) -> None:
        self.contrast = max(0.0, min(3.0, value))

    def set_light_elevation(self, elevation_rad: float) -> None:
        self.light_elevation_rad = max(0.0, min(math.pi / 2, elevation_rad))
        self._compute_shading()

    def set_brightness(self, value: float) -> None:
        self.brightness = max(0.0, min(2.0, value))

    def set_saturation(self, value: float) -> None:
        self.saturation = max(0.0, min(2.0, value))

    def set_mixer_mode(self, mode: str) -> None:
        if mode in ("Additive", "Multiplicative", "Blended"):
            self.mixer_mode = mode

    def set_glow_intensity(self, value: float) -> None:
        self.glow_intensity = max(0.0, min(1.0, value))

    def set_glow_radius(self, value: float) -> None:
        self.glow_radius = max(1.0, min(30.0, value))

    # ------------------------------------------------------------------
    # Rendering (pure Python, returns RGB tuples)
    # ------------------------------------------------------------------
    def render(self, base_color: Sequence[float], width: int, height: int) -> List[List[RGB]]:
        """Render the shaded sphere.

        :param base_color:   (r, g, b) of the object's base/albedo color (0..1).
        :param width:        Output image width in pixels.
        :param height:       Output image height in pixels.
        :return:             ``height`` rows of ``width`` ``(r, g, b)`` triples.
        """
        # Issue #3: grids must match output dimensions. Rebuild if the caller
        # requests a different size than the constructor resolution.
        size = max(width, height)
        if size != self.resolution:
            self.resolution = size
            self._generate_normal_grid()
            self._compute_shading()

        base = tuple(float(c) for c in base_color)
        base_r, base_g, base_b = base
        amb_r, amb_g, amb_b = self.ambient_color
        amb = self.ambient
        sr, sg, sb = self.shadow_color
        hr, hg, hb = self.highlight_color
        lr, lg, lb = self.light_color
        # Normalise the light tint so it carries hue without carrying brightness.
        lmax = lr if lr > lg else lg
        if lb > lmax:
            lmax = lb
        if lmax > 1e-6:
            lr, lg, lb = lr / lmax, lg / lmax, lb / lmax
        lx, ly, lz = self._light_direction()

        # Hoist every per-pixel attribute lookup into a local. Reading self.X 40k
        # times per render dominated the cost and made slider drags feel laggy.
        mask_grid = self._mask_grid
        diffuse_grid = self._diffuse_grid
        shadow_grid = self._shadow_grid
        refl_grid = self._refl_grid
        rim_grid = self._rim_grid
        intensity = self.light_intensity
        shininess = self.shininess
        spec_knee = self.spec_knee
        brightness = self.brightness
        saturation = self.saturation
        mixer_mode = self.mixer_mode
        contrast = self.contrast
        glow_intensity = self.glow_intensity
        has_glow = glow_intensity > 0
        has_contrast = contrast != 1.0
        is_additive = mixer_mode == "Additive"
        is_mult = mixer_mode == "Multiplicative"
        lerp = _lerp
        to_rgb = _rgb_from_floats
        sqrt = math.sqrt
        max_ = max
        min_ = min

        out: List[List[RGB]] = []
        for row in range(height):
            grow: List[RGB] = []
            grow_append = grow.append
            mrow = mask_grid[row]
            drow = diffuse_grid[row]
            srow = shadow_grid[row]
            frow = refl_grid[row]
            rim_row = rim_grid[row]
            for col in range(width):
                if not mrow[col]:
                    grow_append((0, 0, 0))
                    continue

                diffuse = drow[col]

                # --- Base + diffuse -------------------------------------------------
                # Interpolate from a dim base up to the base colour itself, rather
                # than *adding* diffuse on top of the base. The additive form
                # reached 2x the base at full light, so for any base above v=0.5
                # it clipped to pure white across most of the lit hemisphere.
                # The gamma lifts the midtones so the base colour is actually
                # reached across most of the disc. N.L only reaches 1.0 at the
                # single point facing the light, so without it the sphere stayed
                # permanently dimmer than its own base colour and the base read
                # as a tint rather than as the object.
                lit = DIFFUSE_FLOOR + (1.0 - DIFFUSE_FLOOR) * (diffuse ** DIFFUSE_GAMMA) * intensity
                r = base_r * lit
                g = base_g * lit
                b = base_b * lit

                # Tint the lit side toward the light colour, but normalise the tint
                # first. Applied raw it multiplied the base down by its own
                # darkest channel, so the fully lit side still fell short of the
                # base colour and the sphere read as a dark plateau: the median
                # pixel sat at 0.37 against a base of 0.55, and the base colour
                # was barely present anywhere on the disc. Normalising keeps the
                # light's hue and stops it dimming what it is meant to illuminate.
                r = lerp(r, r * lr, TINT_STRENGTH)
                g = lerp(g, g * lg, TINT_STRENGTH)
                b = lerp(b, b * lb, TINT_STRENGTH)

                # --- Shadow color blend (weight baked with the light grid) ---------
                # Pulled back as the surface turns away toward the silhouette.
                # N.L is smallest exactly at the outline, so without this the
                # darkest pixels were the outermost ones and the sphere looked
                # like it had a dark outline drawn round it. The reference pools
                # the shadow *inside* the form and leaves a lighter band at the
                # edge, which is what a real sphere does as the rim gathers light
                # from its surroundings.
                rim = rim_row[col]
                shadow_t = srow[col] * (1.0 - rim * RIM_SHADOW_LIFT)
                r = lerp(r, sr, shadow_t)
                g = lerp(g, sg, shadow_t)
                b = lerp(b, sb, shadow_t)

                # --- Rim light ----------------------------------------------------
                # A soft lift at the silhouette, tinted by the highlight, so the
                # edge reads as a rounded surface turning away rather than as a
                # hard cut-out against the panel.
                if rim:
                    lift = rim * RIM_LIGHT
                    r += hr * lift
                    g += hg * lift
                    b += hb * lift

                # --- Ambient fill ---------------------------------------------------
                if amb:
                    r += amb_r * amb
                    g += amb_g * amb
                    b += amb_b * amb

                # --- Brightness / saturation ---------------------------------------
                if brightness != 1.0:
                    r *= brightness
                    g *= brightness
                    b *= brightness
                if saturation != 1.0:
                    avg = (r + g + b) / 3.0
                    r = avg + (r - avg) * saturation
                    g = avg + (g - avg) * saturation
                    b = avg + (b - avg) * saturation

                # --- Specular highlight (Phong) ------------------------------------
                # The reflection vector depends only on the light, so the grid
                # stores the pre-normalized N.L value; only the shininess power
                # is evaluated here.
                rl = frow[col]
                if rl > 0.0:
                    spec = rl ** shininess
                    # Ease the approach to the highlight. The raw power curve
                    # rises steeply, so the last stretch into the highlight was
                    # an abrupt step rather than a sheen. A smoothstep flattens
                    # the slope at both ends, which makes the highlight arrive
                    # gradually and keeps its edge from reading as a hard rim.
                    # A smoothstep is the wrong easing here: it flattens the
                    # ends but steepens the middle, which sharpened the edge of
                    # the sheen instead of softening it. Squaring biases the
                    # whole falloff toward the lit side, so the highlight spreads
                    # out and fades gradually the way the reference does.
                    spec = spec * spec
                    # Ease the sheen into SPEC_MAX with a soft shoulder instead of
                    # clamping it. A clamp pinned every pixel above the ceiling to
                    # the ceiling, which flattened the core into a ~900px plateau
                    # and left a hard step at its edge, so the highlight read as a
                    # disc sitting on the sphere. This curve hits the same peak at
                    # the same place and spreads the plateau's energy outward, so
                    # the sheen fades gradually with no edge to see.
                    spec = SPEC_MAX * spec * (spec_knee + 1.0) / (spec + spec_knee)
                    r = lerp(r, hr, spec)
                    g = lerp(g, hg, spec)
                    b = lerp(b, hb, spec)

                # --- Mixer mode -----------------------------------------------------
                if is_additive:
                    add = 0.2 * diffuse
                    r = min_(1.0, r + add)
                    g = min_(1.0, g + add)
                    b = min_(1.0, b + add)
                elif is_mult:
                    factor = 0.7 + 0.3 * diffuse
                    r *= factor
                    g *= factor
                    b *= factor

                # --- Contrast (tone mapping) ---------------------------------------
                if has_contrast:
                    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
                    if luma > 0:
                        # Contrast around mid-grey, in exponent form:
                        #     below mid:  0.5 * (2*luma) ** contrast
                        #     above mid:  1 - 0.5 * (2*(1-luma)) ** contrast
                        # Identity at contrast 1, and every pixel collapses to
                        # flat mid-grey as contrast approaches 0.
                        #
                        # The previous version inverted contrast and used it as a
                        # plain gamma exponent, which was wrong twice over. It
                        # divided by contrast, so the slider's own minimum
                        # (contrast 0) raised ZeroDivisionError and took the
                        # docker's render down with it. And it then scaled by
                        # mapped/mapped, which is 1.0 for every input, so
                        # contrast changed nothing at all -- measured as 0
                        # differing pixels across the whole 0.25-3.0 range.
                        # This form has no division, so 0 is simply "flat".
                        if luma < 0.5:
                            mapped = 0.5 * (2.0 * luma) ** contrast
                        else:
                            mapped = 1.0 - 0.5 * (2.0 * (1.0 - luma)) ** contrast
                        scale = mapped / luma
                        r *= scale
                        g *= scale
                        b *= scale

                # --- Glow / bloom ---------------------------------------------------
                if has_glow:
                    glow = diffuse * glow_intensity * 0.3
                    r = min_(1.0, r + glow)
                    g = min_(1.0, g + glow)
                    b = min_(1.0, b + glow)

                grow_append(to_rgb(r, g, b))
            out.append(grow)
        return out

    # ------------------------------------------------------------------
    # Convenience: flat list of pixels (useful for tests / bulk ops)
    # ------------------------------------------------------------------
    def render_flat(self, base_color: Sequence[float], width: int, height: int) -> List[RGB]:
        """Like :meth:`render` but returns a single flattened list of RGB triples."""
        rows = self.render(base_color, width, height)
        return [pixel for row in rows for pixel in row]


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------
def create_engine(resolution: int = 256) -> ColorEngine:
    """Create a :class:`ColorEngine` with default lighting parameters."""
    return ColorEngine(resolution=resolution)
