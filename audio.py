import queue
import threading
from typing import Optional, Callable

import numpy as np
import sounddevice as sd


class AudioManager:
    """
    Captures audio from the Mac's default input device and fans out raw
    16-bit mono PCM chunks to any number of subscribers (live listeners,
    the recorder, ...).
    Also performs real-time audio energy (RMS) calculation for noise detection.
    """

    def __init__(self, channels: int = 1, blocksize: int = 1024):
        self.channels = channels
        self.blocksize = blocksize
        self.sample_rate = 48000
        self.is_active = False
        self.last_error: Optional[str] = None
        self.subscribers: list[queue.Queue] = []
        # RLock: subscribe()/unsubscribe() call start()/stop() while holding it.
        self.lock = threading.RLock()
        self._stop_event: Optional[threading.Event] = None

        # Noise level monitoring
        self.current_rms = 0.0
        self.on_chunk_callbacks: list[Callable[[float, bytes], None]] = []
        self.keep_alive_fn: Optional[Callable[[], bool]] = None

    def start(self) -> bool:
        with self.lock:
            if self.is_active:
                return True
            try:
                # Use whatever macOS has selected as the default input.
                dev = sd.query_devices(kind="input")
                self.sample_rate = int(dev["default_samplerate"])
            except Exception as e:  # noqa: BLE001
                self.last_error = f"No microphone available: {e}"
                return False

            self.last_error = None
            stop_event = threading.Event()  # one event per worker -> no restart races
            self._stop_event = stop_event
            self.is_active = True
            threading.Thread(
                target=self._worker, args=(stop_event, self.sample_rate), daemon=True
            ).start()
            return True

    def _worker(self, stop_event: threading.Event, rate: int):
        try:
            stream = sd.InputStream(
                samplerate=rate,
                channels=self.channels,
                dtype="int16",
                blocksize=self.blocksize,
            )
            stream.start()
        except Exception as e:  # noqa: BLE001
            self.last_error = f"Could not open microphone: {e}"
            print(f"[audio] {self.last_error}")
            with self.lock:
                if self._stop_event is stop_event:
                    self.is_active = False
            return

        try:
            while not stop_event.is_set():
                data, _overflowed = stream.read(self.blocksize)
                chunk = data.tobytes()

                # Calculate RMS sound level (amplitude in 16-bit PCM space, max 32767)
                samples = data.flatten().astype(np.float32)
                rms = float(np.sqrt(np.mean(samples**2))) if len(samples) > 0 else 0.0
                self.current_rms = rms

                with self.lock:
                    subs = list(self.subscribers)
                    callbacks = list(self.on_chunk_callbacks)

                for q in subs:
                    try:
                        q.put_nowait(chunk)
                    except queue.Full:
                        pass  # slow consumer: drop this chunk

                for cb in callbacks:
                    try:
                        cb(rms, chunk)
                    except Exception as e:
                        print(f"[audio callback error] {e}")

        except Exception as e:  # noqa: BLE001
            self.last_error = f"Microphone stream error: {e}"
            print(f"[audio] {self.last_error}")
        finally:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

    def stop(self):
        with self.lock:
            if not self.is_active:
                return
            self.is_active = False
            self.current_rms = 0.0
            if self._stop_event:
                self._stop_event.set()

    def subscribe(self, maxsize: int = 100) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=maxsize)
        with self.lock:
            self.subscribers.append(q)
            if not self.is_active:
                self.start()
        return q

    def set_keep_alive(self, fn: Callable[[], bool]):
        self.keep_alive_fn = fn

    def unsubscribe(self, q: queue.Queue):
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)
            keep_running = self.keep_alive_fn() if self.keep_alive_fn else False
            if not self.subscribers and not keep_running and self.is_active:
                self.stop()

    def register_callback(self, cb: Callable[[float, bytes], None]):
        with self.lock:
            if cb not in self.on_chunk_callbacks:
                self.on_chunk_callbacks.append(cb)

    def unregister_callback(self, cb: Callable[[float, bytes], None]):
        with self.lock:
            if cb in self.on_chunk_callbacks:
                self.on_chunk_callbacks.remove(cb)


audio_manager = AudioManager()
