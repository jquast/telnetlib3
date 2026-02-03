"""
Fingerprint shell for telnet client identification.

This shell displays all negotiated telnet options and client environment
information, useful for identifying client capabilities and debugging
telnet negotiation.

The shell supports active probing of ALL telnet protocol capabilities,
similar to how ucs-detect probes terminal capabilities.
"""

# std imports
import asyncio
import hashlib
import json
import logging
import os
import pprint
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .accessories import encoding_from_lang
from .telopt import (
    DO,
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
# Example: TELNETLIB3_FINGERPRINT_POST_SCRIPT=telnetlib3.fingerprinting:fingerprinting_post_script
FINGERPRINT_POST_SCRIPT = os.environ.get("TELNETLIB3_FINGERPRINT_POST_SCRIPT", "")

# Disable display output to client (still probes and saves)
DISPLAY_OUTPUT = os.environ.get("TELNETLIB3_FINGERPRINT_DISPLAY", "1") == "1"

# Terminal types that uniquely identify specific telnet clients
PROTOCOL_MATCHED_TERMINALS = {
    "syncterm",  # SyncTERM BBS client
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
    (GMCP, "GMCP", "Generic MUD Communication Protocol"),
    (COM_PORT_OPTION, "COM_PORT", "Serial port control (RFC 2217)"),
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

# Build mapping from hex string (e.g., "0x03") to option name (e.g., "SGA")
_OPT_BYTE_TO_NAME = {
    f"0x{opt[0]:02x}": name for opt, name, _ in ALL_PROBE_OPTIONS
}


def _display_fingerprint(session_fp: Dict[str, Any]) -> Dict[str, Any]:
    """
    Filter session fingerprint for display.

    Only shows enabled/supported values and removes timing information.

    :param session_fp: Raw session fingerprint dict.
    :returns: Filtered dict suitable for display.
    """
    result = {}

    # Keep extra info as-is
    if session_fp.get("extra"):
        result["extra"] = session_fp["extra"]

    # Keep ttype_cycle as-is
    if session_fp.get("ttype_cycle"):
        result["ttype_cycle"] = session_fp["ttype_cycle"]

    # Filter probe to only WILL (supported), show as list of names
    if session_fp.get("probe"):
        supported = session_fp["probe"].get("WILL", {})
        if supported:
            result["supported"] = sorted(supported.keys())

    # option_states and timing intentionally omitted

    return result


# ANSI escape for clearing to end of line
CLEAR_EOL = "\x1b[K"


def _update_status_line(writer, message: str) -> None:
    """
    Update the status line in place.

    :param writer: TelnetWriter instance.
    :param message: Message to display on the status line.
    """
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

    # First pass: collect already-negotiated options, queue others for probing
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

    # Send all DO requests at once
    for opt, name, description in to_probe:
        try:
            writer.iac(DO, opt)
        except Exception as exc:
            logger.debug("probe %s failed to send: %s", name, exc)
            results[name] = {
                "status": "error",
                "opt": opt,
                "description": description,
                "error": str(exc),
            }

    # Flush all requests
    await writer.drain()

    # Wait for responses
    await asyncio.sleep(timeout)

    # Collect results
    for idx, (opt, name, description) in enumerate(to_probe, 1):
        if name in results:  # Already handled (error case)
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

    # Collect supported option names in probe order
    supported_names = []
    for opt, name, description in ALL_PROBE_OPTIONS:
        if name not in results:
            continue
        info = results[name]
        if info["status"] == "WILL":
            supported_names.append(name)

    # Display as comma-separated list
    if supported_names:
        lines.append(f"Telnet protocols: {', '.join(supported_names)}")

    # Summary line
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

    # Collect all known keys
    all_keys = (
        _TERMINAL_KEYS + _ENCODING_KEYS + _NETWORK_KEYS + _TTYPE_KEYS + _PROTOCOL_KEYS
    )

    for key in all_keys:
        value = writer.get_extra_info(key)
        if value is not None and value != "":
            fingerprint[key] = value

    # Also try to get any uppercase environment variables that may have been
    # negotiated via NEW_ENVIRON
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

    # Client address
    peername = fingerprint.get("peername")
    if peername:
        attrs.append(f"Client: {peername[0]}:{peername[1]}")
    else:
        attrs.append("Client: unknown")

    # Terminal info - TERM is the selected/final value
    term = fingerprint.get("TERM") or fingerprint.get("term")
    if term:
        attrs.append(f"TERM: {term}")

        # Show other unique TTYPE values from cycle (excluding the selected TERM)
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

    # Window size
    cols = fingerprint.get("cols") or fingerprint.get("COLUMNS")
    rows = fingerprint.get("rows") or fingerprint.get("LINES")
    if cols and rows:
        attrs.append(f"Size: {cols}x{rows}")

    # Speed
    if "tspeed" in fingerprint:
        attrs.append(f"Speed: {fingerprint['tspeed']}")

    # Encoding
    if "charset" in fingerprint:
        attrs.append(f"CHARSET: {fingerprint['charset']}")
    if "LANG" in fingerprint:
        attrs.append(f"LANG: {fingerprint['LANG']}")
    if "encoding" in fingerprint:
        attrs.append(f"Encoding: {fingerprint['encoding']}")
    if "COLORTERM" in fingerprint:
        attrs.append(f"COLORTERM: {fingerprint['COLORTERM']}")

    # Display
    xdisploc = fingerprint.get("xdisploc") or fingerprint.get("DISPLAY")
    if xdisploc:
        attrs.append(f"DISPLAY: {xdisploc}")

    # Environment variables
    for key in ("USER", "LOGNAME", "SHELL", "HOME", "MAIL", "PATH"):
        if key in fingerprint:
            attrs.append(f"{key}: {fingerprint[key]}")

    if len(attrs) <= 1:
        attrs.append("(no negotiated attributes)")

    # Sort alphabetically
    attrs.sort()

    return CRLF.join(attrs)


async def _run_probe(writer, verbose: bool = True) -> Tuple[Dict[str, Dict[str, Any]], float]:
    """
    Run active probe - sends all requests at once, waits, collects results.

    :param writer: TelnetWriter instance.
    :param verbose: Whether to show progress message.
    :returns: Tuple of (probe results dict, elapsed time in seconds).
    """
    total = len(ALL_PROBE_OPTIONS)
    if verbose:
        # use "clear_eos" sequence to continuously rewrite this line
        writer.write(f"\rProbing {total} telnet options...\x1b[J")
        await writer.drain()

    start_time = time.time()
    results = await probe_client_capabilities(writer, timeout=0.5)
    elapsed = time.time() - start_time

    if verbose:
        _update_status_line(writer, "")

    return results, elapsed


def _opt_byte_to_hex(opt: bytes) -> str:
    """Convert option bytes to hex string."""
    if isinstance(opt, bytes) and len(opt) > 0:
        return f"0x{opt[0]:02x}"
    return str(opt)


def _collect_option_states(writer) -> Dict[str, Dict[str, Any]]:
    """
    Collect all telnet option states from writer.

    :param writer: TelnetWriter instance.
    :returns: Dict with remote_option, local_option, pending_option states.
    """
    options = {}

    # Collect remote option states (what the client supports)
    remote = {}
    for opt, enabled in writer.remote_option.items():
        opt_hex = _opt_byte_to_hex(opt)
        remote[opt_hex] = enabled
    options["remote_option"] = remote

    # Collect local option states (what the server supports)
    local = {}
    for opt, enabled in writer.local_option.items():
        opt_hex = _opt_byte_to_hex(opt)
        local[opt_hex] = enabled
    options["local_option"] = local

    # Collect pending option states
    pending = {}
    for opt, is_pending in writer.pending_option.items():
        if isinstance(opt, bytes):
            opt_hex = _opt_byte_to_hex(opt)
        else:
            opt_hex = str(opt)
        pending[opt_hex] = is_pending
    options["pending_option"] = pending

    return options


def _collect_extra_info(writer) -> Dict[str, Any]:
    """
    Collect all extra_info from writer, including private _extra dict.

    :param writer: TelnetWriter instance.
    :returns: Dict of all extra info values.
    """
    extra = {}

    # Try to access the protocol's _extra dict directly for complete data
    protocol = getattr(writer, "_protocol", None) or getattr(writer, "protocol", None)
    if protocol and hasattr(protocol, "_extra"):
        for key, value in protocol._extra.items():
            if isinstance(value, tuple):
                extra[key] = list(value)
            elif isinstance(value, bytes):
                extra[key] = value.hex()
            else:
                extra[key] = value

    # Also collect known keys via public API to catch any we missed
    known_keys = [
        # Terminal
        "TERM", "term", "cols", "rows", "COLUMNS", "LINES",
        # Encoding
        "charset", "LANG", "COLORTERM", "encoding",
        # Network
        "peername", "sockname", "tspeed", "xdisploc", "DISPLAY",
        # Environment
        "USER", "SHELL", "HOME", "PATH", "LOGNAME", "MAIL",
        # Other
        "timeout",
    ]
    for key in known_keys:
        if key not in extra:
            value = writer.get_extra_info(key)
            if value is not None:
                if isinstance(value, tuple):
                    extra[key] = list(value)
                elif isinstance(value, bytes):
                    extra[key] = value.hex()
                else:
                    extra[key] = value

    # Clean up: prefer TERM over term, remove redundant lowercase if uppercase exists
    if "TERM" in extra and "term" in extra:
        del extra["term"]
    if "COLUMNS" in extra and "cols" in extra:
        del extra["cols"]
    if "LINES" in extra and "rows" in extra:
        del extra["rows"]

    # Remove ttype1, ttype2, etc. - these are collected separately in ttype_cycle
    for i in range(1, 20):
        extra.pop(f"ttype{i}", None)

    return extra


def _collect_ttype_cycle(writer) -> List[str]:
    """
    Collect the full TTYPE cycle responses.

    :param writer: TelnetWriter instance.
    :returns: List of all TTYPE responses in order.
    """
    ttype_list = []

    # Try to get from protocol._extra first (most complete data)
    protocol = getattr(writer, "_protocol", None) or getattr(writer, "protocol", None)
    extra_dict = getattr(protocol, "_extra", {}) if protocol else {}

    for i in range(1, 20):  # Check up to 20 ttype responses
        key = f"ttype{i}"
        value = extra_dict.get(key) or writer.get_extra_info(key)
        if value is None:
            break
        ttype_list.append(value)
    return ttype_list


def _collect_protocol_timing(writer) -> Dict[str, Any]:
    """
    Collect timing information from protocol.

    :param writer: TelnetWriter instance.
    :returns: Dict with timing info.
    """
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


def _categorize_term(term: Optional[str]) -> str:
    """
    Categorize terminal type for protocol fingerprint.

    :param term: Terminal type string.
    :returns: Categorized value: specific client name, "Yes-ansi", or "Yes".
    """
    if not term:
        return "None"
    term_lower = term.lower()
    # Check for protocol-matched terminal identifiers
    if term_lower in PROTOCOL_MATCHED_TERMINALS:
        return term_lower.capitalize()
    # Check for ANSI terminals
    if "ansi" in term_lower:
        return "Yes-ansi"
    # Generic terminal
    return "Yes"


def _categorize_terminal_size(cols: Optional[int], rows: Optional[int]) -> str:
    """
    Categorize terminal size for protocol fingerprint.

    :param cols: Column count.
    :param rows: Row count.
    :returns: "Yes-80x25", "Yes-80x24", "Yes-Other", or "None".
    """
    if cols is None or rows is None:
        return "None"
    if (cols, rows) == (80, 25):
        return "Yes-80x25"
    if (cols, rows) == (80, 24):
        return "Yes-80x24"
    return "Yes-Other"


def _create_protocol_fingerprint(
    writer,
    probe_results: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Create anonymized/summarized protocol fingerprint from session data.

    This fingerprint produces deterministic hashes for sessions with similar
    protocol behavior regardless of specific environment values.

    Fields are only included if negotiated. Environment variables are summarized
    as "True" (non-empty value) or "None" (empty string).

    :param writer: TelnetWriter instance.
    :param probe_results: Probe results from capability probing.
    :returns: Dict with anonymized protocol fingerprint data.
    """
    fingerprint: Dict[str, Any] = {
        "probed-protocol": "client",
    }

    # Access protocol's _extra dict to check what was actually negotiated
    protocol = getattr(writer, "_protocol", None) or getattr(writer, "protocol", None)
    extra_dict = getattr(protocol, "_extra", {}) if protocol else {}

    # Environment variables - only include if negotiated via NEW_ENVIRON
    for key in ("HOME", "USER", "SHELL"):
        if key in extra_dict:
            value = extra_dict[key]
            fingerprint[key] = "True" if value else "None"

    # Terminal size categorization
    cols = writer.get_extra_info("cols")
    rows = writer.get_extra_info("rows")
    fingerprint["terminal-size"] = _categorize_terminal_size(cols, rows)

    # Encoding extracted from LANG
    lang = writer.get_extra_info("LANG")
    if lang:
        encoding = encoding_from_lang(lang)
        fingerprint["encoding"] = encoding if encoding else "None"
    else:
        fingerprint["encoding"] = "None"

    # TERM categorization
    term = writer.get_extra_info("TERM") or writer.get_extra_info("term")
    fingerprint["TERM"] = _categorize_term(term)

    # Charset from negotiation
    charset = writer.get_extra_info("charset")
    fingerprint["charset"] = charset if charset else "None"

    # TTYPE count
    ttype_cycle = _collect_ttype_cycle(writer)
    fingerprint["ttype-count"] = len(ttype_cycle)

    # Probe results - sorted lists of supported and refused options
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
    """
    Create deterministic SHA256 hash of protocol fingerprint.

    :param protocol_fingerprint: Dict from _create_protocol_fingerprint().
    :returns: Hexadecimal hash string (first 16 chars).
    """
    canonical = json.dumps(protocol_fingerprint, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _count_protocol_folder_files(protocol_dir: Path) -> int:
    """
    Count JSON files in protocol fingerprint directory.

    :param protocol_dir: Path to protocol fingerprint directory.
    :returns: Number of .json files in directory.
    """
    if not protocol_dir.exists():
        return 0
    return sum(1 for f in protocol_dir.iterdir() if f.suffix == ".json")


def _count_fingerprint_folders() -> int:
    """
    Count fingerprint folders in DATA_DIR.

    :returns: Number of fingerprint folders, or 0 if DATA_DIR not configured.
    """
    if DATA_DIR is None or not DATA_DIR.exists():
        return 0
    return sum(1 for f in DATA_DIR.iterdir() if f.is_dir())


def _build_session_fingerprint(
    writer,
    probe_results: Dict[str, Dict[str, Any]],
    probe_time: float,
) -> Dict[str, Any]:
    """
    Build the session fingerprint dict (raw detailed data).

    :param writer: TelnetWriter instance with full protocol access.
    :param probe_results: Probe results from capability probing.
    :param probe_time: Time taken for probing.
    :returns: Session fingerprint dict.
    """
    extra = _collect_extra_info(writer)
    extra.pop("peername", None)
    extra.pop("sockname", None)

    ttype_cycle = _collect_ttype_cycle(writer)
    option_states = _collect_option_states(writer)
    timing = _collect_protocol_timing(writer)

    # Group probe results by status
    probe_by_status: Dict[str, Dict[str, int]] = {}
    for name, info in probe_results.items():
        status = info["status"]
        opt_byte = info["opt"][0] if isinstance(info["opt"], bytes) else info["opt"]
        if status not in probe_by_status:
            probe_by_status[status] = {}
        probe_by_status[status][name] = opt_byte

    timing["probe"] = probe_time

    return {
        "extra": extra,
        "ttype_cycle": ttype_cycle,
        "option_states": option_states,
        "probe": probe_by_status,
        "timing": timing,
    }


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
    # Check if DATA_DIR is configured
    if DATA_DIR is None:
        return None

    # Use pre-built session fingerprint or build it
    if session_fp is None:
        session_fp = _build_session_fingerprint(writer, probe_results, probe_time)

    # Create display-friendly version for logging
    display_fp = _display_fingerprint(session_fp)

    # Create protocol fingerprint and hash
    protocol_fp = _create_protocol_fingerprint(writer, probe_results)
    protocol_hash = _hash_protocol_fingerprint(protocol_fp)

    # Check if this is a new fingerprint (folder doesn't exist yet)
    protocol_dir = DATA_DIR / protocol_hash
    is_new_fingerprint = not protocol_dir.exists()

    if is_new_fingerprint:
        # Check max fingerprints limit
        if _count_fingerprint_folders() >= FINGERPRINT_MAX_FINGERPRINTS:
            logger.warning(
                "max fingerprints (%d) exceeded, not saving new fingerprint %s",
                FINGERPRINT_MAX_FINGERPRINTS,
                protocol_hash,
            )
            return None

        # Create directory
        try:
            protocol_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("failed to create directory %s: %s", protocol_dir, exc)
            return None

        logger.info("new fingerprint %s: %r", protocol_hash, display_fp)
    else:
        # Existing fingerprint - check file limit
        file_count = _count_protocol_folder_files(protocol_dir)
        if file_count >= FINGERPRINT_MAX_FILES:
            logger.warning(
                "fingerprint %s at file limit (%d), not saving",
                protocol_hash,
                FINGERPRINT_MAX_FILES,
            )
            return None

        logger.info(
            "connection for fingerprint %s: %r",
            protocol_hash,
            display_fp.get("extra", {}),
        )

    # Generate unique session filename
    session_id = str(uuid.uuid4())
    filepath = protocol_dir / f"{session_id}.json"

    # Build complete data record with both fingerprints
    data = {
        "id": session_id,
        "timestamp": time.time(),
        "protocol-fingerprint": protocol_hash,
        "protocol-fingerprint-data": protocol_fp,
        "session-fingerprint": session_fp,
    }

    try:
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        return filepath
    except OSError as exc:
        logger.warning("failed to save fingerprint: %s", exc)
        return None


async def _execute_post_fingerprint_script(filepath: Path) -> None:
    """
    Execute post-fingerprint Python function with saved file path.

    Parses FINGERPRINT_POST_SCRIPT as 'module:function' and calls the function
    with the filepath as argument.

    :param filepath: Path to saved fingerprint JSON file.
    """
    if not FINGERPRINT_POST_SCRIPT:
        return

    import importlib

    try:
        if ":" not in FINGERPRINT_POST_SCRIPT:
            logger.warning(
                "FINGERPRINT_POST_SCRIPT must be 'module:function' format, got: %s",
                FINGERPRINT_POST_SCRIPT,
            )
            return

        module_path, func_name = FINGERPRINT_POST_SCRIPT.rsplit(":", 1)
        module = importlib.import_module(module_path)
        func = getattr(module, func_name)

        # Call the function - run in executor if it's not a coroutine
        result = func(filepath)
        if asyncio.iscoroutine(result):
            await result

        logger.debug("Post-fingerprint script completed: %s", FINGERPRINT_POST_SCRIPT)
    except Exception as exc:
        logger.warning("Post-fingerprint script failed: %s", exc)


async def fingerprinting_server_shell(reader, writer):
    """
    Shell that displays client fingerprint with active probing on connect.

    Immediately probes all telnet options and displays results.
    If DATA_DIR is configured and a file is saved, executes FINGERPRINT_POST_SCRIPT.

    :param reader: TelnetReader instance.
    :param writer: TelnetWriter instance.
    """
    peername = writer.get_extra_info("peername", ("unknown", 0))
    logger.info("fingerprint_shell: connection from %s:%s", peername[0], peername[1])

    # Probe immediately on connect
    probe_results, probe_time = await _run_probe(writer, verbose=DISPLAY_OUTPUT)

    # Build session fingerprint once for both display and storage
    session_fp = _build_session_fingerprint(writer, probe_results, probe_time)

    if DISPLAY_OUTPUT:
        # Filter for display: only enabled/supported, translate hex to names
        display_fp = _display_fingerprint(session_fp)
        # Use client's terminal width if available, default to 80
        width = writer.get_extra_info("cols") or 80
        output = pprint.pformat(display_fp, width=width)
        # Convert \n to \r\n for telnet
        writer.write(output.replace("\n", CRLF))
        writer.write(CRLF)

    # Save comprehensive fingerprint data to file
    filepath = _save_fingerprint_data(writer, probe_results, probe_time, session_fp)
    logger.info("Client fingerprint: %r", session_fp)

    # Execute post-fingerprint script if file was saved and script is configured
    if filepath is not None and FINGERPRINT_POST_SCRIPT:
        await _execute_post_fingerprint_script(filepath)

    await writer.drain()
    writer.close()


def fingerprinting_post_script(filepath):
    """
    Demonstration post-fingerprint script.

    Pretty-prints the fingerprint JSON file contents. Can be used as the
    TELNETLIB3_FINGERPRINT_POST_SCRIPT target::

        export TELNETLIB3_FINGERPRINT_POST_SCRIPT=\\
            telnetlib3.fingerprinting:fingerprinting_post_script
        export TELNETLIB3_DATA_DIR=./data
        python -m telnetlib3 --shell telnetlib3.fingerprinting_server_shell

    :param filepath: Path to the saved fingerprint JSON file.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        logger.warning("Post-script file not found: %s", filepath)
        return

    with open(filepath) as f:
        data = json.load(f)

    pprint.pprint(data)
