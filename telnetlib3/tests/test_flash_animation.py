"""Tests for vital-bar HSV flash animation helpers."""

# 3rd party
import pytest

pytest.importorskip("blessed")

# local
from telnetlib3.client_repl import (
    _FLASH_HOLD,
    _FLASH_RAMP_UP,
    _FLASH_DURATION,
    _lerp_hsv,
    _vital_bar,
    _hsv_to_rgb,
    _rgb_to_hsv,
    _flash_color,
)


class TestRgbHsvRoundTrip:

    @pytest.mark.parametrize(
        "r, g, b",
        [(255, 0, 0), (0, 255, 0), (0, 0, 255), (128, 64, 32), (0, 0, 0), (255, 255, 255)],
    )
    def test_round_trip(self, r, g, b):
        h, s, v = _rgb_to_hsv(r, g, b)
        r2, g2, b2 = _hsv_to_rgb(h, s, v)
        assert abs(r2 - r) <= 1
        assert abs(g2 - g) <= 1
        assert abs(b2 - b) <= 1

    def test_red_hue(self):
        h, s, v = _rgb_to_hsv(255, 0, 0)
        assert abs(h) < 1.0 or abs(h - 360.0) < 1.0
        assert abs(s - 1.0) < 0.01
        assert abs(v - 1.0) < 0.01


class TestLerpHsv:

    def test_endpoints(self):
        a = (0.0, 0.5, 0.8)
        b = (120.0, 1.0, 1.0)
        assert _lerp_hsv(a, b, 0.0) == a
        assert _lerp_hsv(a, b, 1.0) == b

    def test_midpoint(self):
        h, s, v = _lerp_hsv((0.0, 0.0, 0.0), (120.0, 1.0, 1.0), 0.5)
        assert abs(h - 60.0) < 0.01
        assert abs(s - 0.5) < 0.01
        assert abs(v - 0.5) < 0.01

    def test_shortest_arc_wraps_through_zero(self):
        h, _, _ = _lerp_hsv((350.0, 1.0, 1.0), (10.0, 1.0, 1.0), 0.5)
        assert abs(h) < 1.0 or abs(h - 360.0) < 1.0

    def test_shortest_arc_does_not_go_long_way(self):
        h, _, _ = _lerp_hsv((350.0, 1.0, 1.0), (10.0, 1.0, 1.0), 0.25)
        assert h > 340.0 or h < 20.0


class TestFlashColor:

    def test_no_flash_negative_elapsed(self):
        assert _flash_color("#ff0000", -1.0) == "#ff0000"

    def test_no_flash_past_duration(self):
        assert _flash_color("#ff0000", _FLASH_DURATION + 0.1) == "#ff0000"

    def test_at_zero_returns_original(self):
        assert _flash_color("#ff0000", 0.0) == "#ff0000"

    def test_at_exact_duration_returns_original(self):
        assert _flash_color("#ff0000", _FLASH_DURATION) == "#ff0000"

    def test_at_hold_midpoint_returns_white(self):
        mid = _FLASH_RAMP_UP + _FLASH_HOLD / 2.0
        result = _flash_color("#ff0000", mid)
        r = int(result[1:3], 16)
        g = int(result[3:5], 16)
        b = int(result[5:7], 16)
        assert r > 240
        assert g > 240
        assert b > 240

    def test_ramp_symmetry(self):
        t_up = _FLASH_RAMP_UP * 0.5
        ramp_down_start = _FLASH_RAMP_UP + _FLASH_HOLD
        t_down = ramp_down_start + (_FLASH_DURATION - ramp_down_start) * 0.5
        color_up = _flash_color("#66aa44", t_up)
        color_down = _flash_color("#66aa44", t_down)
        r_up = int(color_up[1:3], 16)
        r_down = int(color_down[1:3], 16)
        assert abs(r_up - r_down) < 8

    def test_returns_valid_hex(self):
        for elapsed in [0.0, 0.05, 0.15, 0.25, 0.4, 0.6]:
            result = _flash_color("#3388cc", elapsed)
            assert result.startswith("#")
            assert len(result) == 7
            int(result[1:], 16)


class TestVitalBarFlash:

    def test_flash_elapsed_negative_uses_normal_colors(self):
        frags_normal = _vital_bar(50, 100, 20, "hp", flash_elapsed=-1.0)
        frags_no_arg = _vital_bar(50, 100, 20, "hp")
        sgr_normal = frags_normal[1][0]
        sgr_no_arg = frags_no_arg[1][0]
        assert sgr_normal == sgr_no_arg

    def test_flash_elapsed_at_hold_differs_from_normal(self):
        frags_normal = _vital_bar(50, 100, 20, "hp", flash_elapsed=-1.0)
        mid = _FLASH_RAMP_UP + _FLASH_HOLD / 2.0
        frags_flash = _vital_bar(50, 100, 20, "hp", flash_elapsed=mid)
        assert frags_normal[1][0] != frags_flash[1][0]

    def test_flash_elapsed_past_duration_same_as_normal(self):
        frags_normal = _vital_bar(50, 100, 20, "hp", flash_elapsed=-1.0)
        frags_past = _vital_bar(50, 100, 20, "hp", flash_elapsed=1.0)
        assert frags_normal[1][0] == frags_past[1][0]
