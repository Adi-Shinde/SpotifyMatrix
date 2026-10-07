# Run SpotifyMatrix directly on the Raspberry Pi — no SSH required

Updated **2026-10-07**, against the current application, dependency list, and
`spotifymatrix.service.template`, including the saved-settings change in
`b1b9f6d`. This is a complete local-terminal installation guide for your
**Raspberry Pi Zero 2 W**, **64×64 RGB matrix**, and **Adafruit HAT/Bonnet**.

You use the monitor connected to the Pi by HDMI, the Pi's Terminal, and the
Pi's browser. The laptop, SSH keys, SSH tunnels, and `matrix_control.ps1` are
not needed. **HDMI carries the picture; it does not install or run anything.**
The program runs on the Pi and will keep running without HDMI once its service
is installed and enabled.

This guide assumes your SD card is already flashed with **Raspberry Pi OS
with Desktop**, as shown in your screenshots. Keep that installation; there
is no need to format it again. Commands below target Raspberry Pi OS
Bookworm/Trixie; the library currently requires Python 3.11 or newer.

**Verification boundary:** the instructions were checked against repository
code and current upstream documentation. They have not been executed on your
newly flashed Pi in this session. Complete the checks below to verify that
particular installation. Earlier successful installations do not prove this
card is set up yet.

## Route through the guide

1. [Connect everything and open Terminal](#1-connect-everything-and-open-terminal)
2. [Check the Pi, storage, Wi-Fi, and time](#2-check-the-pi-storage-wi-fi-and-time)
3. [Add persistent swap](#3-add-persistent-swap-before-the-upgrade-or-build)
4. [Update the OS and install tools](#4-update-the-os-and-install-tools)
5. [Get the project and install Python dependencies](#5-get-the-project-and-install-python-dependencies)
6. [Build the matrix library](#6-build-the-led-matrix-library-in-the-same-virtual-environment)
7. [Configure audio, Bluetooth, and CPU core 3](#7-configure-audio-bluetooth-and-cpu-core-3)
8. [Set up the Spotify app and credentials](#8-set-up-the-spotify-app-and-credentials)
9. [Authorize Spotify in the Pi browser](#9-authorize-spotify-in-the-pis-own-browser)
10. [Test the physical matrix manually](#10-test-the-physical-matrix-manually)
11. [Install automatic startup](#11-install-and-enable-automatic-startup)
12. [Open the website and save settings](#12-open-the-website-and-save-your-settings)
13. [Verify everything after a reboot](#13-verify-everything-after-a-reboot)
14. [Shut down, remove HDMI, and test a power-on](#14-shut-down-remove-hdmi-and-test-a-power-on)
15. [Commands for normal use and updates](#15-commands-for-normal-use-and-updates)
16. [Troubleshooting and recovery](#16-troubleshooting-and-recovery)

Do sections 1–14 for the initial setup. Later, use section 15. A checklist at
the end lets you track completion.

## 1. Connect everything and open Terminal

### Hardware

1. Before changing the HAT, ribbon cable, or panel wiring, shut down the Pi
   and disconnect power to both the Pi and the matrix.
2. Leave the flashed SD card in the Pi.
3. Check that the HAT/Bonnet is seated correctly on the GPIO header.
4. Check that its ribbon cable goes to the matrix's **input** connector and
   that the panel has its intended power supply. HDMI does not power the panel.
5. Connect the Pi's mini-HDMI output to the monitor and select that HDMI input.
6. Connect a USB keyboard and mouse through the Pi's USB data/OTG port and a
   suitable hub if needed. The separate **PWR IN** port is for Pi power.
7. Reconnect the intended panel/Pi power supplies. Wait for the desktop.
8. Log in with your Pi account if asked. Your previous screenshot showed
   username `adi` and hostname `matrixspot`.

This repo's service uses `adafruit-hat-pwm`, intended for the Adafruit PWM
hardware modification. A 64×64 panel may also require the board's Address E
configuration. **The Address E bridge and the PWM modification are different
things.** A solder bridge does not disable Linux audio. Use the board's
[Adafruit Bonnet instructions](https://learn.adafruit.com/adafruit-rgb-matrix-bonnet-for-raspberry-pi?view=all)
to identify the modification already present; do not bridge extra pads based
only on this guide. Keep the repo's existing mapping for the previously working
hardware; if the PWM modification is absent, use `adafruit-hat` instead in both
the manual command and installed service.

### Open Terminal

1. Click the Terminal icon in the desktop panel, if present.
2. Otherwise click the Raspberry Pi menu → **Accessories → Terminal**.
3. With a physical keyboard, **Ctrl+Alt+T** is another way to open it.
4. You should see a prompt similar to `adi@matrixspot:~ $`.

If you have only a mouse, use **Raspberry Pi menu → Preferences → Control
Centre → Display → On-screen keyboard → Enabled always**. On older images the
settings window may be called Raspberry Pi Configuration. If asked to authorize
the change, enter your **Pi login password**, not your Spotify password. A USB
keyboard is easier for the longer setup blocks below. See the official
[onscreen-keyboard instructions](https://www.raspberrypi.com/documentation/computers/configuration.html#configure-onscreen-keyboard).

### How to enter commands

- Everything labelled `bash` below runs in **Terminal on the Pi**.
- Do not type the prompt (`adi@matrixspot:~ $`) or the surrounding backticks.
- Copy a command/block from this guide in the **Pi browser**, then paste into
  Terminal with **Ctrl+Shift+V**, or Terminal's **Edit → Paste** menu. Normal
  **Ctrl+C** in Terminal interrupts a program; use **Ctrl+Shift+C** to copy.
- Run one block at a time. Wait for the prompt to return and read its output.
  Stop at an error and use section 16; do not continue through failed steps.
- A block beginning `python3 - <<'PY'` must be pasted in full, including its
  final `PY` on a line by itself. A `>` prompt while entering it means Terminal
  is waiting for the rest of the block, not that the Pi has frozen.
- In a command continued with `\`, that character must be the last character
  on its line. Copy the entire block, including all continued lines.
- `sudo` asks for the **Pi login password** when needed. It displays no letters
  or stars while you type the password. Type it, then press Enter.
- Do not use `sudo git`, `sudo pip`, or a root desktop session for this setup.
  Specific system/hardware commands below use `sudo` where required.

You can read [this guide on GitHub](https://github.com/Adi-Shinde/SpotifyMatrix/blob/main/RUN_ON_PI_LOCAL.md)
in the Pi browser, or open the Markdown file locally. Opening the browser from
the Pi menu → **Internet → Chromium/Firefox** is sufficient.

## 2. Check the Pi, storage, Wi-Fi, and time

Run:

```bash
whoami
hostname
printf '\n'
cat /proc/device-tree/model
printf '\n'
cat /etc/os-release
python3 --version
getconf _NPROCESSORS_CONF
df -h /
lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINTS
```

Expected: your login user, `matrixspot` if you kept that hostname, a Pi Zero 2 W,
Python **3.11+**, and **4** configured CPUs. Commands later derive the home path
from `$HOME`, so they also work for another normal username.

A 32 GB card normally has a small FAT boot partition and a much larger Linux
root partition. The root filesystem should use most of the card (roughly
29 GiB total before accounting for the boot partition/filesystem). A roughly
504 MB `bootfs` partition does **not** mean the rest of the card is missing.
If `/` itself is unexpectedly tiny, use `sudo raspi-config` → **Advanced
Options → Expand Filesystem**, finish, and reboot before continuing. Raspberry
Pi OS normally expands it at first boot; see
[filesystem expansion](https://www.raspberrypi.com/documentation/computers/configuration.html#expand-the-file-system).

### Connect to Wi-Fi on the Pi

1. Click the network/Wi-Fi icon in the desktop panel.
2. Select your **2.4 GHz** network. The Zero 2 W cannot join a 5 GHz-only SSID.
3. Your intended SSID from this conversation is **`Cloudwifi-11C`**. Select the
   name actually listed by the Pi; do not add a space or change its spelling.
4. Enter a Wi-Fi password only if the network requests one. The earlier laptop
   check identified this network as open; verify the current connection below
   rather than treating that historical result as permanent.
5. If the network requires a portal, open the Pi browser and complete its login.
6. If Wi-Fi is unavailable, set the actual WLAN country using
   `sudo raspi-config` → **Localisation Options → WLAN Country**.

Check the active connection:

```bash
nmcli -f IN-USE,SSID,CHAN,SECURITY device wifi list
nmcli -f GENERAL.STATE,GENERAL.CONNECTION,IP4.ADDRESS device show wlan0
ip route
hostname -I
```

The active SSID has `*` in the first column. `--` under SECURITY means open;
WPA/WPA2/WPA3 indicates a secured network. Record the Pi's current IPv4 address.
`10.10.74.130` was its earlier address, but DHCP may assign another.

Check date/time and connectivity:

```bash
date
timedatectl status
curl -I --max-time 20 https://github.com
curl -I --max-time 20 https://accounts.spotify.com
```

If `curl` is not installed yet, test those sites in the Pi browser and install
it in section 4. A real HTTP response shows that the host was reached; a DNS
failure, timeout, or TLS/date error needs fixing before installation. If time
is wrong:

```bash
sudo timedatectl set-ntp true
sudo raspi-config
```

In `raspi-config`, select **Localisation Options → Timezone**, choose your
actual region/city, then **Finish**. The display's clock uses the Pi's time.

The Pi can reach Spotify over the internet even if this shared Wi-Fi blocks
connections between devices. Local setup bypasses the SSH problem. Phone
access to the Pi's website still needs a network that permits device-to-device
traffic; section 12 explains that separately.

## 3. Add persistent swap BEFORE the upgrade or build

The Zero 2 W has 512 MB physical RAM. Disk-backed **swap** uses some SD-card
space when RAM fills; it does not turn the card into faster physical RAM.
Leave any existing zram enabled. Its compressed pages still use RAM, but can
reduce memory pressure; a disk swapfile adds a separate fallback for builds.

Check first:

```bash
free -h
swapon --show
df -h /
```

Keep several GB free for packages and compilation. The following block creates
a 1 GiB swapfile **only if `/swapfile` does not already exist**, activates it,
and adds one boot entry. It deliberately stops rather than overwriting an
existing file that cannot be activated.

```bash
(
set -eu
if swapon --noheadings --raw --show=NAME | grep -Fxq /swapfile; then
    echo '/swapfile is already active; keeping it.'
elif [ -e /swapfile ]; then
    echo 'Existing /swapfile found; activating without overwriting it.'
    sudo swapon /swapfile
else
    sudo dd if=/dev/zero of=/swapfile bs=1M count=1024 status=progress
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
fi
if ! grep -Eq '^[[:space:]]*/swapfile[[:space:]]' /etc/fstab; then
    printf '/swapfile none swap sw 0 0\n' | sudo tee -a /etc/fstab
fi
swapon --show
free -h
)
```

Expected: an active `/swapfile` of about **1G**, plus zram if the OS supplies
it. Total swap varies; it need not be exactly 1.4 GiB. If the block fails, stop.
Do not run `mkswap` or `dd` on an already active swapfile. Do not assume
`dphys-swapfile` is installed on this OS.

## 4. Update the OS and install tools

Run separately, waiting for each command to finish:

```bash
sudo apt update
```

```bash
sudo apt full-upgrade -y
```

```bash
sudo apt install -y git build-essential python3-dev python3-venv python3-pip curl nano fonts-dejavu-core
```

If a package asks about replacing a configuration you previously edited and
you are unsure, retain the currently installed version and note the prompt.
The desktop and browser use substantial RAM, so close unused browser tabs
during the upgrade/build. These steps can take a long time on a Zero 2 W.

If it appears idle, open a **second local Terminal window** and inspect:

```bash
ps -eo pid,etime,pcpu,pmem,stat,args | grep -E '[a]pt|[d]pkg|[c]make|[n]inja|[c]c1plus'
free -h
```

These show activity but do not prove a process is healthy. Do not start a
second upgrade or delete apt lock files while the first is running. If the
upgrade was interrupted and no package operation remains active, repair with:

```bash
sudo dpkg --configure -a
sudo apt --fix-broken install
```

After a successful upgrade/install:

```bash
sudo reboot
```

Wait for the desktop, log in if necessary, and **open Terminal again**. Normal
Raspberry Pi OS upgrades do not require you to reflash. If the image has no
browser installed, install `chromium` with apt now and launch it from the
desktop menu; authorization later needs a browser on this Pi.

## 5. Get the project and install Python dependencies

### Fresh clone

```bash
mkdir -p "$HOME/Documents"
cd "$HOME/Documents"
git clone https://github.com/Adi-Shinde/SpotifyMatrix.git
cd "$HOME/Documents/SpotifyMatrix"
git log -1 --oneline
```

### If the directory already exists

Do not clone over it or delete it. Run:

```bash
cd "$HOME/Documents/SpotifyMatrix"
git remote -v
git status --short
git pull --ff-only origin main
```

The remote should be `https://github.com/Adi-Shinde/SpotifyMatrix.git` (or its
equivalent SSH URL). If local changes prevent the pull, preserve them and
resolve the message; do not use `reset --hard`. The public repository can be
cloned/pulled over HTTPS without a GitHub login. If an old service is already
running, stop it before updating code, as described in section 15.

### Create the virtual environment

From the project directory:

```bash
python3 -m venv .venv
```

Wait for the prompt; it can be silent for a while. Then:

```bash
.venv/bin/python3 -m pip install --upgrade pip
.venv/bin/python3 -m pip install -r requirements.txt
.venv/bin/python3 -c "from PIL import Image; from dotenv import load_dotenv; print('Python dependencies OK')"
```

Expected: **Python dependencies OK**. Using the explicit `.venv/bin/python3`
path avoids accidentally installing packages into system Python. Activating
the venv with `source .venv/bin/activate` is optional; these commands do not
depend on activation surviving a reboot or a new Terminal window.

## 6. Build the LED matrix library in the SAME virtual environment

`rgbmatrix` is not in `requirements.txt`. Build it from the upstream source
and install it into this project's venv:

```bash
cd "$HOME/Documents"
git clone https://github.com/hzeller/rpi-rgb-led-matrix.git
cd "$HOME/Documents/rpi-rgb-led-matrix"
```

If that source directory already exists, use `cd` into it, inspect
`git remote -v` and `git status --short`, and use `git pull --ff-only` if it has
no local work to preserve. Do not continue from an unrelated directory.

Run:

```bash
CMAKE_BUILD_PARALLEL_LEVEL=1 "$HOME/Documents/SpotifyMatrix/.venv/bin/python3" -m pip install .
```

This uses one compilation job to reduce peak RAM use. Wait for completion;
compiling on this board can take tens of minutes. Keep power connected and do
not interrupt it simply because output pauses. The current upstream uses a
root-level `pyproject.toml`; old `make build-python` / `make install-python`
instructions do not apply. Sources:
[upstream Python build](https://github.com/hzeller/rpi-rgb-led-matrix/blob/master/pyproject.toml),
[CMake parallel build setting](https://cmake.org/cmake/help/latest/envvar/CMAKE_BUILD_PARALLEL_LEVEL.html).

Verify the actual venv that the service will use:

```bash
"$HOME/Documents/SpotifyMatrix/.venv/bin/python3" -c "from rgbmatrix import RGBMatrix, RGBMatrixOptions; print('rgbmatrix OK')"
```

Do not continue until it prints **rgbmatrix OK**. Then:

```bash
cd "$HOME/Documents/SpotifyMatrix"
.venv/bin/python3 spotify_matrix.py --help
```

Confirm the help includes `--auth-only`, `--web-port`, and
`--prefer-saved-settings`. If the last flag is absent, the project checkout
predates the saved-settings update and needs updating.

## 7. Configure audio, Bluetooth, and CPU core 3

The setup uses **CPU core 3**, the fourth core (numbering starts at 0), for the
library's display-refresh thread. Keep the other threads available to cores
0–2. **Do not add `CPUAffinity=3` or run the whole application with
`taskset -c 3`.** The library itself pins its refresh thread; constraining the
whole process makes its renderer, web server, and Spotify poller compete there.
The current service template correctly has no `CPUAffinity=` setting. See
[upstream refresh-thread assignment](https://github.com/hzeller/rpi-rgb-led-matrix/blob/master/lib/led-matrix.cc).

### Back up and edit the boot files safely

The following local Python block finds `/boot/firmware` (or legacy `/boot`),
requires a four-core board, validates the existing kernel command line, and
makes first-run backups. It writes `isolcpus=3` on the same line as the existing
arguments, retains `root=`, and appends an explicit `[all]` audio/Bluetooth
section. It refuses a command line with multiple nonempty lines.

**Copy this entire block into Terminal in one paste:**

```bash
sudo python3 - <<'PY'
from pathlib import Path
import os
import re
import shutil

boot = Path('/boot/firmware')
if not (boot / 'cmdline.txt').is_file():
    boot = Path('/boot')
cmd = boot / 'cmdline.txt'
config = boot / 'config.txt'
if not cmd.is_file() or not config.is_file():
    raise SystemExit('STOP: boot files not found; no changes made.')
if (os.cpu_count() or 0) < 4:
    raise SystemExit('STOP: expected at least four CPUs; no changes made.')

lines = [line.strip() for line in cmd.read_text().splitlines() if line.strip()]
if len(lines) != 1:
    raise SystemExit('STOP: cmdline.txt must have one nonempty line; no changes made.')
args = lines[0].split()
if not any(arg.startswith('root=') and len(arg) > 5 for arg in args):
    raise SystemExit('STOP: root= is missing; no changes made.')
args = [arg for arg in args if not arg.startswith('isolcpus=')]
new_cmd = ' '.join(args + ['isolcpus=3']) + '\n'

old_config = config.read_text()
new_config = re.sub(r'(?m)^\s*dtparam=audio=on\s*$',
                    'dtparam=audio=off', old_config)
marker = '# SpotifyMatrix local setup: audio and Bluetooth'
if marker not in new_config:
    new_config = new_config.rstrip() + (
        '\n\n[all]\n' + marker + '\n'
        'dtparam=audio=off\n'
        'dtoverlay=disable-bt\n'
    )

for path in (cmd, config):
    backup = path.with_name(path.name + '.matrix-local.bak')
    if not backup.exists():
        shutil.copy2(path, backup)
    print('Backup:', backup)

for path, content in ((config, new_config), (cmd, new_cmd)):
    # Preserve the existing FAT directory entry rather than replacing the file.
    with path.open('w', encoding='utf-8', newline='\n') as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
os.sync()
print('Boot directory:', boot)
print('Kernel command line:', cmd.read_text().strip())
print('Saved audio=off, disable-bt, and isolcpus=3. Reboot required.')
PY
```

Expected: backup paths, a single kernel command line containing the original
`root=...` and `isolcpus=3`, and the final **Reboot required** message. If it
prints `STOP` or a traceback, do not reboot until you understand the problem.
The backup names in this guide are **`.matrix-local.bak`**, distinct from the
older guide's `.matrixbak` files.

Disable the Bluetooth services if installed:

```bash
sudo systemctl disable --now bluetooth.service
sudo systemctl disable --now hciuart.service
```

If either unit says it does not exist, record that it is absent; it does not
need disabling. This also disconnects Bluetooth keyboards/mice, so use USB
input for this setup. Do not remove Wi-Fi packages.

Check the edited files before reboot:

```bash
if [ -f /boot/firmware/cmdline.txt ]; then BOOT=/boot/firmware; else BOOT=/boot; fi
cat "$BOOT/cmdline.txt"
tail -n 8 "$BOOT/config.txt"
sync
sudo reboot
```

Wait for the desktop and **open Terminal again**. Check:

```bash
cat /sys/devices/system/cpu/isolated
cat /proc/cmdline
systemctl is-active bluetooth.service
lsmod | grep '^snd_bcm2835' || true
swapon --show
```

Expected: isolated CPU **3**, `isolcpus=3` in the running kernel arguments,
Bluetooth inactive/absent, `/swapfile` still active, and **no** `snd_bcm2835`
line. HDMI/USB audio modules have other names; do not remove all sound modules
or the desktop just because you see them. If `snd_bcm2835` remains, use the
audio-specific troubleshooting in section 16 before driving the panel.

`--no-hardware-pulse` remains in this repo's known service baseline. It disables
hardware pulse generation; having the PWM wiring modification does not mean
that flag enables it. Begin with the versioned baseline, then tune separately
after the complete setup works. Audio must still be configured correctly.

## 8. Set up the Spotify app and credentials

The Pi is a **Spotify playback display/controller**, not a Spotify audio player.
You play music on your phone, laptop, or another Spotify device using the same
account you authorize here. No Pi speaker or Bluetooth audio setup is needed.

### Spotify Developer Dashboard

1. Open the browser **on the Pi** from the desktop menu.
2. Visit [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
   and sign in.
3. Open your existing SpotifyMatrix application. Reuse its client credentials
   if you already have them; reflashing the SD card does not delete the app.
4. If you do not have an app, use the dashboard's create-app option, provide
   its requested name/description, and select **Web API** where offered.
5. In the app's settings, register this redirect URI **exactly**:

   ```text
   http://127.0.0.1:8888/callback
   ```

6. Save the dashboard change. This URI is for Spotify login; it is different
   from the control website's port **5000**. Do not substitute the Pi's Wi-Fi
   IP, `matrixspot.local`, `localhost`, HTTPS, or an extra trailing slash.
7. Find **Client ID** and **Client Secret** in the application settings. Use
   those values in the next step; do not use your Spotify login password.
8. For a development-mode app, make sure the Spotify account you will authorize
   is allowed under **Settings → Users Management** where required. Current
   Spotify rules require the app owner to have Premium, and unallowlisted users
   can complete login yet receive API 403 errors. See
   [Spotify development-mode requirements](https://developer.spotify.com/documentation/web-api/concepts/quota-modes).

Spotify requires explicit loopback addresses for this HTTP exception; `localhost`
is not an accepted registered redirect. See
[Spotify redirect rules](https://developer.spotify.com/documentation/web-api/concepts/redirect_uri).

### Put the three values in `.env` on the Pi

Return to Terminal:

```bash
cd "$HOME/Documents/SpotifyMatrix"
nano .env
```

Enter these three lines, replacing the two placeholder values:

```dotenv
SPOTIFY_CLIENT_ID=PASTE_YOUR_CLIENT_ID_HERE
SPOTIFY_CLIENT_SECRET=PASTE_YOUR_CLIENT_SECRET_HERE
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
```

In nano: **Ctrl+O → Enter** saves the file; **Ctrl+X** exits. If the onscreen
keyboard cannot enter nano shortcuts, use a desktop text editor's File → Save
to create `.env` in the project folder. The initial dot matters; it must not
be named `.env.txt`. Enable **Show Hidden Files** in the file manager to see it.

Only these Spotify settings are needed. Do not add `PI_HOST`, `PI_PASS`, your
Wi-Fi password, or your Spotify account password.

Protect the file and check that values are present **without printing secrets**:

```bash
chmod 600 .env
.venv/bin/python3 - <<'PY'
import os
from dotenv import load_dotenv
load_dotenv('.env')
for name in ('SPOTIFY_CLIENT_ID', 'SPOTIFY_CLIENT_SECRET', 'SPOTIFY_REDIRECT_URI'):
    value = os.getenv(name, '')
    if not value or value.startswith('PASTE_'):
        raise SystemExit('STOP: set ' + name + ' in .env')
if os.environ['SPOTIFY_REDIRECT_URI'] != 'http://127.0.0.1:8888/callback':
    raise SystemExit('STOP: check the redirect URI in .env and the dashboard')
print('Spotify configuration present; secrets were not printed.')
PY
```

`.env` and `.cache/` are ignored by Git and do not arrive with a clone. Create
credentials and authorize this Pi even if the laptop checkout already has them.

## 9. Authorize Spotify in the Pi's OWN browser

Do this before starting the service. If this is an existing install, first:

```bash
sudo systemctl stop spotifymatrix.service
```

On a fresh install, **Unit ... not loaded** simply means there is no service
yet. Continue with:

```bash
cd "$HOME/Documents/SpotifyMatrix"
mkdir -p .cache
sudo chown -R "$(id -un):$(id -gn)" .cache
.venv/bin/python3 spotify_matrix.py --auth-only
```

1. Run this as your normal login user, **without `sudo`**. That lets the normal
   desktop browser open correctly. A warning about not elevating process
   priority is harmless during `--auth-only`; it is not driving the LEDs.
2. The Terminal prints a Spotify authorization URL and normally opens it in
   the Pi browser automatically. If no tab opens, select/copy the entire URL
   using Terminal's Edit menu and paste it into the **Pi browser's** address bar.
3. Keep that Terminal open while you use the browser.
4. Sign in to the same Spotify account you use for playback. Approve the
   requested permissions, including playback controls.
5. The browser returns to `http://127.0.0.1:8888/callback?...`. This works because
   both the callback server and browser are **on this Pi**. Do not open the URL
   on your phone/laptop; their `127.0.0.1` refers to themselves.
6. Return to Terminal. It must print **Spotify token cached at
   .cache/spotify_token.json** and return to the prompt.
7. Do not refresh the callback page. Close that tab after Terminal finishes.

The callback page can display success without a usable authorization code in
the current implementation. Verify the actual token, without displaying it:

```bash
.venv/bin/python3 - <<'PY'
import json
from pathlib import Path
path = Path('.cache/spotify_token.json')
if not path.is_file():
    raise SystemExit('STOP: no Spotify token file; authorization did not finish.')
token = json.loads(path.read_text())
if not token.get('access_token') or not token.get('refresh_token'):
    raise SystemExit('STOP: token lacks access_token or refresh_token; authorize again.')
print('Spotify access token and refresh token are present.')
PY
```

Then:

```bash
chmod 700 .cache
chmod 600 .cache/spotify_token.json
```

These permissions tighten the files **at this point**. The current application's
token writer resets the token/cache permissions when it saves a refreshed
token; this is not a permanent permissions fix. Keep this installation on a
trusted Pi/network. Section 16 explains a retry if authorization stalls.

## 10. Test the physical matrix manually

First make sure the service is stopped so only one process drives the panel:

```bash
sudo systemctl stop spotifymatrix.service
cd "$HOME/Documents/SpotifyMatrix"
```

Again, a missing service is expected on a fresh installation. Run the same
hardware baseline as the service template, adding the test-pattern flag:

```bash
sudo .venv/bin/python3 spotify_matrix.py \
    --rows 64 --cols 64 --chain-length 1 --parallel 1 \
    --gpio-slowdown 5 --no-hardware-pulse \
    --hardware-mapping adafruit-hat-pwm \
    --pwm-bits 9 --limit-refresh-rate-hz 200 \
    --prefer-saved-settings --test-pattern
```

Expected: a moving color test pattern on the **physical LED matrix**. This
command does not test Spotify or serve the website. Watch the panel, then press
**Ctrl+C** in that Terminal to stop it and get the prompt back. If the panel
does not work, use section 16 before installing automatic startup.

Now run the full application manually:

```bash
sudo .venv/bin/python3 spotify_matrix.py \
    --rows 64 --cols 64 --chain-length 1 --parallel 1 \
    --gpio-slowdown 5 --no-hardware-pulse \
    --hardware-mapping adafruit-hat-pwm \
    --pwm-bits 9 --limit-refresh-rate-hz 200 \
    --web-port 5000 --prefer-saved-settings
```

Leave this Terminal open. You should see the ready message, token load, and
Spotify polling. Open the Pi browser to **http://127.0.0.1:5000/**. Play a track
on your normal Spotify device. Allow time for polling (after a long pause it
can be up to roughly 30 seconds), album-art download, and lyric lookup. Select
**Original** and **Wake** if needed; previously saved Custom/Idle/Sleep settings
can otherwise hide the song.

Confirm that the panel and live preview show the track. A track with no synced
lyrics is not necessarily a broken install; try a known vocal track. Pause
Spotify and confirm the idle behavior too. This test does not require the
phone and Pi to communicate directly because playback is read via Spotify's
internet API.

When finished, return to the Terminal running the app and press **Ctrl+C**.
Wait for the prompt before installing/starting the service.

## 11. Install and enable automatic startup

This is what makes the project start when you reconnect Pi power, without
HDMI, a Terminal window, desktop login, or an SSH connection.

Run from your normal user's Terminal, not a root shell:

```bash
cd "$HOME/Documents/SpotifyMatrix"
sed -e "s|@USER@|$(id -un)|g" -e "s|@DIR@|$HOME/Documents/SpotifyMatrix|g" \
    spotifymatrix.service.template | sudo tee /etc/systemd/system/spotifymatrix.service
```

Check it before starting:

```bash
sudo systemd-analyze verify /etc/systemd/system/spotifymatrix.service
sudo systemctl daemon-reload
systemctl cat spotifymatrix.service
```

Resolve errors about this unit, missing executables, or bad paths. The printed
unit should contain:

- `User=root` (required by this GPIO/display baseline).
- `WorkingDirectory=/home/adi/Documents/SpotifyMatrix` for user `adi`, or the
  corresponding actual home path for your user.
- `ExecStart=.../.venv/bin/python3 spotify_matrix.py ...` with the hardware
  flags used above and **`--prefer-saved-settings`**.
- `Restart=always`, `RestartSec=5`, and `WantedBy=multi-user.target`.
- **No active `CPUAffinity=` directive**. Comments mentioning it are fine.

If you changed the manual hardware mapping because your board lacks the PWM
modification, make that same change in the installed unit using
`sudo nano /etc/systemd/system/spotifymatrix.service` before starting it.
If `systemctl cat` lists old drop-in overrides beneath the unit, inspect them:
an override can replace ExecStart or reintroduce CPUAffinity. Do not blindly
delete unrelated overrides; reconcile them with this configuration.

Start it and enable future boots:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now spotifymatrix.service
systemctl is-enabled spotifymatrix.service
systemctl is-active spotifymatrix.service
sudo systemctl status spotifymatrix.service --no-pager
```

Expected: **enabled** and **active (running)**. Recheck after about 30 seconds;
a process that momentarily starts then repeatedly crashes is not a working
service. Read logs:

```bash
sudo journalctl -u spotifymatrix.service -n 60 --no-pager
```

### Limit journal growth

Create a journald drop-in rather than changing the main configuration:

```bash
sudo mkdir -p /etc/systemd/journald.conf.d
sudo tee /etc/systemd/journald.conf.d/spotifymatrix-limits.conf > /dev/null <<'EOF'
[Journal]
SystemMaxUse=30M
RuntimeMaxUse=30M
MaxRetentionSec=2day
EOF
sudo systemctl restart systemd-journald
```

These are system-wide journal limits, including other services; actual usage
can temporarily exceed the cap due to active journal files. Inspect with:

```bash
sudo journalctl --disk-usage
```

The service will now continue after closing Terminal or the browser.

## 12. Open the website and save your settings

### On the Pi's HDMI desktop

1. Open Chromium/Firefox from the Pi desktop menu.
2. Click the address bar, enter **http://127.0.0.1:5000/**, and press Enter.
3. Bookmark it as SpotifyMatrix. No public domain or hosting service is needed.
4. Logs are at **http://127.0.0.1:5000/logs**.

Port **5000** is the control panel. Port **8888** is only the temporary Spotify
login callback and normally closes after authorization. `0.0.0.0` in a log
means the server listens on all Pi network interfaces; use `127.0.0.1` or the
actual Pi address in the browser.

### On your phone or laptop

1. Connect it and the Pi to a LAN that allows devices to reach each other.
2. In the Pi Terminal, run:

   ```bash
   hostname -I
   ```

3. Use the current Wi-Fi IPv4 address in the other device's browser:
   **`http://PI_IP:5000/`**, replacing `PI_IP` with that number.
   For example, **http://10.10.74.130:5000/** only if that is still the Pi's IP.
4. **http://matrixspot.local:5000/** can also work if the device supports mDNS
   and you kept that hostname. Try the numeric IP first if it does not resolve.
5. Add/bookmark the page on your phone once it opens.

Your laptop can use the router's 5 GHz band while the Pi uses 2.4 GHz **if the
router bridges them and allows peer traffic**. Matching SSID names or changing
to 2.4 GHz alone does not override client isolation. Your earlier Cloudwifi
connection did not permit laptop-to-Pi access; if it is still isolated, use a
private router or a compatible 2.4 GHz hotspot that permits clients to reach
each other. After changing the Pi's Wi-Fi, recheck its IP. Nothing in this local
installation removes the shared network's isolation.

The current control panel has no login. Keep it on a trusted LAN; do not
port-forward port 5000 to the internet just to reach it from your phone.

### Choose and verify your saved settings

1. Pick **Original**, **CD View**, **Lyrics**, **Album Art**, **Idle Screen**, or
   cast a **Custom Slate**. Original automatically moves from idle to disc to
   lyrics when a song starts.
2. Set brightness, lyric style, the **Scroll Font Size** and **Pop Font Size**
   under **Show Advanced Settings**, speeds, accent, and other choices.
3. For a custom image/GIF, upload it and use **Cast to Matrix**. Editing the
   draft does not replace the last cast until you cast it.
4. Click **Save now** at the top and wait for **Saved on Pi**. Ordinary changes
   autosave too; Save now flushes pending editor changes and provides an
   explicit checkpoint.
5. Reload the browser. Check that the choices are still there.
6. In Terminal run:

   ```bash
   sudo systemctl restart spotifymatrix.service
   ```

7. After it restarts, reload the page and confirm mode, brightness, font sizes,
   and any custom image are restored. This tests restoration from disk, not
   just values held in the browser.

Settings live on the **Pi**, at
`~/Documents/SpotifyMatrix/.cache/settings.json`. They include display settings,
Sleep, custom media, drafts, and the panel's preview/expanded-section choices.
The browser cannot restore a file-picker's original filename, but the saved
image can restore in the canvas. The current song/progress is fetched fresh;
play/skip commands are not replayed at boot.

**Sleep is persistent:** if you leave the display sleeping, it will restart
sleeping and appear blank. Press **Wake**, choose the mode you want to see after
power-on, and save again before the final test. An empty Custom Slate without
saved media falls back to Original.

## 13. Verify everything after a reboot

Keep HDMI attached for this first boot verification:

```bash
sudo reboot
```

After the desktop returns, open Terminal. **Do not run the manual Python
command**; the enabled service should already be running.

Run:

```bash
systemctl is-enabled spotifymatrix.service
systemctl is-active spotifymatrix.service
systemctl show spotifymatrix.service -p MainPID -p NRestarts -p CPUAffinity
cat /sys/devices/system/cpu/isolated
swapon --show
free -h
systemctl is-active bluetooth.service
lsmod | grep '^snd_bcm2835' || true
curl --max-time 10 -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5000/
curl --max-time 10 -fsS http://127.0.0.1:5000/api/state | python3 -m json.tool
sudo journalctl -b -u spotifymatrix.service -n 80 --no-pager
```

Check the results, not just that commands ran:

| Check | What you want |
|---|---|
| Service enabled / active | `enabled` / `active` |
| MainPID / NRestarts | nonzero PID; restart count is not repeatedly increasing |
| CPUAffinity | empty; no service-wide pin to core 3 |
| Isolated CPUs | `3` |
| Swap | `/swapfile` active after reboot; existing zram can remain |
| Bluetooth | inactive/absent |
| Onboard audio | no `snd_bcm2835` line |
| Dashboard request | HTTP `200` |
| `/api/state` | saved brightness/mode/fonts; `sleeping: false` for a visible display |
| Spotify while playing | `is_connected: true`, correct title/artist, and `is_playing: true` |
| Physical matrix | shows the selected mode and responds to website changes |
| Reboot settings | same choices as before reboot |

If Spotify is paused/no device is active, a blank title or `is_playing: false`
does not alone indicate failure. Start a track on the authorized account and
allow a poll cycle. A `Track:` log plus the correct title on the panel is
stronger evidence than the service being merely `active`.

### Confirm the refresh thread's core assignment

```bash
PI_PID="$(systemctl show -p MainPID --value spotifymatrix.service)"
ps -L -p "$PI_PID" -o pid,tid,cls,rtprio,psr,comm
```

Look for the display-refresh thread with **`FF`** scheduling class, realtime
priority **99**, and **PSR 3**. Ordinary Python threads should use the other
cores. `psr` is a snapshot of the last CPU used, not proof of permanent affinity;
repeat the observation if it looks odd. If all threads run on core 3, check the
unit and any drop-ins for CPUAffinity/taskset. If there is no realtime thread,
check root permissions and the library's startup errors.

Optional power-health check:

```bash
vcgencmd get_throttled
```

`0x0` means no currently reported or recorded throttling flags since boot.
Nonzero results need decoding; they can include earlier undervoltage/thermal
events even if the condition has cleared. Fix power/heat issues before relying
on a long unattended run.

## 14. Shut down, remove HDMI, and test a power-on

1. In the control page, choose the mode you want to start with. Make sure the
   panel is awake. Click **Save now**, wait for **Saved on Pi**, and confirm
   there is no saving error.
2. In Terminal run:

   ```bash
   sync
   sudo poweroff
   ```

3. Wait for shutdown to finish and SD-card activity to stop. Do not pull the
   card or power while the shutdown is still running. Allow about 30 seconds
   if you are unsure; the display state/LED alone is not a universal shutdown
   indicator.
4. Disconnect Pi power. Disconnect the panel supply too before changing wiring.
5. Disconnect HDMI. Remove the USB keyboard/mouse/hub if you want the final
   standalone arrangement. Keep the HAT, matrix cable, SD card, and required
   power wiring attached.
6. Restore the intended panel and Pi supplies. Applying Pi power boots it.
7. Wait roughly 1–2 minutes (longer if networking is slow). The enabled
   `spotifymatrix.service` should start without you opening Terminal or logging in.
8. Play a song using the authorized Spotify account and check the physical
   matrix. If you chose Original, allow the initial polling interval and
   disc-before-lyrics delay. If you chose Custom/Idle, expect that chosen mode.
9. If your phone has network access to the Pi, open its current
   `http://PI_IP:5000/` page and confirm the saved choices. If shared Wi-Fi
   blocks that page but the matrix follows playback, the local application can
   still be running correctly.
10. If it does not work, reconnect HDMI/input and inspect section 13's service
    and journal checks. Do not assume a solid green LED identifies one specific
    fault, and do not reformat the card as the first diagnostic step.

After this succeeds, future **power-ons automatically run the project**.
If it is already powered on and you want to restart only the application, use
`sudo systemctl restart spotifymatrix.service` from a Pi Terminal. There is no
additional boot command to enter after unplugging/replugging.

Saved settings are written atomically, but that does not make the whole OS/SD
card immune to an abrupt power cut. Use `sudo poweroff` or the desktop shutdown
menu before disconnecting power when you have access. Removing HDMI while it
is running is fine and does not shut down the application.

### Optional: reduce load by booting without the desktop

Once the complete setup works, you can keep the desktop installed but boot to
the console to free resources:

```bash
sudo systemctl set-default multi-user.target
sudo reboot
```

The matrix service still starts automatically. With HDMI attached you will now
see a text login instead of the desktop; enter your Pi username and password.
You will need a keyboard for this, so keep desktop boot if you only have a mouse
and no working remote access.

To restore desktop boot later, log in at the console and run:

```bash
sudo systemctl set-default graphical.target
sudo reboot
```

For a temporary desktop session while the default remains console:

```bash
sudo systemctl start display-manager
```

This does not uninstall the desktop. Keep a way to return to the Pi browser
for future Spotify authorization if remote access is unavailable.

## 15. Commands for normal use and updates

Open Terminal on the Pi as in section 1 whenever needed.

| Task | Command |
|---|---|
| Status | `sudo systemctl status spotifymatrix.service --no-pager` |
| Start now | `sudo systemctl start spotifymatrix.service` |
| Stop now (still starts next boot) | `sudo systemctl stop spotifymatrix.service` |
| Restart the program | `sudo systemctl restart spotifymatrix.service` |
| Enable future boots and start now | `sudo systemctl enable --now spotifymatrix.service` |
| Disable future boots and stop now | `sudo systemctl disable --now spotifymatrix.service` |
| Follow logs | `sudo journalctl -u spotifymatrix.service -f` |
| Restart the whole Pi | `sudo reboot` |
| Shut down before removing power | `sudo poweroff` |
| Open dashboard on the Pi | Pi browser → `http://127.0.0.1:5000/` |

**Ctrl+C** while following journal logs stops only the log viewer. It does not
stop the systemd service. By contrast, Ctrl+C in a manually launched Python
run stops that manual run.

### Update from GitHub on the Pi

First inspect your checkout:

```bash
cd "$HOME/Documents/SpotifyMatrix"
git status --short
```

Preserve/resolve any local code changes before pulling. Then run each step,
continuing only if the preceding one succeeds:

```bash
sudo systemctl stop spotifymatrix.service
git pull --ff-only origin main
.venv/bin/python3 -m pip install -r requirements.txt
```

Reinstall the current service template too, so changes to startup flags are
applied. Reapply any intentional hardware-specific edits afterwards:

```bash
sed -e "s|@USER@|$(id -un)|g" -e "s|@DIR@|$HOME/Documents/SpotifyMatrix|g" \
    spotifymatrix.service.template | sudo tee /etc/systemd/system/spotifymatrix.service
sudo systemd-analyze verify /etc/systemd/system/spotifymatrix.service
sudo systemctl daemon-reload
sudo systemctl start spotifymatrix.service
git log -1 --oneline
systemctl is-active spotifymatrix.service
curl --max-time 10 -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5000/
```

Check the physical panel and website. A normal pull does not replace the ignored
`.env`, token, or saved settings. You do not need to rebuild the upstream matrix
library every time project code changes. If an update fails, diagnose it before
claiming the new version is running; the service remains stopped until started.

### Re-authorize Spotify when needed

Tokens normally refresh automatically. Re-authorize if logs report revoked or
invalid authorization, or if you need newly added scopes; do not assume a fixed
six-month expiry. Preserve the old token while requesting a new one:

```bash
sudo systemctl stop spotifymatrix.service
cd "$HOME/Documents/SpotifyMatrix"
sudo chown -R "$(id -un):$(id -gn)" .cache
if [ -f .cache/spotify_token.json ]; then
    mv .cache/spotify_token.json ".cache/spotify_token.before-reauth.$(date +%Y%m%d-%H%M%S).json"
fi
.venv/bin/python3 spotify_matrix.py --auth-only
```

Complete the Pi-browser login and **repeat the access/refresh-token verification
from section 9**, then tighten the new token/cache permissions and start the
service. Merely running `--auth-only` with a valid old cached token can exit
without requesting the new permissions; moving that cache makes it request
a fresh authorization. Treat backup token files as credentials too.

### Optional SSH for later

SSH is not required for anything above. If you later want remote Terminal access:

```bash
sudo systemctl enable --now ssh
systemctl is-active ssh
hostname -I
```

From another computer you would use `ssh adi@PI_IP`, substituting your username
and the Pi's current address. Remote access still needs a network that permits
peer connections. Generate any future client SSH key **on that client**, not
on the Pi; the Windows control script needs key access, whereas this local
guide needs neither that script nor a key.

## 16. Troubleshooting and recovery

### Website refuses the connection on the Pi

Check **http://127.0.0.1:5000/** (HTTP, not HTTPS). Run:

```bash
sudo systemctl status spotifymatrix.service --no-pager
sudo journalctl -u spotifymatrix.service -n 100 --no-pager
sudo ss -ltnp 'sport = :5000'
```

If a manual process is already using the panel/port, stop it with Ctrl+C in its
own Terminal before starting the service. Do not run multiple matrix processes.
If the local page works and only the phone fails, investigate the current IP,
phone Wi-Fi connection, and router isolation rather than reinstalling Python.

### Spotify login fails or stays waiting

- Check `.env` and the developer dashboard have the exact same
  `http://127.0.0.1:8888/callback` redirect.
- Use the browser on the Pi. Opening it on the phone does not send a loopback
  callback to the Pi.
- If automatic browser launching fails, copy the printed URL into the Pi
  browser while the auth command is still running.
- If the Terminal never finishes despite a success page, press Ctrl+C **in
  the authorization Terminal**, then rerun and use the newly printed URL.
  Do not reuse an old URL/state or refresh the callback page.
- `Address already in use` on port 8888 means an old auth process may still
  be running. Inspect `sudo ss -ltnp 'sport = :8888'` and stop the identified
  old process via its own Terminal. Do not use a broad `pkill python`.
- API 403 after successful login can mean developer-app access/allowlist or
  Premium restrictions; repeatedly reinstalling dependencies will not fix it.
- If a desktop **Default keyring** dialog appears, that is the browser/desktop
  password store. Use your Pi login password if you choose to create/unlock it;
  it is not requesting a new Spotify client secret.

### Panel stays blank, shows the wrong mode, or flickers

1. Check service logs and the website's saved Sleep/mode choices. Press Wake;
   select Original and save if you want the normal Spotify display.
2. Run the test pattern from section 10 with the service stopped. If that fails
   too, check matrix power, ribbon input/orientation, mapping, and Address E
   configuration with power disconnected before changing cables.
3. Check `/sys/devices/system/cpu/isolated` is `3`, the refresh thread is realtime,
   and no service/drop-in pins the whole process to one core.
4. Check audio and power as below. A monitor picture alone does not prove the
   matrix has sufficient power.
5. Only after the baseline works, tune one hardware flag at a time in the
   installed unit. The baseline slowdown `5`, PWM bits `9`, and cap `200` are
   inherited project values, not a guarantee of optimal flicker performance on
   every panel. For example, reducing slowdown can improve refresh but may
   corrupt the picture; revert if it does. Reload/restart the service after
   unit edits.

### Onboard audio module still loaded

If `lsmod | grep '^snd_bcm2835'` still shows the conflicting onboard module
after section 7 and a reboot, add a module-specific blacklist:

```bash
printf 'blacklist snd_bcm2835\n' | sudo tee /etc/modprobe.d/spotifymatrix-audio.conf
sudo update-initramfs -u
sudo reboot
```

After reboot check that module again. This targets the onboard audio module,
not all HDMI/USB audio. The upstream library documents this additional step
in its [sound-conflict guidance](https://github.com/hzeller/rpi-rgb-led-matrix#bad-interaction-with-sound).

### Missing modules or build fails

Run the exact venv import check in section 6. If `rgbmatrix` is missing there,
it was not installed into the interpreter the service uses. Do not work around
this with `sudo pip` or `--break-system-packages`. If compilation was killed,
check swap, disk space, and kernel OOM messages:

```bash
swapon --show
df -h /
sudo journalctl -k -n 100 --no-pager
```

Retry the one-job build only after fixing the reported problem. If the service
reports missing Spotify environment variables, check `.env` exists in its
WorkingDirectory; `.env` is not supplied by GitHub.

### Settings do not survive a restart

1. Confirm the running code supports `--prefer-saved-settings`.
2. Confirm the installed service and any overrides use that flag, not
   `--no-settings` or a different `--settings` path.
3. Check **Saved on Pi**, not just that a slider moved. Release the slider and
   click Save now before testing.
4. Check available disk space and whether the root filesystem is writable:

   ```bash
   df -h /
   findmnt -no OPTIONS /
   sudo ls -l "$HOME/Documents/SpotifyMatrix/.cache/settings.json"
   sudo journalctl -u spotifymatrix.service -n 80 --no-pager
   ```

5. Reinstall the current template and retest section 12. Do not delete the
   whole `.cache` directory; it also contains the Spotify authorization token.

### Pi will not boot after editing boot files

A green LED by itself is not enough to diagnose the cause. If the failure
began immediately after section 7, restore **this guide's** boot backups:

1. Disconnect power before removing the SD card.
2. Insert it into another computer and open the small FAT `bootfs` partition.
3. Preserve the current `cmdline.txt`/`config.txt` as diagnostic copies.
4. Copy `cmdline.txt.matrix-local.bak` over `cmdline.txt`, and
   `config.txt.matrix-local.bak` over `config.txt`, if those backups exist.
5. Eject safely, return the card to the Pi, and boot again with HDMI attached.
6. If there are no backups, do not invent a `root=PARTUUID=...`; it must identify
   this card's actual Linux partition. Diagnose the partition/boot configuration.

Do not accept a Windows prompt to format the Linux partition. Also disregard
historical `CPUAffinity=3` advice in `updates.md`/`BOOT_RECOVERY.md`; it conflicts
with the current service and upstream refresh-thread behavior. This guide and
the current template use isolation without pinning the entire application.

## Completion checklist

- [ ] Pi boots from the flashed card and I can open its local Terminal.
- [ ] Pi model, username, Python version, root storage, and four CPUs checked.
- [ ] Correct 2.4 GHz Wi-Fi connected; internet and date/time checked.
- [ ] Persistent `/swapfile` enabled before upgrading/compiling.
- [ ] OS updated and required packages installed; upgrade reboot completed.
- [ ] SpotifyMatrix cloned/updated from the correct GitHub repository.
- [ ] Project venv and Python dependencies installed successfully.
- [ ] Matrix library built into that venv; `rgbmatrix OK` printed.
- [ ] Boot backups created; audio/Bluetooth disabled; `isolcpus=3` configured.
- [ ] Reboot confirms isolated core 3, active swap, and no onboard audio module.
- [ ] Spotify dashboard redirect, app access, and `.env` values configured.
- [ ] Pi-browser authorization finished; access and refresh tokens verified.
- [ ] Physical test pattern works, then manual Spotify display and website work.
- [ ] Manual process stopped before starting the systemd service.
- [ ] Current service template installed; no whole-process CPUAffinity pin.
- [ ] Service stays active and is enabled for future boots.
- [ ] Journal growth limits configured.
- [ ] Dashboard opens locally; phone access tested separately if LAN permits it.
- [ ] Choices saved and survive a service restart, including font sizes/media.
- [ ] Reboot checks pass; Spotify track and physical display verified.
- [ ] Display left awake in the intended startup mode, with Saved on Pi shown.
- [ ] Clean shutdown completed; HDMI removed; power-on automatically starts it.

## Sources and code checked

- Repository: `spotify_matrix.py` (CLI, environment loading, OAuth callback,
  token cache, GPIO setup, polling, web server, settings loading/saving).
- Repository: `requirements.txt`, `spotifymatrix.service.template`,
  `matrix_control.ps1`, `README.md`, `userguidefinal.md`, `BOOT_RECOVERY.md`,
  `IMPROVEMENTS.md`, and `updates.md`; conflicting historical instructions
  were not carried into this local workflow.
- [Raspberry Pi configuration](https://www.raspberrypi.com/documentation/computers/configuration.html)
  and [boot configuration](https://www.raspberrypi.com/documentation/computers/config_txt.html).
- [RGB matrix upstream documentation](https://github.com/hzeller/rpi-rgb-led-matrix),
  [Python build](https://github.com/hzeller/rpi-rgb-led-matrix/blob/master/pyproject.toml),
  and [refresh-thread implementation](https://github.com/hzeller/rpi-rgb-led-matrix/blob/master/lib/led-matrix.cc).
- [Spotify app setup](https://developer.spotify.com/documentation/web-api/concepts/apps),
  [redirect rules](https://developer.spotify.com/documentation/web-api/concepts/redirect_uri),
  and [development-mode access](https://developer.spotify.com/documentation/web-api/concepts/quota-modes).
