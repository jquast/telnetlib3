"""
Display, REPL, and post-script functions for telnet fingerprinting.

This module contains all terminal display (blessed/prettytable), ucs-detect
integration, and interactive REPL code split from :mod:`fingerprinting`.
"""

# std imports
import json
import logging
import os
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .fingerprinting import (
    AMBIGUOUS_WIDTH_UNKNOWN,
    DATA_DIR,
    _UNKNOWN_TERMINAL_HASH,
    _atomic_json_write,
    _cooked_input,
    _load_fingerprint_names,
    _resolve_hash_name,
    _validate_suggestion,
)

__all__ = ("fingerprinting_post_script",)

logger = logging.getLogger("telnetlib3.fingerprint")

# DECSCUSR cursor styles
_CURSOR_STEADY_BLOCK = "\x1b[2 q"
_CURSOR_BLINK_UNDERLINE = "\x1b[3 q"


def _run_ucs_detect() -> Optional[Dict[str, Any]]:
    """Run ucs-detect if available and return terminal fingerprint data."""
    import shutil
    import subprocess
    import tempfile

    ucs_detect = shutil.which("ucs-detect")
    if not ucs_detect:
        return None

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        result = subprocess.run(
            [
                ucs_detect,
                "--limit-category-time=1",
                "--limit-codepoints=1000",
                "--limit-errors=2",
                "--probe-silently",
                "--no-final-summary",
                "--no-languages-test",
                "--save-json", str(tmp_path),
            ],
            timeout=120,
        )

        if result.returncode != 0:
            return None

        if not tmp_path.exists():
            logger.warning("ucs-detect did not create output file")
            return None

        with open(tmp_path) as f:
            terminal_data = json.load(f)

        for key in ("python_version", "datetime", "system", "wcwidth_version"):
            terminal_data.pop(key, None)

        return terminal_data

    finally:
        tmp_path.unlink()


def _create_terminal_fingerprint(terminal_data: Dict[str, Any]) -> Dict[str, Any]:
    """Create anonymized terminal fingerprint for hashing."""
    fingerprint: Dict[str, Any] = {}

    results = terminal_data.get("terminal_results", {})
    fingerprint["software_name"] = terminal_data.get("software_name", "unknown")
    fingerprint["software_version"] = terminal_data.get("software_version", "unknown")

    fingerprint["number_of_colors"] = results.get("number_of_colors")
    fingerprint["sixel"] = results.get("sixel", False)
    fingerprint["kitty_graphics"] = results.get("kitty_graphics", False)
    fingerprint["kitty_clipboard_protocol"] = results.get(
        "kitty_clipboard_protocol", False
    )

    da = results.get("device_attributes", {})
    fingerprint["da_service_class"] = da.get("service_class")
    fingerprint["da_extensions"] = sorted(da.get("extensions", []))

    fingerprint["ambiguous_width"] = terminal_data.get("ambiguous_width")

    return fingerprint


def _hash_terminal_fingerprint(terminal_fingerprint: Dict[str, Any]) -> str:
    """Create deterministic SHA256 hash of terminal fingerprint."""
    import hashlib

    canonical = json.dumps(
        terminal_fingerprint, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _wrap_options(options: List[str], max_width: int = 30) -> str:
    """Word-wrap a list of options to fit within max_width."""
    if not options:
        return ""
    return "\n".join(textwrap.wrap(", ".join(options), width=max_width))


def _color_yes_no(term, value: bool) -> str:
    """Apply green/red coloring to boolean value."""
    if value:
        return term.green2("Yes")
    return term.firebrick1("No")


def _format_ttype(
    extra: Dict[str, Any], session_data: Dict[str, Any], wrap_width: int = 30
) -> Optional[str]:
    """Format terminal type from TTYPE cycle for compact display."""
    ttype_cycle = session_data.get("ttype_cycle", [])
    term_type = extra.get("TERM") or extra.get("term")
    if not term_type and not ttype_cycle:
        return None
    primary = ttype_cycle[0] if ttype_cycle else term_type
    primary_lower = primary.lower() if primary else ""
    others = []
    seen = {primary_lower}
    for ttype_val in ttype_cycle[1:]:
        t_lower = ttype_val.lower()
        if t_lower not in seen:
            seen.add(t_lower)
            others.append(t_lower)
    type_str = primary or ""
    if others:
        suffix = ", ".join(others)
        if len(type_str) + len(suffix) + 3 > wrap_width:
            wrapped = "\n".join(textwrap.wrap(suffix, width=wrap_width - 2))
            type_str += f" ({wrapped})"
        else:
            type_str += f" ({suffix})"
    return type_str


def _is_utf8_charset(value: str) -> bool:
    """Test whether a charset or encoding string refers to UTF-8."""
    return value.lower().replace("-", "").replace("_", "") in (
        "utf8",
        "unicode11utf8",
    )


def _format_encoding(
    extra: Dict[str, Any],
    proto_data: Dict[str, Any],
    ambiguous_width: Optional[int] = None,
) -> Optional[Tuple[str, str]]:
    """Consolidate LANG, charset, and encoding into a single key-value pair."""
    lang_val = extra.get("LANG")
    charset_val = extra.get("charset")
    encoding_val = proto_data.get("encoding")

    no_unicode = ambiguous_width == AMBIGUOUS_WIDTH_UNKNOWN

    if charset_val and no_unicode and _is_utf8_charset(charset_val):
        charset_val = "unknown (ascii-only)"

    if lang_val and charset_val:
        return ("LANG (Charset)", f"{lang_val} ({charset_val})")
    elif lang_val:
        return ("LANG", lang_val)
    elif charset_val:
        return ("Charset", charset_val)
    elif encoding_val and encoding_val != "None":
        return ("Encoding", encoding_val)
    return None


def _build_terminal_rows(term, data: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Build (key, value) tuples for terminal capabilities table."""
    pairs: List[Tuple[str, str]] = []
    terminal_probe = data.get("terminal-probe", {})
    terminal_data = terminal_probe.get("session-data", {})
    terminal_results = terminal_data.get("terminal_results", {})
    if not terminal_data:
        return pairs

    if software := terminal_data.get("software_name"):
        if ver := terminal_data.get("software_version"):
            software += f" {ver}"
        if len(software) > 15:
            software = software[:14] + "\u2026"
        pairs.append(("Software", software))

    telnet_probe = data.get("telnet-probe", {})
    session_data = telnet_probe.get("session-data", {})
    extra = session_data.get("extra", {})
    cols = extra.get("cols") or extra.get("COLUMNS")
    rows = extra.get("rows") or extra.get("LINES")
    if cols and rows:
        size_str = f"{cols}x{rows}"
        cell_w = terminal_results.get("cell_width")
        cell_h = terminal_results.get("cell_height")
        if cell_w and cell_h:
            size_str += f" (*{cell_w}x{cell_h})"
        pairs.append(("Size", size_str))

    if (n_colors := terminal_results.get("number_of_colors")) is not None:
        if n_colors >= 16777216:
            color_str = term.green2("24-bit")
        elif n_colors <= 256:
            color_str = term.firebrick1(f"{n_colors}")
        else:
            color_str = term.yellow(f"{n_colors}")
        pairs.append(("Colors", color_str))

    has_fg = terminal_results.get("foreground_color_hex") is not None
    has_bg = terminal_results.get("background_color_hex") is not None
    if has_fg or has_bg:
        pairs.append(("fg/bg colors", _color_yes_no(term, has_fg and has_bg)))

    has_kitty_gfx = terminal_results.get("kitty_graphics", False)
    has_iterm2_gfx = (
        terminal_results.get("iterm2_features") or {}
    ).get("supported", False)
    has_sixel = terminal_results.get("sixel", False)
    if has_kitty_gfx or has_iterm2_gfx:
        protocols = []
        if has_kitty_gfx:
            protocols.append("Kitty")
        if has_iterm2_gfx:
            protocols.append("iTerm2")
        if has_sixel:
            protocols.append("Sixel")
        pairs.append(("Graphics", term.green2(", ".join(protocols))))
    elif has_sixel:
        pairs.append(("Graphics", term.yellow("Sixel")))
    elif any(
        k in terminal_results
        for k in ("sixel", "kitty_graphics", "iterm2_features")
    ):
        pairs.append(("Graphics", term.firebrick1("No")))

    if da := terminal_results.get("device_attributes"):
        if (sc := da.get("service_class")) is not None:
            class_names = {
                1: "VT100", 2: "VT200", 18: "VT330",
                41: "VT420", 61: "VT500", 62: "VT500",
                64: "VT500", 65: "VT500",
            }
            pairs.append((
                "Device Class", class_names.get(sc, f"Class {sc}")
            ))

    screen_ratio = terminal_results.get("screen_ratio")
    if screen_ratio:
        ratio_name = terminal_results.get("screen_ratio_name", "")
        if ratio_name:
            pairs.append(("Aspect Ratio", f"{screen_ratio} ({ratio_name})"))
        else:
            pairs.append(("Aspect Ratio", screen_ratio))

    ambiguous_width = terminal_data.get("ambiguous_width")
    if ambiguous_width == 2:
        pairs.append(("Ambiguous Width", "wide (2)"))

    modes = terminal_results.get("modes", {})
    mode_2027 = modes.get(2027, modes.get("2027"))
    if mode_2027 is not None:
        gc_value = _color_yes_no(term, mode_2027.get("supported"))
        pairs.append(("Graphemes(2027)", gc_value))
    elif modes:
        pairs.append(("Graphemes(2027)", term.yellow("N/A")))

    test_results = terminal_data.get("test_results", {})
    _emoji_keys = (
        "unicode_wide_results", "emoji_zwj_results",
        "emoji_vs16_results", "emoji_vs15_results",
    )
    all_pcts = []
    for key in _emoji_keys:
        for entry in test_results.get(key, {}).values():
            if (pct := entry.get("pct_success")) is not None:
                all_pcts.append(pct)
    if all_pcts:
        avg = sum(all_pcts) / len(all_pcts)
        if avg >= 99.0:
            pairs.append(("Emoji", term.green2("Yes")))
        elif avg >= 33.3:
            pairs.append(("Emoji", term.yellow("Partial")))
        else:
            pairs.append(("Emoji", term.firebrick1("No")))

    return pairs


def _build_telnet_rows(term, data: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Build (key, value) tuples for telnet protocol table."""
    pairs: List[Tuple[str, str]] = []
    telnet_probe = data.get("telnet-probe", {})
    proto_data = telnet_probe.get("fingerprint-data", {})
    session_data = telnet_probe.get("session-data", {})
    extra = session_data.get("extra", {})

    if fp_hash := telnet_probe.get("fingerprint"):
        pairs.append(("Fingerprint", fp_hash))

    wrap_width = 30
    if type_str := _format_ttype(extra, session_data, wrap_width):
        pairs.append(("Terminal Type", type_str))

    terminal_probe = data.get("terminal-probe", {})
    aw = terminal_probe.get("session-data", {}).get("ambiguous_width")
    if encoding_pair := _format_encoding(extra, proto_data, aw):
        pairs.append(encoding_pair)

    if supported := proto_data.get("supported-options"):
        pairs.append(("Options", _wrap_options(supported, wrap_width)))

    slc_tab = session_data.get("slc_tab", {})
    if slc_tab:
        slc_set = slc_tab.get("set", {})
        slc_unset = slc_tab.get("unset", [])
        slc_nosupport = slc_tab.get("nosupport", [])
        parts = []
        if slc_set:
            parts.append(f"{len(slc_set)} set")
        if slc_unset:
            parts.append(f"{len(slc_unset)} unset")
        if slc_nosupport:
            parts.append(f"{len(slc_nosupport)} nosupport")
        if parts:
            pairs.append(("SLC", ", ".join(parts)))

    env_vars = []
    for key in ("USER", "HOME", "SHELL"):
        if proto_data.get(key) == "True":
            env_vars.append(key)
    if env_vars:
        pairs.append(("Environment", ", ".join(env_vars)))

    if tspeed := extra.get("tspeed"):
        pairs.append(("Speed", tspeed))

    return pairs


def _make_terminal(**kwargs):
    """Create a blessed Terminal, falling back to ``ansi`` on setupterm failure."""
    import warnings
    from blessed import Terminal

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        term = Terminal(**kwargs)
    if any("setupterm" in str(w.message) for w in caught):
        kwargs["kind"] = "ansi"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            term = Terminal(**kwargs)
    return term


def _has_unicode(data: Dict[str, Any]) -> bool:
    """Return whether the terminal supports unicode rendering."""
    aw = (data.get("terminal-probe", {})
          .get("session-data", {})
          .get("ambiguous_width", AMBIGUOUS_WIDTH_UNKNOWN))
    return aw >= 1


def _sync_timeout(data: Dict[str, Any]) -> float:
    """Return synchronized output timeout based on measured RTT."""
    cps = (data.get("terminal-probe", {})
           .get("session-data", {})
           .get("cps_summary", {}))
    if (rtt_max := cps.get("rtt_max_ms")) and rtt_max > 0:
        return rtt_max * 1.1 / 1000.0
    return 1.0


def _cursor_style(style: str, has_unicode: bool) -> str:
    """Return DECSCUSR sequence, or empty string for non-unicode terminals."""
    return style if has_unicode else ""


def _cursor_bracket(term, has_unicode: bool) -> str:
    """Return bold-magenta cursor bracket with steady block cursor."""
    block = "\u2588" if has_unicode else " "
    return (f"{term.bold_magenta}[{block}]{term.normal}"
            f"\b\b{_cursor_style(_CURSOR_STEADY_BLOCK, has_unicode)}")


def _display_compact_summary(data: Dict[str, Any], term=None) -> bool:
    """Display compact fingerprint summary using prettytable."""
    try:
        from prettytable import PrettyTable
        from ucs_detect import (
            _collect_side_by_side_lines,
            _paginated_write,
        )
    except ImportError:
        return False

    if term is None:
        term = _make_terminal()

    has_unicode = _has_unicode(data)

    def make_table(title, pairs):
        tbl = PrettyTable()
        if has_unicode:
            tbl.horizontal_char = "\u2550"
            tbl.vertical_char = "\u2551"
            tbl.junction_char = "\u256c"
            tbl.top_junction_char = "\u2566"
            tbl.bottom_junction_char = "\u2569"
            tbl.left_junction_char = "\u2560"
            tbl.right_junction_char = "\u2563"
            tbl.top_left_junction_char = "\u2554"
            tbl.top_right_junction_char = "\u2557"
            tbl.bottom_left_junction_char = "\u255a"
            tbl.bottom_right_junction_char = "\u255d"
        tbl.title = term.magenta(title)
        tbl.field_names = ["Attribute", "Value"]
        tbl.align["Attribute"] = "r"
        tbl.align["Value"] = "l"
        tbl.header = False
        tbl.max_table_width = max(40, (term.width or 80) - 1)
        for key, value in pairs:
            tbl.add_row([key or "", value])
        return str(tbl)

    table_strings = []

    terminal_rows = _build_terminal_rows(term, data)
    if terminal_rows:
        table_strings.append(make_table("Terminal", terminal_rows))

    telnet_rows = _build_telnet_rows(term, data)
    if telnet_rows:
        table_strings.append(make_table("Telnet", telnet_rows))

    if not table_strings:
        return False

    import sys
    timeout = _sync_timeout(data)

    sys.stdout.write(
        term.normal + _cursor_style(_CURSOR_BLINK_UNDERLINE, has_unicode))

    widths = [len(s.split("\n", 1)[0]) for s in table_strings]
    side_by_side = len(widths) < 2 or sum(widths) + 1 < (term.width or 80)

    if side_by_side:
        all_lines = _collect_side_by_side_lines(term, table_strings)
        if has_unicode:
            with term.synchronized_output(timeout=timeout):
                _paginated_write(term, sys.stdout.write, all_lines,
                                 skip_initial_newline=True)
        else:
            _paginated_write(term, sys.stdout.write, all_lines,
                             skip_initial_newline=True)
    else:
        total_lines = sum(len(s.split("\n")) for s in table_strings)
        height = term.height or 25
        needs_paging = total_lines + len(table_strings) > height
        for idx, tbl in enumerate(table_strings):
            lines = tbl.split("\n")
            if has_unicode:
                with term.synchronized_output(timeout=timeout):
                    _paginated_write(term, sys.stdout.write,
                                     lines + [""],
                                     skip_initial_newline=True)
            else:
                _paginated_write(term, sys.stdout.write, lines + [""],
                                 skip_initial_newline=True)
            if (needs_paging
                    and idx + 1 < len(table_strings)
                    and term.is_a_tty):
                sys.stdout.write(
                    f"press return to continue: "
                    f"{_cursor_bracket(term, has_unicode)}")
                sys.stdout.flush()
                with term.cbreak():
                    term.inkey(timeout=None)
                sys.stdout.write(
                    f"\r{term.clear_eol}"
                    f"{_cursor_style(_CURSOR_BLINK_UNDERLINE, has_unicode)}")
                sys.stdout.flush()
    return True


def _build_seen_counts(
    data: Dict[str, Any], names: Optional[Dict[str, str]] = None
) -> str:
    """Build friendly "seen before" text from folder and session counts."""
    if DATA_DIR is None or not DATA_DIR.exists():
        return ""

    telnet_probe = data.get("telnet-probe", {})
    if not (telnet_hash := telnet_probe.get("fingerprint")):
        return ""

    terminal_probe = data.get("terminal-probe", {})
    terminal_hash = terminal_probe.get("fingerprint", _UNKNOWN_TERMINAL_HASH)

    _names = names or {}
    telnet_name = _resolve_hash_name(telnet_hash, _names)
    terminal_name = _resolve_hash_name(terminal_hash, _names)

    folder_path = DATA_DIR / f"client-{telnet_hash}-{terminal_hash}"
    if folder_path.is_dir():
        like_count = sum(
            1 for f in folder_path.iterdir() if f.suffix == ".json"
        )
    else:
        like_count = 0

    visit_count = len(data.get("sessions", []))
    client_ip = _client_ip(data)

    lines: List[str] = []
    if like_count > 1:
        others = like_count - 1
        noun = "client" if others == 1 else "clients"
        lines.append(
            f"I've seen {others} other {noun} with your configuration."
        )

    if visit_count <= 1:
        lines.append(
            f"Welcome {client_ip} using {telnet_name}"
            f" and {terminal_name} - this is the first time we've met!"
        )
    else:
        lines.append(
            f"Welcome back {client_ip} using {telnet_name}"
            f" and {terminal_name} - this is visit #{visit_count}!"
        )

    if lines:
        return "\n".join(lines) + "\n"
    return ""


def _repl_prompt(term, has_unicode: bool = True) -> None:
    """Write the REPL prompt with hotkey legend and bracketed cursor."""
    import sys
    bm = term.bold_magenta
    legend = (
        f"{bm('l-')}logoff, {bm('1-')}terminal, "
        f"{bm('2-')}telnet, {bm('r-')}refresh, "
        f"{bm('u-')}update: "
        f"{_cursor_bracket(term, has_unicode)}"
    )
    sys.stdout.write(f"\r\x1b[J{term.normal}{legend}")
    sys.stdout.flush()


def _paginate(term, text: str, has_unicode: bool = True) -> None:
    """Display text with simple pagination."""
    import sys
    width = term.width or 80
    lines = []
    for raw in text.split("\n"):
        wrapped = textwrap.wrap(
            raw, width=width,
            break_long_words=True, break_on_hyphens=False,
        )
        lines.extend(wrapped if wrapped else [raw])
    page_size = max(1, (term.height or 25) - 1)
    nonstop = False

    for idx, line in enumerate(lines):
        sys.stdout.write(line + "\n")
        if (not nonstop
                and idx > 0
                and (idx + 1) % page_size == 0
                and idx + 1 < len(lines)):
            bm = term.bold_magenta
            prompt = (
                f"{bm('s-')}stop {bm('c-')}continue "
                f"{bm('n-')}nonstop: "
                f"{_cursor_bracket(term, has_unicode)}")
            sys.stdout.write(prompt)
            sys.stdout.flush()
            key = term.inkey(timeout=None)
            sys.stdout.write(
                f"{term.normal}\r{term.clear_eol}"
                f"{_cursor_style(_CURSOR_BLINK_UNDERLINE, has_unicode)}")
            sys.stdout.flush()
            if key == "s":
                return
            elif key == "n":
                nonstop = True
    sys.stdout.flush()


def _normalize_color_hex(hex_color: str) -> str:
    """Normalize X11 color hex to standard 6-digit format."""
    from blessed.colorspace import hex_to_rgb, rgb_to_hex
    r, g, b = hex_to_rgb(hex_color)
    return rgb_to_hex(r, g, b)


def _filter_terminal_detail(
    detail: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Filter terminal session data for display."""
    if not detail:
        return detail
    result = dict(detail)

    for key in ("session_arguments", "height", "width"):
        result.pop(key, None)

    aw = result.get("ambiguous_width")
    if aw is not None and aw != 2:
        del result["ambiguous_width"]

    if "text_sizing" in result:
        result["kitty_text_sizing"] = result.pop("text_sizing")

    for key in list(result):
        if key.startswith(("kitty_", "iterm2_")):
            val = result[key]
            if not val or (isinstance(val, dict) and not any(val.values())):
                del result[key]

    terminal_results = result.get("terminal_results")
    if terminal_results is not None:
        terminal_results = dict(terminal_results)
        if "text_sizing" in terminal_results:
            terminal_results["kitty_text_sizing"] = (
                terminal_results.pop("text_sizing"))
        for key in ("foreground_color_rgb", "background_color_rgb"):
            terminal_results.pop(key, None)
        for key in list(terminal_results):
            if key.startswith(("kitty_", "iterm2_")):
                val = terminal_results[key]
                if not val or (isinstance(val, dict)
                               and not any(val.values())):
                    del terminal_results[key]
        modes = terminal_results.pop("modes", None)
        if modes:
            dec_modes = {}
            for _num, mode in modes.items():
                if isinstance(mode, dict) and mode.get("supported"):
                    name = mode.get("mode_name", str(_num))
                    dec_modes[name] = {
                        "changeable": mode.get("changeable", False),
                        "enabled": mode.get("enabled", False),
                    }
            if dec_modes:
                terminal_results["dec_private_modes"] = dec_modes
        for key in ("foreground_color_hex", "background_color_hex"):
            if key in terminal_results:
                terminal_results[key] = _normalize_color_hex(
                    terminal_results[key]
                )
        result["terminal_results"] = terminal_results

    test_results = result.get("test_results")
    if test_results is not None:
        filtered = {}
        for k, v in test_results.items():
            if not v:
                continue
            if isinstance(v, dict):
                reduced = {}
                for ver, data in v.items():
                    if isinstance(data, dict):
                        reduced[ver] = {
                            sk: sv for sk, sv in data.items()
                            if sk in ("pct_success", "n_total")
                        }
                    else:
                        reduced[ver] = data
                if reduced:
                    filtered[k] = reduced
            else:
                filtered[k] = v
        if filtered:
            result["test_results"] = filtered
        else:
            del result["test_results"]
    return result


def _filter_telnet_detail(
    detail: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Filter telnet probe data for display."""
    if not detail:
        return detail
    import copy
    result = copy.deepcopy(detail)

    if session_data := result.get("session-data"):
        for key in ("probe", "option_states"):
            session_data.pop(key, None)

    if fp_data := result.get("fingerprint-data"):
        fp_data.pop("refused-options", None)

    return result


def _show_detail(term, data: Dict[str, Any], section: str) -> None:
    """Show detailed JSON for a fingerprint section with pagination."""
    import sys
    if section == "terminal":
        terminal_probe = data.get("terminal-probe", {})
        detail = _filter_terminal_detail(terminal_probe.get("session-data"))
        title = "Terminal Probe Results"
    else:
        detail = _filter_telnet_detail(data.get("telnet-probe"))
        title = "Telnet Probe Data"

    underline = term.cyan("=" * len(title))
    sys.stdout.write(
        term.normal
        + _cursor_style(_CURSOR_BLINK_UNDERLINE, _has_unicode(data))
        + term.clear)
    sys.stdout.flush()
    if detail:
        text = (f"{term.magenta(title)}\n"
                f"{underline}\n"
                f"\n"
                f"{json.dumps(detail, indent=2, sort_keys=True)}")
        _paginate(term, text, _has_unicode(data))
    else:
        sys.stdout.write(f"{term.magenta(title)}\n{underline}\n\n(no data)\n")
        sys.stdout.flush()


def _client_ip(data: Dict[str, Any]) -> str:
    """Extract client IP from fingerprint data."""
    sessions = data.get("sessions", [])
    if sessions:
        client = sessions[-1].get("client")
        if client and len(client) >= 1:
            return str(client[0])
    client = data.get("client")
    if client and len(client) >= 1:
        return str(client[0])
    return "unknown"


def _fingerprint_repl(
    term,
    data: Dict[str, Any],
    seen_counts: str = "",
    filepath: Optional[Path] = None,
    names: Optional[Dict[str, str]] = None,
) -> None:
    """Interactive REPL for exploring fingerprint data."""
    import sys
    ip = _client_ip(data)
    _commands = {
        "l": "logoff", "1": "terminal-detail",
        "2": "telnet-detail", "r": "refresh",
        "u": "update", "\x0c": "refresh",
    }

    while True:
        _repl_prompt(term, _has_unicode(data))
        with term.cbreak():
            key = term.inkey(timeout=None)

        key_str = key.name or str(key)
        if key_str in _commands:
            sys.stdout.write(str(key))
            sys.stdout.flush()
            logger.info("%s: repl %s", ip, _commands[key_str])
        elif key_str not in ("KEY_ENTER", "\r", "\n"):
            logger.info("%s: repl unknown key %r", ip, key_str)

        if key == "l" or key.name == "KEY_ESCAPE" or key == "":
            logger.info("%s: repl logoff", ip)
            sys.stdout.write(
                f"\n{term.normal}"
                f"{_cursor_style(_CURSOR_BLINK_UNDERLINE, _has_unicode(data))}")
            sys.stdout.flush()
            break
        elif key == "1":
            _show_detail(term, data, "terminal")
        elif key == "2":
            _show_detail(term, data, "telnet")
        elif key == "u" and filepath is not None:
            _names = names if names is not None else {}
            _prompt_fingerprint_identification(term, data, filepath, _names)
            names = _load_fingerprint_names()
            seen_counts = _build_seen_counts(data, names)
        elif key == "r" or key == "\x0c":
            sys.stdout.write(
                term.normal
                + _cursor_style(_CURSOR_BLINK_UNDERLINE, _has_unicode(data))
                + term.clear)
            sys.stdout.flush()
            _display_compact_summary(data, term)
            if seen_counts:
                sys.stdout.write(seen_counts)
                sys.stdout.flush()


def _prompt_fingerprint_identification(
    term, data: Dict[str, Any], filepath: Path, names: Dict[str, str]
) -> None:
    """Prompt user to identify unknown fingerprint hashes."""
    import sys
    telnet_probe = data.get("telnet-probe", {})
    telnet_hash = telnet_probe.get("fingerprint", "")
    terminal_probe = data.get("terminal-probe", {})
    terminal_hash = terminal_probe.get("fingerprint", _UNKNOWN_TERMINAL_HASH)

    telnet_known = telnet_hash in names
    terminal_known = terminal_hash in names or terminal_hash == _UNKNOWN_TERMINAL_HASH

    if telnet_known and terminal_known:
        return

    sys.stdout.write(
        f"\n{term.bold_magenta}Help our database!{term.normal}\n")
    sys.stdout.flush()

    suggestions: Dict[str, str] = data.get("suggestions", {})

    if not terminal_known:
        software_name = (terminal_probe.get("session-data", {})
                         .get("software_name"))
        if software_name:
            prompt = (f"Terminal emulator name"
                      f" (press return for \"{software_name}\"): ")
        else:
            prompt = f"Terminal emulator name for {terminal_hash}: "
        raw = _cooked_input(prompt)
        if not raw and software_name:
            raw = software_name
        validated = _validate_suggestion(raw)
        if validated:
            suggestions["terminal-emulator"] = validated

    if not telnet_known:
        raw = _cooked_input(f"Telnet client name for {telnet_hash}: ")
        validated = _validate_suggestion(raw)
        if validated:
            suggestions["telnet-client"] = validated

    if suggestions:
        data["suggestions"] = suggestions
        _atomic_json_write(filepath, data)


def _process_client_fingerprint(filepath: Path, data: Dict[str, Any]) -> None:
    """Process client fingerprint: run ucs-detect if available, update file."""
    terminal_data = _run_ucs_detect()

    if terminal_data:
        terminal_fp = _create_terminal_fingerprint(terminal_data)
        terminal_hash = _hash_terminal_fingerprint(terminal_fp)

        data["terminal-probe"] = {
            "fingerprint": terminal_hash,
            "fingerprint-data": terminal_fp,
            "session-data": terminal_data,
        }

        old_dir = filepath.parent
        dir_name = old_dir.name
        if dir_name.endswith(f"-{_UNKNOWN_TERMINAL_HASH}"):
            telnet_hash = dir_name[: -(len(_UNKNOWN_TERMINAL_HASH) + 1)]
            new_dir = old_dir.parent / f"{telnet_hash}-{terminal_hash}"
            if not new_dir.exists():
                try:
                    os.rename(str(old_dir), str(new_dir))
                    filepath = new_dir / filepath.name
                except OSError as exc:
                    logger.warning("failed to rename %s -> %s: %s",
                                   old_dir, new_dir, exc)

        _atomic_json_write(filepath, data)

    try:
        import blessed  # noqa: F401
    except ImportError:
        print(json.dumps(data, indent=2, sort_keys=True))
        return

    import sys
    term = _make_terminal()
    sys.stdout.write(
        _cursor_style(_CURSOR_BLINK_UNDERLINE, _has_unicode(data)))
    sys.stdout.flush()
    names = _load_fingerprint_names()
    seen_counts = _build_seen_counts(data, names)
    if not _display_compact_summary(data, term):
        print(json.dumps(data, indent=2, sort_keys=True))
    if seen_counts:
        sys.stdout.write(seen_counts)
        sys.stdout.flush()

    if term.is_a_tty:
        _prompt_fingerprint_identification(term, data, filepath, names)
        _fingerprint_repl(term, data, seen_counts, filepath, names)


def fingerprinting_post_script(filepath):
    """
    Post-fingerprint script that optionally runs ucs-detect for terminal probing.

    If ucs-detect is available in PATH, runs it to collect terminal capabilities
    and merges the results into the fingerprint data.

    Can be used as the TELNETLIB3_FINGERPRINT_POST_SCRIPT target::

        export TELNETLIB3_FINGERPRINT_POST_SCRIPT=telnetlib3.fingerprinting_display
        export TELNETLIB3_DATA_DIR=./data
        telnetlib3-server --shell telnetlib3.fingerprinting_server_shell

    :param filepath: Path to the saved fingerprint JSON file.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        logger.warning("Post-script file not found: %s", filepath)
        return

    with open(filepath) as f:
        data = json.load(f)

    telnet_probe = data.get("telnet-probe", {})
    probed_protocol = telnet_probe.get(
        "fingerprint-data", {}
    ).get("probed-protocol")

    if probed_protocol == "client":
        _process_client_fingerprint(filepath, data)
    else:
        logger.warning("Unknown probed-protocol: %s", probed_protocol)


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print(f"Usage: python -m {__name__} <filepath>", file=sys.stderr)
        sys.exit(1)
    fingerprinting_post_script(sys.argv[1])
