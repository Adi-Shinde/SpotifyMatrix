# Spotify Matrix — Setup & Usage Guide

Shows the current Spotify album art on a 64x64 RGB matrix as a circular record. The album art is the record surface itself: it is cropped to a disk, spun while Spotify reports playback as active, and left stopped at the current angle when paused.

This uses Spotify's Web API `currently-playing` endpoint, not the browser-only Web Playback SDK. The first run opens Spotify OAuth, then the script stores a refresh token in `.cache/spotify_token.json`.

## Features

- **🎵 Spinning CD View** — Album art as a rotating vinyl record
- **📝 Synchronized Lyrics** — Real-time lyrics from LRCLIB, synced to playback
- **🕐 Clock Mode** — Clean clock face with date and sweeping seconds dot
- **📱 Web Control Panel** — Mobile-friendly dashboard at `http://matrixspot.local:5000`
- **⚡ Live Settings** — Change brightness, spin speed, text speed, and more from your phone

## Files
- `spotify_matrix.py` - Pi runtime script.
- `.env` - local Spotify credentials, ignored by Git.
- `.env.example` - template for recreating local config.
- `requirements.txt` - Python dependencies, excluding the hardware-specific RGB matrix bindings.
- `matrix_control.ps1` - PowerShell control panel for SSH management.

---

## 1. Initial Setup (Raspberry Pi)

First, connect to your Raspberry Pi via SSH, clone the repository, and set up the Python virtual environment:

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

Compiling the low-level RGB matrix C++ bindings will crash a Pi Zero due to memory limits. You must create a 1GB swapfile first:

```bash
sudo dd if=/dev/zero of=/swapfile bs=1M count=1024
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
```

### Install Adafruit RGB Matrix Bindings

Install the hardware bindings using the Adafruit script. Select your Bonnet/HAT, choose **Quality**, and **Reserve a CPU core** when prompted.

```bash
sudo pip3 install adafruit-python-shell --break-system-packages
wget https://github.com/adafruit/Raspberry-Pi-Installer-Scripts/raw/main/rgb-matrix.py
sudo -E env PATH=$PATH python3 rgb-matrix.py
```

*Reboot the Pi when finished.*

---

## 2. Spotify API Setup & Authentication

Log back into the Pi, navigate to the project directory (`cd ~/Documents/SpotifyMatrix`), and create a `.env` file with your Spotify Developer credentials:

```bash
nano .env
```

```env
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
```

### Generate the Token

Because a headless Pi has no web browser, forward the port to your local computer. Run this on your **local computer's terminal** (keep it open in the background):

```bash
ssh -L 8888:127.0.0.1:8888 adi@matrixspot.local
```

Then, run the auth command on the **Raspberry Pi**:

```bash
.venv/bin/python3 spotify_matrix.py --auth-only --no-browser
```

Open the generated URL in your local browser, log in, and authorize. The token will safely cache in `.cache/spotify_token.json`.

---

## 3. Run the Display

To launch the display with optimized, flicker-free hardware settings for the Adafruit Bonnet:

```bash
sudo -E .venv/bin/python3 spotify_matrix.py \
  --rows 64 \
  --cols 64 \
  --chain-length 1 \
  --parallel 1 \
  --gpio-slowdown 5 \
  --no-hardware-pulse \
  --hardware-mapping adafruit-hat-pwm \
  --brightness 60 \
  --web-port 5000
```

---

## 4. Web Control Panel (Control from Your Phone!)

When the matrix is running, a beautiful mobile-friendly control panel is available at:

```
http://matrixspot.local:5000
```

Open this URL on **any device on the same WiFi** — phone, tablet, or laptop.

**Pro tip:** Add this as a bookmark or home screen shortcut on your phone for instant access!

### What You Can Control

| Setting | Range | Description |
|---------|-------|-------------|
| **Display Mode** | CD / Lyrics / Clock | Tap to switch instantly |
| **Brightness** | 1–100 | Slider, immediate effect on the matrix |
| **Spin Speed** | 1–120 RPM | How fast the CD spins |
| **Text Speed** | 1–100 px/s | Scrolling title/artist speed |
| **Poll Rate** | 1–60 seconds | How often Spotify is checked |

### Quick Mode Switch URLs

These direct URLs are useful for phone shortcuts or automation:

- `http://matrixspot.local:5000/mode?set=cd` — Spinning CD
- `http://matrixspot.local:5000/mode?set=lyrics` — Synchronized lyrics
- `http://matrixspot.local:5000/mode?set=clock` — Clock face

### Lyrics View Details

The lyrics view uses the free **LRCLIB API** to fetch time-synced lyrics automatically:
- **Current line** in bright Spotify Green (center)
- **Previous/Next lines** in dim gray (above/below)
- **Progress bar** at the bottom
- Long lines **auto-scroll horizontally**
- No setup needed — lyrics are fetched automatically when a new song plays

---

## 5. Automation (Headless Appliance Mode)

To make the matrix start automatically on boot, create a `systemd` service:

```bash
sudo nano /etc/systemd/system/spotifymatrix.service
```

Paste the following configuration:

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

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable spotifymatrix.service
sudo systemctl start spotifymatrix.service
```

---

## 6. Dual-Mode (Manual Override)

To tinker with the code via SSH later, you MUST pause the background service first to prevent GPIO hardware conflicts:

1. **Stop automation:** `sudo systemctl stop spotifymatrix.service`
2. **Do your work:** `cd ~/Documents/SpotifyMatrix`
3. **Run manual tests:**
```bash
sudo -E .venv/bin/python3 spotify_matrix.py --rows 64 --cols 64 --chain-length 1 --parallel 1 --gpio-slowdown 5 --no-hardware-pulse --hardware-mapping adafruit-hat-pwm --web-port 5000
```
4. **Restart automation when done:** `sudo systemctl start spotifymatrix.service`

---

## 7. The 6-Month Re-Authorization (Maintenance)

Spotify's security policy forces "Refresh Tokens" to expire every 6 months. When this happens, your matrix will stop showing music and stay on the idle clock, and the background logs will show an `invalid_grant` error.

To fix this and get another 6 months of automation:

1. **Stop the Background Service:**
   `sudo systemctl stop spotifymatrix.service`

2. **Delete the Expired Token:**
   `rm ~/Documents/SpotifyMatrix/.cache/spotify_token.json`

3. **Open the Network Bridge** (on your laptop):
   `ssh -L 8888:127.0.0.1:8888 adi@matrixspot.local`

4. **Run the Authenticator** (on the Pi):
   `.venv/bin/python3 spotify_matrix.py --auth-only --no-browser`

5. **Authorize in Browser:**
   Copy the URL, paste it, and click "Agree".

6. **Restart the Automation:**
   `sudo systemctl start spotifymatrix.service`

---

## 8. Changing Brightness

### From the Web Panel (Easiest!)
Open `http://matrixspot.local:5000` and drag the Brightness slider. Changes take effect immediately.

### From the Command Line
When running manually, append `--brightness 60`:
```bash
sudo -E .venv/bin/python3 spotify_matrix.py \
  --rows 64 --cols 64 --chain-length 1 --parallel 1 \
  --gpio-slowdown 5 --no-hardware-pulse \
  --hardware-mapping adafruit-hat-pwm \
  --brightness 60 --web-port 5000
```

### For the Autoboot Service
Edit the service file to include `--brightness 60`:
```bash
sudo nano /etc/systemd/system/spotifymatrix.service
```
Then reload and restart:
```bash
sudo systemctl daemon-reload
sudo systemctl restart spotifymatrix.service
```

Or use the PowerShell control panel (`matrix_control.ps1`) which does this automatically.
