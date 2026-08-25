import locale

import pytest

from radio.i18n import (
    DEFAULT_LANGUAGE,
    SUPPORTED_LANGUAGES,
    TRANSLATIONS,
    normalize_language,
    resolve_system_language,
    t,
)

EXPECTED_LOCALES = {
    "en", "es", "zh-Hans", "hi", "fr", "ar", "bn", "pt",
    "ru", "ur", "id", "de", "ja", "tr", "ko",
}


def test_exactly_the_15_required_locales_are_supported():
    assert set(SUPPORTED_LANGUAGES) == EXPECTED_LOCALES
    assert set(TRANSLATIONS) == EXPECTED_LOCALES


def test_default_language_is_english():
    assert DEFAULT_LANGUAGE == "en"


def test_every_locale_has_a_non_empty_native_name():
    for code, name in SUPPORTED_LANGUAGES.items():
        assert isinstance(name, str) and name.strip(), code


@pytest.mark.parametrize("language", sorted(EXPECTED_LOCALES))
def test_translation_bundle_is_complete_for_every_locale(language):
    """Every locale must translate every key present in the English bundle."""
    english_keys = set(TRANSLATIONS["en"])
    bundle_keys = set(TRANSLATIONS[language])
    missing = english_keys - bundle_keys
    assert not missing, f"{language} missing keys: {sorted(missing)}"


@pytest.mark.parametrize("language", sorted(EXPECTED_LOCALES))
def test_translation_values_are_non_empty_strings(language):
    for key, value in TRANSLATIONS[language].items():
        assert isinstance(value, str) and value.strip(), (language, key)


def test_every_locale_has_the_complete_playlist_source_ui_bundle():
    required = {
        "settings_menu_playlists", "playlist_source_default", "playlist_source_local",
        "playlist_source_remote", "playlist_picker_title", "playlist_picker_hint",
        "playlist_status_default", "playlist_status_local", "playlist_status_fresh",
        "playlist_status_cached", "playlist_status_fallback", "playlist_status_error",
        "playlist_list_empty", "playlist_list_help",
    }
    for language in EXPECTED_LOCALES:
        assert required <= set(TRANSLATIONS[language])


def test_t_returns_translated_string_for_supported_language():
    assert t("app_title", "es") == "Radio"
    assert t("state_playing", "fr") == "En direct"


def test_t_formats_placeholders():
    result = t("favorite_added", "en", name="Alpha News")
    assert result == "Added to favorites: Alpha News"


def test_t_falls_back_to_english_for_unsupported_language():
    assert t("app_title", "xx-unsupported") == t("app_title", "en")


def test_t_falls_back_to_english_for_none_language():
    assert t("state_error", None) == t("state_error", "en")


def test_t_falls_back_to_key_itself_for_unknown_key():
    assert t("this_key_does_not_exist", "en") == "this_key_does_not_exist"


def test_t_missing_placeholder_returns_unformatted_text_without_crashing():
    # Deliberately omit the {name} kwarg the key needs.
    result = t("favorite_added", "en")
    assert result == "Added to favorites: {name}"


# -- normalize_language -----------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("en", "en"),
        ("es", "es"),
        ("es_ES", "es"),
        ("es_ES.UTF-8", "es"),
        ("es-ES", "es"),
        ("pt_BR", "pt"),
        ("pt_PT.UTF-8", "pt"),
        ("zh_Hans", "zh-Hans"),
        ("zh_CN", "zh-Hans"),
        ("zh_TW", "zh-Hans"),
        ("zh", "zh-Hans"),
        ("fr_FR@euro", "fr"),
        ("DE_DE", "de"),
        ("ja_JP.UTF-8", "ja"),
        ("ko_KR", "ko"),
        ("tr_TR", "tr"),
        ("ru_RU", "ru"),
        ("hi_IN", "hi"),
        ("bn_BD", "bn"),
        ("ur_PK", "ur"),
        ("id_ID", "id"),
        ("ar_SA", "ar"),
    ],
)
def test_normalize_language_maps_locale_variants(raw, expected):
    assert normalize_language(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "  ", "C", "POSIX", "xx", "klingon", "zzz_ZZ"])
def test_normalize_language_returns_none_for_unsupported_or_empty(raw):
    assert normalize_language(raw) is None


# -- resolve_system_language -------------------------------------------------


def _clear_locale_env(monkeypatch):
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        monkeypatch.delenv(var, raising=False)


def test_resolve_system_language_reads_lang(monkeypatch):
    _clear_locale_env(monkeypatch)
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    assert resolve_system_language() == "fr"


def test_resolve_system_language_prefers_lc_all_over_lang(monkeypatch):
    _clear_locale_env(monkeypatch)
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    monkeypatch.setenv("LC_ALL", "ja_JP.UTF-8")
    assert resolve_system_language() == "ja"


def test_resolve_system_language_prefers_lc_messages_over_lang(monkeypatch):
    _clear_locale_env(monkeypatch)
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    monkeypatch.setenv("LC_MESSAGES", "de_DE.UTF-8")
    assert resolve_system_language() == "de"


def test_resolve_system_language_falls_back_to_getlocale(monkeypatch):
    _clear_locale_env(monkeypatch)
    monkeypatch.setattr(locale, "getlocale", lambda: ("ko_KR", "UTF-8"))
    assert resolve_system_language() == "ko"


def test_resolve_system_language_defaults_to_english_not_spanish_when_unsupported(monkeypatch):
    _clear_locale_env(monkeypatch)
    monkeypatch.setenv("LANG", "xx_XX.UTF-8")
    monkeypatch.setattr(locale, "getlocale", lambda: (None, None))
    assert resolve_system_language() == "en"


def test_resolve_system_language_defaults_to_english_when_nothing_set(monkeypatch):
    _clear_locale_env(monkeypatch)
    monkeypatch.setattr(locale, "getlocale", lambda: (None, None))
    assert resolve_system_language() == "en"


def test_resolve_system_language_ignores_c_locale(monkeypatch):
    _clear_locale_env(monkeypatch)
    monkeypatch.setenv("LANG", "C")
    monkeypatch.setattr(locale, "getlocale", lambda: (None, None))
    assert resolve_system_language() == "en"
