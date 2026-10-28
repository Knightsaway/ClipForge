"""Audio capture (WASAPI loopback for game/desktop sound, plus optional mic) streamed
into FFmpeg over localhost TCP as raw 48 kHz float PCM.

Each source gets its own track in the recording, so you can mute or lower your mic
later when editing. Silence gaps (WASAPI sends nothing when nothing plays) are filled
by the clock, so audio never drifts away from the video."""
from __future__ import annotations

import os
import socket
import threading
import time

import numpy as np

RATE = 48000
CHANNELS = 2


def list_microphones() -> list[str]:
    try:
        import soundcard as sc
        return [m.name for m in sc.all_microphones(include_loopback=False)]
    except Exception:
        return []


def _com_init():
    try:
        import ctypes
        ctypes.windll.ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED
    except Exception:
        pass


class AudioSender(threading.Thread):
    """kind = 'desktop' or 'mic'. Connects to ffmpeg's listening TCP port."""

    def __init__(self, kind: str, port: int, mic_name: str = "", on_error=None):
        super().__init__(name=f"audio-{kind}", daemon=True)
        self.kind = kind
        self.port = port
        self.mic_name = mic_name
        self.on_error = on_error
        self._halt = threading.Event()
        self.level = 0.0  # 0..1 peak, for the UI meter / auto-clip
        self.level_history: list[tuple[float, float]] = []  # (time, rms) ~10/s
        self.t0_wall: float | None = None   # wall-clock time of the first sample sent (sample k = t0 + k/48000)
        self.device_name = ""               # what was actually opened (for Diagnostics)
        self.heard = False                  # any real (non-zero) sound since start
        self.silent_device = False          # opened fine but only ever delivers digital silence

    def stop(self):
        self._halt.set()

    def _connect(self) -> socket.socket | None:
        deadline = time.monotonic() + 15
        while not self._halt.is_set() and time.monotonic() < deadline:
            try:
                s = socket.create_connection(("127.0.0.1", self.port), timeout=1)
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                s.settimeout(5)
                return s
            except OSError:
                time.sleep(0.02)
        return None

    def _open_device(self):
        if os.environ.get("CLIPFORGE_TEST_AUDIO"):   # tests: a fake sound card, optionally drifting
            return _FakeDevice(float(os.environ.get("CLIPFORGE_TEST_AUDIO", "1") or 1)), CHANNELS
        import soundcard as sc
        if self.kind == "desktop":
            spk = sc.default_speaker()
            dev = sc.get_microphone(id=str(spk.name), include_loopback=True)
            ch = CHANNELS
        else:
            dev = None
            if self.mic_name:
                try:
                    dev = sc.get_microphone(self.mic_name)
                except Exception as e:   # renamed / unplugged: fall back to the Windows default mic
                    if self.on_error:
                        self.on_error(f"Mic '{self.mic_name}' not found ({e}) — using the default microphone")
            dev = dev or sc.default_microphone()
            ch = 1
        return dev, ch

    def run(self):
        _com_init()
        sock = self._connect()
        if sock is None:
            return
        silence_mode = False
        try:
            try:
                dev, ch = self._open_device()
                rec_ctx = dev.recorder(samplerate=RATE, channels=ch, blocksize=480)
                rec = rec_ctx.__enter__()
            except Exception as e:  # no device -> keep the track alive with silence
                if self.on_error:
                    self.on_error(f"{'Microphone' if self.kind == 'mic' else 'Game sound'} can't be recorded: {e}")
                silence_mode, rec_ctx, rec, ch = True, None, None, CHANNELS
            else:
                self.device_name = str(getattr(dev, "name", "") or "")

            start = time.monotonic()
            self.t0_wall = time.time()
            sent = 0
            last_hist = 0.0
            acc_sq, acc_n = 0.0, 0
            while not self._halt.is_set():
                chunk = None
                if not silence_mode:
                    try:
                        data = rec.record(numframes=None)
                    except Exception:
                        data = None
                    if data is not None and len(data):
                        if data.ndim == 1:
                            data = data[:, None]
                        if data.shape[1] == 1:
                            data = np.repeat(data, 2, axis=1)
                        elif data.shape[1] > 2:
                            data = data[:, :2]
                        chunk = data.astype(np.float32, copy=False)
                        if not self.heard and np.any(chunk):
                            self.heard = True
                            self.silent_device = False

                now = time.monotonic()
                expected = int((now - start) * RATE)
                if chunk is not None:
                    # Keep the sample count locked to the real clock. Sound cards run a little fast or
                    # slow; a drifting timeline is what used to make capture stutter and sound choppy.
                    n = len(chunk)
                    target = expected - sent              # samples we "should" send now
                    if target - n > RATE // 5:            # device stalled >200 ms: fill the hole
                        chunk = np.concatenate([np.zeros((target - n - RATE // 100, CHANNELS), np.float32),
                                                chunk])
                    elif n - target > RATE // 5:          # big burst >200 ms ahead: drop the excess
                        chunk = chunk[: max(0, target + RATE // 50)]
                    elif n >= 32 and abs(target - n) > RATE // 100:   # real drift (>10 ms), not timer jitter:
                        err = target - n - (RATE // 100 if target > n else -RATE // 100)   # stretch/squeeze
                        new_n = int(round(n + max(-n * 0.04, min(n * 0.04, err * 0.1))))   # smoothly, max 4%
                        if new_n != n and new_n > 1:
                            x_old = np.linspace(0.0, 1.0, n)
                            x_new = np.linspace(0.0, 1.0, new_n)
                            chunk = np.stack([np.interp(x_new, x_old, chunk[:, c]) for c in range(CHANNELS)],
                                             axis=1).astype(np.float32)
                else:
                    behind = expected - sent
                    if behind > RATE // 25:  # >40 ms of nothing = silence
                        chunk = np.zeros((behind - RATE // 100, CHANNELS), np.float32)

                if chunk is not None and len(chunk):
                    sock.sendall(chunk.tobytes())
                    sent += len(chunk)
                    peak = float(np.max(np.abs(chunk))) if len(chunk) else 0.0
                    self.level = max(peak, self.level * 0.85)
                    acc_sq += float(np.sum(chunk.astype(np.float64) ** 2))
                    acc_n += chunk.size
                else:
                    time.sleep(0.005)

                if (self.kind == "mic" and not silence_mode and not self.heard and not self.silent_device
                        and now - start > 90):
                    # 90 s of perfect digital zeros: real mics always hiss a little, so something is blocking it
                    self.silent_device = True
                    if self.on_error:
                        self.on_error("Your microphone is sending complete silence. Usually Windows is blocking it: "
                                      "Settings → Privacy & security → Microphone → turn on 'Let desktop apps "
                                      "access your microphone'. Or pick another Mic device in ClipForge Settings.")

                if now - last_hist >= 0.1:
                    rms = (acc_sq / acc_n) ** 0.5 if acc_n else 0.0
                    self.level_history.append((time.time(), rms))
                    if len(self.level_history) > 6000:
                        del self.level_history[:1000]
                    acc_sq, acc_n, last_hist = 0.0, 0, now
        except (OSError, ConnectionError):
            pass  # ffmpeg went away; the engine watchdog restarts everything
        finally:
            try:
                if rec_ctx is not None:
                    rec_ctx.__exit__(None, None, None)
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass


def test_microphone(name: str = "", seconds: float = 4.0, on_level=None) -> tuple[float, str]:
    """Listens to a mic for a few seconds. Returns (loudest peak 0..1, device name or error)."""
    if os.environ.get("CLIPFORGE_TEST_AUDIO"):
        return 0.3, "Test microphone"
    try:
        import soundcard as sc
        _com_init()
        dev = None
        if name:
            try:
                dev = sc.get_microphone(name)
            except Exception:
                dev = None
        dev = dev or sc.default_microphone()
        peak = 0.0
        with dev.recorder(samplerate=RATE, channels=1, blocksize=480) as rec:
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                data = rec.record(numframes=RATE // 20)
                lvl = float(np.max(np.abs(data))) if len(data) else 0.0
                peak = max(peak, lvl)
                if on_level:
                    on_level(lvl)
        return peak, str(dev.name)
    except Exception as e:
        return -1.0, str(e)


class _FakeDevice:
    """Test stand-in for a sound card whose clock runs at `rate_factor` of real time, beeping every
    device-second (so alignment and drift correction can be checked without hardware)."""

    def __init__(self, rate_factor: float = 1.0):
        self.rate = rate_factor

    def recorder(self, samplerate=RATE, channels=CHANNELS, blocksize=480):
        dev = self

        class Rec:
            def __enter__(self):
                self.t0 = time.monotonic()
                self.made = 0
                return self

            def __exit__(self, *a):
                return False

            def record(self, numframes=None):
                time.sleep(0.01)
                due = int((time.monotonic() - self.t0) * RATE * dev.rate)
                n = due - self.made
                if n <= 0:
                    return np.zeros((0, CHANNELS), np.float32)
                k = np.arange(self.made, self.made + n)
                self.made = due
                beep = ((k % RATE) < RATE // 20).astype(np.float32) * 0.5 * np.sin(2 * np.pi * 1000 * k / RATE)
                return np.stack([beep, beep], axis=1).astype(np.float32)

        return Rec()
