"""Tests for toolbar fill algorithm and layout helpers."""

# 3rd party
import pytest

pytest.importorskip("blessed")

# local
from telnetlib3.client_repl_render import (
    _BAR_GAP_WIDTH,
    _SEPARATOR_WIDTH,
    _wcswidth,
    _vital_bar,
    _ToolbarSlot,
    _fill_toolbar,
    _layout_toolbar,
    _left_sep_widths,
)


def _text_slot(
    text, priority=1, order=0, side="left", min_width=0, label="", growable=False, grow_params=None
):
    return _ToolbarSlot(
        priority=priority,
        display_order=order,
        width=_wcswidth(text),
        fragments=[("", text)],
        side=side,
        min_width=min_width,
        label=label,
        growable=growable,
        grow_params=grow_params,
    )


def _bar_slot(raw, maxval, width, kind, priority=1, order=0):
    flash_elapsed = -1.0
    frags = _vital_bar(raw, maxval, width, kind, flash_elapsed=flash_elapsed)
    frags_w = sum(_wcswidth(t) for _, t in frags)
    return _ToolbarSlot(
        priority=priority,
        display_order=order,
        width=frags_w,
        fragments=frags,
        side="left",
        min_width=0,
        label="",
        growable=True,
        grow_params=(raw, maxval, kind, flash_elapsed),
    )


class TestFillToolbarNoGrowable:

    def test_no_growable_returns_unchanged(self):
        s1 = _text_slot("Lv.5", order=0)
        s2 = _text_slot("$100", order=1)
        left, right, sep = _fill_toolbar([s1, s2], [], 80)
        assert sep == _SEPARATOR_WIDTH
        assert [s.fragments for s in left] == [s1.fragments, s2.fragments]
        assert right == []

    def test_no_extra_space_returns_unchanged(self):
        s = _bar_slot(50, 100, 20, "hp")
        left, right, sep = _fill_toolbar([s], [], s.width)
        assert sep == _SEPARATOR_WIDTH
        assert len(left) == 1
        assert left[0].width == s.width


class TestFillToolbarGrows:

    def test_bar_grows_with_extra_space(self):
        s = _bar_slot(50, 100, 20, "hp")
        orig_w = s.width
        left, right, sep = _fill_toolbar([s], [], orig_w + 40)
        assert left[0].width > orig_w

    def test_room_name_grows(self):
        full_text = " The Grand Hall of Wizardry"
        s = _ToolbarSlot(
            priority=2,
            display_order=10,
            width=10,
            fragments=[("", full_text[:10])],
            side="right",
            min_width=5,
            label=full_text,
            growable=True,
            grow_params=(full_text,),
        )
        left, right, sep = _fill_toolbar([], [s], 60)
        assert right[0].width > 10

    def test_multiple_bars_share_space(self):
        hp = _bar_slot(50, 100, 20, "hp", priority=1, order=0)
        mp = _bar_slot(30, 80, 20, "mp", priority=2, order=1)
        hp_w = hp.width
        mp_w = mp.width
        total_min = hp_w + mp_w + _SEPARATOR_WIDTH
        left, right, sep = _fill_toolbar([hp, mp], [], total_min + 30)
        assert left[0].width > hp_w
        assert left[1].width > mp_w

    def test_separator_grows(self):
        hp = _bar_slot(50, 100, 20, "hp", priority=1, order=0)
        fixed = _text_slot("Lv.5", order=1)
        total_min = hp.width + fixed.width + _SEPARATOR_WIDTH
        _, _, sep = _fill_toolbar([hp, fixed], [], total_min + 40)
        assert sep >= _SEPARATOR_WIDTH


class TestFillToolbarEdgeCases:

    def test_single_slot_no_separators(self):
        s = _bar_slot(50, 100, 20, "hp")
        left, right, sep = _fill_toolbar([s], [], s.width + 20)
        assert sep == _SEPARATOR_WIDTH
        assert left[0].width > s.width

    def test_zero_cols_returns_unchanged(self):
        s = _bar_slot(50, 100, 20, "hp")
        left, right, sep = _fill_toolbar([s], [], 0)
        assert sep == _SEPARATOR_WIDTH
        assert left[0].width == s.width

    def test_negative_extra_returns_unchanged(self):
        s = _bar_slot(50, 100, 20, "hp")
        left, right, sep = _fill_toolbar([s], [], 5)
        assert sep == _SEPARATOR_WIDTH


class TestBarGapWidth:

    def test_adjacent_bars_use_bar_gap(self):
        hp = _bar_slot(50, 100, 20, "hp", order=0)
        mp = _bar_slot(30, 80, 20, "mp", order=1)
        gaps = _left_sep_widths([hp, mp])
        assert gaps == [_BAR_GAP_WIDTH]

    def test_bar_next_to_text_uses_separator(self):
        fixed = _text_slot("Lv.5", order=0)
        hp = _bar_slot(50, 100, 20, "hp", order=1)
        gaps = _left_sep_widths([fixed, hp])
        assert gaps == [_SEPARATOR_WIDTH]

    def test_three_bars_all_tight(self):
        hp = _bar_slot(50, 100, 20, "hp", order=0)
        mp = _bar_slot(30, 80, 20, "mp", order=1)
        xp = _bar_slot(10, 50, 20, "xp", order=2)
        gaps = _left_sep_widths([hp, mp, xp])
        assert gaps == [_BAR_GAP_WIDTH, _BAR_GAP_WIDTH]

    def test_mixed_slots_gaps(self):
        fixed = _text_slot("Lv.5", order=0)
        hp = _bar_slot(50, 100, 20, "hp", order=1)
        mp = _bar_slot(30, 80, 20, "mp", order=2)
        eta = _text_slot("ETA 2h", order=3)
        gaps = _left_sep_widths([fixed, hp, mp, eta])
        assert gaps == [_SEPARATOR_WIDTH, _BAR_GAP_WIDTH, _SEPARATOR_WIDTH]


class TestLayoutToolbarReturnsSlots:

    def test_returns_toolbar_slots(self):
        s = _text_slot("hello", priority=1, order=0, side="left")
        left, right = _layout_toolbar([s], 80)
        assert len(left) == 1
        assert isinstance(left[0], _ToolbarSlot)
        assert left[0].fragments == s.fragments

    def test_left_right_ordering(self):
        l1 = _text_slot("A", priority=1, order=0, side="left")
        r1 = _text_slot("B", priority=2, order=10, side="right")
        left, right = _layout_toolbar([l1, r1], 80)
        assert len(left) == 1
        assert len(right) == 1
        assert left[0].fragments == l1.fragments
        assert right[0].fragments == r1.fragments
