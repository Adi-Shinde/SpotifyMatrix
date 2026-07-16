# Spotify Matrix - Complete User Guide

Welcome to the definitive guide for setting up and operating your Spotify Matrix display. 

## Table of Contents

* [Chapter 1: Features & Overview](#chapter-1-features--overview)
* [Chapter 2: First-Time Setup & Authentication](#chapter-2-first-time-setup--authentication)
* [Chapter 3: Automating the "Plug & Play" Boot](#chapter-3-automating-the-plug--play-boot)
* [Chapter 4: Web Control Panel & Settings](#chapter-4-web-control-panel--settings)
* [Chapter 5: Dual-Mode (Manual Override)](#chapter-5-dual-mode-manual-override)
* [Chapter 6: Maintenance (6-Month Re-Auth)](#chapter-6-maintenance-6-month-re-auth)

---

## Chapter 1: Features & Overview

The Spotify Matrix turns your 64x64 RGB LED matrix into a dynamic, standalone music display. 

### Key Features
- **✨ Default Mode (Smart Auto-Cycling)** — When music plays, shows a spinning CD for 10s, then smoothly transitions to synchronized lyrics. When paused, it switches to a clean clock. Resets on new tracks.
- **🎵 Spinning CD View** — Your album art is cropped into a vinyl record and spins while music is playing. Smooth spin-up and spin-down easing.
- **📝 Synchronized Lyrics** — Fetches live, synchronized lyrics using the free LRCLIB API. Choose between **Scroll** mode (smooth vertical teleprompter style) or **Pop** mode (3-line flashing focus). 
- **⚡ Smart Scroll** — Lyrics automatically and intelligently scroll left-to-right based on how fast the artist is singing that specific line.
- **🕐 Clock Mode** — A beautiful clock face with the date, day, and a sweeping seconds dot around the border.
- **📱 Web Control Panel** — Control everything from your phone or PC on the same network. No apps needed.
- **📄 Live Logs** — Debug and view status via an in-memory terminal on the web panel.

---

## Chapter 2: First-Time Setup & Authentication

*Do this EXACTLY ONCE to generate your permanent background key. Spotify requires a human to click "Agree" the very first time.*

### Step 1: Open Terminal #1 (The Main Connection)

1. Open Windows PowerShell on your laptop.
2. Log into the Pi:
   ```powershell
   ssh adi@matrixspot.local
   ```
3. Type your password when prompted.
4. Navigate to your project folder:
   ```bash
   cd ~/Documents/SpotifyMatrix
   ```

### Step 2: Open Terminal #2 (The Network Bridge)

Because the Pi does not have a web browser, we must tunnel the connection to your laptop.

1. Open a **second, completely separate** Windows PowerShell window on your laptop.
2. Run the tunnel command:
   ```powershell
   ssh -L 8888:127.0.0.1:8888 adi@matrixspot.local
   ```
3. Type your password when prompted.
4. **Leave this second window open and running in the background.** Do not type anything else into it.

### Step 3: Generate the Permanent Token (In Terminal #1)

1. Go back to your **first** terminal window.
2. Make sure no old scripts are running:
   ```bash
   sudo pkill -f spotify_matrix.py
   ```
3. Run the authentication script. **CRITICAL:** Do NOT use `sudo` here. Use the `--no-browser` flag:
   ```bash
   .venv/bin/python3 spotify_matrix.py --auth-only --no-browser
   ```

### Step 4: Authorize in Your Browser

1. Terminal #1 will print a link starting with `https://accounts.spotify.com/authorize...`.
2. Highlight and copy that entire link.
3. Open your laptop's web browser (Chrome, Edge, Safari).
4. Paste the link into the address bar and hit Enter.
5. Log into Spotify and click **Agree**.
6. The browser will go blank and say authorization is complete.
7. Look at Terminal #1: It will confirm the token is saved to `.cache/spotify_token.json`.
8. **CLEANUP:** You can now close your browser. Close Terminal #2 (the network bridge). You will only need Terminal #1 from here on out.

---

## Chapter 3: Automating the "Plug & Play" Boot

*This turns the Pi into a standalone appliance that boots into the script automatically.*

### Step 1: Create the System Service File (In Terminal #1)

```bash
sudo nano /etc/systemd/system/spotifymatrix.service
```

### Step 2: Paste the Configuration

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

Press **`Ctrl + O`**, hit **`Enter`** to save, and press **`Ctrl + X`** to exit.

### Step 3: Activate the Automation

```bash
sudo systemctl daemon-reload
sudo systemctl enable spotifymatrix.service
sudo systemctl start spotifymatrix.service
```

> **Result:** Your matrix lights up in **Default mode**. The web control panel is accessible at `http://matrixspot.local:5000`.

---

## Chapter 4: Web Control Panel & Settings

*No SSH or terminal needed — just use any web browser on the same WiFi.*

Navigate to: `http://matrixspot.local:5000`

The control panel is organized into 5 intuitive cards:

### 1. Now Playing
Shows the currently active track name, artist, and playback status (Playing/Paused/Stopped).

### 2. Display Mode
Choose what you want to see on the matrix:
- **✨ Default (Auto-cycle):** The recommended mode. Shows the spinning CD for 10s when a song starts, then switches to lyrics. Reverts to the clock when paused. 
- **💿 CD:** Locks the display to the spinning vinyl record view. Shows clock after 5s idle.
- **🎵 Lyrics:** Locks the display to the synced lyrics view.
- **🕐 Clock:** Locks the display to the time/date view.

*Note: The display always boots into Default mode.*

### 3. 🎵 Lyrics Settings
Customize exactly how you read your lyrics:
- **Style:** 
  - **Scroll Mode:** Smoothly scrolls lines vertically like a teleprompter.
  - **Pop Mode:** A 3-line view where lines pop in (previous line, current line, next line).
- **⚡ Smart Scroll Toggle:** When enabled, long lines of lyrics will automatically scroll horizontally. The scroll speed is proportionally synced to how fast the artist is singing that specific line! Unchecking this reverts to standard back-and-forth ping-pong scrolling.
- **Font Sizes:** Independent sliders to adjust the font size of the **Scroll Mode** (default 9) and **Pop Mode** (default 8) to fit your matrix perfectly.

### 4. ⚙ General Settings
Fine-tune the hardware and software properties:
- **Brightness (1-100):** Adjust the LED intensity instantly.
- **Spin Speed (RPM):** How fast the CD view spins when music plays.
- **Text Speed (px/s):** How fast track titles and artist names scroll on the CD view.
- **Poll Rate (1-60s):** How often the system checks Spotify for new song changes. (Default: 5 seconds).

### 5. Actions
- **Reset All Defaults:** One tap to return the matrix to its original boot state (Default mode, default brightness, etc).
- **View Live Logs:** Opens a terminal-style window in your browser at `/logs` to debug errors and view system states in real-time.

---

## Chapter 5: Dual-Mode (Manual Override)

If you want to manually run the code (to test new changes, debug, or tweak Python code), you must stop the background automation first.

**Strict order of operations:**

1. **Open a Terminal & Connect:**
   ```powershell
   ssh adi@matrixspot.local
   ```
2. **STOP the Background Automation:**
   ```bash
   sudo systemctl stop spotifymatrix.service
   ```
3. **Navigate to your folder:**
   ```bash
   cd ~/Documents/SpotifyMatrix
   ```
4. **Do Your Manual Work (Run script):**
   ```bash
   sudo -E .venv/bin/python3 spotify_matrix.py --rows 64 --cols 64 --chain-length 1 --parallel 1 --gpio-slowdown 5 --no-hardware-pulse --hardware-mapping adafruit-hat-pwm --web-port 5000
   ```
5. **START the Background Automation Again (When finished):**
   ```bash
   sudo systemctl start spotifymatrix.service
   ```

---

## Chapter 6: Maintenance (6-Month Re-Auth)

Spotify's security policy forces "Refresh Tokens" to expire every 6 months. When this happens, your matrix will stop showing music and stay on the clock, and the web logs will show an `invalid_grant` error.

1. SSH into the Pi: `ssh adi@matrixspot.local`
2. Stop the service: `sudo systemctl stop spotifymatrix.service`
3. Delete the old token: `rm ~/Documents/SpotifyMatrix/.cache/spotify_token.json`
4. Open your SSH tunnel on your laptop (Terminal #2): `ssh -L 8888:127.0.0.1:8888 adi@matrixspot.local`
5. Run auth on the Pi: `.venv/bin/python3 spotify_matrix.py --auth-only --no-browser`
6. Copy URL → paste into laptop browser → click Agree
7. Start the service again: `sudo systemctl start spotifymatrix.service`
