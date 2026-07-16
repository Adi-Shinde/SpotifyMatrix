# Spotify Matrix

Shows the current Spotify album art on a 64x64 RGB matrix as a circular record. The album art is the record surface itself: it is cropped to a disk, spun while Spotify reports playback as active, and left stopped at the current angle when paused.

This uses Spotify's Web API `currently-playing` endpoint, not the browser-only Web Playback SDK. The first run opens Spotify OAuth, then the script stores a refresh token in `.cache/spotify_token.json`.

## Features

- **🎵 Spinning CD View** — Album art as a rotating vinyl record with smooth spin-up/down easing
- **📝 Synchronized Lyrics** — Real-time lyrics display fetched from LRCLIB, synced to your playback position
- **🕐 Clock Mode** — Clean clock face with date, day, and sweeping seconds dot
- **📱 Web Control Panel** — Full mobile-friendly dashboard at `http://<pi-ip>:5000` to control everything from your phone
- **⚡ Runtime Settings** — Change brightness, spin speed, text speed, polling rate, and display mode without restarting

## Files

- `spotify_matrix.py` - Pi runtime script.
- `.env` - local Spotify credentials, ignored by Git.
- `.env.example` - template for recreating local config.
- `requirements.txt` - Python dependencies, excluding the hardware-specific RGB matrix bindings.
- `matrix_control.ps1` - PowerShell control panel for SSH management.

## Raspberry Pi setup

Install the RGB matrix Python bindings from the `hzeller/rpi-rgb-led-matrix` project for your HAT/wiring, then install this project's dependencies:

```bash
python3 -m venv .venv --system-site-packages
source .venv/bin/activate
pip install -r requirements.txt
```

The `--system-site-packages` flag is useful if the `rgbmatrix` bindings were installed system-wide.

This install sometimes crashes the raspberry pi zero, I had to do some fancy workarounds. Might be easier to use a pi with more memory!

## Spotify setup

In the Spotify developer dashboard, make sure this redirect URI is allowlisted exactly:

```text
http://127.0.0.1:8888/callback
```

For a headless Pi, forward the callback port from your computer:

```bash
ssh -L 8888:127.0.0.1:8888 pi@raspberrypi.local
```

Then run the script on the Pi and open the printed authorization URL in your local browser.

## Run

This is the working command to run the script on your raspberry pi:

```bash
sudo -E .venv/bin/python spotify_matrix.py \
  --rows 64 \
  --cols 64 \
  --chain-length 1 \
  --parallel 1 \
  --gpio-slowdown 4 \
  --no-hardware-pulse \
  --hardware-mapping adafruit-hat \
  --web-port 5000
```

Useful hardware options:

```bash
sudo -E .venv/bin/python spotify_matrix.py \
  --hardware-mapping regular \
  --gpio-slowdown 2 \
  --brightness 65 \
  --web-port 5000
```

For a non-Pi test that writes one PNG frame instead of using matrix hardware:

```bash
python spotify_matrix.py --mock-output /tmp/spotify-matrix-frame.png --once
```

To verify the album art is what spins on the disk, render four local preview frames:

```bash
python spotify_matrix.py --preview-frames /tmp/spotify-matrix-preview
```

## Web Control Panel

When the script is running, a web control panel is available at:

```
http://<pi-ip>:5000
```

For example: `http://matrixspot.local:5000` or `http://192.168.1.xxx:5000`

Open this URL on **any device on the same WiFi** — your phone, tablet, or laptop. From here you can:

- **Switch display modes**: CD (spinning record), Lyrics (synchronized lyrics), Clock
- **Adjust brightness**: 1-100 slider, takes effect immediately
- **Change spin speed**: 1-120 RPM for the CD view
- **Change text speed**: 1-100 px/s for the scrolling title/artist text
- **Change poll rate**: 1-60 seconds between Spotify API calls
- **See current track info**: Title, artist, album art, play/pause status

To disable the web panel, pass `--web-port 0`.

## Lyrics View

The lyrics view uses the free [LRCLIB API](https://lrclib.net) to fetch synchronized lyrics. When a new track starts playing, lyrics are automatically fetched in the background. The display shows:

- **Previous line** (dim gray) at the top
- **Current line** (Spotify green) in the center
- **Next line** (dim gray) at the bottom
- **Progress bar** (1px) at the very bottom

Long lyrics lines automatically scroll horizontally. If no synced lyrics are available for a track, a "♪ No Lyrics ♪" message is shown.

The time sync uses local monotonic clock interpolation between Spotify API polls (every 5 seconds by default), so lyrics stay accurately synced without hammering the API.

## Display Modes

| Mode | Description | How to Activate |
|------|-------------|-----------------|
| **CD** | Spinning album art record with scrolling title | Default mode, or set via web panel |
| **Lyrics** | 3-line synchronized lyrics display | Set via web panel or `http://<pi-ip>:5000/mode?set=lyrics` |
| **Clock** | Full clock face with date and time | Set via web panel or `http://<pi-ip>:5000/mode?set=clock` |

## New CLI Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--web-port` | `5000` | Port for the web control panel (0 to disable) |

All existing flags (`--brightness`, `--rpm`, `--text-speed`, etc.) still work and set the initial values. The web panel can override them at runtime.
