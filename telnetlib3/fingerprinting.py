"""
Fingerprint shell for telnet client identification.

This module probes telnet protocol capabilities, collects session data,
and saves fingerprint files.  Display, REPL, and post-script code live
in :mod:`telnetlib3.fingerprinting_display`.
"""

# std imports
import asyncio
import datetime
import glob as glob_mod
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .accessories import encoding_from_lang
from .telopt import (
    DO,
    DONT,
    BINARY,
    SGA,
    ECHO,
    STATUS,
    TTYPE,
    TSPEED,
    LFLOW,
    XDISPLOC,
    NAWS,
    NEW_ENVIRON,
    CHARSET,
    LINEMODE,
    SNDLOC,
    EOR,
    GMCP,
    COM_PORT_OPTION,
    AUTHENTICATION,
    ENCRYPT,
    TN3270E,
    XAUTH,
    RSP,
    SUPPRESS_LOCAL_ECHO,
    TLS,
    KERMIT,
    SEND_URL,
    FORWARD_X,
    PRAGMA_LOGON,
    SSPI_LOGON,
    PRAGMA_HEARTBEAT,
    X3PAD,
    VT3270REGIME,
    TTYLOC,
    SUPDUPOUTPUT,
    SUPDUP,
    DET,
    BM,
    RCP,
    NAMS,
    RCTE,
    NAOL,
    NAOP,
    NAOCRD,
    NAOHTS,
    NAOHTD,
    NAOFFD,
    NAOVTS,
    NAOVTD,
    NAOLFD,
)

# Data directory for saving fingerprint data - None when unset (no saves)
DATA_DIR: Optional[Path] = (
    Path(os.environ["TELNETLIB3_DATA_DIR"])
    if os.environ.get("TELNETLIB3_DATA_DIR")
    else None
)

# Maximum files per protocol-fingerprint folder
FINGERPRINT_MAX_FILES = int(os.environ.get("TELNETLIB3_FINGERPRINT_MAX_FILES", "200"))

# Maximum number of unique fingerprint folders
FINGERPRINT_MAX_FINGERPRINTS = int(
    os.environ.get("TELNETLIB3_FINGERPRINT_MAX_FINGERPRINTS", "1000")
)

# Post-fingerprint Python module to execute with saved file path
# Example: TELNETLIB3_FINGERPRINT_POST_SCRIPT=telnetlib3.fingerprinting_display
FINGERPRINT_POST_SCRIPT = os.environ.get("TELNETLIB3_FINGERPRINT_POST_SCRIPT", "")


# Terminal types that uniquely identify specific telnet clients
PROTOCOL_MATCHED_TERMINALS = {
    "syncterm",  # SyncTERM BBS client
}

# Terminal types associated with MUD clients, matched case-insensitively.
# These clients are likely to support extended options like GMCP.
MUD_TERMINALS = {
    "mudlet",
    "cmud",
    "zmud",
    "mushclient",
    "atlantis",
    "tintin++",
    "tt++",
    "blowtorch",
    "mudrammer",
    "kildclient",
    "portal",
    "beip",
    "savitar",
}

__all__ = (
    "fingerprinting_server_shell",
    "fingerprinting_post_script",
    "get_client_fingerprint",
    "probe_client_capabilities",
)

logger = logging.getLogger("telnetlib3.fingerprint")

CR, LF = "\r", "\n"
CRLF = CR + LF

# Telnet options to probe, grouped by category
# Each entry is (option_bytes, name, description)
CORE_OPTIONS = [
    (BINARY, "BINARY", "8-bit binary mode"),
    (SGA, "SGA", "Suppress Go Ahead"),
    (ECHO, "ECHO", "Echo mode"),
    (STATUS, "STATUS", "Option status reporting"),
    (TTYPE, "TTYPE", "Terminal type"),
    (TSPEED, "TSPEED", "Terminal speed"),
    (LFLOW, "LFLOW", "Local flow control"),
    (XDISPLOC, "XDISPLOC", "X display location"),
    (NAWS, "NAWS", "Window size"),
    (NEW_ENVIRON, "NEW_ENVIRON", "Environment variables"),
    (CHARSET, "CHARSET", "Character set"),
    (LINEMODE, "LINEMODE", "Line mode with SLC"),
    (EOR, "EOR", "End of Record"),
    # LOGOUT omitted - BSD client times out on this
    (SNDLOC, "SNDLOC", "Send location"),
]

MUD_OPTIONS = [
    (COM_PORT_OPTION, "COM_PORT", "Serial port control (RFC 2217)"),
]

# Options with non-standard byte values (> 140) that crash some clients.
# icy_term (icy_net) only accepts option bytes 0-49, 138-140, and 255,
# returning a hard error for anything else. GMCP-capable MUD clients
# typically self-announce via IAC WILL GMCP, so probing is unnecessary.
EXTENDED_OPTIONS = [
    (GMCP, "GMCP", "Generic MUD Communication Protocol"),
]

LEGACY_OPTIONS = [
    (AUTHENTICATION, "AUTHENTICATION", "Telnet authentication"),
    (ENCRYPT, "ENCRYPT", "Encryption option"),
    (TN3270E, "TN3270E", "3270 terminal emulation"),
    (XAUTH, "XAUTH", "X authentication"),
    (RSP, "RSP", "Remote serial port"),
    (SUPPRESS_LOCAL_ECHO, "SUPPRESS_LOCAL_ECHO", "Local echo suppression"),
    (TLS, "TLS", "TLS negotiation"),
    (KERMIT, "KERMIT", "Kermit file transfer"),
    (SEND_URL, "SEND_URL", "URL sending"),
    (FORWARD_X, "FORWARD_X", "X11 forwarding"),
    (PRAGMA_LOGON, "PRAGMA_LOGON", "Pragma logon"),
    (SSPI_LOGON, "SSPI_LOGON", "SSPI logon"),
    (PRAGMA_HEARTBEAT, "PRAGMA_HEARTBEAT", "Heartbeat"),
    (X3PAD, "X3PAD", "X.3 PAD"),
    (VT3270REGIME, "VT3270REGIME", "VT3270 regime"),
    (TTYLOC, "TTYLOC", "Terminal location"),
    (SUPDUP, "SUPDUP", "SUPDUP protocol"),
    (SUPDUPOUTPUT, "SUPDUPOUTPUT", "SUPDUP output"),
    (DET, "DET", "Data entry terminal"),
    (BM, "BM", "Byte macro"),
    (RCP, "RCP", "Reconnection"),
    (NAMS, "NAMS", "NAMS"),
    (RCTE, "RCTE", "Remote controlled transmit/echo"),
    (NAOL, "NAOL", "Output line width"),
    (NAOP, "NAOP", "Output page size"),
    (NAOCRD, "NAOCRD", "Output CR disposition"),
    (NAOHTS, "NAOHTS", "Output horiz tab stops"),
    (NAOHTD, "NAOHTD", "Output horiz tab disposition"),
    (NAOFFD, "NAOFFD", "Output formfeed disposition"),
    (NAOVTS, "NAOVTS", "Output vert tabstops"),
    (NAOVTD, "NAOVTD", "Output vert tab disposition"),
    (NAOLFD, "NAOLFD", "Output LF disposition"),
]

ALL_PROBE_OPTIONS = CORE_OPTIONS + MUD_OPTIONS + LEGACY_OPTIONS

# All known options including extended, for display/name lookup only
_ALL_KNOWN_OPTIONS = ALL_PROBE_OPTIONS + EXTENDED_OPTIONS

# Build mapping from hex string (e.g., "0x03") to option name (e.g., "SGA")
_OPT_BYTE_TO_NAME = {
    f"0x{opt[0]:02x}": name for opt, name, _ in _ALL_KNOWN_OPTIONS
}


def _display_fingerprint(session_fp: Dict[str, Any]) -> Dict[str, Any]:
    """Filter session fingerprint for display, removing timing and defaults."""
    result = {}

    if session_fp.get("extra"):
        extra = dict(session_fp["extra"])
        extra.pop("timeout", None)
        cols = extra.get("cols")
        rows = extra.get("rows")
        if cols == 80 and rows == 25:
            extra.pop("cols", None)
            extra.pop("rows", None)
        if extra:
            result["extra"] = extra

    if session_fp.get("ttype_cycle"):
        result["ttype_cycle"] = session_fp["ttype_cycle"]

    if session_fp.get("probe"):
        if supported := session_fp["probe"].get("WILL", {}):
            result["supported"] = sorted(supported.keys())

    return result


# ANSI escape for clearing to end of line
CLEAR_EOL = "\x1b[K"


def _update_status_line(writer, message: str) -> None:
    """Update the status line in place."""
    writer.write(f"\r{message}{CLEAR_EOL}")


async def probe_client_capabilities(
    writer,
    options: Optional[List[Tuple[bytes, str, str]]] = None,
    progress_callback: Optional[Callable[[str, int, int, str], None]] = None,
    timeout: float = 0.5,
) -> Dict[str, Dict[str, Any]]:
    """
    Actively probe client for telnet capability support.

    Sends IAC DO for ALL options at once, waits for responses, then collects results.

    :param writer: TelnetWriter instance.
    :param options: List of (opt_bytes, name, description) tuples to probe.
                   Defaults to ALL_PROBE_OPTIONS.
    :param progress_callback: Optional callback(name, idx, total, status) called
                             during result collection.
    :param timeout: Timeout in seconds to wait for all responses.
    :returns: Dict mapping option name to {"status": "WILL"|"WONT"|"timeout",
              "opt": bytes, "description": str}.
    """
    if options is None:
        options = ALL_PROBE_OPTIONS

    results = {}
    to_probe = []

    for opt, name, description in options:
        if writer.remote_option.enabled(opt):
            results[name] = {
                "status": "WILL",
                "opt": opt,
                "description": description,
                "already_negotiated": True,
            }
        elif writer.remote_option.get(opt) is False:
            results[name] = {
                "status": "WONT",
                "opt": opt,
                "description": description,
                "already_negotiated": True,
            }
        else:
            to_probe.append((opt, name, description))

    for opt, name, description in to_probe:
        writer.iac(DO, opt)

    await writer.drain()

    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        all_responded = all(
            writer.remote_option.get(opt) is not None
            for opt, name, desc in to_probe
            if name not in results
        )
        if all_responded:
            break
        await asyncio.sleep(0.05)

    for idx, (opt, name, description) in enumerate(to_probe, 1):
        if name in results:
            continue

        if progress_callback:
            progress_callback(name, idx, len(to_probe), "")

        if writer.remote_option.enabled(opt):
            results[name] = {
                "status": "WILL",
                "opt": opt,
                "description": description,
            }
        elif writer.remote_option.get(opt) is False:
            results[name] = {
                "status": "WONT",
                "opt": opt,
                "description": description,
            }
        else:
            results[name] = {
                "status": "timeout",
                "opt": opt,
                "description": description,
            }

    return results


def format_probe_results(
    results: Dict[str, Dict[str, Any]],
    probe_time: Optional[float] = None,
) -> str:
    """
    Format probe results for display.

    Only shows supported (WILL) options as a comma-separated list.
    Refused and timeout options are still stored in results but not displayed.

    :param results: Dict from probe_client_capabilities().
    :param probe_time: Time taken to probe, in seconds.
    :returns: Formatted multi-line string.
    """
    lines = []

    supported_names = []
    for opt, name, description in ALL_PROBE_OPTIONS:
        if name not in results:
            continue
        if results[name]["status"] == "WILL":
            supported_names.append(name)

    if supported_names:
        lines.append(f"Telnet protocols: {', '.join(supported_names)}")

    supported = len(supported_names)
    refused = sum(1 for r in results.values() if r["status"] == "WONT")
    no_response = sum(1 for r in results.values() if r["status"] == "timeout")

    summary_parts = [f"{supported} supported", f"{refused} refused"]
    if no_response > 0:
        summary_parts.append(f"{no_response} no-response")
    if probe_time is not None:
        summary_parts.append(f"{probe_time:.2f}s")

    lines.append(f"Telnet probe summary: {', '.join(summary_parts)}")

    return CRLF.join(lines)


# Keys to collect from extra_info, grouped by category
_TERMINAL_KEYS = ("TERM", "term", "cols", "rows", "COLUMNS", "LINES")
_ENCODING_KEYS = ("charset", "LANG", "COLORTERM")
_NETWORK_KEYS = ("peername", "sockname", "tspeed", "xdisploc", "DISPLAY")
_TTYPE_KEYS = tuple(f"ttype{n}" for n in range(1, 9))
_PROTOCOL_KEYS = ("encoding",)


def get_client_fingerprint(writer) -> Dict[str, Any]:
    """
    Collect all available client information from writer.

    :param writer: TelnetWriter instance.
    :returns: Dictionary of all negotiated client attributes.
    """
    fingerprint = {}

    all_keys = (
        _TERMINAL_KEYS + _ENCODING_KEYS + _NETWORK_KEYS + _TTYPE_KEYS + _PROTOCOL_KEYS
    )

    for key in all_keys:
        value = writer.get_extra_info(key)
        if value is not None and value != "":
            fingerprint[key] = value

    for env_key in ("USER", "SHELL", "HOME", "PATH", "LOGNAME", "MAIL"):
        value = writer.get_extra_info(env_key)
        if value is not None and value != "":
            fingerprint[env_key] = value

    return fingerprint


def describe_client(writer) -> str:
    """
    Generate a formatted description of the connected client.

    :param writer: TelnetWriter instance.
    :returns: Formatted multi-line string with sorted attributes.
    """
    fingerprint = get_client_fingerprint(writer)
    attrs = []

    peername = fingerprint.get("peername")
    if peername:
        attrs.append(f"Client: {peername[0]}:{peername[1]}")
    else:
        attrs.append("Client: unknown")

    term = fingerprint.get("TERM") or fingerprint.get("term")
    if term:
        attrs.append(f"TERM: {term}")

        ttype_values = []
        for key in _TTYPE_KEYS:
            if key in fingerprint:
                ttype_values.append(fingerprint[key])
        term_lower = term.lower()
        other_types = list(dict.fromkeys(
            v for v in ttype_values if v.lower() != term_lower
        ))
        if other_types:
            attrs.append(f"TTYPE: {', '.join(other_types)}")

    cols = fingerprint.get("cols") or fingerprint.get("COLUMNS")
    rows = fingerprint.get("rows") or fingerprint.get("LINES")
    if cols and rows:
        attrs.append(f"Size: {cols}x{rows}")

    if "tspeed" in fingerprint:
        attrs.append(f"Speed: {fingerprint['tspeed']}")

    if "charset" in fingerprint:
        attrs.append(f"CHARSET: {fingerprint['charset']}")
    if "LANG" in fingerprint:
        attrs.append(f"LANG: {fingerprint['LANG']}")
    if "encoding" in fingerprint:
        attrs.append(f"Encoding: {fingerprint['encoding']}")
    if "COLORTERM" in fingerprint:
        attrs.append(f"COLORTERM: {fingerprint['COLORTERM']}")

    xdisploc = fingerprint.get("xdisploc") or fingerprint.get("DISPLAY")
    if xdisploc:
        attrs.append(f"DISPLAY: {xdisploc}")

    for key in ("USER", "LOGNAME", "SHELL", "HOME", "MAIL", "PATH"):
        if key in fingerprint:
            attrs.append(f"{key}: {fingerprint[key]}")

    if len(attrs) <= 1:
        attrs.append("(no negotiated attributes)")

    attrs.sort()

    return CRLF.join(attrs)


async def _run_probe(
    writer, verbose: bool = True
) -> Tuple[Dict[str, Dict[str, Any]], float]:
    """Run active probe, optionally extending to MUD options."""
    total = len(ALL_PROBE_OPTIONS)
    if verbose:
        writer.write(f"\rProbing {total} telnet options...\x1b[J")
        await writer.drain()

    start_time = time.time()
    results = await probe_client_capabilities(writer, timeout=0.5)

    if _is_maybe_mud(writer) and EXTENDED_OPTIONS:
        ext_results = await probe_client_capabilities(
            writer, options=EXTENDED_OPTIONS, timeout=0.5)
        results.update(ext_results)

    elapsed = time.time() - start_time

    if verbose:
        _update_status_line(writer, "")

    return results, elapsed


def _opt_byte_to_name(opt: bytes) -> str:
    """Convert option bytes to name or hex string."""
    if isinstance(opt, bytes) and len(opt) > 0:
        hex_key = f"0x{opt[0]:02x}"
        return _OPT_BYTE_TO_NAME.get(hex_key, hex_key)
    return str(opt)


def _collect_option_states(writer) -> Dict[str, Dict[str, Any]]:
    """Collect all telnet option states from writer."""
    options = {}

    remote = {}
    for opt, enabled in writer.remote_option.items():
        remote[_opt_byte_to_name(opt)] = enabled
    if remote:
        options["remote"] = remote

    local = {}
    for opt, enabled in writer.local_option.items():
        local[_opt_byte_to_name(opt)] = enabled
    if local:
        options["local"] = local

    return options


def _collect_extra_info(writer) -> Dict[str, Any]:
    """Collect all extra_info from writer, including private _extra dict."""
    extra = {}

    protocol = getattr(writer, "_protocol", None) or getattr(writer, "protocol", None)
    if protocol and hasattr(protocol, "_extra"):
        for key, value in protocol._extra.items():
            if isinstance(value, tuple):
                extra[key] = list(value)
            elif isinstance(value, bytes):
                extra[key] = value.hex()
            else:
                extra[key] = value

    # Transport-level keys not in protocol._extra
    for key in ("peername", "sockname", "timeout"):
        if key not in extra:
            if (value := writer.get_extra_info(key)) is not None:
                extra[key] = list(value) if isinstance(value, tuple) else value

    # Clean up: prefer uppercase over lowercase redundant keys
    if "TERM" in extra and "term" in extra:
        del extra["term"]
    if "COLUMNS" in extra and "cols" in extra:
        del extra["cols"]
    if "LINES" in extra and "rows" in extra:
        del extra["rows"]

    # Remove ttype1, ttype2, etc. - collected separately in ttype_cycle
    for i in range(1, 20):
        extra.pop(f"ttype{i}", None)

    return extra


def _collect_ttype_cycle(writer) -> List[str]:
    """Collect the full TTYPE cycle responses."""
    ttype_list = []

    protocol = getattr(writer, "_protocol", None) or getattr(writer, "protocol", None)
    extra_dict = getattr(protocol, "_extra", {}) if protocol else {}

    for i in range(1, 20):
        if value := (extra_dict.get(f"ttype{i}") or writer.get_extra_info(f"ttype{i}")):
            ttype_list.append(value)
        else:
            break
    return ttype_list


def _collect_protocol_timing(writer) -> Dict[str, Any]:
    """Collect timing information from protocol."""
    timing = {}
    protocol = getattr(writer, "_protocol", None) or getattr(writer, "protocol", None)
    if protocol:
        if hasattr(protocol, "duration"):
            timing["duration"] = protocol.duration
        if hasattr(protocol, "idle"):
            timing["idle"] = protocol.idle
        if hasattr(protocol, "_connect_time"):
            timing["connect_time"] = protocol._connect_time
    return timing


def _collect_slc_tab(writer) -> Dict[str, Any]:
    """Collect non-default SLC entries when LINEMODE was negotiated."""
    from . import slc

    slctab = getattr(writer, "slctab", None)
    if not slctab:
        return {}

    if not (hasattr(writer, "remote_option")
            and writer.remote_option.enabled(LINEMODE)):
        return {}

    defaults = slc.generate_slctab(slc.BSD_SLC_TAB)

    result = {}
    slc_set = {}
    slc_unset = []
    slc_nosupport = []

    for slc_func, slc_def in slctab.items():
        default_def = defaults.get(slc_func)
        if (default_def is not None
                and slc_def.mask == default_def.mask
                and slc_def.val == default_def.val):
            continue

        name = slc.name_slc_command(slc_func)
        if slc_def.nosupport:
            slc_nosupport.append(name)
        elif slc_def.val == slc.theNULL:
            slc_unset.append(name)
        else:
            slc_set[name] = (slc_def.val[0]
                             if isinstance(slc_def.val, bytes)
                             else slc_def.val)

    if slc_set:
        result["set"] = slc_set
    if slc_unset:
        result["unset"] = sorted(slc_unset)
    if slc_nosupport:
        result["nosupport"] = sorted(slc_nosupport)

    return result


def _create_protocol_fingerprint(
    writer,
    probe_results: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Create anonymized/summarized protocol fingerprint from session data.

    Fields are only included if negotiated. Environment variables are summarized
    as "True" (non-empty value) or "None" (empty string).

    :param writer: TelnetWriter instance.
    :param probe_results: Probe results from capability probing.
    :returns: Dict with anonymized protocol fingerprint data.
    """
    fingerprint: Dict[str, Any] = {
        "probed-protocol": "client",
    }

    protocol = getattr(writer, "_protocol", None) or getattr(writer, "protocol", None)
    extra_dict = getattr(protocol, "_extra", {}) if protocol else {}

    for key in ("HOME", "USER", "SHELL"):
        if key in extra_dict:
            fingerprint[key] = "True" if extra_dict[key] else "None"

    # Terminal size categorization (inlined)
    cols = writer.get_extra_info("cols")
    rows = writer.get_extra_info("rows")
    if cols is None or rows is None:
        fingerprint["terminal-size"] = "None"
    elif (cols, rows) == (80, 25):
        fingerprint["terminal-size"] = "Yes-80x25"
    elif (cols, rows) == (80, 24):
        fingerprint["terminal-size"] = "Yes-80x24"
    else:
        fingerprint["terminal-size"] = "Yes-Other"

    # Encoding extracted from LANG
    if lang := writer.get_extra_info("LANG"):
        encoding = encoding_from_lang(lang)
        fingerprint["encoding"] = encoding if encoding else "None"
    else:
        fingerprint["encoding"] = "None"

    # TERM categorization (inlined)
    term = writer.get_extra_info("TERM") or writer.get_extra_info("term")
    if not term:
        fingerprint["TERM"] = "None"
    elif (term_lower := term.lower()) in PROTOCOL_MATCHED_TERMINALS:
        fingerprint["TERM"] = term_lower.capitalize()
    elif "ansi" in term_lower:
        fingerprint["TERM"] = "Yes-ansi"
    else:
        fingerprint["TERM"] = "Yes"

    charset = writer.get_extra_info("charset")
    fingerprint["charset"] = charset if charset else "None"

    ttype_cycle = _collect_ttype_cycle(writer)
    fingerprint["ttype-count"] = len(ttype_cycle)

    supported = sorted([
        name for name, info in probe_results.items()
        if info["status"] == "WILL"
    ])
    refused = sorted([
        name for name, info in probe_results.items()
        if info["status"] == "WONT"
    ])
    fingerprint["supported-options"] = supported
    fingerprint["refused-options"] = refused

    return fingerprint


def _hash_protocol_fingerprint(protocol_fingerprint: Dict[str, Any]) -> str:
    """Create deterministic SHA256 hash of protocol fingerprint."""
    canonical = json.dumps(protocol_fingerprint, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _count_protocol_folder_files(protocol_dir: Path) -> int:
    """Count JSON files in protocol fingerprint directory."""
    if not protocol_dir.exists():
        return 0
    return sum(1 for f in protocol_dir.iterdir() if f.suffix == ".json")


def _count_fingerprint_folders(data_dir: Optional[Path] = None) -> int:
    """Count fingerprint folders in DATA_DIR."""
    _dir = data_dir if data_dir is not None else DATA_DIR
    if _dir is None or not _dir.exists():
        return 0
    return sum(1 for f in _dir.iterdir() if f.is_dir())


_UNKNOWN_TERMINAL_HASH = "0" * 16
AMBIGUOUS_WIDTH_UNKNOWN = -1


def _create_session_fingerprint(writer) -> Dict[str, Any]:
    """Create session identity fingerprint from stable client fields."""
    identity: Dict[str, Any] = {}

    if peername := writer.get_extra_info("peername"):
        identity["client-ip"] = peername[0]

    if term := (writer.get_extra_info("TERM") or writer.get_extra_info("term")):
        identity["TERM"] = term

    for key in ("USER", "HOME", "SHELL", "LANG", "charset"):
        if (value := writer.get_extra_info(key)) is not None and value != "":
            identity[key] = value

    return identity


def _hash_session_fingerprint(session_identity: Dict[str, Any]) -> str:
    """Create deterministic SHA256 hash of session identity."""
    canonical = json.dumps(session_identity, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _load_fingerprint_names(data_dir: Optional[Path] = None) -> Dict[str, str]:
    """Load fingerprint hash-to-name mapping from ``fingerprint_names.json``."""
    _dir = data_dir if data_dir is not None else DATA_DIR
    if _dir is None:
        return {}
    names_file = _dir / "fingerprint_names.json"
    if not names_file.exists():
        return {}
    with open(names_file) as f:
        return json.load(f)


def _save_fingerprint_names(
    names: Dict[str, str], data_dir: Optional[Path] = None
) -> None:
    """Write fingerprint hash-to-name mapping to ``fingerprint_names.json``."""
    _dir = data_dir if data_dir is not None else DATA_DIR
    if _dir is None:
        return
    _atomic_json_write(_dir / "fingerprint_names.json", names)


def _resolve_hash_name(hash_val: str, names: Dict[str, str]) -> str:
    """Return human-readable name for a hash, falling back to the hash itself."""
    return names.get(hash_val, hash_val)


def _validate_suggestion(text: str) -> Optional[str]:
    """Validate a user-submitted fingerprint name suggestion."""
    cleaned = text.strip()
    if not cleaned:
        return None
    for c in cleaned:
        if ord(c) < 32 or ord(c) == 127:
            return None
    return cleaned


def _cooked_input(prompt: str) -> str:
    """Call :func:`input` with echo and canonical mode temporarily enabled."""
    import sys
    import termios
    fd = sys.stdin.fileno()
    old_attrs = termios.tcgetattr(fd)
    new_attrs = list(old_attrs)
    new_attrs[3] |= (termios.ECHO | termios.ICANON)
    termios.tcsetattr(fd, termios.TCSANOW, new_attrs)
    try:
        return input(prompt)
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, old_attrs)


def _atomic_json_write(filepath: Path, data: dict) -> None:
    """Atomically write JSON data to file via write-to-new + rename."""
    tmp_path = filepath.with_suffix(".json.new")
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.rename(str(tmp_path), str(filepath))


def _count_matches(
    hash_val: str,
    position: str = "protocol",
    prefix: str = "client",
    data_dir: Optional[Path] = None,
) -> Dict[str, int]:
    """Count directories and session files matching a fingerprint hash."""
    _dir = data_dir if data_dir is not None else DATA_DIR
    if _dir is None or not _dir.exists():
        return {"dirs": 0, "sessions": 0}
    if position == "protocol":
        pattern = f"{prefix}-{hash_val}-*"
    else:
        pattern = f"{prefix}-*-{hash_val}"
    dirs = glob_mod.glob(str(_dir / pattern) + os.sep)
    sessions = glob_mod.glob(str(_dir / pattern / "*.json"))
    return {"dirs": len(dirs), "sessions": len(sessions)}


def _count_protocol_matches(
    telnet_hash: str,
    prefix: str = "client",
    data_dir: Optional[Path] = None,
) -> Dict[str, int]:
    """Count directories and session files matching a telnet protocol hash."""
    return _count_matches(telnet_hash, "protocol", prefix, data_dir)


def _count_terminal_matches(
    terminal_hash: str,
    prefix: str = "client",
    data_dir: Optional[Path] = None,
) -> Dict[str, int]:
    """Count directories and session files matching a terminal fingerprint hash."""
    return _count_matches(terminal_hash, "terminal", prefix, data_dir)


def _build_session_fingerprint(
    writer,
    probe_results: Dict[str, Dict[str, Any]],
    probe_time: float,
) -> Dict[str, Any]:
    """Build the session fingerprint dict (raw detailed data)."""
    extra = _collect_extra_info(writer)
    extra.pop("peername", None)
    extra.pop("sockname", None)

    ttype_cycle = _collect_ttype_cycle(writer)
    option_states = _collect_option_states(writer)
    timing = _collect_protocol_timing(writer)

    linemode_probed = probe_results.get("LINEMODE", {}).get("status")
    slc_tab = _collect_slc_tab(writer) if linemode_probed == "WILL" else {}

    probe_by_status: Dict[str, Dict[str, int]] = {}
    for name, info in probe_results.items():
        status = info["status"]
        opt_byte = info["opt"][0] if isinstance(info["opt"], bytes) else info["opt"]
        if status not in probe_by_status:
            probe_by_status[status] = {}
        probe_by_status[status][name] = opt_byte

    timing["probe"] = probe_time

    result = {
        "extra": extra,
        "ttype_cycle": ttype_cycle,
        "option_states": option_states,
        "probe": probe_by_status,
        "timing": timing,
    }
    if slc_tab:
        result["slc_tab"] = slc_tab
    return result


def _save_fingerprint_data(
    writer,
    probe_results: Dict[str, Dict[str, Any]],
    probe_time: float,
    session_fp: Optional[Dict[str, Any]] = None,
) -> Optional[Path]:
    """
    Save comprehensive fingerprint data to a JSON file.

    Creates directory structure: DATA_DIR/<protocol-hash>/uuid4.json
    Respects FINGERPRINT_MAX_FILES and FINGERPRINT_MAX_FINGERPRINTS limits.

    :param writer: TelnetWriter instance with full protocol access.
    :param probe_results: Probe results from capability probing.
    :param probe_time: Time taken for probing.
    :param session_fp: Pre-built session fingerprint, or None to build it.
    :returns: Path to saved file, or None if save skipped/failed.
    """
    if DATA_DIR is None:
        return None

    if session_fp is None:
        session_fp = _build_session_fingerprint(writer, probe_results, probe_time)

    display_fp = _display_fingerprint(session_fp)

    protocol_fp = _create_protocol_fingerprint(writer, probe_results)
    telnet_hash = _hash_protocol_fingerprint(protocol_fp)

    session_identity = _create_session_fingerprint(writer)
    session_hash = _hash_session_fingerprint(session_identity)

    folder_name = f"client-{telnet_hash}-{_UNKNOWN_TERMINAL_HASH}"
    probe_dir = DATA_DIR / folder_name
    is_new_dir = not probe_dir.exists()

    if is_new_dir:
        if _count_fingerprint_folders() >= FINGERPRINT_MAX_FINGERPRINTS:
            logger.warning(
                "max fingerprints (%d) exceeded, not saving %s",
                FINGERPRINT_MAX_FINGERPRINTS,
                telnet_hash,
            )
            return None
        try:
            probe_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("failed to create directory %s: %s", probe_dir, exc)
            return None
        logger.info("new fingerprint %s: %r", telnet_hash, display_fp)
    else:
        file_count = _count_protocol_folder_files(probe_dir)
        if file_count >= FINGERPRINT_MAX_FILES:
            logger.warning(
                "fingerprint %s at file limit (%d), not saving",
                telnet_hash,
                FINGERPRINT_MAX_FILES,
            )
            return None
        logger.info(
            "connection for fingerprint %s: %r",
            telnet_hash,
            display_fp.get("extra", {}),
        )

    filepath = probe_dir / f"{session_hash}.json"

    peername = writer.get_extra_info("peername")
    now = datetime.datetime.now(datetime.timezone.utc)
    session_entry = {
        "connected": now.isoformat(),
        "client": list(peername) if peername else None,
        "duration": session_fp.get("timing", {}).get("duration"),
    }

    if filepath.exists():
        try:
            with open(filepath) as f:
                data = json.load(f)
            data["telnet-probe"]["session-data"] = session_fp
            data["sessions"].append(session_entry)
        except (OSError, json.JSONDecodeError, KeyError) as exc:
            logger.warning("failed to read existing %s: %s", filepath, exc)
            data = None

        if data is not None:
            try:
                _atomic_json_write(filepath, data)
                return filepath
            except OSError as exc:
                logger.warning("failed to update fingerprint: %s", exc)
                return None

    data = {
        "telnet-probe": {
            "fingerprint": telnet_hash,
            "fingerprint-data": protocol_fp,
            "session-data": session_fp,
        },
        "sessions": [session_entry],
    }

    try:
        _atomic_json_write(filepath, data)
        return filepath
    except OSError as exc:
        logger.warning("failed to save fingerprint: %s", exc)
        return None


def _is_maybe_mud(writer) -> bool:
    """Return whether the client looks like a MUD client."""
    term = (writer.get_extra_info("TERM") or "").lower()
    if term in MUD_TERMINALS:
        return True
    for key in ("ttype1", "ttype2", "ttype3"):
        if (writer.get_extra_info(key) or "").lower() in MUD_TERMINALS:
            return True
    return False


async def fingerprinting_server_shell(reader, writer):
    """
    Shell that probes client telnet capabilities and runs post-script.

    Immediately probes all telnet options on connect. If DATA_DIR is configured,
    saves fingerprint data and runs the post-script through a PTY so it can
    probe the client's terminal with ucs-detect.

    :param reader: TelnetReader instance.
    :param writer: TelnetWriter instance.
    """
    import sys
    from .server_pty_shell import pty_shell

    probe_results, probe_time = await _run_probe(writer, verbose=False)

    # Disable LINEMODE if it was negotiated - stay in kludge mode (SGA+ECHO)
    # for PTY shell. LINEMODE causes echo loops with GNU telnet when running
    # ucs-detect (client's LIT_ECHO + PTY echo = feedback loop).
    if probe_results.get("LINEMODE", {}).get("status") == "WILL":
        writer.iac(DONT, LINEMODE)
        await writer.drain()
        await asyncio.sleep(0.1)

    # Switch syncterm to Topaz (Amiga) font
    term_type = (writer.get_extra_info("TERM") or "").lower()
    if term_type == "syncterm":
        writer.write("\x1b[0;40 D")
        await writer.drain()

    session_fp = _build_session_fingerprint(writer, probe_results, probe_time)
    filepath = _save_fingerprint_data(writer, probe_results, probe_time, session_fp)

    if filepath is not None:
        post_script = FINGERPRINT_POST_SCRIPT or "telnetlib3.fingerprinting_display"
        await pty_shell(reader, writer, sys.executable,
                        ["-W", "ignore::RuntimeWarning:runpy",
                         "-m", post_script, str(filepath)],
                        raw_mode=True)
    else:
        writer.close()


def fingerprinting_post_script(filepath):
    """
    Post-fingerprint script that optionally runs ucs-detect for terminal probing.

    If ucs-detect is available in PATH, runs it to collect terminal capabilities
    and merges the results into the fingerprint data.

    Can be used as the TELNETLIB3_FINGERPRINT_POST_SCRIPT target::

        TELNETLIB3_FINGERPRINT_POST_SCRIPT=telnetlib3.fingerprinting
        TELNETLIB3_DATA_DIR=./data
        telnetlib3-server --shell fingerprinting_server_shell

    :param filepath: Path to the saved fingerprint JSON file.
    """
    from .fingerprinting_display import fingerprinting_post_script as _fps
    _fps(filepath)


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print(f"Usage: python -m {__name__} <filepath>", file=sys.stderr)
        sys.exit(1)
    fingerprinting_post_script(sys.argv[1])
