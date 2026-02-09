"""Tests for telnetlib3.color_filter — ANSI color palette translation."""

# 3rd party
import pytest

# local
from telnetlib3.color_filter import (
    PALETTES,
    ColorConfig,
    ColorFilter,
    _adjust_color,
    _is_foreground_code,
    _sgr_code_to_palette_index,
)


class TestPaletteData:
    @pytest.mark.parametrize("name", list(PALETTES.keys()))
    def test_palette_has_16_entries(self, name: str) -> None:
        assert len(PALETTES[name]) == 16

    @pytest.mark.parametrize("name", list(PALETTES.keys()))
    def test_palette_rgb_in_range(self, name: str) -> None:
        for r, g, b in PALETTES[name]:
            assert 0 <= r <= 255
            assert 0 <= g <= 255
            assert 0 <= b <= 255

    def test_all_expected_palettes_exist(self) -> None:
        assert set(PALETTES.keys()) == {"ega", "cga", "vga", "amiga", "xterm"}


class TestColorConfig:
    def test_defaults(self) -> None:
        cfg = ColorConfig()
        assert cfg.palette_name == "ega"
        assert cfg.brightness == 0.9
        assert cfg.contrast == 0.8
        assert cfg.background_color == (16, 16, 16)
        assert cfg.reverse_video is False


class TestSgrCodeToPaletteIndex:
    @pytest.mark.parametrize(
        "code,expected", [(30, 0), (31, 1), (32, 2), (33, 3), (34, 4), (35, 5), (36, 6), (37, 7)]
    )
    def test_normal_foreground(self, code: int, expected: int) -> None:
        assert _sgr_code_to_palette_index(code) == expected

    @pytest.mark.parametrize(
        "code,expected", [(40, 0), (41, 1), (42, 2), (43, 3), (44, 4), (45, 5), (46, 6), (47, 7)]
    )
    def test_normal_background(self, code: int, expected: int) -> None:
        assert _sgr_code_to_palette_index(code) == expected

    @pytest.mark.parametrize(
        "code,expected",
        [(90, 8), (91, 9), (92, 10), (93, 11), (94, 12), (95, 13), (96, 14), (97, 15)],
    )
    def test_bright_foreground(self, code: int, expected: int) -> None:
        assert _sgr_code_to_palette_index(code) == expected

    @pytest.mark.parametrize(
        "code,expected",
        [(100, 8), (101, 9), (102, 10), (103, 11), (104, 12), (105, 13), (106, 14), (107, 15)],
    )
    def test_bright_background(self, code: int, expected: int) -> None:
        assert _sgr_code_to_palette_index(code) == expected

    @pytest.mark.parametrize("code", [0, 1, 4, 7, 22, 38, 39, 48, 49, 128])
    def test_non_color_returns_none(self, code: int) -> None:
        assert _sgr_code_to_palette_index(code) is None


class TestIsForegroundCode:
    @pytest.mark.parametrize("code", list(range(30, 38)) + list(range(90, 98)))
    def test_foreground_codes(self, code: int) -> None:
        assert _is_foreground_code(code) is True

    @pytest.mark.parametrize("code", list(range(40, 48)) + list(range(100, 108)))
    def test_background_codes(self, code: int) -> None:
        assert _is_foreground_code(code) is False


class TestAdjustColor:
    def test_identity(self) -> None:
        assert _adjust_color(170, 85, 0, 1.0, 1.0) == (170, 85, 0)

    def test_full_brightness_zero_contrast(self) -> None:
        r, g, b = _adjust_color(200, 100, 50, 1.0, 0.0)
        assert r == 128
        assert g == 128
        assert b == 128

    def test_zero_brightness(self) -> None:
        r, g, b = _adjust_color(200, 100, 50, 0.0, 1.0)
        assert r == 0
        assert g == 0
        assert b == 0

    def test_half_brightness(self) -> None:
        r, g, b = _adjust_color(200, 100, 0, 0.5, 1.0)
        assert r == 100
        assert g == 50
        assert b == 0

    def test_clamp_high(self) -> None:
        r, _, _ = _adjust_color(255, 255, 255, 1.0, 2.0)
        assert r == 255

    def test_clamp_low(self) -> None:
        r, _, _ = _adjust_color(0, 0, 0, 1.0, 2.0)
        assert r == 0

    def test_default_config_values(self) -> None:
        r, g, b = _adjust_color(170, 0, 0, 0.9, 0.8)
        assert 0 <= r <= 255
        assert 0 <= g <= 255
        assert 0 <= b <= 255


class TestColorFilterBasicTranslation:
    def _make_filter(self, **kwargs: object) -> ColorFilter:
        cfg = ColorConfig(brightness=1.0, contrast=1.0, **kwargs)  # type: ignore[arg-type]
        return ColorFilter(cfg)

    def test_red_foreground(self) -> None:
        f = self._make_filter()
        result = f.filter("\x1b[31m")
        ega_red = PALETTES["ega"][1]
        expected_color = f"\x1b[38;2;{ega_red[0]};{ega_red[1]};{ega_red[2]}m"
        assert expected_color in result

    def test_red_background(self) -> None:
        f = self._make_filter()
        result = f.filter("\x1b[41m")
        ega_red = PALETTES["ega"][1]
        expected_color = f"\x1b[48;2;{ega_red[0]};{ega_red[1]};{ega_red[2]}m"
        assert expected_color in result

    def test_bright_red_foreground(self) -> None:
        f = self._make_filter()
        result = f.filter("\x1b[91m")
        ega_bright_red = PALETTES["ega"][9]
        expected = f"\x1b[38;2;{ega_bright_red[0]};" f"{ega_bright_red[1]};{ega_bright_red[2]}m"
        assert expected in result

    def test_bright_red_background(self) -> None:
        f = self._make_filter()
        result = f.filter("\x1b[101m")
        ega_bright_red = PALETTES["ega"][9]
        expected = f"\x1b[48;2;{ega_bright_red[0]};" f"{ega_bright_red[1]};{ega_bright_red[2]}m"
        assert expected in result

    @pytest.mark.parametrize(
        "code,idx", [(30, 0), (31, 1), (32, 2), (33, 3), (34, 4), (35, 5), (36, 6), (37, 7)]
    )
    def test_all_normal_foreground_colors(self, code: int, idx: int) -> None:
        f = self._make_filter()
        result = f.filter(f"\x1b[{code}m")
        rgb = PALETTES["ega"][idx]
        assert f"38;2;{rgb[0]};{rgb[1]};{rgb[2]}" in result

    @pytest.mark.parametrize(
        "code,idx", [(40, 0), (41, 1), (42, 2), (43, 3), (44, 4), (45, 5), (46, 6), (47, 7)]
    )
    def test_all_normal_background_colors(self, code: int, idx: int) -> None:
        f = self._make_filter()
        result = f.filter(f"\x1b[{code}m")
        rgb = PALETTES["ega"][idx]
        assert f"48;2;{rgb[0]};{rgb[1]};{rgb[2]}" in result


class TestColorFilterReset:
    def _make_filter(self) -> ColorFilter:
        cfg = ColorConfig(brightness=1.0, contrast=1.0, background_color=(16, 16, 16))
        return ColorFilter(cfg)

    def test_explicit_reset(self) -> None:
        f = self._make_filter()
        result = f.filter("\x1b[0m")
        assert "\x1b[0m" in result
        assert "\x1b[48;2;16;16;16m" in result

    def test_empty_reset(self) -> None:
        f = self._make_filter()
        result = f.filter("\x1b[m")
        assert "\x1b[0m" in result
        assert "\x1b[48;2;16;16;16m" in result

    def test_reset_in_compound_sequence(self) -> None:
        f = self._make_filter()
        result = f.filter("\x1b[0;31m")
        assert "\x1b[48;2;16;16;16m" in result


class TestColorFilterPassThrough:
    def _make_filter(self) -> ColorFilter:
        return ColorFilter(ColorConfig(brightness=1.0, contrast=1.0))

    def test_256_color_foreground(self) -> None:
        f = self._make_filter()
        result = f.filter("\x1b[38;5;196m")
        assert "38;5;196" in result

    def test_24bit_color_foreground(self) -> None:
        f = self._make_filter()
        result = f.filter("\x1b[38;2;100;200;50m")
        assert "38;2;100;200;50" in result

    def test_256_color_background(self) -> None:
        f = self._make_filter()
        result = f.filter("\x1b[48;5;42m")
        assert "48;5;42" in result

    def test_24bit_color_background(self) -> None:
        f = self._make_filter()
        result = f.filter("\x1b[48;2;10;20;30m")
        assert "48;2;10;20;30" in result

    def test_bold_pass_through(self) -> None:
        f = self._make_filter()
        result = f.filter("\x1b[1m")
        assert "\x1b[1m" in result

    def test_underline_pass_through(self) -> None:
        f = self._make_filter()
        result = f.filter("\x1b[4m")
        assert "\x1b[4m" in result

    def test_default_fg_pass_through(self) -> None:
        f = self._make_filter()
        result = f.filter("\x1b[39m")
        assert "39" in result

    def test_default_bg_pass_through(self) -> None:
        f = self._make_filter()
        result = f.filter("\x1b[49m")
        assert "49" in result

    def test_non_sgr_escape_pass_through(self) -> None:
        f = self._make_filter()
        result = f.filter("\x1b[2J")
        assert "\x1b[2J" in result

    def test_cursor_home_pass_through(self) -> None:
        f = self._make_filter()
        result = f.filter("\x1b[H")
        assert "\x1b[H" in result

    def test_colon_extended_color_pass_through(self) -> None:
        f = self._make_filter()
        result = f.filter("\x1b[38:2::255:0:0m")
        assert "\x1b[38:2::255:0:0m" in result


class TestColorFilterCompound:
    def _make_filter(self) -> ColorFilter:
        return ColorFilter(ColorConfig(brightness=1.0, contrast=1.0))

    def test_bold_plus_red_uses_bright(self) -> None:
        f = self._make_filter()
        result = f.filter("\x1b[1;31m")
        bright_red = PALETTES["ega"][9]
        assert f"38;2;{bright_red[0]};{bright_red[1]};{bright_red[2]}" in result

    def test_red_fg_green_bg(self) -> None:
        f = self._make_filter()
        result = f.filter("\x1b[31;42m")
        fg_rgb = PALETTES["ega"][1]
        bg_rgb = PALETTES["ega"][2]
        assert f"38;2;{fg_rgb[0]};{fg_rgb[1]};{fg_rgb[2]}" in result
        assert f"48;2;{bg_rgb[0]};{bg_rgb[1]};{bg_rgb[2]}" in result


class TestColorFilterBoldAsBright:
    def _make_filter(self) -> ColorFilter:
        return ColorFilter(ColorConfig(brightness=1.0, contrast=1.0))

    def test_bold_black_uses_bright_black(self) -> None:
        f = self._make_filter()
        result = f.filter("\x1b[1;30m")
        bright_black = PALETTES["ega"][8]
        assert f"38;2;{bright_black[0]};{bright_black[1]};{bright_black[2]}" in result

    def test_color_before_bold_in_same_seq(self) -> None:
        f = self._make_filter()
        result = f.filter("\x1b[30;1m")
        bright_black = PALETTES["ega"][8]
        assert f"38;2;{bright_black[0]};{bright_black[1]};{bright_black[2]}" in result

    def test_bold_persists_across_sequences(self) -> None:
        f = self._make_filter()
        f.filter("\x1b[1m")
        result = f.filter("\x1b[30m")
        bright_black = PALETTES["ega"][8]
        assert f"38;2;{bright_black[0]};{bright_black[1]};{bright_black[2]}" in result

    def test_bold_off_reverts_to_normal(self) -> None:
        f = self._make_filter()
        f.filter("\x1b[1m")
        f.filter("\x1b[22m")
        result = f.filter("\x1b[30m")
        normal_black = PALETTES["ega"][0]
        assert f"38;2;{normal_black[0]};{normal_black[1]};{normal_black[2]}" in result

    def test_reset_clears_bold(self) -> None:
        f = self._make_filter()
        f.filter("\x1b[1m")
        f.filter("\x1b[0m")
        result = f.filter("\x1b[30m")
        normal_black = PALETTES["ega"][0]
        assert f"38;2;{normal_black[0]};{normal_black[1]};{normal_black[2]}" in result

    def test_bold_does_not_affect_bright_colors(self) -> None:
        f = self._make_filter()
        result = f.filter("\x1b[1;90m")
        bright_black = PALETTES["ega"][8]
        assert f"38;2;{bright_black[0]};{bright_black[1]};{bright_black[2]}" in result

    def test_bold_does_not_affect_background(self) -> None:
        f = self._make_filter()
        result = f.filter("\x1b[1;40m")
        normal_black = PALETTES["ega"][0]
        assert f"48;2;{normal_black[0]};{normal_black[1]};{normal_black[2]}" in result

    @pytest.mark.parametrize(
        "code,normal_idx", [(30, 0), (31, 1), (32, 2), (33, 3), (34, 4), (35, 5), (36, 6), (37, 7)]
    )
    def test_all_bold_fg_use_bright_palette(self, code: int, normal_idx: int) -> None:
        f = self._make_filter()
        result = f.filter(f"\x1b[1;{code}m")
        bright_rgb = PALETTES["ega"][normal_idx + 8]
        assert f"38;2;{bright_rgb[0]};{bright_rgb[1]};{bright_rgb[2]}" in result


class TestColorFilterChunkedInput:
    def _make_filter(self) -> ColorFilter:
        return ColorFilter(ColorConfig(brightness=1.0, contrast=1.0))

    def test_split_at_esc(self) -> None:
        f = self._make_filter()
        result1 = f.filter("hello\x1b")
        assert "hello" in result1
        assert result1.endswith("hello")
        result2 = f.filter("[31mworld")
        rgb = PALETTES["ega"][1]
        assert f"38;2;{rgb[0]};{rgb[1]};{rgb[2]}" in result2
        assert "world" in result2

    def test_split_mid_params(self) -> None:
        f = self._make_filter()
        result1 = f.filter("hello\x1b[3")
        assert "hello" in result1
        result2 = f.filter("1mworld")
        rgb = PALETTES["ega"][1]
        assert f"38;2;{rgb[0]};{rgb[1]};{rgb[2]}" in result2
        assert "world" in result2

    def test_flush_returns_buffer(self) -> None:
        f = self._make_filter()
        f.filter("hello\x1b[3")
        flushed = f.flush()
        assert flushed == "\x1b[3"

    def test_flush_empty_when_no_buffer(self) -> None:
        f = self._make_filter()
        f.filter("hello")
        assert not f.flush()


class TestColorFilterInitialBackground:
    def test_first_output_has_background(self) -> None:
        f = ColorFilter(ColorConfig(brightness=1.0, contrast=1.0, background_color=(16, 16, 16)))
        result = f.filter("hello")
        assert result.startswith("\x1b[48;2;16;16;16m")
        assert result.endswith("hello")

    def test_second_output_no_extra_background(self) -> None:
        f = ColorFilter(ColorConfig(brightness=1.0, contrast=1.0, background_color=(16, 16, 16)))
        f.filter("hello")
        result2 = f.filter("world")
        assert not result2.startswith("\x1b[48;2;")
        assert result2 == "world"


class TestColorFilterPlainText:
    def test_plain_text_pass_through(self) -> None:
        f = ColorFilter(ColorConfig(brightness=1.0, contrast=1.0))
        result = f.filter("hello world")
        assert "hello world" in result

    def test_empty_string(self) -> None:
        f = ColorFilter(ColorConfig(brightness=1.0, contrast=1.0))
        # First call sets initial, but empty input returns ""
        result = f.filter("")
        assert not result


class TestColorFilterReverseVideo:
    def _make_filter(self) -> ColorFilter:
        return ColorFilter(
            ColorConfig(
                brightness=1.0, contrast=1.0, reverse_video=True, background_color=(16, 16, 16)
            )
        )

    def test_fg_becomes_bg(self) -> None:
        f = self._make_filter()
        result = f.filter("\x1b[31m")
        rgb = PALETTES["ega"][1]
        assert f"48;2;{rgb[0]};{rgb[1]};{rgb[2]}" in result

    def test_bg_becomes_fg(self) -> None:
        f = self._make_filter()
        result = f.filter("\x1b[41m")
        rgb = PALETTES["ega"][1]
        assert f"38;2;{rgb[0]};{rgb[1]};{rgb[2]}" in result

    def test_background_is_inverted(self) -> None:
        f = self._make_filter()
        result = f.filter("x")
        assert "\x1b[48;2;239;239;239m" in result


class TestColorFilterBrightnessContrast:
    def test_reduced_brightness(self) -> None:
        f = ColorFilter(ColorConfig(brightness=0.5, contrast=1.0))
        result = f.filter("\x1b[37m")
        ega_white = PALETTES["ega"][7]
        adjusted = _adjust_color(*ega_white, 0.5, 1.0)
        assert f"38;2;{adjusted[0]};{adjusted[1]};{adjusted[2]}" in result

    def test_reduced_contrast(self) -> None:
        f = ColorFilter(ColorConfig(brightness=1.0, contrast=0.5))
        result = f.filter("\x1b[31m")
        ega_red = PALETTES["ega"][1]
        adjusted = _adjust_color(*ega_red, 1.0, 0.5)
        assert f"38;2;{adjusted[0]};{adjusted[1]};{adjusted[2]}" in result


class TestColorFilterCustomBackground:
    def test_custom_background_in_reset(self) -> None:
        f = ColorFilter(ColorConfig(brightness=1.0, contrast=1.0, background_color=(32, 32, 48)))
        result = f.filter("\x1b[0m")
        assert "\x1b[48;2;32;32;48m" in result

    def test_custom_background_on_initial(self) -> None:
        f = ColorFilter(ColorConfig(brightness=1.0, contrast=1.0, background_color=(32, 32, 48)))
        result = f.filter("hello")
        assert result.startswith("\x1b[48;2;32;32;48m")


class TestColorFilterDifferentPalettes:
    @pytest.mark.parametrize("name", list(PALETTES.keys()))
    def test_palette_red_foreground(self, name: str) -> None:
        f = ColorFilter(ColorConfig(palette_name=name, brightness=1.0, contrast=1.0))
        result = f.filter("\x1b[31m")
        rgb = PALETTES[name][1]
        assert f"38;2;{rgb[0]};{rgb[1]};{rgb[2]}" in result
