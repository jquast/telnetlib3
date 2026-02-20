"""Tests for telnetlib3.color_filter — ANSI color palette translation."""

# 3rd party
import pytest

# local
from telnetlib3.color_filter import (
    PALETTES,
    ColorConfig,
    ColorFilter,
    PetsciiColorFilter,
    AtasciiControlFilter,
    _adjust_color,
    _is_foreground_code,
    _sgr_code_to_palette_index,
)


def _make_filter(**kwargs: object) -> ColorFilter:
    cfg = ColorConfig(brightness=1.0, contrast=1.0, **kwargs)  # type: ignore[arg-type]
    return ColorFilter(cfg)


@pytest.mark.parametrize("name", list(PALETTES.keys()))
def test_palette_has_16_entries(name: str) -> None:
    assert len(PALETTES[name]) == 16


@pytest.mark.parametrize("name", list(PALETTES.keys()))
def test_palette_rgb_in_range(name: str) -> None:
    for r, g, b in PALETTES[name]:
        assert 0 <= r <= 255
        assert 0 <= g <= 255
        assert 0 <= b <= 255


def test_all_expected_palettes_exist() -> None:
    assert set(PALETTES.keys()) == {"ega", "cga", "vga", "xterm", "c64"}


def test_color_config_defaults() -> None:
    cfg = ColorConfig()
    assert cfg.palette_name == "ega"
    assert cfg.brightness == 1.0
    assert cfg.contrast == 1.0
    assert cfg.background_color == (0, 0, 0)
    assert cfg.ice_colors is True


class TestSgrCodeToPaletteIndex:
    @pytest.mark.parametrize(
        "code,expected",
        [
            (30, 0), (31, 1), (32, 2), (33, 3), (34, 4), (35, 5), (36, 6), (37, 7),
            (40, 0), (41, 1), (42, 2), (43, 3), (44, 4), (45, 5), (46, 6), (47, 7),
            (90, 8), (91, 9), (92, 10), (93, 11), (94, 12), (95, 13), (96, 14), (97, 15),
            (100, 8), (101, 9), (102, 10), (103, 11), (104, 12), (105, 13), (106, 14),
            (107, 15),
        ],
    )
    def test_color_code_maps_to_palette_index(self, code: int, expected: int) -> None:
        assert _sgr_code_to_palette_index(code) == expected

    @pytest.mark.parametrize("code", [0, 1, 4, 7, 22, 38, 39, 48, 49, 128])
    def test_non_color_returns_none(self, code: int) -> None:
        assert _sgr_code_to_palette_index(code) is None


@pytest.mark.parametrize("code", list(range(30, 38)) + list(range(90, 98)))
def test_is_foreground_code_true(code: int) -> None:
    assert _is_foreground_code(code) is True


@pytest.mark.parametrize("code", list(range(40, 48)) + list(range(100, 108)))
def test_is_foreground_code_false(code: int) -> None:
    assert _is_foreground_code(code) is False


@pytest.mark.parametrize(
    "r,g,b,brightness,contrast,expected",
    [
        (170, 85, 0, 1.0, 1.0, (170, 85, 0)),
        (200, 100, 50, 1.0, 0.0, (128, 128, 128)),
        (200, 100, 50, 0.0, 1.0, (0, 0, 0)),
        (200, 100, 0, 0.5, 1.0, (100, 50, 0)),
    ],
)
def test_adjust_color_values(
    r: int, g: int, b: int, brightness: float, contrast: float, expected: tuple[int, int, int]
) -> None:
    assert _adjust_color(r, g, b, brightness, contrast) == expected


def test_adjust_color_clamp_high() -> None:
    r, _, _ = _adjust_color(255, 255, 255, 1.0, 2.0)
    assert r == 255


def test_adjust_color_clamp_low() -> None:
    r, _, _ = _adjust_color(0, 0, 0, 1.0, 2.0)
    assert r == 0


def test_adjust_color_in_range() -> None:
    r, g, b = _adjust_color(170, 0, 0, 0.9, 0.8)
    assert 0 <= r <= 255
    assert 0 <= g <= 255
    assert 0 <= b <= 255


@pytest.mark.parametrize(
    "sgr,palette_idx,prefix",
    [("31", 1, "38;2"), ("41", 1, "48;2"), ("91", 9, "38;2"), ("101", 9, "48;2")],
)
def test_color_filter_basic_translation(sgr: str, palette_idx: int, prefix: str) -> None:
    f = _make_filter()
    result = f.filter(f"\x1b[{sgr}m")
    rgb = PALETTES["ega"][palette_idx]
    assert f"{prefix};{rgb[0]};{rgb[1]};{rgb[2]}" in result


@pytest.mark.parametrize(
    "code,idx", [(30, 0), (31, 1), (32, 2), (33, 3), (34, 4), (35, 5), (36, 6), (37, 7)]
)
def test_all_normal_foreground_colors(code: int, idx: int) -> None:
    f = _make_filter()
    result = f.filter(f"\x1b[{code}m")
    rgb = PALETTES["ega"][idx]
    assert f"38;2;{rgb[0]};{rgb[1]};{rgb[2]}" in result


@pytest.mark.parametrize(
    "code,idx", [(40, 0), (41, 1), (42, 2), (43, 3), (44, 4), (45, 5), (46, 6), (47, 7)]
)
def test_all_normal_background_colors(code: int, idx: int) -> None:
    f = _make_filter()
    result = f.filter(f"\x1b[{code}m")
    rgb = PALETTES["ega"][idx]
    assert f"48;2;{rgb[0]};{rgb[1]};{rgb[2]}" in result


class TestColorFilterReset:
    def test_explicit_reset(self) -> None:
        f = _make_filter(background_color=(0, 0, 0))
        result = f.filter("\x1b[0m")
        assert "48;2;0;0;0" in result
        assert "38;2;170;170;170" in result

    def test_empty_reset(self) -> None:
        f = _make_filter(background_color=(0, 0, 0))
        result = f.filter("\x1b[m")
        assert "\x1b[0m" in result
        assert "48;2;0;0;0" in result
        assert "38;2;170;170;170" in result

    def test_reset_in_compound_sequence(self) -> None:
        f = _make_filter(background_color=(0, 0, 0))
        assert "48;2;0;0;0" in f.filter("\x1b[0;31m")

    def test_reset_with_bg_preserves_explicit_bg(self) -> None:
        """SGR 0;30;42 must not override green bg with configured black bg."""
        f = _make_filter(background_color=(0, 0, 0))
        result = f.filter("\x1b[0;30;42m")
        assert "48;2;0;170;0" in result
        last_bg = result.rfind("48;2;")
        assert result[last_bg:].startswith("48;2;0;170;0")

    def test_reset_with_fg_preserves_explicit_fg(self) -> None:
        """SGR 0;31 must use red, not the injected default white."""
        f = _make_filter(background_color=(0, 0, 0))
        result = f.filter("\x1b[0;31m")
        last_fg = result.rfind("38;2;")
        assert result[last_fg:].startswith("38;2;170;0;0")

    def test_bold_after_reset_emits_bright_white(self) -> None:
        """ESC[0m ESC[1m should produce bright white (palette 15)."""
        f = _make_filter(background_color=(0, 0, 0))
        f.filter("\x1b[0m")
        assert "38;2;255;255;255" in f.filter("\x1b[1m")

    def test_bold_after_explicit_fg_emits_bright_color(self) -> None:
        """ESC[31m ESC[1m should produce bright red (palette 9)."""
        f = _make_filter(background_color=(0, 0, 0))
        f.filter("\x1b[31m")
        assert "38;2;255;85;85" in f.filter("\x1b[1m")

    def test_unbold_restores_normal_fg(self) -> None:
        """ESC[31m ESC[1m ESC[22m should restore normal red (palette 1)."""
        f = _make_filter(background_color=(0, 0, 0))
        f.filter("\x1b[31m")
        f.filter("\x1b[1m")
        assert "38;2;170;0;0" in f.filter("\x1b[22m")

    def test_bold_with_explicit_fg_in_same_seq_no_double_inject(self) -> None:
        """ESC[1;31m should not inject default bright fg."""
        f = _make_filter(background_color=(0, 0, 0))
        result = f.filter("\x1b[1;31m")
        assert "255;85;85" in result
        assert "255;255;255" not in result


@pytest.mark.parametrize(
    "seq,needle",
    [
        ("\x1b[38;5;196m", "38;5;196"),
        ("\x1b[38;2;100;200;50m", "38;2;100;200;50"),
        ("\x1b[48;5;42m", "48;5;42"),
        ("\x1b[48;2;10;20;30m", "48;2;10;20;30"),
    ],
)
def test_extended_color_pass_through(seq: str, needle: str) -> None:
    f = _make_filter()
    assert needle in f.filter(seq)


def test_bold_emits_bright_default_fg() -> None:
    f = _make_filter()
    result = f.filter("\x1b[1m")
    assert "1" in result
    assert "38;2;255;255;255" in result


@pytest.mark.parametrize(
    "seq,needle",
    [
        ("\x1b[4m", "\x1b[4m"),
        ("\x1b[2J", "\x1b[2J"),
        ("\x1b[H", "\x1b[H"),
        ("\x1b[38:2::255:0:0m", "\x1b[38:2::255:0:0m"),
    ],
)
def test_non_sgr_pass_through(seq: str, needle: str) -> None:
    f = _make_filter()
    assert needle in f.filter(seq)


@pytest.mark.parametrize(
    "sgr_code,expected_prefix",
    [
        ("39", "38;2;170;170;170"),
        ("49", "48;2;0;0;0"),
    ],
)
def test_default_color_translated(sgr_code: str, expected_prefix: str) -> None:
    f = _make_filter()
    assert expected_prefix in f.filter(f"\x1b[{sgr_code}m")


def test_bold_plus_red_uses_bright() -> None:
    f = _make_filter()
    result = f.filter("\x1b[1;31m")
    bright_red = PALETTES["ega"][9]
    assert f"38;2;{bright_red[0]};{bright_red[1]};{bright_red[2]}" in result


def test_red_fg_green_bg() -> None:
    f = _make_filter()
    result = f.filter("\x1b[31;42m")
    fg_rgb = PALETTES["ega"][1]
    bg_rgb = PALETTES["ega"][2]
    assert f"38;2;{fg_rgb[0]};{fg_rgb[1]};{fg_rgb[2]}" in result
    assert f"48;2;{bg_rgb[0]};{bg_rgb[1]};{bg_rgb[2]}" in result


def test_bold_black_uses_bright_black() -> None:
    f = _make_filter()
    result = f.filter("\x1b[1;30m")
    bright_black = PALETTES["ega"][8]
    assert f"38;2;{bright_black[0]};{bright_black[1]};{bright_black[2]}" in result


def test_color_before_bold_in_same_seq() -> None:
    f = _make_filter()
    result = f.filter("\x1b[30;1m")
    bright_black = PALETTES["ega"][8]
    assert f"38;2;{bright_black[0]};{bright_black[1]};{bright_black[2]}" in result


def test_bold_persists_across_sequences() -> None:
    f = _make_filter()
    f.filter("\x1b[1m")
    result = f.filter("\x1b[30m")
    bright_black = PALETTES["ega"][8]
    assert f"38;2;{bright_black[0]};{bright_black[1]};{bright_black[2]}" in result


def test_bold_off_reverts_to_normal() -> None:
    f = _make_filter()
    f.filter("\x1b[1m")
    f.filter("\x1b[22m")
    result = f.filter("\x1b[30m")
    normal_black = PALETTES["ega"][0]
    assert f"38;2;{normal_black[0]};{normal_black[1]};{normal_black[2]}" in result


def test_reset_clears_bold() -> None:
    f = _make_filter()
    f.filter("\x1b[1m")
    f.filter("\x1b[0m")
    result = f.filter("\x1b[30m")
    normal_black = PALETTES["ega"][0]
    assert f"38;2;{normal_black[0]};{normal_black[1]};{normal_black[2]}" in result


def test_bold_does_not_affect_bright_colors() -> None:
    f = _make_filter()
    result = f.filter("\x1b[1;90m")
    bright_black = PALETTES["ega"][8]
    assert f"38;2;{bright_black[0]};{bright_black[1]};{bright_black[2]}" in result


def test_bold_does_not_affect_background() -> None:
    f = _make_filter()
    result = f.filter("\x1b[1;40m")
    normal_black = PALETTES["ega"][0]
    assert f"48;2;{normal_black[0]};{normal_black[1]};{normal_black[2]}" in result


@pytest.mark.parametrize(
    "code,normal_idx", [(30, 0), (31, 1), (32, 2), (33, 3), (34, 4), (35, 5), (36, 6), (37, 7)]
)
def test_all_bold_fg_use_bright_palette(code: int, normal_idx: int) -> None:
    f = _make_filter()
    result = f.filter(f"\x1b[1;{code}m")
    bright_rgb = PALETTES["ega"][normal_idx + 8]
    assert f"38;2;{bright_rgb[0]};{bright_rgb[1]};{bright_rgb[2]}" in result


def test_reset_bold_color_in_same_seq() -> None:
    f = _make_filter()
    result = f.filter("\x1b[0;1;34m")
    bright_blue = PALETTES["ega"][12]
    assert f"38;2;{bright_blue[0]};{bright_blue[1]};{bright_blue[2]}" in result


class TestColorFilterIceColors:
    def _make_ice_filter(self, ice_colors: bool = True) -> ColorFilter:
        return ColorFilter(ColorConfig(brightness=1.0, contrast=1.0, ice_colors=ice_colors))

    def test_blink_bg_uses_bright_bg(self) -> None:
        f = self._make_ice_filter()
        result = f.filter("\x1b[5;40m")
        bright_black = PALETTES["ega"][8]
        assert f"48;2;{bright_black[0]};{bright_black[1]};{bright_black[2]}" in result

    def test_bg_before_blink_in_same_seq(self) -> None:
        f = self._make_ice_filter()
        result = f.filter("\x1b[40;5m")
        bright_black = PALETTES["ega"][8]
        assert f"48;2;{bright_black[0]};{bright_black[1]};{bright_black[2]}" in result

    def test_blink_persists_across_sequences(self) -> None:
        f = self._make_ice_filter()
        f.filter("\x1b[5m")
        result = f.filter("\x1b[40m")
        bright_black = PALETTES["ega"][8]
        assert f"48;2;{bright_black[0]};{bright_black[1]};{bright_black[2]}" in result

    def test_blink_off_reverts_to_normal(self) -> None:
        f = self._make_ice_filter()
        f.filter("\x1b[5m")
        f.filter("\x1b[25m")
        result = f.filter("\x1b[40m")
        normal_black = PALETTES["ega"][0]
        assert f"48;2;{normal_black[0]};{normal_black[1]};{normal_black[2]}" in result

    def test_reset_clears_blink(self) -> None:
        f = self._make_ice_filter()
        f.filter("\x1b[5m")
        f.filter("\x1b[0m")
        result = f.filter("\x1b[40m")
        normal_black = PALETTES["ega"][0]
        assert f"48;2;{normal_black[0]};{normal_black[1]};{normal_black[2]}" in result

    def test_blink_does_not_affect_foreground(self) -> None:
        f = self._make_ice_filter()
        result = f.filter("\x1b[5;30m")
        normal_black = PALETTES["ega"][0]
        assert f"38;2;{normal_black[0]};{normal_black[1]};{normal_black[2]}" in result

    def test_blink_does_not_affect_bright_bg(self) -> None:
        f = self._make_ice_filter()
        result = f.filter("\x1b[5;100m")
        bright_black = PALETTES["ega"][8]
        assert f"48;2;{bright_black[0]};{bright_black[1]};{bright_black[2]}" in result

    @pytest.mark.parametrize(
        "code,normal_idx", [(40, 0), (41, 1), (42, 2), (43, 3), (44, 4), (45, 5), (46, 6), (47, 7)]
    )
    def test_all_blink_bg_use_bright_palette(self, code: int, normal_idx: int) -> None:
        f = self._make_ice_filter()
        result = f.filter(f"\x1b[5;{code}m")
        bright_rgb = PALETTES["ega"][normal_idx + 8]
        assert f"48;2;{bright_rgb[0]};{bright_rgb[1]};{bright_rgb[2]}" in result

    def test_reset_blink_bg_in_same_seq(self) -> None:
        f = self._make_ice_filter()
        result = f.filter("\x1b[0;5;41m")
        bright_red = PALETTES["ega"][9]
        assert f"48;2;{bright_red[0]};{bright_red[1]};{bright_red[2]}" in result

    def test_ice_colors_disabled(self) -> None:
        f = self._make_ice_filter(ice_colors=False)
        f.filter("x")
        result = f.filter("\x1b[5;40m")
        normal_black = PALETTES["ega"][0]
        assert f"48;2;{normal_black[0]};{normal_black[1]};{normal_black[2]}" in result
        params = result.split("\x1b[")[1].split("m")[0]
        assert "5" in params.split(";")


class TestColorFilterChunkedInput:
    def test_split_at_esc(self) -> None:
        f = _make_filter()
        result1 = f.filter("hello\x1b")
        assert "hello" in result1
        assert result1.endswith("hello")
        result2 = f.filter("[31mworld")
        rgb = PALETTES["ega"][1]
        assert f"38;2;{rgb[0]};{rgb[1]};{rgb[2]}" in result2
        assert "world" in result2

    def test_split_mid_params(self) -> None:
        f = _make_filter()
        result1 = f.filter("hello\x1b[3")
        assert "hello" in result1
        result2 = f.filter("1mworld")
        rgb = PALETTES["ega"][1]
        assert f"38;2;{rgb[0]};{rgb[1]};{rgb[2]}" in result2
        assert "world" in result2

    def test_flush_returns_buffer(self) -> None:
        f = _make_filter()
        f.filter("hello\x1b[3")
        assert f.flush() == "\x1b[3"

    def test_flush_empty_when_no_buffer(self) -> None:
        f = _make_filter()
        f.filter("hello")
        assert not f.flush()


def test_color_filter_initial_background_first_output() -> None:
    f = ColorFilter(ColorConfig(brightness=1.0, contrast=1.0, background_color=(0, 0, 0)))
    result = f.filter("hello")
    assert result.startswith("\x1b[48;2;0;0;0m")
    assert result.endswith("hello")


def test_color_filter_initial_background_second_output() -> None:
    f = ColorFilter(ColorConfig(brightness=1.0, contrast=1.0, background_color=(0, 0, 0)))
    f.filter("hello")
    result2 = f.filter("world")
    assert not result2.startswith("\x1b[48;2;")
    assert result2 == "world"


def test_color_filter_plain_text_pass_through() -> None:
    f = _make_filter()
    assert "hello world" in f.filter("hello world")


def test_color_filter_empty_string() -> None:
    f = _make_filter()
    assert not f.filter("")


def test_color_filter_reduced_brightness() -> None:
    f = ColorFilter(ColorConfig(brightness=0.5, contrast=1.0))
    result = f.filter("\x1b[37m")
    ega_white = PALETTES["ega"][7]
    adjusted = _adjust_color(*ega_white, 0.5, 1.0)
    assert f"38;2;{adjusted[0]};{adjusted[1]};{adjusted[2]}" in result


def test_color_filter_reduced_contrast() -> None:
    f = ColorFilter(ColorConfig(brightness=1.0, contrast=0.5))
    result = f.filter("\x1b[31m")
    ega_red = PALETTES["ega"][1]
    adjusted = _adjust_color(*ega_red, 1.0, 0.5)
    assert f"38;2;{adjusted[0]};{adjusted[1]};{adjusted[2]}" in result


def test_color_filter_custom_background_in_reset() -> None:
    f = ColorFilter(ColorConfig(brightness=1.0, contrast=1.0, background_color=(32, 32, 48)))
    assert "\x1b[48;2;32;32;48m" in f.filter("\x1b[0m")


def test_color_filter_custom_background_on_initial() -> None:
    f = ColorFilter(ColorConfig(brightness=1.0, contrast=1.0, background_color=(32, 32, 48)))
    assert f.filter("hello").startswith("\x1b[48;2;32;32;48m")


@pytest.mark.parametrize("name", [n for n in PALETTES if n != "c64"])
def test_color_filter_palette_red_foreground(name: str) -> None:
    f = ColorFilter(ColorConfig(palette_name=name, brightness=1.0, contrast=1.0))
    result = f.filter("\x1b[31m")
    rgb = PALETTES[name][1]
    assert f"38;2;{rgb[0]};{rgb[1]};{rgb[2]}" in result


class TestPetsciiColorFilter:
    def _make_filter(self, **kwargs: object) -> PetsciiColorFilter:
        cfg = ColorConfig(brightness=1.0, contrast=1.0, **kwargs)  # type: ignore[arg-type]
        return PetsciiColorFilter(cfg)

    @pytest.mark.parametrize(
        "ctrl_char,palette_idx",
        [
            ("\x05", 1),
            ("\x1c", 2),
            ("\x1e", 5),
            ("\x1f", 6),
            ("\x81", 8),
            ("\x90", 0),
            ("\x95", 9),
            ("\x96", 10),
            ("\x97", 11),
            ("\x98", 12),
            ("\x99", 13),
            ("\x9a", 14),
            ("\x9b", 15),
            ("\x9c", 4),
            ("\x9e", 7),
            ("\x9f", 3),
        ],
    )
    def test_color_code_to_24bit(self, ctrl_char: str, palette_idx: int) -> None:
        f = self._make_filter()
        result = f.filter(f"hello{ctrl_char}world")
        rgb = PALETTES["c64"][palette_idx]
        assert f"\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m" in result
        assert ctrl_char not in result
        assert "hello" in result
        assert "world" in result

    def test_rvs_on(self) -> None:
        f = self._make_filter()
        result = f.filter("before\x12after")
        assert "\x1b[7m" in result
        assert "\x12" not in result

    def test_rvs_off(self) -> None:
        f = self._make_filter()
        result = f.filter("before\x92after")
        assert "\x1b[27m" in result
        assert "\x92" not in result

    def test_mixed_colors_and_rvs(self) -> None:
        f = self._make_filter()
        result = f.filter("\x1c\x12hello\x92\x05world")
        red_rgb = PALETTES["c64"][2]
        white_rgb = PALETTES["c64"][1]
        assert f"\x1b[38;2;{red_rgb[0]};{red_rgb[1]};{red_rgb[2]}m" in result
        assert "\x1b[7m" in result
        assert "\x1b[27m" in result
        assert f"\x1b[38;2;{white_rgb[0]};{white_rgb[1]};{white_rgb[2]}m" in result
        assert "hello" in result
        assert "world" in result

    def test_plain_text_unchanged(self) -> None:
        f = self._make_filter()
        assert f.filter("hello world") == "hello world"

    def test_non_petscii_control_chars_unchanged(self) -> None:
        f = self._make_filter()
        assert f.filter("A\x07B\x0bC") == "A\x07B\x0bC"

    def test_cursor_controls_translated(self) -> None:
        f = self._make_filter()
        assert f.filter("A\x13B") == "A\x1b[HB"
        assert f.filter("A\x93B") == "A\x1b[2JB"
        assert f.filter("A\x11B") == "A\x1b[BB"
        assert f.filter("A\x91B") == "A\x1b[AB"
        assert f.filter("A\x1dB") == "A\x1b[CB"
        assert f.filter("A\x9dB") == "A\x1b[DB"
        assert f.filter("A\x14B") == "A\x08\x1b[PB"

    def test_flush_returns_empty(self) -> None:
        f = self._make_filter()
        assert not f.flush()

    def test_brightness_contrast_applied(self) -> None:
        f_full = PetsciiColorFilter(ColorConfig(brightness=1.0, contrast=1.0))
        f_dim = PetsciiColorFilter(ColorConfig(brightness=0.5, contrast=0.5))
        assert f_full.filter("\x1c") != f_dim.filter("\x1c")

    def test_default_config(self) -> None:
        f = PetsciiColorFilter()
        assert "\x1b[38;2;" in f.filter("\x1c")


class TestAtasciiControlFilter:
    @pytest.mark.parametrize(
        "glyph,expected",
        [
            ("\u25c0", "\x08\x1b[P"),
            ("\u25b6", "\t"),
            ("\u21b0", "\x1b[2J\x1b[H"),
            ("\u2191", "\x1b[A"),
            ("\u2193", "\x1b[B"),
            ("\u2190", "\x1b[D"),
            ("\u2192", "\x1b[C"),
        ],
    )
    def test_control_glyph_translated(self, glyph: str, expected: str) -> None:
        f = AtasciiControlFilter()
        assert f.filter(f"before{glyph}after") == f"before{expected}after"

    def test_backspace_erases(self) -> None:
        f = AtasciiControlFilter()
        assert f.filter("DINGO\u25c0\u25c0\u25c0\u25c0\u25c0") == ("DINGO" + "\x08\x1b[P" * 5)

    def test_plain_text_unchanged(self) -> None:
        f = AtasciiControlFilter()
        assert f.filter("hello world") == "hello world"

    def test_atascii_graphics_unchanged(self) -> None:
        f = AtasciiControlFilter()
        text = "\u2663\u2665\u2666\u2660"
        assert f.filter(text) == text

    def test_flush_returns_empty(self) -> None:
        f = AtasciiControlFilter()
        assert not f.flush()

    def test_multiple_controls_in_one_string(self) -> None:
        f = AtasciiControlFilter()
        assert f.filter("\u2191\u2193\u2190\u2192") == "\x1b[A\x1b[B\x1b[D\x1b[C"
