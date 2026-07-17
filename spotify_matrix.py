#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import collections
import datetime
import functools
from io import BytesIO
import json
import math
import os
import re
import secrets
import sys
import threading
import time
import urllib.parse
import urllib.request
from email.message import Message
from urllib.error import HTTPError
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> None:
        return None


AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
CURRENTLY_PLAYING_URL = "https://api.spotify.com/v1/me/player/currently-playing"
SCOPE = "user-read-currently-playing"

LRCLIB_API_URL = "https://lrclib.net/api/get"
LRCLIB_USER_AGENT = "SpotifyMatrix/1.0 (https://github.com/Adi-Shinde/SpotifyMatrix)"

SPOTIFY_GREEN = (30, 215, 96)
LYRIC_DIM_COLOR = (80, 80, 80)

COLOR_THEMES: dict[str, tuple[int, int, int]] = {
    "spotify":  (30, 215, 96),
    "sunset":   (255, 107, 53),
    "ocean":    (0, 150, 255),
    "neon":     (180, 60, 255),
    "rose":     (255, 90, 150),
    "arctic":   (0, 220, 220),
    "gold":     (245, 180, 40),
    "crimson":  (220, 40, 60),
}

# Average ms per spoken word — used to cap scroll speed during instrumental gaps
AVG_MS_PER_WORD = 350


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


#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import collections
import datetime
import functools
from io import BytesIO
import json
import math
import os
import re
import secrets
import sys
import threading
import time
import urllib.parse
import urllib.request
from email.message import Message
from urllib.error import HTTPError
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> None:
        return None


AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
CURRENTLY_PLAYING_URL = "https://api.spotify.com/v1/me/player/currently-playing"
SCOPE = "user-read-currently-playing"

LRCLIB_API_URL = "https://lrclib.net/api/get"
LRCLIB_USER_AGENT = "SpotifyMatrix/1.0 (https://github.com/Adi-Shinde/SpotifyMatrix)"

SPOTIFY_GREEN = (30, 215, 96)
LYRIC_DIM_COLOR = (80, 80, 80)

COLOR_THEMES: dict[str, tuple[int, int, int]] = {
    "spotify":  (30, 215, 96),
    "sunset":   (255, 107, 53),
    "ocean":    (0, 150, 255),
    "neon":     (180, 60, 255),
    "rose":     (255, 90, 150),
    "arctic":   (0, 220, 220),
    "gold":     (245, 180, 40),
    "crimson":  (220, 40, 60),
}

# Average ms per spoken word — used to cap scroll speed during instrumental gaps
AVG_MS_PER_WORD = 350


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
    # Time sync fields
    progress_ms: int = 0
    duration_ms: int = 0
    fetch_time: float = 0.0  # time.monotonic() when Spotify data was fetched
    # Lyrics
    lyrics: list[tuple[int, str]] | None = None  # [(timestamp_ms, text), ...]
    lyrics_track_key: str | None = None
    is_instrumental: bool = False  # True when LRCLIB says track is instrumental
    lyrics_lead_ms: int = 180  # ms to shift lyrics ahead for read-along
    # Accent color
    accent_color: tuple[int, int, int] = (30, 215, 96)  # default SPOTIFY_GREEN
    accent_name: str = "spotify"
    # Custom message overlay
    custom_message: str = ""
    # Custom Slate mode
    custom_slate_frames: list[Image.Image] = field(default_factory=list)
    custom_slate_frame_delay: float = 0.1
    # Runtime-adjustable settings
    display_mode: str = "default"  # "default", "cd", "lyrics", "clock", "custom"
    effective_mode: str = "cd"  # what is actually rendering right now
    lyrics_style: str = "scroll"  # "scroll" or "pop"
    smart_scroll: bool = True  # time-proportional horizontal scrolling
    scroll_font_size: int = 9  # font size for scroll mode
    pop_font_size: int = 9  # font size for pop mode
    spin_speed: float = 10.0  # RPM
    text_scroll_speed: float = 20.0  # px/s
    poll_interval: float = 5.0  # seconds (active polling)
    brightness: int = 65  # 1-100
    # Boot defaults (for reset)
    _default_brightness: int = 65
    _default_spin_speed: float = 10.0
    _default_text_scroll_speed: float = 20.0
    _default_lyrics_style: str = "scroll"
    _default_scroll_font_size: int = 9
    _default_pop_font_size: int = 9


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

    def get_currently_playing(self) -> dict[str, Any] | None:
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
            log("Spotify API: Token expired (401), refreshing...", "warn")
            self._refresh_access_token()
            return self.get_currently_playing()
        if response.status == 429:
            retry_after = int(response.headers.get("Retry-After", "0"))
            log(f"Spotify API: Rate limited (429)! Wait {retry_after}s.", "error")
            raise RateLimitException(retry_after)
        if response.status != 200:
            raise_http_error(response, "Spotify currently-playing request")

        return response.json()

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

        with self.token_cache.open("r", encoding="utf-8") as token_file:
            return json.load(token_file)

    def _save_token(self, token: dict[str, Any]) -> None:
        self.token_cache.parent.mkdir(parents=True, exist_ok=True)
        token["expires_at"] = time.time() + int(token.get("expires_in", 3600)) - 60

        previous_refresh_token = self.token.get("refresh_token") if self.token else None
        if previous_refresh_token and "refresh_token" not in token:
            token["refresh_token"] = previous_refresh_token

        with self.token_cache.open("w", encoding="utf-8") as token_file:
            json.dump(token, token_file, indent=2)

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

    def show(self, image: Image.Image) -> None:
        self.canvas.SetImage(image.convert("RGB"))
        self.canvas = self.matrix.SwapOnVSync(self.canvas)

    def clear(self) -> None:
        self.matrix.Clear()

    def set_brightness(self, value: int) -> None:
        self.matrix.brightness = max(1, min(100, value))


class MockDisplay:
    def __init__(self, output: Path) -> None:
        self.output = output
        self.output.parent.mkdir(parents=True, exist_ok=True)

    def show(self, image: Image.Image) -> None:
        image.save(self.output)

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


@functools.lru_cache(maxsize=16)
def get_font(size: int = 9) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        try:
            return ImageFont.truetype("arial.ttf", size)
        except OSError:
            return ImageFont.load_default()


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

    image = max(images, key=lambda candidate: candidate.get("width") or 0)
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


def download_image(url: str) -> Image.Image:
    request = urllib.request.Request(url)
    with urllib.request.urlopen(request, timeout=15) as response:
        return Image.open(BytesIO(response.read())).convert("RGB")


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


def render_record(art: Image.Image | None, angle: float, size: int) -> Image.Image:
    frame = Image.new("RGBA", (size, size), (0, 0, 0, 255))
    if art is None:
        return frame.convert("RGB")

    disc_size = size
    art_square = ImageOps.fit(art, (disc_size, disc_size), method=Image.Resampling.LANCZOS)
    rotated = art_square.rotate(angle, resample=Image.Resampling.BICUBIC)

    disc_mask = _get_disc_mask(disc_size)
    frame.paste(rotated.convert("RGBA"), (0, 0), disc_mask)

    draw = ImageDraw.Draw(frame, "RGBA")
    draw.ellipse((0, 0, size - 1, size - 1), outline=(220, 220, 220, 200), width=1)

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


def render_idle(size: int) -> Image.Image:
    frame = Image.new("RGB", (size, size), (0, 0, 0))
    draw = ImageDraw.Draw(frame)
    draw.ellipse((0, 0, size - 1, size - 1), outline=(220, 220, 220), width=1)
    center = size // 2
    radius = max(2, size // 25)
    draw.ellipse((center - radius, center - radius, center + radius, center + radius), fill=(18, 18, 18))
    return frame


def render_clock(size: int, is_connected: bool = True,
                 accent_color: tuple[int, int, int] = SPOTIFY_GREEN) -> Image.Image:
    frame = Image.new("RGB", (size, size), (0, 0, 0))
    draw = ImageDraw.Draw(frame)
    now = datetime.datetime.now()

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

    second_angle = (now.second / 60.0) * 360 - 90
    rad = math.radians(second_angle)
    dot_r = outer_r - 1
    sx = cx + math.cos(rad) * dot_r
    sy = cy + math.sin(rad) * dot_r
    draw.ellipse((sx - 1.5, sy - 1.5, sx + 1.5, sy + 1.5), fill=accent_color)

    pulse = (math.sin(time.time() * 2.0) + 1.0) / 2.0
    pulse_brightness = int(50 + pulse * 150)
    if is_connected:
        # Derive pulse from accent color
        pulse_color = tuple(int(c * pulse_brightness / 200) for c in accent_color)
    else:
        pulse_color = (pulse_brightness, 0, 0)

    pulse_margin = max(4, size // 12)
    pulse_x = size - pulse_margin
    pulse_y = size - pulse_margin
    pulse_r = 2
    draw.ellipse((pulse_x - pulse_r, pulse_y - pulse_r, pulse_x + pulse_r, pulse_y + pulse_r), fill=pulse_color)

    return frame


# ═══════════════════════════════════════════════════════════════════
#  RENDERING — SCROLLING TEXT / FULL FRAME / TRANSITIONS
# ═══════════════════════════════════════════════════════════════════

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

    offset_x = -(scroll_x % unit_w)
    cur_x = offset_x

    while cur_x < size_x:
        if cur_x + unit_w > 0:
            draw.text((cur_x, y_pos), full_unit, fill=text_color, font=font)
        cur_x += unit_w

    fade_width = min(6, size_x // 10)
    for i in range(fade_width):
        left_x = i
        right_x = size_x - 1 - i
        for y in range(banner_y0, banner_y1 + 1):
            orig = image.getpixel((left_x, y))
            blended = tuple(int(orig[c] * i / fade_width) for c in range(3))
            image.putpixel((left_x, y), blended)
            orig = image.getpixel((right_x, y))
            blended = tuple(int(orig[c] * i / fade_width) for c in range(3))
            image.putpixel((right_x, y), blended)

    return image


def create_full_frame(
    art_image: Image.Image | None,
    angle: float,
    scroll_x: float,
    display_text: str,
    size_x: int,
    size_y: int,
    args: argparse.Namespace,
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

    cd_img = render_record(art_image, angle, cd_size) if art_image else render_idle(cd_size)

    frame = Image.new("RGB", (size_x, size_y), (0, 0, 0))
    cd_x = (size_x - cd_size) // 2
    cd_y = (banner_h + gap) if (has_text and args.text_position == "top") else 0
    frame.paste(cd_img, (cd_x, cd_y))

    now = datetime.datetime.now()
    hour_str = now.strftime("%I").lstrip("0")
    minute_str = now.strftime("%M")

    clock_font_size = max(9, args.text_font_size + 1)
    clock_font = get_font(clock_font_size)
    dummy_draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))

    hour_bbox = dummy_draw.textbbox((0, 0), hour_str, font=clock_font)
    minute_bbox = dummy_draw.textbbox((0, 0), minute_str, font=clock_font)

    hour_h = hour_bbox[3] - hour_bbox[1]
    minute_w = minute_bbox[2] - minute_bbox[0]
    minute_h = minute_bbox[3] - minute_bbox[1]

    draw = ImageDraw.Draw(frame)
    if args.text_position == "top":
        clock_y = size_y - max(hour_h, minute_h) - 1
    else:
        clock_y = 1

    hour_x = 1
    minute_x = size_x - minute_w - 1

    for dx in [-1, 0, 1]:
        for dy in [-1, 0, 1]:
            if dx != 0 or dy != 0:
                draw.text((hour_x + dx, clock_y - hour_bbox[1] + dy), hour_str, fill=(0, 0, 0), font=clock_font)
                draw.text((minute_x + dx, clock_y - minute_bbox[1] + dy), minute_str, fill=(0, 0, 0), font=clock_font)

    draw.text((hour_x, clock_y - hour_bbox[1]), hour_str, fill=(200, 200, 200), font=clock_font)
    draw.text((minute_x, clock_y - minute_bbox[1]), minute_str, fill=(200, 200, 200), font=clock_font)

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

    if mode in ("slide", "slide-left"):
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


def parse_lrc(synced_lyrics: str) -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    for line in synced_lyrics.splitlines():
        match = _LRC_LINE_RE.match(line.strip())
        if match:
            minutes = int(match.group(1))
            seconds = float(match.group(2))
            text = match.group(3).strip()
            timestamp_ms = int((minutes * 60 + seconds) * 1000)
            result.append((timestamp_ms, text))
    result.sort(key=lambda x: x[0])
    return result


def fetch_lyrics(
    artist: str, track: str, album: str, duration_s: int
) -> tuple[list[tuple[int, str]] | None, bool]:
    """Fetch synced lyrics from LRCLIB.

    Returns (lyrics_list_or_None, is_instrumental).
    """
    try:
        params = {
            "artist_name": artist,
            "track_name": track,
            "album_name": album,
            "duration": str(duration_s),
        }
        req = urllib.request.Request(
            f"{LRCLIB_API_URL}?{urllib.parse.urlencode(params)}",
            headers={"User-Agent": LRCLIB_USER_AGENT},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        # Detect instrumental tracks
        is_instrumental = bool(data.get("instrumental", False))
        if not is_instrumental:
            plain = data.get("plainLyrics") or ""
            synced_raw = data.get("syncedLyrics") or ""
            if not plain.strip() and not synced_raw.strip():
                is_instrumental = True

        synced = data.get("syncedLyrics")
        if not synced:
            return None, is_instrumental
        parsed = parse_lrc(synced)
        return (parsed if parsed else None), is_instrumental
    except Exception as exc:
        log(f"LRCLIB: Failed to fetch lyrics: {exc}", "warn")
        return None, False


def fetch_lyrics_async(
    artist: str, track: str, album: str, duration_s: int,
    state: SharedPlaybackState, lock: threading.Lock, track_key: str,
) -> None:
    log(f"LRCLIB: Fetching lyrics for '{track}' by '{artist}'...")
    lyrics, is_instrumental = fetch_lyrics(artist, track, album, duration_s)
    with lock:
        if state.art_key == track_key:
            state.lyrics = lyrics
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


# Persistent state for smooth lyrics scrolling (updated each frame)
_lyrics_scroll_state = {
    "last_idx": -1,
    "scroll_y": 0.0,       # current interpolated Y offset
    "target_y": 0.0,       # target Y offset
    "transition_start": 0.0,
}

LYRICS_FONT_SIZE = 9
LYRICS_LINE_HEIGHT = 14   # pixels between lines (font 9 ≈ 10px tall + 4px gap)
LYRICS_CENTER_Y = 28      # vertical center for the active line
LYRICS_SCROLL_DURATION = 0.4  # seconds for smooth scroll animation
LYRICS_H_SCROLL_SPEED = 15.0  # px/s for horizontal overflow scroll


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
    text_w: int, size: int, now_mono: float, is_active: bool,
    time_on_screen_ms: int,
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

    # Calculate estimated progress
    if is_playing and fetch_time > 0:
        elapsed_since_fetch = (time.monotonic() - fetch_time) * 1000
        estimated_progress = stored_progress_ms + int(elapsed_since_fetch)
    else:
        estimated_progress = stored_progress_ms

    if duration_ms > 0:
        estimated_progress = min(estimated_progress, duration_ms)

    # Apply lyrics lead offset (read-ahead: see words before they're sung)
    display_progress = estimated_progress + lyrics_lead_ms

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
            note = "\u266a"
            nbbox = draw.textbbox((0, 0), note, font=font)
            nw = nbbox[2] - nbbox[0]
            draw.text((x - nw - 3, y - nbbox[1]), note, fill=accent_color, font=font)
            draw.text((x + tw + 3, y - nbbox[1]), note, fill=accent_color, font=font)
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

        if style == "scroll":
            # Smooth vertical scroll animation
            line_height = max(10, font_size + 4)
            center_y = size // 2 - font_size // 2

            if idx != _lyrics_scroll_state["last_idx"]:
                _lyrics_scroll_state["last_idx"] = idx
                _lyrics_scroll_state["target_y"] = float(idx * line_height)
                _lyrics_scroll_state["transition_start"] = now_mono

            target_y = _lyrics_scroll_state["target_y"]
            elapsed = now_mono - _lyrics_scroll_state["transition_start"]

            if elapsed < LYRICS_SCROLL_DURATION:
                t = elapsed / LYRICS_SCROLL_DURATION
                eased = 1.0 - (1.0 - t) ** 3
                old_y = _lyrics_scroll_state["scroll_y"]
                current_y = old_y + (target_y - old_y) * eased
            else:
                current_y = target_y
                _lyrics_scroll_state["scroll_y"] = target_y

            if elapsed >= LYRICS_SCROLL_DURATION:
                _lyrics_scroll_state["scroll_y"] = target_y

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
                    x = _legacy_h_scroll_x(text_w, size, now_mono, True, time_on_screen_ms)

                draw.text((x, y_draw), text, fill=color, font=font)
        else:
            # Pop mode — 3 fixed lines
            y_positions = [10, 28, 46]
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
                    x = _legacy_h_scroll_x(text_w, size, now_mono, True, time_on_screen_ms)

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
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
<title>SpotifyMatrix Control</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

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
    font-family: 'Inter', -apple-system, sans-serif;
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
  .card-title { font-size: 13px; text-transform: uppercase; letter-spacing: 1px; color: #a1a1aa; font-weight: 600; margin-bottom: 16px; text-shadow: 0 1px 4px rgba(0,0,0,0.8); }

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

  /* Color Grid */
  .color-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
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

  <!-- Now Playing -->
  <div class="card now-playing" onclick="toggleLiveLyrics()" style="cursor: pointer;" title="Tap for Live Lyrics">
    <div class="album-art">
      <img id="npImg" src="" style="display:none">
    </div>
    <div class="track-info">
      <div id="npTitle" class="track-title" style="color:var(--text-dim)">Not Playing</div>
      <div id="npArtist" class="track-artist">--</div>
      <div id="npInstr" class="instrumental-badge" style="display:none">&#127929; Instrumental</div>
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
    <div class="card-title">&#128242; Display Mode</div>
    <div class="modes">
      <button class="mode-btn" id="mode-default" onclick="setMode('default')">Auto (Smart)</button>
      <button class="mode-btn" id="mode-cd" onclick="setMode('cd')">CD View</button>
      <button class="mode-btn" id="mode-lyrics" onclick="setMode('lyrics')">Lyrics</button>
      <button class="mode-btn" id="mode-clock" onclick="setMode('clock')">Clock</button>
      <button class="mode-btn" id="mode-custom" onclick="setMode('custom')" style="grid-column: span 2;">Custom Slate</button>
    </div>
  </div>

  <!-- Main Settings (Brightness & Lyric Style) -->
  <div class="card" id="mainSettingsCard">
    <div class="card-title">&#9881; Main Settings</div>
    
    <div class="segmented">
      <div class="seg-btn" id="style-scroll" onclick="setSetting('lyrics-style', 'scroll')">Scroll Mode</div>
      <div class="seg-btn" id="style-pop" onclick="setSetting('lyrics-style', 'pop')">Pop Mode</div>
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
          <span class="name">&#128225; Poll Rate (sec)</span>
          <span class="value" id="pollVal">5</span>
        </div>
        <input type="range" id="pollRate" min="1" max="60" value="5"
               oninput="document.getElementById('pollVal').textContent=this.value"
               onchange="setSetting('poll-rate', this.value)">
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

const COLOR_THEMES = {
  spotify:  {r:30,g:215,b:96},
  sunset:   {r:255,g:107,b:53},
  ocean:    {r:0,g:150,b:255},
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
})();

function toggleAdv() {
  const adv = document.getElementById('advSettings');
  const btn = document.querySelector('.adv-toggle');
  if (adv.style.display === 'block') {
    adv.style.display = 'none';
    btn.innerHTML = 'Show Advanced Settings &#9662;';
  } else {
    adv.style.display = 'block';
    btn.innerHTML = 'Hide Advanced Settings &#9652;';
  }
}

async function fetchState() {
  try {
    const res = await fetch('/api/state');
    const s = await res.json();
    currentState = s;
    updateUI(s);
  } catch(e) {}
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

  // Update modes visibility
  document.querySelectorAll('.mode-btn').forEach(el => el.classList.remove('active'));
  const mBtn = document.getElementById('mode-' + s.display_mode);
  if (mBtn) mBtn.classList.add('active');

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
  }

  // Segmented lyrics style
  document.querySelectorAll('.seg-btn').forEach(el => el.classList.remove('active'));
  const sBtn = document.getElementById('style-' + s.lyrics_style);
  if (sBtn) sBtn.classList.add('active');

  // Sliders
  function setSld(id, val) {
    const el = document.getElementById(id);
    if(el && document.activeElement !== el) {
      el.value = val;
      document.getElementById(id+'Val').textContent = val;
    }
  }
  setSld('brightness', s.brightness);
  setSld('spinSpeed', s.spin_speed);
  setSld('textSpeed', s.text_scroll_speed);
  setSld('pollRate', s.poll_interval);
  setSld('scrollFont', s.scroll_font_size);
  setSld('popFont', s.pop_font_size);
  setSld('leadTime', s.lyrics_lead_ms);

  // Colors
  const t = COLOR_THEMES[s.accent_name] || COLOR_THEMES.spotify;
  document.documentElement.style.setProperty('--accent', `rgb(${t.r},${t.g},${t.b})`);
  document.documentElement.style.setProperty('--accent-dim', `rgba(${t.r},${t.g},${t.b},0.15)`);
  document.documentElement.style.setProperty('--accent-glow', `rgba(${t.r},${t.g},${t.b},0.3)`);
  
  document.querySelectorAll('.color-swatch').forEach(el => {
    el.classList.toggle('active', el.dataset.theme === s.accent_name);
  });
  
  // Badges
  const instr = document.getElementById('npInstr');
  if (s.is_instrumental) { instr.style.display = 'inline-block'; }
  else { instr.style.display = 'none'; }
}

async function setMode(m) {
  try {
    await fetch('/api/mode', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({mode: m})
    });
    setTimeout(fetchState, 100);
  } catch(e) {}
}

async function setSetting(key, value) {
  try {
    await fetch('/api/' + key, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({value: value})
    });
  } catch(e) {}
}

async function setAccentColor(name) {
  try {
    await fetch('/api/accent-color', {
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
    await fetch('/api/reset', { method: 'POST' });
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
    html += `<div id="line-${i}" style="transition:all 0.3s; padding:4px 0;">${line[1] || '· · ·'}</div>`;
  });
  html += '<div style="height:100px;"></div>';
  box.innerHTML = html;
}

function updateLiveLyricsScroll() {
  if (!lyricsData || !currentState.is_playing) return;
  const currentMs = currentState.progress_ms + (Date.now() - window.lastStateFetchTime);
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
</script>
</body>
</html>
                    self._send_json({"error": "Invalid mode"}, 400)

            elif parsed.path == "/api/brightness":
                val = int(body.get("value", 65))
                val = max(1, min(100, val))
                with outer_lock:
                    outer_state.brightness = val
                try:
                    outer_display.set_brightness(val)
                except Exception:
                    pass
                log(f"Brightness set to {val}")
                self._send_json({"ok": True, "brightness": val})

            elif parsed.path == "/api/spin-speed":
                val = float(body.get("value", 20))
                val = max(1.0, min(120.0, val))
                with outer_lock:
                    outer_state.spin_speed = val
                self._send_json({"ok": True, "spin_speed": val})

            elif parsed.path == "/api/text-speed":
                val = float(body.get("value", 12))
                val = max(1.0, min(100.0, val))
                with outer_lock:
                    outer_state.text_scroll_speed = val
                self._send_json({"ok": True, "text_scroll_speed": val})

            elif parsed.path == "/api/poll-rate":
                val = float(body.get("value", 5))
                val = max(1.0, min(60.0, val))
                with outer_lock:
                    outer_state.poll_interval = val
                self._send_json({"ok": True, "poll_interval": val})

            elif parsed.path == "/api/lyrics-style":
                style = body.get("value", "scroll")
                if style in ("scroll", "pop"):
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

            elif parsed.path == "/api/scroll-font-size":
                val = int(body.get("value", 9))
                val = max(6, min(14, val))
                with outer_lock:
                    outer_state.scroll_font_size = val
                self._send_json({"ok": True, "scroll_font_size": val})

            elif parsed.path == "/api/pop-font-size":
                val = int(body.get("value", 8))
                val = max(6, min(14, val))
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
                    outer_state.poll_interval = 5.0
                    outer_state.accent_name = "spotify"
                    outer_state.accent_color = COLOR_THEMES["spotify"]
                    outer_state.lyrics_lead_ms = 180
                try:
                    outer_display.set_brightness(outer_state._default_brightness)
                except Exception:
                    pass
                log("Settings reset to defaults")
                self._send_json({"ok": True})

            elif parsed.path == "/api/accent-color":
                val = body.get("value", "spotify")
                if val in COLOR_THEMES:
                    with outer_lock:
                        outer_state.accent_name = val
                        outer_state.accent_color = COLOR_THEMES[val]
                    self._send_json({"ok": True, "accent_name": val})
                else:
                    self._send_json({"error": "Invalid theme"}, 400)

            elif parsed.path == "/api/lyrics-lead":
                val = int(body.get("value", 180))
                val = max(0, min(500, val))
                with outer_lock:
                    outer_state.lyrics_lead_ms = val
                self._send_json({"ok": True, "lyrics_lead_ms": val})

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
                        for frame_idx in range(img.n_frames):
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

        def _read_body(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length", 0))
                if length > 0:
                    raw = self.rfile.read(length)
                    return json.loads(raw.decode("utf-8"))
            except Exception:
                pass
            return {}

        def _send_html(self, html: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        def _send_json(self, data: Any, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode("utf-8"))

        def _send_state(self) -> None:
            with outer_lock:
                data = {
                    "display_mode": outer_state.display_mode,
                    "effective_mode": outer_state.effective_mode,
                    "brightness": outer_state.brightness,
                    "spin_speed": outer_state.spin_speed,
                    "text_scroll_speed": outer_state.text_scroll_speed,
                    "poll_interval": outer_state.poll_interval,
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
                    "lyrics_lead_ms": outer_state.lyrics_lead_ms,
                }
            self._send_json(data)

        def log_message(self, format: str, *args: Any) -> None:
            return

    try:
        server = HTTPServer(("0.0.0.0", port), ControlPanelHandler)
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

def poll_spotify(
    spotify: SpotifyClient,
    state: SharedPlaybackState,
    state_lock: threading.Lock,
    stop_event: threading.Event,
) -> None:
    first_poll = True
    log("Spotify: Background polling thread started.")

    idle_seconds = 30.0
    last_playing_time = time.time()
    backoff_multiplier = 1
    last_track_key: str | None = None

    while not stop_event.is_set():
        try:
            with state_lock:
                active_seconds = state.poll_interval
            current_wait = active_seconds

            if first_poll:
                log("Spotify: Making initial API connection...")
                first_poll = False

            playback = spotify.get_currently_playing()
            art = playback_art_from_response(playback)

            fetch_time = time.monotonic()

            backoff_multiplier = 1
            with state_lock:
                state.is_connected = True

            if art and art.is_playing:
                last_playing_time = time.time()
                current_wait = active_seconds

                remaining_ms = art.duration_ms - art.progress_ms
                if remaining_ms < 15000:
                    current_wait = max(1.5, remaining_ms / 5000.0)
                    log(f"Spotify: Near track end. Accelerated poll rate: {current_wait:.1f}s", verbose=True)
                elif art.progress_ms < 30000 and art.duration_ms > 120000:
                    current_wait = min(10.0, active_seconds * 1.5)
                    log(f"Spotify: Track just started. Backed off poll rate: {current_wait:.1f}s", verbose=True)

            time_since_played = time.time() - last_playing_time
            if time_since_played > 60.0:
                current_wait = idle_seconds

            if art:
                with state_lock:
                    needs_download = art.key != state.art_key or art.image_url != state.image_url
                    is_new_track = art.key != last_track_key

                image = download_image(art.image_url) if needs_download else None

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

                if is_new_track and art.key:
                    last_track_key = art.key
                    log(f"Track: {art.title} — {art.artist}")
                    with state_lock:
                        state.lyrics = None
                        state.lyrics_track_key = None
                    duration_s = max(1, art.duration_ms // 1000)
                    lyrics_thread = threading.Thread(
                        target=fetch_lyrics_async,
                        args=(art.artist, art.title, art.album_name, duration_s, state, state_lock, art.key),
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
            with state_lock:
                state.is_connected = False
            stop_event.wait(wait_time)


# ═══════════════════════════════════════════════════════════════════
#  MAIN RUN LOOP
# ═══════════════════════════════════════════════════════════════════

def run(args: argparse.Namespace) -> None:
    try:
        os.nice(-5)
        log("Process priority elevated (nice=-5)")
    except (OSError, PermissionError, AttributeError):
        log("Could not elevate process priority (not root, or Windows?)", "warn")

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
        display = MockDisplay(args.mock_output)
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
        _default_brightness=args.brightness,
        _default_spin_speed=args.rpm,
        _default_text_scroll_speed=args.text_speed,
    )
    playback_lock = threading.Lock()
    stop_event = threading.Event()

    control_server = start_control_server(args.web_port, playback_state, playback_lock, display, args)

    poll_thread = threading.Thread(
        target=poll_spotify,
        args=(spotify, playback_state, playback_lock, stop_event),
        daemon=True,
    )
    poll_thread.start()

    angle = 0.0
    scroll_x = 0.0
    last_frame = time.monotonic()

    prev_art_key: str | None = None
    last_art_image: Image.Image | None = None
    last_display_text: str = ""
    old_art_image: Image.Image | None = None
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

    # Default mode auto-cycle state
    default_cd_start: float = 0.0  # when the CD phase started
    default_last_track_key: str | None = None  # to detect new songs in default mode
    DEFAULT_CD_DURATION = 10.0  # seconds to show CD before switching to lyrics

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

            now = time.monotonic()
            delta = now - last_frame
            last_frame = now

            # Apply runtime brightness if changed
            if runtime_brightness != last_brightness:
                try:
                    display.set_brightness(runtime_brightness)
                except Exception:
                    pass
                last_brightness = runtime_brightness

            # ══════════════════════════════════════════════════
            #  MODE ROUTING
            # ══════════════════════════════════════════════════

            # --- STICKY: Custom Slate mode ---
            if display_mode == "custom":
                with playback_lock:
                    playback_state.effective_mode = "custom"
                    frames = playback_state.custom_slate_frames
                    delay = playback_state.custom_slate_frame_delay
                frame = render_custom_slate(size, frames, delay)
                display.show(frame)
                if args.once:
                    break
                sleep_for = max(0.0, (1.0 / args.fps) - (time.monotonic() - frame_start))
                time.sleep(sleep_for)
                continue

            # --- STICKY: Clock mode ---
            if display_mode == "clock":
                with playback_lock:
                    playback_state.effective_mode = "clock"
                frame = render_clock(size, is_connected, accent_color)
                display.show(frame)
                if args.once:
                    break
                sleep_for = max(0.0, (1.0 / args.fps) - (time.monotonic() - frame_start))
                time.sleep(sleep_for)
                continue

            # --- STICKY: Lyrics mode ---
            if display_mode == "lyrics":
                with playback_lock:
                    playback_state.effective_mode = "lyrics"
                frame = render_lyrics(
                    size, current_lyrics, stored_duration_ms,
                    is_playing, fetch_time, stored_progress_ms,
                    playback_state.lyrics_style,
                    playback_state.smart_scroll,
                    playback_state.scroll_font_size if playback_state.lyrics_style == "scroll" else playback_state.pop_font_size,
                    is_instrumental=is_instrumental,
                    lyrics_lead_ms=lyrics_lead_ms,
                    accent_color=accent_color,
                )
                display.show(frame)
                if args.once:
                    break
                sleep_for = max(0.0, (1.0 / args.fps) - (time.monotonic() - frame_start))
                time.sleep(sleep_for)
                continue

            # --- DEFAULT mode (auto-cycling) ---
            if display_mode == "default":
                # Detect new track → reset CD timer
                if current_art_key != default_last_track_key and current_art_key is not None:
                    default_last_track_key = current_art_key
                    default_cd_start = now

                if not is_playing or current_art_key is None:
                    # Paused or nothing playing → clock
                    effective = "clock"
                elif now - default_cd_start < DEFAULT_CD_DURATION:
                    # Within 10s of track start → CD
                    effective = "cd"
                else:
                    # After 10s → lyrics
                    effective = "lyrics"

                with playback_lock:
                    playback_state.effective_mode = effective

                if effective == "clock":
                    frame = render_clock(size, is_connected, accent_color)
                    display.show(frame)
                    if args.once:
                        break
                    sleep_for = max(0.0, (1.0 / args.fps) - (time.monotonic() - frame_start))
                    time.sleep(sleep_for)
                    continue
                elif effective == "lyrics":
                    frame = render_lyrics(
                        size, current_lyrics, stored_duration_ms,
                        is_playing, fetch_time, stored_progress_ms,
                        playback_state.lyrics_style,
                        playback_state.smart_scroll,
                        playback_state.scroll_font_size if playback_state.lyrics_style == "scroll" else playback_state.pop_font_size,
                        is_instrumental=is_instrumental,
                        lyrics_lead_ms=lyrics_lead_ms,
                        accent_color=accent_color,
                    )
                    display.show(frame)
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
                new_frame = render_clock(size, is_connected)
            else:
                new_frame = create_full_frame(
                    current_art_image, angle, scroll_x, display_text,
                    size_x, size_y, args,
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
                        old_frame = render_clock(size)
                    else:
                        if is_playing and not is_idle_state:
                            old_angle = (old_angle - 360.0 * (runtime_rpm / 60.0) * delta) % 360.0
                        old_scroll_x += runtime_text_speed * delta
                        old_frame = create_full_frame(
                            old_art_image, old_angle, old_scroll_x, old_display_text,
                            size_x, size_y, args,
                        )
                    frame = blend_frames(old_frame, new_frame, progress, mode=current_transition_mode)
            else:
                frame = new_frame

            display.show(frame)

            if args.once:
                break

            sleep_for = max(0.0, (1.0 / args.fps) - (time.monotonic() - frame_start))
            time.sleep(sleep_for)

    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        if control_server:
            control_server.shutdown()
        poll_thread.join(timeout=1)
        display.clear()


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
    parser.add_argument("--rpm", type=positive_float, default=20.0)
    parser.add_argument("--token-cache", type=Path, default=Path(".cache/spotify_token.json"))
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
    parser.add_argument("--text-speed", type=positive_float, default=12.0,
                        help="Text scroll speed in pixels per second.")
    parser.add_argument("--text-position", choices=["bottom", "top"], default="bottom",
                        help="Text banner position on matrix.")
    parser.add_argument("--text-banner-height", type=int, default=0,
                        help="Height in pixels of text banner overlay (0 for auto-fit to text).")
    parser.add_argument("--text-font-size", type=int, default=9,
                        help="Font size in points for scrolling text.")
    parser.add_argument("--transition", choices=["slide", "slide-right", "fade", "none"],
                        default="slide",
                        help="Transition animation style when changing tracks.")
    parser.add_argument("--transition-duration", type=positive_float, default=1.5,
                        help="Duration in seconds for track change transition animation.")
    parser.add_argument("--web-port", type=int, default=5000,
                        help="Port for the web control panel (0 to disable).")
    return parser


if __name__ == "__main__":
    run(build_parser().parse_args())