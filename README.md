# Spotify Matrix

A Python project for the Raspberry Pi that displays your current Spotify playback on a 64x64 RGB LED matrix. 

The display seamlessly auto-cycles between beautiful modes: a rotating vinyl record displaying your album art, synchronized scrolling lyrics using LRCLIB, and a clean clock face when playback is paused.

Everything is managed via an intuitive, mobile-friendly Web Control Panel accessible from your browser — including a live preview of exactly what the panel is showing.

## Documentation

This README serves as a brief summary. For full instructions on setup, installation, and usage, please read the definitive user guide:

👉 **[Complete User Guide (userguidefinal.md)](userguidefinal.md)**

## Features at a Glance

- **✨ Original Auto-Cycling** — Seamlessly transitions between CD view, Lyrics, and the idle screen when paused. The disc-before-lyrics delay is adjustable.
- **🎵 Spinning CD View** — Your album art is cropped into a vinyl record and spins dynamically with playback, with a progress ring around the rim.
- **📝 Synchronized Lyrics** — Live synced lyrics in three styles: Karaoke (word-wrapped with per-word highlighting), Scroll, and Pop.
- **🎹 Instrumental Detection** — Displays a beautiful pulsing audio bars visualizer for instrumental tracks instead of a generic "no lyrics" message.
- **🖼️ Album Art Mode** — The cover filling all 64×64 with a hairline progress bar, optionally with a slow Ken Burns drift.
- **🌌 Ambient Idle Screens** — Choose what fills the panel when nothing is playing: Clock (the original), Plasma, Matrix Rain, Starfield, Game of Life, Fireplace, or cycle through them.
- **🎨 Accent Color Themes** — Restyle the matrix and web UI from 7 curated colors, a full custom picker, or **Auto**, which follows the album art.
- **🕐 Clock Mode** — A crisp digital clock with date, day, and a sweeping seconds indicator.
- **🖼️ Custom Slate Canvas** — A built-in WYSIWYG editor on the web panel to upload images/GIFs, overlay text, and cast it instantly to the matrix.
- **📺 Live Preview** — See exactly what the LED panel is showing, in the web UI, without standing over the device.
- **⏯️ Playback Control** — Play, pause and skip from the panel (needs one re-authorization to grant the scope).
- **📱 Web Control Panel** — A rich dashboard for display modes, lyric font sizes, brightness, scroll speeds, and live logs from any device on your WiFi.
- **🎤 Live Web Lyrics** — Follow the synced lyrics on your phone, drift-corrected against the matrix.
- **💾 Settings That Persist** — Brightness, colour, mode and everything else survive a reboot.
- **🔌 Plug & Play Appliance** — Runs entirely as a background systemd service. Just plug in the Pi and it works.

## Quick Links

- [First-Time Authentication](userguidefinal.md#chapter-2-first-time-setup--authentication)
- [Setting up the Background Service](userguidefinal.md#chapter-3-automating-the-plug--play-boot)
- [Using the Web Control Panel](userguidefinal.md#chapter-4-web-control-panel--settings)
- [6-Month Re-Authorization Maintenance](userguidefinal.md#chapter-6-maintenance-6-month-re-auth)

## Upgrading

Settings now persist to `.cache/settings.json`, so the first boot after an
upgrade keeps whatever you had set. Two notes:

- **Playback control and up-next need a re-authorization.** They use Spotify
  scopes the old token does not carry. Run `--auth-only` once (menu option 2 in
  `matrix_control.ps1`); everything else works without it.
- **Nothing changes look on its own.** The idle screen defaults to `clock` and
  the display mode to `Original`, so the panel behaves exactly as before until
  you pick something else.

Run the tests with `python -m pytest tests/ -q` — they need no hardware.

---
*Powered by `hzeller/rpi-rgb-led-matrix`, the Spotify Web API, and LRCLIB.*
