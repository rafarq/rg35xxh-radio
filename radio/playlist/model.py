"""Station data model and stream-type classification."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from urllib.parse import urlsplit


class StreamType(str, enum.Enum):
    """Best-effort classification of a stream URL, inferred from its path/extension.

    Icecast mount points frequently have no file extension at all (they are
    identified at request time via ``icy-*`` response headers), so
    ``ICECAST_OR_UNKNOWN`` is the deliberate fallback rather than an error.
    """

    HLS = "hls"
    AAC = "aac"
    MP3 = "mp3"
    OGG = "ogg"
    ICECAST_OR_UNKNOWN = "icecast_or_unknown"


_EXTENSION_TO_TYPE = {
    ".m3u8": StreamType.HLS,
    ".aac": StreamType.AAC,
    ".mp3": StreamType.MP3,
    ".ogg": StreamType.OGG,
    ".opus": StreamType.OGG,
}


def classify_stream_type(url: str) -> StreamType:
    """Infer a stream's container/format from its URL path.

    Query strings and fragments are ignored so that URLs such as
    ``.../stream.mp3?nocache=1`` still classify correctly. Absent a
    recognized extension (the common case for Icecast mounts), the URL is
    classified as ``ICECAST_OR_UNKNOWN`` rather than raising, since format
    can only be confirmed by actually connecting and reading response
    headers.
    """
    path = urlsplit(url).path.lower()
    for suffix, stream_type in _EXTENSION_TO_TYPE.items():
        if path.endswith(suffix):
            return stream_type
    return StreamType.ICECAST_OR_UNKNOWN


@dataclass(frozen=True)
class Station:
    """A single playlist entry (radio station)."""

    name: str
    url: str
    group: str
    stream_type: StreamType
    tvg_logo: str = ""
    attributes: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        """Stable identity for favorites/recents lookups: name + URL."""
        return f"{self.name}\x1f{self.url}"
