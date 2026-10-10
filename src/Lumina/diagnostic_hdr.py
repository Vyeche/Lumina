"""Isolated HDR display-range diagnostic for the #40 peer review.

Verified processing order in :mod:`color_engine` (see ``tools/verify_clamp.py``):

1. material          -- per-channel, linear RGB
2. contrast          -- **luminance** domain; rescales RGB by ``lut[luma]/luma``
3. saturation        -- luminance + per-channel
4. brightness        -- per-channel multiply
5. grain             -- per-channel add
6. rim               -- per-channel add
7. glow              -- per-channel add
8. Reinhard ``c/(1+c)`` -- **per channel**
9. sRGB encode       -- per channel

The clamp is at step 2. ``_build_contrast_lut`` samples only luma in [0, 1]
and stores 1.0 for every index at or above 1.0, so ``_contrast_luma`` returns
1.0 for any input >= 1.0 by construction; ``render()`` additionally clamps the
lookup index with ``cl = luma if luma < 1.0 else 1.0``. The result is
``scale = 1.0 / luma`` and body luminance pinned to exactly 1.0, which after
Reinhard and the sRGB encode is sRGB 188 for *any* amount of light energy.

Note the contrast LUT encodes only the endpoint-preserving power curve.
Reinhard is a separate, later, per-channel step. The extension below
therefore extends the *power curve*, not a tone mapper.

:class:`HDRRenderEngine` duplicates ``render()`` because the contrast lookup
is inlined in the hot loop and there is no hook to override without adding a
per-pixel call to the release path. The duplication is guarded: in
``hdr_mode="stock"`` it must reproduce the shipped engine byte-identically,
which :mod:`test_diagnostic_hdr` asserts across a settings matrix. If the
engine's display stage changes, that test fails rather than the diagnostic
quietly measuring the wrong thing.
"""

import math
from typing import List, Sequence

from color_engine import ColorEngine

Color = tuple
Pixel = tuple

#: Upper bound of the extended contrast table's domain. Beyond this the
#: diagnostic reports saturation rather than extrapolating.
DEFAULT_EXT_MAX = 6.0
HDR_MODES = ("stock", "extended")


class HDRRenderEngine(ColorEngine):
    """``ColorEngine`` with a selectable display-range path.

    ``hdr_mode="stock"`` reproduces the shipped renderer exactly.
    ``hdr_mode="extended"`` lets material luminance above 1.0 through an
    extension of the existing contrast power curve.

    Nothing else differs: swatches, light geometry, ambient, specular, rim,
    glow, mixer, saturation, brightness and the sRGB output encode are all
    inherited untouched, so any difference is attributable to display range
    alone.
    """

    EXT_LUT_SIZE = 2048

    def __init__(self, *args, hdr_mode: str = "stock",
                 ext_max: float = DEFAULT_EXT_MAX, **kwargs):
        if hdr_mode not in HDR_MODES:
            raise ValueError("hdr_mode must be one of %r, got %r"
                             % (HDR_MODES, hdr_mode))
        super().__init__(*args, **kwargs)
        self.hdr_mode = hdr_mode
        self.ext_max = max(1.0, float(ext_max))
        self._ext_lut: List[float] = []
        self._ext_lut_key = None
        #: pixels whose material luminance exceeded ``ext_max``
        self.ext_saturated_px = 0

    # -- extended contrast curve ----------------------------------------
    @staticmethod
    def _contrast_power(luma: float, contrast: float) -> float:
        """The engine's power curve, evaluated without the >=1.0 early-out.

        Identical to :meth:`ColorEngine._contrast_luma` for luma <= 1.0, and
        its natural continuation above it. C1-continuous at luma == 1.0:
        both branches have slope ``contrast`` there, and at contrast 1.0 the
        whole curve is the identity.
        """
        centered = 2.0 * luma - 1.0
        shaped = math.copysign(abs(centered) ** contrast, centered)
        return 0.5 + 0.5 * shaped

    def _build_ext_lut(self) -> None:
        key = (float(self.contrast), self.ext_max, self.EXT_LUT_SIZE)
        if key == self._ext_lut_key:
            return
        contrast = max(0.5, min(2.0, float(self.contrast)))
        n = self.EXT_LUT_SIZE
        # Index 0 sits exactly on luma == 1.0 so the extension joins the stock
        # table without a step.
        self._ext_lut = [
            self._contrast_power(1.0 + (self.ext_max - 1.0) * i / (n - 1), contrast)
            for i in range(n)
        ]
        self._ext_lut_key = key

    def _extended_contrast(self, luma: float) -> float:
        """Contrast curve for any luma >= 0, matching stock at luma <= 1.0."""
        self._build_ext_lut()
        if luma <= 1.0:
            return self._contrast_from_lut(luma)
        span = self.ext_max - 1.0
        idx = (luma - 1.0) / span * (self.EXT_LUT_SIZE - 1)
        if idx >= self.EXT_LUT_SIZE - 1:
            return self._ext_lut[-1]
        i0 = int(idx)
        i1 = i0 + 1
        f = idx - i0
        return self._ext_lut[i0] + (self._ext_lut[i1] - self._ext_lut[i0]) * f

    # -- render ---------------------------------------------------------
    def render(self, base_color: Sequence[float], width: int, height: int) -> List[List[Pixel]]:
        """Display stage duplicated from ColorEngine.render with one change.

        The contrast lookup uses :meth:`_extended_contrast` in ``extended``
        mode. In ``stock`` mode it is the shipped inline expression, so the
        two are byte-identical (asserted by test_diagnostic_hdr).
        """
        import time as _time
        t_total = _time.perf_counter()
        width = max(2, int(width))
        height = max(2, int(height))

        if self._grid_width != width or self._grid_height != height:
            if not self._restore_geometry(width, height):
                self._generate_geometry(width, height)
                self._compute_shading()
            else:
                self._compute_shading()

        base_srgb = self._coerce_color(base_color)
        base_linear = self._to_linear(base_srgb)
        shadow_linear = self._to_linear(self.shadow_color)
        light_linear = self._to_linear(self.light_color)
        ambient_linear = self._to_linear(self.ambient_color)
        highlight_linear = self._to_linear(self._effective_highlight(base_srgb))
        sky_linear = self._to_linear(self.AMBIENT_SKY)
        ground_linear = self._to_linear(self.GROUND_COLOR)

        self._build_light_stage(width, height)
        light_key = self._light_cache_key_value
        mat_key = self._material_key(light_key, base_srgb)
        self._build_material_stage(width, height, mat_key, (
            shadow_linear, base_linear, light_linear, ambient_linear,
            highlight_linear, sky_linear, ground_linear))

        contrast_lut = self._contrast_lut
        clut_last = len(contrast_lut) - 1
        extended = self.hdr_mode == "extended"
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
        glow_power = max(1.0, self.glow_radius / 4.0)
        rim_color = self._lerp(light_linear, sky_linear, rim_sky_mix)

        self.ext_saturated_px = 0
        output_linear: List[List[Color]] = []
        shoulder = self.TONE_SHOULDER
        span = 1.0 - shoulder
        inv_span = 1.0 / span
        exp = math.exp

        for row in range(height):
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

                luma = 0.2126 * body[0] + 0.7152 * body[1] + 0.0722 * body[2]
                if luma > 1.0e-8:
                    if extended:
                        if luma > self.ext_max:
                            self.ext_saturated_px += 1
                        target = self._extended_contrast(luma)
                    else:
                        cl = luma if luma < 1.0 else 1.0
                        if cl < 0.0:
                            cl = 0.0
                        target = contrast_lut[int(cl * clut_last + 0.5)]
                    body = self._mul(body, target / luma)
                else:
                    body = (0.0, 0.0, 0.0)

                luma = 0.2126 * body[0] + 0.7152 * body[1] + 0.0722 * body[2]
                body = (
                    luma + (body[0] - luma) * saturation,
                    luma + (body[1] - luma) * saturation,
                    luma + (body[2] - luma) * saturation,
                )
                if brightness != 1.0:
                    body = self._mul(body, brightness)

                if grain_on:
                    shadow_factor = 1.0 - self._smoothstep(0.0, 0.55, ndl)
                    amount = grain_amount
                    if shadow_factor > 0.5:
                        amount *= grain_boost
                    scale = grain_depth * amount * self._noise(row, col)
                    body = (
                        max(0.0, body[0] + scale),
                        max(0.0, body[1] + scale),
                        max(0.0, body[2] + scale),
                    )

                r = 1.0 - nv
                r2 = r * r
                rim = r2 * r2 * r * ndl * rim_light
                rim *= 0.5 + 0.5 * (erow[col] if erow[col] < 2.0 else 2.0)
                if rim != 0.0:
                    body = (body[0] + rim_color[0] * rim,
                            body[1] + rim_color[1] * rim,
                            body[2] + rim_color[2] * rim)

                if glow_on:
                    g = 1.0 - nv
                    glow = (g ** glow_power) * ndl * glow_intensity
                    if glow != 0.0:
                        gk = glow * self.GLOW_GAIN
                        body = (body[0] + highlight_linear[0] * gk,
                                body[1] + highlight_linear[1] * gk,
                                body[2] + highlight_linear[2] * gk)

                b0 = body[0]
                b1 = body[1]
                b2 = body[2]
                out_row.append((
                    (b0 if b0 <= shoulder else shoulder + span * (1.0 - exp(-(b0 - shoulder) * inv_span))) if b0 > 0.0 else 0.0,
                    (b1 if b1 <= shoulder else shoulder + span * (1.0 - exp(-(b1 - shoulder) * inv_span))) if b1 > 0.0 else 0.0,
                    (b2 if b2 <= shoulder else shoulder + span * (1.0 - exp(-(b2 - shoulder) * inv_span))) if b2 > 0.0 else 0.0,
                ))

            output_linear.append(out_row)

        if self.smooth > 0.0:
            radius = int(round(self.smooth))
            output_linear = self._soften_linear(output_linear, radius, width, height)

        pixels: List[List[Pixel]] = []
        srgb_lut = self._srgb_lut()
        srgb_last = len(srgb_lut) - 1
        for row in range(height):
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
        self._record_timing("total", (_time.perf_counter() - t_total) * 1000.0)
        return pixels