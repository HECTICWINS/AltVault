"""AltVault - a Roblox account manager that runs on signed Python (no unsigned .exe),
so it works with Windows Smart App Control turned on.

Everything lives in %LOCALAPPDATA%\\AltVault. Account cookies are encrypted with Windows
DPAPI (only your Windows user on this PC can decrypt them) and are never written to the log.
"""
import base64
import ctypes
import ctypes.wintypes as wt
import hashlib
import http.client
import json
import logging
import math
import os
import random
import re
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
import zlib
from tkinter import filedialog, messagebox, simpledialog, ttk

APP_TITLE = "AltVault"
APP_VERSION = "2.5.0"
APP_DIR = os.path.join(os.environ["LOCALAPPDATA"], "AltVault")
DATA_FILE = os.path.join(APP_DIR, "accounts.dat")
SETTINGS_FILE = os.path.join(APP_DIR, "settings.json")
AVATAR_DIR = os.path.join(APP_DIR, "avatars")
LOG_FILE = os.path.join(APP_DIR, "altvault.log")
LEGACY_APP_DIR = os.path.join(os.environ["LOCALAPPDATA"], "PyRAM")   # AltVault's name before v2
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
ROBLOX_EXE = "robloxplayerbeta.exe"
WINDOW_PREFIX = "Roblox - "      # Roblox windows get renamed to "Roblox - <username>"
WINDOW_TIMEOUT = 60              # seconds to wait for a launched client's window
SERVER_TIMEOUT = 45              # seconds to wait for the first account to reach a server
PRESENCE_INTERVAL = 30_000       # ms between online/in-game status refreshes
TICK_INTERVAL = 1_500            # ms between Roblox window checks
MAX_RECENTS = 12
GROUP_PREFIX = "group:"            # tree row ids of group headers
# Anti-AFK: Roblox kicks after 20 idle minutes, and many games rejoin "idle" players into another
# server shortly before that. Every window's own last ping is tracked, and a window that reaches
# AFK_URGENT is pinged without waiting for the user to pause, so none can go past ~15 minutes.
AFK_INTERVAL = (360, 540)        # seconds between pings, randomised so it isn't a fixed rhythm
AFK_IDLE = 2                     # seconds of no mouse/keyboard use before stealing focus
AFK_MAX_WAIT = 300               # if the user never pauses that long, ping anyway
AFK_URGENT = 900                 # a window this long without a ping is pinged right away
AFK_MIN_GAP = 240                # a pass leaves out windows pinged more recently than this
AFK_RETRY = 30                   # seconds before retrying windows that couldn't be pinged
AFK_RETRY_SLOW = 60              # retry delay once a window has missed AFK_SLOW_AFTER times in a row
AFK_SLOW_AFTER = 5
AFK_SETTLE = 0.35                # after focusing: Roblox runs slowly while hidden, let it catch up
AFK_HOLD = 0.2                   # key hold; a short tap can fall between a slowed client's frames
AFK_STUCK = 90                   # seconds after which a still-running pass is treated as stuck
AFK_ALERT_AFTER = 4              # misses in a row (about 2 minutes) before a tray alert
AFK_KEYS = {"space": 0x20, "f13": 0x7C, **{c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz"}}

log = logging.getLogger("altvault")

# ---------------------------------------------------------------- Windows APIs

_crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_dwmapi = ctypes.WinDLL("dwmapi")


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

for _fn in (_crypt32.CryptProtectData, _crypt32.CryptUnprotectData):
    _fn.argtypes = [ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p,
                    ctypes.c_void_p, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(DATA_BLOB)]
    _fn.restype = wt.BOOL
_kernel32.LocalFree.argtypes = [ctypes.c_void_p]
_kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
_kernel32.CreateMutexW.restype = wt.HANDLE
_kernel32.CloseHandle.argtypes = [wt.HANDLE]
_kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
_kernel32.OpenProcess.restype = wt.HANDLE
_kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
_kernel32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
_user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
_user32.IsWindowVisible.argtypes = [wt.HWND]
_user32.IsIconic.argtypes = [wt.HWND]
_user32.GetWindowTextLengthW.argtypes = [wt.HWND]
_user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
_user32.SetWindowTextW.argtypes = [wt.HWND, wt.LPCWSTR]
_user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
_user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, wt.UINT]
_user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
_user32.SetForegroundWindow.argtypes = [wt.HWND]
_user32.SystemParametersInfoW.argtypes = [wt.UINT, wt.UINT, ctypes.c_void_p, wt.UINT]
_user32.GetParent.argtypes = [wt.HWND]
_user32.GetParent.restype = wt.HWND
_user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
_user32.FindWindowW.restype = wt.HWND
_user32.GetForegroundWindow.restype = wt.HWND
_user32.BringWindowToTop.argtypes = [wt.HWND]
_user32.SwitchToThisWindow.argtypes = [wt.HWND, wt.BOOL]
_user32.IsWindow.argtypes = [wt.HWND]
_user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
_user32.MapVirtualKeyW.argtypes = [wt.UINT, wt.UINT]
_user32.keybd_event.argtypes = [wt.BYTE, wt.BYTE, wt.DWORD, ctypes.c_size_t]
_user32.GetLastInputInfo.argtypes = [ctypes.c_void_p]
_user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
_user32.WindowFromPoint.argtypes = [wt.POINT]
_user32.WindowFromPoint.restype = wt.HWND
_user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
_user32.GetAncestor.restype = wt.HWND
_user32.GetWindow.argtypes = [wt.HWND, wt.UINT]
_user32.GetWindow.restype = wt.HWND
_user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
_user32.SendInput.argtypes = [wt.UINT, ctypes.c_void_p, ctypes.c_int]
_user32.IsHungAppWindow.argtypes = [wt.HWND]
_kernel32.GetTickCount.restype = wt.DWORD
_dwmapi.DwmSetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]

CRYPTPROTECT_UI_FORBIDDEN = 0x1
ERROR_ALREADY_EXISTS = 183
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SW_RESTORE = 9
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_NOSENDCHANGING = 0x0400  # skips the min-size check, so Roblox's 816x638 minimum doesn't apply
SPI_GETWORKAREA = 0x0030
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002


SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_ASYNCWINDOWPOS = 0x4000   # post the change to the window's own thread instead of waiting on it
HWND_TOP = 0
GA_ROOT = 2
GW_HWNDPREV = 3
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x0008
INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("_pad", ctypes.c_byte * 32)]   # sized for the largest member
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _U)]


def _dpapi(func, data, entropy=None):
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    extra = None
    if entropy:
        ebuf = ctypes.create_string_buffer(entropy, len(entropy))
        extra = ctypes.byref(DATA_BLOB(len(entropy), ctypes.cast(ebuf, ctypes.POINTER(ctypes.c_char))))
    blob_out = DATA_BLOB()
    if not func(ctypes.byref(blob_in), None, extra, None, None,
                CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        _kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


def protect(data):
    return _dpapi(_crypt32.CryptProtectData, data)


def unprotect(data):
    return _dpapi(_crypt32.CryptUnprotectData, data)


# Roblox only allows one client by creating a named singleton object at startup.
# If we own that name first, every new client starts normally instead of closing the old one.
_multi_handles = []


def enable_multi_roblox():
    """Returns True on success, False if Roblox is already running (close it first)."""
    if _multi_handles:
        return True
    for name in ("ROBLOX_singletonEvent", "ROBLOX_singletonMutex"):
        handle = _kernel32.CreateMutexW(None, True, name)
        err = ctypes.get_last_error()
        if not handle or err == ERROR_ALREADY_EXISTS:
            if handle:
                _kernel32.CloseHandle(handle)
            disable_multi_roblox()
            return False
        _multi_handles.append(handle)
    return True


def disable_multi_roblox():
    while _multi_handles:
        _kernel32.CloseHandle(_multi_handles.pop())


def close_all_roblox():
    subprocess.run(["taskkill", "/F", "/IM", "RobloxPlayerBeta.exe"],
                   capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)


def close_roblox_and_enable_multi():
    """Force-closes every Roblox player window, then grabs the singleton. Returns success."""
    close_all_roblox()
    for _ in range(20):  # the old process can take a moment to release its handles
        if enable_multi_roblox():
            return True
        time.sleep(0.25)
    return False


def _process_name(pid):
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(520)
        size = wt.DWORD(len(buf))
        if _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        _kernel32.CloseHandle(handle)


# Process names of the windows seen on the last scan, keyed by (hwnd, pid) so a reused pid or
# window handle can't inherit a stale name. Saves opening every app's process every tick.
_name_cache = {}


def roblox_windows():
    """Visible, titled top-level windows of Roblox player processes: [(hwnd, pid, title)]."""
    global _name_cache
    found, seen = [], {}

    def callback(hwnd, _lparam):
        if _user32.IsWindowVisible(hwnd):
            length = _user32.GetWindowTextLengthW(hwnd)
            if length:
                pid = wt.DWORD()
                _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                key = (hwnd, pid.value)
                name = _name_cache.get(key)
                if name is None:
                    name = _process_name(pid.value)
                seen[key] = name
                if name == ROBLOX_EXE:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    _user32.GetWindowTextW(hwnd, buf, length + 1)
                    found.append((hwnd, pid.value, buf.value))
        return True

    _user32.EnumWindows(WNDENUMPROC(callback), 0)
    _name_cache = seen
    return found


def kill_process(pid):
    handle = _kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if handle:
        _kernel32.TerminateProcess(handle, 0)
        _kernel32.CloseHandle(handle)


def focus_window(hwnd):
    if _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, SW_RESTORE)
    _user32.SetForegroundWindow(hwnd)


def idle_seconds():
    """How long since the user last touched the mouse or keyboard."""
    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO))
    _user32.GetLastInputInfo(ctypes.byref(info))
    return ((_kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000


def _nudge_input():
    """A zero-distance mouse move: makes this process the source of the latest input, which lifts
    Windows' foreground lock, without the side effects of a key press (an Alt tap opens menus)."""
    event = INPUT(type=INPUT_MOUSE)
    event.mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_MOVE, 0, 0)
    _user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT))


def responsive(hwnd):
    """False if the window's program is 'Not responding'. Several window calls wait for the owning
    program to answer, so touching a frozen window (a Roblox client mid-teleport, say) could stall
    Anti-AFK forever."""
    return bool(hwnd) and _user32.IsWindow(hwnd) and not _user32.IsHungAppWindow(hwnd)


def force_foreground(hwnd):
    """Brings a window to the front even while another app has focus. Returns success.

    Windows' foreground lock blocks background apps from switching focus. Sharing input state with
    the foreground thread plus a harmless synthetic input gets around it; an Alt tap and
    SwitchToThisWindow are later fallbacks. Focus changes are asynchronous, so check and retry.
    Frozen windows are never touched, and raising uses the non-blocking (async) form."""
    if not responsive(hwnd):
        return False
    me = _kernel32.GetCurrentThreadId()
    for attempt in range(6):
        fg = _user32.GetForegroundWindow()
        if fg == hwnd:
            return True
        fg_thread = _user32.GetWindowThreadProcessId(fg, None) if fg and responsive(fg) else 0
        attached = bool(fg_thread) and fg_thread != me and _user32.AttachThreadInput(me, fg_thread, True)
        if attempt < 3:
            _nudge_input()
        else:
            _user32.keybd_event(VK_MENU, 0, 0, 0)
            _user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        _user32.SetWindowPos(hwnd, HWND_TOP, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_ASYNCWINDOWPOS)
        _user32.SetForegroundWindow(hwnd)
        if attached:
            _user32.AttachThreadInput(me, fg_thread, False)
        if attempt >= 2:
            _user32.SwitchToThisWindow(hwnd, True)
        time.sleep(0.08)
    return _user32.GetForegroundWindow() == hwnd


_user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
_user32.OpenInputDesktop.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
_user32.OpenInputDesktop.restype = wt.HANDLE
_user32.CloseDesktop.argtypes = [wt.HANDLE]
DESKTOP_SWITCHDESKTOP = 0x0100

# Things that commonly sit in front and block Anti-AFK, named in plain words for the log and alert.
KNOWN_BLOCKERS = {"credential dialog xaml host": "a Windows permission (UAC) prompt",
                  "lockapp.exe": "the lock screen", "logonui.exe": "the sign-in screen"}


def describe_foreground():
    """What's in front, for the log and the tray alert: the program's file name and window class.
    Window titles are left out because they can hold private text (a browser tab, an email)."""
    desktop = _user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
    if not desktop:   # the input desktop isn't ours: locked, signing in, or a secure UAC prompt
        return "the lock screen or a Windows security prompt"
    _user32.CloseDesktop(desktop)
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return "no window (nothing has focus)"
    pid = wt.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    name = _process_name(pid.value)
    cls = ctypes.create_unicode_buffer(128)
    _user32.GetClassNameW(hwnd, cls, len(cls))
    for key, words in KNOWN_BLOCKERS.items():
        if key in (name, cls.value.lower()):
            return f"{words} ({name or cls.value})"
    if not name:
        return f"a program running as administrator (window class {cls.value})"
    return f"{name} (window class {cls.value})"


def tap_key(vk, hold=AFK_HOLD):
    scan = _user32.MapVirtualKeyW(vk, 0)
    _user32.keybd_event(vk, scan, 0, 0)
    time.sleep(hold)
    _user32.keybd_event(vk, scan, KEYEVENTF_KEYUP, 0)


def fully_visible(hwnd):
    """True if nothing covers the window (checked at its centre and four inner corners)."""
    r = wt.RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return False
    inset = 12
    points = [((r.left + r.right) // 2, (r.top + r.bottom) // 2), (r.left + inset, r.top + inset),
              (r.right - inset, r.top + inset), (r.left + inset, r.bottom - inset), (r.right - inset, r.bottom - inset)]
    return all(_user32.GetAncestor(_user32.WindowFromPoint(wt.POINT(x, y)), GA_ROOT) == hwnd for x, y in points)


def _offscreen_origin():
    gm = _user32.GetSystemMetrics
    return gm(SM_XVIRTUALSCREEN) + gm(SM_CXVIRTUALSCREEN) + 200, gm(SM_YVIRTUALSCREEN)


def _move_async(hwnd, x, y, insert_after=None, flags=0):
    """Moves a window without waiting on its program (so a freeze can't stall us), then waits up to
    ~0.4 s for the move to land. Returns True once it has."""
    _user32.SetWindowPos(hwnd, insert_after, x, y, 0, 0,
                         SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOSENDCHANGING | SWP_ASYNCWINDOWPOS | flags)
    r = wt.RECT()
    for _ in range(20):
        if _user32.GetWindowRect(hwnd, ctypes.byref(r)) and (r.left, r.top) == (x, y):
            return True
        time.sleep(0.02)
    return False


# What the current Anti-AFK pass is doing, so a stuck pass can be reported precisely.
afk_step = {"step": "idle", "since": 0.0}


def _step(name):
    afk_step["step"], afk_step["since"] = name, time.monotonic()


def anti_afk_pass(hwnds, vk):
    """Roblox ignores input sent to background windows, so briefly focus each window, tap the key,
    then give focus back. The key is only sent once Roblox is confirmed in front, so it can never
    land in another app.

    Activating a window brings it to the front, which would flash it over whatever the user is
    doing. So any Roblox window that's covered is first moved off-screen, pinged there, and put
    back in its old place and stacking order afterwards; it is never seen. A window that's fully
    visible already is pinged where it is, since raising it changes nothing on screen.
    Returns (pinged, minimised, missed) lists of hwnds, {hwnd: why it was missed}, and what was in
    front when a window refused focus (None if none did)."""
    _step("starting")
    prev = _user32.GetForegroundWindow()
    pinged, minimised, missed, moved = [], [], [], []
    reasons = {}
    blocker = None
    off_x, off_y = _offscreen_origin()
    stacking = []   # every top-level window, front to back, as it was before the pass
    _user32.EnumWindows(WNDENUMPROC(lambda h, _l: stacking.append(h) or True), 0)
    depth = {h: i for i, h in enumerate(stacking)}
    try:
        for hwnd in hwnds:
            if not _user32.IsWindow(hwnd):
                continue
            if _user32.IsIconic(hwnd):   # restoring it would disturb the user, and it can't take input
                minimised.append(hwnd)
                continue
            if not responsive(hwnd):     # frozen (e.g. mid-teleport): retry it shortly instead
                missed.append(hwnd)
                reasons[hwnd] = "not responding"
                continue
            if hwnd != prev and not fully_visible(hwnd):
                _step("moving a covered window off-screen")
                r = wt.RECT()
                _user32.GetWindowRect(hwnd, ctypes.byref(r))
                above = _user32.GetWindow(hwnd, GW_HWNDPREV)
                moved.append((hwnd, r.left, r.top, above))
                if not _move_async(hwnd, off_x, off_y, flags=SWP_NOZORDER):
                    missed.append(hwnd)   # didn't move in time; focusing it now would flash it
                    reasons[hwnd] = "didn't move off-screen in time"
                    continue
            _step("focusing a Roblox window")
            if not force_foreground(hwnd):
                reasons[hwnd] = "wouldn't take focus"
                if blocker is None:
                    blocker = describe_foreground()
            else:
                time.sleep(AFK_SETTLE)
                if _user32.GetForegroundWindow() == hwnd:
                    _step("pressing the key")
                    tap_key(vk)
                    pinged.append(hwnd)
                    continue
                reasons[hwnd] = "lost focus before the key press"
            missed.append(hwnd)
    finally:   # whatever happens above, never leave the user without focus or a window off-screen
        _step("restoring focus")
        if prev and _user32.GetForegroundWindow() != prev and responsive(prev):
            force_foreground(prev)
        # Put moved windows back, top-most first, each directly below the window that was above
        # it, so the user's stacking order is restored and nothing pops over their current app.
        _step("putting windows back")
        for hwnd, x, y, above in sorted(moved, key=lambda m: depth.get(m[0], 0)):
            flags = 0
            if not above or not _user32.IsWindow(above) or _user32.GetWindowLongW(above, GWL_EXSTYLE) & WS_EX_TOPMOST:
                flags = SWP_NOZORDER
            _move_async(hwnd, x, y, above or None, flags)
        _step("idle")
    return pinged, minimised, missed, reasons, blocker


def tile_windows(hwnds):
    """Arranges the windows in an even grid over the main screen's work area."""
    if not hwnds:
        return
    area = wt.RECT()
    _user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(area), 0)
    cols = math.ceil(math.sqrt(len(hwnds)))
    rows = math.ceil(len(hwnds) / cols)
    width = (area.right - area.left) // cols
    height = (area.bottom - area.top) // rows
    for i, hwnd in enumerate(hwnds):
        if _user32.IsIconic(hwnd):
            _user32.ShowWindow(hwnd, SW_RESTORE)
        _user32.SetWindowPos(hwnd, None, area.left + (i % cols) * width,
                             area.top + (i // cols) * height, width, height,
                             SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOSENDCHANGING)


SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79
DEFAULT_GEOMETRY = "820x560"


def safe_geometry(saved):
    """The saved window geometry, unless it's malformed or its title bar would be off every
    monitor (e.g. the monitor it was on has been unplugged)."""
    m = re.fullmatch(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", str(saved or ""))   # Tk writes "WxH+X+Y"
    if not m:
        return DEFAULT_GEOMETRY
    w, h, x, y = map(int, m.groups())
    gm = _user32.GetSystemMetrics
    left, top = gm(SM_XVIRTUALSCREEN), gm(SM_YVIRTUALSCREEN)
    right, bottom = left + gm(SM_CXVIRTUALSCREEN), top + gm(SM_CYVIRTUALSCREEN)
    if w < 200 or h < 150 or x + w < left + 100 or x > right - 100 or y < top or y > bottom - 50:
        return f"{w}x{h}" if 200 <= w and 150 <= h else DEFAULT_GEOMETRY
    return saved


# ---------------------------------------------------------------- app icon
# Drawn in code (a vault door on an indigo tile) so the app stays one readable file. Shapes are
# signed-distance fields, which give smooth edges at every size without supersampling.
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
ICON_VERSION = 1


def _png(size, rgba):
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    stride = size * 4
    raw = b"".join(b"\0" + rgba[y * stride:(y + 1) * stride] for y in range(size))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _sd_trapezoid(x, y, r1, r2, he):
    """Signed distance to a trapezoid centred on the origin: half-width r1 at the top (y=-he),
    r2 at the bottom (y=+he). After Inigo Quilez's 2D distance functions."""
    k2x, k2y = r2 - r1, 2 * he
    x = abs(x)
    cax, cay = x - min(x, r1 if y < 0 else r2), abs(y) - he
    t = min(max(((r2 - x) * k2x + (he - y) * k2y) / (k2x * k2x + k2y * k2y), 0), 1)
    cbx, cby = x - r2 + k2x * t, y - he + k2y * t
    s = -1 if cbx < 0 and cay < 0 else 1
    return s * math.sqrt(min(cax * cax + cay * cay, cbx * cbx + cby * cby))


def _render_icon(size):
    small = size <= 24   # thicker strokes and a bigger keyhole so it still reads at 16px
    top, bottom = (0x6F, 0x7B, 0xFF), (0x3C, 0x45, 0xB8)
    px = 1 / size        # one pixel in unit coordinates, for anti-aliasing
    ring_r, ring_w = (0.31, 0.085) if small else (0.31, 0.055)
    k = 1.25 if small else 1.0   # keyhole scale
    out = bytearray()
    for j in range(size):
        y = (j + 0.5) / size
        t = y
        bg = [round(top[k] + (bottom[k] - top[k]) * t) for k in range(3)]
        for i in range(size):
            x = (i + 0.5) / size
            # rounded square tile
            qx, qy = abs(x - 0.5) - 0.44 + 0.2, abs(y - 0.5) - 0.44 + 0.2
            tile = math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - 0.2
            a_tile = min(max(0.5 - tile / px, 0), 1)
            if a_tile == 0:
                out += b"\0\0\0\0"
                continue
            dx, dy = x - 0.5, y - 0.5
            ring = abs(math.hypot(dx, dy) - ring_r) - ring_w / 2               # the vault door's rim
            head = math.hypot(dx, dy + 0.045 * k) - 0.075 * k                  # keyhole: round top...
            stem = _sd_trapezoid(dx, dy - 0.06 * k, 0.03 * k, 0.058 * k, 0.085 * k)   # ...flared slot
            d = min(ring, head, stem)
            a_white = min(max(0.5 - d / px, 0), 1)
            rgb = [round(c + (255 - c) * a_white) for c in bg]
            out += bytes((*rgb, round(255 * a_tile)))
    return bytes(out)


def write_icon(path):
    """Writes a multi-size .ico (PNG entries, supported since Windows Vista)."""
    images = [_png(s, _render_icon(s)) for s in ICON_SIZES]
    offset = 6 + 16 * len(images)
    header = struct.pack("<HHH", 0, 1, len(images))
    for s, img in zip(ICON_SIZES, images):
        header += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(img), offset)
        offset += len(img)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(header + b"".join(images))
    os.replace(tmp, path)


def app_icon_path():
    """The cached .ico, drawn on first run (and again if the design changes)."""
    path = os.path.join(APP_DIR, f"altvault-{ICON_VERSION}.ico")
    if not os.path.exists(path):
        os.makedirs(APP_DIR, exist_ok=True)
        write_icon(path)
    return path


# ---------------------------------------------------------------- system tray
# Closing the window hides AltVault to a tray icon so Anti-AFK keeps running. Tk has no tray
# support, so this is the Win32 notification-icon API with its own message-only window and
# message loop on a background thread; anything that touches Tk is handed back via root.after().
TRAY_CLASS = "AltVaultTray"
WM_TRAY = 0x8001        # WM_APP + 1: tray icon mouse events
WM_TRAY_SHOW = 0x8002   # WM_APP + 2: a second launch asking this AltVault to show itself
HWND_MESSAGE = -3
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
NIIF_WARNING = 0x2
WM_DESTROY, WM_CLOSE, WM_LBUTTONUP, WM_RBUTTONUP = 0x0002, 0x0010, 0x0202, 0x0205
TPM_RETURNCMD, TPM_RIGHTBUTTON = 0x0100, 0x0002
MF_STRING, MF_SEPARATOR, MF_CHECKED = 0x0, 0x800, 0x8
IMAGE_ICON, LR_LOADFROMFILE = 1, 0x10
SM_CXSMICON, SM_CYSMICON = 49, 50
ASFW_ANY = -1
TRAY_OPEN, TRAY_AFK, TRAY_QUIT = 1, 2, 3

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR), ("hIconSm", wt.HICON)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT), ("uFlags", wt.UINT),
                ("uCallbackMessage", wt.UINT), ("hIcon", wt.HICON), ("szTip", wt.WCHAR * 128),
                ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD), ("szInfo", wt.WCHAR * 256),
                ("uVersion", wt.UINT), ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
                ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wt.HICON)]


_shell32 = ctypes.WinDLL("shell32", use_last_error=True)
_shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
_user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
_user32.DefWindowProcW.restype = ctypes.c_ssize_t
_user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
_user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
_user32.CreateWindowExW.restype = wt.HWND
_user32.FindWindowExW.argtypes = [wt.HWND, wt.HWND, wt.LPCWSTR, wt.LPCWSTR]
_user32.FindWindowExW.restype = wt.HWND
_user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
_user32.SendMessageTimeoutW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM, wt.UINT, wt.UINT,
                                        ctypes.POINTER(ctypes.c_size_t)]
SMTO_ABORTIFHUNG = 0x0002
_user32.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int, ctypes.c_int, wt.UINT]
_user32.LoadImageW.restype = wt.HANDLE
_user32.DestroyIcon.argtypes = [wt.HICON]
_user32.CreatePopupMenu.restype = wt.HMENU
_user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
_user32.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.LPVOID]
_user32.DestroyMenu.argtypes = [wt.HMENU]
_user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
_user32.DestroyWindow.argtypes = [wt.HWND]
_user32.RegisterWindowMessageW.argtypes = [wt.LPCWSTR]
_user32.AllowSetForegroundWindow.argtypes = [wt.DWORD]
_user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
_user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
_user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
_kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
_kernel32.GetModuleHandleW.restype = wt.HMODULE


def find_tray_window():
    """The tray window of an AltVault already running in this session, if any."""
    return _user32.FindWindowExW(wt.HWND(HWND_MESSAGE), None, TRAY_CLASS, None)


class TrayIcon:
    """The notification-area icon. on_open / on_toggle_afk / on_quit are called on the Tk thread;
    afk_on() is a plain function (safe from this thread) used for the menu's check mark."""

    def __init__(self, root, icon_path, on_open, on_toggle_afk, on_quit, afk_on):
        self.root, self.icon_path = root, icon_path
        self.on_open, self.on_toggle_afk, self.on_quit, self.afk_on = on_open, on_toggle_afk, on_quit, afk_on
        self.hwnd = None
        self.ready = threading.Event()
        self._proc = WNDPROC(self._wndproc)   # keep a reference, or ctypes frees the callback
        threading.Thread(target=self._run, daemon=True).start()
        self.ready.wait(5)

    def _tk(self, fn):
        try:
            self.root.after(0, fn)
        except (RuntimeError, tk.TclError):
            pass

    def _data(self, flags=0):
        nid = NOTIFYICONDATAW()
        nid.cbSize, nid.hWnd, nid.uID, nid.uFlags = ctypes.sizeof(NOTIFYICONDATAW), self.hwnd, 1, flags
        return nid

    def _add(self):
        nid = self._data(NIF_MESSAGE | NIF_ICON | NIF_TIP)
        nid.uCallbackMessage, nid.hIcon, nid.szTip = WM_TRAY, self.hicon, APP_TITLE
        _shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))

    def _run(self):
        hinst = _kernel32.GetModuleHandleW(None)
        # Set before CreateWindowExW, which already sends the window its first messages.
        self.taskbar_created = _user32.RegisterWindowMessageW("TaskbarCreated")
        wc = WNDCLASSEXW(cbSize=ctypes.sizeof(WNDCLASSEXW), lpfnWndProc=self._proc, hInstance=hinst,
                         lpszClassName=TRAY_CLASS)
        _user32.RegisterClassExW(ctypes.byref(wc))
        self.hwnd = _user32.CreateWindowExW(0, TRAY_CLASS, APP_TITLE, 0, 0, 0, 0, 0,
                                            wt.HWND(HWND_MESSAGE), None, hinst, None)
        size = _user32.GetSystemMetrics(SM_CXSMICON), _user32.GetSystemMetrics(SM_CYSMICON)
        self.hicon = _user32.LoadImageW(None, self.icon_path, IMAGE_ICON, *size, LR_LOADFROMFILE)
        self._add()
        self.ready.set()
        msg = wt.MSG()
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAY:
            event = lparam & 0xFFFF
            if event == WM_LBUTTONUP:
                self._tk(self.on_open)
            elif event == WM_RBUTTONUP:
                self._menu(hwnd)
            return 0
        if msg == WM_TRAY_SHOW:
            self._tk(self.on_open)
            return 0
        if msg == self.taskbar_created:   # Explorer restarted: the icon has to be added again
            self._add()
            return 0
        if msg == WM_CLOSE:
            _shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._data()))
            _user32.DestroyIcon(self.hicon)
            _user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            self.hwnd = None
            _user32.PostQuitMessage(0)
            return 0
        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _menu(self, hwnd):
        menu = _user32.CreatePopupMenu()
        _user32.AppendMenuW(menu, MF_STRING, TRAY_OPEN, "Open AltVault")
        _user32.AppendMenuW(menu, MF_STRING | (MF_CHECKED if self.afk_on() else 0), TRAY_AFK, "Anti-AFK")
        _user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        _user32.AppendMenuW(menu, MF_STRING, TRAY_QUIT, "Quit AltVault")
        pt = wt.POINT()
        _user32.GetCursorPos(ctypes.byref(pt))
        _user32.SetForegroundWindow(hwnd)   # without this the menu doesn't close when clicking elsewhere
        choice = _user32.TrackPopupMenu(menu, TPM_RETURNCMD | TPM_RIGHTBUTTON, pt.x, pt.y, 0, hwnd, None)
        _user32.DestroyMenu(menu)
        action = {TRAY_OPEN: self.on_open, TRAY_AFK: self.on_toggle_afk, TRAY_QUIT: self.on_quit}.get(choice)
        if action:
            self._tk(action)

    def notify(self, title, text, warning=False):
        nid = self._data(NIF_INFO)
        nid.szInfoTitle, nid.szInfo = title[:63], text[:255]
        nid.dwInfoFlags = NIIF_WARNING if warning else 0
        _shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def remove(self):
        """Takes the icon out of the tray and ends the tray thread. Waits (briefly) for it to happen,
        or the process could exit first and leave a ghost icon behind."""
        hwnd = self.hwnd
        if hwnd:
            result = ctypes.c_size_t()
            _user32.SendMessageTimeoutW(hwnd, WM_CLOSE, 0, 0, SMTO_ABORTIFHUNG, 2000, ctypes.byref(result))


def dark_title_bar(win):
    win.update_idletasks()
    value = ctypes.c_int(1)
    try:
        _dwmapi.DwmSetWindowAttribute(_user32.GetParent(win.winfo_id()), DWMWA_USE_IMMERSIVE_DARK_MODE,
                                      ctypes.byref(value), ctypes.sizeof(value))
    except OSError:
        pass


def acquire_instance_lock():
    """Returns a handle while this is the only AltVault, else focuses the open one and returns None."""
    handle = _kernel32.CreateMutexW(None, False, "Local\\AltVault_instance")
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        tray = find_tray_window()
        if tray:   # ask the running AltVault to show itself; it may be hidden in the tray
            _user32.AllowSetForegroundWindow(ASFW_ANY)
            _user32.PostMessageW(tray, WM_TRAY_SHOW, 0, 0)
            return None
        # Match Tk's window class too, or an Explorer window open on a folder called "AltVault" would win.
        hwnd = _user32.FindWindowW("TkTopLevel", APP_TITLE)
        if hwnd:
            focus_window(hwnd)
        return None
    return handle


# ---------------------------------------------------------------- per-account client settings
# Roblox's own player settings (GlobalBasicSettings_<n>.xml) are read once, when a client starts.
# Writing an account's values (FPS cap, lowest graphics, mute) just before launching it, then
# putting the user's own values back, gives every account its own settings without touching
# running clients. Nothing is injected into Roblox; these are the same settings its menu changes.
FPS_PRESETS = (15, 20, 30, 45, 60)
FPS_RANGE = (5, 240)
# Settings AltVault may change, by name, with their XML type in Roblox's file.
CLIENT_SETTINGS = {"FramerateCap": b"int", "SavedQualityLevel": b"token",
                   "GraphicsQualityLevel": b"int", "MasterVolume": b"float"}
LOW_GRAPHICS = {"SavedQualityLevel": "1", "GraphicsQualityLevel": "1"}   # Roblox's lowest level
MUTED = {"MasterVolume": "0"}


def _setting_tag(name):
    kind = CLIENT_SETTINGS[name]
    return re.compile(rb"(<" + kind + rb' name="' + name.encode() + rb'">)(-?[0-9.]+)(</' + kind + rb">)")



def read_client_settings(path):
    """{name: value as text} for each of CLIENT_SETTINGS found in Roblox's file; {} if unreadable."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return {}
    found = {}
    for name in CLIENT_SETTINGS:
        m = _setting_tag(name).search(data)
        if m:
            found[name] = m.group(2).decode()
    return found


def write_client_settings(path, values):
    """Changes only the given settings' numbers; every other byte of Roblox's file stays as it was."""
    with open(path, "rb") as f:
        data = f.read()
    new = data
    for name, value in values.items():
        if not re.fullmatch(r"-?[0-9]+(\.[0-9]+)?", str(value)):
            raise ValueError(f"not a number: {value!r}")
        new, found = _setting_tag(name).subn(lambda m: m.group(1) + str(value).encode() + m.group(3), new, count=1)
        if not found:
            raise OSError(f"Roblox's settings file has no {name} setting")
    if new != data:
        tmp = path + ".altvault.tmp"
        with open(tmp, "wb") as f:
            f.write(new)
        os.replace(tmp, path)


def roblox_settings_file():
    """Roblox's player settings file (the newest GlobalBasicSettings_<n>.xml), or None."""
    folder = os.path.join(os.environ["LOCALAPPDATA"], "Roblox")
    try:
        names = [n for n in os.listdir(folder) if re.fullmatch(r"GlobalBasicSettings_\d+\.xml", n)]
    except OSError:
        return None
    if not names:
        return None
    return os.path.join(folder, max(names, key=lambda n: int(re.search(r"\d+", n).group())))


def read_fps_cap(path):
    value = read_client_settings(path).get("FramerateCap")
    return int(value) if value is not None else None


def write_fps_cap(path, value):
    write_client_settings(path, {"FramerateCap": int(value)})


# ---------------------------------------------------------------- import from Roblox Account Manager
# RAM (github.com/ic3w0lf22/Roblox-Account-Manager) saves AccountData.json either as plain JSON or
# encrypted with Windows DPAPI plus this fixed entropy from its source code. Its optional password
# encryption can't be read without the password, so that case is reported instead.
RAM_ENTROPY = b"ROBLOX ACCOUNT MANAGER | :) | BROUGHT TO YOU BUY ic3w0lf"
RAM_PASSWORD_HEADER = b"Roblox Account Manager created by ic3w0lf22 @ github.com ......."


def read_ram_accounts(path):
    """[{'cookie', 'name', 'group', 'note'}] from RAM's AccountData.json. Passwords RAM may have
    saved are never read."""
    with open(path, "rb") as f:
        data = f.read()
    if data.startswith(RAM_PASSWORD_HEADER):
        raise RobloxError("This RAM file is protected with a password. In RAM, turn off password "
                          "encryption, let it save, then import again.")
    text = data.decode("utf-8-sig", "replace").lstrip()
    if not text.startswith(("[", "{")):   # anything that isn't plain JSON is RAM's encrypted format
        try:
            text = _dpapi(_crypt32.CryptUnprotectData, data, RAM_ENTROPY).decode("utf-8-sig")
        except OSError:
            raise RobloxError("Couldn't decrypt this RAM file. It can only be read on the PC and Windows "
                              "account that RAM saved it on.") from None
    try:
        items = json.loads(text)
    except ValueError:
        raise RobloxError("That file isn't a RAM AccountData.json.") from None
    if not isinstance(items, list):
        raise RobloxError("That file isn't a RAM AccountData.json.")
    found = []
    for item in items:
        if not isinstance(item, dict) or not COOKIE_RE.fullmatch(str(item.get("SecurityToken") or "")):
            continue
        group = str(item.get("Group") or "").strip()
        notes = [str(item.get(k) or "").strip() for k in ("Alias", "Description")]
        found.append({"cookie": item["SecurityToken"], "name": str(item.get("Username") or ""),
                      "group": None if group in ("", "Default") else group[:40],
                      "note": " · ".join(n for n in notes if n)[:200]})
    return found


# ---------------------------------------------------------------- storage

def migrate_legacy_data():
    """Moves data from the old PyRAM folder the first time AltVault runs. DPAPI encryption is tied
    to the Windows user, not the path, so the accounts file keeps working after the move."""
    if os.path.exists(APP_DIR) or not os.path.isdir(LEGACY_APP_DIR):
        return
    os.replace(LEGACY_APP_DIR, APP_DIR)   # same drive, so a single atomic rename
    old_log = os.path.join(APP_DIR, "pyram.log")
    if os.path.exists(old_log):
        os.replace(old_log, LOG_FILE)


def load_accounts():
    if not os.path.exists(DATA_FILE):
        return []
    with open(DATA_FILE, "rb") as f:
        return json.loads(unprotect(f.read()).decode("utf-8"))


def save_accounts(accounts):
    os.makedirs(APP_DIR, exist_ok=True)
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "wb") as f:
        f.write(protect(json.dumps(accounts).encode("utf-8")))
    os.replace(tmp, DATA_FILE)
    restrict_to_owner(DATA_FILE)


def _current_user_sid():
    out = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True,
                         creationflags=subprocess.CREATE_NO_WINDOW).stdout
    m = re.search(r"S-1-[\d-]+", out)
    return m.group(0) if m else None


def restrict_to_owner(path):
    """Only you (and SYSTEM) can open the file. The contents are already DPAPI-encrypted to your
    Windows login, so this is defence in depth against other local accounts copying it."""
    sid = _current_user_sid()
    if not sid:
        return
    result = subprocess.run(["icacls", path, "/inheritance:r", "/grant:r", f"*{sid}:F", "*S-1-5-18:F"],
                            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        log.warning("Couldn't restrict access to %s", path)


def load_settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            settings = json.load(f)
        return settings if isinstance(settings, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(settings):
    os.makedirs(APP_DIR, exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f)


# ---------------------------------------------------------------- Roblox web API

class RobloxError(Exception):
    pass


class CookieExpired(RobloxError):
    def __init__(self):
        super().__init__("Cookie is invalid or expired.")


def _is_roblox_url(url):
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and (host == "roblox.com" or host.endswith(".roblox.com"))


class _SafeRedirects(urllib.request.HTTPRedirectHandler):
    """urllib re-sends every header, Cookie included, to wherever a redirect points. Refuse any
    redirect of a cookie-carrying request that would leave https://*.roblox.com; the 3xx is
    returned to the caller instead of being followed."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if req.has_header("Cookie") and not _is_roblox_url(newurl):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_SafeRedirects)


def _request(url, cookie=None, method="GET", headers=None, data=None):
    h = {"User-Agent": USER_AGENT}
    if cookie:
        if not _is_roblox_url(url):   # a login cookie must never leave roblox.com over HTTPS
            raise RobloxError("Refused to send a cookie outside https://*.roblox.com.")
        h["Cookie"] = ".ROBLOSECURITY=" + cookie
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with _opener.open(req, timeout=15) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.headers, e.read()
        finally:
            e.close()
    # URLError, timeouts and dropped connections are OSErrors; a half-read reply is an HTTPException.
    except (OSError, http.client.HTTPException) as e:
        raise RobloxError(f"Network error: {getattr(e, 'reason', None) or type(e).__name__}") from None


def _post(url, cookie, payload=None, extra_headers=None):
    """POST with Roblox's CSRF handshake: the first attempt returns the token to retry with."""
    headers = {"Referer": "https://www.roblox.com/", "Origin": "https://www.roblox.com",
               "Content-Type": "application/json", **(extra_headers or {})}
    body = json.dumps(payload).encode() if payload is not None else b""
    status, hdrs, data = _request(url, cookie, "POST", headers, body)
    if status == 403 and hdrs.get("x-csrf-token"):
        headers["X-CSRF-TOKEN"] = hdrs["x-csrf-token"]
        status, hdrs, data = _request(url, cookie, "POST", headers, body)
    if status == 401:
        raise CookieExpired()
    return status, hdrs, data


def get_user(cookie):
    """Returns {'id', 'name', 'displayName'} for the account the cookie belongs to."""
    status, _, body = _request("https://users.roblox.com/v1/users/authenticated", cookie)
    if status == 401:
        raise CookieExpired()
    if status != 200:
        raise RobloxError(f"Roblox returned HTTP {status}.")
    try:
        user = json.loads(body)
        return {"id": int(user["id"]), "name": str(user["name"]), "displayName": str(user["displayName"])}
    except (ValueError, KeyError, TypeError):
        raise RobloxError("Roblox sent an unexpected reply. Try again in a moment.") from None


# A .ROBLOSECURITY value is printable ASCII with no spaces. Anything else (a stray line break from
# copying, say) would break the HTTP header, so reject it with a clear message instead.
COOKIE_RE = re.compile(r"[\x21-\x7e]{50,8192}")


def get_presence(cookie, user_id):
    """The account's own presence: userPresenceType 0 offline, 1 online, 2 in game, 3 in Studio."""
    status, _, body = _post("https://presence.roblox.com/v1/presence/users", cookie, {"userIds": [user_id]})
    if status != 200:
        raise RobloxError(f"Presence returned HTTP {status}.")
    return json.loads(body)["userPresences"][0]


def get_place_name(cookie, place_id):
    try:
        status, _, body = _request(
            f"https://games.roblox.com/v1/games/multiget-place-details?placeIds={place_id}", cookie)
        if status == 200:
            places = json.loads(body)
            return places[0]["name"] if places else None
    except (RobloxError, ValueError, KeyError):
        pass
    return None


def get_servers(place_id, cursor="", fewest_first=True):
    """One page of a game's public servers as ([{id, playing, maxPlayers, ping, fps}], next_cursor).
    Full servers are left out. This list is public, so no cookie is sent."""
    query = urllib.parse.urlencode({"sortOrder": "Asc" if fewest_first else "Desc", "limit": 100,
                                    "excludeFullGames": "true", **({"cursor": cursor} if cursor else {})})
    status, _, body = _request(f"https://games.roblox.com/v1/games/{int(place_id)}/servers/Public?{query}")
    if status == 429:
        raise RobloxError("Roblox is rate-limiting server lists. Wait a few seconds and try again.")
    if status != 200:
        raise RobloxError(f"Couldn't load servers (HTTP {status}).")
    try:
        page = json.loads(body)
        servers = [{"id": s["id"], "playing": int(s.get("playing") or 0), "maxPlayers": int(s["maxPlayers"]),
                    "ping": s.get("ping"), "fps": s.get("fps")}
                   for s in page.get("data") or [] if re.fullmatch(UUID_RE, str(s.get("id", "")))]
    except (ValueError, KeyError, TypeError, AttributeError):
        raise RobloxError("Roblox sent an unexpected server list.") from None
    return servers, page.get("nextPageCursor") or ""


def find_user(username):
    """{'id', 'name'} for a Roblox username, or None if there's no such (unbanned) user. Public, so
    no cookie is sent."""
    if not re.fullmatch(r"[A-Za-z0-9_]{3,20}", username):
        return None
    status, _, body = _request("https://users.roblox.com/v1/usernames/users", None, "POST",
                               {"Content-Type": "application/json"},
                               json.dumps({"usernames": [username], "excludeBannedUsers": True}).encode())
    if status != 200:
        raise RobloxError(f"Couldn't look up that username (HTTP {status}).")
    try:
        users = json.loads(body)["data"]
        return {"id": int(users[0]["id"]), "name": str(users[0]["name"])} if users else None
    except (ValueError, KeyError, TypeError, IndexError):
        raise RobloxError("Roblox sent an unexpected reply about that username.") from None


def server_of_player(cookie, username):
    """(place_id, job_id, player_name) for the server a player is in, as seen by the account whose
    cookie is given. Raises RobloxError with a plain explanation when it can't be seen."""
    user = find_user(username.strip().lstrip("@"))
    if not user:
        raise RobloxError(f"There's no Roblox user called {username.strip()!r}.")
    p = get_presence(cookie, user["id"])
    if p.get("userPresenceType") != 2:
        raise RobloxError(f"@{user['name']} isn't in a game right now.")
    place, job = p.get("rootPlaceId") or p.get("placeId"), p.get("gameId")
    if not place or not job or not re.fullmatch(UUID_RE, str(job)):
        raise RobloxError(f"@{user['name']} is in a game, but their privacy settings hide which server. "
                          "Usually you need to be friends with them.")
    return str(int(place)), str(job), user["name"]


def pick_smallest_server(servers, needed):
    """The least busy server that still has room for `needed` more players, or None."""
    fits = [s for s in servers if s["maxPlayers"] - s["playing"] >= needed]
    return min(fits, key=lambda s: (s["playing"], s["ping"] if isinstance(s["ping"], (int, float)) else 9999),
               default=None)


def get_headshots(user_ids, size):
    """Returns {user_id: png_bytes} for the accounts' avatar headshots."""
    ids = ",".join(str(i) for i in user_ids)
    status, _, body = _request("https://thumbnails.roblox.com/v1/users/avatar-headshot"
                               f"?userIds={ids}&size={size}x{size}&format=Png&isCircular=true")
    if status != 200:
        return {}
    result = {}
    for item in json.loads(body).get("data", []):
        if item.get("state") == "Completed" and item.get("imageUrl"):
            img_status, _, png = _request(item["imageUrl"])
            if img_status == 200:
                result[item["targetId"]] = png
    return result


def get_auth_ticket(cookie):
    status, hdrs, _ = _post("https://auth.roblox.com/v1/authentication-ticket", cookie,
                            extra_headers={"RBXAuthenticationNegotiation": "1"})
    ticket = hdrs.get("rbx-authentication-ticket")
    if status != 200 or not ticket:
        raise RobloxError(f"Couldn't get a login ticket (HTTP {status}).")
    return ticket


def resolve_share_link(cookie, code):
    """Turns a roblox.com/share?code=...&type=Server link into (place_id, link_code)."""
    status, _, body = _post("https://apis.roblox.com/sharelinks/v1/resolve-link", cookie,
                            {"linkId": code, "linkType": "Server"})
    if status != 200:
        raise RobloxError(f"Roblox couldn't open that private server link (HTTP {status}).")
    try:
        invite = json.loads(body)["privateServerInviteData"]
        state, place_id, link_code = invite.get("status"), invite.get("placeId"), invite.get("linkCode")
    except (ValueError, KeyError, TypeError, AttributeError):
        raise RobloxError("Roblox sent an unexpected reply about that private server link.") from None
    if state != "Valid" or not place_id or not link_code:
        raise RobloxError("That private server link has expired or been turned off. Ask the owner for a new one.")
    return str(int(place_id)), str(link_code)


def build_launch_uri(ticket, place_id, job_id="", link_code=""):
    tracker = str(random.randint(10**11, 10**12 - 1))
    request = "RequestPrivateGame" if link_code else "RequestGameJob" if job_id else "RequestGame"
    params = {"request": request, "browserTrackerId": tracker, "placeId": place_id,
              "isPlayTogetherGame": "false", "joinAttemptId": str(uuid.uuid4()),
              "joinAttemptOrigin": "PlayButton"}
    if link_code:
        params["linkCode"] = link_code
    elif job_id:
        params["gameId"] = job_id
    launcher = "https://www.roblox.com/Game/PlaceLauncher.ashx?" + urllib.parse.urlencode(params)
    return "+".join([
        "roblox-player:1", "launchmode:play", "gameinfo:" + ticket,
        "launchtime:%d" % (time.time() * 1000),
        "placelauncherurl:" + urllib.parse.quote(launcher, safe=""),
        "browsertrackerid:" + tracker, "robloxLocale:en_us", "gameLocale:en_us",
        "channel:", "LaunchExp:InApp",
    ])


def launch(cookie, place_id, job_id="", link_code=""):
    try:
        os.startfile(build_launch_uri(get_auth_ticket(cookie), place_id, job_id, link_code))
    except OSError as e:
        # The OSError text includes the launch link, which holds a login ticket, so it must not
        # reach the log or a dialog.
        raise RobloxError(f"Couldn't start Roblox (Windows error {e.winerror}). "
                          "Is the Roblox player installed?") from None


# ---------------------------------------------------------------- browser login
# "Log in with browser" opens a throwaway browser profile on the Roblox login page. Once the user
# has logged in, the .ROBLOSECURITY cookie is read over the browser's local DevTools connection,
# then the browser is closed and the profile deleted. The user's own browser profile is never
# touched, and the session is never logged out (that would invalidate the saved cookie).

# Only fixed install locations, so nothing on PATH can stand in for the browser.
BROWSERS = (r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
            r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
            r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
            r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
            r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
            r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe")
LOGIN_URL = "https://www.roblox.com/login"
LOGIN_TIMEOUT = 600   # seconds the login window may stay open
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


LOGIN_PROFILE_PREFIX = "AltVault-login-"
_open_logins = set()   # BrowserLogins whose browser may still be open; closed on exit


def remove_stale_login_profiles():
    """Deletes throwaway login profiles left behind if AltVault was killed mid-login. They can hold
    a live session, so none may linger. Only one AltVault runs at a time, so none is in use now."""
    tmp = tempfile.gettempdir()
    for name in os.listdir(tmp):
        if name.startswith(LOGIN_PROFILE_PREFIX):
            shutil.rmtree(os.path.join(tmp, name), ignore_errors=True)


def find_browser():
    return next((p for p in (os.path.expandvars(b) for b in BROWSERS) if os.path.isfile(p)), None)


class DevTools:
    """Just enough of a WebSocket client (RFC 6455) to send Chrome DevTools Protocol commands to a
    browser on this PC."""

    def __init__(self, port, path):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise OSError("DevTools closed the connection")
            head += chunk
        head, self.buf = head.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        headers = {k.strip().lower(): v.strip() for k, _, v in (line.partition(":") for line in lines[1:])}
        accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        if " 101 " not in lines[0] + " " or headers.get("sec-websocket-accept") != accept:
            raise OSError("DevTools refused the connection")
        self.next_id = 0

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise OSError("DevTools closed the connection")
            self.buf += chunk
        data, self.buf = self.buf[:n], self.buf[n:]
        return data

    def _send_frame(self, opcode, payload):
        mask = os.urandom(4)
        n = len(payload)
        head = bytes([0x80 | opcode]) + (bytes([0x80 | n]) if n < 126 else
                                         bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536 else
                                         bytes([0x80 | 127]) + struct.pack(">Q", n))
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def _message(self):
        parts = []
        while True:
            b1, b2 = self._read(2)
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if b2 & 0x80 else None
            data = self._read(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            opcode = b1 & 0x0F
            if opcode == 8:
                raise OSError("DevTools closed the connection")
            if opcode == 9:
                self._send_frame(10, data)   # answer pings
                continue
            if opcode in (0, 1, 2):
                parts.append(data)
                if b1 & 0x80:
                    return b"".join(parts)

    def call(self, method, **params):
        self.next_id += 1
        self._send_frame(1, json.dumps({"id": self.next_id, "method": method, "params": params}).encode())
        while True:
            msg = json.loads(self._message())
            if msg.get("id") == self.next_id:
                if "error" in msg:
                    raise OSError(msg["error"].get("message", "DevTools error"))
                return msg.get("result", {})

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def is_roblox_cookie_domain(domain):
    """True for roblox.com and its subdomains. A plain endswith("roblox.com") would also accept a
    lookalike such as evilroblox.com."""
    host = str(domain or "").lower().lstrip(".")
    return host == "roblox.com" or host.endswith(".roblox.com")


class BrowserLogin:
    """A throwaway browser window on the Roblox login page. wait() returns the cookie once the user
    has logged in; close() shuts the browser and deletes its profile. Safe to cancel from any thread."""

    def __init__(self):
        browser = find_browser()
        if not browser:
            raise RobloxError("No Chrome, Edge or Brave was found. Use “Paste a cookie” instead.")
        self.profile = tempfile.mkdtemp(prefix=LOGIN_PROFILE_PREFIX)
        _open_logins.add(self)
        self.devtools = None
        self.cancelled = False
        self.proc = subprocess.Popen([
            browser, f"--user-data-dir={self.profile}", "--remote-debugging-port=0", "--no-first-run",
            "--no-default-browser-check", "--disable-sync", "--disable-extensions", "--new-window", LOGIN_URL])

    def _connect(self):
        # The browser writes its randomly chosen DevTools port and path here once it has started.
        with open(os.path.join(self.profile, "DevToolsActivePort"), encoding="utf-8") as f:
            port, path = f.read().split()[:2]
        self.devtools = DevTools(int(port), path)

    def wait(self, timeout=LOGIN_TIMEOUT):
        deadline = time.monotonic() + timeout
        while not self.cancelled:
            if self.proc.poll() is not None:
                raise RobloxError("The login window was closed before the login finished.")
            if time.monotonic() > deadline:
                raise RobloxError("The login timed out. Try again.")
            try:
                if not self.devtools:
                    self._connect()
                for c in self.devtools.call("Storage.getCookies").get("cookies", []):
                    if c.get("name") == ".ROBLOSECURITY" and c.get("value") \
                            and is_roblox_cookie_domain(c.get("domain")):
                        return c["value"]
            except (OSError, ValueError, KeyError):   # browser still starting, or reconnect needed
                if self.devtools:
                    self.devtools.close()
                    self.devtools = None
            time.sleep(1)
        raise RobloxError("Login cancelled.")

    def close(self):
        self.cancelled = True
        _open_logins.discard(self)
        if self.devtools:
            try:
                self.devtools.call("Browser.close")
            except (OSError, ValueError):
                pass
            self.devtools.close()
            self.devtools = None
        try:
            self.proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        for _ in range(20):   # the browser's helper processes can hold files for a moment
            shutil.rmtree(self.profile, ignore_errors=True)
            if not os.path.exists(self.profile):
                return
            time.sleep(0.5)
        log.warning("Couldn't fully delete the temporary login profile")


UUID_RE = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"


def parse_place_id(text):
    """Accepts a bare place ID, a roblox.com game link, or a recent-games entry like 'Name  (123)'."""
    text = text.strip()
    if text.isdigit():
        return text
    m = (re.search(r"roblox\.com/(?:[a-z]{2}/)?games/(\d+)", text) or re.search(r"placeId=(\d+)", text)
         or re.search(r"\((\d+)\)\s*$", text))
    return m.group(1) if m else None


PRIVATE_CODE = r"([A-Za-z0-9_-]{4,200})"


def parse_private_server(text):
    """Recognises private server links. Returns None for anything else, or a dict with either
    'link_code' (and 'place_id') for roblox.com/games/...?privateServerLinkCode=... links, or
    'share_code' for roblox.com/share?code=...&type=Server links, which need resolving first."""
    text = text.strip()
    if not re.search(r"(^|[/.])roblox\.com/", text, re.I):
        return None
    m = re.search(r"[?&]privateServerLinkCode=" + PRIVATE_CODE, text)
    if m:
        place = re.search(r"roblox\.com/(?:[a-z]{2}/)?games/(\d+)", text, re.I)
        return {"place_id": place.group(1) if place else None, "link_code": m.group(1)}
    if re.search(r"roblox\.com/share\?", text, re.I):
        code = re.search(r"[?&]code=" + PRIVATE_CODE, text)
        kind = re.search(r"[?&]type=(\w+)", text)
        if code and (not kind or kind.group(1).lower() == "server"):
            return {"share_code": code.group(1)}
    return None


def parse_job_id(text):
    """'' for no job ID, the server's UUID if one is in the text, None if the text isn't valid."""
    text = text.strip()
    if not text:
        return ""
    m = re.search(UUID_RE, text)
    return m.group(0) if m else None


# ---------------------------------------------------------------- UI theme

C = {"bg": "#1e1f22", "panel": "#2b2d31", "field": "#383a40", "sel": "#404249",
     "fg": "#e3e5e8", "muted": "#949ba4", "accent": "#5865f2", "accent_hover": "#4752c4",
     "green": "#23a55a", "red": "#f23f43", "yellow": "#f0b232"}
FONT = ("Segoe UI", 10)


def apply_theme(root, scale):
    s = ttk.Style(root)
    s.theme_use("clam")
    root.configure(bg=C["bg"])
    s.configure(".", background=C["bg"], foreground=C["fg"], fieldbackground=C["field"],
                bordercolor=C["bg"], lightcolor=C["bg"], darkcolor=C["bg"], troughcolor=C["bg"],
                focuscolor=C["accent"], selectbackground=C["accent"], selectforeground="white",
                insertcolor=C["fg"], font=FONT)
    s.configure("TButton", background=C["panel"], padding=(12, 6), borderwidth=0)
    s.map("TButton", background=[("disabled", C["bg"]), ("pressed", C["sel"]), ("active", C["sel"])],
          foreground=[("disabled", C["muted"])])
    s.configure("Accent.TButton", background=C["accent"], foreground="white", font=("Segoe UI Semibold", 10))
    s.map("Accent.TButton", background=[("disabled", C["panel"]), ("pressed", C["accent_hover"]),
                                        ("active", C["accent_hover"])])
    s.configure("TEntry", padding=6, borderwidth=0)
    s.configure("TCombobox", padding=5, borderwidth=0, background=C["field"], arrowcolor=C["muted"])
    s.map("TCombobox", fieldbackground=[("readonly", C["field"])], background=[("active", C["sel"])],
          arrowcolor=[("active", C["fg"])])
    root.option_add("*TCombobox*Listbox.background", C["field"])
    root.option_add("*TCombobox*Listbox.foreground", C["fg"])
    root.option_add("*TCombobox*Listbox.selectBackground", C["accent"])
    root.option_add("*TCombobox*Listbox.font", FONT)
    s.configure("TCheckbutton", indicatorbackground=C["field"], indicatorforeground="white", padding=2)
    s.map("TCheckbutton", indicatorbackground=[("selected", C["accent"])], background=[("active", C["bg"])])
    s.configure("Treeview", background=C["panel"], fieldbackground=C["panel"], borderwidth=0,
                rowheight=int(50 * scale))
    s.map("Treeview", background=[("selected", C["sel"])], foreground=[("selected", C["fg"])])
    s.configure("Treeview.Heading", background=C["panel"], foreground=C["muted"], borderwidth=0,
                padding=(6, 6), font=("Segoe UI Semibold", 9))
    s.map("Treeview.Heading", background=[("active", C["panel"])])
    s.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    s.configure("Vertical.TScrollbar", background=C["sel"], troughcolor=C["panel"], arrowcolor=C["muted"],
                bordercolor=C["panel"], lightcolor=C["sel"], darkcolor=C["sel"], borderwidth=0)
    s.map("Vertical.TScrollbar", background=[("active", C["field"])])
    s.configure("Muted.TLabel", foreground=C["muted"])
    s.configure("Small.TLabel", foreground=C["muted"], font=("Segoe UI", 9))
    s.configure("Title.TLabel", font=("Segoe UI Semibold", 16))
    s.configure("Error.TLabel", foreground=C["red"])
    s.configure("Success.TLabel", foreground=C["green"])


def themed_menu(root):
    return tk.Menu(root, tearoff=0, bg=C["panel"], fg=C["fg"], activebackground=C["accent"],
                   activeforeground="white", bd=0, font=FONT)


# ---------------------------------------------------------------- UI

COOKIE_STEPS = [
    "Log into the account on roblox.com in a private window (Ctrl+Shift+N).",
    "Press F12 > Application (Firefox: Storage) > Cookies > https://www.roblox.com.",
    "Copy the whole Value of .ROBLOSECURITY and paste it here.",
    "Close the private window. Don't click Log Out, which kills the cookie.",
]


class AddAccountDialog(tk.Toplevel):
    def __init__(self, app, for_account=None):
        super().__init__(app.root, bg=C["bg"])
        self.app = app
        self.login = None   # the BrowserLogin in progress, if any
        wrap = int(500 * app.scale)
        self.title(f"Update login: {for_account['name']}" if for_account else "Add account")
        self.resizable(False, False)
        self.transient(app.root)
        frm = ttk.Frame(self, padding=18)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text=f"Log in again as @{for_account['name']}" if for_account
                  else "Add a Roblox account", style="Title.TLabel").pack(anchor="w")
        ttk.Label(frm, text="A private browser window opens on roblox.com. Log in as usual (2-step verification "
                  "works too). AltVault saves the account and closes the window. Your normal browser and its "
                  "logins aren't touched.", wraplength=wrap, justify="left").pack(anchor="w", pady=(8, 0))
        self.login_btn = ttk.Button(frm, text="Log in with browser", style="Accent.TButton", command=self.toggle_login)
        self.login_btn.pack(anchor="w", pady=(12, 0))
        self.status = ttk.Label(frm, text="", style="Muted.TLabel", wraplength=wrap, justify="left")
        self.status.pack(anchor="w", pady=(8, 0))

        ttk.Label(frm, text="Note (optional)", style="Muted.TLabel").pack(anchor="w", pady=(10, 2))
        self.note_var = tk.StringVar(value=for_account.get("note", "") if for_account else "")
        ttk.Entry(frm, textvariable=self.note_var).pack(fill="x")

        # Pasting a cookie by hand stays available for anyone who prefers it, folded away.
        self.more_btn = ttk.Button(frm, text="Paste a cookie instead ▸", command=self.toggle_paste)
        self.more_btn.pack(anchor="w", pady=(12, 0))
        self.paste = ttk.Frame(frm)
        for i, step in enumerate(COOKIE_STEPS, 1):
            ttk.Label(self.paste, text=f"{i}.  {step}", style="Small.TLabel", wraplength=wrap,
                      justify="left").pack(anchor="w", pady=(6 if i == 1 else 1, 0))
        self.cookie_var = tk.StringVar()
        self.cookie_entry = ttk.Entry(self.paste, textvariable=self.cookie_var, show="•")
        self.cookie_entry.pack(fill="x", pady=(8, 0))
        self.add_btn = ttk.Button(self.paste, text="Save account", command=self.submit)
        self.add_btn.pack(anchor="e", pady=(8, 0))
        ttk.Label(self.paste, text="A cookie works like a password. Only ever paste it into AltVault.",
                  style="Small.TLabel").pack(anchor="w", pady=(4, 0))

        if not for_account:
            ttk.Button(frm, text="Import from Roblox Account Manager...", command=self.import_ram).pack(
                anchor="w", pady=(6, 0))
        ttk.Button(frm, text="Close", command=self.destroy).pack(side="bottom", anchor="e", pady=(14, 0))
        self.cookie_entry.bind("<Return>", lambda _e: self.submit())
        self.bind("<Escape>", lambda _e: self.destroy())
        dark_title_bar(self)
        self.grab_set()
        self.login_btn.focus_set()

    def import_ram(self):
        path = filedialog.askopenfilename(parent=self, title="RAM's AccountData.json",
                                          filetypes=[("RAM accounts", "AccountData.json"), ("JSON", "*.json")])
        if not path:
            return
        self.status.config(text="Reading and checking accounts...", style="Muted.TLabel")

        def report(text):
            if self.winfo_exists():
                self.status.config(text=text, style="Muted.TLabel")
        threading.Thread(target=import_ram_into, args=(self.app, path, report), daemon=True).start()

    def toggle_paste(self):
        if self.paste.winfo_ismapped():
            self.paste.pack_forget()
            self.more_btn.config(text="Paste a cookie instead ▸")
        else:
            self.paste.pack(fill="x", after=self.more_btn)
            self.more_btn.config(text="Paste a cookie instead ▾")
            self.cookie_entry.focus_set()

    # ------------------------------------------------------------ browser login
    def toggle_login(self):
        if self.login:
            self.login.cancelled = True
            self.status.config(text="Cancelling...", style="Muted.TLabel")
            return
        try:
            self.login = BrowserLogin()
        except (RobloxError, OSError) as e:
            self.status.config(text=str(e), style="Error.TLabel")
            return
        self.login_btn.config(text="Cancel login")
        self.status.config(text="Log in to Roblox in the browser window that just opened. This window "
                           "updates by itself when you're done.", style="Muted.TLabel")
        login, note = self.login, self.note_var.get().strip()

        def work():
            try:
                cookie = login.wait()
                user = get_user(cookie)
                result = (self._login_done, user, cookie, note)
            except RobloxError as e:
                result = (self._login_failed, str(e))
            except Exception:
                log.exception("Browser login failed")
                result = (self._login_failed, "Something went wrong with the browser login. Try again.")
            finally:
                login.close()   # shut the browser and delete its throwaway profile, whatever happened
            try:
                self.after(0, *result)
            except (RuntimeError, tk.TclError):
                pass   # this window was closed meanwhile

        threading.Thread(target=work, daemon=True).start()

    def _login_done(self, user, cookie, note):
        self.login = None
        self.app.add_or_update(user, cookie, note)
        self.note_var.set("")
        self.login_btn.config(text="Log in another account")
        self.status.config(text=f"Saved @{user['name']}. Log in another account, or Close.", style="Success.TLabel")

    def _login_failed(self, msg):
        self.login = None
        self.login_btn.config(text="Log in with browser")
        self.status.config(text=msg, style="Muted.TLabel" if msg == "Login cancelled." else "Error.TLabel")

    def destroy(self):
        if self.login:   # the worker sees this within a second and closes the browser
            self.login.cancelled = True
        super().destroy()

    def submit(self):
        cookie = self.cookie_var.get().strip().strip('"')
        if cookie.startswith(".ROBLOSECURITY="):
            cookie = cookie[len(".ROBLOSECURITY="):]
        if not cookie:
            self.status.config(text="Paste the cookie first.", style="Error.TLabel")
            return
        if not COOKIE_RE.fullmatch(cookie):
            self.status.config(text="That doesn't look like a whole .ROBLOSECURITY value. Copy the "
                               "Value column again (no spaces or line breaks).", style="Error.TLabel")
            return
        self.add_btn.config(state="disabled")
        self.status.config(text="Checking with Roblox...", style="Muted.TLabel")
        note = self.note_var.get().strip()

        def work():
            try:
                user = get_user(cookie)
            except RobloxError as e:
                result = (self._failed, str(e))
            except Exception:
                log.exception("Checking a cookie failed")
                result = (self._failed, "Something went wrong checking the cookie. Try again.")
            else:
                result = (self._succeeded, user, cookie, note)
            try:
                self.after(0, *result)
            except (RuntimeError, tk.TclError):
                pass   # the dialog was closed while Roblox was being checked

        threading.Thread(target=work, daemon=True).start()

    def _failed(self, msg):
        self.status.config(text=msg, style="Error.TLabel")
        self.add_btn.config(state="normal")

    def _succeeded(self, user, cookie, note):
        self.app.add_or_update(user, cookie, note)
        try:   # don't leave the cookie sitting on the clipboard for other apps to read
            if cookie in self.clipboard_get():
                self.clipboard_clear()
        except tk.TclError:
            pass
        self.destroy()


def import_ram_into(app, path, report):
    """Reads a RAM file, checks every login with Roblox, then saves the working ones in one go.
    report(text) is called on the Tk thread with the outcome."""
    try:
        entries = read_ram_accounts(path)
    except (RobloxError, OSError) as e:
        message = str(e)
        app.ui(report, message)
        return
    good, expired = [], []
    for i, entry in enumerate(entries, 1):
        app.set_status(f"Checking imported account {i}/{len(entries)}...")
        try:
            good.append((get_user(entry["cookie"]), entry))
        except RobloxError as e:
            if not isinstance(e, CookieExpired):
                log.warning("Import check failed: %s", e)
            expired.append(entry["name"] or "an account")

    def save():
        app.add_many(good)
        if not entries:
            report("No accounts were found in that file.")
            return
        text = f"Imported {len(good)} account{'s' if len(good) != 1 else ''}."
        if expired:
            text += (f" Skipped {len(expired)} whose login has expired: " + ", ".join(expired[:8]) +
                     ("..." if len(expired) > 8 else "") + ". Add those again with Log in with browser.")
        report(text)
    app.ui(save)


class ServerBrowser(tk.Toplevel):
    """A game's public servers with player counts, ping and FPS. Pick one to join with the selected
    accounts, or let AltVault pick the least busy one that has room for all of them."""

    def __init__(self, app, place_id):
        super().__init__(app.root, bg=C["bg"])
        self.app, self.place_id = app, place_id
        self.servers, self.cursor, self.loading = [], "", False
        self.title("Servers")
        self.transient(app.root)
        self.geometry(f"{int(560 * app.scale)}x{int(460 * app.scale)}")
        frm = ttk.Frame(self, padding=14)
        frm.pack(fill="both", expand=True)
        top = ttk.Frame(frm)
        top.pack(fill="x")
        self.heading = ttk.Label(top, text="Loading servers...", style="Title.TLabel")
        self.heading.pack(side="left")
        self.order = tk.StringVar(value="Fewest players")
        order = ttk.Combobox(top, textvariable=self.order, state="readonly", width=16,
                             values=("Fewest players", "Most players"))
        order.pack(side="right")
        order.bind("<<ComboboxSelected>>", lambda _e: self.reload())

        btns = ttk.Frame(frm)
        btns.pack(side="bottom", fill="x", pady=(10, 0))
        self.join_btn = ttk.Button(btns, text="Join this server", style="Accent.TButton", command=self.join_selected)
        self.join_btn.pack(side="right")
        ttk.Button(btns, text="Join least busy", command=self.join_smallest).pack(side="right", padx=(0, 6))
        ttk.Button(btns, text="Copy Job ID", command=self.copy_id).pack(side="left")
        self.more_btn = ttk.Button(btns, text="Load more", command=self.load)
        self.more_btn.pack(side="left", padx=(6, 0))
        self.status = ttk.Label(frm, text="Joins with the accounts selected in AltVault.", style="Small.TLabel")
        self.status.pack(side="bottom", anchor="w", pady=(8, 0))

        cols = ("players", "ping", "fps", "id")
        self.tree = ttk.Treeview(frm, columns=cols, show="headings", selectmode="browse")
        for col, title, width in zip(cols, ("Players", "Ping", "FPS", "Server / Job ID"), (80, 70, 60, 300)):
            self.tree.heading(col, text=title, anchor="w")
            self.tree.column(col, width=int(width * app.scale), anchor="w", stretch=col == "id")
        self.tree.configure(style="Servers.Treeview")
        ttk.Style(self).configure("Servers.Treeview", rowheight=int(26 * app.scale))
        self.tree.pack(fill="both", expand=True, pady=(10, 0))
        self.tree.bind("<Double-1>", lambda _e: self.join_selected())
        self.bind("<Escape>", lambda _e: self.destroy())
        dark_title_bar(self)
        self.after(50, lambda: self.winfo_exists() and dark_title_bar(self))   # again once it's on screen
        self.load()

    def reload(self):
        self.servers, self.cursor = [], ""
        self.tree.delete(*self.tree.get_children())
        self.load()

    def load(self):
        if self.loading:
            return
        self.loading = True
        self.more_btn.config(state="disabled")
        fewest, cursor = self.order.get() == "Fewest players", self.cursor

        def work():
            try:
                servers, nxt = get_servers(self.place_id, cursor, fewest)
                self.app.ui(self.loaded, servers, nxt, None)
            except RobloxError as e:
                self.app.ui(self.loaded, [], cursor, str(e))

        threading.Thread(target=work, daemon=True).start()

    def loaded(self, servers, cursor, error):
        if not self.winfo_exists():
            return
        self.loading, self.cursor = False, cursor
        for s in servers:
            if self.tree.exists(s["id"]):   # pages can overlap while servers fill and empty
                continue
            self.servers.append(s)
            ping = f"{s['ping']} ms" if isinstance(s["ping"], (int, float)) else "-"
            fps = f"{s['fps']:.0f}" if isinstance(s["fps"], (int, float)) else "-"
            self.tree.insert("", "end", iid=s["id"], values=(f"{s['playing']}/{s['maxPlayers']}", ping, fps, s["id"]))
        self.heading.config(text=f"{len(self.servers)} servers" if self.servers else "No open servers")
        self.more_btn.config(state="normal" if cursor else "disabled")
        if error:
            self.status.config(text=error)

    def chosen(self):
        sel = self.tree.selection()
        return sel[0] if sel else None

    def copy_id(self):
        if self.chosen():
            self.app.copy(self.chosen())
            self.status.config(text="Job ID copied.")

    def join(self, job_id):
        self.app.job_var.set(job_id)
        self.app.place_var.set(str(self.place_id))
        self.destroy()
        self.app.join_selected()

    def join_selected(self):
        if not self.chosen():
            self.status.config(text="Pick a server first, or use Join least busy.")
            return
        self.join(self.chosen())

    def join_smallest(self):
        needed = max(1, len(self.app.selected_accounts()))
        server = pick_smallest_server(self.servers, needed)
        if not server:
            self.status.config(text=f"None of the loaded servers has room for {needed} more players. Try Load more.")
            return
        self.join(server["id"])


class App:
    def __init__(self, root, accounts):
        self.root = root
        self.accounts = accounts
        self.settings = load_settings()
        self.scale = root.winfo_fpixels("1i") / 96
        self.presence = {}     # account id -> presence dict
        self.expired = set()   # account ids whose cookie stopped working
        self.running = {}      # Roblox pid -> account id
        self.avatars = {}      # account id -> PhotoImage
        sizes = (48, 60, 75, 100, 110, 150)
        self.avatar_px = next((s for s in sizes if s >= 40 * self.scale), 150)
        self.blank_avatar = tk.PhotoImage(width=self.avatar_px, height=self.avatar_px)
        self.joining = False
        self.afk_job = None           # pending after() id for the next anti-AFK ping
        self.afk_busy = False
        self.afk_wait_started = None  # when a due ping started waiting for the user to pause
        self.afk_last = {}            # hwnd -> monotonic time of its last successful ping
        self.afk_misses = {}          # hwnd -> passes in a row that couldn't ping it
        self.afk_alerted = False      # a "blocked" tray alert is showing for the current stuck spell
        self.afk_busy_since = 0.0     # when the running pass started (watchdog)
        self.afk_pass_id = 0          # identifies the current pass, so a late stuck one is ignored
        self.afk_waiting_for = None   # why a due ping is being held back, for the log
        self.afk_waiting_since = 0.0
        self.afk_waiting_logged = False
        self.afk_on = False           # mirrors the Anti-AFK box for the tray thread, which can't read Tk
        self.tray = None              # set by main(); without it, closing the window quits

        apply_theme(root, self.scale)
        root.title(APP_TITLE)
        root.geometry(safe_geometry(self.settings.get("geometry")))
        root.minsize(int(640 * self.scale), int(440 * self.scale))
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.build()
        dark_title_bar(root)

        self.load_cached_avatars()
        self.refresh()
        if self.multi_var.get():
            self.toggle_multi(quiet=True)
        if self.anti_afk_var.get():
            self.toggle_anti_afk(quiet=True)
        threading.Thread(target=self.fetch_missing_avatars, daemon=True).start()
        self.root.after(500, self.tick)
        self.root.after(1000, self.refresh_presence)
        # Nobody sees presence while AltVault is minimised, so it pauses then and catches up on restore.
        self.presence_stale = False
        root.bind("<Map>", self.on_restore)

    # ------------------------------------------------------------ layout
    def build(self):
        outer = ttk.Frame(self.root, padding=(16, 14, 16, 12))
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text=APP_TITLE, style="Title.TLabel").pack(side="left")
        self.summary = ttk.Label(header, text="", style="Muted.TLabel")
        self.summary.pack(side="left", padx=(12, 0), pady=(6, 0))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.refresh())
        search = ttk.Entry(header, textvariable=self.search_var, width=22)
        search.pack(side="right")
        ttk.Label(header, text="Search", style="Muted.TLabel").pack(side="right", padx=(0, 6))

        game = ttk.Frame(outer)
        game.pack(fill="x", pady=(12, 0))
        ttk.Label(game, text="Game", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(game, text="Server / Job ID (optional)", style="Muted.TLabel").grid(
            row=0, column=1, sticky="w", padx=(10, 0))
        self.place_var = tk.StringVar(value=self.settings.get("place", ""))
        self.place_box = ttk.Combobox(game, textvariable=self.place_var)
        self.place_box.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        self.job_var = tk.StringVar()
        ttk.Entry(game, textvariable=self.job_var).grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=(2, 0))
        self.star_btn = ttk.Button(game, text="☆", width=3, command=self.toggle_favorite)
        self.star_btn.grid(row=1, column=2, padx=(6, 0), pady=(2, 0))
        ttk.Button(game, text="Servers...", command=self.open_servers).grid(row=1, column=3, padx=(6, 0), pady=(2, 0))
        self.place_var.trace_add("write", lambda *_: self.update_star())
        game.columnconfigure(0, weight=3)
        game.columnconfigure(1, weight=2)
        ttk.Label(game, text="Paste a place ID, a game link or a private server link, or pick a recent game.",
                  style="Small.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", pady=(3, 0))
        self.update_recents_box()

        # Footer and buttons are packed against the bottom first so the table can't squeeze them out.
        footer = ttk.Frame(outer)
        footer.pack(side="bottom", fill="x", pady=(10, 0))
        self.multi_var = tk.BooleanVar(value=self.settings.get("multi", True))
        ttk.Checkbutton(footer, text="Multi-Roblox", variable=self.multi_var,
                        command=self.toggle_multi).pack(side="left")
        self.same_server_var = tk.BooleanVar(value=self.settings.get("same_server", True))
        ttk.Checkbutton(footer, text="Same server",
                        variable=self.same_server_var, command=self.save_settings).pack(side="left", padx=(14, 0))
        self.auto_tile_var = tk.BooleanVar(value=self.settings.get("auto_tile", True))
        ttk.Checkbutton(footer, text="Auto-tile windows",
                        variable=self.auto_tile_var, command=self.save_settings).pack(side="left", padx=(14, 0))
        self.anti_afk_var = tk.BooleanVar(value=self.settings.get("anti_afk", False))
        ttk.Checkbutton(footer, text="Anti-AFK", variable=self.anti_afk_var,
                        command=self.toggle_anti_afk).pack(side="left", padx=(14, 0))
        self.status = ttk.Label(footer, text="", style="Muted.TLabel")
        self.status.pack(side="right")

        btns = ttk.Frame(outer)
        btns.pack(side="bottom", fill="x", pady=(12, 0))
        ttk.Button(btns, text="Add account", command=lambda: AddAccountDialog(self)).pack(side="left")
        ttk.Button(btns, text="Remove", command=self.remove_selected).pack(side="left", padx=(6, 0))
        self.join_btn = ttk.Button(btns, text="Join selected", style="Accent.TButton", command=self.join_selected)
        self.join_btn.pack(side="right")
        ttk.Button(btns, text="Close all Roblox", command=self.close_all).pack(side="right", padx=(0, 6))
        ttk.Button(btns, text="Tile windows", command=self.tile).pack(side="right", padx=(0, 6))

        table = ttk.Frame(outer)
        table.pack(fill="both", expand=True, pady=(12, 0))
        cols = ("display", "user", "status", "note")
        self.tree = ttk.Treeview(table, columns=cols, show="tree headings", selectmode="extended", height=5)
        self.tree.column("#0", width=int(64 * self.scale), stretch=False, anchor="center")
        for col, title, width in zip(cols, ("Display name", "Username", "Status", "Note"),
                                     (150, 150, 210, 150)):
            self.tree.heading(col, text=title, anchor="w")
            self.tree.column(col, width=int(width * self.scale), anchor="w")
        self.tree.tag_configure("offline", foreground=C["muted"])
        self.tree.tag_configure("expired", foreground=C["red"])
        self.tree.tag_configure("ingame", foreground=C["green"])
        self.tree.tag_configure("group", foreground=C["muted"], font=("Segoe UI Semibold", 10))
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.empty_hint = ttk.Label(table, text="No accounts yet — click “Add account” to start.",
                                    style="Muted.TLabel", background=C["panel"])
        # Double-clicking a group header only opens or closes it; it never launches the whole group.
        self.tree.bind("<Double-1>", self.on_double_click)
        self.tree.bind("<Return>", lambda _e: self.join_selected())
        self.tree.bind("<Delete>", lambda _e: self.remove_selected())
        self.tree.bind("<Control-a>", lambda _e: self.tree.selection_set(self.account_rows()))
        self.tree.bind("<<TreeviewOpen>>", lambda _e: self.group_toggled(True))
        self.tree.bind("<<TreeviewClose>>", lambda _e: self.group_toggled(False))
        self.tree.bind("<Button-3>", self.show_menu)

    # ------------------------------------------------------------ helpers
    def ui(self, fn, *args):
        self.root.after(0, lambda: fn(*args))

    def set_status(self, text):
        self.ui(lambda: self.status.config(text=text))

    def account(self, account_id):
        return next((a for a in self.accounts if a["id"] == account_id), None)

    def selected_accounts(self):
        """Selected accounts; selecting a group header selects everyone in it."""
        ids = set()
        for iid in self.tree.selection():
            ids |= set(self.tree.get_children(iid)) if iid.startswith(GROUP_PREFIX) else {iid}
        return [a for a in self.accounts if str(a["id"]) in ids]

    def save(self):
        save_accounts(self.accounts)

    def save_settings(self):
        place = self.place_var.get().strip()
        if parse_private_server(place):   # a private server link is an invite; never write it to disk
            place = self.settings.get("place", "")
        self.settings.update(multi=self.multi_var.get(), same_server=self.same_server_var.get(),
                             auto_tile=self.auto_tile_var.get(), anti_afk=self.anti_afk_var.get(),
                             place=place)
        save_settings(self.settings)

    def status_of(self, acc):
        """(text, tag) for the account's Status column."""
        if acc["id"] in self.expired:
            return "Login expired — right-click > Log in again", "expired"
        running = acc["id"] in self.running.values()
        p = self.presence.get(acc["id"])
        kind = p.get("userPresenceType") if p else None
        cap = self.fps_cap_of(acc)
        extras = ([f"{cap} FPS"] if cap else []) + \
            (["low graphics"] if acc["id"] in self.flagged("low_graphics") else []) + \
            (["muted"] if acc["id"] in self.flagged("muted") else [])
        fps = "".join(f" · {e}" for e in extras)
        if kind == 2:
            where = p.get("lastLocation") or "a game"
            return (f"● In game · {where}" + ("" if running else " (another device)")
                    + (fps if running else "")), "ingame"
        if running:
            return "● Roblox open" + (" · no anti-AFK" if self.afk_skipped(acc) else "") + fps, "ingame"
        if kind == 3:
            return "● In Studio" + fps, ""
        if kind == 1:
            return "● Online" + fps, ""
        if kind == 0:
            return "Offline" + fps, "offline"
        return fps.lstrip(" ·"), ""

    def group_of(self, acc):
        group = acc.get("group")
        return group.strip() if isinstance(group, str) and group.strip() else None

    def groups(self):
        return sorted({g for g in map(self.group_of, self.accounts) if g}, key=str.lower)

    def refresh(self):
        selection = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        query = self.search_var.get().strip().lower()
        collapsed = set(self.settings.get("collapsed_groups") or [])
        parents = {}
        for group in self.groups():
            members = [a for a in self.accounts if self.group_of(a) == group]
            parents[group] = self.tree.insert(
                "", "end", iid=GROUP_PREFIX + group, open=group not in collapsed, tags=("group",),
                values=(f"{group}", f"{len(members)} account{'s' if len(members) != 1 else ''}", "", ""))
        for acc in self.accounts:
            haystack = f"{acc['name']} {acc['displayName']} {acc.get('note', '')} {self.group_of(acc) or ''}".lower()
            if query and query not in haystack:
                continue
            text, tag = self.status_of(acc)
            self.tree.insert(parents.get(self.group_of(acc), ""), "end", iid=str(acc["id"]),
                             image=self.avatars.get(acc["id"], self.blank_avatar),
                             values=(acc["displayName"], "@" + acc["name"], text, acc.get("note", "")),
                             tags=(tag,) if tag else ())
        for group, iid in parents.items():   # hide groups the search filtered empty
            if query and not self.tree.get_children(iid):
                self.tree.delete(iid)
        self.tree.selection_set([i for i in selection if self.tree.exists(i)])
        if self.accounts:
            self.empty_hint.place_forget()
        else:
            self.empty_hint.place(relx=0.5, rely=0.5, anchor="center")
        n_running = len(set(self.running.values()))
        self.summary.config(text=f"{len(self.accounts)} accounts" + (f" · {n_running} open" if n_running else ""))

    def on_double_click(self, event):
        if not self.tree.identify_row(event.y).startswith(GROUP_PREFIX):
            self.join_selected()

    def account_rows(self):
        """Every account row in the list, inside groups too."""
        rows = []
        for iid in self.tree.get_children():
            rows += list(self.tree.get_children(iid)) if iid.startswith(GROUP_PREFIX) else [iid]
        return rows

    def group_toggled(self, opened):
        iid = self.tree.focus()
        if not iid.startswith(GROUP_PREFIX):
            return
        collapsed = set(self.settings.get("collapsed_groups") or [])
        (collapsed.discard if opened else collapsed.add)(iid[len(GROUP_PREFIX):])
        self.settings["collapsed_groups"] = sorted(collapsed)
        save_settings(self.settings)

    def set_group(self, accounts, group):
        for acc in accounts:
            if group:
                acc["group"] = group
            else:
                acc.pop("group", None)
        self.save()
        self.refresh()
        who = f"@{accounts[0]['name']}" if len(accounts) == 1 else f"{len(accounts)} accounts"
        self.status.config(text=f"Moved {who} to {group}." if group else f"Took {who} out of their group.")

    def ask_new_group(self, accounts):
        name = simpledialog.askstring("New group", "Group name (for example Mains or Alts):", parent=self.root)
        name = (name or "").strip()[:40]
        if name:
            self.set_group(accounts, name)

    def rename_group(self, old):
        name = simpledialog.askstring("Rename group", "New name:", initialvalue=old, parent=self.root)
        name = (name or "").strip()[:40]
        if name and name != old:
            self.set_group([a for a in self.accounts if self.group_of(a) == old], name)

    def group_menu(self, parent, accounts):
        menu = themed_menu(parent)
        current = {self.group_of(a) for a in accounts}
        for group in self.groups():
            menu.add_command(label=("✓ " if current == {group} else "    ") + group,
                             command=lambda g=group: self.set_group(accounts, g))
        if self.groups():
            menu.add_separator()
        menu.add_command(label="New group...", command=lambda: self.ask_new_group(accounts))
        if current != {None}:
            menu.add_command(label="No group", command=lambda: self.set_group(accounts, None))
        return menu

    def recents(self):
        items = self.settings.get("recents")
        return [r for r in items if isinstance(r, dict) and "id" in r and "name" in r] \
            if isinstance(items, list) else []

    def favorites(self):
        items = self.settings.get("favorites")
        return [f for f in items if isinstance(f, dict) and "id" in f and "name" in f] \
            if isinstance(items, list) else []

    def update_recents_box(self):
        """Favourites first (starred), then recent games that aren't favourites."""
        favs = self.favorites()
        fav_ids = {f["id"] for f in favs}
        self.place_box["values"] = [f"★ {f['name']}  ({f['id']})" for f in favs] + \
            [f"{r['name']}  ({r['id']})" for r in self.recents() if r["id"] not in fav_ids]
        self.update_star()

    def update_star(self):
        if hasattr(self, "star_btn"):
            place_id = None if parse_private_server(self.place_var.get()) else parse_place_id(self.place_var.get())
            starred = place_id in {f["id"] for f in self.favorites()}
            self.star_btn.config(text="★" if starred else "☆")

    def toggle_favorite(self):
        text = self.place_var.get()
        place_id = None if parse_private_server(text) else parse_place_id(text)
        if not place_id:
            self.status.config(text="Enter a game first, then click ☆ to add it to favourites.")
            return
        favs = self.favorites()
        if place_id in {f["id"] for f in favs}:
            self.settings["favorites"] = [f for f in favs if f["id"] != place_id]
            save_settings(self.settings)
            self.update_recents_box()
            self.status.config(text="Removed from favourites.")
            return
        known = {r["id"]: r["name"] for r in self.recents()}
        m = re.match(r"\s*(?:★\s*)?(.+?)\s+\(\d+\)\s*$", text)
        name = known.get(place_id) or (m.group(1) if m else None)

        def save(name):
            self.settings["favorites"] = self.favorites() + [{"id": place_id, "name": name or f"Place {place_id}"}]
            save_settings(self.settings)
            self.update_recents_box()
            self.status.config(text=f"Added {name or 'this game'} to favourites.")

        if name:
            save(name)
            return
        cookie = next((a["cookie"] for a in self.accounts if a["id"] not in self.expired), None)
        threading.Thread(target=lambda: self.ui(save, get_place_name(cookie, place_id) if cookie else None),
                         daemon=True).start()

    def add_recent(self, place_id, name):
        recents = [r for r in self.recents() if r["id"] != place_id]
        recents.insert(0, {"id": place_id, "name": name or f"Place {place_id}"})
        self.settings["recents"] = recents[:MAX_RECENTS]
        save_settings(self.settings)
        self.update_recents_box()

    # ------------------------------------------------------------ background refresh
    def load_cached_avatars(self):
        for acc in self.accounts:
            path = os.path.join(AVATAR_DIR, f"{acc['id']}_{self.avatar_px}.png")
            if os.path.exists(path):
                try:
                    self.avatars[acc["id"]] = tk.PhotoImage(file=path)
                except tk.TclError:
                    pass

    def fetch_missing_avatars(self):
        missing = [a["id"] for a in self.accounts if a["id"] not in self.avatars]
        if not missing:
            return
        try:
            pngs = get_headshots(missing, self.avatar_px)
        except RobloxError as e:
            log.warning("Avatar fetch failed: %s", e)
            return
        os.makedirs(AVATAR_DIR, exist_ok=True)
        for account_id, png in pngs.items():
            with open(os.path.join(AVATAR_DIR, f"{account_id}_{self.avatar_px}.png"), "wb") as f:
                f.write(png)

        def apply():
            self.load_cached_avatars()
            self.refresh()
        self.ui(apply)

    def on_restore(self, event):
        if event.widget is self.root and self.presence_stale:
            self.presence_stale = False
            self.refresh_presence(reschedule=False)

    def refresh_presence(self, reschedule=True):
        if reschedule:
            self.root.after(PRESENCE_INTERVAL, self.refresh_presence)
        if self.root.state() in ("iconic", "withdrawn"):   # minimised, or hidden in the tray
            self.presence_stale = True
            return
        accounts = list(self.accounts)

        def work():
            for acc in accounts:
                try:
                    self.presence[acc["id"]] = get_presence(acc["cookie"], acc["id"])
                    self.expired.discard(acc["id"])
                except CookieExpired:
                    self.expired.add(acc["id"])
                except (RobloxError, ValueError, KeyError, IndexError) as e:
                    log.warning("Presence for %s failed: %s", acc["name"], e)
            self.ui(self.refresh)

        threading.Thread(target=work, daemon=True).start()

    def tick(self):
        """Keeps track of which Roblox window belongs to which account and labels each window."""
        try:
            windows = roblox_windows()
            alive = {pid for _, pid, _ in windows}
            by_name = {a["name"].lower(): a["id"] for a in self.accounts}
            before = dict(self.running)
            for hwnd, pid, title in windows:
                if pid not in self.running and title.startswith(WINDOW_PREFIX):
                    account_id = by_name.get(title[len(WINDOW_PREFIX):].lower())
                    if account_id:
                        self.running[pid] = account_id   # window from before AltVault was reopened
                acc = self.account(self.running.get(pid))
                if acc and title != WINDOW_PREFIX + acc["name"]:
                    _user32.SetWindowTextW(hwnd, WINDOW_PREFIX + acc["name"])
            self.running = {pid: a for pid, a in self.running.items() if pid in alive}
            if self.running != before:
                self.refresh()
        except Exception:
            log.exception("Window check failed")
        self.root.after(TICK_INTERVAL, self.tick)

    # ------------------------------------------------------------ account actions
    def add_many(self, items):
        """Saves several checked accounts at once: [(user, {'cookie', 'group', 'note'})]."""
        by_id = {a["id"]: a for a in self.accounts}
        for user, entry in items:
            acc = by_id.get(user["id"])
            if acc is None:
                acc = {"id": user["id"], "note": ""}
                self.accounts.append(acc)
                by_id[user["id"]] = acc
            acc.update(name=user["name"], displayName=user["displayName"], cookie=entry["cookie"])
            if entry.get("note") and not acc.get("note"):
                acc["note"] = entry["note"]
            if entry.get("group") and not self.group_of(acc):
                acc["group"] = entry["group"]
            self.expired.discard(user["id"])
        if items:
            self.save()
            self.refresh()
            threading.Thread(target=self.fetch_missing_avatars, daemon=True).start()
            self.refresh_presence(reschedule=False)

    def add_or_update(self, user, cookie, note):
        for acc in self.accounts:
            if acc["id"] == user["id"]:
                acc.update(name=user["name"], displayName=user["displayName"], cookie=cookie)
                if note:
                    acc["note"] = note
                break
        else:
            self.accounts.append({"id": user["id"], "name": user["name"],
                                  "displayName": user["displayName"], "cookie": cookie, "note": note})
        self.expired.discard(user["id"])
        self.save()
        self.refresh()
        self.status.config(text=f"Saved @{user['name']}.")
        threading.Thread(target=self.fetch_missing_avatars, daemon=True).start()
        self.refresh_presence(reschedule=False)

    def remove_selected(self):
        sel = self.selected_accounts()
        if not sel:
            return
        names = ", ".join("@" + a["name"] for a in sel)
        if not messagebox.askyesno("Remove", f"Remove {names} from AltVault?\n\n"
                                   "This only deletes the saved login here, not the Roblox account.",
                                   parent=self.root):
            return
        self.accounts = [a for a in self.accounts if a not in sel]
        self.save()
        self.refresh()

    def edit_note(self, acc):
        note = simpledialog.askstring("Edit note", f"Note for @{acc['name']}:",
                                      initialvalue=acc.get("note", ""), parent=self.root)
        if note is not None:
            acc["note"] = note.strip()
            self.save()
            self.refresh()

    def move(self, acc, delta):
        i = self.accounts.index(acc)
        j = max(0, min(len(self.accounts) - 1, i + delta))
        self.accounts.insert(j, self.accounts.pop(i))
        self.save()
        self.refresh()

    def windows_of(self, acc):
        pids = {pid for pid, a in self.running.items() if a == acc["id"]}
        return [(hwnd, pid) for hwnd, pid, _ in roblox_windows() if pid in pids]

    def show_menu(self, event):
        row = self.tree.identify_row(event.y)
        if not row:
            return
        if row not in self.tree.selection():
            self.tree.selection_set(row)
        sel = self.selected_accounts()
        menu = themed_menu(self.root)
        if row.startswith(GROUP_PREFIX):
            group = row[len(GROUP_PREFIX):]
            menu.add_command(label=f"Join game ({len(sel)} accounts)", command=self.join_selected)
            menu.add_command(label="Join a player's server...", command=lambda: self.ask_join_player(sel))
            menu.add_cascade(label=f"FPS cap ({len(sel)} accounts)", menu=self.fps_menu(menu, sel))
            self.add_performance_items(menu, sel)
            menu.add_separator()
            menu.add_command(label="Rename group...", command=lambda: self.rename_group(group))
            menu.add_command(label="Ungroup (keeps the accounts)", command=lambda: self.set_group(sel, None))
            menu.tk_popup(event.x_root, event.y_root)
            return
        menu.add_command(label="Join game" if len(sel) == 1 else f"Join game ({len(sel)} accounts)",
                         command=self.join_selected)
        menu.add_command(label="Join a player's server...", command=lambda: self.ask_join_player(sel))
        if len(sel) == 1:
            acc = sel[0]
            wins = self.windows_of(acc)
            menu.add_command(label="Show Roblox window", state="normal" if wins else "disabled",
                             command=lambda: focus_window(wins[0][0]))
            menu.add_command(label="Close Roblox window", state="normal" if wins else "disabled",
                             command=lambda: [kill_process(pid) for _, pid in wins])
            p = self.presence.get(acc["id"]) or {}
            if p.get("userPresenceType") == 2 and p.get("gameId"):
                menu.add_command(label="Use this account's server for joining",
                                 command=lambda: self.use_server_of(p))
            menu.add_separator()
            menu.add_command(label="Copy username", command=lambda: self.copy(acc["name"]))
            menu.add_command(label="Open profile", command=lambda: webbrowser.open(
                f"https://www.roblox.com/users/{acc['id']}/profile"))
            menu.add_command(label="Turn anti-AFK on for this account" if self.afk_skipped(acc)
                             else "Turn anti-AFK off for this account",
                             command=lambda: self.toggle_afk_skip(acc))
            menu.add_cascade(label="FPS cap", menu=self.fps_menu(menu, sel))
            self.add_performance_items(menu, sel)
            menu.add_cascade(label="Group", menu=self.group_menu(menu, sel))
            menu.add_command(label="Edit note", command=lambda: self.edit_note(acc))
            menu.add_command(label="Log in again", command=lambda: AddAccountDialog(self, acc))
            menu.add_command(label="Move up", command=lambda: self.move(acc, -1))
            menu.add_command(label="Move down", command=lambda: self.move(acc, 1))
        else:
            menu.add_cascade(label=f"FPS cap ({len(sel)} accounts)", menu=self.fps_menu(menu, sel))
            self.add_performance_items(menu, sel)
            menu.add_cascade(label=f"Group ({len(sel)} accounts)", menu=self.group_menu(menu, sel))
        menu.add_separator()
        menu.add_command(label="Remove", command=self.remove_selected)
        menu.tk_popup(event.x_root, event.y_root)

    # ------------------------------------------------------------ FPS caps
    def fps_caps(self):
        caps = self.settings.get("fps_caps")
        if not isinstance(caps, dict):
            return {}
        lo, hi = FPS_RANGE
        return {k: v for k, v in caps.items() if isinstance(v, int) and lo <= v <= hi}

    def fps_cap_of(self, acc):
        return self.fps_caps().get(str(acc["id"]))

    def set_fps_cap(self, accounts, cap):
        caps = self.fps_caps()
        for acc in accounts:
            if cap:
                caps[str(acc["id"])] = cap
            else:
                caps.pop(str(acc["id"]), None)
        self.settings["fps_caps"] = caps
        save_settings(self.settings)
        self.refresh()
        who = f"@{accounts[0]['name']}" if len(accounts) == 1 else f"{len(accounts)} accounts"
        self.status.config(text=f"{who}: {f'{cap} FPS cap' if cap else 'no FPS cap'}, from their next join.")

    def ask_fps_cap(self, accounts):
        lo, hi = FPS_RANGE
        current = {self.fps_cap_of(a) for a in accounts}
        cap = simpledialog.askinteger("FPS cap", f"Frames per second ({lo}–{hi}):", parent=self.root,
                                      minvalue=lo, maxvalue=hi,
                                      initialvalue=current.pop() if len(current) == 1 and None not in current else 30)
        if cap:
            self.set_fps_cap(accounts, cap)

    def fps_menu(self, parent, accounts):
        menu = themed_menu(parent)
        caps = {self.fps_cap_of(a) for a in accounts}
        mark = caps.pop() if len(caps) == 1 else "mixed"
        menu.add_command(label=("✓ " if mark is None else "    ") + "No cap (your Roblox setting)",
                         command=lambda: self.set_fps_cap(accounts, None))
        for value in FPS_PRESETS:
            menu.add_command(label=("✓ " if mark == value else "    ") + f"{value} FPS",
                             command=lambda v=value: self.set_fps_cap(accounts, v))
        custom = mark not in (None, "mixed") and mark not in FPS_PRESETS
        menu.add_command(label=("✓ " if custom else "    ") + (f"Custom ({mark} FPS)..." if custom else "Custom..."),
                         command=lambda: self.ask_fps_cap(accounts))
        return menu

    # ------------------------------------------------------------ lowest graphics / mute
    def add_performance_items(self, menu, accounts):
        for key, label in (("low_graphics", "Lowest graphics"), ("muted", "Mute")):
            on = all(a["id"] in self.flagged(key) for a in accounts)
            menu.add_command(label=("✓ " if on else "") + label,
                             command=lambda k=key, t=label.lower(), o=on: self.set_flag(k, accounts, not o, t))

    def flagged(self, key):
        ids = self.settings.get(key)
        return {i for i in ids if isinstance(i, int)} if isinstance(ids, list) else set()

    def set_flag(self, key, accounts, on, label):
        ids = self.flagged(key)
        for acc in accounts:
            (ids.add if on else ids.discard)(acc["id"])
        self.settings[key] = sorted(ids)
        save_settings(self.settings)
        self.refresh()
        who = f"@{accounts[0]['name']}" if len(accounts) == 1 else f"{len(accounts)} accounts"
        self.status.config(text=f"{who}: {label} {'on' if on else 'off'}, from their next join.")

    def client_overrides(self, acc):
        """The Roblox settings this account should start with, beyond the user's own."""
        values = {}
        cap = self.fps_cap_of(acc)
        if cap:
            values["FramerateCap"] = str(cap)
        if acc["id"] in self.flagged("low_graphics"):
            values.update(LOW_GRAPHICS)
        if acc["id"] in self.flagged("muted"):
            values.update(MUTED)
        return values

    def client_values_in_use(self):
        """Every value AltVault writes for some account, by setting name."""
        used = {"FramerateCap": {str(c) for c in self.fps_caps().values()}}
        if self.flagged("low_graphics"):
            for name, value in LOW_GRAPHICS.items():
                used.setdefault(name, set()).add(value)
        if self.flagged("muted"):
            for name, value in MUTED.items():
                used.setdefault(name, set()).add(value)
        return used

    def client_state(self):
        """(own, written): the user's own values and the last values AltVault wrote. Settings saved
        by version 2.3, which only knew the FPS cap, are carried over."""
        own = self.settings.get("client_own")
        written = self.settings.get("client_written")
        if not isinstance(own, dict):
            own = {} if self.settings.get("fps_own") is None else {"FramerateCap": str(self.settings["fps_own"])}
        if not isinstance(written, dict):
            written = {} if self.settings.get("fps_written") is None else {"FramerateCap": str(self.settings["fps_written"])}
        self.settings["client_own"], self.settings["client_written"] = own, written
        return own, written

    def apply_client_settings(self, path, acc):
        """Writes the settings this account should start with: its own overrides, otherwise the user's
        own values. The user's own value of each setting is learned the first time, and again whenever
        the file holds a value AltVault didn't write. A client can save its settings back when it
        closes, so a value matching one AltVault uses for some account is never taken as the user's."""
        current = read_client_settings(path)
        own, written = self.client_state()
        used = self.client_values_in_use()
        for name, value in current.items():
            if name not in own or (value != written.get(name) and value not in used.get(name, ())):
                own[name] = value
        targets = {name: own[name] for name in current if name in own}
        targets.update({k: v for k, v in self.client_overrides(acc).items() if k in current})
        changes = {name: value for name, value in targets.items() if current.get(name) != value}
        if changes:
            write_client_settings(path, changes)
            written.update(changes)
        self.ui(save_settings, self.settings)

    def restore_client_settings(self, path):
        """Puts the user's own Roblox settings back once a client has started with its own."""
        own, written = self.client_state()
        current = read_client_settings(path)
        changes = {name: own[name] for name in current if name in own and current[name] != own[name]}
        if changes:
            write_client_settings(path, changes)
            written.update(changes)
            self.ui(save_settings, self.settings)

    def needs_client_settings(self):
        own, _ = self.client_state()
        return bool(self.fps_caps() or self.flagged("low_graphics") or self.flagged("muted") or own)

    def ask_join_player(self, accounts):
        if not accounts:
            return
        name = simpledialog.askstring("Join a player", "Their Roblox username:", parent=self.root)
        name = (name or "").strip()
        if not name:
            return
        viewer = next((a for a in accounts if a["id"] not in self.expired), None)
        if not viewer:
            messagebox.showerror("Join a player", "The selected accounts need to log in again first.", parent=self.root)
            return
        self.status.config(text=f"Finding {name}...")

        def work():
            try:
                place, job, found = server_of_player(viewer["cookie"], name)
            except RobloxError as e:
                message = str(e)   # e itself is cleared when the except block ends
                self.ui(lambda: (self.status.config(text=""),
                                 messagebox.showerror("Join a player", message, parent=self.root)))
                return

            def go():
                self.place_var.set(place)
                self.job_var.set(job)
                self.status.config(text=f"Joining @{found}'s server...")
                self.join_selected()
            self.ui(go)

        threading.Thread(target=work, daemon=True).start()

    def open_servers(self):
        text = self.place_var.get()
        if parse_private_server(text):
            messagebox.showinfo("Servers", "A private server link already points at one server, so there's "
                                "no list to browse. Just click Join selected.", parent=self.root)
            return
        place_id = parse_place_id(text)
        if not place_id:
            messagebox.showinfo("Servers", "Enter a game first (a place ID or roblox.com/games link).",
                                parent=self.root)
            return
        ServerBrowser(self, place_id)

    def use_server_of(self, presence):
        self.place_var.set(str(presence.get("rootPlaceId") or presence.get("placeId")))
        self.job_var.set(presence["gameId"])
        self.status.config(text="Server copied — select accounts and Join.")

    def copy(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status.config(text=f"Copied {text}.")

    # ------------------------------------------------------------ Roblox window actions
    def tile(self):
        order = {a["id"]: i for i, a in enumerate(self.accounts)}
        wins = sorted(roblox_windows(), key=lambda w: order.get(self.running.get(w[1]), len(order)))
        if not wins:
            self.status.config(text="No Roblox windows open.")
            return
        tile_windows([hwnd for hwnd, _, _ in wins])
        self.status.config(text=f"Tiled {len(wins)} window(s).")

    def close_all(self):
        if not roblox_windows():
            self.status.config(text="No Roblox windows open.")
            return
        if not messagebox.askyesno("Close all Roblox", "Close every Roblox window?", parent=self.root):
            return
        close_all_roblox()
        if self.multi_var.get():
            self.root.after(1000, lambda: self.toggle_multi(quiet=True))
        self.status.config(text="Closed all Roblox windows.")

    def toggle_multi(self, quiet=False):
        # The tick box is what the user wants; whether it's active yet is checked again on Join.
        if self.multi_var.get():
            if enable_multi_roblox():
                self.status.config(text="Multi-Roblox is on.")
            elif not quiet:
                self.status.config(text="Multi-Roblox turns on at your next Join (Roblox is open now).")
        else:
            disable_multi_roblox()
            self.status.config(text="Multi-Roblox is off.")
        self.save_settings()

    # ------------------------------------------------------------ anti-AFK
    # Event-driven to stay near zero power: one after() timer sleeps until the next ping is due, and
    # only while a due ping waits for the user to pause does it check once a second.
    def toggle_anti_afk(self, quiet=False):
        if self.afk_job:
            self.root.after_cancel(self.afk_job)
            self.afk_job = None
        self.afk_wait_started = None
        self.afk_last.clear()   # unknown how long they've been idle, so every window is due now
        self.afk_misses.clear()
        self.afk_alerted = False
        self.afk_on = self.anti_afk_var.get()
        log.info("Anti-AFK %s", "on" if self.afk_on else "off")
        if self.anti_afk_var.get():
            self.schedule_afk(3)
            if not quiet:
                self.status.config(text="Anti-AFK is on — open Roblox windows get a jump every 6–9 min.")
        else:
            self.status.config(text="Anti-AFK is off.")
        self.save_settings()

    def schedule_afk(self, seconds):
        if self.afk_job:
            self.root.after_cancel(self.afk_job)
        self.afk_job = self.root.after(int(seconds * 1000), self.afk_due)

    def afk_skip_ids(self):
        ids = self.settings.get("afk_skip")
        return {i for i in ids if isinstance(i, int)} if isinstance(ids, list) else set()

    def afk_skipped(self, acc):
        return acc["id"] in self.afk_skip_ids()

    def toggle_afk_skip(self, acc):
        skip = self.afk_skip_ids()
        skip ^= {acc["id"]}
        self.settings["afk_skip"] = sorted(skip)
        save_settings(self.settings)
        self.refresh()
        self.status.config(text=f"Anti-AFK {'off' if acc['id'] in skip else 'on'} for @{acc['name']}.")

    def afk_due(self):
        """Timer callback for the next ping. Whatever goes wrong, it always leaves a timer running
        and says so in the log; Anti-AFK must never stop silently."""
        if self.afk_job:   # no-op when the timer fired this; stops a second timer otherwise
            self.root.after_cancel(self.afk_job)
            self.afk_job = None
        if not self.anti_afk_var.get():
            return
        try:
            self._afk_due()
        except Exception:
            log.exception("Anti-AFK scheduling failed; trying again in a minute")
            self.afk_busy = False
            self.schedule_afk(60)

    def _afk_due(self):
        now = time.monotonic()
        if self.afk_busy:
            # Watchdog: a pass takes ~1 s per window. If one is still running after 90 s it's stuck
            # (the worker can't be killed, but it no longer blocks new pings).
            if now - self.afk_busy_since > AFK_STUCK:
                log.warning("Anti-AFK: a ping got stuck at '%s'; starting a fresh one", afk_step["step"])
                self.afk_busy = False
            else:
                self.schedule_afk(5)
                return
        if self.joining:   # don't fight a join for focus
            self._afk_waiting("a join to finish")
            self.schedule_afk(5)
            return
        skip = self.afk_skip_ids()
        windows = [hwnd for hwnd, pid, _ in roblox_windows() if self.running.get(pid) not in skip]
        for gone in (set(self.afk_last) | set(self.afk_misses)) - set(windows):
            self.afk_last.pop(gone, None)
            self.afk_misses.pop(gone, None)
        ages = {h: now - self.afk_last[h] if h in self.afk_last else math.inf for h in windows}
        # A window never pinged (just opened, or Anti-AFK just turned on) counts as due. Windows
        # pinged a moment ago are left out, so a retry for one window doesn't disturb the rest.
        targets = [h for h in windows if not _user32.IsIconic(h) and (ages[h] >= AFK_MIN_GAP or h in self.afk_misses)]
        if not targets:
            if not windows:
                log.info("Anti-AFK: no Roblox windows to ping; checking again in a few minutes")
            self.afk_wait_started = None
            self.schedule_afk(self.afk_next_delay(now))
            return
        oldest = max(ages[h] for h in targets)
        if self.afk_wait_started is None:
            self.afk_wait_started = now
        if (oldest < AFK_URGENT and idle_seconds() < AFK_IDLE
                and now - self.afk_wait_started < AFK_MAX_WAIT):
            self._afk_waiting("you to pause using the mouse/keyboard")
            self.schedule_afk(1)
            return
        self.afk_wait_started = None
        self.afk_waiting_for = None

        hwnds = targets + [h for h in windows if _user32.IsIconic(h)]   # minimised ones are only reported
        vk = AFK_KEYS.get(str(self.settings.get("afk_key", "space")).lower(), AFK_KEYS["space"])
        self.afk_busy, self.afk_busy_since = True, now
        self.afk_pass_id += 1
        pass_id = self.afk_pass_id

        def work():
            try:
                result = anti_afk_pass(hwnds, vk)
            except Exception:
                log.exception("Anti-AFK pass failed")
                result = ([], [], list(targets), {h: "pass failed" for h in targets}, None)
            self.ui(self.afk_finished, *result, pass_id, oldest)

        threading.Thread(target=work, daemon=True).start()

    def _afk_waiting(self, reason):
        """Logs (once) when a due ping has been held up for over a minute."""
        if self.afk_waiting_for != reason:
            self.afk_waiting_for, self.afk_waiting_since = reason, time.monotonic()
        elif time.monotonic() - self.afk_waiting_since > 60 and not self.afk_waiting_logged:
            log.info("Anti-AFK: ping waiting for %s", reason)
            self.afk_waiting_logged = True

    def afk_next_delay(self, now):
        """Seconds until the next pass: soon if a window needs a retry, else when the window that
        has gone longest without a ping reaches a fresh random interval."""
        if self.afk_misses:
            return AFK_RETRY_SLOW if max(self.afk_misses.values()) >= AFK_SLOW_AFTER else AFK_RETRY
        if not self.afk_last:
            return random.uniform(*AFK_INTERVAL)
        return max(5, min(self.afk_last.values()) + random.uniform(*AFK_INTERVAL) - now)

    def afk_window_name(self, hwnd):
        pid = wt.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        acc = self.account(self.running.get(pid.value))
        return "@" + acc["name"] if acc else f"window {hwnd}"

    def afk_finished(self, pinged, minimised, missed, reasons=None, blocker=None, pass_id=None, oldest=None):
        if pass_id is not None and pass_id != self.afk_pass_id:
            log.info("Anti-AFK: a ping that had got stuck finished late (pinged %d)", len(pinged))
            return   # a newer pass has taken over; its result is the one that counts
        self.afk_busy = False
        self.afk_waiting_logged = False
        now = time.monotonic()
        for h in pinged:
            self.afk_last[h] = now
            self.afk_misses.pop(h, None)
        for h in missed:
            self.afk_misses[h] = self.afk_misses.get(h, 0) + 1
        parts = [f"pinged {len(pinged)}"]
        if minimised:
            parts.append(f"{len(minimised)} minimised (restore to keep them safe)")
        if missed:
            parts.append(f"{len(missed)} missed, retrying")
        details = "; ".join(f"{self.afk_window_name(h)} {(reasons or {}).get(h, 'missed')}"
                            f" ({self.afk_misses[h]}x)" for h in missed)
        if blocker:
            details += f"; in front: {blocker}"
        gap = f", longest gap {oldest / 60:.1f} min" if oldest not in (None, math.inf) else ""
        self.afk_alert(missed, blocker, oldest)
        if not self.anti_afk_var.get():
            log.info("Anti-AFK: %s%s%s", ", ".join(parts), gap, f" [{details}]" if details else "")
            return
        delay = self.afk_next_delay(now)
        self.schedule_afk(delay)
        nxt = time.strftime("%H:%M", time.localtime(time.time() + delay))
        log.info("Anti-AFK: %s%s; next ping ~%s%s", ", ".join(parts), gap, nxt,
                 f" [{details}]" if details else "")
        self.status.config(text=f"Anti-AFK {time.strftime('%H:%M')}: " + ", ".join(parts) + f" · next ~{nxt}")

    def afk_alert(self, missed, blocker, oldest):
        """One tray alert per stuck spell: once a window has missed AFK_ALERT_AFTER pings in a row,
        say which accounts and what's in the way. A second message says when it's working again."""
        stuck = [h for h in missed if self.afk_misses.get(h, 0) >= AFK_ALERT_AFTER]
        if stuck and not self.afk_alerted:
            self.afk_alerted = True
            names = ", ".join(self.afk_window_name(h) for h in stuck)
            minutes = "" if oldest in (None, math.inf) else \
                f" They'll be idle-kicked in about {max(0, round(20 - oldest / 60))} min."
            text = f"Can't reach {names}. In front: {blocker or 'unknown'}.{minutes}"
            log.warning("Anti-AFK blocked: %s", text)
            if self.tray:
                self.tray.notify("Anti-AFK is blocked", text, warning=True)
        elif not self.afk_misses and self.afk_alerted:
            self.afk_alerted = False
            log.info("Anti-AFK: working again")
            if self.tray:
                self.tray.notify("Anti-AFK is working again", "Every Roblox window was pinged.")

    def ensure_multi(self):
        """Makes Multi-Roblox active before a join. Returns False if the user cancelled."""
        if not self.multi_var.get() or enable_multi_roblox():
            return True
        if not messagebox.askyesno(
                "Multi-Roblox", "Roblox was opened before Multi-Roblox could start, so new windows "
                "would close it.\n\nClose all Roblox windows now and continue?", parent=self.root):
            return False
        if close_roblox_and_enable_multi():
            self.status.config(text="Multi-Roblox is on.")
            return True
        messagebox.showerror("Multi-Roblox", "Couldn't turn on Multi-Roblox. Close Roblox from "
                             "Task Manager, then Join again.", parent=self.root)
        return False

    # ------------------------------------------------------------ joining
    def join_selected(self):
        if self.joining:
            return
        sel = self.selected_accounts()
        if not sel:
            messagebox.showinfo("Join", "Select one or more accounts first (Ctrl+click for several).",
                                parent=self.root)
            return
        private = parse_private_server(self.place_var.get())
        if private and not private.get("share_code") and not private.get("place_id"):
            messagebox.showerror("Join", "That private server link doesn't say which game it's for. Copy the "
                                 "link again from the private server's menu on roblox.com.", parent=self.root)
            return
        place_id = private.get("place_id") if private else parse_place_id(self.place_var.get())
        if not private and not place_id:
            messagebox.showerror("Join", "Enter a place ID, a roblox.com/games/... link or a private server link.",
                                 parent=self.root)
            return
        job_id = "" if private else parse_job_id(self.job_var.get())
        if job_id is None:
            messagebox.showerror("Join", "The server / Job ID should look like "
                                 "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx (or leave it empty).", parent=self.root)
            return
        expired = [a for a in sel if a["id"] in self.expired]
        if expired:
            messagebox.showerror("Join", "These accounts need to log in again first (right-click > Log in again):\n" +
                                 "\n".join("@" + a["name"] for a in expired), parent=self.root)
            return
        if len(sel) > 1 and not self.multi_var.get():
            if not messagebox.askyesno("Join", "Multi-Roblox is off, so each launch will close the "
                                       "previous window. Continue anyway?", parent=self.root):
                return
        if not self.ensure_multi():
            return
        self.save_settings()
        self.joining = True
        self.join_btn.config(state="disabled", text="Joining...")
        # Everyone joining through a private server link ends up in that private server anyway.
        same_server = self.same_server_var.get() and len(sel) > 1 and not private
        if private and self.job_var.get().strip():
            self.status.config(text="Joining a private server, so the Job ID box is ignored.")
        threading.Thread(target=self.join_worker, args=(sel, place_id, job_id, same_server, private),
                         daemon=True).start()

    def resolve_private(self, accounts, private):
        """(place_id, link_code) for a private server link, resolving share links with the first
        account that's still logged in."""
        if "link_code" in private:
            return private["place_id"], private["link_code"]
        self.set_status("Opening the private server link...")
        for acc in accounts:
            try:
                return resolve_share_link(acc["cookie"], private["share_code"])
            except CookieExpired:
                self.expired.add(acc["id"])
        raise RobloxError("Every selected account needs to log in again before it can open the link.")

    def join_worker(self, accounts, place_id, job_id, same_server, private=None):
        problems = []
        link_code = ""
        fps_file = None
        if self.needs_client_settings():
            fps_file = roblox_settings_file()
            if fps_file is None:
                problems.append("Couldn't find Roblox's settings file, so FPS caps, graphics and mute weren't applied. "
                                "Start Roblox once normally, then try again.")
        try:
            if private:
                try:
                    place_id, link_code = self.resolve_private(accounts, private)
                except RobloxError as e:
                    problems.append(str(e))
                    return
            for i, acc in enumerate(accounts):
                last = i == len(accounts) - 1
                self.set_status(f"Launching @{acc['name']} ({i + 1}/{len(accounts)})...")
                before = {pid for _, pid, _ in roblox_windows()}
                if fps_file:
                    try:
                        self.apply_client_settings(fps_file, acc)
                    except (OSError, ValueError) as e:
                        log.warning("Couldn't apply per-account Roblox settings: %s", e)
                        problems.append(f"Couldn't apply per-account settings ({e}), so accounts start with your normal Roblox settings.")
                        fps_file = None
                try:
                    launch(acc["cookie"], place_id, job_id, link_code)
                except CookieExpired:
                    self.expired.add(acc["id"])
                    problems.append(f"@{acc['name']}: login expired — right-click > Log in again.")
                    continue
                except (RobloxError, OSError) as e:
                    log.warning("Launch of %s failed: %s", acc["name"], e)
                    problems.append(f"@{acc['name']}: {e}")
                    continue
                pid = self.wait_for_new_window(before)
                if pid is None:
                    problems.append(f"@{acc['name']}: Roblox didn't open a window within {WINDOW_TIMEOUT}s.")
                    continue
                self.ui(self.running.__setitem__, pid, acc["id"])
                time.sleep(3)
                if fps_file:   # the client has read its cap by now
                    try:
                        self.restore_client_settings(fps_file)
                    except (OSError, ValueError) as e:
                        log.warning("Couldn't restore your own Roblox settings: %s", e)
                if before - {p for _, p, _ in roblox_windows()}:
                    problems.append("An earlier Roblox window closed when this one opened, so "
                                    "Multi-Roblox isn't working with this Roblox version.")
                if i == 0:
                    threading.Thread(target=self.remember_place, args=(acc["cookie"], place_id),
                                     daemon=True).start()
                if same_server and not job_id and not link_code and not last:
                    self.set_status(f"Waiting for @{acc['name']} to reach a server...")
                    job_id = self.wait_for_server(acc, place_id) or ""
                    if not job_id:
                        problems.append(f"Couldn't see which server @{acc['name']} joined, so the other "
                                        "accounts went to random servers. (Their 'Joins' privacy "
                                        "setting may hide it.)")
                        same_server = False
        except Exception as e:
            log.exception("Join failed")
            problems.append(f"Unexpected error: {e}")
        finally:
            if fps_file:   # never leave an alt's cap as the user's own Roblox setting
                try:
                    self.restore_client_settings(fps_file)
                except (OSError, ValueError) as e:
                    log.warning("Couldn't restore your own Roblox settings: %s", e)
            self.ui(self.join_finished, problems)

    def wait_for_new_window(self, before):
        deadline = time.time() + WINDOW_TIMEOUT
        while time.time() < deadline:
            for _, pid, _ in roblox_windows():
                if pid not in before:
                    return pid
            time.sleep(0.5)
        return None

    def wait_for_server(self, acc, place_id):
        deadline = time.time() + SERVER_TIMEOUT
        while time.time() < deadline:
            time.sleep(3)
            try:
                p = get_presence(acc["cookie"], acc["id"])
            except (RobloxError, ValueError, KeyError, IndexError):
                continue
            self.presence[acc["id"]] = p
            places = {str(p.get("placeId")), str(p.get("rootPlaceId"))}
            if p.get("userPresenceType") == 2 and p.get("gameId") and place_id in places:
                return p["gameId"]
        return None

    def remember_place(self, cookie, place_id):
        name = get_place_name(cookie, place_id)
        self.ui(self.add_recent, place_id, name)

    def join_finished(self, problems):
        self.joining = False
        self.join_btn.config(state="normal", text="Join selected")
        self.status.config(text="Done." if not problems else "Done, with problems.")
        self.refresh()
        if self.auto_tile_var.get() and len(roblox_windows()) > 1:
            self.tile()
            # Roblox can resize its window once the game finishes loading, so tile again after that.
            self.root.after(5000, self.tile)
        self.root.after(8000, lambda: self.refresh_presence(reschedule=False))
        if problems:
            messagebox.showwarning("Join", "\n\n".join(problems), parent=self.root)

    def on_close(self):
        """The window's X: hide to the tray so Anti-AFK keeps working. Quit is in the tray menu."""
        self.settings["geometry"] = self.root.geometry()
        self.save_settings()
        if not self.tray:
            self.root.destroy()
            return
        self.root.withdraw()
        if not self.settings.get("tray_hint_shown"):
            self.tray.notify("AltVault is still running",
                             "Anti-AFK and your accounts keep working. Click the tray icon to open AltVault, "
                             "or right-click it to quit.")
            self.settings["tray_hint_shown"] = True
            save_settings(self.settings)

    def show_from_tray(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def tray_toggle_afk(self):
        self.anti_afk_var.set(not self.anti_afk_var.get())
        self.toggle_anti_afk()

    def quit_app(self):
        if self.root.state() != "withdrawn":
            self.settings["geometry"] = self.root.geometry()
        self.save_settings()
        if self.tray:
            self.tray.remove()
        self.root.destroy()


def main():
    try:
        migrate_legacy_data()
    except OSError as e:
        # Starting anyway would show an empty account list, so stop and say why.
        _user32.MessageBoxW(None, f"Couldn't move your saved accounts from\n{LEGACY_APP_DIR}\nto\n{APP_DIR}"
                            f"\n\n{e.strerror}. Close PyRAM if it's still open, then start {APP_TITLE} again.",
                            APP_TITLE, 0x10)
        return
    os.makedirs(APP_DIR, exist_ok=True)
    logging.basicConfig(filename=LOG_FILE, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    log.info("AltVault %s starting", APP_VERSION)
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    instance = acquire_instance_lock()
    if instance is None:
        return
    try:   # own taskbar identity, so Windows shows AltVault's icon instead of grouping it under Python
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AltVault.AccountManager")
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    try:
        root.iconbitmap(default=app_icon_path())   # default= applies to every dialog too
    except (OSError, tk.TclError):
        log.warning("Couldn't set the window icon")

    def report(exc, val, tb):
        log.error("Unhandled UI error", exc_info=(exc, val, tb))
        # Only interrupt with a dialog while the user is in AltVault. An error from a background
        # timer must never pop up over Roblox or whatever else they're doing.
        if _user32.GetForegroundWindow() == _user32.GetParent(root.winfo_id()):
            messagebox.showerror(APP_TITLE, f"Something went wrong:\n{val}\n\nDetails are in {LOG_FILE}")
    root.report_callback_exception = report

    try:
        accounts = load_accounts()
    except (OSError, ValueError) as e:
        # Don't start with an empty list, or the next save would wipe the real file.
        log.exception("Couldn't read accounts")
        messagebox.showerror(APP_TITLE, f"Couldn't read saved accounts:\n{e}\n\nFile: {DATA_FILE}")
        root.destroy()
        return
    threading.Thread(target=remove_stale_login_profiles, daemon=True).start()
    if os.path.exists(DATA_FILE):
        threading.Thread(target=restrict_to_owner, args=(DATA_FILE,), daemon=True).start()
    app = App(root, accounts)
    try:
        app.tray = TrayIcon(root, app_icon_path(), app.show_from_tray, app.tray_toggle_afk, app.quit_app,
                            lambda: app.afk_on)
    except (OSError, AttributeError):
        log.exception("Couldn't create the tray icon; closing the window will quit")
    root.mainloop()
    if app.tray:
        app.tray.remove()
    for login in list(_open_logins):   # never leave a login browser or its profile behind
        login.close()
    disable_multi_roblox()


if __name__ == "__main__":
    main()
