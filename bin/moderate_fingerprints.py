#!/usr/bin/env python
"""Moderate fingerprint name suggestions."""

import collections
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

_JQ = shutil.which("jq")
_UNKNOWN = "0" * 16
_PROBES = {
    "telnet-probe": ("telnet-client", "telnet-client-revision"),
    "terminal-probe": ("terminal-emulator", "terminal-emulator-revision"),
}


def _iter_files(data_dir):
    """Yield (path, data) for each client JSON file."""
    client_base = data_dir / "client"
    if client_base.is_dir():
        for path in sorted(client_base.glob("*/*/*.json")):
            try:
                with open(path) as f:
                    yield path, json.load(f)
            except (OSError, json.JSONDecodeError):
                continue


def _print_json(label, data):
    """Print labeled JSON, colorized through jq when available."""
    raw = json.dumps(data, indent=4, sort_keys=True)
    if _JQ:
        r = subprocess.run(
            [_JQ, "-C", "."], input=raw, capture_output=True, text=True)
        if r.returncode == 0:
            raw = r.stdout.rstrip("\n")
    print(f"{label} {raw}")


def _load_names(data_dir):
    try:
        with open(data_dir / "fingerprint_names.json") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _save_names(data_dir, names):
    path = data_dir / "fingerprint_names.json"
    tmp = path.with_suffix(".json.new")
    with open(tmp, "w") as f:
        json.dump(names, f, indent=2, sort_keys=True)
    os.rename(tmp, path)
    print(f"\nSaved {path}")


def _scan(data_dir, names, revise=False):
    """Return list of (label, hash, suggestions, fp_data) for review."""
    suggestions = collections.defaultdict(list)
    fp_data = {}
    labels = {}

    for _, data in _iter_files(data_dir):
        file_sug = data.get("suggestions", {})
        for probe_key, (sug_key, rev_key) in _PROBES.items():
            h = data.get(probe_key, {}).get("fingerprint")
            if not h or h == _UNKNOWN:
                continue
            labels.setdefault(h, probe_key.split("-")[0])
            fp_data.setdefault(
                h, data.get(probe_key, {}).get("fingerprint-data", {}))
            look = rev_key if revise else sug_key
            if look in file_sug:
                suggestions[h].append(file_sug[look])

    return [
        (labels[h], h, suggestions.get(h, []), fp_data[h])
        for h in sorted(fp_data)
        if (h in names) == revise
    ]


def _review(entries, names):
    """Interactive review loop. Return True if any names were added."""
    updated = False
    for label, h, sug_list, fpd in entries:
        current = names.get(h)
        print(f"\n{'=' * 60}\n  {label}: {h}")
        if current:
            print(f"  current name: {current}")

        default = ""
        if sug_list:
            counter = collections.Counter(sug_list)
            default = counter.most_common(1)[0][0]
            print(f"  {sum(counter.values())} suggestion(s):")
            for name, count in counter.most_common():
                print(f"    {count}x  {name}")
        else:
            print("  (no client suggestions)")

        if fpd:
            _print_json("  fingerprint-data:", fpd)

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
    moved = 0
    stale = set()
    for path, data in _iter_files(data_dir):
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
    for path, _ in _iter_files(data_dir):
        hashes.update({path.parent.parent.name, path.parent.name})
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


def main():
    data_dir_env = os.environ.get("TELNETLIB3_DATA_DIR")
    if not data_dir_env:
        print("Error: TELNETLIB3_DATA_DIR not set", file=sys.stderr)
        sys.exit(1)
    data_dir = Path(data_dir_env)
    if not data_dir.exists():
        print(f"Error: {data_dir} does not exist", file=sys.stderr)
        sys.exit(1)

    revise = "--check-revise" in sys.argv
    relocated = _relocate(data_dir)
    if relocated:
        print(f"Relocated {relocated} file(s).\n")

    names = _load_names(data_dir)
    if "--no-prune" not in sys.argv and _prune(data_dir, names):
        _save_names(data_dir, names)

    entries = _scan(data_dir, names, revise)
    if entries and _review(entries, names):
        _save_names(data_dir, names)
    elif not entries:
        print("Nothing to review.")


if __name__ == "__main__":
    main()
