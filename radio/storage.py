"""JSON persistence for favorites, recents and config.

All writes are atomic (write to a temp file in the same directory, then
``os.replace``) so a crash or power loss mid-write on the RG35XX H's SD
card never leaves a half-written, corrupt JSON file behind. Every path is
validated to resolve underneath the target data directory (REQUISITOS.md
§1: the app must never write outside ``radio/``).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List

MAX_RECENTS = 20

DEFAULT_CONFIG: Dict[str, Any] = {
    "volume": 60,
    "last_group": None,
    "last_station_id": None,
    "language": None,
    "playlist_source": None,
}


class DataStore:
    """Owns favorites.json, recent.json and config.json under one data dir."""

    def __init__(self, data_dir: "str | Path"):
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.favorites_path = self.data_dir / "favorites.json"
        self.recent_path = self.data_dir / "recent.json"
        self.config_path = self.data_dir / "config.json"

    # -- generic atomic read/write -----------------------------------

    def _resolve_in_data_dir(self, path: Path) -> Path:
        resolved = path.resolve()
        if self.data_dir != resolved and self.data_dir not in resolved.parents:
            raise ValueError(f"refusing to write outside data dir: {resolved}")
        return resolved

    def _read_json(self, path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    def _write_json_atomic(self, path: Path, value: Any) -> None:
        path = self._resolve_in_data_dir(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(value, fh, ensure_ascii=False, indent=2, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    # -- favorites ------------------------------------------------------

    def load_favorites(self) -> List[str]:
        return self._read_json(self.favorites_path, [])

    def save_favorites(self, station_ids: List[str]) -> None:
        self._write_json_atomic(self.favorites_path, list(station_ids))

    def add_favorite(self, station_id: str) -> List[str]:
        favorites = self.load_favorites()
        if station_id not in favorites:
            favorites.append(station_id)
            self.save_favorites(favorites)
        return favorites

    def remove_favorite(self, station_id: str) -> List[str]:
        favorites = self.load_favorites()
        if station_id in favorites:
            favorites.remove(station_id)
            self.save_favorites(favorites)
        return favorites

    # -- recents ----------------------------------------------------------

    def load_recents(self) -> List[str]:
        return self._read_json(self.recent_path, [])

    def save_recents(self, station_ids: List[str]) -> None:
        self._write_json_atomic(self.recent_path, list(station_ids)[:MAX_RECENTS])

    def add_recent(self, station_id: str) -> List[str]:
        """Push a station to the top of recents, deduplicated, capped at MAX_RECENTS."""
        recents = self.load_recents()
        if station_id in recents:
            recents.remove(station_id)
        recents.insert(0, station_id)
        recents = recents[:MAX_RECENTS]
        self.save_recents(recents)
        return recents

    # -- config -------------------------------------------------------------

    def load_config(self) -> Dict[str, Any]:
        from radio.i18n import normalize_language
        from radio.playlist.sources import normalize_playlist_source_value

        config = dict(DEFAULT_CONFIG)
        config.update(self._read_json(self.config_path, {}))
        # A corrupt/foreign/unsupported language value must never crash
        # startup or silently propagate; treat it as "not configured" so
        # the caller falls back to system-language detection.
        config["language"] = normalize_language(config.get("language"))
        # Same treatment for the selected playlist source: a corrupt/stale
        # value (e.g. a path-traversal attempt, a non-http(s) URL) is never
        # trusted as-is; it normalizes to None so the caller falls back to
        # the bundled default playlist.
        config["playlist_source"] = normalize_playlist_source_value(config.get("playlist_source"))
        return config

    def save_config(self, config: Dict[str, Any]) -> None:
        self._write_json_atomic(self.config_path, config)

    def update_config(self, **changes: Any) -> Dict[str, Any]:
        config = self.load_config()
        config.update(changes)
        self.save_config(config)
        return config
