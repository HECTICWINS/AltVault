"""AltVault - a Roblox account manager that runs on signed Python (no unsigned .exe),
so it works with Windows Smart App Control turned on.

Everything lives in %LOCALAPPDATA%\\AltVault. Account cookies are encrypted with Windows
DPAPI (only your Windows user on this PC can decrypt them) and are never written to the log.
"""
import base64
import collections
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
from tkinter import filedialog, font as tkfont, ttk

APP_TITLE = "AltVault"
APP_VERSION = "2.7.0"
GITHUB_URL = "https://github.com/HECTICWINS/AltVault"
APP_DIR = os.path.join(os.environ["LOCALAPPDATA"], "AltVault")
DATA_FILE = os.path.join(APP_DIR, "accounts.dat")
SETTINGS_FILE = os.path.join(APP_DIR, "settings.json")
AVATAR_DIR = os.path.join(APP_DIR, "avatars")
ICON_DIR = os.path.join(APP_DIR, "game-icons")
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


WM_SETTEXT = 0x000C


def set_window_title(hwnd, title):
    """Renames a window, giving up after half a second if its program has stopped answering (plain
    SetWindowTextW waits for it indefinitely, which would freeze AltVault along with a frozen Roblox)."""
    buf = ctypes.create_unicode_buffer(title)
    result = ctypes.c_size_t()
    _user32.SendMessageTimeoutW(hwnd, WM_SETTEXT, 0, ctypes.cast(buf, ctypes.c_void_p).value or 0,
                                SMTO_ABORTIFHUNG, 500, ctypes.byref(result))


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


# Hide from taskbar: a tool window gets no taskbar button (and no Alt+Tab entry). The taskbar only
# looks at the style when a window is shown, so the window is hidden and shown again around the
# change, without activating it. Changing the style waits on Roblox, so frozen windows are skipped.
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
SW_HIDE, SW_SHOWNA = 0, 8
_user32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, wt.LONG]
_user32.ShowWindowAsync.argtypes = [wt.HWND, ctypes.c_int]


def on_taskbar(hwnd):
    return not _user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW


def set_on_taskbar(hwnd, shown):
    """Puts a window on the taskbar or takes it off. Returns False if it was frozen and left alone."""
    if not responsive(hwnd):
        return False
    style = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    new = style & ~WS_EX_TOOLWINDOW if shown else (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
    if new != style:
        _user32.ShowWindowAsync(hwnd, SW_HIDE)
        _user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ctypes.c_long(new & 0xFFFFFFFF).value)
        _user32.ShowWindowAsync(hwnd, SW_SHOWNA)
    return True


# ---------------------------------------------------------------- live view
# The Live page shows Roblox windows through DWM thumbnails: Windows' compositor draws a scaled,
# live copy of each window straight into AltVault's window, the same way taskbar previews work.
# Nothing is captured or copied, Roblox isn't touched, and it costs nothing once the page is left.

class DWM_THUMBNAIL_PROPERTIES(ctypes.Structure):
    _fields_ = [("dwFlags", wt.DWORD), ("rcDestination", wt.RECT), ("rcSource", wt.RECT),
                ("opacity", wt.BYTE), ("fVisible", wt.BOOL), ("fSourceClientAreaOnly", wt.BOOL)]


DWM_TNP_RECTDESTINATION, DWM_TNP_OPACITY, DWM_TNP_VISIBLE, DWM_TNP_SOURCECLIENTAREAONLY = 0x1, 0x4, 0x8, 0x10
_dwmapi.DwmRegisterThumbnail.argtypes = [wt.HWND, wt.HWND, ctypes.POINTER(ctypes.c_void_p)]
_dwmapi.DwmRegisterThumbnail.restype = ctypes.c_long
_dwmapi.DwmUnregisterThumbnail.argtypes = [ctypes.c_void_p]
_dwmapi.DwmUpdateThumbnailProperties.argtypes = [ctypes.c_void_p, ctypes.POINTER(DWM_THUMBNAIL_PROPERTIES)]
_dwmapi.DwmUpdateThumbnailProperties.restype = ctypes.c_long
_user32.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
_user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]


def register_thumbnail(dest, src):
    """A handle for a live copy of window `src` drawn inside top-level window `dest`, or None."""
    thumb = ctypes.c_void_p()
    if _dwmapi.DwmRegisterThumbnail(dest, src, ctypes.byref(thumb)) != 0 or not thumb.value:
        return None
    return thumb.value


def place_thumbnail(thumb, rect):
    """Shows the thumbnail in rect (left, top, right, bottom, in dest's client pixels)."""
    props = DWM_THUMBNAIL_PROPERTIES(
        DWM_TNP_RECTDESTINATION | DWM_TNP_OPACITY | DWM_TNP_VISIBLE | DWM_TNP_SOURCECLIENTAREAONLY,
        wt.RECT(*rect), wt.RECT(), 255, True, True)
    return _dwmapi.DwmUpdateThumbnailProperties(thumb, ctypes.byref(props)) == 0


def unregister_thumbnail(thumb):
    _dwmapi.DwmUnregisterThumbnail(thumb)


def client_size(hwnd):
    r = wt.RECT()
    return (r.right, r.bottom) if _user32.GetClientRect(hwnd, ctypes.byref(r)) else (0, 0)


def client_origin(hwnd):
    pt = wt.POINT(0, 0)
    _user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y


def fit_rect(src_w, src_h, x, y, w, h):
    """The biggest rect with the source's shape, centred in (x, y, w, h): (left, top, right, bottom)."""
    if src_w <= 0 or src_h <= 0 or w <= 0 or h <= 0:
        return x, y, x + max(w, 0), y + max(h, 0)
    scale = min(w / src_w, h / src_h)
    fw, fh = round(src_w * scale), round(src_h * scale)
    left, top = x + (w - fw) // 2, y + (h - fh) // 2
    return left, top, left + fw, top + fh


def live_grid(n, width, height, header, gap, aspect):
    """(cols, rows, tile_w, tile_h) for n tiles as big as fits in width x height, no scrolling.
    A tile is a `header`-high title row over a body with the windows' shape (aspect = w / h)."""
    best = None
    for cols in range(1, n + 1):
        rows = math.ceil(n / cols)
        cell_w = (width - gap * (cols - 1)) / cols
        cell_h = (height - gap * (rows - 1)) / rows
        body_w = min(cell_w, (cell_h - header) * aspect)
        if best is None or body_w > best[0]:
            best = (body_w, cols, rows)
    body_w, cols, rows = best
    body_w = max(int(body_w), 1)
    return cols, rows, body_w, int(body_w / aspect) + header


class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD)] + \
               [(name, ctypes.c_size_t) for name in (
                   "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                   "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]


class PROCESS_MEMORY_COUNTERS_EX2(ctypes.Structure):
    """Adds the private working set: the 'Memory' figure Task Manager shows (Windows 10 21H2+)."""
    _fields_ = PROCESS_MEMORY_COUNTERS._fields_ + [
        ("PrivateUsage", ctypes.c_size_t), ("PrivateWorkingSetSize", ctypes.c_size_t),
        ("SharedCommitUsage", ctypes.c_ulonglong)]


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", wt.DWORD), ("dwMemoryLoad", wt.DWORD)] + \
               [(name, ctypes.c_ulonglong) for name in (
                   "ullTotalPhys", "ullAvailPhys", "ullTotalPageFile", "ullAvailPageFile",
                   "ullTotalVirtual", "ullAvailVirtual", "ullAvailExtendedVirtual")]


PROCESS_VM_READ = 0x0010
_kernel32.GetProcessTimes.argtypes = [wt.HANDLE] + [ctypes.POINTER(wt.FILETIME)] * 4
_kernel32.K32GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD]
_kernel32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MEMORYSTATUSEX)]


def process_usage(pid):
    """(CPU seconds used so far, bytes of RAM in use) for a process. Either is None if Windows
    won't say (Roblox may refuse the memory query); None overall if the process can't be opened."""
    handle = (_kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, False, pid)
              or _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid))
    if not handle:
        return None
    try:
        times = [wt.FILETIME() for _ in range(4)]
        cpu = None
        if _kernel32.GetProcessTimes(handle, *map(ctypes.byref, times)):
            kernel, user = ((t.dwHighDateTime << 32 | t.dwLowDateTime) for t in times[2:])
            cpu = (kernel + user) / 1e7
        mem = PROCESS_MEMORY_COUNTERS_EX2(cb=ctypes.sizeof(PROCESS_MEMORY_COUNTERS_EX2))
        if _kernel32.K32GetProcessMemoryInfo(handle, ctypes.byref(mem), ctypes.sizeof(mem)):
            return cpu, mem.PrivateWorkingSetSize   # matches Task Manager
        old = PROCESS_MEMORY_COUNTERS(cb=ctypes.sizeof(PROCESS_MEMORY_COUNTERS))   # older Windows
        ok = _kernel32.K32GetProcessMemoryInfo(handle, ctypes.byref(old), ctypes.sizeof(old))
        return cpu, old.WorkingSetSize if ok else None
    finally:
        _kernel32.CloseHandle(handle)


def memory_status():
    """(free, total) physical RAM in bytes."""
    status = MEMORYSTATUSEX(dwLength=ctypes.sizeof(MEMORYSTATUSEX))
    _kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    return status.ullAvailPhys, status.ullTotalPhys


class CpuMeter:
    """Turns CPU seconds per process into a share of the whole PC, like Task Manager shows."""

    def __init__(self):
        self.last = {}   # pid -> (monotonic time, cpu seconds)

    def percent(self, samples, now=None):
        """samples: {pid: cpu seconds}. Returns the total % since the last call, or None at first."""
        now = time.monotonic() if now is None else now
        total, known = 0.0, False
        for pid, cpu in samples.items():
            if pid in self.last:
                then, before = self.last[pid]
                if now > then:
                    total += max(cpu - before, 0) / (now - then)
                    known = True
        self.last = {pid: (now, cpu) for pid, cpu in samples.items()}
        return 100 * total / (os.cpu_count() or 1) if known else None


_kernel32.GetSystemTimes.argtypes = [ctypes.POINTER(wt.FILETIME)] * 3


def system_times():
    """(idle, busy) CPU seconds for the whole PC since it started."""
    idle, kernel, user = (wt.FILETIME() for _ in range(3))
    _kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
    i, k, u = ((t.dwHighDateTime << 32 | t.dwLowDateTime) / 1e7 for t in (idle, kernel, user))
    return i, k + u - i   # kernel time includes idle time


class PcMeter:
    """The whole PC's CPU use between two calls, as a percentage (None on the first call)."""

    def __init__(self):
        self.last = None

    def percent(self, times=None):
        idle, busy = times or system_times()
        last, self.last = self.last, (idle, busy)
        if not last or idle + busy <= sum(last):
            return None
        return 100 * (busy - last[1]) / (idle + busy - sum(last))


HISTORY_FILE = os.path.join(APP_DIR, "history.json")
HISTORY_HOURS = 24 * 7


class History:
    """Hour-by-hour totals for the Stats and Pulse pages, kept for a week in history.json.
    Only numbers are stored: never names, cookies or window titles."""

    def __init__(self, path=None):
        self.path = path or HISTORY_FILE
        self.hours = {}   # hour number (Unix time // 3600) -> {field: total}
        self.dirty = False
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self.hours = {int(h): {k: v for k, v in b.items() if isinstance(v, (int, float))}
                          for h, b in data.items() if isinstance(b, dict)}
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    @staticmethod
    def hour(now=None):
        return int((time.time() if now is None else now) // 3600)

    def add(self, now=None, **amounts):
        bucket = self.hours.setdefault(self.hour(now), {})
        for field, n in amounts.items():
            if n is not None:
                bucket[field] = bucket.get(field, 0) + n
        self.dirty = True

    def series(self, field, hours, now=None, per=None):
        """Oldest-first values of `field` for each of the last `hours` hours. With per='samples',
        each is an average (field total / samples that hour), None where there were none."""
        end = self.hour(now)
        out = []
        for h in range(end - hours + 1, end + 1):
            bucket = self.hours.get(h, {})
            if per:
                n = bucket.get(per, 0)
                out.append(bucket.get(field, 0) / n if n else None)
            else:
                out.append(bucket.get(field, 0))
        return out

    def sample(self, now=None, **values):
        """Adds readings to be averaged: each value goes into its field, and 1 into field_n."""
        known = {k: v for k, v in values.items() if v is not None}
        self.add(now, **known, **{k + "_n": 1 for k in known})

    def total(self, field, hours, now=None):
        return sum(self.series(field, hours, now))

    def save(self, now=None):
        if not self.dirty:
            return
        oldest = self.hour(now) - HISTORY_HOURS
        self.hours = {h: b for h, b in self.hours.items() if h > oldest}
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({str(h): b for h, b in sorted(self.hours.items())}, f)
        os.replace(tmp, self.path)
        self.dirty = False


def percent_of(part, whole):
    return None if not whole else 100 * part / whole


def short_number(n):
    """1234 -> '1,234', 98765 -> '98.8K', 2500000 -> '2.5M'."""
    for above, size, suffix in ((1e12, 1e12, "T"), (1e9, 1e9, "B"), (1e6, 1e6, "M"), (1e4, 1e3, "K")):
        if abs(n) >= above:
            return f"{n / size:.1f}".rstrip("0").rstrip(".") + suffix
    return f"{n:,.0f}" if n == int(n) else f"{n:,.1f}"


def per_minute(value, unit):
    """One alt's hatch speed typed as eggs/min, eggs/hour or seconds per egg, as eggs per minute."""
    return {"min": value, "hour": value / 60, "sec": 60 / value}[unit]


def hatch_totals(per_minute, alts):
    """Eggs per (minute, hour, day) for `alts` alts that each hatch `per_minute`."""
    rate = max(per_minute, 0) * max(alts, 0)
    return rate, rate * 60, rate * 1440


SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79
DEFAULT_GEOMETRY = "1200x740"


LAYOUT_VERSION = 2   # 2.7: sidebar pages and the Live grid


def work_area():
    area = wt.RECT()
    _user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(area), 0)
    return area.right - area.left, area.bottom - area.top


def min_size(scale):
    w, h = work_area()
    return min(int(900 * scale), w), min(int(640 * scale), h)


def fit_to_screen(geometry):
    """Shrinks a 'WxH[+X+Y]' geometry to the main screen's work area, centring a size-only one."""
    m = re.fullmatch(r"(\d+)x(\d+)(.*)", geometry)
    if not m:
        return geometry
    aw, ah = work_area()
    w, h = min(int(m.group(1)), aw), min(int(m.group(2)), ah - 40)
    return f"{w}x{h}{m.group(3) or f'+{(aw - w) // 2}+{max((ah - h) // 2 - 20, 0)}'}"


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
ICON_VERSION = 2


def _png(size, rgba, height=None):
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    height = height or size
    stride = size * 4
    raw = b"".join(b"\0" + rgba[y * stride:(y + 1) * stride] for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, height, 8, 6, 0, 0, 0))
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
    top, bottom = (0x93, 0x6B, 0xFF), (0x4A, 0x27, 0xA6)
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


DWMWA_WINDOW_CORNER_PREFERENCE, DWMWA_BORDER_COLOR, DWMWA_CAPTION_COLOR, DWMWA_TEXT_COLOR = 33, 34, 35, 36
DWMWCP_ROUND = 2


def _colorref(color):
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return r | g << 8 | b << 16


def dark_title_bar(win):
    """Dark title bar in the app's own colours with a hairline border and rounded corners, so the
    window frame blends into the app. Windows 11 honours all of it; older Windows ignores what it
    doesn't know and keeps the plain dark title bar."""
    win.update_idletasks()
    hwnd = _user32.GetParent(win.winfo_id())
    for attribute, value in ((DWMWA_USE_IMMERSIVE_DARK_MODE, 1), (DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND),
                             (DWMWA_CAPTION_COLOR, _colorref(C["bg"])), (DWMWA_TEXT_COLOR, _colorref(C["muted"])),
                             (DWMWA_BORDER_COLOR, _colorref(C["line"]))):
        v = ctypes.c_int(value)
        try:
            _dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(v), ctypes.sizeof(v))
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


# ---------------------------------------------------------------- transfer file
# accounts.dat only opens for this Windows user on this PC, which is what keeps it safe. To move
# accounts elsewhere they're exported to a .altvault file locked with a password instead: scrypt
# turns the password into a key (slow on purpose, to make guessing expensive), and AES-256-GCM
# from Windows' own crypto library (CNG) encrypts the accounts and detects any tampering. The
# header (magic, version, salt, nonce) is authenticated too, so it can't be swapped either.
EXPORT_MAGIC = b"ALTVAULT"
EXPORT_VERSION = 1
EXPORT_EXT = ".altvault"
SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1, "maxmem": 64 * 1024 * 1024, "dklen": 32}
MIN_PASSWORD = 8
STATUS_AUTH_TAG_MISMATCH = 0xC000A002


class BCRYPT_AUTH_INFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.ULONG), ("dwInfoVersion", wt.ULONG), ("pbNonce", ctypes.c_void_p),
                ("cbNonce", wt.ULONG), ("pbAuthData", ctypes.c_void_p), ("cbAuthData", wt.ULONG),
                ("pbTag", ctypes.c_void_p), ("cbTag", wt.ULONG), ("pbMacContext", ctypes.c_void_p),
                ("cbMacContext", wt.ULONG), ("cbAAD", wt.ULONG), ("cbData", ctypes.c_ulonglong),
                ("dwFlags", wt.ULONG)]


_bcrypt = ctypes.WinDLL("bcrypt")
_bcrypt.BCryptOpenAlgorithmProvider.argtypes = [ctypes.POINTER(ctypes.c_void_p), wt.LPCWSTR, wt.LPCWSTR, wt.ULONG]
_bcrypt.BCryptSetProperty.argtypes = [ctypes.c_void_p, wt.LPCWSTR, ctypes.c_void_p, wt.ULONG, wt.ULONG]
_bcrypt.BCryptGenerateSymmetricKey.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
                                               wt.ULONG, ctypes.c_char_p, wt.ULONG, wt.ULONG]
for _fn in (_bcrypt.BCryptEncrypt, _bcrypt.BCryptDecrypt):
    _fn.argtypes = [ctypes.c_void_p, ctypes.c_char_p, wt.ULONG, ctypes.c_void_p, ctypes.c_void_p, wt.ULONG,
                    ctypes.c_char_p, wt.ULONG, ctypes.POINTER(wt.ULONG), wt.ULONG]
for _fn in (_bcrypt.BCryptOpenAlgorithmProvider, _bcrypt.BCryptSetProperty, _bcrypt.BCryptGenerateSymmetricKey,
            _bcrypt.BCryptEncrypt, _bcrypt.BCryptDecrypt):
    _fn.restype = ctypes.c_long
_bcrypt.BCryptDestroyKey.argtypes = [ctypes.c_void_p]
_bcrypt.BCryptCloseAlgorithmProvider.argtypes = [ctypes.c_void_p, wt.ULONG]


def aes_gcm(encrypt, key, nonce, data, aad, tag=None):
    """AES-GCM with a 16-byte tag. Encrypting returns (ciphertext, tag); decrypting returns the
    plaintext, or raises ValueError if the key is wrong or anything was changed."""
    def check(status):
        if status:
            raise OSError(f"Windows crypto error 0x{status & 0xFFFFFFFF:08X}")
    alg, hkey = ctypes.c_void_p(), ctypes.c_void_p()
    check(_bcrypt.BCryptOpenAlgorithmProvider(ctypes.byref(alg), "AES", None, 0))
    try:
        mode = ctypes.create_unicode_buffer("ChainingModeGCM")
        check(_bcrypt.BCryptSetProperty(alg, "ChainingMode", mode, ctypes.sizeof(mode), 0))
        check(_bcrypt.BCryptGenerateSymmetricKey(alg, ctypes.byref(hkey), None, 0, key, len(key), 0))
        nonce_buf, aad_buf = ctypes.create_string_buffer(nonce, len(nonce)), ctypes.create_string_buffer(aad, len(aad))
        tag_buf = ctypes.create_string_buffer(tag or bytes(16), 16)
        info = BCRYPT_AUTH_INFO(ctypes.sizeof(BCRYPT_AUTH_INFO), 1, ctypes.cast(nonce_buf, ctypes.c_void_p), len(nonce),
                                ctypes.cast(aad_buf, ctypes.c_void_p), len(aad), ctypes.cast(tag_buf, ctypes.c_void_p), 16)
        out, done = ctypes.create_string_buffer(max(len(data), 1)), wt.ULONG()
        fn = _bcrypt.BCryptEncrypt if encrypt else _bcrypt.BCryptDecrypt
        status = fn(hkey, data, len(data), ctypes.byref(info), None, 0, out, len(data), ctypes.byref(done), 0)
        if not encrypt and status & 0xFFFFFFFF == STATUS_AUTH_TAG_MISMATCH:
            raise ValueError("wrong password or damaged file")
        check(status)
        return (out.raw[:done.value], tag_buf.raw) if encrypt else out.raw[:done.value]
    finally:
        if hkey:
            _bcrypt.BCryptDestroyKey(hkey)
        _bcrypt.BCryptCloseAlgorithmProvider(alg, 0)


def _export_key(password, salt):
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, **SCRYPT)


def seal_export(payload, password):
    """The bytes of a .altvault file holding `payload` (JSON-able), locked with `password`."""
    salt, nonce = os.urandom(16), os.urandom(12)
    header = EXPORT_MAGIC + bytes([EXPORT_VERSION]) + salt + nonce
    body, tag = aes_gcm(True, _export_key(password, salt), nonce, json.dumps(payload).encode("utf-8"), header)
    return header + body + tag


def open_export(data, password):
    """The payload of a .altvault file. Raises RobloxError with a plain message if it can't be read."""
    head = len(EXPORT_MAGIC)
    if len(data) < head + 1 + 16 + 12 + 16 or not data.startswith(EXPORT_MAGIC):
        raise RobloxError("That isn't an AltVault export file.")
    if data[head] != EXPORT_VERSION:
        raise RobloxError("This export was made by a newer AltVault. Update AltVault, then import it again.")
    salt, nonce = data[head + 1:head + 17], data[head + 17:head + 29]
    header, body, tag = data[:head + 29], data[head + 29:-16], data[-16:]
    try:
        payload = json.loads(aes_gcm(False, _export_key(password, salt), nonce, body, header, tag))
    except ValueError:
        raise RobloxError("Wrong password, or the file was damaged.") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("accounts"), list):
        raise RobloxError("That export file has no accounts in it.")
    return payload

# --- access key (begin) ---
# ---------------------------------------------------------------- access key
# This build only starts with a key. A key is a digital signature, made with the maker's private
# key (which is never in this program or its repository), over this PC's code, so it works on one
# PC and nowhere else, and it can't be changed or invented without the private key. Only the
# matching public key is here, and it can check a key but not make one. The check happens offline.
# Keys are made with tools/license_tool.py.
LICENSE_REQUIRED = True
LICENSE_PUBLIC_KEY = "4543533120000000c92aba53f172a26fdd6dd7ce55b42d6b76aef5d87a014fa82738a2ceb837442a266171f83048f983868040e5df78d951d685017ea9779356b186c76f30ff9217"   # hex of an ECDSA P-256 public key blob
LICENSE_FILE = os.path.join(APP_DIR, "license.key")   # in the data folder, so updates keep it
KEY_PREFIX = "AVK"
LICENSE_CONTACT = "hecticxww on Discord"   # where to ask for a key (a name, Discord tag or link), shown in the activation window

_bcrypt.BCryptImportKeyPair.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
                                        ctypes.c_char_p, wt.ULONG, wt.ULONG]
_bcrypt.BCryptImportKeyPair.restype = ctypes.c_long
_bcrypt.BCryptVerifySignature.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p, wt.ULONG,
                                          ctypes.c_char_p, wt.ULONG, wt.ULONG]
_bcrypt.BCryptVerifySignature.restype = ctypes.c_long


def b32(data):
    return base64.b32encode(data).decode().rstrip("=")


def unb32(text):
    text = re.sub(r"[^A-Za-z2-7]", "", text).upper()
    return base64.b32decode(text + "=" * (-len(text) % 8))


def group(text, size):
    return "-".join(text[i:i + size] for i in range(0, len(text), size))


def pc_raw():
    """10 bytes that identify this Windows install: a hash of its machine GUID. They stay the same
    across AltVault updates and change only if Windows is reinstalled."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            guid = str(winreg.QueryValueEx(k, "MachineGuid")[0])
    except OSError:
        guid = f"unknown-{uuid.getnode()}"
    return hashlib.sha256(b"AltVault pc|" + guid.encode()).digest()[:10]


def pc_code():
    """The code shown to the user, like ABCD-EFGH-JKLM-NPQR, to send to whoever makes their key."""
    return group(b32(pc_raw()), 4)


def license_message(pc, expires):
    """What a key signs: this PC's bytes and an expiry date as YYYYMMDD (0 = never expires)."""
    return b"AltVault licence v1|" + pc + b"|" + struct.pack(">I", expires)


def _ecdsa_valid(public_blob, digest, signature):
    alg, key = ctypes.c_void_p(), ctypes.c_void_p()
    if _bcrypt.BCryptOpenAlgorithmProvider(ctypes.byref(alg), "ECDSA_P256", None, 0):
        return False
    try:
        if _bcrypt.BCryptImportKeyPair(alg, None, "ECCPUBLICBLOB", ctypes.byref(key), public_blob,
                                       len(public_blob), 0):
            return False
        try:
            return _bcrypt.BCryptVerifySignature(key, None, digest, len(digest), signature, len(signature), 0) == 0
        finally:
            _bcrypt.BCryptDestroyKey(key)
    finally:
        _bcrypt.BCryptCloseAlgorithmProvider(alg, 0)


def check_key(text, pc=None, public_key=None, today=None):
    """('ok'|'invalid'|'expired', plain explanation) for a key typed or pasted by the user."""
    compact = re.sub(r"[^A-Za-z2-7]", "", text or "").upper()
    if not compact.startswith(KEY_PREFIX):
        return "invalid", "That doesn't look like an AltVault key."
    try:
        payload = unb32(compact[len(KEY_PREFIX):])
        public_blob = bytes.fromhex(public_key or LICENSE_PUBLIC_KEY)
    except ValueError:
        return "invalid", "That key is incomplete. Paste the whole key."
    if len(payload) != 4 + 64:
        return "invalid", "That key is incomplete. Paste the whole key."
    expires, signature = struct.unpack(">I", payload[:4])[0], payload[4:]
    digest = hashlib.sha256(license_message(pc or pc_raw(), expires)).digest()
    if not _ecdsa_valid(public_blob, digest, signature):
        return "invalid", "This key isn't valid for this PC. Keys only work on the PC they were made for."
    if expires and expires < int(today or time.strftime("%Y%m%d")):
        return "expired", "This key has expired. Ask for a new one."
    return "ok", "Key accepted."


def license_state():
    """('ok'|'none'|'invalid'|'expired', explanation) for the key saved on this PC, if any."""
    if not LICENSE_REQUIRED:
        return "ok", "No key needed."
    try:
        with open(LICENSE_FILE, encoding="utf-8") as f:
            return check_key(f.read(4096))
    except OSError:
        return "none", "No key is saved on this PC yet."


def save_key(text):
    os.makedirs(APP_DIR, exist_ok=True)
    with open(LICENSE_FILE, "w", encoding="utf-8") as f:
        f.write(text.strip() + "\n")

# --- access key (end) ---

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


def clean_name(text):
    """A game name Tk can draw. Emoji (outside the Basic Multilingual Plane) show as empty boxes,
    so they're dropped along with any brackets they leave empty: '[🎃] Tap Sim' -> 'Tap Sim'."""
    text = "".join(ch for ch in str(text or "") if ord(ch) <= 0xFFFF and ch not in "️‍")
    text = re.sub(r"[\[(【]\s*[\])】]", "", text)
    text = re.sub(r"([\[(【])\s+", r"\1", text)
    text = re.sub(r"\s+([\])】])", r"\1", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def get_place_name(cookie, place_id):
    try:
        status, _, body = _request(
            f"https://games.roblox.com/v1/games/multiget-place-details?placeIds={place_id}", cookie)
        if status == 200:
            places = json.loads(body)
            return (clean_name(places[0]["name"]) or None) if places else None
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

# "Purple night": near-blacks with a faint violet tint, and a dark violet accent used sparingly
# (the selected page, main buttons, ticks, focus rims, chart lines).
C = {"bg": "#17161b", "panel": "#211f27", "field": "#2b2832", "sel": "#393443", "hover": "#312d39",
     "fg": "#ebe8f0", "muted": "#9c96a8", "dim": "#6c6678", "deep": "#0e0d11",
     "accent": "#7446e0", "accent_hover": "#6438c9", "accent_pressed": "#552fae", "accent_light": "#8a5ff0",
     "accent_text": "#b49cff", "green": "#23a55a", "red": "#f23f43", "yellow": "#f0b232",
     "outline": "#5a5466", "thumb": "#4a4555", "check": "#7d7789", "field_hover": "#504a5d",
     "pressed": "#45404f", "disabled": "#1d1b22", "panel_disabled": "#29262f",
     "line": "#2f2b38", "line_hover": "#4a4458", "glass_light": "#6e6585",
     "danger": "#da373c", "danger_hover": "#bf2f33", "danger_pressed": "#a3282b"}
FONT = ("Segoe UI", 9)
NAV_HOVER = C["hover"]


def _rgb(color):
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def _segment_distance(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


TICK = ((0.26, 0.52), (0.43, 0.69), (0.75, 0.33))
CHEVRON = ((0.3, 0.4), (0.5, 0.6), (0.7, 0.4))


def shape_image(w, h, radius, fill=None, border=None, border_w=1.0, box=None, check=None, check_w=1.5, mark=TICK,
                light=None, light_h=16):
    """A PNG (bytes) of an anti-aliased rounded rectangle, the building block of AltVault's
    buttons, boxes, checkboxes and scrollbars. `box` is (x, y, w, h) inside the image (default:
    all of it); `fill`/`border` are '#rrggbb' or None for see-through; `check` draws a tick in
    that colour (or another two-stroke `mark`). `light` makes the rim glow toward that colour over the
    top `light_h` pixels, like the lit edge of glass. Like the app icon, it's a signed-distance field,
    so edges are smooth at any size."""
    bx, by, bw, bh = box or (0, 0, w, h)
    fill_c, border_c, check_c = (_rgb(c) if c else None for c in (fill, border, check))
    light_c = _rgb(light) if light else None
    base_border = border_c
    tick = [(bx + bw * fx, by + bh * fy) for fx, fy in mark]
    out = bytearray()
    for j in range(h):
        y = j + 0.5
        if light_c and base_border:   # this row's rim colour: bright at the top edge, settling to the base
            t = max(0.0, 1 - j / light_h) ** 1.5
            border_c = tuple(b + (c - b) * t for b, c in zip(base_border, light_c))
        for i in range(w):
            x = i + 0.5
            qx = abs(x - bx - bw / 2) - (bw / 2 - radius)
            qy = abs(y - by - bh / 2) - (bh / 2 - radius)
            d = math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - radius
            outer = min(max(0.5 - d, 0), 1)
            if not outer:
                out += b"\0\0\0\0"
                continue
            inner = min(max(0.5 - (d + border_w), 0), 1) if border_c else 1
            # Mix fill (inside) and border (the rim), tracking how opaque the result is.
            rgb, alpha = (0, 0, 0), 0.0
            if fill_c:
                rgb, alpha = fill_c, inner
            if border_c and inner < 1:
                rim = 1 - inner
                total = alpha + rim * (1 - alpha)
                rgb = tuple((c1 * alpha + c2 * rim * (1 - alpha)) / total for c1, c2 in zip(rgb, border_c))
                alpha = total
            if check_c:
                dist = min(_segment_distance(x, y, *tick[k], *tick[k + 1]) for k in (0, 1)) - check_w / 2
                ink = min(max(0.5 - dist, 0), 1)
                if ink:
                    total = ink + alpha * (1 - ink)
                    rgb = tuple((c2 * ink + c1 * alpha * (1 - ink)) / total for c1, c2 in zip(rgb, check_c))
                    alpha = total
            out += bytes((*(round(c) for c in rgb), round(255 * alpha * outer)))
    return _png(w, bytes(out), h)


# Colours of the drawn widgets, per state.
# Each state is (fill, hairline). Everyday buttons are outlined and flat; only the main action is filled.
BUTTON = {"normal": (C["panel"], C["line"]), "active": (C["hover"], C["line_hover"]),
          "pressed": (C["sel"], C["line_hover"]), "disabled": (C["bg"], C["line"])}
ACCENT = {"normal": (C["accent"], None), "active": (C["accent_hover"], None),
          "pressed": (C["accent_pressed"], None), "disabled": (C["panel"], None)}
DANGER = {"normal": (C["danger"], None), "active": (C["danger_hover"], None),
          "pressed": (C["danger_pressed"], None), "disabled": (C["panel"], None)}
PANEL_BUTTON = {"normal": (C["bg"], C["line"]), "active": (C["hover"], C["line_hover"]),
                "pressed": (C["sel"], C["line_hover"]), "disabled": (C["bg"], C["line"])}


def apply_theme(root, scale):
    """Discord-like dark theme. Buttons, boxes, checkboxes and scrollbars are drawn as rounded
    images with their own hover, pressed and focus looks; Tk's stock widgets are square and flat."""
    s = ttk.Style(root)
    s.theme_use("clam")
    root.configure(bg=C["bg"])
    images = root.altvault_images = {}

    def image(name, *args, **kw):
        images[name] = tk.PhotoImage(master=root, data=base64.b64encode(shape_image(*args, **kw)))
        return images[name]

    r = max(round(4 * scale), 3)
    # Tk paints the stretchy middle of these images by tiling it. A thin middle means hundreds of
    # tiles per repaint (a wide entry took ~0.4 s to draw), so the middle is made generous.
    side = 2 * (r + 1) + round(48 * scale)
    small = 2 * r + 2   # the smallest a control may shrink to; the image itself is only bigger to paint faster
    hair = max(scale, 1.0)   # one-pixel hairline

    def button_element(name, colors):
        imgs = {state: image(f"{name}-{state}", side, side, r, fill, line, border_w=hair)
                for state, (fill, line) in colors.items()}
        s.element_create(f"{name}.bg", "image", imgs["normal"], ("disabled", imgs["disabled"]),
                         ("pressed", imgs["pressed"]), ("active", imgs["active"]), border=r + 1,
                         padding=(r + 2, 1), width=small, height=small, sticky="nsew")
        return f"{name}.bg"

    s.configure(".", background=C["bg"], foreground=C["fg"], fieldbackground=C["field"],
                bordercolor=C["bg"], lightcolor=C["bg"], darkcolor=C["bg"], troughcolor=C["bg"],
                focuscolor=C["accent"], selectbackground=C["accent"], selectforeground="white",
                insertcolor=C["fg"], font=FONT)
    for style, name, colors in (("TButton", "AVButton", BUTTON), ("Accent.TButton", "AVAccent", ACCENT),
                                ("Panel.TButton", "AVPanelButton", PANEL_BUTTON), ("Danger.TButton", "AVDanger", DANGER)):
        element = button_element(name, colors)
        s.layout(style, [("Button.background", {"sticky": "nsew", "children": [(element, {"sticky": "nsew", "children": [
            ("Button.padding", {"sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]})]})]})])
    s.configure("TButton", padding=(round(8 * scale), round(5 * scale)), borderwidth=0, anchor="center")
    s.map("TButton", foreground=[("disabled", C["dim"])], background=[("active", C["bg"])])
    s.configure("Accent.TButton", foreground="white", font=("Segoe UI Semibold", 9))
    s.map("Accent.TButton", foreground=[("disabled", C["dim"])])
    s.configure("Danger.TButton", foreground="white", font=("Segoe UI Semibold", 9))
    s.configure("Panel.TButton", background=C["panel"])
    s.map("Panel.TButton", background=[("active", C["panel"])])

    # Text boxes and drop-downs: a rounded field whose rim lights up on hover and on focus.
    field = {"normal": image("field", side, side, r, C["field"], C["line"], border_w=hair),
             "hover": image("field-hover", side, side, r, C["field"], C["line_hover"], border_w=hair),
             "focus": image("field-focus", side, side, r, C["field"], C["accent"], border_w=max(1.4 * scale, 1.2))}
    s.element_create("AVField.field", "image", field["normal"], ("focus", field["focus"]),
                     ("hover", field["hover"]), border=r + 1, padding=(r + 2, 1), width=small, height=small,
                     sticky="nsew")
    s.layout("TEntry", [("AVField.field", {"sticky": "nsew", "children": [
        ("Entry.padding", {"sticky": "nsew", "children": [("Entry.textarea", {"sticky": "nsew"})]})]})])
    s.configure("TEntry", padding=(round(4 * scale), round(5 * scale)), borderwidth=0)
    # A read-only or disabled box defaults to Windows' light grey; the rounded corners would show it.
    s.map("TEntry", fieldbackground=[("readonly", C["field"]), ("disabled", C["field"])],
          background=[("readonly", C["bg"]), ("disabled", C["bg"])],
          foreground=[("disabled", C["dim"]), ("readonly", C["fg"])])
    s.layout("TCombobox", [("AVField.field", {"sticky": "nsew", "children": [
        ("AVCombo.arrow", {"side": "right", "sticky": ""}),
        ("Combobox.padding", {"sticky": "nsew", "children": [("Combobox.textarea", {"sticky": "nsew"})]})]})])
    aw = max(round(22 * scale), 16)
    chevrons = [image(f"chevron{i}", aw, aw, 0, None, check=color, check_w=1.6 * scale, mark=CHEVRON,
                      box=(aw * 0.22, aw * 0.22, aw * 0.56, aw * 0.56)) for i, color in enumerate((C["muted"], C["fg"]))]
    s.element_create("AVCombo.arrow", "image", chevrons[0], ("hover", chevrons[1]), ("focus", chevrons[1]))
    s.configure("TCombobox", padding=(round(3 * scale), round(2 * scale)), borderwidth=0, background=C["field"], arrowcolor=C["muted"],
                arrowsize=int(12 * scale))
    s.map("TCombobox", fieldbackground=[("readonly", C["field"])], background=[("active", C["field"])],
          arrowcolor=[("active", C["fg"])], selectbackground=[("readonly", C["field"])],
          selectforeground=[("readonly", C["fg"])])
    root.option_add("*TCombobox*Listbox.background", C["field"])
    root.option_add("*TCombobox*Listbox.foreground", C["fg"])
    root.option_add("*TCombobox*Listbox.selectBackground", C["accent"])
    root.option_add("*TCombobox*Listbox.borderWidth", 0)
    root.option_add("*TCombobox*Listbox.font", FONT)

    # Checkboxes: a rounded square that fills with the accent colour and a tick when on.
    box = max(round(15 * scale), 12)
    gap = round(8 * scale)
    br = max(round(3 * scale), 2)
    rim = max(1.4 * scale, 1.1)
    kw = {"box": (0.5, 0.5, box - 1, box - 1)}
    checks = {"off": image("check-off", box + gap, box, br, None, C["check"], border_w=rim, **kw),
              "off-hover": image("check-off-hover", box + gap, box, br, None, C["fg"], border_w=rim, **kw),
              "on": image("check-on", box + gap, box, br, C["accent"], check="#ffffff", check_w=1.8 * scale, **kw),
              "on-hover": image("check-on-hover", box + gap, box, br, C["accent_light"], check="#ffffff",
                                check_w=1.8 * scale, **kw)}
    s.element_create("AVCheck.indicator", "image", checks["off"], ("selected active", checks["on-hover"]),
                     ("selected", checks["on"]), ("active", checks["off-hover"]), sticky="w")
    s.layout("TCheckbutton", [("Checkbutton.padding", {"sticky": "nsew", "children": [
        ("AVCheck.indicator", {"side": "left", "sticky": ""}),
        ("Checkbutton.label", {"side": "left", "sticky": "nsew"})]})])
    s.configure("TCheckbutton", padding=(0, 3))
    s.map("TCheckbutton", background=[("active", C["bg"])], foreground=[("active", "#ffffff")])

    # Slim scrollbars: a rounded thumb, no arrows.
    tw, th = max(round(10 * scale), 8), max(round(56 * scale), 40)
    inset = max(round(2.5 * scale), 2)
    thumb_box = (inset, inset, tw - 2 * inset, th - 2 * inset)
    thumb = image("thumb", tw, th, (tw - 2 * inset) / 2, C["thumb"], box=thumb_box)
    thumb_hover = image("thumb-hover", tw, th, (tw - 2 * inset) / 2, C["dim"], box=thumb_box)
    s.element_create("AVScroll.thumb", "image", thumb, ("active", thumb_hover), ("pressed", thumb_hover),
                     border=(0, tw // 2 + 1), width=tw, height=tw, sticky="nsew")
    s.layout("Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
        ("AVScroll.thumb", {"expand": "1", "sticky": "nsew"})]})])
    s.configure("Vertical.TScrollbar", troughcolor=C["panel"], background=C["panel"], borderwidth=0,
                arrowsize=tw)

    # Group arrows in lists. Tk 8.6 doesn't tell image elements whether a row is open, so the
    # stock arrow is hidden and group rows show one of these chevrons as their own image instead.
    ind = max(round(16 * scale), 12)
    tree_kw = {"check": C["muted"], "check_w": 1.5 * scale, "box": (0, 0, ind, ind)}
    image("tree-closed", ind, ind, 0, None, mark=((0.4, 0.28), (0.62, 0.5), (0.4, 0.72)), **tree_kw)
    image("tree-open", ind, ind, 0, None, mark=CHEVRON, **tree_kw)
    leaf = image("tree-leaf", max(round(4 * scale), 2), ind, 0, None)
    s.element_create("AVTree.indicator", "image", leaf, sticky="")
    s.layout("Item", [("Treeitem.padding", {"sticky": "nswe", "children": [
        ("AVTree.indicator", {"side": "left", "sticky": ""}), ("Treeitem.image", {"side": "left", "sticky": ""}),
        ("Treeitem.text", {"side": "left", "sticky": ""})]})])
    s.configure("Treeview", background=C["panel"], fieldbackground=C["panel"], borderwidth=0,
                rowheight=int(50 * scale))
    s.map("Treeview", background=[("selected", C["sel"])], foreground=[("selected", C["fg"])])
    s.configure("Treeview.Heading", background=C["panel"], foreground=C["muted"], borderwidth=0,
                padding=(6, 6), font=("Segoe UI Semibold", 9))
    s.map("Treeview.Heading", background=[("active", C["panel"])], foreground=[("active", C["fg"])])
    s.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    s.configure("Muted.TLabel", foreground=C["muted"])
    s.configure("Small.TLabel", foreground=C["muted"], font=("Segoe UI", 8))
    s.configure("Title.TLabel", font=("Segoe UI Semibold", 15))
    s.configure("Section.TLabel", foreground=C["muted"], font=("Segoe UI Semibold", 9))
    s.configure("Error.TLabel", foreground=C["red"])
    s.configure("Hint.TLabel", background=C["field"], foreground=C["muted"])
    s.configure("Success.TLabel", foreground=C["green"])
    s.configure("Panel.TLabel", background=C["panel"])
    s.configure("PanelMuted.TLabel", background=C["panel"], foreground=C["muted"])
    s.configure("PanelSmall.TLabel", background=C["panel"], foreground=C["muted"], font=("Segoe UI", 9))
    s.configure("Card.TFrame", background=C["panel"])
    # Glass cards: rounded, a hair of tint, and a rim that glows along the top edge. The fill is flat,
    # so text labels drawn on a card (with the card's colour behind them) sit cleanly on it.
    gr = max(round(7 * scale), 4)
    glass = image("glass", 2 * (gr + 1) + round(56 * scale), 2 * (gr + 1) + round(56 * scale), gr, C["panel"],
                  C["line"], border_w=hair, light=C["glass_light"], light_h=round(18 * scale))
    s.element_create("AVGlass.bg", "image", glass, border=gr + 1, width=2 * gr, height=2 * gr, sticky="nsew")
    s.layout("Glass.TFrame", [("AVGlass.bg", {"sticky": "nsew"})])


def add_placeholder(entry, var, text):
    """Grey hint text shown inside an empty box. It's a label laid over the box, so the box's real
    value stays empty; clicking the hint just focuses the box."""
    hint = ttk.Label(entry, text=text, style="Hint.TLabel", cursor="xterm")
    hint.bind("<Button-1>", lambda _e: entry.focus_set())

    def update(*_):
        if var.get():
            hint.place_forget()
        else:
            hint.place(x=9, rely=0.5, anchor="w")
    var.trace_add("write", update)
    update()
    return hint


_icon_fonts = {}


def icon_font(widget):
    """Windows' icon font: Segoe Fluent Icons on Windows 11, Segoe MDL2 Assets on Windows 10."""
    root = widget.winfo_toplevel()
    if root not in _icon_fonts:
        families = set(tkfont.families(root))
        _icon_fonts[root] = next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if f in families),
                                 "Segoe UI Symbol")
    return _icon_fonts[root]


class Dialog(tk.Toplevel):
    """AltVault's own message box, so no bright Windows dialog flashes over the dark app. Shows an
    icon, a title, the message, an optional text box and buttons; .result is the chosen button's
    value (or the typed text), None if closed with Esc or the window's X."""

    KINDS = {"info": ("", "accent_text"), "question": ("", "accent_text"),
             "warning": ("", "yellow"), "error": ("", "red")}

    def __init__(self, parent, title, message, kind="info", buttons=(("OK", True),), danger=False,
                 entry=None, validate=None):
        parent = (parent or tk._default_root).winfo_toplevel()
        super().__init__(parent, bg=C["bg"])
        self.withdraw()
        self.result, self.validate = None, validate
        scale = parent.winfo_fpixels("1i") / 96
        self.title(title)
        self.resizable(False, False)
        if parent.winfo_viewable():
            self.transient(parent)
        frm = ttk.Frame(self, padding=(int(22 * scale), int(20 * scale)))
        frm.pack(fill="both", expand=True)
        glyph, color = self.KINDS[kind]
        tk.Label(frm, text=glyph, font=(icon_font(parent), 20), bg=C["bg"], fg=C[color]).grid(
            row=0, column=0, rowspan=3, sticky="n", padx=(0, int(16 * scale)), pady=(int(2 * scale), 0))
        ttk.Label(frm, text=title, font=("Segoe UI Semibold", 12)).grid(row=0, column=1, sticky="w")
        ttk.Label(frm, text=message, wraplength=int(380 * scale), justify="left").grid(
            row=1, column=1, sticky="w", pady=(int(4 * scale), 0))
        self.var = None
        if entry is not None:
            self.var = tk.StringVar(value=entry)
            box = ttk.Entry(frm, textvariable=self.var, width=40)
            box.grid(row=2, column=1, sticky="ew", pady=(int(12 * scale), 0))
            box.bind("<Return>", lambda _e: self.choose(True))
            box.select_range(0, "end")
        self.error = ttk.Label(frm, text="", style="Error.TLabel")
        self.error.grid(row=3, column=1, sticky="w", pady=(int(6 * scale), 0))
        self.error.grid_remove()   # shown only when there's something to say
        row = ttk.Frame(frm)
        row.grid(row=4, column=0, columnspan=2, sticky="e", pady=(int(18 * scale), 0))
        last = len(buttons) - 1
        for i, (label, value) in enumerate(buttons):   # the last button is the main one
            style = ("Danger.TButton" if danger else "Accent.TButton") if i == last else "TButton"
            btn = ttk.Button(row, text=label, style=style, command=lambda v=value: self.choose(v))
            btn.pack(side="left", padx=(int(8 * scale) if i else 0, 0))
            if i == last and entry is None:
                btn.focus_set()
        self.bind("<Escape>", lambda _e: self.destroy())
        if entry is None:
            self.bind("<Return>", lambda _e: self.choose(buttons[last][1]))
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        if parent.winfo_viewable():
            x = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
            y = parent.winfo_rooty() + (parent.winfo_height() - h) // 3
        else:
            x, y = (self.winfo_screenwidth() - w) // 2, (self.winfo_screenheight() - h) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.deiconify()
        dark_title_bar(self)
        self.lift()
        self.grab_set()
        if entry is not None:
            box.focus_set()
        self.wait_window()

    def choose(self, value):
        if self.var is not None and value is True:
            text = self.var.get()
            problem = self.validate(text) if self.validate else None
            if problem:
                self.error.config(text=problem)
                self.error.grid()
                return
            value = text
        self.result = value
        self.destroy()


def show_info(title, message, parent=None):
    Dialog(parent, title, message, "info")


def show_warning(title, message, parent=None):
    Dialog(parent, title, message, "warning")


def show_error(title, message, parent=None):
    Dialog(parent, title, message, "error")


def ask_yes_no(title, message, parent=None, yes="Yes", no="Cancel", danger=False):
    return Dialog(parent, title, message, "warning" if danger else "question",
                  ((no, False), (yes, True)), danger=danger).result is True


def ask_string(title, prompt, parent=None, initialvalue=""):
    return Dialog(parent, title, prompt, "question", (("Cancel", None), ("OK", True)), entry=initialvalue or "").result


def ask_integer(title, prompt, parent=None, minvalue=None, maxvalue=None, initialvalue=None):
    def check(text):
        try:
            n = int(text.strip())
        except ValueError:
            return "Type a whole number."
        if (minvalue is not None and n < minvalue) or (maxvalue is not None and n > maxvalue):
            return f"Pick a number from {minvalue} to {maxvalue}."
        return None
    text = Dialog(parent, title, prompt, "question", (("Cancel", None), ("OK", True)),
                  entry="" if initialvalue is None else str(initialvalue), validate=check).result
    return int(text.strip()) if text is not None else None


def themed_menu(root):
    return tk.Menu(root, tearoff=0, bg=C["deep"], fg=C["fg"], activebackground=C["accent"],
                   activeforeground="white", bd=0, relief="flat", activeborderwidth=4, font=FONT)


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
        self.title(f"Update login: {app.who(for_account)}" if for_account else "Add account")
        self.resizable(False, False)
        self.transient(app.root)
        frm = ttk.Frame(self, padding=18)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text=f"Log in again as {app.who(for_account)}" if for_account
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
        self.status.config(text=f"Saved {self.app.who(self.app.account(user['id']))}. Log in another account, "
                           "or Close.", style="Success.TLabel")

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
    check_and_add(app, entries, report)


MAX_EXPORT_BYTES = 20 * 1024 * 1024


def export_entries(payload):
    """The accounts of an opened .altvault file, in the form add_many takes. Anything malformed is
    dropped, since the file may have been made by hand."""
    found = []
    for item in payload.get("accounts") or []:
        if not isinstance(item, dict) or not COOKIE_RE.fullmatch(str(item.get("cookie") or "")):
            continue
        group = item.get("group")
        cap = item.get("fps_cap")
        found.append({"cookie": item["cookie"], "name": str(item.get("name") or ""),
                      "group": group.strip()[:40] or None if isinstance(group, str) else None,
                      "note": str(item.get("note") or "")[:200],
                      "fps_cap": cap if isinstance(cap, int) and FPS_RANGE[0] <= cap <= FPS_RANGE[1] else None,
                      **{k: item.get(k) is True for k in ("low_graphics", "muted", "afk_skip")}})
    return found


def check_and_add(app, entries, report, favorites=None):
    """Checks every login with Roblox (on this worker thread), then saves the working ones in one go
    and calls report(text) on the Tk thread."""
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
        if favorites:
            app.merge_favorites(favorites)
        if not entries:
            report("No accounts were found in that file.")
            return
        text = f"Imported {len(good)} account{'s' if len(good) != 1 else ''}."
        if expired:
            text += (f" Skipped {len(expired)} whose login has expired: " + ", ".join(expired[:8]) +
                     ("..." if len(expired) > 8 else "") + ". Add those again with Log in with browser.")
        report(text)
    app.ui(save)


class SettingsPage(ttk.Frame):
    """Options you set once, in two columns. Everyday controls (Join, Anti-AFK) stay elsewhere."""

    def __init__(self, app, parent):
        super().__init__(parent)
        self.app = app
        wrap = int(300 * app.scale)
        page_header(self, "Settings", "Things you set once. Changes are saved straight away.")
        cols = ttk.Frame(self)
        cols.pack(fill="both", expand=True, pady=(int(4 * app.scale), 0))
        left, right = ttk.Frame(cols), ttk.Frame(cols)
        left.grid(row=0, column=0, sticky="nwe", padx=(0, int(32 * app.scale)))
        right.grid(row=0, column=1, sticky="nwe")
        cols.columnconfigure((0, 1), weight=1, uniform="col")
        self.wrapped = []   # labels that rewrap when the page is resized

        def section(frm, title, first=False):
            ttk.Label(frm, text=title, style="Section.TLabel").pack(anchor="w", pady=(10 if first else 18, 0))

        def detail(frm, text, indent=24):
            label = ttk.Label(frm, text=text, style="Small.TLabel", wraplength=wrap, justify="left")
            label.pack(anchor="w", fill="x", padx=(indent, 0))
            self.wrapped.append((label, indent))

        def option(frm, var, title, text, command):
            ttk.Checkbutton(frm, text=title, variable=var, command=command).pack(anchor="w", pady=(8, 0))
            detail(frm, text)

        section(left, "Joining", first=True)
        option(left, app.multi_var, "Multi-Roblox", "Run several Roblox windows at once. Turn off only if you "
               "want each launch to replace the previous window.", app.toggle_multi)
        option(left, app.same_server_var, "Put accounts in the same server",
               "When joining several accounts with no server chosen, they all follow the first one.",
               app.save_settings)
        option(left, app.auto_tile_var, "Arrange windows after joining",
               "Splits the screen evenly between every Roblox window, like the Tile button.", app.save_settings)

        section(left, "Anti-AFK")
        row = ttk.Frame(left)
        row.pack(anchor="w", pady=(8, 2))
        ttk.Label(row, text="Key it presses").pack(side="left")
        self.key_var = tk.StringVar(value=str(app.settings.get("afk_key", "space")).lower())
        keys = ttk.Combobox(row, textvariable=self.key_var, state="readonly", width=8,
                            values=["space", "f13"] + list("abcdefghijklmnopqrstuvwxyz"))
        keys.pack(side="left", padx=(8, 0))
        keys.bind("<<ComboboxSelected>>", lambda _e: self.set_key())
        detail(left, "Space makes your character jump. F13 is a key no game uses, for games where "
               "jumping gets in the way.", indent=0)

        section(right, "Privacy", first=True)
        option(right, app.stream_var, "Stream mode", "Hides usernames and avatars in AltVault and in Roblox "
               "window titles, so you can share your screen.", app.toggle_stream)
        option(right, app.hide_taskbar_var, "Hide Roblox from the taskbar", "Keeps the taskbar and Alt+Tab "
               "clear. Open a window from the Live page instead.", app.toggle_taskbar)

        section(right, "Your accounts")
        detail(right, "Updates keep your accounts by themselves. To move them to another PC or Windows "
               "user, export them to a file locked with a password.", indent=0)
        row = ttk.Frame(right)
        row.pack(anchor="w", pady=(8, 0))
        ttk.Button(row, text="Export...", command=app.export_accounts).pack(side="left")
        ttk.Button(row, text="Import...", command=app.import_accounts).pack(side="left", padx=(6, 0))

        section(right, "About")
        ttk.Label(right, text=f"AltVault {APP_VERSION}  ·  Made by HecticWins").pack(anchor="w", pady=(6, 0))
        links = ttk.Frame(right)
        links.pack(anchor="w", pady=(6, 0))
        ttk.Button(links, text="Open on GitHub",
                   command=lambda: webbrowser.open(GITHUB_URL)).pack(side="left")
        ttk.Button(links, text="Open log folder", command=lambda: os.startfile(APP_DIR)).pack(side="left", padx=(6, 0))
        left.bind("<Configure>", self.rewrap)

    def rewrap(self, event):
        for label, indent in self.wrapped:
            label.config(wraplength=max(event.width - indent - 4, 120))

    def set_key(self):
        self.app.settings["afk_key"] = self.key_var.get()
        save_settings(self.app.settings)
        self.app.status.config(text=f"Anti-AFK will press {self.key_var.get().upper()}.")


class PasswordDialog(tk.Toplevel):
    """Asks for an export file's password and waits. `confirm` adds a second box, for choosing a new
    one. `check(password)` may raise RobloxError, shown in the dialog so the user can try again; its
    return value is .result. .password is None if cancelled."""

    def __init__(self, app, title, text, confirm=False, check=None):
        super().__init__(app.root, bg=C["bg"])
        self.password, self.result, self.confirm, self.check = None, None, confirm, check
        self.title(title)
        self.resizable(False, False)
        self.transient(app.root)
        frm = ttk.Frame(self, padding=18)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text=title, style="Title.TLabel").pack(anchor="w")
        ttk.Label(frm, text=text, wraplength=int(380 * app.scale), justify="left").pack(anchor="w", pady=(8, 0))
        self.vars = [tk.StringVar() for _ in range(2 if confirm else 1)]
        entries = []
        for var, label in zip(self.vars, ("Password", "Type it again")):
            ttk.Label(frm, text=label, style="Muted.TLabel").pack(anchor="w", pady=(10, 2))
            entries.append(ttk.Entry(frm, textvariable=var, show="•"))
            entries[-1].pack(fill="x")
            entries[-1].bind("<Return>", lambda _e: self.ok())
        self.error = ttk.Label(frm, text="", style="Error.TLabel")
        self.error.pack(anchor="w", pady=(8, 0))
        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text="OK", style="Accent.TButton", command=self.ok).pack(side="right")
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right", padx=(0, 6))
        self.bind("<Escape>", lambda _e: self.destroy())
        dark_title_bar(self)
        self.grab_set()
        entries[0].focus_set()
        self.wait_window()

    def ok(self):
        password = self.vars[0].get()
        if self.confirm and len(password) < MIN_PASSWORD:
            self.error.config(text=f"Use at least {MIN_PASSWORD} characters.")
        elif self.confirm and password != self.vars[1].get():
            self.error.config(text="The two passwords don't match.")
        elif password:
            if self.check:
                self.error.config(text="Checking...")
                self.update_idletasks()
                try:
                    self.result = self.check(password)
                except RobloxError as e:
                    self.error.config(text=str(e))
                    return
            self.password = password
            self.destroy()


def set_text(widget, text=None, **options):
    """Changes a label only if something differs. Re-setting the same text still makes Tk lay out
    and repaint the window, which on a page refreshed every couple of seconds wastes real power."""
    if text is not None:
        options["text"] = text
    changed = {k: v for k, v in options.items() if str(widget.cget(k)) != str(v)}
    if changed:
        widget.config(**changed)


class LivePage(ttk.Frame):
    """Every Roblox window as a live tile (see 'live view' above), with a stats panel on the right.
    Thumbnails exist only while this page is on screen. Stats are sampled every few seconds, and
    only then; nothing here runs while AltVault is minimised, in the tray, or on another page."""

    SAMPLE_MS = 3000
    GAP = 6
    UNITS = (("min", "eggs/min"), ("hour", "eggs/hour"), ("sec", "sec/egg"))

    def __init__(self, app, parent):
        super().__init__(parent)
        self.app = app
        self.tiles = {}        # hwnd -> LiveTile
        self.thumbs = {}       # hwnd -> DWM thumbnail handle
        self.placed = {}       # hwnd -> the rect its thumbnail was last given
        self.shown = False
        self.sample_job = None
        self.cpu = CpuMeter()
        self.ram_used = None   # share of the PC's RAM in use, for the bar
        s = app.scale
        # The stats panel runs the page's full height, beside the title, toolbar and tiles.
        self.panel = ttk.Frame(self, style="Glass.TFrame", width=int(240 * s))
        self.panel.pack(side="right", fill="y", padx=(int(14 * s), 0))
        self.panel.pack_propagate(False)
        left = ttk.Frame(self)
        left.pack(side="left", fill="both", expand=True)
        ttk.Label(left, text="Live", style="Title.TLabel").pack(anchor="w")
        self.summary = ttk.Label(left, text="", style="Small.TLabel")
        self.summary.pack(anchor="w")

        # Quick switches for what matters while watching: privacy and the taskbar, then window actions.
        bar = ttk.Frame(left)
        bar.pack(fill="x", pady=(10, 0))
        self.stream_btn = ttk.Button(bar, command=self.flip_stream)
        self.stream_btn.pack(side="left")
        ttk.Checkbutton(bar, text="Hide from taskbar", variable=app.hide_taskbar_var,
                        command=app.toggle_taskbar).pack(side="left", padx=(14, 0))
        ttk.Button(bar, text="Close all", command=app.close_all).pack(side="right")
        ttk.Button(bar, text="Arrange windows", command=app.tile).pack(side="right", padx=(0, 6))
        self.update_toolbar()

        self.area = tk.Frame(left, bg=C["bg"])
        self.area.pack(fill="both", expand=True, pady=(12, 0))
        self.area.bind("<Configure>", lambda _e: self.layout())
        self.empty = ttk.Label(self.area, text="No Roblox windows open.\nJoin from Accounts and they show up here.",
                               style="Muted.TLabel", justify="center")
        self.build_panel()

    def flip_stream(self):
        self.app.stream_var.set(not self.app.stream_var.get())
        self.app.toggle_stream()

    def update_toolbar(self):
        on = self.app.stream_var.get()
        set_text(self.stream_btn, f"Stream mode: {'on' if on else 'off'}", style="Accent.TButton" if on else "TButton")

    # ------------------------------------------------------------ stats panel
    def build_panel(self):
        s = self.app.scale
        pad = int(16 * s)
        P = C["panel"]
        small = ("Segoe UI", 8)
        self.values = {}

        def label(parent, text="", fg=C["fg"], font=FONT, **kw):
            return tk.Label(parent, text=text, bg=P, fg=fg, font=font, **kw)

        def section(title, parent=None):
            row = tk.Frame(parent or self.panel, bg=P)
            row.pack(fill="x", padx=pad, pady=(int(9 * s), 0))
            label(row, title, font=("Segoe UI Semibold", 9)).pack(side="left")
            return row

        def stat(key, text, dot=None, parent=None):
            row = tk.Frame(parent or self.panel, bg=P)
            row.pack(fill="x", padx=pad, pady=(int(1 * s), 0))
            if dot:
                label(row, "●", fg=dot, font=small).pack(side="left", padx=(0, int(6 * s)))
            label(row, text, fg=C["muted"] if not dot else C["fg"]).pack(side="left")
            self.values[key] = label(row, "—", font=("Segoe UI Semibold", 10))
            self.values[key].pack(side="right")

        # Headline: how many windows are in a game, and a one-line verdict.
        top = tk.Frame(self.panel, bg=P)
        top.pack(fill="x", padx=pad, pady=(int(10 * s), 0))
        self.big = label(top, "0", font=("Segoe UI Semibold", 22))
        self.big.pack(side="left")
        self.big_of = label(top, " / 0", fg=C["muted"], font=("Segoe UI Semibold", 14))
        self.big_of.pack(side="left", anchor="s", pady=(0, int(5 * s)))
        label(self.panel, "windows in a game", fg=C["muted"], font=small).pack(anchor="w", padx=pad)
        pill = tk.Frame(self.panel, bg=C["panel"], highlightthickness=1, highlightbackground=C["line_hover"])
        pill.pack(anchor="w", padx=pad, pady=(int(8 * s), 0))
        self.pill_dot = tk.Label(pill, text="●", bg=C["panel"], fg=C["muted"], font=small)
        self.pill_dot.pack(side="left", padx=(int(8 * s), int(4 * s)), pady=int(3 * s))
        self.pill_text = tk.Label(pill, text="", bg=C["panel"], fg=C["fg"], font=small)
        self.pill_text.pack(side="left", padx=(0, int(10 * s)))

        section("Windows")
        stat("ingame", "In a game", C["green"])
        stat("loading", "Joining", C["yellow"])
        stat("hung", "Not responding", C["red"])
        stat("other", "Not from AltVault", C["dim"])

        section("This PC")
        stat("cpu", "Roblox CPU")
        stat("ram", "Roblox RAM")
        stat("free", "Free RAM")
        self.ram_bar = tk.Canvas(self.panel, height=max(int(5 * s), 3), bg=C["field"], highlightthickness=0, bd=0)
        self.ram_bar.pack(fill="x", padx=pad, pady=(int(5 * s), 0))
        self.ram_bar.bind("<Configure>", lambda _e: self.draw_ram_bar())

        section("Egg hatch")
        saved = self.app.settings.get("hatch")
        saved = saved if isinstance(saved, dict) else {}
        value, unit = saved.get("value"), saved.get("unit")
        self.unit = unit if unit in dict(self.UNITS) else "min"
        self.eggs_var = tk.StringVar(value=f"{value:g}" if isinstance(value, (int, float)) and value > 0 else "")
        eggs = ttk.Entry(self.panel, textvariable=self.eggs_var)
        eggs.pack(fill="x", padx=pad, pady=(int(2 * s), 0))
        add_placeholder(eggs, self.eggs_var, "e.g. 58  (one alt's hatch speed)")
        self.eggs_var.trace_add("write", lambda *_: self.eggs_changed())
        seg = tk.Frame(self.panel, bg=C["field"])
        seg.pack(fill="x", padx=pad, pady=(int(6 * s), int(2 * s)))
        self.unit_btns = {}
        for key, text in self.UNITS:
            btn = tk.Label(seg, text=text, bg=C["field"], fg=C["muted"], font=small, cursor="hand2", pady=int(3 * s))
            btn.pack(side="left", fill="x", expand=True, padx=1, pady=1)
            btn.bind("<Button-1>", lambda _e, k=key: self.set_unit(k))
            self.unit_btns[key] = btn
        stat("per_min", "Per minute")
        stat("per_hour", "Per hour")
        stat("per_day", "Per day")
        stat("hatched", "Hatched, 24 h (est.)")
        self.values["per_hour"].config(fg=C["accent_text"])
        self.hint_box = tk.Frame(self.panel, bg=P)   # holds the hint so it keeps its place when hidden
        self.hint_box.pack(fill="x")
        self.hint_shown = True
        self.hatch_hint = label(self.hint_box, "", fg=C["muted"], font=small, justify="left", wraplength=int(205 * s))
        self.hatch_hint.pack(anchor="w", padx=pad, pady=(int(3 * s), 0))
        self.paint_units()

        # Lowest priority: dropped when the window is too short to show it without cutting off.
        self.day_box = tk.Frame(self.panel, bg=P)
        self.day_shown = False
        head = section("Last 24 hours", self.day_box)
        link = label(head, "Open Stats  ›", fg=C["accent_text"], font=small, cursor="hand2")
        link.pack(side="right")
        link.bind("<Button-1>", lambda _e: self.app.show_page("stats"))
        link.bind("<Enter>", lambda _e: link.config(fg=C["fg"], font=small + ("underline",)))
        link.bind("<Leave>", lambda _e: link.config(fg=C["accent_text"], font=small))
        stat("day_ingame", "In a game", C["green"], self.day_box)
        stat("day_launch", "Launches that worked", C["accent"], self.day_box)
        stat("day_pings", "Anti-AFK pings", C["accent_text"], self.day_box)
        stat("day_closed", "Closed outside AltVault", C["red"], self.day_box)
        self.panel.bind("<Configure>", lambda _e: self.fit_panel())

    def fit_panel(self):
        """Keeps the stats column from being cut off: the 24-hour block is dropped first if the window
        is too short, then the explanatory hint under the hatch totals."""
        wrap = max(self.panel.winfo_width() - 2 * int(16 * self.app.scale), 80)
        self.hatch_hint.config(wraplength=wrap)
        self.panel.update_idletasks()
        optional = (self.day_box, self.hint_box)
        # Where the last always-shown row really ends on screen (no guessing at padding).
        fixed_bottom = max(w.winfo_y() + w.winfo_height() for w in self.panel.winfo_children()
                           if w not in optional and w.winfo_manager())
        day_h = self.day_box.winfo_reqheight() + int(10 * self.app.scale)
        hint_h = self.hatch_hint.winfo_reqheight() + int(8 * self.app.scale)
        room = self.panel.winfo_height() - fixed_bottom - int(8 * self.app.scale)
        show_day = room >= day_h
        room -= day_h if show_day else 0
        show_hint = room >= hint_h
        if (show_day, show_hint) != (self.day_shown, self.hint_shown):
            self.after_idle(self.fit_panel)   # layout moved; measure again until it settles
        if show_day != self.day_shown:
            self.day_shown = show_day
            self.day_box.pack(fill="x") if show_day else self.day_box.pack_forget()
        if show_hint != self.hint_shown:
            self.hint_shown = show_hint
            if show_hint:
                self.hatch_hint.pack(anchor="w", padx=int(16 * self.app.scale), pady=(int(3 * self.app.scale), 0))
            else:   # an emptied frame keeps its old height unless told otherwise
                self.hatch_hint.pack_forget()
                self.hint_box.config(height=1)

    def set_unit(self, unit):
        self.unit = unit
        self.paint_units()
        self.eggs_changed()

    def paint_units(self):
        for key, btn in self.unit_btns.items():
            on = key == self.unit
            btn.config(bg=C["sel"] if on else C["field"], fg=C["fg"] if on else C["muted"])

    def eggs_value(self):
        try:
            value = float(self.eggs_var.get().replace(",", "."))
        except ValueError:
            return None
        return value if 0 < value < 1e9 and math.isfinite(value) else None

    def eggs_rate(self):
        """Eggs per minute for one alt, whichever unit it was typed in."""
        value = self.eggs_value()
        return None if value is None else per_minute(value, self.unit)

    def eggs_changed(self):
        value = self.eggs_value()
        if value is None:
            self.app.settings.pop("hatch", None)
        else:
            self.app.settings["hatch"] = {"value": value, "unit": self.unit}
        save_settings(self.app.settings)
        self.update_hatch()

    def update_hatch(self):
        before = str(self.hatch_hint.cget("text"))
        self._update_hatch()
        if str(self.hatch_hint.cget("text")) != before:   # the hint's height changed: refit the column
            self.after_idle(self.fit_panel)

    def _update_hatch(self):
        rate, alts = self.eggs_rate(), self.count("green")
        if rate is None:
            for key in ("per_min", "per_hour", "per_day"):
                set_value(self.values[key], "—")
            set_text(self.hatch_hint, "Type how fast ONE alt hatches (you read it in the game), then pick its "
                                      "unit. AltVault multiplies it by the accounts in a game.")
            return
        for key, total in zip(("per_min", "per_hour", "per_day"), hatch_totals(rate, alts)):
            set_value(self.values[key], short_number(total))
        if alts:
            set_text(self.hatch_hint, f"{alts} alt{'s' if alts != 1 else ''} in a game · "
                                      f"{short_number(rate * 60)} per hour each")
        else:
            set_text(self.hatch_hint, "No account is in a game yet, so the totals are 0. They count up "
                                      "as accounts join.")

    def count(self, color):
        return sum(1 for t in self.tiles.values() if t.color == color)

    def update_headline(self):
        n, ingame, loading, hung = len(self.tiles), self.count("green"), self.count("yellow"), self.count("red")
        other = self.count("grey")
        set_text(self.big, str(ingame))
        set_text(self.big_of, f" / {n}")
        if not n:
            dot, text = C["muted"], "No Roblox windows"
        elif hung:
            dot, text = C["red"], f"{hung} not responding"
        elif loading:
            dot, text = C["yellow"], f"{loading} still joining"
        elif other:
            dot, text = C["muted"], f"{other} not opened by AltVault"
        else:
            dot, text = C["green"], "Every window is in a game"
        set_text(self.pill_dot, fg=dot)
        set_text(self.pill_text, text)
        for key, value in (("ingame", ingame), ("loading", loading), ("hung", hung), ("other", other)):
            set_value(self.values[key], str(value))
        set_text(self.summary, f"{n} window{'s' if n != 1 else ''} · {ingame} in a game · click a tile to "
                                 "bring it to the front" if n else "Roblox windows show up here while they're open")

    def draw_ram_bar(self):
        bar = self.ram_bar
        key = (None if self.ram_used is None else round(self.ram_used, 3), bar.winfo_width())
        if key == getattr(self, "_ram_drawn", None):
            return
        self._ram_drawn = key
        bar.delete("all")
        if self.ram_used is None:
            return
        free = 1 - self.ram_used
        color = C["green"] if free > 0.25 else C["yellow"] if free > 0.1 else C["red"]
        bar.create_rectangle(0, 0, int(bar.winfo_width() * self.ram_used), bar.winfo_height(), fill=color, width=0)

    def sample(self):
        """Roblox's CPU and RAM, the PC's free RAM and Anti-AFK's timing. Reschedules itself only
        while the page is seen."""
        self.sample_job = None
        if not self.shown or self.app.root.state() in ("iconic", "withdrawn"):
            return   # app.on_restore starts it again
        pids = {t.pid for t in self.tiles.values()}
        usage = {pid: process_usage(pid) for pid in pids}
        cpu = self.cpu.percent({pid: u[0] for pid, u in usage.items() if u and u[0] is not None})
        rams = [u[1] if u else None for u in usage.values()]
        set_text(self.values["cpu"], "…" if cpu is None and pids else f"{cpu or 0:.0f}%")
        set_text(self.values["ram"], "—" if None in rams else f"{sum(rams) / 2 ** 30:.1f} GB")
        free, total = memory_status()
        set_text(self.values["free"], f"{free / 2 ** 30:.1f} of {total / 2 ** 30:.0f} GB")
        self.ram_used = 1 - free / total if total else None
        self.draw_ram_bar()
        app = self.app
        h = app.history
        day = lambda f: h.total(f, 24)
        ingame, ok, failed = percent_of(day("ingame"), day("open")), day("launch_ok"), day("launch_fail")
        launch = percent_of(ok, ok + failed)
        set_value(self.values["day_ingame"], "—" if ingame is None else f"{ingame:.1f}%")
        set_value(self.values["day_launch"], "—" if launch is None else f"{launch:.1f}%")
        set_value(self.values["day_pings"], short_number(day("pings")))
        set_value(self.values["day_closed"], f"{day('closed'):g}")
        set_value(self.values["hatched"], short_number(day("eggs")))
        self.sample_job = self.after(1000 if cpu is None and pids else self.SAMPLE_MS, self.sample)

    # ------------------------------------------------------------ tiles
    def on_show(self):
        self.shown = True
        self.sync(roblox_windows())
        self.resume()
        self.after_idle(self.fit_panel)

    def on_hide(self):
        self.shown = False
        self.release()
        if self.sample_job:
            self.after_cancel(self.sample_job)
            self.sample_job = None

    def resume(self):
        """(Re)starts sampling, e.g. when AltVault comes back from the tray."""
        if self.shown and not self.sample_job:
            self.cpu.last.clear()
            self.sample()

    def release(self):
        for thumb in self.thumbs.values():
            unregister_thumbnail(thumb)
        self.thumbs.clear()
        self.placed.clear()

    def sync(self, windows):
        """Adds and removes tiles to match the open Roblox windows, and updates each one's header."""
        if not self.shown:
            return
        alive = {hwnd: pid for hwnd, pid, _ in windows}
        changed = set(alive) != set(self.tiles)
        for hwnd in set(self.tiles) - set(alive):
            if hwnd in self.thumbs:
                unregister_thumbnail(self.thumbs.pop(hwnd))
            self.placed.pop(hwnd, None)
            self.tiles.pop(hwnd).destroy()
        order = {a["id"]: i for i, a in enumerate(self.app.accounts)}
        for hwnd, pid, title in sorted(windows, key=lambda w: order.get(self.app.running.get(w[1]), len(order))):
            tile = self.tiles.get(hwnd)
            if tile is None:
                tile = self.tiles[hwnd] = LiveTile(self, hwnd, pid)
            tile.update_state(*self.app.live_state(hwnd, pid, title))
        if changed:
            self.layout()
        else:
            self.place_thumbs()
        self.update_headline()
        self.update_toolbar()
        self.update_hatch()

    def layout(self):
        if not self.shown:
            return
        if not self.tiles:
            self.empty.place(relx=0.5, rely=0.45, anchor="center")
            return
        self.empty.place_forget()
        width, height = self.area.winfo_width(), self.area.winfo_height()
        if width < 50 or height < 50:
            return
        s = self.app.scale
        sizes = [client_size(h) for h in self.tiles]
        shapes = [w / h for w, h in sizes if w > 0 and h > 0]
        aspect = min(max(sum(shapes) / len(shapes), 0.6), 2.5) if shapes else 16 / 10
        gap = int(self.GAP * s)
        cols, rows, tile_w, tile_h = live_grid(len(self.tiles), width, height, LiveTile.header_px(s), gap, aspect)
        tiles = list(self.tiles.values())
        for i, tile in enumerate(tiles):
            row, col = divmod(i, cols)
            tile.place(x=col * (tile_w + gap), y=row * (tile_h + gap), width=tile_w, height=tile_h)   # from the top left
        self.after_idle(lambda: self.place_thumbs(settle=True))

    def place_thumbs(self, settle=False):
        """Puts each window's thumbnail over its tile's body, keeping the window's shape. Minimised
        and frozen windows get a note instead (DWM has no fresh picture of them)."""
        if not self.shown or not self.winfo_ismapped():
            return
        if settle:   # tiles were just placed; make their positions final before reading them
            self.update_idletasks()
        dest = _user32.GetParent(self.app.root.winfo_id())
        ox, oy = client_origin(dest)
        for hwnd, tile in self.tiles.items():
            note = "Minimised: click to restore" if _user32.IsIconic(hwnd) else None
            if note or not tile.winfo_ismapped():
                if hwnd in self.thumbs:
                    unregister_thumbnail(self.thumbs.pop(hwnd))
                    self.placed.pop(hwnd, None)
                tile.set_note(note)
                continue
            if hwnd not in self.thumbs:
                thumb = register_thumbnail(dest, hwnd)
                if not thumb:
                    tile.set_note("No preview")
                    continue
                self.thumbs[hwnd] = thumb
            tile.set_note(None)
            b = tile.body
            rect = fit_rect(*client_size(hwnd), b.winfo_rootx() - ox, b.winfo_rooty() - oy,
                            b.winfo_width(), b.winfo_height())
            if self.placed.get(hwnd) != rect:
                place_thumbnail(self.thumbs[hwnd], rect)
                self.placed[hwnd] = rect

    def show_window(self, hwnd):
        focus_window(hwnd)

    def close_window(self, hwnd):
        tile = self.tiles.get(hwnd)
        if tile and ask_yes_no("Close Roblox", f"Close {tile.label.cget('text')}'s Roblox window?",
                                     parent=self.app.root, yes="Close", danger=True):
            self.app.close_windows([tile.pid])


class LiveTile(tk.Frame):
    """One Roblox window on the Live page: a header (status, name, show, close) over the body that
    its thumbnail is drawn on. Clicking anywhere on the tile brings that window to the front."""

    COLORS = {"green": C["green"], "yellow": C["yellow"], "red": C["red"], "grey": C["dim"]}

    @staticmethod
    def header_px(scale):
        return int(24 * scale)

    def __init__(self, page, hwnd, pid):
        super().__init__(page.area, bg=C["panel"], cursor="hand2", highlightthickness=1,
                         highlightbackground=C["line"], highlightcolor=C["line"])
        self.hwnd, self.pid, self.color = hwnd, pid, None
        s = page.app.scale
        self.config(highlightbackground=C["accent"])   # a new window glows once, then settles
        self.after(30, lambda: fade(self, "highlightbackground", C["line"], steps=18, ms=24))
        head = tk.Frame(self, bg=C["panel"], height=self.header_px(s))
        head.pack(fill="x")
        head.pack_propagate(False)
        icons = page.app.icon_font
        for glyph, action in (("", page.close_window), ("", page.show_window)):
            btn = tk.Label(head, text=glyph, font=(icons, 9), bg=C["panel"], fg=C["muted"], padx=int(6 * s))
            btn.pack(side="right", fill="y")
            btn.bind("<Enter>", lambda _e=None, b=btn: b.config(fg=C["fg"], bg=C["sel"]))
            btn.bind("<Leave>", lambda _e=None, b=btn: b.config(fg=C["muted"], bg=C["panel"]))
            btn.bind("<Button-1>", lambda _e=None, a=action: a(hwnd) or "break")
            btn.bind("<Enter>", lambda _e=None: fade(self, "highlightbackground", C["line_hover"]), add="+")
            btn.bind("<Leave>", self.leave, add="+")
        self.dot = tk.Label(head, text="●", font=("Segoe UI", 9), bg=C["panel"], fg=C["muted"])
        self.dot.pack(side="left", padx=(int(8 * s), 0))
        self.label = tk.Label(head, text="", font=("Segoe UI Semibold", 9), bg=C["panel"], fg=C["fg"], anchor="w")
        self.label.pack(side="left", fill="x", expand=True, padx=(int(4 * s), 0))
        self.body = tk.Frame(self, bg=C["deep"])
        self.body.pack(fill="both", expand=True, padx=int(2 * s), pady=(0, int(2 * s)))
        self.note = tk.Label(self.body, text="", bg=C["deep"], fg=C["muted"], font=FONT)
        for widget in (self, head, self.dot, self.label, self.body, self.note):
            widget.bind("<Button-1>", lambda _e=None: page.show_window(hwnd))
            widget.bind("<Enter>", lambda _e=None: fade(self, "highlightbackground", C["line_hover"]), add="+")
            widget.bind("<Leave>", self.leave, add="+")

    def leave(self, _event=None):
        """The outline stays lit while the pointer is anywhere on the tile, buttons included."""
        under = self.winfo_containing(*self.winfo_pointerxy())
        while under is not None and under is not self:
            under = under.master
        if under is None:
            fade(self, "highlightbackground", C["line"])

    def update_state(self, color, name):
        if color != self.color:
            self.color = color
            self.dot.config(fg=self.COLORS[color])
        if self.label.cget("text") != name:
            self.label.config(text=name)

    def set_note(self, text):
        if text:
            set_text(self.note, text)
            if not self.note.winfo_manager():
                self.note.place(relx=0.5, rely=0.5, anchor="center")
        elif self.note.winfo_manager():
            self.note.place_forget()


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


def blend(c1, c2, t):
    """The colour t of the way from c1 to c2."""
    a, b = _rgb(c1), _rgb(c2)
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


FADE_STEPS, FADE_MS = 6, 18   # a hover fade is about a tenth of a second


def fade(widget, option, target, steps=FADE_STEPS, ms=FADE_MS):
    """Eases a widget's colour option to `target` over a few frames, instead of snapping. Only runs
    while a change is happening (and a new change takes over a running one), so it costs nothing at rest."""
    jobs = widget.__dict__.setdefault("_fade_jobs", {})
    widget.__dict__.setdefault("_fade_final", {})[option] = target
    if option in jobs:
        widget.after_cancel(jobs.pop(option))
    start = str(widget.cget(option))
    if not start.startswith("#"):
        r, g, b = (v >> 8 for v in widget.winfo_rgb(start))
        start = "#%02x%02x%02x" % (r, g, b)
    if start == target:
        return

    def step(i):
        jobs.pop(option, None)
        try:
            widget.config(**{option: target if i == steps else blend(start, target, i / steps)})
        except tk.TclError:   # the widget was destroyed mid-fade
            return
        if i < steps:
            jobs[option] = widget.after(ms, step, i + 1)
    step(1)


def flash(widget, color=None, option="fg", steps=14):
    """A brief highlight on something that just changed, easing back to its resting colour."""
    rest = widget.__dict__.get("_fade_final", {}).get(option) or str(widget.cget(option))
    if not rest.startswith("#"):
        r, g, b = (v >> 8 for v in widget.winfo_rgb(rest))
        rest = "#%02x%02x%02x" % (r, g, b)
    color = color or ("#ffffff" if rest == C["accent_text"] else C["accent_text"])
    try:
        widget.config(**{option: color})
    except tk.TclError:
        return
    fade(widget, option, rest, steps=steps, ms=22)
    widget.__dict__["_fade_final"][option] = rest


def set_value(widget, text):
    """set_text for a number or status that should catch the eye when it changes (not when it first appears)."""
    old = str(widget.cget("text"))
    if old != str(text):
        widget.config(text=text)
        if old not in ("", "—", "…"):
            flash(widget)


class FlashLabel(tk.Label):
    """A label that blinks when its text changes: the status line, so feedback never goes unnoticed."""

    def configure(self, cnf=None, **kw):
        new = kw.get("text")
        changed = new is not None and new != "" and str(new) != str(self.cget("text"))
        result = super().configure(cnf, **kw)
        if changed:
            flash(self, C["fg"], steps=16)
        return result
    config = configure


class Rail(tk.Canvas):
    """The sidebar, drawn as one canvas so its highlight can move like liquid: the selected page's
    pill glides to the next item when you change page, a softer hover pill follows the mouse, and
    icons and labels blend colour as a pill passes under them. Motion only runs while it's happening."""

    ITEM_H, GLIDE_MS, HOVER_MS = 54, 230, 140
    BOTTOM = ("settings",)

    def __init__(self, parent, app, items, logo, scale):
        s = self.s = scale
        self.w = int(68 * s)   # wide enough for the longest label (Accounts) with room either side
        super().__init__(parent, bg=C["panel"], width=self.w, highlightthickness=0, bd=0)
        self.app, self.keys, self.logo = app, [k for k, _, _ in items], logo
        self.h, self.pad = int(self.ITEM_H * s), int(7 * s)
        pw, ph, r = self.w - 2 * self.pad, self.h - int(4 * s), int(8 * s)
        mk = lambda fill, w, h, rad: tk.PhotoImage(master=parent, data=base64.b64encode(shape_image(w, h, rad, fill)))
        rim = dict(border=C["line_hover"], border_w=max(s, 1.0), light=C["glass_light"], light_h=int(14 * s))
        mk2 = lambda fill, w, h, rad, **kw: tk.PhotoImage(
            master=parent, data=base64.b64encode(shape_image(w, h, rad, fill, **kw)))
        self.img_hover = mk2(C["hover"], pw, ph, r, border=C["line"], border_w=max(s, 1.0), light=C["line_hover"],
                             light_h=int(12 * s))
        self.img_sel = mk2(C["sel"], pw, ph, r, **rim)
        self.backdrop_id = self.create_image(0, 0, anchor="nw")   # the gradient, redrawn when the height changes
        self.backdrop, self.backdrop_h = None, 0
        self.edge_id = self.create_line(self.w - 1, 0, self.w - 1, 4000, fill=C["line"])
        self.bar_h, bar_w = int(20 * s), max(int(3 * s), 3)
        self.img_bar = mk(C["accent"], bar_w, self.bar_h, bar_w / 2)
        self.create_image(self.w // 2, int(26 * s), image=logo)
        self.hover_id = self.create_image(self.pad, 0, image=self.img_hover, anchor="nw", state="hidden")
        self.sel_id = self.create_image(self.pad, 0, image=self.img_sel, anchor="nw", state="hidden")
        self.bar_id = self.create_image(0, 0, image=self.img_bar, anchor="nw", state="hidden")
        icons = app.icon_font
        self.icon = {k: self.create_text(self.w // 2, 0, text=g, font=(icons, 13), fill=C["muted"]) for k, g, _ in items}
        self.label = {k: self.create_text(self.w // 2, 0, text=t, font=("Segoe UI", 8), fill=C["muted"]) for k, _, t in items}
        self.mark = self.create_text(self.w // 2, 0, text="HecticWins", font=("Segoe UI Semibold", 7), fill=C["dim"])
        self.pos, self.sel_key, self.sel_y, self.hover_key, self.hover_y = {}, None, None, None, None
        self.jobs, self.shades = {}, {}
        self.tag_bind(self.mark, "<Enter>", lambda _e: self.itemconfigure(self.mark, fill=C["accent_text"]))
        self.tag_bind(self.mark, "<Leave>", lambda _e: self.itemconfigure(self.mark, fill=C["dim"]))
        self.tag_bind(self.mark, "<Button-1>", lambda _e: webbrowser.open(GITHUB_URL))
        self.bind("<Configure>", lambda _e: self.layout())
        self.bind("<Motion>", self.motion)
        self.bind("<Leave>", lambda _e: self.leave())
        self.bind("<Button-1>", self.click)

    def layout(self):
        """Items run down from the logo; Settings sits above the maker's mark at the bottom."""
        height, s = self.winfo_height(), self.s
        if height != self.backdrop_h and height > 1:
            self.backdrop_h = height
            rows = bytearray()
            for y in range(height):   # a faint violet glow at the top, settling into the panel colour, darker at the foot
                f = y / height
                row = blend(C["panel"], "#2e2838", max(0.0, 1 - f / 0.45) * 0.9) if f < 0.45 else \
                    blend(C["panel"], C["bg"], (f - 0.6) / 0.4 * 0.8) if f > 0.6 else C["panel"]
                rows += bytes((*_rgb(row), 255)) * self.w
            self.backdrop = tk.PhotoImage(master=self, data=base64.b64encode(_png(self.w, bytes(rows), height)))
            self.itemconfigure(self.backdrop_id, image=self.backdrop)
            self.tag_lower(self.backdrop_id)
        top = int(54 * s)
        for i, key in enumerate(k for k in self.keys if k not in self.BOTTOM):
            self.pos[key] = top + i * self.h
        for key in self.keys:
            if key in self.BOTTOM:
                self.pos[key] = max(height - int(30 * s) - self.h, top)
        for key, y in self.pos.items():
            self.coords(self.icon[key], self.w // 2, y + int(0.36 * self.h))
            self.coords(self.label[key], self.w // 2, y + int(0.74 * self.h))
        self.coords(self.mark, self.w // 2, height - int(14 * s))
        if self.sel_key in self.pos:
            self.sel_y = self.pos[self.sel_key]
            self.apply()

    def select(self, key):
        self.sel_key = key
        if key not in self.pos:   # not laid out yet; layout() places it
            return
        if self.sel_y is None or not self.winfo_ismapped():
            job = self.jobs.pop("sel_y", None)   # a glide still running would drag the pill back
            if job:
                self.after_cancel(job)
            self.sel_y = self.pos[key]
            self.apply()
        else:
            self.glide("sel_y", self.pos[key], self.GLIDE_MS)

    def glide(self, attr, target, ms):
        """Eases self.<attr> to `target` (ease-out), redrawing each frame; a new glide takes over."""
        job = self.jobs.pop(attr, None)
        if job:
            self.after_cancel(job)
        start, t0 = getattr(self, attr), time.perf_counter()

        def step():
            p = min((time.perf_counter() - t0) * 1000 / ms, 1.0)
            setattr(self, attr, start + (target - start) * (1 - (1 - p) ** 3))
            self.apply()
            self.jobs[attr] = self.after(15, step) if p < 1 else None
            if p >= 1:
                self.jobs.pop(attr, None)
        step()

    def apply(self):
        s, pad = self.s, self.pad
        if self.sel_y is not None:
            self.coords(self.sel_id, pad, self.sel_y + int(2 * s))
            self.coords(self.bar_id, 0, self.sel_y + (self.h - self.bar_h) // 2)
            self.itemconfigure(self.sel_id, state="normal")
            self.itemconfigure(self.bar_id, state="normal")
        if self.hover_y is not None:
            self.coords(self.hover_id, pad, self.hover_y + int(2 * s))
            self.itemconfigure(self.hover_id, state="normal")
        else:
            self.itemconfigure(self.hover_id, state="hidden")
        for key, y in self.pos.items():
            lit = 0.0 if self.sel_y is None else max(0.0, 1 - abs(y - self.sel_y) / self.h)
            near = 0.0 if self.hover_y is None else max(0.0, 1 - abs(y - self.hover_y) / self.h) * 0.85
            rest = blend(C["muted"], C["fg"], near)
            shades = {self.icon[key]: blend(rest, C["accent_text"], lit),
                      self.label[key]: blend(rest, C["fg"], lit)}
            for item, colour in shades.items():
                if self.shades.get(item) != colour:
                    self.shades[item] = colour
                    self.itemconfigure(item, fill=colour)

    def key_at(self, x, y):
        return next((k for k, top in self.pos.items() if top <= y < top + self.h), None)

    def motion(self, event):
        key = self.key_at(event.x, event.y)
        if key is None:
            return self.leave()
        self.configure(cursor="hand2")
        if key == self.hover_key:
            return
        first = self.hover_key is None
        self.hover_key = key
        if first:   # appears in place, then follows the mouse
            self.hover_y = self.pos[key]
            self.apply()
        else:
            self.glide("hover_y", self.pos[key], self.HOVER_MS)

    def leave(self):
        self.configure(cursor="")
        job = self.jobs.pop("hover_y", None)
        if job:
            self.after_cancel(job)
        self.hover_key = self.hover_y = None
        self.apply()

    def click(self, event):
        key = self.key_at(event.x, event.y)
        if key:
            self.app.show_page(key)


SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def page_header(page, title, subtitle):
    """A page's title over a one-line explanation. Returns the header frame (for extra controls
    on the right) and the subtitle label."""
    header = ttk.Frame(page)
    header.pack(fill="x")
    text = ttk.Frame(header)
    text.pack(side="left", fill="x", expand=True)
    ttk.Label(text, text=title, style="Title.TLabel").pack(anchor="w")
    sub = ttk.Label(text, text=subtitle, style="Small.TLabel")
    sub.pack(anchor="w")
    return header, sub


class Segmented(tk.Frame):
    """A row of mutually exclusive options (like '24 hours | 7 days') with hover and selected looks."""

    def __init__(self, parent, options, value, command, scale, bg=None, font=("Segoe UI", 9)):
        super().__init__(parent, bg=C["field"], padx=2, pady=2)
        self.value, self.command, self.labels = value, command, {}
        for key, text in options:
            lbl = tk.Label(self, text=text, bg=C["field"], fg=C["muted"], font=font, cursor="hand2",
                           padx=int(10 * scale), pady=int(3 * scale))
            lbl.pack(side="left", fill="x", expand=True)
            lbl.bind("<Button-1>", lambda _e, k=key: self.set(k, notify=True))
            lbl.bind("<Enter>", lambda _e, k=key: self.paint(hover=k))
            lbl.bind("<Leave>", lambda _e: self.paint())
            self.labels[key] = lbl
        self.paint()

    def set(self, key, notify=False):
        self.value = key
        self.paint(hover=key)
        if notify:
            self.command(key)

    def paint(self, hover=None):
        for key, lbl in self.labels.items():
            on = key == self.value
            fade(lbl, "bg", C["sel"] if on else NAV_HOVER if key == hover else C["field"])
            fade(lbl, "fg", C["fg"] if on or key == hover else C["muted"])


class StatCard(ttk.Frame):
    """A glass card with a small title, one big number and a line under it."""

    def __init__(self, parent, title, scale):
        super().__init__(parent, style="Glass.TFrame", padding=(int(16 * scale), int(12 * scale)))
        self.title = tk.Label(self, text=title, bg=C["panel"], fg=C["muted"], font=("Segoe UI", 9), justify="left")
        self.title.pack(anchor="w")
        self.value = tk.Label(self, text="—", bg=C["panel"], fg=C["fg"], font=("Segoe UI Semibold", 20))
        self.value.pack(anchor="w")
        self.sub = tk.Label(self, text="", bg=C["panel"], fg=C["muted"], font=("Segoe UI", 9), justify="left")
        self.sub.pack(anchor="w")
        # Wrap the caption to the card's width, so a narrow card shows all of it instead of cropping it.
        self.bind("<Configure>", lambda e: [w.config(wraplength=max(e.width - 2 * int(16 * scale), 60))
                                            for w in (self.title, self.sub)])

    blink = True   # False for live gauges (CPU, memory) that change on every reading

    def set(self, value, sub="", color=None):
        set_text(self.value, fg=color or C["fg"])
        (set_value if self.blink else set_text)(self.value, value)
        set_text(self.sub, sub)

    def add_link(self, text, command):
        """A small accent-coloured action under the caption."""
        link = tk.Label(self, text=text, bg=C["panel"], fg=C["accent_text"], font=("Segoe UI", 9), cursor="hand2")
        link.pack(anchor="w", pady=(2, 0))
        link.bind("<Button-1>", lambda _e: command())
        link.bind("<Enter>", lambda _e: fade(link, "fg", C["fg"]))
        link.bind("<Leave>", lambda _e: fade(link, "fg", C["accent_text"]))
        return link


class Chart(tk.Canvas):
    """A line or bar chart on a canvas. Hovering shows the values under the pointer. Series are
    [(values, colour, name)], oldest first; None means no data for that point."""

    def __init__(self, parent, scale, kind="line", unit="%", top=None):
        super().__init__(parent, bg=C["panel"], highlightthickness=0, bd=0, height=int(150 * scale))
        self.scale, self.kind, self.unit, self.fixed_top = scale, kind, unit, top
        self.series, self.labels, self.point_labels = [], ("", ""), []
        self.bind("<Configure>", lambda _e: self.draw())
        self.bind("<Motion>", self.hover)
        self.bind("<Leave>", lambda _e: self.delete("hover"))

    def set(self, series, labels, point_labels=()):
        self.series, self.labels, self.point_labels = series, labels, list(point_labels)
        self.draw()

    def geometry(self):
        s = self.scale
        return int(40 * s), int(22 * s), max(self.winfo_width() - int(10 * s), 1), max(self.winfo_height() - int(22 * s), 1)

    def top(self):
        if self.fixed_top:
            return self.fixed_top
        peak = max([v for values, _, _ in self.series for v in values if v is not None] or [0])
        if peak <= 0:
            return 1
        step = 10 ** math.floor(math.log10(peak))
        return next(m * step for m in (1, 2, 2.5, 5, 10) if m * step >= peak)

    def fmt(self, v):
        return f"{v:.0f}{self.unit}" if self.unit == "%" else short_number(v) + self.unit

    def x_of(self, i, n, left, right):
        if self.kind == "bars":
            return left + (i + 0.5) * (right - left) / n
        return left + i * (right - left) / max(n - 1, 1)

    def draw(self):
        self.delete("all")
        left, top_y, right, bottom = self.geometry()
        if right - left < 20 or bottom - top_y < 20:
            return
        top = self.top()
        small = ("Segoe UI", 8)
        empty = not any(v for values, _, _ in self.series for v in values)
        for frac in (0, 0.5, 1):
            y = bottom - frac * (bottom - top_y)
            self.create_line(left, y, right, y, fill=C["hover"])
            if not empty:
                self.create_text(left - int(6 * self.scale), y, text=self.fmt(top * frac), anchor="e",
                                 fill=C["muted"], font=small)
        self.create_text(left, bottom + int(4 * self.scale), text=self.labels[0], anchor="nw", fill=C["muted"], font=small)
        self.create_text(right, bottom + int(4 * self.scale), text=self.labels[1], anchor="ne", fill=C["muted"], font=small)
        x = right   # legend, right-aligned above the chart
        for _, color, name in reversed(self.series):
            item = self.create_text(x, int(9 * self.scale), text=name, anchor="e", fill=C["muted"], font=small)
            x = self.bbox(item)[0] - int(6 * self.scale)
            dot = int(4 * self.scale)
            self.create_oval(x - 2 * dot, int(9 * self.scale) - dot, x, int(9 * self.scale) + dot, fill=color, width=0)
            x -= 2 * dot + int(14 * self.scale)
        n = max((len(v) for v, _, _ in self.series), default=0)
        if empty:
            self.create_text((left + right) / 2, (top_y + bottom) / 2, fill=C["muted"], font=("Segoe UI", 9),
                             text="No data yet. AltVault fills this in while it runs.")
            return
        y_of = lambda v: bottom - min(v / top, 1) * (bottom - top_y)
        if self.kind == "bars":
            slot = (right - left) / n
            for k, (values, color, _) in enumerate(self.series):
                # Later series sit inside earlier ones (narrower), so a part-of-a-whole stays readable.
                width = max(slot * (0.66 if k == 0 else 0.4), 1)
                for i, v in enumerate(values):
                    if v:
                        cx = self.x_of(i, n, left, right)
                        self.create_rectangle(cx - width / 2, y_of(v), cx + width / 2, bottom, fill=color, width=0)
            return
        for values, color, _ in self.series:
            runs, run = [], []
            for i, v in enumerate(values):
                if v is None:
                    if run:
                        runs.append(run)
                    run = []
                else:
                    run.append((self.x_of(i, n, left, right), y_of(v)))
            if run:
                runs.append(run)
            for run in runs:
                if len(run) == 1:
                    (px, py), = run
                    self.create_oval(px - 2, py - 2, px + 2, py + 2, fill=color, width=0)
                    continue
                area = [run[0][0], bottom] + [c for pt in run for c in pt] + [run[-1][0], bottom]
                self.create_polygon(area, fill=blend(C["panel"], color, 0.18), width=0)
                self.create_line([c for pt in run for c in pt], fill=color, width=max(2 * self.scale, 1.5))

    def hover(self, event):
        self.delete("hover")
        left, top_y, right, bottom = self.geometry()
        n = max((len(v) for v, _, _ in self.series), default=0)
        if not n or not left <= event.x <= right:
            return
        if self.kind == "bars":
            i = min(int((event.x - left) / (right - left) * n), n - 1)
        else:
            i = round((event.x - left) / (right - left) * (n - 1))
        x = self.x_of(i, n, left, right)
        self.create_line(x, top_y, x, bottom, fill=C["muted"], dash=(2, 3), tags="hover")
        lines = [self.point_labels[i]] if i < len(self.point_labels) else []
        lines += [f"{name}: {'—' if values[i] is None else self.fmt(values[i])}" for values, _, name in self.series]
        text = self.create_text(0, 0, text="\n".join(lines), anchor="nw", fill=C["fg"], font=("Segoe UI", 8),
                                justify="left", tags="hover")
        x0, y0, x1, y1 = self.bbox(text)
        pad = int(6 * self.scale)
        tx = x + pad * 2 if x + pad * 2 + (x1 - x0) < right else x - pad * 2 - (x1 - x0)
        self.coords(text, tx, top_y + pad)
        x0, y0, x1, y1 = self.bbox(text)
        box = self.create_rectangle(x0 - pad, y0 - pad, x1 + pad, y1 + pad, fill=C["deep"], outline=C["sel"], tags="hover")
        self.tag_raise(text, box)


def chart_card(parent, title, chart_args, scale):
    """A panel holding a titled chart; returns (frame, chart)."""
    card = ttk.Frame(parent, style="Glass.TFrame", padding=(int(16 * scale), int(12 * scale)))
    tk.Label(card, text=title, bg=C["panel"], fg=C["fg"], font=("Segoe UI Semibold", 10)).pack(anchor="w")
    chart = Chart(card, scale, **chart_args)
    chart.pack(fill="both", expand=True, pady=(int(4 * scale), 0))
    return card, chart


def hour_labels(hours, now=None):
    """Hover labels for hourly points, oldest first: 'Tue 14:00'."""
    end = History.hour(now)
    return [time.strftime("%a %H:00", time.localtime(h * 3600)) for h in range(end - hours + 1, end + 1)]


RANGES = (("day", "24 hours"), ("week", "7 days"))
RANGE_HOURS = {"day": 24, "week": 24 * 7}


class StatsPage(ttk.Frame):
    """How the farm has been doing: time in game, launches, Anti-AFK and unexpected closes, from
    History's hourly totals. Redrawn when shown and once a minute while visible."""

    def __init__(self, app, parent):
        super().__init__(parent)
        self.app, self.job = app, None
        s = app.scale
        header, _ = page_header(self, "Stats", "How your accounts have been doing. Counted by AltVault while it runs.")
        self.range = Segmented(header, RANGES, "day", lambda _k: self.refresh(), s)
        self.range.pack(side="right", anchor="s")
        cards = ttk.Frame(self)
        cards.pack(fill="x", pady=(int(14 * s), 0))
        self.cards = {}
        for i, (key, title) in enumerate((("ingame", "Time in a game"), ("eggs", "Eggs hatched (estimate)"),
                                          ("launches", "Launches that worked"), ("pings", "Anti-AFK pings"),
                                          ("closed", "Closed outside AltVault"))):
            self.cards[key] = StatCard(cards, title, s)
            self.cards[key].grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else int(10 * s), 0))
            cards.columnconfigure(i, weight=1, uniform="card")
        self.cards["eggs"].add_link("Set total…", app.ask_eggs_total)
        charts = ttk.Frame(self)
        charts.pack(fill="both", expand=True, pady=(int(10 * s), 0))
        card, self.windows_chart = chart_card(charts, "Roblox windows", {"kind": "bars", "unit": ""}, s)
        card.pack(fill="both", expand=True)
        card, self.afk_chart = chart_card(charts, "Anti-AFK", {"kind": "bars", "unit": ""}, s)
        card.pack(fill="both", expand=True, pady=(int(10 * s), 0))

    def on_show(self):
        self.refresh()

    def on_hide(self):
        if self.job:
            self.after_cancel(self.job)
            self.job = None

    def refresh(self):
        if self.job:
            self.after_cancel(self.job)
        self.job = self.after(60_000, self.refresh)
        h = self.app.history
        hours = RANGE_HOURS[self.range.value]
        span = "24 hours" if hours == 24 else "7 days"
        total = lambda f: h.total(f, hours)
        ingame = percent_of(total("ingame"), total("open"))
        self.cards["ingame"].set("—" if ingame is None else f"{ingame:.1f}%",
                                 f"of the time Roblox was open, last {span}",
                                 None if ingame is None else C["green"] if ingame >= 95 else C["yellow"] if ingame >= 80 else C["red"])
        self.cards["eggs"].set(*self.app.egg_card(hours, span))
        ok, failed = total("launch_ok"), total("launch_fail")
        rate = percent_of(ok, ok + failed)
        self.cards["launches"].set("—" if rate is None else f"{rate:.1f}%",
                                   f"{ok + failed:g} launch{'es' if ok + failed != 1 else ''}, {failed:g} failed")
        pings, missed = total("pings"), total("missed")
        self.cards["pings"].set(short_number(pings), f"{missed:g} missed and retried")
        closed = total("closed")
        self.cards["closed"].set(f"{closed:g}", "crashes, kicks or closed by hand",
                                 C["red"] if closed else None)
        labels = hour_labels(hours)
        ends = (f"{span} ago", "now")
        self.windows_chart.set([(h.series("open", hours, per="open_n"), C["outline"], "Open"),
                                (h.series("ingame", hours, per="ingame_n"), C["green"], "In a game")], ends, labels)
        self.afk_chart.set([(h.series("pings", hours), C["accent"], "Pings"),
                            (h.series("missed", hours), C["red"], "Missed")], ends, labels)


PULSE_RANGES = (("hour", "Last hour"), ("day", "24 hours"), ("week", "7 days"))


class PulsePage(ttk.Frame):
    """Your PC and Roblox: CPU and memory now (every 2 s while this page is open) and over time
    (a sample a minute, kept by App whether or not the page is open)."""

    LIVE_MS = 2000

    def __init__(self, app, parent):
        super().__init__(parent)
        self.app, self.job = app, None
        self.pc, self.rbx = PcMeter(), CpuMeter()
        s = app.scale
        header, _ = page_header(self, "Pulse", "Your PC and Roblox. Live while this page is open, "
                                "and sampled once a minute otherwise.")
        self.range = Segmented(header, PULSE_RANGES, "hour", lambda _k: self.draw_charts(), s)
        self.range.pack(side="right", anchor="s")
        cards = ttk.Frame(self)
        cards.pack(fill="x", pady=(int(14 * s), 0))
        self.cards = {}
        for i, (key, title) in enumerate((("pc_cpu", "PC CPU"), ("pc_ram", "PC memory"),
                                          ("rbx_cpu", "Roblox CPU"), ("rbx_ram", "Roblox memory"))):
            self.cards[key] = StatCard(cards, title, s)
            self.cards[key].blink = False
            self.cards[key].grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else int(10 * s), 0))
            cards.columnconfigure(i, weight=1, uniform="card")
        charts = ttk.Frame(self)
        charts.pack(fill="both", expand=True, pady=(int(10 * s), 0))
        card, self.cpu_chart = chart_card(charts, "CPU", {"top": 100}, s)
        card.pack(fill="both", expand=True)
        card, self.ram_chart = chart_card(charts, "Memory", {"top": 100}, s)
        card.pack(fill="both", expand=True, pady=(int(10 * s), 0))

    def on_show(self):
        self.pc.last = None
        self.rbx.last.clear()
        self.live()
        self.draw_charts()

    def on_hide(self):
        if self.job:
            self.after_cancel(self.job)
            self.job = None

    def live(self):
        self.job = None
        if self.app.root.state() in ("iconic", "withdrawn") or self.app.page != "pulse":
            return
        sample = measure(roblox_windows(), self.pc, self.rbx)
        free, total = sample["free"], sample["total"]
        gb = 2 ** 30
        fmt = lambda v: "…" if v is None else f"{v:.0f}%"
        self.cards["pc_cpu"].set(fmt(sample["pc_cpu"]), f"{os.cpu_count() or 1} logical processors")
        self.cards["pc_ram"].set(fmt(sample["pc_ram"]), f"{(total - free) / gb:.1f} of {total / gb:.0f} GB in use",
                                 C["red"] if free < 0.1 * total else C["yellow"] if free < 0.25 * total else None)
        n = sample["windows"]
        self.cards["rbx_cpu"].set(fmt(sample["rbx_cpu"]) if n else "—", f"{n} window{'s' if n != 1 else ''}")
        ram = sample["rbx_bytes"]
        self.cards["rbx_ram"].set("—" if ram is None or not n else f"{ram / gb:.1f} GB",
                                  f"{ram / n / 2 ** 20:,.0f} MB per window" if ram and n else "")
        self.job = self.after(self.LIVE_MS, self.live)

    def draw_charts(self):
        h = self.app.history
        key = self.range.value
        if key == "hour":
            recent = list(self.app.recent)
            now = time.time()
            ends = ("60 min ago", "now")
            # One point per minute; the newest samples go on the right, gaps stay empty.
            slots = {round((now - t) / 60): sample for t, sample in recent}
            points = [slots.get(m) for m in range(59, -1, -1)]
            pick = lambda f: [p[f] if p else None for p in points]
            labels = [time.strftime("%H:%M", time.localtime(now - m * 60)) for m in range(59, -1, -1)]
            cpu = [(pick("pc_cpu"), C["accent"], "PC"), (pick("rbx_cpu"), C["green"], "Roblox")]
            ram = [(pick("pc_ram"), C["accent"], "PC"), (pick("rbx_ram"), C["green"], "Roblox")]
        else:
            hours = RANGE_HOURS[key]
            ends = ("24 hours ago" if key == "day" else "7 days ago", "now")
            labels = hour_labels(hours)
            avg = lambda f: h.series(f, hours, per=f + "_n")
            cpu = [(avg("pc_cpu"), C["accent"], "PC"), (avg("rbx_cpu"), C["green"], "Roblox")]
            ram = [(avg("pc_ram"), C["accent"], "PC"), (avg("rbx_ram"), C["green"], "Roblox")]
        self.cpu_chart.set(cpu, ends, labels)
        self.ram_chart.set(ram, ends, labels)


def measure(windows, pc_meter, rbx_meter):
    """One reading of the PC and the Roblox windows. CPU values are None on a meter's first call."""
    usage = {pid: process_usage(pid) for _, pid, _ in windows}
    rams = [u[1] if u else None for u in usage.values()]
    free, total = memory_status()
    rbx_bytes = None if None in rams else sum(rams)
    return {"windows": len(windows), "free": free, "total": total,
            "pc_cpu": pc_meter.percent(), "pc_ram": percent_of(total - free, total),
            "rbx_cpu": rbx_meter.percent({pid: u[0] for pid, u in usage.items() if u and u[0] is not None})
            if usage else 0.0,
            "rbx_bytes": rbx_bytes, "rbx_ram": percent_of(rbx_bytes, total) if rbx_bytes is not None else None}


def get_game_icons(place_ids):
    """{place_id (str): png_bytes} of games' icons. Public, so no cookie is sent."""
    ids = ",".join(str(int(i)) for i in place_ids)
    status, _, body = _request("https://thumbnails.roblox.com/v1/places/gameicons"
                               f"?placeIds={ids}&returnPolicy=PlaceHolder&size=50x50&format=Png&isCircular=false")
    if status != 200:
        return {}
    result = {}
    for item in json.loads(body).get("data", []):
        if item.get("state") == "Completed" and item.get("imageUrl"):
            img_status, _, png = _request(item["imageUrl"])
            if img_status == 200:
                result[str(item["targetId"])] = png
    return result


class GamesPage(ttk.Frame):
    """Favourite and recent games in one list, with their icons. Pick one to join it from the
    Accounts page, browse its servers, star it, or open it on roblox.com."""

    def __init__(self, app, parent):
        super().__init__(parent)
        self.app = app
        self.icons = {}   # place id -> PhotoImage
        s = app.scale
        page_header(self, "Games", "Favourites first, then the games you've played. Double-click one to pick it for joining.")
        add = ttk.Frame(self)
        add.pack(fill="x", pady=(int(14 * s), 0))
        self.add_var = tk.StringVar()
        entry = ttk.Entry(add, textvariable=self.add_var)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _e: self.add_game())
        add_placeholder(entry, self.add_var, "Paste a game link or place ID to add it to favourites")
        ttk.Button(add, text="Add to favourites", command=self.add_game).pack(side="left", padx=(8, 0))

        btns = ttk.Frame(self)
        btns.pack(side="bottom", fill="x", pady=(int(12 * s), 0))
        self.pick_btn = ttk.Button(btns, text="Pick for joining", style="Accent.TButton", command=self.pick)
        self.pick_btn.pack(side="right")
        self.star_btn = ttk.Button(btns, text="☆ Favourite", command=self.toggle_star)
        self.star_btn.pack(side="right", padx=(0, 6))
        self.servers_btn = ttk.Button(btns, text="Servers...", command=self.servers)
        self.servers_btn.pack(side="right", padx=(0, 6))
        self.open_btn = ttk.Button(btns, text="Open on Roblox", command=self.open_page)
        self.open_btn.pack(side="left")
        self.remove_btn = ttk.Button(btns, text="Remove", command=self.remove)
        self.remove_btn.pack(side="left", padx=(6, 0))

        table = ttk.Frame(self)
        table.pack(fill="both", expand=True, pady=(int(12 * s), 0))
        self.tree = ttk.Treeview(table, columns=("name", "kind", "id"), show="tree headings", selectmode="browse",
                                 style="Games.Treeview")
        ttk.Style(self).configure("Games.Treeview", rowheight=max(int(62 * s), 58))
        self.tree.column("#0", width=int(80 * s), stretch=False, anchor="center")
        for col, title, width in (("name", "Game", 360), ("kind", "", 120), ("id", "Place ID", 160)):
            self.tree.heading(col, text=title, anchor="w")
            self.tree.column(col, width=int(width * s), anchor="w", stretch=col == "name")
        self.tree.tag_configure("fav", foreground=C["fg"])
        self.tree.tag_configure("recent", foreground=C["muted"])
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.pack(side="left", fill="both", expand=True)
        auto_scrollbar(self.tree, scroll)
        add_row_hover(self.tree)
        self.empty = ttk.Label(table, text="No games yet. Join one from Accounts, or paste a link above.",
                               style="PanelMuted.TLabel")
        self.tree.bind("<Double-1>", lambda e: self.tree.identify_row(e.y) and self.pick())
        self.tree.bind("<Return>", lambda _e: self.pick())
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.update_buttons())
        self.blank = tk.PhotoImage(width=50, height=50)

    def on_show(self):
        self.refresh()

    def games(self):
        """[(place_id, name, starred)]: favourites, then recents that aren't favourites."""
        favs = self.app.favorites()
        ids = {f["id"] for f in favs}
        return [(f["id"], f["name"], True) for f in favs] + \
            [(r["id"], r["name"], False) for r in self.app.recents() if r["id"] not in ids]

    def refresh(self):
        chosen = self.chosen()
        self.tree.delete(*self.tree.get_children())
        games = self.games()
        for place, name, starred in games:
            self.tree.insert("", "end", iid=place, image=self.icons.get(place, self.blank),
                             values=("  " + name, "★ Favourite" if starred else "Recent", place),
                             tags=("fav" if starred else "recent",))
        if chosen and self.tree.exists(chosen):
            self.tree.selection_set(chosen)
        elif games:
            self.tree.selection_set(games[0][0])
        if games:
            self.empty.place_forget()
        else:
            self.empty.place(relx=0.5, rely=0.5, anchor="center")
        self.update_buttons()
        self.load_icons([g[0] for g in games])

    def load_icons(self, places):
        """Shows cached icons straight away and fetches missing ones in the background."""
        missing = []
        for place in places:
            if place in self.icons:
                continue
            path = os.path.join(ICON_DIR, f"{place}.png")
            try:
                self.icons[place] = tk.PhotoImage(file=path)
                if self.tree.exists(place):
                    self.tree.item(place, image=self.icons[place])
            except tk.TclError:
                missing.append(place)
        if not missing:
            return

        def work():
            try:
                pngs = get_game_icons(missing[:100])
            except (RobloxError, ValueError):
                return
            os.makedirs(ICON_DIR, exist_ok=True)
            for place, png in pngs.items():
                with open(os.path.join(ICON_DIR, f"{place}.png"), "wb") as f:
                    f.write(png)
            self.app.ui(lambda: self.load_icons([p for p in missing if p in pngs]))
        threading.Thread(target=work, daemon=True).start()

    def chosen(self):
        sel = self.tree.selection()
        return sel[0] if sel else None

    def update_buttons(self):
        place = self.chosen()
        state = "normal" if place else "disabled"
        for btn in (self.pick_btn, self.star_btn, self.servers_btn, self.open_btn, self.remove_btn):
            btn.config(state=state)
        starred = place in {f["id"] for f in self.app.favorites()}
        self.star_btn.config(text="★ Unfavourite" if starred else "☆ Favourite")

    def name_of(self, place):
        return next((name for p, name, _ in self.games() if p == place), f"Place {place}")

    def pick(self):
        place = self.chosen()
        if place:
            self.app.place_var.set(f"{self.name_of(place)}  ({place})")
            self.app.job_var.set("")
            self.app.show_page("accounts")
            self.app.status.config(text=f"Picked {self.name_of(place)}. Select accounts and Join.")

    def servers(self):
        if self.chosen():
            ServerBrowser(self.app, self.chosen())

    def open_page(self):
        if self.chosen():
            webbrowser.open(f"https://www.roblox.com/games/{int(self.chosen())}")

    def toggle_star(self):
        place = self.chosen()
        if not place:
            return
        favs = self.app.favorites()
        if place in {f["id"] for f in favs}:
            self.app.settings["favorites"] = [f for f in favs if f["id"] != place]
        else:
            self.app.settings["favorites"] = favs + [{"id": place, "name": self.name_of(place)}]
        save_settings(self.app.settings)
        self.app.update_recents_box()
        self.refresh()

    def remove(self):
        place = self.chosen()
        if not place:
            return
        self.app.settings["favorites"] = [f for f in self.app.favorites() if f["id"] != place]
        self.app.settings["recents"] = [r for r in self.app.recents() if r["id"] != place]
        save_settings(self.app.settings)
        self.app.update_recents_box()
        self.refresh()

    def add_game(self):
        text = self.add_var.get()
        if parse_private_server(text):
            self.app.status.config(text="Private server links aren't saved. Paste them on the Accounts page.")
            return
        place = parse_place_id(text)
        if not place:
            self.app.status.config(text="That isn't a game link or place ID.")
            return
        self.add_var.set("")
        cookie = next((a["cookie"] for a in self.app.accounts if a["id"] not in self.app.expired), None)

        def save(name):
            favs = [f for f in self.app.favorites() if f["id"] != place]
            self.app.settings["favorites"] = favs + [{"id": place, "name": name or f"Place {place}"}]
            save_settings(self.app.settings)
            self.app.update_recents_box()
            self.refresh()
            self.tree.selection_set(place)
            self.app.status.config(text=f"Added {name or 'the game'} to favourites.")
        threading.Thread(target=lambda: self.app.ui(save, get_place_name(cookie, place) if cookie else None),
                         daemon=True).start()


class ProfilesPage(ttk.Frame):
    """Saved setups: a name, a set of accounts and a game. Launching one selects those accounts and
    joins them, exactly as if you'd done it on the Accounts page."""

    def __init__(self, app, parent):
        super().__init__(parent)
        self.app = app
        s = app.scale
        page_header(self, "Profiles", "Saved setups: which accounts go to which game. Launch one with a click.")
        btns = ttk.Frame(self)
        btns.pack(side="bottom", fill="x", pady=(int(12 * s), 0))
        ttk.Button(btns, text="+ New profile", command=lambda: self.edit(None)).pack(side="left")
        self.launch_btn = ttk.Button(btns, text="Launch", style="Accent.TButton", command=self.launch)
        self.launch_btn.pack(side="right")
        self.edit_btn = ttk.Button(btns, text="Edit...", command=lambda: self.edit(self.chosen()))
        self.edit_btn.pack(side="right", padx=(0, 6))
        self.delete_btn = ttk.Button(btns, text="Delete", command=self.delete)
        self.delete_btn.pack(side="right", padx=(0, 6))
        table = ttk.Frame(self)
        table.pack(fill="both", expand=True, pady=(int(14 * s), 0))
        self.tree = ttk.Treeview(table, columns=("name", "accounts", "game"), show="headings", selectmode="browse",
                                 style="Rows.Treeview")
        ttk.Style(self).configure("Rows.Treeview", rowheight=int(40 * s))
        for col, title, width in (("name", "Profile", 220), ("accounts", "Accounts", 320), ("game", "Game", 260)):
            self.tree.heading(col, text="   " + title, anchor="w")
            self.tree.column(col, width=int(width * s), anchor="w")
        self.tree.pack(side="left", fill="both", expand=True)
        auto_scrollbar(self.tree, ttk.Scrollbar(table, orient="vertical", command=self.tree.yview))
        add_row_hover(self.tree)
        self.empty = ttk.Label(table, text="No profiles yet. Click “+ New profile” to save a setup, for example\n"
                               "“Farm: 12 alts in Pet Simulator”.", style="PanelMuted.TLabel", justify="center")
        self.tree.bind("<Double-1>", lambda e: self.tree.identify_row(e.y) and self.launch())
        self.tree.bind("<Return>", lambda _e: self.launch())
        self.tree.bind("<Delete>", lambda _e: self.delete())
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.update_buttons())

    def on_show(self):
        self.refresh()

    def profiles(self):
        items = self.app.settings.get("profiles")
        return [p for p in items if isinstance(p, dict) and isinstance(p.get("name"), str)
                and isinstance(p.get("accounts"), list)] if isinstance(items, list) else []

    def save(self, profiles):
        self.app.settings["profiles"] = profiles
        save_settings(self.app.settings)
        self.refresh()

    def refresh(self):
        chosen = self.chosen()
        self.tree.delete(*self.tree.get_children())
        for i, p in enumerate(self.profiles()):
            accounts = [self.app.account(a) for a in p["accounts"]]
            names = [self.app.who(a) for a in accounts if a]
            shown = ", ".join(names[:4]) + (f" +{len(names) - 4} more" if len(names) > 4 else "")
            self.tree.insert("", "end", iid=str(i), values=(
                "   " + p["name"], f"   {len(names)}  ·  {shown}" if names else "   none left",
                "   " + (clean_name(p.get("place_name")) or f"Place {p.get('place')}")))
        if chosen and self.tree.exists(chosen):
            self.tree.selection_set(chosen)
        elif self.profiles():
            self.tree.selection_set("0")
        if self.profiles():
            self.empty.place_forget()
        else:
            self.empty.place(relx=0.5, rely=0.5, anchor="center")
        self.update_buttons()

    def chosen(self):
        sel = self.tree.selection()
        return sel[0] if sel else None

    def update_buttons(self):
        state = "normal" if self.chosen() else "disabled"
        for btn in (self.launch_btn, self.edit_btn, self.delete_btn):
            btn.config(state=state)

    def edit(self, iid):
        profiles = self.profiles()
        current = profiles[int(iid)] if iid is not None else None
        result = ProfileDialog(self.app, current).result
        if result is None:
            return
        if current is None:
            profiles.append(result)
        else:
            profiles[int(iid)] = result
        self.save(profiles)
        self.tree.selection_set(str(profiles.index(result)))

    def delete(self):
        iid = self.chosen()
        if iid is None:
            return
        profiles = self.profiles()
        if ask_yes_no("Delete profile", f"Delete the profile “{profiles[int(iid)]['name']}”?\n\n"
                               "The accounts themselves stay.", parent=self.app.root, yes="Delete", danger=True):
            del profiles[int(iid)]
            self.save(profiles)

    def launch(self):
        iid = self.chosen()
        if iid is not None:
            self.app.launch_profile(self.profiles()[int(iid)])


class ProfileDialog(tk.Toplevel):
    """Create or edit a profile. .result is the saved profile dict, or None if cancelled."""

    def __init__(self, app, profile=None):
        super().__init__(app.root, bg=C["bg"])
        self.app, self.result = app, None
        s = app.scale
        self.title("Edit profile" if profile else "New profile")
        self.transient(app.root)
        self.geometry(f"{int(460 * s)}x{int(560 * s)}")
        self.minsize(int(380 * s), int(420 * s))
        frm = ttk.Frame(self, padding=18)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="Edit profile" if profile else "New profile", style="Title.TLabel").pack(anchor="w")
        ttk.Label(frm, text="Name", style="Muted.TLabel").pack(anchor="w", pady=(12, 2))
        self.name_var = tk.StringVar(value=profile["name"] if profile else "")
        name = ttk.Entry(frm, textvariable=self.name_var)
        name.pack(fill="x")
        ttk.Label(frm, text="Game", style="Muted.TLabel").pack(anchor="w", pady=(10, 2))
        place = profile.get("place") if profile else None
        self.place_var = tk.StringVar(value=f"{profile.get('place_name') or 'Place'}  ({place})" if place else "")
        game = ttk.Combobox(frm, textvariable=self.place_var, values=app.place_box["values"])
        game.pack(fill="x")
        add_placeholder(game, self.place_var, "Paste a game link, or pick one")
        ttk.Label(frm, text="Accounts (click to tick)", style="Muted.TLabel").pack(anchor="w", pady=(10, 2))
        btns = ttk.Frame(frm)
        btns.pack(side="bottom", fill="x", pady=(12, 0))
        ttk.Button(btns, text="Save", style="Accent.TButton", command=self.save).pack(side="right")
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right", padx=(0, 6))
        self.error = ttk.Label(btns, text="", style="Error.TLabel")
        self.error.pack(side="left")
        listing = ttk.Frame(frm)
        listing.pack(fill="both", expand=True)
        self.list = tk.Listbox(listing, selectmode="multiple", activestyle="none", bg=C["panel"], fg=C["fg"],
                               selectbackground=C["accent"], selectforeground="white", highlightthickness=0,
                               bd=0, font=FONT, exportselection=False)
        scroll = ttk.Scrollbar(listing, orient="vertical", command=self.list.yview)
        self.list.pack(side="left", fill="both", expand=True)
        auto_scrollbar(self.list, scroll)
        chosen = set(profile["accounts"]) if profile else {a["id"] for a in app.selected_accounts()}
        for i, acc in enumerate(app.accounts):
            group = app.group_of(acc)
            self.list.insert("end", f"  {app.who(acc)}" + (f"   ·  {group}" if group else ""))
            if acc["id"] in chosen:
                self.list.selection_set(i)
        self.bind("<Escape>", lambda _e: self.destroy())
        name.bind("<Return>", lambda _e: self.save())
        dark_title_bar(self)
        self.grab_set()
        name.focus_set()
        self.wait_window()

    def save(self):
        name = self.name_var.get().strip()[:60]
        text = self.place_var.get()
        place = None if parse_private_server(text) else parse_place_id(text)
        accounts = [self.app.accounts[i]["id"] for i in self.list.curselection()]
        if not name:
            self.error.config(text="Give the profile a name.")
        elif parse_private_server(text):
            self.error.config(text="Private server links aren't saved. Use a game link.")
        elif not place:
            self.error.config(text="Pick a game, or paste a game link.")
        elif not accounts:
            self.error.config(text="Tick at least one account.")
        else:
            m = re.match(r"\s*(?:★\s*)?(.+?)\s+\(\d+\)\s*$", text)
            known = {r["id"]: r["name"] for r in self.app.recents() + self.app.favorites()}
            self.result = {"name": name, "accounts": accounts, "place": place,
                           "place_name": known.get(place) or (m.group(1) if m else f"Place {place}")}
            self.destroy()


def auto_scrollbar(widget, scroll):
    """Shows the scrollbar only while the list is longer than its box."""
    def update(first, last):
        scroll.set(first, last)
        needed = float(first) > 0 or float(last) < 1
        if needed and not scroll.winfo_manager():
            scroll.pack(side="right", fill="y")
        elif not needed and scroll.winfo_manager():
            scroll.pack_forget()
    widget.configure(yscrollcommand=update)


def add_row_hover(tree):
    """Highlights the row under the pointer, like a web list."""
    tree.tag_configure("hover", background=NAV_HOVER)
    state = {"row": None}

    def clear():
        row = state["row"]
        if row and tree.exists(row):
            tree.item(row, tags=tuple(t for t in tree.item(row, "tags") if t != "hover"))
        state["row"] = None

    def move(event):
        row = tree.identify_row(event.y)
        if row == state["row"]:
            return
        clear()
        if row and row not in tree.selection():
            tree.item(row, tags=tuple(tree.item(row, "tags")) + ("hover",))
            state["row"] = row
    tree.bind("<Motion>", move, add="+")
    tree.bind("<Leave>", lambda _e: clear(), add="+")
    tree.bind("<<TreeviewSelect>>", lambda _e: clear(), add="+")


# --- access key (begin) ---
class ActivationWindow(tk.Toplevel):
    """Shown at start when this PC has no valid key: the PC code to send to the key's maker, and a box
    for the key. .activated is True once a good key has been saved; closing the window ends AltVault."""

    def __init__(self, root, reason=""):
        super().__init__(root, bg=C["bg"])
        self.activated = False
        scale = root.winfo_fpixels("1i") / 96
        self.title(f"Activate {APP_TITLE}")
        self.resizable(False, False)
        frm = ttk.Frame(self, padding=(int(26 * scale), int(22 * scale)))
        frm.pack(fill="both", expand=True)
        wrap = int(440 * scale)
        ttk.Label(frm, text=f"Activate {APP_TITLE}", style="Title.TLabel").pack(anchor="w")
        ask = f"Message {LICENSE_CONTACT}" if LICENSE_CONTACT else "Message the person who made AltVault"
        ttk.Label(frm, text=f"This copy needs a key, and a key works on one PC only. {ask} with the PC code "
                  "below to ask for one. If they approve, they'll send you a key to paste here.",
                  wraplength=wrap, justify="left").pack(anchor="w", pady=(8, 0))
        ttk.Label(frm, text="Your PC code", style="Muted.TLabel").pack(anchor="w", pady=(16, 3))
        row = ttk.Frame(frm)
        row.pack(fill="x")
        self.code_var = tk.StringVar(value=pc_code())
        code = ttk.Entry(row, textvariable=self.code_var, state="readonly", font=("Consolas", 12), width=26)
        code.pack(side="left", fill="x", expand=True)
        self.copy_btn = ttk.Button(row, text="Copy", command=self.copy_code)
        self.copy_btn.pack(side="left", padx=(6, 0))
        self.request_btn = ttk.Button(row, text="Copy request message", command=self.copy_request)
        self.request_btn.pack(side="left", padx=(6, 0))
        ttk.Label(frm, text="Your key", style="Muted.TLabel").pack(anchor="w", pady=(14, 3))
        self.key_var = tk.StringVar()
        entry = ttk.Entry(frm, textvariable=self.key_var)
        entry.pack(fill="x")
        add_placeholder(entry, self.key_var, "Paste the whole key here")
        entry.bind("<Return>", lambda _e: self.activate())
        self.status = ttk.Label(frm, text=reason, style="Error.TLabel", wraplength=wrap, justify="left")
        self.status.pack(anchor="w", pady=(10, 0))
        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(14, 0))
        ttk.Button(btns, text="Activate", style="Accent.TButton", command=self.activate).pack(side="right")
        ttk.Button(btns, text="Quit", command=self.destroy).pack(side="right", padx=(0, 6))
        self.bind("<Escape>", lambda _e: self.destroy())
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        self.geometry(f"+{(self.winfo_screenwidth() - w) // 2}+{(self.winfo_screenheight() - h) // 3}")
        dark_title_bar(self)
        self.grab_set()
        entry.focus_set()
        self.wait_window()

    def copy_code(self):
        self.clipboard_clear()
        self.clipboard_append(self.code_var.get())
        set_text(self.copy_btn, "Copied")
        self.after(1500, lambda: self.winfo_exists() and set_text(self.copy_btn, "Copy"))

    def copy_request(self):
        self.clipboard_clear()
        self.clipboard_append(f"Hi, could I have an {APP_TITLE} key? My PC code is {self.code_var.get()}")
        set_text(self.request_btn, "Copied: paste it in a message")
        self.after(2500, lambda: self.winfo_exists() and set_text(self.request_btn, "Copy request message"))

    def activate(self):
        state, why = check_key(self.key_var.get())
        if state != "ok":
            self.status.config(text=why)
            return
        try:
            save_key(self.key_var.get())
        except OSError as e:
            self.status.config(text=f"Couldn't save the key: {e.strerror or e}")
            return
        self.activated = True
        self.destroy()

# --- access key (end) ---


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
        self.taskbar_busy = False     # a worker is moving Roblox windows on or off the taskbar
        self.history = History()      # hourly totals for Stats, Pulse and the Live panel
        self.last_sample_at = None    # monotonic time of the previous minute reading
        self.eggs_dirty = False       # the egg total changed since it was last written to settings.json
        self.recent = collections.deque(maxlen=60)   # (time, measure()) once a minute, for Pulse
        self.pc_meter, self.rbx_meter = PcMeter(), CpuMeter()
        self.next_sample = time.monotonic() + 5
        self.next_save = time.monotonic() + 300
        self.expected_close = set()   # Roblox pids AltVault was asked to close, so they aren't counted
        self.tick_job = None

        if not hasattr(root, "altvault_images"):   # main() styles the window first when it asks for a key
            apply_theme(root, self.scale)
        root.title(APP_TITLE)
        saved = self.settings.get("geometry")
        if self.settings.get("layout") != LAYOUT_VERSION:   # windows sized for the old layout start fresh
            saved, self.settings["layout"] = None, LAYOUT_VERSION
        root.geometry(fit_to_screen(safe_geometry(saved)))
        root.minsize(*min_size(self.scale))
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
        self.tick_job = self.root.after(500, self.tick)
        self.root.after(1000, self.refresh_presence)
        # Nobody sees presence while AltVault is minimised, so it pauses then and catches up on restore.
        self.presence_stale = False
        root.bind("<Map>", self.on_restore)

    # ------------------------------------------------------------ layout
    NAV = (("accounts", "\uE716", "Accounts"), ("profiles", "\uE77B", "Profiles"), ("games", "\uE7FC", "Games"),
           ("live", "\uE7F4", "Live"), ("stats", "\uE9D2", "Stats"), ("pulse", "\uE95E", "Pulse"),
           ("settings", "\uE713", "Settings"))

    def build(self):
        s = self.scale
        families = set(tkfont.families(self.root))
        self.icon_font = next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if f in families),
                              "Segoe UI Symbol")
        # Options used by more than one page.
        self.multi_var = tk.BooleanVar(value=self.settings.get("multi", True))
        self.same_server_var = tk.BooleanVar(value=self.settings.get("same_server", True))
        self.auto_tile_var = tk.BooleanVar(value=self.settings.get("auto_tile", True))
        self.anti_afk_var = tk.BooleanVar(value=self.settings.get("anti_afk", False))
        self.stream_var = tk.BooleanVar(value=self.settings.get("stream_mode", False))
        self.hide_taskbar_var = tk.BooleanVar(value=self.settings.get("hide_taskbar", False))

        # Sidebar: the app icon, then one item per page; Settings sits at the bottom.
        logo_px = int(26 * s)
        self.logo = tk.PhotoImage(data=base64.b64encode(_png(logo_px, _render_icon(logo_px))))
        self.rail = Rail(self.root, self, self.NAV, self.logo, s)
        self.rail.pack(side="left", fill="y")

        main = ttk.Frame(self.root, padding=(16, 14, 16, 12))
        main.pack(side="left", fill="both", expand=True)
        # The Anti-AFK switch and the status line, the same on every page.
        footer = ttk.Frame(main)
        footer.pack(side="bottom", fill="x", pady=(10, 0))
        ttk.Checkbutton(footer, text="Anti-AFK", variable=self.anti_afk_var,
                        command=self.toggle_anti_afk).pack(side="left")
        self.status = FlashLabel(footer, text="", bg=C["bg"], fg=C["muted"], font=FONT)
        self.status.pack(side="right")

        host = ttk.Frame(main)
        host.pack(fill="both", expand=True)
        self.pages = {"accounts": self.build_accounts(host), "profiles": ProfilesPage(self, host),
                      "games": GamesPage(self, host), "live": LivePage(self, host), "stats": StatsPage(self, host),
                      "pulse": PulsePage(self, host), "settings": SettingsPage(self, host)}
        self.page = None
        self.show_page("accounts")
        for i, (key, _, _) in enumerate(self.NAV[:-1], 1):   # Ctrl+1..6; Settings is Ctrl+,
            self.root.bind(f"<Control-Key-{i}>", lambda _e, k=key: self.show_page(k))
        self.root.bind("<Control-comma>", lambda _e: self.show_page("settings"))

    def show_page(self, key):
        if key == self.page:
            return
        old = self.pages.get(self.page)
        if old is not None:
            getattr(old, "on_hide", lambda: None)()
            old.pack_forget()
        self.page = key
        self.pages[key].pack(fill="both", expand=True)
        self.rail.select(key)
        getattr(self.pages[key], "on_show", lambda: None)()

    def build_accounts(self, host):
        outer = ttk.Frame(host)
        header, self.summary = page_header(outer, "Accounts", "")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.refresh())
        search = ttk.Entry(header, textvariable=self.search_var, width=28)
        search.pack(side="right", anchor="s")
        add_placeholder(search, self.search_var, "Search accounts")

        # Where to join: the game, then an optional server, side by side with labels above.
        game = ttk.Frame(outer)
        game.pack(fill="x", pady=(14, 0))
        ttk.Label(game, text="Game", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(game, text="Server (optional)", style="Muted.TLabel").grid(row=0, column=3, sticky="w", padx=(14, 0))
        self.place_var = tk.StringVar(value=self.settings.get("place", ""))
        self.place_box = ttk.Combobox(game, textvariable=self.place_var)
        self.place_box.grid(row=1, column=0, sticky="ew", pady=(3, 0))
        add_placeholder(self.place_box, self.place_var, "Paste a game or private server link, or pick one")
        self.star_btn = ttk.Button(game, text="☆", width=3, command=self.toggle_favorite)
        self.star_btn.grid(row=1, column=1, padx=(6, 0), pady=(3, 0), sticky="ns")
        ttk.Button(game, text="Servers...", command=self.open_servers).grid(row=1, column=2, padx=(6, 0), pady=(3, 0),
                                                                           sticky="ns")
        self.place_var.trace_add("write", lambda *_: self.update_star())
        self.job_var = tk.StringVar()
        job = ttk.Entry(game, textvariable=self.job_var)
        job.grid(row=1, column=3, sticky="ew", padx=(14, 0), pady=(3, 0))
        add_placeholder(job, self.job_var, "Any server, or paste a Job ID")
        game.columnconfigure(0, weight=3)
        game.columnconfigure(3, weight=2)
        self.update_recents_box()

        btns = ttk.Frame(outer)
        btns.pack(side="bottom", fill="x", pady=(12, 0))
        ttk.Button(btns, text="+ Add account", command=lambda: AddAccountDialog(self)).pack(side="left")
        self.join_btn = ttk.Button(btns, text="Join", style="Accent.TButton", command=self.join_selected)
        self.join_btn.pack(side="right")
        self.close_btn = ttk.Button(btns, text="Close all", command=self.close_all, state="disabled")
        self.close_btn.pack(side="right", padx=(0, 6))
        self.tile_btn = ttk.Button(btns, text="Tile", command=self.tile, state="disabled")
        self.tile_btn.pack(side="right", padx=(0, 6))

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
        self.tree.pack(side="left", fill="both", expand=True)
        auto_scrollbar(self.tree, scroll)
        self.empty_hint = ttk.Label(table, text="No accounts yet. Click “+ Add account” to start.",
                                    style="Muted.TLabel", background=C["panel"])
        # Double-clicking a group header only opens or closes it; it never launches the whole group.
        self.tree.bind("<Double-1>", self.on_double_click)
        self.tree.bind("<Return>", lambda _e: self.join_selected())
        self.tree.bind("<Delete>", lambda _e: self.remove_selected())
        self.tree.bind("<Control-a>", lambda _e: self.tree.selection_set(self.account_rows()))
        self.tree.bind("<Button-1>", self.tree_click)
        self.tree.bind("<<TreeviewOpen>>", lambda _e: self.group_toggled(True))
        self.tree.bind("<<TreeviewClose>>", lambda _e: self.group_toggled(False))
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.update_join_label())
        self.tree.bind("<Button-3>", self.show_menu)
        add_row_hover(self.tree)
        return outer

    def update_join_label(self):
        """The Join button says how many accounts it will launch."""
        if self.joining:
            return
        n = len(self.selected_accounts())
        self.join_btn.config(text="Join" if n == 0 else f"Join {n} account{'s' if n != 1 else ''}",
                             state="normal" if n else "disabled")

    def update_window_buttons(self, count):
        """Tile and Close all only do something while Roblox windows are open."""
        state = "normal" if count else "disabled"
        for btn in (self.tile_btn, self.close_btn):
            if str(btn.cget("state")) != state:
                btn.config(state=state)

    # ------------------------------------------------------------ helpers
    def ui(self, fn, *args):
        self.root.after(0, lambda: fn(*args))

    def set_status(self, text):
        self.ui(lambda: self.status.config(text=text))

    def account(self, account_id):
        return next((a for a in self.accounts if a["id"] == account_id), None)

    def who(self, acc):
        """How an account is named on screen: '@username', or 'Account 3' in stream mode."""
        if self.stream_var.get():
            n = next((i for i, a in enumerate(self.accounts, 1) if a is acc), None)
            return f"Account {n}" if n else "an account"
        return "@" + acc["name"]

    def window_title(self, acc):
        return WINDOW_PREFIX + self.who(acc).lstrip("@")

    def live_state(self, hwnd, pid, title):
        """(colour, name) for a Live tile: red while the window is frozen, grey for a window AltVault
        can't tie to an account (opened by hand, say), green once its account is in a game, and
        yellow while it's still joining."""
        acc = self.account(self.running.get(pid))
        name = self.who(acc) if acc else "Roblox" if self.stream_var.get() else title
        if not responsive(hwnd):
            return "red", name
        if not acc:
            return "grey", name
        p = self.presence.get(acc["id"])
        return ("green" if p and p.get("userPresenceType") == 2 else "yellow"), name

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
                             stream_mode=self.stream_var.get(), hide_taskbar=self.hide_taskbar_var.get(),
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
            where = clean_name(p.get("lastLocation")) or "a game"
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
                image=self.chevron(group not in collapsed),
                values=(f"{group}   ·   {len(members)} account{'s' if len(members) != 1 else ''}", "", "", ""))
        stream = self.stream_var.get()   # names and avatars stay off screen
        for acc in self.accounts:
            names = self.who(acc) if stream else f"{acc['name']} {acc['displayName']}"
            haystack = f"{names} {acc.get('note', '')} {self.group_of(acc) or ''}".lower()
            if query and query not in haystack:
                continue
            text, tag = self.status_of(acc)
            self.tree.insert(parents.get(self.group_of(acc), ""), "end", iid=str(acc["id"]),
                             image=self.blank_avatar if stream else self.avatars.get(acc["id"], self.blank_avatar),
                             values=(self.who(acc) if stream else acc["displayName"], "" if stream else "@" + acc["name"],
                                     text, acc.get("note", "")),
                             tags=(tag,) if tag else ())
        for group, iid in parents.items():   # hide groups the search filtered empty
            if query and not self.tree.get_children(iid):
                self.tree.delete(iid)
        self.tree.selection_set([i for i in selection if self.tree.exists(i)])
        shown = ("display",) + (() if stream else ("user",)) + ("status",) + \
            (("note",) if any(a.get("note") for a in self.accounts) else ())
        if tuple(self.tree["displaycolumns"]) != shown:
            self.tree["displaycolumns"] = shown
            self.tree.heading("display", text="Account" if stream else "Display name")
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

    def chevron(self, opened):
        return self.root.altvault_images["tree-open" if opened else "tree-closed"]

    def tree_click(self, event):
        """Clicking a group's chevron opens or closes it without selecting the whole group."""
        row = self.tree.identify_row(event.y)
        if row.startswith(GROUP_PREFIX) and "image" in self.tree.identify_element(event.x, event.y):
            opened = not self.tree.item(row, "open")
            self.tree.item(row, open=opened)
            self.group_toggled(opened, row)
            return "break"
        return None

    def group_toggled(self, opened, iid=None):
        iid = iid or self.tree.focus()
        if not iid.startswith(GROUP_PREFIX):
            return
        self.tree.item(iid, image=self.chevron(opened))
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
        who = self.who(accounts[0]) if len(accounts) == 1 else f"{len(accounts)} accounts"
        self.status.config(text=f"Moved {who} to {group}." if group else f"Took {who} out of their group.")

    def ask_new_group(self, accounts):
        name = ask_string("New group", "Group name (for example Mains or Alts):", parent=self.root)
        name = (name or "").strip()[:40]
        if name:
            self.set_group(accounts, name)

    def rename_group(self, old):
        name = ask_string("Rename group", "New name:", initialvalue=old, parent=self.root)
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
        return self.saved_games("recents")

    def favorites(self):
        return self.saved_games("favorites")

    def saved_games(self, key):
        """Saved recent or favourite games, with names made drawable (older saves kept emoji)."""
        items = self.settings.get(key)
        return [{**g, "name": clean_name(g["name"]) or f"Place {g['id']}"} for g in items
                if isinstance(g, dict) and "id" in g and "name" in g] if isinstance(items, list) else []

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
        if event.widget is not self.root:
            return
        if self.presence_stale:
            self.presence_stale = False
            self.refresh_presence(reschedule=False)
        if self.page == "live":
            self.pages["live"].resume()

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
            self.update_window_buttons(len(windows))
            alive = {pid for _, pid, _ in windows}
            by_title = {}   # a window is titled with the username, or 'Account n' in stream mode
            for i, a in enumerate(self.accounts, 1):
                by_title[(WINDOW_PREFIX + a["name"]).lower()] = by_title[f"{WINDOW_PREFIX}account {i}"] = a["id"]
            before = dict(self.running)
            for hwnd, pid, title in windows:
                if pid not in self.running:
                    account_id = by_title.get(title.lower())
                    if account_id:
                        self.running[pid] = account_id   # window from before AltVault was reopened
                acc = self.account(self.running.get(pid))
                if acc and title != self.window_title(acc) and responsive(hwnd):
                    set_window_title(hwnd, self.window_title(acc))
            self.running = {pid: a for pid, a in self.running.items() if pid in alive}
            gone = set(before) - alive
            closed = len(gone - self.expected_close)
            if closed:
                self.history.add(closed=closed)
            self.expected_close -= gone
            if self.running != before:
                self.refresh()
            now = time.monotonic()
            if now >= self.next_sample:
                self.next_sample = now + 60
                self.sample_minute(windows)
            if now >= self.next_save:
                self.next_save = now + 300
                self.save_history()
            want = not self.hide_taskbar_var.get()
            self.set_taskbar([hwnd for hwnd, _, _ in windows if on_taskbar(hwnd) != want], want)
            if self.page == "live" and self.root.state() not in ("iconic", "withdrawn"):
                self.pages["live"].sync(windows)
        except Exception:
            log.exception("Window check failed")
        self.tick_job = self.root.after(TICK_INTERVAL, self.tick)

    def sample_minute(self, windows):
        """Once a minute, AltVault or not on screen: one reading for Stats and Pulse. A few cheap
        system calls; Roblox itself isn't touched."""
        reading = measure(windows, self.pc_meter, self.rbx_meter)
        # "In a game" is only knowable for windows tied to an account, so only those are counted as
        # open; a window opened by hand would otherwise make a healthy session look half-broken.
        linked = [w for w in windows if self.running.get(w[1])]
        ingame = sum(1 for _, pid, _ in linked
                     if (self.presence.get(self.running.get(pid)) or {}).get("userPresenceType") == 2)
        now = time.monotonic()   # real time since the last reading (capped, so a sleeping PC doesn't count)
        minutes = 1.0 if self.last_sample_at is None else min(now - self.last_sample_at, 120) / 60
        self.last_sample_at = now
        self.count_eggs(ingame, minutes)
        self.history.sample(open=len(linked), ingame=ingame if linked else None,
                            pc_cpu=reading["pc_cpu"], pc_ram=reading["pc_ram"],
                            rbx_cpu=reading["rbx_cpu"] if windows else None,
                            rbx_ram=reading["rbx_ram"] if windows else None)
        self.recent.append((time.time(), reading))
        if self.page == "pulse":
            self.pages["pulse"].draw_charts()

    def hatch_rate(self):
        """One alt's hatch speed in eggs per minute, as typed on the Live page; None if not set."""
        h = self.settings.get("hatch")
        if isinstance(h, dict) and isinstance(h.get("value"), (int, float)) and h["value"] > 0 \
                and h.get("unit") in ("min", "hour", "sec"):
            return per_minute(h["value"], h["unit"])
        return None

    def count_eggs(self, alts_in_game, minutes):
        """Adds the eggs `alts_in_game` accounts would have hatched in `minutes` at the typed speed.
        An estimate: the game's own counter is out of AltVault's reach."""
        rate = self.hatch_rate()
        if not rate or alts_in_game <= 0 or minutes <= 0:
            return 0.0
        eggs = rate * alts_in_game * minutes
        self.history.add(eggs=eggs)
        self.settings["eggs_total"] = float(self.settings.get("eggs_total") or 0) + eggs
        self.settings.setdefault("eggs_since", time.time())
        self.eggs_dirty = True
        return eggs

    def egg_card(self, hours, span):
        """(value, caption) for the Stats card: eggs over the chosen range, and the running total."""
        rate = self.hatch_rate()
        eggs = self.history.total("eggs", hours)
        total = float(self.settings.get("eggs_total") or 0)
        if rate is None and not total:
            return "—", "Type one alt's hatch speed on the Live page to start counting."
        since = self.settings.get("eggs_since")
        when = f" since {time.strftime('%d %b', time.localtime(since))}" if since else ""
        return short_number(eggs), f"last {span}" + "\n" + f"Total{when}: {short_number(total)}"

    def set_eggs_total(self, value):
        """Makes the running total match the game's own counter (the estimate carries on from there)."""
        self.settings["eggs_total"] = max(float(value), 0.0)
        self.settings["eggs_since"] = time.time()
        self.eggs_dirty = True
        self.save_history()

    def ask_eggs_total(self):
        def check(text):
            try:
                n = float(text.replace(",", "").strip())
            except ValueError:
                return "Type a number, such as 12500."
            return None if 0 <= n < 1e15 else "That number is out of range."
        text = Dialog(self.root, "Set eggs hatched", "Type the eggs hatched total your game shows right now. "
                      "AltVault will carry on counting from it. Use 0 to start over.", "question",
                      (("Cancel", None), ("Set total", True)),
                      entry=f"{float(self.settings.get('eggs_total') or 0):.0f}", validate=check).result
        if text is not None:
            self.set_eggs_total(float(text.replace(",", "").strip()))
            if self.page == "stats":
                self.pages["stats"].refresh()
            self.status.config(text=f"Eggs hatched total set to {short_number(self.settings['eggs_total'])}.")

    def save_history(self):
        try:
            self.history.save()
        except OSError as e:
            log.warning("Couldn't save history: %s", e)
        if self.eggs_dirty:   # the running total lives in settings.json; written with the history, not every minute
            self.eggs_dirty = False
            try:
                save_settings(self.settings)
            except OSError as e:
                log.warning("Couldn't save the egg total: %s", e)

    def close_windows(self, pids):
        """Closes Roblox processes on purpose, so they don't count as unexpected closes."""
        for pid in pids:
            self.expected_close.add(pid)
            kill_process(pid)

    def launch_profile(self, profile):
        accounts = [a for a in (self.account(i) for i in profile["accounts"]) if a]
        if not accounts:
            show_info("Launch profile", "None of this profile's accounts are in AltVault any more. "
                                "Edit the profile to pick accounts.", parent=self.root)
            return
        self.search_var.set("")   # rows hidden by a search can't be selected
        self.place_var.set(f"{profile.get('place_name') or 'Place'}  ({profile['place']})")
        self.job_var.set("")
        self.tree.selection_set([str(a["id"]) for a in accounts])
        self.show_page("accounts")
        self.update_join_label()
        self.join_selected()

    # ------------------------------------------------------------ account actions
    def add_many(self, items):
        """Saves several checked accounts at once: [(user, {'cookie', 'group', 'note'})]. Entries
        from an AltVault export also carry 'fps_cap', 'low_graphics', 'muted' and 'afk_skip'; they
        fill in what isn't set here yet and never undo a setting made on this PC."""
        by_id = {a["id"]: a for a in self.accounts}
        caps = self.fps_caps()
        flags = {key: self.flagged(key) for key in ("low_graphics", "muted")}
        flags["afk_skip"] = self.afk_skip_ids()
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
            if entry.get("fps_cap") and str(user["id"]) not in caps:
                caps[str(user["id"])] = entry["fps_cap"]
            for key, ids in flags.items():
                if entry.get(key):
                    ids.add(user["id"])
            self.expired.discard(user["id"])
        if any(set(e) & {"fps_cap", *flags} for _, e in items):
            self.settings["fps_caps"] = caps
            self.settings.update({key: sorted(ids) for key, ids in flags.items()})
            save_settings(self.settings)
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
        self.status.config(text=f"Saved {self.who(self.account(user['id']))}.")
        threading.Thread(target=self.fetch_missing_avatars, daemon=True).start()
        self.refresh_presence(reschedule=False)

    def remove_selected(self):
        sel = self.selected_accounts()
        if not sel:
            return
        names = ", ".join(self.who(a) for a in sel)
        if not ask_yes_no("Remove accounts", f"Remove {names} from AltVault?\n\n"
                          "This only deletes the saved login here, not the Roblox account.",
                          parent=self.root, yes="Remove", danger=True):
            return
        self.accounts = [a for a in self.accounts if a not in sel]
        self.save()
        self.refresh()

    def edit_note(self, acc):
        note = ask_string("Edit note", f"Note for {self.who(acc)}:",
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
        """Right-click menu: the common action first, then grouped submenus."""
        row = self.tree.identify_row(event.y)
        if not row:
            return
        if row not in self.tree.selection():
            self.tree.selection_set(row)
        sel = self.selected_accounts()
        if not sel:
            return
        many = f" ({len(sel)} accounts)" if len(sel) > 1 else ""
        menu = themed_menu(self.root)
        menu.add_command(label="Join game" + many, command=self.join_selected)
        menu.add_command(label="Join a player's server...", command=lambda: self.ask_join_player(sel))
        if len(sel) == 1 and not row.startswith(GROUP_PREFIX):
            acc = sel[0]
            wins = self.windows_of(acc)
            p = self.presence.get(acc["id"]) or {}
            if wins:
                menu.add_command(label="Show Roblox window", command=lambda: focus_window(wins[0][0]))
                menu.add_command(label="Close Roblox window", command=lambda: self.close_windows([pid for _, pid in wins]))
            if p.get("userPresenceType") == 2 and p.get("gameId"):
                menu.add_command(label="Use this account's server for joining",
                                 command=lambda: self.use_server_of(p))
        menu.add_separator()
        menu.add_cascade(label="Performance", menu=self.performance_menu(menu, sel))
        if row.startswith(GROUP_PREFIX):
            group = row[len(GROUP_PREFIX):]
            menu.add_separator()
            menu.add_command(label="Rename group...", command=lambda: self.rename_group(group))
            menu.add_command(label="Ungroup (keeps the accounts)", command=lambda: self.set_group(sel, None))
        else:
            menu.add_cascade(label="Group", menu=self.group_menu(menu, sel))
            if len(sel) == 1:
                menu.add_cascade(label="Account", menu=self.account_menu(menu, sel[0]))
            menu.add_separator()
            menu.add_command(label="Remove" + many + "...", command=self.remove_selected)
        menu.tk_popup(event.x_root, event.y_root)

    def performance_menu(self, parent, accounts):
        menu = themed_menu(parent)
        menu.add_cascade(label="FPS cap", menu=self.fps_menu(menu, accounts))
        self.add_performance_items(menu, accounts)
        menu.add_separator()
        on = not any(self.afk_skipped(a) for a in accounts)
        menu.add_command(label=("✓ " if on else "") + "Anti-AFK",
                         command=lambda: self.set_afk_skip(accounts, on))
        return menu

    def account_menu(self, parent, acc):
        menu = themed_menu(parent)
        menu.add_command(label="Copy username", command=lambda: self.copy(acc["name"]))
        menu.add_command(label="Open profile", command=lambda: webbrowser.open(
            f"https://www.roblox.com/users/{acc['id']}/profile"))
        menu.add_command(label="Edit note...", command=lambda: self.edit_note(acc))
        menu.add_command(label="Log in again...", command=lambda: AddAccountDialog(self, acc))
        menu.add_separator()
        menu.add_command(label="Move up", command=lambda: self.move(acc, -1))
        menu.add_command(label="Move down", command=lambda: self.move(acc, 1))
        return menu

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
        who = self.who(accounts[0]) if len(accounts) == 1 else f"{len(accounts)} accounts"
        self.status.config(text=f"{who}: {f'{cap} FPS cap' if cap else 'no FPS cap'}, from their next join.")

    def ask_fps_cap(self, accounts):
        lo, hi = FPS_RANGE
        current = {self.fps_cap_of(a) for a in accounts}
        cap = ask_integer("FPS cap", f"Frames per second ({lo}–{hi}):", parent=self.root,
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
        who = self.who(accounts[0]) if len(accounts) == 1 else f"{len(accounts)} accounts"
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
        name = ask_string("Join a player", "Their Roblox username:", parent=self.root)
        name = (name or "").strip()
        if not name:
            return
        viewer = next((a for a in accounts if a["id"] not in self.expired), None)
        if not viewer:
            show_error("Join a player", "The selected accounts need to log in again first.", parent=self.root)
            return
        self.status.config(text=f"Finding {name}...")

        def work():
            try:
                place, job, found = server_of_player(viewer["cookie"], name)
            except RobloxError as e:
                message = str(e)   # e itself is cleared when the except block ends
                self.ui(lambda: (self.status.config(text=""),
                                 show_error("Join a player", message, parent=self.root)))
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
            show_info("Servers", "A private server link already points at one server, so there's "
                                "no list to browse. Just click Join.", parent=self.root)
            return
        place_id = parse_place_id(text)
        if not place_id:
            show_info("Servers", "Enter a game first (a place ID or roblox.com/games link).",
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

    # ------------------------------------------------------------ privacy
    def toggle_stream(self):
        on = self.stream_var.get()
        self.save_settings()
        self.refresh()
        self.tick_now()
        self.status.config(text="Stream mode is on: names and avatars are hidden." if on else "Stream mode is off.")

    def toggle_taskbar(self):
        hide = self.hide_taskbar_var.get()
        self.save_settings()
        self.tick_now()
        self.status.config(text="Roblox windows are off the taskbar. Open them from the Live page." if hide
                           else "Roblox windows are back on the taskbar.")

    def tick_now(self):
        """Applies window titles and taskbar changes straight away instead of at the next tick."""
        if self.tick_job:
            self.root.after_cancel(self.tick_job)
        self.tick()

    def set_taskbar(self, hwnds, shown):
        """Moves windows on or off the taskbar on a worker thread, since each change waits on Roblox.
        Anything left over (a window that was frozen, say) is picked up by the next tick."""
        if self.taskbar_busy or self.afk_busy or not hwnds:   # never hide a window mid-ping
            return
        self.taskbar_busy = True

        def work():
            try:
                for hwnd in hwnds:
                    set_on_taskbar(hwnd, shown)
            finally:
                self.taskbar_busy = False
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ export / import
    def export_payload(self):
        caps, skip = self.fps_caps(), self.afk_skip_ids()
        low, muted = self.flagged("low_graphics"), self.flagged("muted")
        accounts = [{"id": a["id"], "name": a["name"], "displayName": a["displayName"], "cookie": a["cookie"],
                     "note": a.get("note", ""), "group": self.group_of(a), "fps_cap": caps.get(str(a["id"])),
                     "low_graphics": a["id"] in low, "muted": a["id"] in muted, "afk_skip": a["id"] in skip}
                    for a in self.accounts]
        return {"app": APP_TITLE, "version": APP_VERSION, "accounts": accounts, "favorites": self.favorites()}

    def export_accounts(self):
        n = len(self.accounts)
        if not n:
            show_info("Export accounts", "There are no accounts to export yet.", parent=self.root)
            return
        path = filedialog.asksaveasfilename(parent=self.root, title="Export accounts", defaultextension=EXPORT_EXT,
                                            initialfile="AltVault accounts" + EXPORT_EXT,
                                            filetypes=[("AltVault export", "*" + EXPORT_EXT)])
        if not path:
            return
        who = f"all {n} accounts" if n > 1 else "the account"
        password = PasswordDialog(self, "Export accounts", "Choose a password for the file. Anyone with the file "
                                  f"and its password can log into {who}, so keep both private. A forgotten "
                                  "password can't be recovered.", confirm=True).password
        if not password:
            return
        try:
            data = seal_export(self.export_payload(), password)
            tmp = path + ".tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except OSError as e:
            log.warning("Export failed: %s", e)
            show_error("Export accounts", f"Couldn't save the file:\n{e}", parent=self.root)
            return
        log.info("Exported %d accounts", n)
        self.status.config(text=f"Exported {n} account{'s' if n != 1 else ''} to {os.path.basename(path)}.")

    def import_accounts(self):
        path = filedialog.askopenfilename(parent=self.root, title="Import accounts",
                                          filetypes=[("AltVault export", "*" + EXPORT_EXT)])
        if not path:
            return
        try:
            if os.path.getsize(path) > MAX_EXPORT_BYTES:
                raise OSError("The file is too big to be an AltVault export.")
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            show_error("Import accounts", f"Couldn't read the file:\n{e}", parent=self.root)
            return
        payload = PasswordDialog(self, "Import accounts", "Type the password this file was exported with.",
                                 check=lambda pw: open_export(data, pw)).result
        if payload is None:
            return
        self.status.config(text="Checking imported accounts...")

        def report(text):
            show_info("Import accounts", text, parent=self.root)
        threading.Thread(target=check_and_add, args=(self, export_entries(payload), report, payload.get("favorites")),
                         daemon=True).start()

    def merge_favorites(self, items):
        favs = self.favorites()
        known = {f["id"] for f in favs}
        for item in items if isinstance(items, list) else []:
            place = str(item.get("id", "")) if isinstance(item, dict) else ""
            if re.fullmatch(r"\d{1,20}", place) and place not in known:
                favs.append({"id": place, "name": str(item.get("name") or f"Place {place}")[:100]})
                known.add(place)
        self.settings["favorites"] = favs
        save_settings(self.settings)
        self.update_recents_box()

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
        if not ask_yes_no("Close all Roblox", "Close every Roblox window?", parent=self.root, yes="Close all",
                          danger=True):
            return
        self.expected_close |= {pid for _, pid, _ in roblox_windows()}
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

    def set_afk_skip(self, accounts, skip_them):
        skip = self.afk_skip_ids()
        for acc in accounts:
            (skip.add if skip_them else skip.discard)(acc["id"])
        self.settings["afk_skip"] = sorted(skip)
        save_settings(self.settings)
        self.refresh()
        who = self.who(accounts[0]) if len(accounts) == 1 else f"{len(accounts)} accounts"
        self.status.config(text=f"Anti-AFK {'off' if skip_them else 'on'} for {who}.")

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
        return self.who(acc) if acc else f"window {hwnd}"

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
        self.history.add(pings=len(pinged), missed=len(missed))
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
        if not ask_yes_no(
                "Multi-Roblox", "Roblox was opened before Multi-Roblox could start, so new windows "
                "would close it.\n\nClose all Roblox windows now and continue?", parent=self.root,
                yes="Close Roblox and join", danger=True):
            return False
        if close_roblox_and_enable_multi():
            self.status.config(text="Multi-Roblox is on.")
            return True
        show_error("Multi-Roblox", "Couldn't turn on Multi-Roblox. Close Roblox from "
                             "Task Manager, then Join again.", parent=self.root)
        return False

    # ------------------------------------------------------------ joining
    def join_selected(self):
        if self.joining:
            return
        sel = self.selected_accounts()
        if not sel:
            show_info("Join", "Select one or more accounts first (Ctrl+click for several).",
                                parent=self.root)
            return
        private = parse_private_server(self.place_var.get())
        if private and not private.get("share_code") and not private.get("place_id"):
            show_error("Join", "That private server link doesn't say which game it's for. Copy the "
                                 "link again from the private server's menu on roblox.com.", parent=self.root)
            return
        place_id = private.get("place_id") if private else parse_place_id(self.place_var.get())
        if not private and not place_id:
            show_error("Join", "Enter a place ID, a roblox.com/games/... link or a private server link.",
                                 parent=self.root)
            return
        job_id = "" if private else parse_job_id(self.job_var.get())
        if job_id is None:
            show_error("Join", "The server / Job ID should look like "
                                 "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx (or leave it empty).", parent=self.root)
            return
        expired = [a for a in sel if a["id"] in self.expired]
        if expired:
            show_error("Join", "These accounts need to log in again first (right-click > Log in again):\n" +
                                 "\n".join(self.who(a) for a in expired), parent=self.root)
            return
        if len(sel) > 1 and not self.multi_var.get():
            if not ask_yes_no("Join", "Multi-Roblox is off, so each launch will close the "
                              "previous window. Continue anyway?", parent=self.root, yes="Continue"):
                return
        if not self.ensure_multi():
            return
        self.save_settings()
        self.joining = True
        self.join_btn.config(state="disabled", text=f"{SPINNER[0]}  Joining...")
        self.spin(1)
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
                self.set_status(f"Launching {self.who(acc)} ({i + 1}/{len(accounts)})...")
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
                    problems.append(f"{self.who(acc)}: login expired — right-click > Log in again.")
                    self.ui(lambda: self.history.add(launch_fail=1))
                    continue
                except (RobloxError, OSError) as e:
                    log.warning("Launch of %s failed: %s", acc["name"], e)
                    problems.append(f"{self.who(acc)}: {e}")
                    self.ui(lambda: self.history.add(launch_fail=1))
                    continue
                pid = self.wait_for_new_window(before)
                if pid is None:
                    problems.append(f"{self.who(acc)}: Roblox didn't open a window within {WINDOW_TIMEOUT}s.")
                    self.ui(lambda: self.history.add(launch_fail=1))
                    continue
                self.ui(lambda: self.history.add(launch_ok=1))
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
                    self.set_status(f"Waiting for {self.who(acc)} to reach a server...")
                    job_id = self.wait_for_server(acc, place_id) or ""
                    if not job_id:
                        problems.append(f"Couldn't see which server {self.who(acc)} joined, so the other "
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

    def spin(self, i):
        """Animates the Join button while a join runs. It stops by itself when the join ends."""
        if self.joining:
            set_text(self.join_btn, f"{SPINNER[i % len(SPINNER)]}  Joining...")
            self.root.after(90, self.spin, i + 1)

    def join_finished(self, problems):
        self.joining = False
        self.join_btn.config(state="normal")
        self.update_join_label()
        self.status.config(text="Done." if not problems else "Done, with problems.")
        self.refresh()
        if self.auto_tile_var.get() and len(roblox_windows()) > 1:
            self.tile()
            # Roblox can resize its window once the game finishes loading, so tile again after that.
            self.root.after(5000, self.tile)
        self.root.after(8000, lambda: self.refresh_presence(reschedule=False))
        if problems:
            show_warning("Join", "\n\n".join(problems), parent=self.root)

    def on_close(self):
        """The window's X: hide to the tray so Anti-AFK keeps working. Quit is in the tray menu."""
        self.settings["geometry"] = self.root.geometry()
        self.save_settings()
        if not self.tray:
            self.save_history()
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
        self.save_history()
        for hwnd, _, _ in roblox_windows():   # never leave Roblox windows without a taskbar button
            if not on_taskbar(hwnd):
                set_on_taskbar(hwnd, True)
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
            show_error(APP_TITLE, f"Something went wrong:\n{val}\n\nDetails are in {LOG_FILE}")
    root.report_callback_exception = report

    try:
        accounts = load_accounts()
    except (OSError, ValueError) as e:
        # Don't start with an empty list, or the next save would wipe the real file.
        log.exception("Couldn't read accounts")
        show_error(APP_TITLE, f"Couldn't read saved accounts:\n{e}\n\nFile: {DATA_FILE}")
        root.destroy()
        return
    # --- access key (begin) ---
    state, why = license_state()
    if state != "ok":   # no valid key on this PC: ask for one, or end here
        apply_theme(root, root.winfo_fpixels("1i") / 96)
        root.withdraw()
        if not ActivationWindow(root, "" if state == "none" else why).activated:
            log.info("Not activated; exiting")
            root.destroy()
            return
        root.deiconify()
        log.info("Activated")
    # --- access key (end) ---
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
