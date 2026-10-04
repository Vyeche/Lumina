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
from typing import List, Sequence, Tuple


Color = Tuple[float, float, float]
Pixel = Tuple[int, int, int]
Normal = Tuple[float, float, float]


class ColorEngine:
    """Generate a shaded, front-facing 3D sphere as an RGB pixel grid."""

    LIGHT_TYPES = ("Point", "Sun", "Spot", "Area")
    MIXER_MODES = ("Blended", "Additive", "Multiplicative")

    # The old engine used 900 as a raw point-light value. That made the
    # inverse-square result several times larger than one and forced the output
    # through aggressive clipping/rolloff. Lumina instead uses a calibrated
    # reference flux: at LIGHT_DISTANCE the illuminance is exactly 1.0 before
    # the user-controlled intensity multiplier.
    LIGHT_DISTANCE = 3.0
    REFERENCE_POINT_FLUX = 4.0 * math.pi * (LIGHT_DISTANCE ** 2)
    REFERENCE_AREA_POWER = REFERENCE_POINT_FLUX

    # Blender-like spot defaults. spot_size is a full cone angle; the shader
    # compares against the corresponding half-angle around the center axis.
    # 35deg/0.25 keeps the sphere edge in penumbra so Spot differs from Point.
    # Sphere angular radius at light distance 3 is asin(1/3)=19.47deg; the old
    # 60deg/0.35 put the whole visible sphere inside the inner cone.
    SPOT_OUTER_DEG = 35.0
    SPOT_BLEND = 0.25

    # Nominal area emitter size. NOTE: the current center-sampled approximation
    # divides by area then multiplies back, so AREA_SIZE has no intensity
    # effect yet. Kept at 2.0 as the future default for true multi-sample
    # Square/Rectangle/Disk integration (spec spread, shadow softness).
    AREA_SIZE = 2.0

    AMBIENT_SCALE = 1.0
    AMBIENT_SKY = (0.72, 0.78, 0.92)
    AMBIENT_SKY_MIX = 0.18

    # Artistic defaults. These values are intentionally moderate because the
    # point-light energy is now normalized at the reference distance.
    DIFFUSE_FLOOR = 0.10
    DIFFUSE_GAMMA = 0.85
    WRAP_DIFFUSE = 0.35

    SPEC_MAX = 0.24
    # Immutable renderer default. Live state is self.spec_max (property).
    SPEC_MAX_DEFAULT = 0.24
    SPEC_TIGHT = 0.90
    SPEC_KNEE = 0.22
    SPEC_KNEE_MIN = 0.02
    SPEC_KNEE_MAX = 0.95

    RIM_POWER = 5.0
    RIM_LIGHT = 0.14
    # Immutable rim default. Live state is self.rim_light (property).
    RIM_LIGHT_DEFAULT = 0.14
    RIM_TOP_WEIGHT = 0.35
    RIM_BOTTOM_GAIN = 1.0
    RIM_SHADOW_LIFT = 0.0

    DARK_FALLOFF = 0.80
    DARK_WASH = 0.06
    DARK_AMBIENT_FLOOR = 0.25

    DARK_GRAIN_BOOST = 2.0
    DARK_GRAIN_ABS = 0.05
    GRAIN_DEPTH = 0.12

    def __init__(self, resolution: int = 256):
        self.resolution = max(8, min(int(resolution), 512))

        # User-facing controls.
        self.ambient = 0.10
        # Azimuth convention: 0 = right, 90 = bottom, 270 = top.
        # 274 mirrors the requested 86 across the horizontal: same dial
        # position, but the key light comes from above as painters expect.
        self.light_azimuth = 287.0
        self.light_elevation = 45.0
        self.shininess = 3.52
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

        self._normal_grid: List[List[Normal]] = []
        self._mask_grid: List[List[bool]] = []
        self._coverage_grid: List[List[float]] = []
        self._grid_width = 0
        self._grid_height = 0

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

    def set_spec_knee(self, value) -> None:
        self.spec_knee = max(self.SPEC_KNEE_MIN, min(self.SPEC_KNEE_MAX, float(value)))
        self._compute_shading()

    def set_contrast(self, value) -> None:
        self.contrast = max(0.0, float(value))
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

        dx = 2.0 / float(width - 1)
        dy = 2.0 / float(height - 1)
        pixel_half_diagonal = 0.5 * math.sqrt(dx * dx + dy * dy)

        for row in range(height):
            y = -1.0 + 2.0 * row / float(height - 1)
            normal_row: List[Normal] = []
            mask_row: List[bool] = []
            coverage_row: List[float] = []

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
                else:
                    normal = (0.0, 0.0, 0.0)

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

        self._normal_grid = normals
        self._mask_grid = masks
        self._coverage_grid = coverage
        self._grid_width = width
        self._grid_height = height

    def _generate_normal_grid(self) -> None:
        self._generate_geometry(self.resolution, self.resolution)

    # ------------------------------------------------------------------
    # Light direction cache
    # ------------------------------------------------------------------
    def _compute_shading(self) -> None:
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
        # Center-point approximation of a square Blender-like area emitter.
        # The emitter normal faces the origin.
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
        lamp_to_surface = (-ldir[0], -ldir[1], -ldir[2])
        emitter_normal = (-self._light_vector[0], -self._light_vector[1], -self._light_vector[2])
        cos_alpha = max(0.0, emitter_normal[0] * lamp_to_surface[0]
                              + emitter_normal[1] * lamp_to_surface[1]
                              + emitter_normal[2] * lamp_to_surface[2])

        area = max(0.001, self.AREA_SIZE * self.AREA_SIZE)
        power_density = (self.REFERENCE_AREA_POWER * self.light_intensity) / area
        illuminance = power_density * cos_alpha / (distance * distance + 0.01)
        # Calibrate the larger area-light denominator back to the same visual
        # energy scale used by the reference point light.
        illuminance *= area
        return ldir, illuminance

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
        tonal_position = self._clamp(ndl) ** self.DIFFUSE_GAMMA

        shadow_to_base = self._smoothstep(0.0, 0.62, tonal_position)
        base_to_light = self._smoothstep(0.62, 1.0, tonal_position)

        color = self._lerp(shadow_linear, base_linear, shadow_to_base)
        color = self._lerp(color, light_linear, base_to_light)
        return color

    def _power_response(self, illuminance: float, ndl: float) -> float:
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
        luma = 0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]
        contrasted_luma = self._contrast_luma(luma, self.contrast)
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
                     light_linear: Color) -> Color:
        rim = (1.0 - nv) ** self.RIM_POWER
        rim *= ndl
        rim *= self.rim_light
        rim *= (0.5 + 0.5 * min(2.0, illuminance))
        return self._mul(light_linear, rim)

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
        # Reinhard is used only as a final safety net for specular/highlight
        # values above display range. It is applied per channel in linear RGB.
        return tuple(c / (1.0 + max(0.0, c)) for c in color)  # type: ignore[return-value]

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
    # Rendering
    # ------------------------------------------------------------------
    def render(self, base_color: Sequence[float], width: int, height: int) -> List[List[Pixel]]:
        width = max(2, int(width))
        height = max(2, int(height))

        if self._grid_width != width or self._grid_height != height:
            self._generate_geometry(width, height)
            self._compute_shading()

        # Input colors are sRGB values at the plugin boundary. Convert them once
        # and use linear RGB for all interpolation and lighting.
        base_srgb = self._coerce_color(base_color)
        base_linear = self._to_linear(base_srgb)
        shadow_linear = self._to_linear(self.shadow_color)
        light_linear = self._to_linear(self.light_color)
        ambient_linear = self._to_linear(self.ambient_color)
        highlight_linear = self._to_linear(self.highlight_color)
        sky_linear = self._to_linear(self.AMBIENT_SKY)

        output_linear: List[List[Color]] = []

        for row in range(height):
            out_row: List[Color] = []
            y = -1.0 + 2.0 * row / float(height - 1)

            for col in range(width):
                if not self._mask_grid[row][col]:
                    out_row.append((0.0, 0.0, 0.0))
                    continue

                x = -1.0 + 2.0 * col / float(width - 1)
                r2 = x * x + y * y
                z = math.sqrt(max(0.0, 1.0 - r2))
                nx, ny, nz = self._normal_grid[row][col]

                ldir, illuminance = self._light_for_surface(x, y, z)
                ndl = max(0.0, nx * ldir[0] + ny * ldir[1] + nz * ldir[2])
                nv = max(0.0, nz)  # camera is at +Z

                tonal_color = self._tonal_color(
                    shadow_linear,
                    base_linear,
                    light_linear,
                    ndl,
                )

                # Physical inverse-square / directional energy controls the
                # intensity while the tonal map controls the artist-selected
                # shadow/base/light colors.
                direct_factor = self._power_response(illuminance, ndl)

                # Preserve the selected shadow color even when the key light is
                # turned away. Ambient acts as a lift rather than replacing it.
                shadow_factor = 1.0 - self._smoothstep(0.0, 0.55, ndl)
                ambient_factor = self._clamp(
                    self.ambient * self.AMBIENT_SCALE
                    + shadow_factor * self.ambient * 0.35
                )
                ambient_tint = self._lerp(shadow_linear, ambient_linear, 0.35)

                # The direct sphere color is driven by the physical lighting
                # response. A small floor prevents a dead black result at the
                # extreme limb, which matches the manual's artistic dark floor.
                body_factor = max(self.DIFFUSE_FLOOR, direct_factor)
                body = self._mul(tonal_color, body_factor)
                body = self._add(body, self._mul(ambient_tint, ambient_factor * self.AMBIENT_SKY_MIX))

                # A cool sky contribution is strongest in the unlit portion.
                body = self._add(body, self._mul(sky_linear, ambient_factor * 0.05))

                # Blinn-Phong specular. H uses the actual per-pixel L and fixed
                # V=(0,0,1), exactly matching the front-facing projected sphere.
                hx, hy, hz = self._normalize3(
                    ldir[0],
                    ldir[1],
                    ldir[2] + 1.0,
                )
                ndh = max(0.0, nx * hx + ny * hy + nz * hz)
                knee = max(self.SPEC_KNEE_MIN, min(self.SPEC_KNEE_MAX, self.spec_knee))
                knee_curve = self._smoothstep(knee, 1.0, ndh)
                spec_term = (
                    illuminance
                    * ndl
                    * (ndh ** self.shininess)
                    * knee_curve
                )
                spec_term *= self.spec_max
                spec_term = self._clamp(spec_term, 0.0, 4.0)
                body = self._add(body, self._mul(highlight_linear, spec_term))

                # Mixer modes modify the lit sphere response once, using the
                # local illumination term so the effect follows the light
                # rather than acting as a second global display transform.
                if self.mixer_mode == "Additive":
                    body = self._add(body, self._mul((1.0, 1.0, 1.0), ndl * 0.06))
                elif self.mixer_mode == "Multiplicative":
                    body = self._mul(body, 0.78 + 0.22 * ndl)

                # Body grading: contrast, then saturation/brightness. Accents
                # stay above all three so Tone can't recolor them.
                body = self._apply_contrast(body)
                body = self._apply_saturation(body)
                body = self._apply_brightness(body)

                # Grain on the graded body at controlled strength: contrast no
                # longer stretches it. The shadow boost is preserved; it is an
                # intentional artistic choice, just not amplified twice.
                body = self._apply_grain(body, row, col, shadow_factor)

                # Fresnel-style rim as an accent layer above grading: it lives
                # in low-luma pixels, so contrasting first would crush it.
                # Restricted to the illuminated side so it does not create a
                # second light source in the shadow.
                body = self._add(
                    body,
                    self._compute_rim(nv, ndl, illuminance, light_linear),
                )

                # Glow sits alongside rim: an artistic bloom/accent above
                # grading (so neither contrast nor Tone can suppress or
                # recolor it) but below tone mapping (so Reinhard still
                # compresses the combined body/rim/glow energy instead of
                # letting glow bypass it).
                body = self._add(
                    body,
                    self._compute_glow(nv, ndl, highlight_linear),
                )

                body = self._tone_map(body)
                out_row.append(body)

            output_linear.append(out_row)

        # Image-space smoothing remains optional and bounded by the mask. The
        # sphere itself is still generated from exact analytic normals.
        if self.smooth > 0.0:
            radius = int(round(self.smooth))
            output_linear = self._soften_linear(output_linear, radius, width, height)

        pixels: List[List[Pixel]] = []
        for row in range(height):
            prow: List[Pixel] = []
            for col in range(width):
                if not self._mask_grid[row][col]:
                    prow.append((0, 0, 0))
                    continue
                srgb = self._from_linear(output_linear[row][col])
                prow.append((
                    int(self._clamp(srgb[0]) * 255.0 + 0.5),
                    int(self._clamp(srgb[1]) * 255.0 + 0.5),
                    int(self._clamp(srgb[2]) * 255.0 + 0.5),
                ))
            pixels.append(prow)
        return pixels

    # ------------------------------------------------------------------
    # Smoothing
    # ------------------------------------------------------------------
    def _soften_linear(self, grid: List[List[Color]], radius: int, width: int, height: int) -> List[List[Color]]:
        radius = max(0, min(2, int(radius)))
        if radius == 0:
            return grid

        if radius == 1:
            kernel = [1.0, 2.0, 1.0]
        else:
            kernel = [1.0, 4.0, 6.0, 4.0, 1.0]
        total = sum(kernel)
        kernel = [k / total for k in kernel]
        offset = len(kernel) // 2

        horizontal: List[List[Color]] = [
            [(0.0, 0.0, 0.0) for _ in range(width)] for _ in range(height)
        ]

        for row in range(height):
            for col in range(width):
                if not self._mask_grid[row][col]:
                    continue
                ar = ag = ab = aw = 0.0
                for k, weight in enumerate(kernel):
                    c = min(width - 1, max(0, col + k - offset))
                    if self._mask_grid[row][c]:
                        px = grid[row][c]
                        ar += px[0] * weight
                        ag += px[1] * weight
                        ab += px[2] * weight
                        aw += weight
                if aw > 0.0:
                    horizontal[row][col] = (ar / aw, ag / aw, ab / aw)

        result: List[List[Color]] = [
            [(0.0, 0.0, 0.0) for _ in range(width)] for _ in range(height)
        ]
        for row in range(height):
            for col in range(width):
                if not self._mask_grid[row][col]:
                    continue
                ar = ag = ab = aw = 0.0
                for k, weight in enumerate(kernel):
                    r = min(height - 1, max(0, row + k - offset))
                    if self._mask_grid[r][col]:
                        px = horizontal[r][col]
                        ar += px[0] * weight
                        ag += px[1] * weight
                        ab += px[2] * weight
                        aw += weight
                if aw > 0.0:
                    result[row][col] = (ar / aw, ag / aw, ab / aw)
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
