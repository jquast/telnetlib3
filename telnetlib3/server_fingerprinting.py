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
import sys
import json
import time
import shutil
import asyncio
import logging
import datetime
import subprocess
from typing import Any, Dict, List, Optional, Tuple

# local
from .telopt import (
    DO,
    VAR,
    LFLOW,
    LINEMODE,
    NAWS,
    USERVAR,
    NEW_ENVIRON,
    SNDLOC,
    TSPEED,
    TTYPE,
    VALUE,
    XDISPLOC,
)
from .stream_reader import TelnetReader
from .stream_writer import TelnetWriter
from . import fingerprinting as _fps
from .fingerprinting import (
    FINGERPRINT_MAX_FILES,
    FINGERPRINT_MAX_FINGERPRINTS,
    ALL_PROBE_OPTIONS,
    _hash_fingerprint,
    _atomic_json_write,
    _opt_byte_to_name,
    _save_fingerprint_name,
)

__all__ = (
    "fingerprinting_client_shell",
    "probe_server_capabilities",
)

# Options where only the client sends WILL (in response to a server's DO).
# A server should never WILL these — they describe client-side properties.
# The probe must not send DO for these; their state is already captured
# in ``server_requested`` (what the server sent DO for).
_CLIENT_ONLY_WILL = frozenset({
    TTYPE, TSPEED, NAWS, XDISPLOC, NEW_ENVIRON, LFLOW, LINEMODE, SNDLOC,
})

_BANNER_MAX_BYTES = 1024
_NEGOTIATION_SETTLE = 0.5
_BANNER_WAIT = 3.0
_POST_RETURN_WAIT = 3.0
_JQ = shutil.which("jq")

logger = logging.getLogger("telnetlib3.server_fingerprint")


_DISPLAY_SKIP_KEYS = {"raw_hex"}


def _cull_display(obj: Any) -> Any:
    """Recursively remove empty, false-valued, and verbose entries for display."""
    if isinstance(obj, dict):
        return {k: _cull_display(v) for k, v in obj.items()
                if k not in _DISPLAY_SKIP_KEYS
                and v is not False and v != {} and v != [] and v != ""}
    if isinstance(obj, list):
        return [_cull_display(item) for item in obj]
    return obj


def _print_json(data: Dict[str, Any]) -> None:
    """Print *data* as JSON to stdout, colorized through ``jq`` when available."""
    raw = json.dumps(_cull_display(data), indent=2, sort_keys=True)
    if _JQ:
        result = subprocess.run(
            [_JQ, "-C", "."], input=raw, capture_output=True, text=True, check=False,
        )
        if result.returncode == 0:
            raw = result.stdout.rstrip("\n")
    print(raw, file=sys.stdout)


async def fingerprinting_client_shell(
    reader: TelnetReader,
    writer: TelnetWriter,
    *,
    host: str,
    port: int,
    save_path: Optional[str] = None,
    silent: bool = False,
    set_name: Optional[str] = None,
    environ_encoding: str = "ascii",
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
    """
    writer.environ_encoding = environ_encoding
    start_time = time.time()

    # 1. Let straggler negotiation settle
    await asyncio.sleep(_NEGOTIATION_SETTLE)

    # 2. Read banner (pre-return)
    banner_before = await _read_banner(reader, timeout=_BANNER_WAIT)

    # 3. Send return, read post-return data
    writer.write(b"\r\n")
    await writer.drain()
    banner_after = await _read_banner(reader, timeout=_POST_RETURN_WAIT)

    # 4. Snapshot option states before probing
    option_states = _collect_server_option_states(writer)

    # 5. Active probe
    probe_start = time.time()
    probe_results = await probe_server_capabilities(writer)
    probe_time = time.time() - probe_start

    # 6. Peer IP
    peername = writer.get_extra_info("peername")
    ip = peername[0] if peername else host

    total_time = time.time() - start_time

    # 7. Build session dicts
    session_data: Dict[str, Any] = {
        "encoding": writer.environ_encoding,
        "option_states": option_states,
        "banner_before_return": _format_banner(banner_before),
        "banner_after_return": _format_banner(banner_after),
        "timing": {
            "probe": probe_time,
            "total": total_time,
        },
    }
    session_entry: Dict[str, Any] = {
        "host": host,
        "ip": ip,
        "port": port,
        "connected": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    # 8. Save
    _save_server_fingerprint_data(
        writer=writer,
        probe_results=probe_results,
        session_data=session_data,
        session_entry=session_entry,
        save_path=save_path,
    )

    # 9. Set name in fingerprint_names.json
    if set_name is not None:
        protocol_fp = _create_server_protocol_fingerprint(writer, probe_results)
        protocol_hash = _hash_fingerprint(protocol_fp)
        try:
            _save_fingerprint_name(protocol_hash, set_name)
            logger.info("set name %r for %s", set_name, protocol_hash)
        except ValueError:
            logger.warning("--set-name requires --data-dir or $TELNETLIB3_DATA_DIR")

    # 10. Display
    if not silent:
        protocol_fp = _create_server_protocol_fingerprint(writer, probe_results)
        protocol_hash = _hash_fingerprint(protocol_fp)
        display_data: Dict[str, Any] = {
            "server-probe": {
                "fingerprint": protocol_hash,
                "fingerprint-data": protocol_fp,
                "session_data": session_data,
            },
            "sessions": [session_entry],
        }
        _print_json(display_data)

    # 11. Close
    writer.close()


async def probe_server_capabilities(
    writer: TelnetWriter,
    options: Optional[List[Tuple[bytes, str, str]]] = None,
    timeout: float = 0.5,
) -> Dict[str, Dict[str, Any]]:
    """
    Actively probe a remote server for telnet capability support.

    Sends ``IAC DO`` for all options not yet negotiated, then waits
    for ``WILL``/``WONT`` responses.

    :param writer: :class:`~telnetlib3.stream_writer.TelnetWriter` instance.
    :param options: List of ``(opt_bytes, name, description)`` tuples.
        Defaults to :data:`~telnetlib3.fingerprinting.ALL_PROBE_OPTIONS`.
    :param timeout: Seconds to wait for all responses.
    :returns: Dict mapping option name to status dict.
    """
    if options is None:
        options = [
            (opt, name, desc) for opt, name, desc in ALL_PROBE_OPTIONS
            if opt not in _CLIENT_ONLY_WILL
        ]

    results: Dict[str, Dict[str, Any]] = {}
    to_probe: List[Tuple[bytes, str, str]] = []

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

    # Send IAC DO for each unprobed option
    for opt, name, description in to_probe:
        writer.iac(DO, opt)

    await writer.drain()

    # Wait for responses
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

    # Collect results
    for opt, name, description in to_probe:
        if name in results:
            continue
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


def _parse_environ_send(raw: bytes) -> List[Dict[str, Any]]:
    """
    Parse a raw NEW_ENVIRON SEND payload into structured entries.

    :param raw: Bytes following ``SB NEW_ENVIRON SEND`` up to ``SE``.
    :returns: List of dicts, each with ``type`` (``"VAR"`` or ``"USERVAR"``),
        ``name`` (ASCII text portion), and optionally ``data_hex`` for
        trailing binary bytes.
    """
    entries: List[Dict[str, Any]] = []
    delimiters = {VAR[0], USERVAR[0]}
    value_byte = VALUE[0]

    # find positions of VAR/USERVAR delimiters
    breaks = [i for i, b in enumerate(raw) if b in delimiters]

    for idx, ptr in enumerate(breaks):
        kind = "VAR" if raw[ptr:ptr + 1] == VAR else "USERVAR"
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

        # extract the ASCII-printable prefix as the variable name
        ascii_end = 0
        for i, b in enumerate(name_part):
            if 0x20 <= b < 0x7F:
                ascii_end = i + 1
            else:
                break
        name_text = name_part[:ascii_end].decode("ascii") if ascii_end else ""

        entry: Dict[str, Any] = {"type": kind, "name": name_text}
        if val_part:
            entry["value_hex"] = val_part.hex()
        entries.append(entry)

    return entries


def _collect_server_option_states(
    writer: TelnetWriter,
) -> Dict[str, Dict[str, Any]]:
    """
    Collect telnet option states from the server perspective.

    :param writer: :class:`~telnetlib3.stream_writer.TelnetWriter` instance.
    :returns: Dict with ``server_offered`` (server WILL) and
        ``server_requested`` (server DO) entries.
    """
    server_offered: Dict[str, Any] = {}
    for opt, enabled in writer.remote_option.items():
        server_offered[_opt_byte_to_name(opt)] = enabled

    server_requested: Dict[str, Any] = {}
    for opt, enabled in writer.local_option.items():
        server_requested[_opt_byte_to_name(opt)] = enabled

    result: Dict[str, Any] = {
        "server_offered": server_offered,
        "server_requested": server_requested,
    }

    if writer.environ_send_raw is not None:
        result["environ_requested"] = _parse_environ_send(
            writer.environ_send_raw
        )

    return result


def _create_server_protocol_fingerprint(
    writer: TelnetWriter,
    probe_results: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Create anonymized protocol fingerprint for a remote server.

    :param writer: :class:`~telnetlib3.stream_writer.TelnetWriter` instance.
    :param probe_results: Results from :func:`probe_server_capabilities`.
    :returns: Deterministic fingerprint dict suitable for hashing.
    """
    offered = sorted(
        name for name, info in probe_results.items()
        if info["status"] == "WILL"
    )
    refused = sorted(
        name for name, info in probe_results.items()
        if info["status"] in ("WONT", "timeout")
    )

    requested = sorted(
        _opt_byte_to_name(opt)
        for opt, enabled in writer.local_option.items()
        if enabled
    )

    return {
        "probed-protocol": "server",
        "offered-options": offered,
        "requested-options": requested,
        "refused-options": refused,
    }


def _save_server_fingerprint_data(
    writer: TelnetWriter,
    probe_results: Dict[str, Dict[str, Any]],
    session_data: Dict[str, Any],
    session_entry: Dict[str, Any],
    save_path: Optional[str] = None,
) -> Optional[str]:
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
    :returns: Path to saved file, or ``None`` if saving was skipped.
    """
    protocol_fp = _create_server_protocol_fingerprint(writer, probe_results)
    protocol_hash = _hash_fingerprint(protocol_fp)

    data: Dict[str, Any] = {
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
    is_new_dir = not os.path.exists(server_dir)

    if is_new_dir:
        if _count_server_fingerprint_folders(data_dir) >= FINGERPRINT_MAX_FINGERPRINTS:
            logger.warning(
                "max fingerprints (%d) exceeded, not saving %s",
                FINGERPRINT_MAX_FINGERPRINTS,
                protocol_hash,
            )
            return None
        os.makedirs(server_dir, exist_ok=True)
        logger.info("new server fingerprint %s", protocol_hash)
    else:
        file_count = sum(
            1 for f in os.listdir(server_dir) if f.endswith(".json")
        )
        if file_count >= FINGERPRINT_MAX_FILES:
            logger.warning(
                "fingerprint %s at file limit (%d), not saving",
                protocol_hash,
                FINGERPRINT_MAX_FILES,
            )
            return None
        logger.info("connection for server fingerprint %s", protocol_hash)

    filepath = os.path.join(server_dir, f"{session_hash}.json")

    if os.path.exists(filepath):
        with open(filepath, encoding="utf-8") as f:
            existing = json.load(f)
        existing["server-probe"]["session_data"] = session_data
        existing["sessions"].append(session_entry)
        _atomic_json_write(filepath, existing)
        return filepath

    _atomic_json_write(filepath, data)
    return filepath


def _count_server_fingerprint_folders(data_dir: Optional[str] = None) -> int:
    """
    Count unique fingerprint folders in ``DATA_DIR/server/``.

    :param data_dir: Override data directory.  Falls back to :data:`DATA_DIR`.
    :returns: Number of fingerprint subdirectories.
    """
    _dir = data_dir if data_dir is not None else _fps.DATA_DIR
    if _dir is None:
        return 0
    server_dir = os.path.join(_dir, "server")
    if not os.path.exists(server_dir):
        return 0
    return sum(
        1 for f in os.listdir(server_dir)
        if os.path.isdir(os.path.join(server_dir, f))
    )


def _format_banner(data: bytes) -> Dict[str, Any]:
    """
    Format raw banner bytes for JSON serialization.

    :param data: Raw bytes from the server.
    :returns: Dict with ``raw_hex``, ``text``, and ``length`` keys.
    """
    return {
        "raw_hex": data.hex(),
        "text": data.decode("utf-8", errors="replace"),
        "length": len(data),
    }


async def _read_banner(
    reader: TelnetReader,
    timeout: float = _BANNER_WAIT,
) -> bytes:
    """
    Read up to :data:`_BANNER_MAX_BYTES` from *reader* with timeout.

    Returns whatever bytes were received before the timeout, which may
    be empty if the server sends nothing.

    :param reader: :class:`~telnetlib3.stream_reader.TelnetReader` instance.
    :param timeout: Seconds to wait for data.
    :returns: Banner bytes (may be empty).
    """
    try:
        data = await asyncio.wait_for(
            reader.read(_BANNER_MAX_BYTES), timeout=timeout
        )
    except (asyncio.TimeoutError, EOFError):
        data = b""
    return data


