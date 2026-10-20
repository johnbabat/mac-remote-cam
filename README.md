# Mac Remote Cam

A self-hosted, privacy-first remote camera, microphone, and smart surveillance system built for macOS. Control and monitor your Mac's camera and microphone from any smartphone browser over local Wi-Fi or from anywhere in the world via Tailscale.

**Intended use:** personal home monitoring (your own home, pets, packages) on your own Mac. Everything runs locally and recordings stay on your machine; it is not a hosted service and does not collect data about other users.

---

## Features

- **On-Demand Hardware Control**: Turn the MacBook camera and microphone completely on or off remotely from your phone. When off, the camera sensor and green indicator LED remain completely powered down.
- **Smart Auto-Sleep Watchdog**: Automatically powers down the camera and microphone if no viewer is connected and no monitoring mode is active for 30 seconds.
- **Motion Detection Monitoring**:
  - Uses dual-stage computer vision (inter-frame pixel differencing + MOG2 adaptive background subtraction with morphological filtering) calibrated to ignore minor sensor noise while detecting real movement.
  - Automatically records **3-minute synchronized video + audio MP4 clips** (`motion_YYYYMMDD_HHMMSS.mp4`).
  - Seamlessly rolls over into consecutive 3-minute recordings as long as motion continues.
- **Noise (Sound) Detection Monitoring**:
  - Continuously analyzes real-time 16-bit PCM audio RMS energy levels.
  - Triggers automatic **3-minute video + audio recordings** (`noise_YYYYMMDD_HHMMSS.mp4`) when sustained sound (voices, knocks, doors, claps) exceeds the threshold.
  - Can run independently or alongside Motion Detection.
- **Live Audio Streaming**: Low-latency raw PCM audio streaming to mobile browsers (including iOS Safari Web Audio API support).
- **Manual Recording & Snapshots**: Capture 720p H.264/AAC MP4 video recordings and high-resolution JPEG snapshots on demand.
- **Categorized Media Gallery**: Browse, preview, download, and delete captured **Pictures** and **Videos** directly from the mobile web UI.
- **Sleep Prevention (`caffeinate`)**: Automatically keeps your Mac awake while the server is running so you never lose connection.
- **Built-in Security**:
  - HMAC-SHA256 signed session cookies.
  - Constant-time password verification and automatic IP rate-limiting / lockout after failed login attempts.
  - Path-traversal protection on all media endpoints.

---

## Project Structure

```text
├── main.py              # FastAPI server, authentication middleware, REST API & streaming routes
├── camera.py            # Thread-safe OpenCV capture, motion/noise state machine, watchdog
├── audio.py             # SoundDevice microphone capture, PCM pub/sub fan-out, RMS energy analyzer
├── recorder.py          # PyAV H.264 video + AAC audio MP4 muxer with atomic .part file writing
├── templates/
│   ├── index.html       # Mobile-first responsive dashboard UI
│   └── login.html       # Password authentication screen
├── start.sh             # CLI startup script
├── start.command        # macOS Login Items double-clickable launcher
├── requirements.txt     # Python dependencies
└── .env.example         # Example environment configuration
```

---

## Getting Started

### 1. Prerequisites
- **macOS** (Apple Silicon or Intel)
- **Python 3.11+**

### 2. Installation

Clone the repository and create a virtual environment:

```bash
git clone https://github.com/johnbabat/mac-remote-cam.git
cd mac-remote-cam

python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

### 3. Configuration

Copy the example environment file and set your password and session secret:

```bash
cp .env.example .env
```

Edit `.env`:
```ini
CAM_PASSWORD=your_secure_password
SESSION_SECRET=your_random_hex_secret
```
*(Tip: Generate a random secret with `openssl rand -hex 32`)*

> **Note:** Changing `CAM_PASSWORD` or `SESSION_SECRET` and restarting the server immediately invalidates all existing sessions across all devices.

---

## Running the Server

Start the server from your terminal:

```bash
./start.sh
```

On first run, macOS will prompt **Terminal** for permission to access the **Camera** and **Microphone**. Click **Allow**.

The server listens on `http://0.0.0.0:8000`.

### Auto-Start on Mac Login

Because macOS security (TCC) blocks headless background daemons (`launchd`) from accessing the webcam and microphone, auto-start should run within Terminal's security context:

1. Open **System Settings → General → Login Items & Extensions**.
2. Under **Open at Login**, click the **`+`** button.
3. Select [`start.command`](./start.command) from this repository folder and click **Open**.

---

## Accessing From Your Phone

### Option 1: Local Wi-Fi
When your phone is on the same Wi-Fi network as your Mac:
1. Find your Mac's local IP address:
   ```bash
   ipconfig getifaddr en0
   ```
2. Open Safari or Chrome on your phone and visit:
   ```text
   http://<your-mac-local-ip>:8000
   ```

### Option 2: Anywhere in the World (via Tailscale)
To securely access your camera over 4G/5G cellular or external Wi-Fi without opening router ports:

1. **Install Tailscale on your Mac**:
   ```bash
   brew install tailscale
   sudo brew services start tailscale
   tailscale up
   ```
2. **Install Tailscale on your Phone** (from the App Store or Google Play) and sign in with the same account.
3. **Get your Mac's permanent Tailscale IP**:
   ```bash
   tailscale ip -4
   ```
4. **Connect from your phone**:
   Open your mobile browser and visit:
   ```text
   http://<your-tailscale-ip>:8000
   ```
5. **Save as a Mobile App**:
   - **iOS (Safari)**: Tap **Share → Add to Home Screen**
   - **Android (Chrome)**: Tap **⋮ → Add to Home screen**

---

## License

MIT — see [LICENSE](LICENSE).

## Roadmap

Planned features and improvements are tracked in [ROADMAP.md](ROADMAP.md).
