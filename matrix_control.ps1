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
$SERVICE = "spotifymatrix.service"
$EXECSTART_BASE = "/home/adi/Documents/SpotifyMatrix/.venv/bin/python3 spotify_matrix.py --rows 64 --cols 64 --chain-length 1 --parallel 1 --gpio-slowdown 5 --no-hardware-pulse --hardware-mapping adafruit-hat-pwm --pwm-bits 9 --limit-refresh-rate-hz 200"
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
    $cmd = "cd /home/adi/Documents/SpotifyMatrix ; sudo -E .venv/bin/python3 spotify_matrix.py " +
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

# ── Change brightness in service file + restart ─────────────
function Set-ServiceBrightness {
    param([int]$Brightness)
    Write-Section "SET AUTOBOOT BRIGHTNESS TO $Brightness"
    Write-Info "Editing service file..."

    $newExec = "$EXECSTART_BASE --brightness $Brightness"
    $sedCmd = "sudo sed -i 's|^ExecStart=.*|ExecStart=$newExec|' $SERVICE_FILE"
    $out = Invoke-SSH -Command "$sedCmd && echo DONE"

    if ($out -match "DONE") {
        Write-Success "Service file updated with --brightness $Brightness."
    }
    else {
        Write-Warn "sed output: $out"
    }

    Write-Info "Reloading & restarting service..."
    $out2 = Invoke-SSH -Command "sudo systemctl daemon-reload && sudo systemctl enable $SERVICE && sudo systemctl restart $SERVICE && echo RESTARTED"
    if ($out2 -match "RESTARTED") {
        Write-Success "Service restarted at brightness $Brightness!"
    }
    else {
        Write-Warn "Output: $out2"
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
    Write-Host ""
    Write-Host ($out -replace '__PULL_OK__', '') -ForegroundColor Gray
    Write-Host ""

    if ($out -notmatch "__PULL_OK__") {
        Write-Err "git pull failed - service NOT restarted."
        Write-Warn "Fix the problem above on the Pi, then run this again."
        return
    }

    if ($out -match "Already up to date") {
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
    Write-Info "After clicking Agree, the auth window will confirm the token is saved."
    Write-Info "Then close both new windows and use option 2 to re-enable autoboot."
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
function Optimize-AntiFlicker {
    Write-Section "ANTI-FLICKER OPTIMIZATION"
    Write-Info "This applies system-level tweaks to reduce LED flicker."
    Write-Info "These only need to be run ONCE (they persist across reboots)."
    Write-Host ""
    Write-Host "  What will be done:" -ForegroundColor White
    Write-Host "  - Isolate CPU core 3 for the matrix (isolcpus=3)" -ForegroundColor DarkGray
    Write-Host "  - Disable onboard audio (conflicts with PWM timing)" -ForegroundColor DarkGray
    Write-Host "  - Disable Bluetooth service (frees resources)" -ForegroundColor DarkGray
    Write-Host ""
    $confirm = Read-Host "  Apply optimizations? Type YES to confirm"
    if ($confirm -ne "YES") { Write-Info "Cancelled."; return }

    # 1. isolcpus=3 in /boot/cmdline.txt (if not already present)
    Write-Info "Setting isolcpus=3 in /boot/cmdline.txt..."
    $cmd1 = "grep -q 'isolcpus=3' /boot/cmdline.txt && echo ALREADY_SET || sudo sed -i 's/$/ isolcpus=3/' /boot/cmdline.txt && echo ISOLCPUS_DONE"
    $out1 = Invoke-SSH -Command $cmd1
    if ($out1 -match "ALREADY_SET") {
        Write-Info "isolcpus=3 already configured."
    }
    elseif ($out1 -match "ISOLCPUS_DONE") {
        Write-Success "isolcpus=3 added to boot config."
    }
    else {
        Write-Warn "isolcpus output: $out1"
    }

    # 1b. Pin the service to the core we just reserved.
    # isolcpus only takes core 3 away from the general scheduler — on its own
    # it gives that core to nobody, so the matrix keeps competing on 0-2 while
    # a whole core sits idle. CPUAffinity in the unit is what actually puts the
    # process there. Check with: taskset -cp $(pgrep -f spotify_matrix.py)
    Write-Info "Pinning the service to core 3 (CPUAffinity)..."
    $cmdAff = "grep -q '^CPUAffinity=3' $SERVICE_FILE && echo AFFINITY_SET || " +
    "(sudo sed -i '/^\[Service\]/a CPUAffinity=3' $SERVICE_FILE && " +
    "sudo systemctl daemon-reload && echo AFFINITY_DONE)"
    $outAff = Invoke-SSH -Command $cmdAff
    if ($outAff -match "AFFINITY_SET") {
        Write-Info "CPUAffinity=3 already in the unit file."
    }
    elseif ($outAff -match "AFFINITY_DONE") {
        Write-Success "CPUAffinity=3 added - the reserved core is now actually used."
    }
    else {
        Write-Warn "CPUAffinity output: $outAff"
    }

    # 2. Disable audio in /boot/config.txt
    Write-Info "Disabling onboard audio..."
    $cmd2 = "grep -q '^dtparam=audio=off' /boot/config.txt && echo ALREADY_OFF || (sudo sed -i 's/^dtparam=audio=on/dtparam=audio=off/' /boot/config.txt && grep -q '^dtparam=audio=off' /boot/config.txt && echo AUDIO_OFF || (echo 'dtparam=audio=off' | sudo tee -a /boot/config.txt > /dev/null && echo AUDIO_OFF))"
    $out2 = Invoke-SSH -Command $cmd2
    if ($out2 -match "ALREADY_OFF") {
        Write-Info "Audio already disabled."
    }
    elseif ($out2 -match "AUDIO_OFF") {
        Write-Success "Onboard audio disabled."
    }
    else {
        Write-Warn "Audio output: $out2"
    }

    # 3. Disable Bluetooth
    Write-Info "Disabling Bluetooth service..."
    $cmd3 = "sudo systemctl disable bluetooth.service 2>/dev/null; sudo systemctl stop bluetooth.service 2>/dev/null; echo BT_DONE"
    $out3 = Invoke-SSH -Command $cmd3
    if ($out3 -match "BT_DONE") {
        Write-Success "Bluetooth disabled."
    }

    Write-Host ""
    Write-Success "All optimizations applied!"
    Write-Warn "A REBOOT is required for isolcpus and audio changes to take effect."
    $reboot = Read-Host "  Reboot now? (yes/no)"
    if ($reboot -eq "yes") {
        Invoke-SSH -Command "sudo reboot" | Out-Null
        Write-Success "Rebooting... wait ~30s then reconnect."
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
