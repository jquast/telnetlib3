#!/usr/bin/env python
# pylint: disable=cyclic-import
"""Moderate fingerprint name suggestions."""

# std imports
import os
import sys
import json
import shutil
import signal
import socket
import argparse
import subprocess
import collections
from pathlib import Path

try:
    from wcwidth import iter_sequences, strip_sequences

    _HAS_WCWIDTH = True
except ImportError:
    _HAS_WCWIDTH = False

_BAT = shutil.which("bat") or shutil.which("batcat")
_JQ = shutil.which("jq")
_UNKNOWN = "0" * 16
_PROBES = {
    "telnet-probe": ("telnet-client", "telnet-client-revision"),
    "terminal-probe": ("terminal-emulator", "terminal-emulator-revision"),
    "server-probe": ("telnet-server", "telnet-server-revision"),
}


def _iter_files(data_dir):
    """Yield (path, data) for each fingerprint JSON file."""
    client_base = data_dir / "client"
    if client_base.is_dir():
        for path in sorted(client_base.glob("*/*/*.json")):
            try:
                with open(path, encoding="utf-8") as f:
                    yield path, json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
    server_base = data_dir / "server"
    if server_base.is_dir():
        for path in sorted(server_base.glob("*/*.json")):
            try:
                with open(path, encoding="utf-8") as f:
                    yield path, json.load(f)
            except (OSError, json.JSONDecodeError):
                continue


def _print_json(label, data):
    """Print labeled JSON, colorized through bat or jq when available."""
    raw = json.dumps(data, indent=4, sort_keys=True)
    if _BAT:
        r = subprocess.run(
            [_BAT, "-l", "json", "--style=plain", "--color=always"],
            input=raw,
            capture_output=True,
            text=True,
            check=False,
        )
        if r.returncode == 0:
            raw = r.stdout.rstrip("\n")
    elif _JQ:
        r = subprocess.run([_JQ, "-C", "."], input=raw, capture_output=True, text=True, check=False)
        if r.returncode == 0:
            raw = r.stdout.rstrip("\n")
    print(f"{label} {raw}")


def _print_telnet_context(session_data):
    """Print key telnet session fields for moderation context."""
    ttype_cycle = session_data.get("ttype_cycle", [])
    if ttype_cycle:
        print(f"  ttype cycle: {' -> '.join(ttype_cycle)}")

    extra = session_data.get("extra", {})
    if extra:
        for key in sorted(extra):
            print(f"  {key}: {extra[key]}")


def _print_terminal_context(session_data):
    """Print key terminal session fields for moderation context."""
    software = session_data.get("software_name")
    version = session_data.get("software_version")
    if software:
        sw_str = software
        if version:
            sw_str += f" {version}"
        print(f"  software: {sw_str}")

    aw = session_data.get("ambiguous_width")
    if aw is not None:
        print(f"  ambiguous_width: {aw}")


def _resolve_dns(host, timeout=5):
    """Resolve forward and reverse DNS for *host*, with timeout."""
    forward = []
    reverse = []

    def _alarm_handler(signum, frame):
        raise TimeoutError

    old_handler = signal.signal(signal.SIGALRM, _alarm_handler)
    try:
        signal.alarm(timeout)
        try:
            infos = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
            forward = sorted({info[4][0] for info in infos})
        except (socket.gaierror, TimeoutError):
            pass
        for addr in forward:
            try:
                hostname, _, _ = socket.gethostbyaddr(addr)
                reverse.append(hostname)
            except (socket.herror, socket.gaierror, TimeoutError):
                continue
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)
    return forward, sorted(set(reverse))


def _format_banner(banner_data):
    """Return (clean_text, raw_display) from a banner dict."""
    text = banner_data.get("text", "")
    raw_hex = banner_data.get("raw_hex", "")
    if _HAS_WCWIDTH and text:
        clean = strip_sequences(text)
    else:
        clean = text
    if _HAS_WCWIDTH and text:
        parts = []
        for seq in iter_sequences(text):
            parts.append(repr(seq))
        raw_display = " ".join(parts)
    else:
        raw_display = raw_hex
    return clean, raw_display


def _print_server_context(session_data):
    """Print server fingerprint details for moderation context."""
    for banner_key, banner_label in (
        ("banner_before_return", "pre-return"),
        ("banner_after_return", "post-return"),
    ):
        banner = session_data.get(banner_key, {})
        if not banner:
            continue
        clean, raw_display = _format_banner(banner)
        if clean:
            print(f"  banner ({banner_label}, clean):")
            for line in clean.splitlines():
                print(f"    {line}")
            print()
        if raw_display:
            print(f"  banner ({banner_label}, raw):")
            for i in range(0, len(raw_display), 76):
                print(f"    {raw_display[i:i + 76]}")
            print()

    host = session_data.get("host", "")
    port = session_data.get("port", "")
    if host:
        host_str = f"{host}:{port}" if port else host
        print(f"  host: {host_str}")
        forward, reverse = _resolve_dns(host)
        if forward:
            print(f"  forward DNS: {', '.join(forward)}")
        if reverse:
            print(f"  reverse DNS: {', '.join(reverse)}")


def _print_paired(paired_hashes, label, names):
    """Print paired fingerprint hashes with names when known."""
    if not paired_hashes:
        return
    other_label = "terminal" if label == "telnet" else "telnet"
    parts = []
    for ph in sorted(paired_hashes):
        name = names.get(ph)
        if name:
            parts.append(f"{name} ({ph[:8]})")
        else:
            parts.append(ph[:12])
    print(f"  paired {other_label}: {', '.join(parts)}")


def _load_names(data_dir):
    try:
        with open(data_dir / "fingerprint_names.json", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _save_names(data_dir, names):
    path = data_dir / "fingerprint_names.json"
    tmp = path.with_suffix(".json.new")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(names, f, indent=2, sort_keys=True)
    os.rename(tmp, path)
    print(f"\nSaved {path}")


def _scan(data_dir, names, revise=False):
    """Return entries for review.

    Each entry is ``(label, hash, suggestions, fp_data, session, paired)``.
    """
    suggestions = collections.defaultdict(list)
    fp_data = {}
    labels = {}
    sessions = {}
    paired = collections.defaultdict(set)

    for _, data in _iter_files(data_dir):
        file_sug = data.get("suggestions", {})
        for probe_key, (sug_key, rev_key) in _PROBES.items():
            h = data.get(probe_key, {}).get("fingerprint")
            if not h or h == _UNKNOWN:
                continue
            labels.setdefault(h, probe_key.split("-", maxsplit=1)[0])
            fp_data.setdefault(h, data.get(probe_key, {}).get("fingerprint-data", {}))
            sessions.setdefault(h, data.get(probe_key, {}).get("session_data", {}))
            if probe_key in ("telnet-probe", "terminal-probe"):
                other = "terminal-probe" if probe_key == "telnet-probe" else "telnet-probe"
                other_h = data.get(other, {}).get("fingerprint")
                if other_h and other_h != _UNKNOWN:
                    paired[h].add(other_h)
            look = rev_key if revise else sug_key
            if look in file_sug:
                suggestions[h].append(file_sug[look])

    return [
        (
            labels[h],
            h,
            suggestions.get(h, []),
            fp_data[h],
            sessions.get(h, {}),
            paired.get(h, set()),
        )
        for h in sorted(fp_data)
        if (h in names) == revise
    ]


def _review(entries, names):
    """Interactive review loop. Return True if any names were added."""
    updated = False
    for label, h, sug_list, fpd, session_data, paired_hashes in entries:
        current = names.get(h)
        print(f"\n{'=' * 60}\n  {label}: {h}")
        if current:
            print(f"  current name: {current}")

        if fpd:
            _print_json("  fingerprint-data:", fpd)

        if label == "telnet" and session_data:
            _print_telnet_context(session_data)
        elif label == "terminal" and session_data:
            _print_terminal_context(session_data)
        elif label == "server" and session_data:
            _print_server_context(session_data)
        _print_paired(paired_hashes, label, names)

        default = ""
        if sug_list:
            counter = collections.Counter(sug_list)
            default = counter.most_common(1)[0][0]
            print(f"  {sum(counter.values())} suggestion(s):")
            for name, count in counter.most_common():
                print(f"    {count}x  {name}")
        else:
            print("  (no client suggestions)")

        suffix = f"for '{default}'" if default else "to skip"
        try:
            raw = input(f"  Name (return {suffix}): ").strip()
        except EOFError:
            print()
            continue
        except KeyboardInterrupt:
            print("\nAborted.")
            return updated

        chosen = raw or default
        if chosen and chosen != current:
            names[h] = chosen
            updated = True
            print(f"  -> {h} = {chosen}")

    return updated


def _relocate(data_dir):
    """Move misplaced JSON files to match their internal fingerprint hashes."""
    client_base = data_dir / "client"
    server_base = data_dir / "server"
    moved = 0
    stale = set()
    for path, data in _iter_files(data_dir):
        sh = data.get("server-probe", {}).get("fingerprint")
        if sh:
            if path.parent.name == sh:
                continue
            target = server_base / sh / path.name
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            os.rename(path, target)
            moved += 1
            stale.add(path.parent)
            continue
        th = data.get("telnet-probe", {}).get("fingerprint")
        tmh = data.get("terminal-probe", {}).get("fingerprint", _UNKNOWN)
        if not th:
            continue
        if path.parent.parent.name == th and path.parent.name == tmh:
            continue
        target = client_base / th / tmh / path.name
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        os.rename(path, target)
        moved += 1
        stale.add(path.parent)

    for d in stale:
        try:
            d.rmdir()
            d.parent.rmdir()
        except OSError:
            pass
    return moved


def _prune(data_dir, names):
    """Remove named hashes that have no data files."""
    hashes = set()
    for _path, data in _iter_files(data_dir):
        for probe_key in _PROBES:
            h = data.get(probe_key, {}).get("fingerprint")
            if h and h != _UNKNOWN:
                hashes.add(h)
    orphaned = {h: n for h, n in names.items() if h not in hashes}
    if not orphaned:
        return False

    print(f"Found {len(orphaned)} orphaned hash(es):")
    for h, name in sorted(orphaned.items(), key=lambda x: x[1]):
        print(f"  {h}  {name}")
    try:
        if input("\nRemove? [y/N] ").strip().lower() != "y":
            return False
    except (EOFError, KeyboardInterrupt):
        print()
        return False

    for h in orphaned:
        del names[h]
    return True


def _get_argument_parser():
    """Build argument parser for ``moderate_fingerprints`` CLI."""
    parser = argparse.ArgumentParser(
        description="Moderate fingerprint name suggestions",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("TELNETLIB3_DATA_DIR"),
        help="directory for fingerprint data (default: $TELNETLIB3_DATA_DIR)",
    )
    parser.add_argument(
        "--check-revise", action="store_true", help="review already-named fingerprints for revision"
    )
    parser.add_argument(
        "--no-prune",
        action="store_true",
        help="skip pruning orphaned hashes from fingerprint_names.json",
    )
    return parser


def main():
    """CLI entry point for moderating fingerprint name suggestions."""
    args = _get_argument_parser().parse_args()

    if not args.data_dir:
        print("Error: --data-dir or $TELNETLIB3_DATA_DIR required", file=sys.stderr)
        sys.exit(1)
    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        print(f"Error: {data_dir} does not exist", file=sys.stderr)
        sys.exit(1)

    relocated = _relocate(data_dir)
    if relocated:
        print(f"Relocated {relocated} file(s).\n")

    names = _load_names(data_dir)
    if not args.no_prune and _prune(data_dir, names):
        _save_names(data_dir, names)

    entries = _scan(data_dir, names, args.check_revise)
    if entries and _review(entries, names):
        _save_names(data_dir, names)
    elif not entries:
        print("Nothing to review.")


if __name__ == "__main__":
    main()
