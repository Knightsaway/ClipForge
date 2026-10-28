"""Global hotkeys through the Win32 RegisterHotKey API.

No keyboard hooks, no injected DLLs - the same mechanism Windows itself uses, so it
works in fullscreen games and doesn't look suspicious to anti-cheat."""
from __future__ import annotations

import ctypes
import threading
from typing import Callable

from .config import IS_WINDOWS

MODS = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

VK = {
    "pageup": 0x21, "pagedown": 0x22, "end": 0x23, "home": 0x24, "insert": 0x2D,
    "delete": 0x2E, "pause": 0x13, "scrolllock": 0x91, "printscreen": 0x2C,
    "space": 0x20, "tab": 0x09, "backspace": 0x08, "enter": 0x0D,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "num0": 0x60, "num1": 0x61, "num2": 0x62, "num3": 0x63, "num4": 0x64,
    "num5": 0x65, "num6": 0x66, "num7": 0x67, "num8": 0x68, "num9": 0x69,
    "num*": 0x6A, "num+": 0x6B, "num-": 0x6D, "num.": 0x6E, "num/": 0x6F,
}
for i in range(1, 25):
    VK[f"f{i}"] = 0x6F + i
for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
    VK[c.lower()] = ord(c)


def parse(combo: str) -> tuple[int, int] | None:
    parts = [p.strip().lower().replace(" ", "") for p in combo.split("+") if p.strip()]
    if not parts:
        return None
    mods = 0
    for p in parts[:-1]:
        if p not in MODS:
            return None
        mods |= MODS[p]
    key = VK.get(parts[-1])
    if key is None:
        return None
    return mods, key


class HotkeyManager:
    def __init__(self):
        self._thread: threading.Thread | None = None
        self._tid = 0
        self._bindings: dict[str, tuple[str, Callable[[], None]]] = {}
        self.failed: list[str] = []
        self._ready = threading.Event()

    def set_bindings(self, bindings: dict[str, tuple[str, Callable[[], None]]]):
        """bindings: name -> (combo string, callback). Restarts the listener."""
        self.stop()
        self._bindings = bindings
        if IS_WINDOWS:
            self._ready.clear()
            self._thread = threading.Thread(target=self._loop, name="hotkeys", daemon=True)
            self._thread.start()
            self._ready.wait(2)

    def _loop(self):
        from ctypes import wintypes as wt
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
        self._tid = kernel32.GetCurrentThreadId()
        ids: dict[int, Callable[[], None]] = {}
        self.failed = []
        for i, (name, (combo, cb)) in enumerate(self._bindings.items(), start=1):
            parsed = parse(combo)
            if not parsed:
                self.failed.append(f"{name}: can't understand '{combo}'")
                continue
            mods, vk = parsed
            if user32.RegisterHotKey(None, i, mods | MOD_NOREPEAT, vk):
                ids[i] = cb
            else:
                self.failed.append(f"{name}: '{combo}' is taken by another app")
        self._ready.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam in ids:
                try:
                    ids[msg.wParam]()
                except Exception as e:  # never let a callback kill the hotkey thread
                    print("hotkey callback error:", e)
        for i in ids:
            user32.UnregisterHotKey(None, i)

    def stop(self):
        if IS_WINDOWS and self._thread and self._thread.is_alive():
            ctypes.windll.user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
            self._thread.join(1)
        self._thread = None
