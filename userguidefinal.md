# Spotify Matrix — Complete User Guide

The definitive guide for building, running and maintaining the Spotify Matrix display.

**Chapter 1 was rebuilt from scratch on 2026-09-07** by doing a real end-to-end
install on a freshly reformatted SD card. Every command in it was actually run
and verified. Several steps in the previous version of this guide were wrong or
had gone stale — those are called out inline so you do not repeat them.

## Table of Contents

* [Chapter 1: Full Build From a Blank SD Card](#chapter-1-full-build-from-a-blank-sd-card)
* [Chapter 2: Mistakes to Avoid](#chapter-2-mistakes-to-avoid)
* [Chapter 3: Features & Overview](#chapter-3-features--overview)
* [Chapter 4: Web Control Panel & Settings](#chapter-4-web-control-panel--settings)
* [Chapter 5: Dual-Mode (Manual Override)](#chapter-5-dual-mode-manual-override)
* [Chapter 6: Maintenance & Re-Auth](#chapter-6-maintenance--re-auth)
* [Chapter 7: Flicker & Performance Tuning](#chapter-7-flicker--performance-tuning)

---

## Chapter 1: Full Build From a Blank SD Card

Verified against: **Raspberry Pi Zero 2 W**, **Raspberry Pi OS Lite 64-bit
(Debian 13 "trixie")**, kernel **6.18.39**, 64x64 panel on an Adafruit RGB
Matrix HAT.

Total time is roughly 60–90 minutes, most of it waiting. **The single most
important rule: never Ctrl+C a step because it looks stuck.** See
[Chapter 2](#chapter-2-mistakes-to-avoid).

### Step 1 — Flash the SD card

Use **Raspberry Pi Imager**. Choose **Raspberry Pi OS Lite (64-bit)** — there is
no need for a desktop, the Pi is headless.

Open the gear icon (⚙️ / `Ctrl+Shift+X`) and set:

| Setting | Value | Why it matters |
|---|---|---|
| Hostname | `matrixspot` | Everything resolves `matrixspot.local`. Changing it means editing scripts. |
| Username | `adi` | The service file and `matrix_control.ps1` hardcode `/home/adi/...`. |
| Password | something real | Only used for the first login and `sudo`. It is not used by any script. |
| Enable SSH | yes, password auth | You swap to key auth in Step 3. |
| Wi-Fi | your SSID + password | Set the correct country code or Wi-Fi will not come up. |
| Locale / timezone | yours | The clock face uses this. |

Write, eject, insert into the Pi, power on, and wait ~90 seconds.

### Step 2 — First SSH connection

If this Pi has existed before, your PC still has the **old** SSH host key saved
and will refuse to connect with `REMOTE HOST IDENTIFICATION HAS CHANGED`. That
is expected after a reflash — the Pi genuinely has a new identity. Clear it:

```powershell
ssh-keygen -R matrixspot.local
```

Then connect and accept the new key when prompted:

```powershell
ssh adi@matrixspot.local
```

> If `matrixspot.local` will not resolve, mDNS has not come up yet. Find the Pi
> on your router's device list, or run `arp -a` and look for its IP, then use
> `ssh adi@<ip>`. Resolution usually recovers on its own within a few minutes.

### Step 3 — Set up SSH key authentication

`matrix_control.ps1` authenticates with keys, not passwords, so this is
required — not optional.

**Generate the key on your Windows laptop, NOT on the Pi.** A key generated on
the Pi lets the Pi log in to other machines, which is the opposite of what you
want. Run this in PowerShell, and press Enter three times (default path, empty
passphrase):

```powershell
ssh-keygen -t ed25519 -C "matrixspot"
```

Copy the public half to the Pi (this asks for the Pi password one final time):

```powershell
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh adi@matrixspot.local "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
```

Verify it worked — this should log in with no password prompt at all:

```powershell
ssh adi@matrixspot.local
```

### Step 4 — Add swap BEFORE anything heavy

The Pi Zero 2 W has ~415 MB of usable RAM. Both the OS upgrade and the C++
compile in Step 6 will exhaust it and either crawl or get OOM-killed. Do this
first, not after something has already stalled.

**`dphys-swapfile` does not exist on trixie.** Older guides (including the
previous version of this one) tell you to edit `/etc/dphys-swapfile`; that
package is not installed and the command will fail with `command not found`.
Trixie provides swap through **zram** instead — a compressed swap device living
in RAM, which does not help when RAM itself is the constraint.

Check what you actually have:

```bash
free -h
swapon --show
```

You will see `/dev/zram0` at roughly 415 MB. Leave it alone and add a real
disk-backed swapfile alongside it:

```bash
sudo fallocate -l 1G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h
```

`free -h` must now show **`Swap: 1.4Gi`** (415 M zram + 1 G file). The `/etc/fstab`
line is what makes it survive reboots. During the real build this swapfile peaked
at over 500 MB in use during the compile — without it that step fails.

### Step 5 — Update the OS

```bash
sudo apt update
sudo apt full-upgrade -y
```

This takes **20–40 minutes** on a Zero 2 W and will appear frozen for long
stretches — `Reading package lists... 28%` can sit unchanged for many minutes.
That is normal. Do not interrupt it.

If you are worried it has died, open a **second** SSH window and confirm it is
still working rather than killing it:

```bash
ps -p $(pgrep -f 'apt full-upgrade' | head -1) -o pid,etime,pcpu,stat,cmd
```

Nonzero `%CPU`, or a `D`/`S` state with elapsed time climbing, means it is
alive. High `wa` (I/O wait) in `top` just means it is swapping, not hung.

To make it immune to a dropped SSH session entirely:

```bash
nohup sudo apt full-upgrade -y > ~/upgrade.log 2>&1 &
disown
tail -f ~/upgrade.log      # Ctrl+C stops watching, not the upgrade
```

A kernel upgrade may reboot the Pi on its own, which will drop your session.
That is expected. Reconnect after ~60 seconds.

If a previous upgrade was genuinely killed partway, repair it before continuing:

```bash
sudo dpkg --configure -a
sudo apt --fix-broken install -y
```

### Step 6 — Dependencies, repo, virtualenv

```bash
sudo apt install -y git build-essential python3-dev python3-venv python3-pip
mkdir -p ~/Documents
cd ~/Documents
git clone https://github.com/Adi-Shinde/SpotifyMatrix.git
cd SpotifyMatrix
```

Create the virtualenv. **This takes 1–2 minutes and prints nothing while it
works.** Wait for the prompt to return:

```bash
python3 -m venv .venv
```

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

Your prompt should now be prefixed with `(.venv)`.

### Step 7 — Build the LED matrix library

`rgbmatrix` is deliberately absent from `requirements.txt`: it is compiled from
`hzeller/rpi-rgb-led-matrix` against this specific hardware and is not
pip-installable from PyPI.

> **The build method changed upstream and the old instructions are dead.**
> Guides (including the previous version of this one) tell you to run
> `make build-python` / `make install-python`. Those targets **no longer exist**
> in any directory of the current repository and fail with
> `make: *** No rule to make target 'build-python'. Stop.`
> The project moved to a `scikit-build-core` + Cython build driven by a
> `pyproject.toml` at the **repository root**. The correct command is
> `pip install .` from that root.

With the venv still active:

```bash
cd ~/Documents
git clone https://github.com/hzeller/rpi-rgb-led-matrix.git
cd rpi-rgb-led-matrix
pip install .
```

This is the slowest step in the whole build — **15–20 minutes** on a Zero 2 W,
with load average climbing past 10 and heavy swap use. It prints
`Building wheel for rgbmatrix (pyproject.toml): still running...` repeatedly.
That is progress, not a hang. `cmake`, `ninja` and `cython` are fetched
automatically as build dependencies; you do not need to apt-install them.

Verify it landed **inside the venv**, which is where the service will look:

```bash
~/Documents/SpotifyMatrix/.venv/bin/python3 -c "import rgbmatrix; print('ok')"
```

Do not continue until this prints `ok`.

### Step 8 — Spotify credentials

The Pi needs only the three Spotify values. `PI_HOST` and `PI_PASS` are for
`matrix_control.ps1` on your laptop — and `PI_PASS` is obsolete entirely, since
the script uses SSH keys. Do not copy a password onto the Pi.

Create `~/Documents/SpotifyMatrix/.env` containing exactly:

```
SPOTIFY_CLIENT_ID=<your client id>
SPOTIFY_CLIENT_SECRET=<your client secret>
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
```

Or copy your existing one from the laptop and strip the extra lines:

```powershell
scp C:\path\to\SpotifyMatrix\.env adi@matrixspot.local:~/Documents/SpotifyMatrix/.env
```

Lock it down — it holds a client secret:

```bash
chmod 600 ~/Documents/SpotifyMatrix/.env
```

### Step 9 — One-time Spotify authorization

Spotify requires a human to click **Agree** once. The Pi has no browser, so the
callback is tunnelled to your laptop.

**Window B** — open this first, in a separate PowerShell window, and leave it
running untouched:

```powershell
ssh -L 8888:127.0.0.1:8888 adi@matrixspot.local
```

**Window A** — on the Pi:

```bash
cd ~/Documents/SpotifyMatrix
.venv/bin/python3 spotify_matrix.py --auth-only --no-browser
```

It prints a `https://accounts.spotify.com/authorize...` link. Copy it into your
laptop browser, log in, click **Agree**.

> **The success page in your browser can lie.** There is a bug in the callback
> handler: it sends "Spotify authorization complete. You can close this tab."
> *before* checking that an authorization `code` actually arrived. If the code is
> missing, the browser shows success while the Pi waits forever.
>
> **Always verify on the Pi, not in the browser:**
> ```bash
> ls -la ~/Documents/SpotifyMatrix/.cache/spotify_token.json
> ```
> If that file does not exist, the auth did **not** work regardless of what the
> browser said. Kill the process and retry with a fresh link — the `state` value
> changes each run, so an old link will never work.
>
> Do not refresh the success tab. A reload hits `/callback` without a `code`,
> which overwrites the captured code with nothing.

Once the token exists, tighten its permissions — it is a live credential and is
created world-writable by default:

```bash
chmod 700 ~/Documents/SpotifyMatrix/.cache
chmod 600 ~/Documents/SpotifyMatrix/.cache/spotify_token.json
```

The service runs as root, so it can still read them.

### Step 10 — Install the systemd service

The unit is versioned in the repo as `spotifymatrix.service.template`. Install
it from there rather than hand-writing one:

```bash
cd ~/Documents/SpotifyMatrix
sed -e "s|@USER@|$USER|g" -e "s|@DIR@|$HOME/Documents/SpotifyMatrix|g" \
    spotifymatrix.service.template | sudo tee /etc/systemd/system/spotifymatrix.service
sudo systemctl daemon-reload
sudo systemctl enable --now spotifymatrix.service
sudo systemctl status spotifymatrix.service --no-pager
```

It must report `active (running)`. If it failed:

```bash
journalctl -u spotifymatrix -n 50 --no-pager
```

### Step 11 — Disable Bluetooth and onboard audio

Onboard audio uses the same PWM hardware the matrix needs, so it must go.
Bluetooth just frees resources.

```bash
sudo systemctl disable bluetooth.service
sudo systemctl stop bluetooth.service
```

Find the boot partition — **trixie/Bookworm use `/boot/firmware`, not `/boot`**.
Writing to the wrong one silently does nothing:

```bash
if [ -f /boot/firmware/cmdline.txt ]; then BOOT=/boot/firmware; else BOOT=/boot; fi
echo $BOOT
```

Back both files up before editing anything (this is your recovery path):

```bash
sudo cp -a $BOOT/cmdline.txt $BOOT/cmdline.txt.matrixbak
sudo cp -a $BOOT/config.txt  $BOOT/config.txt.matrixbak
sync
```

Disable onboard audio:

```bash
CF="$BOOT/config.txt"
sudo cp "$CF" /tmp/config.new
if grep -q "^dtparam=audio=on" /tmp/config.new; then
  sed -i "s/^dtparam=audio=on/dtparam=audio=off/" /tmp/config.new
else
  printf 'dtparam=audio=off\n' >> /tmp/config.new
fi
sudo cp /tmp/config.new "$CF"; sync; rm -f /tmp/config.new
grep -n "dtparam=audio" "$CF"
```

### Step 12 — Isolate CPU core 3

Only do this if `nproc` reports **4 or more**. Isolating a core that does not
exist breaks the service.

The write below is deliberately careful: it collapses the file to exactly one
line (a stray newline becomes a second line of kernel arguments), validates that
`root=` survived, and uses `cp` rather than `sed -i` so the FAT directory entry
is preserved. A malformed `cmdline.txt` means a Pi that will not boot.

```bash
CL="$BOOT/cmdline.txt"
if grep -q "isolcpus=" "$CL"; then
  echo "already set"
else
  NEW="$(tr -d '\r\n' < "$CL") isolcpus=3"
  printf '%s\n' "$NEW" > /tmp/cmdline.new
  if [ "$(wc -l < /tmp/cmdline.new)" -eq 1 ] && grep -q "root=" /tmp/cmdline.new; then
    sudo cp /tmp/cmdline.new "$CL"; sync; echo "isolcpus added"
  else
    echo "VALIDATION FAILED"
  fi
  rm -f /tmp/cmdline.new
fi
cat "$CL"
```

**Read the printed line before rebooting.** It must be one line and must still
contain `root=`. If it says `VALIDATION FAILED`, nothing was changed — stop and
investigate.

> **Do NOT add `CPUAffinity=3` to the systemd unit.** Earlier advice (including
> `IMPROVEMENTS.md` P6) said to pair `isolcpus=3` with `CPUAffinity=3` on the
> assumption that nothing pins itself to the reserved core. That assumption is
> wrong, and following it makes flicker *worse*. See
> [Chapter 7](#chapter-7-flicker--performance-tuning).

Sync twice and reboot — a reboot racing an unflushed FAT write is how boot files
get truncated:

```bash
sync; sleep 1; sync
sudo reboot
```

### Step 13 — Verify everything

Reconnect after ~60 seconds and check all of it:

```bash
ssh adi@matrixspot.local

systemctl is-active spotifymatrix      # active
systemctl is-enabled spotifymatrix     # enabled
cat /sys/devices/system/cpu/isolated   # 3
free -h | grep -i swap                 # 1.4Gi, survived reboot
systemctl is-active bluetooth          # inactive
grep '^dtparam=audio' /boot/firmware/config.txt   # dtparam=audio=off
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5000/   # 200
```

Confirm the realtime thread has core 3 to itself. Exactly one thread should be
`FF 99`, and it should be the **only** one with `psr=3`:

```bash
ps -eLo pid,tid,cls,rtprio,psr,comm | grep python3
```

Healthy output looks like this — RT thread alone on core 3, Python work spread
across 0–2:

```
1983 1983 TS   -  2 python3
1983 1985 FF  99  3 python3     <- realtime refresh thread, alone on core 3
1983 1986 TS   -  2 python3
1983 1987 TS   -  1 python3
1983 1988 TS   -  0 python3
```

Finally, from your laptop:

```
http://matrixspot.local:5000
```

Watch the journal to confirm it is really talking to Spotify:

```bash
journalctl -u spotifymatrix -f
```

You should see `Token loaded from`, `Spotify Matrix — Ready`, then a
`Track: ...` line and `LRCLIB: Found N synced lyric lines.`

---

## Chapter 2: Mistakes to Avoid

Every one of these was hit during the real 2026-09-07 rebuild. They cost more
time than the actual work did.

### Never Ctrl+C something that looks stuck

This caused the single worst cascade of the build. `python3 -m venv .venv` was
interrupted because it sat silent for a minute. That left a half-built `.venv`
with no `bin/activate`, and **every following command failed as a consequence**:

- `source .venv/bin/activate` → `No such file or directory`
- `pip install -r requirements.txt` → fell through to system Python →
  `error: externally-managed-environment` (PEP 668)
- the next `git clone` was interrupted too, so `cd` and `make` failed on a
  directory that never existed

None of those were real errors. They were all downstream of one impatient
Ctrl+C. Steps that legitimately look frozen: `apt update`/`full-upgrade`,
`python3 -m venv`, and `pip install .` for the matrix library.

### Do not paste a whole block of commands at once

Bash keeps reading the rest of your pasted buffer as separate commands. If
command 2 of 8 fails or gets interrupted, commands 3–8 still run against a
broken state and produce a wall of misleading errors. Paste one block, wait for
the prompt, then paste the next.

### `pkill -f <pattern>` can kill your own shell

`pkill -f "auth-only"` matches **any** process whose command line contains that
text — including the very `bash -c` running your `pkill`. The command silently
killed itself mid-script, which looked like a mysterious hang.

Use a pattern that cannot match itself:

```bash
pkill -f "spotify_matrix[.]py --auth-onl[y]"
```

### Generate the SSH key on the laptop, not the Pi

Running `ssh-keygen` while SSH'd into the Pi creates a key that lets the *Pi*
log in elsewhere. It does nothing for passwordless access *to* the Pi. The key
must be generated on the machine you are connecting **from**.

### `dphys-swapfile` does not exist on trixie

It fails with `command not found` and `sed: can't read /etc/dphys-swapfile`.
Use the `fallocate` swapfile method in [Step 4](#step-4--add-swap-before-anything-heavy).

### `make build-python` does not exist any more

Upstream replaced the Makefile Python build with `pyproject.toml` +
`scikit-build-core`. Running it from either the repo root *or*
`bindings/python` fails identically with
`No rule to make target 'build-python'`. Use `pip install .` from the
repository root.

### Do not trust the Spotify success page

It is printed before the code is validated. Always confirm
`.cache/spotify_token.json` exists on the Pi. See
[Step 9](#step-9--one-time-spotify-authorization).

### Add swap before the upgrade, not after

Sequence matters. Running `apt full-upgrade` on 415 MB of RAM with only zram
available is what makes it crawl for 30+ minutes. Do
[Step 4](#step-4--add-swap-before-anything-heavy) first.

### A dropped SSH session does not always kill the remote command

After closing a terminal mid-upgrade, the `apt full-upgrade` process was still
running on the Pi ~13 minutes later and still holding the dpkg lock. The right
move is to **wait for it**, not to kill it or delete lock files:

```bash
ps -p <pid> -o pid,etime,pcpu,stat,cmd
```

Deleting `/var/lib/dpkg/lock-frontend` while a real apt process holds it is how
you actually corrupt the package database.

---

## Chapter 3: Features & Overview

- **✨ Original / Auto-Cycling** — idle screen → spinning disc → synced lyrics,
  resetting on each new track. The disc-before-lyrics delay is adjustable.
- **🎵 Spinning CD View** — album art cropped into a vinyl record that spins with
  playback, with a progress ring around the rim and eased spin-up/spin-down.
- **📝 Synchronized Lyrics** — live synced lyrics from LRCLIB in three styles:
  Karaoke (word-wrapped, per-word highlight), Scroll, and Pop.
- **🎹 Instrumental Detection** — a pulsing audio-bars visualizer instead of a
  "no lyrics" message.
- **🖼️ Album Art Mode** — the cover filling all 64×64 with a hairline progress
  bar, optionally with a slow Ken Burns drift.
- **🌌 Ambient Idle Screens** — Clock, Plasma, Matrix Rain, Starfield, Game of
  Life, Fireplace, or Cycle.
- **🎨 Accent Colours** — 7 presets, a custom picker, or **Auto** (follows the
  album art).
- **🕐 Clock Mode** — digital clock with date, day, and a sweeping seconds dot.
- **🖼️ Custom Slate Canvas** — WYSIWYG editor to upload images/GIFs, overlay
  text, and cast instantly.
- **📺 Live Preview** — see what the panel is showing, from the web UI.
- **⏯️ Playback Control** — play, pause, skip.
- **📱 Web Control Panel** — full dashboard from any device on your Wi-Fi.
- **💾 Persistent Settings** — saved to `.cache/settings.json`, debounced and
  written atomically; survives reboots.
- **🔌 Plug & Play** — runs as a systemd service. Plug the Pi in and it works.

---

## Chapter 4: Web Control Panel & Settings

No SSH needed. From any browser on the same Wi-Fi:

```
http://matrixspot.local:5000
```

**Now Playing** — track, artist, playback state, over a glassmorphism blur of
the album art.

**Saving your choices** — every configuration change automatically saves on
the Pi in `~/Documents/SpotifyMatrix/.cache/settings.json`. Wait for **Saved on
Pi** before turning off power. **Save now** also flushes any pending editor
text, and retries if the disk was temporarily unable to save. An error is shown
instead of claiming success. Disconnecting HDMI has no effect on these settings.

Saved choices include Original or another display mode, idle screen, lyric
style, both font sizes, smart scrolling, lyric lead, brightness, spin/text
speeds, disc duration, line width, progress ring, art pan, accent choice and
custom color, and Sleep. Custom images/GIFs and the last cast restore too.
The editor's draft image, text, color, preview switch, and expanded advanced
settings/live lyrics choices are also stored on the Pi. Editing a draft does
not replace the last cast until you press **Cast to Matrix**. Browsers cannot
restore a local file-picker selection, but its saved image is restored in the
canvas. An empty Custom Slate without saved media falls back to Original.

**Reset All** saves the defaults and clears saved custom media/drafts. Play,
pause, skip, and clearing logs are actions rather than configuration to repeat
at boot. Spotify's current song and playback progress are fetched fresh.

Install the current service template from Step 10 and restart the service after
updating an existing installation. Its `--prefer-saved-settings` flag lets web
choices take priority over boot defaults. Manual command-line runs can still
override a saved setting with an explicit flag.

**🎤 Live Lyrics** — synced lyrics streamed to your phone, drift-corrected
against the matrix, with the active line in your accent colour.

**Display Mode** — Original (auto-cycle), CD, Lyrics, Album Art, Idle Screen,
or Clock.

**🎵 Lyrics Settings** — style (Karaoke / Scroll / Pop), Smart Scroll toggle,
independent font sizes per style, and a lyrics-lead slider (up to 500 ms) to
show words slightly before they are sung.

**🖼️ Custom Slate** — upload an image or animated GIF, add text and colour, cast
it to the panel.

**💬 Custom Message** — replaces the title/artist crawl in CD mode until cleared.

**🎨 Accent Colour** — 7 presets, a full hex picker, or Auto (from album art).
Updates the matrix and the web UI instantly.

**⚙️ General Settings** — brightness (1–100), spin speed (RPM), text scroll
speed. API polling is automatic and adaptive: 10 s for the first 30 s of a
track, 5 s while playing, 1.5 s in the last 10 seconds to catch the transition
cleanly, and 30 s when paused for over a minute.

**Actions** — Reset All Defaults, and View Live Logs at `/logs`.

---

## Chapter 5: Dual-Mode (Manual Override)

To run the code by hand (testing changes, debugging), stop the service first —
two processes cannot both drive the panel.

```powershell
ssh adi@matrixspot.local
```

```bash
sudo systemctl stop spotifymatrix.service
cd ~/Documents/SpotifyMatrix
sudo -E .venv/bin/python3 spotify_matrix.py \
    --rows 64 --cols 64 --chain-length 1 --parallel 1 \
    --gpio-slowdown 5 --no-hardware-pulse \
    --hardware-mapping adafruit-hat-pwm \
    --pwm-bits 9 --limit-refresh-rate-hz 200 \
    --web-port 5000
```

When finished:

```bash
sudo systemctl start spotifymatrix.service
```

There is also `--mock-output`, which runs with no hardware at all — useful for
testing on a laptop.

---

## Chapter 6: Maintenance & Re-Auth

### Day-to-day

Use `matrix_control.ps1` from your laptop for brightness, updates, re-auth,
logs, status and reboot. It authenticates with SSH keys.

```powershell
cd C:\path\to\SpotifyMatrix
.\matrix_control.ps1
```

### Updating the code

Menu **4 → 1 (Update code)**, or manually:

```bash
cd ~/Documents/SpotifyMatrix
git pull
sudo systemctl restart spotifymatrix.service
```

### 6-month re-authorization

Spotify refresh tokens expire roughly every 6 months. The panel stays on the
idle screen and the logs show `invalid_grant`.

```bash
sudo systemctl stop spotifymatrix.service
rm ~/Documents/SpotifyMatrix/.cache/spotify_token.json
```

Then repeat [Step 9](#step-9--one-time-spotify-authorization) — including
verifying the token file actually appeared — and restart:

```bash
sudo systemctl start spotifymatrix.service
```

### If the Pi will not boot

**Do not reflash.** A solid, unblinking green LED almost always means a bad
`cmdline.txt`. Put the card in any PC, open the `bootfs` FAT partition, and copy
`cmdline.txt.matrixbak` over `cmdline.txt`. Full detail in
[BOOT_RECOVERY.md](BOOT_RECOVERY.md).

### Log storage

The journal can fill an SD card. Cap it with menu **4 → 6**, or:

```bash
sudo journalctl --vacuum-size=30M --vacuum-time=2d
```

---

## Chapter 7: Flicker & Performance Tuning

### Do not set `CPUAffinity=3`

This is the most important correction in this guide. `IMPROVEMENTS.md` P6
recommended pairing `isolcpus=3` with `CPUAffinity=3` in the systemd unit,
reasoning that isolating a core without pinning anything to it "takes a quarter
of the CPU away and gives it to nobody."

**That reasoning is wrong, because the library pins itself.** In
`lib/gpio.cc`, `Timers::Init()` sets `cpu3`'s scaling governor to `performance`
and comments *"If we have it, we run the update thread on core3"*, and
`lib/thread.cc` applies a `pthread_setaffinity_np` to the realtime thread. The
library detects the isolated core and moves its own RT thread there.

`CPUAffinity=` in systemd constrains **every thread in the process**. Setting it
to `3` therefore drags the Python renderer, the web server and the Spotify
poller onto core 3 as well, where they compete with the realtime refresh thread
that is supposed to own that core exclusively. Because
`sched_rt_runtime_us` is `990000`, the RT thread is forced to yield 10 ms every
second — and with three starved threads queued behind it, that yield is exactly
when the panel visibly flickers.

Observed on the real device. **With** `CPUAffinity=3` — everything jammed onto
core 3:

```
tid=1101 TS   -  psr=3
tid=1281 FF  99  psr=3     <- RT thread, sharing
tid=1282 TS   -  psr=3
tid=1283 TS   -  psr=3
tid=1284 TS   -  psr=3
```

**Without** it — RT thread alone on the isolated core, which is the intended
design:

```
tid=1983 TS   -  psr=2
tid=1985 FF  99  psr=3     <- RT thread, exclusive
tid=1986 TS   -  psr=2
tid=1987 TS   -  psr=1
tid=1988 TS   -  psr=0
```

To remove it from an existing install:

```bash
sudo sed -i '/^CPUAffinity=3/d' /etc/systemd/system/spotifymatrix.service
sudo systemctl daemon-reload
sudo systemctl restart spotifymatrix
```

Keep `isolcpus=3` — it is what reserves the core for the library to claim.

### Other flicker levers, in order of likely impact

Change **one at a time** and look at the panel between each, or you will not
know which one mattered.

**`--gpio-slowdown`** (currently `5`). This stretches every GPIO write; higher
means a lower refresh rate and more visible flicker. hzeller's guidance is
roughly 1–2 for Pi 1/2/3-class boards and 4 for a Pi 4. The **Pi Zero 2 W is
BCM2710, Pi 3-class**, so `5` is likely tuned for the wrong board. Try `2`:

```bash
sudo sed -i 's/--gpio-slowdown 5/--gpio-slowdown 2/' /etc/systemd/system/spotifymatrix.service
sudo systemctl daemon-reload && sudo systemctl restart spotifymatrix
```

If you see ghosting or corrupted pixels, it is too low — go back up.

**`--limit-refresh-rate-hz`** (currently `200`). A hard cap on refresh rate.
200 Hz is low enough to be perceptible, especially in peripheral vision. Try
raising it or removing the flag entirely.

**`--pwm-bits`** (currently `9`). Fewer bits means a faster refresh but less
colour depth. Lowering to `8` buys refresh rate at the cost of gradients.

**Confirm root.** The RT thread needs `SCHED_FIFO` priority, which needs root.
If the journal shows `Can't set realtime thread priority`, the unit is not
running as root and colour stability will be bad. Check for a `FF 99` thread:

```bash
ps -eLo pid,tid,cls,rtprio,psr,comm | grep python3
```

### Healthy baseline

For reference, a correctly configured Zero 2 W reports:

| Check | Expected |
|---|---|
| `cat /sys/devices/system/cpu/isolated` | `3` |
| RT thread | exactly one `FF 99`, alone on `psr=3` |
| `cat /proc/sys/kernel/sched_rt_runtime_us` | `990000` |
| `cat /sys/devices/system/cpu/cpu3/cpufreq/scaling_governor` | `performance` |
| `systemctl is-active bluetooth` | `inactive` |
| `grep '^dtparam=audio' /boot/firmware/config.txt` | `dtparam=audio=off` |

The last three are set automatically by the library or by
[Step 11](#step-11--disable-bluetooth-and-onboard-audio).

---

*Powered by `hzeller/rpi-rgb-led-matrix`, the Spotify Web API, and LRCLIB.*
