# SpotifyMatrix — Code Audit & Implementation Plan

Audit of `spotify_matrix.py` (3036 lines) + `matrix_control.ps1` (528 lines).
Every item below was verified against the source; line numbers are exact.

> **Line numbers refer to the ORIGINAL file, before Phases 0–4 were applied.**
> They no longer match the current source.

---

## Legend

`✅` done · `🟡` partly done · `⏸️` deliberately deferred · `❌` dropped · `⬜` not started

Status is marked inline next to each item below, and on each phase in the
Master Implementation Plan at the end.

**Found and fixed during implementation, not in the original audit:**

- Lyric style, smart-scroll and both font sizes were read *outside* the state
  lock at the render call sites, inconsistently with every other field.
- `♪` (U+266A) on the "No Lyrics" screen is absent from the default PIL font
  and was rendering as a tofu box on the panel. Now drawn with primitives.
- Rejecting an oversized POST without draining the body resets the connection
  mid-send, so the client never receives the 413. Now drained in chunks.
- The crisp-text threshold needs to be **60**, not the 110 I first guessed.
  Measured against the stock font at size 7: 60 keeps every stroke and removes
  all 74 half-lit pixels; 110 already eats thin stems ("for" → "lor"); 140
  destroys the glyphs.
- Karaoke word placement cannot advance by summing per-word widths — those do
  not add up to the width of the joined row (2px drift per row measured), so
  words visibly overlapped. Positions are now measured from the row prefix.

---

# PART 1 — CONFIRMED BUGS

## 1.1 Critical (crash / appliance dies)

### ✅ B1. Corrupt token file crashes the service into a restart loop
`_load_token()` (L287–292) calls `json.load()` with no error handling, inside
`SpotifyClient.__init__`. A truncated or zero-byte `.cache/spotify_token.json`
raises `JSONDecodeError` before anything else starts → process exits → systemd
restarts → crashes again, forever. The matrix goes black and nothing in the web
panel can tell you why (the panel never starts).

This is not hypothetical, because of B2.

**Fix:** wrap in `try/except (OSError, json.JSONDecodeError)`, log, return `None`
(falls through to normal re-auth path).

### ✅ B2. Token writes are not atomic — power loss corrupts the token
`_save_token()` (L302–303) does `open(path, "w")` which truncates first, then
writes. Pull the Pi's power during that window and you get a zero-byte file →
B1. For a device whose whole design is "just unplug it", this will happen.

**Fix:** write to `path.with_suffix(".tmp")`, `f.flush()`, `os.fsync(f.fileno())`,
then `os.replace(tmp, path)`. `os.replace` is atomic on POSIX.

### ✅ B3. Infinite recursion on a permanently-rejected token
`get_currently_playing()` (L262–265): on HTTP 401 it refreshes and calls
*itself* recursively. If the refresh succeeds but the new token is still
rejected (revoked app authorization, scope change, account issue), this recurses
until `RecursionError`. The `except Exception` in `poll_spotify` catches it, so
you get a backoff loop spamming RecursionError tracebacks instead of a clear
"re-authorize needed" message.

**Fix:** add a `_retry: bool = False` parameter; refresh and retry exactly once,
then raise a clear `RuntimeError("Spotify rejected the refreshed token — re-run
with --auth-only")`.

### ✅ B4. `SIGTERM` is not handled → matrix stays lit after `systemctl stop`
There is no `signal` import anywhere in the file. The cleanup that calls
`display.clear()` lives in a `finally` block (L2949–2954) that only runs on
`KeyboardInterrupt`. systemd sends `SIGTERM`, whose default action terminates
the process immediately — the `finally` never executes.

Observable symptom: stop or restart the service and the panel keeps displaying
the last rendered frame indefinitely.

**Fix:**
```python
import signal
def _on_term(signum, frame):
    raise KeyboardInterrupt
signal.signal(signal.SIGTERM, _on_term)
```
Add `KillSignal=SIGTERM` + `TimeoutStopSec=5` to the unit file.

---

## 1.2 High (visibly wrong behaviour)

### ✅ B5. Instrumental badge in the web UI never appears
`updateUI()` reads `s.is_instrumental` (L1845) but `_send_state()` (L2379–2400)
does not include that key. It is only present in `/api/lyrics` (L2182). So
`s.is_instrumental` is always `undefined` and the badge is permanently hidden —
despite all the backend detection work being done correctly.

**Fix:** add `"is_instrumental": outer_state.is_instrumental` to `_send_state`.

### ✅ B6. Lyrics scroll animation snaps backwards on fast lines
In `render_lyrics` scroll mode (L1231–1249), `_lyrics_scroll_state["scroll_y"]`
is *only* assigned when a transition finishes (L1246/L1249). When the index
changes, the code reads `old_y = scroll_y` — the last **completed** position,
not where the text visually is right now.

For lines closer together than `LYRICS_SCROLL_DURATION` (0.4 s) — i.e. exactly
the rap/fast-verse case — every new line snaps back to the previous anchor and
re-animates. Visible stutter.

**Fix:** on index change, snapshot the current interpolated position first:
```python
if idx != _lyrics_scroll_state["last_idx"]:
    _lyrics_scroll_state["scroll_y"] = current_y_from_previous_frame
    _lyrics_scroll_state["last_idx"] = idx
    ...
```

### ✅ B7. Track change causes a whiplash scroll through the whole song
`_lyrics_scroll_state` is module-global and never reset on track change. Song A
ends at line 40 (`target_y = 560`); song B starts at line 0 (`target_y = 0`).
The animation smoothly scrolls through 560 px of nothing over 0.4 s.

**Fix:** reset the dict when `art_key` changes. Cleanest: move this state onto
a small `LyricsRenderer` class keyed by track, instead of a module global.

### ✅ B8. `is_instrumental` is not cleared on track change
`poll_spotify` (L2492–2494) resets `state.lyrics` and `state.lyrics_track_key`
on a new track but not `state.is_instrumental`. Play an instrumental, then a
vocal track: the matrix shows the pulsing-bars visualizer and "Instrumental" for
the ~1 s until LRCLIB responds.

**Fix:** add `state.is_instrumental = False` in the same block.

### ✅ B9. Accent colour ignored by the CD-mode idle clock
Four `render_clock` call sites, two of them wrong:

| Line | Call | Accent |
|------|------|--------|
| 2753 | `render_clock(size, is_connected, accent_color)` | ✅ |
| 2803 | `render_clock(size, is_connected, accent_color)` | ✅ |
| 2909 | `render_clock(size, is_connected)` | ❌ always green |
| 2926 | `render_clock(size)` | ❌ green + always "connected" |

Set a custom accent, sit in CD mode until it idles → the clock is Spotify green.
L2926 additionally loses the disconnected (red pulse) indicator during the
transition.

**Fix:** pass `is_connected, accent_color` at both sites.

### ✅ B10. Advanced Settings panel gets stuck hidden
`updateUI()` (L1799–1810): entering `custom` mode hides `mainSettings`,
`advBtn`, `advCard`, `colorCard`. The `else` branch restores `mainSettings`,
`advBtn`, `colorCard` — but **not `advCard`** (`advSettings`).

Repro: open Advanced → switch to Custom Slate → switch back. The panel is hidden
but the button still reads "Hide Advanced Settings", so one click does nothing
(it flips to `display:none`, which it already is) and you must click twice.

**Fix:** track the intended state in a JS variable rather than reading
`style.display`, and restore `advCard` in the `else` branch.

### ✅ B11. Web live-lyrics drawer never refreshes on track change
`fetchLyricsData()` is called only from `toggleLiveLyrics()` (L1950). The 500 ms
`lyricsInterval` only runs `updateLiveLyricsScroll()`, which re-uses the cached
`lyricsData`. Leave the drawer open across a track change and you are reading
the previous song's lyrics against the new song's timestamps.

**Fix:** track `art_key` in `currentState`; re-fetch when it changes.

### ✅ B12. Web live lyrics drift by up to one poll interval
`updateLiveLyricsScroll()` (L1982):
```js
const currentMs = currentState.progress_ms + (Date.now() - window.lastStateFetchTime);
```
`progress_ms` is not fresh at the moment of the HTTP fetch — it is whatever the
**Spotify poll thread** last stored, which is 0–5 s old while playing and up to
30 s old when idle. The client compensates only for the HTTP round-trip, so the
phone display lags the matrix by up to 5 s.

The server already has what's needed (`state.fetch_time`, a monotonic stamp) but
never exposes it.

**Fix:** add `"progress_age_ms": int((time.monotonic() - fetch_time) * 1000)` to
`_send_state`, and in JS use
`progress_ms + progress_age_ms + (Date.now() - lastStateFetchTime)`.
Also apply `lyrics_lead_ms` client-side so phone and matrix agree.

---

## 1.3 Medium (robustness / DoS / hygiene)

### ✅ B13. Web server is single-threaded
L2407 uses `HTTPServer`, not `ThreadingHTTPServer`. All requests serialize on
one thread. Consequences:
- Two phones open → their 2 s `/api/state` + 2 s `/api/logs` polls queue behind
  each other.
- A `/api/custom-media` GIF upload (base64 decode + N× LANCZOS resize) blocks
  the entire panel for its duration.
- One stalled client can wedge the panel until TCP times out.

**Fix:** `ThreadingHTTPServer` with `daemon_threads = True`. One-line change,
large improvement. Add `Content-Length` to `_send_json`/`_send_html` too — they
currently omit it, forcing connection-close on every request.

### ✅ B14. Unbounded request body → trivial OOM on a Pi
`_read_body()` (L2353–2361) reads `Content-Length` bytes with no cap. Anything
on your LAN can POST 2 GB of JSON and the Pi OOM-kills the service.

`/api/custom-media` (L2321–2347) compounds it: no cap on decoded image size and
no cap on `img.n_frames`. A 500-frame GIF becomes 500 × 64×64×3 = 6 MB of
retained frames *plus* 500 LANCZOS resizes of the source, all while holding the
single server thread (B13).

**Fix:** reject `Content-Length > 8 MB` with 413; cap `n_frames` at ~120 and log
the truncation; cap decoded pixel count (`Image.MAX_IMAGE_PIXELS`).

### ✅ B15. No input validation on numeric endpoints
`int(body.get("value", ...))` at L2217, L2258, L2265, L2294–2296, L2311 and
`float(...)` at L2229, L2236 all raise on non-numeric input, killing the handler
mid-response. Combined with B13 the client sees a reset connection.

**Fix:** one helper — `_num(body, "value", default, lo, hi, cast)` — that
catches `(TypeError, ValueError)` and returns 400.

### ⏸️ B16. World-writable token cache

> **Deferred to Phase 5 on purpose.** The atomic-write half of this (B2) is
> done, but the `chmod` is deliberately held back: `--auth-only` runs as your
> login user while the systemd service runs as root, and both read and rewrite
> this file. Tightening to `0600` without *also* making the service non-root
> would break re-authorization. The code carries a comment saying exactly this,
> so it cannot be tightened by accident.

`_save_token` (L305–309) chmods the token file to `0o666` and its directory to
`0o777`. That file holds a **Spotify refresh token** — a long-lived credential.
Any local user or process can read or replace it.

This is presumably to let both `sudo` runs (via `matrix_control.ps1` manual
mode) and plain-user runs share the cache. Better: pick one identity. Run the
service as user `adi` with `AmbientCapabilities=CAP_SYS_NICE` (and the GPIO
group) instead of root, and chmod `0o600`.

### ⬜ B17. Web panel is unauthenticated on `0.0.0.0`
Bound to all interfaces (L2407) with no auth. Anyone on the LAN can change modes,
brightness, cast images, and read logs. For a home LAN this is a defensible
choice — but it should be a *choice*: add `--web-bind` (default `0.0.0.0`) and
an optional `--web-token` checked against a header or `?k=` query param.

### ✅ B18. LRCLIB 404 is logged as a failure
`fetch_lyrics` (L980–982) catches everything with a bare `except Exception`.
LRCLIB returns **404** for "no match", which `urllib` raises as `HTTPError`, so
a completely normal "this track isn't in the database" shows up in your logs as
`LRCLIB: Failed to fetch lyrics: HTTP Error 404: Not Found`. It also returns
`is_instrumental=False`, which is correct but accidental.

**Fix:** catch `HTTPError` separately; treat 404 as a clean "no lyrics" result.

### ✅ B19. Enhanced-LRC word timestamps render as literal text
`_LRC_LINE_RE` (L928) captures everything after `[mm:ss.xx]` as the line text.
If LRCLIB returns *enhanced* LRC (inline `<mm:ss.xx>` per-word tags), those tags
are drawn on the matrix as visible garbage.

**Fix:** strip `<\d+:\d+(?:\.\d+)?>` from the text — or better, **parse them**,
which gives you true per-word karaoke timing for free (see F1).

### ✅ B20. Pop mode line positions are hardcoded for one font size
L1304: `y_positions = [10, 28, 46]`. These are fixed regardless of `font_size`,
but the Pop Font Size slider goes to 14 (L1614). At 14 the three lines are 18 px
apart with ~16 px glyphs — they collide.

**Fix:** compute positions from `font_size`: `spacing = font_size + 5`, centred.

---

## 1.4 Low (dead code / doc drift)

- 🟡 **B21.** `LYRICS_FONT_SIZE`, `LYRICS_LINE_HEIGHT`, `LYRICS_CENTER_Y`,
  `LYRICS_H_SCROLL_SPEED` (L1031–1035) are each referenced exactly once — their
  own definition. All dead; scroll mode computes its own values and
  `_legacy_h_scroll_x` hardcodes `15.0`.
  **Done:** all four constants removed during the Phase 4 lyrics rewrite.
  **Remaining:** the other dead code in this section (B22, B23) is untouched.
- ⬜ **B22.** `_legacy_h_scroll_x(now_mono=...)` — parameter never used.
- ⬜ **B23.** `slide-left` is handled in `blend_frames` (L877) but is not in the
  `--transition` choices (L3026), so it is unreachable.
- ⬜ **B24.** `/api/smart-scroll` (L2251) exists and works, but there is **no UI
  control for it anywhere** in the HTML. The feature is invisible.
- ⬜ **B25.** GET `/mode` (L2190) accepts `default|cd|lyrics|clock`; POST
  `/api/mode` (L2208) also accepts `custom`. Inconsistent.
- ⬜ **B26.** `effective_mode` is computed and sent in `/api/state` but the web UI
  never displays it — in Auto mode you can't tell what's actually on screen.
- ⬜ **B27.** `has_lyrics` is sent in `/api/state` and never read by the JS.
- ✅ **B28.** `renderLyricsHTML()` (L1974) injects `line[1]` via `innerHTML`
  without escaping. The logs page has an `escapeHtml` helper (L2128); the
  control panel does not. Low risk (LRCLIB content), trivial to fix.
  *Done early — the same function was already being edited for B11/B12.*
- ⬜ **B29.** README says "**8** curated colors" — `COLOR_THEMES` has **7**
  (the 8th swatch is the custom picker).
- ⬜ **B30.** README claims "send custom **scrolling text** directly to the LED
  matrix". No such endpoint exists. The Custom Slate bakes static text into a
  64×64 canvas image; there is no text-message or scrolling-message API.
- ⬜ **B31.** `matrix_control.ps1` L17–23 requires `PI_HOST` and `PI_PASS` in
  `.env` and validates both — then **never uses either**. Every SSH call
  hardcodes `adi@matrixspot.local` (L70, L85, L254). `PI_PASS` in particular
  makes you store a plaintext password for nothing, since the script relies on
  key-based auth.
  **Fix:** use `$PI_HOST` in all three places; delete `PI_PASS` entirely.
- ✅ **B32.** `active_seconds` (L2437) is assigned inside the `try` but read from
  the `except` handlers (L2543, L2555). Safe today only because it's the first
  statement in the block. Promote it to a module constant.

---

# PART 2 — PERFORMANCE

The Pi is the bottleneck and the render loop is doing far more work than it
needs to. I could not benchmark locally (no Pillow on this machine), so these
are structural arguments, not measured numbers — but the fixes are
straightforward wins regardless of the exact figures.

### ✅ P1. The album art is re-downscaled from 640×640 on **every single frame**
`render_record()` (L616):
```python
art_square = ImageOps.fit(art, (disc_size, disc_size), method=Image.Resampling.LANCZOS)
rotated = art_square.rotate(angle, resample=Image.Resampling.BICUBIC)
```
`art` is the full-resolution Spotify image, and `playback_art_from_response`
(L571) deliberately picks the **largest** available (`max(..., key=width)` →
640×640). So at 20 FPS the Pi performs a 640×640 → 64×64 LANCZOS resample
20 times a second, discarding an identical result each time. The resize depends
only on the artwork; only the rotation depends on the frame.

**Fix (three levels, do at least the first two):**
1. Request a smaller image. Spotify returns 640/300/64 variants — pick the
   smallest that is ≥ the disc size instead of the largest. Cuts download time,
   decode time, and RAM immediately.
2. Pre-fit once at download time. Store `state.image_fitted` alongside
   `state.image`; `render_record` then only rotates.
3. Pre-render the rotation. At track change, build ~60 pre-rotated 64×64 frames
   into a list and index by angle. Per-frame cost drops to a list lookup and a
   paste. ~60 × 12 KB ≈ 700 KB of RAM, trivially affordable.

### ✅ P2. The text edge-fade is a Python per-pixel loop
`draw_scrolling_text` (L783–792) calls `getpixel` + `putpixel` twice per pixel
across `fade_width(6) × banner_height` — roughly 130 pixel round-trips per
frame, each crossing the Python↔C boundary. PIL per-pixel access is the slowest
path in the library.

**Fix:** build the fade gradient **once** as an `"L"` mask (cached by banner
geometry) and apply it with `Image.composite(image, black, mask)` — a single
C-level operation. Pure win, identical output.

### ✅ P3. The clock re-renders 12 tick marks and 3 text layouts at 20 FPS
`render_clock` (L652–725) recomputes `textbbox` three times, draws 12 tick
lines, and redraws the full face every frame — for content that changes once per
**minute**. Only the seconds dot and the pulse animate.

**Fix:** cache the static face keyed by `(minute, accent, size)`; `.copy()` it
and draw only the seconds dot and pulse on top.

### ✅ P4. `create_full_frame` draws the clock overlay 10× per frame
L848–855: an 8-direction shadow loop plus 2 foreground draws, times 2 text
strings = 20 `draw.text` calls per frame, for a string that changes once a
minute. Same fix: cache the rendered overlay keyed by `HH:MM`.

### ✅ P5. Full 20 FPS is spent on static screens
Clock mode, idle, and single-frame Custom Slate all re-render and re-upload at
`args.fps`. Nothing about a paused clock needs 20 FPS.

**Fix:** per-mode frame budget — CD/lyrics at `args.fps`, clock at 10, static
slate at 2. Meaningful idle CPU and power reduction for a device that is idle
most of the day.

### ⬜ P6. `isolcpus=3` alone probably isn't doing what you think
`matrix_control.ps1` `Optimize-AntiFlicker` (L312–318) adds `isolcpus=3` to
`/boot/cmdline.txt`. That *reserves* core 3 from the general scheduler, but
nothing in the script or the service file pins the matrix process to it. Unless
the hzeller library's own affinity logic picks it up, you have removed a core
from the scheduler and given it to nobody.

**Fix:** verify with `taskset -cp <pid>` and, if needed, add
`CPUAffinity=3` to the systemd unit or launch under `taskset -c 3`.

---

# PART 3 — LYRICS: FIXING READABILITY (your main complaint)

Your diagnosis is right and the fix is bigger than a slider.

**Why horizontal scrolling is the wrong primitive here.** A rap line is ~40–60
characters. At the current font that's 3–5 screen-widths. Sliding shows you a
64 px window that *follows* the vocal, so by construction the words you can see
are the ones already sung — the next word is off-screen until it slides in. No
value of `lyrics_lead_ms` fixes that, because lead shifts *time*, and the
problem is *space*. You are trying to read a 60-character line through a
12-character slot.

**The fix: stop scrolling sideways. Wrap vertically.**

### ✅ F1. New third mode: **Karaoke** (alongside Scroll / Pop)

Three changes that compose:

**(a) Word-wrap the active line into a static block.**
Break the line at word boundaries into rows that each fit in 64 px, and render
them stacked and centred. No horizontal motion at all. A 48-character rap line
at a 5×7 pixel font (12 chars/row) becomes 4 rows × 9 px = 36 px — comfortably
inside 64 px. **You see the entire line at once, before and while it's sung.**
That is what actual karaoke displays do, and it solves read-ahead completely.

**(b) Progressive per-word highlight.**
The whole line is visible in a dim colour; words turn accent-coloured as they're
sung. Now motion carries *position* information instead of *content* — you
always know where you are without losing sight of what's next.

LRCLIB gives line-level timestamps, so interpolate within the line, weighting
each word by `len(word) + 1`:
```python
def word_progress(words, elapsed_ms, line_dur_ms):
    weights = [len(w) + 1 for w in words]
    total = sum(weights)
    acc, out = 0, []
    for w, wt in zip(words, weights):
        start = acc / total * line_dur_ms
        acc += wt
        out.append((w, start, acc / total * line_dur_ms))
    return out
```
And if the response *is* enhanced LRC with `<mm:ss.xx>` tags (see B19), use the
real per-word times instead of interpolating — you get exact karaoke for free
on tracks that have it.

**(c) Next-line preview.**
Render the upcoming line dimly in whatever vertical space is left. Because
nothing scrolls horizontally any more, the preview is fully readable rather
than truncated at `x=2` the way non-active lines are today (L1295).

Suggested 64 px budget: 4 px top pad · up to 4 rows active line · 2 px gap · up
to 2 rows next line · 1 px progress bar.

### ✅ F2. Ship a real pixel font — this *is* a code problem

You said 8 is too small and 9 is slightly too big and assumed it's a hardware
limit. It isn't. `get_font()` (L523–531) uses `ImageFont.load_default(size=N)`,
which is **Aileron — a proportional, anti-aliased TTF**. On a 64×64 LED panel
anti-aliasing is actively harmful: each glyph edge lands on half-lit LEDs, so
strokes smear across physical pixels. That's most of why 8 reads as mush and 9
reads as bulky — you're not choosing between two sizes, you're choosing between
two blur radii.

A **bitmap/pixel font** is designed so every stroke is exactly on the pixel
grid. At the same apparent size it is dramatically sharper, which in practice
means you can go *smaller* and still read it.

Options, best first:
1. **Bundle a pixel TTF** — Pixel Operator 8, Silkscreen, or m5x7. Free, drop-in
   via `ImageFont.truetype(path, 8)`, renders essentially AA-free at its design
   size and integer multiples.
2. **Use the fonts already on your Pi.** `rpi-rgb-led-matrix/fonts/` ships
   `4x6.bdf`, `5x7.bdf`, `6x10.bdf`, `tom-thumb.bdf`. Convert once to PIL format
   (`pilfont`) and load with `ImageFont.load()`.
3. **No new files:** kill the AA on the existing font. Draw text into an `"L"`
   mask, threshold it, and paste solid colour through it:
   ```python
   mask = Image.new("L", frame.size, 0)
   ImageDraw.Draw(mask).text(pos, text, fill=255, font=font)
   frame.paste(color, (0, 0), mask.point(lambda p: 255 if p > 110 else 0))
   ```
   Costs nothing and sharpens every mode immediately. Worth doing first as a
   quick experiment before committing to a font file.

This also gives you the in-between sizes you're missing: 4×6 sits between the
current 8 and 9 in apparent size while being *more* legible, because it's
on-grid.

### ✅ F3. Auto-fit the font instead of a fixed slider
With wrapping in place, pick the largest available font for which the wrapped
line fits the row budget. Short pop hook → big and bold; dense rap bar → drop a
step. The slider becomes "**maximum** font size" — a preference, not a
compromise you re-tune per genre.

### ✅ F4. Self-correcting timing (retire the manual lead slider)
`lyrics_lead_ms` is a manual constant correcting a *systematic* error: Spotify's
reported `progress_ms` lags, and you extrapolate from a poll up to 5 s old.
On every poll you can measure the error — compare predicted progress against
what Spotify actually reports — and maintain a smoothed offset:
```python
error = actual_progress_ms - predicted_progress_ms
self.offset_ms = 0.8 * self.offset_ms + 0.2 * error
```
Drift stops accumulating between polls, and the lead slider becomes a genuine
taste control (how far ahead you like to read) rather than a drift patch.

### ✅ F5. Smaller lyric wins
- Cache lyrics to `.cache/lyrics/<sha1>.json`. Replays become instant and you
  stop hammering LRCLIB.
- **LRCLIB `/api/search` fallback.** You only call `/api/get` (L42), which
  requires an exact artist + track + album + duration match. Titles like
  `Song (feat. X) - Remastered 2011` miss constantly. On 404: strip
  `- Remastered`, `(feat. …)`, `- Radio Edit`, etc. and retry, then fall back to
  `/api/search`. This is likely the single biggest increase in how often lyrics
  show up at all.
- Show a "searching lyrics…" state instead of silently sitting on the CD view.

---

# PART 4 — WEB PANEL

### ⬜ W1. Live matrix preview (highest value, low effort)
Keep the last rendered frame in shared state; add `GET /api/frame.png` that
encodes that 64×64 image and returns it. Display it in the panel at 4× with
`image-rendering: pixelated`. You immediately see exactly what the panel shows
without looking at it, which also makes every other setting easier to tune.
A 64×64 PNG is ~1–3 KB; 2 FPS over LAN is nothing.

### ⬜ W2. Show what's actually rendering
`effective_mode` is already in `/api/state` and unused (B26). In Auto mode,
label the active button — "Auto (Smart) · now: lyrics".

### ⬜ W3. Playback controls
Add `user-modify-playback-state` to `SCOPE` (L40) and wire play/pause/next/prev
to `PUT /me/player/play|pause` and `POST /me/player/next|previous`. Natural fit
for a device already sitting on your desk. Requires one re-auth (the
`matrix_control.ps1` Reauth flow already handles this).

### ⬜ W4. Progress bar and elapsed/total time
You already have `progress_ms` and `duration_ms` in `/api/state`; the panel
doesn't render them. Cheap, makes the Now Playing card feel finished.

### ⬜ W5. Missing controls for existing features
- **Smart scroll toggle** — endpoint exists, no UI (B24).
- **Auto-cycle duration** — `DEFAULT_CD_DURATION` (L2695) is hardcoded to 10 s.
- **Transition style / duration** — CLI-only today.

### ⬜ W6. `Off` / sleep button
A blank-the-panel switch that stops rendering and sleeps the loop, so idle CPU
goes to near zero. Pairs with F6 below.

### ⬜ W7. Robustness
- Poll `/api/state` with `AbortController` + a timeout so a hung request doesn't
  stall the UI.
- Show a "disconnected" banner when fetches fail (currently every `catch` is
  silently empty — L1763, L1857, L1868, L1879, L1887, L1941, L1963).
- Drop the two Google Fonts `@import`s (L1372, L2036) or self-host them: the
  panel is a LAN appliance UI and shouldn't need internet to render properly.
- Escape lyric text before `innerHTML` (B28).

---

# PART 5 — FEATURES WORTH ADDING

Ordered by value-to-effort.

### ⬜ F6. Persist settings across restarts ⭐ *biggest quality-of-life win*
Nothing is saved. Brightness, accent colour, lyrics style, font sizes, lead,
spin speed, mode — every one resets to CLI defaults on reboot or
`systemctl restart`. For something advertised as "plug & play appliance", that's
the most jarring gap in the project.

Save the runtime-adjustable fields of `SharedPlaybackState` to
`.cache/settings.json` (debounced ~2 s after a change, atomic write like B2) and
load at startup with CLI flags still overriding.

### ⬜ F7. Auto accent colour from album art
Extract the dominant vibrant colour from the artwork and use it as the accent —
an `"auto"` entry alongside the 7 themes. Resize the art to 16×16, convert to
HSV, pick the highest `saturation × value` cluster, clamp minimum brightness so
it stays visible on LEDs. ~20 lines, runs once per track, and it makes the whole
display feel reactive to the music.

### ⬜ F8. Night mode / scheduled brightness
Auto-dim (or blank) between configurable hours. For a bedroom device this is the
difference between usable and unplugged. Add `--night-start`, `--night-end`,
`--night-brightness`, exposed in the panel.

### ⬜ F9. Full-bleed album art mode
A fifth mode: the artwork filling all 64×64 with a 1 px progress bar. No disc
crop, no rotation — the simplest and often best-looking mode, and the cheapest
to render.

### ⬜ F10. Progress ring around the vinyl
In CD view, draw a thin arc around the disc showing track position. Uses data
you already have, adds real information density to the flagship view.

### ⬜ F11. Boot / error status screen
If Spotify auth fails today, the process exits and the matrix goes black — on a
headless appliance you have no idea why. Render "No Wi-Fi", "Spotify auth
needed", "Connecting…" directly on the panel. Turns a black screen into a
diagnosis.

### ⬜ F12. Actually implement the scrolling text message
README promises it (B30). A `POST /api/message {text, duration}` that scrolls
arbitrary text across the matrix is ~30 lines given `draw_scrolling_text`
already exists, and closes a documentation gap.

---

# PART 6 — STRUCTURE & TOOLING

### ⬜ S1. Split the file
3036 lines in one module, including ~660 lines of HTML/CSS/JS inside Python
string literals (L1365–2141). Editing the panel means editing a string — no
syntax highlighting, no linting, and one stray `"""` breaks the whole program.

```
spotify_matrix/
  __main__.py      # CLI + run loop
  spotify.py       # SpotifyClient, LocalCallbackServer
  lyrics.py        # LRCLIB fetch/parse/cache
  render/
    disc.py  clock.py  lyrics.py  text.py  slate.py
  display.py       # MatrixDisplay / MockDisplay
  web/
    server.py
    static/{panel.html, logs.html, panel.css, panel.js}
  state.py         # SharedPlaybackState + persistence
```
Serve `static/` from disk (with a `--dev` no-cache flag) and you can edit the
panel live without restarting the matrix.

### ⬜ S2. Add tests for the pure functions
Several functions are pure and trivially testable, and two of them have bugs
above that a test would have caught (`parse_lrc` vs enhanced LRC → B19):
`parse_lrc`, `get_current_lyric_index`, `_smart_h_scroll_x`,
`_get_line_duration_ms`, `playback_art_from_response`, `blend_frames`.
A single `tests/test_pure.py` with pytest is maybe 100 lines and needs no
hardware.

### ⬜ S3. Commit the systemd unit file
The unit lives only on the Pi and is edited in place by `sed` (L169). It should
be in the repo as `spotifymatrix.service.template` — right now the service
config is undocumented, unversioned, and unrecoverable if the SD card dies.
Include `Restart=always`, `RestartSec=5`, `KillSignal=SIGTERM`,
`TimeoutStopSec=5`.

### ⬜ S4. Pin dependencies
`requirements.txt` has `Pillow>=10.0`, `python-dotenv>=1.0`. Unbounded upper
ranges on an appliance you update with `git pull` means a Pillow major bump can
break the display remotely. Pin exact versions; note that `rgbmatrix` is built
from source and is not pip-installable.

### ⬜ S5. Fix the PowerShell script
- Use `$PI_HOST` instead of hardcoding `adi@matrixspot.local` (B31).
- Delete `PI_PASS` — it is required, validated, and never used.
- Extract the duplicated brightness-prompt block (L390–399, L426–435).
- The `Update-Code` flow stops the service, pulls, restarts — but never checks
  whether the pull succeeded. A failed pull silently restarts the old code.

---

# PART 7 — VISUAL QUALITY & ADDITIONAL FEATURES

## 7.1 Image pipeline — why the panel looks muddier than the source

These are the highest-impact *visual* changes in the whole document, and none of
them are currently done anywhere in the code.

### ✅ V1. Gamma correction ⭐ *biggest single look improvement*
LED PWM output is **linear**; human brightness perception is roughly a **2.2
power curve**. Nothing in the pipeline accounts for this — `display.show()`
([spotify_matrix.py:481](spotify_matrix.py:481)) hands PIL's raw sRGB bytes
straight to the panel. The result: midtones read too bright, shadows crush to
solid black, and dark album art loses all detail.

A 256-entry lookup table applied once per frame fixes it, and `Image.point()`
does it at C speed:
```python
_GAMMA_LUT = [min(255, int(((i / 255) ** 1.8) * 255 + 0.5)) for i in range(256)] * 3
frame.point(_GAMMA_LUT)
```
Tune the exponent by eye (1.6–2.2). Expose as `--gamma`. Note the hzeller
library applies its own CIE1931 curve in recent versions, so measure before and
after rather than stacking corrections blindly.

### ✅ V2. Contrast and saturation boost for album art
You run `--pwm-bits 9` (matrix_control.ps1:113) — 512 levels per channel instead
of 2048. Combined with a 64×64 crop, subtle or desaturated artwork turns to
mush. A mild `ImageEnhance.Color(1.3)` + `ImageEnhance.Contrast(1.15)` applied
**once at download time** (free — it's not per-frame) makes art dramatically more
readable at this size. Expose both as CLI flags.

### ❌ V3. Dithering to hide banding — DROPPED, the premise was wrong

> **Dropped as unnecessary, not deferred.** The original reasoning here — that
> 9 PWM bits causes banding worth dithering against — does not hold.
> `--pwm-bits 9` gives 512 levels per channel, which is *more* than an 8-bit
> source image's 256. The panel can already represent every value in the
> artwork, so there is nothing to dither against. Any banding you see comes
> from the source image and the downscale, not from panel quantization.
> Implementing this would have added cost for no visible change.

*Original suggestion, kept for the record:* ordered (Bayer 4×4) dithering
during the downscale to break bands into noise the eye integrates.

### ✅ V4. Snap scrolling text to integer pixels ⭐ *kills the shimmer*
`draw_scrolling_text` ([spotify_matrix.py:774](spotify_matrix.py:774)) computes
`offset_x = -(scroll_x % unit_w)` as a **float** and passes it straight to
`draw.text()`. PIL rounds internally, so with a variable frame delta the text
steps unevenly — 1px, 1px, 2px, 1px — which on an LED panel reads as a constant
shimmer/judder.

**Fix:** `cur_x = int(offset_x)` before drawing, and accumulate `scroll_x` as a
float but round at draw time. Same family of problem as the font anti-aliasing
(F2): on a 64×64 panel, anything off the pixel grid looks broken.

### ✅ V5. Ease brightness changes
`set_brightness` ([spotify_matrix.py:488](spotify_matrix.py:488)) is applied
instantly whenever the slider moves ([spotify_matrix.py:2724](spotify_matrix.py:2724)).
Ramping over ~0.4 s feels far better, and it's what makes night mode (F8) fade
in unnoticed instead of snapping.

---

## 7.2 Idle & ambient — the panel is idle most of the day

Right now "not playing" means a clock, forever. This is the biggest missed
opportunity in the project: it's an always-on light source in your room.

### ⬜ V6. Ambient screensaver modes
A rotating set of generative visuals for idle, cheap enough for a Pi because
they're all direct pixel writes at 64×64:

- **Matrix rain** — falling glyph columns (thematically obligatory)
- **Plasma / Perlin field** — smooth colour flow, the classic LED demo
- **Starfield** — depth-scrolling points
- **Conway's Game of Life** — seeded in the accent colour, auto-reseeds on
  stagnation
- **Fireplace** — the classic bottom-up heat diffusion; genuinely warm-looking
  on a real panel
- **Analog clock face** — a second clock variant with sweeping hands

Add `--idle-mode {clock,rain,plasma,stars,life,fire,cycle}` plus a web selector,
where `cycle` rotates every few minutes. Each is 20–40 lines. This is high
visual payoff for low effort and low risk — nothing else depends on it.

### ⬜ V7. Weather on the clock face
Open-Meteo needs **no API key**:
`https://api.open-meteo.com/v1/forecast?latitude=..&longitude=..&current=temperature_2m,weather_code`

Temperature plus a small pixel-art condition icon in the clock's dead space,
refreshed every 15 minutes and cached. For an always-on desk display this is
probably the most *used* non-Spotify feature you could add.

### ⬜ V8. Ken Burns pan on full-bleed art
Pair with F9: slow drift and zoom over the artwork instead of a static image.
Stops a paused screen from looking frozen. Reuses the pre-rotated-frame cache
idea from P1.

---

## 7.3 Reactive & informational

### ❌ V9. Beat-synced pulse — RULED OUT
Spotify's `/v1/audio-analysis/{id}` (beat timestamps) and
`/v1/audio-features/{id}` (`tempo`, `energy`) would have allowed pulsing the
disc exactly on the beat with no microphone.

**Not available to this project.** Spotify deprecated both endpoints for newly
created apps in November 2024; only apps with prior access retained them. This
app was registered after that cutoff, so both return `403`. Dropped from the
plan — do not spend time on it.

Pseudo-tempo derived from average lyric-line interval was considered as a
fallback and rejected as too crude to be worth the complexity.

### ✅ V10. Authentic vinyl RPM
`--rpm` defaults to 10 ([spotify_matrix.py:3003](spotify_matrix.py:3003)), which
is arbitrary. `33.333` is a real LP and costs nothing to adopt as the default.

### ⬜ V11. Album palette strip
Extract the 5 dominant colours from the artwork (same pass as F7's accent
extraction) and draw them as a 2px strip on the lyrics and clock screens. Ties
every mode visually to the track currently playing, for essentially zero cost
once F7 exists.

### ⬜ V12. Liked-track indicator
`GET /v1/me/tracks/contains?ids=` with scope `user-library-read` — one call per
track. Draw a small heart in the corner if it's in your library. Tiny feature,
disproportionately satisfying.

### ⬜ V13. Up-next / queue peek
`GET /v1/me/player/queue` (scope `user-read-playback-state`) → show the next
track's title during the last 10 seconds of the current one. Natural companion
to the existing end-of-track poll acceleration
([spotify_matrix.py:2457](spotify_matrix.py:2457)).

### ⬜ V14. On-matrix toast overlay
When a setting changes from the web panel, flash a small overlay on the matrix
("Brightness 80", "Karaoke mode"). Confirms you're controlling the device you
think you are, and makes the panel feel connected rather than fire-and-forget.

### ⬜ V15. Track-change accent flash
A brief 1px accent-coloured border pulse when a new song starts — a peripheral
cue that something changed, without needing to read anything.

---

## ⬜ 7.4 New transitions
`blend_frames` ([spotify_matrix.py:867](spotify_matrix.py:867)) currently offers
slide and fade. All of these are cheap PIL mask operations:

- **Pixel dissolve** — random pixels swap over; looks superb on LEDs
- **Vinyl drop** — the new record falls onto the platter and settles
- **Radial wipe** — clock-hand sweep reveal
- **Zoom** — old art shrinks away as the new one grows in

---

## ⬜ 7.5 Optional hardware (small, cheap, genuinely useful)

- **Ambient light sensor** (BH1750 or a photoresistor + ADC, ~$3, I²C): drive
  brightness automatically. Strictly better than the scheduled night mode in
  F8 — the panel dims when *your room* dims, not when the clock says so.
- **Rotary encoder + push button** on spare GPIO: cycle modes and set brightness
  without reaching for a phone. Check pin availability against the Adafruit
  HAT's usage first.
- **PIR motion sensor**: blank the panel when the room is empty. Cuts power and
  LED wear on a device that's on 24/7.

---

# MASTER IMPLEMENTATION PLAN

Everything above, consolidated and ordered. Each phase is independently
shippable and testable with `--mock-output` before it ever touches the Pi.

Estimates assume familiarity with the codebase.

| Phase | Theme | Effort | Why here |
|-------|-------|--------|----------|
| 0 | Crash-safety | ~1 h | Stops the two crash-loop paths |
| 1 | Visible bug fixes | ~1 h | Cheap, high-noticeability |
| 2 | Visual pipeline | ~1.5 h | Biggest look improvement per line changed |
| 3 | Performance | ~2 h | Do before new render modes inherit the slow path |
| 4 | Lyrics overhaul ⭐ | ~4 h | Your main complaint |
| 5 | Persistence & appliance | ~2 h | Makes it a real appliance |
| 6 | Web panel | ~3 h | Control + live preview |
| 7 | Display modes & idle | ~4 h | Highest visual payoff |
| 8 | Reactive features | ~3 h | Beat sync, queue, liked, toasts |
| 9 | Cleanup & structure | ~3 h | Pay down before the file gets bigger |

---

## ✅ Phase 0 — Crash-safety (~1 h)
**Goal: the appliance never dies silently.**

1. **B1** guard `_load_token` against corrupt JSON → fall through to re-auth
2. **B2** atomic token write (`tmp` + `fsync` + `os.replace`)
3. **B4** SIGTERM handler so `display.clear()` actually runs
4. **B3** bounded 401 retry with an actionable error message
5. **B13** `ThreadingHTTPServer` + `daemon_threads`
6. **B14/B15** body size cap (8 MB), frame cap (120), numeric validation helper

**Verify:** `truncate -s 0 .cache/spotify_token.json`, start → clean re-auth, no
crash loop. `systemctl stop` → panel goes dark.

## ✅ Phase 1 — Visible bug fixes (~1 h)
7. **B5** `is_instrumental` in `/api/state` (badge starts working)
8. **B8** reset `is_instrumental` on track change
9. **B9** pass accent + connection state to both bare `render_clock` calls
10. **B10** restore the Advanced panel when leaving Custom Slate
11. **B11/B12** live-lyrics refetch on track change + `progress_age_ms` drift fix
12. **B6/B7** lyrics scroll snapback + reset on track change
13. **B18** treat LRCLIB 404 as "no lyrics", not an error

## ✅ Phase 2 — Visual pipeline (~1.5 h) ⭐ *best look-per-effort in the doc*
14. **V1** gamma LUT + `--gamma` (measure against the library's own CIE curve)
15. **V2** saturation/contrast boost at download time
16. **V4** integer pixel snapping in `draw_scrolling_text` (kills shimmer)
17. **V5** eased brightness ramp
18. **V3** ordered dithering on the art downscale *(optional, subtle)*

**Verify:** dump `--mock-output` frames of a dark album cover before and after.

## ✅ Phase 3 — Performance (~2 h)
19. **P1** smallest adequate Spotify image variant → pre-fit once → pre-rotated
    frame cache
20. **P2** cached gradient mask for the text edge fade
21. **P3/P4** cache clock face and clock overlay by minute
22. **P5** per-mode frame budgets (CD/lyrics full, clock 10, static slate 2)

**Verify:** `htop` via control script option 8, before and after.

## ✅ Phase 4 — Lyrics overhaul (~4 h) ⭐ *your main ask*
23. **F2 (c)** threshold the anti-aliasing first — measure how far that alone gets you
24. **F2 (a/b)** bundle a pixel font, add `--lyrics-font`
25. **F1** Karaoke mode: word wrap → per-word highlight → next-line preview
26. **B19** parse enhanced-LRC `<mm:ss.xx>` word timings when present
27. **F3** auto-fit font sizing (slider becomes a maximum)
28. **B20** derive pop-mode line positions from font size
29. **F4** self-correcting progress offset (retires the lead slider as a patch)
30. **F5** lyrics disk cache + title normalization + `/api/search` fallback
31. Countdown indicator during instrumental gaps, replacing the static `· · ·`

**Verify:** one dense rap track, one slow ballad, `--mock-output` both.

## ⬜ Phase 5 — Persistence & appliance polish (~2 h)
32. **F6** persisted settings ⭐ (debounced, atomic, CLI still overrides)
33. **F11** on-matrix status/error screens ("No Wi-Fi", "Spotify auth needed")
34. **F8** night mode / scheduled brightness (uses V5's ramp)
35. **S3** commit the systemd unit as a versioned template
36. **B16** run as non-root, token file `0600`

## ⬜ Phase 6 — Web panel (~3 h)
37. **W1** live matrix preview via `/api/frame.png` ⭐
38. **W2/W4** effective-mode label, progress bar and elapsed/total
39. **W5** surface hidden settings: smart-scroll toggle (**B24**), cycle
    duration, transition style/duration
40. **W6** off/sleep button
41. **W7** abort timeouts, disconnect banner, self-hosted fonts, escape lyrics
42. **B17** `--web-bind` and optional `--web-token`

## ⬜ Phase 7 — Display modes & idle (~4 h) ⭐ *highest visual payoff*
43. **F9** full-bleed album art mode + **V8** Ken Burns pan
44. **F10** progress ring around the vinyl
45. **F7** auto accent colour from album art + **V11** palette strip
46. **V6** ambient idle modes — start with plasma and matrix rain, then
    starfield / Life / fireplace; add `--idle-mode` and `cycle`
47. **V7** weather on the clock face (Open-Meteo, no key)
48. **7.4** new transitions: pixel dissolve, vinyl drop
49. **V10** authentic 33⅓ RPM default

## ⬜ Phase 8 — Reactive features (~2.5 h)
*(**V9** beat sync removed — endpoints are 403 for apps registered after Nov 2024.)*

50. **W3** playback controls (scope change + one re-auth via the existing flow)
51. **V13** up-next queue peek · **V12** liked-track heart
52. **V14** on-matrix toast overlay · **V15** track-change accent flash
53. **F12** scrolling text message endpoint (closes the README's false claim)

## ⬜ Phase 9 — Cleanup & structure (~3 h)
55. **B21–B32** dead constants, unreachable `slide-left`, `/mode` inconsistency,
    unescaped lyric HTML, README corrections (7 themes, no scrolling-message API)
56. **B31** `matrix_control.ps1`: use `$PI_HOST`, delete unused `PI_PASS`,
    check whether `git pull` actually succeeded before restarting
57. **P6** verify `isolcpus=3` is actually being used (`taskset -cp`)
58. **S4** pin dependencies
59. **S2** pytest for the pure functions
60. **S1** split the module; move the panel to real `.html`/`.css`/`.js` files

---

## Recommended order if you only do some of it

**Session 1 (2 h) — Phase 0 + 1.** No new features, but both crash loops die,
the panel clears on stop, and six visible bugs go away.

**Session 2 (1.5 h) — Phase 2.** Gamma and pixel-snapping. The smallest diff
with the largest change in how the display actually looks.

**Session 3 (4 h) — Phase 4.** The lyrics rewrite — the thing actually bothering
you day to day.

**Then** Phase 5 (persistence) and Phase 7 (idle modes), in that order — those
two are what turn it from a Spotify display into something worth leaving on.
