# Spotify Matrix Change Log

## 2026-09-05 — Phases 5–9

Nothing here changes how the panel looks until you choose to. The display mode
still defaults to **Original** (idle screen → disc → lyrics) and the idle screen
still defaults to **Clock**.

**Settings now survive a restart.** Brightness, accent colour, mode, lyric
style, fonts and speeds are saved to `.cache/settings.json` — debounced 2s after
the last change, written atomically, and validated on load. A CLI flag you
actually type still wins over a saved value.

**Ambient idle screens.** The clock is now one of seven choices: Clock, Plasma,
Matrix Rain, Starfield, Game of Life, Fireplace, or Cycle. They fill the screen
whenever nothing is playing, and permanently in Idle Screen mode.

**New display modes and detail.** Album Art mode (cover filling the panel, with
an optional slow Ken Burns drift), a progress ring around the vinyl, an "Auto"
accent colour derived from the album art, an up-next peek in the last seconds of
a track, and a brief accent border flash on a new song.

**On-matrix status.** A headless auth or network failure used to be a black
panel; it now says "Spotify auth needed" or "No Wi-Fi" on the LEDs.

**Web panel rebuilt.** A live preview of the actual panel, play/pause/skip, a
progress bar with elapsed and total, the idle-screen picker, and controls for
settings that previously existed with no UI. Requests now time out instead of
hanging, failures show a banner instead of being swallowed, and both pages
dropped their Google Fonts imports so the panel renders with no internet.

**Housekeeping.** `matrix_control.ps1` finally uses `PI_HOST` (it was required,
validated, then ignored) and `PI_PASS` is deleted — it authenticates with keys,
so that was a plaintext password stored for nothing. `git pull` failures no
longer silently restart the old code. `isolcpus=3` now has a matching
`CPUAffinity=3`, so the reserved core is actually used. The systemd unit is
committed as a template, dependencies are bounded, and `tests/test_pure.py`
covers the pure functions with no hardware needed.

**Re-authorization:** playback control and up-next need Spotify scopes the old
token lacks. Run `--auth-only` once (menu option 2). Everything else works
without it.

## 2026-07-18 00:45:43
- **Optimized Web UI Sync:** Added immediate UI state fetching upon slider changes to eliminate UI desync/lag.
- **Removed Manual Poll Rate Slider:** Fully deleted manual poll interval selection since the backend actively optimizes it dynamically based on Spotify playback state.
- **Fixed API Fallbacks:** Changed API endpoints to properly source defaults (like 10.0 RPM and 20.0 Scroll Speed) from the main state structure to prevent duplicate/desynced defaults.
- **Optimized Track Transition Polling:** Altered the accelerated track transition polling logic to activate during the last 10 seconds of a track (down from 15 seconds) to save bandwidth.

