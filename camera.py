import os
import threading
import time
from datetime import datetime
from typing import Optional, Tuple

import cv2
import numpy as np

from audio import audio_manager
from recorder import Recorder


class CameraManager:
    """
    Thread-safe camera controller for macOS.
    Includes on-demand live streaming, motion detection monitoring,
    noise (sound) detection monitoring, and automatic recording in 3-minute chunks.
    """

    def __init__(self, camera_index: int = 0, idle_timeout: int = 30):
        self.camera_index = camera_index
        self.idle_timeout = idle_timeout
        self.cap: Optional[cv2.VideoCapture] = None
        self.is_active = False
        self.lock = threading.Lock()
        self.last_frame = None
        self.last_frame_time = 0.0
        self.fps = 0.0
        self._frame_count = 0
        self._fps_timer = time.time()
        self._worker_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Recording
        self.record_lock = threading.Lock()
        self.recorder: Optional[Recorder] = None
        self._record_audio_q = None
        self.is_auto_recording = False
        self.auto_recording_start_time = 0.0
        self.auto_record_trigger_type: Optional[str] = None  # "motion" or "noise"
        self.auto_record_duration = 180.0  # 3 minutes

        # Motion detection
        self.is_monitoring = False
        self.motion_detected = False
        self._last_motion_time = 0.0
        self._bg_subtractor: Optional[cv2.BackgroundSubtractorMOG2] = None
        self._prev_gray: Optional[np.ndarray] = None
        self._consecutive_motion_frames = 0
        self._motion_eval_timer = 0.0

        # Noise detection
        self.is_noise_monitoring = False
        self.noise_detected = False
        self._last_noise_time = 0.0
        # RMS threshold: 16-bit PCM values range up to 32767.
        # Ambient quiet room: ~50-300 RMS. Normal conversation / door knock / clap: 800-4000+ RMS.
        self.noise_threshold_rms = 1000.0
        self.noise_persistence_count = 0

        self.media_dir = "media"

        # Presence tracking for auto-off
        self._viewers = 0
        self._viewers_lock = threading.Lock()
        self.last_heartbeat = 0.0
        self._watchdog_started = False

        # Register audio callback for noise analysis
        audio_manager.register_callback(self._on_audio_chunk)
        audio_manager.set_keep_alive(lambda: self.is_noise_monitoring)

    # ---------------------------------------------------------------- camera

    def start(self) -> Tuple[bool, str]:
        with self.lock:
            if self.is_active and self.cap and self.cap.isOpened():
                return True, "Camera is already running"

            cap = cv2.VideoCapture(self.camera_index, cv2.CAP_AVFOUNDATION)
            if not cap.isOpened():
                cap = cv2.VideoCapture(self.camera_index)
            if not cap.isOpened():
                return False, "Could not open camera. Check Camera permission for Terminal in System Settings."

            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            cap.set(cv2.CAP_PROP_FPS, 30)

            self.cap = cap
            self.is_active = True
            self.last_heartbeat = time.time()
            self._stop_event = threading.Event()

            # Reset motion detector
            self._bg_subtractor = cv2.createBackgroundSubtractorMOG2(
                history=300, varThreshold=25, detectShadows=False
            )

            self._worker_thread = threading.Thread(
                target=self._capture_loop, args=(cap, self._stop_event), daemon=True
            )
            self._worker_thread.start()
            return True, "Camera turned on"

    def stop(self) -> Tuple[bool, str]:
        # Disabling monitoring if stopping camera
        self.is_monitoring = False
        self.is_noise_monitoring = False
        self.is_auto_recording = False
        self.auto_record_trigger_type = None

        # Finish any active recording first
        self.stop_recording()

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
            try:
                cap.release()
            except Exception:
                pass
        self.last_frame = None
        self.fps = 0.0
        self._bg_subtractor = None
        self._prev_gray = None
        self._consecutive_motion_frames = 0
        return True, "Camera turned off"

    # -------------------------------------------------------- motion monitoring

    def toggle_monitoring(self, enable: Optional[bool] = None) -> Tuple[bool, str]:
        if enable is None:
            enable = not self.is_monitoring

        if enable:
            if not self.is_active:
                ok, msg = self.start()
                if not ok:
                    return False, msg
            self.is_monitoring = True
            return True, "Motion monitoring active"
        else:
            self.is_monitoring = False
            if self.is_auto_recording and self.auto_record_trigger_type == "motion":
                self.stop_recording()
            return True, "Motion monitoring disabled"

    # -------------------------------------------------------- noise monitoring

    def toggle_noise_monitoring(self, enable: Optional[bool] = None) -> Tuple[bool, str]:
        if enable is None:
            enable = not self.is_noise_monitoring

        if enable:
            # Need camera active so recording can capture both video & audio
            if not self.is_active:
                ok, msg = self.start()
                if not ok:
                    return False, msg
            audio_manager.start()
            self.is_noise_monitoring = True
            return True, "Noise monitoring active"
        else:
            self.is_noise_monitoring = False
            self.noise_detected = False
            if self.is_auto_recording and self.auto_record_trigger_type == "noise":
                self.stop_recording()
            if not audio_manager.subscribers:
                audio_manager.stop()
            return True, "Noise monitoring disabled"

    def _on_audio_chunk(self, rms: float, chunk: bytes):
        """Called for every incoming audio buffer from the microphone."""
        now = time.time()
        if not self.is_noise_monitoring:
            return

        if rms >= self.noise_threshold_rms:
            self.noise_persistence_count += 1
            # Require 2 buffers (~40ms) to filter out occasional click/pop
            if self.noise_persistence_count >= 2:
                self._last_noise_time = now
                self.noise_detected = True
        else:
            self.noise_persistence_count = max(0, self.noise_persistence_count - 1)
            if now - self._last_noise_time > 3.0:
                self.noise_detected = False

    def _evaluate_motion(self, frame: np.ndarray, now: float):
        """
        Calibrated motion detection:
        - Filters out minor fidgets / micro-movements (head turns, blinking, minor shift).
        - Reliably triggers on deliberate actions: waving hand, standing up, walking across room.
        - Uses morphological opening (erodes isolated pixel noise) and multi-frame persistence.
        """
        small = cv2.resize(frame, (480, 270))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (17, 17), 0)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))

        # 1. Inter-frame difference
        frame_diff_detected = False
        if self._prev_gray is not None:
            delta = cv2.absdiff(self._prev_gray, blurred)
            _, delta_thresh = cv2.threshold(delta, 25, 255, cv2.THRESH_BINARY)
            delta_opened = cv2.morphologyEx(delta_thresh, cv2.MORPH_OPEN, kernel)
            delta_count = cv2.countNonZero(delta_opened)
            if delta_count > 650:
                frame_diff_detected = True
        self._prev_gray = blurred

        # 2. Adaptive background subtraction
        bg_detected = False
        if self._bg_subtractor is not None:
            fg_mask = self._bg_subtractor.apply(blurred, learningRate=0.005)
            _, bg_thresh = cv2.threshold(fg_mask, 200, 255, cv2.THRESH_BINARY)
            bg_opened = cv2.morphologyEx(bg_thresh, cv2.MORPH_OPEN, kernel)
            bg_count = cv2.countNonZero(bg_opened)
            if bg_count > 800:
                bg_detected = True

        if frame_diff_detected or bg_detected:
            self._consecutive_motion_frames += 1
            if self._consecutive_motion_frames >= 2:
                self._last_motion_time = now
                self.motion_detected = True
        else:
            self._consecutive_motion_frames = max(0, self._consecutive_motion_frames - 1)
            if now - self._last_motion_time > 3.5:
                self.motion_detected = False

        # Motion recording trigger
        if self.is_monitoring:
            if not self.is_recording and self.motion_detected:
                ok, fname = self.start_recording(self.media_dir, trigger_type="motion")
                if ok:
                    self.is_auto_recording = True
                    self.auto_record_trigger_type = "motion"
                    self.auto_recording_start_time = now
                    print(f"[motion] Motion triggered! Started recording: {fname}")

    def _check_auto_recording_rollover(self, now: float):
        """Handles 3-minute chunk completion and rolling continuation."""
        if not self.is_auto_recording:
            return

        elapsed = now - self.auto_recording_start_time
        if elapsed >= self.auto_record_duration:
            print(f"[{self.auto_record_trigger_type}] 3-minute chunk finished. Finalizing recording...")
            last_trigger = self.auto_record_trigger_type
            self.stop_recording()

            # Check if condition is still active to roll into next chunk
            roll_needed = False
            trigger = None
            if self.is_monitoring and self.motion_detected:
                roll_needed = True
                trigger = "motion"
            elif self.is_noise_monitoring and self.noise_detected:
                roll_needed = True
                trigger = "noise"

            if roll_needed:
                print(f"[{trigger}] Event still active! Starting next 3-minute recording...")
                ok, fname = self.start_recording(self.media_dir, trigger_type=trigger)
                if ok:
                    self.is_auto_recording = True
                    self.auto_record_trigger_type = trigger
                    self.auto_recording_start_time = now

    def _capture_loop(self, cap: cv2.VideoCapture, stop_event: threading.Event):
        while not stop_event.is_set():
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.05)
                continue
            now = time.time()
            self.last_frame = frame
            self.last_frame_time = now
            self._frame_count += 1

            # Feed active recorder
            rec = self.recorder
            if rec is not None:
                rec.add_video_frame(frame, time.monotonic())

            # Evaluate motion every ~100ms
            if self.is_monitoring and (now - self._motion_eval_timer >= 0.1):
                self._motion_eval_timer = now
                self._evaluate_motion(frame, now)

            # Evaluate noise trigger
            if self.is_noise_monitoring and not self.is_recording and self.noise_detected:
                ok, fname = self.start_recording(self.media_dir, trigger_type="noise")
                if ok:
                    print(f"[noise] Noise triggered! Started recording: {fname}")

            # Check 3-minute rollover
            self._check_auto_recording_rollover(now)

            elapsed = now - self._fps_timer
            if elapsed >= 1.0:
                self.fps = round(self._frame_count / elapsed, 1)
                self._frame_count = 0
                self._fps_timer = now

    # ------------------------------------------------------------- recording

    def start_recording(self, output_dir: str, trigger_type: Optional[str] = None) -> Tuple[bool, str]:
        with self.record_lock:
            if self.recorder is not None:
                return True, self.recorder.filename
            if not self.is_active or self.last_frame is None:
                return False, "Turn the camera on before recording"

            os.makedirs(output_dir, exist_ok=True)
            if trigger_type:
                prefix = trigger_type  # "motion" or "noise"
            else:
                prefix = "recording"
            filename = f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
            h, w = self.last_frame.shape[:2]

            audio_q = audio_manager.subscribe(maxsize=1000)
            rec = Recorder(
                os.path.join(output_dir, filename), w, h,
                audio_q=audio_q if audio_manager.is_active else None,
                sample_rate=audio_manager.sample_rate,
            )
            rec.start()
            self.recorder = rec
            self._record_audio_q = audio_q
            if trigger_type:
                self.is_auto_recording = True
                self.auto_record_trigger_type = trigger_type
                self.auto_recording_start_time = time.time()
            return True, filename

    def stop_recording(self) -> Tuple[bool, Optional[str]]:
        with self.record_lock:
            rec, audio_q = self.recorder, self._record_audio_q
            self.recorder, self._record_audio_q = None, None
            self.is_auto_recording = False
            self.auto_record_trigger_type = None
        if rec is None:
            return False, "Not currently recording"

        ok = rec.stop()
        if audio_q is not None:
            audio_manager.unsubscribe(audio_q)
        return (True, rec.filename) if ok else (False, rec.error)

    # ------------------------------------------------------------ snapshots

    def get_jpeg_frame(self, quality: int = 75) -> Optional[bytes]:
        frame = self.last_frame
        if frame is None or not self.is_active:
            return None

        annotated = frame.copy()
        timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if self.recorder is not None:
            if self.auto_record_trigger_type == "motion":
                rec_tag = "MOTION REC"
            elif self.auto_record_trigger_type == "noise":
                rec_tag = "NOISE REC"
            else:
                rec_tag = "REC"
            cv2.circle(annotated, (25, 30), 8, (0, 0, 255), -1)
            cv2.putText(annotated, f"{rec_tag}  {timestamp_str}", (42, 36),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 0, 255), 2, cv2.LINE_AA)
        else:
            tags = []
            if self.is_monitoring:
                tags.append("MOTION")
            if self.is_noise_monitoring:
                tags.append("NOISE")
            tag_str = f"[{'+'.join(tags)}] " if tags else ""
            cv2.putText(annotated, f"{tag_str}{timestamp_str}", (16, 36),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 255, 0), 2, cv2.LINE_AA)

        ok, buffer = cv2.imencode(".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        return buffer.tobytes() if ok else None

    def capture_snapshot(self, output_dir: str) -> Tuple[bool, str]:
        frame = self.last_frame
        if not self.is_active or frame is None:
            return False, "Camera is not active"
        os.makedirs(output_dir, exist_ok=True)
        filename = f"snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        cv2.imwrite(os.path.join(output_dir, filename), frame)
        return True, filename

    # ------------------------------------------------------ presence / auto-off

    def heartbeat(self):
        self.last_heartbeat = time.time()

    def add_viewer(self):
        with self._viewers_lock:
            self._viewers += 1
        self.heartbeat()

    def remove_viewer(self):
        with self._viewers_lock:
            self._viewers = max(0, self._viewers - 1)
        self.heartbeat()

    def start_watchdog(self):
        if self._watchdog_started:
            return
        self._watchdog_started = True

        def loop():
            while True:
                time.sleep(5)
                idle = time.time() - self.last_heartbeat
                # Do NOT auto-turn off if motion or noise monitoring is active or currently recording
                if (self.is_active and not self.is_monitoring and not self.is_noise_monitoring
                        and self.recorder is None and self._viewers == 0 and idle > self.idle_timeout):
                    print(f"[watchdog] Nobody watching for {int(idle)}s - turning camera off")
                    self.stop()
                    audio_manager.stop()

        threading.Thread(target=loop, daemon=True).start()

    @property
    def is_recording(self) -> bool:
        return self.recorder is not None

    def get_status(self) -> dict:
        rec = self.recorder
        duration = 0
        if rec:
            duration = int(time.monotonic() - rec.start_time)
        return {
            "is_active": self.is_active,
            "fps": self.fps if self.is_active else 0,
            "is_recording": rec is not None,
            "record_duration": duration,
            "is_monitoring": self.is_monitoring,
            "motion_detected": self.motion_detected,
            "is_noise_monitoring": self.is_noise_monitoring,
            "noise_detected": self.noise_detected,
            "noise_rms": int(audio_manager.current_rms),
            "is_auto_recording": self.is_auto_recording,
            "auto_record_trigger_type": self.auto_record_trigger_type,
            "is_motion_recording": self.is_auto_recording and self.auto_record_trigger_type == "motion",
            "is_noise_recording": self.is_auto_recording and self.auto_record_trigger_type == "noise"
        }


camera_manager = CameraManager()
