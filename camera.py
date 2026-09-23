import os
import threading
import time
from datetime import datetime
from typing import Optional, Tuple

import cv2


class CameraManager:
    def __init__(self, camera_index: int = 0):
        self.camera_index = camera_index
        self.cap: Optional[cv2.VideoCapture] = None
        self.is_active = False
        self.lock = threading.Lock()
        self.last_frame = None
        self.fps = 0.0
        self._frame_count = 0
        self._fps_timer = time.time()
        self._worker_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self) -> Tuple[bool, str]:
        with self.lock:
            if self.is_active and self.cap and self.cap.isOpened():
                return True, "Camera is already running"
            cap = cv2.VideoCapture(self.camera_index, cv2.CAP_AVFOUNDATION)
            if not cap.isOpened():
                cap = cv2.VideoCapture(self.camera_index)
            if not cap.isOpened():
                return False, "Could not open camera."
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            cap.set(cv2.CAP_PROP_FPS, 30)
            self.cap = cap
            self.is_active = True
            self._stop_event = threading.Event()
            self._worker_thread = threading.Thread(
                target=self._capture_loop, args=(cap, self._stop_event), daemon=True
            )
            self._worker_thread.start()
            return True, "Camera started"

    def stop(self) -> Tuple[bool, str]:
        with self.lock:
            if not self.is_active:
                return True, "Camera is already off"
            self.is_active = False
            self._stop_event.set()
            worker, cap = self._worker_thread, self.cap
            self._worker_thread, self.cap = None, None
        if worker:
            worker.join(timeout=3)
        if cap:
            cap.release()
        self.last_frame = None
        self.fps = 0.0
        return True, "Camera turned off"

    def _capture_loop(self, cap: cv2.VideoCapture, stop_event: threading.Event):
        while not stop_event.is_set():
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.05)
                continue
            self.last_frame = frame
            self._frame_count += 1
            now = time.time()
            if now - self._fps_timer >= 1.0:
                self.fps = round(self._frame_count / (now - self._fps_timer), 1)
                self._frame_count = 0
                self._fps_timer = now

    def get_jpeg_frame(self, quality: int = 75) -> Optional[bytes]:
        if self.last_frame is None or not self.is_active:
            return None
        ok, buffer = cv2.imencode(".jpg", self.last_frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        return buffer.tobytes() if ok else None

    def capture_snapshot(self, output_dir: str) -> Tuple[bool, str]:
        if not self.is_active or self.last_frame is None:
            return False, "Camera is not active"
        os.makedirs(output_dir, exist_ok=True)
        filename = f"snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        cv2.imwrite(os.path.join(output_dir, filename), self.last_frame)
        return True, filename

    def get_status(self) -> dict:
        return {"is_active": self.is_active, "fps": self.fps if self.is_active else 0}


camera_manager = CameraManager()
