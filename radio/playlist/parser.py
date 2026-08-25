"""Robust, dependency-free Extended M3U parser.

Tolerates malformed individual entries (missing URL, unterminated
attributes, orphan URL lines, duplicate/garbled ``#EXTINF`` blocks) by
skipping just the offending entry and recording a human-readable reason,
rather than aborting the whole playlist load (REQUISITOS.md §5).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from radio.playlist.model import Station, StreamType, classify_stream_type

logger = logging.getLogger(__name__)

UNKNOWN_GROUP = "Sin categoría"

_EXTINF_PREFIX = "#EXTINF:"
_EXTGRP_PREFIX = "#EXTGRP:"
_ATTR_RE = re.compile(r'([A-Za-z0-9_-]+)="([^"]*)"')


@dataclass
class ParseError:
    line_number: int
    reason: str
    raw: str


@dataclass
class ParseResult:
    stations: List[Station] = field(default_factory=list)
    errors: List[ParseError] = field(default_factory=list)

    @property
    def groups(self) -> List[str]:
        """Distinct group names, in first-seen (deterministic) order."""
        seen: dict[str, None] = {}
        for station in self.stations:
            seen.setdefault(station.group, None)
        return list(seen.keys())


def _split_attrs_and_title(rest: str) -> tuple[str, str]:
    """Split an ``#EXTINF:`` payload into its attribute blob and title.

    ``rest`` is everything after ``#EXTINF:``, e.g.
    ``-1 group-title="News",My Station``. The separating comma is the
    first one that appears outside of a quoted attribute value, since
    station titles may themselves legally contain commas.
    """
    in_quotes = False
    for index, char in enumerate(rest):
        if char == '"':
            in_quotes = not in_quotes
        elif char == "," and not in_quotes:
            return rest[:index], rest[index + 1 :]
    raise ValueError("no unquoted comma separating attributes from title")


def _parse_extinf(rest: str) -> dict:
    """Parse an ``#EXTINF:`` line body into a dict of attrs/name/duration."""
    attrs_blob, title = _split_attrs_and_title(rest)

    # Duration is the leading token of attrs_blob (e.g. "-1" or "123.4").
    duration_token, _, attr_tail = attrs_blob.strip().partition(" ")
    attributes = dict(_ATTR_RE.findall(attr_tail))

    name = title.strip()
    if not name:
        raise ValueError("empty station title")

    return {
        "name": name,
        "duration": duration_token,
        "attributes": attributes,
    }


def parse_m3u(text: str) -> ParseResult:
    """Parse Extended M3U content from an in-memory string.

    Malformed entries are skipped and recorded in ``ParseResult.errors``;
    parsing always completes and returns whatever valid stations it found.
    """
    result = ParseResult()

    pending_extinf: Optional[dict] = None
    pending_line_no: int = 0
    pending_extgrp: Optional[str] = None
    swallow_next_url: bool = False

    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith(_EXTINF_PREFIX):
            if pending_extinf is not None:
                result.errors.append(
                    ParseError(
                        pending_line_no,
                        "#EXTINF not followed by a URL before next #EXTINF",
                        raw_line,
                    )
                )
                pending_extinf = None
                pending_extgrp = None
            try:
                pending_extinf = _parse_extinf(line[len(_EXTINF_PREFIX) :])
                pending_line_no = line_no
                swallow_next_url = False
            except ValueError as exc:
                result.errors.append(ParseError(line_no, str(exc), raw_line))
                pending_extinf = None
                swallow_next_url = True
            continue

        if line.startswith(_EXTGRP_PREFIX):
            pending_extgrp = line[len(_EXTGRP_PREFIX) :].strip()
            continue

        if line.startswith("#"):
            # Header (#EXTM3U) or a free-form comment: neither is an error.
            continue

        # Anything else is treated as a URL/stream reference line.
        if pending_extinf is None:
            if swallow_next_url:
                # This URL belongs to the malformed #EXTINF just above it;
                # it's already accounted for by that entry's ParseError.
                swallow_next_url = False
                continue
            result.errors.append(
                ParseError(line_no, "URL line with no preceding #EXTINF", raw_line)
            )
            continue

        url = line
        if not url:
            result.errors.append(
                ParseError(pending_line_no, "empty URL", raw_line)
            )
            pending_extinf = None
            pending_extgrp = None
            continue

        attributes = pending_extinf["attributes"]
        group = attributes.get("group-title") or pending_extgrp or UNKNOWN_GROUP
        station = Station(
            name=pending_extinf["name"],
            url=url,
            group=group,
            stream_type=classify_stream_type(url),
            tvg_logo=attributes.get("tvg-logo", ""),
            attributes=dict(attributes),
        )
        result.stations.append(station)
        pending_extinf = None
        pending_extgrp = None

    if pending_extinf is not None:
        result.errors.append(
            ParseError(pending_line_no, "#EXTINF at end of file without a URL", "")
        )

    for error in result.errors:
        logger.warning("Skipping malformed M3U entry at line %d: %s", error.line_number, error.reason)

    return result


def parse_m3u_file(path: "str | Path") -> ParseResult:
    """Parse an Extended M3U file from disk (UTF-8)."""
    content = Path(path).read_text(encoding="utf-8")
    return parse_m3u(content)
