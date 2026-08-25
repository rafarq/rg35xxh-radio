import json

import pytest

from radio.storage import MAX_RECENTS, DataStore


@pytest.fixture
def store(tmp_path):
    return DataStore(tmp_path / "radio_data")


def test_data_dir_created_below_given_path(tmp_path):
    data_dir = tmp_path / "nested" / "radio_data"
    DataStore(data_dir)
    assert data_dir.is_dir()


def test_favorites_round_trip(store):
    assert store.load_favorites() == []
    store.add_favorite("station-1")
    store.add_favorite("station-2")
    assert store.load_favorites() == ["station-1", "station-2"]


def test_add_favorite_is_idempotent(store):
    store.add_favorite("station-1")
    store.add_favorite("station-1")
    assert store.load_favorites() == ["station-1"]


def test_remove_favorite(store):
    store.add_favorite("station-1")
    store.add_favorite("station-2")
    store.remove_favorite("station-1")
    assert store.load_favorites() == ["station-2"]


def test_favorites_survive_reopen(tmp_path):
    data_dir = tmp_path / "radio_data"
    DataStore(data_dir).add_favorite("station-1")
    reopened = DataStore(data_dir)
    assert reopened.load_favorites() == ["station-1"]


def test_recents_are_most_recent_first(store):
    store.add_recent("a")
    store.add_recent("b")
    store.add_recent("c")
    assert store.load_recents() == ["c", "b", "a"]


def test_recents_deduplicate_and_move_to_top(store):
    store.add_recent("a")
    store.add_recent("b")
    store.add_recent("a")
    assert store.load_recents() == ["a", "b"]


def test_recents_capped_at_max(store):
    for i in range(MAX_RECENTS + 10):
        store.add_recent(f"station-{i}")
    recents = store.load_recents()
    assert len(recents) == MAX_RECENTS
    assert recents[0] == f"station-{MAX_RECENTS + 9}"


def test_config_defaults_when_missing(store):
    config = store.load_config()
    assert config["volume"] == 60
    assert config["last_group"] is None
    assert config["language"] is None


def test_config_language_round_trips(store):
    store.update_config(language="fr")
    reopened = DataStore(store.data_dir)
    assert reopened.load_config()["language"] == "fr"


def test_config_language_survives_zh_hans_dash(store):
    store.update_config(language="zh-Hans")
    reopened = DataStore(store.data_dir)
    assert reopened.load_config()["language"] == "zh-Hans"


def test_config_invalid_language_normalizes_to_none(store):
    store.update_config(language="not-a-real-locale")
    reopened = DataStore(store.data_dir)
    assert reopened.load_config()["language"] is None


def test_config_language_locale_variant_normalizes(store):
    store.update_config(language="pt_BR.UTF-8")
    reopened = DataStore(store.data_dir)
    assert reopened.load_config()["language"] == "pt"


def test_config_update_persists(store):
    store.update_config(volume=80, last_group="News")
    reopened = DataStore(store.data_dir)
    config = reopened.load_config()
    assert config["volume"] == 80
    assert config["last_group"] == "News"


def test_writes_are_valid_json_on_disk(store):
    store.add_favorite("station-1")
    raw = store.favorites_path.read_text(encoding="utf-8")
    assert json.loads(raw) == ["station-1"]


def test_write_does_not_leave_temp_files_behind(store):
    store.add_favorite("station-1")
    store.update_config(volume=10)
    store.add_recent("x")
    leftovers = [p for p in store.data_dir.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


def test_refuses_to_write_outside_data_dir(store, tmp_path):
    outside = tmp_path / "outside.json"
    with pytest.raises(ValueError):
        store._write_json_atomic(outside, {"x": 1})
    assert not outside.exists()


def test_unicode_names_round_trip(store):
    store.add_favorite("Emisora Núñez\x1fhttps://x/á")
    reopened = DataStore(store.data_dir)
    assert reopened.load_favorites() == ["Emisora Núñez\x1fhttps://x/á"]
