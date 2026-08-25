"""Discovery, download, caching and safe resolution of user-shareable playlist sources.

Three kinds of playlist source exist (:class:`PlaylistSourceKind`):

* ``default``  — the bundled ``radio/data/playlist.m3u``, always available.
* ``local``    — a ``*.m3u``/``*.m3u8`` file the user dropped into
  ``radio/playlists/`` (matched case-insensitively).
* ``remote``   — an ``http``/``https`` URL listed one-per-line in
  ``radio/playlists/playlist_urls.txt``.

:func:`load_source` is the single entry point the app calls to turn a
selected/persisted :class:`PlaylistSource` into parsed stations. It never
raises and never blocks app launch on a bad source: local files that
disappeared and remote URLs that are no longer listed, time out, exceed the
size bound, fail TLS validation, aren't UTF-8 or parse to zero stations all
fall back — remote sources fall back to a cached copy of the last
successful download first, then to the bundled default; local/invalid
sources fall straight back to the bundled default (REQUISITOS.md §1: never
crash startup on user-supplied/network input).

Security notes:

* Only ``http``/``https`` URLs are ever fetched or persisted — checked
  before any network call, and again on every redirect hop, since
  ``urllib`` would otherwise happily follow a redirect (or, for a
  hand-edited config, open a request) against ``file://`` or other schemes.
* TLS certificate verification stays on: :func:`download_playlist_text`
  builds an ``ssl.create_default_context`` (optionally pinned at the
  bundled CA bundle via ``cacert_path``, the same one used for stream
  playback) and never disables hostname/cert checking.
* Downloads are bounded in both time (``timeout``) and size (``max_bytes``,
  enforced by reading at most ``max_bytes + 1`` bytes) so a slow or
  oversized response can't hang or exhaust memory on embedded hardware.
* Local source ids are matched only against filenames actually discovered
  under ``playlists_dir`` right now (case-insensitive), never resolved as
  arbitrary user-supplied paths, so a corrupt/hand-edited config can't be
  used to read files outside that directory.
"""

from __future__ import annotations

import enum
import hashlib
import logging
import os
import ssl
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlsplit

from radio.playlist.parser import ParseResult, parse_m3u, parse_m3u_file

logger = logging.getLogger(__name__)

PACKAGE_ROOT = Path(__file__).resolve().parent.parent  # .../radio

DEFAULT_PLAYLISTS_DIR = PACKAGE_ROOT / "playlists"
DEFAULT_URLS_FILENAME = "playlist_urls.txt"
DEFAULT_CACHE_DIR = PACKAGE_ROOT / "data" / "playlist-cache"
DEFAULT_PLAYLIST_PATH = PACKAGE_ROOT / "data" / "playlist.m3u"

LOCAL_EXTENSIONS = (".m3u", ".m3u8")
ALLOWED_URL_SCHEMES = ("http", "https")

MAX_URL_LENGTH = 2048
MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024  # 2 MiB: generous for a text playlist, bounded for embedded HW.
DOWNLOAD_TIMEOUT_SECONDS = 10.0
USER_AGENT = "RG35XXH-Radio/1.0 (+playlist-source-fetch)"


class PlaylistSourceKind(str, enum.Enum):
    DEFAULT = "default"
    LOCAL = "local"
    REMOTE = "remote"


@dataclass(frozen=True)
class PlaylistSource:
    """One selectable playlist origin.

    ``id`` is the stable, persistable identifier: empty for ``default``, the
    bare filename for ``local`` (never a path), the full URL for ``remote``.
    ``path`` is only ever set for ``default``/``local`` and is purely a
    convenience for the resolver — never trusted on its own without being
    re-derived from a fresh directory listing (see module docstring).
    """

    kind: PlaylistSourceKind
    id: str
    path: Optional[Path] = None

    def config_value(self) -> dict:
        """JSON-safe persisted form, mirroring ``radio.i18n``'s plain-string config values."""
        return {"kind": self.kind.value, "id": self.id}


DEFAULT_SOURCE = PlaylistSource(kind=PlaylistSourceKind.DEFAULT, id="", path=DEFAULT_PLAYLIST_PATH)


class PlaylistFetchError(Exception):
    """Raised for any recoverable failure while fetching/validating a remote playlist."""


@dataclass
class PlaylistLoadResult:
    parse_result: ParseResult
    source: PlaylistSource
    status: str  # "default" | "local" | "fresh" | "cached" | "fallback"
    error: Optional[str] = None


# -- discovery ----------------------------------------------------------------


def discover_local_playlists(playlists_dir: "str | Path" = DEFAULT_PLAYLISTS_DIR) -> List[PlaylistSource]:
    """List ``*.m3u``/``*.m3u8`` files directly under ``playlists_dir`` (case-insensitive).

    Non-recursive by design: subdirectories are not descended into. Returns
    ``[]`` (never raises) if the directory doesn't exist yet, since a fresh
    install won't have one until the user creates it or drops a file in.
    """
    directory = Path(playlists_dir)
    if not directory.is_dir():
        return []

    found = []
    try:
        entries = list(directory.iterdir())
    except OSError:
        return []
    for entry in entries:
        try:
            if not entry.is_file():
                continue
        except OSError:
            continue
        if entry.suffix.lower() in LOCAL_EXTENSIONS:
            found.append(PlaylistSource(kind=PlaylistSourceKind.LOCAL, id=entry.name, path=entry))
    found.sort(key=lambda source: source.id.lower())
    return found


def _urls_file_path(playlists_dir: "str | Path", urls_path: "str | Path | None") -> Path:
    if urls_path is not None:
        return Path(urls_path)
    return Path(playlists_dir) / DEFAULT_URLS_FILENAME


def read_url_list(
    urls_path: "str | Path | None" = None,
    playlists_dir: "str | Path" = DEFAULT_PLAYLISTS_DIR,
) -> List[str]:
    """Parse ``playlist_urls.txt``: one URL per line, blank lines and ``#`` comments ignored.

    Only well-formed ``http``/``https`` URLs within :data:`MAX_URL_LENGTH`
    survive; anything else is silently skipped (a malformed line must never
    crash discovery), and duplicates are dropped while preserving order.
    """
    path = _urls_file_path(playlists_dir, urls_path)
    if not path.is_file():
        return []

    try:
        raw_text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    urls: List[str] = []
    seen = set()
    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if len(line) > MAX_URL_LENGTH:
            logger.warning("Skipping overlong playlist URL line (%d chars)", len(line))
            continue
        scheme = urlsplit(line).scheme.lower()
        if scheme not in ALLOWED_URL_SCHEMES:
            logger.warning("Skipping playlist URL with unsupported scheme: %r", line)
            continue
        if line in seen:
            continue
        seen.add(line)
        urls.append(line)
    return urls


def list_available_sources(
    playlists_dir: "str | Path" = DEFAULT_PLAYLISTS_DIR,
    urls_path: "str | Path | None" = None,
) -> List[PlaylistSource]:
    """Every currently selectable source: default, then local files, then remote URLs."""
    sources: List[PlaylistSource] = [DEFAULT_SOURCE]
    sources.extend(discover_local_playlists(playlists_dir))
    for url in read_url_list(urls_path, playlists_dir):
        sources.append(PlaylistSource(kind=PlaylistSourceKind.REMOTE, id=url, path=None))
    return sources


# -- config value <-> PlaylistSource -------------------------------------------


def normalize_playlist_source_value(value: object) -> Optional[dict]:
    """Structurally validate a persisted ``playlist_source`` config value.

    Mirrors ``radio.i18n.normalize_language``: returns a clean
    ``{"kind", "id"}`` dict, or ``None`` if ``value`` is missing, foreign, or
    unsafe (a path-traversal attempt in a local id, a non-http(s) remote
    URL, ...). ``None`` means "not configured" and the caller falls back to
    the default source — a corrupt/hand-edited config.json must never crash
    startup or resolve to something unintended.
    """
    if not isinstance(value, dict):
        return None
    kind = value.get("kind")
    raw_id = value.get("id", "")

    if kind == PlaylistSourceKind.DEFAULT.value:
        return {"kind": PlaylistSourceKind.DEFAULT.value, "id": ""}

    if kind == PlaylistSourceKind.LOCAL.value:
        if not isinstance(raw_id, str) or not raw_id:
            return None
        if raw_id in (".", "..") or "/" in raw_id or "\\" in raw_id or "\x00" in raw_id:
            return None
        return {"kind": PlaylistSourceKind.LOCAL.value, "id": raw_id}

    if kind == PlaylistSourceKind.REMOTE.value:
        if not isinstance(raw_id, str) or not raw_id or len(raw_id) > MAX_URL_LENGTH:
            return None
        if urlsplit(raw_id).scheme.lower() not in ALLOWED_URL_SCHEMES:
            return None
        return {"kind": PlaylistSourceKind.REMOTE.value, "id": raw_id}

    return None


def source_from_value(value: object) -> PlaylistSource:
    """Convert a (raw or already-normalized) config value into a :class:`PlaylistSource`.

    Always returns something usable — :data:`DEFAULT_SOURCE` for anything
    invalid/unset — never raises.
    """
    normalized = normalize_playlist_source_value(value)
    if normalized is None:
        return DEFAULT_SOURCE
    kind = normalized["kind"]
    if kind == PlaylistSourceKind.LOCAL.value:
        return PlaylistSource(kind=PlaylistSourceKind.LOCAL, id=normalized["id"], path=None)
    if kind == PlaylistSourceKind.REMOTE.value:
        return PlaylistSource(kind=PlaylistSourceKind.REMOTE, id=normalized["id"], path=None)
    return DEFAULT_SOURCE


# -- atomic cache write ---------------------------------------------------------


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def cache_path_for_url(url: str, cache_dir: "str | Path" = DEFAULT_CACHE_DIR) -> Path:
    """Stable, filesystem-safe cache filename for ``url`` (a hash, never the URL text itself)."""
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return Path(cache_dir) / f"{digest}.m3u"


# -- download -------------------------------------------------------------------


class _SchemeRestrictedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuses to follow a redirect to anything but http/https.

    ``urllib``'s default redirect handling only acts on HTTP(S) responses in
    the first place, but a malicious/misconfigured server could still issue
    a ``Location`` header pointing at a scheme this app never intends to
    fetch; this closes that gap explicitly rather than relying on defaults.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        scheme = urlsplit(newurl).scheme.lower()
        if scheme not in ALLOWED_URL_SCHEMES:
            raise PlaylistFetchError(f"refusing redirect to unsupported scheme: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_url(url: str, cacert_path: "str | Path | None", timeout: float):
    """Open ``url`` for reading. Isolated as its own function so tests can stub the network."""
    context = ssl.create_default_context(cafile=str(cacert_path) if cacert_path else None)
    opener = urllib.request.build_opener(
        _SchemeRestrictedRedirectHandler(),
        urllib.request.HTTPSHandler(context=context),
    )
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return opener.open(request, timeout=timeout)


def download_playlist_text(
    url: str,
    cacert_path: "str | Path | None" = None,
    timeout: float = DOWNLOAD_TIMEOUT_SECONDS,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
) -> str:
    """Fetch ``url`` and return its body decoded as UTF-8 text.

    Raises :class:`PlaylistFetchError` (never a raw network/SSL exception)
    for: a non-http(s) scheme, an oversized URL, any connection/TLS/timeout
    failure, a response exceeding ``max_bytes``, or a body that isn't valid
    UTF-8.
    """
    if len(url) > MAX_URL_LENGTH:
        raise PlaylistFetchError("URL exceeds maximum length")
    scheme = urlsplit(url).scheme.lower()
    if scheme not in ALLOWED_URL_SCHEMES:
        raise PlaylistFetchError(f"unsupported URL scheme: {scheme or '(none)'}")

    try:
        with _open_url(url, cacert_path, timeout) as response:
            raw = response.read(max_bytes + 1)
    except PlaylistFetchError:
        raise
    except (urllib.error.URLError, OSError, ssl.SSLError, ValueError) as exc:
        raise PlaylistFetchError(str(exc)) from exc

    if len(raw) > max_bytes:
        raise PlaylistFetchError(f"playlist exceeds maximum size of {max_bytes} bytes")

    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PlaylistFetchError(f"playlist is not valid UTF-8: {exc}") from exc


# -- resolution -------------------------------------------------------------------


def _load_default(default_playlist_path: "str | Path" = DEFAULT_PLAYLIST_PATH) -> PlaylistLoadResult:
    try:
        parse_result = parse_m3u_file(default_playlist_path)
    except (OSError, UnicodeDecodeError) as exc:
        logger.error("Failed to load bundled default playlist %s: %s", default_playlist_path, exc)
        return PlaylistLoadResult(ParseResult(), DEFAULT_SOURCE, "default", str(exc))
    return PlaylistLoadResult(parse_result, DEFAULT_SOURCE, "default", None)


def _load_local(
    source: PlaylistSource,
    playlists_dir: "str | Path",
    default_playlist_path: "str | Path",
) -> PlaylistLoadResult:
    available = {found.id.lower(): found for found in discover_local_playlists(playlists_dir)}
    match = available.get(source.id.lower())
    if match is None:
        fallback = _load_default(default_playlist_path)
        return PlaylistLoadResult(fallback.parse_result, DEFAULT_SOURCE, "fallback", "local playlist not found")

    try:
        parse_result = parse_m3u_file(match.path)
    except (OSError, UnicodeDecodeError) as exc:
        fallback = _load_default(default_playlist_path)
        return PlaylistLoadResult(fallback.parse_result, DEFAULT_SOURCE, "fallback", str(exc))

    if not parse_result.stations:
        fallback = _load_default(default_playlist_path)
        return PlaylistLoadResult(
            fallback.parse_result, DEFAULT_SOURCE, "fallback", "local playlist has no valid stations"
        )

    return PlaylistLoadResult(parse_result, match, "local", None)


def _load_remote(
    source: PlaylistSource,
    playlists_dir: "str | Path",
    urls_path: "str | Path | None",
    cache_dir: "str | Path",
    cacert_path: "str | Path | None",
    default_playlist_path: "str | Path",
    timeout: float,
) -> PlaylistLoadResult:
    allowed = set(read_url_list(urls_path, playlists_dir))
    if source.id not in allowed:
        fallback = _load_default(default_playlist_path)
        return PlaylistLoadResult(
            fallback.parse_result, DEFAULT_SOURCE, "fallback", "remote playlist URL no longer configured"
        )

    cache_file = cache_path_for_url(source.id, cache_dir)
    error_detail: Optional[str] = None
    try:
        text = download_playlist_text(source.id, cacert_path, timeout=timeout)
        parse_result = parse_m3u(text)
        if not parse_result.stations:
            raise PlaylistFetchError("downloaded playlist has no valid stations")
        try:
            _write_text_atomic(cache_file, text)
        except OSError as exc:
            logger.warning("Could not cache downloaded playlist for %s: %s", source.id, exc)
        return PlaylistLoadResult(parse_result, source, "fresh", None)
    except PlaylistFetchError as exc:
        error_detail = str(exc)
        logger.warning("Remote playlist download failed for %s: %s", source.id, exc)

    if cache_file.is_file():
        try:
            cached_text = cache_file.read_text(encoding="utf-8")
            parse_result = parse_m3u(cached_text)
        except (OSError, UnicodeDecodeError):
            parse_result = ParseResult()
        if parse_result.stations:
            return PlaylistLoadResult(parse_result, source, "cached", error_detail)

    fallback = _load_default(default_playlist_path)
    return PlaylistLoadResult(fallback.parse_result, DEFAULT_SOURCE, "fallback", error_detail)


def load_source(
    source: PlaylistSource,
    *,
    playlists_dir: "str | Path" = DEFAULT_PLAYLISTS_DIR,
    urls_path: "str | Path | None" = None,
    cache_dir: "str | Path" = DEFAULT_CACHE_DIR,
    cacert_path: "str | Path | None" = None,
    default_playlist_path: "str | Path" = DEFAULT_PLAYLIST_PATH,
    timeout: float = DOWNLOAD_TIMEOUT_SECONDS,
) -> PlaylistLoadResult:
    """Resolve ``source`` to parsed stations, never raising and never blocking launch.

    * ``default`` — parses the bundled playlist directly.
    * ``local`` — re-validated against a fresh directory listing; missing,
      unreadable or empty-after-parse falls back to ``default``.
    * ``remote`` — re-validated against the current ``playlist_urls.txt``
      (a stale/removed URL falls back immediately, without any network
      call); otherwise tries a fresh download, then the last good cached
      copy, then ``default``, in that order.
    """
    if source.kind == PlaylistSourceKind.LOCAL:
        return _load_local(source, playlists_dir, default_playlist_path)
    if source.kind == PlaylistSourceKind.REMOTE:
        return _load_remote(source, playlists_dir, urls_path, cache_dir, cacert_path, default_playlist_path, timeout)
    return _load_default(default_playlist_path)
