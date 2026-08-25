import pytest
from types import SimpleNamespace

PIL = pytest.importorskip("PIL")

from PIL import ImageFont

from radio.ui import render
from radio.i18n import t
from radio.app.state import SettingsView
from radio.playlist.sources import DEFAULT_SOURCE, PlaylistSource, PlaylistSourceKind


@pytest.fixture(autouse=True)
def clear_font_cache():
    render._font_cache.clear()
    yield
    render._font_cache.clear()


def test_font_uses_system_truetype_when_available(monkeypatch, tmp_path):
    font_path = tmp_path / "default.ttf"
    font_path.write_bytes(b"not a real font, just needs to exist for the path check")

    monkeypatch.setattr(render, "SYSTEM_FONT_PATH", str(font_path))
    monkeypatch.setattr(render, "BUNDLED_FONT_PATH", str(tmp_path / "missing.ttf"))

    sentinel = object()
    calls = []

    def fake_truetype(path, size):
        calls.append((path, size))
        return sentinel

    monkeypatch.setattr(ImageFont, "truetype", fake_truetype)

    font = render._font(18)

    assert font is sentinel
    assert calls == [(str(font_path), 18)]


def test_font_falls_back_to_load_default_without_size_kwarg(monkeypatch, tmp_path):
    monkeypatch.setattr(render, "SYSTEM_FONT_PATH", str(tmp_path / "missing-system.ttf"))
    monkeypatch.setattr(render, "BUNDLED_FONT_PATH", str(tmp_path / "missing-bundled.ttf"))

    def fake_truetype(path, size):
        raise OSError("cannot open resource")

    sentinel = object()

    def fake_load_default(*args, **kwargs):
        # Pillow 9.0.1's load_default() takes no arguments at all; passing
        # any would raise TypeError there, so assert none are passed.
        assert args == ()
        assert kwargs == {}
        return sentinel

    monkeypatch.setattr(ImageFont, "truetype", fake_truetype)
    monkeypatch.setattr(ImageFont, "load_default", fake_load_default)

    font = render._font(24)

    assert font is sentinel


def test_font_caches_result_per_size(monkeypatch, tmp_path):
    monkeypatch.setattr(render, "SYSTEM_FONT_PATH", str(tmp_path / "missing-system.ttf"))
    monkeypatch.setattr(render, "BUNDLED_FONT_PATH", str(tmp_path / "missing-bundled.ttf"))

    calls = []

    def fake_load_default(*args, **kwargs):
        calls.append(1)
        return object()

    monkeypatch.setattr(ImageFont, "load_default", fake_load_default)

    first = render._font(16)
    second = render._font(16)

    assert first is second
    assert len(calls) == 1


def _settings_app(*, language="en", sources=(DEFAULT_SOURCE,), status="default", error=None):
    return SimpleNamespace(
        language=language,
        t=lambda key, **kwargs: t(key, language, **kwargs),
        settings_view=SettingsView.MENU,
        settings_index=0,
        settings_message="",
        available_playlist_sources=list(sources),
        playlist_source_status=status,
        playlist_source_error=error,
    )


def test_settings_menu_has_playlist_language_and_credits_in_order(monkeypatch):
    app = _settings_app()
    captured = {}
    monkeypatch.setattr(render, "_draw_list", lambda draw, labels, selected, **kwargs: captured.update(labels=labels, selected=selected))
    render.render_settings(app)
    assert captured["labels"] == ["Playlists", "Language", "Credits"]
    assert captured["selected"] == 0


def test_playlist_picker_renders_source_kinds_status_and_rtl(monkeypatch):
    app = _settings_app(
        language="ar",
        sources=(
            DEFAULT_SOURCE,
            PlaylistSource(PlaylistSourceKind.LOCAL, "Shared.M3U"),
            PlaylistSource(PlaylistSourceKind.REMOTE, "https://example.test/shared.m3u"),
        ),
        status="cached",
        error="offline",
    )
    app.settings_view = SettingsView.PLAYLIST
    app.settings_index = 2
    captured = {}
    monkeypatch.setattr(render, "_draw_header", lambda draw, title, subtitle="", rtl=False: captured.update(title=title, subtitle=subtitle, rtl=rtl))
    monkeypatch.setattr(render, "_draw_list", lambda draw, labels, selected, **kwargs: captured.update(labels=labels, selected=selected, **kwargs))
    render.render_settings(app)
    assert captured["title"] == t("playlist_picker_title", "ar")
    assert "offline" in captured["subtitle"]
    assert captured["selected"] == 2 and captured["rtl"] is True
    assert captured["labels"] == [
        t("playlist_source_default", "ar"),
        t("playlist_source_local", "ar", name="Shared.M3U"),
        t("playlist_source_remote", "ar", url="https://example.test/shared.m3u"),
    ]
