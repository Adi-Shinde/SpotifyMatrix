# Spotify Matrix

Shows the current Spotify album art on a 64x64 RGB matrix as a circular record. The album art is the record surface itself: it is cropped to a disk, spun while Spotify reports playback as active, and left stopped at the current angle when paused.

This uses Spotify's Web API `currently-playing` endpoint, not the browser-only Web Playback SDK. The first run opens Spotify OAuth, then the script stores a refresh token in `.cache/spotify_token.json`.

## Features

- **✨ Default Mode** — Smart auto-cycling: CD spinning for 10s → Lyrics → Clock when paused
- **🎵 Spinning CD View** — Album art as a rotating vinyl record with smooth spin-up/down easing
- **📝 Synchronized Lyrics** — Smooth vertically-scrolling lyrics from LRCLIB, synced to playback
- **🕐 Clock Mode** — Clean clock face with date, day, and sweeping seconds dot
- **📱 Web Control Panel** — Mobile-friendly dashboard at `http://<pi-ip>:5000`
- **📄 Live Logs** — In-memory log viewer at `http://<pi-ip>:5000/logs`
- **⚡ Runtime Settings** — Change brightness, spin speed, text speed, polling rate, and display mode without restarting
- **🔄 Reset All** — One-tap reset to boot defaults from the web panel

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

For a non-Pi test that writes one PNG frame instead of using matrix hardware:

```bash
python spotify_matrix.py --mock-output /tmp/spotify-matrix-frame.png --once
```

To verify the album art is what spins on the disk, render four local preview frames:

```bash
python spotify_matrix.py --preview-frames /tmp/spotify-matrix-preview
```

## Display Modes

| Mode | Description | Behavior |
|------|-------------|----------|
| **✨ Default** | Smart auto-cycle (recommended) | CD 10s → Lyrics → Clock when paused. Resets on new track. |
| **💿 CD** | Sticky spinning record | Stays on CD view until you switch. Shows clock after 5s idle. |
| **🎵 Lyrics** | Sticky synced lyrics | Stays on lyrics view until you switch. |
| **🕐 Clock** | Sticky clock face | Stays on clock until you switch. |

The display always starts in **Default** mode on boot (both auto and manual). Selecting CD, Lyrics, or Clock "locks" that mode. Click Default (or Reset All) to return to auto-cycling.

## Web Control Panel

When the script is running, a web control panel is available at:

```
http://<pi-ip>:5000
```

For example: `http://matrixspot.local:5000` or `http://192.168.1.xxx:5000`

Open this URL on **any device on the same WiFi**. From here you can:

- **Switch display modes**: Default ✨, CD 💿, Lyrics 🎵, Clock 🕐
- **Adjust brightness**: 1-100 slider, takes effect immediately
- **Change spin speed**: 1-120 RPM for the CD view
- **Change text speed**: 1-100 px/s for the scrolling title/artist text
- **Change poll rate**: 1-60 seconds between Spotify API calls
- **Reset all settings** to boot defaults
- **View live logs** at `/logs`

## Lyrics View

The lyrics view uses the free [LRCLIB API](https://lrclib.net) to fetch synchronized lyrics. The display shows:

- **Smooth vertical scroll** — lyrics scroll upward like a karaoke teleprompter
- **Current line** in bright Spotify Green, centered
- **Previous/next lines** in dim gray, fading at edges
- **Long lines auto-scroll** horizontally
- **Progress bar** (1px) at the very bottom

## Live Logs

Visit `http://<pi-ip>:5000/logs` for a terminal-style log viewer:

- Color-coded entries (green info, yellow warnings, red errors)
- Auto-scrolls to latest entries
- Clear button to flush the buffer
- In **manual mode** (SSH terminal): verbose every-second ticks are shown
- In **auto mode** (systemd service): only important events are logged (track changes, errors, mode switches)

## Quick Mode URLs

```
http://matrixspot.local:5000/mode?set=default
http://matrixspot.local:5000/mode?set=cd
http://matrixspot.local:5000/mode?set=lyrics
http://matrixspot.local:5000/mode?set=clock
```
