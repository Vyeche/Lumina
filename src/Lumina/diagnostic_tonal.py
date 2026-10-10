"""Diagnostic tonal-interpolation variants for the #40 peer review.

Production ``ColorEngine`` interpolates the artist-authored shadow/base/light
swatches in linear RGB (linearise -> lerp -> light). This module provides an
isolated A/B harness that tests the competing hypothesis -- interpolate the
same swatches in **sRGB**, then convert the ramp result to linear before the
lighting continues -- *without* touching the production pipeline.

Design constraints honoured here:

* Nothing in :mod:`color_engine` is modified. The variants are subclasses, so
  the shipped render path and its defaults are unchanged.
* Only the tonal ramp differs. Ambient/environment compositing, specular,
  light geometry, mixer, contrast, rim, glow and the final sRGB encode are
  inherited untouched -- in particular ambient and specular are *not* silently
  moved into sRGB.
* ``linear`` is verified against the stock engine so the harness cannot be
  blamed for an unrelated difference.

    from diagnostic_tonal import TonalVariantEngine
    e = TonalVariantEngine(variant="srgb_ramp", resolution=256)

Both variants are defined by the same endpoint contract: at blend weight 0
the result is exactly the shadow swatch, and at weight 1 exactly the base (or
light) swatch, in linear RGB. :mod:`test_diagnostic_tonal` asserts this.
"""

import math
from typing import List, Sequence

from color_engine import ColorEngine

Color = tuple
VARIANTS = ("linear", "srgb_ramp")


class TonalVariantEngine(ColorEngine):
    """``ColorEngine`` with a switchable shadow/base/light ramp space."""

    #: ``"unity"`` replaces the engine's display-referred direct-light response
    #: ``2E/(1+E)`` with a plain ``1.0``, i.e. N.L reaches the tonal colour
    #: unattenuated. This isolates stage 1 (direct response) from the tonal
    #: ramp exactly as ``srgb_ramp`` isolates stage 3. It exists to test
    #: whether the engine's response compression -- not interpolation -- is the
    #: first stage to diverge from the reference. Diagnostic only.
    DIRECT_MODES = ("engine", "unity")

    def __init__(self, *args, variant: str = "linear",
                 direct_mode: str = "engine", **kwargs):
        if variant not in VARIANTS:
            raise ValueError("variant must be one of %r, got %r"
                             % (VARIANTS, variant))
        if direct_mode not in self.DIRECT_MODES:
            raise ValueError("direct_mode must be one of %r, got %r"
                             % (self.DIRECT_MODES, direct_mode))
        super().__init__(*args, **kwargs)
        self.variant = variant
        self.direct_mode = direct_mode

    # -- ramp definitions ------------------------------------------------
    def _ramp_colors(self, colors, weights=None):
        """Return one linear RGB triple per tonal weight pair.

        ``colors`` is the engine's linear colour tuple; the sRGB variant also
        needs the original sRGB swatches, which are still on the instance.

        ``weights`` defaults to the engine's tonal LUT. Pass an explicit list
        to evaluate the ramp at exact blend weights: the shipped LUT samples
        N.L uniformly and so never lands on the exact base endpoint (weights
        step from ``shadow->base`` 0.99988 straight to 1.0 with a non-zero
        ``base->light``), which makes "is the endpoint exact?" untestable
        against the sampled table.
        """
        shadow_lin, base_lin, light_lin = colors[0], colors[1], colors[2]
        if weights is None:
            weights = self._tonal_lut()
        if self.variant == "linear":
            table = []
            for s2b, b2l in weights:
                c = self._lerp(shadow_lin, base_lin, s2b)
                table.append(self._lerp(c, light_lin, b2l))
            return table
        shadow_srgb = self.shadow_color
        base_srgb = self._coerce_color(self._base_srgb_cache)
        light_srgb = self.light_color
        table = []
        for s2b, b2l in weights:
            c = self._lerp(shadow_srgb, base_srgb, s2b)
            c = self._lerp(c, light_srgb, b2l)
            table.append(self._to_linear(c))
        return table

    # The stock material loop recomputes the lerp inline for speed. The
    # diagnostic variants do not need that speed, so they look the ramp up.
    def _build_material_stage(self, width, height, key, colors):
        """Stock material stage (color_engine). The ``linear`` variant is the
        shipped code unchanged; other variants replace the tonal colour with
        their ramp, and ``direct_mode`` can flatten the direct term."""
        if key == self._material_cache_key_value:
            return
        (shadow_linear, base_linear, light_linear, ambient_linear,
         highlight_linear, sky_linear, ground_linear) = colors
        ramp = None if self.variant == "linear" else self._ramp_colors(colors)
        ramp_last = len(ramp) - 1 if ramp else 0

        bodies: List[List[Color]] = []
        spec_lut = self._spec_lut
        spec_last = len(spec_lut) - 1
        shininess = self.shininess  # noqa: F841 (kept for clarity)
        spec_knee = max(self.SPEC_KNEE_MIN, min(self.SPEC_KNEE_MAX, self.spec_knee))
        mixer = self.mixer_mode
        spec_max = self.spec_max
        spec_gain = self.SPEC_GAIN
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
        reflect_span = self.REFLECT_END - reflect_a
        sky_b = self.sky_bounce
        ground_b = self.ground_bounce
        for row in range(height):
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
                if ramp is not None:
                    tcr, tcg, tcb = ramp[int(ndlc * ramp_last + 0.5)]
                de = illuminance * ndl
                gde = diffuse_gain * de
                if self.direct_mode == "unity":
                    direct_factor = 1.0
                else:
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

    def render(self, base_color: Sequence[float], width: int, height: int):
        # The ramp table needs the base swatch in sRGB, which render() knows
        # but _build_material_stage does not receive.
        self._base_srgb_cache = tuple(self._coerce_color(base_color))
        self._material_cache_key_value = None
        return super().render(base_color, width, height)