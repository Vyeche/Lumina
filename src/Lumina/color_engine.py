"""Pure-Python sphere geometry and lighting engine for the Lumina Krita plugin.

The renderer is deliberately split into two layers:

1. A mathematically correct projected unit sphere supplies the surface normal,
   visibility mask, and antialiased coverage.
2. A lighting stage computes Blender-like Point, Sun, Spot, and Area behavior,
   then applies the artist-controlled shadow/base/light colors used by Lumina.

All lighting arithmetic is performed in linear RGB. QColor values arrive in
sRGB space at the processor boundary and are converted there before being sent
into this engine. The engine itself has no Qt dependency.
"""

import math
import time
import logging
from typing import List, Sequence, Tuple

LOG = logging.getLogger("Lumina.engine")


Color = Tuple[float, float, float]
Pixel = Tuple[int, int, int]
Normal = Tuple[float, float, float]


class RenderCancelled(Exception):
    """A render stopped by its cancel flag (see ColorEngine._cancel)."""


class ColorEngine:
    """Generate a shaded, front-facing 3D sphere as an RGB pixel grid."""

    # Anything with is_set() (a threading.Event), checked once per row of
    # each stage: an off-thread render nobody wants any more stops within a
    # row instead of taking the CPU from the previews that replaced it. The
    # stage caches are committed only once a stage completes, so a stop
    # leaves them consistent.
    _cancel = None

    def _check_cancel(self) -> None:
        cancel = self._cancel
        if cancel is not None and cancel.is_set():
            raise RenderCancelled()

    LIGHT_TYPES = ("Point", "Sun", "Spot", "Area")
    MIXER_MODES = ("Blended", "Additive", "Multiplicative")

    # The old engine used 900 as a raw point-light value. That made the
    # inverse-square result several times larger than one and forced the output
    # through aggressive clipping/rolloff. Lumina instead uses a calibrated
    # reference flux: at LIGHT_DISTANCE the illuminance is exactly 1.0 before
    # the user-controlled intensity multiplier.
    LIGHT_DISTANCE = 3.0
    REFERENCE_POINT_FLUX = 4.0 * math.pi * (LIGHT_DISTANCE ** 2)

    # Blender-like spot defaults. spot_size is a full cone angle; the shader
    # compares against the corresponding half-angle around the center axis.
    # 35deg/0.25 keeps the sphere edge in penumbra so Spot differs from Point.
    # Sphere angular radius at light distance 3 is asin(1/3)=19.47deg; the old
    # 60deg/0.35 put the whole visible sphere inside the inner cone.
    SPOT_OUTER_DEG = 35.0
    SPOT_BLEND = 0.25

    # Side of the square area emitter, in sphere radii, at LIGHT_DISTANCE. It
    # sets how soft the Area light is: the panel is treated as a disc of the
    # same area, and its angular size spreads the terminator into a penumbra
    # (issue #53). It used to have no effect at all (divided out and
    # multiplied back), and the light was ~18x too bright.
    AREA_SIZE = 2.0

    AMBIENT_SCALE = 1.2
    AMBIENT_SKY = (0.72, 0.78, 0.92)
    # The sky-tinted share of the flat ambient fill. 0: the reference lighting
    # app shows no grey-blue wash over the lit body, and fitting against it
    # drove this to zero (it read as desaturation, most visibly on reds).
    AMBIENT_SKY_MIX = 0.0

    # Artistic defaults. These values are intentionally moderate because the
    # point-light energy is now normalized at the reference distance.
    # Raised from 0.10 with the tone-curve change below: the reference keeps
    # the unlit side in the shadow colour rather than near-black.
    DIFFUSE_FLOOR = 0.076
    # How soon the diffuse response LEVEL * (1 + g)E / (1 + gE) bends over
    # (DIFFUSE_LEVEL below sets how high it goes). It was 1.155 with the
    # level tied to it (2gE/(1+gE)): the shade then rose fast and was nearly
    # at full brightness by N.L 0.3, squeezed into a narrow band. The
    # reference brightens almost linearly from the terminator to N.L 0.6,
    # a long grade into the shadow (issue #45, round 2).
    DIFFUSE_GAIN = 0.329
    # Shadow build-up. The reference darkens evenly through the terminator
    # into its core band; clamping the diffuse term to the floor stopped it
    # dead there and left a flat plateau behind it, which read as a hard
    # line. The floor is met with a smooth maximum of this width instead
    # (0 = the plain clamp).
    FLOOR_SOFTNESS = 0.193
    # Reflected light: light bounced back into the shadow side, strongest at
    # the silhouette. The reference's unlit side is darkest in a band behind
    # the terminator and lifts again toward the edge, in the surface's own
    # colour. 0 = off.
    REFLECT_STRENGTH = 0.065
    REFLECT_POWER = 3.5          # used only when REFLECT_END is 0
    # The bounce comes from the side opposite the light: the reference lifts
    # its lower-left limb (light from the upper right) but not the lower
    # right. Weight = alignment with the light's opposite direction ** SPREAD;
    # 0 = all the way round.
    REFLECT_SPREAD = 3.88
    # Shape of the reflected band across the limb, as edge = 1 - N.z. With
    # REFLECT_END > 0 it rises as a smoothstep from REFLECT_START to
    # REFLECT_END and then holds: a band with depth, rising out of the core
    # and levelling off at the silhouette, as the reference's does. 0 = the
    # old edge ** REFLECT_POWER, which put nearly all of it in a thin line
    # at the very edge.
    REFLECT_START = 0.38
    REFLECT_END = 0.82
    # Level of the diffuse curve at full light. The curve is
    # LEVEL * (1 + g)E / (1 + gE): g (DIFFUSE_GAIN) sets how soon it bends
    # over and LEVEL how high it goes, so the shade can grade longer
    # without dimming the lit body. 0 = 2g / (1 + g), the old 2gE/(1+gE).
    DIFFUSE_LEVEL = 1.32
    # Shadow hue (issue #45). The reference darkens through the shadow with
    # its chroma in step with its lightness -- the shadow is the same rich
    # colour, darker -- and its hue moves toward the shadow target steadily,
    # from the lit side of the terminator into the core. The shadow target
    # is an ingredient for hue and chroma, not a lightness to reach (green's
    # #00a793 is lighter than the reference's own core).
    #
    # With SHADOW_HUE_MAX > 0 the surface colour on the shadow side keeps the
    # base's lightness and moves its OKLab chroma direction (a/L, b/L) toward
    # the shadow target's, by up to SHADOW_HUE_MAX, rising smoothly as signed
    # N.L goes from SHADOW_HUE_START down to SHADOW_HUE_CORE (negative: past
    # the terminator). The lighting supplies all the darkening. 0 = the
    # linear shadow-colour mix (TONAL_SHADOW_MIX).
    SHADOW_HUE_MAX = 0.575
    SHADOW_HUE_START = 0.57
    SHADOW_HUE_CORE = -0.32
    SHADOW_HUE_LUT_SIZE = 256
    # Environment light (the sky-tinted ambient and the sky / ground bounce)
    # reflects off the surface's own colour: 1 = multiplied by it, as light
    # does. 0 = added as plain light, which washed a grey film over dark,
    # saturated shadows.
    ENV_ALBEDO = 0.7

    # Tonal path, fitted to the reference app's spheres (Ref Tester document):
    # shadow -> base across N.L in [TONAL_SHADOW_START, TONAL_BASE], then
    # base -> light as ((N.L - TONAL_BASE) / (1 - TONAL_BASE)) ** POWER, so
    # the light colour gathers into the highlight instead of washing over the
    # whole lit half (the old 0.62-1.0 smoothstep did that).
    TONAL_SHADOW_START = 0.0
    TONAL_BASE = 0.60
    # 8.0 -> 2.39 and TONAL_LIGHT_MAX 0.4 -> 0.16 with the highlight refit
    # (issue #45): a smaller share of the light colour, spread more evenly
    # over the lit side, instead of a strong dose packed at the peak.
    TONAL_LIGHT_POWER = 2.39
    # How much of each target the sphere actually reaches. The reference uses
    # the targets as ingredients, not paint: its unlit side is roughly a
    # base/shadow mix (hue between the two), darkened by the shading, and its
    # highlight only part of the way to the light target. 1.0 = the target
    # itself at the extremes.
    TONAL_SHADOW_MIX = 0.62
    TONAL_LIGHT_MAX = 0.16
    # Warmth of the default (white) specular, scaled by the base's
    # saturation: the reference's highlight is warm on saturated colours
    # (green #d3f2a6, orange #faceab) and neutral on greys (black #b3b3b3).
    # A preset's own highlight colour is never altered. 0 = always white.
    SPEC_WARMTH = 0.51
    SPEC_WARM_TINT = (1.0, 0.88, 0.65)
    # Where the highlight sits (issue #45). The reference's highlight peaks
    # about two thirds of the way out toward the light and stays bright to
    # the lit silhouette -- a lobe around the light direction itself -- not
    # around the Blinn half-vector, which put Lumina's nearer the centre and
    # let it die well before the edge. The lobe's axis slides from the
    # half-vector (0) to the light direction (1). 0.845 with the highlight
    # refit: the reference peaks about 0.66 of the way out; at 0.875 (and a
    # stronger sheen) Lumina's sat at 0.74, almost on the light direction.
    SPEC_TOWARD_LIGHT = 0.845

    # Display roll-off: values pass through unchanged up to this point, then
    # ease exponentially toward 1. It replaced a plain Reinhard x/(1+x), which
    # compressed everything -- a fully lit #ff0000 rendered as a muddy #bb0000
    # and a white highlight topped out near #bcbcbc. 0.646 -> 0.556 with the
    # highlight refit: the peak eases off sooner, as the reference's does.
    TONE_SHOULDER = 0.556

    SPEC_MAX = 0.61
    # Glow: light added at the lit silhouette, in the highlight colour, at
    # this share of the Glow value. 0.18 left even Glow 100% at under a fifth
    # of a stop -- the slider barely showed and Neon had no bloom.
    GLOW_GAIN = 0.5
    # Immutable renderer default. Live state is self.spec_max (property).
    SPEC_MAX_DEFAULT = 0.61
    # Engine-side scale on the specular sheen, under the user's Highlight
    # strength (spec_max): fitted against the reference's peak brightness
    # without moving anyone's saved slider. 1.0 = unscaled. 0.748: Lumina's
    # peak was 0.02-0.10 OKLab L brighter than the reference's on every
    # sphere (0.06 on average).
    SPEC_GAIN = 0.748
    SPEC_TIGHT = 0.90
    SPEC_KNEE = 0.22
    SPEC_KNEE_MIN = 0.02
    SPEC_KNEE_MAX = 0.95

    # Lookup-table sizes. The sRGB curve is fixed so its table is built once
    # per class; contrast/spec tables are rebuilt when their parameter
    # changes (once per slider change, not once per pixel).
    SRGB_LUT_SIZE = 4096
    SPEC_LUT_SIZE = 1024
    CONTRAST_LUT_SIZE = 4096
    _SRGB_LUT = None

    RIM_POWER = 5.0
    # 0.10: the reference's lit silhouette carries no white film; its edge
    # light on the shadow side is the reflected bounce above.
    RIM_LIGHT = 0.10
    # Immutable rim default. Live state is self.rim_light (property).
    RIM_LIGHT_DEFAULT = 0.10
    # Rim tint blend: rim_color = lerp(light, sky, RIM_SKY_MIX), so the edge
    # accent separates chromatically instead of re-tinting the warm body.
    RIM_SKY_MIX = 0.60
    # Hemisphere bounce strengths (directional environmental form, separate
    # from the flat ambient floor). Deliberately small.
    SKY_BOUNCE = 0.02
    GROUND_BOUNCE = 0.01
    GROUND_COLOR = (0.32, 0.26, 0.20)
    RIM_TOP_WEIGHT = 0.35
    RIM_BOTTOM_GAIN = 1.0
    RIM_SHADOW_LIFT = 0.0

    # Output-cache algorithm versions (issue #40 §1). Bump
    # RENDER_ALGORITHM_VERSION whenever any class-level constant or pipeline
    # stage above changes output pixels; GEOMETRY_ALGORITHM_VERSION covers
    # the normal/mask/coverage grids, which derive purely from dimensions.
    RENDER_ALGORITHM_VERSION = 7  # #53: Area light normalised and soft
    GEOMETRY_ALGORITHM_VERSION = 1

    DARK_FALLOFF = 0.80
    DARK_WASH = 0.06
    DARK_AMBIENT_FLOOR = 0.25

    DARK_GRAIN_BOOST = 2.0
    DARK_GRAIN_ABS = 0.05
    GRAIN_DEPTH = 0.12

    def __init__(self, resolution: int = 256):
        self.resolution = max(8, min(int(resolution), 512))

        # User-facing controls.
        self.ambient = 0.05
        # Azimuth convention: 0 = right, 90 = bottom, 270 = top.
        # 274 mirrors the requested 86 across the horizontal: same dial
        # position, but the key light comes from above as painters expect.
        # 304/41 (upper right) is where the reference lighting app's light
        # sits, recovered by fitting its spheres; it replaced 287/45.
        self.light_azimuth = 304.0
        self.light_elevation = 41.0
        self.shininess = 8.9
        self.spec_knee = 0.22
        # Live specular ceiling. SPEC_MAX/SPEC_MAX_DEFAULT is the immutable
        # renderer default; spec_max property is the single source of truth.
        self._spec_max = float(self.SPEC_MAX_DEFAULT)
        self._rim_light = float(self.RIM_LIGHT_DEFAULT)

        self.ambient_color: Color = (0.88, 0.88, 0.88)
        self.shadow_color: Color = (0.1569, 0.1569, 0.2353)
        self.highlight_color: Color = (1.0, 1.0, 1.0)
        self.light_color: Color = (1.0, 1.0, 1.0)

        self.light_intensity = 1.0
        self.light_type = "Sun"

        self.grain = 0.0
        self.smooth = 2.0
        self.contrast = 1.0
        # Hidden display-stage multiplier kept for API/preset compatibility.
        # The user-facing "Tone" control maps to saturation, not brightness.
        self.brightness = 1.0
        self.saturation = 1.0

        self.mixer_mode = "Blended"
        self.glow_intensity = 0.0
        self.glow_radius = 10.0

        # Environment/accent state behind the new Advanced sliders.
        self._rim_sky_mix = float(self.RIM_SKY_MIX)
        self._sky_bounce = float(self.SKY_BOUNCE)
        self._ground_bounce = float(self.GROUND_BOUNCE)

        self._normal_grid: List[List[Normal]] = []
        self._mask_grid: List[List[bool]] = []
        self._coverage_grid: List[List[float]] = []
        self._pos_grid: List[List[Normal]] = []
        self._grid_width = 0
        self._grid_height = 0

        # Stage caches (see render): light/material results reused when only
        # display controls change.
        self._light_cache_key_value = None
        self._light_ldir = []
        self._light_illum = []
        self._light_ndl = []
        self._light_half = []
        self._material_cache_key_value = None
        self._material_body = []

        # Per-parameter lookup tables; rebuilt on change, not per pixel.
        self._contrast_lut: List[float] = []
        self._spec_lut: List[float] = []
        self._build_contrast_lut()
        self._build_spec_lut()

        # Rolling render timings (last 10), for the perf log.
        self._timings: dict = {}

        # Geometry/shading caches keyed by render size (Run-2): switching
        # between the 96 px drag preview and the 200 px final used to
        # regenerate ~40 ms of geometry on every switch. Grids are
        # deterministic per size, so keep both warm. Shading additionally
        # keys on light direction; both caches are FIFO-bounded so a
        # quality-slider drag cannot grow them without limit.
        self._geo_cache: dict = {}
        self._shading_cache: dict = {}

        self._light_vector = (0.0, 0.0, 1.0)
        self._compute_shading()
        self._generate_geometry(self.resolution, self.resolution)

    # ------------------------------------------------------------------
    # Numeric helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
        return lo if value < lo else hi if value > hi else value

    @classmethod
    def _smoothstep(cls, edge0: float, edge1: float, x: float) -> float:
        if edge0 == edge1:
            return 1.0 if x >= edge1 else 0.0
        t = cls._clamp((x - edge0) / (edge1 - edge0))
        return t * t * (3.0 - 2.0 * t)

    @staticmethod
    def _lerp(a: Color, b: Color, t: float) -> Color:
        return (
            a[0] + (b[0] - a[0]) * t,
            a[1] + (b[1] - a[1]) * t,
            a[2] + (b[2] - a[2]) * t,
        )

    @staticmethod
    def _add(a: Color, b: Color) -> Color:
        return (a[0] + b[0], a[1] + b[1], a[2] + b[2])

    @staticmethod
    def _mul(a: Color, s: float) -> Color:
        return (a[0] * s, a[1] * s, a[2] * s)

    @staticmethod
    def _mul_color(a: Color, b: Color) -> Color:
        return (a[0] * b[0], a[1] * b[1], a[2] * b[2])

    @staticmethod
    def _normalize3(x: float, y: float, z: float) -> Normal:
        length = math.sqrt(x * x + y * y + z * z)
        if length <= 1.0e-12:
            return (0.0, 0.0, 1.0)
        return (x / length, y / length, z / length)

    # ------------------------------------------------------------------
    # Color-space helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _srgb_to_linear_component(value: float) -> float:
        value = max(0.0, min(1.0, float(value)))
        if value <= 0.04045:
            return value / 12.92
        return ((value + 0.055) / 1.055) ** 2.4

    @staticmethod
    def _linear_to_srgb_component(value: float) -> float:
        value = max(0.0, float(value))
        if value <= 0.0031308:
            return 12.92 * value
        return 1.055 * (value ** (1.0 / 2.4)) - 0.055

    @classmethod
    def _to_linear(cls, color: Sequence[float]) -> Color:
        return (
            cls._srgb_to_linear_component(color[0]),
            cls._srgb_to_linear_component(color[1]),
            cls._srgb_to_linear_component(color[2]),
        )

    @classmethod
    def _from_linear(cls, color: Color) -> Color:
        return (
            cls._linear_to_srgb_component(color[0]),
            cls._linear_to_srgb_component(color[1]),
            cls._linear_to_srgb_component(color[2]),
        )

    @classmethod
    def _srgb_lut(cls) -> List[float]:
        # Fixed-function table: linear [0,1] -> sRGB. Built once per class
        # so the per-pixel conversion is an index, not a pow().
        if cls._SRGB_LUT is None:
            n = cls.SRGB_LUT_SIZE
            cls._SRGB_LUT = [
                cls._linear_to_srgb_component(i / float(n - 1))
                for i in range(n)
            ]
        return cls._SRGB_LUT

    @classmethod
    def _srgb_from_lut(cls, value: float) -> float:
        table = cls._srgb_lut()
        last = len(table) - 1
        idx = int(max(0.0, min(1.0, value)) * last + 0.5)
        return table[idx]

    def _build_contrast_lut(self) -> None:
        # Endpoint-preserving power curve sampled once per contrast change.
        n = self.CONTRAST_LUT_SIZE
        contrast = max(0.5, min(2.0, float(self.contrast)))
        self._contrast_lut_value = float(self.contrast)
        lut: List[float] = []
        for i in range(n):
            luma = i / float(n - 1)
            if luma <= 0.0:
                lut.append(0.0)
            elif luma >= 1.0:
                lut.append(1.0)
            else:
                centered = 2.0 * luma - 1.0
                lut.append(0.5 + 0.5 * math.copysign(abs(centered) ** contrast, centered))
        self._contrast_lut = lut

    def _contrast_from_lut(self, luma: float) -> float:
        if not self._contrast_lut:
            self._build_contrast_lut()
        last = len(self._contrast_lut) - 1
        idx = int(max(0.0, min(1.0, luma)) * last + 0.5)
        return self._contrast_lut[idx]

    def _build_spec_lut(self) -> None:
        # ndh^shininess over [0,1]; rebuilt when shininess changes.
        n = self.SPEC_LUT_SIZE
        power = max(1.0, float(self.shininess))
        self._spec_lut_value = float(self.shininess)
        self._spec_lut = [(i / float(n - 1)) ** power for i in range(n)]

    def _spec_from_lut(self, ndh: float) -> float:
        # Linear interpolation between entries keeps the lobe smooth.
        # Self-heals if shininess was assigned directly (tests do this).
        if getattr(self, "_spec_lut_value", None) != float(self.shininess):
            self._build_spec_lut()
        table = self._spec_lut
        last = len(table) - 1
        pos = max(0.0, min(1.0, ndh)) * last
        i0 = int(pos)
        i1 = min(last, i0 + 1)
        frac = pos - i0
        return table[i0] + (table[i1] - table[i0]) * frac

    # ------------------------------------------------------------------
    # State setters
    # ------------------------------------------------------------------
    def _coerce_color(self, color: Sequence[float]) -> Color:
        values = tuple(float(c) for c in color)
        if len(values) != 3:
            raise ValueError("Color must contain exactly three channels")
        # Accept either normalized floats or integer RGB values.
        if any(abs(v) > 1.0 for v in values):
            values = tuple(v / 255.0 for v in values)
        return (
            self._clamp(values[0]),
            self._clamp(values[1]),
            self._clamp(values[2]),
        )

    def set_ambient_color(self, color) -> None:
        self.ambient_color = self._coerce_color(color)
        self._compute_shading()

    def set_shadow_color(self, color) -> None:
        self.shadow_color = self._coerce_color(color)
        self._compute_shading()

    def set_highlight_color(self, color) -> None:
        self.highlight_color = self._coerce_color(color)
        self._compute_shading()

    def set_light_color(self, color) -> None:
        self.light_color = self._coerce_color(color)
        self._compute_shading()

    def set_light_angle(self, azimuth, elevation=None) -> None:
        self.light_azimuth = float(azimuth) % 360.0
        if elevation is not None:
            self.light_elevation = self._clamp(float(elevation), 0.0, 90.0)
        self._compute_shading()

    def set_light_elevation(self, elevation) -> None:
        self.light_elevation = self._clamp(float(elevation), 0.0, 90.0)
        self._compute_shading()

    # Backward-compat aliases for the pre-fix docker (which used
    # light_azimuth_deg degrees + light_elevation_rad radians).
    @property
    def light_azimuth_deg(self) -> float:
        return float(self.light_azimuth)

    @light_azimuth_deg.setter
    def light_azimuth_deg(self, value) -> None:
        self.light_azimuth = float(value) % 360.0
        self._compute_shading()

    @property
    def light_elevation_rad(self) -> float:
        return math.radians(float(self.light_elevation))

    @light_elevation_rad.setter
    def light_elevation_rad(self, value) -> None:
        self.light_elevation = self._clamp(math.degrees(float(value)), 0.0, 90.0)
        self._compute_shading()

    def set_ambient(self, value) -> None:
        self.ambient = self._clamp(float(value))
        self._compute_shading()

    def set_light_intensity(self, value) -> None:
        self.light_intensity = max(0.0, float(value))
        self._compute_shading()

    def set_shininess(self, value) -> None:
        self.shininess = max(1.0, min(64.0, float(value)))
        self._build_spec_lut()
        self._compute_shading()

    @property
    def spec_max(self) -> float:
        return float(getattr(self, "_spec_max", self.SPEC_MAX_DEFAULT))

    @spec_max.setter
    def spec_max(self, value) -> None:
        self._spec_max = max(0.0, min(1.0, float(value)))
        self._compute_shading()

    def set_spec_max(self, value) -> None:
        # Per-preset specular ceiling. Baseline 0.24; Matte ~0.10, Neon ~0.28.
        self.spec_max = value

    @property
    def rim_light(self) -> float:
        return float(getattr(self, "_rim_light", self.RIM_LIGHT_DEFAULT))

    @rim_light.setter
    def rim_light(self, value) -> None:
        self._rim_light = max(0.0, min(1.0, float(value)))
        self._compute_shading()

    def set_rim_light(self, value) -> None:
        self.rim_light = value

    @property
    def rim_sky_mix(self) -> float:
        return float(getattr(self, "_rim_sky_mix", self.RIM_SKY_MIX))

    @rim_sky_mix.setter
    def rim_sky_mix(self, value) -> None:
        self._rim_sky_mix = max(0.0, min(1.0, float(value)))
        self._compute_shading()

    def set_rim_sky_mix(self, value) -> None:
        self.rim_sky_mix = value

    @property
    def sky_bounce(self) -> float:
        return float(getattr(self, "_sky_bounce", self.SKY_BOUNCE))

    @sky_bounce.setter
    def sky_bounce(self, value) -> None:
        self._sky_bounce = max(0.0, min(1.0, float(value)))
        self._compute_shading()

    def set_sky_bounce(self, value) -> None:
        self.sky_bounce = value

    @property
    def ground_bounce(self) -> float:
        return float(getattr(self, "_ground_bounce", self.GROUND_BOUNCE))

    @ground_bounce.setter
    def ground_bounce(self, value) -> None:
        self._ground_bounce = max(0.0, min(1.0, float(value)))
        self._compute_shading()

    def set_ground_bounce(self, value) -> None:
        self.ground_bounce = value

    def set_spec_knee(self, value) -> None:
        self.spec_knee = max(self.SPEC_KNEE_MIN, min(self.SPEC_KNEE_MAX, float(value)))
        self._compute_shading()

    def set_contrast(self, value) -> None:
        self.contrast = max(0.0, float(value))
        self._build_contrast_lut()
        self._compute_shading()

    def set_brightness(self, value) -> None:
        self.brightness = max(0.0, float(value))
        self._compute_shading()

    def set_saturation(self, value) -> None:
        self.saturation = max(0.0, float(value))
        self._compute_shading()

    def set_light_type(self, value) -> None:
        value = str(value)
        self.light_type = value if value in self.LIGHT_TYPES else "Point"
        self._compute_shading()

    def set_mixer_mode(self, mode) -> None:
        mode = str(mode)
        self.mixer_mode = mode if mode in self.MIXER_MODES else "Blended"
        self._compute_shading()

    def set_glow_intensity(self, value) -> None:
        self.glow_intensity = max(0.0, float(value))
        self._compute_shading()

    def set_glow_radius(self, value) -> None:
        self.glow_radius = max(0.5, float(value))
        self._compute_shading()

    def set_grain(self, value) -> None:
        self.grain = max(0.0, float(value))
        self._compute_shading()

    def set_smooth(self, value) -> None:
        self.smooth = max(0.0, min(2.0, float(value)))
        self._compute_shading()

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------
    def _generate_geometry(self, width: int, height: int) -> None:
        width = max(2, int(width))
        height = max(2, int(height))

        normals: List[List[Normal]] = []
        masks: List[List[bool]] = []
        coverage: List[List[float]] = []
        positions: List[List[Normal]] = []

        dx = 2.0 / float(width - 1)
        dy = 2.0 / float(height - 1)
        pixel_half_diagonal = 0.5 * math.sqrt(dx * dx + dy * dy)

        for row in range(height):
            y = -1.0 + 2.0 * row / float(height - 1)
            normal_row: List[Normal] = []
            mask_row: List[bool] = []
            coverage_row: List[float] = []
            pos_row: List[Normal] = []

            for col in range(width):
                x = -1.0 + 2.0 * col / float(width - 1)
                r2 = x * x + y * y
                inside = r2 <= 1.0

                if inside:
                    z2 = max(0.0, 1.0 - r2)
                    z = math.sqrt(z2)

                    # The exact center is a real sphere point. Its normal is
                    # +Z, never (0,0,0). This fixes the central-pixel singularity
                    # in the previous implementation.
                    normal = self._normalize3(x, y, z)
                    pos_row.append((x, y, z))
                else:
                    normal = (0.0, 0.0, 0.0)
                    pos_row.append((0.0, 0.0, 0.0))

                radial_distance = math.sqrt(r2)
                edge_distance = 1.0 - radial_distance
                coverage_value = self._clamp(
                    (edge_distance + pixel_half_diagonal)
                    / max(1.0e-12, 2.0 * pixel_half_diagonal)
                )

                normal_row.append(normal)
                mask_row.append(inside)
                coverage_row.append(coverage_value)

            normals.append(normal_row)
            masks.append(mask_row)
            coverage.append(coverage_row)
            positions.append(pos_row)

        self._normal_grid = normals
        self._mask_grid = masks
        self._coverage_grid = coverage
        self._pos_grid = positions
        self._grid_width = width
        self._grid_height = height
        # Geometry changed: drop dependent stage caches.
        self._light_cache_key_value = None
        self._material_cache_key_value = None
        self._geo_cache[(width, height)] = (
            normals, masks, coverage, positions)
        while len(self._geo_cache) > 6:
            self._geo_cache.pop(next(iter(self._geo_cache)))

    def _restore_geometry(self, width: int, height: int) -> bool:
        """Restore cached grids for a previously built size. True on hit.

        Stage caches key on size, so a restore keeps them valid -- no
        invalidation, unlike a fresh generate above.
        """
        try:
            entry = self._geo_cache.get((width, height))
        except Exception:  # pragma: no cover - diagnostics only
            return False
        if entry is None:
            return False
        normals, masks, coverage, positions = entry
        self._normal_grid = normals
        self._mask_grid = masks
        self._coverage_grid = coverage
        self._pos_grid = positions
        self._grid_width = width
        self._grid_height = height
        return True

    def _generate_normal_grid(self) -> None:
        self._generate_geometry(self.resolution, self.resolution)

    # ------------------------------------------------------------------
    # Light direction cache
    # ------------------------------------------------------------------
    def _compute_shading(self) -> None:
        key = (self._grid_width, self._grid_height,
               self.light_azimuth, self.light_elevation)
        try:
            entry = self._shading_cache.get(key)
        except Exception:  # pragma: no cover - diagnostics only
            entry = None
        if entry is not None:
            (self._light_vector, self._diffuse_cache,
             self._shadow_cache, self._spec_cache) = entry
            return
        azimuth = math.radians(self.light_azimuth)
        elevation = math.radians(self.light_elevation)
        self._light_vector = self._normalize3(
            math.cos(elevation) * math.cos(azimuth),
            math.cos(elevation) * math.sin(azimuth),
            math.sin(elevation),
        )

        # Keep these historical cache names available for compatibility with
        # older callers/tests. The actual renderer recomputes point-light
        # vectors per pixel because a point light is not a constant direction.
        self._diffuse_cache = []
        self._shadow_cache = []
        self._spec_cache = []
        if self._grid_width and self._grid_height:
            diffuse: List[List[float]] = []
            shadow: List[List[float]] = []
            spec: List[List[float]] = []
            for row in self._normal_grid:
                drow: List[float] = []
                srow: List[float] = []
                hrow: List[float] = []
                for nx, ny, nz in row:
                    if nx == 0.0 and ny == 0.0 and nz == 0.0:
                        drow.append(0.0)
                        srow.append(1.0)
                        hrow.append(0.0)
                        continue
                    ndl = max(0.0, nx * self._light_vector[0] + ny * self._light_vector[1] + nz * self._light_vector[2])
                    hx, hy, hz = self._normalize3(
                        self._light_vector[0],
                        self._light_vector[1],
                        self._light_vector[2] + 1.0,
                    )
                    ndh = max(0.0, nx * hx + ny * hy + nz * hz)
                    drow.append(ndl)
                    srow.append((1.0 - ndl) ** self.DARK_FALLOFF)
                    hrow.append(ndh)
                diffuse.append(drow)
                shadow.append(srow)
                spec.append(hrow)
            self._diffuse_cache = diffuse
            self._shadow_cache = shadow
            self._spec_cache = spec
        self._shading_cache[key] = (
            self._light_vector, self._diffuse_cache,
            self._shadow_cache, self._spec_cache)
        while len(self._shading_cache) > 12:
            self._shading_cache.pop(next(iter(self._shading_cache)))

    def _compute_shading_cache(self) -> None:
        self._compute_shading()

    # ------------------------------------------------------------------
    # Light implementations
    # ------------------------------------------------------------------
    def _point_position(self) -> Normal:
        return self._light_vector

    def _point_illuminance(self, surface_x: float, surface_y: float, surface_z: float) -> Tuple[Normal, float]:
        # Unit sphere is the scene object. The virtual point light sits at the
        # configured direction and LIGHT_DISTANCE units from the center.
        lx = self._light_vector[0] * self.LIGHT_DISTANCE
        ly = self._light_vector[1] * self.LIGHT_DISTANCE
        lz = self._light_vector[2] * self.LIGHT_DISTANCE

        vx = lx - surface_x
        vy = ly - surface_y
        vz = lz - surface_z
        distance = math.sqrt(vx * vx + vy * vy + vz * vz)
        if distance <= 1.0e-6:
            return (0.0, 0.0, 1.0), 0.0

        ldir = (vx / distance, vy / distance, vz / distance)
        flux = self.REFERENCE_POINT_FLUX * self.light_intensity
        illuminance = flux / (4.0 * math.pi * (distance * distance + 0.01))
        return ldir, illuminance

    def _sun_illuminance(self) -> Tuple[Normal, float]:
        return self._light_vector, self.light_intensity

    def _spot_illuminance(self, surface_x: float, surface_y: float, surface_z: float) -> Tuple[Normal, float]:
        ldir, point_energy = self._point_illuminance(surface_x, surface_y, surface_z)

        # The virtual spotlight is aimed at the sphere center. Its axis is the
        # direction from the light toward the origin, which equals the negative
        # of the surface-to-light position vector.
        axis = (-self._light_vector[0], -self._light_vector[1], -self._light_vector[2])

        # ldir points from the surface toward the lamp. The vector from lamp to
        # surface is therefore -ldir.
        lamp_to_surface = (-ldir[0], -ldir[1], -ldir[2])
        theta_cos = axis[0] * lamp_to_surface[0] + axis[1] * lamp_to_surface[1] + axis[2] * lamp_to_surface[2]

        outer_half = math.radians(self.SPOT_OUTER_DEG * 0.5)
        inner_half = outer_half * (1.0 - self.SPOT_BLEND)
        outer_cos = math.cos(outer_half)
        inner_cos = math.cos(inner_half)

        if theta_cos <= outer_cos:
            cone = 0.0
        elif theta_cos >= inner_cos:
            cone = 1.0
        else:
            cone = self._smoothstep(outer_cos, inner_cos, theta_cos)

        return ldir, point_energy * cone

    def _area_illuminance(self, surface_x: float, surface_y: float, surface_z: float) -> Tuple[Normal, float]:
        """A square panel facing the sphere, AREA_SIZE across, at the light.

        Brightness is the Point light's (inverse square, the same reference
        flux) times the panel's own cosine toward the surface, so Area and
        Point light the sphere equally hard where both reach.

        Softness: the panel, as a disc of equal area, spans an angular radius
        a seen from the surface, so it keeps lighting a little past the point
        where its centre sets. Over the penumbra |cos| < sin a the cosine is
        replaced by (cos + sin a)^2 / (4 sin a), the standard horizon
        approximation for a disc light: smooth, meeting plain cos at the
        lit edge and 0 at the far one. The light direction handed back is
        bent toward the normal to carry that cosine, so N.L everywhere
        downstream (tonal path, shadow hue, specular) sees the soft edge.
        The sphere is the unit sphere at the origin, so the surface point is
        its own normal.
        """
        lv = self._light_vector
        dist = self.LIGHT_DISTANCE
        vx = lv[0] * dist - surface_x
        vy = lv[1] * dist - surface_y
        vz = lv[2] * dist - surface_z
        distance = math.sqrt(vx * vx + vy * vy + vz * vz)
        if distance <= 1.0e-6:
            return (0.0, 0.0, 1.0), 0.0
        lx, ly, lz = vx / distance, vy / distance, vz / distance
        # Panel facing the origin: its cosine toward this surface point.
        cos_emit = lv[0] * lx + lv[1] * ly + lv[2] * lz
        if cos_emit <= 0.0:
            return (lx, ly, lz), 0.0
        flux = self.REFERENCE_POINT_FLUX * self.light_intensity
        illuminance = flux / (4.0 * math.pi * (distance * distance + 0.01)) * cos_emit

        nx, ny, nz = surface_x, surface_y, surface_z
        cos_c = nx * lx + ny * ly + nz * lz
        radius = self.AREA_SIZE / math.sqrt(math.pi)          # equal-area disc
        sin_a = min(0.95, radius / math.sqrt(distance * distance + radius * radius))
        if cos_c >= sin_a or sin_a <= 0.0:
            return (lx, ly, lz), illuminance
        if cos_c <= -sin_a:
            return (lx, ly, lz), 0.0
        cos_soft = (cos_c + sin_a) * (cos_c + sin_a) / (4.0 * sin_a)
        # Bend the direction in the plane of the normal and the panel so that
        # N.L equals the soft cosine.
        tx, ty, tz = lx - cos_c * nx, ly - cos_c * ny, lz - cos_c * nz
        tlen = math.sqrt(tx * tx + ty * ty + tz * tz)
        if tlen <= 1.0e-9:
            return (lx, ly, lz), illuminance
        sin_soft = math.sqrt(max(0.0, 1.0 - cos_soft * cos_soft))
        bent = (cos_soft * nx + sin_soft * tx / tlen,
                cos_soft * ny + sin_soft * ty / tlen,
                cos_soft * nz + sin_soft * tz / tlen)
        return bent, illuminance

    def _area_spec_spread(self) -> float:
        """How much wider the highlight is under the Area light (1 = Point's).

        A panel reflects as a patch, not a point: its highlight is the
        surface's own lobe blurred by the panel's angular size. With the lobe
        ndh ** n about sqrt(2 / n) radians wide and the panel's angular
        radius a (as a disc of equal area, seen from LIGHT_DISTANCE), the two
        add in quadrature, so the lobe stretches by
        f = sqrt(1 + a^2 n / 2). The same light spread wider is dimmer: the
        material stage divides the peak by f^2.
        """
        if self.light_type != "Area":
            return 1.0
        radius = self.AREA_SIZE / math.sqrt(math.pi)
        a = math.atan2(radius, self.LIGHT_DISTANCE)
        n = max(1.0, float(self.shininess))
        return math.sqrt(1.0 + a * a * n / 2.0)

    # Form zones, the painter's names for the bands a light lays over a form,
    # named on hover so the artist can see where each one sits under the
    # current lamp (issue #53). Thresholds are in effective N.L: the light
    # actually arriving (a Spot's beam, an Area's soft edge), not geometry.
    ZONE_HIGHLIGHT = 0.35       # share of the specular lobe's peak
    ZONE_LIGHT = 0.55           # effective N.L at and above: the lit plane
    ZONE_HALFTONE = 0.12        # down to here: turning away from the light
    ZONE_TERMINATOR = -0.12     # signed N.L down to here: the shadow edge
    ZONE_REFLECT = 0.35         # share of the reflected band's full strength

    def form_zone(self, u: float, v: float) -> str:
        """Name the form zone at sphere coordinates (u, v) in [-1, 1], v down:
        Highlight, Light, Halftone, Terminator, Core shadow or Reflected light.
        """
        r2 = u * u + v * v
        if r2 >= 1.0:
            k = 0.999 / math.sqrt(r2)
            u, v, r2 = u * k, v * k, 0.998
        nz = math.sqrt(1.0 - r2)
        nx, ny = u, v
        ldir, illum = self._light_for_surface(nx, ny, nz)
        cos_l = nx * ldir[0] + ny * ldir[1] + nz * ldir[2]
        lit = illum * max(0.0, cos_l)
        if lit > 0.0:
            hx, hy, hz = self._normalize3(ldir[0], ldir[1], ldir[2] + 1.0)
            toward = self.SPEC_TOWARD_LIGHT
            if toward > 0.0:
                hx, hy, hz = self._normalize3(hx + (ldir[0] - hx) * toward,
                                              hy + (ldir[1] - hy) * toward,
                                              hz + (ldir[2] - hz) * toward)
            ndh = max(0.0, nx * hx + ny * hy + nz * hz)
            spread = self._area_spec_spread()
            if spread != 1.0:
                ndh = math.cos(math.acos(min(1.0, ndh)) / spread)
            lobe = self._spec_from_lut(ndh) * self._smoothstep(
                max(self.SPEC_KNEE_MIN, min(self.SPEC_KNEE_MAX, self.spec_knee)), 1.0, ndh)
            if self.spec_max > 0.0 and lobe * min(1.0, lit) >= self.ZONE_HIGHLIGHT:
                return "Highlight"
            if lit >= self.ZONE_LIGHT:
                return "Light"
            if lit >= self.ZONE_HALFTONE:
                return "Halftone"
        # Signed N.L against the lamp's own direction: where the form turns
        # past the light. Outside a Spot's beam it is shadow however it faces.
        if cos_l >= self.ZONE_TERMINATOR and (illum > 0.0 or self.light_type != "Spot"):
            return "Terminator"
        if self.REFLECT_STRENGTH > 0.0:
            edge = 1.0 - nz
            span = self.REFLECT_END - self.REFLECT_START
            if span > 0.0:
                t = max(0.0, min(1.0, (edge - self.REFLECT_START) / span))
                band = t * t * (3.0 - 2.0 * t)
            else:
                band = edge ** self.REFLECT_POWER
            lv = self._light_vector
            lxy, nxy = math.hypot(lv[0], lv[1]), math.hypot(nx, ny)
            if self.REFLECT_SPREAD > 0.0 and lxy > 1e-6 and nxy > 1e-6:
                away = -(nx * lv[0] + ny * lv[1]) / (lxy * nxy)
                band *= max(0.0, away) ** self.REFLECT_SPREAD
            if band >= self.ZONE_REFLECT:
                return "Reflected light"
        return "Core shadow"

    def _light_for_surface(self, surface_x: float, surface_y: float, surface_z: float) -> Tuple[Normal, float]:
        if self.light_type == "Sun":
            return self._sun_illuminance()
        if self.light_type == "Spot":
            return self._spot_illuminance(surface_x, surface_y, surface_z)
        if self.light_type == "Area":
            return self._area_illuminance(surface_x, surface_y, surface_z)
        return self._point_illuminance(surface_x, surface_y, surface_z)

    # ------------------------------------------------------------------
    # Artist-controlled tonal color map
    # ------------------------------------------------------------------
    def _tonal_color(self, shadow_linear: Color, base_linear: Color, light_linear: Color, ndl: float) -> Color:
        # Artistic color zones use raw NdotL (gamma only), NOT the wrapped
        # value. Wrapped diffuse is for lighting-energy softening; using it
        # here pushed NdotL=0 to ~52% base, squeezing the shadow target.
        # Zones: 0.0=shadow, ~0.3=transition, ~0.5=base, ~0.7=transition, 1.0=light.
        shadow_to_base, base_to_light = self._tonal_weights(ndl)

        color = self._lerp(shadow_linear, base_linear, shadow_to_base)
        color = self._lerp(color, light_linear, base_to_light)
        return color

    def _tonal_weights(self, ndl: float):
        # Scalar zone weights; the table version below must match exactly.
        return self._tonal_pair(self._clamp(ndl))

    @staticmethod
    def _linear_to_oklab(c):
        l = 0.4122214708 * c[0] + 0.5363325363 * c[1] + 0.0514459929 * c[2]
        m = 0.2119034982 * c[0] + 0.6806995451 * c[1] + 0.1073969566 * c[2]
        s = 0.0883024619 * c[0] + 0.2817188376 * c[1] + 0.6299787005 * c[2]
        l, m, s = (math.copysign(abs(v) ** (1.0 / 3.0), v) for v in (l, m, s))
        return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
                1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
                0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)

    @staticmethod
    def _oklab_to_linear(L, a, b):
        l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
        m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
        s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
        return (max(0.0, 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
                max(0.0, -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
                max(0.0, -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s))

    def _shadow_hue_lut(self, shadow_linear, base_linear):
        """Surface colour across signed N.L in [-1, 1] (see SHADOW_HUE_MAX)."""
        size = self.SHADOW_HUE_LUT_SIZE
        Lb, ab, bb = self._linear_to_oklab(base_linear)
        Ls, as_, bs = self._linear_to_oklab(shadow_linear)
        nb = (ab / Lb, bb / Lb) if Lb > 1e-4 else (0.0, 0.0)
        ns = (as_ / Ls, bs / Ls) if Ls > 1e-4 else nb
        start, core, top = self.SHADOW_HUE_START, self.SHADOW_HUE_CORE, self.SHADOW_HUE_MAX
        span = (start - core) if start > core else 1e-6
        table = []
        for i in range(size):
            sn = -1.0 + 2.0 * i / (size - 1)
            t = (start - sn) / span
            t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
            w = top * t * t * (3.0 - 2.0 * t)
            if w <= 0.0:
                table.append(tuple(base_linear))
                continue
            na = nb[0] + (ns[0] - nb[0]) * w
            nbb = nb[1] + (ns[1] - nb[1]) * w
            table.append(self._oklab_to_linear(Lb, Lb * na, Lb * nbb))
        return table

    @classmethod
    def _tonal_pair(cls, ndl: float):
        lo, mid = cls.TONAL_SHADOW_START, cls.TONAL_BASE
        ramp = min(1.0, max(0.0, (ndl - lo) / (mid - lo)))
        shadow_to_base = 1.0 - cls.TONAL_SHADOW_MIX * (1.0 - ramp)
        base_to_light = cls.TONAL_LIGHT_MAX * min(
            1.0, max(0.0, (ndl - mid) / (1.0 - mid))) ** cls.TONAL_LIGHT_POWER
        return shadow_to_base, base_to_light

    TONAL_LUT_SIZE = 256
    _TONAL_LUT = None

    @classmethod
    def _tonal_lut(cls):
        if cls._TONAL_LUT is None:
            table = []
            for i in range(cls.TONAL_LUT_SIZE):
                table.append(cls._tonal_pair(i / float(cls.TONAL_LUT_SIZE - 1)))
            cls._TONAL_LUT = table
        return cls._TONAL_LUT

    def _power_response(self, illuminance: float, ndl: float) -> float:
        # The physical point/spot/area calculation produces illuminance. A UI
        # color sphere still needs a display-referred response so 200% intensity
        # does not instantly white-clip the entire object. At the calibrated
        # reference point, E=1 gives response=1.
        direct_energy = max(0.0, illuminance * ndl)
        return (2.0 * direct_energy) / (1.0 + direct_energy) if direct_energy > 0.0 else 0.0
        # The physical point/spot/area calculation produces illuminance. A UI
        # color sphere still needs a display-referred response so 200% intensity
        # does not instantly white-clip the entire object. At the calibrated
        # reference point, E=1 gives response=1.
        direct_energy = max(0.0, illuminance * ndl)
        return (2.0 * direct_energy) / (1.0 + direct_energy) if direct_energy > 0.0 else 0.0

    # ------------------------------------------------------------------
    # Final display transforms
    # ------------------------------------------------------------------
    @staticmethod
    def _contrast_luma(luma: float, contrast: float) -> float:
        # Endpoint-preserving S-curve around the midtone: black stays black,
        # white stays white, and no nonzero luma is ever forced to zero.
        # The old 0.5-centered linear rescale hard-crushed everything below
        # 0.5-0.5/contrast to exact black, erasing shadows and the rim.
        luma = max(0.0, min(1.0, float(luma)))
        contrast = max(0.5, min(2.0, float(contrast)))

        if luma <= 0.0:
            return 0.0
        if luma >= 1.0:
            return 1.0

        centered = 2.0 * luma - 1.0
        shaped = math.copysign(abs(centered) ** contrast, centered)
        return 0.5 + 0.5 * shaped

    def _apply_contrast(self, color: Color) -> Color:
        # Contrast acts on luminance while preserving the color direction.
        # Uses the prebuilt curve: same math as _contrast_luma, no pow().
        # Self-heals if contrast was assigned directly (tests do this).
        if getattr(self, "_contrast_lut_value", None) != float(self.contrast):
            self._build_contrast_lut()
        luma = 0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]
        contrasted_luma = self._contrast_from_lut(luma)
        if luma > 1.0e-8:
            scale = contrasted_luma / luma
            return self._mul(color, scale)
        return (0.0, 0.0, 0.0)

    def _apply_saturation(self, color: Color) -> Color:
        # Global tone control is saturation, as the docker labels this row
        # "Tone" and calls set_saturation().
        luma = 0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]
        return (
            luma + (color[0] - luma) * self.saturation,
            luma + (color[1] - luma) * self.saturation,
            luma + (color[2] - luma) * self.saturation,
        )

    def _apply_brightness(self, color: Color) -> Color:
        return self._mul(color, self.brightness)

    def _compute_rim(self, nv: float, ndl: float, illuminance: float,
                     light_linear: Color, sky_linear: Color) -> Color:
        # (1-nv)^5 expanded exactly: r*r*r*r*r with no pow() call.
        r = 1.0 - nv
        r2 = r * r
        rim = r2 * r2 * r
        rim *= ndl
        rim *= self.rim_light
        rim *= (0.5 + 0.5 * min(2.0, illuminance))
        rim_color = self._lerp(light_linear, sky_linear, self.rim_sky_mix)
        return self._mul(rim_color, rim)

    def _compute_glow(self, nv: float, ndl: float,
                      highlight_linear: Color) -> Color:
        # Engine stores glow as a fraction (docker sends percent / 100);
        # an extra /100 here once made it ~100x too weak.
        if self.glow_intensity <= 0.0:
            return (0.0, 0.0, 0.0)
        glow_power = max(1.0, self.glow_radius / 4.0)
        glow = ((1.0 - nv) ** glow_power) * ndl
        glow *= self.glow_intensity
        return self._mul(highlight_linear, glow * 0.18)

    @staticmethod
    def _tone_map(color: Color) -> Color:
        # Same roll-off as the render loop: identity below the shoulder.
        return tuple(ColorEngine._shoulder(c) for c in color)  # type: ignore[return-value]

    @classmethod
    def _shoulder(cls, c: float) -> float:
        if c <= 0.0:
            return 0.0
        k = cls.TONE_SHOULDER
        if c <= k:
            return c
        span = 1.0 - k
        return k + span * (1.0 - math.exp(-(c - k) / span))

    @staticmethod
    def _noise(row: int, col: int) -> float:
        # Stable 32-bit integer hash -> [-1, 1].
        x = (row * 374761393 + col * 668265263) & 0xFFFFFFFF
        x ^= (x >> 13)
        x = (x * 1274126177) & 0xFFFFFFFF
        x ^= (x >> 16)
        return (x / 4294967295.0) * 2.0 - 1.0

    def _apply_grain(self, color: Color, row: int, col: int, shadow_factor: float) -> Color:
        if self.grain <= 0.0:
            return color
        amount = self.grain / 100.0
        if shadow_factor > 0.5:
            amount *= 1.0 + self.DARK_GRAIN_BOOST
        noise = self._noise(row, col)
        scale = self.GRAIN_DEPTH * amount
        return (
            max(0.0, color[0] + noise * scale),
            max(0.0, color[1] + noise * scale),
            max(0.0, color[2] + noise * scale),
        )

    # ------------------------------------------------------------------
    # Staged rendering
    #
    # The pipeline splits by dependency so display-only drags (contrast,
    # saturation, brightness, grain, rim, glow) reuse the expensive
    # lighting/material stages:
    #   LIGHT:    ldir / illuminance / NdotL  (light + geometry)
    #   MATERIAL: body up to the mixer        (light + colors + material)
    #   DISPLAY:  everything from contrast on (cheap: LUTs, mults, 1 pow)
    # ------------------------------------------------------------------
    def _effective_highlight(self, base_srgb) -> Color:
        """The specular colour for this base: a preset's colour as set, the
        default white warmed by SPEC_WARMTH in proportion to saturation."""
        hl = tuple(self.highlight_color)
        if self.SPEC_WARMTH <= 0.0 or hl != (1.0, 1.0, 1.0):
            return hl
        top = max(base_srgb)
        sat = (top - min(base_srgb)) / top if top > 0.0 else 0.0
        w = self.SPEC_WARMTH * min(1.0, sat / 0.5)
        return tuple(1.0 + (c - 1.0) * w for c in self.SPEC_WARM_TINT)

    def _light_cache_key(self, width: int, height: int):
        return (self.light_type, self.light_azimuth, self.light_elevation,
                self.light_intensity, width, height, self.SPEC_TOWARD_LIGHT)

    def _build_light_stage(self, width: int, height: int):
        """Per-pixel light direction, illuminance and NdotL. Cached by key."""
        key = self._light_cache_key(width, height)
        if key == self._light_cache_key_value:
            return
        ldirs: List[List[Normal]] = []
        illums: List[List[float]] = []
        ndls: List[List[float]] = []
        halfs: List[List[Normal]] = []
        toward = self.SPEC_TOWARD_LIGHT
        if self.light_type == "Sun":
            # Parallel light: direction, illuminance and half vector are the
            # same everywhere, so they are computed once (same expressions
            # as below, same values) and only N.L varies per pixel.
            ldir, illuminance = self._sun_illuminance()
            lx, ly, lz = ldir
            hx, hy, hz = self._normalize3(lx, ly, lz + 1.0)
            if toward > 0.0:
                hx, hy, hz = self._normalize3(hx + (lx - hx) * toward,
                                              hy + (ly - hy) * toward,
                                              hz + (lz - hz) * toward)
            half = (hx, hy, hz)
            out_dir, out_half = (0.0, 0.0, 1.0), (0.0, 0.0, 1.0)
            for row in range(height):
                self._check_cancel()
                mrow = self._mask_grid[row]
                nrm_row = self._normal_grid[row]
                ndls.append([max(0.0, n[0] * lx + n[1] * ly + n[2] * lz) if m else 0.0
                             for n, m in zip(nrm_row, mrow)])
                ldirs.append([ldir if m else out_dir for m in mrow])
                illums.append([illuminance if m else 0.0 for m in mrow])
                halfs.append([half if m else out_half for m in mrow])
            self._light_ldir = ldirs
            self._light_illum = illums
            self._light_ndl = ndls
            self._light_half = halfs
            self._light_cache_key_value = key
            return
        light_for = self._light_for_surface
        sqrt = math.sqrt
        for row in range(height):
            self._check_cancel()
            lrow: List[Normal] = []
            erow: List[float] = []
            nrow: List[float] = []
            hrow: List[Normal] = []
            pos_row = self._pos_grid[row]
            nrm_row = self._normal_grid[row]
            mrow = self._mask_grid[row]
            for col in range(width):
                if not mrow[col]:
                    lrow.append((0.0, 0.0, 1.0))
                    erow.append(0.0)
                    nrow.append(0.0)
                    hrow.append((0.0, 0.0, 1.0))
                    continue
                x, y, z = pos_row[col]
                nx, ny, nz = nrm_row[col]
                ldir, illuminance = light_for(x, y, z)
                lx, ly, lz = ldir
                ndl = nx * lx + ny * ly + nz * lz
                ndl = ndl if ndl > 0.0 else 0.0
                # Blinn-Phong halfway vector H = normalize(L + V) with fixed
                # V=(0,0,1). Depends only on the light, so it lives in the
                # light cache instead of costing a sqrt per material pixel.
                # (_normalize3 inlined, same arithmetic: #56.)
                hz0 = lz + 1.0
                length = sqrt(lx * lx + ly * ly + hz0 * hz0)
                if length <= 1.0e-12:
                    hx, hy, hz = 0.0, 0.0, 1.0
                else:
                    hx, hy, hz = lx / length, ly / length, hz0 / length
                if toward > 0.0:
                    ax = hx + (lx - hx) * toward
                    ay = hy + (ly - hy) * toward
                    az = hz + (lz - hz) * toward
                    length = sqrt(ax * ax + ay * ay + az * az)
                    if length <= 1.0e-12:
                        hx, hy, hz = 0.0, 0.0, 1.0
                    else:
                        hx, hy, hz = ax / length, ay / length, az / length
                hrow.append((hx, hy, hz))
                lrow.append(ldir)
                erow.append(illuminance)
                nrow.append(ndl)
            ldirs.append(lrow)
            illums.append(erow)
            ndls.append(nrow)
            halfs.append(hrow)
        self._light_ldir = ldirs
        self._light_illum = illums
        self._light_ndl = ndls
        self._light_half = halfs
        self._light_cache_key_value = key

    def _material_key(self, light_key, base_srgb):
        eng = self
        return (light_key, base_srgb,
                eng.shadow_color, eng.light_color, eng.highlight_color,
                eng.ambient_color, eng.ambient, eng.shininess, eng.spec_knee,
                eng.spec_max, eng.mixer_mode, eng.sky_bounce,
                eng.ground_bounce)

    def render_signature(self):
        """Explicit full-render signature for the one-entry output cache.

        Every output-affecting setting, unrounded (no float rounding: two
        states that differ by any amount are different keys). Class-level
        pipeline constants are covered by RENDER_ALGORITHM_VERSION, which
        must be bumped whenever they change output. Geometry derives purely
        from dimensions plus GEOMETRY_ALGORITHM_VERSION (see the processor
        key); the base color is part of the processor key, not here.
        """
        return (self.light_type, self.light_azimuth, self.light_elevation,
                self.light_intensity, tuple(self.light_color),
                tuple(self.shadow_color), tuple(self.highlight_color),
                tuple(self.ambient_color), self.ambient, self.shininess,
                self.spec_knee, self.spec_max, self.rim_light,
                self.rim_sky_mix, self.sky_bounce, self.ground_bounce,
                self.contrast, self.brightness, self.saturation,
                self.mixer_mode, self.glow_intensity, self.glow_radius,
                self.grain, self.smooth)

    def _build_material_stage(self, width, height, key, colors):
        """Body color through the mixer (pre-contrast). Cached by key."""
        if key == self._material_cache_key_value:
            return
        (shadow_linear, base_linear, light_linear, ambient_linear,
         highlight_linear, sky_linear, ground_linear) = colors
        bodies: List[List[Color]] = []
        spec_lut = self._spec_lut
        spec_last = len(spec_lut) - 1
        shininess = self.shininess  # noqa: F841 (kept for clarity)
        spec_knee = max(self.SPEC_KNEE_MIN, min(self.SPEC_KNEE_MAX, self.spec_knee))
        mixer = self.mixer_mode
        spec_max = self.spec_max
        spec_gain = self.SPEC_GAIN
        # Area light: a broader, dimmer highlight (see _area_spec_spread).
        spec_spread = self._area_spec_spread()
        spec_spread_inv = 1.0 / spec_spread
        spec_gain *= spec_spread_inv * spec_spread_inv
        acos = math.acos
        cos = math.cos
        tonal_lut = self._tonal_lut()
        tonal_last = len(tonal_lut) - 1
        ambient_base = self.ambient * self.AMBIENT_SCALE
        ambient_k = self.ambient * 0.35
        ambient_sky_mix = self.AMBIENT_SKY_MIX
        diffuse_floor = self.DIFFUSE_FLOOR
        diffuse_gain = self.DIFFUSE_GAIN
        diffuse_level = self.DIFFUSE_LEVEL
        if diffuse_level <= 0.0:
            diffuse_level = 2.0 * diffuse_gain / (1.0 + diffuse_gain)
        diffuse_k = diffuse_level * (1.0 + diffuse_gain)
        floor_soft = self.FLOOR_SOFTNESS
        env_k = self.ENV_ALBEDO
        hue_lut = (self._shadow_hue_lut(shadow_linear, base_linear)
                   if self.SHADOW_HUE_MAX > 0.0 else None)
        hue_last = self.SHADOW_HUE_LUT_SIZE - 1
        reflect_k = self.REFLECT_STRENGTH
        reflect_p = self.REFLECT_POWER
        reflect_s = self.REFLECT_SPREAD
        reflect_a = self.REFLECT_START
        reflect_e = self.REFLECT_END
        reflect_span = reflect_e - reflect_a
        sky_b = self.sky_bounce
        ground_b = self.ground_bounce
        for row in range(height):
            self._check_cancel()
            brow: List[Color] = []
            mrow = self._mask_grid[row]
            nrm_row = self._normal_grid[row]
            lrow = self._light_ldir[row]
            erow = self._light_illum[row]
            drow = self._light_ndl[row]
            for col in range(width):
                if not mrow[col]:
                    brow.append((0.0, 0.0, 0.0))
                    continue
                nx, ny, nz = nrm_row[col]
                ldir = lrow[col]
                illuminance = erow[col]
                ndl = drow[col]

                tl = tonal_lut
                ndlc = ndl if ndl < 1.0 else 1.0
                if ndlc < 0.0:
                    ndlc = 0.0
                shadow_to_base, base_to_light = tl[int(ndlc * tonal_last + 0.5)]
                if hue_lut is not None:
                    # Shadow hue by signed N.L (SHADOW_HUE_MAX).
                    sn = nx * ldir[0] + ny * ldir[1] + nz * ldir[2]
                    t0r, t0g, t0b = hue_lut[int((sn + 1.0) * 0.5 * hue_last + 0.5)]
                else:
                    # Inline double-lerp: shadow -> base -> light.
                    t0r = shadow_linear[0] + (base_linear[0] - shadow_linear[0]) * shadow_to_base
                    t0g = shadow_linear[1] + (base_linear[1] - shadow_linear[1]) * shadow_to_base
                    t0b = shadow_linear[2] + (base_linear[2] - shadow_linear[2]) * shadow_to_base
                tcr = t0r + (light_linear[0] - t0r) * base_to_light
                tcg = t0g + (light_linear[1] - t0g) * base_to_light
                tcb = t0b + (light_linear[2] - t0b) * base_to_light
                de = illuminance * ndl
                gde = diffuse_gain * de
                direct_factor = diffuse_k * de / (1.0 + gde) if de > 0.0 else 0.0
                # Inline smoothstep(0, 0.55, ndl).
                st = ndl / 0.55
                if st < 0.0:
                    st = 0.0
                elif st > 1.0:
                    st = 1.0
                shadow_factor = 1.0 - st * st * (3.0 - 2.0 * st)
                ambient_factor = ambient_base + shadow_factor * ambient_k
                # Inline ambient tint lerp (fixed 0.35).
                atr = shadow_linear[0] + (ambient_linear[0] - shadow_linear[0]) * 0.35
                atg = shadow_linear[1] + (ambient_linear[1] - shadow_linear[1]) * 0.35
                atb = shadow_linear[2] + (ambient_linear[2] - shadow_linear[2]) * 0.35

                if floor_soft > 0.0:
                    # Smooth maximum of the diffuse term and the floor.
                    gap = direct_factor - diffuse_floor
                    h = floor_soft - (gap if gap > 0.0 else -gap)
                    body_factor = direct_factor if gap > 0.0 else diffuse_floor
                    if h > 0.0:
                        body_factor += h * h * 0.25 / floor_soft
                else:
                    body_factor = direct_factor
                    if body_factor < diffuse_floor:
                        body_factor = diffuse_floor
                mix = ambient_factor * ambient_sky_mix
                br = tcr * body_factor + atr * mix
                bg = tcg * body_factor + atg * mix
                bb = tcb * body_factor + atb * mix
                sk = ambient_factor * 0.05
                if env_k > 0.0:
                    # Environment light takes the surface's colour (ENV_ALBEDO).
                    er = 1.0 - env_k + env_k * tcr
                    eg = 1.0 - env_k + env_k * tcg
                    eb = 1.0 - env_k + env_k * tcb
                else:
                    er = eg = eb = 1.0
                br += sky_linear[0] * sk * er
                bg += sky_linear[1] * sk * eg
                bb += sky_linear[2] * sk * eb
                if reflect_k > 0.0:
                    # Shadow side only, rising toward the silhouette, and
                    # (with REFLECT_SPREAD) on the side facing away from the
                    # light in the picture plane.
                    edge = 1.0 - (nz if nz > 0.0 else 0.0)
                    if reflect_span > 0.0:
                        rt = (edge - reflect_a) / reflect_span
                        rt = 0.0 if rt < 0.0 else (1.0 if rt > 1.0 else rt)
                        rk = reflect_k * shadow_factor * rt * rt * (3.0 - 2.0 * rt)
                    else:
                        rk = reflect_k * shadow_factor * edge ** reflect_p
                    if reflect_s > 0.0:
                        lxy = math.hypot(ldir[0], ldir[1])
                        nxy = math.hypot(nx, ny)
                        if lxy > 1e-6 and nxy > 1e-6:
                            away = -(nx * ldir[0] + ny * ldir[1]) / (lxy * nxy)
                            rk *= (away if away > 0.0 else 0.0) ** reflect_s
                    br += base_linear[0] * rk
                    bg += base_linear[1] * rk
                    bb += base_linear[2] * rk
                yn = -ny
                if yn < 0.0:
                    yn = 0.0
                yp = ny
                if yp < 0.0:
                    yp = 0.0
                br += sky_linear[0] * yn * sky_b * er
                bg += sky_linear[1] * yn * sky_b * eg
                bb += sky_linear[2] * yn * sky_b * eb
                br += ground_linear[0] * yp * ground_b * er
                bg += ground_linear[1] * yp * ground_b * eg
                bb += ground_linear[2] * yp * ground_b * eb
                body = (br, bg, bb)

                hx, hy, hz = self._light_half[row][col]
                ndh = max(0.0, nx * hx + ny * hy + nz * hz)
                if spec_spread != 1.0:
                    # The whole lobe, knee included, stretched in angle.
                    ndh = cos(acos(ndh if ndh < 1.0 else 1.0) * spec_spread_inv)
                knee_curve = self._smoothstep(spec_knee, 1.0, ndh)
                spec_pos = max(0.0, min(1.0, ndh)) * spec_last
                i0 = int(spec_pos)
                i1 = spec_last if i0 >= spec_last else i0 + 1
                spec_pow = spec_lut[i0] + (spec_lut[i1] - spec_lut[i0]) * (spec_pos - i0)
                spec_term = illuminance * ndl * spec_pow * knee_curve
                spec_term *= spec_max * spec_gain
                spec_term = self._clamp(spec_term, 0.0, 4.0)
                body = self._add(body, self._mul(highlight_linear, spec_term))

                if mixer == "Additive":
                    body = self._add(body, self._mul((1.0, 1.0, 1.0), ndl * 0.06))
                elif mixer == "Multiplicative":
                    body = self._mul(body, 0.78 + 0.22 * ndl)
                brow.append(body)
            bodies.append(brow)
        self._material_body = bodies
        self._material_cache_key_value = key

    def _record_timing(self, name: str, ms: float) -> None:
        samples = self._timings.get(name)
        if samples is None:
            samples = self._timings[name] = []
        samples.append(ms)
        if len(samples) > 10:
            del samples[:-10]

    def clear_timing(self, *names: str) -> None:
        """Drop rolling timing samples for the named stages.

        Timings are last-10 averages, and some stages only record in one
        mode: ``smooth`` records nothing when the blur is bypassed and
        ``geometry`` nothing when the grid size is unchanged. Without a
        reset, a mode switch leaves the log reprinting the *previous* mode's
        costs indefinitely -- which once sent a real investigation after a
        22 ms geometry ghost and a 45 ms smooth ghost. Callers clear on
        render-profile transitions so each mode's log lines describe that
        mode.
        """
        for name in names:
            try:
                self._timings.pop(name, None)
            except Exception:  # pragma: no cover - diagnostics only
                pass

    def _log_timings(self, width: int, height: int, total_ms: float) -> None:
        now = time.perf_counter()
        if now - getattr(self, "_last_timing_log", 0.0) < 2.0:
            return
        self._last_timing_log = now
        parts = []
        for name, samples in sorted(self._timings.items()):
            if samples:
                parts.append("%s=%.1f" % (name, sum(samples) / len(samples)))
        LOG.debug("render %dx%d total=%.1fms %s",
                  width, height, total_ms, " ".join(parts))

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Rendering (issue #56: the fast pipeline)
    # ------------------------------------------------------------------
    # render() and render_bgra() run the pipeline on flat per-channel lists,
    # only over the pixels inside the sphere, with the per-pixel terms that
    # depend on geometry and light cached with the light. A colour change (a
    # Hue / Sat / Value drag, the commonest edit) then reruns one short loop.
    # Every expression keeps the reference order, so the output is
    # bit-identical to render_reference(), the original implementation, kept
    # for the diagnostics and for the test that holds the two together.

    def render(self, base_color: Sequence[float], width: int, height: int) -> List[List[Pixel]]:
        """Render to rows of (r, g, b) ints (outside the sphere: (0, 0, 0))."""
        width = max(2, int(width))
        height = max(2, int(height))
        if not self._fast_ok():
            return self.render_reference(base_color, width, height)
        red, green, blue = self._fast_render(base_color, width, height)[:3]
        return [list(zip(red[r * width:(r + 1) * width], green[r * width:(r + 1) * width],
                         blue[r * width:(r + 1) * width])) for r in range(height)]

    def render_bgra(self, base_color: Sequence[float], width: int, height: int) -> bytearray:
        """Render straight to premultiplied-free BGRA bytes with the analytic
        circle coverage in alpha; antialiased edge pixels take the colour of
        their nearest interior pixel (as the processor's packer did)."""
        width = max(2, int(width))
        height = max(2, int(height))
        if not self._fast_ok():
            return None
        red, green, blue, geo = self._fast_render(base_color, width, height)
        for dst, src in geo["edge_src"]:
            red[dst] = red[src]
            green[dst] = green[src]
            blue[dst] = blue[src]
        raw = bytearray(width * height * 4)
        raw[0::4] = bytes(blue)
        raw[1::4] = bytes(green)
        raw[2::4] = bytes(red)
        raw[3::4] = geo["alpha"]
        return raw

    def _fast_ok(self) -> bool:
        """The fast path covers the stock material stage only; an engine that
        overrides it (the diagnostics) renders through render_reference()."""
        cls = type(self)
        return (cls._build_material_stage is ColorEngine._build_material_stage
                and cls.render is ColorEngine.render)

    def _fast_geometry(self, width: int, height: int) -> dict:
        """Per-size tables: the sphere's row spans, flat normals, alpha bytes,
        each edge pixel's nearest interior pixel, blur normalisers. Built
        from the same grids as the reference, once per size."""
        cache = self.__dict__.setdefault("_fast_geo_cache", {})
        geo = cache.get((width, height))
        if geo is not None:
            return geo
        mask, cov = self._mask_grid, self._coverage_grid
        spans = []
        for row in range(height):
            mrow = mask[row]
            cols = [c for c in range(width) if mrow[c]]
            if cols:
                # The circle's interior is one run per row.
                assert cols[-1] - cols[0] + 1 == len(cols)
                spans.append((row, cols[0], cols[-1] + 1))
        n = width * height
        flat_mask = [0.0] * n
        for row, c0, c1 in spans:
            base = row * width
            for c in range(c0, c1):
                flat_mask[base + c] = 1.0
        alpha = bytearray(n)
        edge_src = []
        for row in range(height):
            mrow, crow = mask[row], cov[row]
            for col in range(width):
                a = crow[col]
                if mrow[col] or a > 0.0:
                    alpha[row * width + col] = int(max(0.0, min(1.0, a)) * 255.0 + 0.5)
                if not mrow[col] and a > 0.0:
                    src = self._nearest_inside(mask, row, col, width, height)
                    if src is not None:
                        edge_src.append((row * width + col, src[0] * width + src[1]))
        geo = {"spans": spans, "mask": flat_mask, "alpha": bytes(alpha),
               "edge_src": edge_src, "blur": {}}
        cache[(width, height)] = geo
        while len(cache) > 6:
            cache.pop(next(iter(cache)))
        return geo

    @staticmethod
    def _nearest_inside(mask, row, col, width, height):
        """The processor's nearest-shade search, returning the position."""
        for radius in range(1, max(width, height)):
            for dr in range(-radius, radius + 1):
                dcs = range(-radius, radius + 1) if abs(dr) == radius else (-radius, radius)
                for dc in dcs:
                    r, c = row + dr, col + dc
                    if 0 <= r < height and 0 <= c < width and mask[r][c]:
                        return r, c
        return None

    def _fast_lighting(self, width: int, height: int, geo: dict) -> dict:
        """Per-pixel terms that depend on geometry, the light and the
        material's non-colour settings, not on the colours. Cached by key."""
        key = (self._light_cache_key_value, self.spec_max, self.shininess, self.spec_knee,
               self.mixer_mode, self.ambient, self.SPEC_GAIN, self.AREA_SIZE,
               self.DIFFUSE_FLOOR, self.DIFFUSE_GAIN, self.DIFFUSE_LEVEL, self.FLOOR_SOFTNESS,
               self.AMBIENT_SCALE, self.AMBIENT_SKY_MIX, self.REFLECT_STRENGTH,
               self.REFLECT_POWER, self.REFLECT_SPREAD, self.REFLECT_START, self.REFLECT_END,
               self.SHADOW_HUE_MAX, self.SHADOW_HUE_LUT_SIZE, self.SPEC_LUT_SIZE,
               self.TONAL_BASE, self.TONAL_LIGHT_POWER, self.TONAL_SHADOW_START,
               self.TONAL_SHADOW_MIX, self.TONAL_LIGHT_MAX, width, height)
        lit = self.__dict__.get("_fast_light")
        if lit is not None and lit["key"] == key:
            return lit
        if getattr(self, "_spec_lut_value", None) != float(self.shininess):
            self._build_spec_lut()
        spec_lut = self._spec_lut
        spec_last = len(spec_lut) - 1
        spec_knee = max(self.SPEC_KNEE_MIN, min(self.SPEC_KNEE_MAX, self.spec_knee))
        spec_max = self.spec_max
        spec_gain = self.SPEC_GAIN
        spec_spread = self._area_spec_spread()
        spec_spread_inv = 1.0 / spec_spread
        spec_gain *= spec_spread_inv * spec_spread_inv
        acos, cos = math.acos, math.cos
        tonal_lut = self._tonal_lut()
        tonal_last = len(tonal_lut) - 1
        ambient_base = self.ambient * self.AMBIENT_SCALE
        ambient_k = self.ambient * 0.35
        ambient_sky_mix = self.AMBIENT_SKY_MIX
        diffuse_floor = self.DIFFUSE_FLOOR
        diffuse_gain = self.DIFFUSE_GAIN
        diffuse_level = self.DIFFUSE_LEVEL
        if diffuse_level <= 0.0:
            diffuse_level = 2.0 * diffuse_gain / (1.0 + diffuse_gain)
        diffuse_k = diffuse_level * (1.0 + diffuse_gain)
        floor_soft = self.FLOOR_SOFTNESS
        hue_on = self.SHADOW_HUE_MAX > 0.0
        hue_last = self.SHADOW_HUE_LUT_SIZE - 1
        reflect_k = self.REFLECT_STRENGTH
        reflect_p = self.REFLECT_POWER
        reflect_s = self.REFLECT_SPREAD
        reflect_a = self.REFLECT_START
        reflect_span = self.REFLECT_END - reflect_a
        mixer = self.mixer_mode
        hypot = math.hypot
        n = width * height
        # Flat, full-size, so the colour pass indexes them like its output.
        stb_l = [0.0] * n; btl_l = [0.0] * n; hue_l = [0] * n; bf_l = [0.0] * n
        mix_l = [0.0] * n; sk_l = [0.0] * n; rk_l = [0.0] * n; yn_l = [0.0] * n
        yp_l = [0.0] * n; spec_l = [0.0] * n; mx_l = [0.0] * n; ndl_l = [0.0] * n
        nv_l = [0.0] * n; ill_l = [0.0] * n; sf_l = [0.0] * n
        nxy_l = geo.get("nxy")
        if nxy_l is None:
            nxy_l = [0.0] * n
            for row, c0, c1 in geo["spans"]:
                nrm_row = self._normal_grid[row]
                for col in range(c0, c1):
                    nxy_l[row * width + col] = hypot(nrm_row[col][0], nrm_row[col][1])
            geo["nxy"] = nxy_l
        last_ldir = None
        lxy = 0.0
        for row, c0, c1 in geo["spans"]:
            self._check_cancel()
            nrm_row = self._normal_grid[row]
            lrow = self._light_ldir[row]
            erow = self._light_illum[row]
            drow = self._light_ndl[row]
            hrow = self._light_half[row]
            base = row * width
            for col in range(c0, c1):
                i = base + col
                nx, ny, nz = nrm_row[col]
                ldir = lrow[col]
                illuminance = erow[col]
                ndl = drow[col]
                ndlc = ndl if ndl < 1.0 else 1.0
                if ndlc < 0.0:
                    ndlc = 0.0
                stb_l[i], btl_l[i] = tonal_lut[int(ndlc * tonal_last + 0.5)]
                if hue_on:
                    sn = nx * ldir[0] + ny * ldir[1] + nz * ldir[2]
                    hue_l[i] = int((sn + 1.0) * 0.5 * hue_last + 0.5)
                de = illuminance * ndl
                gde = diffuse_gain * de
                direct_factor = diffuse_k * de / (1.0 + gde) if de > 0.0 else 0.0
                st = ndl / 0.55
                if st < 0.0:
                    st = 0.0
                elif st > 1.0:
                    st = 1.0
                shadow_factor = 1.0 - st * st * (3.0 - 2.0 * st)
                sf_l[i] = shadow_factor
                ambient_factor = ambient_base + shadow_factor * ambient_k
                if floor_soft > 0.0:
                    gap = direct_factor - diffuse_floor
                    h = floor_soft - (gap if gap > 0.0 else -gap)
                    body_factor = direct_factor if gap > 0.0 else diffuse_floor
                    if h > 0.0:
                        body_factor += h * h * 0.25 / floor_soft
                else:
                    body_factor = direct_factor
                    if body_factor < diffuse_floor:
                        body_factor = diffuse_floor
                bf_l[i] = body_factor
                mix_l[i] = ambient_factor * ambient_sky_mix
                sk_l[i] = ambient_factor * 0.05
                # Lit side (shadow_factor 0): the reflected term is exactly 0.
                if reflect_k > 0.0 and shadow_factor != 0.0:
                    edge = 1.0 - (nz if nz > 0.0 else 0.0)
                    if reflect_span > 0.0:
                        rt = (edge - reflect_a) / reflect_span
                        rt = 0.0 if rt < 0.0 else (1.0 if rt > 1.0 else rt)
                        rk = reflect_k * shadow_factor * rt * rt * (3.0 - 2.0 * rt)
                    else:
                        rk = reflect_k * shadow_factor * edge ** reflect_p
                    if reflect_s > 0.0:
                        if ldir is not last_ldir:
                            last_ldir = ldir
                            lxy = hypot(ldir[0], ldir[1])
                        nxy = nxy_l[i]
                        if lxy > 1e-6 and nxy > 1e-6:
                            away = -(nx * ldir[0] + ny * ldir[1]) / (lxy * nxy)
                            rk *= (away if away > 0.0 else 0.0) ** reflect_s
                    rk_l[i] = rk
                yn = -ny
                yn_l[i] = yn if yn > 0.0 else 0.0
                yp_l[i] = ny if ny > 0.0 else 0.0
                # Unlit (N.L 0) or below the knee: the specular is exactly 0.
                if ndl != 0.0 and illuminance != 0.0:
                    hx, hy, hz = hrow[col]
                    ndh = nx * hx + ny * hy + nz * hz
                    ndh = ndh if ndh > 0.0 else 0.0
                    if spec_spread != 1.0:
                        ndh = cos(acos(ndh if ndh < 1.0 else 1.0) * spec_spread_inv)
                    if spec_knee == 1.0:
                        knee_curve = 1.0 if ndh >= 1.0 else 0.0
                    else:
                        kt = (ndh - spec_knee) / (1.0 - spec_knee)
                        kt = 0.0 if kt < 0.0 else (1.0 if kt > 1.0 else kt)
                        knee_curve = kt * kt * (3.0 - 2.0 * kt)
                    if knee_curve != 0.0:
                        spec_pos = (0.0 if ndh < 0.0 else (1.0 if ndh > 1.0 else ndh)) * spec_last
                        i0 = int(spec_pos)
                        i1 = spec_last if i0 >= spec_last else i0 + 1
                        spec_pow = spec_lut[i0] + (spec_lut[i1] - spec_lut[i0]) * (spec_pos - i0)
                        spec_term = illuminance * ndl * spec_pow * knee_curve
                        spec_term *= spec_max * spec_gain
                        spec_l[i] = 0.0 if spec_term < 0.0 else (4.0 if spec_term > 4.0 else spec_term)
                if mixer == "Additive":
                    mx_l[i] = ndl * 0.06
                elif mixer == "Multiplicative":
                    mx_l[i] = 0.78 + 0.22 * ndl
                ndl_l[i] = ndl
                nv_l[i] = nz if nz > 0.0 else 0.0
                ill_l[i] = illuminance
        lit = {"key": key, "stb": stb_l, "btl": btl_l, "hue": hue_l, "bf": bf_l,
               "mix": mix_l, "sk": sk_l, "rk": rk_l, "yn": yn_l, "yp": yp_l,
               "spec": spec_l, "mx": mx_l, "ndl": ndl_l, "nv": nv_l, "ill": ill_l,
               "sf": sf_l, "accent": {}}
        self._fast_light = lit
        return lit

    def _fast_accents(self, lit: dict, geo: dict) -> tuple:
        """Rim and glow weights per pixel (geometry x light x their settings)."""
        glow_power = max(1.0, self.glow_radius / 4.0)
        key = (self.rim_light, self.glow_intensity, glow_power)
        acc = lit["accent"].get(key)
        if acc is not None:
            return acc
        rim_k = self.rim_light
        glow_on = self.glow_intensity > 0.0
        glow_intensity = self.glow_intensity
        n = len(lit["ndl"])
        rim_l = [0.0] * n
        glow_l = [0.0] * n if glow_on else None
        ndl_l, nv_l, ill_l = lit["ndl"], lit["nv"], lit["ill"]
        for row, c0, c1 in geo["spans"]:
            for i in range(row * self._fast_width + c0, row * self._fast_width + c1):
                ndl = ndl_l[i]
                nv = nv_l[i]
                r = 1.0 - nv
                r2 = r * r
                rim = r2 * r2 * r * ndl * rim_k
                e = ill_l[i]
                rim *= 0.5 + 0.5 * (e if e < 2.0 else 2.0)
                rim_l[i] = rim
                if glow_on:
                    glow_l[i] = ((1.0 - nv) ** glow_power) * ndl * glow_intensity
        acc = (rim_l, glow_l)
        lit["accent"] = {key: acc}
        return acc

    def _fast_render(self, base_color, width: int, height: int):
        """The pipeline up to 8-bit channels: flat red, green, blue lists
        (0 outside the sphere), plus the per-size tables."""
        t_total = time.perf_counter()
        if self._grid_width != width or self._grid_height != height:
            if not self._restore_geometry(width, height):
                t_geo = time.perf_counter()
                self._generate_geometry(width, height)
                self._compute_shading()
                self._record_timing("geometry", (time.perf_counter() - t_geo) * 1000.0)
            else:
                self._compute_shading()
        self._fast_width = width
        base_srgb = self._coerce_color(base_color)
        base_linear = self._to_linear(base_srgb)
        shadow_linear = self._to_linear(self.shadow_color)
        light_linear = self._to_linear(self.light_color)
        ambient_linear = self._to_linear(self.ambient_color)
        highlight_linear = self._to_linear(self._effective_highlight(base_srgb))
        sky_linear = self._to_linear(self.AMBIENT_SKY)
        ground_linear = self._to_linear(self.GROUND_COLOR)

        t_light = time.perf_counter()
        self._build_light_stage(width, height)
        self._record_timing("light", (time.perf_counter() - t_light) * 1000.0)
        geo = self._fast_geometry(width, height)

        t_mat = time.perf_counter()
        lit = self._fast_lighting(width, height, geo)
        rim_l, glow_l = self._fast_accents(lit, geo)
        n = width * height
        out_r = [0.0] * n
        out_g = [0.0] * n
        out_b = [0.0] * n
        s0, s1, s2 = shadow_linear
        b0_, b1_, b2_ = base_linear
        l0, l1, l2 = light_linear
        hl0, hl1, hl2 = highlight_linear
        sky0, sky1, sky2 = sky_linear
        gr0, gr1, gr2 = ground_linear
        atr = shadow_linear[0] + (ambient_linear[0] - shadow_linear[0]) * 0.35
        atg = shadow_linear[1] + (ambient_linear[1] - shadow_linear[1]) * 0.35
        atb = shadow_linear[2] + (ambient_linear[2] - shadow_linear[2]) * 0.35
        env_k = self.ENV_ALBEDO
        env_on = env_k > 0.0
        reflect_on = self.REFLECT_STRENGTH > 0.0
        sky_b = self.sky_bounce
        ground_b = self.ground_bounce
        hue_lut = (self._shadow_hue_lut(shadow_linear, base_linear)
                   if self.SHADOW_HUE_MAX > 0.0 else None)
        mixer = self.mixer_mode
        additive = mixer == "Additive"
        multiplicative = mixer == "Multiplicative"
        contrast_lut = self._contrast_lut
        clut_last = len(contrast_lut) - 1
        saturation = self.saturation
        brightness = self.brightness
        grain_on = self.grain > 0.0
        grain_amount = self.grain / 100.0
        grain_boost = 1.0 + self.DARK_GRAIN_BOOST
        grain_depth = self.GRAIN_DEPTH
        noise = self._noise
        rim_color = self._lerp(light_linear, sky_linear, self.rim_sky_mix)
        rc0, rc1, rc2 = rim_color
        glow_on = glow_l is not None
        glow_gain = self.GLOW_GAIN
        shoulder = self.TONE_SHOULDER
        span = 1.0 - shoulder
        inv_span = 1.0 / span
        exp = math.exp
        stb_l, btl_l, hue_l, bf_l = lit["stb"], lit["btl"], lit["hue"], lit["bf"]
        mix_l, sk_l, rk_l, yn_l, yp_l = lit["mix"], lit["sk"], lit["rk"], lit["yn"], lit["yp"]
        spec_l, mx_l, ndl_l, sf_l = lit["spec"], lit["mx"], lit["ndl"], lit["sf"]
        for row, c0, c1 in geo["spans"]:
            self._check_cancel()
            base = row * width
            for i in range(base + c0, base + c1):
                # --- material (colours) ---
                stb = stb_l[i]
                btl = btl_l[i]
                if hue_lut is not None:
                    t0r, t0g, t0b = hue_lut[hue_l[i]]
                else:
                    t0r = s0 + (b0_ - s0) * stb
                    t0g = s1 + (b1_ - s1) * stb
                    t0b = s2 + (b2_ - s2) * stb
                tcr = t0r + (l0 - t0r) * btl
                tcg = t0g + (l1 - t0g) * btl
                tcb = t0b + (l2 - t0b) * btl
                bf = bf_l[i]
                mix = mix_l[i]
                br = tcr * bf + atr * mix
                bg = tcg * bf + atg * mix
                bb = tcb * bf + atb * mix
                sk = sk_l[i]
                if env_on:
                    er = 1.0 - env_k + env_k * tcr
                    eg = 1.0 - env_k + env_k * tcg
                    eb = 1.0 - env_k + env_k * tcb
                else:
                    er = eg = eb = 1.0
                br += sky0 * sk * er
                bg += sky1 * sk * eg
                bb += sky2 * sk * eb
                if reflect_on:
                    rk = rk_l[i]
                    br += b0_ * rk
                    bg += b1_ * rk
                    bb += b2_ * rk
                yn = yn_l[i]
                yp = yp_l[i]
                br += sky0 * yn * sky_b * er
                bg += sky1 * yn * sky_b * eg
                bb += sky2 * yn * sky_b * eb
                br += gr0 * yp * ground_b * er
                bg += gr1 * yp * ground_b * eg
                bb += gr2 * yp * ground_b * eb
                st = spec_l[i]
                br = br + hl0 * st
                bg = bg + hl1 * st
                bb = bb + hl2 * st
                if additive:
                    m = mx_l[i]
                    br = br + 1.0 * m
                    bg = bg + 1.0 * m
                    bb = bb + 1.0 * m
                elif multiplicative:
                    m = mx_l[i]
                    br = br * m
                    bg = bg * m
                    bb = bb * m
                # --- display ---
                luma = 0.2126 * br + 0.7152 * bg + 0.0722 * bb
                if luma > 1.0e-8:
                    cl = luma if luma < 1.0 else 1.0
                    if cl < 0.0:
                        cl = 0.0
                    f = contrast_lut[int(cl * clut_last + 0.5)] / luma
                    br = br * f
                    bg = bg * f
                    bb = bb * f
                else:
                    br = bg = bb = 0.0
                luma = 0.2126 * br + 0.7152 * bg + 0.0722 * bb
                br = luma + (br - luma) * saturation
                bg = luma + (bg - luma) * saturation
                bb = luma + (bb - luma) * saturation
                if brightness != 1.0:
                    br = br * brightness
                    bg = bg * brightness
                    bb = bb * brightness
                if grain_on:
                    amount = grain_amount
                    if sf_l[i] > 0.5:
                        amount *= grain_boost
                    nz_ = noise(row, i - base)
                    scale = grain_depth * amount
                    br = max(0.0, br + nz_ * scale)
                    bg = max(0.0, bg + nz_ * scale)
                    bb = max(0.0, bb + nz_ * scale)
                rim = rim_l[i]
                if rim != 0.0:
                    br = br + rc0 * rim
                    bg = bg + rc1 * rim
                    bb = bb + rc2 * rim
                if glow_on:
                    glow = glow_l[i]
                    if glow != 0.0:
                        gk = glow * glow_gain
                        br = br + hl0 * gk
                        bg = bg + hl1 * gk
                        bb = bb + hl2 * gk
                out_r[i] = (br if br <= shoulder else shoulder + span * (1.0 - exp(-(br - shoulder) * inv_span))) if br > 0.0 else 0.0
                out_g[i] = (bg if bg <= shoulder else shoulder + span * (1.0 - exp(-(bg - shoulder) * inv_span))) if bg > 0.0 else 0.0
                out_b[i] = (bb if bb <= shoulder else shoulder + span * (1.0 - exp(-(bb - shoulder) * inv_span))) if bb > 0.0 else 0.0
        self._record_timing("material", (time.perf_counter() - t_mat) * 1000.0)

        if self.smooth > 0.0:
            t_smooth = time.perf_counter()
            radius = max(0, min(2, int(int(round(self.smooth)))))
            if radius > 0:
                out_r, out_g, out_b = self._fast_blur(out_r, out_g, out_b, radius, width, height, geo)
            self._record_timing("smooth", (time.perf_counter() - t_smooth) * 1000.0)

        t_srgb = time.perf_counter()
        lut8 = self._srgb_lut8()
        last = len(lut8) - 1
        mask = geo["mask"]
        red = [lut8[int((0.0 if v < 0.0 else (1.0 if v > 1.0 else v)) * last + 0.5)] if m else 0
               for v, m in zip(out_r, mask)]
        green = [lut8[int((0.0 if v < 0.0 else (1.0 if v > 1.0 else v)) * last + 0.5)] if m else 0
                 for v, m in zip(out_g, mask)]
        blue = [lut8[int((0.0 if v < 0.0 else (1.0 if v > 1.0 else v)) * last + 0.5)] if m else 0
                for v, m in zip(out_b, mask)]
        self._record_timing("srgb", (time.perf_counter() - t_srgb) * 1000.0)
        total_ms = (time.perf_counter() - t_total) * 1000.0
        self._record_timing("total", total_ms)
        self._log_timings(width, height, total_ms)
        return red, green, blue, geo

    @classmethod
    def _srgb_lut8(cls) -> List[int]:
        """The sRGB table already rounded to 8 bits, exactly as the reference
        rounds each lookup."""
        lut = cls.__dict__.get("_SRGB_LUT8")
        if lut is None:
            lut = [int(v * 255.0 + 0.5) for v in cls._srgb_lut()]
            cls._SRGB_LUT8 = lut
        return lut

    def _fast_blur(self, rr, gg, bb, radius, width, height, geo):
        """The masked separable blur of _soften_linear on flat channels:
        same kernel, same edge replication, same summation order, so the
        result is bit-identical. Each tap is a shifted slice; the per-pixel
        normalisers depend only on the mask and are cached per size."""
        if radius == 1:
            kernel = (1.0 / 4.0, 2.0 / 4.0, 1.0 / 4.0)
        else:
            sixth = 1.0 / 16.0
            kernel = (sixth, 4.0 * sixth, 6.0 * sixth, 4.0 * sixth, sixth)
        k = len(kernel)
        off = k // 2
        mask = geo["mask"]
        w, h = width, height
        n = w * h
        inv = geo["blur"].get(radius)
        if inv is None:
            inv_h = self._blur_pass(mask, kernel, w, h, horizontal=True)
            inv_v = self._blur_pass(mask, kernel, w, h, horizontal=False)
            inv = ([1.0 / a if m else 0.0 for a, m in zip(inv_h, mask)],
                   [1.0 / a if m else 0.0 for a, m in zip(inv_v, mask)])
            geo["blur"][radius] = inv
        inv_h, inv_v = inv
        border_free = geo.get("border_free")
        if border_free is None:
            border_free = not any(mask[i] for i in range(w)) and \
                not any(mask[i] for i in range(n - w, n)) and \
                not any(mask[r * w] or mask[r * w + w - 1] for r in range(h))
            geo["border_free"] = border_free
        out = []
        for ch in (rr, gg, bb):
            if border_free:
                # Nothing on the border is inside the sphere, so every
                # replicated edge tap is a 0 and plain shifts are exact.
                hv = self._blur_flat(ch, kernel, 1, n, inv_h)
                out.append(self._blur_flat(hv, kernel, w, n, inv_v))
            else:
                hsum = self._blur_pass(ch, kernel, w, h, horizontal=True)
                hv = [a * b for a, b in zip(hsum, inv_h)]
                vsum = self._blur_pass(hv, kernel, w, h, horizontal=False)
                out.append([a * b for a, b in zip(vsum, inv_v)])
        return out[0], out[1], out[2]

    @staticmethod
    def _blur_flat(vals, kernel, stride, n, inv):
        """sum(v[i + (k - off) * stride] * kernel[k]) * inv[i] with zero
        padding, in the reference's tap order."""
        k = len(kernel)
        off = k // 2
        pad = [0.0] * (off * stride)
        padded = pad + vals + pad
        taps = [padded[j * stride:j * stride + n] for j in range(k)]
        if k == 5:
            w0, w1, w2, w3, w4 = kernel
            return [(0.0 + a * w0 + b * w1 + c * w2 + d * w3 + e * w4) * iv
                    for a, b, c, d, e, iv in zip(*taps, inv)]
        w0, w1, w2 = kernel
        return [(0.0 + a * w0 + b * w1 + c * w2) * iv for a, b, c, iv in zip(*taps, inv)]

    @staticmethod
    def _blur_pass(vals, kernel, w, h, horizontal):
        """One pass of sum(v[k] * kernel[k]) over the taps, edge-replicated,
        accumulated in tap order from 0.0 (as the reference does)."""
        k = len(kernel)
        off = k // 2
        if horizontal:
            out = []
            ext = out.extend
            for r in range(h):
                row = vals[r * w:(r + 1) * w]
                padded = [row[0]] * off + row + [row[-1]] * off
                taps = [padded[j:j + w] for j in range(k)]
                if k == 5:
                    w0, w1, w2, w3, w4 = kernel
                    ext([0.0 + a * w0 + b * w1 + c * w2 + d * w3 + e * w4
                         for a, b, c, d, e in zip(*taps)])
                else:
                    w0, w1, w2 = kernel
                    ext([0.0 + a * w0 + b * w1 + c * w2 for a, b, c in zip(*taps)])
            return out
        first, lastrow = vals[0:w], vals[(h - 1) * w:h * w]
        padded = first * off + vals + lastrow * off
        n = w * h
        taps = [padded[j * w:j * w + n] for j in range(k)]
        if k == 5:
            w0, w1, w2, w3, w4 = kernel
            return [0.0 + a * w0 + b * w1 + c * w2 + d * w3 + e * w4
                    for a, b, c, d, e in zip(*taps)]
        w0, w1, w2 = kernel
        return [0.0 + a * w0 + b * w1 + c * w2 for a, b, c in zip(*taps)]

    def render_reference(self, base_color: Sequence[float], width: int, height: int) -> List[List[Pixel]]:
        """The original pipeline, unchanged: the reference the fast one is
        held to, and the path for engines that override the material stage."""
        width = max(2, int(width))
        height = max(2, int(height))
        t_total = time.perf_counter()

        if self._grid_width != width or self._grid_height != height:
            if not self._restore_geometry(width, height):
                t_geo = time.perf_counter()
                self._generate_geometry(width, height)
                self._compute_shading()
                self._record_timing("geometry", (time.perf_counter() - t_geo) * 1000.0)
            else:
                # Warm size: shading hits its own cache (keyed on size and
                # light direction), so the switch costs a dict lookup.
                self._compute_shading()

        # Input colors are sRGB values at the plugin boundary. Convert them once
        # and use linear RGB for all interpolation and lighting.
        base_srgb = self._coerce_color(base_color)
        base_linear = self._to_linear(base_srgb)
        shadow_linear = self._to_linear(self.shadow_color)
        light_linear = self._to_linear(self.light_color)
        ambient_linear = self._to_linear(self.ambient_color)
        highlight_linear = self._to_linear(self._effective_highlight(base_srgb))
        sky_linear = self._to_linear(self.AMBIENT_SKY)
        ground_linear = self._to_linear(self.GROUND_COLOR)

        t_light = time.perf_counter()
        self._build_light_stage(width, height)
        self._record_timing("light", (time.perf_counter() - t_light) * 1000.0)

        t_mat = time.perf_counter()
        light_key = self._light_cache_key_value
        mat_key = self._material_key(light_key, base_srgb)
        self._build_material_stage(width, height, mat_key, (
            shadow_linear, base_linear, light_linear, ambient_linear,
            highlight_linear, sky_linear, ground_linear))
        self._record_timing("material", (time.perf_counter() - t_mat) * 1000.0)

        t_disp = time.perf_counter()
        contrast_lut = self._contrast_lut
        clut_last = len(contrast_lut) - 1
        saturation = self.saturation
        brightness = self.brightness
        grain_on = self.grain > 0.0
        grain_amount = self.grain / 100.0
        grain_boost = 1.0 + self.DARK_GRAIN_BOOST
        grain_depth = self.GRAIN_DEPTH
        rim_light = self.rim_light
        rim_sky_mix = self.rim_sky_mix
        glow_on = self.glow_intensity > 0.0
        glow_intensity = self.glow_intensity
        glow_gain = self.GLOW_GAIN
        glow_power = max(1.0, self.glow_radius / 4.0)
        rim_k = rim_light
        # rim tint is constant across the sphere for a render.
        rim_color = self._lerp(light_linear, sky_linear, rim_sky_mix)

        output_linear: List[List[Color]] = []
        shoulder = self.TONE_SHOULDER
        span = 1.0 - shoulder
        inv_span = 1.0 / span
        exp = math.exp

        for row in range(height):
            self._check_cancel()
            out_row: List[Color] = []
            mrow = self._mask_grid[row]
            nrm_row = self._normal_grid[row]
            brow = self._material_body[row]
            drow = self._light_ndl[row]
            erow = self._light_illum[row]

            for col in range(width):
                if not mrow[col]:
                    out_row.append((0.0, 0.0, 0.0))
                    continue

                body = brow[col]
                ndl = drow[col]
                nz = nrm_row[col][2]
                nv = nz if nz > 0.0 else 0.0

                # Contrast via table lookup.
                luma = 0.2126 * body[0] + 0.7152 * body[1] + 0.0722 * body[2]
                if luma > 1.0e-8:
                    cl = luma if luma < 1.0 else 1.0
                    if cl < 0.0:
                        cl = 0.0
                    body = self._mul(body, contrast_lut[int(cl * clut_last + 0.5)] / luma)
                else:
                    body = (0.0, 0.0, 0.0)

                # Saturation / brightness (body grading complete).
                luma = 0.2126 * body[0] + 0.7152 * body[1] + 0.0722 * body[2]
                body = (
                    luma + (body[0] - luma) * saturation,
                    luma + (body[1] - luma) * saturation,
                    luma + (body[2] - luma) * saturation,
                )
                if brightness != 1.0:
                    body = self._mul(body, brightness)

                # Grain at controlled strength on the graded body.
                if grain_on:
                    shadow_factor = 1.0 - self._smoothstep(0.0, 0.55, ndl)
                    amount = grain_amount
                    if shadow_factor > 0.5:
                        amount *= grain_boost
                    noise = self._noise(row, col)
                    scale = grain_depth * amount
                    body = (
                        max(0.0, body[0] + noise * scale),
                        max(0.0, body[1] + noise * scale),
                        max(0.0, body[2] + noise * scale),
                    )

                # Rim accent (exact multiplies, no pow).
                r = 1.0 - nv
                r2 = r * r
                rim = r2 * r2 * r * ndl * rim_k
                rim *= 0.5 + 0.5 * (erow[col] if erow[col] < 2.0 else 2.0)
                if rim != 0.0:
                    body = (body[0] + rim_color[0] * rim,
                            body[1] + rim_color[1] * rim,
                            body[2] + rim_color[2] * rim)

                # Glow accent.
                if glow_on:
                    g = 1.0 - nv
                    glow = (g ** glow_power) * ndl * glow_intensity
                    if glow != 0.0:
                        gk = glow * glow_gain
                        body = (body[0] + highlight_linear[0] * gk,
                                body[1] + highlight_linear[1] * gk,
                                body[2] + highlight_linear[2] * gk)

                # Inline display roll-off (see TONE_SHOULDER): identity up to
                # the shoulder, exponential ease to 1 above it.
                b0 = body[0]
                b1 = body[1]
                b2 = body[2]
                out_row.append((
                    (b0 if b0 <= shoulder else shoulder + span * (1.0 - exp(-(b0 - shoulder) * inv_span))) if b0 > 0.0 else 0.0,
                    (b1 if b1 <= shoulder else shoulder + span * (1.0 - exp(-(b1 - shoulder) * inv_span))) if b1 > 0.0 else 0.0,
                    (b2 if b2 <= shoulder else shoulder + span * (1.0 - exp(-(b2 - shoulder) * inv_span))) if b2 > 0.0 else 0.0,
                ))

            output_linear.append(out_row)
        self._record_timing("display", (time.perf_counter() - t_disp) * 1000.0)


        # Image-space smoothing remains optional and bounded by the mask. The
        # sphere itself is still generated from exact analytic normals.
        if self.smooth > 0.0:
            t_smooth = time.perf_counter()
            radius = int(round(self.smooth))
            output_linear = self._soften_linear(output_linear, radius, width, height)
            self._record_timing("smooth", (time.perf_counter() - t_smooth) * 1000.0)

        pixels: List[List[Pixel]] = []
        srgb_lut = self._srgb_lut()
        srgb_last = len(srgb_lut) - 1
        t_srgb = time.perf_counter()
        for row in range(height):
            self._check_cancel()
            prow: List[Pixel] = []
            for col in range(width):
                if not self._mask_grid[row][col]:
                    prow.append((0, 0, 0))
                    continue
                lin = output_linear[row][col]
                prow.append((
                    int(srgb_lut[int(max(0.0, min(1.0, lin[0])) * srgb_last + 0.5)] * 255.0 + 0.5),
                    int(srgb_lut[int(max(0.0, min(1.0, lin[1])) * srgb_last + 0.5)] * 255.0 + 0.5),
                    int(srgb_lut[int(max(0.0, min(1.0, lin[2])) * srgb_last + 0.5)] * 255.0 + 0.5),
                ))
            pixels.append(prow)
        self._record_timing("srgb", (time.perf_counter() - t_srgb) * 1000.0)
        total_ms = (time.perf_counter() - t_total) * 1000.0
        self._record_timing("total", total_ms)
        self._log_timings(width, height, total_ms)
        return pixels

    # ------------------------------------------------------------------
    # Smoothing
    # ------------------------------------------------------------------
    def _soften_linear(self, grid: List[List[Color]], radius: int, width: int, height: int) -> List[List[Color]]:
        radius = max(0, min(2, int(radius)))
        if radius == 0:
            return grid

        if radius == 1:
            kernel = (1.0 / 4.0, 2.0 / 4.0, 1.0 / 4.0)
            offset = 1
        else:
            sixth = 1.0 / 16.0
            kernel = (sixth, 4.0 * sixth, 6.0 * sixth, 4.0 * sixth, sixth)
            offset = 2
        klen = len(kernel)
        mask = self._mask_grid
        wm1 = width - 1
        hm1 = height - 1

        horizontal: List[List[Color]] = [
            [(0.0, 0.0, 0.0) for _ in range(width)] for _ in range(height)
        ]

        for row in range(height):
            self._check_cancel()
            hrow_out = horizontal[row]
            mrow = mask[row]
            grow = grid[row]
            for col in range(width):
                if not mrow[col]:
                    continue
                ar = ag = ab = aw = 0.0
                for k in range(klen):
                    c = col + k - offset
                    if c < 0:
                        c = 0
                    elif c > wm1:
                        c = wm1
                    if mask[row][c]:
                        px = grow[c]
                        w = kernel[k]
                        ar += px[0] * w
                        ag += px[1] * w
                        ab += px[2] * w
                        aw += w
                if aw > 0.0:
                    inv = 1.0 / aw
                    hrow_out[col] = (ar * inv, ag * inv, ab * inv)

        result: List[List[Color]] = [
            [(0.0, 0.0, 0.0) for _ in range(width)] for _ in range(height)
        ]
        for row in range(height):
            self._check_cancel()
            rrow_out = result[row]
            for col in range(width):
                if not mask[row][col]:
                    continue
                ar = ag = ab = aw = 0.0
                for k in range(klen):
                    r = row + k - offset
                    if r < 0:
                        r = 0
                    elif r > hm1:
                        r = hm1
                    if mask[r][col]:
                        px = horizontal[r][col]
                        w = kernel[k]
                        ar += px[0] * w
                        ag += px[1] * w
                        ab += px[2] * w
                        aw += w
                if aw > 0.0:
                    inv = 1.0 / aw
                    rrow_out[col] = (ar * inv, ag * inv, ab * inv)
        return result

    # Kept for compatibility with tests that used the historical method.
    def _soften(self, grid, mask, width, height):
        return self._soften_linear(grid, int(round(self.smooth)), width, height)

    # ------------------------------------------------------------------
    # Compatibility helpers
    # ------------------------------------------------------------------
    def mask_grid(self):
        return self._mask_grid

    def coverage_grid(self):
        return self._coverage_grid

    def _rolloff(self, pixel: Color) -> Color:
        maximum = max(pixel)
        if maximum > 1.0:
            scale = 1.0 / (1.0 + maximum)
            return self._mul(pixel, scale)
        return pixel

    @staticmethod
    def _rgb_from_floats(r: float, g: float, b: float) -> Pixel:
        return (
            int(max(0.0, min(1.0, r)) * 255.0 + 0.5),
            int(max(0.0, min(1.0, g)) * 255.0 + 0.5),
            int(max(0.0, min(1.0, b)) * 255.0 + 0.5),
        )


# Module-level aliases kept for the pre-fix docker/tests, which did
# ``from .color_engine import SPEC_KNEE, SPEC_KNEE_MIN, SPEC_KNEE_MAX``.
SPEC_KNEE = ColorEngine.SPEC_KNEE
SPEC_KNEE_MIN = ColorEngine.SPEC_KNEE_MIN
SPEC_KNEE_MAX = ColorEngine.SPEC_KNEE_MAX
LIGHT_TYPES = ColorEngine.LIGHT_TYPES
MIXER_MODES = ColorEngine.MIXER_MODES
