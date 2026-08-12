# Spotify Matrix Change Log

## 2026-07-18 00:45:43
- **Optimized Web UI Sync:** Added immediate UI state fetching upon slider changes to eliminate UI desync/lag.
- **Removed Manual Poll Rate Slider:** Fully deleted manual poll interval selection since the backend actively optimizes it dynamically based on Spotify playback state.
- **Fixed API Fallbacks:** Changed API endpoints to properly source defaults (like 10.0 RPM and 20.0 Scroll Speed) from the main state structure to prevent duplicate/desynced defaults.
- **Optimized Track Transition Polling:** Altered the accelerated track transition polling logic to activate during the last 10 seconds of a track (down from 15 seconds) to save bandwidth.

