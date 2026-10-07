# ============================================================
#  SPOTIFY MATRIX - CONTROL PANEL
#  Automates SSH management for your Pi LED Matrix display
# ============================================================

# Load environment variables from .env file
if (Test-Path ".env") {
    foreach ($line in Get-Content .env) {
        if ($line -match "^\s*#" -or $line -match "^\s*$") { continue }
        $name, $value = $line -split '=', 2
        $name = $name.Trim()
        $value = $value.Trim() -replace '^"(.*)"$', '$1' -replace "^'(.*)'$", '$1'
        Set-Item -Path "Env:$name" -Value $value
    }
}

# PI_HOST is the ssh target, e.g. adi@matrixspot.local. It used to be
# required, validated, and then ignored — every ssh call hardcoded the host.
# PI_PASS is gone entirely: this script authenticates with keys, so storing a
# plaintext password in .env bought nothing and leaked a credential.
$PI_HOST = $env:PI_HOST

if (-not $PI_HOST) {
    Write-Host "Error: PI_HOST not set in .env (expected e.g. adi@matrixspot.local)" -ForegroundColor Red
    exit 1
}

# Accept a bare hostname too, so an existing .env without the user still works.
if ($PI_HOST -notmatch '@') { $PI_HOST = "adi@$PI_HOST" }
$PI_USER = ($PI_HOST -split '@')[0]
$PI_NAME = ($PI_HOST -split '@')[1]
$PI_DIR = "~/Documents/SpotifyMatrix"
# systemd needs an absolute path; $PI_DIR's ~ is only expanded by a shell.
$PI_ABS_DIR = "/home/$PI_USER/Documents/SpotifyMatrix"
$SERVICE = "spotifymatrix.service"
# Derived from $PI_USER rather than hardcoding /home/adi — the whole point of
# honouring PI_HOST is that the user is configurable.
$EXECSTART_BASE = "$PI_ABS_DIR/.venv/bin/python3 spotify_matrix.py --rows 64 --cols 64 --chain-length 1 --parallel 1 --gpio-slowdown 5 --no-hardware-pulse --hardware-mapping adafruit-hat-pwm --pwm-bits 9 --limit-refresh-rate-hz 200 --prefer-saved-settings"
$SERVICE_FILE = "/etc/systemd/system/spotifymatrix.service"

# ── Colour helpers ──────────────────────────────────────────
function Write-Header {
    Clear-Host
    Write-Host ""
    Write-Host "  +====================================================+" -ForegroundColor Cyan
    Write-Host "  |       [*]  SPOTIFY MATRIX CONTROL PANEL  [*]      |" -ForegroundColor Cyan
    $hostLine = ("  |  Pi: $PI_NAME ($PI_USER)").PadRight(55) + "|"
    Write-Host $hostLine -ForegroundColor DarkCyan
    Write-Host "  +====================================================+" -ForegroundColor Cyan
    Write-Host ""
}

function Write-Section($title) {
    Write-Host ""
    Write-Host "  -- $title --" -ForegroundColor DarkYellow
    Write-Host ""
}

function Write-Success($msg) { Write-Host "  [OK]  $msg" -ForegroundColor Green }
function Write-Info($msg) { Write-Host "  [>>]  $msg" -ForegroundColor Cyan }
function Write-Warn($msg) { Write-Host "  [!!]  $msg" -ForegroundColor Yellow }
function Write-Err($msg) { Write-Host "  [XX]  $msg" -ForegroundColor Red }

# ── Open interactive SSH window ─────────────────────────────
# Uses a here-string to write the temp launcher — guaranteed real newlines in PS 5.1.
# $WindowTitle and $psEscaped expand NOW; `$Host and `$remoteCmd stay as literals
# in the child script so they resolve inside the child PowerShell session.
function Open-SshWindow {
    param(
        [string]$RemoteCommand,
        [string]$WindowTitle = "Matrix Pi"
    )

    $tempSh = [System.IO.Path]::GetTempFileName() + ".ps1"

    # Escape single-quotes for a PS single-quoted string  ( ' becomes '' )
    $psEscaped = $RemoteCommand.Replace("'", "''")

    # Here-string: real newlines guaranteed. Backtick-$ stays literal in output.
    $content = @"
`$Host.UI.RawUI.WindowTitle = '$WindowTitle'
`$remoteCmd = '$psEscaped'
& ssh -o StrictHostKeyChecking=no -t $PI_HOST `$remoteCmd
Write-Host ''
Write-Host '[Session ended - press Enter to close]' -ForegroundColor DarkGray
Read-Host
"@
    [System.IO.File]::WriteAllText($tempSh, $content, [System.Text.UTF8Encoding]::new($false))
    Start-Process "powershell.exe" -ArgumentList "-NoExit", "-File", $tempSh
}

# ── Core SSH runner (non-interactive, returns output) ───────
# KEY: pass $Command as a bare variable to & ssh.
# PowerShell wraps it as a single Windows argument → SSH sends it as one string
# to the remote shell → remote bash handles && and ; natively. No bash -c needed.
function Invoke-SSH {
    param([string]$Command)
    $result = & ssh -o StrictHostKeyChecking=no $PI_HOST $Command 2>&1
    return $result
}

# ── Brightness prompt ────────────────────────────────────────
# Was duplicated in both menus, and both copies cast with [int]$val, which
# throws on anything non-numeric and dumped a PowerShell error at the user.
# Returns 0 when the input is unusable, so callers just check for > 0.
function Read-Brightness {
    $val = Read-Host "  Enter brightness (1-100)"
    $parsed = 0
    if (-not [int]::TryParse($val, [ref]$parsed)) {
        Write-Warn "'$val' is not a number."
        return 0
    }
    if ($parsed -lt 1 -or $parsed -gt 100) {
        Write-Warn "Must be between 1 and 100."
        return 0
    }
    return $parsed
}

# ── Pause helper ─────────────────────────────────────────────
function Pause-Menu {
    Write-Host ""
    Write-Host "  Press Enter to return to menu..." -ForegroundColor DarkGray
    Read-Host | Out-Null
}

# ════════════════════════════════════════════════════════════
#  ACTION FUNCTIONS
# ════════════════════════════════════════════════════════════

# ── 1. Manual Run ────────────────────────────────────────────
function Run-Manual {
    param([int]$Brightness = 60)

    Write-Section "MANUAL RUN  (Brightness: $Brightness)"
    Write-Info "Stopping autoboot service first to free GPIO pins..."
    Invoke-SSH -Command "sudo systemctl stop $SERVICE 2>/dev/null; echo DONE" | Out-Null

    Write-Info "Launching matrix in a new terminal window. Press Ctrl+C in that window to stop."
    $cmd = "cd $PI_DIR ; sudo -E .venv/bin/python3 spotify_matrix.py " +
    "--rows 64 --cols 64 --chain-length 1 --parallel 1 " +
    "--gpio-slowdown 5 --no-hardware-pulse " +
    "--hardware-mapping adafruit-hat-pwm " +
    "--pwm-bits 9 --limit-refresh-rate-hz 200 " +
    "--brightness $Brightness"

    Open-SshWindow -RemoteCommand $cmd -WindowTitle "MATRIX MANUAL - Brightness $Brightness"
    Write-Success "Terminal opened. The matrix is running live."
    Write-Warn "When you close that window the service will NOT auto-restart."
    Write-Warn "Use menu option 2 to re-enable autoboot when done."
}

# ── 2. Enable Autoboot ───────────────────────────────────────
function Enable-Autoboot {
    Write-Section "ENABLE AUTOBOOT SERVICE"
    Write-Info "Running: daemon-reload -> enable -> start"
    $out = Invoke-SSH -Command "sudo systemctl daemon-reload && sudo systemctl enable $SERVICE && sudo systemctl start $SERVICE && echo SUCCESS"
    if ($out -match "SUCCESS") {
        Write-Success "Autoboot enabled & service started!"
    }
    else {
        Write-Warn "SSH Output:"
        Write-Host "  $out" -ForegroundColor Gray
    }
}

# ── 3. Stop & disable Autoboot ───────────────────────────────
function Stop-Autoboot {
    Write-Section "STOP & DISABLE AUTOBOOT SERVICE"
    $out = Invoke-SSH -Command "sudo systemctl stop $SERVICE && sudo systemctl disable $SERVICE && echo SUCCESS"
    if ($out -match "SUCCESS") {
        Write-Success "Service stopped and disabled. Pi will NOT auto-start on next reboot."
    }
    else {
        Write-Warn "SSH Output:"
        Write-Host "  $out" -ForegroundColor Gray
    }
}

# ── Stop temporarily (service re-enables on reboot) ─────────
function Stop-AutobootTemp {
    Write-Section "STOP SERVICE (temporary)"
    $out = Invoke-SSH -Command "sudo systemctl stop $SERVICE && echo STOPPED"
    if ($out -match "STOPPED") {
        Write-Success "Service stopped (will restart on next reboot)."
    }
    else {
        Write-Warn "SSH Output:"
        Write-Host "  $out" -ForegroundColor Gray
    }
}

# ── Change brightness and persist it through the web API ─────
function Set-ServiceBrightness {
    param([int]$Brightness)
    Write-Section "SAVE BRIGHTNESS TO $Brightness"
    $payload = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes((@{value=$Brightness} | ConvertTo-Json -Compress)))
    $remoteCommand = "printf %s '$payload' | base64 -d | curl --fail --silent --show-error -H 'Content-Type: application/json' --data-binary @- http://127.0.0.1:5000/api/brightness"
    $out = Invoke-SSH -Command $remoteCommand
    if (($out -join "`n") -match '"saved"\s*:\s*true') {
        Write-Success "Brightness applied and saved on the Pi for the next boot."
    } else {
        Write-Err "Could not save brightness. Make sure SpotifyMatrix is running."
        Write-Host ($out -join "`n") -ForegroundColor Gray
    }
}

# ── Check service status ─────────────────────────────────────
function Show-Status {
    Write-Section "SERVICE STATUS"
    $out = Invoke-SSH -Command "sudo systemctl status $SERVICE --no-pager -l 2>&1 | head -30"
    Write-Host $out -ForegroundColor Gray
}

# ── Watch live logs ──────────────────────────────────────────
function Watch-Logs {
    Write-Section "LIVE LOGS"
    Write-Info "Opening log stream in a new terminal (press Ctrl+C in that window to stop)..."
    Open-SshWindow -RemoteCommand "sudo journalctl -u $SERVICE -f" -WindowTitle "MATRIX LOGS - Live"
    Write-Success "Log window opened."
}

# ── Git pull & restart ───────────────────────────────────────
function Update-Code {
    Write-Section "UPDATE CODE (git pull)"
    Write-Info "Stopping service..."
    Invoke-SSH -Command "sudo systemctl stop $SERVICE 2>/dev/null" | Out-Null

    Write-Info "Pulling latest code from GitHub..."
    # Echo a sentinel only when git actually succeeds. Without this a failed
    # pull (conflict, no network, dirty tree) was indistinguishable from a
    # good one and the old code was silently restarted as if updated.
    $out = Invoke-SSH -Command "cd $PI_DIR && git pull 2>&1 && echo __PULL_OK__"

    # Flatten to a single string before testing. Invoke-SSH returns an array of
    # lines, and against an array PowerShell's -match/-notmatch are FILTERS, not
    # boolean tests: -notmatch returns every non-matching line, which is almost
    # always non-empty and therefore truthy. That made a successful pull report
    # as a failure.
    $outText = ($out | Out-String)

    Write-Host ""
    Write-Host ($outText -replace '__PULL_OK__', '').Trim() -ForegroundColor Gray
    Write-Host ""

    if ($outText -notmatch "__PULL_OK__") {
        Write-Err "git pull failed - service NOT restarted."
        Write-Warn "Fix the problem above on the Pi, then run this again."
        return
    }

    if ($outText -match "Already up to date") {
        Write-Info "Already up to date."
    }
    else {
        Write-Success "Code updated."
    }

    Write-Info "Restarting service..."
    Enable-Autoboot
}

# ── Reboot Pi ────────────────────────────────────────────────
function Reboot-Pi {
    Write-Warn "This will reboot the Pi. The matrix will restart after ~30 seconds."
    $confirm = Read-Host "  Type YES to confirm"
    if ($confirm -eq "YES") {
        Invoke-SSH -Command "sudo reboot" | Out-Null
        Write-Success "Reboot command sent. Wait ~30 seconds then you can reconnect."
    }
    else {
        Write-Info "Reboot cancelled."
    }
}

# ── Re-authenticate Spotify token ───────────────────────────
function Reauth-Spotify {
    Write-Section "SPOTIFY RE-AUTHENTICATION"
    Write-Warn "Use this when the matrix stops updating and shows 'invalid_grant' errors."
    Write-Warn "Spotify refresh tokens expire roughly every 6 months."
    Write-Host ""
    Write-Host "  STEPS THAT WILL HAPPEN:" -ForegroundColor White
    Write-Host "  1) Service stopped + old token deleted" -ForegroundColor DarkGray
    Write-Host "  2) A NEW window opens with the SSH tunnel (KEEP IT OPEN)" -ForegroundColor DarkGray
    Write-Host "  3) A SECOND new window runs the auth command" -ForegroundColor DarkGray
    Write-Host "  4) Copy the URL from window 3 into your browser and click Agree" -ForegroundColor DarkGray
    Write-Host "  5) Close both new windows, then use menu 2 -> Enable Autoboot" -ForegroundColor DarkGray
    Write-Host ""
    $confirm = Read-Host "  Ready? Type YES to start"
    if ($confirm -ne "YES") { Write-Info "Cancelled."; return }

    Write-Info "Stopping service & deleting expired token..."
    Invoke-SSH -Command "sudo systemctl stop $SERVICE; rm -f $PI_DIR/.cache/spotify_token.json; echo DONE" | Out-Null

    Write-Info "Opening SSH tunnel window (KEEP THIS OPEN until auth is complete)..."
    Start-Process "powershell.exe" -ArgumentList "-NoExit", "-Command",
    "`$Host.UI.RawUI.WindowTitle='SSH TUNNEL - KEEP OPEN'; ssh -L 8888:127.0.0.1:8888 $PI_HOST"

    Start-Sleep -Seconds 4

    Write-Info "Opening auth command window..."
    Open-SshWindow `
        -RemoteCommand "cd $PI_DIR && .venv/bin/python3 spotify_matrix.py --auth-only --no-browser" `
        -WindowTitle "SPOTIFY AUTH - Copy the URL to your browser"

    Write-Host ""
    Write-Success "Both windows are open."
    Write-Info "In the auth window, copy the long URL and paste it into your browser."
    Write-Warn "Do NOT refresh the browser tab after it says 'authorization complete'."
    Write-Warn "That page is printed before the code is checked, so a reload can"
    Write-Warn "wipe the captured code and leave the Pi waiting forever."
    Write-Host ""
    Read-Host "  Press Enter once you have clicked Agree in the browser"

    # The browser's success page is not evidence: the callback handler prints it
    # before verifying a code actually arrived. The token file on the Pi is the
    # only thing that proves the auth worked.
    Write-Info "Verifying the token actually landed on the Pi..."
    $tokenCheck = (Invoke-SSH -Command "test -f $PI_DIR/.cache/spotify_token.json && echo TOKEN_OK || echo TOKEN_MISSING" | Out-String)

    if ($tokenCheck -match "TOKEN_OK") {
        Write-Success "Token saved. Locking down its permissions..."
        Invoke-SSH -Command "chmod 700 $PI_DIR/.cache; chmod 600 $PI_DIR/.cache/spotify_token.json; echo DONE" | Out-Null
        Write-Info "Close the two auth windows, then restarting the service..."
        $out = Invoke-SSH -Command "sudo systemctl start $SERVICE && echo STARTED"
        if ($out -match "STARTED") { Write-Success "Service restarted. Re-auth complete." }
        else { Write-Warn "Could not restart: $out" }
    }
    else {
        Write-Err "No token file on the Pi - the authorization did NOT work."
        Write-Warn "Ignore what the browser said; it reports success either way."
        Write-Warn "Close both windows and run this option again. The URL contains a"
        Write-Warn "one-time 'state' value, so an old link will never work."
    }
}

# ── Cap journal logs (SD card protection) ────────────────────
function Cap-Logs {
    Write-Section "CAP JOURNAL LOGS (SD Card Protection)"
    Write-Info "Configuring journald: max 30MB on disk, keep 2 days only..."

    # Set SystemMaxUse=30M and MaxRetentionSec=2day in journald.conf
    $configCmd = @(
        "sudo sed -i 's/^#*SystemMaxUse=.*/SystemMaxUse=30M/' /etc/systemd/journald.conf",
        "sudo sed -i 's/^#*MaxRetentionSec=.*/MaxRetentionSec=2day/' /etc/systemd/journald.conf",
        "grep -q '^SystemMaxUse=' /etc/systemd/journald.conf || echo 'SystemMaxUse=30M' | sudo tee -a /etc/systemd/journald.conf > /dev/null",
        "grep -q '^MaxRetentionSec=' /etc/systemd/journald.conf || echo 'MaxRetentionSec=2day' | sudo tee -a /etc/systemd/journald.conf > /dev/null",
        "sudo systemctl restart systemd-journald",
        "sudo journalctl --vacuum-size=30M --vacuum-time=2d 2>&1",
        "echo CAP_DONE"
    ) -join "; "

    $out = Invoke-SSH -Command $configCmd
    if ($out -match "CAP_DONE") {
        Write-Success "Journal capped at 30MB / 2 days. Old logs purged."
        # Show how much space is used now
        $usage = Invoke-SSH -Command "journalctl --disk-usage 2>&1"
        Write-Info "Current journal usage: $usage"
    }
    else {
        Write-Warn "Output: $out"
    }
}

# ── Anti-flicker system optimization (one-time) ────────────────
# ── Where the real boot partition is mounted ─────────────────────
# Raspberry Pi OS Bookworm moved the FAT boot partition from /boot to
# /boot/firmware. Writing to the wrong one is either a silent no-op (a file
# on the ext4 root that the bootloader never reads) or, worse, an edit to a
# file the firmware DOES read while you think it is inert.
function Get-BootPath {
    $probe = Invoke-SSH -Command "if [ -f /boot/firmware/cmdline.txt ]; then echo /boot/firmware; elif [ -f /boot/cmdline.txt ]; then echo /boot; else echo NONE; fi"
    return ($probe | Out-String).Trim()
}

# ── Restore boot files from the backups this script makes ────────
function Restore-BootConfig {
    Write-Section "RESTORE BOOT CONFIG FROM BACKUP"
    $bootPath = Get-BootPath
    if ($bootPath -eq "NONE") { Write-Err "Could not locate the boot partition."; return }

    $listing = (Invoke-SSH -Command "ls -la $bootPath/*.matrixbak 2>/dev/null || echo NO_BACKUPS" | Out-String)
    if ($listing -match "NO_BACKUPS") {
        Write-Warn "No backups found in $bootPath."
        Write-Info "If the Pi will not boot, see BOOT_RECOVERY.md - you can fix"
        Write-Info "this from any PC with the SD card, without reflashing."
        return
    }
    Write-Host $listing.Trim() -ForegroundColor Gray
    Write-Host ""
    if ((Read-Host "  Restore these? Type YES") -ne "YES") { Write-Info "Cancelled."; return }

    $cmd = "for f in $bootPath/*.matrixbak; do sudo cp `"`$f`" `"`${f%.matrixbak}`"; done; sync; echo RESTORED"
    $out = (Invoke-SSH -Command $cmd | Out-String)
    if ($out -match "RESTORED") {
        Write-Success "Boot files restored. Reboot to apply."
    }
    else {
        Write-Warn "Restore output: $($out.Trim())"
    }
}

function Optimize-AntiFlicker {
    Write-Section "ANTI-FLICKER OPTIMIZATION"

    # This function edits files the Pi needs in order to boot at all. A bad
    # cmdline.txt means a kernel that cannot find its root filesystem, which
    # looks like a solid green ACT LED and no boot. So: back everything up,
    # write in a way that preserves the FAT directory entry, validate before
    # committing, and sync before any reboot.
    $bootPath = Get-BootPath
    if ($bootPath -eq "NONE") {
        Write-Err "Could not find cmdline.txt in /boot or /boot/firmware."
        Write-Warn "Aborting rather than guessing at boot-critical paths."
        return
    }
    Write-Info "Boot partition detected at: $bootPath"

    $cores = 0
    [void][int]::TryParse((Invoke-SSH -Command "nproc" | Out-String).Trim(), [ref]$cores)
    Write-Info "CPU cores reported: $cores"

    $doIsolate = $cores -ge 4
    if (-not $doIsolate) {
        Write-Warn "Fewer than 4 cores - skipping isolcpus=3."
        Write-Warn "Isolating a core that does not exist breaks the service."
    }

    Write-Host ""
    Write-Host "  What will be done:" -ForegroundColor White
    if ($doIsolate) {
        Write-Host "  - Back up cmdline.txt and config.txt (*.matrixbak)" -ForegroundColor DarkGray
        Write-Host "  - Isolate CPU core 3 for the matrix (isolcpus=3)" -ForegroundColor DarkGray
        Write-Host "  - Remove any stale CPUAffinity pin (the library pins itself)" -ForegroundColor DarkGray
    }
    Write-Host "  - Disable onboard audio (conflicts with PWM timing)" -ForegroundColor DarkGray
    Write-Host "  - Disable Bluetooth service (frees resources)" -ForegroundColor DarkGray
    Write-Host ""
    Write-Warn "Boot files are backed up first. If the Pi ever fails to boot,"
    Write-Warn "BOOT_RECOVERY.md shows how to fix it from a PC - no reflash."
    Write-Host ""
    if ((Read-Host "  Apply optimizations? Type YES to confirm") -ne "YES") {
        Write-Info "Cancelled."; return
    }

    # ── 0. Back up both boot files ───────────────────────────────
    Write-Info "Backing up boot files..."
    $bk = "sudo cp -a $bootPath/cmdline.txt $bootPath/cmdline.txt.matrixbak; " +
    "[ -f $bootPath/config.txt ] && sudo cp -a $bootPath/config.txt $bootPath/config.txt.matrixbak; " +
    "sync; echo BACKUP_OK"
    if ((Invoke-SSH -Command $bk | Out-String) -match "BACKUP_OK") {
        Write-Success "Backups written (*.matrixbak)."
    }
    else {
        Write-Err "Backup failed - refusing to edit boot files."
        return
    }

    # ── 1. isolcpus=3, written safely ────────────────────────────
    if ($doIsolate) {
        Write-Info "Setting isolcpus=3 in $bootPath/cmdline.txt..."

        # Built from single-quoted chunks so PowerShell leaves the shell's own
        # $ variables alone. Three things make this safe where `sed -i` was not:
        #   * `tr -d '\r\n'` collapses the file to exactly one line, so a stray
        #     trailing blank line cannot become a second kernel-args line and a
        #     CRLF file cannot smuggle a CR into the middle of the arguments.
        #   * the result is validated (one line, still has root=) BEFORE it is
        #     allowed to replace the live file.
        #   * `cp` truncates the existing file in place instead of unlinking
        #     and recreating it the way `sed -i` does, so the FAT directory
        #     entry is never rewritten. Then sync.
        $cl = "$bootPath/cmdline.txt"
        $iso = 'CL="' + $cl + '"; ' +
        'if grep -q "isolcpus=" "$CL"; then echo ALREADY_SET; else ' +
        'NEW="$(tr -d ''\r\n'' < "$CL") isolcpus=3"; ' +
        'printf ''%s\n'' "$NEW" > /tmp/cmdline.new; ' +
        'if [ "$(wc -l < /tmp/cmdline.new)" -eq 1 ] && grep -q "root=" /tmp/cmdline.new; then ' +
        'sudo cp /tmp/cmdline.new "$CL"; sync; echo ISOLCPUS_DONE; ' +
        'else echo VALIDATION_FAILED; fi; ' +
        'rm -f /tmp/cmdline.new; fi'

        $out1 = (Invoke-SSH -Command $iso | Out-String)
        if ($out1 -match "ALREADY_SET") {
            Write-Info "isolcpus already configured."
        }
        elseif ($out1 -match "ISOLCPUS_DONE") {
            Write-Success "isolcpus=3 added."
        }
        elseif ($out1 -match "VALIDATION_FAILED") {
            Write-Err "New cmdline.txt failed validation - original left untouched."
            return
        }
        else {
            Write-Warn "isolcpus output: $($out1.Trim())"
        }

        # Show the operator the actual line that will boot the Pi.
        $shown = (Invoke-SSH -Command "cat $cl" | Out-String).Trim()
        Write-Host ""
        Write-Host "  cmdline.txt is now:" -ForegroundColor White
        Write-Host "  $shown" -ForegroundColor Gray
        Write-Host ""
        if ($shown -notmatch "root=") {
            Write-Err "cmdline.txt has no root= parameter. Restoring backup!"
            Invoke-SSH -Command "sudo cp $cl.matrixbak $cl; sync" | Out-Null
            Write-Warn "Backup restored. Nothing further applied."
            return
        }

        # ── 1b. Make sure the service is NOT pinned ──────────────
        # This used to add CPUAffinity=3, on the theory that isolcpus reserves
        # a core and nothing claims it. The library claims it itself:
        # lib/gpio.cc runs its update thread on cpu3 and lib/thread.cc pins the
        # realtime thread there. systemd's CPUAffinity constrains EVERY thread,
        # so pinning dragged the renderer, web server and poller onto core 3 to
        # fight the SCHED_FIFO refresh thread — which is a flicker source, not a
        # fix. Strip it if an older run of this script left it behind.
        Write-Info "Ensuring the service is not CPU-pinned (it must not be)..."
        $cmdAff = "grep -q '^CPUAffinity=' $SERVICE_FILE && " +
        "(sudo sed -i '/^CPUAffinity=/d' $SERVICE_FILE && sudo systemctl daemon-reload && echo AFFINITY_REMOVED) || " +
        "echo AFFINITY_ABSENT"
        $outAff = (Invoke-SSH -Command $cmdAff | Out-String)
        if ($outAff -match "AFFINITY_ABSENT") { Write-Info "No CPUAffinity set (correct)." }
        elseif ($outAff -match "AFFINITY_REMOVED") { Write-Success "Removed stale CPUAffinity pin." }
        else { Write-Warn "CPUAffinity output: $($outAff.Trim())" }
    }

    # ── 2. Disable onboard audio ─────────────────────────────────
    Write-Info "Disabling onboard audio..."
    $cfg = "$bootPath/config.txt"
    $aud = 'CF="' + $cfg + '"; ' +
    'if grep -q "^dtparam=audio=off" "$CF"; then echo ALREADY_OFF; else ' +
    'sudo cp "$CF" /tmp/config.new 2>/dev/null || cp "$CF" /tmp/config.new; ' +
    'if grep -q "^dtparam=audio=on" /tmp/config.new; then ' +
    'sed -i "s/^dtparam=audio=on/dtparam=audio=off/" /tmp/config.new; ' +
    'else printf ''dtparam=audio=off\n'' >> /tmp/config.new; fi; ' +
    'sudo cp /tmp/config.new "$CF"; sync; rm -f /tmp/config.new; echo AUDIO_OFF; fi'
    $out2 = (Invoke-SSH -Command $aud | Out-String)
    if ($out2 -match "ALREADY_OFF") { Write-Info "Audio already disabled." }
    elseif ($out2 -match "AUDIO_OFF") { Write-Success "Onboard audio disabled." }
    else { Write-Warn "Audio output: $($out2.Trim())" }

    # ── 3. Disable Bluetooth (systemd only, no boot files) ───────
    Write-Info "Disabling Bluetooth service..."
    $out3 = (Invoke-SSH -Command "sudo systemctl disable bluetooth.service 2>/dev/null; sudo systemctl stop bluetooth.service 2>/dev/null; echo BT_DONE" | Out-String)
    if ($out3 -match "BT_DONE") { Write-Success "Bluetooth disabled." }

    Write-Host ""
    Write-Success "All optimizations applied."
    Write-Info "Backups kept at $bootPath/*.matrixbak (menu option 9 restores them)."
    Write-Warn "A REBOOT is required for isolcpus and audio to take effect."
    Write-Host ""
    $reboot = Read-Host "  Reboot now? (yes/no)"
    if ($reboot -eq "yes") {
        # sync twice and pause: the boot partition is FAT, and a reboot that
        # races an unflushed write is how boot files get truncated.
        Invoke-SSH -Command "sync; sleep 1; sync" | Out-Null
        Write-Info "Filesystems synced. Rebooting..."
        Invoke-SSH -Command "sudo reboot" | Out-Null
        Write-Success "Rebooting - wait ~30s then reconnect."
        Write-Info "If it does not come back, see BOOT_RECOVERY.md."
    }
    else {
        Write-Info "Not rebooting. Changes apply on the next boot."
    }
}

# ── Monitor system resources ──────────────────────────────────
function Check-Resources {
    Write-Section "SYSTEM RESOURCES (htop)"
    Write-Info "Opening htop in a new window. Press 'q' in that window to quit."
    $cmd = "htop"
    Open-SshWindow -RemoteCommand $cmd -WindowTitle "MATRIX SYSTEM RESOURCES"
    Write-Success "htop opened in a new terminal window."
}

# ════════════════════════════════════════════════════════════
#  SUB-MENUS
# ════════════════════════════════════════════════════════════

function Menu-Manual {
    while ($true) {
        Write-Header
        Write-Host "  [ MANUAL RUN ]" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "  Stops the autoboot service and launches the matrix in a new" -ForegroundColor DarkGray
        Write-Host "  SSH terminal so you can see real-time logs." -ForegroundColor DarkGray
        Write-Host ""
        Write-Host "  1)  Brightness 30   - dim, easy on the eyes at night" -ForegroundColor White
        Write-Host "  2)  Brightness 60   - default, balanced (recommended)" -ForegroundColor White
        Write-Host "  3)  Brightness 100  - full power" -ForegroundColor White
        Write-Host "  4)  Custom value    - type any value 1-100" -ForegroundColor White
        Write-Host "  0)  Back" -ForegroundColor DarkGray
        Write-Host ""
        $choice = Read-Host "  Select"
        switch ($choice) {
            "1" { Run-Manual -Brightness 30; Pause-Menu; return }
            "2" { Run-Manual -Brightness 60; Pause-Menu; return }
            "3" { Run-Manual -Brightness 100; Pause-Menu; return }
            "4" {
                $b = Read-Brightness
                if ($b -gt 0) { Run-Manual -Brightness $b; Pause-Menu; return }
            }
            "0" { return }
            default { Write-Warn "Invalid option." }
        }
    }
}

function Menu-Autoboot {
    while ($true) {
        Write-Header
        Write-Host "  [ AUTOBOOT / SERVICE MANAGEMENT ]" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "  1)  Enable autoboot     - daemon-reload + enable + start" -ForegroundColor White
        Write-Host "  2)  Set brightness  30  - edit service file + restart" -ForegroundColor White
        Write-Host "  3)  Set brightness  60  - edit service file + restart" -ForegroundColor White
        Write-Host "  4)  Set brightness 100  - edit service file + restart" -ForegroundColor White
        Write-Host "  5)  Custom brightness   - type any value 1-100" -ForegroundColor White
        Write-Host "  6)  Watch live logs     - open log stream in new window" -ForegroundColor White
        Write-Host "  7)  Check status        - show systemctl status output" -ForegroundColor White
        Write-Host "  0)  Back" -ForegroundColor DarkGray
        Write-Host ""
        $choice = Read-Host "  Select"
        switch ($choice) {
            "1" { Enable-Autoboot; Pause-Menu }
            "2" { Set-ServiceBrightness -Brightness 30; Pause-Menu }
            "3" { Set-ServiceBrightness -Brightness 60; Pause-Menu }
            "4" { Set-ServiceBrightness -Brightness 100; Pause-Menu }
            "5" {
                $b = Read-Brightness
                if ($b -gt 0) { Set-ServiceBrightness -Brightness $b; Pause-Menu }
            }
            "6" { Watch-Logs; Pause-Menu }
            "7" { Show-Status; Pause-Menu }
            "0" { return }
            default { Write-Warn "Invalid option." }
        }
    }
}

function Menu-StopAutoboot {
    while ($true) {
        Write-Header
        Write-Host "  [ STOP / DISABLE AUTOBOOT ]" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "  1)  Stop & DISABLE service  - Pi will NOT auto-start on next boot" -ForegroundColor White
        Write-Host "  2)  Stop service ONLY       - still enabled, restarts on next reboot" -ForegroundColor White
        Write-Host "  0)  Back" -ForegroundColor DarkGray
        Write-Host ""
        $choice = Read-Host "  Select"
        switch ($choice) {
            "1" { Stop-Autoboot; Pause-Menu }
            "2" { Stop-AutobootTemp; Pause-Menu }
            "0" { return }
            default { Write-Warn "Invalid option." }
        }
    }
}

function Menu-Maintenance {
    while ($true) {
        Write-Header
        Write-Host "  [ MAINTENANCE & TOOLS ]" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "  1)  Update code          - git pull then restart service" -ForegroundColor White
        Write-Host "  2)  Re-auth Spotify      - fix invalid_grant / expired token" -ForegroundColor White
        Write-Host "  3)  Reboot Pi            - full system reboot" -ForegroundColor White
        Write-Host "  4)  Check service status - quick health check" -ForegroundColor White
        Write-Host "  5)  Watch live logs      - open log stream in new window" -ForegroundColor White
        Write-Host "  6)  Cap log storage      - limit to 30MB / 2 days (SD card safe)" -ForegroundColor White
        Write-Host "  7)  Anti-flicker setup   - isolcpus + disable audio & BT (one-time)" -ForegroundColor White
        Write-Host "  8)  Check resources      - open htop to monitor CPU/RAM usage" -ForegroundColor White
        Write-Host "  9)  Restore boot config  - undo option 7 from its backups" -ForegroundColor White
        Write-Host "  0)  Back" -ForegroundColor DarkGray
        Write-Host ""
        $choice = Read-Host "  Select"
        switch ($choice) {
            "1" { Update-Code; Pause-Menu }
            "2" { Reauth-Spotify; Pause-Menu }
            "3" { Reboot-Pi; Pause-Menu }
            "4" { Show-Status; Pause-Menu }
            "5" { Watch-Logs; Pause-Menu }
            "6" { Cap-Logs; Pause-Menu }
            "7" { Optimize-AntiFlicker; Pause-Menu }
            "8" { Check-Resources; Pause-Menu }
            "9" { Restore-BootConfig; Pause-Menu }
            "0" { return }
            default { Write-Warn "Invalid option." }
        }
    }
}

# ════════════════════════════════════════════════════════════
#  MAIN MENU
# ════════════════════════════════════════════════════════════
function Main-Menu {
    while ($true) {
        Write-Header
        Write-Host "  What would you like to do?" -ForegroundColor White
        Write-Host ""
        Write-Host "  1)  Manual Run            - SSH in and run live with logs" -ForegroundColor Cyan
        Write-Host "  2)  Autoboot Management   - Enable / set brightness / restart" -ForegroundColor Green
        Write-Host "  3)  Stop Autoboot         - Disable the background service" -ForegroundColor Red
        Write-Host "  4)  Maintenance & Tools   - Update code, reauth, reboot" -ForegroundColor Magenta
        Write-Host "  5)  Quick Status          - Check if service is running" -ForegroundColor Yellow
        Write-Host "  0)  Exit" -ForegroundColor DarkGray
        Write-Host ""
        $choice = Read-Host "  Select"
        switch ($choice) {
            "1" { Menu-Manual }
            "2" { Menu-Autoboot }
            "3" { Menu-StopAutoboot }
            "4" { Menu-Maintenance }
            "5" { Show-Status; Pause-Menu }
            "0" {
                Write-Host ""
                Write-Host "  Goodbye! Your matrix keeps spinning." -ForegroundColor Cyan
                Write-Host ""
                exit
            }
            default { Write-Warn "Invalid option - try again." }
        }
    }
}

# ── Entry point ──────────────────────────────────────────────
Main-Menu
