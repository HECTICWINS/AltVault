"""AltVault - a Roblox account manager that runs on signed Python (no unsigned .exe),
so it works with Windows Smart App Control turned on.

Everything lives in %LOCALAPPDATA%\\AltVault. Account cookies are encrypted with Windows
DPAPI (only your Windows user on this PC can decrypt them) and are never written to the log.
"""
import ctypes
import ctypes.wintypes as wt
import http.client
import json
import logging
import math
import os
import random
import re
import struct
import subprocess
import threading
import time
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
import zlib
from tkinter import messagebox, simpledialog, ttk

APP_TITLE = "AltVault"
APP_VERSION = "2.0.0"
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
# Anti-AFK: Roblox kicks after 20 idle minutes. Worst case between pings is the longest interval
# + waiting for the user to pause + retries = 11 + 5 + 1.5 min, still under 20.
AFK_INTERVAL = (480, 660)        # seconds between pings, randomised so it isn't a fixed rhythm
AFK_IDLE = 2                     # seconds of no mouse/keyboard use before stealing focus
AFK_MAX_WAIT = 300               # if the user never pauses that long, ping anyway
AFK_RETRY = 30                   # seconds before retrying windows that refused focus
AFK_MAX_RETRIES = 3
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


def _dpapi(func, data):
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    if not func(ctypes.byref(blob_in), None, None, None, None,
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


def force_foreground(hwnd):
    """Brings a window to the front even while another app has focus. Returns success.

    Windows' foreground lock blocks background apps from switching focus. Sharing input state with
    the foreground thread plus a harmless synthetic input gets around it; an Alt tap and
    SwitchToThisWindow are later fallbacks. Focus changes are asynchronous, so check and retry."""
    me = _kernel32.GetCurrentThreadId()
    for attempt in range(6):
        fg = _user32.GetForegroundWindow()
        if fg == hwnd:
            return True
        fg_thread = _user32.GetWindowThreadProcessId(fg, None) if fg else 0
        attached = bool(fg_thread) and fg_thread != me and _user32.AttachThreadInput(me, fg_thread, True)
        if attempt < 3:
            _nudge_input()
        else:
            _user32.keybd_event(VK_MENU, 0, 0, 0)
            _user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        _user32.BringWindowToTop(hwnd)
        _user32.SetForegroundWindow(hwnd)
        if attached:
            _user32.AttachThreadInput(me, fg_thread, False)
        if attempt >= 2:
            _user32.SwitchToThisWindow(hwnd, True)
        time.sleep(0.08)
    return _user32.GetForegroundWindow() == hwnd


def tap_key(vk):
    scan = _user32.MapVirtualKeyW(vk, 0)
    _user32.keybd_event(vk, scan, 0, 0)
    time.sleep(0.06)
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


def anti_afk_pass(hwnds, vk):
    """Roblox ignores input sent to background windows, so briefly focus each window, tap the key,
    then give focus back. The key is only sent once Roblox is confirmed in front, so it can never
    land in another app.

    Activating a window brings it to the front, which would flash it over whatever the user is
    doing. So any Roblox window that's covered is first moved off-screen, pinged there, and put
    back in its old place and stacking order afterwards; it is never seen. A window that's fully
    visible already is pinged where it is, since raising it changes nothing on screen.
    Returns (pinged, minimised, missed) lists of hwnds."""
    prev = _user32.GetForegroundWindow()
    pinged, minimised, missed, moved = [], [], [], []
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
            if hwnd != prev and not fully_visible(hwnd):
                r = wt.RECT()
                _user32.GetWindowRect(hwnd, ctypes.byref(r))
                above = _user32.GetWindow(hwnd, GW_HWNDPREV)
                moved.append((hwnd, r.left, r.top, above))
                _user32.SetWindowPos(hwnd, None, off_x, off_y, 0, 0,
                                     SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOSENDCHANGING)
            if force_foreground(hwnd):
                time.sleep(0.1)
                if _user32.GetForegroundWindow() == hwnd:
                    tap_key(vk)
                    pinged.append(hwnd)
                    continue
            missed.append(hwnd)
    finally:   # whatever happens above, never leave the user without focus or a window off-screen
        if prev and _user32.GetForegroundWindow() != prev and _user32.IsWindow(prev):
            force_foreground(prev)
        # Put moved windows back, top-most first, each directly below the window that was above
        # it, so the user's stacking order is restored and nothing pops over their current app.
        for hwnd, x, y, above in sorted(moved, key=lambda m: depth.get(m[0], 0)):
            flags = SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOSENDCHANGING
            if not above or not _user32.IsWindow(above) or _user32.GetWindowLongW(above, GWL_EXSTYLE) & WS_EX_TOPMOST:
                flags |= SWP_NOZORDER
            _user32.SetWindowPos(hwnd, above or None, x, y, 0, 0, flags)
    return pinged, minimised, missed


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
        # Match Tk's window class too, or an Explorer window open on a folder called "AltVault" would win.
        hwnd = _user32.FindWindowW("TkTopLevel", APP_TITLE)
        if hwnd:
            focus_window(hwnd)
        return None
    return handle


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


def build_launch_uri(ticket, place_id, job_id=""):
    tracker = str(random.randint(10**11, 10**12 - 1))
    params = {"request": "RequestGameJob" if job_id else "RequestGame",
              "browserTrackerId": tracker, "placeId": place_id,
              "isPlayTogetherGame": "false", "joinAttemptId": str(uuid.uuid4()),
              "joinAttemptOrigin": "PlayButton"}
    if job_id:
        params["gameId"] = job_id
    launcher = "https://www.roblox.com/Game/PlaceLauncher.ashx?" + urllib.parse.urlencode(params)
    return "+".join([
        "roblox-player:1", "launchmode:play", "gameinfo:" + ticket,
        "launchtime:%d" % (time.time() * 1000),
        "placelauncherurl:" + urllib.parse.quote(launcher, safe=""),
        "browsertrackerid:" + tracker, "robloxLocale:en_us", "gameLocale:en_us",
        "channel:", "LaunchExp:InApp",
    ])


def launch(cookie, place_id, job_id=""):
    try:
        os.startfile(build_launch_uri(get_auth_ticket(cookie), place_id, job_id))
    except OSError as e:
        # The OSError text includes the launch link, which holds a login ticket, so it must not
        # reach the log or a dialog.
        raise RobloxError(f"Couldn't start Roblox (Windows error {e.winerror}). "
                          "Is the Roblox player installed?") from None


UUID_RE = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"


def parse_place_id(text):
    """Accepts a bare place ID, a roblox.com game link, or a recent-games entry like 'Name  (123)'."""
    text = text.strip()
    if text.isdigit():
        return text
    m = (re.search(r"roblox\.com/(?:[a-z]{2}/)?games/(\d+)", text) or re.search(r"placeId=(\d+)", text)
         or re.search(r"\((\d+)\)\s*$", text))
    return m.group(1) if m else None


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


def themed_menu(root):
    return tk.Menu(root, tearoff=0, bg=C["panel"], fg=C["fg"], activebackground=C["accent"],
                   activeforeground="white", bd=0, font=FONT)


# ---------------------------------------------------------------- UI

ADD_STEPS = [
    "Open a private window (Ctrl+Shift+N in Chrome/Edge) and log into the account on roblox.com.",
    "Press F12 > Application tab (Firefox: Storage) > Cookies > https://www.roblox.com.",
    "Click .ROBLOSECURITY and copy its whole Value, then paste it below.",
    "Close the private window. Don't click Log Out — that kills the cookie.",
]


class AddAccountDialog(tk.Toplevel):
    def __init__(self, app, for_account=None):
        super().__init__(app.root, bg=C["bg"])
        self.app = app
        self.title(f"Update cookie — {for_account['name']}" if for_account else "Add account")
        self.resizable(False, False)
        self.transient(app.root)
        frm = ttk.Frame(self, padding=18)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="Add a Roblox account", style="Title.TLabel").pack(anchor="w")
        for i, step in enumerate(ADD_STEPS, 1):
            ttk.Label(frm, text=f"{i}.  {step}", wraplength=int(520 * app.scale),
                      justify="left").pack(anchor="w", pady=(8 if i == 1 else 2, 0))
        ttk.Button(frm, text="Open roblox.com login",
                   command=lambda: webbrowser.open("https://www.roblox.com/login")).pack(anchor="w", pady=(10, 0))

        ttk.Label(frm, text=".ROBLOSECURITY cookie", style="Muted.TLabel").pack(anchor="w", pady=(14, 2))
        self.cookie_var = tk.StringVar()
        cookie_entry = ttk.Entry(frm, textvariable=self.cookie_var, show="•")
        cookie_entry.pack(fill="x")
        ttk.Label(frm, text="Note (optional)", style="Muted.TLabel").pack(anchor="w", pady=(10, 2))
        self.note_var = tk.StringVar(value=for_account.get("note", "") if for_account else "")
        ttk.Entry(frm, textvariable=self.note_var).pack(fill="x")
        ttk.Label(frm, text="This cookie works like your password. Only ever paste it into AltVault.",
                  style="Small.TLabel").pack(anchor="w", pady=(10, 0))
        self.status = ttk.Label(frm, text="", style="Error.TLabel")
        self.status.pack(anchor="w", pady=(6, 0))

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(10, 0))
        self.add_btn = ttk.Button(btns, text="Save account", style="Accent.TButton", command=self.submit)
        self.add_btn.pack(side="right")
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right", padx=8)
        self.bind("<Return>", lambda _e: self.submit())
        self.bind("<Escape>", lambda _e: self.destroy())
        dark_title_bar(self)
        self.grab_set()
        cookie_entry.focus_set()

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
        self.afk_retry_hwnds = None   # windows to retry after a pass that couldn't focus them
        self.afk_retries = 0

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
        game.columnconfigure(0, weight=3)
        game.columnconfigure(1, weight=2)
        ttk.Label(game, text="Paste a place ID or roblox.com/games link, or pick a recent game.",
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
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.empty_hint = ttk.Label(table, text="No accounts yet — click “Add account” to start.",
                                    style="Muted.TLabel", background=C["panel"])
        self.tree.bind("<Double-1>", lambda _e: self.join_selected())
        self.tree.bind("<Return>", lambda _e: self.join_selected())
        self.tree.bind("<Delete>", lambda _e: self.remove_selected())
        self.tree.bind("<Control-a>", lambda _e: self.tree.selection_set(self.tree.get_children()))
        self.tree.bind("<Button-3>", self.show_menu)

    # ------------------------------------------------------------ helpers
    def ui(self, fn, *args):
        self.root.after(0, lambda: fn(*args))

    def set_status(self, text):
        self.ui(lambda: self.status.config(text=text))

    def account(self, account_id):
        return next((a for a in self.accounts if a["id"] == account_id), None)

    def selected_accounts(self):
        ids = set(self.tree.selection())
        return [a for a in self.accounts if str(a["id"]) in ids]

    def save(self):
        save_accounts(self.accounts)

    def save_settings(self):
        self.settings.update(multi=self.multi_var.get(), same_server=self.same_server_var.get(),
                             auto_tile=self.auto_tile_var.get(), anti_afk=self.anti_afk_var.get(),
                             place=self.place_var.get().strip())
        save_settings(self.settings)

    def status_of(self, acc):
        """(text, tag) for the account's Status column."""
        if acc["id"] in self.expired:
            return "Cookie expired — right-click > Update cookie", "expired"
        running = acc["id"] in self.running.values()
        p = self.presence.get(acc["id"])
        kind = p.get("userPresenceType") if p else None
        if kind == 2:
            where = p.get("lastLocation") or "a game"
            return f"● In game · {where}" + ("" if running else " (another device)"), "ingame"
        if running:
            return "● Roblox open" + (" · no anti-AFK" if self.afk_skipped(acc) else ""), "ingame"
        if kind == 3:
            return "● In Studio", ""
        if kind == 1:
            return "● Online", ""
        if kind == 0:
            return "Offline", "offline"
        return "", ""

    def refresh(self):
        selection = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        query = self.search_var.get().strip().lower()
        for acc in self.accounts:
            haystack = f"{acc['name']} {acc['displayName']} {acc.get('note', '')}".lower()
            if query and query not in haystack:
                continue
            text, tag = self.status_of(acc)
            self.tree.insert("", "end", iid=str(acc["id"]), image=self.avatars.get(acc["id"], self.blank_avatar),
                             values=(acc["displayName"], "@" + acc["name"], text, acc.get("note", "")),
                             tags=(tag,) if tag else ())
        self.tree.selection_set([i for i in selection if self.tree.exists(i)])
        if self.accounts:
            self.empty_hint.place_forget()
        else:
            self.empty_hint.place(relx=0.5, rely=0.5, anchor="center")
        n_running = len(set(self.running.values()))
        self.summary.config(text=f"{len(self.accounts)} accounts" + (f" · {n_running} open" if n_running else ""))

    def recents(self):
        items = self.settings.get("recents")
        return [r for r in items if isinstance(r, dict) and "id" in r and "name" in r] \
            if isinstance(items, list) else []

    def update_recents_box(self):
        self.place_box["values"] = [f"{r['name']}  ({r['id']})" for r in self.recents()]

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
        if self.root.state() == "iconic":
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
        menu.add_command(label="Join game" if len(sel) == 1 else f"Join game ({len(sel)} accounts)",
                         command=self.join_selected)
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
            menu.add_command(label="Edit note", command=lambda: self.edit_note(acc))
            menu.add_command(label="Update cookie", command=lambda: AddAccountDialog(self, acc))
            menu.add_command(label="Move up", command=lambda: self.move(acc, -1))
            menu.add_command(label="Move down", command=lambda: self.move(acc, 1))
        menu.add_separator()
        menu.add_command(label="Remove", command=self.remove_selected)
        menu.tk_popup(event.x_root, event.y_root)

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
        self.afk_wait_started, self.afk_retry_hwnds, self.afk_retries = None, None, 0
        if self.anti_afk_var.get():
            self.schedule_afk(3)
            if not quiet:
                self.status.config(text="Anti-AFK is on — open Roblox windows get a jump every ~10 min.")
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
        if self.afk_job:   # no-op when the timer fired this; stops a second timer otherwise
            self.root.after_cancel(self.afk_job)
            self.afk_job = None
        if not self.anti_afk_var.get():
            return
        if self.afk_busy or self.joining:   # don't fight a join for focus
            self.schedule_afk(5)
            return
        if self.afk_wait_started is None:
            self.afk_wait_started = time.monotonic()
        if idle_seconds() < AFK_IDLE and time.monotonic() - self.afk_wait_started < AFK_MAX_WAIT:
            self.schedule_afk(1)
            return
        self.afk_wait_started = None

        skip = self.afk_skip_ids()
        hwnds = [hwnd for hwnd, pid, _ in roblox_windows() if self.running.get(pid) not in skip]
        if self.afk_retry_hwnds is not None:
            hwnds = [h for h in hwnds if h in self.afk_retry_hwnds]
        if not hwnds:
            self.afk_retry_hwnds, self.afk_retries = None, 0
            self.schedule_afk(random.uniform(*AFK_INTERVAL))
            return
        vk = AFK_KEYS.get(str(self.settings.get("afk_key", "space")).lower(), AFK_KEYS["space"])
        self.afk_busy = True

        def work():
            try:
                result = anti_afk_pass(hwnds, vk)
            except Exception:
                log.exception("Anti-AFK pass failed")
                result = ([], [], [])
            self.ui(self.afk_finished, *result)

        threading.Thread(target=work, daemon=True).start()

    def afk_finished(self, pinged, minimised, missed):
        self.afk_busy = False
        parts = [f"pinged {len(pinged)}"]
        if minimised:
            parts.append(f"{len(minimised)} minimised (restore to keep them safe)")
        if missed:
            parts.append(f"{len(missed)} busy, retrying")
        log.info("Anti-AFK: %s", ", ".join(parts))
        if not self.anti_afk_var.get():
            return
        self.status.config(text=f"Anti-AFK {time.strftime('%H:%M')}: " + ", ".join(parts))
        if missed and self.afk_retries < AFK_MAX_RETRIES:
            self.afk_retries += 1
            self.afk_retry_hwnds = set(missed)
            self.schedule_afk(AFK_RETRY)
        else:
            self.afk_retry_hwnds, self.afk_retries = None, 0
            self.schedule_afk(random.uniform(*AFK_INTERVAL))

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
        place_id = parse_place_id(self.place_var.get())
        if not place_id:
            messagebox.showerror("Join", "Enter a place ID or a roblox.com/games/... link.", parent=self.root)
            return
        job_id = parse_job_id(self.job_var.get())
        if job_id is None:
            messagebox.showerror("Join", "The server / Job ID should look like "
                                 "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx (or leave it empty).", parent=self.root)
            return
        expired = [a for a in sel if a["id"] in self.expired]
        if expired:
            messagebox.showerror("Join", "These accounts need a fresh cookie first:\n" +
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
        same_server = self.same_server_var.get() and len(sel) > 1
        threading.Thread(target=self.join_worker, args=(sel, place_id, job_id, same_server),
                         daemon=True).start()

    def join_worker(self, accounts, place_id, job_id, same_server):
        problems = []
        try:
            for i, acc in enumerate(accounts):
                last = i == len(accounts) - 1
                self.set_status(f"Launching @{acc['name']} ({i + 1}/{len(accounts)})...")
                before = {pid for _, pid, _ in roblox_windows()}
                try:
                    launch(acc["cookie"], place_id, job_id)
                except CookieExpired:
                    self.expired.add(acc["id"])
                    problems.append(f"@{acc['name']}: cookie expired — right-click > Update cookie.")
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
                if before - {p for _, p, _ in roblox_windows()}:
                    problems.append("An earlier Roblox window closed when this one opened, so "
                                    "Multi-Roblox isn't working with this Roblox version.")
                if i == 0:
                    threading.Thread(target=self.remember_place, args=(acc["cookie"], place_id),
                                     daemon=True).start()
                if same_server and not job_id and not last:
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
        self.settings["geometry"] = self.root.geometry()
        self.save_settings()
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
    if os.path.exists(DATA_FILE):
        threading.Thread(target=restrict_to_owner, args=(DATA_FILE,), daemon=True).start()
    App(root, accounts)
    root.mainloop()
    disable_multi_roblox()


if __name__ == "__main__":
    main()
