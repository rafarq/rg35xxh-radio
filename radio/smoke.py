"""Command-line smoke test: parse radio/data/playlist.m3u and validate counts.

Run with ``python -m radio.smoke`` from the repository root. Exits non-zero
if the parsed entry/group counts don't match the expected playlist shape
(REQUISITOS.md §4: 1041 entries across 24 groups).
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

from radio.playlist import parse_m3u_file

EXPECTED_ENTRIES = 1041
EXPECTED_GROUPS = 24

PACKAGE_ROOT = Path(__file__).resolve().parent
DEFAULT_PLAYLIST = PACKAGE_ROOT / "data" / "playlist.m3u"


def run(playlist_path: "str | Path" = DEFAULT_PLAYLIST) -> int:
    result = parse_m3u_file(playlist_path)

    entry_count = len(result.stations)
    group_count = len(result.groups)
    type_counts = Counter(station.stream_type.value for station in result.stations)

    print(f"Playlist: {playlist_path}")
    print(f"Entries parsed: {entry_count} (expected {EXPECTED_ENTRIES})")
    print(f"Groups found: {group_count} (expected {EXPECTED_GROUPS})")
    print(f"Malformed entries skipped: {len(result.errors)}")
    print("Stream type breakdown:")
    for stream_type, count in sorted(type_counts.items()):
        print(f"  {stream_type}: {count}")

    ok = entry_count == EXPECTED_ENTRIES and group_count == EXPECTED_GROUPS
    print("OK" if ok else "MISMATCH")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(run())
