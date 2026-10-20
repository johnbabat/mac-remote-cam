# Roadmap

Planned work for Mac Remote Cam, roughly in priority order.

## Testing & reliability

- [ ] Add a `pytest` suite: auth (login, lockout, session cookie), media path-traversal guards, gallery listing/deletion
- [ ] Unit-test the motion detector and noise RMS trigger against recorded sample frames/audio
- [ ] Test the recorder's `.part` → `.mp4` atomic finalize, including crash/interrupt cases
- [ ] Recover or clean up orphaned `.part` files on startup
- [ ] Set up GitHub Actions CI (lint + tests on macOS runner)

## Configuration & settings UI

- [ ] Move hard-coded tunables (motion thresholds, noise RMS threshold `850`, auto-record duration `180s`, idle watchdog timeout) into `.env` / a config file
- [ ] Add a settings panel in the dashboard to adjust sensitivity and clip length live
- [ ] Persist settings across restarts

## Storage management

- [ ] Retention policy: auto-delete clips older than N days or when the media folder exceeds a size cap
- [ ] Show disk usage in the dashboard
- [ ] Bulk select / delete in the gallery
- [ ] Generate video thumbnails for the gallery

## Notifications

- [ ] Web Push notification to the phone when motion/noise recording starts
- [ ] Optional quiet hours / scheduled monitoring windows

## AI event descriptions & semantic search

Opt-in feature for personal home use that calls existing hosted LLM APIs (OpenAI, Anthropic or Google Gemini, chosen by the user with their own API key) to describe what the camera sees, so recordings can be searched in plain language. No models are trained, fine-tuned or hosted by this project; it is a client of those APIs.

- [ ] Pluggable provider setting (OpenAI / Anthropic / Gemini) with the API key read from `.env`
- [ ] When motion/noise monitoring triggers, sample a few key frames from the clip and send them to the selected provider's vision API to get a short text description (e.g. "a cat jumps onto the desk", "the front door opens")
- [ ] Store descriptions with timestamps, trigger type and clip filename in a local SQLite event log
- [ ] Generate text embeddings for each description and store them locally (e.g. `sqlite-vec`) for semantic search
- [ ] Dashboard search bar: query like "when did the door open?" and get matching clips ranked by relevance, with a jump to the moment in the video
- [ ] Event timeline view showing AI descriptions alongside the gallery
- [ ] Privacy controls: disabled by default, clear indicator when frames leave the Mac, per-mode toggle, and deleting a clip also deletes its description and embedding
- [ ] Rate limiting / frame sampling settings to control API usage and cost

## Phone app

Today the dashboard is a mobile web page opened in the phone's browser. A native app would make it faster to open and allow real push notifications.

- [ ] Pick the stack: React Native (Expo) for iOS + Android from one codebase, or SwiftUI if iOS-only
- [ ] Token-based auth endpoint for the app (the web UI uses session cookies), stored in the iOS Keychain / Android Keystore
- [ ] Live view: MJPEG video and PCM audio playback, start/stop camera, toggle motion and noise monitoring
- [ ] Gallery: browse, play, download and delete clips and snapshots
- [ ] Native push notifications (APNs / FCM) when a recording is triggered, with a tap to open the clip
- [ ] Event search screen once AI event descriptions are available
- [ ] Connection settings: local Wi-Fi address and Tailscale address, with automatic fallback

## Streaming improvements

- [ ] Replace MJPEG + raw PCM with a lower-bandwidth stream (HLS or WebRTC) with synchronized audio/video
- [ ] Adaptive quality based on connection (Tailscale over cellular vs. local Wi-Fi)
- [ ] Two-way audio (talk back through the Mac speakers)

## Packaging & UX

- [ ] Menu-bar app wrapper with start/stop and status indicator
- [ ] Proper `launchd` agent as an alternative to the Login Items launcher
- [ ] Installable PWA manifest and icons for "Add to Home Screen"
- [ ] Support selecting among multiple cameras/microphones (e.g. external USB webcam)

## Docs

- [ ] Troubleshooting guide for macOS camera/microphone (TCC) permissions
- [ ] Screenshots of the dashboard in the README
