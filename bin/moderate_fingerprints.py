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
_UNKNOWN_TERMINAL_HASH = "0" * 16

_PROBE_KEYS = {
    "telnet-client": "telnet-probe",
    "terminal-emulator": "terminal-probe",
}

_REVISION_KEYS = {
    "telnet-client-revision": "telnet-probe",
    "terminal-emulator-revision": "terminal-probe",
}


def _iter_client_files(data_dir):
    """Yield (path, data) for each client JSON file."""
    client_base = data_dir / "client"
    if not client_base.is_dir():
        return
    for path in sorted(client_base.glob("*/*/*.json")):
        try:
            with open(path) as f:
                yield path, json.load(f)
        except (OSError, json.JSONDecodeError):
            continue


def _print_json(label, data):
    """Print labeled JSON, colorized through jq when available."""
    json_str = json.dumps(data, indent=4, sort_keys=True)
    if _JQ:
        result = subprocess.run(
            [_JQ, "-C", "."], input=json_str, capture_output=True, text=True)
        if result.returncode == 0:
            json_str = result.stdout.rstrip("\n")
    print(f"{label} {json_str}")


def _load_names(data_dir):
    """Load existing fingerprint_names.json or return empty dict."""
    try:
        with open(data_dir / "fingerprint_names.json") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _save_names(data_dir, names):
    """Save fingerprint_names.json."""
    path = data_dir / "fingerprint_names.json"
    tmp = path.with_suffix(".json.new")
    with open(tmp, "w") as f:
        json.dump(names, f, indent=2, sort_keys=True)
    os.rename(tmp, path)
    print(f"\nSaved {path}")


def _scan(data_dir, names, check_revise=False):
    """Scan all client files, return list of (type, hash, suggestions, fp_data).

    Each entry is an unnamed hash (or revision candidate) with its
    suggestions (possibly empty) and sample fingerprint-data.
    """
    suggestions = collections.defaultdict(list)
    fp_data_map = {}
    all_hashes = set()

    key_map = _REVISION_KEYS if check_revise else _PROBE_KEYS

    for _, data in _iter_client_files(data_dir):
        file_suggestions = data.get("suggestions", {})

        for sug_key, probe_key in key_map.items():
            hash_val = data.get(probe_key, {}).get("fingerprint")
            if not hash_val or hash_val == _UNKNOWN_TERMINAL_HASH:
                continue

            key = (sug_key, hash_val)
            all_hashes.add(key)

            if sug_key in file_suggestions:
                suggestions[key].append(file_suggestions[sug_key])

            if key not in fp_data_map:
                fp_data_map[key] = data.get(probe_key, {}).get(
                    "fingerprint-data", {})

    entries = []
    for key in sorted(all_hashes):
        sug_key, hash_val = key
        if check_revise:
            if hash_val not in names:
                continue
        else:
            if hash_val in names:
                continue
        sug_list = suggestions.get(key, [])
        entries.append((sug_key, hash_val, sug_list, fp_data_map.get(key, {})))

    return entries


def _review(entries, names):
    """Interactive review loop. Return True if any names were added."""
    updated = False
    for sug_key, hash_val, sug_list, fp_data in entries:
        current_name = names.get(hash_val)
        header = f"\n{'=' * 60}\n  {sug_key}: {hash_val}"
        if current_name:
            header += f"\n  current name: {current_name}"

        if sug_list:
            counter = collections.Counter(sug_list)
            default = counter.most_common(1)[0][0]
            print(f"{header}\n  {sum(counter.values())} suggestion(s):")
            for name, count in counter.most_common():
                print(f"    {count}x  {name}")
        else:
            default = ""
            print(f"{header}\n  (no client suggestions)")

        if fp_data:
            _print_json("  fingerprint-data:", fp_data)

        if default:
            prompt = f"  Name (return for '{default}', Ctrl-D to skip): "
        else:
            prompt = "  Name (return to skip, Ctrl-D to stop): "

        try:
            raw = input(prompt).strip()
        except EOFError:
            print()
            continue
        except KeyboardInterrupt:
            print("\nAborted.")
            return updated

        chosen = raw if raw else default
        if chosen and chosen != current_name:
            names[hash_val] = chosen
            updated = True
            print(f"  -> {hash_val} = {chosen}")
        elif current_name:
            print(f"  -> keeping {current_name}")

    return updated


def _relocate(data_dir):
    """Move misplaced JSON files to match their internal fingerprint hashes."""
    client_base = data_dir / "client"
    moved = 0
    stale_dirs = set()
    for path, data in _iter_client_files(data_dir):
        telnet_hash = data.get("telnet-probe", {}).get("fingerprint")
        terminal_hash = data.get("terminal-probe", {}).get(
            "fingerprint", _UNKNOWN_TERMINAL_HASH)
        if not telnet_hash:
            continue
        dir_telnet = path.parent.parent.name
        dir_terminal = path.parent.name
        if dir_telnet == telnet_hash and dir_terminal == terminal_hash:
            continue
        target = client_base / telnet_hash / terminal_hash / path.name
        if target.exists():
            print(f"  skip {path.name}: "
                  f"already exists in {telnet_hash}/{terminal_hash}/")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        os.rename(path, target)
        moved += 1
        print(f"  {path.name}: {dir_telnet}/{dir_terminal}/"
              f" -> {telnet_hash}/{terminal_hash}/")
        stale_dirs.add(path.parent)

    for d in stale_dirs:
        try:
            d.rmdir()
            d.parent.rmdir()
        except OSError:
            pass
    return moved


def _prune(data_dir, names):
    """Remove named hashes that have no data files."""
    data_hashes = set()
    for path, _ in _iter_client_files(data_dir):
        data_hashes.update({path.parent.parent.name, path.parent.name})
    orphaned = {h: name for h, name in names.items() if h not in data_hashes}

    if not orphaned:
        print("No orphaned hashes found.")
        return False

    print(f"Found {len(orphaned)} orphaned hash(es) with no data files:\n")
    for h, name in sorted(orphaned.items(), key=lambda x: x[1]):
        print(f"  {h}  {name}")

    print()
    try:
        answer = input("Remove these entries? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False

    if answer != "y":
        print("No changes made.")
        return False

    for h in orphaned:
        del names[h]
    return True


def main():
    """Review and moderate fingerprint name suggestions."""
    data_dir_env = os.environ.get("TELNETLIB3_DATA_DIR")
    if not data_dir_env:
        print("Error: TELNETLIB3_DATA_DIR not set", file=sys.stderr)
        sys.exit(1)

    data_dir = Path(data_dir_env)
    if not data_dir.exists():
        print(f"Error: {data_dir} does not exist", file=sys.stderr)
        sys.exit(1)

    check_revise = "--check-revise" in sys.argv
    skip_prune = "--no-prune" in sys.argv

    relocated = _relocate(data_dir)
    if relocated:
        print(f"Relocated {relocated} file(s).\n")

    names = _load_names(data_dir)

    if not skip_prune and _prune(data_dir, names):
        _save_names(data_dir, names)

    entries = _scan(data_dir, names, check_revise)

    if entries:
        if _review(entries, names):
            _save_names(data_dir, names)
            return
    else:
        print("Nothing to review.")

    print("\nNo changes made.")


if __name__ == "__main__":
    main()
