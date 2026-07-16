# SPOTIFY MATRIX - COMPLETE USER GUIDE

This guide explains how to operate your Spotify Matrix display. It covers the mandatory first-time authentication, how to set up the automated "Plug & Play" background service, and how to safely override that service to manually tinker with the code.

## UNDERSTANDING HOW IT WORKS

* **The "One-Time" Rule:** You cannot skip using a web browser the very first time. Spotify requires a human to click "Agree." Once you do this, the Python script (`spotify_matrix.py`) saves a secret `refresh_token` inside the hidden `.cache/spotify_token.json` file.

* **The "Forever" Automation:** From that moment on, your Raspberry Pi will use that saved token to silently re-authenticate itself in the background over the internet every time it boots up. You will never need to open a terminal or a browser again just to use it.

---

## PHASE 1: FIRST-TIME SETUP & AUTHENTICATION

*Do this EXACTLY ONCE to generate your permanent background key.*

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

## PHASE 2: AUTOMATING THE "PLUG & PLAY" BOOT

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
ExecStart=/home/adi/Documents/SpotifyMatrix/.venv/bin/python3 spotify_matrix.py --rows 64 --cols 64 --chain-length 1 --parallel 1 --gpio-slowdown 5 --no-hardware-pulse --hardware-mapping adafruit-hat-pwm --web-port 5000
Restart=always
RestartSec=10

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

> **Result:** Your matrix lights up in **Default mode**: when music plays it shows the spinning CD for 10 seconds, then smoothly transitions to lyrics. When paused, it shows the clock. The web control panel is accessible at `http://matrixspot.local:5000`.

---

## PHASE 3: CONTROLLING FROM YOUR PHONE (WEB CONTROL PANEL)

*No SSH or terminal needed — just use any web browser.*

Once the matrix is running (either via autoboot service or manual run), open this URL on **any device connected to the same WiFi**:

```
http://matrixspot.local:5000
```

**Pro tip:** Bookmark this URL on your phone's home screen for instant access!

### Display Modes

| Mode | Button | Behavior |
|------|--------|----------|
| **Default** | ✨ Default | Auto-cycles: CD 10s → Lyrics → Clock on pause. The recommended mode. |
| **CD** | 💿 CD | Locks to spinning record view. Won't change automatically. |
| **Lyrics** | 🎵 Lyrics | Locks to lyrics view. Won't change automatically. |
| **Clock** | 🕐 Clock | Locks to clock view. Won't change automatically. |

**Default mode** is the smart auto-cycling mode:
- Song starts playing → CD spinning for 10 seconds
- After 10 seconds → automatically switches to Lyrics
- New song starts → back to CD for 10 seconds → then Lyrics again
- Music paused → immediately shows Clock
- Music resumes → CD for 10 seconds → then Lyrics

**Sticky modes** (CD, Lyrics, Clock) stay locked on that mode no matter what. To get back to auto-cycling, tap **Default** or use **Reset All**.

### Settings

| Setting | Range | Description |
|---------|-------|-------------|
| **Brightness** | 1–100 | Immediate effect on the matrix |
| **Spin Speed** | 1–120 RPM | How fast the CD spins |
| **Text Speed** | 1–100 px/s | Scrolling title/artist speed |
| **Poll Rate** | 1–60 seconds | How often Spotify is checked |

### Reset All

The **Reset All** button returns everything to boot defaults:
- Mode → Default
- Brightness → original startup value
- Spin Speed → original startup value
- Text Speed → original startup value
- Poll Rate → 5 seconds

### Live Logs

Tap **View Logs** to open the log viewer at `/logs`:
- Terminal-style dark page with color-coded entries
- Auto-scrolls to latest
- Clear button to flush the buffer
- In **auto mode** (systemd): only important events (track changes, errors)
- In **manual mode** (SSH): verbose every-second status ticks

### Lyrics View

The lyrics view displays synchronized lyrics from the free LRCLIB service:
- Lyrics **scroll vertically** like a karaoke teleprompter (no popping/flashing)
- The **current line** is shown in bright **Spotify Green** in the center
- Previous and next lines appear in dim gray, fading at the top/bottom edges
- Long lines **auto-scroll horizontally**
- A tiny **progress bar** at the bottom tracks the song position
- If no synced lyrics exist for a track, it shows "♪ No Lyrics ♪"

### Quick Mode URLs

These direct URLs work for phone shortcuts or automation:
- `http://matrixspot.local:5000/mode?set=default`
- `http://matrixspot.local:5000/mode?set=cd`
- `http://matrixspot.local:5000/mode?set=lyrics`
- `http://matrixspot.local:5000/mode?set=clock`

---

## PHASE 4: HOW TO LIVE WITH BOTH WORLDS (DUAL-MODE)

### Scenario A: Normal Daily Use (Fully Automated)

You are completely finished.

* Close all terminal windows on your laptop.
* Whenever you plug the Pi into power, it boots up, connects to WiFi, refreshes its Spotify token, and launches the display — all on its own.
* **Control it from your phone** by opening `http://matrixspot.local:5000`.
* It always starts in **Default** mode (auto-cycling).

### Scenario B: Manual Override (When you want to tinker or update code)

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

4. **Do Your Manual Work:**
```bash
sudo -E .venv/bin/python3 spotify_matrix.py --rows 64 --cols 64 --chain-length 1 --parallel 1 --gpio-slowdown 5 --no-hardware-pulse --hardware-mapping adafruit-hat-pwm --web-port 5000
```

5. **START the Background Automation Again:**
```bash
sudo systemctl start spotifymatrix.service
```

---

## PHASE 5: THE 6-MONTH RE-AUTHORIZATION (MAINTENANCE)

Spotify's security policy forces "Refresh Tokens" to expire every 6 months. When this happens, your matrix will stop showing music and stay on the clock, and the background logs will show an `invalid_grant` error.

1. `sudo systemctl stop spotifymatrix.service`
2. `rm ~/Documents/SpotifyMatrix/.cache/spotify_token.json`
3. Open SSH tunnel: `ssh -L 8888:127.0.0.1:8888 adi@matrixspot.local`
4. Run auth: `.venv/bin/python3 spotify_matrix.py --auth-only --no-browser`
5. Copy URL → browser → click Agree
6. `sudo systemctl start spotifymatrix.service`

---

## QUICK REFERENCE

| Command | Description |
|---------|-------------|
| `sudo systemctl status spotifymatrix.service` | Check if running |
| `sudo journalctl -u spotifymatrix.service -f` | Watch live logs |
| `sudo reboot` | Restart Pi |
| `http://matrixspot.local:5000` | Web control panel |
| `http://matrixspot.local:5000/logs` | Live log viewer |
| `http://matrixspot.local:5000/mode?set=default` | Reset to default mode |