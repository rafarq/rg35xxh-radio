"""Pillow-based frame rendering for the 640x480 RG35XX H screen.

Imports Pillow at module scope, but this module itself is only ever
imported lazily from inside :mod:`radio.ui.app` functions — never from
``radio/app/state.py`` or from anything a desktop unit test imports —
so ``pytest`` runs fine on a machine without Pillow installed.

Rendering is pure: every function takes a :class:`radio.app.state.RadioApp`
(or plain values) and returns a freshly drawn ``PIL.Image``. Nothing here
touches SDL2, the input device or the player subprocess; the frame loop
in ``radio/ui/app.py`` owns pushing the returned image onto a texture.
"""

from __future__ import annotations

import math
import os
from typing import List, Sequence

from PIL import Image, ImageDraw, ImageFont

from radio.app.state import RadioApp, Screen, SettingsView
from radio.i18n import SUPPORTED_LANGUAGES
from radio.playback.controller import PlaybackState
from radio.playlist.model import Station
from radio.playlist.sources import PlaylistSource, PlaylistSourceKind

WIDTH = 640
HEIGHT = 480

BG_COLOR = (12, 14, 20)
FG_COLOR = (230, 230, 235)
DIM_COLOR = (130, 132, 140)
ACCENT_COLOR = (80, 170, 255)
SELECTED_BG = (40, 60, 90)
FAVORITE_COLOR = (240, 190, 60)
ERROR_COLOR = (230, 90, 90)
OK_COLOR = (100, 210, 130)
CARD_BG = (22, 25, 34)
CARD_BORDER_DIM = (55, 60, 74)

ROW_HEIGHT = 34
HEADER_HEIGHT = 56
FOOTER_HEIGHT = 28
LIST_TOP = HEADER_HEIGHT + 8
VISIBLE_ROWS = (HEIGHT - LIST_TOP - FOOTER_HEIGHT) // ROW_HEIGHT

# HOME dashboard: exactly 4 cards (Favorites, Categories, Recents,
# Settings), sized to fit the 640x480 screen with margin either side.
CARD_COUNT = 4
CARD_WIDTH = 136
CARD_HEIGHT = 236
CARD_GAP = 16
CARD_TOP = 88
CARD_RADIUS = 18
ICON_TOP_MARGIN = 30
ICON_SIZE = 64
HOME_ICON_SCALE = 4
HOME_ICON_PADDING = 4
RESAMPLE_LANCZOS = getattr(Image, "Resampling", Image).LANCZOS
HOME_SELECTED_BORDER = (244, 197, 66)
HOME_SELECTED_GLOW = (78, 62, 29)
HOME_CARD_INNER = (31, 36, 48)
HOME_FOOTER_BG = (15, 18, 27)

# Languages whose script reads right-to-left: on-screen text for these
# is right-aligned instead of the default left alignment.
RTL_LANGUAGES = {"ar", "ur"}

CREDITS_NAME = "Rafael Roa"
CREDITS_URL = "https://rafarq.com"
CREDITS_SOCIAL_LINKS = (
    ("GitHub", "https://github.com/rafarq"),
    ("LinkedIn", "https://www.linkedin.com/in/rafaroa"),
    ("Instagram", "https://www.instagram.com/r4f4r04"),
    ("Threads", "https://www.threads.net/@r4f4r04"),
    ("Mastodon", "https://mastodon.cloud/@rafarq"),
)

_font_cache: dict = {}

# Preferred TrueType fonts, checked in order. The RG35XX H's stock firmware
# ships a system font at this path; a bundled fallback can live alongside
# the app assets. Neither is guaranteed to exist (e.g. on desktop), so
# _font() always falls back to Pillow's built-in bitmap font.
SYSTEM_FONT_PATH = "/mnt/vendor/bin/default.ttf"
BUNDLED_FONT_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "assets", "default.ttf")


def _font(size: int) -> ImageFont.ImageFont:
    font = _font_cache.get(size)
    if font is None:
        font = None
        for path in (SYSTEM_FONT_PATH, BUNDLED_FONT_PATH):
            try:
                font = ImageFont.truetype(path, size)
                break
            except OSError:
                continue
        if font is None:
            # Pillow 9.0.1's load_default() takes no arguments at all.
            font = ImageFont.load_default()
        _font_cache[size] = font
    return font


def _is_rtl(language: str) -> bool:
    return language in RTL_LANGUAGES


def _new_frame() -> Image.Image:
    return Image.new("RGB", (WIDTH, HEIGHT), BG_COLOR)


def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> float:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _draw_header(draw: ImageDraw.ImageDraw, title: str, subtitle: str = "", rtl: bool = False) -> None:
    draw.rectangle([(0, 0), (WIDTH, HEADER_HEIGHT)], fill=(20, 22, 30))
    title_font = _font(24)
    if rtl:
        draw.text((WIDTH - 16 - _text_width(draw, title, title_font), 10), title, fill=FG_COLOR, font=title_font)
    else:
        draw.text((16, 10), title, fill=FG_COLOR, font=title_font)
    if subtitle:
        subtitle_font = _font(14)
        if rtl:
            draw.text(
                (WIDTH - 16 - _text_width(draw, subtitle, subtitle_font), 34),
                subtitle,
                fill=DIM_COLOR,
                font=subtitle_font,
            )
        else:
            draw.text((16, 34), subtitle, fill=DIM_COLOR, font=subtitle_font)


def _draw_radio_mark(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
    """Draw the compact radio brand mark in code, without an asset file."""
    gold = HOME_SELECTED_BORDER
    draw.rounded_rectangle([(x, y), (x + 34, y + 30)], radius=7, fill=(27, 32, 43), outline=gold, width=2)
    draw.line([(x + 8, y + 9), (x + 26, y + 4)], fill=gold, width=2)
    draw.ellipse([(x + 8, y + 14), (x + 14, y + 20)], fill=gold)
    draw.line([(x + 19, y + 17), (x + 28, y + 17)], fill=ACCENT_COLOR, width=2)
    draw.line([(x + 19, y + 22), (x + 25, y + 22)], fill=ACCENT_COLOR, width=2)


def _draw_home_brand(draw: ImageDraw.ImageDraw, app: RadioApp, rtl: bool) -> None:
    """Home-only brand area; other screens retain their established header."""
    draw.rectangle([(0, 0), (WIDTH, 76)], fill=(15, 18, 27))
    if rtl:
        mark_x = WIDTH - 58
        text_right = mark_x - 12
        title_font, subtitle_font = _font(25), _font(14)
        title, subtitle = app.t("app_title"), app.t("home_subtitle")
        draw.text((text_right - _text_width(draw, title, title_font), 14), title, fill=FG_COLOR, font=title_font)
        draw.text((text_right - _text_width(draw, subtitle, subtitle_font), 42), subtitle, fill=DIM_COLOR, font=subtitle_font)
    else:
        _draw_radio_mark(draw, 28, 20)
        draw.text((76, 14), app.t("app_title"), fill=FG_COLOR, font=_font(25))
        draw.text((76, 42), app.t("home_subtitle"), fill=DIM_COLOR, font=_font(14))
        return
    _draw_radio_mark(draw, mark_x, 20)


def _scroll_window(selected_index: int, total: int, visible: int) -> int:
    """First visible row so ``selected_index`` stays on screen."""
    if total <= visible:
        return 0
    start = selected_index - visible // 2
    start = max(0, min(start, total - visible))
    return start


def _draw_list(
    draw: ImageDraw.ImageDraw,
    labels: Sequence[str],
    selected_index: int,
    favorite_flags: Sequence[bool] = (),
    rtl: bool = False,
    empty_label: str = "",
) -> None:
    total = len(labels)
    if total == 0:
        font = _font(16)
        if rtl:
            draw.text((WIDTH - 16 - _text_width(draw, empty_label, font), LIST_TOP), empty_label, fill=DIM_COLOR, font=font)
        else:
            draw.text((16, LIST_TOP), empty_label, fill=DIM_COLOR, font=font)
        return

    start = _scroll_window(selected_index, total, VISIBLE_ROWS)
    end = min(total, start + VISIBLE_ROWS)

    for row, index in enumerate(range(start, end)):
        y = LIST_TOP + row * ROW_HEIGHT
        is_selected = index == selected_index
        if is_selected:
            draw.rectangle([(0, y), (WIDTH, y + ROW_HEIGHT)], fill=SELECTED_BG)
        label = labels[index]
        is_favorite = favorite_flags[index] if index < len(favorite_flags) else False
        prefix = "★ " if is_favorite else ""
        color = ACCENT_COLOR if is_selected else FG_COLOR
        font = _font(18)
        text = f"{prefix}{label}"
        if rtl:
            draw.text((WIDTH - 20 - _text_width(draw, text, font), y + 6), text, fill=color, font=font)
        else:
            draw.text((20, y + 6), text, fill=color, font=font)


def _draw_footer(draw: ImageDraw.ImageDraw, hint: str, rtl: bool = False) -> None:
    draw.rectangle([(0, HEIGHT - FOOTER_HEIGHT), (WIDTH, HEIGHT)], fill=(20, 22, 30))
    font = _font(13)
    if rtl:
        draw.text((WIDTH - 16 - _text_width(draw, hint, font), HEIGHT - FOOTER_HEIGHT + 6), hint, fill=DIM_COLOR, font=font)
    else:
        draw.text((16, HEIGHT - FOOTER_HEIGHT + 6), hint, fill=DIM_COLOR, font=font)


def _draw_star_icon_vector(draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int, color) -> None:
    """5-point star, vector-drawn (no external icon assets)."""
    outer_r = size / 2
    inner_r = outer_r * 0.42
    points = []
    for i in range(10):
        angle = math.pi / 2 + i * math.pi / 5
        radius = outer_r if i % 2 == 0 else inner_r
        points.append((cx + radius * math.cos(angle), cy - radius * math.sin(angle)))
    draw.polygon(points, fill=color)


def _draw_grid_icon_vector(draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int, color) -> None:
    """2x2 rounded-square grid, standing in for a categories/folder icon."""
    cell = size * 0.42
    gap = size * 0.16
    for dx in (-1, 1):
        for dy in (-1, 1):
            x0 = cx + dx * gap / 2 + (0 if dx > 0 else -cell)
            y0 = cy + dy * gap / 2 + (0 if dy > 0 else -cell)
            draw.rounded_rectangle(
                [(x0, y0), (x0 + cell, y0 + cell)], radius=cell * 0.22, fill=color
            )


def _draw_clock_icon_vector(draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int, color) -> None:
    """Clock face with hour/minute hands, standing in for a history icon."""
    radius = size / 2
    draw.ellipse(
        [(cx - radius, cy - radius), (cx + radius, cy + radius)], outline=color, width=5
    )
    draw.line([(cx, cy), (cx, cy - radius * 0.55)], fill=color, width=5)
    draw.line([(cx, cy), (cx + radius * 0.4, cy)], fill=color, width=5)


def _draw_gear_icon_vector(
    draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int, color, hub_fill=(0, 0, 0, 0)
) -> None:
    """Vector gear/cog icon, standing in for a settings icon."""
    outer_r = size / 2
    inner_r = outer_r * 0.62
    hub_r = outer_r * 0.32
    tooth_count = 8
    half_tooth = math.pi / tooth_count * 0.42
    points = []
    for i in range(tooth_count):
        center_angle = 2 * math.pi * i / tooth_count
        for angle in (center_angle - half_tooth, center_angle + half_tooth):
            points.append((cx + outer_r * math.cos(angle), cy + outer_r * math.sin(angle)))
        next_angle = 2 * math.pi * (i + 1) / tooth_count - half_tooth
        points.append((cx + inner_r * math.cos(center_angle + half_tooth), cy + inner_r * math.sin(center_angle + half_tooth)))
        points.append((cx + inner_r * math.cos(next_angle), cy + inner_r * math.sin(next_angle)))
    draw.polygon(points, fill=color)
    draw.ellipse([(cx - hub_r, cy - hub_r), (cx + hub_r, cy + hub_r)], fill=hub_fill)


# Retained for callers outside the Home compositor.  Home itself renders the
# vector forms into a larger transparent layer before reducing them.
def _draw_star_icon(draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int, color) -> None:
    _draw_star_icon_vector(draw, cx, cy, size, color)


def _draw_grid_icon(draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int, color) -> None:
    _draw_grid_icon_vector(draw, cx, cy, size, color)


def _draw_clock_icon(draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int, color) -> None:
    _draw_clock_icon_vector(draw, cx, cy, size, color)


def _draw_gear_icon(draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int, color) -> None:
    _draw_gear_icon_vector(draw, cx, cy, size, color, hub_fill=BG_COLOR)


_HOME_ICON_VECTORS = {
    "favorites": _draw_star_icon_vector,
    "categories": _draw_grid_icon_vector,
    "recents": _draw_clock_icon_vector,
    "settings": _draw_gear_icon_vector,
}


def _draw_home_icon(frame: Image.Image, name: str, cx: int, cy: int, size: int, color) -> None:
    """Composite a supersampled vector icon onto the RGB Home frame."""
    scale = HOME_ICON_SCALE
    padding = HOME_ICON_PADDING
    layer_size = (size + padding * 2) * scale
    layer = Image.new("RGBA", (layer_size, layer_size), (0, 0, 0, 0))
    vector = _HOME_ICON_VECTORS[name]
    center = layer_size // 2
    vector(ImageDraw.Draw(layer), center, center, size * scale, color)
    icon = layer.resize((size + padding * 2, size + padding * 2), RESAMPLE_LANCZOS)
    frame.paste(icon, (cx - size // 2 - padding, cy - size // 2 - padding), icon)


def _text_center(draw: ImageDraw.ImageDraw, cx: int, y: int, text: str, font, fill) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    draw.text((cx - w / 2, y), text, fill=fill, font=font)


def _home_card_specs(app: RadioApp):
    return (
        ("favorites", app.t("home_card_favorites"), app.t("home_card_favorites_description"), FAVORITE_COLOR, f"{len(app.favorites)}"),
        ("categories", app.t("home_card_categories"), app.t("home_card_categories_description"), ACCENT_COLOR, f"{len(app.groups)}"),
        ("recents", app.t("home_card_recents"), app.t("home_card_recents_description"), ACCENT_COLOR, f"{len(app.recents)}"),
        ("settings", app.t("home_card_settings"), app.t("home_card_settings_description"), ACCENT_COLOR, SUPPORTED_LANGUAGES.get(app.language, "")),
    )


def _draw_home_pill(draw: ImageDraw.ImageDraw, cx: int, y: int, text: str, is_settings: bool) -> None:
    font = _font(10)
    prefix_width = 16 if is_settings else 0
    pill_width = min(CARD_WIDTH - 22, int(_text_width(draw, text, font)) + prefix_width + 20)
    x0 = cx - pill_width // 2
    draw.rounded_rectangle([(x0, y), (x0 + pill_width, y + 24)], radius=12, fill=(18, 22, 31), outline=(56, 65, 82))
    if is_settings:
        globe_x, globe_y = x0 + 12, y + 12
        draw.ellipse([(globe_x - 5, globe_y - 5), (globe_x + 5, globe_y + 5)], outline=ACCENT_COLOR, width=1)
        draw.line([(globe_x - 5, globe_y), (globe_x + 5, globe_y)], fill=ACCENT_COLOR)
        draw.line([(globe_x, globe_y - 5), (globe_x, globe_y + 5)], fill=ACCENT_COLOR)
    _text_center(draw, cx + (prefix_width // 2 if is_settings else 0), y + 6, text, font, DIM_COLOR)


def _draw_home_description(draw: ImageDraw.ImageDraw, cx: int, y: int, text: str) -> None:
    """Keep localized card copy within a card, using at most two short lines."""
    font = _font(11)
    max_width = CARD_WIDTH - 18
    if _text_width(draw, text, font) <= max_width:
        _text_center(draw, cx, y, text, font, DIM_COLOR)
        return
    words = text.split()
    lines = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and _text_width(draw, candidate, font) > max_width:
            lines.append(current)
            current = word
            if len(lines) == 1:
                break
        else:
            current = candidate
    if not lines:
        lines.append("")
    remainder = " ".join(words[len(lines[0].split()):]) if words else text
    while remainder and _text_width(draw, remainder + "…", font) > max_width:
        remainder = remainder[:-1]
    lines = [lines[0], (remainder + "…") if remainder and remainder != text else remainder]
    for line_index, line in enumerate(line for line in lines if line):
        _text_center(draw, cx, y + line_index * 13, line, font, DIM_COLOR)


def _draw_home_footer(draw: ImageDraw.ImageDraw, app: RadioApp, rtl: bool) -> None:
    draw.rectangle([(0, HEIGHT - 48), (WIDTH, HEIGHT)], fill=HOME_FOOTER_BG)
    hints = (("◄►", app.t("footer_home_move")), ("A", app.t("footer_home_open")), ("≡", app.t("footer_home_quit")))
    if rtl:
        hints = tuple(reversed(hints))
    x = 30
    for symbol, label in hints:
        draw.ellipse([(x, HEIGHT - 36), (x + 22, HEIGHT - 14)], fill=(36, 42, 55), outline=(76, 86, 106))
        _text_center(draw, x + 11, HEIGHT - 32, symbol, _font(10), FG_COLOR)
        draw.text((x + 30, HEIGHT - 33), label, fill=DIM_COLOR, font=_font(12))
        x += 202


def render_home(app: RadioApp) -> Image.Image:
    frame = _new_frame()
    draw = ImageDraw.Draw(frame)
    rtl = _is_rtl(app.language)
    _draw_home_brand(draw, app, rtl)

    specs = _home_card_specs(app)
    total_width = CARD_COUNT * CARD_WIDTH + (CARD_COUNT - 1) * CARD_GAP
    start_x = (WIDTH - total_width) // 2

    for index, (name, label, description, color, count_text) in enumerate(specs):
        x0 = start_x + index * (CARD_WIDTH + CARD_GAP)
        y0 = CARD_TOP
        x1 = x0 + CARD_WIDTH
        y1 = y0 + CARD_HEIGHT
        is_selected = index == app.home_index

        # The dark outer ring is a deliberately restrained selection glow.
        if is_selected:
            draw.rounded_rectangle([(x0 - 4, y0 - 4), (x1 + 4, y1 + 4)], radius=CARD_RADIUS + 3, outline=HOME_SELECTED_GLOW, width=3)
        bg = (27, 32, 43) if is_selected else CARD_BG
        border = HOME_SELECTED_BORDER if is_selected else CARD_BORDER_DIM
        border_width = 3 if is_selected else 2
        draw.rounded_rectangle(
            [(x0, y0), (x1, y1)], radius=CARD_RADIUS, fill=bg, outline=border, width=border_width
        )
        draw.rounded_rectangle([(x0 + 6, y0 + 6), (x1 - 6, y1 - 6)], radius=CARD_RADIUS - 5, outline=HOME_CARD_INNER, width=1)

        cx = (x0 + x1) // 2
        icon_cy = y0 + ICON_TOP_MARGIN + ICON_SIZE // 2
        _draw_home_icon(frame, name, cx, icon_cy, ICON_SIZE, color)

        label_color = HOME_SELECTED_BORDER if is_selected else FG_COLOR
        _text_center(draw, cx, icon_cy + ICON_SIZE // 2 + 20, label, _font(16), label_color)
        _draw_home_description(draw, cx, icon_cy + ICON_SIZE // 2 + 46, description)
        _draw_home_pill(draw, cx, y1 - 42, count_text, name == "settings")

    _draw_home_footer(draw, app, rtl)
    return frame


def render_categories(app: RadioApp) -> Image.Image:
    frame = _new_frame()
    draw = ImageDraw.Draw(frame)
    rtl = _is_rtl(app.language)
    _draw_header(draw, app.t("categories_title"), app.t("count_groups", count=len(app.groups)), rtl=rtl)
    _draw_list(draw, app.groups, app.category_index, rtl=rtl, empty_label=app.t("empty_list"))
    _draw_footer(draw, app.t("footer_categories"), rtl=rtl)
    return frame


def _station_labels(stations: Sequence[Station]) -> List[str]:
    return [station.name for station in stations]


def render_stations(app: RadioApp) -> Image.Image:
    frame = _new_frame()
    draw = ImageDraw.Draw(frame)
    rtl = _is_rtl(app.language)
    group = app.current_group() or "-"
    stations = app.current_station_list()
    _draw_header(draw, group, app.t("count_stations", count=len(stations)), rtl=rtl)
    labels = _station_labels(stations)
    favorite_flags = [app.is_favorite(station) for station in stations]
    _draw_list(draw, labels, app.station_index, favorite_flags, rtl=rtl, empty_label=app.t("empty_list"))
    _draw_footer(draw, app.favorite_message or app.t("footer_stations"), rtl=rtl)
    return frame


def render_favorites(app: RadioApp) -> Image.Image:
    frame = _new_frame()
    draw = ImageDraw.Draw(frame)
    rtl = _is_rtl(app.language)
    stations = app.current_station_list()
    _draw_header(draw, app.t("favorites_title"), app.t("count_stations", count=len(stations)), rtl=rtl)
    labels = _station_labels(stations)
    _draw_list(draw, labels, app.station_index, [True] * len(stations), rtl=rtl, empty_label=app.t("empty_list"))
    _draw_footer(draw, app.favorite_message or app.t("footer_favorites"), rtl=rtl)
    return frame


def render_recents(app: RadioApp) -> Image.Image:
    frame = _new_frame()
    draw = ImageDraw.Draw(frame)
    rtl = _is_rtl(app.language)
    stations = app.current_station_list()
    _draw_header(draw, app.t("recents_title"), app.t("count_stations", count=len(stations)), rtl=rtl)
    labels = _station_labels(stations)
    favorite_flags = [app.is_favorite(station) for station in stations]
    _draw_list(draw, labels, app.station_index, favorite_flags, rtl=rtl, empty_label=app.t("empty_list"))
    _draw_footer(draw, app.favorite_message or app.t("footer_recents"), rtl=rtl)
    return frame


def _state_labels(app: RadioApp):
    return {
        PlaybackState.IDLE: (app.t("state_stopped"), DIM_COLOR),
        PlaybackState.STARTING: (app.t("state_connecting"), ACCENT_COLOR),
        PlaybackState.PLAYING: (app.t("state_playing"), OK_COLOR),
        PlaybackState.PAUSED: (app.t("state_paused"), ACCENT_COLOR),
        PlaybackState.STOPPED: (app.t("state_stopped"), DIM_COLOR),
        PlaybackState.ERROR: (app.t("state_error"), ERROR_COLOR),
    }


def render_now_playing(app: RadioApp) -> Image.Image:
    frame = _new_frame()
    draw = ImageDraw.Draw(frame)
    rtl = _is_rtl(app.language)
    station = app.now_playing
    _draw_header(draw, app.t("now_playing_title"), rtl=rtl)

    if station is None:
        no_selection = app.t("no_selection")
        font = _font(18)
        if rtl:
            draw.text((WIDTH - 20 - _text_width(draw, no_selection, font), LIST_TOP), no_selection, fill=DIM_COLOR, font=font)
        else:
            draw.text((20, LIST_TOP), no_selection, fill=DIM_COLOR, font=font)
        _draw_footer(draw, app.t("footer_engine_unavailable"), rtl=rtl)
        return frame

    label, color = _state_labels(app).get(app.player.state, ("-", DIM_COLOR))
    fav_marker = "★ " if app.is_favorite(station) else ""

    name_font = _font(22)
    group_font = _font(14)
    state_font = _font(18)
    name_text = f"{fav_marker}{station.name}"
    if rtl:
        draw.text((WIDTH - 20 - _text_width(draw, name_text, name_font), LIST_TOP), name_text, fill=FG_COLOR, font=name_font)
        draw.text((WIDTH - 20 - _text_width(draw, station.group, group_font), LIST_TOP + 34), station.group, fill=DIM_COLOR, font=group_font)
        draw.text((WIDTH - 20 - _text_width(draw, label, state_font), LIST_TOP + 64), label, fill=color, font=state_font)
    else:
        draw.text((20, LIST_TOP), name_text, fill=FG_COLOR, font=name_font)
        draw.text((20, LIST_TOP + 34), station.group, fill=DIM_COLOR, font=group_font)
        draw.text((20, LIST_TOP + 64), label, fill=color, font=state_font)

    if app.status_message:
        status_font = _font(14)
        if rtl:
            draw.text((WIDTH - 20 - _text_width(draw, app.status_message, status_font), LIST_TOP + 96), app.status_message, fill=ERROR_COLOR, font=status_font)
        else:
            draw.text((20, LIST_TOP + 96), app.status_message, fill=ERROR_COLOR, font=status_font)

    volume_label = app.t("volume_label", percent=app.volume)
    volume_font = _font(16)
    if rtl:
        draw.text((WIDTH - 20 - _text_width(draw, volume_label, volume_font), HEIGHT - FOOTER_HEIGHT - 40), volume_label, fill=FG_COLOR, font=volume_font)
        bar_x1, bar_y0 = WIDTH - 20, HEIGHT - FOOTER_HEIGHT - 16
        bar_x0, bar_y1 = WIDTH - 320, HEIGHT - FOOTER_HEIGHT - 8
        draw.rectangle([(bar_x0, bar_y0), (bar_x1, bar_y1)], outline=DIM_COLOR)
        fill_x0 = bar_x1 - int((bar_x1 - bar_x0) * (app.volume / 100))
        if fill_x0 < bar_x1:
            draw.rectangle([(fill_x0, bar_y0), (bar_x1, bar_y1)], fill=ACCENT_COLOR)
    else:
        draw.text((20, HEIGHT - FOOTER_HEIGHT - 40), volume_label, fill=FG_COLOR, font=volume_font)
        bar_x0, bar_y0 = 20, HEIGHT - FOOTER_HEIGHT - 16
        bar_x1, bar_y1 = 320, HEIGHT - FOOTER_HEIGHT - 8
        draw.rectangle([(bar_x0, bar_y0), (bar_x1, bar_y1)], outline=DIM_COLOR)
        fill_x1 = bar_x0 + int((bar_x1 - bar_x0) * (app.volume / 100))
        if fill_x1 > bar_x0:
            draw.rectangle([(bar_x0, bar_y0), (fill_x1, bar_y1)], fill=ACCENT_COLOR)

    pause_label = app.t("action_resume") if app.player.state == PlaybackState.PAUSED else app.t("action_pause")
    _draw_footer(draw, app.t("footer_now_playing", pause_label=pause_label), rtl=rtl)
    return frame


def _render_settings_menu(app: RadioApp, draw: ImageDraw.ImageDraw, rtl: bool) -> None:
    _draw_header(draw, app.t("settings_title"), rtl=rtl)
    labels = [
        app.t("settings_menu_playlists"),
        app.t("settings_language_label"),
        app.t("settings_menu_credits"),
    ]
    _draw_list(draw, labels, app.settings_index, rtl=rtl)
    _draw_footer(draw, app.settings_message or app.t("settings_menu_hint"), rtl=rtl)


def _truncate_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    """Keep untrusted filenames/URLs within the fixed-width handheld UI."""
    if _text_width(draw, text, font) <= max_width:
        return text
    ellipsis = "…"
    clipped = text
    while clipped and _text_width(draw, clipped + ellipsis, font) > max_width:
        clipped = clipped[:-1]
    return clipped + ellipsis


def _playlist_source_label(app: RadioApp, source: PlaylistSource) -> str:
    if source.kind == PlaylistSourceKind.DEFAULT:
        return app.t("playlist_source_default")
    if source.kind == PlaylistSourceKind.LOCAL:
        return app.t("playlist_source_local", name=source.id)
    return app.t("playlist_source_remote", url=source.id)


def _playlist_status_label(app: RadioApp) -> str:
    status = app.playlist_source_status
    status_key = {
        "default": "playlist_status_default",
        "local": "playlist_status_local",
        "fresh": "playlist_status_fresh",
        "cached": "playlist_status_cached",
        "fallback": "playlist_status_fallback",
    }.get(status, "playlist_status_fallback")
    label = app.t(status_key)
    if app.playlist_source_error:
        return app.t("playlist_status_error", status=label, error=app.playlist_source_error)
    return label


def _render_settings_playlist(app: RadioApp, draw: ImageDraw.ImageDraw, rtl: bool) -> None:
    # Status lives in the header so the selected source and its resolution
    # result remain visible while a long source list scrolls below.
    status = _truncate_text(draw, _playlist_status_label(app), _font(14), WIDTH - 32)
    _draw_header(draw, app.t("playlist_picker_title"), status, rtl=rtl)
    sources = app.available_playlist_sources
    labels = [
        _truncate_text(draw, _playlist_source_label(app, source), _font(18), WIDTH - 40)
        for source in sources
    ]
    _draw_list(
        draw,
        labels,
        app.settings_index,
        rtl=rtl,
        empty_label=app.t("playlist_list_empty"),
    )
    if not sources:
        help_text = _truncate_text(draw, app.t("playlist_list_help"), _font(14), WIDTH - 40)
        if rtl:
            draw.text(
                (WIDTH - 20 - _text_width(draw, help_text, _font(14)), LIST_TOP + ROW_HEIGHT),
                help_text,
                fill=DIM_COLOR,
                font=_font(14),
            )
        else:
            draw.text((20, LIST_TOP + ROW_HEIGHT), help_text, fill=DIM_COLOR, font=_font(14))
    hint = app.settings_message or app.t("playlist_picker_hint")
    _draw_footer(draw, hint, rtl=rtl)


def _render_settings_language(app: RadioApp, draw: ImageDraw.ImageDraw, rtl: bool) -> None:
    _draw_header(draw, app.t("settings_language_label"), rtl=rtl)
    codes = list(SUPPORTED_LANGUAGES)
    labels = [SUPPORTED_LANGUAGES[code] for code in codes]
    _draw_list(draw, labels, app.settings_index, rtl=rtl)
    _draw_footer(draw, app.settings_message or app.t("settings_language_hint"), rtl=rtl)


def _render_settings_credits(app: RadioApp, draw: ImageDraw.ImageDraw, rtl: bool) -> None:
    _draw_header(draw, app.t("credits_title"), rtl=rtl)
    lines = [
        app.t("app_title"),
        app.t("credits_created_by", name=CREDITS_NAME),
        CREDITS_URL,
        *(f"{label}  {url}" for label, url in CREDITS_SOCIAL_LINKS),
    ]
    fonts = [_font(22), *[_font(16)] * (len(lines) - 1)]
    y = LIST_TOP
    for line, font in zip(lines, fonts):
        if rtl:
            draw.text((WIDTH - 20 - _text_width(draw, line, font), y), line, fill=FG_COLOR, font=font)
        else:
            draw.text((20, y), line, fill=FG_COLOR, font=font)
        y += 34
    _draw_footer(draw, app.t("footer_credits"), rtl=rtl)


_SETTINGS_RENDERERS = {
    SettingsView.MENU: _render_settings_menu,
    SettingsView.PLAYLIST: _render_settings_playlist,
    SettingsView.LANGUAGE: _render_settings_language,
    SettingsView.CREDITS: _render_settings_credits,
}


def render_settings(app: RadioApp) -> Image.Image:
    frame = _new_frame()
    draw = ImageDraw.Draw(frame)
    rtl = _is_rtl(app.language)
    renderer = _SETTINGS_RENDERERS[app.settings_view]
    renderer(app, draw, rtl)
    return frame


_RENDERERS = {
    Screen.HOME: render_home,
    Screen.CATEGORIES: render_categories,
    Screen.STATIONS: render_stations,
    Screen.FAVORITES: render_favorites,
    Screen.RECENTS: render_recents,
    Screen.NOW_PLAYING: render_now_playing,
    Screen.SETTINGS: render_settings,
}


def render_frame(app: RadioApp) -> Image.Image:
    """Draw the frame for ``app``'s current screen."""
    engine_status = app.engine_status()
    if engine_status and app.screen == Screen.NOW_PLAYING:
        frame = _new_frame()
        draw = ImageDraw.Draw(frame)
        rtl = _is_rtl(app.language)
        _draw_header(draw, app.t("engine_unavailable_title"), rtl=rtl)
        font = _font(16)
        if rtl:
            draw.text((WIDTH - 20 - _text_width(draw, engine_status, font), LIST_TOP), engine_status, fill=ERROR_COLOR, font=font)
        else:
            draw.text((20, LIST_TOP), engine_status, fill=ERROR_COLOR, font=font)
        _draw_footer(draw, app.t("footer_engine_unavailable"), rtl=rtl)
        return frame

    renderer = _RENDERERS[app.screen]
    return renderer(app)
