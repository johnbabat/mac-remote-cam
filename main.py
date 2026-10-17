import asyncio
import hashlib
import hmac
import os
import queue
import subprocess
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse, StreamingResponse)
from fastapi.templating import Jinja2Templates

from audio import audio_manager
from camera import camera_manager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

MEDIA_DIR = os.path.join(BASE_DIR, "media")
os.makedirs(MEDIA_DIR, exist_ok=True)

# ----------------------------------------------------------------- auth setup

CAM_PASSWORD = os.environ.get("CAM_PASSWORD", "")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
if not CAM_PASSWORD or not SESSION_SECRET:
    raise SystemExit("CAM_PASSWORD and SESSION_SECRET must be set in .env")

# Static session token derived from the password: changing the password
# (or the secret) logs out every device.
SESSION_TOKEN = hmac.new(SESSION_SECRET.encode(), b"cam-session:" + CAM_PASSWORD.encode(),
                         hashlib.sha256).hexdigest()
SESSION_COOKIE = "cam_session"
SESSION_MAX_AGE = 30 * 24 * 3600  # 30 days

PUBLIC_PATHS = {"/login", "/favicon.ico"}
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 300
_failed_logins: dict[str, tuple[int, float]] = {}  # ip -> (count, locked_until)


def is_authenticated(request: Request) -> bool:
    token = request.cookies.get(SESSION_COOKIE, "")
    return bool(token) and hmac.compare_digest(token, SESSION_TOKEN)


class AuthMiddleware:
    """Pure ASGI middleware (doesn't interfere with streaming responses)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)
        if scope["path"] in PUBLIC_PATHS or is_authenticated(request):
            return await self.app(scope, receive, send)
        if scope["path"] == "/":
            response = RedirectResponse("/login", status_code=303)
        else:
            response = JSONResponse({"detail": "Not authenticated"}, status_code=401)
        await response(scope, receive, send)


# ------------------------------------------------------------ keep Mac awake

caffeinate_proc: subprocess.Popen | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global caffeinate_proc
    # -i: no idle sleep, -m: no disk sleep, -s: no system sleep on AC power.
    # -w: exit automatically when this server process exits.
    # (No -d: the display may sleep; the camera keeps working.)
    try:
        caffeinate_proc = subprocess.Popen(["caffeinate", "-ims", "-w", str(os.getpid())])
    except Exception as e:  # noqa: BLE001
        print(f"[caffeinate] could not start: {e}")
    camera_manager.start_watchdog()
    yield
    # Shutdown: finalize any recording and release the camera/mic.
    camera_manager.stop()
    audio_manager.stop()
    if caffeinate_proc and caffeinate_proc.poll() is None:
        caffeinate_proc.terminate()


app = FastAPI(title="Mac Remote Cam", lifespan=lifespan)
app.add_middleware(AuthMiddleware)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


def safe_media_path(filename: str) -> str:
    name = os.path.basename(filename)
    if not name or name.startswith(".") or name != filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = os.path.join(MEDIA_DIR, name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="File not found")
    return path


# ---------------------------------------------------------------- auth routes

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if is_authenticated(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request=request, name="login.html")


@app.post("/login")
async def login(request: Request):
    ip = request.client.host if request.client else "unknown"
    count, locked_until = _failed_logins.get(ip, (0, 0.0))
    if time.time() < locked_until:
        wait = int(locked_until - time.time())
        raise HTTPException(status_code=429, detail=f"Too many attempts. Try again in {wait}s.")

    try:
        body = await request.json()
        password = str(body.get("password", ""))
    except Exception:
        password = ""

    if not hmac.compare_digest(password.encode(), CAM_PASSWORD.encode()):
        count += 1
        locked_until = time.time() + LOCKOUT_SECONDS if count >= MAX_FAILED_LOGINS else 0.0
        _failed_logins[ip] = (0 if locked_until else count, locked_until)
        print(f"[auth] failed login from {ip} ({count})")
        raise HTTPException(status_code=401, detail="Wrong password")

    _failed_logins.pop(ip, None)
    response = JSONResponse({"success": True})
    response.set_cookie(SESSION_COOKIE, SESSION_TOKEN, max_age=SESSION_MAX_AGE,
                        httponly=True, samesite="strict")
    return response


@app.post("/logout")
async def logout():
    response = JSONResponse({"success": True})
    response.delete_cookie(SESSION_COOKIE)
    return response


# ------------------------------------------------------------------ main page

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/api/status")
async def get_status():
    camera_manager.heartbeat()
    status = camera_manager.get_status()
    status["audio_active"] = audio_manager.is_active
    status["audio_error"] = audio_manager.last_error
    status["caffeinated"] = caffeinate_proc is not None and caffeinate_proc.poll() is None
    return status


# Blocking hardware calls use plain `def` so FastAPI runs them in a thread
# pool instead of freezing the event loop (and every other request).

@app.post("/api/start")
def start_camera():
    success, message = camera_manager.start()
    if not success:
        raise HTTPException(status_code=500, detail=message)
    return {"success": True, "message": message}


@app.post("/api/stop")
def stop_camera():
    _, message = camera_manager.stop()
    audio_manager.stop()
    return {"success": True, "message": message}


@app.post("/api/monitor/toggle")
def toggle_monitor():
    camera_manager.media_dir = MEDIA_DIR
    success, message = camera_manager.toggle_monitoring()
    if not success:
        raise HTTPException(status_code=500, detail=message)
    return {"success": True, "message": message, "is_monitoring": camera_manager.is_monitoring}


@app.post("/api/monitor/noise/toggle")
def toggle_noise_monitor():
    camera_manager.media_dir = MEDIA_DIR
    success, message = camera_manager.toggle_noise_monitoring()
    if not success:
        raise HTTPException(status_code=500, detail=message)
    return {"success": True, "message": message, "is_noise_monitoring": camera_manager.is_noise_monitoring}


@app.post("/api/record/start")
def start_recording():
    success, result = camera_manager.start_recording(output_dir=MEDIA_DIR)
    if not success:
        raise HTTPException(status_code=400, detail=result)
    return {"success": True, "filename": result, "message": "Recording started"}


@app.post("/api/record/stop")
def stop_recording():
    success, result = camera_manager.stop_recording()
    if not success:
        raise HTTPException(status_code=400, detail=result)
    return {"success": True, "filename": result, "download_url": f"/media/{result}",
            "message": f"Saved {result}"}


@app.post("/api/snapshot")
def snapshot():
    success, result = camera_manager.capture_snapshot(output_dir=MEDIA_DIR)
    if not success:
        raise HTTPException(status_code=400, detail=result)
    return {"success": True, "filename": result, "download_url": f"/media/{result}",
            "message": "Snapshot captured"}


# -------------------------------------------------------------------- gallery

@app.get("/api/gallery")
async def get_gallery():
    files = []
    for fname in sorted(os.listdir(MEDIA_DIR), reverse=True):
        if not fname.endswith((".jpg", ".mp4")):
            continue
        stat = os.stat(os.path.join(MEDIA_DIR, fname))
        files.append({
            "name": fname,
            "url": f"/media/{fname}",
            "type": "video" if fname.endswith(".mp4") else "photo",
            "size_mb": round(stat.st_size / (1024 * 1024), 2),
            "created": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
        })
    return {"files": files}


@app.delete("/api/media/{filename}")
async def delete_file(filename: str):
    path = safe_media_path(filename)
    os.remove(path)
    return {"success": True, "message": f"Deleted {filename}"}


@app.get("/media/{filename}")
async def download_file(filename: str):
    path = safe_media_path(filename)
    media_type = "video/mp4" if filename.endswith(".mp4") else "image/jpeg"
    return FileResponse(path, media_type=media_type, filename=filename)


# -------------------------------------------------------------------- streams

async def video_frame_generator():
    camera_manager.add_viewer()
    try:
        last_sent = 0.0
        while camera_manager.is_active:
            if camera_manager.last_frame_time != last_sent:
                last_sent = camera_manager.last_frame_time
                frame_bytes = await asyncio.to_thread(camera_manager.get_jpeg_frame, 70)
                if frame_bytes:
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n")
            await asyncio.sleep(0.03)
    finally:
        camera_manager.remove_viewer()


@app.get("/stream")
async def stream():
    if not camera_manager.is_active:
        raise HTTPException(status_code=400, detail="Camera is currently turned off.")
    return StreamingResponse(video_frame_generator(),
                             media_type="multipart/x-mixed-replace; boundary=frame",
                             headers={"Cache-Control": "no-store"})


async def audio_stream_generator(q: queue.Queue):
    """Raw 16-bit mono PCM; the browser plays it via the Web Audio API."""
    try:
        while True:
            try:
                yield q.get_nowait()
            except queue.Empty:
                if not audio_manager.is_active:
                    break
                await asyncio.sleep(0.01)
    finally:
        audio_manager.unsubscribe(q)


@app.get("/audio")
async def audio_feed():
    q = audio_manager.subscribe()
    if not audio_manager.is_active:
        audio_manager.unsubscribe(q)
        raise HTTPException(status_code=503, detail=audio_manager.last_error or "Microphone unavailable")
    return StreamingResponse(
        audio_stream_generator(q),
        media_type="application/octet-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Sample-Rate": str(audio_manager.sample_rate),
        },
    )


if __name__ == "__main__":
    import uvicorn
    # timeout_graceful_shutdown: don't hang forever on open streams when you
    # press Ctrl+C; the lifespan shutdown then finalizes any recording.
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False,
                timeout_graceful_shutdown=3)
