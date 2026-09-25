import asyncio
import os
import time
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from camera import camera_manager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MEDIA_DIR = os.path.join(BASE_DIR, "media")
os.makedirs(MEDIA_DIR, exist_ok=True)

app = FastAPI(title="Mac Remote Cam")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/api/status")
async def get_status():
    return camera_manager.get_status()

@app.post("/api/start")
def start_camera():
    ok, msg = camera_manager.start()
    if not ok:
        raise HTTPException(status_code=500, detail=msg)
    return {"success": True, "message": msg}

@app.post("/api/stop")
def stop_camera():
    _, msg = camera_manager.stop()
    return {"success": True, "message": msg}

@app.post("/api/snapshot")
def snapshot():
    ok, res = camera_manager.capture_snapshot(output_dir=MEDIA_DIR)
    if not ok:
        raise HTTPException(status_code=400, detail=res)
    return {"success": True, "filename": res}

async def video_frame_generator():
    while camera_manager.is_active:
        frame = camera_manager.get_jpeg_frame()
        if frame:
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
        await asyncio.sleep(0.04)

@app.get("/stream")
async def video_feed():
    return StreamingResponse(video_frame_generator(), media_type="multipart/x-mixed-replace; boundary=frame")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000)
