"""Extended M3U playlist parsing, station modelling and playlist-source selection."""

from radio.playlist.model import Station, StreamType, classify_stream_type
from radio.playlist.parser import ParseResult, parse_m3u, parse_m3u_file
from radio.playlist.sources import (
    DEFAULT_SOURCE,
    PlaylistFetchError,
    PlaylistLoadResult,
    PlaylistSource,
    PlaylistSourceKind,
    discover_local_playlists,
    list_available_sources,
    load_source,
    read_url_list,
)

__all__ = [
    "Station",
    "StreamType",
    "classify_stream_type",
    "ParseResult",
    "parse_m3u",
    "parse_m3u_file",
    "DEFAULT_SOURCE",
    "PlaylistFetchError",
    "PlaylistLoadResult",
    "PlaylistSource",
    "PlaylistSourceKind",
    "discover_local_playlists",
    "list_available_sources",
    "load_source",
    "read_url_list",
]
