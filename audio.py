import queue
import threading
from typing import Optional
import sounddevice as sd


class AudioManager:
    def __init__(self, channels: int = 1, blocksize: int = 1024):
        self.channels = channels
        self.blocksize = blocksize
        self.sample_rate = 48000
        self.is_active = False
        self.last_error: Optional[str] = None
        self.subscribers: list[queue.Queue] = []
        self.lock = threading.RLock()
        self._stop_event: Optional[threading.Event] = None

    def start(self) -> bool:
        with self.lock:
            if self.is_active:
                return True
            try:
                dev = sd.query_devices(kind="input")
                self.sample_rate = int(dev["default_samplerate"])
            except Exception as e:
                self.last_error = f"No microphone available: {e}"
                return False
            self.last_error = None
            stop_event = threading.Event()
            self._stop_event = stop_event
            self.is_active = True
            threading.Thread(target=self._worker, args=(stop_event, self.sample_rate), daemon=True).start()
            return True

    def _worker(self, stop_event: threading.Event, rate: int):
        try:
            stream = sd.InputStream(samplerate=rate, channels=self.channels, dtype="int16", blocksize=self.blocksize)
            stream.start()
        except Exception as e:
            self.last_error = f"Could not open microphone: {e}"
            with self.lock:
                if self._stop_event is stop_event:
                    self.is_active = False
            return
        try:
            while not stop_event.is_set():
                data, _ = stream.read(self.blocksize)
                chunk = data.tobytes()
                with self.lock:
                    subs = list(self.subscribers)
                for q in subs:
                    try:
                        q.put_nowait(chunk)
                    except queue.Full:
                        pass
        finally:
            stream.stop()
            stream.close()

    def stop(self):
        with self.lock:
            self.is_active = False
            if self._stop_event:
                self._stop_event.set()

    def subscribe(self, maxsize: int = 100) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=maxsize)
        with self.lock:
            self.subscribers.append(q)
            if not self.is_active:
                self.start()
        return q

    def unsubscribe(self, q: queue.Queue):
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)
            if not self.subscribers and self.is_active:
                self.stop()


audio_manager = AudioManager()
