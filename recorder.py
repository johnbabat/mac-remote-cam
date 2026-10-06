import os
import queue
import threading
import time
from fractions import Fraction
from typing import Optional

import av
import numpy as np


class Recorder:
    """
    Records camera frames + microphone audio into an iPhone-compatible MP4
    (H.264 video + AAC audio) using PyAV.

    - Video timestamps come from the wall clock, so playback speed is correct
      no matter what frame rate the camera actually delivers.
    - Encoding runs on its own thread so it never slows down the live stream.
    - The file is written as `<name>.mp4.part` and renamed when finished, so
      half-written files never show up in the gallery.
    """

    def __init__(self, final_path: str, width: int, height: int,
                 audio_q: Optional[queue.Queue] = None, sample_rate: int = 48000):
        self.final_path = final_path
        self.filename = os.path.basename(final_path)
        # libx264 requires even dimensions
        self.width = width - (width % 2)
        self.height = height - (height % 2)
        self.audio_q = audio_q
        self.sample_rate = sample_rate
        self.error: Optional[str] = None
        self.dropped_frames = 0
        self.start_time = 0.0

        self._video_q: queue.Queue = queue.Queue(maxsize=120)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.start_time = time.monotonic()
        self._thread.start()

    def add_video_frame(self, frame_bgr: np.ndarray, ts: float):
        try:
            self._video_q.put_nowait((frame_bgr, ts))
        except queue.Full:
            self.dropped_frames += 1

    def stop(self, timeout: float = 15.0) -> bool:
        self._stop.set()
        self._thread.join(timeout)
        return self.error is None

    # ------------------------------------------------------------------ #

    def _run(self):
        tmp_path = self.final_path + ".part"
        container = None
        try:
            container = av.open(tmp_path, mode="w", format="mp4",
                                options={"movflags": "+faststart"})

            vstream = container.add_stream("libx264", rate=30)
            vstream.width = self.width
            vstream.height = self.height
            vstream.pix_fmt = "yuv420p"
            vstream.time_base = Fraction(1, 1000)
            vstream.codec_context.time_base = Fraction(1, 1000)
            vstream.options = {"preset": "veryfast", "crf": "23"}

            astream = None
            if self.audio_q is not None:
                astream = container.add_stream("aac", rate=self.sample_rate)
                astream.layout = "mono"
                astream.bit_rate = 96000

            last_pts = -1
            audio_samples = 0

            def drain_audio():
                nonlocal audio_samples
                if astream is None:
                    return
                while True:
                    try:
                        chunk = self.audio_q.get_nowait()
                    except queue.Empty:
                        return
                    samples = np.frombuffer(chunk, dtype=np.int16).reshape(1, -1)
                    aframe = av.AudioFrame.from_ndarray(samples, format="s16", layout="mono")
                    aframe.sample_rate = self.sample_rate
                    aframe.pts = audio_samples
                    aframe.time_base = Fraction(1, self.sample_rate)
                    audio_samples += samples.shape[1]
                    for packet in astream.encode(aframe):
                        container.mux(packet)

            def encode_video(frame_bgr, ts):
                nonlocal last_pts
                pts = int((ts - self.start_time) * 1000)
                if pts <= last_pts:
                    pts = last_pts + 1
                last_pts = pts
                if frame_bgr.shape[1] != self.width or frame_bgr.shape[0] != self.height:
                    frame_bgr = frame_bgr[: self.height, : self.width]
                vframe = av.VideoFrame.from_ndarray(np.ascontiguousarray(frame_bgr), format="bgr24")
                vframe.pts = pts
                vframe.time_base = Fraction(1, 1000)
                for packet in vstream.encode(vframe):
                    container.mux(packet)

            while not self._stop.is_set() or not self._video_q.empty():
                drain_audio()
                try:
                    frame_bgr, ts = self._video_q.get(timeout=0.02)
                except queue.Empty:
                    continue
                encode_video(frame_bgr, ts)

            drain_audio()
            for packet in vstream.encode(None):
                container.mux(packet)
            if astream is not None:
                for packet in astream.encode(None):
                    container.mux(packet)

            container.close()
            container = None
            os.replace(tmp_path, self.final_path)
        except Exception as e:  # noqa: BLE001
            self.error = f"Recording failed: {e}"
            print(f"[recorder] {self.error}")
        finally:
            if container is not None:
                try:
                    container.close()
                except Exception:
                    pass
