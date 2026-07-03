#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
from io import BytesIO
import json
import os
import secrets
import threading
import time
import urllib.parse
import urllib.request
from email.message import Message
from urllib.error import HTTPError
import webbrowser
from dataclasses import dataclass
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


@dataclass
class PlaybackArt:
    key: str
    image_url: str
    is_playing: bool
    title: str = ""
    artist: str = ""


@dataclass
class SharedPlaybackState:
    art_key: str | None = None
    image_url: str | None = None
    image: Image.Image | None = None
    is_playing: bool = False
    title: str = ""
    artist: str = ""


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

    def get_currently_playing(self) -> dict[str, Any] | None:
        token = self._valid_access_token()
        response = http_request(
            "GET",
            CURRENTLY_PLAYING_URL,
            params={"additional_types": "track,episode"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )

        if response.status == 204:
            return None
        if response.status == 401:
            self._refresh_access_token()
            return self.get_currently_playing()
        if response.status == 429:
            retry_after = int(response.headers.get("Retry-After", "5"))
            time.sleep(max(retry_after, 1))
            return None
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

        self.matrix = RGBMatrix(options=options)
        self.canvas = self.matrix.CreateFrameCanvas()

    def show(self, image: Image.Image) -> None:
        self.canvas.SetImage(image.convert("RGB"))
        self.canvas = self.matrix.SwapOnVSync(self.canvas)

    def clear(self) -> None:
        self.matrix.Clear()


class MockDisplay:
    def __init__(self, output: Path) -> None:
        self.output = output
        self.output.parent.mkdir(parents=True, exist_ok=True)

    def show(self, image: Image.Image) -> None:
        image.save(self.output)

    def clear(self) -> None:
        return


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
    else:
        images = item.get("images", [])
        title = item.get("name") or ""
        show = item.get("show") or {}
        artist_name = show.get("name") or ""

    if not images:
        return None

    image = max(images, key=lambda candidate: candidate.get("width") or 0)
    item_id = item.get("id") or item.get("uri") or image["url"]
    return PlaybackArt(
        key=str(item_id),
        image_url=image["url"],
        is_playing=bool(playback.get("is_playing")),
        title=title,
        artist=artist_name,
    )


def download_image(url: str) -> Image.Image:
    import requests

    response = requests.get(url, timeout=15)
    response.raise_for_status()
    return Image.open(BytesIO(response.content)).convert("RGB")


def render_record(art: Image.Image | None, angle: float, size: int) -> Image.Image:
    frame = Image.new("RGBA", (size, size), (0, 0, 0, 255))
    if art is None:
        return frame.convert("RGB")

    margin = 0
    disc_size = size
    # The album art is the record surface: rotate it first, then cut it into a circular disk.
    art_square = ImageOps.fit(art, (disc_size, disc_size), method=Image.Resampling.LANCZOS)
    rotated = art_square.rotate(angle, resample=Image.Resampling.BICUBIC)

    disc_mask = Image.new("L", (disc_size, disc_size), 0)
    mask_draw = ImageDraw.Draw(disc_mask)
    mask_draw.ellipse((0, 0, disc_size - 1, disc_size - 1), fill=255)
    frame.paste(rotated.convert("RGBA"), (margin, margin), disc_mask)

    draw = ImageDraw.Draw(frame, "RGBA")
    outer = (margin, margin, size - margin - 1, size - margin - 1)
    draw.ellipse(outer, outline=(220, 220, 220, 200), width=1)

    center = size // 2
    label_radius = max(3, size // 16)
    hole_radius = max(1, size // 40)
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


def get_font(size: int = 9) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        try:
            return ImageFont.truetype("arial.ttf", size)
        except OSError:
            return ImageFont.load_default()


def get_text_height(font_size: int = 9) -> int:
    font = get_font(font_size)
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    bbox = draw.textbbox((0, 0), "Ag - Mj", font=font)
    return max(1, bbox[3] - bbox[1])


def render_clock(size: int) -> Image.Image:
    import datetime
    import math
    frame = Image.new("RGB", (size, size), (0, 0, 0))
    draw = ImageDraw.Draw(frame)
    now = datetime.datetime.now()
    time_str = now.strftime("%H:%M")

    font = get_font(max(10, size // 3))
    bbox = draw.textbbox((0, 0), time_str, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    # Text content
    day_str = now.strftime("%a").upper()               # e.g., "FRI"
    time_str = now.strftime("%I:%M %p").lstrip("0")    # e.g., "4:05 PM"
    date_str = now.strftime("%b %d").upper()           # e.g., "OCT 25"
    
    # Make fonts a bit smaller
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
    
    # Draw Day
    day_x = (size - (day_bbox[2] - day_bbox[0])) // 2
    draw.text((day_x, start_y - day_bbox[1]), day_str, fill=(130, 170, 255), font=small_font)
    
    # Draw Time (AM/PM)
    time_y = start_y + day_h + gap
    time_x = (size - (time_bbox[2] - time_bbox[0])) // 2
    draw.text((time_x, time_y - time_bbox[1]), time_str, fill=(255, 255, 255), font=time_font)

    x = (size - text_w) // 2
    y = (size - text_h) // 2 - bbox[1]
    draw.text((x, y), time_str, fill=(255, 255, 255), font=font)
    # Draw Date
    date_y = time_y + time_h + gap
    date_x = (size - (date_bbox[2] - date_bbox[0])) // 2
    draw.text((date_x, date_y - date_bbox[1]), date_str, fill=(180, 180, 180), font=small_font)

    margin = 2
    draw.ellipse((margin, margin, size - margin - 1, size - margin - 1), outline=(80, 80, 100), width=2)
    # Thin outer circle
    margin = 1
    draw.ellipse((margin, margin, size - margin - 1, size - margin - 1), outline=(60, 60, 90), width=1)

    # Sweeping seconds red dot
    second_angle = (now.second / 60.0) * 360 - 90
    rad = math.radians(second_angle)
    cx = size / 2.0
    cy = size / 2.0
    radius = (size - margin * 2) / 2.0
    sx = cx + math.cos(rad) * radius
    sy = cy + math.sin(rad) * radius
    draw.ellipse((sx - 2, sy - 2, sx + 2, sy + 2), fill=(200, 50, 50))
    draw.ellipse((sx - 1.5, sy - 1.5, sx + 1.5, sy + 1.5), fill=(230, 40, 40))

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

    separator = "   -   "
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


def poll_spotify(
    spotify: SpotifyClient,
    state: SharedPlaybackState,
    state_lock: threading.Lock,
    stop_event: threading.Event,
    poll_seconds: float,
) -> None:
    last_status: str | None = None

    while not stop_event.is_set():
        try:
            playback = spotify.get_currently_playing()
            art = playback_art_from_response(playback)

            if art:
                with state_lock:
                    needs_download = art.key != state.art_key or art.image_url != state.image_url

                image = download_image(art.image_url) if needs_download else None

                with state_lock:
                    state.art_key = art.key
                    state.image_url = art.image_url
                    state.is_playing = art.is_playing
                    state.title = art.title
                    state.artist = art.artist
                    if image is not None:
                        state.image = image

                status = f"art found, is_playing={art.is_playing}, title={art.title!r}"
            else:
                with state_lock:
                    state.art_key = None
                    state.image_url = None
                    state.image = None
                    state.is_playing = False
                    state.title = ""
                    state.artist = ""
                status = "no currently playing item"

            if status != last_status:
                print(f"Spotify: {status}", flush=True)
                last_status = status
        except Exception as exc:
            print(f"Spotify poll failed: {exc}", flush=True)

        stop_event.wait(poll_seconds)


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
        display = MockDisplay(args.mock_output)
    else:
        display = MatrixDisplay(args)

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

    playback_state = SharedPlaybackState()
    playback_lock = threading.Lock()
    stop_event = threading.Event()
    poll_thread = threading.Thread(
        target=poll_spotify,
        args=(spotify, playback_state, playback_lock, stop_event, args.poll_seconds),
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

    try:
        while True:
            frame_start = time.monotonic()
            with playback_lock:
                current_art_image = playback_state.image
                current_art_key = playback_state.art_key
                is_playing = playback_state.is_playing
                title = playback_state.title
                artist = playback_state.artist

            now = time.monotonic()
            delta = now - last_frame
            last_frame = now

            display_text = ""
            if not args.no_text:
                if title and artist:
                    display_text = f"{title} - {artist}"
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

            if not is_idle_state and is_playing and current_art_image is not None:
                angle = (angle - 360.0 * (args.rpm / 60.0) * delta) % 360.0

            if not is_idle_state:
                scroll_x += args.text_speed * delta

            if is_idle_state:
                new_frame = render_clock(size)
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
                            old_angle = (old_angle - 360.0 * (args.rpm / 60.0) * delta) % 360.0
                        old_scroll_x += args.text_speed * delta

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
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
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
    text_str = f"{title} - {artist}"
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
    parser.add_argument("--poll-seconds", type=positive_float, default=2.0)
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
    return parser


if __name__ == "__main__":
    run(build_parser().parse_args())