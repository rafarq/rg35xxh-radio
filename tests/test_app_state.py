from pathlib import Path

import pytest

from radio.app.state import (
    CONNECT_TIMEOUT_SECONDS,
    HOME_CARDS,
    SETTINGS_MENU_ITEMS,
    RadioApp,
    Screen,
    SettingsView,
)
from radio.playback.controller import PlaybackError, PlaybackState
from radio.playlist.sources import (
    DEFAULT_SOURCE,
    PlaylistLoadResult,
    PlaylistSource,
    PlaylistSourceKind,
)
from radio.playlist.parser import parse_m3u
from radio.storage import DataStore

SAMPLE_M3U = """#EXTM3U
#EXTINF:-1 group-title="News",Alpha News
https://example.com/alpha.mp3
#EXTINF:-1 group-title="News",Beta News
https://example.com/beta.mp3
#EXTINF:-1 group-title="Music",Gamma Music
https://example.com/gamma.mp3
"""


class FakePlayer:
    """Minimal stand-in for PlayerController: no real subprocess involved."""

    def __init__(self, engine_path=None, fail_on_start=False):
        self.engine_path = engine_path or Path("/nonexistent/engine")
        self.state = PlaybackState.IDLE
        self.current_url = None
        self.started_urls = []
        self.stop_calls = 0
        self.fail_on_start = fail_on_start

    def start(self, url):
        if self.fail_on_start:
            raise PlaybackError("boom")
        self.started_urls.append(url)
        self.current_url = url
        self.state = PlaybackState.STARTING

    def stop(self):
        self.stop_calls += 1
        self.current_url = None
        self.state = PlaybackState.STOPPED

    def poll(self):
        return self.state


@pytest.fixture
def parse_result():
    return parse_m3u(SAMPLE_M3U)


@pytest.fixture
def store(tmp_path):
    return DataStore(tmp_path / "data")


@pytest.fixture
def player():
    return FakePlayer()


@pytest.fixture
def app(parse_result, store, player):
    return RadioApp(parse_result, store, player)


def go_to_categories(app):
    """Navigate HOME to the Categories screen via the Categories card."""
    app.go_home()
    app.home_index = HOME_CARDS.index(Screen.CATEGORIES)
    app.handle_button("A", True)


def go_to_favorites(app):
    """Navigate HOME to the Favorites screen via the Favorites card."""
    app.go_home()
    app.home_index = HOME_CARDS.index(Screen.FAVORITES)
    app.handle_button("A", True)


def test_initial_screen_is_home(app):
    assert app.screen == Screen.HOME
    assert app.groups == ["News", "Music"]


def test_home_dy_moves_card_cursor_and_wraps(app):
    assert app.home_index == 0
    app.handle_axis("DY", 1)
    assert app.home_index == 1
    app.handle_axis("DY", 1)
    assert app.home_index == 2
    app.handle_axis("DY", 1)
    assert app.home_index == 3
    app.handle_axis("DY", 1)
    assert app.home_index == 0


def test_home_a_targets_selected_card(app):
    app.home_index = HOME_CARDS.index(Screen.FAVORITES)
    app.handle_button("A", True)
    assert app.screen == Screen.FAVORITES

    app.go_home()
    app.home_index = HOME_CARDS.index(Screen.RECENTS)
    app.handle_button("A", True)
    assert app.screen == Screen.RECENTS

    app.go_home()
    app.home_index = HOME_CARDS.index(Screen.CATEGORIES)
    app.handle_button("A", True)
    assert app.screen == Screen.CATEGORIES


def test_dy_moves_category_cursor_and_wraps(app):
    go_to_categories(app)
    app.handle_axis("DY", 1)
    assert app.category_index == 1
    app.handle_axis("DY", 1)
    assert app.category_index == 0


def test_a_enters_stations_for_selected_category(app):
    go_to_categories(app)
    app.handle_button("A", True)
    assert app.screen == Screen.STATIONS
    stations = app.current_station_list()
    assert [s.name for s in stations] == ["Alpha News", "Beta News"]


def test_b_from_stations_returns_to_categories(app):
    go_to_categories(app)
    app.handle_button("A", True)
    app.handle_button("B", True)
    assert app.screen == Screen.CATEGORIES


def test_b_from_categories_returns_home(app):
    go_to_categories(app)
    app.handle_button("B", True)
    assert app.screen == Screen.HOME


def test_l1_r1_change_category_while_browsing_stations(app):
    go_to_categories(app)
    app.handle_button("A", True)
    app.handle_button("R1", True)
    assert app.current_group() == "Music"
    assert app.station_index == 0
    app.handle_button("L1", True)
    assert app.current_group() == "News"


def test_a_on_stations_plays_selected_station(app, player):
    go_to_categories(app)
    app.handle_button("A", True)
    app.handle_button("A", True)
    assert app.screen == Screen.NOW_PLAYING
    assert player.started_urls == ["https://example.com/alpha.mp3"]
    assert app.now_playing.name == "Alpha News"


def test_play_adds_to_recents_and_persists_last_selection(app, store):
    go_to_categories(app)
    app.handle_button("A", True)
    app.handle_button("A", True)
    assert store.load_recents() == [app.now_playing.id]
    config = store.load_config()
    assert config["last_group"] == "News"
    assert config["last_station_id"] == app.now_playing.id


def test_start_toggles_favorite_and_persists(app, store):
    go_to_categories(app)
    app.handle_button("A", True)
    app.handle_button("START", True)
    station = app.selected_station()
    assert station.id in app.favorites
    assert store.load_favorites() == [station.id]
    app.handle_button("START", True)
    assert app.favorites == []


def test_b_on_now_playing_stops_playback_and_goes_back(app, player):
    go_to_categories(app)
    app.handle_button("A", True)
    app.handle_button("A", True)
    app.handle_button("B", True)
    assert player.stop_calls == 1
    assert app.now_playing is None
    assert app.screen == Screen.STATIONS


def test_dx_no_longer_adjusts_volume_anywhere(app):
    assert app.volume == 60
    app.handle_axis("DX", 1)
    assert app.volume == 60
    go_to_categories(app)
    app.handle_button("A", True)
    app.handle_button("A", True)
    assert app.screen == Screen.NOW_PLAYING
    app.handle_axis("DX", 1)
    app.handle_axis("DX", -1)
    assert app.volume == 60


def test_volume_buttons_adjust_volume_on_home(app, store):
    assert app.screen == Screen.HOME
    app.handle_button("VOLUME_UP", True)
    assert app.volume == 65
    assert store.load_config()["volume"] == 65
    assert app.screen == Screen.HOME  # volume buttons never navigate
    app.handle_button("VOLUME_DOWN", True)
    assert app.volume == 60
    assert app.screen == Screen.HOME


def test_volume_buttons_adjust_volume_on_lists(app):
    go_to_categories(app)
    app.handle_button("A", True)
    assert app.screen == Screen.STATIONS
    app.handle_button("VOLUME_UP", True)
    assert app.volume == 65
    assert app.screen == Screen.STATIONS
    app.handle_button("VOLUME_DOWN", True)
    app.handle_button("VOLUME_DOWN", True)
    assert app.volume == 55
    assert app.screen == Screen.STATIONS


def test_volume_buttons_adjust_volume_on_now_playing(app, store):
    go_to_categories(app)
    app.handle_button("A", True)
    app.handle_button("A", True)
    assert app.screen == Screen.NOW_PLAYING
    app.handle_button("VOLUME_UP", True)
    assert app.volume == 65
    assert store.load_config()["volume"] == 65
    assert app.screen == Screen.NOW_PLAYING
    app.handle_button("VOLUME_DOWN", True)
    assert app.volume == 60
    assert app.screen == Screen.NOW_PLAYING


def test_volume_buttons_repeat_while_held(app):
    app.handle_button("VOLUME_UP", True, repeat=True)
    assert app.volume == 65


def test_volume_is_clamped_to_0_100(app):
    for _ in range(30):
        app.handle_button("VOLUME_UP", True)
    assert app.volume == 100
    for _ in range(50):
        app.handle_button("VOLUME_DOWN", True)
    assert app.volume == 0


def test_menu_requests_exit_and_stops_playback(app, player):
    app.handle_button("A", True)
    app.handle_button("A", True)
    app.handle_button("MENU", True)
    assert app.should_exit is True
    assert player.stop_calls == 1


def test_y_and_x_are_noops_the_dashboard_is_the_only_section_entry(app):
    assert app.screen == Screen.HOME
    app.handle_button("Y", True)
    assert app.screen == Screen.HOME
    app.handle_button("X", True)
    assert app.screen == Screen.HOME


def test_select_goes_home(app):
    go_to_favorites(app)
    app.handle_button("SELECT", True)
    assert app.screen == Screen.HOME


def test_repeat_does_not_retrigger_single_shot_buttons(app, player):
    go_to_categories(app)
    app.handle_button("A", True)
    app.handle_button("A", True, repeat=True)
    assert app.screen == Screen.STATIONS
    assert player.started_urls == []


def test_repeat_is_allowed_for_category_paging(app):
    go_to_categories(app)
    app.handle_button("A", True)
    app.handle_button("R1", True, repeat=True)
    assert app.current_group() == "Music"


def test_button_release_is_ignored(app):
    app.handle_button("A", False)
    assert app.screen == Screen.HOME


def test_playback_error_sets_status_message_without_crashing(parse_result, store):
    player = FakePlayer(fail_on_start=True)
    app = RadioApp(parse_result, store, player)
    go_to_categories(app)
    app.handle_button("A", True)
    app.handle_button("A", True)
    assert app.screen == Screen.NOW_PLAYING
    assert "No se pudo iniciar" in app.status_message
    assert app.now_playing is not None


def test_tick_clears_status_once_playing(app, player):
    app.handle_button("A", True)
    app.handle_button("A", True)
    player.state = PlaybackState.PLAYING
    app.tick()
    assert app.status_message == ""


def test_tick_reports_error_status(app, player):
    app.handle_button("A", True)
    app.handle_button("A", True)
    player.state = PlaybackState.ERROR
    app.tick()
    assert app.status_message != ""


def test_tick_enforces_connect_timeout(parse_result, store):
    clock = [0.0]
    player = FakePlayer()
    app = RadioApp(parse_result, store, player, now=lambda: clock[0])
    go_to_categories(app)
    app.handle_button("A", True)
    app.handle_button("A", True)
    clock[0] = CONNECT_TIMEOUT_SECONDS + 1
    app.tick()
    assert player.stop_calls == 1
    assert "agotado" in app.status_message


def test_activate_on_now_playing_retries_after_error(app, player):
    go_to_categories(app)
    app.handle_button("A", True)
    app.handle_button("A", True)
    player.state = PlaybackState.ERROR
    app.tick()
    player.started_urls.clear()
    app.handle_button("A", True)
    assert player.started_urls == ["https://example.com/alpha.mp3"]


def test_restores_last_selection_from_config_on_reopen(parse_result, store):
    bootstrap_app = RadioApp(parse_result, store, FakePlayer())
    go_to_categories(bootstrap_app)
    bootstrap_app.handle_button("A", True)
    bootstrap_app.handle_button("R1", True)
    bootstrap_app.handle_button("A", True)  # plays Gamma Music

    reopened = RadioApp(parse_result, store, FakePlayer())
    assert reopened.current_group() == "Music"
    reopened.screen = Screen.STATIONS
    assert reopened.selected_station().name == "Gamma Music"


def test_engine_status_reports_missing_binary(app, tmp_path):
    app.player.engine_path = tmp_path / "missing-engine"
    assert "no encontrado" in app.engine_status()


def test_engine_status_reports_non_executable(app, tmp_path):
    engine = tmp_path / "engine"
    engine.write_text("#!/bin/sh\n")
    engine.chmod(0o644)
    app.player.engine_path = engine
    assert "permisos de ejecución" in app.engine_status()


def test_engine_status_none_when_present_and_executable(app, tmp_path):
    engine = tmp_path / "engine"
    engine.write_text("#!/bin/sh\n")
    engine.chmod(0o755)
    app.player.engine_path = engine
    assert app.engine_status() is None


def test_b_on_empty_favorites_returns_home(app):
    go_to_favorites(app)
    assert app.screen == Screen.FAVORITES
    assert app.favorites == []
    app.handle_button("B", True)
    assert app.screen == Screen.HOME


def test_start_on_selected_category_station_persists_favorite_and_feedback(app, store):
    go_to_categories(app)
    app.handle_button("A", True)
    station = app.selected_station()
    app.handle_button("START", True)
    assert station.id in app.favorites
    assert store.load_favorites() == [station.id]
    assert app.favorite_message != ""


def test_uses_persisted_language_when_config_has_a_valid_one(parse_result, store, player, monkeypatch):
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.setenv("LANG", "ja_JP.UTF-8")
    store.update_config(language="fr")
    app = RadioApp(parse_result, store, player)
    assert app.language == "fr"


def test_falls_back_to_system_detection_when_config_has_no_language(
    parse_result, store, player, monkeypatch
):
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    app = RadioApp(parse_result, store, player)
    assert app.language == "de"


def test_falls_back_to_english_when_config_and_system_are_unsupported(
    parse_result, store, player, monkeypatch
):
    import locale

    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.setenv("LANG", "xx_XX.UTF-8")
    monkeypatch.setattr(locale, "getlocale", lambda: (None, None))
    app = RadioApp(parse_result, store, player)
    assert app.language == "en"


def test_set_language_updates_active_language_and_persists(app, store):
    app.set_language("ko")
    assert app.language == "ko"
    assert store.load_config()["language"] == "ko"


def test_set_language_persists_across_reopen(parse_result, store, player):
    app = RadioApp(parse_result, store, player)
    app.set_language("ru")

    reopened = RadioApp(parse_result, store, player)
    assert reopened.language == "ru"


def test_set_language_normalizes_locale_variant(app, store):
    app.set_language("pt_BR.UTF-8")
    assert app.language == "pt"
    assert store.load_config()["language"] == "pt"


def test_set_language_falls_back_to_english_for_invalid_code(app, store):
    app.set_language("not-a-locale")
    assert app.language == "en"
    assert store.load_config()["language"] == "en"


def test_app_t_translates_using_active_language(app):
    app.set_language("es")
    assert app.t("app_title") == "Radio"
    assert app.t("state_error") == "Error"


def test_removing_selected_favorite_keeps_station_index_valid(app, store):
    go_to_categories(app)
    app.handle_button("A", True)
    first, second = app.current_station_list()[0], app.current_station_list()[1]
    app.station_index = 0
    app.handle_button("START", True)
    app.station_index = 1
    app.handle_button("START", True)

    app.open_favorites()
    assert app.current_station_list() == [first, second]
    app.station_index = 1
    app.handle_button("START", True)

    assert app.favorites == [first.id]
    assert app.station_index == 0
    assert app.selected_station() == first


def _source_result(source, name, group="Imported"):
    return PlaylistLoadResult(
        parse_m3u(
            '#EXTINF:-1 group-title="%s",%s\nhttps://example.com/%s.mp3\n'
            % (group, name, name.lower().replace(" ", "-"))
        ),
        source,
        "default" if source.kind == PlaylistSourceKind.DEFAULT else "local",
    )


def test_playlist_settings_is_first_menu_item_and_back_returns_to_menu(app):
    app.open_settings()
    assert SETTINGS_MENU_ITEMS[0] == "playlist"
    app.handle_button("A", True)
    assert app.settings_view == SettingsView.PLAYLIST
    app.handle_button("B", True)
    assert app.settings_view == SettingsView.MENU
    assert app.screen == Screen.SETTINGS


def test_selecting_default_source_reloads_immediately_and_persists(parse_result, store, player):
    sources = [DEFAULT_SOURCE]
    default_result = _source_result(DEFAULT_SOURCE, "Bundled")
    app = RadioApp(
        parse_result,
        store,
        player,
        playlist_loader=lambda source: default_result,
        playlist_source_lister=lambda: sources,
    )
    app.open_settings()
    app.handle_button("A", True)
    app.handle_button("A", True)

    assert [station.name for station in app.parse_result.stations] == ["Bundled"]
    assert app.playlist_source == DEFAULT_SOURCE
    assert store.load_config()["playlist_source"] == {"kind": "default", "id": ""}


def test_selecting_local_source_refreshes_catalogue_and_prunes_history(parse_result, store, player):
    local = PlaylistSource(PlaylistSourceKind.LOCAL, "mine.m3u")
    local_result = _source_result(local, "Local station")
    old_station = parse_result.stations[0]
    store.save_favorites([old_station.id])
    store.save_recents([old_station.id])
    app = RadioApp(
        parse_result,
        store,
        player,
        playlist_loader=lambda source: local_result,
        playlist_source_lister=lambda: [DEFAULT_SOURCE, local],
    )

    app.open_settings()
    app.handle_button("A", True)
    app.move_settings(1)
    app.handle_button("A", True)

    assert app.playlist_source == local
    assert app.playlist_source_status == "local"
    assert app.groups == ["Imported"]
    assert app.favorites == []
    assert app.recents == []
    assert store.load_config()["playlist_source"] == {"kind": "local", "id": "mine.m3u"}


def test_selecting_remote_source_uses_loader_result_and_exposes_status(parse_result, store, player):
    remote = PlaylistSource(PlaylistSourceKind.REMOTE, "https://example.com/radio.m3u")
    remote_result = PlaylistLoadResult(
        _source_result(remote, "Remote station").parse_result, remote, "cached", "network unavailable"
    )
    app = RadioApp(
        parse_result,
        store,
        player,
        playlist_loader=lambda source: remote_result,
        playlist_source_lister=lambda: [DEFAULT_SOURCE, remote],
    )

    app.open_settings()
    app.handle_button("A", True)
    app.move_settings(1)
    app.handle_button("A", True)

    assert app.playlist_source == remote
    assert app.playlist_source_status == "cached"
    assert app.playlist_source_error == "network unavailable"
    assert app.available_playlist_sources == [DEFAULT_SOURCE, remote]
    assert store.load_config()["playlist_source"] == {"kind": "remote", "id": remote.id}


def test_stale_source_fallback_persists_default_and_stops_removed_station(parse_result, store, player):
    stale = PlaylistSource(PlaylistSourceKind.LOCAL, "gone.m3u")
    fallback = PlaylistLoadResult(
        _source_result(DEFAULT_SOURCE, "Fallback").parse_result,
        DEFAULT_SOURCE,
        "fallback",
        "local playlist not found",
    )
    app = RadioApp(
        parse_result,
        store,
        player,
        playlist_loader=lambda source: fallback,
        playlist_source_lister=lambda: [DEFAULT_SOURCE, stale],
    )
    go_to_categories(app)
    app.handle_button("A", True)
    app.handle_button("A", True)
    assert app.screen == Screen.NOW_PLAYING

    app.select_playlist_source(stale)

    assert app.playlist_source == DEFAULT_SOURCE
    assert app.playlist_source_status == "fallback"
    assert app.playlist_source_error == "local playlist not found"
    assert app.now_playing is None
    assert app.screen == Screen.HOME
    assert player.stop_calls == 1
    assert store.load_config()["playlist_source"] == {"kind": "default", "id": ""}


def test_startup_stale_source_fallback_replaces_persisted_selection(parse_result, store, player):
    stale = PlaylistSource(PlaylistSourceKind.LOCAL, "gone.m3u")
    store.update_config(playlist_source=stale.config_value())
    fallback = PlaylistLoadResult(
        _source_result(DEFAULT_SOURCE, "Fallback").parse_result,
        DEFAULT_SOURCE,
        "fallback",
        "local playlist not found",
    )

    app = RadioApp(
        fallback.parse_result,
        store,
        player,
        playlist_load_result=fallback,
        playlist_source_lister=lambda: [DEFAULT_SOURCE],
    )

    assert app.playlist_source == DEFAULT_SOURCE
    assert app.playlist_source_error == "local playlist not found"
    assert store.load_config()["playlist_source"] == DEFAULT_SOURCE.config_value()
