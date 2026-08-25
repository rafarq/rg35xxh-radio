"""Desktop-testable navigation/state machine driving the whole app.

Deliberately free of any SDL2/Pillow import so unit tests exercise
button handling, navigation, favorites/recents and playback wiring
without a display, audio device or the real ffmpeg engine
(``radio/ui/`` imports graphics lazily and only *reads* this object each
frame; it never owns app logic itself).

Button semantics (PLAN.md Fases 2-4):

* ``A``      — on Home, open the selected card (Favoritos/Categorías/
               Recientes); enter a category / play the selected station /
               retry after an error on the now-playing screen otherwise.
* ``B``      — always means back: returns to the immediately previous
               meaningful screen (tracked on a back stack), stopping
               playback first if leaving now-playing; a safe no-op on Home.
* ``SELECT`` — jump Home from anywhere (clears the back stack).
* ``START``  — toggle favorite for the selected/now-playing station; never
               acts on the Home dashboard (there is no single station
               selected there).
* ``X``      — pause/resume playback on now-playing; on a station list
               (Stations/Favorites/Recents) plays the selected station,
               same as ``A``; a safe no-op on Home/Categories. Deliberately
               *not* a Favorites/Recents shortcut.
* ``Y``      — unused; always a safe no-op.
* ``L1``/``R1`` — previous/next category.
* ``DY``     — move the list cursor (categories or stations); on Home,
               also moves the selected card left/right (single row).
* ``DX``     — on Home, move the selected card left/right; does nothing
               on now-playing (volume is no longer on DX).
* ``VOLUME_DOWN``/``VOLUME_UP`` — the dedicated physical volume buttons.
               Adjust volume by ``VOLUME_STEP`` from every screen, not
               just now-playing; they never navigate.
* ``MENU``   — request a clean exit (stops playback first).

The launch screen is ``Screen.HOME``: a dashboard of three large cards
(Favoritos/Categorías/Recientes) that replaced the old Favorites-first
launch list.
"""

from __future__ import annotations

import enum
import os
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from radio.audio.volume import SystemVolume
from radio.i18n import (
    DEFAULT_LANGUAGE,
    SUPPORTED_LANGUAGES,
    normalize_language,
    resolve_system_language,
    t,
)
from radio.playback.controller import PlaybackError, PlaybackState, PlayerController
from radio.playlist import sources as playlist_sources
from radio.playlist.model import Station
from radio.playlist.parser import ParseResult
from radio.playlist.sources import PlaylistSource, PlaylistSourceKind
from radio.storage import DataStore

VOLUME_STEP = 5
VOLUME_MIN = 0
VOLUME_MAX = 100
CONNECT_TIMEOUT_SECONDS = 10.0

# Single-shot (non-repeatable) buttons: acting on their autorepeat would
# spam favorite toggles, re-enter screens, etc.
_SINGLE_SHOT_BUTTONS = {"A", "B", "SELECT", "START", "MENU", "X"}


class Screen(str, enum.Enum):
    HOME = "home"
    CATEGORIES = "categories"
    STATIONS = "stations"
    FAVORITES = "favorites"
    RECENTS = "recents"
    NOW_PLAYING = "now_playing"
    SETTINGS = "settings"


HOME_CARDS = (Screen.FAVORITES, Screen.CATEGORIES, Screen.RECENTS, Screen.SETTINGS)

# Stable display order for the settings language picker: the 15 supported
# locale codes, in the same order as radio.i18n.SUPPORTED_LANGUAGES.
LANGUAGE_CODES = list(SUPPORTED_LANGUAGES)


class SettingsView(str, enum.Enum):
    """Sub-view within :attr:`Screen.SETTINGS`.

    Settings is a single ``Screen`` with its own tiny nested navigation
    (menu -> language picker / credits -> back to menu -> back to Home) so
    the top-level back stack only ever sees one ``Screen.SETTINGS`` entry.
    """

    MENU = "menu"
    PLAYLIST = "playlist"
    LANGUAGE = "language"
    CREDITS = "credits"


# The Settings menu's selectable rows, in display order.  Keep playlist
# selection first: it is the only setting that changes the station catalogue.
SETTINGS_MENU_ITEMS = ("playlist", "language", "credits")


class RadioApp:
    """Owns navigation, selection, playback wiring and persistence."""

    def __init__(
        self,
        parse_result: ParseResult,
        store: DataStore,
        player: PlayerController,
        now: Optional[Callable[[], float]] = None,
        volume_control: Optional[SystemVolume] = None,
        playlist_load_result: Optional[playlist_sources.PlaylistLoadResult] = None,
        playlist_loader: Optional[
            Callable[[PlaylistSource], playlist_sources.PlaylistLoadResult]
        ] = None,
        playlist_source_lister: Optional[Callable[[], List[PlaylistSource]]] = None,
    ):
        self.store = store
        self.player = player
        self.volume_control = volume_control
        self._now = now or time.monotonic
        # The optional dependencies keep the long-standing three-argument
        # constructor useful to tests and callers that only have a ParseResult.
        self._playlist_loader = playlist_loader or playlist_sources.load_source
        self._playlist_source_lister = playlist_source_lister or playlist_sources.list_available_sources
        self.playlist_load_result = playlist_load_result or playlist_sources.PlaylistLoadResult(
            parse_result=parse_result,
            source=playlist_sources.DEFAULT_SOURCE,
            status="default",
        )
        self.playlist_source: PlaylistSource = self.playlist_load_result.source
        self.playlist_source_status: str = self.playlist_load_result.status
        self.playlist_source_error: Optional[str] = self.playlist_load_result.error
        self.playlist_sources: List[PlaylistSource] = []
        # Aliases make the renderer-facing state explicit without forcing the
        # UI to know how discovery or loading works.
        self.available_playlist_sources: List[PlaylistSource] = self.playlist_sources
        self._refresh_playlist_sources()

        self._set_station_catalogue(parse_result)

        self.favorites: List[str] = store.load_favorites()
        self.recents: List[str] = store.load_recents()
        self._prune_station_history()

        config = store.load_config()

        # If resolution fell back (for example a persisted local file was
        # removed), remember the effective safe source so every launch does
        # not retry a known-stale selection.
        if (
            playlist_sources.source_from_value(config.get("playlist_source")).config_value()
            != self.playlist_source.config_value()
        ):
            self.store.update_config(playlist_source=self.playlist_source.config_value())
            config["playlist_source"] = self.playlist_source.config_value()

        # DataStore.load_config() already normalizes this to a supported
        # code or None; a valid persisted language always wins over
        # system detection, per the current language-resolution rule.
        self.language: str = config.get("language") or resolve_system_language()

        self.volume: int = config.get("volume", 60)
        if self.volume_control is not None:
            hardware_volume = self.volume_control.get_volume_percent()
            if hardware_volume is not None:
                self.volume = hardware_volume
                self.store.update_config(volume=self.volume)

        self.screen: Screen = Screen.HOME
        self._back_stack: List[Screen] = []
        self.home_index: int = 0
        self.category_index: int = 0
        self.station_index: int = 0
        self.settings_index: int = 0
        self.settings_view: SettingsView = SettingsView.MENU
        self.status_message: str = ""
        self.favorite_message: str = ""
        self.settings_message: str = ""

        self.now_playing: Optional[Station] = None
        self.connect_started_at: Optional[float] = None
        self.should_exit: bool = False

        last_group = config.get("last_group")
        if last_group and last_group in self.groups:
            self.category_index = self.groups.index(last_group)
        last_station_id = config.get("last_station_id")
        if last_station_id and last_group:
            for idx, station in enumerate(self._stations_for_group(last_group)):
                if station.id == last_station_id:
                    self.station_index = idx
                    break

    def _set_station_catalogue(self, parse_result: ParseResult) -> None:
        """Replace the active catalogue while retaining the UI's simple indexes."""
        self.parse_result = parse_result
        self.stations_by_id: Dict[str, Station] = {s.id: s for s in parse_result.stations}
        self.stations_by_group: Dict[str, List[Station]] = {}
        for station in parse_result.stations:
            self.stations_by_group.setdefault(station.group, []).append(station)
        self.groups: List[str] = list(parse_result.groups)

    def _refresh_playlist_sources(self) -> None:
        """Discover choices defensively; a bad user directory must not break Settings."""
        try:
            sources = list(self._playlist_source_lister())
        except Exception:
            sources = []
        if not any(source.kind == PlaylistSourceKind.DEFAULT for source in sources):
            sources.insert(0, playlist_sources.DEFAULT_SOURCE)
        self.playlist_sources = sources
        self.available_playlist_sources = self.playlist_sources

    def _playlist_source_index(self) -> int:
        for index, source in enumerate(self.playlist_sources):
            if source.kind == self.playlist_source.kind and source.id == self.playlist_source.id:
                return index
        return 0

    def _prune_station_history(self) -> None:
        """Drop ids that cannot be selected from the newly loaded catalogue."""
        valid_ids = self.stations_by_id
        favorites = [station_id for station_id in self.favorites if station_id in valid_ids]
        recents = [station_id for station_id in self.recents if station_id in valid_ids]
        if favorites != self.favorites:
            self.favorites = favorites
            self.store.save_favorites(favorites)
        if recents != self.recents:
            self.recents = recents
            self.store.save_recents(recents)

    def select_playlist_source(self, source: PlaylistSource) -> None:
        """Load and immediately apply a Settings selection without risking stale state."""
        try:
            result = self._playlist_loader(source)
        except Exception as exc:
            # Custom/injected loaders get the same no-crash guarantee as the
            # production resolver. Keep the currently working catalogue.
            self.playlist_source_error = str(exc)
            self.playlist_source_status = "fallback"
            return

        self.playlist_load_result = result
        self.playlist_source = result.source
        self.playlist_source_status = result.status
        self.playlist_source_error = result.error
        self._set_station_catalogue(result.parse_result)
        self._prune_station_history()
        self.category_index = 0
        self.station_index = 0

        if self.now_playing is not None:
            replacement = self.stations_by_id.get(self.now_playing.id)
            if replacement is None:
                self.stop_playback()
                if self.screen == Screen.NOW_PLAYING:
                    self.go_home()
            else:
                self.now_playing = replacement

        self.store.update_config(
            playlist_source=result.source.config_value(),
            last_group=None,
            last_station_id=None,
        )
        self._refresh_playlist_sources()
        self.settings_index = self._playlist_source_index()

    # -- read-only helpers for the UI layer ---------------------------------

    def current_group(self) -> Optional[str]:
        if not self.groups:
            return None
        return self.groups[self.category_index % len(self.groups)]

    def _stations_for_group(self, group: str) -> List[Station]:
        return self.stations_by_group.get(group, [])

    def current_station_list(self) -> List[Station]:
        if self.screen == Screen.FAVORITES:
            return [self.stations_by_id[sid] for sid in self.favorites if sid in self.stations_by_id]
        if self.screen == Screen.RECENTS:
            return [self.stations_by_id[sid] for sid in self.recents if sid in self.stations_by_id]
        group = self.current_group()
        return self._stations_for_group(group) if group else []

    def selected_station(self) -> Optional[Station]:
        stations = self.current_station_list()
        if not stations:
            return None
        return stations[self.station_index % len(stations)]

    def is_favorite(self, station: Station) -> bool:
        return station.id in self.favorites

    # -- engine health --------------------------------------------------

    def engine_status(self) -> Optional[str]:
        """Actionable message if the bundled engine can't possibly run, else None."""
        engine_path = self.player.engine_path
        if not engine_path.exists():
            return self.t("engine_missing", path=engine_path)
        if not os.access(engine_path, os.X_OK):
            return self.t("engine_not_executable", path=engine_path)
        return None

    # -- input dispatch ------------------------------------------------------

    def handle_button(self, name: str, pressed: bool, repeat: bool = False) -> None:
        if not pressed:
            return
        if repeat and name in _SINGLE_SHOT_BUTTONS:
            return

        if name == "MENU":
            self.request_exit()
        elif name == "SELECT":
            self.go_home()
        elif name == "L1":
            self.move_category(-1)
        elif name == "R1":
            self.move_category(1)
        elif name == "A":
            self.activate()
        elif name == "B":
            self.go_back()
        elif name == "START":
            self.toggle_favorite()
        elif name == "X":
            self.handle_x()
        elif name == "VOLUME_UP":
            self.set_volume(VOLUME_STEP)
        elif name == "VOLUME_DOWN":
            self.set_volume(-VOLUME_STEP)

    def handle_axis(self, name: str, value: int) -> None:
        if value == 0:
            return
        if name == "DY":
            if self.screen == Screen.HOME:
                self.move_home(1 if value > 0 else -1)
            elif self.screen == Screen.CATEGORIES:
                self.move_category(1 if value > 0 else -1)
            elif self.screen in (Screen.STATIONS, Screen.FAVORITES, Screen.RECENTS):
                self.move_station(1 if value > 0 else -1)
            elif self.screen == Screen.SETTINGS:
                self.move_settings(1 if value > 0 else -1)
        elif name == "DX":
            if self.screen == Screen.HOME:
                self.move_home(1 if value > 0 else -1)

    # -- navigation -----------------------------------------------------

    def _goto(self, screen: Screen) -> None:
        """Switch screens, remembering where to return to on ``B``."""
        if screen != self.screen:
            self._back_stack.append(self.screen)
        self.screen = screen

    def enter_stations(self) -> None:
        if not self.groups:
            return
        self._goto(Screen.STATIONS)
        self.station_index = 0
        self.favorite_message = ""

    def open_categories(self) -> None:
        self._goto(Screen.CATEGORIES)
        self.favorite_message = ""

    def open_favorites(self) -> None:
        self._goto(Screen.FAVORITES)
        self.station_index = 0
        self.favorite_message = ""

    def open_recents(self) -> None:
        self._goto(Screen.RECENTS)
        self.station_index = 0
        self.favorite_message = ""

    def open_settings(self) -> None:
        self._goto(Screen.SETTINGS)
        self.settings_view = SettingsView.MENU
        self.settings_index = 0
        self.settings_message = ""

    def open_playlist_settings(self) -> None:
        self._refresh_playlist_sources()
        self.settings_view = SettingsView.PLAYLIST
        self.settings_index = self._playlist_source_index()
        self.settings_message = ""

    def go_home(self) -> None:
        self.screen = Screen.HOME
        self._back_stack.clear()
        self.favorite_message = ""
        self.settings_message = ""

    def go_back(self) -> None:
        if self.screen == Screen.HOME:
            return  # top-level: safe no-op.
        if self.screen == Screen.SETTINGS and self.settings_view != SettingsView.MENU:
            # Language picker / Credits back to the Settings menu, without
            # popping the outer back stack (Settings is still one screen).
            self.settings_view = SettingsView.MENU
            self.settings_index = 0
            self.settings_message = ""
            return
        if self.screen == Screen.NOW_PLAYING:
            self.stop_playback()
        previous = self._back_stack.pop() if self._back_stack else Screen.HOME
        self.screen = previous
        if previous == Screen.CATEGORIES:
            self.station_index = 0
        self.favorite_message = ""
        self.settings_message = ""

    def move_home(self, delta: int) -> None:
        if self.screen != Screen.HOME:
            return
        self.home_index = (self.home_index + delta) % len(HOME_CARDS)

    def move_category(self, delta: int) -> None:
        if self.screen not in (Screen.CATEGORIES, Screen.STATIONS):
            return
        if not self.groups:
            return
        self.category_index = (self.category_index + delta) % len(self.groups)
        self.station_index = 0

    def move_station(self, delta: int) -> None:
        stations = self.current_station_list()
        if not stations:
            return
        self.station_index = (self.station_index + delta) % len(stations)

    def move_settings(self, delta: int) -> None:
        if self.screen != Screen.SETTINGS:
            return
        if self.settings_view == SettingsView.MENU:
            self.settings_index = (self.settings_index + delta) % len(SETTINGS_MENU_ITEMS)
        elif self.settings_view == SettingsView.PLAYLIST:
            if self.playlist_sources:
                self.settings_index = (self.settings_index + delta) % len(self.playlist_sources)
        elif self.settings_view == SettingsView.LANGUAGE:
            self.settings_index = (self.settings_index + delta) % len(LANGUAGE_CODES)
        # CREDITS has nothing to scroll: a safe no-op.

    def activate(self) -> None:
        if self.screen == Screen.HOME:
            target = HOME_CARDS[self.home_index]
            if target == Screen.FAVORITES:
                self.open_favorites()
            elif target == Screen.RECENTS:
                self.open_recents()
            elif target == Screen.SETTINGS:
                self.open_settings()
            else:
                self.open_categories()
        elif self.screen == Screen.CATEGORIES:
            self.enter_stations()
        elif self.screen in (Screen.STATIONS, Screen.FAVORITES, Screen.RECENTS):
            self.play_selected()
        elif self.screen == Screen.SETTINGS:
            if self.settings_view == SettingsView.MENU:
                selected = SETTINGS_MENU_ITEMS[self.settings_index % len(SETTINGS_MENU_ITEMS)]
                if selected == "playlist":
                    self.open_playlist_settings()
                    return
                self.settings_view = SettingsView.LANGUAGE if selected == "language" else SettingsView.CREDITS
                self.settings_index = (
                    LANGUAGE_CODES.index(self.language)
                    if self.settings_view == SettingsView.LANGUAGE
                    else 0
                )
                self.settings_message = ""
            elif self.settings_view == SettingsView.PLAYLIST:
                if self.playlist_sources:
                    self.select_playlist_source(
                        self.playlist_sources[self.settings_index % len(self.playlist_sources)]
                    )
            elif self.settings_view == SettingsView.LANGUAGE:
                self.save_selected_language()
        elif self.screen == Screen.NOW_PLAYING:
            if (
                self.player.state in (PlaybackState.ERROR, PlaybackState.STOPPED)
                and self.now_playing is not None
            ):
                self._play_station(self.now_playing)

    def handle_x(self) -> None:
        """X: pause/resume on now-playing; starts the selection on station lists.

        A safe no-op on Home/Categories, where no single station is playing
        or selected. Never used as a Favorites/Recents shortcut.
        """
        if self.screen == Screen.NOW_PLAYING:
            self.toggle_pause()
        elif self.screen in (Screen.STATIONS, Screen.FAVORITES, Screen.RECENTS):
            self.play_selected()

    def toggle_pause(self) -> None:
        try:
            self.player.toggle_pause()
        except PlaybackError:
            pass

    # -- playback ---------------------------------------------------------

    def _play_station(self, station: Station) -> None:
        try:
            self.player.start(station.url)
        except PlaybackError as exc:
            self.status_message = self.t("status_playback_start_error", error=exc)
            self.now_playing = station
            self.connect_started_at = None
            self._goto(Screen.NOW_PLAYING)
            return

        self.now_playing = station
        self.connect_started_at = self._now()
        self.status_message = self.t("status_connecting")
        self._goto(Screen.NOW_PLAYING)
        self.recents = self.store.add_recent(station.id)
        self.store.update_config(last_group=station.group, last_station_id=station.id)

    def play_selected(self) -> None:
        station = self.selected_station()
        if station is not None:
            self._play_station(station)

    def stop_playback(self) -> None:
        try:
            self.player.stop()
        except Exception:
            pass
        self.now_playing = None
        self.connect_started_at = None
        self.status_message = ""

    def toggle_favorite(self) -> None:
        station = None
        if self.screen == Screen.NOW_PLAYING:
            station = self.now_playing
        elif self.screen in (Screen.STATIONS, Screen.FAVORITES, Screen.RECENTS):
            station = self.selected_station()
        # HOME and CATEGORIES have no single station selected: START is a no-op there.
        if station is None:
            return
        if station.id in self.favorites:
            self.favorites = self.store.remove_favorite(station.id)
            self.favorite_message = self.t("favorite_removed", name=station.name)
        else:
            self.favorites = self.store.add_favorite(station.id)
            self.favorite_message = self.t("favorite_added", name=station.name)

        if self.screen == Screen.FAVORITES:
            count = len(self.favorites)
            self.station_index = 0 if count == 0 else min(self.station_index, count - 1)

    def set_volume(self, delta: int) -> None:
        new_volume = max(VOLUME_MIN, min(VOLUME_MAX, self.volume + delta))
        if self.volume_control is not None:
            if not self.volume_control.set_volume_percent(new_volume):
                self.status_message = self.t("status_volume_hw_error")
                return
        self.volume = new_volume
        self.store.update_config(volume=self.volume)

    def set_language(self, language: str) -> None:
        """Set the active UI language and persist it (invalid codes fall back to English)."""
        self.language = normalize_language(language) or DEFAULT_LANGUAGE
        self.store.update_config(language=self.language)

    def save_selected_language(self) -> None:
        """Persist the language currently highlighted in the Settings list.

        Called on ``A`` while on :attr:`Screen.SETTINGS`; every screen reads
        ``app.language``/``app.t`` fresh each frame, so the very next redraw
        reflects the new language with no extra signalling needed.
        """
        language = LANGUAGE_CODES[self.settings_index % len(LANGUAGE_CODES)]
        self.set_language(language)
        self.settings_message = self.t("settings_saved")

    def t(self, key: str, **kwargs: object) -> str:
        """Translate ``key`` for the app's current active language."""
        return t(key, self.language, **kwargs)

    def request_exit(self) -> None:
        self.stop_playback()
        self.should_exit = True

    # -- per-frame bookkeeping -----------------------------------------

    def tick(self) -> None:
        """Non-blocking per-frame poll of playback state; call every frame."""
        if self.player.state == PlaybackState.STARTING and self.connect_started_at is not None:
            if self._now() - self.connect_started_at > CONNECT_TIMEOUT_SECONDS:
                self.player.stop()
                self.status_message = self.t("status_connect_timeout")
                self.connect_started_at = None
                return

        state = self.player.poll()
        if state == PlaybackState.ERROR:
            if not self.status_message:
                self.status_message = self.t("status_playback_error")
            self.connect_started_at = None
        elif state == PlaybackState.PLAYING:
            self.status_message = ""
            self.connect_started_at = None
        elif state == PlaybackState.STOPPED:
            self.connect_started_at = None
