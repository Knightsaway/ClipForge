"""Which game is running? Uses the foreground window, so clips get a proper name
instead of 'Win64' (GG labels Unreal games by their folder name)."""
from __future__ import annotations

import ctypes
import re
from pathlib import Path

from .config import IS_WINDOWS

NOT_GAMES = {
    "explorer", "chrome", "msedge", "firefox", "opera", "brave", "discord", "steam",
    "steamwebhelper", "epicgameslauncher", "battle.net", "code", "python", "pythonw",
    "clipforge", "obs64", "spotify", "searchhost", "shellexperiencehost", "applicationframehost",
    "textinputhost", "lockapp", "taskmgr", "steelseriesgg", "nvidia app", "nvcontainer",
}

GAME_DIRS = ("steamapps\\common", "\\epic games\\", "\\riot games\\", "\\call of duty",
             "\\xboxgames\\", "\\games\\", "\\ea games\\", "\\ubisoft game launcher\\games",
             "\\gog galaxy\\games", "\\battlestate games\\")

GENERIC_EXE = re.compile(r"^(win64|win32|game|client|launcher|.*-win64-shipping|.*-shipping|"
                         r"cod|bootstrapper|unityplayer)$", re.I)


_SIGS_SET = False


def _set_sigs():
    global _SIGS_SET
    if _SIGS_SET:
        return
    from ctypes import wintypes as wt
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    u.GetForegroundWindow.restype = wt.HWND
    u.GetWindowTextLengthW.argtypes = [wt.HWND]
    u.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    u.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
    u.GetWindowRect.argtypes = [wt.HWND, ctypes.c_void_p]
    u.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
    u.MonitorFromWindow.restype = ctypes.c_void_p
    u.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    k.OpenProcess.restype = wt.HANDLE
    k.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    k.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
    k.CloseHandle.argtypes = [wt.HANDLE]
    _SIGS_SET = True


def _foreground():
    if not IS_WINDOWS:
        return None, "", ""
    _set_sigs()
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None, "", ""
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    title = buf.value
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = ""
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if h:
        size = ctypes.c_ulong(1024)
        pbuf = ctypes.create_unicode_buffer(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, pbuf, ctypes.byref(size)):
            exe = pbuf.value
        kernel32.CloseHandle(h)
    return hwnd, title, exe


def _is_fullscreenish(hwnd) -> bool:
    """True if the window covers its whole monitor (borderless or exclusive fullscreen)."""
    user32 = ctypes.windll.user32

    class RECT(ctypes.Structure):
        _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long), ("r", ctypes.c_long), ("b", ctypes.c_long)]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]

    wr = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(wr)):
        return False
    mon = user32.MonitorFromWindow(hwnd, 2)
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
        return False
    m = mi.rcMonitor
    return wr.l <= m.l and wr.t <= m.t and wr.r >= m.r and wr.b >= m.b


LAST_GAME_WINDOW: dict = {}   # {"hwnd": int, "exe": str, "name": str} of the last focused game


def current_game(aliases: dict | None = None) -> tuple[str | None, bool]:
    """Returns (nice game name or None, is_game_focused)."""
    hwnd, title, exe = _foreground()
    if not hwnd:
        return None, False
    stem = Path(exe).stem if exe else ""
    if stem.lower() in NOT_GAMES:
        return None, False
    aliases = aliases or {}
    if stem in aliases:
        LAST_GAME_WINDOW.update(hwnd=int(hwnd), exe=exe, name=aliases[stem])
        return aliases[stem], True
    fullscreen = _is_fullscreenish(hwnd)
    in_game_dir = any(d in exe.lower() for d in GAME_DIRS)
    if not (fullscreen or in_game_dir):
        return None, False
    name = title.strip() if title.strip() else stem
    if GENERIC_EXE.match(stem) and title.strip():
        name = title.strip()
    elif not title.strip() or len(title) > 60:
        name = stem
    # tidy up things like "Call of Duty® HQ" / "Enshrouded - v0.7.4"
    name = re.sub(r"\s+[-|–]\s+v?\d[\w.\- ]*$", "", name)
    name = name.replace("®", "").replace("™", "").strip()
    LAST_GAME_WINDOW.update(hwnd=int(hwnd), exe=exe, name=name or stem)
    return (name or stem or "Desktop"), True


def safe_folder_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip(" .")
    return name[:60] or "Desktop"


def window_alive(hwnd) -> bool:
    """True while the game's window still exists (also when it's behind other windows or minimized)."""
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        return bool(ctypes.windll.user32.IsWindow(ctypes.c_void_p(int(hwnd))))
    except Exception:
        return False
