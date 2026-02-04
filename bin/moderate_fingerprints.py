#!/usr/bin/env python
"""
Moderate fingerprint name suggestions.

Scans client fingerprint JSON files for user-submitted name suggestions,
groups them by hash, and prompts the operator to accept or override names
for unknown fingerprint hashes.

Reads ``TELNETLIB3_DATA_DIR`` environment variable for the data directory.

Example usage::

    $ export TELNETLIB3_DATA_DIR=./data
    $ python bin/moderate_fingerprints.py
    $ python bin/moderate_fingerprints.py --check-revise
    $ python bin/moderate_fingerprints.py --no-prune
"""

# std imports
import collections
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


_JQ = shutil.which("jq")

_HASH_SOURCES = {
    "telnet-client": "telnet-probe",
    "terminal-emulator": "terminal-probe",
    "telnet-client-revision": "telnet-probe",
    "terminal-emulator-revision": "terminal-probe",
}


def _print_json(label, data):
    """Print labeled JSON, colorized through ``jq`` when available."""
    json_str = json.dumps(data, indent=4, sort_keys=True)
    if _JQ:
        result = subprocess.run(
            [_JQ, "-C", "."],
            input=json_str,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            json_str = result.stdout.rstrip("\n")
    print(f"{label} {json_str}")


def _atomic_json_write(filepath, data):
    """Atomically write JSON data to file via write-to-new + rename."""
    tmp_path = filepath.with_suffix(".json.new")
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.rename(str(tmp_path), str(filepath))


def _load_names(data_dir):
    """Load existing fingerprint_names.json or return empty dict."""
    names_file = data_dir / "fingerprint_names.json"
    if names_file.exists():
        with open(names_file) as f:
            return json.load(f)
    return {}


def _scan_all(data_dir):
    """Scan all client JSON files for suggestion and revision keys.

    :returns: (suggestions, sample_data) where suggestions maps
              (suggestion_key, hash_val) to list of suggested names,
              and sample_data maps the same keys to fingerprint-data dicts.
    """
    suggestions = collections.defaultdict(list)
    sample_data = {}

    client_base = data_dir / "client"
    if not client_base.is_dir():
        return suggestions, sample_data

    for json_file in sorted(client_base.glob("*/*/*.json")):
        try:
            with open(json_file) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue

        file_suggestions = data.get("suggestions", {})
        if not file_suggestions:
            continue

        hashes = {
            "telnet-probe": data.get("telnet-probe", {}).get("fingerprint"),
            "terminal-probe": data.get("terminal-probe", {}).get("fingerprint"),
        }

        for sug_key, probe_key in _HASH_SOURCES.items():
            if sug_key not in file_suggestions:
                continue
            hash_val = hashes.get(probe_key)
            if not hash_val:
                continue
            key = (sug_key, hash_val)
            suggestions[key].append(file_suggestions[sug_key])
            if key not in sample_data:
                sample_data[key] = data.get(probe_key, {}).get(
                    "fingerprint-data", {})

    return suggestions, sample_data


def _review(entries, sample_data, names):
    """Interactive review loop for a set of suggestion entries.

    :param entries: Dict of (sug_key, hash_val) -> list of suggested names.
    :param sample_data: Dict of (sug_key, hash_val) -> fingerprint-data.
    :param names: Mutable names dict, updated in place.
    :returns: True if any names were updated.
    """
    updated = False
    for (sug_key, hash_val), suggestion_list in sorted(entries.items()):
        current_name = names.get(hash_val)
        counter = collections.Counter(suggestion_list)
        most_common = counter.most_common(1)[0][0]
        total = sum(counter.values())

        print(f"\n{'=' * 60}")
        print(f"  {sug_key}: {hash_val}")
        if current_name:
            print(f"  current name: {current_name}")
        print(f"  {total} suggestion(s):")
        for name, count in counter.most_common():
            print(f"    {count}x  {name}")

        fp_data = sample_data.get((sug_key, hash_val))
        if fp_data:
            _print_json("  fingerprint-data:", fp_data)

        prompt = f"  Name (press return for '{most_common}', Ctrl-D to skip): "
        try:
            raw = input(prompt).strip()
        except EOFError:
            print()
            continue
        except KeyboardInterrupt:
            print("\nAborted.")
            return updated

        chosen = raw if raw else most_common
        if chosen and chosen != current_name:
            names[hash_val] = chosen
            updated = True
            print(f"  -> {hash_val} = {chosen}")
        elif current_name:
            print(f"  -> keeping {current_name}")

    return updated


def _collect_data_hashes(data_dir):
    """Collect all hashes that have actual data files in the client directory.

    :param data_dir: Path to the data directory.
    :returns: Set of hash strings found as telnet or terminal directories.
    """
    client_base = data_dir / "client"
    if not client_base.is_dir():
        return set()

    found = set()
    for telnet_dir in client_base.iterdir():
        if not telnet_dir.is_dir():
            continue
        found.add(telnet_dir.name)
        for terminal_dir in telnet_dir.iterdir():
            if not terminal_dir.is_dir():
                continue
            found.add(terminal_dir.name)
    return found


def _prune(data_dir, names):
    """Remove named hashes that have no data files in the client directory.

    :param data_dir: Path to the data directory.
    :param names: Mutable names dict, updated in place.
    :returns: True if any names were removed.
    """
    data_hashes = _collect_data_hashes(data_dir)
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
    names = _load_names(data_dir)

    if not skip_prune:
        pruned = _prune(data_dir, names)
        if pruned:
            _atomic_json_write(data_dir / "fingerprint_names.json", names)
            print(f"\nSaved {data_dir / 'fingerprint_names.json'}")

    all_suggestions, sample_data = _scan_all(data_dir)

    if check_revise:
        revision_keys = {"telnet-client-revision", "terminal-emulator-revision"}
        entries = {
            k: v for k, v in all_suggestions.items()
            if k[0] in revision_keys and k[1] in names
        }
        if not entries:
            print("No revision suggestions found.")
            return
    else:
        new_keys = {"telnet-client", "terminal-emulator"}
        entries = {
            k: v for k, v in all_suggestions.items()
            if k[0] in new_keys and k[1] not in names
        }
        if not entries:
            if all_suggestions:
                print("All suggested hashes are already named.")
            else:
                print("No suggestions found.")
            return

    updated = _review(entries, sample_data, names)
    if updated:
        _atomic_json_write(data_dir / "fingerprint_names.json", names)
        print(f"\nSaved {data_dir / 'fingerprint_names.json'}")
    else:
        print("\nNo changes made.")


if __name__ == "__main__":
    main()
