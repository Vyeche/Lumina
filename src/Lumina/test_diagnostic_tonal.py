"""Regression tests for the #40 diagnostic tonal variants.

Two things are asserted:

1. The ``linear`` variant is byte-identical to the stock ``ColorEngine``, so
   the harness cannot introduce an unrelated difference into an A/B result.
2. Both variants hit the ramp endpoints exactly. The endpoint contract is
   what makes the interpolation-space comparison meaningful: if the two spaces
   disagreed at an endpoint, a centre-pixel difference could never be
   attributed to interpolation.

Production behaviour is untouched -- these tests read the shipped defaults and
assert they have not moved.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from color_engine import ColorEngine
from diagnostic_tonal import VARIANTS, TonalVariantEngine

TRIAD = {"shadow": (0x2b / 255, 0x08 / 255, 0x50 / 255),
         "base": (0x74 / 255, 0x33 / 255, 0x56 / 255),
         "light": (0xc2 / 255, 0x5f / 255, 0x42 / 255)}


def _engine(cls, **kw):
    e = cls(resolution=48, **kw)
    e.set_light_type("Sun")
    e.set_light_angle(302, 27)
    e.set_shadow_color(TRIAD["shadow"])
    e.set_light_color(TRIAD["light"])
    e.set_highlight_color((1.0, 1.0, 1.0))
    e.set_ambient(0.10)
    e.set_sky_bounce(0.0)
    e.set_ground_bounce(0.0)
    return e


def test_linear_variant_matches_stock_engine():
    """The 'linear' variant must reproduce the shipped engine exactly."""
    stock = _engine(ColorEngine)
    diag = _engine(TonalVariantEngine, variant="linear")
    a = stock.render(TRIAD["base"], 48, 48)
    b = diag.render(TRIAD["base"], 48, 48)
    assert a == b, "linear diagnostic variant diverged from the stock engine"


def test_both_variants_hit_ramp_endpoints_exactly():
    """At weight 0/1 the ramp is exactly the swatch, in linear RGB, for both."""
    stock = ColorEngine(resolution=8)
    for variant in VARIANTS:
        e = TonalVariantEngine(variant=variant, resolution=8)
        e.shadow_color = TRIAD["shadow"]
        e.light_color = TRIAD["light"]
        e._base_srgb_cache = TRIAD["base"]
        shadow_lin = stock._to_linear(TRIAD["shadow"])
        base_lin = stock._to_linear(TRIAD["base"])
        light_lin = stock._to_linear(TRIAD["light"])

        colors = (shadow_lin, base_lin, light_lin, None, None, None, None)

        # Exact endpoints, queried at explicit weights rather than by scanning
        # the sampled LUT.
        endpoints = e._ramp_colors(colors, weights=[(0.0, 0.0)])
        assert all(abs(endpoints[0][i] - shadow_lin[i]) < 1e-12
                   for i in range(3)), \
            "%s: shadow endpoint must be the shadow swatch exactly" % variant

        endpoints = e._ramp_colors(colors, weights=[(1.0, 0.0)])
        assert all(abs(endpoints[0][i] - base_lin[i]) < 1e-12
                   for i in range(3)), \
            "%s: base endpoint must be the base swatch exactly" % variant

        endpoints = e._ramp_colors(colors, weights=[(1.0, 1.0)])
        assert all(abs(endpoints[0][i] - light_lin[i]) < 1e-12
                   for i in range(3)), \
            "%s: light endpoint must be the light swatch exactly" % variant

        # The two ramps must agree at every endpoint, which is what makes an
        # endpoint-pixel difference attributable to something other than the
        # interpolation space.
        for w in ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0)):
            for variant2 in VARIANTS:
                e2 = TonalVariantEngine(variant=variant2, resolution=8)
                e2.shadow_color = TRIAD["shadow"]
                e2.light_color = TRIAD["light"]
                e2._base_srgb_cache = TRIAD["base"]
                got = e2._ramp_colors(colors, weights=[w])[0]
                assert all(abs(got[i] - e._ramp_colors(colors, weights=[w])[0][i])
                           < 1e-12 for i in range(3)), \
                    "variants disagree at endpoint %r" % (w,)


def test_intermediate_weights_differ_between_spaces():
    """The two spaces must actually diverge mid-ramp, or the A/B is vacuous."""
    stock = ColorEngine(resolution=8)
    lin_e = TonalVariantEngine(variant="linear", resolution=8)
    srgb_e = TonalVariantEngine(variant="srgb_ramp", resolution=8)
    for e in (lin_e, srgb_e):
        e.shadow_color = TRIAD["shadow"]
        e.light_color = TRIAD["light"]
        e._base_srgb_cache = TRIAD["base"]
    colors = (stock._to_linear(TRIAD["shadow"]),
              stock._to_linear(TRIAD["base"]),
              stock._to_linear(TRIAD["light"]), None, None, None, None)
    a = lin_e._ramp_colors(colors)
    b = srgb_e._ramp_colors(colors)
    mid = len(a) // 2
    assert any(abs(a[mid][i] - b[mid][i]) > 1e-6 for i in range(3)), \
        "srgb_ramp and linear must differ at intermediate weights"


def test_production_defaults_unchanged():
    """Guards against an accidental default edit during the investigation.

    Read from a pristine engine -- the shared ``_engine`` helper sets the
    geometry, so it cannot be used to observe the shipped defaults.
    """
    d = ColorEngine(resolution=48)
    # The shipped defaults after the reference calibration (#44).
    assert d.light_azimuth == 304.0, "shipped azimuth default must stay 304"
    assert d.light_elevation == 41.0, "shipped elevation default must stay 41"
    assert d.ambient == 0.05
    assert d.rim_light == 0.10
    assert d.spec_max == 0.61
    assert d.shininess == 8.9


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))