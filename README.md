# Spotify Matrix

A Python project for the Raspberry Pi that displays your current Spotify playback on a 64x64 RGB LED matrix. 

The display seamlessly auto-cycles between beautiful modes: a rotating vinyl record displaying your album art, synchronized scrolling lyrics using LRCLIB, and a clean clock face when playback is paused.

Everything is managed via an intuitive, mobile-friendly Web Control Panel accessible from your browser.

## Documentation

This README serves as a brief summary. For full instructions on setup, installation, and usage, please read the definitive user guide:

👉 **[Complete User Guide (userguidefinal.md)](userguidefinal.md)**

## Features at a Glance

- **✨ Smart Auto-Cycling** — Seamlessly transitions between CD view (10s), Lyrics, and Clock (when paused).
- **🎵 Spinning CD View** — Your album art is cropped into a vinyl record and spins dynamically with playback.
- **📝 Synchronized Lyrics** — Live synced lyrics with multiple styling modes (Scroll/Pop) and intelligent time-proportional horizontal scrolling.
- **🕐 Clock Mode** — A crisp digital clock with date, day, and a sweeping seconds indicator.
- **📱 Web Control Panel** — A rich 5-card dashboard to control display modes, lyric font sizes, brightness, scroll speeds, and live logs from any device on your WiFi.
- **🔌 Plug & Play Appliance** — Runs entirely as a background systemd service. Just plug in the Pi and it works.

## Quick Links

- [First-Time Authentication](userguidefinal.md#chapter-2-first-time-setup--authentication)
- [Setting up the Background Service](userguidefinal.md#chapter-3-automating-the-plug--play-boot)
- [Using the Web Control Panel](userguidefinal.md#chapter-4-web-control-panel--settings)
- [6-Month Re-Authorization Maintenance](userguidefinal.md#chapter-6-maintenance-6-month-re-auth)

---
*Powered by `hzeller/rpi-rgb-led-matrix`, the Spotify Web API, and LRCLIB.*
