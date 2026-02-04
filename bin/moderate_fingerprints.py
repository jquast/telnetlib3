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
"""

# std imports
import collections
import json
import os
import sys
from pathlib import Path


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


def _scan_suggestions(data_dir):
    """Scan client-*/*.json files for suggestions, grouped by hash.

    :returns: Dict mapping (hash_type, hash_val) to list of
              (suggestion_text, filepath) tuples, plus a Counter
              for frequency.
    """
    suggestions = collections.defaultdict(list)
    sample_data = {}

    for client_dir in sorted(data_dir.iterdir()):
        if not client_dir.is_dir() or not client_dir.name.startswith("client-"):
            continue
        for json_file in sorted(client_dir.glob("*.json")):
            try:
                with open(json_file) as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue

            file_suggestions = data.get("suggestions", {})
            if not file_suggestions:
                continue

            telnet_hash = data.get("telnet-probe", {}).get("fingerprint")
            terminal_probe = data.get("terminal-probe", {})
            terminal_hash = terminal_probe.get("fingerprint")

            if telnet_hash and "telnet-client" in file_suggestions:
                key = ("telnet-client", telnet_hash)
                suggestions[key].append(file_suggestions["telnet-client"])
                if key not in sample_data:
                    sample_data[key] = data.get(
                        "telnet-probe", {}
                    ).get("fingerprint-data", {})

            if terminal_hash and "terminal-emulator" in file_suggestions:
                key = ("terminal-emulator", terminal_hash)
                suggestions[key].append(file_suggestions["terminal-emulator"])
                if key not in sample_data:
                    sample_data[key] = terminal_probe.get(
                        "fingerprint-data", {}
                    )

    return suggestions, sample_data


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

    names = _load_names(data_dir)
    suggestions, sample_data = _scan_suggestions(data_dir)

    if not suggestions:
        print("No suggestions found.")
        return

    # Filter to unknown hashes only
    unknown = {
        key: vals for key, vals in suggestions.items()
        if key[1] not in names
    }

    if not unknown:
        print("All suggested hashes are already named.")
        return

    updated = False
    for (hash_type, hash_val), suggestion_list in sorted(unknown.items()):
        counter = collections.Counter(suggestion_list)
        most_common = counter.most_common(1)[0][0]
        total = sum(counter.values())

        print(f"\n{'=' * 60}")
        print(f"  {hash_type}: {hash_val}")
        print(f"  {total} suggestion(s):")
        for name, count in counter.most_common():
            print(f"    {count}x  {name}")

        fp_data = sample_data.get((hash_type, hash_val))
        if fp_data:
            print(f"  fingerprint-data: {json.dumps(fp_data, indent=4)}")

        prompt = f"  Name (press return for '{most_common}', Ctrl-D to skip): "
        try:
            raw = input(prompt).strip()
        except EOFError:
            print()
            continue
        except KeyboardInterrupt:
            print("\nAborted.")
            return
        chosen = raw if raw else most_common

        if chosen:
            names[hash_val] = chosen
            updated = True
            print(f"  -> {hash_val} = {chosen}")

    if updated:
        _atomic_json_write(data_dir / "fingerprint_names.json", names)
        print(f"\nSaved {data_dir / 'fingerprint_names.json'}")
    else:
        print("\nNo changes made.")


if __name__ == "__main__":
    main()
