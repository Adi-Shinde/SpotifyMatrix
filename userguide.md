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
2. Make sure no old scripts are running by killing stuck processes:
```bash
sudo pkill -f spotify_matrix.py

```


3. Run the authentication script. **CRITICAL:** Do NOT use `sudo` here. You want your normal user account to own this file. Use the `--no-browser` flag:


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


8. **CLEANUP:** You can now close your browser. You can also securely close Terminal #2 (the network bridge). You will only need Terminal #1 from here on out.

---

## PHASE 2: AUTOMATING THE "PLUG & PLAY" BOOT

*This turns the Pi into a standalone appliance that boots into the script automatically.*

### Step 1: Create the System Service File (In Terminal #1)

1. Ensure you are in Terminal #1.
2. Open a new configuration file in the system directory:
```bash
sudo nano /etc/systemd/system/spotifymatrix.service

```



### Step 2: Paste the Configuration

Copy the exact text below and paste it into the nano editor.
*(Note: This includes the `Environment` line to explicitly define the Python virtual environment paths, uses your optimized hardware flags, and enables the web control panel on port 5000)*

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

3. Press **`Ctrl + O`**, hit **`Enter`** to save, and press **`Ctrl + X`** to exit.

### Step 3: Activate the Automation

Tell the Pi to read your new file and turn it on permanently:

```bash
sudo systemctl daemon-reload
sudo systemctl enable spotifymatrix.service
sudo systemctl start spotifymatrix.service

```

> **Result:** Your matrix should instantly light up. If music is playing, it will spin. If paused, it will show the idle clock. The web control panel is now accessible at `http://matrixspot.local:5000`.
> 
> 

---

## PHASE 3: CONTROLLING FROM YOUR PHONE (WEB CONTROL PANEL)

*No SSH or terminal needed — just use any web browser.*

Once the matrix is running (either via autoboot service or manual run), a **web control panel** is available on your local network. Open this URL on **any device connected to the same WiFi**:

```
http://matrixspot.local:5000
```

**Pro tip:** Bookmark this URL on your phone's home screen for instant access!

### What You Can Do From the Web Panel

| Feature | Description |
|---------|-------------|
| **Display Mode** | Tap to switch between 💿 CD (spinning record), 🎵 Lyrics (synced lyrics), or 🕐 Clock |
| **Brightness** | Drag the slider from 1 to 100. Changes take effect immediately on the matrix. |
| **Spin Speed** | Adjust the RPM of the spinning CD from 1 to 120. |
| **Text Speed** | Control how fast the song title/artist scrolls at the bottom (1-100 px/s). |
| **Poll Rate** | How often the Pi checks Spotify for track changes (1-60 seconds). Default is 5s. |
| **Now Playing** | See the current track title, artist, and album art at the top. |

### Quick Mode Switching URLs

If you prefer direct links (useful for Shortcuts/automation), these also work:

- **CD mode:** `http://matrixspot.local:5000/mode?set=cd`
- **Lyrics mode:** `http://matrixspot.local:5000/mode?set=lyrics`
- **Clock mode:** `http://matrixspot.local:5000/mode?set=clock`

### Lyrics View

When you switch to **Lyrics mode**, the matrix displays synchronized lyrics from the free LRCLIB service:
- The **current lyric line** is shown in bright **Spotify Green** in the center
- The **previous** and **next** lines appear in dim gray above and below
- A tiny **progress bar** at the bottom tracks the song position
- Long lines automatically **scroll horizontally**
- If no synced lyrics exist for a track, it shows "♪ No Lyrics ♪"

Lyrics are fetched automatically when a new song starts — no setup required!

---

## PHASE 4: HOW TO LIVE WITH BOTH WORLDS (DUAL-MODE)

*How to handle the system now that it is automated.*

### Scenario A: Normal Daily Use (Fully Automated)

You are completely finished.

* You can close all terminal windows on your laptop.
* You can unplug the Raspberry Pi from the wall.
* Whenever you plug the Pi into power, it will boot up, wait for Wi-Fi, refresh its own Spotify token, and launch the display script entirely on its own. **No laptop or SSH required.**
* **Control it from your phone** by opening `http://matrixspot.local:5000` in your browser.

### Scenario B: Manual Override (When you want to tinker or update code)

Because the background service (`spotifymatrix.service`) is always running as root to control the LEDs, you **cannot** just SSH in and run the script manually. If you try, the manual script will fight the background script for control of the GPIO pins, causing flickering and crashes.

**Here is the strict order of operations for manual tinkering:**

1. **Open a Terminal & Connect:**
Open PowerShell on your laptop and log in:
```powershell
ssh adi@matrixspot.local

```


2. **STOP the Background Automation:**
You must pause the automatic service to free up the LED panel:
```bash
sudo systemctl stop spotifymatrix.service

```


*(The LED matrix will instantly go dark. The hardware is now yours to command).*
3. **Navigate to your folder:**
```bash
cd ~/Documents/SpotifyMatrix

```


4. **Do Your Manual Work:**
* *Want to update the code?* Run: `git pull`
* *Want to test a new script command?* Run it normally:
```bash
sudo -E .venv/bin/python3 spotify_matrix.py --rows 64 --cols 64 --chain-length 1 --parallel 1 --gpio-slowdown 5 --no-hardware-pulse --hardware-mapping adafruit-hat-pwm --web-port 5000

```


* *(Press `Ctrl + C` to stop your manual test when you are done observing it).*


5. **START the Background Automation Again:**
When you are done testing and want the Pi to go back to being a standalone appliance, turn the service back on:
```bash
sudo systemctl start spotifymatrix.service

```


6. **Disconnect:**
You can now safely close your laptop terminal. The Pi is back in auto-pilot mode.

---

## PHASE 5: THE 6-MONTH RE-AUTHORIZATION (MAINTENANCE)

Spotify's security policy forces "Refresh Tokens" to expire every 6 months. When this happens, your matrix will stop showing music and stay on the idle clock, and the background logs will show an `invalid_grant` error.

To fix this and get another 6 months of automation, you just need to clear the old cache and re-authenticate:

1. **Stop the Background Service:**
   Open PowerShell, SSH into the Pi (`ssh adi@matrixspot.local`), and stop the service:
   `sudo systemctl stop spotifymatrix.service`

2. **Delete the Expired Token:**
   `rm ~/Documents/SpotifyMatrix/.cache/spotify_token.json`

3. **Open the Network Bridge:**
   Open a SECOND PowerShell window on your laptop and run:
   `ssh -L 8888:127.0.0.1:8888 adi@matrixspot.local`

4. **Run the Authenticator:**
   Go back to your FIRST terminal window and run:
   `cd ~/Documents/SpotifyMatrix`
   `.venv/bin/python3 spotify_matrix.py --auth-only --no-browser`

5. **Authorize in Browser:**
   Copy the URL printed in the terminal, paste it into your laptop's browser, and click "Agree".

6. **Restart the Automation:**
   Once the terminal confirms the token is saved, close the browser and the second terminal, and start the service back up:
   `sudo systemctl start spotifymatrix.service`

You are now good for another 6 months!

---

## QUICK REFERENCE COMMANDS

**Check if the automated service is running or failing:**

```bash
sudo systemctl status spotifymatrix.service

```

**Watch the live background logs (to see song titles changing, etc.):**

```bash
sudo journalctl -u spotifymatrix.service -f

```

*(Press `Ctrl + C` to exit the log viewer).*

**Restart the Pi completely:**

```bash
sudo reboot

```

**Open the web control panel:**

```
http://matrixspot.local:5000
```

**Quick mode switch from any browser:**

```
http://matrixspot.local:5000/mode?set=cd
http://matrixspot.local:5000/mode?set=lyrics
http://matrixspot.local:5000/mode?set=clock
```