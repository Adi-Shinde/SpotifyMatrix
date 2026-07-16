# Spotify Matrix — Setup & Usage Guide

Shows the current Spotify album art on a 64x64 RGB matrix as a circular record. The album art is the record surface itself: it is cropped to a disk, spun while Spotify reports playback as active, and left stopped at the current angle when paused.

## Features

- **✨ Default Mode** — Smart auto-cycling: CD 10s → Lyrics → Clock on pause
- **🎵 Spinning CD View** — Album art as a rotating vinyl record
- **📝 Synchronized Lyrics** — Smooth vertical scrolling from LRCLIB
- **🕐 Clock Mode** — Clean clock face with date and sweeping seconds
- **📱 Web Control Panel** — Mobile-friendly dashboard at `http://matrixspot.local:5000`
- **📄 Live Logs** — In-memory log viewer at `/logs`
- **⚡ Live Settings** — Brightness, speed, poll rate adjustable from phone
- **🔄 Reset All** — One-tap return to defaults

## Files
- `spotify_matrix.py` - Pi runtime script.
- `.env` - local Spotify credentials, ignored by Git.
- `.env.example` - template for recreating local config.
- `requirements.txt` - Python dependencies.
- `matrix_control.ps1` - PowerShell control panel for SSH management.

---

## 1. Initial Setup (Raspberry Pi)

```bash
mkdir -p ~/Documents
cd ~/Documents
git clone https://github.com/Adi-Shinde/SpotifyMatrix.git
cd SpotifyMatrix

sudo apt update
sudo apt install -y python3-venv wget
python3 -m venv .venv --system-site-packages
source .venv/bin/activate
pip install -r requirements.txt
```

### Memory Fix (Crucial for Pi Zero 2W)

```bash
sudo dd if=/dev/zero of=/swapfile bs=1M count=1024
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
```

### Install Adafruit RGB Matrix Bindings

```bash
sudo pip3 install adafruit-python-shell --break-system-packages
wget https://github.com/adafruit/Raspberry-Pi-Installer-Scripts/raw/main/rgb-matrix.py
sudo -E env PATH=$PATH python3 rgb-matrix.py
```

*Reboot the Pi when finished.*

---

## 2. Spotify API Setup & Authentication

Create a `.env` file:

```env
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
```

Forward the port (on your local computer):
```bash
ssh -L 8888:127.0.0.1:8888 adi@matrixspot.local
```

Then on the Pi:
```bash
.venv/bin/python3 spotify_matrix.py --auth-only --no-browser
```

Open the generated URL in your browser, authorize, and the token caches in `.cache/spotify_token.json`.

---

## 3. Run the Display

```bash
sudo -E .venv/bin/python3 spotify_matrix.py \
  --rows 64 --cols 64 --chain-length 1 --parallel 1 \
  --gpio-slowdown 5 --no-hardware-pulse \
  --hardware-mapping adafruit-hat-pwm \
  --brightness 60 --web-port 5000
```

The display starts in **Default mode**: CD spinning → Lyrics after 10s → Clock when paused.

---

## 4. Web Control Panel (Control from Your Phone!)

```
http://matrixspot.local:5000
```

### Display Modes

| Mode | Behavior |
|------|----------|
| **✨ Default** | Auto-cycles: CD 10s → Lyrics → Clock on pause. Resets on new track. |
| **💿 CD** | Locked to spinning record. Won't auto-switch. |
| **🎵 Lyrics** | Locked to lyrics. Won't auto-switch. |
| **🕐 Clock** | Locked to clock. Won't auto-switch. |

Both manual and auto boot always start in Default mode.

### Settings

| Setting | Range | Description |
|---------|-------|-------------|
| **Brightness** | 1–100 | Immediate effect |
| **Spin Speed** | 1–120 RPM | CD spin rate |
| **Text Speed** | 1–100 px/s | Title scroll speed |
| **Poll Rate** | 1–60 sec | Spotify check interval |

### Reset All
Returns all settings to boot defaults (Default mode, original brightness/speed/poll rate).

### Lyrics
- Smooth **vertical scrolling** (no popping/flashing)
- Current line in Spotify Green, centered
- Previous/next lines dim, fading at edges
- Long lines auto-scroll horizontally
- Progress bar at bottom

### Live Logs
Visit `/logs` for a terminal-style log viewer. Auto mode only logs important events. Manual mode shows verbose ticks.

---

## 5. Automation (Headless Appliance Mode)

```bash
sudo nano /etc/systemd/system/spotifymatrix.service
```

```ini
[Unit]
Description=Spotify LED Matrix Auto-Player
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/home/adi/Documents/SpotifyMatrix
Environment="PATH=/home/adi/Documents/SpotifyMatrix/.venv/bin:/usr/bin"
ExecStartPre=/bin/sleep 10
ExecStart=/home/adi/Documents/SpotifyMatrix/.venv/bin/python3 spotify_matrix.py --rows 64 --cols 64 --chain-length 1 --parallel 1 --gpio-slowdown 5 --no-hardware-pulse --hardware-mapping adafruit-hat-pwm --web-port 5000
Restart=always
RestartSec=10
Nice=-20
CPUSchedulingPolicy=fifo
CPUSchedulingPriority=99

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable spotifymatrix.service
sudo systemctl start spotifymatrix.service
```

---

## 6. Dual-Mode (Manual Override)

1. `sudo systemctl stop spotifymatrix.service`
2. Do your work / run manually
3. `sudo systemctl start spotifymatrix.service`

---

## 7. The 6-Month Re-Authorization

1. `sudo systemctl stop spotifymatrix.service`
2. `rm ~/Documents/SpotifyMatrix/.cache/spotify_token.json`
3. Open SSH tunnel: `ssh -L 8888:127.0.0.1:8888 adi@matrixspot.local`
4. `.venv/bin/python3 spotify_matrix.py --auth-only --no-browser`
5. Copy URL → browser → Agree
6. `sudo systemctl start spotifymatrix.service`

---

## 8. Changing Brightness

**From the Web Panel (Easiest!):** Drag the Brightness slider at `http://matrixspot.local:5000`.

**From CLI:** Append `--brightness 60` to the run command.

**For Autoboot Service:** Edit the service file, then `sudo systemctl daemon-reload && sudo systemctl restart spotifymatrix.service`.

Or use `matrix_control.ps1` which does this automatically.
