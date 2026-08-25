"""Offline coverage for user-shareable playlist source handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from radio.playlist import sources


VALID_M3U = """#EXTM3U
#EXTINF:-1 group-title="News",Example
https://stream.example.test/live.mp3
"""


def _default_playlist(tmp_path: Path) -> Path:
    path = tmp_path / "default.m3u"
    path.write_text(VALID_M3U, encoding="utf-8")
    return path


def test_local_discovery_is_case_insensitive_and_non_recursive(tmp_path):
    playlists = tmp_path / "playlists"
    playlists.mkdir()
    (playlists / "News.M3U").write_text(VALID_M3U, encoding="utf-8")
    (playlists / "music.m3U8").write_text(VALID_M3U, encoding="utf-8")
    (playlists / "ignored.txt").write_text("no", encoding="utf-8")
    nested = playlists / "nested"
    nested.mkdir()
    (nested / "hidden.m3u").write_text(VALID_M3U, encoding="utf-8")

    discovered = sources.discover_local_playlists(playlists)

    assert [source.id for source in discovered] == ["music.m3U8", "News.M3U"]
    assert all(source.kind is sources.PlaylistSourceKind.LOCAL for source in discovered)


def test_url_list_allows_http_https_and_ignores_comments_invalid_and_duplicates(tmp_path):
    urls = tmp_path / "playlist_urls.txt"
    urls.write_text(
        "\n# shared lists\n https://example.test/one.m3u \n"
        "http://example.test/two.m3u\nhttps://example.test/one.m3u\n"
        "file:///etc/passwd\nnot-a-url\n",
        encoding="utf-8",
    )

    assert sources.read_url_list(urls) == [
        "https://example.test/one.m3u",
        "http://example.test/two.m3u",
    ]


@pytest.mark.parametrize(
    "value",
    [
        {"kind": "local", "id": "../secret.m3u"},
        {"kind": "local", "id": "folder/list.m3u"},
        {"kind": "local", "id": r"folder\\list.m3u"},
        {"kind": "remote", "id": "file:///etc/passwd"},
        {"kind": "remote", "id": "ftp://example.test/list.m3u"},
        {"kind": "unknown", "id": "anything"},
    ],
)
def test_source_config_rejects_path_traversal_and_unsafe_values(value):
    assert sources.normalize_playlist_source_value(value) is None
    assert sources.source_from_value(value) == sources.DEFAULT_SOURCE


def test_source_config_accepts_safe_local_and_remote_values():
    assert sources.normalize_playlist_source_value({"kind": "local", "id": "Share.M3U"}) == {
        "kind": "local", "id": "Share.M3U"
    }
    assert sources.normalize_playlist_source_value({"kind": "remote", "id": "https://example.test/list.m3u"}) == {
        "kind": "remote", "id": "https://example.test/list.m3u"
    }


def test_https_download_is_bounded_and_uses_verifying_ssl_context(monkeypatch, tmp_path):
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size):
            seen["read_size"] = size
            return b"abc"

    class Opener:
        def open(self, request, timeout):
            seen["url"] = request.full_url
            seen["timeout"] = timeout
            return Response()

    context = object()
    ca_file = tmp_path / "cacert.pem"
    ca_file.write_text("test", encoding="utf-8")
    monkeypatch.setattr(sources.ssl, "create_default_context", lambda cafile: seen.setdefault("cafile", cafile) and context)

    def fake_build_opener(*handlers):
        seen["handlers"] = handlers
        return Opener()

    monkeypatch.setattr(sources.urllib.request, "build_opener", fake_build_opener)
    assert sources.download_playlist_text("https://example.test/list.m3u", ca_file, timeout=2.5, max_bytes=3) == "abc"
    assert seen["cafile"] == str(ca_file)
    assert seen["read_size"] == 4  # max_bytes + one byte to detect overflow
    assert seen["url"] == "https://example.test/list.m3u"
    assert seen["timeout"] == 2.5
    https_handlers = [handler for handler in seen["handlers"] if isinstance(handler, sources.urllib.request.HTTPSHandler)]
    assert len(https_handlers) == 1 and https_handlers[0]._context is context


def test_oversized_download_is_rejected_without_live_network(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, size): return b"abcd"

    monkeypatch.setattr(sources, "_open_url", lambda *args: Response())
    with pytest.raises(sources.PlaylistFetchError, match="maximum size"):
        sources.download_playlist_text("https://example.test/list.m3u", max_bytes=3)


def test_remote_load_uses_fresh_then_cache_then_bundled_fallback(monkeypatch, tmp_path):
    playlists = tmp_path / "playlists"
    playlists.mkdir()
    url = "https://example.test/list.m3u"
    (playlists / "playlist_urls.txt").write_text(url + "\n", encoding="utf-8")
    default = _default_playlist(tmp_path)
    cache = tmp_path / "cache"
    remote = sources.PlaylistSource(sources.PlaylistSourceKind.REMOTE, url)

    monkeypatch.setattr(sources, "download_playlist_text", lambda *args, **kwargs: VALID_M3U)
    fresh = sources.load_source(remote, playlists_dir=playlists, cache_dir=cache, default_playlist_path=default)
    assert fresh.status == "fresh" and fresh.source == remote and fresh.parse_result.stations

    monkeypatch.setattr(sources, "download_playlist_text", lambda *args, **kwargs: (_ for _ in ()).throw(sources.PlaylistFetchError("offline")))
    cached = sources.load_source(remote, playlists_dir=playlists, cache_dir=cache, default_playlist_path=default)
    assert cached.status == "cached" and cached.source == remote and cached.error == "offline"

    sources.cache_path_for_url(url, cache).unlink()
    fallback = sources.load_source(remote, playlists_dir=playlists, cache_dir=cache, default_playlist_path=default)
    assert fallback.status == "fallback" and fallback.source == sources.DEFAULT_SOURCE


def test_malformed_or_empty_local_and_remote_playlists_fall_back(monkeypatch, tmp_path):
    playlists = tmp_path / "playlists"
    playlists.mkdir()
    (playlists / "empty.M3U").write_text("#EXTM3U\n# broken", encoding="utf-8")
    default = _default_playlist(tmp_path)
    local = sources.PlaylistSource(sources.PlaylistSourceKind.LOCAL, "EMPTY.m3u")
    assert sources.load_source(local, playlists_dir=playlists, default_playlist_path=default).status == "fallback"

    url = "https://example.test/bad.m3u"
    (playlists / "playlist_urls.txt").write_text(url, encoding="utf-8")
    monkeypatch.setattr(sources, "download_playlist_text", lambda *args, **kwargs: "#EXTM3U\n# no stations")
    remote = sources.PlaylistSource(sources.PlaylistSourceKind.REMOTE, url)
    result = sources.load_source(remote, playlists_dir=playlists, default_playlist_path=default, cache_dir=tmp_path / "cache")
    assert result.status == "fallback" and result.source == sources.DEFAULT_SOURCE
