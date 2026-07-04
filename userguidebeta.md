Here is the combined, fully formatted `README.md` file containing all setup, authentication, and automation steps. You can copy this entire block and paste it directly into your GitHub repository.

```markdown
# Spotify Matrix

Shows the current Spotify album art on a 64x64 RGB matrix as a circular record. The album art is the record surface itself: it is cropped to a disk, spun while Spotify reports playback as active, and left stopped at the current angle when paused.

This uses Spotify's Web API `currently-playing` endpoint, not the browser-only Web Playback SDK. The first run opens Spotify OAuth, then the script stores a refresh token in `.cache/spotify_token.json`.

## Files
- `spotify_matrix.py` - Pi runtime script.
- `.env` - local Spotify credentials, ignored by Git.
- `.env.example` - template for recreating local config.
- `requirements.txt` - Python dependencies, excluding the hardware-specific RGB matrix bindings.

---

## 1. Initial Setup (Raspberry Pi)

First, connect to your Raspberry Pi via SSH, clone the repository, and set up the Python virtual environment:

```bash
mkdir -p ~/Documents
cd ~/Documents
git clone [https://github.com/Adi-Shinde/SpotifyMatrix.git](https://github.com/Adi-Shinde/SpotifyMatrix.git)
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
wget [https://github.com/adafruit/Raspberry-Pi-Installer-Scripts/raw/main/rgb-matrix.py](https://github.com/adafruit/Raspberry-Pi-Installer-Scripts/raw/main/rgb-matrix.py)
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
SPOTIFY_REDIRECT_URI=[http://127.0.0.1:8888/callback](http://127.0.0.1:8888/callback)

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
  --brightness 60

```

---

## 4. Automation (Headless Appliance Mode)

To make the matrix start automatically on boot without flickering, create a `systemd` service:

```bash
sudo nano /etc/systemd/system/spotifymatrix.service

```

Paste the following configuration. This includes a boot delay and maximum real-time CPU priority to ensure smooth rendering:

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
ExecStart=/home/adi/Documents/SpotifyMatrix/.venv/bin/python3 spotify_matrix.py --rows 64 --cols 64 --chain-length 1 --parallel 1 --gpio-slowdown 5 --no-hardware-pulse --hardware-mapping adafruit-hat-pwm
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

## 5. Dual-Mode (Manual Override)

To tinker with the code via SSH later, you MUST pause the background service first to prevent GPIO hardware conflicts:

1. **Stop automation:** `sudo systemctl stop spotifymatrix.service`
2. **Do your work:** `cd ~/Documents/SpotifyMatrix`
3. **Run manual tests:** ```bash
sudo -E .venv/bin/python3 spotify_matrix.py --rows 64 --cols 64 --chain-length 1 --parallel 1 --gpio-slowdown 5 --no-hardware-pulse --hardware-mapping adafruit-hat-pwm
```

```
## PHASE 4: THE 6-MONTH RE-AUTHORIZATION (MAINTENANCE)

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

4. **Restart automation when done:** `sudo systemctl start spotifymatrix.service`
Updating Auto Mode (systemd Service)
To make this brightness change permanent for when the Pi boots up, you need to edit the background service file.

Step 1: Open the service file

Bash
sudo nano /etc/systemd/system/spotifymatrix.service
Step 2: Edit the ExecStart line
Find the ExecStart= line and add --brightness 60 to the very end of it. It should look exactly like this:

Ini, TOML
ExecStart=/home/adi/Documents/SpotifyMatrix/.venv/bin/python3 spotify_matrix.py --rows 64 --cols 64 --chain-length 1 --parallel 1 --gpio-slowdown 5 --no-hardware-pulse --hardware-mapping adafruit-hat-pwm --brightness 60
Step 3: Save and exit
Press Ctrl+O, hit Enter to save, then press Ctrl+X to exit nano.

Step 4: Reload and restart the service
Run these commands to apply the changes to the background service:

Bash
sudo systemctl daemon-reload
sudo systemctl restart spotifymatrix.service
Your matrix will now automatically boot up at a much more comfortable 6/10 brightness!
```

```
