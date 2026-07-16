#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
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
    # Runtime-adjustable settings
    display_mode: str = "cd"  # "cd", "lyrics", "clock"
    spin_speed: float = 20.0  # RPM
    text_scroll_speed: float = 12.0  # px/s
    poll_interval: float = 5.0  # seconds (active polling)
    brightness: int = 65  # 1-100


@dataclass
class HttpResponse:
    status: int
    headers: Message
    body: bytes

    def json(self) -> dict[str, Any]:
        return json.loads(self.body.decode("utf-8"))


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
            print(f"Spotify: Token loaded from {token_cache}", flush=True)
        else:
            print("Spotify: No token found in cache", flush=True)

    def get_currently_playing(self) -> dict[str, Any] | None:
        token = self._valid_access_token()
        response = http_request(
            "GET",
            CURRENTLY_PLAYING_URL,
            params={"additional_types": "track,episode"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
        if sys.stdout.isatty() or response.status != 200:
            print(f"Spotify API: Received HTTP {response.status}", flush=True)

        if response.status == 204:
            return None
        if response.status == 401:
            print("Spotify API: Token expired or invalid (401), attempting refresh...", flush=True)
            self._refresh_access_token()
            return self.get_currently_playing()
        if response.status == 429:
            retry_after = int(response.headers.get("Retry-After", "0"))
            print(f"Spotify API: Rate limited (429)! Server requested wait of {retry_after} seconds.", flush=True)
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
        print("Spotify: Refreshing access token...", flush=True)
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
        """Set matrix brightness at runtime (1-100)."""
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
        """No-op for mock display."""
        pass


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

    # Extract progress and duration from the playback response
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


_disc_mask_cache: dict[int, Image.Image] = {}


def _get_disc_mask(size: int) -> Image.Image:
    """Return a cached circular mask for the given size."""
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
    # The album art is the record surface: rotate it first, then cut it into a circular disk.
    art_square = ImageOps.fit(art, (disc_size, disc_size), method=Image.Resampling.LANCZOS)
    rotated = art_square.rotate(angle, resample=Image.Resampling.BICUBIC)

    disc_mask = _get_disc_mask(disc_size)
    frame.paste(rotated.convert("RGBA"), (0, 0), disc_mask)

    draw = ImageDraw.Draw(frame, "RGBA")
    draw.ellipse((0, 0, size - 1, size - 1), outline=(220, 220, 220, 200), width=1)

    center = size // 2
    label_radius = max(3, size // 16)
    hole_radius = max(1, size // 40)

    # Center label
    draw.ellipse(
        (
            center - label_radius,
            center - label_radius,
            center + label_radius,
            center + label_radius,
        ),
        fill=(16, 16, 16, 210),
        outline=(220, 220, 220, 90),
    )
    # Spindle hole
    draw.ellipse(
        (
            center - hole_radius,
            center - hole_radius,
            center + hole_radius,
            center + hole_radius,
        ),
        fill=(0, 0, 0, 255),
    )
    return frame.convert("RGB")


def render_idle(size: int) -> Image.Image:
    frame = Image.new("RGB", (size, size), (0, 0, 0))
    draw = ImageDraw.Draw(frame)
    margin = 0
    draw.ellipse((margin, margin, size - margin - 1, size - margin - 1), outline=(220, 220, 220), width=1)
    center = size // 2
    radius = max(2, size // 25)
    draw.ellipse((center - radius, center - radius, center + radius, center + radius), fill=(18, 18, 18))
    return frame


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


def render_clock(size: int, is_connected: bool = True) -> Image.Image:
    frame = Image.new("RGB", (size, size), (0, 0, 0))
    draw = ImageDraw.Draw(frame)
    now = datetime.datetime.now()

    # Text content
    day_str = now.strftime("%a").upper()               # e.g., "FRI"
    time_str = now.strftime("%I:%M %p").lstrip("0")    # e.g., "4:05 PM"
    date_str = now.strftime("%b %d").upper()           # e.g., "OCT 25"

    small_font = get_font(max(8, size // 8))
    time_font = get_font(max(10, size // 5))

    # Calculate bounding boxes
    day_bbox = draw.textbbox((0, 0), day_str, font=small_font)
    time_bbox = draw.textbbox((0, 0), time_str, font=time_font)
    date_bbox = draw.textbbox((0, 0), date_str, font=small_font)

    day_h = day_bbox[3] - day_bbox[1]
    time_h = time_bbox[3] - time_bbox[1]
    date_h = date_bbox[3] - date_bbox[1]

    # Layout gap and total height
    gap = 2
    total_h = day_h + gap + time_h + gap + date_h
    start_y = (size - total_h) // 2

    # Draw Day — dim Spotify green
    day_x = (size - (day_bbox[2] - day_bbox[0])) // 2
    draw.text((day_x, start_y - day_bbox[1]), day_str, fill=SPOTIFY_GREEN, font=small_font)

    # Draw Time (AM/PM)
    time_y = start_y + day_h + gap
    time_x = (size - (time_bbox[2] - time_bbox[0])) // 2
    draw.text((time_x, time_y - time_bbox[1]), time_str, fill=(255, 255, 255), font=time_font)

    # Draw Date
    date_y = time_y + time_h + gap
    date_x = (size - (date_bbox[2] - date_bbox[0])) // 2
    draw.text((date_x, date_y - date_bbox[1]), date_str, fill=(180, 180, 180), font=small_font)

    # Outer circle ring
    margin = 1
    draw.ellipse((margin, margin, size - margin - 1, size - margin - 1), outline=(50, 50, 70), width=1)

    # Hour tick marks around the ring
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

    # Sweeping seconds dot — tick once per second
    second_angle = (now.second / 60.0) * 360 - 90
    rad = math.radians(second_angle)
    dot_r = outer_r - 1
    sx = cx + math.cos(rad) * dot_r
    sy = cy + math.sin(rad) * dot_r
    draw.ellipse((sx - 1.5, sy - 1.5, sx + 1.5, sy + 1.5), fill=SPOTIFY_GREEN)

    # Connection status pulse dot (bottom right corner, with margins)
    pulse = (math.sin(time.time() * 2.0) + 1.0) / 2.0  # 0.0 to 1.0
    pulse_brightness = int(50 + pulse * 150)
    if is_connected:
        pulse_color = (0, pulse_brightness, int(pulse_brightness * 0.3)) # Green
    else:
        pulse_color = (pulse_brightness, 0, 0) # Red
    
    pulse_margin = max(4, size // 12)
    pulse_x = size - pulse_margin
    pulse_y = size - pulse_margin
    pulse_r = 2 # slightly bigger than 1
    draw.ellipse((pulse_x - pulse_r, pulse_y - pulse_r, pulse_x + pulse_r, pulse_y + pulse_r), fill=pulse_color)

    return frame


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

    # High-contrast solid background banner
    draw.rectangle((0, banner_y0, size_x - 1, banner_y1), fill=bg_color)

    separator = "   - - -   "
    full_unit = text + separator
    unit_bbox = draw.textbbox((0, 0), full_unit, font=font)
    unit_w = unit_bbox[2] - unit_bbox[0]

    if unit_w <= 0:
        return image

    offset_x = - (scroll_x % unit_w)
    cur_x = offset_x

    while cur_x < size_x:
        if cur_x + unit_w > 0:
            draw.text((cur_x, y_pos), full_unit, fill=text_color, font=font)
        cur_x += unit_w

    # Gradient fade edges — text smoothly emerges from / fades into black
    fade_width = min(6, size_x // 10)
    for i in range(fade_width):
        alpha = int(255 * (i / fade_width))
        fade_color = tuple(int(c * alpha / 255) for c in bg_color) or (0, 0, 0)
        # Left edge fade (opaque → transparent)
        left_x = i
        for y in range(banner_y0, banner_y1 + 1):
            orig = image.getpixel((left_x, y))
            blended = tuple(int(orig[c] * i / fade_width) for c in range(3))
            image.putpixel((left_x, y), blended)
        # Right edge fade (transparent → opaque)
        right_x = size_x - 1 - i
        for y in range(banner_y0, banner_y1 + 1):
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

    # Draw clock overlay
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

    # Text outline for visibility
    for dx in [-1, 0, 1]:
        for dy in [-1, 0, 1]:
            if dx != 0 or dy != 0:
                draw.text((hour_x + dx, clock_y - hour_bbox[1] + dy), hour_str, fill=(0, 0, 0), font=clock_font)
                draw.text((minute_x + dx, clock_y - minute_bbox[1] + dy), minute_str, fill=(0, 0, 0), font=clock_font)

    draw.text((hour_x, clock_y - hour_bbox[1]), hour_str, fill=(200, 200, 200), font=clock_font)
    draw.text((minute_x, clock_y - minute_bbox[1]), minute_str, fill=(200, 200, 200), font=clock_font)

    if has_text:
        frame = draw_scrolling_text(
            frame,
            text=display_text,
            scroll_x=scroll_x,
            position=args.text_position,
            banner_height=banner_h,
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
        (255, 0, 0),
        (255, 160, 0),
        (255, 255, 0),
        (0, 255, 0),
        (0, 120, 255),
        (80, 0, 255),
        (255, 255, 255),
        (0, 0, 0),
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
    """Parse an LRC synced lyrics string into [(timestamp_ms, text), ...]."""
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
) -> list[tuple[int, str]] | None:
    """Fetch synced lyrics from LRCLIB. Returns parsed list or None."""
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

        synced = data.get("syncedLyrics")
        if not synced:
            return None
        parsed = parse_lrc(synced)
        return parsed if parsed else None
    except Exception as exc:
        print(f"LRCLIB: Failed to fetch lyrics: {exc}", flush=True)
        return None


def fetch_lyrics_async(
    artist: str,
    track: str,
    album: str,
    duration_s: int,
    state: SharedPlaybackState,
    lock: threading.Lock,
    track_key: str,
) -> None:
    """Background thread target: fetch lyrics and store in shared state."""
    print(f"LRCLIB: Fetching lyrics for '{track}' by '{artist}'...", flush=True)
    lyrics = fetch_lyrics(artist, track, album, duration_s)
    with lock:
        # Only store if the track hasn't changed while we were fetching
        if state.art_key == track_key:
            state.lyrics = lyrics
            state.lyrics_track_key = track_key
    if lyrics:
        print(f"LRCLIB: Found {len(lyrics)} synced lyric lines.", flush=True)
    else:
        print("LRCLIB: No synced lyrics available for this track.", flush=True)


# ═══════════════════════════════════════════════════════════════════
#  LYRICS RENDERER
# ═══════════════════════════════════════════════════════════════════

def get_current_lyric_index(lyrics: list[tuple[int, str]], progress_ms: int) -> int:
    """Binary search to find the index of the current lyric line."""
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


def render_lyrics(
    size: int,
    lyrics: list[tuple[int, str]] | None,
    progress_ms: int,
    duration_ms: int,
    is_playing: bool,
    fetch_time: float,
    stored_progress_ms: int,
) -> Image.Image:
    """Render the 3-line lyrics view for the 64x64 matrix."""
    frame = Image.new("RGB", (size, size), (0, 0, 0))
    draw = ImageDraw.Draw(frame)
    font = get_font(8)

    # Calculate estimated progress with local time interpolation
    if is_playing and fetch_time > 0:
        elapsed_since_fetch = (time.monotonic() - fetch_time) * 1000
        estimated_progress = stored_progress_ms + int(elapsed_since_fetch)
    else:
        estimated_progress = stored_progress_ms

    # Clamp to duration
    if duration_ms > 0:
        estimated_progress = min(estimated_progress, duration_ms)

    if not lyrics:
        # No lyrics available — show placeholder
        no_lyrics_text = "No Lyrics"
        bbox = draw.textbbox((0, 0), no_lyrics_text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        x = (size - tw) // 2
        y = (size - th) // 2
        draw.text((x, y - bbox[1]), no_lyrics_text, fill=LYRIC_DIM_COLOR, font=font)

        # Music note decorations
        note = "♪"
        note_bbox = draw.textbbox((0, 0), note, font=font)
        nw = note_bbox[2] - note_bbox[0]
        draw.text((x - nw - 3, y - note_bbox[1]), note, fill=SPOTIFY_GREEN, font=font)
        draw.text((x + tw + 3, y - note_bbox[1]), note, fill=SPOTIFY_GREEN, font=font)
    else:
        idx = get_current_lyric_index(lyrics, estimated_progress)

        # Y positions for 3 lines on 64px display
        y_positions = [10, 28, 46]
        line_indices = [idx - 1, idx, idx + 1]
        colors = [LYRIC_DIM_COLOR, SPOTIFY_GREEN, LYRIC_DIM_COLOR]

        # Calculate how long the current lyric has been active (for scrolling)
        if idx >= 0:
            lyric_start_ms = lyrics[idx][0]
            time_on_screen_ms = estimated_progress - lyric_start_ms
        else:
            time_on_screen_ms = 0

        for line_i, (li, y_pos, color) in enumerate(zip(line_indices, y_positions, colors)):
            if li < 0 or li >= len(lyrics):
                continue
            text = lyrics[li][1]
            if not text.strip():
                continue

            bbox = draw.textbbox((0, 0), text, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
            y_draw = y_pos - bbox[1]

            if text_w <= size:
                # Text fits — center it
                x = (size - text_w) // 2
                draw.text((x, y_draw), text, fill=color, font=font)
            else:
                # Text overflows — apply horizontal scroll
                overflow = text_w - size
                if line_i == 1:
                    # Current line: scroll based on time on screen
                    scroll_speed = 20.0  # px/s
                    scroll_offset = (time_on_screen_ms / 1000.0) * scroll_speed
                    # Ping-pong: scroll right, pause, scroll left
                    cycle_duration = overflow / scroll_speed  # time to scroll one direction
                    total_cycle = cycle_duration * 2 + 1.0  # add 0.5s pause at each end
                    t = (time_on_screen_ms / 1000.0) % total_cycle
                    if t < 0.5:
                        x = 0
                    elif t < 0.5 + cycle_duration:
                        x = -int(overflow * ((t - 0.5) / cycle_duration))
                    elif t < 1.0 + cycle_duration:
                        x = -overflow
                    else:
                        x = -int(overflow * (1.0 - (t - 1.0 - cycle_duration) / cycle_duration))
                else:
                    # Context lines: slow constant scroll
                    scroll_time = time.monotonic() * 10.0  # slow
                    x = -int(scroll_time % (overflow + size)) + size // 2
                    x = max(-overflow, min(0, x))
                draw.text((x, y_draw), text, fill=color, font=font)

    # Progress bar at bottom (1px height)
    if duration_ms > 0:
        progress_frac = max(0.0, min(1.0, estimated_progress / duration_ms))
        bar_w = int(progress_frac * size)
        if bar_w > 0:
            draw.rectangle((0, size - 1, bar_w - 1, size - 1), fill=SPOTIFY_GREEN)
        # Dim background for remaining
        if bar_w < size:
            draw.rectangle((bar_w, size - 1, size - 1, size - 1), fill=(30, 30, 30))

    return frame


# ═══════════════════════════════════════════════════════════════════
#  WEB CONTROL PANEL
# ═══════════════════════════════════════════════════════════════════

CONTROL_PANEL_HTML = """<!DOCTYPE html>
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
    --card: #141414;
    --card-border: #1e1e1e;
    --text: #e4e4e7;
    --text-dim: #71717a;
    --green: #1ed760;
    --green-dim: rgba(30, 215, 96, 0.15);
    --green-glow: rgba(30, 215, 96, 0.3);
    --accent: #1db954;
    --danger: #ef4444;
    --radius: 12px;
  }
  
  body {
    font-family: 'Inter', -apple-system, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    padding: 16px;
    padding-bottom: 40px;
    -webkit-tap-highlight-color: transparent;
  }
  
  .header {
    text-align: center;
    padding: 20px 0 24px;
  }
  .header h1 {
    font-size: 20px;
    font-weight: 700;
    letter-spacing: -0.5px;
    background: linear-gradient(135deg, var(--green), #1ed79a);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }
  .header .subtitle {
    font-size: 11px;
    color: var(--text-dim);
    margin-top: 4px;
    text-transform: uppercase;
    letter-spacing: 1.5px;
  }
  
  .status-dot {
    display: inline-block;
    width: 6px; height: 6px;
    border-radius: 50%;
    margin-right: 4px;
    vertical-align: middle;
  }
  .status-dot.connected { background: var(--green); box-shadow: 0 0 6px var(--green-glow); }
  .status-dot.disconnected { background: var(--danger); }
  
  .card {
    background: var(--card);
    border: 1px solid var(--card-border);
    border-radius: var(--radius);
    padding: 16px;
    margin-bottom: 12px;
  }
  .card-title {
    font-size: 11px;
    font-weight: 600;
    color: var(--text-dim);
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-bottom: 12px;
  }
  
  /* Now Playing */
  .now-playing {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .now-playing .album-art {
    width: 48px; height: 48px;
    border-radius: 8px;
    background: #222;
    flex-shrink: 0;
    overflow: hidden;
  }
  .now-playing .album-art img {
    width: 100%; height: 100%;
    object-fit: cover;
  }
  .now-playing .track-info {
    flex: 1;
    min-width: 0;
  }
  .track-info .title {
    font-size: 14px;
    font-weight: 600;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .track-info .artist {
    font-size: 12px;
    color: var(--text-dim);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .play-status {
    font-size: 10px;
    color: var(--green);
    margin-top: 2px;
  }
  .play-status.paused { color: var(--text-dim); }
  
  /* Mode Selector */
  .mode-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 8px;
  }
  .mode-btn {
    background: var(--bg);
    border: 2px solid var(--card-border);
    border-radius: 10px;
    padding: 14px 8px;
    text-align: center;
    cursor: pointer;
    transition: all 0.2s ease;
    -webkit-user-select: none;
    user-select: none;
  }
  .mode-btn:active { transform: scale(0.96); }
  .mode-btn.active {
    border-color: var(--green);
    background: var(--green-dim);
    box-shadow: 0 0 12px var(--green-glow);
  }
  .mode-btn .icon { font-size: 24px; margin-bottom: 4px; }
  .mode-btn .label {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  
  /* Sliders */
  .slider-group {
    margin-bottom: 16px;
  }
  .slider-group:last-child { margin-bottom: 0; }
  .slider-label {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
  }
  .slider-label .name {
    font-size: 13px;
    font-weight: 500;
  }
  .slider-label .value {
    font-size: 13px;
    font-weight: 600;
    color: var(--green);
    min-width: 40px;
    text-align: right;
  }
  input[type="range"] {
    -webkit-appearance: none;
    width: 100%;
    height: 6px;
    border-radius: 3px;
    background: #333;
    outline: none;
  }
  input[type="range"]::-webkit-slider-thumb {
    -webkit-appearance: none;
    width: 20px; height: 20px;
    border-radius: 50%;
    background: var(--green);
    cursor: pointer;
    box-shadow: 0 0 8px var(--green-glow);
  }
  input[type="range"]::-moz-range-thumb {
    width: 20px; height: 20px;
    border-radius: 50%;
    background: var(--green);
    cursor: pointer;
    border: none;
  }
  
  .footer {
    text-align: center;
    padding: 20px 0;
    font-size: 10px;
    color: var(--text-dim);
  }
</style>
</head>
<body>

<div class="header">
  <h1>SpotifyMatrix</h1>
  <div class="subtitle">
    <span class="status-dot connected" id="statusDot"></span>
    <span id="statusText">Connected</span>
  </div>
</div>

<!-- Now Playing Card -->
<div class="card">
  <div class="card-title">Now Playing</div>
  <div class="now-playing">
    <div class="album-art" id="albumArt"></div>
    <div class="track-info">
      <div class="title" id="trackTitle">—</div>
      <div class="artist" id="trackArtist">Waiting for Spotify...</div>
      <div class="play-status" id="playStatus">⏸ Paused</div>
    </div>
  </div>
</div>

<!-- Display Mode Card -->
<div class="card">
  <div class="card-title">Display Mode</div>
  <div class="mode-grid">
    <div class="mode-btn active" data-mode="cd" onclick="setMode('cd')">
      <div class="icon">💿</div>
      <div class="label">CD</div>
    </div>
    <div class="mode-btn" data-mode="lyrics" onclick="setMode('lyrics')">
      <div class="icon">🎵</div>
      <div class="label">Lyrics</div>
    </div>
    <div class="mode-btn" data-mode="clock" onclick="setMode('clock')">
      <div class="icon">🕐</div>
      <div class="label">Clock</div>
    </div>
  </div>
</div>

<!-- Settings Card -->
<div class="card">
  <div class="card-title">Settings</div>
  
  <div class="slider-group">
    <div class="slider-label">
      <span class="name">☀ Brightness</span>
      <span class="value" id="brightnessVal">65</span>
    </div>
    <input type="range" id="brightness" min="1" max="100" value="65"
           oninput="document.getElementById('brightnessVal').textContent=this.value"
           onchange="setSetting('brightness', this.value)">
  </div>
  
  <div class="slider-group">
    <div class="slider-label">
      <span class="name">💫 Spin Speed (RPM)</span>
      <span class="value" id="spinVal">20</span>
    </div>
    <input type="range" id="spinSpeed" min="1" max="120" value="20"
           oninput="document.getElementById('spinVal').textContent=this.value"
           onchange="setSetting('spin-speed', this.value)">
  </div>
  
  <div class="slider-group">
    <div class="slider-label">
      <span class="name">📜 Text Speed (px/s)</span>
      <span class="value" id="textVal">12</span>
    </div>
    <input type="range" id="textSpeed" min="1" max="100" value="12"
           oninput="document.getElementById('textVal').textContent=this.value"
           onchange="setSetting('text-speed', this.value)">
  </div>
  
  <div class="slider-group">
    <div class="slider-label">
      <span class="name">📡 Poll Rate (seconds)</span>
      <span class="value" id="pollVal">5</span>
    </div>
    <input type="range" id="pollRate" min="1" max="60" value="5"
           oninput="document.getElementById('pollVal').textContent=this.value"
           onchange="setSetting('poll-rate', this.value)">
  </div>
</div>

<div class="footer">SpotifyMatrix · matrixspot.local</div>

<script>
let currentState = {};

async function fetchState() {
  try {
    const res = await fetch('/api/state');
    if (!res.ok) return;
    const data = await res.json();
    currentState = data;
    updateUI(data);
  } catch(e) {
    document.getElementById('statusDot').className = 'status-dot disconnected';
    document.getElementById('statusText').textContent = 'Connection Lost';
  }
}

function updateUI(s) {
  // Status
  const dot = document.getElementById('statusDot');
  const stxt = document.getElementById('statusText');
  dot.className = 'status-dot ' + (s.is_connected ? 'connected' : 'disconnected');
  stxt.textContent = s.is_connected ? 'Connected' : 'Disconnected';
  
  // Now Playing
  document.getElementById('trackTitle').textContent = s.title || '—';
  document.getElementById('trackArtist').textContent = s.artist || 'Waiting for Spotify...';
  
  const ps = document.getElementById('playStatus');
  if (s.is_playing) {
    ps.textContent = '▶ Playing';
    ps.className = 'play-status';
  } else {
    ps.textContent = '⏸ Paused';
    ps.className = 'play-status paused';
  }
  
  // Album art
  const artDiv = document.getElementById('albumArt');
  if (s.image_url) {
    if (!artDiv.querySelector('img') || artDiv.querySelector('img').src !== s.image_url) {
      artDiv.innerHTML = '<img src="' + s.image_url + '" alt="Album Art">';
    }
  } else {
    artDiv.innerHTML = '';
  }
  
  // Mode buttons
  document.querySelectorAll('.mode-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.mode === s.display_mode);
  });
  
  // Sliders — only update if user is not actively dragging
  if (document.activeElement?.id !== 'brightness') {
    document.getElementById('brightness').value = s.brightness;
    document.getElementById('brightnessVal').textContent = s.brightness;
  }
  if (document.activeElement?.id !== 'spinSpeed') {
    document.getElementById('spinSpeed').value = Math.round(s.spin_speed);
    document.getElementById('spinVal').textContent = Math.round(s.spin_speed);
  }
  if (document.activeElement?.id !== 'textSpeed') {
    document.getElementById('textSpeed').value = Math.round(s.text_scroll_speed);
    document.getElementById('textVal').textContent = Math.round(s.text_scroll_speed);
  }
  if (document.activeElement?.id !== 'pollRate') {
    document.getElementById('pollRate').value = Math.round(s.poll_interval);
    document.getElementById('pollVal').textContent = Math.round(s.poll_interval);
  }
}

async function setMode(mode) {
  // Optimistic UI update
  document.querySelectorAll('.mode-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.mode === mode);
  });
  try {
    await fetch('/api/mode', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({mode: mode})
    });
  } catch(e) {}
}

async function setSetting(name, value) {
  try {
    await fetch('/api/' + name, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({value: Number(value)})
    });
  } catch(e) {}
}

// Poll state every 2 seconds
fetchState();
setInterval(fetchState, 2000);
</script>

</body>
</html>"""


def start_control_server(
    port: int,
    state: SharedPlaybackState,
    lock: threading.Lock,
    display: MatrixDisplay | MockDisplay,
) -> HTTPServer | None:
    """Start the web control panel HTTP server on a background thread."""
    if port <= 0:
        return None

    outer_state = state
    outer_lock = lock
    outer_display = display

    class ControlPanelHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)

            if parsed.path == "/" or parsed.path == "":
                self._send_html(CONTROL_PANEL_HTML)
            elif parsed.path == "/api/state":
                self._send_state()
            # Legacy simple endpoint for compatibility
            elif parsed.path == "/mode":
                params = urllib.parse.parse_qs(parsed.query)
                mode = params.get("set", [""])[0]
                if mode in ("cd", "lyrics", "clock"):
                    with outer_lock:
                        outer_state.display_mode = mode
                    self._send_json({"ok": True, "mode": mode})
                else:
                    self._send_json({"error": "Invalid mode. Use: cd, lyrics, clock"}, 400)
            else:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not Found")

        def do_POST(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            body = self._read_body()

            if parsed.path == "/api/mode":
                mode = body.get("mode", "")
                if mode in ("cd", "lyrics", "clock"):
                    with outer_lock:
                        outer_state.display_mode = mode
                    self._send_json({"ok": True, "mode": mode})
                else:
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

        def _send_json(self, data: dict, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode("utf-8"))

        def _send_state(self) -> None:
            with outer_lock:
                data = {
                    "display_mode": outer_state.display_mode,
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
                    "has_lyrics": outer_state.lyrics is not None and len(outer_state.lyrics or []) > 0,
                    "progress_ms": outer_state.progress_ms,
                    "duration_ms": outer_state.duration_ms,
                }
            self._send_json(data)

        def log_message(self, format: str, *args: Any) -> None:
            # Suppress HTTP access logs to avoid journal bloat
            return

    try:
        server = HTTPServer(("0.0.0.0", port), ControlPanelHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(f"Web Control Panel: http://0.0.0.0:{port}/", flush=True)
        return server
    except OSError as exc:
        print(f"Web Control Panel: Failed to start on port {port}: {exc}", flush=True)
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
    last_status: str | None = None
    first_poll = True
    print("Spotify: Background polling thread started.", flush=True)

    idle_seconds = 30.0
    last_playing_time = time.time()
    backoff_multiplier = 1
    last_track_key: str | None = None

    while not stop_event.is_set():
        try:
            # Read current poll interval from shared state
            with state_lock:
                active_seconds = state.poll_interval
            current_wait = active_seconds

            if first_poll:
                print("Spotify: Making initial API connection...", flush=True)
                first_poll = False
                
            playback = spotify.get_currently_playing()
            art = playback_art_from_response(playback)

            # Record fetch time for local time interpolation
            fetch_time = time.monotonic()

            # Reset backoff and mark connected on successful API call
            backoff_multiplier = 1
            with state_lock:
                state.is_connected = True

            if art and art.is_playing:
                # Active playback detected
                last_playing_time = time.time()
                if current_wait != active_seconds:
                    print(f"Spotify: Playback resumed. Switching to active polling ({active_seconds}s).", flush=True)
                current_wait = active_seconds

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
                    # Time sync: store progress and fetch time
                    state.progress_ms = art.progress_ms
                    state.duration_ms = art.duration_ms
                    state.fetch_time = fetch_time
                    if image is not None:
                        state.image = image

                # Fire lyrics fetch on new track
                if is_new_track and art.key:
                    last_track_key = art.key
                    # Clear old lyrics immediately
                    with state_lock:
                        state.lyrics = None
                        state.lyrics_track_key = None
                    # Fetch new lyrics in background
                    duration_s = max(1, art.duration_ms // 1000)
                    lyrics_thread = threading.Thread(
                        target=fetch_lyrics_async,
                        args=(art.artist, art.title, art.album_name, duration_s, state, state_lock, art.key),
                        daemon=True,
                    )
                    lyrics_thread.start()

                status = f"art found, is_playing={art.is_playing}, title={art.title!r}"
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

            # Prepend active/idle polling state to the status log
            if current_wait == active_seconds:
                time_until_idle = max(0, int(60.0 - (time.time() - last_playing_time)))
                prefix = f"[Active | {time_until_idle}s to idle]"
            else:
                prefix = "[Idle]"
            
            is_interactive = sys.stdout.isatty()

            if is_interactive:
                # Verbose mode for manual terminal usage: tick every second
                for _ in range(int(current_wait)):
                    if stop_event.is_set():
                        break
                    
                    if current_wait == active_seconds:
                        time_until_idle = max(0, int(60.0 - (time.time() - last_playing_time)))
                        prefix = f"[Active | {time_until_idle}s to idle]"
                    else:
                        prefix = "[Idle]"
                    
                    tick_status = f"{prefix} {status}"
                    print(f"Spotify: {tick_status}", flush=True)
                    stop_event.wait(1.0)
            else:
                # Silent mode for systemd/auto usage: save CPU and SD card.
                # No periodic status printing is done in the background to prevent journal bloat.
                stop_event.wait(current_wait)

        except RateLimitException as exc:
            wait_time = exc.retry_after
            if wait_time <= 0:
                wait_time = active_seconds * backoff_multiplier
                print(f"Spotify API: Rate limited without Retry-After header. Exponential backoff: {wait_time}s...", flush=True)
                backoff_multiplier = min(backoff_multiplier * 2, 64) # Cap backoff
            else:
                print(f"Spotify API: Rate limited. Retrying after {wait_time}s...", flush=True)
            
            with state_lock:
                state.is_connected = False
            stop_event.wait(wait_time)

        except Exception as exc:
            print(f"Spotify poll failed: {exc}", flush=True)
            wait_time = active_seconds * backoff_multiplier
            print(f"Spotify API: Connection error. Exponential backoff: {wait_time}s...", flush=True)
            backoff_multiplier = min(backoff_multiplier * 2, 64)
            with state_lock:
                state.is_connected = False
            stop_event.wait(wait_time)


# ═══════════════════════════════════════════════════════════════════
#  MAIN RUN LOOP
# ═══════════════════════════════════════════════════════════════════

def run(args: argparse.Namespace) -> None:
    if args.preview_frames:
        render_preview_frames(args.preview_frames)
        return

    load_dotenv()

    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    redirect_uri = os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")

    missing = [
        name
        for name, value in (
            ("SPOTIFY_CLIENT_ID", client_id),
            ("SPOTIFY_CLIENT_SECRET", client_secret),
            ("SPOTIFY_REDIRECT_URI", redirect_uri),
        )
        if not value
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
        print("Matrix: Initializing Mock Display...", flush=True)
        display = MockDisplay(args.mock_output)
    else:
        print("Matrix: Initializing hardware RGB Matrix... (this may take a few seconds)", flush=True)
        display = MatrixDisplay(args)

    # Startup info banner
    print("\n" + "=" * 48, flush=True)
    print("  Spotify Matrix — Ready", flush=True)
    print("=" * 48, flush=True)
    print(f"  Display:    {args.cols}x{args.rows}  brightness={args.brightness}", flush=True)
    print(f"  Hardware:   {args.hardware_mapping}  gpio-slowdown={args.gpio_slowdown}", flush=True)
    print(f"  Animation:  {args.fps} FPS  {args.rpm} RPM  transition={args.transition}", flush=True)
    print(f"  Polling:    5s active / 30s idle (dynamic)", flush=True)
    if args.web_port > 0:
        print(f"  Web Panel:  http://0.0.0.0:{args.web_port}/", flush=True)
    print("=" * 48 + "\n", flush=True)

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
        spin_speed=args.rpm,
        text_scroll_speed=args.text_speed,
        brightness=args.brightness,
    )
    playback_lock = threading.Lock()
    stop_event = threading.Event()

    # Start web control panel
    control_server = start_control_server(args.web_port, playback_state, playback_lock, display)

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

    # Spin easing state
    SPIN_EASE_DURATION = 1.0  # seconds to ramp up/down
    current_rpm: float = 0.0
    was_playing: bool = False
    spin_transition_start: float = 0.0
    spin_from_rpm: float = 0.0

    # Track previous display mode for transitions
    prev_display_mode: str = "cd"
    # Track last brightness to detect changes
    last_brightness: int = args.brightness

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
                # Time sync data
                stored_progress_ms = playback_state.progress_ms
                stored_duration_ms = playback_state.duration_ms
                fetch_time = playback_state.fetch_time
                current_lyrics = playback_state.lyrics
                is_connected = playback_state.is_connected

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

            # Handle forced mode (lyrics or clock bypass idle logic)
            if display_mode == "clock":
                # Clock mode: render clock directly, no idle delay
                frame = render_clock(size, is_connected)
                display.show(frame)

                if args.once:
                    break
                sleep_for = max(0.0, (1.0 / args.fps) - (time.monotonic() - frame_start))
                time.sleep(sleep_for)
                prev_display_mode = display_mode
                continue

            if display_mode == "lyrics":
                # Lyrics mode: render lyrics view
                frame = render_lyrics(
                    size,
                    current_lyrics,
                    stored_progress_ms,
                    stored_duration_ms,
                    is_playing,
                    fetch_time,
                    stored_progress_ms,
                )
                display.show(frame)

                if args.once:
                    break
                sleep_for = max(0.0, (1.0 / args.fps) - (time.monotonic() - frame_start))
                time.sleep(sleep_for)
                prev_display_mode = display_mode
                continue

            # ── CD mode (existing logic) ──────────────────────────

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

            # Detect idle state
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

            # Spin easing — smoothly ramp RPM up or down
            target_rpm = runtime_rpm if (not is_idle_state and is_playing and current_art_image is not None) else 0.0
            if (is_playing and not was_playing) or (not is_playing and was_playing):
                spin_from_rpm = current_rpm
                spin_transition_start = now
            was_playing = is_playing

            spin_elapsed = now - spin_transition_start
            if spin_elapsed < SPIN_EASE_DURATION:
                t = spin_elapsed / SPIN_EASE_DURATION
                eased_t = t * t * (3.0 - 2.0 * t)  # smoothstep
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
                    current_art_image,
                    angle,
                    scroll_x,
                    display_text,
                    size_x,
                    size_y,
                    args,
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
                            old_art_image,
                            old_angle,
                            old_scroll_x,
                            old_display_text,
                            size_x,
                            size_y,
                            args,
                        )
                    frame = blend_frames(old_frame, new_frame, progress, mode=current_transition_mode)
            else:
                frame = new_frame

            display.show(frame)

            if args.once:
                break

            sleep_for = max(0.0, (1.0 / args.fps) - (time.monotonic() - frame_start))
            time.sleep(sleep_for)

            prev_display_mode = display_mode
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        if control_server:
            control_server.shutdown()
        poll_thread.join(timeout=1)
        display.clear()


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
    parser.add_argument(
        "--no-hardware-pulse",
        action="store_true",
        help="Avoid Pi onboard sound conflict at the cost of more possible flicker.",
    )
    parser.add_argument("--fps", type=positive_float, default=20.0)
    parser.add_argument("--rpm", type=positive_float, default=20.0)
    parser.add_argument("--token-cache", type=Path, default=Path(".cache/spotify_token.json"))
    parser.add_argument("--mock-output", type=Path, help="Write the current frame PNG instead of using RGB matrix hardware.")
    parser.add_argument("--preview-frames", type=Path, help="Render sample spinning-album-art disk frames and exit.")
    parser.add_argument("--auth-only", action="store_true", help="Authorize Spotify, cache the token, and exit without using the matrix.")
    parser.add_argument("--test-pattern", action="store_true", help="Show a bright moving color test pattern without using Spotify.")
    parser.add_argument("--once", action="store_true", help="Render one frame and exit.")
    parser.add_argument("--no-browser", action="store_true", help="Print the Spotify auth URL without trying to open a browser.")
    parser.add_argument("--no-text", action="store_true", help="Disable scrolling song title and artist text overlay.")
    parser.add_argument("--text-speed", type=positive_float, default=12.0, help="Text scroll speed in pixels per second.")
    parser.add_argument("--text-position", choices=["bottom", "top"], default="bottom", help="Text banner position on matrix.")
    parser.add_argument("--text-banner-height", type=int, default=0, help="Height in pixels of text banner overlay (0 for auto-fit to text).")
    parser.add_argument("--text-font-size", type=int, default=9, help="Font size in points for scrolling text.")
    parser.add_argument(
        "--transition",
        choices=["slide", "slide-right", "fade", "none"],
        default="slide",
        help="Transition animation style when changing tracks.",
    )
    parser.add_argument(
        "--transition-duration",
        type=positive_float,
        default=1.5,
        help="Duration in seconds for track change transition animation.",
    )
    parser.add_argument(
        "--web-port",
        type=int,
        default=5000,
        help="Port for the web control panel (0 to disable). Access at http://<pi-ip>:<port>/",
    )
    return parser


if __name__ == "__main__":
    run(build_parser().parse_args())