#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import collections
import colorsys
import datetime
import functools
from io import BytesIO
import json
import math
import os
import random
import re
import secrets
import signal
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
from email.message import Message
from urllib.error import HTTPError, URLError
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> None:
        return None


AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
CURRENTLY_PLAYING_URL = "https://api.spotify.com/v1/me/player/currently-playing"
PLAYER_API_URL = "https://api.spotify.com/v1/me/player"
QUEUE_API_URL = "https://api.spotify.com/v1/me/player/queue"

# Playback control and queue peek need scopes beyond reading the current track.
# They are requested unconditionally so a fresh authorization picks them up;
# an existing token simply lacks them until the next `--auth-only`, and the
# features that need them report that rather than failing silently.
SCOPE = " ".join((
    "user-read-currently-playing",
    "user-read-playback-state",
    "user-modify-playback-state",
))

LRCLIB_API_URL = "https://lrclib.net/api/get"
LRCLIB_SEARCH_URL = "https://lrclib.net/api/search"
LRCLIB_USER_AGENT = "SpotifyMatrix/1.0 (https://github.com/Adi-Shinde/SpotifyMatrix)"

SPOTIFY_GREEN = (30, 215, 96)
LYRIC_DIM_COLOR = (80, 80, 80)

COLOR_THEMES: dict[str, tuple[int, int, int]] = {
    "spotify":  (30, 215, 96),
    "sunset":   (255, 107, 53),
    "neon":     (180, 60, 255),
    "rose":     (255, 90, 150),
    "arctic":   (0, 220, 220),
    "gold":     (245, 180, 40),
    "crimson":  (220, 40, 60)
}

# What the panel can be pinned to. "default" is the original appliance
# behaviour — idle screen when stopped, disc for the first seconds of a track,
# then lyrics — and stays the default so an upgrade changes nothing on its own.
DISPLAY_MODES = ("default", "cd", "lyrics", "art", "clock", "custom")

# What fills the screen when nothing is playing. "clock" is the original.
# "cycle" rotates through the animated ones every IDLE_CYCLE_SECONDS.
IDLE_MODES = ("clock", "plasma", "rain", "stars", "life", "fire", "cycle")
IDLE_ANIMATED = ("plasma", "rain", "stars", "life", "fire")
IDLE_CYCLE_SECONDS = 120.0

# Average ms per spoken word — used to cap scroll speed during instrumental gaps
AVG_MS_PER_WORD = 350

# Alpha cutoff when stripping font anti-aliasing. See draw_text_batch — this is
# measured, not guessed: 60 keeps every stroke of the stock font at size 7,
# 110 eats thin stems, 140 destroys the glyphs.
CRISP_THRESHOLD = 60

# Web panel request limits. The panel is unauthenticated on the LAN, so an
# accidental or hostile oversized upload must not be able to OOM the Pi.
MAX_REQUEST_BYTES = 8 * 1024 * 1024
# Upper bound on how much of an oversized body we will read and throw away in
# order to answer 413 cleanly. Past this we let the connection drop.
MAX_DRAIN_BYTES = 64 * 1024 * 1024
MAX_SLATE_FRAMES = 120

# Spotify poll cadence (seconds). Module-level so the exception handlers in
# poll_spotify cannot reference it before assignment.
POLL_ACTIVE_SECONDS = 5.0
POLL_IDLE_SECONDS = 30.0

# Brightness units per second when easing toward a new target.
BRIGHTNESS_RAMP_PER_SEC = 120.0

# Preferred album-art download size. Above the 64px panel so rotation has
# resampling headroom, but far below Spotify's 640px original — which cost a
# slow download and a full-size decode for detail the panel cannot show.
# Spotify serves 640/300/64, so this selects the 300px variant.
ART_TARGET_PX = 160


# ═══════════════════════════════════════════════════════════════════
#  LOGGING — In-memory ring buffer + console output
# ═══════════════════════════════════════════════════════════════════

class LogBuffer:
    """Thread-safe in-memory ring buffer for log messages."""

    def __init__(self, maxlen: int = 200) -> None:
        self._buffer: collections.deque[dict[str, str]] = collections.deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def add(self, msg: str, level: str = "info") -> None:
        now = datetime.datetime.now().strftime("%H:%M:%S")
        entry = {"time": now, "msg": msg, "level": level}
        with self._lock:
            self._buffer.append(entry)

    def get_all(self) -> list[dict[str, str]]:
        with self._lock:
            return list(self._buffer)

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()


# Global log buffer instance
_log_buffer = LogBuffer(maxlen=200)
_is_interactive = sys.stdout.isatty()


def log(msg: str, level: str = "info", *, console: bool = True, verbose: bool = False) -> None:
    """
    Log a message to the ring buffer and optionally to console.

    Args:
        msg: The log message.
        level: "info", "warn", or "error".
        console: If True, also print to stdout (always True for important events).
        verbose: If True, this is a verbose/tick message. Only printed in interactive mode.
    """
    _log_buffer.add(msg, level)
    if console:
        if verbose and not _is_interactive:
            # In auto/systemd mode, skip verbose tick messages
            return
        print(msg, flush=True)


# ═══════════════════════════════════════════════════════════════════
#  DATA MODELS
# ═══════════════════════════════════════════════════════════════════

@dataclass
class PlaybackArt:
    key: str
    image_url: str
    is_playing: bool
    title: str = ""
    artist: str = ""
    album_name: str = ""
    progress_ms: int = 0
    duration_ms: int = 0


@dataclass
class SharedPlaybackState:
    art_key: str | None = None
    image_url: str | None = None
    image: Image.Image | None = None
    is_playing: bool = False
    title: str = ""
    artist: str = ""
    album_name: str = ""
    is_connected: bool = True
    # Why the panel is not showing music, in words, for the on-matrix status
    # screen. Empty means "nothing to explain" — the normal case.
    status_message: str = "Connecting"
    status_detail: str = ""
    # Time sync fields
    progress_ms: int = 0
    duration_ms: int = 0
    fetch_time: float = 0.0  # time.monotonic() when Spotify data was fetched
    # Smoothed correction for systematic lag in Spotify's reported progress,
    # measured by comparing each poll against what we extrapolated. Keeps drift
    # from accumulating so lyrics_lead_ms can be a taste control, not a patch.
    progress_offset_ms: float = 0.0
    # Lyrics
    lyrics: list[tuple[int, str]] | None = None  # [(timestamp_ms, text), ...]
    # Per-line [(word, start_ms), ...] when the source is enhanced LRC; empty
    # lists otherwise, in which case karaoke mode interpolates word timing.
    lyrics_words: list[list[tuple[str, int]]] = field(default_factory=list)
    lyrics_track_key: str | None = None
    is_instrumental: bool = False  # True when LRCLIB says track is instrumental
    lyrics_lead_ms: int = 180  # ms to shift lyrics ahead for read-along
    # Accent color
    accent_color: tuple[int, int, int] = (30, 215, 96)  # default SPOTIFY_GREEN
    accent_name: str = "spotify"
    # Contrast color derived from artwork (used for CD borders, hairline progress bar, or globally when chosen)
    contrast_accent_color: tuple[int, int, int] = (0, 220, 220)
    # Custom Slate mode
    custom_slate_frames: list[Image.Image] = field(default_factory=list)
    custom_slate_frame_delay: float = 0.1
    # Up-next track title, filled in during the last seconds of the current one.
    queue_next: str = ""
    # Last frame handed to the panel, for the web preview. Rebound wholesale by
    # the render loop (atomic under the GIL), so readers need no lock.
    last_frame: Image.Image | None = None
    # Runtime-adjustable settings
    display_mode: str = "default"  # see DISPLAY_MODES
    effective_mode: str = "cd"  # what is actually rendering right now
    # What replaces the clock when nothing is playing. "clock" reproduces the
    # original appliance behaviour exactly; the rest are ambient animations.
    idle_mode: str = "clock"  # see IDLE_MODES
    lyrics_style: str = "scroll"  # "scroll", "pop" or "karaoke"
    smart_scroll: bool = True  # time-proportional horizontal scrolling
    scroll_font_size: int = 9  # font size for scroll mode
    pop_font_size: int = 9  # font size for pop mode
    spin_speed: float = 10.0  # RPM
    text_scroll_speed: float = 20.0  # px/s
    brightness: int = 65  # 1-100
    # Seconds the auto-cycling "default" mode shows the disc before lyrics.
    cd_duration: float = 10.0
    progress_ring: bool = True  # thin arc around the disc showing track position
    art_pan: bool = False  # Ken Burns drift in full-bleed art mode (disabled by default)
    line_width: int = 1  # 1-5 px border/progress line width for CD and full art
    sleeping: bool = False  # panel blanked on request
    # Boot defaults (for reset)
    _default_brightness: int = 65
    _default_spin_speed: float = 10.0
    _default_text_scroll_speed: float = 20.0
    _default_lyrics_style: str = "scroll"
    _default_scroll_font_size: int = 9
    _default_pop_font_size: int = 9
    _default_line_width: int = 1


# ═══════════════════════════════════════════════════════════════════
#  SETTINGS PERSISTENCE
# ═══════════════════════════════════════════════════════════════════

# Fields written to disk and restored at boot. Everything here is something a
# person can change from the panel; derived and per-track state is excluded on
# purpose so a stale settings file can never pin the display to an old song.
PERSISTED_FIELDS: tuple[str, ...] = (
    "display_mode",
    "idle_mode",
    "lyrics_style",
    "smart_scroll",
    "scroll_font_size",
    "pop_font_size",
    "spin_speed",
    "text_scroll_speed",
    "brightness",
    "lyrics_lead_ms",
    "accent_name",
    "accent_color",
    "contrast_accent_color",
    "line_width",
    "cd_duration",
    "progress_ring",
    "art_pan",
)

# Bumped whenever the meaning of a saved field changes. A file from a
# different schema is ignored wholesale rather than half-applied: keys that
# happen to overlap would otherwise restore a display state the rest of the
# file no longer describes, and the panel comes up looking wrong for reasons
# nothing explains.
SETTINGS_VERSION = 1

# How long to wait after the last change before writing. Dragging a slider
# fires a request per step; without this the SD card takes the whole sweep.
SETTINGS_DEBOUNCE_SECONDS = 2.0

# POST endpoints whose effect should outlive a restart. Everything else — slate
# uploads, log clears, playback commands — is deliberately transient.
PERSISTING_ENDPOINTS: frozenset[str] = frozenset({
    "/api/mode",
    "/api/idle-mode",
    "/api/brightness",
    "/api/spin-speed",
    "/api/text-speed",
    "/api/lyrics-style",
    "/api/smart-scroll",
    "/api/scroll-font-size",
    "/api/pop-font-size",
    "/api/accent-color",
    "/api/lyrics-lead",
    "/api/cd-duration",
    "/api/progress-ring",
    "/api/art-pan",
    "/api/line-width",
    "/api/border-width",
    "/api/reset",
})

_settings_dirty = threading.Event()


def mark_settings_dirty() -> None:
    """Flag that a persisted field changed. The saver thread does the write."""
    _settings_dirty.set()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON so a power cut cannot leave a truncated file behind.

    Same reasoning as the token cache: this device is designed to be unplugged,
    so every write it makes must be all-or-nothing.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def save_settings(path: Path, state: SharedPlaybackState, lock: threading.Lock) -> None:
    with lock:
        payload: dict[str, Any] = {name: getattr(state, name) for name in PERSISTED_FIELDS}
    payload["version"] = SETTINGS_VERSION
    # Tuples survive a round-trip as lists; normalise now so the loader does not
    # have to care which it is reading.
    accent = payload.get("accent_color")
    if isinstance(accent, tuple):
        payload["accent_color"] = list(accent)
    contrast = payload.get("contrast_accent_color")
    if isinstance(contrast, tuple):
        payload["contrast_accent_color"] = list(contrast)
    try:
        _atomic_write_json(path, payload)
    except OSError as exc:
        log(f"Settings: could not save to {path}: {exc}", "warn")


def apply_saved_settings(path: Path, state: SharedPlaybackState) -> bool:
    """Load saved settings over `state`. Returns True if anything was applied.

    Every value is validated against the same bounds the web API enforces — a
    hand-edited or partially-written file must not be able to put the renderer
    into a state the UI cannot reach.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return False
    except (OSError, json.JSONDecodeError) as exc:
        log(f"Settings: ignoring unreadable {path}: {exc}", "warn")
        return False

    if not isinstance(data, dict):
        return False

    if data.get("version") != SETTINGS_VERSION:
        log(
            f"Settings: {path} is from a different format "
            f"(version {data.get('version')!r}) — starting from defaults. "
            "It will be overwritten on the next change.",
            "warn",
        )
        return False

    def _clamp(value: Any, lo: float, hi: float, cast: Any) -> Any | None:
        # OverflowError matters more here than anywhere else: this runs during
        # startup, so a settings file containing 1e400 (which JSON parses as
        # float inf, and int(inf) rejects) would take the whole appliance down
        # before the web panel exists to fix it from.
        try:
            out = cast(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if out != out or out in (float("inf"), float("-inf")):  # NaN or inf
            return None
        return max(lo, min(hi, out))

    applied = False

    if data.get("display_mode") in DISPLAY_MODES and data["display_mode"] != "custom":
        # "custom" is deliberately not restorable: the slate image itself is not
        # persisted, so booting into it would show an empty screen.
        state.display_mode = data["display_mode"]
        applied = True
    if data.get("idle_mode") in IDLE_MODES:
        state.idle_mode = data["idle_mode"]
        applied = True
    if data.get("lyrics_style") in ("scroll", "pop", "karaoke"):
        state.lyrics_style = data["lyrics_style"]
        applied = True
    if isinstance(data.get("smart_scroll"), bool):
        state.smart_scroll = data["smart_scroll"]
        applied = True
    if isinstance(data.get("progress_ring"), bool):
        state.progress_ring = data["progress_ring"]
        applied = True
    if isinstance(data.get("art_pan"), bool):
        state.art_pan = data["art_pan"]
        applied = True

    for name, lo, hi, cast in (
        ("scroll_font_size", 6, 14, int),
        ("pop_font_size", 6, 14, int),
        ("spin_speed", 1.0, 120.0, float),
        ("text_scroll_speed", 1.0, 100.0, float),
        ("brightness", 1, 100, int),
        ("lyrics_lead_ms", 0, 500, int),
        ("cd_duration", 2.0, 120.0, float),
        ("line_width", 1, 5, int),
    ):
        if name in data:
            value = _clamp(data[name], lo, hi, cast)
            if value is not None:
                setattr(state, name, value)
                applied = True

    raw_contrast = data.get("contrast_accent_color")
    if isinstance(raw_contrast, (list, tuple)) and len(raw_contrast) == 3:
        channels = [_clamp(c, 0, 255, int) for c in raw_contrast]
        if all(c is not None for c in channels):
            state.contrast_accent_color = (channels[0], channels[1], channels[2])
            applied = True

    accent_name = data.get("accent_name")
    if accent_name in COLOR_THEMES:
        state.accent_name = accent_name
        state.accent_color = COLOR_THEMES[accent_name]
        applied = True
    elif accent_name in ("custom", "auto"):
        raw = data.get("accent_color")
        if isinstance(raw, (list, tuple)) and len(raw) == 3:
            channels = [_clamp(c, 0, 255, int) for c in raw]
            if all(c is not None for c in channels):
                state.accent_name = accent_name
                state.accent_color = (channels[0], channels[1], channels[2])
                applied = True
    elif accent_name == "contrast":
        state.accent_name = "contrast"
        state.accent_color = state.contrast_accent_color
        applied = True

    return applied


def settings_saver(
    path: Path,
    state: SharedPlaybackState,
    lock: threading.Lock,
    stop_event: threading.Event,
) -> None:
    """Flush settings SETTINGS_DEBOUNCE_SECONDS after the last change."""
    while not stop_event.is_set():
        if not _settings_dirty.wait(timeout=1.0):
            continue
        # Coalesce a burst of changes (a slider drag) into one write.
        while not stop_event.is_set():
            _settings_dirty.clear()
            if not _settings_dirty.wait(timeout=SETTINGS_DEBOUNCE_SECONDS):
                break
        save_settings(path, state, lock)
    # Final flush so a change made moments before shutdown is not lost.
    if _settings_dirty.is_set():
        save_settings(path, state, lock)


@dataclass
class HttpResponse:
    status: int
    headers: Message
    body: bytes

    def json(self) -> dict[str, Any]:
        return json.loads(self.body.decode("utf-8"))


# ═══════════════════════════════════════════════════════════════════
#  HTTP UTILITIES
# ═══════════════════════════════════════════════════════════════════

def http_request(
    method: str,
    url: str,
    *,
    params: dict[str, str] | None = None,
    data: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10,
) -> HttpResponse:
    if params:
        separator = "&" if urllib.parse.urlparse(url).query else "?"
        url = f"{url}{separator}{urllib.parse.urlencode(params)}"

    encoded_data = urllib.parse.urlencode(data).encode("utf-8") if data else None
    request = urllib.request.Request(
        url,
        data=encoded_data,
        headers=headers or {},
        method=method,
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return HttpResponse(response.status, response.headers, response.read())
    except HTTPError as exc:
        return HttpResponse(exc.code, exc.headers, exc.read())


def raise_http_error(response: HttpResponse, context: str) -> None:
    body = response.body.decode("utf-8", errors="replace")
    raise RuntimeError(f"{context} failed with HTTP {response.status}: {body}")


class RateLimitException(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__(f"Spotify API rate limited. Retry after {retry_after}s.")
        self.retry_after = retry_after


# ═══════════════════════════════════════════════════════════════════
#  SPOTIFY CLIENT
# ═══════════════════════════════════════════════════════════════════

class SpotifyClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        token_cache: Path,
        open_browser: bool,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.token_cache = token_cache
        self.open_browser = open_browser
        self.token = self._load_token()
        if self.token:
            log(f"Spotify: Token loaded from {token_cache}")
        else:
            log("Spotify: No token found in cache")

    def get_currently_playing(self, *, _retried: bool = False) -> dict[str, Any] | None:
        token = self._valid_access_token()
        response = http_request(
            "GET",
            CURRENTLY_PLAYING_URL,
            params={"additional_types": "track,episode"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
        if _is_interactive or response.status != 200:
            log(f"Spotify API: HTTP {response.status}", verbose=True)

        if response.status == 204:
            return None
        if response.status == 401:
            # Refresh and retry exactly once. Recursing unconditionally here
            # spins forever when the refreshed token is still rejected
            # (revoked app authorization, changed scopes, disabled account).
            if _retried:
                raise RuntimeError(
                    "Spotify rejected the refreshed access token (401). "
                    "The authorization was likely revoked — re-run with --auth-only."
                )
            log("Spotify API: Token expired (401), refreshing...", "warn")
            self._refresh_access_token()
            return self.get_currently_playing(_retried=True)
        if response.status == 429:
            retry_after = int(response.headers.get("Retry-After", "0"))
            log(f"Spotify API: Rate limited (429)! Wait {retry_after}s.", "error")
            raise RateLimitException(retry_after)
        if response.status != 200:
            raise_http_error(response, "Spotify currently-playing request")

        return response.json()

    def playback_command(self, action: str) -> tuple[bool, str]:
        """Send a transport command. Returns (ok, human-readable message).

        Errors are returned rather than raised: this is driven by a button on
        a phone, and "no active device" is a normal thing to report, not a
        failure worth breaking the poll loop over.
        """
        endpoints = {
            "play": ("PUT", f"{PLAYER_API_URL}/play"),
            "pause": ("PUT", f"{PLAYER_API_URL}/pause"),
            "next": ("POST", f"{PLAYER_API_URL}/next"),
            "previous": ("POST", f"{PLAYER_API_URL}/previous"),
        }
        if action not in endpoints:
            return False, f"Unknown action '{action}'"

        method, url = endpoints[action]
        token = self._valid_access_token()
        response = http_request(
            method, url, headers={"Authorization": f"Bearer {token}"}, timeout=5
        )
        if response.status == 401:
            self._refresh_access_token()
            token = self._valid_access_token()
            response = http_request(
                method, url, headers={"Authorization": f"Bearer {token}"}, timeout=5
            )

        # 204 is the documented success; 202 shows up when the device is still
        # waking. Both mean the command was accepted.
        if response.status in (200, 202, 204):
            return True, action
        if response.status == 403:
            return False, "Not authorized — re-run --auth-only to grant playback control"
        if response.status == 404:
            return False, "No active Spotify device — start playback somewhere first"
        if response.status == 429:
            return False, "Rate limited by Spotify, try again shortly"
        return False, f"Spotify returned HTTP {response.status}"

    def get_queue(self) -> list[dict[str, Any]]:
        """Upcoming tracks, or [] if the scope is missing or nothing is queued."""
        token = self._valid_access_token()
        response = http_request(
            "GET", QUEUE_API_URL,
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
        if response.status != 200:
            return []
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError):
            return []
        queue = payload.get("queue")
        return queue if isinstance(queue, list) else []

    def authorize(self) -> None:
        self._valid_access_token()

    def _valid_access_token(self) -> str:
        if not self.token:
            self.token = self._authorize()

        if time.time() >= float(self.token.get("expires_at", 0)):
            self._refresh_access_token()

        return str(self.token["access_token"])

    def _load_token(self) -> dict[str, Any] | None:
        if not self.token_cache.exists():
            return None

        # A truncated or corrupt cache must not be fatal. This file is written
        # on a Pi that gets unplugged without shutting down, so a zero-byte or
        # half-written token is a realistic state. Crashing here happens before
        # the web panel starts, so systemd would restart into the same crash
        # forever with no way to see why.
        try:
            with self.token_cache.open("r", encoding="utf-8") as token_file:
                token = json.load(token_file)
        except (OSError, ValueError) as exc:
            log(f"Spotify: Token cache unreadable ({exc}) — re-authorization needed.", "error")
            return None

        if not isinstance(token, dict) or "access_token" not in token:
            log("Spotify: Token cache malformed — re-authorization needed.", "error")
            return None
        return token

    def _save_token(self, token: dict[str, Any]) -> None:
        self.token_cache.parent.mkdir(parents=True, exist_ok=True)
        token["expires_at"] = time.time() + int(token.get("expires_in", 3600)) - 60

        previous_refresh_token = self.token.get("refresh_token") if self.token else None
        if previous_refresh_token and "refresh_token" not in token:
            token["refresh_token"] = previous_refresh_token

        # Atomic write: truncating the real file first means a power cut mid-write
        # leaves an empty token and forces a full re-auth. Write a temp file,
        # fsync it, then rename — os.replace is atomic on POSIX.
        tmp_path = self.token_cache.with_suffix(self.token_cache.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as token_file:
            json.dump(token, token_file, indent=2)
            token_file.flush()
            os.fsync(token_file.fileno())
        os.replace(tmp_path, self.token_cache)

        # NOTE: these permissions are deliberately loose because --auth-only runs
        # as the login user while the systemd service runs as root, and both need
        # to read and rewrite this file. os.replace takes the temp file's mode, so
        # the chmod has to happen after the rename, not before.
        # Tighten to 0600 only together with running the service as a non-root
        # user (see IMPROVEMENTS.md B16, Phase 5) — doing it alone breaks re-auth.
        try:
            os.chmod(self.token_cache, 0o666)
            os.chmod(self.token_cache.parent, 0o777)
        except OSError:
            pass

        self.token = token

    def _authorize(self) -> dict[str, Any]:
        state = secrets.token_urlsafe(18)
        parsed_redirect = urllib.parse.urlparse(self.redirect_uri)
        if parsed_redirect.hostname not in {"127.0.0.1", "localhost"}:
            raise RuntimeError("This script expects a localhost Spotify redirect URI.")

        callback = LocalCallbackServer(
            host=parsed_redirect.hostname or "127.0.0.1",
            port=parsed_redirect.port or 80,
            path=parsed_redirect.path or "/callback",
            expected_state=state,
        )

        query = urllib.parse.urlencode(
            {
                "client_id": self.client_id,
                "response_type": "code",
                "redirect_uri": self.redirect_uri,
                "scope": SCOPE,
                "state": state,
            }
        )
        auth_url = f"{AUTH_URL}?{query}"

        print("Authorize Spotify in your browser:")
        print(auth_url)
        if self.open_browser:
            webbrowser.open(auth_url)

        code = callback.wait_for_code()
        token = self._post_token(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
            }
        )
        self._save_token(token)
        return token

    def _refresh_access_token(self) -> None:
        log("Spotify: Refreshing access token...")
        refresh_token = self.token.get("refresh_token") if self.token else None
        if not refresh_token:
            self.token = self._authorize()
            return

        token = self._post_token(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            }
        )
        self._save_token(token)

    def _post_token(self, data: dict[str, str]) -> dict[str, Any]:
        credentials = f"{self.client_id}:{self.client_secret}".encode("utf-8")
        basic_auth = base64.b64encode(credentials).decode("ascii")
        response = http_request(
            "POST",
            TOKEN_URL,
            data=data,
            headers={
                "Authorization": f"Basic {basic_auth}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=10,
        )
        if response.status != 200:
            raise_http_error(response, "Spotify token request")
        return response.json()


class LocalCallbackServer:
    def __init__(self, host: str, port: int, path: str, expected_state: str) -> None:
        self.code: str | None = None
        self.error: str | None = None
        self.state_error: str | None = None
        self.path = path
        self.expected_state = expected_state

        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                parsed = urllib.parse.urlparse(self.path)
                params = urllib.parse.parse_qs(parsed.query)

                if parsed.path != parent.path:
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(b"Wrong callback path.")
                    return

                returned_state = params.get("state", [""])[0]
                if returned_state != parent.expected_state:
                    parent.state_error = "Spotify callback state did not match."
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"State mismatch.")
                    return

                if "error" in params:
                    parent.error = params["error"][0]
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"Spotify authorization failed.")
                    return

                parent.code = params.get("code", [None])[0]
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"Spotify authorization complete. You can close this tab.")

            def log_message(self, format: str, *args: Any) -> None:
                return

        self.server = HTTPServer((host, port), Handler)

    def wait_for_code(self) -> str:
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        try:
            while not self.code and not self.error and not self.state_error:
                time.sleep(0.1)
        finally:
            self.server.shutdown()
            self.server.server_close()

        if self.state_error:
            raise RuntimeError(self.state_error)
        if self.error:
            raise RuntimeError(f"Spotify authorization failed: {self.error}")
        if not self.code:
            raise RuntimeError("Spotify authorization did not return a code.")
        return self.code


# ═══════════════════════════════════════════════════════════════════
#  DISPLAY BACKENDS
# ═══════════════════════════════════════════════════════════════════

@functools.lru_cache(maxsize=8)
def _gamma_table(gamma: float) -> tuple[int, ...]:
    """256*3 lookup table for Image.point(), or empty for a no-op gamma.

    LED PWM output is linear but sRGB pixel values are perceptually encoded, so
    sending them straight to the panel makes midtones read too bright and
    crushes shadows. Decoding to linear with an exponent fixes it.

    Note: recent versions of the hzeller library apply their own CIE1931
    luminance curve. Stacking both double-corrects and destroys shadow detail,
    which is why the default is 1.0 (off) — measure on your panel, then pick a
    value (1.6-2.2 is the useful range) with --gamma.
    """
    if abs(gamma - 1.0) < 0.01:
        return ()
    ramp = [min(255, int(((i / 255.0) ** gamma) * 255.0 + 0.5)) for i in range(256)]
    return tuple(ramp * 3)


def apply_gamma(image: Image.Image, gamma: float) -> Image.Image:
    table = _gamma_table(gamma)
    if not table:
        return image
    return image.point(list(table))


def enhance_album_art(
    image: Image.Image, saturation: float, contrast: float
) -> Image.Image:
    """Boost art once at download time — never per frame.

    A 64x64 crop of a subtle album cover loses most of its separation. A mild
    saturation and contrast lift restores it, and because this runs once per
    track it is effectively free.
    """
    if abs(saturation - 1.0) > 0.01:
        image = ImageEnhance.Color(image).enhance(saturation)
    if abs(contrast - 1.0) > 0.01:
        image = ImageEnhance.Contrast(image).enhance(contrast)
    return image


class MatrixDisplay:
    def __init__(self, args: argparse.Namespace) -> None:
        try:
            from rgbmatrix import RGBMatrix, RGBMatrixOptions
        except ImportError as exc:
            raise RuntimeError(
                "The rgbmatrix Python bindings are not installed. "
                "Install hzeller/rpi-rgb-led-matrix on the Pi, or run with --mock-output."
            ) from exc

        options = RGBMatrixOptions()
        options.rows = args.rows
        options.cols = args.cols
        options.chain_length = args.chain_length
        options.parallel = args.parallel
        options.brightness = args.brightness
        options.gpio_slowdown = args.gpio_slowdown
        options.hardware_mapping = args.hardware_mapping
        options.pwm_bits = args.pwm_bits
        options.limit_refresh_rate_hz = args.limit_refresh_rate_hz
        options.disable_hardware_pulsing = args.no_hardware_pulse
        options.drop_privileges = False

        self.matrix = RGBMatrix(options=options)
        self.canvas = self.matrix.CreateFrameCanvas()
        self.gamma = float(getattr(args, "gamma", 1.0))

    def show(self, image: Image.Image) -> None:
        self.canvas.SetImage(apply_gamma(image.convert("RGB"), self.gamma))
        self.canvas = self.matrix.SwapOnVSync(self.canvas)

    def clear(self) -> None:
        self.matrix.Clear()

    def set_brightness(self, value: int) -> None:
        self.matrix.brightness = max(1, min(100, value))


class MockDisplay:
    def __init__(self, output: Path, gamma: float = 1.0) -> None:
        self.output = output
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.gamma = gamma

    def show(self, image: Image.Image) -> None:
        # Same gamma path as hardware so preview frames match the panel.
        apply_gamma(image.convert("RGB"), self.gamma).save(self.output)

    def clear(self) -> None:
        return

    def set_brightness(self, value: int) -> None:
        pass


# ═══════════════════════════════════════════════════════════════════
#  IMAGE / FONT HELPERS
# ═══════════════════════════════════════════════════════════════════

def demo_album_art(size: int) -> Image.Image:
    image = Image.new("RGB", (size, size), (18, 18, 18))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, size // 2, size // 2), fill=(238, 70, 60))
    draw.rectangle((size // 2, 0, size, size // 2), fill=(245, 180, 40))
    draw.rectangle((0, size // 2, size // 2, size), fill=(35, 150, 235))
    draw.rectangle((size // 2, size // 2, size, size), fill=(65, 185, 95))
    draw.line((0, 0, size, size), fill=(255, 255, 255), width=max(2, size // 18))
    draw.line((size, 0, 0, size), fill=(0, 0, 0), width=max(2, size // 22))
    return image


# Optional pixel-font path, set once from --lyrics-font. A bitmap/pixel font
# lands every stroke on the pixel grid; the stock PIL default is a proportional
# anti-aliased TTF, which on a 64x64 panel smears each glyph across half-lit
# LEDs. That blur is most of why small sizes read as mush.
_PIXEL_FONT_PATH: str | None = None


def set_pixel_font(path: str | None) -> None:
    global _PIXEL_FONT_PATH
    if not path:
        return
    if not Path(path).exists():
        log(f"Font: '{path}' not found — falling back to the default font.", "warn")
        return
    try:
        ImageFont.truetype(path, 9)
    except OSError as exc:
        log(f"Font: '{path}' could not be loaded ({exc}) — using the default.", "warn")
        return
    _PIXEL_FONT_PATH = path
    get_font.cache_clear()
    get_text_height.cache_clear()
    log(f"Font: using pixel font {path}")


@functools.lru_cache(maxsize=32)
def get_font(size: int = 9) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    if _PIXEL_FONT_PATH:
        try:
            return ImageFont.truetype(_PIXEL_FONT_PATH, size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        try:
            return ImageFont.truetype("arial.ttf", size)
        except OSError:
            return ImageFont.load_default()


def draw_text_batch(
    frame: Image.Image,
    items: list[tuple[tuple[int, int], str]],
    font: Any,
    color: tuple[int, int, int],
    crisp: bool = True,
    threshold: int = CRISP_THRESHOLD,
) -> None:
    """Draw several same-coloured strings, optionally with anti-aliasing removed.

    PIL always anti-aliases TrueType glyphs. Those partial-coverage pixels
    become half-lit LEDs, which is what makes small text look blurry rather
    than small. Rendering into a mask and thresholding snaps every glyph back
    onto the pixel grid.

    Batched by colour so a wrapped karaoke line costs two masks per frame
    rather than one per word.

    The threshold matters more than it looks: measured against the stock font
    at size 7, 60 keeps every stroke while removing all 74 half-lit pixels,
    whereas 110 already starts eating thin stems ("for" renders as "lor") and
    140 destroys the text. Tune with --text-threshold if you load a different
    font.
    """
    if not items:
        return
    if not crisp:
        draw = ImageDraw.Draw(frame)
        for xy, text in items:
            draw.text(xy, text, fill=color, font=font)
        return

    mask = Image.new("L", frame.size, 0)
    mask_draw = ImageDraw.Draw(mask)
    for xy, text in items:
        mask_draw.text(xy, text, fill=255, font=font)
    frame.paste(color, (0, 0), mask.point(lambda p: 255 if p >= threshold else 0))


@functools.lru_cache(maxsize=16)
def get_text_height(font_size: int = 9) -> int:
    font = get_font(font_size)
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    bbox = draw.textbbox((0, 0), "Ag - Mj", font=font)
    return max(1, bbox[3] - bbox[1])


# ═══════════════════════════════════════════════════════════════════
#  PLAYBACK ART EXTRACTION
# ═══════════════════════════════════════════════════════════════════

def playback_art_from_response(playback: dict[str, Any] | None) -> PlaybackArt | None:
    if not playback:
        return None

    item = playback.get("item")
    if not item:
        return None

    item_type = item.get("type")
    if item_type == "track":
        images = item.get("album", {}).get("images", [])
        title = item.get("name") or ""
        artists = item.get("artists", [])
        artist_name = ", ".join(a.get("name") for a in artists if a.get("name"))
        album_name = item.get("album", {}).get("name", "")
    else:
        images = item.get("images", [])
        title = item.get("name") or ""
        show = item.get("show") or {}
        artist_name = show.get("name") or ""
        album_name = show.get("name") or ""

    if not images:
        return None

    image = pick_art_variant(images, ART_TARGET_PX)
    item_id = item.get("id") or item.get("uri") or image["url"]

    progress_ms = int(playback.get("progress_ms", 0))
    duration_ms = int(item.get("duration_ms", 0))

    return PlaybackArt(
        key=str(item_id),
        image_url=image["url"],
        is_playing=bool(playback.get("is_playing")),
        title=title,
        artist=artist_name,
        album_name=album_name,
        progress_ms=progress_ms,
        duration_ms=duration_ms,
    )


def pick_art_variant(images: list[dict[str, Any]], target: int) -> dict[str, Any]:
    """Smallest artwork at least `target` px wide, falling back to the largest.

    Spotify offers 640/300/64 variants. This used to always take the 640 and
    then downscale it to 64 — paying the download, the decode and the resize
    for detail that cannot survive a 64x64 panel. Note we deliberately do not
    pick an exact 64px source: the disc is rotated, and resampling a
    already-minimal image every frame visibly degrades it.
    """
    usable = [img for img in images if (img.get("width") or 0) >= target]
    if usable:
        return min(usable, key=lambda candidate: candidate.get("width") or 0)
    return max(images, key=lambda candidate: candidate.get("width") or 0)


def download_image(
    url: str, *, saturation: float = 1.0, contrast: float = 1.0
) -> Image.Image:
    request = urllib.request.Request(url)
    with urllib.request.urlopen(request, timeout=15) as response:
        image = Image.open(BytesIO(response.read())).convert("RGB")
    return enhance_album_art(image, saturation, contrast)


# ═══════════════════════════════════════════════════════════════════
#  RENDERING — RECORD / IDLE / CLOCK
# ═══════════════════════════════════════════════════════════════════

_disc_mask_cache: dict[int, Image.Image] = {}


def _get_disc_mask(size: int) -> Image.Image:
    if size not in _disc_mask_cache:
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
        _disc_mask_cache[size] = mask
    return _disc_mask_cache[size]


# Only the current track's fitted art is ever needed, so this holds one entry
# per (track, disc size) and is cleared on change.
_fitted_art_cache: dict[tuple[str, int], Image.Image] = {}


def _get_fitted_art(art: Image.Image, art_key: str | None, size: int) -> Image.Image:
    """Square-crop and downscale the artwork once per track, not once per frame.

    ImageOps.fit with LANCZOS was previously running on every frame against the
    full-resolution Spotify image — 20 identical resamples a second, all thrown
    away. The result depends only on the artwork and the disc size; only the
    rotation is per-frame.
    """
    if art_key is None:
        # No stable identity to key on — don't risk serving another track's art.
        return ImageOps.fit(art, (size, size), method=Image.Resampling.LANCZOS)

    cache_key = (art_key, size)
    cached = _fitted_art_cache.get(cache_key)
    if cached is not None:
        return cached

    # Keep a few entries: during a track transition the old and new discs are
    # both rendered every frame, and a single-entry cache would thrash between
    # them and refit both on every frame.
    if len(_fitted_art_cache) >= 4:
        _fitted_art_cache.clear()
    fitted = ImageOps.fit(art, (size, size), method=Image.Resampling.LANCZOS)
    _fitted_art_cache[cache_key] = fitted
    return fitted


_contained_art_cache: dict[tuple[str, int], Image.Image] = {}


def _get_contained_art(art: Image.Image, art_key: str | None, size: int) -> Image.Image:
    """Fit the album art cleanly into (size, size) preserving aspect ratio without crop or stretch.

    If the artwork is non-square, letterbox or pillarbox black bars are added so the entire
    artwork fits as much as it can within the matrix display, perfectly steady without bouncing.
    """
    if art_key is None:
        w, h = art.size
        scale = min(size / max(1, w), size / max(1, h))
        nw = max(1, min(size, int(round(w * scale))))
        nh = max(1, min(size, int(round(h * scale))))
        resized = art.resize((nw, nh), Image.Resampling.LANCZOS).convert("RGB")
        frame = Image.new("RGB", (size, size), (0, 0, 0))
        frame.paste(resized, ((size - nw) // 2, (size - nh) // 2))
        return frame

    cache_key = (art_key, size)
    cached = _contained_art_cache.get(cache_key)
    if cached is not None:
        return cached

    if len(_contained_art_cache) >= 4:
        _contained_art_cache.clear()

    w, h = art.size
    scale = min(size / max(1, w), size / max(1, h))
    nw = max(1, min(size, int(round(w * scale))))
    nh = max(1, min(size, int(round(h * scale))))
    resized = art.resize((nw, nh), Image.Resampling.LANCZOS).convert("RGB")
    frame = Image.new("RGB", (size, size), (0, 0, 0))
    frame.paste(resized, ((size - nw) // 2, (size - nh) // 2))
    _contained_art_cache[cache_key] = frame
    return frame


def render_record(
    art: Image.Image | None,
    angle: float,
    size: int,
    art_key: str | None = None,
    progress: float | None = None,
    accent_color: tuple[int, int, int] = SPOTIFY_GREEN,
    border_color: tuple[int, int, int] | None = None,
    line_width: int = 1,
) -> Image.Image:
    frame = Image.new("RGBA", (size, size), (0, 0, 0, 255))
    if art is None:
        return frame.convert("RGB")

    disc_size = size
    art_square = _get_fitted_art(art, art_key, disc_size)
    rotated = art_square.rotate(angle, resample=Image.Resampling.BICUBIC)

    disc_mask = _get_disc_mask(disc_size)
    frame.paste(rotated.convert("RGBA"), (0, 0), disc_mask)

    draw = ImageDraw.Draw(frame, "RGBA")
    rim_color = border_color if border_color is not None else accent_color
    lw = max(1, min(line_width, max(1, size // 8)))
    # Subtle bezel ring in dimmed contrast color so the border is cleanly defined against the artwork
    dim_rim = tuple(int(c * 0.5) for c in rim_color) + (180,)
    draw.ellipse((0, 0, size - 1, size - 1), outline=dim_rim, width=lw)

    # Track position as an arc on the bezel. The disc already has a rim, so
    # this costs no space — the played portion simply lights up in the accent/contrast
    # colour, which reads at a glance without adding another element.
    if progress is not None and 0.0 <= progress <= 1.0:
        arc_w = max(lw, lw + 1 if lw == 1 else lw)
        draw.arc(
            (0, 0, size - 1, size - 1),
            start=-90, end=-90 + 360 * progress,
            fill=rim_color + (255,), width=arc_w,
        )

    center = size // 2
    label_radius = max(3, size // 16)
    hole_radius = max(1, size // 40)

    draw.ellipse(
        (center - label_radius, center - label_radius,
         center + label_radius, center + label_radius),
        fill=(16, 16, 16, 210), outline=(220, 220, 220, 90),
    )
    draw.ellipse(
        (center - hole_radius, center - hole_radius,
         center + hole_radius, center + hole_radius),
        fill=(0, 0, 0, 255),
    )
    return frame.convert("RGB")


_accent_extract_cache: dict[str, tuple[int, int, int]] = {}


def extract_accent_color(
    art: Image.Image, art_key: str | None = None
) -> tuple[int, int, int]:
    """Pick a vivid, panel-legible accent colour out of the artwork.

    Downscales hard first — at 16x16 the image is already a colour summary, and
    picking from 256 pixels costs nothing once per track. Scores by saturation
    times value so a large dull background loses to a small vivid element,
    which is what the eye picks out of the cover anyway. Value is then floored,
    because a colour that is legible on a screen can be invisible on LEDs.
    """
    if art_key is not None:
        cached = _accent_extract_cache.get(art_key)
        if cached is not None:
            return cached

    thumb = art.convert("RGB").resize((16, 16), Image.Resampling.BILINEAR)
    buckets: dict[int, list[float]] = {}

    # tobytes() rather than getdata(): getdata is deprecated for removal in
    # Pillow 14, and raw bytes avoid building 256 tuples we immediately unpack.
    raw = thumb.tobytes()
    pixels = [(raw[i], raw[i + 1], raw[i + 2]) for i in range(0, len(raw), 3)]

    for r, g, b in pixels:
        h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        if v < 0.15:
            continue  # near-black tells us nothing about the cover's colour
        # Group by hue so many similar pixels reinforce each other instead of
        # each competing as an individual candidate.
        bucket = int(h * 24) % 24
        weight = s * v
        entry = buckets.setdefault(bucket, [0.0, 0.0, 0.0, 0.0])
        entry[0] += weight
        entry[1] += h * weight
        entry[2] += s * weight
        entry[3] += v * weight

    if not buckets:
        return SPOTIFY_GREEN

    best = max(buckets.values(), key=lambda entry: entry[0])
    total = best[0] or 1.0
    hue, sat, val = best[1] / total, best[2] / total, best[3] / total

    # Push toward something the panel can actually show: weak colours read as
    # grey once they are 64 dim LEDs.
    sat = max(0.55, min(1.0, sat * 1.25))
    val = max(0.75, min(1.0, val * 1.3))
    r, g, b = colorsys.hsv_to_rgb(hue, sat, val)
    colour = (int(r * 255), int(g * 255), int(b * 255))

    if art_key is not None:
        if len(_accent_extract_cache) > 32:
            _accent_extract_cache.clear()
        _accent_extract_cache[art_key] = colour
    return colour


_contrast_extract_cache: dict[str, tuple[int, int, int]] = {}


def extract_contrast_accent_color(
    art: Image.Image, art_key: str | None = None
) -> tuple[int, int, int]:
    """Pick an aesthetically harmonious, high-contrast accent colour against the artwork.

    Designed for the CD border, the song length line, and the full album hairline
    progress bar (or globally when the 'contrast' accent theme is chosen).

    Unlike naive RGB inversion which yields muddy or washed out tones, this uses
    color harmony in HSV space:
    1. Analyzes the dominant hue and saturation of the artwork.
    2. Calculates the perceptual average luminance (Luma).
    3. Shifts the dominant hue by 180° (complementary) for maximum chromatic contrast.
    4. Enforces clean, vibrant saturation so it never looks washed out.
    5. Sets value / brightness based on the artwork's luma to guarantee sharp contrast
       (e.g. bright vivid aqua on deep red; radiant gold on dark cobalt; rich indigo
       on pale/white covers).
    6. For monochrome or grayscale covers, falls back to a crisp electric cyan or
       bright gold rather than a dull gray.
    """
    if art_key is not None:
        cached = _contrast_extract_cache.get(art_key)
        if cached is not None:
            return cached

    thumb = art.convert("RGB").resize((16, 16), Image.Resampling.BILINEAR)
    raw = thumb.tobytes()
    pixels = [(raw[i], raw[i + 1], raw[i + 2]) for i in range(0, len(raw), 3)]

    if not pixels:
        return (0, 220, 220)

    # Average perceptual luminance (0.0 to 255.0)
    avg_luma = sum(0.299 * r + 0.587 * g + 0.114 * b for r, g, b in pixels) / len(pixels)

    buckets: dict[int, list[float]] = {}
    total_sat = 0.0

    for r, g, b in pixels:
        h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        total_sat += s
        if v < 0.15:
            continue
        bucket = int(h * 24) % 24
        weight = s * v
        entry = buckets.setdefault(bucket, [0.0, 0.0, 0.0, 0.0])
        entry[0] += weight
        entry[1] += h * weight
        entry[2] += s * weight
        entry[3] += v * weight

    mean_sat = total_sat / len(pixels)

    # If the artwork is mostly monochrome / grayscale
    if not buckets or mean_sat < 0.12:
        if avg_luma < 128:
            colour = (0, 225, 235)  # Electric Cyan on dark monochrome
        else:
            colour = (35, 75, 220)  # Deep Cobalt Indigo on light monochrome
    else:
        best = max(buckets.values(), key=lambda entry: entry[0])
        total = best[0] or 1.0
        dom_hue = best[1] / total

        # Shift 180° for complementary contrast
        contrast_hue = (dom_hue + 0.5) % 1.0

        # Fine-tune aesthetic pairings:
        # - Red -> Cyan/Aqua
        # - Blue -> Warm Gold
        # - Green -> Rose/Magenta
        # - Purple -> Mint
        if dom_hue < 0.07 or dom_hue > 0.93:
            contrast_hue = 0.50
        elif 0.56 <= dom_hue <= 0.68:
            contrast_hue = 0.12
        elif 0.22 <= dom_hue <= 0.40:
            contrast_hue = 0.90
        elif 0.70 <= dom_hue <= 0.82:
            contrast_hue = 0.27

        sat = 0.85
        if avg_luma < 100:
            val = 0.98
        elif avg_luma < 170:
            val = 0.92
        else:
            sat = 0.95
            val = 0.72

        r, g, b = colorsys.hsv_to_rgb(contrast_hue, sat, val)
        c_r, c_g, c_b = int(r * 255), int(g * 255), int(b * 255)

        # Ensure luminance separation of at least 50 units
        contrast_luma = 0.299 * c_r + 0.587 * c_g + 0.114 * c_b
        if abs(contrast_luma - avg_luma) < 50:
            if avg_luma > 128:
                c_r, c_g, c_b = int(c_r * 0.75), int(c_g * 0.75), int(c_b * 0.75)
            else:
                c_r = min(255, int(c_r * 1.25) or 220)
                c_g = min(255, int(c_g * 1.25) or 220)
                c_b = min(255, int(c_b * 1.25) or 220)

        colour = (c_r, c_g, c_b)

    if art_key is not None:
        if len(_contrast_extract_cache) > 32:
            _contrast_extract_cache.clear()
        _contrast_extract_cache[art_key] = colour

    return colour


def render_full_art(
    art: Image.Image | None,
    size: int,
    art_key: str | None = None,
    progress: float | None = None,
    accent_color: tuple[int, int, int] = SPOTIFY_GREEN,
    pan: bool = False,
    pan_phase: float = 0.0,
    progress_color: tuple[int, int, int] | None = None,
    line_width: int = 1,
) -> Image.Image:
    """Artwork cleanly fitted into the panel with aspect ratio preserved, and a progress bar.

    Never stretches, crops or forces zoom. Non-square covers have black bars.
    """
    if art is None:
        return render_idle(size)

    # Cleanly fit the artwork into (size, size) preserving aspect ratio
    frame = _get_contained_art(art, art_key, size).copy()

    if progress is not None and 0.0 <= progress <= 1.0 and line_width > 0:
        draw = ImageDraw.Draw(frame)
        lw = max(1, min(line_width, size // 4))
        y0 = size - lw
        y1 = size - 1
        # Dim the untravelled part rather than leaving it bare, so the bar is
        # readable over a light-coloured cover.
        draw.rectangle((0, y0, size - 1, y1), fill=(28, 28, 28))
        filled = int((size - 1) * progress)
        bar_color = progress_color if progress_color is not None else accent_color
        if filled > 0:
            draw.rectangle((0, y0, filled, y1), fill=bar_color)
    return frame


# How long the new-track border pulse lasts.
TRACK_FLASH_SECONDS = 0.9


def apply_track_flash(
    image: Image.Image, strength: float, accent: tuple[int, int, int]
) -> Image.Image:
    """Pulse a 1px accent border — a peripheral "the song changed" cue.

    Drawn on the frame the panel is about to show rather than as a mode of its
    own, so it works the same whichever view is up. Mutates a copy: the caller
    may still be holding the original for a transition blend.
    """
    if strength <= 0.0:
        return image
    # Ease out, so it reads as a flash decaying rather than a border that
    # switches off.
    k = max(0.0, min(1.0, strength)) ** 0.6
    out = image.copy()
    draw = ImageDraw.Draw(out)
    colour = tuple(int(c * k) for c in accent)
    draw.rectangle((0, 0, out.width - 1, out.height - 1), outline=colour, width=1)
    return out


def render_idle(size: int) -> Image.Image:
    frame = Image.new("RGB", (size, size), (0, 0, 0))
    draw = ImageDraw.Draw(frame)
    draw.ellipse((0, 0, size - 1, size - 1), outline=(220, 220, 220), width=1)
    center = size // 2
    radius = max(2, size // 25)
    draw.ellipse((center - radius, center - radius, center + radius, center + radius), fill=(18, 18, 18))
    return frame


def render_status(
    size: int,
    headline: str,
    detail: str = "",
    accent_color: tuple[int, int, int] = SPOTIFY_GREEN,
    crisp: bool = True,
) -> Image.Image:
    """A readable on-panel explanation of why nothing else is showing.

    On a headless appliance a failure is otherwise indistinguishable from a
    dead panel: the process exits, the matrix goes black, and there is nothing
    to diagnose from. Saying "Spotify auth needed" turns that into an
    instruction.
    """
    frame = Image.new("RGB", (size, size), (0, 0, 0))
    draw = ImageDraw.Draw(frame)
    font = get_font(8)

    # Breathing dot at the top, so a stalled screen still reads as alive.
    pulse = (math.sin(time.time() * 2.5) + 1.0) / 2.0
    dot_color = tuple(int(c * (0.35 + 0.65 * pulse)) for c in accent_color)
    cx = size / 2.0
    draw.ellipse((cx - 2, 7, cx + 2, 11), fill=dot_color)

    words = headline.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= size - 4 or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)

    line_h = 10
    block_h = len(lines) * line_h + (line_h if detail else 0)
    y = int((size - block_h) / 2) + 4

    items = []
    for line in lines:
        width = draw.textlength(line, font=font)
        items.append(((int((size - width) / 2), y), line))
        y += line_h
    draw_text_batch(frame, items, font, (235, 235, 235), crisp=crisp)

    if detail:
        small = get_font(7)
        width = draw.textlength(detail, font=small)
        draw_text_batch(
            frame,
            [((int((size - width) / 2), y + 2), detail)],
            small,
            LYRIC_DIM_COLOR,
            crisp=crisp,
        )

    return frame


_clock_face_cache: dict[tuple, Image.Image] = {}


def render_clock(size: int, is_connected: bool = True,
                 accent_color: tuple[int, int, int] = SPOTIFY_GREEN) -> Image.Image:
    """Clock frame. The static face is cached; only the second dot and the
    connection pulse are drawn per frame.

    The face (three text layouts, twelve tick marks, the bezel) changes once a
    minute but was being rebuilt on every one of the ~20 frames per second.
    """
    now = datetime.datetime.now()
    face_key = (size, accent_color, now.hour, now.minute, now.day)

    face = _clock_face_cache.get(face_key)
    if face is None:
        _clock_face_cache.clear()
        face = _render_clock_face(size, accent_color, now)
        _clock_face_cache[face_key] = face

    frame = face.copy()
    draw = ImageDraw.Draw(frame)

    margin = 1
    cx = cy = size / 2.0
    outer_r = (size - margin * 2) / 2.0

    second_angle = (now.second / 60.0) * 360 - 90
    rad = math.radians(second_angle)
    dot_r = outer_r - 1
    sx = cx + math.cos(rad) * dot_r
    sy = cy + math.sin(rad) * dot_r
    draw.ellipse((sx - 1.5, sy - 1.5, sx + 1.5, sy + 1.5), fill=accent_color)

    pulse = (math.sin(time.time() * 2.0) + 1.0) / 2.0
    pulse_brightness = int(50 + pulse * 150)
    if is_connected:
        pulse_color = tuple(int(c * pulse_brightness / 200) for c in accent_color)
    else:
        pulse_color = (pulse_brightness, 0, 0)

    pulse_margin = max(4, size // 12)
    pulse_x = size - pulse_margin
    pulse_y = size - pulse_margin
    pulse_r = 2
    draw.ellipse(
        (pulse_x - pulse_r, pulse_y - pulse_r, pulse_x + pulse_r, pulse_y + pulse_r),
        fill=pulse_color,
    )

    return frame


def _render_clock_face(
    size: int, accent_color: tuple[int, int, int], now: datetime.datetime
) -> Image.Image:
    frame = Image.new("RGB", (size, size), (0, 0, 0))
    draw = ImageDraw.Draw(frame)

    day_str = now.strftime("%a").upper()
    time_str = now.strftime("%I:%M %p").lstrip("0")
    date_str = now.strftime("%b %d").upper()

    small_font = get_font(max(8, size // 8))
    time_font = get_font(max(10, size // 5))

    day_bbox = draw.textbbox((0, 0), day_str, font=small_font)
    time_bbox = draw.textbbox((0, 0), time_str, font=time_font)
    date_bbox = draw.textbbox((0, 0), date_str, font=small_font)

    day_h = day_bbox[3] - day_bbox[1]
    time_h = time_bbox[3] - time_bbox[1]
    date_h = date_bbox[3] - date_bbox[1]

    gap = 2
    total_h = day_h + gap + time_h + gap + date_h
    start_y = (size - total_h) // 2

    day_x = (size - (day_bbox[2] - day_bbox[0])) // 2
    draw.text((day_x, start_y - day_bbox[1]), day_str, fill=accent_color, font=small_font)

    time_y = start_y + day_h + gap
    time_x = (size - (time_bbox[2] - time_bbox[0])) // 2
    draw.text((time_x, time_y - time_bbox[1]), time_str, fill=(255, 255, 255), font=time_font)

    date_y = time_y + time_h + gap
    date_x = (size - (date_bbox[2] - date_bbox[0])) // 2
    draw.text((date_x, date_y - date_bbox[1]), date_str, fill=(180, 180, 180), font=small_font)

    margin = 1
    draw.ellipse((margin, margin, size - margin - 1, size - margin - 1), outline=(50, 50, 70), width=1)

    cx = size / 2.0
    cy = size / 2.0
    outer_r = (size - margin * 2) / 2.0
    inner_r = outer_r - max(2, size // 20)
    for hour in range(12):
        tick_angle = math.radians(hour * 30 - 90)
        x1 = cx + math.cos(tick_angle) * outer_r
        y1 = cy + math.sin(tick_angle) * outer_r
        x2 = cx + math.cos(tick_angle) * inner_r
        y2 = cy + math.sin(tick_angle) * inner_r
        tick_color = (100, 100, 120) if hour % 3 != 0 else (160, 160, 180)
        draw.line((x1, y1, x2, y2), fill=tick_color, width=1)

    return frame


# ═══════════════════════════════════════════════════════════════════
#  RENDERING — SCROLLING TEXT / FULL FRAME / TRANSITIONS
# ═══════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════
#  AMBIENT IDLE SCREENS
# ═══════════════════════════════════════════════════════════════════
#
# These replace the clock while nothing is playing. They exist because the
# panel is idle most of the day and a static clock wastes it — but "clock" is
# still the default, so an upgrade changes nothing until you pick otherwise.
#
# All of them render on a coarse grid and scale up. At 64x64 a per-pixel Python
# loop is 4096 round-trips into PIL per frame, which the Pi cannot afford; at
# 32x32 it is 1024, and everything here is smooth enough that the upscale is
# invisible.

IDLE_COARSE = 32  # working grid for the pixel-loop animations


def _accent_palette(accent: tuple[int, int, int]) -> list[tuple[int, int, int]]:
    """256-entry cyclic palette centred on the accent hue.

    A straight black→accent→white ramp washes out: plasma values cluster near
    the middle, so most of the screen lands in the blown-out top half. Rotating
    hue instead keeps every value fully saturated, which is what makes the
    classic effect read as colour flow rather than a moving highlight. The
    rotation is deliberately partial (±60°) so the result still looks like the
    accent colour rather than a rainbow.
    """
    h, _, _ = colorsys.rgb_to_hsv(*(c / 255.0 for c in accent))
    palette: list[tuple[int, int, int]] = []
    for i in range(256):
        phase = i / 255.0 * 2.0 * math.pi
        hue = (h + math.sin(phase) * 0.17) % 1.0
        # Keep the darkest point well above black so the panel never looks off.
        value = 0.35 + 0.65 * (0.5 - 0.5 * math.cos(phase))
        r, g, b = colorsys.hsv_to_rgb(hue, 0.85, value)
        palette.append((int(r * 255), int(g * 255), int(b * 255)))
    return palette


_FIRE_PALETTE: list[tuple[int, int, int]] = []


def _fire_palette() -> list[tuple[int, int, int]]:
    """Black → red → orange → yellow → white, the classic heat ramp."""
    global _FIRE_PALETTE
    if _FIRE_PALETTE:
        return _FIRE_PALETTE
    palette = []
    for i in range(256):
        t = i / 255.0
        if t < 0.33:
            k = t / 0.33
            palette.append((int(140 * k), 0, 0))
        elif t < 0.66:
            k = (t - 0.33) / 0.33
            palette.append((140 + int(115 * k), int(130 * k), 0))
        else:
            k = (t - 0.66) / 0.34
            palette.append((255, 130 + int(125 * k), int(200 * k)))
    _FIRE_PALETTE = palette
    return palette


class IdleScreen:
    """Renders whichever idle visual is selected, holding its animation state.

    One object owns all of them so switching modes cannot leak state between
    animations, and so `cycle` can rotate without re-seeding on every frame.
    """

    def __init__(self) -> None:
        self._plasma_t = 0.0
        self._accent_cache: tuple[tuple[int, int, int], list[tuple[int, int, int]]] | None = None
        self._rain: list[dict[str, Any]] = []
        self._rain_size = 0
        self._stars: list[list[float]] = []
        self._life: list[bytearray] = []
        self._life_ghost: list[bytearray] = []
        self._life_next_step = 0.0
        self._life_history: collections.deque[int] = collections.deque(maxlen=12)
        self._fire: list[bytearray] = []
        self._cycle_index = 0
        self._cycle_started = 0.0

    # ── palette helper ────────────────────────────────────────────
    def _palette(self, accent: tuple[int, int, int]) -> list[tuple[int, int, int]]:
        if self._accent_cache is None or self._accent_cache[0] != accent:
            self._accent_cache = (accent, _accent_palette(accent))
        return self._accent_cache[1]

    # ── dispatch ──────────────────────────────────────────────────
    def render(
        self,
        size: int,
        mode: str,
        is_connected: bool,
        accent: tuple[int, int, int],
        delta: float,
        now: float,
    ) -> Image.Image:
        if mode == "cycle":
            if self._cycle_started == 0.0:
                self._cycle_started = now
            if now - self._cycle_started >= IDLE_CYCLE_SECONDS:
                self._cycle_started = now
                self._cycle_index = (self._cycle_index + 1) % len(IDLE_ANIMATED)
            mode = IDLE_ANIMATED[self._cycle_index]

        if mode == "plasma":
            frame = self._plasma(size, accent, delta)
        elif mode == "rain":
            frame = self._rain_frame(size, accent, delta)
        elif mode == "stars":
            frame = self._starfield(size, accent, delta)
        elif mode == "life":
            frame = self._life_frame(size, accent, now)
        elif mode == "fire":
            frame = self._fire_frame(size)
        else:
            # "clock" and anything unrecognised fall back to the original face,
            # which already draws its own connection pulse.
            return render_clock(size, is_connected, accent)

        if not is_connected:
            # The clock signals this with a pulsing red dot; the animations get
            # a quieter version so a dropped connection is still visible.
            draw = ImageDraw.Draw(frame)
            pulse = int(80 + 120 * ((math.sin(now * 2.0) + 1.0) / 2.0))
            draw.ellipse((size - 4, size - 4, size - 2, size - 2), fill=(pulse, 0, 0))
        return frame

    # ── plasma ────────────────────────────────────────────────────
    def _plasma(self, size: int, accent: tuple[int, int, int], delta: float) -> Image.Image:
        self._plasma_t += delta * 0.6
        t = self._plasma_t
        n = IDLE_COARSE
        palette = self._palette(accent)
        buf = bytearray(n * n * 3)
        sin = math.sin

        # Three interfering waves — the classic demoscene plasma. Row terms are
        # hoisted out of the inner loop; that alone roughly halves the work.
        for y in range(n):
            sy = sin(y * 0.20 + t * 1.1)
            sy2 = y * 0.14
            base = y * n * 3
            for x in range(n):
                v = sin(x * 0.18 + t) + sy + sin((x * 0.11 + sy2) + t * 0.7)
                idx = int((v + 3.0) * 42.5)
                r, g, b = palette[0 if idx < 0 else 255 if idx > 255 else idx]
                off = base + x * 3
                buf[off] = r
                buf[off + 1] = g
                buf[off + 2] = b

        small = Image.frombytes("RGB", (n, n), bytes(buf))
        return small.resize((size, size), Image.Resampling.BILINEAR)

    # ── matrix rain ───────────────────────────────────────────────
    def _rain_frame(self, size: int, accent: tuple[int, int, int], delta: float) -> Image.Image:
        col_w = 6
        columns = max(1, size // col_w)
        if not self._rain or self._rain_size != size:
            self._rain_size = size
            # Seed across the whole height, not above the top edge: starting
            # every column off-screen leaves the panel nearly empty for the
            # first few seconds, which is exactly when you are looking at it.
            self._rain = [
                {
                    "y": random.uniform(0.0, float(size)),
                    "speed": random.uniform(14.0, 40.0),
                    "length": random.randint(6, 13),
                    "chars": [random.choice("01ABCDEFXYZ#*+=<>") for _ in range(12)],
                }
                for _ in range(columns)
            ]

        frame = Image.new("RGB", (size, size), (0, 0, 0))
        font = get_font(7)
        r, g, b = accent
        # Bucket the trail into four brightness levels so the whole screen is
        # four batched text draws instead of one per glyph.
        buckets: list[list[tuple[tuple[int, int], str]]] = [[], [], [], []]
        head: list[tuple[tuple[int, int], str]] = []

        for col, stream in enumerate(self._rain):
            stream["y"] += stream["speed"] * delta
            if stream["y"] - stream["length"] * 7 > size:
                stream["y"] = random.uniform(-24.0, -6.0)
                stream["speed"] = random.uniform(14.0, 40.0)
                stream["length"] = random.randint(6, 13)

            x = col * col_w
            for i in range(stream["length"]):
                y = int(stream["y"]) - i * 7
                if y < -7 or y > size:
                    continue
                glyph = stream["chars"][(i + int(stream["y"]) // 7) % len(stream["chars"])]
                if i == 0:
                    head.append(((x, y), glyph))
                else:
                    level = min(3, (i * 4) // max(1, stream["length"]))
                    buckets[level].append(((x, y), glyph))

        for level, items in enumerate(buckets):
            if not items:
                continue
            k = (0.75, 0.5, 0.3, 0.15)[level]
            draw_text_batch(
                frame, items, font,
                (int(r * k), int(g * k), int(b * k)), crisp=True,
            )
        if head:
            draw_text_batch(frame, head, font, (230, 255, 230), crisp=True)
        return frame

    # ── starfield ─────────────────────────────────────────────────
    def _starfield(self, size: int, accent: tuple[int, int, int], delta: float) -> Image.Image:
        if not self._stars:
            self._stars = [
                [random.uniform(-1.0, 1.0), random.uniform(-1.0, 1.0), random.uniform(0.05, 1.0)]
                for _ in range(70)
            ]

        frame = Image.new("RGB", (size, size), (0, 0, 0))
        draw = ImageDraw.Draw(frame)
        half = size / 2.0
        r, g, b = accent

        for star in self._stars:
            star[2] -= delta * 0.35
            if star[2] <= 0.02:
                star[0] = random.uniform(-1.0, 1.0)
                star[1] = random.uniform(-1.0, 1.0)
                star[2] = 1.0
            k = 1.0 / star[2]
            x = half + star[0] * k * half * 0.6
            y = half + star[1] * k * half * 0.6
            if not (0 <= x < size and 0 <= y < size):
                continue
            # Near stars are brighter and bigger — the only depth cue available.
            depth = min(1.0, (1.0 - star[2]) ** 1.5)
            colour = (
                int(r + (255 - r) * depth),
                int(g + (255 - g) * depth),
                int(b + (255 - b) * depth),
            )
            if depth > 0.75:
                draw.rectangle((x, y, x + 1, y + 1), fill=colour)
            else:
                draw.point((x, y), fill=colour)
        return frame

    # ── Conway's Game of Life ─────────────────────────────────────
    def _seed_life(self, n: int) -> None:
        self._life = [
            bytearray(1 if random.random() < 0.32 else 0 for _ in range(n))
            for _ in range(n)
        ]
        self._life_ghost = [bytearray(n) for _ in range(n)]
        self._life_history.clear()

    def _life_frame(self, size: int, accent: tuple[int, int, int], now: float) -> Image.Image:
        n = IDLE_COARSE
        if not self._life:
            self._seed_life(n)

        # Step on a timer, not per frame: Life at 10 FPS is unreadable, and the
        # step is the expensive part.
        if now >= self._life_next_step:
            self._life_next_step = now + 0.18
            grid = self._life
            nxt = [bytearray(n) for _ in range(n)]
            for y in range(n):
                up = grid[y - 1]
                mid = grid[y]
                down = grid[(y + 1) % n]
                row = nxt[y]
                for x in range(n):
                    xl = x - 1
                    xr = (x + 1) % n
                    count = (
                        up[xl] + up[x] + up[xr]
                        + mid[xl] + mid[xr]
                        + down[xl] + down[x] + down[xr]
                    )
                    row[x] = 1 if (count == 3 or (count == 2 and mid[x])) else 0

            # Decay the ghost layer, then stamp cells that died this step.
            if not self._life_ghost:
                self._life_ghost = [bytearray(n) for _ in range(n)]
            for y in range(n):
                ghost = self._life_ghost[y]
                was = grid[y]
                now_row = nxt[y]
                for x in range(n):
                    if was[x] and not now_row[x]:
                        ghost[x] = 150
                    elif ghost[x]:
                        ghost[x] = ghost[x] - 30 if ghost[x] > 30 else 0
            self._life = nxt

            # Reseed when the board dies out or locks into a short cycle,
            # otherwise it settles into still lifes and stops being ambient.
            population = sum(sum(row) for row in nxt)
            self._life_history.append(population)
            if population == 0 or (
                len(self._life_history) == self._life_history.maxlen
                and len(set(self._life_history)) <= 2
            ):
                self._seed_life(n)

        # Live cells at full accent, cells that died recently as a fading
        # ghost. The trail is what makes gliders legible at 32x32 — without it
        # the board reads as unrelated blinking dots between steps.
        live = accent
        buf = bytearray(n * n * 3)
        for y in range(n):
            row = self._life[y]
            ghost_row = self._life_ghost[y] if self._life_ghost else None
            base = y * n * 3
            for x in range(n):
                if row[x]:
                    r, g, b = live
                elif ghost_row is not None and ghost_row[x]:
                    k = ghost_row[x] / 255.0
                    r, g, b = (int(live[0] * k), int(live[1] * k), int(live[2] * k))
                else:
                    r = g = b = 0
                off = base + x * 3
                buf[off] = r
                buf[off + 1] = g
                buf[off + 2] = b
        small = Image.frombytes("RGB", (n, n), bytes(buf))
        return small.resize((size, size), Image.Resampling.NEAREST)

    # ── fireplace ─────────────────────────────────────────────────
    def _fire_frame(self, size: int) -> Image.Image:
        n = IDLE_COARSE
        if not self._fire:
            self._fire = [bytearray(n) for _ in range(n)]

        grid = self._fire
        # Fresh heat along the bottom row, then diffuse upward. Keeping the
        # source noisy is what makes the flame flicker rather than pulse.
        bottom = grid[n - 1]
        for x in range(n):
            bottom[x] = random.randint(160, 255) if random.random() < 0.8 else random.randint(0, 90)

        palette = _fire_palette()
        for y in range(n - 2, -1, -1):
            row = grid[y]
            below = grid[y + 1]
            for x in range(n):
                left = below[x - 1] if x > 0 else below[x]
                right = below[x + 1] if x < n - 1 else below[x]
                value = (left + below[x] * 2 + right) // 4
                decay = 6 if value > 40 else 2
                row[x] = max(0, value - decay)

        buf = bytearray(n * n * 3)
        for y in range(n):
            row = grid[y]
            base = y * n * 3
            for x in range(n):
                r, g, b = palette[row[x]]
                off = base + x * 3
                buf[off] = r
                buf[off + 1] = g
                buf[off + 2] = b
        small = Image.frombytes("RGB", (n, n), bytes(buf))
        return small.resize((size, size), Image.Resampling.BILINEAR)


def draw_scrolling_text(
    image: Image.Image,
    text: str,
    scroll_x: float,
    position: str = "bottom",
    banner_height: int = 0,
    text_color: tuple[int, int, int] = (255, 255, 255),
    bg_color: tuple[int, int, int] = (0, 0, 0),
    font_size: int = 9,
) -> Image.Image:
    if not text.strip():
        return image

    size_x, size_y = image.size
    draw = ImageDraw.Draw(image)
    font = get_font(font_size)

    bbox = draw.textbbox((0, 0), text, font=font)
    text_h = bbox[3] - bbox[1]
    y_offset = bbox[1]

    actual_banner_h = banner_height if banner_height > 0 else text_h

    if position == "top":
        banner_y0 = 0
        banner_y1 = actual_banner_h - 1
        y_pos = banner_y0 + max(0, (actual_banner_h - text_h) // 2) - y_offset
    else:
        banner_y0 = size_y - actual_banner_h
        banner_y1 = size_y - 1
        y_pos = banner_y0 + (actual_banner_h - text_h) - y_offset

    draw.rectangle((0, banner_y0, size_x - 1, banner_y1), fill=bg_color)

    separator = "   - - -   "
    full_unit = text + separator
    unit_bbox = draw.textbbox((0, 0), full_unit, font=font)
    unit_w = unit_bbox[2] - unit_bbox[0]

    if unit_w <= 0:
        return image

    # Snap to whole pixels. Drawing at fractional x let PIL round inconsistently
    # between frames, which on an LED panel reads as a permanent shimmer.
    offset_x = -int(scroll_x % unit_w)
    cur_x = offset_x

    while cur_x < size_x:
        if cur_x + unit_w > 0:
            draw.text((cur_x, y_pos), full_unit, fill=text_color, font=font)
        cur_x += unit_w

    _apply_edge_fade(image, banner_y0, banner_y1)
    return image


@functools.lru_cache(maxsize=8)
def _edge_fade_mask(size_x: int, height: int) -> Image.Image:
    """Horizontal edge-fade mask, built once per banner geometry.

    Replaces a getpixel/putpixel loop that crossed the Python/C boundary about
    130 times a frame. Image.composite does the same work in one C call.
    """
    fade_width = min(6, size_x // 10)
    mask = Image.new("L", (size_x, 1), 255)
    for i in range(fade_width):
        level = int(255 * i / fade_width) if fade_width else 255
        mask.putpixel((i, 0), level)
        mask.putpixel((size_x - 1 - i, 0), level)
    return mask.resize((size_x, height), Image.Resampling.NEAREST)


def _apply_edge_fade(image: Image.Image, banner_y0: int, banner_y1: int) -> None:
    height = banner_y1 - banner_y0 + 1
    if height <= 0:
        return
    size_x = image.size[0]
    band = image.crop((0, banner_y0, size_x, banner_y1 + 1))
    faded = Image.composite(
        band, Image.new("RGB", band.size, (0, 0, 0)), _edge_fade_mask(size_x, height)
    )
    image.paste(faded, (0, banner_y0))


@functools.lru_cache(maxsize=4)
def _clock_overlay(
    size_x: int, size_y: int, hour_str: str, minute_str: str,
    font_size: int, text_position: str,
) -> Image.Image:
    """Transparent HH / MM corner overlay for the CD view.

    This is 20 draw.text calls (an 8-direction shadow pass plus two
    foregrounds) for a string that changes once a minute, so it is cached by
    the time it displays rather than rebuilt every frame.
    """
    overlay = Image.new("RGBA", (size_x, size_y), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    clock_font = get_font(font_size)

    hour_bbox = draw.textbbox((0, 0), hour_str, font=clock_font)
    minute_bbox = draw.textbbox((0, 0), minute_str, font=clock_font)

    hour_h = hour_bbox[3] - hour_bbox[1]
    minute_w = minute_bbox[2] - minute_bbox[0]
    minute_h = minute_bbox[3] - minute_bbox[1]

    if text_position == "top":
        clock_y = size_y - max(hour_h, minute_h) - 1
    else:
        clock_y = 1

    hour_x = 1
    minute_x = size_x - minute_w - 1

    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx or dy:
                draw.text((hour_x + dx, clock_y - hour_bbox[1] + dy),
                          hour_str, fill=(0, 0, 0, 255), font=clock_font)
                draw.text((minute_x + dx, clock_y - minute_bbox[1] + dy),
                          minute_str, fill=(0, 0, 0, 255), font=clock_font)

    draw.text((hour_x, clock_y - hour_bbox[1]),
              hour_str, fill=(200, 200, 200, 255), font=clock_font)
    draw.text((minute_x, clock_y - minute_bbox[1]),
              minute_str, fill=(200, 200, 200, 255), font=clock_font)

    return overlay


def create_full_frame(
    art_image: Image.Image | None,
    angle: float,
    scroll_x: float,
    display_text: str,
    size_x: int,
    size_y: int,
    args: argparse.Namespace,
    art_key: str | None = None,
    progress: float | None = None,
    accent_color: tuple[int, int, int] = SPOTIFY_GREEN,
    border_color: tuple[int, int, int] | None = None,
    line_width: int = 1,
) -> Image.Image:
    has_text = bool(display_text) and not args.no_text
    if has_text:
        text_h = get_text_height(args.text_font_size)
        banner_h = args.text_banner_height if args.text_banner_height > 0 else text_h
        gap = 1
        cd_size = max(1, min(size_x, size_y - banner_h - gap))
    else:
        banner_h = 0
        gap = 0
        cd_size = min(size_x, size_y)

    cd_img = (
        render_record(
            art_image, angle, cd_size, art_key, progress,
            accent_color, border_color=border_color,
            line_width=line_width,
        )
        if art_image
        else render_idle(cd_size)
    )

    frame = Image.new("RGB", (size_x, size_y), (0, 0, 0))
    cd_x = (size_x - cd_size) // 2
    cd_y = (banner_h + gap) if (has_text and args.text_position == "top") else 0
    frame.paste(cd_img, (cd_x, cd_y))

    now = datetime.datetime.now()
    overlay = _clock_overlay(
        size_x, size_y,
        now.strftime("%I").lstrip("0"), now.strftime("%M"),
        max(9, args.text_font_size + 1), args.text_position,
    )
    frame.paste(overlay, (0, 0), overlay)

    if has_text:
        frame = draw_scrolling_text(
            frame, text=display_text, scroll_x=scroll_x,
            position=args.text_position, banner_height=banner_h,
            font_size=args.text_font_size,
        )

    return frame


def blend_frames(
    old_frame: Image.Image,
    new_frame: Image.Image,
    progress: float,
    mode: str = "slide",
) -> Image.Image:
    size_x, size_y = new_frame.size
    p = max(0.0, min(1.0, progress))
    eased_p = 1.0 - (1.0 - p) ** 3

    if mode == "slide":
        offset = int(eased_p * size_x)
        out_frame = Image.new("RGB", (size_x, size_y), (0, 0, 0))
        out_frame.paste(old_frame, (-offset, 0))
        out_frame.paste(new_frame, (size_x - offset, 0))
        return out_frame
    elif mode == "slide-right":
        offset = int(eased_p * size_x)
        out_frame = Image.new("RGB", (size_x, size_y), (0, 0, 0))
        out_frame.paste(old_frame, (offset, 0))
        out_frame.paste(new_frame, (-size_x + offset, 0))
        return out_frame
    elif mode == "slide-up":
        offset = int(eased_p * size_y)
        out_frame = Image.new("RGB", (size_x, size_y), (0, 0, 0))
        out_frame.paste(old_frame, (0, -offset))
        out_frame.paste(new_frame, (0, size_y - offset))
        return out_frame
    elif mode == "slide-down":
        offset = int(eased_p * size_y)
        out_frame = Image.new("RGB", (size_x, size_y), (0, 0, 0))
        out_frame.paste(old_frame, (0, offset))
        out_frame.paste(new_frame, (0, -size_y + offset))
        return out_frame
    elif mode == "fade":
        return Image.blend(old_frame, new_frame, eased_p)
    else:
        return new_frame


def render_test_pattern(size: int, offset: int) -> Image.Image:
    frame = Image.new("RGB", (size, size), (0, 0, 0))
    draw = ImageDraw.Draw(frame)
    colors = (
        (255, 0, 0), (255, 160, 0), (255, 255, 0), (0, 255, 0),
        (0, 120, 255), (80, 0, 255), (255, 255, 255), (0, 0, 0),
    )
    stripe_width = max(1, size // len(colors))
    for index, color in enumerate(colors):
        x0 = (index * stripe_width + offset) % size
        draw.rectangle((x0, 0, min(size - 1, x0 + stripe_width - 1), size - 1), fill=color)
        if x0 + stripe_width > size:
            draw.rectangle((0, 0, (x0 + stripe_width) % size, size - 1), fill=color)
    draw.rectangle((0, 0, size - 1, size - 1), outline=(255, 255, 255))
    return frame


# ═══════════════════════════════════════════════════════════════════
#  LRCLIB LYRICS FETCHING
# ═══════════════════════════════════════════════════════════════════

_LRC_LINE_RE = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]\s*(.*)")
# Enhanced-LRC per-word timestamps, e.g. "<00:12.34>Hello <00:12.90>world".
_LRC_WORD_RE = re.compile(r"<(\d+):(\d+(?:\.\d+)?)>")


def _parse_word_tags(text: str) -> tuple[str, list[tuple[str, int]]]:
    """Split an enhanced-LRC line into clean text plus (word, start_ms) pairs.

    These tags were previously left in the line body and drawn on the matrix as
    literal '<00:12.34>' garbage. Parsing them instead gives exact per-word
    karaoke timing for free on tracks that carry it.
    """
    matches = list(_LRC_WORD_RE.finditer(text))
    clean = re.sub(r"\s+", " ", _LRC_WORD_RE.sub("", text)).strip()
    if not matches:
        return clean, []

    words: list[tuple[str, int]] = []
    for index, match in enumerate(matches):
        start_ms = int((int(match.group(1)) * 60 + float(match.group(2))) * 1000)
        end_pos = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        chunk = text[match.end():end_pos].strip()
        if chunk:
            words.append((chunk, start_ms))
    return clean, words


def parse_lrc(synced_lyrics: str) -> tuple[list[tuple[int, str]], list[list[tuple[str, int]]]]:
    """Parse LRC into (lines, per_line_word_timings).

    word_timings[i] is empty for plain LRC and populated for enhanced LRC.
    """
    rows: list[tuple[int, str, list[tuple[str, int]]]] = []
    for line in synced_lyrics.splitlines():
        match = _LRC_LINE_RE.match(line.strip())
        if not match:
            continue
        minutes = int(match.group(1))
        seconds = float(match.group(2))
        text, word_times = _parse_word_tags(match.group(3))
        rows.append((int((minutes * 60 + seconds) * 1000), text, word_times))

    rows.sort(key=lambda row: row[0])
    return [(ts, text) for ts, text, _ in rows], [words for _, _, words in rows]


# Suffixes Spotify adds that LRCLIB's exact-match endpoint will not forgive.
_TITLE_NOISE_RE = re.compile(
    r"""\s*(?:
          -\s*(?:\d{4}\s*)?(?:remaster(?:ed)?|re-?recorded|radio\s*edit|single\s*version
              |album\s*version|mono|stereo|live|demo|edit|deluxe)\b.*$
        | \((?:feat|ft|with|featuring)\.?[^)]*\)
        | \[(?:feat|ft|with|featuring)\.?[^\]]*\]
        | \((?:remaster(?:ed)?|live|mono|stereo|deluxe|bonus)[^)]*\)
    )""",
    re.IGNORECASE | re.VERBOSE,
)


def normalize_track_title(title: str) -> str:
    """Strip decorations that break LRCLIB's exact-match lookup.

    'Song (feat. X) - Remastered 2011' misses /api/get every time, even though
    the plain title is in the database.
    """
    cleaned = _TITLE_NOISE_RE.sub("", title)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–—")
    return cleaned or title


def _lyrics_cache_path(cache_dir: Path, artist: str, track: str, duration_s: int) -> Path:
    import hashlib
    key = f"{artist.lower()}|{track.lower()}|{duration_s // 5}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
    return cache_dir / f"{digest}.json"


def _lrclib_get(params: dict[str, str], url: str = LRCLIB_API_URL) -> dict[str, Any] | None:
    req = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode(params)}",
        headers={"User-Agent": LRCLIB_USER_AGENT},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _lyrics_from_payload(
    data: dict[str, Any]
) -> tuple[list[tuple[int, str]] | None, list[list[tuple[str, int]]], bool]:
    is_instrumental = bool(data.get("instrumental", False))
    if not is_instrumental:
        plain = data.get("plainLyrics") or ""
        synced_raw = data.get("syncedLyrics") or ""
        if not plain.strip() and not synced_raw.strip():
            is_instrumental = True

    synced = data.get("syncedLyrics")
    if not synced:
        return None, [], is_instrumental
    lines, words = parse_lrc(synced)
    return (lines if lines else None), words, is_instrumental


def fetch_lyrics(
    artist: str, track: str, album: str, duration_s: int,
    cache_dir: Path | None = None,
) -> tuple[list[tuple[int, str]] | None, list[list[tuple[str, int]]], bool]:
    """Fetch synced lyrics from LRCLIB.

    Returns (lines_or_None, per_line_word_timings, is_instrumental).

    Tries, in order: the on-disk cache, an exact /api/get, an /api/get with the
    title normalized, then /api/search. The exact endpoint requires artist,
    track, album and duration all to match, so on its own it misses a large
    fraction of a normal library.
    """
    cache_path = (
        _lyrics_cache_path(cache_dir, artist, track, duration_s) if cache_dir else None
    )
    if cache_path and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            lines = [(int(t), s) for t, s in cached.get("lines") or []]
            words = [[(w, int(t)) for w, t in wl] for wl in cached.get("words") or []]
            log("LRCLIB: Loaded lyrics from cache.", verbose=True)
            return (lines or None), words, bool(cached.get("instrumental"))
        except (OSError, ValueError, TypeError):
            pass  # fall through and refetch

    clean_title = normalize_track_title(track)
    attempts: list[tuple[str, dict[str, str], str]] = [
        ("exact", {
            "artist_name": artist, "track_name": track,
            "album_name": album, "duration": str(duration_s),
        }, LRCLIB_API_URL),
    ]
    if clean_title != track:
        attempts.append(("normalized title", {
            "artist_name": artist, "track_name": clean_title,
            "album_name": album, "duration": str(duration_s),
        }, LRCLIB_API_URL))

    result: tuple[list[tuple[int, str]] | None, list[list[tuple[str, int]]], bool] | None = None

    for label, params, url in attempts:
        try:
            data = _lrclib_get(params, url)
        except HTTPError as exc:
            if exc.code == 404:
                log(f"LRCLIB: No {label} match.", verbose=True)
                continue
            log(f"LRCLIB: HTTP {exc.code} on {label} lookup.", "warn")
            return None, [], False
        except (URLError, TimeoutError, OSError) as exc:
            log(f"LRCLIB: Network error: {exc}", "warn")
            return None, [], False
        except ValueError as exc:
            log(f"LRCLIB: Bad response: {exc}", "warn")
            return None, [], False
        if data:
            result = _lyrics_from_payload(data)
            break

    if result is None:
        result = _lrclib_search(artist, clean_title, duration_s)

    if result is None:
        return None, [], False

    lines, words, instrumental = result
    if cache_path and (lines or instrumental):
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps({
                    "lines": lines or [],
                    "words": words,
                    "instrumental": instrumental,
                }),
                encoding="utf-8",
            )
        except OSError:
            pass  # cache is an optimization, never fatal

    return lines, words, instrumental


def _lrclib_search(
    artist: str, track: str, duration_s: int
) -> tuple[list[tuple[int, str]] | None, list[list[tuple[str, int]]], bool] | None:
    """Last-resort fuzzy lookup, picking the closest result by duration."""
    try:
        results = _lrclib_get(
            {"artist_name": artist, "track_name": track}, LRCLIB_SEARCH_URL
        )
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        log(f"LRCLIB: Search failed: {exc}", verbose=True)
        return None

    if not isinstance(results, list) or not results:
        log("LRCLIB: Search returned nothing.", verbose=True)
        return None

    synced = [r for r in results if r.get("syncedLyrics")]
    if not synced:
        return None

    # Duration is the strongest signal that we matched the right recording.
    best = min(synced, key=lambda r: abs((r.get("duration") or 0) - duration_s))
    if abs((best.get("duration") or 0) - duration_s) > 8:
        log("LRCLIB: Search hits were all the wrong length — ignoring.", verbose=True)
        return None

    log("LRCLIB: Matched via search fallback.")
    return _lyrics_from_payload(best)


def fetch_lyrics_async(
    artist: str, track: str, album: str, duration_s: int,
    state: SharedPlaybackState, lock: threading.Lock, track_key: str,
    cache_dir: Path | None = None,
) -> None:
    log(f"LRCLIB: Fetching lyrics for '{track}' by '{artist}'...")
    lyrics, words, is_instrumental = fetch_lyrics(
        artist, track, album, duration_s, cache_dir
    )
    with lock:
        if state.art_key == track_key:
            state.lyrics = lyrics
            state.lyrics_words = words
            state.lyrics_track_key = track_key
            state.is_instrumental = is_instrumental
    if lyrics:
        log(f"LRCLIB: Found {len(lyrics)} synced lyric lines.")
    elif is_instrumental:
        log("LRCLIB: Track is instrumental — no lyrics expected.")
    else:
        log("LRCLIB: No synced lyrics available for this track.")


# ═══════════════════════════════════════════════════════════════════
#  LYRICS RENDERER — Smooth vertical scroll
# ═══════════════════════════════════════════════════════════════════

def get_current_lyric_index(lyrics: list[tuple[int, str]], progress_ms: int) -> int:
    if not lyrics:
        return -1
    lo, hi = 0, len(lyrics) - 1
    result = -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if lyrics[mid][0] <= progress_ms:
            result = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return result


LYRICS_SCROLL_DURATION = 0.4  # seconds for smooth scroll animation
KARAOKE_MAX_ROWS = 4          # wrapped rows allowed for the active line


class _LyricsScroll:
    """Vertical scroll animation state for lyrics 'scroll' mode.

    Replaces a module-level dict that had two defects:

    1. The animation start position was the last *completed* target rather than
       where the text actually was on screen. Lines arriving closer together
       than LYRICS_SCROLL_DURATION (i.e. rap and fast verses) therefore snapped
       backwards before animating forward again.
    2. Nothing reset between tracks, so finishing a song on line 40 and starting
       the next on line 0 smoothly scrolled through 40 lines of nothing.
    """

    def __init__(self) -> None:
        self.track_key: str | None = None
        self.last_idx: int = -1
        self.from_y: float = 0.0
        self.target_y: float = 0.0
        self.current_y: float = 0.0
        self.transition_start: float = 0.0

    def sync_track(self, track_key: str | None) -> None:
        if track_key == self.track_key:
            return
        self.track_key = track_key
        self.last_idx = -1
        self.from_y = self.target_y = self.current_y = 0.0
        self.transition_start = 0.0

    def position(self, idx: int, line_height: int, now: float) -> float:
        if idx != self.last_idx:
            self.from_y = self.current_y  # animate from the visible position
            self.last_idx = idx
            self.target_y = float(idx * line_height)
            self.transition_start = now

        elapsed = now - self.transition_start
        if elapsed < LYRICS_SCROLL_DURATION:
            t = elapsed / LYRICS_SCROLL_DURATION
            eased = 1.0 - (1.0 - t) ** 3
            self.current_y = self.from_y + (self.target_y - self.from_y) * eased
        else:
            self.current_y = self.target_y
        return self.current_y


_lyrics_scroll = _LyricsScroll()


def _get_line_duration_ms(lyrics: list[tuple[int, str]], idx: int, total_duration_ms: int) -> int:
    """How long a lyric line is displayed before the next one starts."""
    if idx < 0 or idx >= len(lyrics):
        return 3000  # fallback
    start = lyrics[idx][0]
    if idx + 1 < len(lyrics):
        end = lyrics[idx + 1][0]
    else:
        end = total_duration_ms if total_duration_ms > 0 else start + 5000
    return max(200, end - start)  # at least 200ms


def _smart_h_scroll_x(
    text_w: int, size: int, time_on_screen_ms: int, line_duration_ms: int,
    text: str = "",
) -> int:
    """Calculate x offset for smart time-proportional horizontal scroll.

    Always starts at x=2 (first word visible).  Scrolls left proportionally
    so the end of the text is reached right as the line finishes.
    Uses ease-in-out for natural feel.

    Word-rate cap: if the line duration far exceeds the expected reading time,
    cap the scroll window so text scrolls at a natural reading pace instead
    of stretching across a long instrumental gap.
    """
    overflow = text_w - size + 4  # 4px right padding
    if overflow <= 0:
        return (size - text_w) // 2  # centered

    # Word-rate cap: estimate how long this text should take to read
    effective_duration = line_duration_ms
    if text:
        word_count = max(1, len(text.split()))
        expected_read_ms = word_count * AVG_MS_PER_WORD + 1000  # buffer
        if line_duration_ms > expected_read_ms * 2:
            # Cap scroll to natural reading speed, don't stretch across gap
            effective_duration = expected_read_ms

    # Leave a small margin at start and end of the line duration
    margin_ms = min(300, effective_duration // 6)
    scroll_window = max(1, effective_duration - margin_ms * 2)
    t_in_scroll = time_on_screen_ms - margin_ms

    if t_in_scroll <= 0:
        return 2  # first word visible
    if t_in_scroll >= scroll_window:
        return 2 - overflow  # last word visible

    # Ease-in-out cubic
    frac = t_in_scroll / scroll_window
    if frac < 0.5:
        eased = 4.0 * frac * frac * frac
    else:
        eased = 1.0 - (-2.0 * frac + 2.0) ** 3 / 2.0

    return 2 - int(eased * overflow)


def _legacy_h_scroll_x(
    text_w: int, size: int, is_active: bool, time_on_screen_ms: int,
) -> int:
    """Legacy horizontal scroll (non-smart): ping-pong for active, static for inactive."""
    overflow = text_w - size + 4
    if overflow <= 0:
        return (size - text_w) // 2

    if not is_active:
        return 2  # non-active lines: show start, no scroll

    # Active line: ping-pong scroll
    scroll_speed = 15.0  # px/s
    cycle_duration = overflow / scroll_speed
    pause = 1.0
    total_cycle = pause + cycle_duration + pause + cycle_duration
    t = (time_on_screen_ms / 1000.0) % total_cycle
    if t < pause:
        return 2
    elif t < pause + cycle_duration:
        frac = (t - pause) / cycle_duration
        return 2 - int(frac * overflow)
    elif t < pause * 2 + cycle_duration:
        return 2 - overflow
    else:
        frac = (t - pause * 2 - cycle_duration) / cycle_duration
        return 2 - overflow + int(frac * overflow)


LYRIC_NEXT_COLOR = (55, 55, 55)
LYRIC_UNSUNG_COLOR = (120, 120, 120)


def word_spans(
    text: str,
    line_start_ms: int,
    line_end_ms: int,
    word_times: list[tuple[str, int]] | None = None,
) -> list[tuple[str, int]]:
    """[(word, start_ms), ...] for a lyric line.

    Uses real enhanced-LRC timings when the source has them, otherwise spreads
    the line's duration across the words weighted by length — longer words take
    proportionally longer to sing, which tracks reality closely enough to read
    along by.
    """
    words = text.split()
    if not words:
        return []
    if word_times and len(word_times) == len(words):
        return list(word_times)

    weights = [len(w) + 1 for w in words]
    total = sum(weights) or 1
    duration = max(1, line_end_ms - line_start_ms)
    spans: list[tuple[str, int]] = []
    accumulated = 0
    for word, weight in zip(words, weights):
        spans.append((word, line_start_ms + int(duration * accumulated / total)))
        accumulated += weight
    return spans


def wrap_to_width(words: list[str], font: Any, max_width: int, draw: ImageDraw.ImageDraw) -> list[list[str]]:
    """Greedy word wrap into rows no wider than max_width."""
    rows: list[list[str]] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join(current + [word])
        if current and draw.textlength(candidate, font=font) > max_width:
            rows.append(current)
            current = [word]
        else:
            current.append(word)
    if current:
        rows.append(current)
    return rows


def _fit_lyric_block(
    words: list[str], size: int, max_font: int, max_rows: int,
    draw: ImageDraw.ImageDraw,
) -> tuple[Any, int, list[list[str]]]:
    """Largest font (down to 6) whose wrapped block fits in max_rows.

    A fixed size forces one compromise across every genre; a dense rap bar and
    a three-word hook want different sizes. The slider becomes a maximum.
    """
    for font_size in range(max_font, 5, -1):
        font = get_font(font_size)
        rows = wrap_to_width(words, font, size - 4, draw)
        if len(rows) <= max_rows:
            return font, font_size, rows
    font = get_font(6)
    return font, 6, wrap_to_width(words, font, size - 4, draw)[:max_rows]


def _render_karaoke(
    frame: Image.Image,
    draw: ImageDraw.ImageDraw,
    size: int,
    lyrics: list[tuple[int, str]],
    lyrics_words: list[list[tuple[str, int]]],
    idx: int,
    display_progress: int,
    duration_ms: int,
    max_font: int,
    accent_color: tuple[int, int, int],
    crisp: bool,
) -> None:
    """Karaoke layout: the whole line, wrapped, with words lit as they are sung.

    Horizontal scrolling could only ever show a 64px window that *follows* the
    vocal, so the words visible were the ones already sung and the next word was
    off-screen until it slid in. No amount of lead time fixes that, because the
    constraint is space, not time. Wrapping shows the entire line at once and
    lets the highlight carry position instead of content.
    """
    # Before the first line: count down to it rather than showing nothing.
    if idx < 0:
        first_start = lyrics[0][0]
        remaining = first_start - display_progress
        if 0 < remaining <= 5000:
            _draw_countdown(frame, draw, size, remaining, accent_color, crisp)
        return

    line_text = lyrics[idx][1]
    line_end = lyrics[idx + 1][0] if idx + 1 < len(lyrics) else (
        duration_ms if duration_ms > 0 else lyrics[idx][0] + 5000
    )

    # Empty active line means an instrumental gap — show how long until the
    # next line instead of a static row of dots.
    if not line_text.strip():
        remaining = line_end - display_progress
        if remaining > 0:
            _draw_countdown(frame, draw, size, remaining, accent_color, crisp)
        return

    word_times = lyrics_words[idx] if idx < len(lyrics_words) else []
    spans = word_spans(line_text, lyrics[idx][0], line_end, word_times)

    font, font_size, rows = _fit_lyric_block(
        [w for w, _ in spans], size, max_font, KARAOKE_MAX_ROWS, draw
    )
    line_height = font_size + 3

    # Next-line preview in whatever vertical space is left.
    next_rows: list[list[str]] = []
    next_font = get_font(max(6, font_size - 1))
    if idx + 1 < len(lyrics) and lyrics[idx + 1][1].strip():
        available = (size - 4) // line_height - len(rows)
        if available >= 1:
            next_rows = wrap_to_width(
                lyrics[idx + 1][1].split(), next_font, size - 4, draw
            )[: min(2, available)]

    next_height = len(next_rows) * (max(6, font_size - 1) + 3)
    total_height = len(rows) * line_height + (next_height + 3 if next_rows else 0)
    y = max(1, (size - 1 - total_height) // 2)

    sung: list[tuple[tuple[int, int], str]] = []
    unsung: list[tuple[tuple[int, int], str]] = []

    word_index = 0
    for row in rows:
        row_text = " ".join(row)
        row_x = (size - draw.textlength(row_text, font=font)) / 2.0
        # Position each word by measuring the row prefix, not by summing
        # per-word advances: the widths of "word " measured separately do not
        # add up to the width of the joined row, so words creep and overlap.
        prefix = ""
        for word in row:
            x = row_x + draw.textlength(prefix, font=font)
            start_ms = spans[word_index][1] if word_index < len(spans) else 0
            target = sung if display_progress >= start_ms else unsung
            target.append(((int(round(x)), y), word))
            prefix += word + " "
            word_index += 1
        y += line_height

    draw_text_batch(frame, unsung, font, LYRIC_UNSUNG_COLOR, crisp)
    draw_text_batch(frame, sung, font, accent_color, crisp)

    if next_rows:
        y += 3
        preview: list[tuple[tuple[int, int], str]] = []
        for row in next_rows:
            row_text = " ".join(row)
            row_width = draw.textlength(row_text, font=next_font)
            preview.append((((size - int(row_width)) // 2, y), row_text))
            y += max(6, font_size - 1) + 3
        draw_text_batch(frame, preview, next_font, LYRIC_NEXT_COLOR, crisp)


def _draw_countdown(
    frame: Image.Image,
    draw: ImageDraw.ImageDraw,
    size: int,
    remaining_ms: int,
    accent_color: tuple[int, int, int],
    crisp: bool,
) -> None:
    """Shrinking bar plus dots showing when the next line lands."""
    total = 5000.0
    fraction = max(0.0, min(1.0, remaining_ms / total))
    bar_w = int((size - 16) * fraction)
    y = size // 2

    if bar_w > 0:
        draw.rectangle((8, y - 1, 8 + bar_w, y + 1), fill=accent_color)

    # Drawn, not typed: U+2022 is absent from the default PIL font and renders
    # as a tofu box.
    dots = max(1, min(3, int(remaining_ms / 1000) + 1))
    spacing = 7
    start_x = size // 2 - ((dots - 1) * spacing) // 2
    for i in range(dots):
        cx = start_x + i * spacing
        draw.rectangle((cx - 1, y + 7, cx, y + 8), fill=LYRIC_UNSUNG_COLOR)


def render_lyrics(
    size: int,
    lyrics: list[tuple[int, str]] | None,
    duration_ms: int,
    is_playing: bool,
    fetch_time: float,
    stored_progress_ms: int,
    style: str = "scroll",
    smart_scroll: bool = True,
    font_size: int = 9,
    is_instrumental: bool = False,
    lyrics_lead_ms: int = 150,
    accent_color: tuple[int, int, int] = SPOTIFY_GREEN,
    track_key: str | None = None,
    lyrics_words: list[list[tuple[str, int]]] | None = None,
    crisp: bool = True,
    progress_offset_ms: float = 0.0,
) -> Image.Image:
    """Render lyrics with smooth scrolling or 3-line pop, with optional smart scroll.

    Enhancements:
    - Instrumental visualizer (pulsing bars) when track is instrumental.
    - Empty active lines show subtle '· · ·' dots.
    - lyrics_lead_ms shifts estimated progress forward for read-ahead.
    - accent_color replaces hardcoded SPOTIFY_GREEN.
    """
    frame = Image.new("RGB", (size, size), (0, 0, 0))
    draw = ImageDraw.Draw(frame)
    font = get_font(font_size)

    _lyrics_scroll.sync_track(track_key)

    # Calculate estimated progress
    if is_playing and fetch_time > 0:
        elapsed_since_fetch = (time.monotonic() - fetch_time) * 1000
        estimated_progress = stored_progress_ms + int(elapsed_since_fetch)
    else:
        estimated_progress = stored_progress_ms

    if duration_ms > 0:
        estimated_progress = min(estimated_progress, duration_ms)

    # Lead is the user's read-ahead preference; the offset is the measured
    # correction for Spotify's reporting lag. They serve different purposes.
    display_progress = estimated_progress + lyrics_lead_ms + int(progress_offset_ms)

    if not lyrics:
        if is_instrumental:
            # ── Instrumental Visualizer: pulsing bars ──
            now_t = time.monotonic()
            num_bars = 8
            bar_gap = 2
            total_bar_w = size - (num_bars + 1) * bar_gap
            bar_w = max(2, total_bar_w // num_bars)
            max_bar_h = size // 2 - 8
            min_bar_h = 4

            for i in range(num_bars):
                # Each bar pulses at a different phase
                phase = i * (math.pi / num_bars * 2)
                pulse = (math.sin(now_t * 3.0 + phase) + 1.0) / 2.0
                bar_h = int(min_bar_h + pulse * (max_bar_h - min_bar_h))

                bx = bar_gap + i * (bar_w + bar_gap)
                by = size // 2 - bar_h // 2 - 4

                # Gradient the color intensity per bar
                intensity = 0.4 + 0.6 * pulse
                bar_color = tuple(int(c * intensity) for c in accent_color)
                draw.rectangle((bx, by, bx + bar_w - 1, by + bar_h - 1), fill=bar_color)

            # "Instrumental" label below bars
            label = "Instrumental"
            lbbox = draw.textbbox((0, 0), label, font=font)
            lw = lbbox[2] - lbbox[0]
            lx = (size - lw) // 2
            ly = size // 2 + max_bar_h // 2
            draw.text((lx, ly - lbbox[1]), label, fill=LYRIC_DIM_COLOR, font=font)
        else:
            # No lyrics placeholder
            no_lyrics_text = "No Lyrics"
            bbox = draw.textbbox((0, 0), no_lyrics_text, font=font)
            tw = bbox[2] - bbox[0]
            x = (size - tw) // 2
            y = (size - (bbox[3] - bbox[1])) // 2
            draw.text((x, y - bbox[1]), no_lyrics_text, fill=LYRIC_DIM_COLOR, font=font)
            # Drawn rather than the U+266A character, which the default PIL
            # font does not contain and renders as a tofu box on the panel.
            note_y = y + 2
            for note_x in (x - 7, x + tw + 4):
                draw.ellipse(
                    (note_x, note_y + 3, note_x + 3, note_y + 6), fill=accent_color
                )
                draw.line(
                    (note_x + 3, note_y + 4, note_x + 3, note_y - 2), fill=accent_color
                )
                draw.line(
                    (note_x + 3, note_y - 2, note_x + 5, note_y - 1), fill=accent_color
                )
    else:
        idx = get_current_lyric_index(lyrics, display_progress)
        now_mono = time.monotonic()

        # Common: time the current line has been on screen
        if idx >= 0:
            time_on_screen_ms = max(0, display_progress - lyrics[idx][0])
            line_dur_ms = _get_line_duration_ms(lyrics, idx, duration_ms)
        else:
            time_on_screen_ms = 0
            line_dur_ms = 3000

        # Dots pattern for empty active lines (instrumental gaps)
        dots_text = "· · ·"

        if style == "karaoke":
            _render_karaoke(
                frame, draw, size, lyrics, lyrics_words or [], idx,
                display_progress, duration_ms, font_size, accent_color, crisp,
            )
        elif style == "scroll":
            # Smooth vertical scroll animation
            line_height = max(10, font_size + 4)
            center_y = size // 2 - font_size // 2

            current_y = _lyrics_scroll.position(idx, line_height, now_mono)

            visible_range = max(3, (size // line_height) // 2 + 1)
            fade_zone = 12

            for offset in range(-visible_range, visible_range + 1):
                li = idx + offset
                if li < 0 or li >= len(lyrics):
                    continue

                text = lyrics[li][1]
                is_active_line = (li == idx)

                # Handle empty lines
                if not text.strip():
                    if is_active_line:
                        # Show dots for instrumental gap at active position
                        text = dots_text
                    else:
                        continue

                line_target_y = li * line_height
                y_pixel = center_y + (line_target_y - current_y)

                if y_pixel < -12 or y_pixel > size + 12:
                    continue

                base_color = accent_color if is_active_line else LYRIC_DIM_COLOR

                # Vertical edge fade
                fade_factor = 1.0
                if y_pixel < fade_zone:
                    fade_factor = max(0.0, y_pixel / fade_zone)
                elif y_pixel > size - fade_zone - line_height:
                    fade_factor = max(0.0, (size - y_pixel - line_height) / fade_zone)
                fade_factor = max(0.0, min(1.0, fade_factor))
                color = tuple(int(c * fade_factor) for c in base_color)

                bbox = draw.textbbox((0, 0), text, font=font)
                text_w = bbox[2] - bbox[0]
                y_draw = int(y_pixel) - bbox[1]

                if text_w <= size:
                    x = (size - text_w) // 2
                elif not is_active_line:
                    # Non-active lines: show start, no scroll
                    x = 2
                elif smart_scroll:
                    x = _smart_h_scroll_x(text_w, size, time_on_screen_ms, line_dur_ms, text)
                else:
                    x = _legacy_h_scroll_x(text_w, size, True, time_on_screen_ms)

                draw.text((x, y_draw), text, fill=color, font=font)
        else:
            # Pop mode — 3 lines, spaced from the font size. The old fixed
            # [10, 28, 46] collided once the size slider went past ~11.
            spacing = max(12, font_size + 6)
            centre = size // 2 - font_size // 2
            y_positions = [centre - spacing, centre, centre + spacing]
            line_indices = [idx - 1, idx, idx + 1]
            colors = [LYRIC_DIM_COLOR, accent_color, LYRIC_DIM_COLOR]

            for line_i, (li, y_pos, color) in enumerate(zip(line_indices, y_positions, colors)):
                if li < 0 or li >= len(lyrics):
                    continue

                text = lyrics[li][1]
                is_active_line = (line_i == 1)

                # Handle empty lines
                if not text.strip():
                    if is_active_line:
                        text = dots_text
                    else:
                        continue

                bbox = draw.textbbox((0, 0), text, font=font)
                text_w = bbox[2] - bbox[0]
                y_draw = y_pos - bbox[1]

                if text_w <= size:
                    x = (size - text_w) // 2
                elif not is_active_line:
                    # Non-active lines: show start, no scroll
                    x = 2
                elif smart_scroll:
                    li_dur = _get_line_duration_ms(lyrics, li, duration_ms)
                    li_time = max(0, display_progress - lyrics[li][0])
                    x = _smart_h_scroll_x(text_w, size, li_time, li_dur, text)
                else:
                    x = _legacy_h_scroll_x(text_w, size, True, time_on_screen_ms)

                draw.text((x, y_draw), text, fill=color, font=font)

    # Progress bar at bottom (1px height)
    if duration_ms > 0:
        progress_frac = max(0.0, min(1.0, estimated_progress / duration_ms))
        bar_w = int(progress_frac * size)
        if bar_w > 0:
            draw.rectangle((0, size - 1, bar_w - 1, size - 1), fill=accent_color)
        if bar_w < size:
            draw.rectangle((bar_w, size - 1, size - 1, size - 1), fill=(30, 30, 30))

    return frame


def render_custom_slate(size: int, frames: list[Image.Image], delay: float) -> Image.Image:
    if not frames:
        return Image.new("RGB", (size, size), (0, 0, 0))
    if len(frames) == 1:
        return frames[0].copy()
    idx = int(time.time() / delay) % len(frames)
    return frames[idx].copy()


# ═══════════════════════════════════════════════════════════════════
#  WEB CONTROL PANEL — HTML
# ═══════════════════════════════════════════════════════════════════

CONTROL_PANEL_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
<title>SpotifyMatrix Control</title>
<style>
  /* No web-font import: this panel is a LAN appliance UI and must render
     correctly with no internet. The stack below resolves to the platform UI
     font everywhere, which is what Inter was approximating anyway. */

  * { margin: 0; padding: 0; box-sizing: border-box; }

  :root {
    --bg: #0a0a0a;
    --card: rgba(20, 20, 20, 0.6);
    --card-border: rgba(255, 255, 255, 0.1);
    --text: #e4e4e7;
    --text-dim: #71717a;
    --accent: #1ed760;
    --accent-dim: rgba(30, 215, 96, 0.15);
    --accent-glow: rgba(30, 215, 96, 0.3);
    --gold: #f59e0b;
  }

  body {
    background-color: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto,
                 'Helvetica Neue', Arial, sans-serif;
    -webkit-font-smoothing: antialiased;
    padding: 20px;
    padding-bottom: 60px;
  }

  /* Dynamic Background */
  #appBackground {
    position: fixed;
    top: -20px; left: -20px; right: -20px; bottom: -20px;
    background-size: cover;
    background-position: center;
    filter: blur(40px) brightness(0.4);
    z-index: -1;
    transition: background-image 1s ease;
  }

  .container { max-width: 480px; margin: 0 auto; display: flex; flex-direction: column; gap: 16px; position: relative; z-index: 1; }
  .header { text-align: center; margin-bottom: 10px; text-shadow: 0 2px 10px rgba(0,0,0,0.5); }
  .header h1 { font-size: 24px; font-weight: 700; letter-spacing: -0.5px; }
  .header p { font-size: 13px; color: #a1a1aa; margin-top: 4px; }

  /* Glassmorphism Cards */
  .card {
    background: var(--card);
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    border: 1px solid var(--card-border);
    border-radius: 16px;
    padding: 20px;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
  }
  .card-title { font-size: 13px; text-transform: uppercase; letter-spacing: 1px; color: #a1a1aa; font-weight: 600; margin-bottom: 16px; text-shadow: 0 1px 4px rgba(0,0,0,0.8);
    display: flex; align-items: center; justify-content: space-between; gap: 10px; }

  .now-playing { display: flex; gap: 16px; align-items: center; position: relative; overflow: hidden; }
  .album-art { width: 72px; height: 72px; border-radius: 8px; background: #27272a; flex-shrink: 0; box-shadow: 0 4px 12px rgba(0,0,0,0.4); }
  .album-art img { width: 100%; height: 100%; border-radius: 8px; object-fit: cover; }
  .track-info { flex: 1; min-width: 0; }
  .track-title { font-size: 16px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; text-shadow: 0 1px 4px rgba(0,0,0,0.8); }
  .track-artist { font-size: 14px; color: #a1a1aa; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 4px; text-shadow: 0 1px 4px rgba(0,0,0,0.8); }
  .instrumental-badge { display: inline-block; font-size: 10px; font-weight: 600; background: var(--accent-dim); color: var(--accent); padding: 2px 6px; border-radius: 4px; margin-top: 6px; border: 1px solid var(--accent-glow); }

  /* Modes */
  .modes { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; }
  .mode-btn {
    background: rgba(255,255,255,0.05); border: 1px solid var(--card-border); color: var(--text);
    padding: 12px; border-radius: 12px; font-size: 14px; font-weight: 500; cursor: pointer; transition: all 0.2s;
  }
  .mode-btn.active { background: var(--accent-dim); border-color: var(--accent); color: var(--accent); }
  .mode-btn:active { transform: scale(0.98); }

  /* Sliders */
  .slider-group { margin-bottom: 16px; }
  .slider-group:last-child { margin-bottom: 0; }
  .slider-label { display: flex; justify-content: space-between; margin-bottom: 8px; font-size: 13px; font-weight: 500; text-shadow: 0 1px 4px rgba(0,0,0,0.8); }
  .slider-label .value { color: var(--accent); font-variant-numeric: tabular-nums; }
  input[type=range] {
    width: 100%; -webkit-appearance: none; background: transparent; height: 24px; cursor: pointer;
  }
  input[type=range]::-webkit-slider-runnable-track {
    width: 100%; height: 6px; background: rgba(255,255,255,0.1); border-radius: 3px;
  }
  input[type=range]::-webkit-slider-thumb {
    height: 18px; width: 18px; border-radius: 50%; background: var(--text);
    -webkit-appearance: none; margin-top: -6px; box-shadow: 0 2px 6px rgba(0,0,0,0.5); transition: transform 0.1s;
  }
  input[type=range]:active::-webkit-slider-thumb { transform: scale(1.2); background: var(--accent); }

  /* Lyric Styles */
  .segmented { display: flex; background: rgba(0,0,0,0.4); border-radius: 8px; padding: 4px; margin-bottom: 16px; border: 1px solid var(--card-border); }
  .seg-btn {
    flex: 1; text-align: center; padding: 6px 0; font-size: 13px; font-weight: 500;
    color: var(--text-dim); cursor: pointer; border-radius: 6px; transition: all 0.2s;
  }
  .seg-btn.active { background: rgba(255,255,255,0.1); color: var(--text); box-shadow: 0 2px 8px rgba(0,0,0,0.2); }

  /* Custom Slate */
  #customSlateCard { display: none; } /* Hidden by default */
  .msg-input-row { display: flex; gap: 8px; }
  .msg-input {
    flex: 1; background: rgba(0,0,0,0.4); border: 1px solid var(--card-border);
    color: white; padding: 10px 12px; border-radius: 8px; font-family: inherit; font-size: 14px; outline: none;
  }
  .msg-input:focus { border-color: var(--accent); }
  .msg-btn {
    background: var(--accent); color: #000; border: none; font-weight: 600;
    padding: 0 16px; border-radius: 8px; cursor: pointer; font-size: 14px;
  }
  .msg-btn.clear { background: rgba(255,255,255,0.1); color: var(--text); }
  
  /* Expander for Advanced Settings */
  .adv-toggle {
    width: 100%; text-align: center; background: transparent; border: 1px solid var(--card-border);
    color: var(--text-dim); padding: 10px; border-radius: 12px; cursor: pointer; font-size: 13px; font-weight: 500; margin-bottom: 16px;
  }
  #advSettings { display: none; }

  /* ── Added controls ──────────────────────────────────────────── */
  .banner {
    background: rgba(200,40,50,0.18); border: 1px solid rgba(220,60,70,0.5);
    color: #ffb4b4; padding: 10px 14px; border-radius: 10px; margin-bottom: 14px;
    font-size: 13px; font-weight: 500; text-align: center;
  }
  .banner.warn {
    background: rgba(220,160,40,0.16); border-color: rgba(240,180,60,0.5);
    color: #ffd89b;
  }
  .now-label {
    font-size: 11px; font-weight: 500; color: var(--accent);
    text-transform: uppercase; letter-spacing: 0.04em;
  }
  .mode-hint {
    color: #a1a1aa; font-size: 11.5px; line-height: 1.45; margin-top: 10px;
  }
  .select {
    width: 100%; background: rgba(0,0,0,0.45); color: white; font-family: inherit;
    font-size: 14px; padding: 10px 12px; border-radius: 8px;
    border: 1px solid var(--card-border); outline: none; margin-top: 10px;
  }
  .select:focus-visible { border-color: var(--accent); }

  .preview-wrap { display: flex; justify-content: center; padding: 6px 0 2px; }
  /* Nearest-neighbour and a fixed box: the whole point is to see the pixels. */
  #matrixPreview {
    width: 192px; height: 192px; image-rendering: pixelated;
    border-radius: 10px; border: 1px solid var(--card-border);
    background: #000; box-shadow: 0 6px 18px rgba(0,0,0,0.55);
  }
  .mini-toggle { font-size: 11px; color: #a1a1aa; font-weight: 500; }
  .mini-toggle input { vertical-align: middle; margin-right: 3px; }

  .progress-track {
    height: 5px; background: rgba(255,255,255,0.14); border-radius: 3px;
    overflow: hidden;
  }
  .progress-fill {
    height: 100%; width: 0%; background: var(--accent); border-radius: 3px;
    transition: width 0.35s linear;
  }
  .progress-times {
    display: flex; justify-content: space-between; margin-top: 7px;
    font-size: 11.5px; color: #a1a1aa; font-variant-numeric: tabular-nums;
  }
  .transport {
    display: flex; justify-content: center; align-items: center; gap: 18px;
    margin-top: 14px;
  }
  .tp-btn {
    background: rgba(255,255,255,0.08); border: 1px solid var(--card-border);
    color: white; width: 42px; height: 42px; border-radius: 50%;
    font-size: 17px; cursor: pointer; line-height: 1;
    display: flex; align-items: center; justify-content: center;
  }
  .tp-btn:active { transform: scale(0.92); }
  .tp-btn.tp-main {
    width: 52px; height: 52px; font-size: 21px;
    background: var(--accent); border-color: transparent; color: #04140a;
  }
  .up-next {
    font-size: 11px; color: #a1a1aa; margin-top: 5px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .toggle-row { display: flex; flex-direction: column; gap: 12px; margin-top: 18px; }
  .toggle { display: flex; align-items: flex-start; gap: 9px; cursor: pointer; font-size: 13px; }
  .toggle input { margin-top: 2px; width: 16px; height: 16px; accent-color: var(--accent); }
  .toggle em { display: block; font-style: normal; font-size: 11px; color: #8b8b93; margin-top: 1px; }
  .btn-sleep { background: rgba(80,90,160,0.28); }

  /* Color Grid */
  .color-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; }
  .color-swatch {
    aspect-ratio: 1; border-radius: 50%; cursor: pointer; position: relative;
    border: 2px solid transparent; transition: transform 0.2s; box-shadow: 0 4px 12px rgba(0,0,0,0.4);
  }
  .color-swatch:active { transform: scale(0.9); }
  .color-swatch .check {
    position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
    color: white; font-size: 16px; font-weight: bold; opacity: 0; text-shadow: 0 1px 4px rgba(0,0,0,0.5);
  }
  .color-swatch.active { border-color: white; transform: scale(1.1); }
  .color-swatch.active .check { opacity: 1; }

  .btn-row { display: flex; gap: 12px; }
  .btn {
    flex: 1; padding: 12px; border-radius: 12px; border: none; font-weight: 600; font-size: 14px; cursor: pointer;
  }
  .btn-reset { background: rgba(255,255,255,0.05); color: #ef4444; border: 1px solid rgba(239, 68, 68, 0.2); }
  .btn-logs { background: rgba(255,255,255,0.05); color: var(--text); border: 1px solid var(--card-border); }

  .footer { text-align: center; font-size: 12px; color: #52525b; margin-top: 24px; text-shadow: 0 1px 4px rgba(0,0,0,0.8); }
</style>
</head>
<body>

<div id="appBackground"></div>

<div class="container">
  <div class="header">
    <h1>SpotifyMatrix</h1>
  </div>

  <!-- Connection / status banner. Hidden until something is actually wrong,
       so the normal case costs no vertical space. -->
  <div id="connBanner" class="banner" style="display:none"></div>

  <!-- Now Playing -->
  <div class="card now-playing" onclick="toggleLiveLyrics()" style="cursor: pointer;" title="Tap for Live Lyrics">
    <div class="album-art">
      <img id="npImg" src="" style="display:none">
    </div>
    <div class="track-info">
      <div id="npTitle" class="track-title" style="color:var(--text-dim)">Not Playing</div>
      <div id="npArtist" class="track-artist">--</div>
      <div id="npInstr" class="instrumental-badge" style="display:none">&#127929; Instrumental</div>
      <div id="npNext" class="up-next" style="display:none"></div>
    </div>
  </div>

  <!-- Progress -->
  <div class="card" id="progressCard" style="display:none">
    <div class="progress-track"><div class="progress-fill" id="progFill"></div></div>
    <div class="progress-times">
      <span id="progElapsed">0:00</span>
      <span id="progTotal">0:00</span>
    </div>
    <div class="transport" id="transport" style="display:none">
      <button class="tp-btn" onclick="playback('previous')" title="Previous">&#9198;</button>
      <button class="tp-btn tp-main" id="tpPlay" onclick="togglePlay()" title="Play/Pause">&#9199;</button>
      <button class="tp-btn" onclick="playback('next')" title="Next">&#9197;</button>
    </div>
  </div>

  <!-- Live matrix preview -->
  <div class="card">
    <div class="card-title">
      <span>&#128250; Live Preview</span>
      <label class="mini-toggle"><input type="checkbox" id="previewOn" checked onchange="togglePreview()"> on</label>
    </div>
    <div class="preview-wrap">
      <img id="matrixPreview" alt="Live view of the LED matrix" width="64" height="64">
    </div>
  </div>

  <!-- Live Lyrics Drawer -->
  <div class="card" id="liveLyricsCard" style="display:none;">
    <div class="card-title">&#127908; Live Lyrics</div>
    <div id="liveLyricsBox" style="height:200px; overflow-y:auto; font-size:14px; line-height:1.6; color:var(--text-dim); text-align:center; padding-right:10px; position:relative;">
      <!-- Lyrics injected here -->
    </div>
  </div>

  <!-- Display Mode -->
  <div class="card">
    <div class="card-title">
      <span>&#128242; Display Mode</span>
      <span class="now-label" id="effectiveLabel"></span>
    </div>
    <div class="modes">
      <button class="mode-btn" id="mode-default" onclick="setMode('default')">Original</button>
      <button class="mode-btn" id="mode-cd" onclick="setMode('cd')">CD View</button>
      <button class="mode-btn" id="mode-lyrics" onclick="setMode('lyrics')">Lyrics</button>
      <button class="mode-btn" id="mode-art" onclick="setMode('art')">Album Art</button>
      <button class="mode-btn" id="mode-clock" onclick="setMode('clock')">Idle Screen</button>
      <button class="mode-btn" id="mode-custom" onclick="setMode('custom')">Custom Slate</button>
    </div>
    <p class="mode-hint" id="modeHint">Original: idle screen when stopped, disc when a track starts, then lyrics.</p>
  </div>

  <!-- Idle screen -->
  <div class="card">
    <div class="card-title">&#127756; Idle Screen</div>
    <p class="mode-hint">Shown whenever nothing is playing &mdash; and permanently in Idle Screen mode.</p>
    <select id="idleMode" class="select" onchange="setSetting('idle-mode', this.value)">
      <option value="clock">Clock (original)</option>
      <option value="plasma">Plasma</option>
      <option value="rain">Matrix Rain</option>
      <option value="stars">Starfield</option>
      <option value="life">Game of Life</option>
      <option value="fire">Fireplace</option>
      <option value="cycle">Cycle through all</option>
    </select>
  </div>

  <!-- Main Settings (Brightness & Lyric Style) -->
  <div class="card" id="mainSettingsCard">
    <div class="card-title">&#9881; Main Settings</div>
    
    <div class="segmented">
      <div class="seg-btn" id="style-karaoke" onclick="setSetting('lyrics-style', 'karaoke')">Karaoke</div>
      <div class="seg-btn" id="style-scroll" onclick="setSetting('lyrics-style', 'scroll')">Scroll</div>
      <div class="seg-btn" id="style-pop" onclick="setSetting('lyrics-style', 'pop')">Pop</div>
    </div>

    <div class="slider-group">
      <div class="slider-label">
        <span class="name">&#9728; Brightness</span>
        <span class="value" id="brightnessVal">65</span>
      </div>
      <input type="range" id="brightness" min="1" max="100" value="65"
             oninput="document.getElementById('brightnessVal').textContent=this.value"
             onchange="setSetting('brightness', this.value)">
    </div>
  </div>

  <!-- Custom Slate Editor -->
  <div class="card" id="customSlateCard">
    <div class="card-title">&#127912; Custom Slate (Canvas)</div>
    <p style="color:#a1a1aa; font-size:12px; margin-bottom:10px; text-shadow: 0 1px 4px rgba(0,0,0,0.8);">Upload an image or GIF to cast it to the Matrix!</p>
    <input type="file" id="slateUpload" accept="image/*" style="margin-bottom:10px; width:100%; color: white; background: rgba(0,0,0,0.4); padding: 5px; border-radius:4px; border: 1px solid var(--card-border);">
    
    <div class="msg-input-row" style="margin-bottom:10px;">
      <input type="text" class="msg-input" id="slateText" placeholder="Add text...">
      <input type="color" id="slateColor" value="#ffffff" style="width:30px; border:none; padding:0; background:none;">
      <button class="msg-btn" onclick="addSlateText()">Add</button>
    </div>
    
    <div style="display: flex; justify-content: center; margin-bottom: 10px;">
      <canvas id="slateCanvas" width="64" height="64" style="width: 128px; height: 128px; border: 1px solid var(--card-border); image-rendering: pixelated; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.5);"></canvas>
    </div>
    <div class="msg-input-row" style="justify-content:center;">
      <button class="msg-btn clear" onclick="clearSlate()">Clear</button>
      <button class="msg-btn" onclick="sendCustomSlate()">Cast to Matrix</button>
    </div>
  </div>

  <!-- Advanced Settings -->
  <button class="adv-toggle" id="advToggleBtn" onclick="toggleAdv()">Show Advanced Settings &#9662;</button>
  <div id="advSettings">
    <div class="card">
      <div class="card-title">&#9881; Advanced Tweaks</div>

      <div class="slider-group">
        <div class="slider-label">
          <span class="name">Scroll Font Size</span>
          <span class="value" id="scrollFontVal">9</span>
        </div>
        <input type="range" id="scrollFont" min="6" max="14" value="9"
               oninput="document.getElementById('scrollFontVal').textContent=this.value"
               onchange="setSetting('scroll-font-size', this.value)">
      </div>
      <div class="slider-group">
        <div class="slider-label">
          <span class="name">Pop Font Size</span>
          <span class="value" id="popFontVal">9</span>
        </div>
        <input type="range" id="popFont" min="6" max="14" value="9"
               oninput="document.getElementById('popFontVal').textContent=this.value"
               onchange="setSetting('pop-font-size', this.value)">
      </div>
      <div class="slider-group">
        <div class="slider-label">
          <span class="name">Lyrics Lead (ms)</span>
          <span class="value" id="leadVal">180</span>
        </div>
        <input type="range" id="leadTime" min="0" max="500" step="10" value="180"
               oninput="document.getElementById('leadVal').textContent=this.value"
               onchange="setSetting('lyrics-lead', this.value)">
      </div>

      <div class="slider-group" style="margin-top:16px;">
        <div class="slider-label">
          <span class="name">&#128171; Spin Speed (RPM)</span>
          <span class="value" id="spinVal">10</span>
        </div>
        <input type="range" id="spinSpeed" min="1" max="120" value="10"
               oninput="document.getElementById('spinVal').textContent=this.value"
               onchange="setSetting('spin-speed', this.value)">
      </div>
      <div class="slider-group">
        <div class="slider-label">
          <span class="name">&#128220; Text Scroll Speed</span>
          <span class="value" id="textVal">20</span>
        </div>
        <input type="range" id="textSpeed" min="1" max="100" value="20"
               oninput="document.getElementById('textVal').textContent=this.value"
               onchange="setSetting('text-speed', this.value)">
      </div>

      <div class="slider-group">
        <div class="slider-label">
          <span class="name">&#128337; Disc Before Lyrics (s)</span>
          <span class="value" id="cdDurVal">10</span>
        </div>
        <input type="range" id="cdDuration" min="2" max="60" value="10"
               oninput="document.getElementById('cdDurVal').textContent=this.value"
               onchange="setSetting('cd-duration', this.value)">
      </div>

      <div class="slider-group">
        <div class="slider-label">
          <span class="name">&#9899; Line Width (CD &amp; Art)</span>
          <span class="value" id="lineWidthVal">1</span>
        </div>
        <input type="range" id="lineWidth" min="1" max="5" value="1"
               oninput="document.getElementById('lineWidthVal').textContent=this.value"
               onchange="setSetting('line-width', this.value)">
      </div>

      <div class="toggle-row">
        <label class="toggle">
          <input type="checkbox" id="smartScroll" onchange="setSetting('smart-scroll', this.checked)">
          <span>Smart scroll <em>time-proportional lyric scrolling</em></span>
        </label>
        <label class="toggle">
          <input type="checkbox" id="progressRing" onchange="setSetting('progress-ring', this.checked)">
          <span>Progress ring <em>arc around the disc</em></span>
        </label>
        <label class="toggle">
          <input type="checkbox" id="artPan" onchange="setSetting('art-pan', this.checked)">
          <span>Ken Burns pan <em>slow drift in Album Art mode</em></span>
        </label>
      </div>

    </div>
  </div>

  <!-- Accent Color -->
  <div class="card" id="colorCard">
    <div class="card-title">&#127912; Accent Color</div>
    <div class="color-grid" id="colorGrid"></div>
  </div>

  <!-- Actions -->
  <div class="card">
    <div class="btn-row">
      <button class="btn btn-sleep" id="sleepBtn" onclick="toggleSleep()">&#127769; Sleep</button>
      <button class="btn btn-reset" onclick="resetAll()">&#8635; Reset All</button>
      <button class="btn btn-logs" onclick="window.location='/logs'">&#128196; Logs</button>
    </div>
  </div>

  <div class="footer">SpotifyMatrix &middot; matrixspot.local</div>
</div>

<script>
let currentState = {};
let lyricsOpen = false;
let lyricsData = null;
let lyricsInterval = null;
let lastArtKey = undefined;

const COLOR_THEMES = {
  spotify:  {r:30,g:215,b:96},
  sunset:   {r:255,g:107,b:53},
  neon:     {r:180,g:60,b:255},
  rose:     {r:255,g:90,b:150},
  arctic:   {r:0,g:220,b:220},
  gold:     {r:245,g:180,b:40},
  crimson:  {r:220,g:40,b:60}
};

(function buildSwatches() {
  const grid = document.getElementById('colorGrid');
  for (const [name, c] of Object.entries(COLOR_THEMES)) {
    const el = document.createElement('div');
    el.className = 'color-swatch';
    el.dataset.theme = name;
    el.style.background = `rgb(${c.r},${c.g},${c.b})`;
    el.innerHTML = '<span class="check">&#10003;</span>';
    el.onclick = () => setAccentColor(name);
    grid.appendChild(el);
  }
  
  // "Auto" — follow the album art. Its swatch shows whatever colour the
  // device most recently derived, so it doubles as a readout.
  const autoEl = document.createElement('div');
  autoEl.className = 'color-swatch';
  autoEl.dataset.theme = 'auto';
  autoEl.title = 'Match the album art';
  autoEl.style.background = 'linear-gradient(135deg,#ff6b35,#b43cff,#00dcdc)';
  autoEl.innerHTML = '<span class="check">&#10003;</span>';
  autoEl.onclick = () => setAccentColor('auto');
  grid.appendChild(autoEl);

  // "Contrast" — aesthetically complement the album art.
  const contrastEl = document.createElement('div');
  contrastEl.className = 'color-swatch';
  contrastEl.dataset.theme = 'contrast';
  contrastEl.title = 'Contrast against album art (clean complementary)';
  contrastEl.style.background = 'linear-gradient(135deg,#00f2fe,#4facfe,#fa709a)';
  contrastEl.innerHTML = '<span class="check">&#10003;</span>';
  contrastEl.onclick = () => setAccentColor('contrast');
  grid.appendChild(contrastEl);

  // Custom Color Picker
  const customEl = document.createElement('div');
  customEl.className = 'color-swatch';
  customEl.dataset.theme = 'custom';
  customEl.style.position = 'relative';
  customEl.style.overflow = 'hidden';
  customEl.style.background = 'conic-gradient(red, yellow, lime, aqua, blue, magenta, red)';
  
  const pickerInput = document.createElement('input');
  pickerInput.type = 'color';
  pickerInput.style.position = 'absolute';
  pickerInput.style.opacity = '0';
  pickerInput.style.width = '100%';
  pickerInput.style.height = '100%';
  pickerInput.style.cursor = 'pointer';
  pickerInput.style.zIndex = '10';
  pickerInput.style.left = '0';
  pickerInput.style.top = '0';
  pickerInput.value = '#00dcdd';
  
  customEl.onclick = () => pickerInput.click();
  
  pickerInput.addEventListener('input', async (e) => {
    const hex = e.target.value;
    customEl.style.background = hex;
  });
  
  pickerInput.addEventListener('change', async (e) => {
    const hex = e.target.value;
    const r = parseInt(hex.substr(1,2), 16);
    const g = parseInt(hex.substr(3,2), 16);
    const b = parseInt(hex.substr(5,2), 16);
    try {
      await fetch('/api/accent-color', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({value: 'custom', r: r, g: g, b: b})
      });
      setTimeout(fetchState, 100);
    } catch(err) {}
  });
  
  customEl.appendChild(pickerInput);
  const check = document.createElement('span');
  check.className = 'check';
  check.innerHTML = '&#10003;';
  customEl.appendChild(check);
  grid.appendChild(customEl);
})();

/* Track the intended state explicitly. Reading style.display broke when
   Custom Slate mode hid the panel behind our back: the button still said
   "Hide", so the next click set display:none on an already-hidden panel. */
let advOpen = false;

function applyAdvVisibility(visible) {
  const adv = document.getElementById('advSettings');
  const btn = document.querySelector('.adv-toggle');
  if (adv) adv.style.display = visible ? 'block' : 'none';
  if (btn) btn.innerHTML = visible
    ? 'Hide Advanced Settings &#9652;'
    : 'Show Advanced Settings &#9662;';
}

function toggleAdv() {
  advOpen = !advOpen;
  applyAdvVisibility(advOpen);
}

function escapeHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

/* Every request goes through here so one hung fetch cannot stall the polling
   loop. Without the abort a phone that suspends mid-request leaves the panel
   frozen until TCP eventually gives up. */
async function apiFetch(url, options, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs || 6000);
  try {
    return await fetch(url, Object.assign({}, options || {}, {signal: controller.signal}));
  } finally {
    clearTimeout(timer);
  }
}

let consecutiveFailures = 0;

function showBanner(text, kind) {
  const el = document.getElementById('connBanner');
  if (!el) return;
  if (!text) { el.style.display = 'none'; return; }
  el.textContent = text;
  el.className = 'banner' + (kind ? ' ' + kind : '');
  el.style.display = 'block';
}

async function fetchState() {
  try {
    const res = await apiFetch('/api/state');
    const s = await res.json();
    currentState = s;
    consecutiveFailures = 0;
    updateUI(s);
  } catch(e) {
    // One miss is normal on a phone waking up; say so only if it persists.
    if (++consecutiveFailures >= 3) {
      showBanner('Cannot reach the matrix — check that it is powered on.');
    }
  }
}

function fmtTime(ms) {
  if (!ms || ms < 0) ms = 0;
  const total = Math.floor(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return m + ':' + String(s).padStart(2, '0');
}

function updateUI(s) {
  // Update Background Image
  const appBg = document.getElementById('appBackground');
  if (s.image_url) {
    appBg.style.backgroundImage = `url('${s.image_url}')`;
    document.getElementById('npImg').src = s.image_url;
    document.getElementById('npImg').style.display = 'block';
  } else {
    appBg.style.backgroundImage = 'none';
    document.getElementById('npImg').style.display = 'none';
  }
  
  if (s.is_playing) {
    document.getElementById('npTitle').textContent = s.title;
    document.getElementById('npTitle').style.color = 'var(--text)';
    document.getElementById('npArtist').textContent = s.artist;
  } else {
    document.getElementById('npTitle').textContent = s.is_connected ? "Paused" : "Not Playing";
    document.getElementById('npTitle').style.color = 'var(--text-dim)';
    document.getElementById('npArtist').textContent = "--";
  }

  // Banner: the device's own words about why nothing is playing beat any
  // guess the browser could make.
  if (s.status_message) {
    showBanner(s.status_message + (s.status_detail ? ' — ' + s.status_detail : ''));
  } else if (!s.is_connected) {
    showBanner('Matrix is online but Spotify is unreachable.', 'warn');
  } else if (s.sleeping) {
    showBanner('Panel is asleep.', 'warn');
  } else {
    showBanner('');
  }

  // Up next
  const nextEl = document.getElementById('npNext');
  if (nextEl) {
    if (s.queue_next) {
      nextEl.textContent = '\\u23ED Next: ' + s.queue_next;
      nextEl.style.display = 'block';
    } else {
      nextEl.style.display = 'none';
    }
  }

  updateProgress(s);

  // Update modes visibility
  document.querySelectorAll('.mode-btn').forEach(el => el.classList.remove('active'));
  const mBtn = document.getElementById('mode-' + s.display_mode);
  if (mBtn) mBtn.classList.add('active');

  // What is actually on the panel right now. In Auto/Original the chosen mode
  // does not tell you that, which made the button misleading.
  const effLabel = document.getElementById('effectiveLabel');
  if (effLabel) {
    const names = {
      cd: 'CD view', lyrics: 'Lyrics', art: 'Album art', clock: 'Clock',
      custom: 'Custom slate', plasma: 'Plasma', rain: 'Matrix rain',
      stars: 'Starfield', life: 'Game of Life', fire: 'Fireplace'
    };
    const eff = names[s.effective_mode] || s.effective_mode || '';
    effLabel.textContent = (s.display_mode === 'default' && eff) ? 'now: ' + eff : '';
  }

  const hint = document.getElementById('modeHint');
  if (hint) {
    const hints = {
      default: 'Original: idle screen when stopped, disc when a track starts, then lyrics.',
      cd: 'Always the spinning disc.',
      lyrics: 'Always lyrics.',
      art: 'Cover art filling the panel, with a progress bar.',
      clock: 'Always the idle screen selected below.',
      custom: 'Your uploaded image or GIF.'
    };
    hint.textContent = hints[s.display_mode] || '';
  }

  const idleSel = document.getElementById('idleMode');
  if (idleSel && document.activeElement !== idleSel && s.idle_mode) {
    idleSel.value = s.idle_mode;
  }

  function setChk(id, val) {
    const el = document.getElementById(id);
    if (el && document.activeElement !== el) el.checked = !!val;
  }
  setChk('smartScroll', s.smart_scroll);
  setChk('progressRing', s.progress_ring);
  setChk('artPan', s.art_pan);

  const sleepBtn = document.getElementById('sleepBtn');
  if (sleepBtn) sleepBtn.innerHTML = s.sleeping ? '\\u2600 Wake' : '\\u{1F319} Sleep';

  const transport = document.getElementById('transport');
  if (transport) transport.style.display = s.can_control ? 'flex' : 'none';
  const tpPlay = document.getElementById('tpPlay');
  if (tpPlay) tpPlay.innerHTML = s.is_playing ? '\\u23F8' : '\\u25B6';

  const customCard = document.getElementById('customSlateCard');
  const mainSettings = document.getElementById('mainSettingsCard');
  const advBtn = document.getElementById('advToggleBtn');
  const advCard = document.getElementById('advSettings');
  const colorCard = document.getElementById('colorCard');
  
  if (s.display_mode === 'custom') {
    customCard.style.display = 'block';
    if(mainSettings) mainSettings.style.display = 'none';
    if(advBtn) advBtn.style.display = 'none';
    if(advCard) advCard.style.display = 'none';
    if(colorCard) colorCard.style.display = 'none';
  } else {
    customCard.style.display = 'none';
    if(mainSettings) mainSettings.style.display = 'block';
    if(advBtn) advBtn.style.display = 'block';
    if(colorCard) colorCard.style.display = 'block';
    applyAdvVisibility(advOpen);   // restore whatever the user had chosen
  }

  // Segmented lyrics style
  document.querySelectorAll('.seg-btn').forEach(el => el.classList.remove('active'));
  const sBtn = document.getElementById('style-' + s.lyrics_style);
  if (sBtn) sBtn.classList.add('active');

  // Sliders
  function setSld(id, lblId, val) {
    const el = document.getElementById(id);
    if(el && document.activeElement !== el) {
      el.value = val;
      const lbl = document.getElementById(lblId);
      if(lbl) lbl.textContent = val;
    }
  }
  setSld('brightness', 'brightnessVal', s.brightness);
  setSld('spinSpeed', 'spinVal', s.spin_speed);
  setSld('textSpeed', 'textVal', s.text_scroll_speed);
  setSld('scrollFont', 'scrollFontVal', s.scroll_font_size);
  setSld('popFont', 'popFontVal', s.pop_font_size);
  setSld('leadTime', 'leadVal', s.lyrics_lead_ms);
  setSld('cdDuration', 'cdDurVal', Math.round(s.cd_duration));
  setSld('lineWidth', 'lineWidthVal', s.line_width || 1);

  // Colors. Prefer the exact RGB the device reports — a named theme lookup
  // cannot express 'custom' or the art-derived 'auto'.
  const rgb = s.accent_rgb || [];
  const t = (rgb.length === 3)
    ? {r: rgb[0], g: rgb[1], b: rgb[2]}
    : (COLOR_THEMES[s.accent_name] || COLOR_THEMES.spotify);
  document.documentElement.style.setProperty('--accent', `rgb(${t.r},${t.g},${t.b})`);
  document.documentElement.style.setProperty('--accent-dim', `rgba(${t.r},${t.g},${t.b},0.15)`);
  document.documentElement.style.setProperty('--accent-glow', `rgba(${t.r},${t.g},${t.b},0.3)`);
  const autoSw = document.querySelector('.color-swatch[data-theme="auto"]');
  if (autoSw && s.accent_name === 'auto') autoSw.style.background = `rgb(${t.r},${t.g},${t.b})`;
  const contrastSw = document.querySelector('.color-swatch[data-theme="contrast"]');
  const cRgb = s.contrast_accent_rgb || [];
  if (contrastSw && cRgb.length === 3) {
    contrastSw.style.background = `rgb(${cRgb[0]},${cRgb[1]},${cRgb[2]})`;
  }
  
  document.querySelectorAll('.color-swatch').forEach(el => {
    el.classList.toggle('active', el.dataset.theme === s.accent_name);
  });
  
  // Badges
  const instr = document.getElementById('npInstr');
  if (s.is_instrumental) { instr.style.display = 'inline-block'; }
  else { instr.style.display = 'none'; }

  // has_lyrics was being sent and never read. Mark the Lyrics button when
  // there is nothing to show, so picking it is not a silent no-op.
  const lyricsBtn = document.getElementById('mode-lyrics');
  if (lyricsBtn) {
    const none = s.is_playing && !s.has_lyrics && !s.is_instrumental;
    lyricsBtn.textContent = none ? 'Lyrics (none)' : 'Lyrics';
    lyricsBtn.style.opacity = none ? '0.55' : '1';
  }

  // Refetch lyrics when the track changes, otherwise an open drawer keeps
  // showing the previous song's words against the new song's timestamps.
  if (s.art_key !== lastArtKey) {
    lastArtKey = s.art_key;
    if (lyricsOpen) fetchLyricsData();
  }
}

/* ── Progress bar ───────────────────────────────────────────────── */
/* progress_ms is only as fresh as the last Spotify poll, so add its reported
   age plus our own round-trip rather than treating it as current. */
function updateProgress(s) {
  const card = document.getElementById('progressCard');
  if (!card) return;
  if (!s.duration_ms) { card.style.display = 'none'; return; }
  card.style.display = 'block';

  let elapsed = s.progress_ms + (s.progress_age_ms || 0);
  if (s.is_playing) elapsed += (Date.now() - (window.lastStateFetchTime || Date.now()));
  elapsed = Math.max(0, Math.min(s.duration_ms, elapsed));

  document.getElementById('progFill').style.width =
    (elapsed / s.duration_ms * 100).toFixed(2) + '%';
  document.getElementById('progElapsed').textContent = fmtTime(elapsed);
  document.getElementById('progTotal').textContent = fmtTime(s.duration_ms);
}

/* ── Live matrix preview ────────────────────────────────────────── */
/* Reloaded on a timer with a cache-busting query. A 64x64 PNG is 1-3 KB, so
   2 FPS over the LAN is nothing; it pauses when the tab is hidden. */
let previewTimer = null;

function refreshPreview() {
  const img = document.getElementById('matrixPreview');
  const on = document.getElementById('previewOn');
  if (!img || !on || !on.checked || document.hidden) return;
  img.src = '/api/frame.png?t=' + Date.now();
}

function togglePreview() {
  const on = document.getElementById('previewOn');
  const img = document.getElementById('matrixPreview');
  if (on && on.checked) {
    refreshPreview();
  } else if (img) {
    img.removeAttribute('src');
  }
}

/* ── Transport ──────────────────────────────────────────────────── */
async function playback(action) {
  try {
    const res = await apiFetch('/api/playback', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({action: action})
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      showBanner(err.error || 'Playback command failed.', 'warn');
    }
    setTimeout(fetchState, 350);
  } catch(e) {
    showBanner('Playback command did not reach the matrix.', 'warn');
  }
}

function togglePlay() {
  playback(currentState.is_playing ? 'pause' : 'play');
}

async function toggleSleep() {
  const next = !currentState.sleeping;
  try {
    await apiFetch('/api/sleep', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({value: next})
    });
    setTimeout(fetchState, 150);
  } catch(e) {}
}

async function setMode(m) {
  try {
    await apiFetch('/api/mode', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({mode: m})
    });
    setTimeout(fetchState, 100);
  } catch(e) {}
}

async function setSetting(key, value) {
  try {
    await apiFetch('/api/' + key, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({value: value})
    });
    setTimeout(fetchState, 100);
  } catch(e) {}
}

async function setAccentColor(name) {
  try {
    await apiFetch('/api/accent-color', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({value: name})
    });
    setTimeout(fetchState, 100);
  } catch(e) {}
}

async function resetAll() {
  if (!confirm('Reset all settings to defaults?')) return;
  try {
    await apiFetch('/api/reset', { method: 'POST' });
    setTimeout(fetchState, 300);
  } catch(e) {}
}

/* Custom Slate Logic */
const slateInput = document.getElementById('slateUpload');
const slateCanvas = document.getElementById('slateCanvas');
const slateCtx = slateCanvas.getContext('2d', { willReadFrequently: true });
let customImageBase64 = null;

slateInput.addEventListener('change', function(e) {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = function(event) {
    customImageBase64 = event.target.result;
    const img = new Image();
    img.onload = function() {
      slateCtx.clearRect(0,0,64,64);
      let w = img.width;
      let h = img.height;
      if (w > h) { h = Math.round(64 * (h/w)); w = 64; } else { w = Math.round(64 * (w/h)); h = 64; }
      slateCtx.drawImage(img, (64-w)/2, (64-h)/2, w, h);
    };
    img.src = customImageBase64;
  };
  reader.readAsDataURL(file);
});

function addSlateText() {
  const text = document.getElementById('slateText').value;
  const color = document.getElementById('slateColor').value;
  if (!text) return;
  slateCtx.fillStyle = color;
  slateCtx.font = "10px sans-serif";
  slateCtx.textAlign = "center";
  slateCtx.textBaseline = "middle";
  slateCtx.fillText(text, 32, 32);
  customImageBase64 = slateCanvas.toDataURL("image/png");
}

function clearSlate() {
  slateCtx.clearRect(0, 0, 64, 64);
  customImageBase64 = null;
}

async function sendCustomSlate() {
  if (!customImageBase64) return;
  try {
    const res = await fetch('/api/custom-media', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ image_base64: customImageBase64 })
    });
    if (res.ok) setMode('custom');
  } catch(e) {}
}

// Live Lyrics
function toggleLiveLyrics() {
  lyricsOpen = !lyricsOpen;
  const c = document.getElementById('liveLyricsCard');
  c.style.display = lyricsOpen ? 'block' : 'none';
  if (lyricsOpen) {
    fetchLyricsData();
    lyricsInterval = setInterval(updateLiveLyricsScroll, 500);
  } else {
    clearInterval(lyricsInterval);
  }
}

async function fetchLyricsData() {
  try {
    const res = await fetch('/api/lyrics');
    const data = await res.json();
    lyricsData = data.lyrics;
    renderLyricsHTML();
  } catch(e) {}
}

function renderLyricsHTML() {
  const box = document.getElementById('liveLyricsBox');
  if (!lyricsData || lyricsData.length === 0) {
    box.innerHTML = '<div style="margin-top:80px;font-style:italic;">No synced lyrics available.</div>';
    return;
  }
  let html = '<div style="height:80px;"></div>';
  lyricsData.forEach((line, i) => {
    const text = line[1] ? escapeHtml(line[1]) : '· · ·';
    html += `<div id="line-${i}" style="transition:all 0.3s; padding:4px 0;">${text}</div>`;
  });
  html += '<div style="height:100px;"></div>';
  box.innerHTML = html;
}

function updateLiveLyricsScroll() {
  if (!lyricsData || !currentState.is_playing) return;
  /* progress_ms is only as fresh as the last Spotify poll, so compensating for
     the HTTP round-trip alone left the phone up to 5s behind the matrix.
     progress_age_ms closes that gap; the lead offset keeps both in step. */
  const currentMs = currentState.progress_ms
    + (currentState.progress_age_ms || 0)
    + (Date.now() - window.lastStateFetchTime)
    + (currentState.lyrics_lead_ms || 0);
  let activeIdx = -1;
  for (let i = lyricsData.length - 1; i >= 0; i--) {
    if (currentMs >= lyricsData[i][0]) {
      activeIdx = i;
      break;
    }
  }
  const box = document.getElementById('liveLyricsBox');
  for (let i=0; i<lyricsData.length; i++) {
    const el = document.getElementById('line-'+i);
    if (!el) continue;
    if (i === activeIdx) {
      el.style.color = 'var(--text)';
      el.style.fontWeight = '600';
      el.style.fontSize = '16px';
    } else {
      el.style.color = 'var(--text-dim)';
      el.style.fontWeight = '400';
      el.style.fontSize = '14px';
    }
  }
  if (activeIdx !== -1) {
    const activeEl = document.getElementById('line-'+activeIdx);
    if (activeEl) {
      box.scrollTop = activeEl.offsetTop - box.offsetTop - 80;
    }
  }
}

// Fetch loop
window.lastStateFetchTime = Date.now();
fetchState();
setInterval(() => {
  window.lastStateFetchTime = Date.now();
  fetchState();
}, 2000);

// Advance the progress bar between state fetches so it moves smoothly rather
// than stepping once every two seconds.
setInterval(() => {
  if (currentState && currentState.is_playing) updateProgress(currentState);
}, 500);

// Live preview, paused while the tab is hidden so a backgrounded phone is not
// pulling frames it cannot show.
previewTimer = setInterval(refreshPreview, 500);
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) { refreshPreview(); fetchState(); }
});
refreshPreview();
</script>
</body>
</html>
"""


# ═══════════════════════════════════════════════════════════════════
#  WEB LOGS PAGE — HTML
# ═══════════════════════════════════════════════════════════════════

LOGS_PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SpotifyMatrix Logs</title>
<style>
  /* Self-contained for the same reason as the control panel. */

  * { margin: 0; padding: 0; box-sizing: border-box; }

  body {
    font-family: ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas,
                 'Liberation Mono', monospace;
    background: #0a0a0a;
    color: #a1a1aa;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
  }

  .toolbar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 12px 16px;
    background: #141414;
    border-bottom: 1px solid #1e1e1e;
    position: sticky;
    top: 0;
    z-index: 10;
  }
  .toolbar a {
    color: #1ed760;
    text-decoration: none;
    font-size: 12px;
    font-weight: 500;
  }
  .toolbar .title {
    font-size: 13px;
    font-weight: 600;
    color: #e4e4e7;
  }
  .toolbar button {
    background: #1e1e1e;
    border: 1px solid #333;
    color: #a1a1aa;
    padding: 6px 12px;
    border-radius: 6px;
    font-family: inherit;
    font-size: 11px;
    cursor: pointer;
  }
  .toolbar button:hover { background: #2a2a2a; color: #e4e4e7; }

  .log-container {
    flex: 1;
    padding: 8px 12px;
    overflow-y: auto;
    font-size: 11px;
    line-height: 1.6;
  }

  .log-line { white-space: pre-wrap; word-break: break-all; }
  .log-line .ts { color: #555; }
  .log-line.info .msg { color: #a1a1aa; }
  .log-line.warn .msg { color: #f59e0b; }
  .log-line.error .msg { color: #ef4444; }
</style>
</head>
<body>
<div class="toolbar">
  <a href="/">&larr; Control Panel</a>
  <span class="title">Logs</span>
  <button onclick="clearLogs()">Clear</button>
</div>
<div class="log-container" id="logContainer"></div>
<script>
const container = document.getElementById('logContainer');
let autoScroll = true;

container.addEventListener('scroll', () => {
  const atBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 40;
  autoScroll = atBottom;
});

async function fetchLogs() {
  try {
    const res = await fetch('/api/logs');
    if (!res.ok) return;
    const logs = await res.json();
    container.innerHTML = logs.map(l =>
      '<div class="log-line ' + l.level + '">' +
      '<span class="ts">[' + l.time + ']</span> ' +
      '<span class="msg">' + escapeHtml(l.msg) + '</span></div>'
    ).join('');
    if (autoScroll) container.scrollTop = container.scrollHeight;
  } catch(e) {}
}

function escapeHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

async function clearLogs() {
  try { await fetch('/api/logs/clear', { method: 'POST' }); } catch(e) {}
  container.innerHTML = '';
}

fetchLogs();
setInterval(fetchLogs, 2000);
</script>
</body>
</html>"""


# ═══════════════════════════════════════════════════════════════════
#  WEB CONTROL PANEL — SERVER
# ═══════════════════════════════════════════════════════════════════

def start_control_server(
    port: int,
    state: SharedPlaybackState,
    lock: threading.Lock,
    display: MatrixDisplay | MockDisplay,
    args: argparse.Namespace,
    spotify: SpotifyClient | None = None,
) -> HTTPServer | None:
    if port <= 0:
        return None

    outer_state = state
    outer_lock = lock
    outer_display = display
    outer_args = args
    outer_spotify = spotify

    class ControlPanelHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)

            if parsed.path == "/" or parsed.path == "":
                self._send_html(CONTROL_PANEL_HTML)
            elif parsed.path == "/logs":
                self._send_html(LOGS_PAGE_HTML)
            elif parsed.path == "/api/state":
                self._send_state()
            elif parsed.path == "/api/logs":
                self._send_json(_log_buffer.get_all())
            elif parsed.path == "/api/frame.png":
                self._send_frame_png()
            elif parsed.path == "/api/lyrics":
                with outer_lock:
                    data = {
                        "lyrics": outer_state.lyrics,
                        "progress_ms": outer_state.progress_ms,
                        "duration_ms": outer_state.duration_ms,
                        "is_playing": outer_state.is_playing,
                        "is_instrumental": outer_state.is_instrumental,
                        "server_time": time.time(),
                        "lyrics_lead_ms": outer_state.lyrics_lead_ms,
                    }
                self._send_json(data)
            elif parsed.path == "/mode":
                params = urllib.parse.parse_qs(parsed.query)
                mode = params.get("set", [""])[0]
                # Same vocabulary as POST /api/mode — these used to disagree,
                # so a mode you could set from the panel 400'd from a URL.
                if mode in DISPLAY_MODES:
                    with outer_lock:
                        outer_state.display_mode = mode
                        outer_state.sleeping = False
                    mark_settings_dirty()
                    log(f"Mode changed to '{mode}' via URL")
                    self._send_json({"ok": True, "mode": mode})
                else:
                    self._send_json(
                        {"error": f"Invalid mode. Use: {', '.join(DISPLAY_MODES)}"}, 400
                    )
            else:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not Found")

        def do_POST(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            self._body_too_large = False
            body = self._read_body()

            if self._body_too_large:
                self._send_json({"error": "Request body too large"}, 413)
                return

            if parsed.path == "/api/mode":
                mode = body.get("mode", "")
                if mode in DISPLAY_MODES:
                    with outer_lock:
                        outer_state.display_mode = mode
                        # Picking a mode is an explicit "show me something",
                        # so it also wakes a sleeping panel.
                        outer_state.sleeping = False
                    log(f"Mode changed to '{mode}' via web panel")
                    self._send_json({"ok": True, "mode": mode})
                else:
                    self._send_json(
                        {"error": f"Invalid mode. Use: {', '.join(DISPLAY_MODES)}"}, 400
                    )

            elif parsed.path == "/api/brightness":
                val = self._num(body, outer_state._default_brightness, 1, 100)
                if val is None:
                    self._send_json({"error": "brightness must be a number 1-100"}, 400)
                    return
                with outer_lock:
                    outer_state.brightness = val
                # Brightness is applied by the render loop so it can ease; setting
                # it here too would defeat the ramp.
                log(f"Brightness set to {val}")
                self._send_json({"ok": True, "brightness": val})

            elif parsed.path == "/api/spin-speed":
                val = self._num(body, outer_state._default_spin_speed, 1.0, 120.0, float)
                if val is None:
                    self._send_json({"error": "spin-speed must be a number 1-120"}, 400)
                    return
                with outer_lock:
                    outer_state.spin_speed = val
                self._send_json({"ok": True, "spin_speed": val})

            elif parsed.path == "/api/text-speed":
                val = self._num(body, outer_state._default_text_scroll_speed, 1.0, 100.0, float)
                if val is None:
                    self._send_json({"error": "text-speed must be a number 1-100"}, 400)
                    return
                with outer_lock:
                    outer_state.text_scroll_speed = val
                self._send_json({"ok": True, "text_scroll_speed": val})

            elif parsed.path == "/api/lyrics-style":
                style = body.get("value", "scroll")
                if style in ("scroll", "pop", "karaoke"):
                    with outer_lock:
                        outer_state.lyrics_style = style
                    self._send_json({"ok": True, "lyrics_style": style})
                else:
                    self._send_json({"error": "Invalid style"}, 400)

            elif parsed.path == "/api/smart-scroll":
                val = bool(body.get("value", True))
                with outer_lock:
                    outer_state.smart_scroll = val
                self._send_json({"ok": True, "smart_scroll": val})

            elif parsed.path == "/api/idle-mode":
                val = body.get("value", "clock")
                if val in IDLE_MODES:
                    with outer_lock:
                        outer_state.idle_mode = val
                    log(f"Idle screen set to '{val}'")
                    self._send_json({"ok": True, "idle_mode": val})
                else:
                    self._send_json(
                        {"error": f"Invalid idle mode. Use: {', '.join(IDLE_MODES)}"}, 400
                    )

            elif parsed.path == "/api/cd-duration":
                val = self._num(body, 10.0, 2.0, 120.0, float)
                if val is None:
                    self._send_json({"error": "cd-duration must be a number 2-120"}, 400)
                    return
                with outer_lock:
                    outer_state.cd_duration = val
                self._send_json({"ok": True, "cd_duration": val})

            elif parsed.path == "/api/progress-ring":
                val = bool(body.get("value", True))
                with outer_lock:
                    outer_state.progress_ring = val
                self._send_json({"ok": True, "progress_ring": val})

            elif parsed.path == "/api/art-pan":
                val = bool(body.get("value", True))
                with outer_lock:
                    outer_state.art_pan = val
                self._send_json({"ok": True, "art_pan": val})

            elif parsed.path in ("/api/line-width", "/api/border-width"):
                val = self._num(body, 1, 1, 5, int, "value")
                if val is None:
                    self._send_json({"error": "line-width must be an integer 1-5"}, 400)
                    return
                with outer_lock:
                    outer_state.line_width = val
                log(f"Line width set to {val}px")
                self._send_json({"ok": True, "line_width": val})

            elif parsed.path == "/api/sleep":
                val = bool(body.get("value", False))
                with outer_lock:
                    outer_state.sleeping = val
                self._send_json({"ok": True, "sleeping": val})

            elif parsed.path == "/api/scroll-font-size":
                val = self._num(body, outer_state._default_scroll_font_size, 6, 14)
                if val is None:
                    self._send_json({"error": "font size must be a number 6-14"}, 400)
                    return
                with outer_lock:
                    outer_state.scroll_font_size = val
                self._send_json({"ok": True, "scroll_font_size": val})

            elif parsed.path == "/api/pop-font-size":
                val = self._num(body, outer_state._default_pop_font_size, 6, 14)
                if val is None:
                    self._send_json({"error": "font size must be a number 6-14"}, 400)
                    return
                with outer_lock:
                    outer_state.pop_font_size = val
                self._send_json({"ok": True, "pop_font_size": val})

            elif parsed.path == "/api/reset":
                with outer_lock:
                    outer_state.display_mode = "default"
                    outer_state.lyrics_style = outer_state._default_lyrics_style
                    outer_state.smart_scroll = True
                    outer_state.scroll_font_size = outer_state._default_scroll_font_size
                    outer_state.pop_font_size = outer_state._default_pop_font_size
                    outer_state.brightness = outer_state._default_brightness
                    outer_state.spin_speed = outer_state._default_spin_speed
                    outer_state.text_scroll_speed = outer_state._default_text_scroll_speed
                    outer_state.accent_name = "spotify"
                    outer_state.accent_color = COLOR_THEMES["spotify"]
                    outer_state.contrast_accent_color = (0, 220, 220)
                    outer_state.line_width = outer_state._default_line_width
                    outer_state.lyrics_lead_ms = 180
                    outer_state.idle_mode = "clock"
                    outer_state.cd_duration = 10.0
                    outer_state.progress_ring = True
                    outer_state.art_pan = False
                    outer_state.sleeping = False
                try:
                    outer_display.set_brightness(outer_state._default_brightness)
                except Exception:
                    pass
                log("Settings reset to defaults")
                self._send_json({"ok": True})

            elif parsed.path == "/api/accent-color":
                val = body.get("value", "spotify")
                if val == "custom":
                    r = self._num(body, 255, 0, 255, int, "r")
                    g = self._num(body, 255, 0, 255, int, "g")
                    b = self._num(body, 255, 0, 255, int, "b")
                    if r is None or g is None or b is None:
                        self._send_json({"error": "r/g/b must be numbers 0-255"}, 400)
                        return
                    with outer_lock:
                        outer_state.accent_name = "custom"
                        outer_state.accent_color = (r, g, b)
                    log(f"Accent color set to custom ({r},{g},{b})")
                    self._send_json({"ok": True, "accent_name": "custom"})
                elif val == "auto":
                    # Follow the album art. Resolve immediately from whatever is
                    # playing so the change is visible now rather than at the
                    # next track.
                    with outer_lock:
                        outer_state.accent_name = "auto"
                        if outer_state.image is not None:
                            outer_state.accent_color = extract_accent_color(
                                outer_state.image, outer_state.art_key
                            )
                    log("Accent color following album art")
                    self._send_json({"ok": True, "accent_name": "auto"})
                elif val == "contrast":
                    # Contrast against album art.
                    with outer_lock:
                        outer_state.accent_name = "contrast"
                        if outer_state.image is not None:
                            outer_state.contrast_accent_color = extract_contrast_accent_color(
                                outer_state.image, outer_state.art_key
                            )
                        outer_state.accent_color = outer_state.contrast_accent_color
                    log("Accent color set to contrast against album art")
                    self._send_json({"ok": True, "accent_name": "contrast"})
                elif val in COLOR_THEMES:
                    with outer_lock:
                        outer_state.accent_name = val
                        outer_state.accent_color = COLOR_THEMES[val]
                    self._send_json({"ok": True, "accent_name": val})
                else:
                    self._send_json({"error": "Invalid theme"}, 400)

            elif parsed.path == "/api/lyrics-lead":
                val = self._num(body, 180, 0, 500)
                if val is None:
                    self._send_json({"error": "lyrics-lead must be a number 0-500"}, 400)
                    return
                with outer_lock:
                    outer_state.lyrics_lead_ms = val
                self._send_json({"ok": True, "lyrics_lead_ms": val})

            elif parsed.path == "/api/playback":
                if outer_spotify is None or not outer_args.enable_playback_control:
                    self._send_json({"error": "Playback control is disabled"}, 403)
                    return
                action = body.get("action", "")
                try:
                    ok, message = outer_spotify.playback_command(action)
                except Exception as exc:
                    ok, message = False, str(exc)
                if ok:
                    log(f"Playback: {message}")
                    self._send_json({"ok": True, "action": action})
                else:
                    log(f"Playback: {message}", "warn")
                    self._send_json({"error": message}, 400)

            elif parsed.path == "/api/logs/clear":
                _log_buffer.clear()
                self._send_json({"ok": True})

            elif parsed.path == "/api/custom-media":
                try:
                    img_data = body.get("image_base64", "")
                    if img_data.startswith("data:image"):
                        img_data = img_data.split(",")[1]
                    decoded = base64.b64decode(img_data)
                    img = Image.open(BytesIO(decoded))
                    frames = []
                    delay = 0.1
                    if getattr(img, "is_animated", False):
                        # Cap frame count: an unbounded GIF means N LANCZOS
                        # resizes plus N retained frames, all on the Pi's RAM.
                        total = img.n_frames
                        kept = min(total, MAX_SLATE_FRAMES)
                        if kept < total:
                            log(
                                f"Custom slate: GIF has {total} frames, "
                                f"keeping the first {kept}.",
                                "warn",
                            )
                        for frame_idx in range(kept):
                            img.seek(frame_idx)
                            frame_rgb = Image.new("RGB", img.size)
                            frame_rgb.paste(img)
                            frames.append(frame_rgb.resize((64, 64), Image.Resampling.LANCZOS))
                        delay = img.info.get("duration", 100) / 1000.0
                        if delay <= 0.01:
                            delay = 0.1
                    else:
                        frames.append(img.convert("RGB").resize((64, 64), Image.Resampling.LANCZOS))
                    with outer_lock:
                        outer_state.custom_slate_frames = frames
                        outer_state.custom_slate_frame_delay = delay
                        outer_state.display_mode = "custom"
                    self._send_json({"ok": True})
                except Exception as e:
                    self._send_json({"error": str(e)}, 400)
            else:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not Found")
                return

            # One place to record "a setting changed", rather than a call in
            # every branch. Validation failures return early and never reach it,
            # so a rejected request cannot dirty the saved settings.
            if parsed.path in PERSISTING_ENDPOINTS:
                mark_settings_dirty()

        def _read_body(self) -> dict:
            """Read and parse a JSON request body, refusing oversized payloads.

            Reading Content-Length bytes unconditionally lets anything on the LAN
            exhaust the Pi's memory with a single POST.
            """
            try:
                length = int(self.headers.get("Content-Length", 0))
            except (TypeError, ValueError):
                return {}
            if length <= 0:
                return {}
            if length > MAX_REQUEST_BYTES:
                # Drain in fixed-size chunks before answering. Replying without
                # consuming the body resets the connection mid-send, so the
                # client never gets to read the 413 — it just sees a dropped
                # socket. Discarding as we go keeps memory flat regardless of
                # how much was announced.
                self._body_too_large = True
                remaining = min(length, MAX_DRAIN_BYTES)
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                return {}
            try:
                raw = self.rfile.read(length)
                parsed = json.loads(raw.decode("utf-8"))
            except Exception:
                return {}
            return parsed if isinstance(parsed, dict) else {}

        def _num(
            self,
            body: dict,
            default: float,
            lo: float,
            hi: float,
            cast: Any = int,
            key: str = "value",
        ) -> Any:
            """Coerce and clamp a numeric field, or None if it is not a number.

            Without this, a non-numeric value raises inside the handler and the
            client just sees a dropped connection.
            """
            raw = body.get(key, default)
            # OverflowError is not a ValueError: JSON parses 1e400 as float
            # inf, and int(inf) raises OverflowError, which escaped this
            # handler mid-response and dropped the client's connection.
            try:
                value = cast(raw)
            except (TypeError, ValueError, OverflowError):
                return None
            if value != value or value in (float("inf"), float("-inf")):
                return None
            return max(lo, min(hi, value))

        def _send_html(self, html: str) -> None:
            payload = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(payload)

        def _send_json(self, data: Any, status: int = 200) -> None:
            payload = json.dumps(data).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(payload)

        def _send_frame_png(self) -> None:
            """Serve the last frame the matrix was given, as a PNG.

            Lets the panel show exactly what the LEDs show, which makes every
            other setting tunable without standing over the device. Read
            without the lock on purpose: the attribute holds an immutable
            finished frame and rebinding it is atomic, so a reader gets either
            the previous frame or the next one — never a half-drawn image.
            """
            frame = outer_state.last_frame
            if frame is None:
                self._send_json({"error": "no frame rendered yet"}, 404)
                return
            buffer = BytesIO()
            frame.save(buffer, format="PNG", optimize=False, compress_level=1)
            payload = buffer.getvalue()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def _send_state(self) -> None:
            with outer_lock:
                # progress_ms is only as fresh as the last Spotify poll (up to
                # 5s while playing, 30s when idle). Send its age so the client
                # can extrapolate properly instead of assuming it is current.
                fetch_time = outer_state.fetch_time
                progress_age_ms = (
                    int((time.monotonic() - fetch_time) * 1000) if fetch_time > 0 else 0
                )
                data = {
                    "art_key": outer_state.art_key,
                    "is_instrumental": outer_state.is_instrumental,
                    "progress_age_ms": progress_age_ms,
                    "display_mode": outer_state.display_mode,
                    "effective_mode": outer_state.effective_mode,
                    "brightness": outer_state.brightness,
                    "spin_speed": outer_state.spin_speed,
                    "text_scroll_speed": outer_state.text_scroll_speed,
                    "title": outer_state.title,
                    "artist": outer_state.artist,
                    "album_name": outer_state.album_name,
                    "is_playing": outer_state.is_playing,
                    "is_connected": outer_state.is_connected,
                    "image_url": outer_state.image_url,
                    "lyrics_style": outer_state.lyrics_style,
                    "smart_scroll": outer_state.smart_scroll,
                    "scroll_font_size": outer_state.scroll_font_size,
                    "pop_font_size": outer_state.pop_font_size,
                    "has_lyrics": outer_state.lyrics is not None and len(outer_state.lyrics or []) > 0,
                    "progress_ms": outer_state.progress_ms,
                    "duration_ms": outer_state.duration_ms,
                    "accent_name": outer_state.accent_name,
                    "accent_rgb": list(outer_state.accent_color),
                    "contrast_accent_rgb": list(outer_state.contrast_accent_color),
                    "lyrics_lead_ms": outer_state.lyrics_lead_ms,
                    "idle_mode": outer_state.idle_mode,
                    "cd_duration": outer_state.cd_duration,
                    "progress_ring": outer_state.progress_ring,
                    "art_pan": outer_state.art_pan,
                    "line_width": outer_state.line_width,
                    "sleeping": outer_state.sleeping,
                    "status_message": outer_state.status_message,
                    "status_detail": outer_state.status_detail,
                    "queue_next": outer_state.queue_next,
                    "can_control": outer_args.enable_playback_control,
                }
            self._send_json(data)

        def log_message(self, format: str, *args: Any) -> None:
            return

    try:
        # Threaded: the single-threaded HTTPServer serialized every request, so a
        # slate upload or a stalled phone would block the whole panel.
        server = ThreadingHTTPServer(("0.0.0.0", port), ControlPanelHandler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        log(f"Web Control Panel: http://0.0.0.0:{port}/")
        return server
    except OSError as exc:
        log(f"Web Control Panel: Failed to start on port {port}: {exc}", "error")
        return None


# ═══════════════════════════════════════════════════════════════════
#  SPOTIFY POLLING THREAD
# ═══════════════════════════════════════════════════════════════════

def _classify_poll_failure(exc: Exception) -> tuple[str, str]:
    """Turn a poll exception into (headline, detail) for the status screen.

    The detail string doubles as a tag: anything containing "auth" is treated
    as permanent and shown immediately rather than after repeated failures.
    """
    text = str(exc).lower()

    if isinstance(exc, HTTPError) and exc.code in (401, 403):
        return "Spotify auth needed", "run --auth-only"
    if isinstance(exc, (socket.gaierror, socket.timeout, TimeoutError)):
        return "No Wi-Fi", "check network"
    if isinstance(exc, URLError):
        return "No Wi-Fi", "check network"
    if "re-run with --auth-only" in text or "invalid_grant" in text or "refresh" in text:
        return "Spotify auth needed", "run --auth-only"
    if any(token in text for token in ("name or service", "unreachable", "timed out", "connection")):
        return "No Wi-Fi", "check network"
    return "Spotify error", "see /logs"


def poll_spotify(
    spotify: SpotifyClient,
    state: SharedPlaybackState,
    state_lock: threading.Lock,
    stop_event: threading.Event,
    args: argparse.Namespace,
) -> None:
    first_poll = True
    log("Spotify: Background polling thread started.")

    active_seconds = POLL_ACTIVE_SECONDS
    idle_seconds = POLL_IDLE_SECONDS
    last_playing_time = time.time()
    backoff_multiplier = 1
    last_track_key: str | None = None
    last_poll_progress = 0
    last_poll_mono = 0.0
    progress_offset = 0.0
    consecutive_failures = 0
    # One entry, keyed by track: "have we already looked up what follows this".
    queue_fetched_for: dict[str, bool] = {}

    while not stop_event.is_set():
        try:
            current_wait = active_seconds

            if first_poll:
                log("Spotify: Making initial API connection...")
                first_poll = False

            playback = spotify.get_currently_playing()
            art = playback_art_from_response(playback)

            fetch_time = time.monotonic()

            backoff_multiplier = 1
            consecutive_failures = 0
            with state_lock:
                state.is_connected = True
                state.status_message = ""
                state.status_detail = ""

            if art and art.is_playing:
                last_playing_time = time.time()
                current_wait = active_seconds

                remaining_ms = art.duration_ms - art.progress_ms
                if remaining_ms < 10000:
                    current_wait = max(1.5, remaining_ms / 5000.0)
                    log(f"Spotify: Near track end. Accelerated poll rate: {current_wait:.1f}s", verbose=True)
                    # Piggyback the queue lookup on the poll we are already
                    # accelerating, so "up next" costs one extra request per
                    # track rather than one per poll.
                    if args.enable_queue_peek and not queue_fetched_for.get(art.key):
                        queue_fetched_for.clear()
                        queue_fetched_for[art.key] = True
                        try:
                            queue = spotify.get_queue()
                        except Exception as exc:
                            queue = []
                            log(f"Queue peek unavailable: {exc}", verbose=True)
                        upcoming = ""
                        if queue:
                            nxt = queue[0] or {}
                            name = nxt.get("name") or ""
                            artists = nxt.get("artists") or []
                            who = artists[0].get("name", "") if artists else ""
                            upcoming = f"{name} · {who}".strip(" ·") if name else ""
                        with state_lock:
                            state.queue_next = upcoming
                elif art.progress_ms < 30000 and art.duration_ms > 120000:
                    current_wait = min(10.0, active_seconds * 1.5)
                    log(f"Spotify: Track just started. Backed off poll rate: {current_wait:.1f}s", verbose=True)

            time_since_played = time.time() - last_playing_time
            if time_since_played > 60.0:
                current_wait = idle_seconds

            if art:
                # Measure how far our extrapolation drifted from what Spotify
                # actually reports, and fold it into a smoothed offset. A large
                # error means a seek or a track change, not drift — reset there.
                if (
                    art.is_playing
                    and art.key == last_track_key
                    and last_poll_mono > 0.0
                ):
                    predicted = last_poll_progress + (fetch_time - last_poll_mono) * 1000.0
                    error = art.progress_ms - predicted
                    if abs(error) < 2000:
                        progress_offset = 0.85 * progress_offset + 0.15 * error
                    else:
                        progress_offset = 0.0
                    with state_lock:
                        state.progress_offset_ms = max(-1000.0, min(1000.0, progress_offset))
                last_poll_progress = art.progress_ms
                last_poll_mono = fetch_time

                with state_lock:
                    needs_download = art.key != state.art_key or art.image_url != state.image_url
                    is_new_track = art.key != last_track_key

                image = (
                    download_image(
                        art.image_url,
                        saturation=args.art_saturation,
                        contrast=args.art_contrast,
                    )
                    if needs_download
                    else None
                )

                with state_lock:
                    state.art_key = art.key
                    state.image_url = art.image_url
                    state.is_playing = art.is_playing
                    state.title = art.title
                    state.artist = art.artist
                    state.album_name = art.album_name
                    state.progress_ms = art.progress_ms
                    state.duration_ms = art.duration_ms
                    state.fetch_time = fetch_time
                    if image is not None:
                        state.image = image
                    if state.image is not None:
                        state.contrast_accent_color = extract_contrast_accent_color(
                            state.image, state.art_key
                        )
                        if state.accent_name == "auto":
                            state.accent_color = extract_accent_color(
                                state.image, state.art_key
                            )
                        elif state.accent_name == "contrast":
                            state.accent_color = state.contrast_accent_color

                if is_new_track and art.key:
                    last_track_key = art.key
                    log(f"Track: {art.title} — {art.artist}")
                    with state_lock:
                        state.lyrics = None
                        state.lyrics_words = []
                        state.lyrics_track_key = None
                        # Must reset too, or an instrumental followed by a vocal
                        # track shows the visualizer until LRCLIB answers.
                        state.is_instrumental = False
                        # Belongs to the track that just ended.
                        state.queue_next = ""
                    duration_s = max(1, art.duration_ms // 1000)
                    lyrics_thread = threading.Thread(
                        target=fetch_lyrics_async,
                        args=(art.artist, art.title, art.album_name, duration_s,
                              state, state_lock, art.key, args.lyrics_cache),
                        daemon=True,
                    )
                    lyrics_thread.start()

                status = f"is_playing={art.is_playing}, title={art.title!r}"
            else:
                with state_lock:
                    state.art_key = None
                    state.image_url = None
                    state.image = None
                    state.is_playing = False
                    state.title = ""
                    state.artist = ""
                    state.album_name = ""
                    state.progress_ms = 0
                    state.duration_ms = 0
                    state.fetch_time = 0.0
                last_track_key = None
                status = "no currently playing item"

            # Verbose logging (interactive only — skipped in auto/systemd)
            if current_wait == active_seconds:
                time_until_idle = max(0, int(60.0 - (time.time() - last_playing_time)))
                prefix = f"[Active | {time_until_idle}s to idle]"
            else:
                prefix = "[Idle]"

            if _is_interactive:
                for _ in range(int(current_wait)):
                    if stop_event.is_set():
                        break
                    if current_wait == active_seconds:
                        time_until_idle = max(0, int(60.0 - (time.time() - last_playing_time)))
                        prefix = f"[Active | {time_until_idle}s to idle]"
                    else:
                        prefix = "[Idle]"
                    log(f"Spotify: {prefix} {status}", verbose=True)
                    stop_event.wait(1.0)
            else:
                stop_event.wait(current_wait)

        except RateLimitException as exc:
            wait_time = exc.retry_after
            if wait_time <= 0:
                wait_time = active_seconds * backoff_multiplier
                log(f"Spotify API: Rate limited. Backoff: {wait_time}s", "warn")
                backoff_multiplier = min(backoff_multiplier * 2, 64)
            else:
                log(f"Spotify API: Rate limited. Retry after {wait_time}s", "warn")

            with state_lock:
                state.is_connected = False
            stop_event.wait(wait_time)

        except Exception as exc:
            log(f"Spotify poll failed: {exc}", "error")
            wait_time = active_seconds * backoff_multiplier
            log(f"Spotify API: Backoff: {wait_time}s", "warn")
            backoff_multiplier = min(backoff_multiplier * 2, 64)
            consecutive_failures += 1

            # Say what is wrong on the panel itself, but only once the failure
            # looks persistent — a single dropped request should not replace the
            # clock, which already shows a red pulse while disconnected.
            message, detail = _classify_poll_failure(exc)
            with state_lock:
                state.is_connected = False
                if message and (consecutive_failures >= 3 or "auth" in detail):
                    state.status_message = message
                    state.status_detail = detail
            stop_event.wait(wait_time)


# ═══════════════════════════════════════════════════════════════════
#  MAIN RUN LOOP
# ═══════════════════════════════════════════════════════════════════

def _install_signal_handlers() -> None:
    """Turn SIGTERM/SIGINT into KeyboardInterrupt so cleanup actually runs.

    systemd stops the service with SIGTERM, whose default action kills the
    process outright — the `finally` block that calls display.clear() never
    runs, so the panel stays frozen on its last frame after `systemctl stop`.
    """
    def _raise_interrupt(signum: int, frame: Any) -> None:
        raise KeyboardInterrupt

    for sig_name in ("SIGTERM", "SIGINT", "SIGHUP"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue  # SIGHUP does not exist on Windows
        try:
            signal.signal(sig, _raise_interrupt)
        except (OSError, ValueError):
            pass


# CLI flag → the SharedPlaybackState field it seeds. Used to let an explicit
# flag win over a value restored from settings.json.
_CLI_STATE_OVERRIDES: tuple[tuple[str, str], ...] = (
    ("--brightness", "brightness"),
    ("--rpm", "spin_speed"),
    ("--text-speed", "text_scroll_speed"),
    ("--lyrics-style", "lyrics_style"),
    ("--idle-mode", "idle_mode"),
    ("--cd-duration", "cd_duration"),
    ("--line-width", "line_width"),
)


def _explicit_cli_overrides(args: argparse.Namespace) -> list[tuple[str, Any]]:
    """Fields the user set on the command line this run, so they beat settings.

    argparse cannot tell a passed value from a default, so this checks argv
    directly — the flag being present at all is what makes it an override.
    """
    out: list[tuple[str, Any]] = []
    for flag, dest in _CLI_STATE_OVERRIDES:
        if any(arg == flag or arg.startswith(f"{flag}=") for arg in sys.argv[1:]):
            out.append((dest, getattr(args, flag.lstrip("-").replace("-", "_"))))
    return out


def run(args: argparse.Namespace) -> None:
    _install_signal_handlers()

    try:
        os.nice(-5)
        log("Process priority elevated (nice=-5)")
    except (OSError, PermissionError, AttributeError):
        log("Could not elevate process priority (not root, or Windows?)", "warn")

    set_pixel_font(args.lyrics_font)

    if args.preview_frames:
        render_preview_frames(args.preview_frames)
        return

    load_dotenv()

    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    redirect_uri = os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")

    missing = [
        name for name, value in (
            ("SPOTIFY_CLIENT_ID", client_id),
            ("SPOTIFY_CLIENT_SECRET", client_secret),
            ("SPOTIFY_REDIRECT_URI", redirect_uri),
        ) if not value
    ]
    if missing:
        raise SystemExit(f"Missing required environment values: {', '.join(missing)}")

    spotify = SpotifyClient(
        client_id=client_id or "",
        client_secret=client_secret or "",
        redirect_uri=redirect_uri,
        token_cache=args.token_cache,
        open_browser=not args.no_browser,
    )

    if args.auth_only:
        spotify.authorize()
        print(f"Spotify token cached at {args.token_cache}")
        return

    display: MatrixDisplay | MockDisplay
    if args.mock_output:
        log("Matrix: Initializing Mock Display...")
        display = MockDisplay(args.mock_output, gamma=args.gamma)
    else:
        log("Matrix: Initializing hardware RGB Matrix...")
        display = MatrixDisplay(args)

    log("=" * 48)
    log("  Spotify Matrix — Ready")
    log("=" * 48)
    log(f"  Display:    {args.cols}x{args.rows}  brightness={args.brightness}")
    log(f"  Hardware:   {args.hardware_mapping}  gpio-slowdown={args.gpio_slowdown}")
    log(f"  Animation:  {args.fps} FPS  {args.rpm} RPM  transition={args.transition}")
    log(f"  Polling:    5s active / 30s idle (dynamic)")
    if args.web_port > 0:
        log(f"  Web Panel:  http://0.0.0.0:{args.web_port}/")
    log("=" * 48)

    size_x = args.cols
    size_y = args.rows
    size = min(size_x, size_y)

    if args.test_pattern:
        try:
            offset = 0
            while True:
                display.show(render_test_pattern(size, offset))
                offset = (offset + 1) % size
                time.sleep(1.0 / args.fps)
        except KeyboardInterrupt:
            pass
        finally:
            display.clear()
        return

    playback_state = SharedPlaybackState(
        display_mode="default",
        spin_speed=args.rpm,
        text_scroll_speed=args.text_speed,
        brightness=args.brightness,
        lyrics_style=args.lyrics_style,
        line_width=getattr(args, "line_width", 1),
        _default_brightness=args.brightness,
        _default_spin_speed=args.rpm,
        _default_text_scroll_speed=args.text_speed,
        _default_lyrics_style=args.lyrics_style,
        _default_line_width=getattr(args, "line_width", 1),
    )
    # Saved settings load over the CLI defaults, but anything passed explicitly
    # on the command line wins — otherwise a stale settings file would silently
    # override a flag you just typed.
    playback_state.idle_mode = args.idle_mode
    playback_state.cd_duration = args.cd_duration
    if not args.no_settings:
        if apply_saved_settings(args.settings, playback_state):
            log(f"Settings: restored from {args.settings}")
        for dest, value in _explicit_cli_overrides(args):
            setattr(playback_state, dest, value)

    playback_lock = threading.Lock()
    stop_event = threading.Event()

    if not args.no_settings:
        threading.Thread(
            target=settings_saver,
            args=(args.settings, playback_state, playback_lock, stop_event),
            daemon=True,
        ).start()

    control_server = start_control_server(
        args.web_port, playback_state, playback_lock, display, args, spotify,
    )

    poll_thread = threading.Thread(
        target=poll_spotify,
        args=(spotify, playback_state, playback_lock, stop_event, args),
        daemon=True,
    )
    poll_thread.start()

    flash_until: float = 0.0
    flash_last_key: str | None = None

    def present(image: Image.Image) -> None:
        """Push a frame to the panel and keep it for the web preview.

        Also where the new-track flash is applied, so every mode gets it
        without each render path having to know about it.
        """
        if flash_until > 0.0:
            remaining = flash_until - time.monotonic()
            if remaining > 0.0:
                image = apply_track_flash(
                    image, remaining / TRACK_FLASH_SECONDS,
                    playback_state.accent_color,
                )
        display.show(image)
        playback_state.last_frame = image

    angle = 0.0
    scroll_x = 0.0
    last_frame = time.monotonic()
    idle_fps = min(args.fps, args.idle_fps)

    prev_art_key: str | None = None
    last_art_image: Image.Image | None = None
    last_art_key: str | None = None
    last_display_text: str = ""
    old_art_image: Image.Image | None = None
    old_art_key: str | None = None
    old_angle: float = 0.0
    old_scroll_x: float = 0.0
    old_display_text: str = ""
    old_is_idle: bool = False

    is_idle_state: bool = False
    idle_since: float | None = None
    current_transition_mode = args.transition

    transition_active: bool = False
    transition_start: float = 0.0

    SPIN_EASE_DURATION = 1.0
    current_rpm: float = 0.0
    was_playing: bool = False
    spin_transition_start: float = 0.0
    spin_from_rpm: float = 0.0

    last_brightness: int = args.brightness
    eased_brightness: float = float(args.brightness)

    # Default mode auto-cycle state
    default_cd_start: float = 0.0  # when the CD phase started
    default_last_track_key: str | None = None  # to detect new songs in default mode

    was_sleeping: bool = False
    idle_screen = IdleScreen()

    try:
        while True:
            frame_start = time.monotonic()
            with playback_lock:
                current_art_image = playback_state.image
                current_art_key = playback_state.art_key
                is_playing = playback_state.is_playing
                title = playback_state.title
                artist = playback_state.artist
                display_mode = playback_state.display_mode
                runtime_rpm = playback_state.spin_speed
                runtime_text_speed = playback_state.text_scroll_speed
                runtime_brightness = playback_state.brightness
                stored_progress_ms = playback_state.progress_ms
                stored_duration_ms = playback_state.duration_ms
                fetch_time = playback_state.fetch_time
                current_lyrics = playback_state.lyrics
                is_connected = playback_state.is_connected
                is_instrumental = playback_state.is_instrumental
                lyrics_lead_ms = playback_state.lyrics_lead_ms
                accent_color = playback_state.accent_color
                contrast_accent_color = playback_state.contrast_accent_color
                # These were previously read outside the lock at the render call
                # sites, inconsistently with every other field here.
                lyrics_style = playback_state.lyrics_style
                smart_scroll = playback_state.smart_scroll
                current_lyrics_words = playback_state.lyrics_words
                progress_offset_ms = playback_state.progress_offset_ms
                idle_mode = playback_state.idle_mode
                cd_duration = playback_state.cd_duration
                show_progress_ring = playback_state.progress_ring
                art_pan = playback_state.art_pan
                line_width = playback_state.line_width
                is_sleeping = playback_state.sleeping
                status_message = playback_state.status_message
                status_detail = playback_state.status_detail
                queue_next = playback_state.queue_next
                lyrics_font_size = (
                    playback_state.scroll_font_size
                    if playback_state.lyrics_style in ("scroll", "karaoke")
                    else playback_state.pop_font_size
                )

            now = time.monotonic()
            delta = now - last_frame
            last_frame = now

            # Live track position, extrapolated from the last poll so the ring
            # and bar advance smoothly instead of stepping once per poll.
            if stored_duration_ms > 0 and fetch_time > 0:
                elapsed_ms = (now - fetch_time) * 1000.0 if is_playing else 0.0
                track_progress: float | None = max(0.0, min(
                    1.0, (stored_progress_ms + elapsed_ms) / stored_duration_ms
                ))
            else:
                track_progress = None
            ring_progress = track_progress if show_progress_ring else None

            # Track change, detected once for every mode. The CD path has its
            # own transition bookkeeping further down; this is only the flash.
            if current_art_key != flash_last_key:
                if flash_last_key is not None and current_art_key is not None:
                    flash_until = now + TRACK_FLASH_SECONDS
                flash_last_key = current_art_key

            # Ease brightness toward the target instead of snapping. Also lets
            # scheduled dimming fade in rather than visibly stepping.
            if abs(eased_brightness - runtime_brightness) > 0.01:
                step = BRIGHTNESS_RAMP_PER_SEC * delta
                gap = runtime_brightness - eased_brightness
                eased_brightness += max(-step, min(step, gap))
                if abs(runtime_brightness - eased_brightness) < 0.5:
                    eased_brightness = float(runtime_brightness)
                applied = int(round(eased_brightness))
                if applied != last_brightness:
                    try:
                        display.set_brightness(applied)
                    except Exception:
                        pass
                    last_brightness = applied

            # ══════════════════════════════════════════════════
            #  MODE ROUTING
            # ══════════════════════════════════════════════════

            # --- OVERRIDE: panel asleep ---
            # Blank and idle down hard. Nothing below this needs to run, and the
            # point of the switch is that the device stops drawing power.
            if is_sleeping:
                if not was_sleeping:
                    display.clear()
                    # Keep the web preview honest: the panel is dark, so the
                    # preview must be too rather than serving the last frame
                    # from before the switch.
                    playback_state.last_frame = Image.new("RGB", (size_x, size_y), (0, 0, 0))
                    was_sleeping = True
                    log("Panel asleep")
                if args.once:
                    break
                time.sleep(0.25)
                continue
            if was_sleeping:
                was_sleeping = False
                log("Panel awake")

            # --- OVERRIDE: something is wrong and nothing is playing ---
            # Only when idle: a status screen must never interrupt a track that
            # is still rendering fine from cached state.
            if status_message and not is_playing and display_mode != "custom":
                frame = render_status(
                    size, status_message, status_detail, accent_color,
                    crisp=not args.no_crisp_text,
                )
                present(frame)
                if args.once:
                    break
                sleep_for = max(0.0, (1.0 / idle_fps) - (time.monotonic() - frame_start))
                time.sleep(sleep_for)
                continue

            # --- STICKY: Custom Slate mode ---
            if display_mode == "custom":
                with playback_lock:
                    playback_state.effective_mode = "custom"
                    frames = playback_state.custom_slate_frames
                    delay = playback_state.custom_slate_frame_delay
                frame = render_custom_slate(size, frames, delay)
                present(frame)
                if args.once:
                    break
                # A single still image does not need to be re-sent 20x/second.
                slate_fps = args.fps if len(frames) > 1 else args.static_fps
                sleep_for = max(0.0, (1.0 / slate_fps) - (time.monotonic() - frame_start))
                time.sleep(sleep_for)
                continue

            # --- STICKY: Idle screen (clock, or the chosen ambient visual) ---
            if display_mode == "clock":
                with playback_lock:
                    playback_state.effective_mode = idle_mode
                frame = idle_screen.render(
                    size, idle_mode, is_connected, accent_color, delta, now,
                )
                present(frame)
                if args.once:
                    break
                # The animations are motion, not a ticking dot — give them the
                # full frame budget rather than the clock's reduced one.
                pinned_fps = args.fps if idle_mode != "clock" else idle_fps
                sleep_for = max(0.0, (1.0 / pinned_fps) - (time.monotonic() - frame_start))
                time.sleep(sleep_for)
                continue

            # --- STICKY: Lyrics mode ---
            if display_mode == "lyrics":
                with playback_lock:
                    playback_state.effective_mode = "lyrics"
                frame = render_lyrics(
                    size, current_lyrics, stored_duration_ms,
                    is_playing, fetch_time, stored_progress_ms,
                    lyrics_style, smart_scroll, lyrics_font_size,
                    is_instrumental=is_instrumental,
                    lyrics_lead_ms=lyrics_lead_ms,
                    accent_color=accent_color,
                    track_key=current_art_key,
                    lyrics_words=current_lyrics_words,
                    crisp=not args.no_crisp_text,
                    progress_offset_ms=progress_offset_ms,
                )
                present(frame)
                if args.once:
                    break
                sleep_for = max(0.0, (1.0 / args.fps) - (time.monotonic() - frame_start))
                time.sleep(sleep_for)
                continue

            # --- STICKY: Full-bleed album art ---
            if display_mode == "art":
                with playback_lock:
                    playback_state.effective_mode = "art"
                if current_art_image is None:
                    frame = idle_screen.render(
                        size, idle_mode, is_connected, accent_color, delta, now,
                    )
                else:
                    frame = render_full_art(
                        current_art_image, size, current_art_key,
                        progress=track_progress,
                        accent_color=accent_color,
                        pan=art_pan and is_playing,
                        pan_phase=now,
                        progress_color=contrast_accent_color,
                        line_width=line_width,
                    )
                present(frame)
                if args.once:
                    break
                # A still cover needs no frame budget; a panning one does.
                art_fps = args.fps if (art_pan and is_playing) else args.static_fps
                sleep_for = max(0.0, (1.0 / art_fps) - (time.monotonic() - frame_start))
                time.sleep(sleep_for)
                continue

            # --- DEFAULT mode (auto-cycling) ---
            if display_mode == "default":
                # Detect new track → reset CD timer
                if current_art_key != default_last_track_key and current_art_key is not None:
                    default_last_track_key = current_art_key
                    default_cd_start = now

                if not is_playing or current_art_key is None:
                    # Paused or nothing playing → the idle screen
                    effective = "idle"
                elif now - default_cd_start < cd_duration:
                    # Within the disc window of track start → CD
                    effective = "cd"
                else:
                    # After it → lyrics
                    effective = "lyrics"

                with playback_lock:
                    playback_state.effective_mode = (
                        idle_mode if effective == "idle" else effective
                    )

                if effective == "idle":
                    frame = idle_screen.render(
                        size, idle_mode, is_connected, accent_color, delta, now,
                    )
                    present(frame)
                    if args.once:
                        break
                    idle_budget = args.fps if idle_mode != "clock" else idle_fps
                    sleep_for = max(0.0, (1.0 / idle_budget) - (time.monotonic() - frame_start))
                    time.sleep(sleep_for)
                    continue
                elif effective == "lyrics":
                    frame = render_lyrics(
                        size, current_lyrics, stored_duration_ms,
                        is_playing, fetch_time, stored_progress_ms,
                        lyrics_style, smart_scroll, lyrics_font_size,
                        is_instrumental=is_instrumental,
                        lyrics_lead_ms=lyrics_lead_ms,
                        accent_color=accent_color,
                        track_key=current_art_key,
                        lyrics_words=current_lyrics_words,
                        crisp=not args.no_crisp_text,
                        progress_offset_ms=progress_offset_ms,
                    )
                    present(frame)
                    if args.once:
                        break
                    sleep_for = max(0.0, (1.0 / args.fps) - (time.monotonic() - frame_start))
                    time.sleep(sleep_for)
                    continue
                # else effective == "cd" → fall through to CD rendering below

            # ── CD mode (sticky or default-cd phase) ──────────────

            if display_mode == "cd":
                with playback_lock:
                    playback_state.effective_mode = "cd"

            display_text = ""
            if not args.no_text:
                if title and artist:
                    display_text = f"{title} · {artist}"
                elif title or artist:
                    display_text = title or artist

            # Detect track change
            if prev_art_key is not None and current_art_key != prev_art_key and args.transition != "none":
                old_art_image = last_art_image
                old_art_key = last_art_key
                old_angle = angle
                old_scroll_x = scroll_x
                old_display_text = last_display_text
                old_is_idle = is_idle_state
                scroll_x = 0.0
                transition_active = True
                transition_start = now
                current_transition_mode = args.transition

            # Detect idle state (CD mode only — in default mode, clock is handled above)
            if display_mode == "cd":
                if not is_playing or current_art_key is None:
                    if idle_since is None:
                        idle_since = now
                    elif now - idle_since >= 5.0 and not is_idle_state:
                        old_art_image = last_art_image
                        old_art_key = last_art_key
                        old_angle = angle
                        old_scroll_x = scroll_x
                        old_display_text = last_display_text
                        old_is_idle = is_idle_state
                        is_idle_state = True
                        transition_active = True
                        transition_start = now
                        current_transition_mode = "slide-down"
                else:
                    idle_since = None
                    if is_idle_state:
                        old_art_image = last_art_image
                        old_art_key = last_art_key
                        old_angle = angle
                        old_scroll_x = scroll_x
                        old_display_text = last_display_text
                        old_is_idle = is_idle_state
                        is_idle_state = False
                        transition_active = True
                        transition_start = now
                        current_transition_mode = "slide-up"
                        scroll_x = 0.0

            prev_art_key = current_art_key
            last_art_image = current_art_image
            last_art_key = current_art_key
            last_display_text = display_text

            # Spin easing
            target_rpm = runtime_rpm if (not is_idle_state and is_playing and current_art_image is not None) else 0.0
            if (is_playing and not was_playing) or (not is_playing and was_playing):
                spin_from_rpm = current_rpm
                spin_transition_start = now
            was_playing = is_playing

            spin_elapsed = now - spin_transition_start
            if spin_elapsed < SPIN_EASE_DURATION:
                t = spin_elapsed / SPIN_EASE_DURATION
                eased_t = t * t * (3.0 - 2.0 * t)
                current_rpm = spin_from_rpm + (target_rpm - spin_from_rpm) * eased_t
            else:
                current_rpm = target_rpm

            if current_rpm > 0.01:
                angle = (angle - 360.0 * (current_rpm / 60.0) * delta) % 360.0

            if not is_idle_state:
                scroll_x += runtime_text_speed * delta

            if is_idle_state:
                new_frame = idle_screen.render(
                    size, idle_mode, is_connected, accent_color, delta, now,
                )
            else:
                new_frame = create_full_frame(
                    current_art_image, angle, scroll_x, display_text,
                    size_x, size_y, args, art_key=current_art_key,
                    progress=ring_progress, accent_color=accent_color,
                    border_color=contrast_accent_color,
                    line_width=line_width,
                )

            if transition_active and current_transition_mode != "none":
                elapsed = now - transition_start
                duration = max(0.1, args.transition_duration)
                progress = elapsed / duration

                if progress >= 1.0:
                    transition_active = False
                    frame = new_frame
                else:
                    if old_is_idle:
                        # Re-rendering the live animation here would advance it
                        # twice per frame; a still clock face is the honest
                        # "what we were showing" for the blend.
                        old_frame = render_clock(size, is_connected, accent_color)
                    else:
                        if is_playing and not is_idle_state:
                            old_angle = (old_angle - 360.0 * (runtime_rpm / 60.0) * delta) % 360.0
                        old_scroll_x += runtime_text_speed * delta
                        old_frame = create_full_frame(
                            old_art_image, old_angle, old_scroll_x, old_display_text,
                            size_x, size_y, args, art_key=old_art_key,
                            progress=ring_progress, accent_color=accent_color,
                            border_color=contrast_accent_color,
                            line_width=line_width,
                        )
                    frame = blend_frames(old_frame, new_frame, progress, mode=current_transition_mode)
            else:
                frame = new_frame

            present(frame)

            if args.once:
                break

            # CD mode settled into its idle clock is as static as clock mode —
            # but an animated idle screen still needs the full frame budget.
            frame_fps = (
                idle_fps
                if (is_idle_state and not transition_active and idle_mode == "clock")
                else args.fps
            )
            sleep_for = max(0.0, (1.0 / frame_fps) - (time.monotonic() - frame_start))
            time.sleep(sleep_for)

    except KeyboardInterrupt:
        log("Shutting down...")
    finally:
        # Best-effort: every step here must run even if an earlier one fails,
        # otherwise a partial shutdown leaves the panel lit.
        stop_event.set()
        if control_server:
            try:
                control_server.shutdown()
                control_server.server_close()
            except Exception:
                pass
        try:
            poll_thread.join(timeout=1)
        except Exception:
            pass
        try:
            display.clear()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════
#  CLI & ENTRY POINT
# ═══════════════════════════════════════════════════════════════════

def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def render_preview_frames(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    art = demo_album_art(96)
    title = "Blinding Lights"
    artist = "The Weeknd"
    text_str = f"{title} · {artist}"
    size_x, size_y = 64, 64
    font_size = 9
    text_h = get_text_height(font_size)
    banner_h = text_h
    gap = 1
    cd_size = max(1, min(size_x, size_y - banner_h - gap))
    cd_x = (size_x - cd_size) // 2
    for index, angle in enumerate((0, 45, 90, 135)):
        cd_img = render_record(art, angle, cd_size)
        frame = Image.new("RGB", (size_x, size_y), (0, 0, 0))
        frame.paste(cd_img, (cd_x, 0))
        frame = draw_scrolling_text(frame, text_str, scroll_x=index * 15.0, banner_height=banner_h, font_size=font_size)
        frame.save(directory / f"album-disk-{index:02d}.png")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Spin Spotify album art on a 64x64 RGB matrix.")
    parser.add_argument("--rows", type=int, default=64)
    parser.add_argument("--cols", type=int, default=64)
    parser.add_argument("--chain-length", type=int, default=1)
    parser.add_argument("--parallel", type=int, default=1)
    parser.add_argument("--brightness", type=int, default=65)
    parser.add_argument("--gpio-slowdown", type=int, default=2)
    parser.add_argument("--hardware-mapping", default="regular")
    parser.add_argument("--pwm-bits", type=int, default=11)
    parser.add_argument("--limit-refresh-rate-hz", type=int, default=120)
    parser.add_argument("--no-hardware-pulse", action="store_true",
                        help="Avoid Pi onboard sound conflict.")
    parser.add_argument("--fps", type=positive_float, default=20.0)
    parser.add_argument("--rpm", type=positive_float, default=33.333,
                        help="Disc rotation speed. 33.333 is a real LP.")
    parser.add_argument("--idle-fps", type=positive_float, default=10.0,
                        help="Frame rate for the clock/idle screen. Only the second "
                             "dot and pulse animate, so full FPS is wasted CPU.")
    parser.add_argument("--static-fps", type=positive_float, default=2.0,
                        help="Frame rate for a non-animated Custom Slate image.")
    parser.add_argument("--gamma", type=float, default=1.0,
                        help="Gamma correction for the panel (1.0 = off). LEDs are "
                             "linear but sRGB is not; try 1.8. Leave at 1.0 if your "
                             "rgbmatrix build already applies CIE1931 correction.")
    parser.add_argument("--art-saturation", type=float, default=1.15,
                        help="Saturation multiplier applied to album art at download "
                             "time (1.0 = off).")
    parser.add_argument("--art-contrast", type=float, default=1.08,
                        help="Contrast multiplier applied to album art at download "
                             "time (1.0 = off).")
    parser.add_argument("--token-cache", type=Path, default=Path(".cache/spotify_token.json"))
    parser.add_argument("--lyrics-cache", type=Path, default=Path(".cache/lyrics"),
                        help="Directory for cached LRCLIB responses. Replays become "
                             "instant and LRCLIB stops being re-queried.")
    parser.add_argument("--lyrics-font", default=None,
                        help="Path to a pixel/bitmap TTF (e.g. PixelOperator8.ttf, or "
                             "a converted 5x7.bdf from rpi-rgb-led-matrix/fonts/). "
                             "Renders far crisper than the anti-aliased default.")
    parser.add_argument("--text-threshold", type=int, default=CRISP_THRESHOLD,
                        help="Alpha cutoff (0-255) when removing font anti-aliasing. "
                             "Lower keeps more of each stroke; raise it only if your "
                             "font renders too heavy.")
    parser.add_argument("--no-crisp-text", action="store_true",
                        help="Keep font anti-aliasing in lyrics. AA puts glyph edges "
                             "on half-lit LEDs, so it is thresholded off by default.")
    parser.add_argument("--lyrics-style", choices=["karaoke", "scroll", "pop"],
                        default="karaoke",
                        help="Startup lyrics style. 'karaoke' word-wraps the whole "
                             "line and lights words as they are sung.")
    parser.add_argument("--mock-output", type=Path,
                        help="Write the current frame PNG instead of using RGB matrix hardware.")
    parser.add_argument("--preview-frames", type=Path,
                        help="Render sample spinning-album-art disk frames and exit.")
    parser.add_argument("--auth-only", action="store_true",
                        help="Authorize Spotify, cache the token, and exit without using the matrix.")
    parser.add_argument("--test-pattern", action="store_true",
                        help="Show a bright moving color test pattern without using Spotify.")
    parser.add_argument("--once", action="store_true", help="Render one frame and exit.")
    parser.add_argument("--no-browser", action="store_true",
                        help="Print the Spotify auth URL without trying to open a browser.")
    parser.add_argument("--no-text", action="store_true",
                        help="Disable scrolling song title and artist text overlay.")
    parser.add_argument("--text-speed", type=positive_float, default=20.0,
                        help="Text scroll speed in pixels per second.")
    parser.add_argument("--text-position", choices=["bottom", "top"], default="bottom",
                        help="Text banner position on matrix.")
    parser.add_argument("--text-banner-height", type=int, default=0,
                        help="Height in pixels of text banner overlay (0 for auto-fit to text).")
    parser.add_argument("--text-font-size", type=int, default=9,
                        help="Font size in points for scrolling text.")
    # blend_frames also implements slide-up/slide-down, which the render loop
    # uses for the idle transition; they are offered here too rather than being
    # reachable only from inside the code.
    parser.add_argument("--transition",
                        choices=["slide", "slide-right", "slide-up", "slide-down",
                                 "fade", "none"],
                        default="slide",
                        help="Transition animation style when changing tracks.")
    parser.add_argument("--transition-duration", type=positive_float, default=1.5,
                        help="Duration in seconds for track change transition animation.")
    parser.add_argument("--web-port", type=int, default=5000,
                        help="Port for the web control panel (0 to disable).")
    parser.add_argument("--settings", type=Path, default=Path(".cache/settings.json"),
                        help="Where panel settings are persisted so brightness, "
                             "colour and mode survive a restart.")
    parser.add_argument("--no-settings", action="store_true",
                        help="Do not load or save settings — start from CLI "
                             "defaults every time.")
    parser.add_argument("--idle-mode", choices=list(IDLE_MODES), default="clock",
                        help="What fills the screen when nothing is playing. "
                             "'clock' is the original behaviour; 'cycle' rotates "
                             "through the ambient animations.")
    parser.add_argument("--no-playback-control", dest="enable_playback_control",
                        action="store_false",
                        help="Hide the play/pause/skip buttons in the web panel. "
                             "They need a token authorized for playback control; "
                             "re-run --auth-only once after upgrading.")
    parser.add_argument("--no-queue-peek", dest="enable_queue_peek",
                        action="store_false",
                        help="Do not show the next track during the last seconds "
                             "of the current one.")
    parser.add_argument("--cd-duration", type=positive_float, default=10.0,
                        help="Seconds the auto-cycling default mode shows the "
                             "spinning disc before switching to lyrics.")
    parser.add_argument("--line-width", type=int, default=1, choices=range(1, 6),
                        help="Line width in pixels for CD border and album art progress bar (1-5, default: 1).")
    return parser


if __name__ == "__main__":
    run(build_parser().parse_args())