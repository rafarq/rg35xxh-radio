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


def _home_app(*, language="en", home_index=0):
    return SimpleNamespace(
        language=language,
        home_index=home_index,
        favorites=("one", "two"),
        groups=("News", "Music", "Talk"),
        recents=("one",),
        t=lambda key, **kwargs: t(key, language, **kwargs),
    )


def test_home_dashboard_is_a_complete_640x480_four_card_layout(monkeypatch):
    app = _home_app()
    labels = []
    monkeypatch.setattr(
        render,
        "_text_center",
        lambda draw, cx, y, text, font, fill: labels.append(text),
    )

    frame = render.render_home(app)

    assert frame.size == (render.WIDTH, render.HEIGHT) == (640, 480)
    assert [spec[0] for spec in render._home_card_specs(app)] == [
        "favorites", "categories", "recents", "settings"
    ]
    assert all(app.t(key) in labels for key in (
        "home_card_favorites", "home_card_categories", "home_card_recents",
        "home_card_settings", "home_card_favorites_description",
        "home_card_categories_description", "home_card_recents_description",
    ))
    assert app.t("home_card_settings_description") in labels
    assert {"2", "3", "1", "English"} <= set(labels)


def test_home_dashboard_uses_compact_cards_and_supersampled_icons(monkeypatch):
    """Home icons are composited from a high-resolution vector layer."""
    app = _home_app()

    def old_direct_path(*args, **kwargs):
        raise AssertionError("Home must not draw legacy icons directly on the frame")

    monkeypatch.setattr(render, "_draw_star_icon", old_direct_path)
    monkeypatch.setattr(render, "_draw_grid_icon", old_direct_path)
    monkeypatch.setattr(render, "_draw_clock_icon", old_direct_path)
    monkeypatch.setattr(render, "_draw_gear_icon", old_direct_path)

    frame = render.render_home(app)

    assert frame.mode == "RGB"
    assert frame.size == (640, 480)
    assert 230 <= render.CARD_HEIGHT <= 240
    assert len(render._home_card_specs(app)) == 4
    assert render.CARD_TOP + render.CARD_HEIGHT < render.HEIGHT - 48

    start_x = (render.WIDTH - (
        render.CARD_COUNT * render.CARD_WIDTH + (render.CARD_COUNT - 1) * render.CARD_GAP
    )) // 2
    icon_cx = start_x + render.CARD_WIDTH // 2
    icon_cy = render.CARD_TOP + render.ICON_TOP_MARGIN + render.ICON_SIZE // 2
    icon_box = (
        icon_cx - render.ICON_SIZE // 2 - 2,
        icon_cy - render.ICON_SIZE // 2 - 2,
        icon_cx + render.ICON_SIZE // 2 + 3,
        icon_cy + render.ICON_SIZE // 2 + 3,
    )
    icon_pixels = [
        frame.getpixel((x, y))
        for x in range(icon_box[0], icon_box[2])
        for y in range(icon_box[1], icon_box[3])
    ]
    assert any(
        pixel not in {render.CARD_BG, render.FAVORITE_COLOR}
        and pixel[0] > render.CARD_BG[0]
        and pixel[0] < render.FAVORITE_COLOR[0]
        for pixel in icon_pixels
    )


def test_home_selected_card_uses_gold_border_and_glow():
    app = _home_app(home_index=1)
    frame = render.render_home(app)
    start_x = (render.WIDTH - (render.CARD_COUNT * render.CARD_WIDTH + (render.CARD_COUNT - 1) * render.CARD_GAP)) // 2
    selected_x = start_x + render.CARD_WIDTH + render.CARD_GAP

    assert frame.getpixel((selected_x, render.CARD_TOP + render.CARD_RADIUS)) == render.HOME_SELECTED_BORDER
    assert frame.getpixel((selected_x - 3, render.CARD_TOP + render.CARD_RADIUS)) == render.HOME_SELECTED_GLOW


@pytest.mark.parametrize("language", ["es", "ar"])
def test_home_localizes_descriptions_and_renders_rtl_without_overflow(monkeypatch, language):
    app = _home_app(language=language, home_index=3)
    labels = []
    monkeypatch.setattr(
        render,
        "_text_center",
        lambda draw, cx, y, text, font, fill: labels.append(text),
    )

    frame = render.render_home(app)

    assert frame.size == (640, 480)
    assert all(app.t(key) in labels for key in (
        "home_card_favorites_description", "home_card_categories_description",
        "home_card_recents_description", "home_card_settings_description",
    ))
    assert render._is_rtl(language) is (language == "ar")


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


def test_credits_render_every_verified_link_in_a_640x480_rtl_frame(monkeypatch):
    app = _settings_app(language="ar")
    app.settings_view = SettingsView.CREDITS
    frame = render.render_settings(app)
    captured = []

    class DrawRecorder:
        def text(self, position, text, **kwargs):
            captured.append((position, text))

    monkeypatch.setattr(render, "_draw_header", lambda *args, **kwargs: None)
    monkeypatch.setattr(render, "_draw_footer", lambda *args, **kwargs: None)
    monkeypatch.setattr(render, "_font", lambda size: size)
    monkeypatch.setattr(render, "_text_width", lambda draw, text, font: len(text))

    render._render_settings_credits(app, DrawRecorder(), rtl=True)

    expected = [
        "Rafael Roa",
        "https://rafarq.com",
        "https://github.com/rafarq",
        "https://www.linkedin.com/in/rafaroa",
        "https://www.instagram.com/r4f4r04",
        "https://www.threads.net/@r4f4r04",
        "https://mastodon.cloud/@rafarq",
    ]
    rendered = [text for _, text in captured]
    assert frame.size == (640, 480)
    assert all(any(link in rendered_text for rendered_text in rendered) for link in expected)
    assert all(any(label in rendered_text for rendered_text in rendered) for label, _ in render.CREDITS_SOCIAL_LINKS)
    assert all(position[0] == render.WIDTH - 20 - len(text) for position, text in captured)
    assert max(position[1] for position, _ in captured) < render.HEIGHT - render.FOOTER_HEIGHT
