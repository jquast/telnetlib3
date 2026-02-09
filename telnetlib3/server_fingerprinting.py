"""
Fingerprint shell for telnet server identification.

This module probes remote telnet servers for protocol capabilities,
collects banner data and session information, and saves fingerprint
files.  It mirrors :mod:`telnetlib3.fingerprinting` but operates as
a client connecting *to* a server.
"""

from __future__ import annotations

# std imports
import os
import re
import sys
import json
import time
import shutil
import asyncio
import logging
import datetime
import subprocess
from typing import Any

# local
from . import fingerprinting as _fps
from .telopt import (
    VAR,
    MSSP,
    NAWS,
    LFLOW,
    TTYPE,
    VALUE,
    SNDLOC,
    TSPEED,
    USERVAR,
    LINEMODE,
    XDISPLOC,
    NEW_ENVIRON,
)
from .stream_reader import TelnetReader
from .stream_writer import TelnetWriter
from .fingerprinting import (
    EXTENDED_OPTIONS,
    ALL_PROBE_OPTIONS,
    QUICK_PROBE_OPTIONS,
    _hash_fingerprint,
    _opt_byte_to_name,
    _atomic_json_write,
    _save_fingerprint_name,
    _save_fingerprint_to_dir,
    probe_client_capabilities,
)

__all__ = ("fingerprinting_client_shell", "probe_server_capabilities")

# Options where only the client sends WILL (in response to a server's DO).
# A server should never WILL these — they describe client-side properties.
# The probe must not send DO for these; their state is already captured
# in ``server_requested`` (what the server sent DO for).
_CLIENT_ONLY_WILL = frozenset({TTYPE, TSPEED, NAWS, XDISPLOC, NEW_ENVIRON, LFLOW, LINEMODE, SNDLOC})

_BANNER_MAX_BYTES = 65536
_NEGOTIATION_SETTLE = 0.5
_BANNER_WAIT = 3.0
_POST_RETURN_WAIT = 3.0
_PROBE_TIMEOUT = 0.5
_JQ = shutil.which("jq")

# Match "yes/no" or "y/n" surrounded by non-alphanumeric chars (or at
# string boundaries).  Used to auto-answer confirmation prompts.
_YN_RE = re.compile(rb"(?i)(?:^|[^a-zA-Z0-9])(yes/no|y/n)(?:[^a-zA-Z0-9]|$)")

# Match MUD/BBS login prompts that offer 'who' as a command.
# Quoted: "enter a name (or 'who')", or bare WHO without surrounding
# alphanumerics: "\nWHO                to see players connected.\n"
_WHO_RE = re.compile(rb"(?i)(?:'who'|\"who\"|(?:^|[^a-zA-Z0-9])who(?:[^a-zA-Z0-9]|$))")

# Same pattern for 'help' — offered as a login-screen command on many MUDs.
_HELP_RE = re.compile(rb"(?i)(?:'help'|\"help\"|(?:^|[^a-zA-Z0-9])help(?:[^a-zA-Z0-9]|$))")

# Match "color?" prompts — many MUDs ask if the user wants color.
_COLOR_RE = re.compile(rb"(?i)color\s*\?")

# Match numbered menu items offering UTF-8, e.g. "5) UTF-8" or "3) utf8".
# Many BBS/MUD systems present a charset selection menu at connect time.
_MENU_UTF8_RE = re.compile(rb"(\d+)\s*\)\s*UTF-?8", re.IGNORECASE)

# Match "gb/big5" encoding selection prompts common on Chinese BBS systems.
_GB_BIG5_RE = re.compile(rb"(?i)(?:^|[^a-zA-Z0-9])gb\s*/\s*big\s*5(?:[^a-zA-Z0-9]|$)")

logger = logging.getLogger("telnetlib3.server_fingerprint")


def _is_display_worthy(v: Any) -> bool:
    """Return True if *v* should be kept in culled display output."""
    # pylint: disable-next=use-implicit-booleaness-not-comparison-to-string
    return v is not False and v != {} and v != [] and v != "" and v != b""


def _cull_display(obj: Any) -> Any:
    """Recursively remove empty, false-valued, and verbose entries for display."""
    if isinstance(obj, dict):
        return {k: _cull_display(v) for k, v in obj.items() if _is_display_worthy(v)}
    if isinstance(obj, list):
        return [_cull_display(item) for item in obj]
    if isinstance(obj, bytes):
        try:
            return obj.decode("utf-8")
        except UnicodeDecodeError:
            return obj.hex()
    return obj


def _print_json(data: dict[str, Any]) -> None:
    """Print *data* as JSON to stdout, colorized through ``jq`` when available."""
    raw = json.dumps(_cull_display(data), indent=2, sort_keys=True)
    if _JQ:
        result = subprocess.run(
            [_JQ, "-C", "."], input=raw, capture_output=True, text=True, check=False
        )
        if result.returncode == 0:
            raw = result.stdout.rstrip("\n")
    print(raw, file=sys.stdout)


def _detect_yn_prompt(banner: bytes) -> bytes:
    r"""
    Return an appropriate first-prompt response based on banner content.

    If the banner contains a ``yes/no`` or ``y/n`` confirmation prompt
    (case-insensitive, delimited by non-alphanumeric characters), returns
    ``b"yes\r\n"`` or ``b"y\r\n"`` respectively.

    If the banner contains a ``color?`` prompt (case-insensitive),
    returns ``b"y\r\n"`` to accept color.

    If the banner contains a numbered menu item for UTF-8 (e.g.
    ``5) UTF-8``), returns the digit followed by ``b"\r\n"`` to select
    the UTF-8 charset option.

    If the banner contains a ``gb/big5`` encoding selection prompt
    (common on Chinese BBS systems), returns ``b"big5\r\n"`` to select
    Big5 encoding.

    If the banner contains a MUD/BBS login prompt offering ``'who'`` as
    an alternative (e.g. "enter a name (or 'who')"), returns
    ``b"who\r\n"``.  Similarly, ``'help'`` prompts return ``b"help\r\n"``.

    Otherwise returns a bare ``b"\r\n"``.

    :param banner: Raw banner bytes collected before the first prompt.
    :returns: Response bytes to send.
    """
    match = _YN_RE.search(banner)
    if match:
        token = match.group(1).lower()
        if token == b"yes/no":
            return b"yes\r\n"
        return b"y\r\n"
    if _COLOR_RE.search(banner):
        return b"y\r\n"
    menu_match = _MENU_UTF8_RE.search(banner)
    if menu_match:
        return menu_match.group(1) + b"\r\n"
    if _GB_BIG5_RE.search(banner):
        return b"big5\r\n"
    if _WHO_RE.search(banner):
        return b"who\r\n"
    if _HELP_RE.search(banner):
        return b"help\r\n"
    return b"\r\n"


async def fingerprinting_client_shell(
    reader: TelnetReader,
    writer: TelnetWriter,
    *,
    host: str,
    port: int,
    save_path: str | None = None,
    silent: bool = False,
    set_name: str | None = None,
    environ_encoding: str = "ascii",
    scan_type: str = "quick",
    mssp_wait: float = 5.0,
    banner_quiet_time: float = 2.0,
    banner_max_wait: float = 8.0,
    banner_max_bytes: int = _BANNER_MAX_BYTES,
) -> None:
    """
    Client shell that fingerprints a remote telnet server.

    Designed to be used with :func:`functools.partial` to bind CLI
    arguments, then passed as the ``shell`` callback to
    :func:`~telnetlib3.client.open_connection` with ``encoding=False``.

    :param reader: Binary-mode :class:`~telnetlib3.stream_reader.TelnetReader`.
    :param writer: Binary-mode :class:`~telnetlib3.stream_writer.TelnetWriter`.
    :param host: Remote hostname or IP address.
    :param port: Remote port number.
    :param save_path: If set, write fingerprint JSON directly to this path.
    :param silent: Suppress fingerprint output to stdout.
    :param set_name: If set, store this name for the fingerprint hash in
        ``fingerprint_names.json`` without requiring moderation.
    :param environ_encoding: Encoding for NEW_ENVIRON data.  Default
        ``"ascii"`` per :rfc:`1572`; use ``"cp037"`` for EBCDIC hosts.
    :param scan_type: ``"quick"`` probes CORE + MUD options only (default);
        ``"full"`` includes all LEGACY options.
    :param mssp_wait: Max seconds since connect to wait for MSSP data.
    :param banner_quiet_time: Seconds of silence before considering the
        banner complete.
    :param banner_max_wait: Max seconds to wait for banner data.
    :param banner_max_bytes: Maximum bytes per banner read call.
    """
    writer.environ_encoding = environ_encoding
    try:
        await _fingerprint_session(
            reader,
            writer,
            host=host,
            port=port,
            save_path=save_path,
            silent=silent,
            set_name=set_name,
            scan_type=scan_type,
            mssp_wait=mssp_wait,
            banner_quiet_time=banner_quiet_time,
            banner_max_wait=banner_max_wait,
            banner_max_bytes=banner_max_bytes,
        )
    except (ConnectionError, EOFError) as exc:
        logger.warning("%s:%d: %s", host, port, exc)
        writer.close()


async def _fingerprint_session(  # pylint: disable=too-many-locals
    reader: TelnetReader,
    writer: TelnetWriter,
    *,
    host: str,
    port: int,
    save_path: str | None,
    silent: bool,
    set_name: str | None,
    scan_type: str = "quick",
    mssp_wait: float = 5.0,
    banner_quiet_time: float = 2.0,
    banner_max_wait: float = 8.0,
    banner_max_bytes: int = _BANNER_MAX_BYTES,
) -> None:
    """Run the fingerprint session (inner helper for error handling)."""
    start_time = time.time()

    # 1. Let straggler negotiation settle
    await asyncio.sleep(_NEGOTIATION_SETTLE)

    # 2. Read banner (pre-return) — wait until output stops
    banner_before = await _read_banner_until_quiet(
        reader, quiet_time=banner_quiet_time, max_wait=banner_max_wait, max_bytes=banner_max_bytes
    )

    # 3. Send return (or "yes"/"y" if the banner contains a y/n prompt)
    prompt_response = _detect_yn_prompt(banner_before)
    writer.write(prompt_response)
    await writer.drain()
    banner_after = await _read_banner_until_quiet(
        reader, quiet_time=banner_quiet_time, max_wait=banner_max_wait, max_bytes=banner_max_bytes
    )

    # 4. Snapshot option states before probing
    session_data: dict[str, Any] = {"option_states": _collect_server_option_states(writer)}

    # 5. Active probe (skip if connection already lost)
    if writer.is_closing():
        probe_results: dict[str, Any] = {}
        probe_time = 0.0
    else:
        probe_time = time.time()
        probe_results = await probe_server_capabilities(
            writer, scan_type=scan_type, timeout=_PROBE_TIMEOUT
        )
        probe_time = time.time() - probe_time

    # 5b. If server acknowledged MSSP but data hasn't arrived yet, wait.
    await _await_mssp_data(writer, start_time + mssp_wait)

    # 6. Complete session dicts
    session_data.update(
        {
            "scan_type": scan_type,
            "encoding": writer.environ_encoding,
            "banner_before_return": _format_banner(banner_before, encoding=writer.environ_encoding),
            "banner_after_return": _format_banner(banner_after, encoding=writer.environ_encoding),
            "timing": {"probe": probe_time, "total": time.time() - start_time},
        }
    )
    if writer.mssp_data is not None:
        session_data["mssp"] = writer.mssp_data
    if writer.zmp_data:
        session_data["zmp"] = writer.zmp_data
    if writer.atcp_data:
        session_data["atcp"] = [{"package": pkg, "value": val} for pkg, val in writer.atcp_data]
    if writer.aardwolf_data:
        session_data["aardwolf"] = writer.aardwolf_data
    if writer.mxp_data:
        session_data["mxp"] = [d.hex() if d else "activated" for d in writer.mxp_data]
    if writer.comport_data:
        session_data["comport"] = writer.comport_data

    session_entry: dict[str, Any] = {
        "host": host,
        "ip": (writer.get_extra_info("peername") or (host,))[0],
        "port": port,
        "connected": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    # 7. Save
    _save_server_fingerprint_data(
        writer=writer,
        probe_results=probe_results,
        session_data=session_data,
        session_entry=session_entry,
        save_path=save_path,
        scan_type=scan_type,
    )

    # 8. Set name in fingerprint_names.json
    if set_name is not None:
        protocol_fp = _create_server_protocol_fingerprint(
            writer, probe_results, scan_type=scan_type
        )
        protocol_hash = _hash_fingerprint(protocol_fp)
        try:
            _save_fingerprint_name(protocol_hash, set_name)
            logger.info("set name %r for %s", set_name, protocol_hash)
        except ValueError:
            logger.warning("--set-name requires --data-dir or $TELNETLIB3_DATA_DIR")

    # 9. Display
    if not silent:
        protocol_fp = _create_server_protocol_fingerprint(
            writer, probe_results, scan_type=scan_type
        )
        protocol_hash = _hash_fingerprint(protocol_fp)
        _print_json(
            {
                "server-probe": {
                    "fingerprint": protocol_hash,
                    "fingerprint-data": protocol_fp,
                    "session_data": session_data,
                },
                "sessions": [session_entry],
            }
        )

    # 10. Close
    writer.close()


async def probe_server_capabilities(
    writer: TelnetWriter,
    options: list[tuple[bytes, str, str]] | None = None,
    timeout: float = 0.5,
    scan_type: str = "quick",
) -> dict[str, _fps.ProbeResult]:
    """
    Actively probe a remote server for telnet capability support.

    Sends ``IAC DO`` for all options not yet negotiated, then waits
    for ``WILL``/``WONT`` responses.  Delegates to
    :func:`~telnetlib3.fingerprinting.probe_client_capabilities`.

    :param writer: :class:`~telnetlib3.stream_writer.TelnetWriter` instance.
    :param options: List of ``(opt_bytes, name, description)`` tuples.
        Defaults to option list based on *scan_type*, minus client-only
        options.
    :param timeout: Seconds to wait for all responses.
    :param scan_type: ``"quick"`` probes CORE + MUD options only;
        ``"full"`` includes LEGACY options.  Default ``"quick"``.
    :returns: Dict mapping option name to status dict.
    """
    if options is None:
        base = ALL_PROBE_OPTIONS if scan_type == "full" else QUICK_PROBE_OPTIONS
        # Servers handle unknown options gracefully, so always include
        # EXTENDED_OPTIONS (MUD protocols with high byte values).
        base = base + EXTENDED_OPTIONS
        options = [(opt, name, desc) for opt, name, desc in base if opt not in _CLIENT_ONLY_WILL]
    return await probe_client_capabilities(writer, options=options, timeout=timeout)


def _parse_environ_send(raw: bytes) -> list[dict[str, Any]]:
    """
    Parse a raw NEW_ENVIRON SEND payload into structured entries.

    :param raw: Bytes following ``SB NEW_ENVIRON SEND`` up to ``SE``.
    :returns: List of dicts, each with ``type`` (``"VAR"`` or ``"USERVAR"``),
        ``name`` (ASCII text portion), and optionally ``data_hex`` for
        trailing binary bytes.
    """
    entries: list[dict[str, Any]] = []
    delimiters = {VAR[0], USERVAR[0]}
    value_byte = VALUE[0]

    # Per RFC 1572: bare SEND with no VAR/USERVAR list means "send all"
    if not raw:
        return [{"type": "VAR", "name": "*"}, {"type": "USERVAR", "name": "*"}]

    # find positions of VAR/USERVAR delimiters
    breaks = [i for i, b in enumerate(raw) if b in delimiters]

    for idx, ptr in enumerate(breaks):
        kind = "VAR" if raw[ptr : ptr + 1] == VAR else "USERVAR"
        start = ptr + 1
        end = breaks[idx + 1] if idx + 1 < len(breaks) else len(raw)
        chunk = raw[start:end]

        if not chunk:
            # bare VAR or USERVAR with no name = "send all"
            entries.append({"type": kind, "name": "*"})
            continue

        # split on VALUE byte if present
        if value_byte in chunk:
            name_part, val_part = chunk.split(bytes([value_byte]), 1)
        else:
            name_part = chunk
            val_part = b""

        # contiguous ASCII-printable prefix only; trailing binary is ignored
        ascii_end = 0
        for i, b in enumerate(name_part):
            if 0x20 <= b < 0x7F:
                ascii_end = i + 1
            else:
                break
        name_text = name_part[:ascii_end].decode("ascii") if ascii_end else ""

        entry: dict[str, Any] = {"type": kind, "name": name_text}
        if val_part:
            entry["value_hex"] = val_part.hex()
        entries.append(entry)

    return entries


def _collect_server_option_states(writer: TelnetWriter) -> dict[str, dict[str, Any]]:
    """
    Collect telnet option states from the server perspective.

    :param writer: :class:`~telnetlib3.stream_writer.TelnetWriter` instance.
    :returns: Dict with ``server_offered`` (server WILL) and
        ``server_requested`` (server DO) entries.
    """
    server_offered: dict[str, Any] = {}
    for opt, enabled in writer.remote_option.items():
        server_offered[_opt_byte_to_name(opt)] = enabled

    server_requested: dict[str, Any] = {}
    for opt, enabled in writer.local_option.items():
        server_requested[_opt_byte_to_name(opt)] = enabled

    result: dict[str, Any] = {
        "server_offered": server_offered,
        "server_requested": server_requested,
    }

    if writer.environ_send_raw is not None:
        result["environ_requested"] = _parse_environ_send(writer.environ_send_raw)

    return result


def _create_server_protocol_fingerprint(
    writer: TelnetWriter, probe_results: dict[str, _fps.ProbeResult], scan_type: str = "quick"
) -> dict[str, Any]:
    """
    Create anonymized protocol fingerprint for a remote server.

    :param writer: :class:`~telnetlib3.stream_writer.TelnetWriter` instance.
    :param probe_results: Results from :func:`probe_server_capabilities`.
    :param scan_type: ``"quick"`` or ``"full"`` probe depth used.
    :returns: Deterministic fingerprint dict suitable for hashing.
    """
    offered = sorted(name for name, info in probe_results.items() if info["status"] == "WILL")
    refused = sorted(
        name for name, info in probe_results.items() if info["status"] in ("WONT", "timeout")
    )

    requested = sorted(
        _opt_byte_to_name(opt) for opt, enabled in writer.local_option.items() if enabled
    )

    return {
        "probed-protocol": "server",
        "scan-type": scan_type,
        "offered-options": offered,
        "requested-options": requested,
        "refused-options": refused,
    }


def _save_server_fingerprint_data(
    writer: TelnetWriter,
    probe_results: dict[str, _fps.ProbeResult],
    session_data: dict[str, Any],
    session_entry: dict[str, Any],
    *,
    save_path: str | None = None,
    scan_type: str = "quick",
) -> str | None:
    """
    Save server fingerprint data to a JSON file.

    Directory structure: ``DATA_DIR/server/<protocol_hash>/<session_hash>.json``

    :param writer: :class:`~telnetlib3.stream_writer.TelnetWriter` instance.
    :param probe_results: Results from :func:`probe_server_capabilities`.
    :param session_data: Pre-built dict with ``option_states``, ``banner_before_return``,
        ``banner_after_return``, and ``timing`` keys.
    :param session_entry: Pre-built dict with ``host``, ``ip``, ``port``,
        and ``connected`` keys.
    :param save_path: If set, write directly to this path.
    :param scan_type: ``"quick"`` or ``"full"`` probe depth used.
    :returns: Path to saved file, or ``None`` if saving was skipped.
    """
    protocol_fp = _create_server_protocol_fingerprint(writer, probe_results, scan_type=scan_type)
    protocol_hash = _hash_fingerprint(protocol_fp)

    data: dict[str, Any] = {
        "server-probe": {
            "fingerprint": protocol_hash,
            "fingerprint-data": protocol_fp,
            "session_data": session_data,
        },
        "sessions": [session_entry],
    }

    # Direct save path
    if save_path is not None:
        save_dir = os.path.dirname(save_path)
        if save_dir:
            os.makedirs(save_dir, exist_ok=True)
        _atomic_json_write(save_path, data)
        logger.info("saved server fingerprint to %s", save_path)
        return save_path

    # DATA_DIR-based save
    data_dir = _fps.DATA_DIR
    if data_dir is None:
        return None
    if not os.path.isdir(data_dir):
        os.makedirs(data_dir, exist_ok=True)

    session_identity = {
        "host": session_entry["host"],
        "port": session_entry["port"],
        "ip": session_entry["ip"],
    }
    session_hash = _hash_fingerprint(session_identity)
    server_dir = os.path.join(data_dir, "server", protocol_hash)

    return _save_fingerprint_to_dir(
        target_dir=server_dir,
        session_hash=session_hash,
        data=data,
        probe_key="server-probe",
        data_dir=data_dir,
        side="server",
        protocol_hash=protocol_hash,
    )


def _format_banner(data: bytes, encoding: str = "utf-8") -> str:
    """
    Format raw banner bytes for JSON serialization.

    Default ``"utf-8"`` is intentional -- banners are typically UTF-8
    regardless of ``environ_encoding``; callers may override.

    :param data: Raw bytes from the server.
    :param encoding: Character encoding to use for decoding.
    :returns: Decoded text string (undecodable bytes replaced).
    """
    return data.decode(encoding, errors="replace")


async def _await_mssp_data(writer: TelnetWriter, deadline: float) -> None:
    """Wait for MSSP data until *deadline* if server acknowledged MSSP."""
    if not writer.remote_option.enabled(MSSP) or writer.mssp_data is not None:
        return
    remaining = deadline - time.time()
    while remaining > 0 and writer.mssp_data is None:
        await asyncio.sleep(min(0.05, remaining))
        remaining = deadline - time.time()


async def _read_banner(
    reader: TelnetReader, timeout: float = _BANNER_WAIT, max_bytes: int = _BANNER_MAX_BYTES
) -> bytes:
    """
    Read up to *max_bytes* from *reader* with timeout.

    Returns whatever bytes were received before the timeout, which may
    be empty if the server sends nothing.

    :param reader: :class:`~telnetlib3.stream_reader.TelnetReader` instance.
    :param timeout: Seconds to wait for data.
    :param max_bytes: Maximum bytes to read in a single call.
    :returns: Banner bytes (may be empty).
    """
    try:
        data = await asyncio.wait_for(reader.read(max_bytes), timeout=timeout)
    except (asyncio.TimeoutError, EOFError):
        data = b""
    return data


async def _read_banner_until_quiet(
    reader: TelnetReader,
    quiet_time: float = 2.0,
    max_wait: float = 8.0,
    max_bytes: int = _BANNER_MAX_BYTES,
) -> bytes:
    """
    Read banner data until output stops for *quiet_time* seconds.

    Keeps reading chunks as they arrive.  If no new data appears within
    *quiet_time* seconds (or *max_wait* total elapses), returns everything
    collected so far.

    :param reader: :class:`~telnetlib3.stream_reader.TelnetReader` instance.
    :param quiet_time: Seconds of silence before considering banner complete.
    :param max_wait: Maximum total seconds to wait for banner data.
    :param max_bytes: Maximum bytes per read call.
    :returns: Banner bytes (may be empty).
    """
    chunks: list[bytes] = []
    loop = asyncio.get_event_loop()
    deadline = loop.time() + max_wait
    while loop.time() < deadline:
        remaining = min(quiet_time, deadline - loop.time())
        if remaining <= 0:
            break
        try:
            chunk = await asyncio.wait_for(reader.read(max_bytes), timeout=remaining)
            if not chunk:
                break
            chunks.append(chunk)
        except (asyncio.TimeoutError, EOFError):
            break
    return b"".join(chunks)
