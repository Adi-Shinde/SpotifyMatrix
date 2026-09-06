"""Tests for the pure functions in spotify_matrix.

No hardware, no network, no Spotify credentials — everything here is input
in, value out. Two of these cover bugs that shipped (enhanced-LRC word tags
rendering as literal text, and settings validation), which is the argument for
having the file at all.

Run:  python -m pytest tests/ -q
"""
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import spotify_matrix as sm


# ── parse_lrc ────────────────────────────────────────────────────────

def test_parse_lrc_basic_timestamps():
    lyrics, words = sm.parse_lrc("[00:12.50]hello world\n[01:05.00]second line")
    assert lyrics == [(12500, "hello world"), (65000, "second line")]
    assert words == [[], []]


def test_parse_lrc_sorts_out_of_order_lines():
    lyrics, _ = sm.parse_lrc("[00:20.00]later\n[00:10.00]earlier")
    assert [text for _, text in lyrics] == ["earlier", "later"]


def test_parse_lrc_strips_enhanced_word_tags():
    """B19: inline <mm:ss.xx> tags used to be drawn on the panel verbatim."""
    lyrics, words = sm.parse_lrc("[00:10.00]<00:10.00>Never <00:10.50>gonna")
    assert lyrics == [(10000, "Never gonna")]
    assert "<" not in lyrics[0][1]
    assert words[0] == [("Never", 10000), ("gonna", 10500)]


def test_parse_lrc_ignores_metadata_and_blank_lines():
    lyrics, _ = sm.parse_lrc("[ar:Artist]\n[al:Album]\n\n[00:01.00]real line")
    assert lyrics == [(1000, "real line")]


def test_parse_lrc_handles_empty_input():
    assert sm.parse_lrc("") == ([], [])


# ── get_current_lyric_index ──────────────────────────────────────────

LYRICS = [(0, "a"), (1000, "b"), (2000, "c")]


@pytest.mark.parametrize("progress,expected", [
    (-50, -1),     # before the first line: -1 means "nothing to show yet"
    (0, 0),
    (999, 0),
    (1000, 1),     # exactly on a boundary picks that line
    (1500, 1),
    (2000, 2),
    (99999, 2),    # past the end stays on the last line
])
def test_get_current_lyric_index(progress, expected):
    assert sm.get_current_lyric_index(LYRICS, progress) == expected


def test_get_current_lyric_index_empty():
    assert sm.get_current_lyric_index([], 5000) == -1


# ── pick_art_variant ─────────────────────────────────────────────────

IMAGES = [
    {"url": "big", "width": 640},
    {"url": "mid", "width": 300},
    {"url": "small", "width": 64},
]


def test_pick_art_variant_takes_smallest_above_target():
    """P1: this used to always take the 640px original and downscale it."""
    assert sm.pick_art_variant(IMAGES, 160)["url"] == "mid"


def test_pick_art_variant_falls_back_to_largest():
    assert sm.pick_art_variant(IMAGES, 2000)["url"] == "big"


def test_pick_art_variant_exact_match_is_eligible():
    assert sm.pick_art_variant(IMAGES, 300)["url"] == "mid"


# ── normalize_track_title ────────────────────────────────────────────

@pytest.mark.parametrize("raw", [
    "Song - Remastered 2011",
    "Song (feat. Someone)",
    "Song - Radio Edit",
])
def test_normalize_track_title_strips_noise(raw):
    """F5: these suffixes are why exact LRCLIB lookups missed so often."""
    assert sm.normalize_track_title(raw).strip().lower().startswith("song")
    assert len(sm.normalize_track_title(raw)) < len(raw)


def test_normalize_track_title_leaves_clean_titles_alone():
    assert sm.normalize_track_title("Bohemian Rhapsody") == "Bohemian Rhapsody"


# ── word_progress / word_spans ───────────────────────────────────────

def test_word_spans_cover_the_line_in_order():
    spans = sm.word_spans("one two three", 0, 3000)
    assert [word for word, _ in spans] == ["one", "two", "three"]
    starts = [start for _, start in spans]
    assert starts == sorted(starts), "word starts must advance monotonically"
    assert starts[0] == 0
    assert starts[-1] <= 3000


def test_word_spans_weights_longer_words_with_more_time():
    spans = sm.word_spans("a extraordinarily b", 0, 3000)
    gap_after_short = spans[1][1] - spans[0][1]
    gap_after_long = spans[2][1] - spans[1][1]
    assert gap_after_long > gap_after_short


def test_word_spans_prefers_real_word_timings_when_present():
    """Enhanced LRC gives exact times; interpolation is only the fallback."""
    spans = sm.word_spans("one two", 0, 2000, [("one", 100), ("two", 900)])
    assert spans[0][1] == 100
    assert spans[1][1] == 900


def test_word_spans_handles_empty_text():
    assert sm.word_spans("", 0, 1000) == []


# ── blend_frames ─────────────────────────────────────────────────────

def _solid(colour):
    from PIL import Image
    return Image.new("RGB", (64, 64), colour)


BLACK, WHITE = (0, 0, 0), (255, 255, 255)


@pytest.mark.parametrize("mode", ["slide", "slide-right", "slide-up", "slide-down", "fade"])
def test_blend_frames_endpoints(mode):
    old, new = _solid(BLACK), _solid(WHITE)
    at_start = sm.blend_frames(old, new, 0.0, mode=mode)
    at_end = sm.blend_frames(old, new, 1.0, mode=mode)
    assert at_start.size == (64, 64)
    assert at_end.getpixel((32, 32)) == WHITE


def test_blend_frames_fade_progresses_monotonically():
    """Eased with 1-(1-p)^3, so the midpoint is well past halfway by design."""
    old, new = _solid(BLACK), _solid(WHITE)
    values = [
        sm.blend_frames(old, new, p, mode="fade").getpixel((32, 32))[0]
        for p in (0.0, 0.25, 0.5, 0.75, 1.0)
    ]
    assert values == sorted(values), "fade must never go backwards"
    assert values[0] < values[-1]
    assert values[-1] == 255


def test_blend_frames_every_cli_transition_is_reachable():
    """B23: slide-left was implemented but absent from --transition choices."""
    parser = sm.build_parser()
    choices = [
        action.choices for action in parser._actions
        if action.dest == "transition"
    ][0]
    old, new = _solid(BLACK), _solid(WHITE)
    for mode in choices:
        out = sm.blend_frames(old, new, 0.5, mode=mode)
        assert out.size == (64, 64), f"{mode} did not render"


# ── settings persistence ─────────────────────────────────────────────

def test_settings_round_trip(tmp_path):
    path = tmp_path / "settings.json"
    state = sm.SharedPlaybackState()
    state.brightness = 42
    state.idle_mode = "fire"
    state.accent_name = "neon"
    state.accent_color = (180, 60, 255)
    state.cd_duration = 25.0
    sm.save_settings(path, state, threading.Lock())

    restored = sm.SharedPlaybackState()
    assert sm.apply_saved_settings(path, restored) is True
    assert restored.brightness == 42
    assert restored.idle_mode == "fire"
    assert restored.accent_color == (180, 60, 255)
    assert restored.cd_duration == 25.0


def test_settings_round_trip_contrast(tmp_path):
    path = tmp_path / "settings_contrast.json"
    state = sm.SharedPlaybackState()
    state.accent_name = "contrast"
    state.contrast_accent_color = (0, 240, 250)
    state.accent_color = (0, 240, 250)
    sm.save_settings(path, state, threading.Lock())

    restored = sm.SharedPlaybackState()
    assert sm.apply_saved_settings(path, restored) is True
    assert restored.accent_name == "contrast"
    assert restored.contrast_accent_color == (0, 240, 250)
    assert restored.accent_color == (0, 240, 250)


def _write_settings(path, body: dict) -> None:
    """Write a settings file stamped with the current schema version."""
    import json
    body.setdefault("version", sm.SETTINGS_VERSION)
    path.write_text(json.dumps(body))


def test_settings_clamps_out_of_range_values(tmp_path):
    path = tmp_path / "settings.json"
    _write_settings(path, {"brightness": 99999, "cd_duration": -5})
    state = sm.SharedPlaybackState()
    sm.apply_saved_settings(path, state)
    assert state.brightness == 100
    assert state.cd_duration == 2.0


def test_settings_rejects_unknown_enum_values(tmp_path):
    path = tmp_path / "settings.json"
    _write_settings(path, {"idle_mode": "../../etc/passwd", "lyrics_style": "nope"})
    state = sm.SharedPlaybackState()
    sm.apply_saved_settings(path, state)
    assert state.idle_mode == "clock"
    assert state.lyrics_style == "scroll"


def test_settings_never_restores_custom_slate_mode(tmp_path):
    """The slate image is not persisted, so booting into it shows nothing."""
    path = tmp_path / "settings.json"
    _write_settings(path, {"display_mode": "custom"})
    state = sm.SharedPlaybackState()
    sm.apply_saved_settings(path, state)
    assert state.display_mode == "default"


def test_settings_from_a_foreign_schema_is_ignored_wholesale(tmp_path):
    """A real file from an earlier experiment was found in .cache/.

    Its keys partly overlap ours ('idle_mode': 'plasma') but 'display_mode'
    was 'auto_screensaver', which means nothing here. Half-applying it would
    bring the panel up looking wrong with nothing to explain why.
    """
    path = tmp_path / "settings.json"
    path.write_text(
        '{"brightness": 82, "display_mode": "auto_screensaver",'
        ' "idle_mode": "plasma", "spin_speed": 45.0, "saved_at": 1787638199.0}'
    )
    state = sm.SharedPlaybackState()
    assert sm.apply_saved_settings(path, state) is False
    assert state.idle_mode == "clock"
    assert state.brightness == 65
    assert state.spin_speed == 10.0


def test_saved_settings_carry_a_version(tmp_path):
    import json
    path = tmp_path / "settings.json"
    sm.save_settings(path, sm.SharedPlaybackState(), threading.Lock())
    assert json.loads(path.read_text())["version"] == sm.SETTINGS_VERSION


def test_settings_survives_a_corrupt_file(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{ this is not json")
    state = sm.SharedPlaybackState()
    assert sm.apply_saved_settings(path, state) is False
    assert state.brightness == 65


def test_settings_missing_file_is_not_an_error(tmp_path):
    assert sm.apply_saved_settings(tmp_path / "absent.json", sm.SharedPlaybackState()) is False


def test_save_settings_is_atomic(tmp_path):
    """No .tmp file may survive a completed write."""
    path = tmp_path / "settings.json"
    sm.save_settings(path, sm.SharedPlaybackState(), threading.Lock())
    assert path.exists()
    assert list(tmp_path.glob("*.tmp")) == []


# ── accent extraction ────────────────────────────────────────────────

def test_extract_accent_color_is_vivid_and_visible():
    """F7: a colour that reads on a screen can vanish on 64 dim LEDs."""
    from PIL import Image
    art = Image.new("RGB", (64, 64), (120, 20, 20))  # dull dark red
    r, g, b = sm.extract_accent_color(art, None)
    assert max(r, g, b) >= 190, "should be brightened for the panel"
    assert r > g and r > b, "should stay recognisably red"


def test_extract_accent_color_handles_all_black_art():
    from PIL import Image
    black = Image.new("RGB", (64, 64), (0, 0, 0))
    assert sm.extract_accent_color(black, None) == sm.SPOTIFY_GREEN


def test_extract_accent_color_caches_by_track_key():
    from PIL import Image
    first = sm.extract_accent_color(Image.new("RGB", (8, 8), (200, 30, 30)), "k1")
    # Same key, different art: the cached value must win.
    second = sm.extract_accent_color(Image.new("RGB", (8, 8), (30, 30, 200)), "k1")
    assert first == second


def test_extract_contrast_accent_color_against_red():
    """Red album art must contrast cleanly with a vivid cyan/aqua."""
    from PIL import Image
    red_art = Image.new("RGB", (64, 64), (210, 25, 25))
    r, g, b = sm.extract_contrast_accent_color(red_art, None)
    assert max(g, b) > r, "should complementary shift away from red toward cyan"
    assert max(r, g, b) >= 190, "must be vivid and bright enough to read clearly"


def test_extract_contrast_accent_color_against_blue():
    """Blue album art must contrast cleanly with a warm gold/amber."""
    from PIL import Image
    blue_art = Image.new("RGB", (64, 64), (25, 45, 210))
    r, g, b = sm.extract_contrast_accent_color(blue_art, None)
    assert r > b, "should shift away from blue toward warm gold"
    assert max(r, g, b) >= 190, "must be vivid and bright"


def test_extract_contrast_accent_color_handles_monochrome():
    """Dark monochrome yields bright cyan, light yields cobalt."""
    from PIL import Image
    black = Image.new("RGB", (64, 64), (0, 0, 0))
    white = Image.new("RGB", (64, 64), (255, 255, 255))
    c_black = sm.extract_contrast_accent_color(black, None)
    c_white = sm.extract_contrast_accent_color(white, None)
    assert c_black == (0, 225, 235), "black art yields vivid electric cyan"
    assert c_white == (35, 75, 220), "white art yields deep cobalt"


def test_extract_contrast_accent_color_caches_by_track_key():
    from PIL import Image
    first = sm.extract_contrast_accent_color(Image.new("RGB", (8, 8), (200, 30, 30)), "ck1")
    second = sm.extract_contrast_accent_color(Image.new("RGB", (8, 8), (30, 30, 200)), "ck1")
    assert first == second


def test_render_record_with_border_color():
    from PIL import Image
    art = Image.new("RGB", (64, 64), (200, 20, 20))
    img = sm.render_record(art, 0.0, 64, border_color=(0, 240, 250), progress=0.4, line_width=3)
    assert isinstance(img, Image.Image)
    assert img.size == (64, 64)


def test_render_full_art_with_progress_color():
    from PIL import Image
    art = Image.new("RGB", (64, 64), (200, 20, 20))
    img = sm.render_full_art(art, 64, progress=0.5, progress_color=(0, 240, 250), line_width=3)
    assert isinstance(img, Image.Image)
    assert img.size == (64, 64)


def test_get_contained_art_square():
    from PIL import Image
    art = Image.new("RGB", (300, 300), (255, 0, 0))
    contained = sm._get_contained_art(art, "sq1", 64)
    assert contained.size == (64, 64)
    assert contained.getpixel((0, 0)) == (255, 0, 0)
    assert contained.getpixel((63, 63)) == (255, 0, 0)


def test_get_contained_art_wide_landscape_with_black_bars():
    from PIL import Image
    # 120x60 (2:1 aspect ratio) inside 64x64 -> should scale to 64x32 with 16px top & bottom black bars
    art = Image.new("RGB", (120, 60), (255, 0, 0))
    contained = sm._get_contained_art(art, "wide1", 64)
    assert contained.size == (64, 64)
    # Top and bottom rows must be black bars
    assert contained.getpixel((32, 5)) == (0, 0, 0)
    assert contained.getpixel((32, 60)) == (0, 0, 0)
    # Center must be red
    assert contained.getpixel((32, 32)) == (255, 0, 0)


def test_get_contained_art_tall_portrait_with_black_bars():
    from PIL import Image
    # 60x120 (1:2 aspect ratio) inside 64x64 -> should scale to 32x64 with 16px left & right black bars
    art = Image.new("RGB", (60, 120), (0, 0, 255))
    contained = sm._get_contained_art(art, "tall1", 64)
    assert contained.size == (64, 64)
    # Left and right columns must be black bars
    assert contained.getpixel((5, 32)) == (0, 0, 0)
    assert contained.getpixel((60, 32)) == (0, 0, 0)
    # Center must be blue
    assert contained.getpixel((32, 32)) == (0, 0, 255)


def test_settings_round_trip_line_width(tmp_path):
    path = tmp_path / "settings.json"
    state = sm.SharedPlaybackState()
    state.line_width = 3
    state.art_pan = False
    sm.save_settings(path, state, threading.Lock())

    loaded = sm.SharedPlaybackState()
    assert sm.apply_saved_settings(path, loaded) is True
    assert loaded.line_width == 3
    assert loaded.art_pan is False


# ── poll failure classification ──────────────────────────────────────

def test_classify_poll_failure_recognises_auth_errors():
    from urllib.error import HTTPError
    exc = HTTPError("u", 401, "Unauthorized", None, None)
    headline, detail = sm._classify_poll_failure(exc)
    assert "auth" in headline.lower()
    assert "auth" in detail


def test_classify_poll_failure_recognises_network_errors():
    import socket
    headline, _ = sm._classify_poll_failure(socket.gaierror("no dns"))
    assert headline == "No Wi-Fi"


def test_classify_poll_failure_has_a_fallback():
    headline, detail = sm._classify_poll_failure(ValueError("something odd"))
    assert headline and detail


# ── idle screens ─────────────────────────────────────────────────────

@pytest.mark.parametrize("mode", sm.IDLE_MODES)
def test_every_idle_mode_renders(mode):
    screen = sm.IdleScreen()
    frame = None
    for i in range(3):
        frame = screen.render(64, mode, True, sm.SPOTIFY_GREEN, 0.05, 100.0 + i * 0.05)
    assert frame.size == (64, 64)
    assert frame.mode == "RGB"


def test_idle_modes_and_display_modes_match_the_api():
    """Anything the render loop can show must be settable, and vice versa."""
    for mode in sm.IDLE_MODES:
        assert mode in sm.CONTROL_PANEL_HTML, f"{mode} has no UI option"
    for mode in sm.DISPLAY_MODES:
        assert f"mode-{mode}" in sm.CONTROL_PANEL_HTML, f"{mode} has no UI button"
