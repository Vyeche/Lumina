"""Regression tests for the #40 isolated HDR display-range diagnostic.

The duplicated display stage in :mod:`diagnostic_hdr` exists only because
``ColorEngine.render`` inlines the contrast lookup in its hot loop. These
tests pin that duplication to the shipped engine so the diagnostic cannot
silently measure a different pipeline:

* ``stock`` mode must be byte-identical to ``ColorEngine`` across a matrix of
  settings, including settings that push material luminance above 1.0.
* The extended curve must match stock exactly at and below luma 1.0.
* The extended curve must be continuous and monotonic through 1.0.

Production behaviour is untouched; the shipped defaults are asserted unchanged.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

from color_engine import ColorEngine
from diagnostic_hdr import HDRRenderEngine, HDR_MODES, DEFAULT_EXT_MAX

# Settings that exercise the clamp: white highlight, hot specular, no fill.
HOT = dict(shadow=(0.2, 0.1, 0.3), light=(1.0, 1.0, 1.0), highlight=(1.0, 1.0, 1.0),
           ambient=0.10, intensity=8.0)
WARM = dict(shadow=(0.169, 0.031, 0.314), light=(0.761, 0.373, 0.259),
            highlight=(1.0, 1.0, 1.0), ambient=0.10, intensity=1.0)


def _make(cls, spec, **extra):
    e = cls(resolution=64, **extra)
    e.set_light_type("Sun")
    e.set_light_angle(302, 27)
    e.set_light_intensity(spec["intensity"])
    e.set_shadow_color(spec["shadow"])
    e.set_light_color(spec["light"])
    e.set_highlight_color(spec["highlight"])
    e.set_ambient(spec["ambient"])
    e.set_sky_bounce(0.05)
    e.set_ground_bounce(0.05)
    return e


MATRIX = []
for _spec_name, _spec in (("hot", HOT), ("warm", WARM)):
    for _amb in (0.0, 0.10, 0.30):
        for _con in (0.5, 1.0, 1.7):
            for _rim in (0.0, 0.14, 0.6):
                for _glow in (0.0, 0.5):
                    MATRIX.append((_spec_name, {**_spec, "ambient": _amb},
                                   _con, _rim, _glow))


@pytest.mark.parametrize("name,spec,con,rim,glow", MATRIX)
def test_stock_mode_is_byte_identical_to_production(name, spec, con, rim, glow):
    """The duplicated display stage must not change stock behaviour."""
    base = (0.45, 0.2, 0.34)
    a = _make(ColorEngine, spec)
    a.set_contrast(con)
    a.set_rim_light(rim)
    a.set_glow_intensity(glow)
    b = _make(HDRRenderEngine, spec, hdr_mode="stock")
    b.set_contrast(con)
    b.set_rim_light(rim)
    b.set_glow_intensity(glow)
    assert a.render(base, 64, 64) == b.render(base, 64, 64)


def test_extended_matches_stock_at_and_below_one():
    """No regression below the clamp boundary, and exact join at 1.0."""
    e = HDRRenderEngine(resolution=8, hdr_mode="extended")
    for contrast in (0.5, 1.0, 1.5, 2.0):
        e.set_contrast(contrast)
        for i in range(0, 4096, 37):
            luma = i / 4095.0
            assert e._extended_contrast(luma) == e._contrast_from_lut(luma), (
                "extended path diverged below 1.0 at luma=%r contrast=%r"
                % (luma, contrast))
        assert e._extended_contrast(1.0) == e._contrast_from_lut(1.0)


def test_extended_is_continuous_at_one():
    """Value and slope join smoothly: no step, no kink at the boundary.

    Slope is probed on the analytic curve, not the 4096-entry stock table:
    its resolution near luma=1.0 is 1/4095 ~ 2.4e-4, so a finite difference
    smaller than that lands inside one cell and reads a false zero slope.
    """
    e = HDRRenderEngine(resolution=8, hdr_mode="extended")
    for contrast in (0.5, 1.0, 1.5, 2.0):
        e.set_contrast(contrast)
        below = e._extended_contrast(1.0 - 1e-7)
        at = e._extended_contrast(1.0)
        above = e._extended_contrast(1.0 + 1e-7)
        assert abs(at - below) < 1e-6, "step at luma=1.0 (contrast %r)" % contrast
        assert abs(at - above) < 1e-6, "step at luma=1.0 (contrast %r)" % contrast

        # analytic continuity of the extended curve across the boundary
        h = 1e-6
        f = e._contrast_power
        s_below = (f(1.0, contrast) - f(1.0 - h, contrast)) / h
        s_above = (f(1.0 + h, contrast) - f(1.0, contrast)) / h
        assert abs(s_below - s_above) < 1e-3, (
            "slope discontinuity at luma=1.0 (contrast %r): %r vs %r"
            % (contrast, s_below, s_above))

        # and the tabulated extension agrees with the analytic curve
        for luma in (1.0001, 1.2, 2.0, 3.62, 6.0):
            got = e._extended_contrast(luma)
            assert abs(got - f(luma, contrast)) < 5e-3, (
                "extended LUT drifted from the analytic curve at %r" % luma)


def test_extended_is_monotonic_above_one():
    e = HDRRenderEngine(resolution=8, hdr_mode="extended")
    for contrast in (0.5, 1.0, 1.5, 2.0):
        e.set_contrast(contrast)
        prev = e._extended_contrast(1.0)
        i = 1
        while i <= 4096:
            luma = 1.0 + (DEFAULT_EXT_MAX - 1.0) * i / 4096.0
            cur = e._extended_contrast(luma)
            assert cur >= prev - 1e-9, (
                "non-monotonic at luma=%r contrast=%r" % (luma, contrast))
            prev = cur
            i += 1


def test_identity_at_default_contrast():
    """At contrast 1.0 the curve is the identity, so 3.62 stays 3.62."""
    e = HDRRenderEngine(resolution=8, hdr_mode="extended")
    e.set_contrast(1.0)
    for luma in (1.0, 1.06, 2.0, 3.62, 6.0):
        assert e._extended_contrast(luma) == pytest.approx(luma, rel=1e-6)


@pytest.mark.skip(reason="The Reinhard luminance clamp this measured was replaced by the "
                         "TONE_SHOULDER roll-off in the reference calibration (#44): stock "
                         "neutral white now reaches 240, not 188.")
def test_extended_recovers_range_stock_cannot_show():
    """The point of the exercise: stock pins neutral output, extended does not.

    The clamp caps *luminance* at 1.0, so a neutral colour tops out at sRGB
    188 (Reinhard(1.0) encoded). A saturated channel can still read higher
    because it carries luma more cheaply -- 1.0/0.2126 = 4.70 in red --
    so the ceiling is asserted on luminance, not on any single channel.
    """
    base = (0.45, 0.2, 0.34)
    out = {}
    for mode in HDR_MODES:
        e = _make(HDRRenderEngine, HOT, hdr_mode=mode)
        e.set_rim_light(0.0)
        e.set_glow_intensity(0.0)
        e.set_sky_bounce(0.0)
        e.set_ground_bounce(0.0)
        px = e.render(base, 64, 64)
        out[mode] = max(max(p) for row in px for p in row)

        # stock must pin the brightest pixel's *display* luminance at the
        # Reinhard(1.0) ceiling, i.e. ~188 in 8-bit sRGB terms
        if mode == "stock":
            worst = max(0.2126 * p[0] + 0.7152 * p[1] + 0.0722 * p[2]
                        for row in px for p in row)
            assert worst <= 189.0, "stock display luminance exceeded its clamp: %r" % worst
    assert out["extended"] > out["stock"] + 20, (
        "extended path barely exceeded the stock ceiling: %r" % out)


@pytest.mark.skip(reason="The Reinhard luminance clamp this measured was replaced by the "
                         "TONE_SHOULDER roll-off in the reference calibration (#44): stock "
                         "neutral white now reaches 240, not 188.")
def test_neutral_white_cannot_exceed_188_on_stock_but_can_when_extended():
    """A white sphere is the cleanest probe: neutral, so luma == channel."""
    base = (1.0, 1.0, 1.0)
    got = {}
    for mode in HDR_MODES:
        e = _make(HDRRenderEngine, HOT, hdr_mode=mode)
        e.set_shadow_color((1.0, 1.0, 1.0))
        e.set_light_color((1.0, 1.0, 1.0))
        e.set_rim_light(0.0)
        e.set_glow_intensity(0.0)
        e.set_sky_bounce(0.0)
        e.set_ground_bounce(0.0)
        px = e.render(base, 64, 64)
        got[mode] = max(max(p) for row in px for p in row)
    assert got["stock"] <= 188, "neutral stock ceiling moved: %r" % got["stock"]
    assert got["extended"] > 220, (
        "extended neutral still cannot reach display white: %r" % got)


def test_bad_mode_rejected():
    with pytest.raises(ValueError):
        HDRRenderEngine(resolution=8, hdr_mode="nope")


def test_production_defaults_unchanged():
    """The diagnostic must not have moved a shipped default (the values the
    reference calibration, #44, set)."""
    d = ColorEngine(resolution=32)
    assert d.light_azimuth == 304.0
    assert d.light_elevation == 41.0
    assert d.ambient == 0.05
    assert d.contrast == 1.0
    assert d.rim_light == 0.10
    assert d.spec_max == 0.61
    assert d.shininess == 8.9
    assert ColorEngine.CONTRAST_LUT_SIZE == 4096
    assert not hasattr(d, "hdr_mode"), "hdr_mode must not exist on the production class"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))