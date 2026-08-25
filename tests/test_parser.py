from pathlib import Path

import pytest

from radio.playlist.model import StreamType, classify_stream_type
from radio.playlist.parser import UNKNOWN_GROUP, parse_m3u, parse_m3u_file

REPO_ROOT = Path(__file__).resolve().parent.parent
PLAYLIST_PATH = REPO_ROOT / "radio" / "data" / "playlist.m3u"


def test_real_playlist_counts():
    result = parse_m3u_file(PLAYLIST_PATH)
    assert len(result.stations) == 1041
    assert len(result.groups) == 24
    assert result.errors == []


def test_real_playlist_preserves_utf8_and_attributes():
    result = parse_m3u_file(PLAYLIST_PATH)
    ser = next(s for s in result.stations if s.name == "Cadena SER")
    assert ser.group == "Radio_Populares"
    assert ser.url.startswith("https://")
    assert ser.tvg_logo.startswith("https://graph.facebook.com")
    assert ser.attributes["tvg-name"] == "Cadena SER"

    accented = [s for s in result.stations if "í" in s.group or "ñ" in s.group]
    assert accented, "expected at least one accented group-title to survive UTF-8 parsing"


def test_basic_valid_entry():
    text = (
        "#EXTM3U\n"
        '#EXTINF:-1 group-title="News" tvg-logo="http://x/logo.png",My Station\n'
        "https://example.com/stream.mp3\n"
    )
    result = parse_m3u(text)
    assert len(result.stations) == 1
    station = result.stations[0]
    assert station.name == "My Station"
    assert station.url == "https://example.com/stream.mp3"
    assert station.group == "News"
    assert station.tvg_logo == "http://x/logo.png"
    assert station.stream_type == StreamType.MP3
    assert result.errors == []


def test_title_with_comma_is_preserved():
    text = (
        '#EXTINF:-1 group-title="News",Radio, The Best\n'
        "https://example.com/a\n"
    )
    result = parse_m3u(text)
    assert result.stations[0].name == "Radio, The Best"


def test_extgrp_fallback_when_no_group_title_attribute():
    text = "#EXTINF:-1,Some Station\n#EXTGRP:Legacy Group\nhttps://example.com/a\n"
    result = parse_m3u(text)
    assert result.stations[0].group == "Legacy Group"


def test_missing_group_falls_back_to_unknown():
    text = "#EXTINF:-1,Some Station\nhttps://example.com/a\n"
    result = parse_m3u(text)
    assert result.stations[0].group == UNKNOWN_GROUP


def test_orphan_url_without_extinf_is_skipped_not_fatal():
    text = "https://example.com/orphan\n#EXTINF:-1,Valid\nhttps://example.com/valid\n"
    result = parse_m3u(text)
    assert len(result.stations) == 1
    assert result.stations[0].name == "Valid"
    assert len(result.errors) == 1
    assert "URL line with no preceding #EXTINF" in result.errors[0].reason


def test_extinf_without_following_url_is_skipped_not_fatal():
    text = "#EXTINF:-1,Dangling\n#EXTINF:-1,Valid\nhttps://example.com/valid\n"
    result = parse_m3u(text)
    assert len(result.stations) == 1
    assert result.stations[0].name == "Valid"
    assert len(result.errors) == 1


def test_extinf_at_end_of_file_without_url_is_skipped():
    text = "#EXTINF:-1,Dangling At EOF\n"
    result = parse_m3u(text)
    assert result.stations == []
    assert len(result.errors) == 1


def test_extinf_with_empty_title_is_skipped():
    text = '#EXTINF:-1 group-title="News",\nhttps://example.com/a\n'
    result = parse_m3u(text)
    assert result.stations == []
    assert len(result.errors) == 1


def test_extinf_with_no_comma_is_skipped():
    text = "#EXTINF:-1 no comma here\nhttps://example.com/a\n"
    result = parse_m3u(text)
    assert result.stations == []
    assert len(result.errors) == 1


def test_blank_lines_and_free_comments_ignored():
    text = (
        "#EXTM3U\n"
        "# just a comment\n"
        "\n"
        '#EXTINF:-1 group-title="News",Station\n'
        "\n"
        "https://example.com/a\n"
    )
    result = parse_m3u(text)
    assert len(result.stations) == 1
    assert result.errors == []


def test_malformed_entries_do_not_abort_rest_of_file():
    text = (
        "https://orphan/one\n"
        "#EXTINF:-1,Dangling\n"
        '#EXTINF:-1 group-title="G1",Good One\n'
        "https://example.com/good1\n"
        '#EXTINF:-1 group-title="G2",Good Two\n'
        "https://example.com/good2.aac\n"
    )
    result = parse_m3u(text)
    names = [s.name for s in result.stations]
    assert names == ["Good One", "Good Two"]
    assert len(result.errors) == 2


def test_groups_are_ordered_by_first_appearance():
    text = (
        '#EXTINF:-1 group-title="B",S1\nhttps://x/1\n'
        '#EXTINF:-1 group-title="A",S2\nhttps://x/2\n'
        '#EXTINF:-1 group-title="B",S3\nhttps://x/3\n'
    )
    result = parse_m3u(text)
    assert result.groups == ["B", "A"]


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://example.com/stream.mp3", StreamType.MP3),
        ("https://example.com/stream.MP3?x=1", StreamType.MP3),
        ("https://example.com/stream.aac", StreamType.AAC),
        ("https://example.com/playlist.m3u8", StreamType.HLS),
        ("https://example.com/playlist.m3u8#frag", StreamType.HLS),
        ("https://example.com/stream.ogg", StreamType.OGG),
        ("https://example.com/icecast/mount", StreamType.ICECAST_OR_UNKNOWN),
        ("https://example.com/", StreamType.ICECAST_OR_UNKNOWN),
    ],
)
def test_classify_stream_type(url, expected):
    assert classify_stream_type(url) == expected
