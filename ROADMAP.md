# Roadmap

Planned work for Mac Remote Cam, roughly in priority order. Estimates are rough.

## Testing & reliability (~6h)

- [ ] Add a `pytest` suite: auth (login, lockout, session cookie), media path-traversal guards, gallery listing/deletion
- [ ] Unit-test the motion detector and noise RMS trigger against recorded sample frames/audio
- [ ] Test the recorder's `.part` → `.mp4` atomic finalize, including crash/interrupt cases
- [ ] Recover or clean up orphaned `.part` files on startup
- [ ] Set up GitHub Actions CI (lint + tests on macOS runner)

## Configuration & settings UI (~4h)

- [ ] Move hard-coded tunables (motion thresholds, noise RMS threshold `850`, auto-record duration `180s`, idle watchdog timeout) into `.env` / a config file
- [ ] Add a settings panel in the dashboard to adjust sensitivity and clip length live
- [ ] Persist settings across restarts

## Storage management (~3h)

- [ ] Retention policy: auto-delete clips older than N days or when the media folder exceeds a size cap
- [ ] Show disk usage in the dashboard
- [ ] Bulk select / delete in the gallery
- [ ] Generate video thumbnails for the gallery

## Notifications (~3h)

- [ ] Web Push notification to the phone when motion/noise recording starts
- [ ] Optional quiet hours / scheduled monitoring windows

## Streaming improvements (~5h)

- [ ] Replace MJPEG + raw PCM with a lower-bandwidth stream (HLS or WebRTC) with synchronized audio/video
- [ ] Adaptive quality based on connection (Tailscale over cellular vs. local Wi-Fi)
- [ ] Two-way audio (talk back through the Mac speakers)

## Packaging & UX (~4h)

- [ ] Menu-bar app wrapper with start/stop and status indicator
- [ ] Proper `launchd` agent as an alternative to the Login Items launcher
- [ ] Installable PWA manifest and icons for "Add to Home Screen"
- [ ] Support selecting among multiple cameras/microphones (e.g. external USB webcam)

## Docs (~1h)

- [ ] Troubleshooting guide for macOS camera/microphone (TCC) permissions
- [ ] Screenshots of the dashboard in the README
