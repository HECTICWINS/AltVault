"""AltVault - a Roblox account manager that runs on signed Python (no unsigned .exe),
so it works with Windows Smart App Control turned on.

Everything lives in %LOCALAPPDATA%\\AltVault. Account cookies are encrypted with Windows
DPAPI (only your Windows user on this PC can decrypt them) and are never written to the log.
"""
import base64
import calendar
import collections
import ctypes
import ctypes.wintypes as wt
import hashlib
import http.client
import json
import logging
import math
import os
import queue
import random
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
import winreg
import zlib
from tkinter import filedialog, font as tkfont, ttk

APP_TITLE = "AltVault"
APP_VERSION = "2.8.0"
GITHUB_URL = "https://github.com/HECTICWINS/AltVault"
APP_DIR = os.path.join(os.environ["LOCALAPPDATA"], "AltVault")
DATA_FILE = os.path.join(APP_DIR, "accounts.dat")
SETTINGS_FILE = os.path.join(APP_DIR, "settings.json")
PRIVATE_FILE = os.path.join(APP_DIR, "private-link.dat")
WEBHOOK_FILE = os.path.join(APP_DIR, "webhook.dat")
FLAGS_BACKUP = os.path.join(APP_DIR, "client-flags-backup.json")
STARTED = time.monotonic()   # for timing how long the window takes to be ready
UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")   # the new interface's page
# The explanations under each option on the new Settings page.
WEB_SETTING_TEXT = {
    "multi": "Run several Roblox windows at once. Turn off only if you want each launch to replace the previous window.",
    "same_server": "When joining several accounts with no server chosen, AltVault picks a server with room for all of them. "
                   "If there's none, the others follow the first account.",
    "auto_tile": "Splits the screen evenly between every Roblox window, like the Tile button.",
    "launch_delay": "A gap gives each client time to load before the next one starts. While a join runs, the Join button stops it.",
    "launch_memory_wait": "If memory is over 90% full, the next client waits up to a minute for it to free up, so the ones "
                          "already running don't stall.",
    "modes": "Normal: your own Roblox settings. Grind: alts at 15 FPS, lowest graphics, muted, in Windows' efficiency mode. "
             "Max alts: like Grind at 10 FPS, and Windows moves alts out of memory first when it runs short.",
    "main": "The account you play on. It keeps your own Roblox settings and normal priority in every mode.",
    "lighter": "Accounts on lowest graphics also start with the lowest texture quality, no anti-aliasing, a plain sky and "
               "no grass, using the rendering flags Roblox allows players to set. Skipped when Roblox starts through a "
               "bootstrapper such as Bloxstrap.",
    "afk_how": "Roblox only reads input from the window in front, so Anti-AFK borrows focus for a moment and gives it straight "
               "back. It waits for you to pause and stops as soon as you touch the mouse or keyboard.",
    "afk_key": "F13 is a key no game uses, so your character stays where it is. Space makes it jump.",
    "webhook": "Get disconnects, expired logins, crashes and low memory on your phone. In a Discord server you own: Server "
               "Settings, Integrations, Webhooks, New Webhook, Copy Webhook URL, then paste it here. It's stored encrypted "
               "to your Windows user.",
    "panic": "Closes every Roblox window and stops any join, from any app, without asking first.",
    "stream": "Hides usernames, avatars and your saved private server link in AltVault, and usernames in Roblox window "
              "titles, so you can share your screen.",
    "taskbar": "Keeps the taskbar and Alt+Tab clear. Open a window from the Live page instead.",
    "private": "The last private server link you used is kept, encrypted to your Windows user, and comes back in the Game "
               "box when you reopen AltVault.",
    "accounts": "Updates keep your accounts by themselves. To move them to another PC or Windows user, export them to a "
                "file locked with a password.",
    "backup": "Back up your options, games, profiles and per-account settings. Logins and private server links are never included.",
    "clean_logs": "Once a day, Roblox's own log files older than 3 days are deleted.",
    "update_check": "Once a day, asks GitHub whether a newer AltVault is out. Nothing is downloaded by itself.",
}
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
AFK_BUSY_INTERVAL = (720, 840)   # while the PC is in use, wait this long instead: each ping borrows focus
AFK_AWAY = 120                   # seconds without mouse/keyboard use before the PC counts as not in use
AFK_IDLE = 4                     # seconds of no mouse/keyboard use before borrowing focus
AFK_MAX_WAIT = 300               # if the user never pauses that long, ping anyway
AFK_URGENT = 900                 # a window this long without a ping is pinged right away
AFK_MIN_GAP = 240                # a pass leaves out windows pinged more recently than this
AFK_RETRY = 30                   # seconds before retrying windows that couldn't be pinged
AFK_RETRY_SLOW = 60              # retry delay once a window has missed AFK_SLOW_AFTER times in a row
AFK_SLOW_AFTER = 5
AFK_SETTLE = 0.35                # after focusing: Roblox runs slowly while hidden, let it catch up
AFK_HOLD = 0.2                   # key hold; a short tap can fall between a slowed client's frames
AFK_WATCH = 0.25                 # after each window, listen this long for the user coming back
AFK_STUCK = 90                   # seconds after which a still-running pass is treated as stuck
AFK_ALERT_AFTER = 4              # misses in a row (about 2 minutes) before a tray alert
AFK_PRESSURE = 0.85              # memory this full makes Roblox clients stall: give them longer to catch up
AFK_SETTLE_SLOW = 1.2            # ...by waiting this long after focusing a window
LOST_AFTER = 300                 # seconds a window may sit out of its game before it counts as disconnected
HIDDEN_PRESENCE_EVERY = 6        # while hidden in the tray, check presence every 6th time (3 min) if windows are open
MEMORY_ALERT = 0.92              # memory this full for 3 readings in a row, with several Roblox windows: warn
LAUNCH_MEMORY = 0.90             # memory this full: wait before starting the next Roblox client...
LAUNCH_MEMORY_WAIT = 60          # ...for up to this many seconds, then launch anyway
LAUNCH_DELAYS = (0, 5, 10, 20, 30)   # seconds between launches the user can choose
ROBLOX_LOG_DAYS = 3              # Roblox's own log files older than this are deleted once a day
WEBHOOK_PER_HOUR = 20            # Discord alerts sent at most this often, so a bad night can't spam
PANIC_HOTKEY = "Ctrl+Alt+End"    # closes every Roblox window, from anywhere
AFK_DEFAULT_KEY = "f13"          # a key no game uses: a jump can bump a character away from what it's doing
AFK_KEYS = {"space": 0x20, "f13": 0x7C, **{c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz"}}

log = logging.getLogger("altvault")


class ActivityFeed(logging.Handler):
    """Keeps the latest log lines for the Home page. Log lines never hold logins, links or window titles."""

    def __init__(self):
        super().__init__(logging.INFO)
        self.items = collections.deque(maxlen=40)

    def emit(self, record):
        self.items.appendleft((record.created, record.levelno, record.getMessage()))


ACTIVITY = ActivityFeed()
log.addHandler(ACTIVITY)

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


PROCESS_SET_INFORMATION = 0x0200
NORMAL_PRIORITY_CLASS, BELOW_NORMAL_PRIORITY_CLASS = 0x0020, 0x4000
PROCESS_MEMORY_PRIORITY, PROCESS_POWER_THROTTLING = 0, 4      # PROCESS_INFORMATION_CLASS values
MEMORY_PRIORITY_LOW, MEMORY_PRIORITY_NORMAL = 2, 5
POWER_THROTTLING_EXECUTION_SPEED = 0x1


class POWER_THROTTLING(ctypes.Structure):
    _fields_ = [("Version", wt.ULONG), ("ControlMask", wt.ULONG), ("StateMask", wt.ULONG)]


_kernel32.SetPriorityClass.argtypes = [wt.HANDLE, wt.DWORD]
_kernel32.SetProcessInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]


def set_process_saving(pid, eco=False, memory_low=False):
    """Asks Windows to run a Roblox client gently, or normally again. eco: Windows' efficiency mode
    (EcoQoS: a lower clock, its efficient cores) and below-normal CPU priority, the switches behind
    Task Manager's Efficiency mode. memory_low: when memory runs short, Windows pages this client
    out before your other programs. Roblox itself isn't touched. False if Windows refused."""
    handle = _kernel32.OpenProcess(PROCESS_SET_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        speed = POWER_THROTTLING_EXECUTION_SPEED if eco else 0   # both 0: Windows decides, as normal
        throttle = POWER_THROTTLING(1, speed, speed)
        memory = wt.ULONG(MEMORY_PRIORITY_LOW if memory_low else MEMORY_PRIORITY_NORMAL)
        return all((
            _kernel32.SetPriorityClass(handle, BELOW_NORMAL_PRIORITY_CLASS if eco else NORMAL_PRIORITY_CLASS),
            _kernel32.SetProcessInformation(handle, PROCESS_POWER_THROTTLING, ctypes.byref(throttle),
                                            ctypes.sizeof(throttle)),
            _kernel32.SetProcessInformation(handle, PROCESS_MEMORY_PRIORITY, ctypes.byref(memory),
                                            ctypes.sizeof(memory))))
    finally:
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


def last_input_tick():
    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO))
    _user32.GetLastInputInfo(ctypes.byref(info))
    return info.dwTime


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


def press_when_ready(hwnd, vk, strong=False):
    """For a window that's now in front: lets it catch up, then taps the key once, but only while it is
    still in front. With `strong` (memory nearly full, so clients stall) it waits longer first. Never more
    than one short tap: a second tap or a long hold is a double jump, which pulls a character off whatever
    it's standing at in the game. Returns True if the key was sent."""
    time.sleep(AFK_SETTLE_SLOW if strong else AFK_SETTLE)
    if _user32.GetForegroundWindow() != hwnd:
        return False
    _step("pressing the key")
    tap_key(vk)
    return True


def user_came_back():
    """Waits a moment after a key tap and says if the mouse or keyboard was used in that moment. AltVault
    makes no input during the wait, so any input is the user's."""
    time.sleep(0.03)   # our own key-up must land first
    mark = last_input_tick()
    time.sleep(AFK_WATCH)
    return last_input_tick() != mark


def memory_used_fraction():
    free, total = memory_status()
    return 1 - free / total if total else 0.0


def anti_afk_pass(hwnds, vk, strong=False):
    """Roblox ignores input sent to background windows, so briefly focus each window, tap the key,
    then give focus back. The key is only sent once Roblox is confirmed in front, so it can never
    land in another app.

    Activating a window brings it to the front, which would flash it over whatever the user is
    doing. So any Roblox window that's covered is first moved off-screen, pinged there, and put
    back in its old place and stacking order afterwards; it is never seen. A window that's fully
    visible already is pinged where it is, since raising it changes nothing on screen.
    If the user touches the mouse or keyboard mid-pass, the pass stops and hands focus straight back;
    the windows it didn't reach are picked up by the next pass.
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
                if press_when_ready(hwnd, vk, strong):
                    pinged.append(hwnd)
                    if hwnd != hwnds[-1] and user_came_back():
                        log.info("Anti-AFK: stopped early so you can use your PC; the rest follow shortly")
                        break
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


SW_SHOWNOACTIVATE, SW_SHOWMINNOACTIVE = 4, 7


def minimize_windows(hwnds, minimized):
    """Minimises the windows (a minimised Roblox draws nothing, so it uses far less CPU and GPU) or
    restores them, without taking focus. Async, so a frozen Roblox can't freeze AltVault."""
    for hwnd in hwnds:
        _user32.ShowWindowAsync(hwnd, SW_SHOWMINNOACTIVE if minimized else SW_SHOWNOACTIVATE)


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


SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79
DEFAULT_GEOMETRY = "1320x800"


LAYOUT_VERSION = 3   # 2.8: wider sidebar and the Home page


def work_area():
    area = wt.RECT()
    _user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(area), 0)
    return area.right - area.left, area.bottom - area.top


def min_size(scale):
    w, h = work_area()
    return min(int(1020 * scale), w), min(int(660 * scale), h)


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
_user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
_user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
_user32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
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


MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
VK_END = 0x23
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012


class Hotkey:
    """A key combination that works from any app. Its thread sleeps inside GetMessage until the
    keys are pressed, so it uses no CPU while waiting. `callback` runs on that thread."""

    def __init__(self, mods, vk, callback):
        self.thread_id, self.registered = None, False
        ready = threading.Event()
        self.thread = threading.Thread(target=self._run, args=(mods, vk, callback, ready), daemon=True)
        self.thread.start()
        ready.wait(2)

    def _run(self, mods, vk, callback, ready):
        self.thread_id = _kernel32.GetCurrentThreadId()
        self.registered = bool(_user32.RegisterHotKey(None, 1, mods | MOD_NOREPEAT, vk))
        ready.set()
        if not self.registered:   # another program already uses these keys
            return
        msg = wt.MSG()
        try:
            while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    callback()
        finally:
            _user32.UnregisterHotKey(None, 1)

    def stop(self):
        if self.registered and self.thread_id:
            _user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)


DWMWA_WINDOW_CORNER_PREFERENCE, DWMWA_BORDER_COLOR, DWMWA_CAPTION_COLOR, DWMWA_TEXT_COLOR = 33, 34, 35, 36
DWMWCP_ROUND = 2


def _colorref(color):
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return r | g << 8 | b << 16


def dark_title_bar(win):
    """Dark title bar in the app's own colours with a hairline border and rounded corners, so the
    window frame blends into the app. Windows 11 honours all of it; older Windows ignores what it
    doesn't know and keeps the plain dark title bar."""
    # A window about to appear for the first time is held back until its frame is dark, or it
    # shows a light title bar for a moment.
    first = win.state() == "normal" and not win.winfo_ismapped()
    if first:
        win.withdraw()
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
    if first:
        win.deiconify()


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

# Modes: one switch for how hard every account except your main one saves power and memory. The
# main account keeps your own Roblox settings and normal priority, so the one you play stays smooth.
# An account's own FPS cap, if it has one, wins over the mode's.
MODES = {"normal": {},
         "grind": {"fps": 15, "low_graphics": True, "muted": True, "eco": True},
         "max": {"fps": 10, "low_graphics": True, "muted": True, "eco": True, "memory_low": True}}
MODE_NAMES = (("normal", "Normal"), ("grind", "Grind"), ("max", "Max alts"))
MODE_HELP = {"normal": "Every account starts with your own Roblox settings and its per-account ones.",
             "grind": "Alts run at 15 FPS, lowest graphics and muted, in Windows' efficiency mode.",
             "max": "Like Grind at 10 FPS, and Windows moves alts out of memory first when it runs short."}


def mode_of(settings):
    mode = settings.get("mode")
    return mode if isinstance(mode, str) and mode in MODES else "normal"


def main_account(settings):
    main = settings.get("main_account")
    return main if isinstance(main, int) and not isinstance(main, bool) else None


def mode_rules(settings, account_id):
    """What the current mode asks of this account: nothing for your main account."""
    return {} if account_id == main_account(settings) else MODES[mode_of(settings)]


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


# Roblox lets players set a short list of rendering flags (its Fast Flag allowlist) in
# ClientAppSettings.json beside the player. Alts on lowest graphics also get these, written just
# before they start and taken out again once the client has read them, like the settings above.
# The file as it was is saved first, so even a crash mid-launch can't leave alt flags behind.
LOW_FLAGS = {"DFFlagTextureQualityOverrideEnabled": "True", "DFIntTextureQualityOverride": "0",
             "FIntDebugForceMSAASamples": "0", "FFlagDebugSkyGray": "True",
             "FIntFRMMaxGrassDistance": "0", "FIntFRMMinGrassDistance": "0",
             "DFIntDebugFRMQualityLevelOverride": "1"}
FLAGS_NAME = "ClientAppSettings.json"


def wants_low_flags(settings, account_id):
    """True if this account starts on lowest graphics (its own setting or the mode's) and the
    lighter rendering option is on."""
    if settings.get("lighter_rendering", True) is False:
        return False
    low = settings.get("low_graphics")
    return (isinstance(low, list) and account_id in low) or bool(mode_rules(settings, account_id).get("low_graphics"))


def roblox_player_folder():
    """The folder of the Roblox player that roblox-player: links start, or None when they go through
    a bootstrapper (Bloxstrap and others rewrite the flags file themselves on every launch)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"roblox-player\shell\open\command") as key:
            command = winreg.QueryValue(key, None)
    except OSError:
        return None
    m = re.match(r'\s*"([^"]+)"|\s*(\S+)', command or "")
    exe = m and (m.group(1) or m.group(2))
    if not exe or os.path.basename(exe).lower() != ROBLOX_EXE or not os.path.isfile(exe):
        return None
    return os.path.dirname(exe)


def write_flags(folder, flags):
    """Adds `flags` to the player's ClientAppSettings.json. Raises ValueError, leaving the file
    alone, if it holds something other than a flags object."""
    path = os.path.join(folder, "ClientSettings", FLAGS_NAME)
    try:
        with open(path, "rb") as f:
            old = f.read()
    except FileNotFoundError:
        old = None
    current = {}
    if old and old.strip():
        current = json.loads(old.decode("utf-8-sig"))
        if not isinstance(current, dict):
            raise ValueError("ClientAppSettings.json isn't a flags file")
    os.makedirs(APP_DIR, exist_ok=True)
    with open(FLAGS_BACKUP, "w", encoding="utf-8") as f:
        json.dump({"path": path, "old": None if old is None else base64.b64encode(old).decode()}, f)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".altvault.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({**current, **flags}, f, indent=1)
    os.replace(tmp, path)


def restore_flags():
    """Puts the flags file back exactly as write_flags found it. Does nothing if nothing is pending."""
    try:
        with open(FLAGS_BACKUP, encoding="utf-8") as f:
            saved = json.load(f)
        path, old = saved["path"], saved["old"]
    except FileNotFoundError:
        return
    except (OSError, ValueError, KeyError, TypeError):
        os.remove(FLAGS_BACKUP)
        return
    if os.path.basename(str(path)) == FLAGS_NAME:   # never touch any other file
        if old is None:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
        else:
            tmp = path + ".altvault.tmp"
            with open(tmp, "wb") as f:
                f.write(base64.b64decode(old))
            os.replace(tmp, path)
    os.remove(FLAGS_BACKUP)


def restore_flags_safely():
    try:
        restore_flags()
    except OSError as e:
        log.warning("Couldn't put Roblox's flags file back: %s", type(e).__name__)


def clean_roblox_logs(days=ROBLOX_LOG_DAYS, now=None):
    """Deletes Roblox's own log files older than `days`; one a client still has open is skipped.
    Returns (files, bytes) removed."""
    folder = os.path.join(os.environ["LOCALAPPDATA"], "Roblox", "logs")
    cutoff = (now or time.time()) - days * 86400
    files = size = 0
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return 0, 0
    for entry in entries:
        if not entry.name.lower().endswith(".log"):
            continue
        try:
            st = entry.stat()
            if entry.is_file() and st.st_mtime < cutoff:
                os.remove(entry.path)
                files, size = files + 1, size + st.st_size
        except OSError:
            continue
    return files, size


RELEASES_API = "https://api.github.com/repos/HECTICWINS/AltVault/releases/latest"


def version_tuple(text):
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(text or "").strip())
    return tuple(int(n) for n in m.groups()) if m else None


def latest_release():
    """(version tuple, release page) of the newest published AltVault, or None. Asks GitHub only;
    nothing about you or your accounts is sent, and nothing is downloaded."""
    status, _, body = _request(RELEASES_API, headers={"Accept": "application/vnd.github+json"})
    if status != 200:
        return None
    data = json.loads(body)
    version, page = version_tuple(data.get("tag_name")), str(data.get("html_url", ""))
    if not version or not page.startswith(GITHUB_URL + "/releases/"):
        return None
    return version, page


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


def load_private_link():
    """The saved private server link, or None. A file that can't be read (made on another PC or
    Windows user, or damaged) just means no saved link; it never stops AltVault starting."""
    try:
        with open(PRIVATE_FILE, "rb") as f:
            link = unprotect(f.read()).decode("utf-8")
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        log.warning("Couldn't read the saved private server link (%s)", type(e).__name__)
        return None
    return link if parse_private_server(link) else None


def save_private_link(link):
    """A private server link is an invite, so like a login it's only stored encrypted to this Windows
    user, never in settings.json or the log. The file is replaced in one step so a crash mid-save
    can't lose the link already saved."""
    os.makedirs(APP_DIR, exist_ok=True)
    tmp = PRIVATE_FILE + ".tmp"
    with open(tmp, "wb") as f:
        f.write(protect(link.encode("utf-8")))
    os.replace(tmp, PRIVATE_FILE)
    restrict_to_owner(PRIVATE_FILE)


# ---------------------------------------------------------------- Discord alerts
# A webhook link lets anyone who has it post in that channel, so like a private server link it's
# only stored encrypted to this Windows user, and never logged.
WEBHOOK_RE = re.compile(r"https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/\d{5,25}/[\w-]{20,100}")


def load_webhook():
    try:
        with open(WEBHOOK_FILE, "rb") as f:
            url = unprotect(f.read()).decode("utf-8")
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        log.warning("Couldn't read the saved Discord webhook (%s)", type(e).__name__)
        return None
    return url if WEBHOOK_RE.fullmatch(url) else None


def save_webhook(url):
    """Saves a Discord webhook link, or deletes the saved one when url is None."""
    if url is None:
        try:
            os.remove(WEBHOOK_FILE)
        except FileNotFoundError:
            pass
        return
    if not WEBHOOK_RE.fullmatch(url):
        raise ValueError("not a Discord webhook link")
    os.makedirs(APP_DIR, exist_ok=True)
    tmp = WEBHOOK_FILE + ".tmp"
    with open(tmp, "wb") as f:
        f.write(protect(url.encode("utf-8")))
    os.replace(tmp, WEBHOOK_FILE)
    restrict_to_owner(WEBHOOK_FILE)


def send_webhook(url, title, text):
    """Posts one alert to a Discord webhook. No login is ever sent, and @mentions are disabled so a
    username can't ping anyone. Returns True if Discord accepted it."""
    if not WEBHOOK_RE.fullmatch(url):
        return False
    body = json.dumps({"username": "AltVault", "content": f"**{title}**\n{text}"[:1900],
                       "allowed_mentions": {"parse": []}}).encode()
    status, _, _ = _request(url, method="POST", headers={"Content-Type": "application/json"}, data=body)
    return status in (200, 204)


# What a settings backup may hold, and each value's type. Logins and private server links are not here.
BACKUP_KEYS = {"multi": bool, "same_server": bool, "auto_tile": bool, "anti_afk": bool, "stream_mode": bool,
               "hide_taskbar": bool, "sidebar_wide": bool, "afk_key": str, "accent": str, "favorites": list,
               "recents": list, "profiles": list, "fps_caps": dict, "low_graphics": list, "muted": list,
               "afk_skip": list, "mode": str, "main_account": int, "launch_delay": int, "launch_memory_wait": bool,
               "lighter_rendering": bool, "clean_logs": bool, "update_check": bool, "panic_hotkey": bool}


def settings_backup(settings):
    return {"altvault_settings": 1, **{k: settings[k] for k in BACKUP_KEYS if k in settings}}


def restore_backup(settings, data):
    """Copies the valid values of a backup into settings. Returns how many were restored; raises
    ValueError if it isn't a settings backup. Unknown keys and wrong types are ignored."""
    if not isinstance(data, dict) or data.get("altvault_settings") != 1:
        raise ValueError("not an AltVault settings backup")
    good = {k: v for k, v in data.items() if isinstance(v, BACKUP_KEYS.get(k, ()))}
    settings.update(good)
    return len(good)


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


# ---------------------------------------------------------------- Doctor
# Works out why an account dropped, from what Roblox itself writes to its log files. Each log names
# its account (userid), so evidence is tied to the right one. Only new bytes are read, once a minute
# while Roblox is open. Findings hold numbers and times only: no log text, names or server IDs.
DOCTOR_FILE = os.path.join(APP_DIR, "doctor.json")
DOCTOR_DAYS, DOCTOR_MAX = 7, 300
DOCTOR_FIRST_LOOK = 24 * 3600     # how far back the very first scan looks
DOCTOR_MEMORY_NEAR = 120          # critical memory this close to a drop counts as evidence
DOCTOR_FRESH = 300                # a finding this recent describes what's true right now
CLIENT_LEFT = 285                 # the client left by itself: closed, or teleporting. Not a problem.
LOG_FILE_RE = re.compile(r".*_Player_[0-9A-Fa-f]+_last\.log")
LOG_EVENT = re.compile(rb"Sending disconnect with reason: (\d+)|Joining game '([0-9a-fA-F-]{36})' place (\d+)"
                       rb"|clienttime:[\d.]+, userid:(\d+)|memoryPrioritizationCallback \(level:3\)")


def log_time(text):
    """A log line's UTC 'YYYY-MM-DDTHH:MM:SS' as Unix time, or None if it isn't one."""
    try:
        return calendar.timegm(time.strptime(text.decode("ascii"), "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, UnicodeDecodeError):
        return None


def parse_client_log(state, data):
    """Reads whole lines of one client's log into events, carrying `state` (a dict) between calls.
    Returns [{'t', 'user', 'kind': 'exit'|'moved', 'code', 'memory'}]."""
    stamp = lambda pos: data[data.rfind(b"\n", 0, pos) + 1:][:19]
    events, memory_at = [], None

    def memory_near(t):
        last = log_time(stamp(memory_at)) if memory_at is not None else state.get("memory")
        return last is not None and 0 <= t - last <= DOCTOR_MEMORY_NEAR

    for m in LOG_EVENT.finditer(data):
        code, job, place, user = m.group(1), m.group(2), m.group(3), m.group(4)
        if user:
            state["user"] = int(user)
        elif job:
            if state.get("job") not in (None, job) and state.get("place") == place:
                t = log_time(stamp(m.start()))
                if t is not None:
                    events.append({"t": t, "user": state.get("user"), "kind": "moved", "code": None, "memory": memory_near(t)})
            state["job"], state["place"] = job, place
        elif code:
            t = log_time(stamp(m.start()))
            if t is not None and (t, int(code)) != state.get("last_exit"):   # Roblox writes each one twice
                state["last_exit"] = (t, int(code))
                events.append({"t": t, "user": state.get("user"), "kind": "exit", "code": int(code), "memory": memory_near(t)})
        else:
            memory_at = m.start()
    if memory_at is not None:
        state["memory"] = log_time(stamp(memory_at))
    return events


# What Roblox's disconnect codes mean: (title, why, what to do, button, colour key).
DOCTOR_CODES = {
    264: ("Signed in somewhere else", "This account joined from another device or session, so Roblox ended this one.",
          "Make sure the account isn't open anywhere else, such as a phone or another PC, then rejoin it.", None, "red"),
    266: ("Connection timed out", "Roblox stopped hearing from this client. A stalled client or a network drop both do this.",
          "Check your internet, then rejoin it.", "live", "yellow"),
    267: ("Kicked by the game", "The game removed this player itself. That's the game's own rule, not Roblox or AltVault.",
          "Check the message in the Roblox window for the game's reason.", "live", "red"),
    268: ("Kicked for unexpected behaviour", "Roblox's client check ended this session.",
          "Rejoin it once. If it keeps happening, stop running other tools alongside this client.", "live", "red"),
    273: ("Signed in somewhere else", "Roblox ended this session because the account joined from somewhere else.",
          "Make sure the account isn't open anywhere else, such as a phone or another PC, then rejoin it.", None, "red"),
    277: ("Lost connection", "The connection to the game server dropped.",
          "Check your internet. If only this account dropped, rejoin it.", "live", "yellow"),
    279: ("Couldn't connect to the server", "The client never finished connecting to the game server.",
          "Try joining again. If every account fails, the game or Roblox may be having problems.", "live", "yellow"),
    286: ("Out of memory", "Roblox said this PC didn't have enough memory to keep running the game.",
          "Close other apps or run fewer clients.", "pulse", "red"),
}
DOCTOR_EVENTS = {
    "login": ("Login rejected", "Roblox no longer accepts this account's saved login, so it can't launch or stay connected.",
              "Right-click the account and choose Log in again.", "login", "red"),
    "lost": ("Out of the game", "Its window stayed open, but the account has been outside any game for over 5 minutes, "
             "and Roblox gave no disconnect reason.", "Check the window on the Live page, then rejoin it.", "live", "red"),
    "crash": ("Client closed unexpectedly", "The Roblox window disappeared and Roblox wrote no reason to its log, "
              "which usually means it crashed.", "Rejoin it. If it keeps happening, check how full your memory is.", "pulse", "red"),
    "moved": ("Moved to another server", "The account left its server and rejoined a different one of the same game. "
              "Roblox's log doesn't say what started the move.", "Nothing, unless you need it to stay in one server.", None, "yellow"),
    "memory": ("Memory nearly full", "Roblox clients stall when memory runs out, and stalled clients can miss Anti-AFK's key press.",
               "Close other apps or run fewer clients.", "pulse", "yellow"),
}


def explain(item):
    """(title, why, what to do, button, colour key) for a finding."""
    code = item.get("code")
    if item["kind"] != "exit":
        title, why, fix, action, color = DOCTOR_EVENTS[item["kind"]]
        if item.get("lasted"):
            why += f" It stopped working {item['lasted']} after it was saved."
        if item.get("memory"):
            why += " Memory was critically low at the time."
        return title, why, fix, action, color
    if code == 278:
        title, why, fix, action = "Idle-kicked by Roblox", "Roblox saw no input from this client for 20 minutes.", \
            "Rejoin it. Keep its window open, not minimised, so Anti-AFK can reach it.", "live"
        if item.get("anti_afk") == "off":
            why, fix, action = why + " Anti-AFK was off.", "Turn on Anti-AFK, then rejoin it.", "afk"
        elif item.get("anti_afk") == "skipped":
            why, fix = why + " Anti-AFK is turned off for this account.", \
                "Tick Anti-AFK under Performance on its right-click menu, then rejoin it."
        elif item.get("afk_key") == "f13":
            why += " Anti-AFK was pressing F13, so this game may not count that key as activity."
            fix, action = "In Settings, under Anti-AFK, pick a letter this game doesn't use, then rejoin it.", None
        elif item.get("memory"):
            why += " Memory was critically low, so the client may have been too stalled to register Anti-AFK's key press."
            fix, action = "Close other apps or run fewer clients, then rejoin it.", "pulse"
        return title, why, fix, action, "red"
    if code in DOCTOR_CODES:
        title, why, fix, action, color = DOCTOR_CODES[code]
        if item.get("memory"):
            why += " Memory was critically low at the time."
    else:
        title, why, fix, action, color = "Disconnected", "Roblox ended this session with a code AltVault doesn't know.", \
            "Rejoin it. If it keeps happening, look the code up on Roblox's support pages.", "live", "yellow"
    return f"{title} (error {code})" if code else title, why, fix, action, color


class Doctor:
    """Findings, newest first, kept in doctor.json. scan() runs on a worker thread and touches only the
    log bookkeeping; everything else is used from the Tk thread."""

    def __init__(self, path=None, folder=None):
        self.path = path or DOCTOR_FILE
        self.folder = folder or os.path.join(os.environ.get("LOCALAPPDATA", ""), "Roblox", "logs")
        self.items, self.logs, self.exits, self.dirty = [], {}, {}, False
        self.since = time.time() - DOCTOR_FIRST_LOOK
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self.since = max(float(data["since"]), self.since - DOCTOR_DAYS * 86400)
            self.items = [i for i in data["items"] if isinstance(i, dict) and isinstance(i.get("t"), (int, float))
                          and (i.get("kind") in DOCTOR_EVENTS or i.get("kind") == "exit")]
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass

    def add(self, kind, account_id, t=None, **details):
        """Records a finding. Returns it, or None if the same one is already there."""
        item = {"t": int(time.time() if t is None else t), "kind": kind, "id": account_id, **details}
        if any((i["t"], i["kind"], i.get("id")) == (item["t"], kind, account_id) for i in self.items):
            return None
        self.items.append(item)
        cutoff = time.time() - DOCTOR_DAYS * 86400
        self.items = sorted((i for i in self.items if i["t"] >= cutoff), key=lambda i: i["t"], reverse=True)[:DOCTOR_MAX]
        self.dirty = True
        return item if item in self.items else None

    def count(self, kinds, hours=24):
        cutoff = time.time() - hours * 3600
        return sum(1 for i in self.items if i["t"] >= cutoff and i["kind"] in kinds)

    def scan(self):
        """New events from Roblox's logs since the last call."""
        events = []
        try:
            names = [n for n in os.listdir(self.folder) if LOG_FILE_RE.fullmatch(n) and "CrashHandler" not in n]
        except OSError:
            return events
        seen = set()
        for name in names:
            path = os.path.join(self.folder, name)
            seen.add(path)
            try:
                info = os.stat(path)
                state = self.logs.get(path)
                if state is None:
                    if info.st_mtime < self.since:
                        continue
                    state = self.logs[path] = {"offset": 0}
                if info.st_size <= state["offset"]:
                    continue
                with open(path, "rb") as f:
                    f.seek(state["offset"])
                    data = f.read()
            except OSError:   # Roblox can delete old logs at any moment
                continue
            end = data.rfind(b"\n") + 1   # the last line may be half written; it's read next time
            state["offset"] += end
            for event in parse_client_log(state, data[:end]):
                if event["kind"] == "exit" and event["user"]:
                    self.exits[event["user"]] = event["t"]
                if event["t"] > self.since:
                    events.append(event)
        self.logs = {p: st for p, st in self.logs.items() if p in seen}
        return sorted(events, key=lambda e: e["t"])

    def save(self):
        self.since = max(self.since, time.time() - DOCTOR_FRESH)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"since": self.since, "items": self.items}, f)
        os.replace(tmp, self.path)
        self.dirty = False


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


def _post(url, cookie, payload=None, extra_headers=None, expire=True):
    """POST with Roblox's CSRF handshake: the first attempt returns the token to retry with. A 401
    raises CookieExpired unless expire=False, which hands it back so the caller can look closer."""
    headers = {"Referer": "https://www.roblox.com/", "Origin": "https://www.roblox.com",
               "Content-Type": "application/json", **(extra_headers or {})}
    body = json.dumps(payload).encode() if payload is not None else b""
    status, hdrs, data = _request(url, cookie, "POST", headers, body)
    if status == 403 and hdrs.get("x-csrf-token"):
        headers["X-CSRF-TOKEN"] = hdrs["x-csrf-token"]
        status, hdrs, data = _request(url, cookie, "POST", headers, body)
    if status == 401 and expire:
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


def server_with_room(place_id, needed, pages=3):
    """The emptiest public server with room for `needed` more players, or None. Sending every
    account straight there is quicker than waiting for the first one to reach a server."""
    cursor = ""
    for _ in range(pages):
        servers, cursor = get_servers(place_id, cursor)
        for s in servers:   # emptiest first
            if s["maxPlayers"] - s["playing"] >= needed:
                return s["id"]
        if not cursor:
            break
    return None


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


def _ticket_request(cookie):
    return _post("https://auth.roblox.com/v1/authentication-ticket", cookie,
                 extra_headers={"RBXAuthenticationNegotiation": "1"}, expire=False)


def get_auth_ticket(cookie):
    status, hdrs, body = _ticket_request(cookie)
    if status == 401:
        # The ticket call says 401 for a login that is really dead, but Roblox also refuses it for a
        # login that still works on the website. Only a failing profile check proves expiry.
        get_user(cookie)   # raises CookieExpired if the login is genuinely gone
        said = re.sub(r"[^ -~]", "", body.decode("utf-8", "replace"))[:160]
        log.warning("Roblox refused a launch ticket (HTTP 401: %s) although the login still works; retrying", said)
        time.sleep(2)
        status, hdrs, body = _ticket_request(cookie)
        if status == 401:
            raise RobloxError("Roblox refused to start this account even though its login still works. "
                              "Wait a minute and try again, or launch fewer accounts at once.")
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
     "danger": "#da373c", "danger_hover": "#bf2f33", "danger_pressed": "#a3282b",
     "pick": "#7446e0", "on_pick": "#ffffff", "on_accent": "#ffffff"}   # pick: a chosen menu or list item
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
BUTTON, ACCENT, DANGER, PANEL_BUTTON = {}, {}, {}, {}


def set_widget_colors():
    BUTTON.update(normal=(C.get("button", C["panel"]), C.get("button_line", C["line"])),
                  active=(C["hover"], C["line_hover"]), pressed=(C["sel"], C["line_hover"]), disabled=(C["bg"], C["line"]))
    ACCENT.update(normal=(C["accent"], None), active=(C["accent_hover"], None),
                  pressed=(C["accent_pressed"], None), disabled=(C["panel"], None))
    DANGER.update(normal=(C["danger"], None), active=(C["danger_hover"], None),
                  pressed=(C["danger_pressed"], None), disabled=(C["panel"], None))
    PANEL_BUTTON.update(normal=(C["bg"], C["line"]), active=(C["hover"], C["line_hover"]),
                        pressed=(C["sel"], C["line_hover"]), disabled=(C["bg"], C["line"]))


set_widget_colors()


# The accent colours you can pick. Everything else in the palette stays the same.
ACCENTS = {
    "Violet": {"accent": "#7446e0", "accent_hover": "#6438c9", "accent_pressed": "#552fae",
               "accent_light": "#8a5ff0", "accent_text": "#b49cff"},
    "Blue": {"accent": "#3b76e8", "accent_hover": "#2f65cf", "accent_pressed": "#2755ae",
             "accent_light": "#5b92f5", "accent_text": "#9cc4ff"},
    "Teal": {"accent": "#14a08f", "accent_hover": "#118a7c", "accent_pressed": "#0e7367",
             "accent_light": "#2cc0ae", "accent_text": "#7fe3d6"},
    "Rose": {"accent": "#d9437a", "accent_hover": "#c0386a", "accent_pressed": "#a22f59",
             "accent_light": "#ec6491", "accent_text": "#ffa3c0"},
}


def apply_accent(name):
    """Switches the palette to a saved accent. Must run before apply_theme, which bakes the colours in."""
    colors = ACCENTS.get(name) or ACCENTS["Violet"]
    C.update(colors, pick=colors["accent"])
    set_widget_colors()


# The new interface's colours (ui/app.css, taken from the Figma design): plain greys, the main
# button light with dark text, focus a plain light outline, no tint and no glow.
NEW_LOOK = {"bg": "#151516", "panel": "#1b1b1d", "field": "#202023", "sel": "#2c2c30", "hover": "#26262a",
            "fg": "#e8e8e8", "muted": "#a0a0a3", "dim": "#8e8e93", "deep": "#1b1b1d",
            "accent": "#e8e8e8", "accent_hover": "#ffffff", "accent_pressed": "#cfcfd2", "accent_light": "#ffffff",
            "accent_text": "#e8e8e8", "on_accent": "#151516", "pick": "#3d3d42", "on_pick": "#e8e8e8",
            "green": "#5db85d", "red": "#de6a6a", "yellow": "#cf9f3e",
            "outline": "#333336", "thumb": "#3a3a3e", "check": "#8e8e93", "field_hover": "#3a3a3e",
            "pressed": "#2c2c30", "disabled": "#18181a", "panel_disabled": "#1d1d20",
            "line": "#2a2a2d", "line_hover": "#45454a", "glass_light": "#2a2a2d", "button": "#202023", "button_line": "#333336",
            "danger": "#b8483f", "danger_hover": "#c65449", "danger_pressed": "#a03f37"}


def apply_new_look():
    """Gives the Tk windows that remain (dialogs, toasts, menus) the new interface's look. Like
    apply_accent, it must run before apply_theme, which bakes the colours in."""
    C.update(NEW_LOOK)
    set_widget_colors()


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
                focuscolor=C["accent"], selectbackground=C["pick"], selectforeground=C["on_pick"],
                insertcolor=C["fg"], font=FONT)
    for style, name, colors in (("TButton", "AVButton", BUTTON), ("Accent.TButton", "AVAccent", ACCENT),
                                ("Panel.TButton", "AVPanelButton", PANEL_BUTTON), ("Danger.TButton", "AVDanger", DANGER)):
        element = button_element(name, colors)
        s.layout(style, [("Button.background", {"sticky": "nsew", "children": [(element, {"sticky": "nsew", "children": [
            ("Button.padding", {"sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]})]})]})])
    s.configure("TButton", padding=(round(8 * scale), round(5 * scale)), borderwidth=0, anchor="center")
    s.map("TButton", foreground=[("disabled", C["dim"])], background=[("active", C["bg"])])
    s.configure("Accent.TButton", foreground=C["on_accent"], font=("Segoe UI Semibold", 9))
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
    root.option_add("*TCombobox*Listbox.selectBackground", C["pick"])
    root.option_add("*TCombobox*Listbox.selectForeground", C["on_pick"])
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
              "on": image("check-on", box + gap, box, br, C["accent"], check=C["on_accent"], check_w=1.8 * scale, **kw),
              "on-hover": image("check-on-hover", box + gap, box, br, C["accent_light"], check=C["on_accent"],
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
    glow = round(18 * scale)
    # Tk repeats whatever is outside the border, so the whole glow must sit inside it. Otherwise the
    # glow's gradient repeats down both sides and the rim looks like a dashed line.
    top = root.altvault_glass_top = glow + 1
    side, middle = gr + 1, round(56 * scale)

    def glass_card(name, fill, rim):
        picture = image(name, 2 * side + middle, top + side + middle, gr, fill, rim, border_w=hair,
                        light=C["glass_light"], light_h=glow)
        s.element_create(f"{name}.bg", "image", picture, border=(side, top, side, side),
                         width=2 * side, height=top + side, sticky="nsew")

    glass_card("AVGlass", C["panel"], C["line"])
    s.layout("Glass.TFrame", [("AVGlass.bg", {"sticky": "nsew"})])
    glass_card("AVGlassHover", C["hover"], C["line_hover"])
    s.layout("GlassHover.TFrame", [("AVGlassHover.bg", {"sticky": "nsew"})])


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
        dark_title_bar(self)
        self.deiconify()
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
    return tk.Menu(root, tearoff=0, bg=C["deep"], fg=C["fg"], activebackground=C["pick"],
                   activeforeground=C["on_pick"], bd=0, relief="flat", activeborderwidth=4, font=FONT)


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
    """Options you set once, grouped into tabs. Everyday controls (Join, Anti-AFK) stay elsewhere."""

    TABS = (("general", "General"), ("modes", "Modes"), ("afk", "Anti-AFK"), ("alerts", "Alerts"),
            ("privacy", "Privacy"), ("look", "Appearance"), ("data", "Data"))

    def __init__(self, app, parent):
        super().__init__(parent)
        self.app = app
        s = app.scale
        wrap = int(560 * s)
        header, _ = page_header(self, "Settings", "Changes are saved straight away.")
        self.tab = Segmented(header, self.TABS, "general", lambda key: self.show_tab(key), s)
        self.tab.pack(side="right", anchor="s")
        self.body = ttk.Frame(self)
        self.body.pack(fill="both", expand=True, pady=(int(8 * s), 0))
        self.tabs = {key: ttk.Frame(self.body) for key, _ in self.TABS}
        self.tabs["general"].pack(fill="both", expand=True)

        def section(frm, title, first=False):
            ttk.Label(frm, text=title, style="Section.TLabel").pack(anchor="w", pady=(6 if first else 18, 0))

        def detail(frm, text, indent=24):
            label = ttk.Label(frm, text=text, style="Small.TLabel", wraplength=wrap, justify="left")
            label.pack(anchor="w", fill="x", padx=(indent, 0))

        def option(frm, var, title, text, command):
            ttk.Checkbutton(frm, text=title, variable=var, command=command).pack(anchor="w", pady=(8, 0))
            detail(frm, text)

        def buttons(frm, *items):
            row = ttk.Frame(frm)
            row.pack(anchor="w", pady=(8, 0))
            for i, (text, command) in enumerate(items):
                ttk.Button(row, text=text, command=command).pack(side="left", padx=(6 if i else 0, 0))
            return row

        def setting(frm, key, default, title, text, after=None):
            """A tick box saved straight into settings.json under `key`."""
            var = tk.BooleanVar(value=app.settings.get(key, default) is not False)

            def changed():
                app.settings[key] = var.get()
                save_settings(app.settings)
                if after:
                    after(var.get())
            option(frm, var, title, text, changed)
            return var

        tab = self.tabs["general"]
        section(tab, "Joining", first=True)
        option(tab, app.multi_var, "Multi-Roblox", "Run several Roblox windows at once. Turn off only if you "
               "want each launch to replace the previous window.", app.toggle_multi)
        option(tab, app.same_server_var, "Put accounts in the same server",
               "When joining several accounts with no server chosen, AltVault picks a server with room for all of "
               "them. If there's none, the others follow the first account.", app.save_settings)
        option(tab, app.auto_tile_var, "Arrange windows after joining",
               "Splits the screen evenly between every Roblox window, like the Tile button.", app.save_settings)
        row = ttk.Frame(tab)
        row.pack(anchor="w", pady=(10, 2))
        ttk.Label(row, text="Wait between launches").pack(side="left")
        self.delay_var = tk.StringVar(value=self.delay_label(app.settings.get("launch_delay", 0)))
        delay = ttk.Combobox(row, textvariable=self.delay_var, state="readonly", width=10,
                             values=[self.delay_label(d) for d in LAUNCH_DELAYS])
        delay.pack(side="left", padx=(8, 0))
        delay.bind("<<ComboboxSelected>>", lambda _e: self.set_delay())
        detail(tab, "A gap gives each client time to load before the next one starts. While a join runs, the "
               "Join button stops it.", indent=0)
        setting(tab, "launch_memory_wait", True, "Wait for free memory before each launch",
                f"If memory is over {LAUNCH_MEMORY:.0%} full, the next client waits up to a minute for it to free up, "
                "so the ones already running don't stall.")

        tab = self.tabs["modes"]
        section(tab, "Mode", first=True)
        mode = Segmented(tab, MODE_NAMES, mode_of(app.settings), app.set_mode, s)
        mode.pack(anchor="w", pady=(8, 0))
        app.mode_controls.append(mode)
        for key, label in MODE_NAMES:
            detail(tab, f"{label}: {MODE_HELP[key]}", indent=0)
        detail(tab, "FPS, graphics and sound apply from each window's next join, because Roblox reads them when it "
               "starts. Efficiency mode and memory priority apply to open windows straight away. An account's own "
               "FPS cap wins over the mode's.", indent=0)
        section(tab, "Main account")
        self.main_var = tk.StringVar()
        self.main_box = ttk.Combobox(tab, textvariable=self.main_var, state="readonly", width=28)
        self.main_box.pack(anchor="w", pady=(8, 2))
        self.main_box.bind("<<ComboboxSelected>>", lambda _e: self.set_main())
        detail(tab, "The account you play on. It keeps your own Roblox settings and normal priority in every mode. "
               "Right-click an account, Performance, Main account does the same.", indent=0)
        section(tab, "Lighter rendering")
        setting(tab, "lighter_rendering", True, "Lower textures and effects on lowest graphics",
                "Accounts on lowest graphics also start with the lowest texture quality, no anti-aliasing, a plain "
                "sky and no grass, using the rendering flags Roblox allows players to set. That saves memory and "
                "GPU. Skipped when Roblox starts through a bootstrapper such as Bloxstrap.")

        tab = self.tabs["alerts"]
        section(tab, "Discord alerts", first=True)
        detail(tab, "Get disconnects, expired logins, crashes and low memory on your phone. In a Discord server "
               "you own: Server Settings, Integrations, Webhooks, New Webhook, Copy Webhook URL, then paste it "
               "here. The link is stored encrypted to your Windows user.", indent=0)
        self.hook_var = tk.StringVar()
        hook = ttk.Entry(tab, textvariable=self.hook_var, show="•", width=60)
        hook.pack(anchor="w", pady=(8, 0))
        self.hook_row = buttons(tab, ("Save", self.save_hook), ("Send a test", self.test_hook), ("Remove", self.remove_hook))
        self.hook_state = ttk.Label(tab, text="", style="Small.TLabel")
        self.hook_state.pack(anchor="w", pady=(6, 0))
        self.show_hook_state()
        section(tab, "Panic hotkey")
        setting(tab, "panic_hotkey", False, f"Close everything with {PANIC_HOTKEY}",
                "Closes every Roblox window and stops any join, from any app, without asking first.",
                after=app.set_panic_hotkey)

        tab = self.tabs["afk"]
        section(tab, "How it works", first=True)
        detail(tab, "Roblox only reads input from the window in front, so Anti-AFK borrows focus for a moment and "
               "gives it straight back. It waits for you to pause, pings less often while you're using your PC, "
               "and stops as soon as you touch the mouse or keyboard. Switch it on or off at the bottom of any page.",
               indent=0)
        section(tab, "Key it presses")
        row = ttk.Frame(tab)
        row.pack(anchor="w", pady=(8, 2))
        self.key_var = tk.StringVar(value=str(app.settings.get("afk_key", AFK_DEFAULT_KEY)).lower())
        keys = ttk.Combobox(row, textvariable=self.key_var, state="readonly", width=8,
                            values=["f13", "space"] + list("abcdefghijklmnopqrstuvwxyz"))
        keys.pack(side="left")
        keys.bind("<<ComboboxSelected>>", lambda _e: self.set_key())
        detail(tab, "F13 is a key no game uses, so your character stays exactly where it is. Space makes it "
               "jump, which can bump it away from an egg or anything else it's standing at.", indent=0)

        tab = self.tabs["privacy"]
        section(tab, "Privacy", first=True)
        option(tab, app.stream_var, "Stream mode", "Hides usernames, avatars and your saved private server link "
               "in AltVault, and usernames in Roblox window titles, so you can share your screen.", app.toggle_stream)
        option(tab, app.hide_taskbar_var, "Hide Roblox from the taskbar", "Keeps the taskbar and Alt+Tab "
               "clear. Open a window from the Live page instead.", app.toggle_taskbar)
        section(tab, "Private servers")
        detail(tab, "The last private server link you used is kept, encrypted to your Windows user, and comes "
               "back in the Game box when you reopen AltVault.", indent=0)
        buttons(tab, ("Forget private link", app.forget_private_link))

        tab = self.tabs["look"]
        section(tab, "Accent colour", first=True)
        current = app.settings.get("accent") if app.settings.get("accent") in ACCENTS else "Violet"
        self.accent = Segmented(tab, [(name, name) for name in ACCENTS], current, self.set_accent, s)
        self.accent.pack(anchor="w", pady=(8, 0))
        detail(tab, "Used on the selected page, main buttons, ticks and charts. A new colour applies the next "
               "time you open AltVault.", indent=0)
        section(tab, "Sidebar")
        self.wide_var = tk.BooleanVar(value=app.settings.get("sidebar_wide", True))
        option(tab, self.wide_var, "Show page names", "Turn off for a slim, icons-only sidebar. Ctrl+B does the same.",
               lambda: app.set_sidebar(self.wide_var.get()))

        tab = self.tabs["data"]
        section(tab, "Your accounts", first=True)
        detail(tab, "Updates keep your accounts by themselves. To move them to another PC or Windows "
               "user, export them to a file locked with a password.", indent=0)
        buttons(tab, ("Export accounts...", app.export_accounts), ("Import accounts...", app.import_accounts))
        section(tab, "Your settings")
        detail(tab, "Back up your options, games, profiles and per-account settings. Logins and private server "
               "links are never included.", indent=0)
        buttons(tab, ("Back up settings...", app.export_settings), ("Restore settings...", app.import_settings))
        section(tab, "Upkeep")
        setting(tab, "clean_logs", True, "Delete old Roblox logs",
                f"Once a day, Roblox's own log files older than {ROBLOX_LOG_DAYS} days are deleted. They pile up over time.")
        setting(tab, "update_check", True, "Check for updates",
                "Once a day, asks GitHub whether a newer AltVault is out. Nothing is downloaded by itself.")
        section(tab, "About")
        ttk.Label(tab, text=f"AltVault {APP_VERSION}  ·  Made by HecticWins").pack(anchor="w", pady=(6, 0))
        buttons(tab, ("Open on GitHub", lambda: webbrowser.open(GITHUB_URL)),
                ("Open log folder", lambda: os.startfile(APP_DIR)), ("Keyboard shortcuts", app.show_shortcuts))
        buttons(tab, ("Check for updates", lambda: app.check_updates(tell=True)),
                ("Download page", lambda: webbrowser.open((app.update_available or (None, GITHUB_URL + "/releases"))[1])),
                ("Copy diagnostics", app.copy_diagnostics))

    def show_tab(self, key):
        for name, frame in self.tabs.items():
            frame.pack_forget() if name != key else frame.pack(fill="both", expand=True)
        if key == "modes":
            self.fill_main_box()

    def on_show(self):
        self.fill_main_box()

    def fill_main_box(self):
        app = self.app
        self.main_choices = [None] + list(app.accounts)
        set_text(self.main_box, values=["None"] + [app.who(a) for a in app.accounts])
        main = app.account(main_account(app.settings))
        self.main_var.set(app.who(main) if main else "None")

    def set_main(self):
        i = self.main_box.current()
        if 0 <= i < len(self.main_choices):
            self.app.set_main(self.main_choices[i])

    @staticmethod
    def delay_label(seconds):
        return "No wait" if not seconds else f"{seconds} seconds"

    def set_delay(self):
        seconds = next((d for d in LAUNCH_DELAYS if self.delay_label(d) == self.delay_var.get()), 0)
        self.app.settings["launch_delay"] = seconds
        save_settings(self.app.settings)

    def show_hook_state(self, text=None):
        set_text(self.hook_state, text or ("A webhook is saved. Alerts go to Discord." if self.app.webhook
                                           else "No webhook saved."))

    def save_hook(self):
        error = self.app.set_webhook(self.hook_var.get())
        if error:
            return self.show_hook_state(error)
        self.hook_var.set("")
        self.show_hook_state("Saved. Send a test to check it.")

    def remove_hook(self):
        self.app.set_webhook(None)
        self.hook_var.set("")
        self.show_hook_state()

    def test_hook(self):
        if not self.app.webhook:
            return self.show_hook_state("Save a webhook first.")
        self.show_hook_state("Sending...")
        self.app.test_webhook(lambda ok: self.show_hook_state(
            "Sent. Check your Discord channel." if ok else "Discord didn't accept it. Check the link, or make a new webhook."))

    def set_key(self):
        self.app.settings["afk_key"] = self.key_var.get()
        save_settings(self.app.settings)
        self.app.status.config(text=f"Anti-AFK will press {self.key_var.get().upper()}.")

    def set_accent(self, name):
        self.app.settings["accent"] = name
        save_settings(self.app.settings)
        self.app.toast("Accent colour saved", f"{name} applies the next time you open AltVault.")


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
        stat("lost", "Disconnected", C["red"])
        stat("hung", "Not responding", C["red"])
        stat("other", "Not from AltVault", C["dim"])

        section("This PC")
        stat("cpu", "Roblox CPU")
        stat("ram", "Roblox RAM")
        stat("free", "Free RAM")
        self.ram_bar = tk.Canvas(self.panel, height=max(int(5 * s), 3), bg=C["field"], highlightthickness=0, bd=0)
        self.ram_bar.pack(fill="x", padx=pad, pady=(int(5 * s), 0))
        self.ram_bar.bind("<Configure>", lambda _e: self.draw_ram_bar())

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
        """Keeps the stats column from being cut off: the 24-hour block is dropped if the window is
        too short for it."""
        self.panel.update_idletasks()
        # Where the last always-shown row really ends on screen (no guessing at padding).
        fixed_bottom = max(w.winfo_y() + w.winfo_height() for w in self.panel.winfo_children()
                           if w is not self.day_box and w.winfo_manager())
        day_h = self.day_box.winfo_reqheight() + int(10 * self.app.scale)
        show_day = self.panel.winfo_height() - fixed_bottom - int(8 * self.app.scale) >= day_h
        if show_day != self.day_shown:
            self.day_shown = show_day
            self.after_idle(self.fit_panel)   # layout moved; measure again until it settles
            self.day_box.pack(fill="x") if show_day else self.day_box.pack_forget()

    def count(self, color):
        return sum(1 for t in self.tiles.values() if t.color == color)

    def update_headline(self):
        n, ingame, loading, hung = len(self.tiles), self.count("green"), self.count("yellow"), self.count("red")
        other, lost = self.count("grey"), self.count("lost")
        set_text(self.big, str(ingame))
        set_text(self.big_of, f" / {n}")
        if not n:
            dot, text = C["muted"], "No Roblox windows"
        elif hung:
            dot, text = C["red"], f"{hung} not responding"
        elif lost:
            dot, text = C["red"], f"{lost} disconnected"
        elif loading:
            dot, text = C["yellow"], f"{loading} still joining"
        elif other:
            dot, text = C["muted"], f"{other} not opened by AltVault"
        else:
            dot, text = C["green"], "Every window is in a game"
        set_text(self.pill_dot, fg=dot)
        set_text(self.pill_text, text)
        for key, value in (("ingame", ingame), ("loading", loading), ("lost", lost), ("hung", hung), ("other", other)):
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

    COLORS = {"green": C["green"], "yellow": C["yellow"], "red": C["red"], "lost": C["red"], "grey": C["dim"]}

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
        set_text(self.label, name, fg=C["red"] if color in ("lost", "red") else C["fg"])

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


_tip_window = None


def show_tip(owner, x, y, text):
    hide_tip()
    global _tip_window
    _tip_window = tk.Toplevel(owner)
    _tip_window.overrideredirect(True)
    _tip_window.attributes("-topmost", True)
    tk.Label(_tip_window, text=text, bg=C["field"], fg=C["fg"], font=FONT, padx=8, pady=4, justify="left",
             wraplength=320, highlightthickness=1, highlightbackground=C["line_hover"]).pack()
    _tip_window.geometry(f"+{x}+{y}")


def hide_tip():
    global _tip_window
    if _tip_window is not None:
        try:
            _tip_window.destroy()
        except tk.TclError:
            pass
        _tip_window = None


def tip(widget, text):
    """Explains a control after the mouse has rested on it for a moment."""
    job = [None]

    def enter(_e):
        job[0] = widget.after(500, lambda: show_tip(widget, widget.winfo_rootx(),
                                                    widget.winfo_rooty() + widget.winfo_height() + 4, text))

    def leave(_e):
        if job[0]:
            widget.after_cancel(job[0])
            job[0] = None
        hide_tip()
    widget.bind("<Enter>", enter, add="+")
    widget.bind("<Leave>", leave, add="+")
    widget.bind("<Button-1>", leave, add="+")


class Rail(tk.Canvas):
    """The sidebar, drawn as one canvas so its highlight can move like liquid: the selected page's
    pill glides to the next item when you change page, a softer hover pill follows the mouse, and
    icons and labels blend colour as a pill passes under them. Motion only runs while it's happening.
    Wide, it shows page names in groups; slim, just icons, with the name as a tooltip."""

    ITEM_H, GLIDE_MS, HOVER_MS = 42, 230, 140
    WIDE, SLIM = 196, 64
    BOTTOM = ("settings",)

    def __init__(self, parent, app, items, logo, scale, groups=(), wide=True):
        s = self.s = scale
        self.wide = wide
        self.w = int((self.WIDE if wide else self.SLIM) * s)
        super().__init__(parent, bg=C["panel"], width=self.w, highlightthickness=0, bd=0)
        self.app, self.keys, self.logo, self.groups = app, [k for k, _, _ in items], logo, groups
        self.names = {k: t for k, _, t in items}
        self.h, self.pad = int(self.ITEM_H * s), int(8 * s)
        self.backdrop_id = self.create_image(0, 0, anchor="nw")   # the gradient, redrawn when the size changes
        self.backdrop, self.backdrop_size = None, None
        self.edge_id = self.create_line(self.w - 1, 0, self.w - 1, 4000, fill=C["line"])
        self.bar_h, bar_w = int(20 * s), max(int(3 * s), 3)
        self.img_bar = tk.PhotoImage(master=parent, data=base64.b64encode(
            shape_image(bar_w, self.bar_h, bar_w / 2, C["accent"])))
        self.logo_id = self.create_image(0, int(28 * s), image=logo)
        self.brand = self.create_text(0, int(28 * s), text="AltVault", anchor="w", fill=C["fg"],
                                      font=("Segoe UI Semibold", 11))
        icons = app.icon_font
        self.fold = self.create_text(0, int(28 * s), text="\uE76B", font=(icons, 9), fill=C["dim"])
        self.heads = [self.create_text(0, 0, text=title.upper(), anchor="w", fill=C["dim"], font=("Segoe UI Semibold", 7))
                      if title else None for title, _ in groups]
        self.hover_id = self.create_image(self.pad, 0, anchor="nw", state="hidden")
        self.sel_id = self.create_image(self.pad, 0, anchor="nw", state="hidden")
        self.bar_id = self.create_image(0, 0, image=self.img_bar, anchor="nw", state="hidden")
        self.make_pills()
        self.icon = {k: self.create_text(0, 0, text=g, font=(icons, 12), fill=C["muted"]) for k, g, _ in items}
        self.label = {k: self.create_text(0, 0, text=t, anchor="w", font=("Segoe UI", 10), fill=C["muted"])
                      for k, _, t in items}
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

    def make_pills(self):
        s = self.s
        width, height, radius = self.w - 2 * self.pad, self.h - int(4 * s), int(8 * s)
        mk = lambda fill, **kw: tk.PhotoImage(master=self, data=base64.b64encode(
            shape_image(width, height, radius, fill, border_w=max(s, 1.0), **kw)))
        self.img_hover = mk(C["hover"], border=C["line"], light=C["line_hover"], light_h=int(12 * s))
        self.img_sel = mk(C["sel"], border=C["line_hover"], light=C["glass_light"], light_h=int(14 * s))
        self.itemconfigure(self.hover_id, image=self.img_hover)
        self.itemconfigure(self.sel_id, image=self.img_sel)

    def set_wide(self, wide):
        if wide != self.wide:
            hide_tip()
            self.wide, self.w = wide, int((self.WIDE if wide else self.SLIM) * self.s)
            self.configure(width=self.w)
            self.make_pills()
            self.layout()

    def layout(self):
        """Groups run down from the logo; Settings sits above the maker's mark at the bottom."""
        height, s, wide = self.winfo_height(), self.s, self.wide
        if (self.w, height) != self.backdrop_size and height > 1:
            self.backdrop_size = (self.w, height)
            glow = blend(C["panel"], C["accent"], 0.13)
            rows = bytearray()
            for y in range(height):   # a faint accent glow at the top, settling into the panel colour, darker at the foot
                f = y / height
                row = blend(C["panel"], glow, max(0.0, 1 - f / 0.45) * 0.9) if f < 0.45 else \
                    blend(C["panel"], C["bg"], (f - 0.6) / 0.4 * 0.8) if f > 0.6 else C["panel"]
                rows += bytes((*_rgb(row), 255)) * self.w
            self.backdrop = tk.PhotoImage(master=self, data=base64.b64encode(_png(self.w, bytes(rows), height)))
            self.itemconfigure(self.backdrop_id, image=self.backdrop)
            self.tag_lower(self.backdrop_id)
        self.coords(self.edge_id, self.w - 1, 0, self.w - 1, 4000)
        word = "normal" if wide else "hidden"
        self.coords(self.logo_id, self.pad + int(20 * s) if wide else self.w // 2, int(28 * s))
        self.coords(self.brand, self.pad + int(40 * s), int(28 * s))
        self.coords(self.fold, self.w - int(20 * s), int(28 * s))
        self.itemconfigure(self.brand, state=word)
        self.itemconfigure(self.fold, state=word)
        y = int(58 * s)
        for head, (title, keys) in zip(self.heads, self.groups):
            if head is not None:
                self.itemconfigure(head, state=word)
                if wide:
                    self.coords(head, self.pad + int(10 * s), y + int(10 * s))
                    y += int(22 * s)
                else:
                    y += int(8 * s)
            for key in keys:
                self.pos[key] = y
                y += self.h
            y += int(6 * s)
        for key in self.BOTTOM:
            self.pos[key] = max(height - int(30 * s) - self.h, y)
        icon_x = self.pad + int(20 * s) if wide else self.w // 2
        for key, top in self.pos.items():
            self.coords(self.icon[key], icon_x, top + self.h // 2)
            self.coords(self.label[key], self.pad + int(40 * s), top + self.h // 2)
            self.itemconfigure(self.label[key], state=word)
        self.coords(self.mark, self.w // 2, height - int(14 * s))
        self.itemconfigure(self.mark, text="HecticWins" if wide else "HW")
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
        if not self.wide:   # the names are hidden, so say which page this is
            show_tip(self, self.winfo_rootx() + self.w + 6, self.winfo_rooty() + self.pos[key] + int(10 * self.s),
                     self.names[key])
        if first:   # appears in place, then follows the mouse
            self.hover_y = self.pos[key]
            self.apply()
        else:
            self.glide("hover_y", self.pos[key], self.HOVER_MS)

    def leave(self):
        hide_tip()
        self.configure(cursor="")
        job = self.jobs.pop("hover_y", None)
        if job:
            self.after_cancel(job)
        self.hover_key = self.hover_y = None
        self.apply()

    def click(self, event):
        if event.y < int(52 * self.s) and (not self.wide or event.x > self.w - int(40 * self.s)):
            return self.app.set_sidebar(not self.wide)
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
        for i, (key, title) in enumerate((("ingame", "Time in a game"),
                                          ("launches", "Launches that worked"), ("pings", "Anti-AFK pings"),
                                          ("closed", "Closed outside AltVault"))):
            self.cards[key] = StatCard(cards, title, s)
            self.cards[key].grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else int(10 * s), 0))
            cards.columnconfigure(i, weight=1, uniform="card")
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
        ok, failed = total("launch_ok"), total("launch_fail")
        rate = percent_of(ok, ok + failed)
        self.cards["launches"].set("—" if rate is None else f"{rate:.1f}%",
                                   f"{ok + failed:g} launch{'es' if ok + failed != 1 else ''}, {failed:g} failed")
        pings, missed = total("pings"), total("missed")
        self.cards["pings"].set(short_number(pings), f"{missed:g} missed and retried")
        closed, lost, moved = total("closed"), total("lost"), total("moved")
        extra = f"{chr(10)}{lost:g} dropped out of a game · {moved:g} moved server" if lost or moved else ""
        self.cards["closed"].set(f"{closed:g}", "crashes, kicks or closed by hand" + extra,
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


class ActionTile(ttk.Frame):
    """A quick action on a rounded card: an icon, a name and a short line, with a hover and a click."""

    def __init__(self, parent, app, icon, title, text, command):
        s = app.scale
        super().__init__(parent, style="Glass.TFrame", cursor="hand2", padding=(int(14 * s), int(11 * s)))
        glyph = tk.Label(self, text=icon, bg=C["panel"], fg=C["accent_text"], font=(app.icon_font, 14))
        glyph.pack(side="left", padx=(0, int(12 * s)))
        words = tk.Frame(self, bg=C["panel"])
        words.pack(side="left", fill="x")
        name = tk.Label(words, text=title, bg=C["panel"], fg=C["fg"], font=("Segoe UI Semibold", 10), anchor="w")
        name.pack(anchor="w")
        line = tk.Label(words, text=text, bg=C["panel"], fg=C["muted"], font=FONT, anchor="w")
        line.pack(anchor="w")
        parts = (glyph, words, name, line)

        def paint(lit):
            self.configure(style="GlassHover.TFrame" if lit else "Glass.TFrame")
            for part in parts:
                part.configure(bg=C["hover"] if lit else C["panel"])
        for part in (self, *parts):
            part.bind("<Enter>", lambda _e: paint(True))
            part.bind("<Leave>", lambda _e: paint(False))
            part.bind("<Button-1>", lambda _e: command())


class HomePage(ttk.Frame):
    """The overview: what's running, what needs you, the common actions and what happened lately.
    It reads numbers AltVault already has, and only while the page is on screen."""

    REFRESH_MS, ROWS, FEED = 3000, 5, 12

    def __init__(self, app, parent):
        super().__init__(parent)
        self.app, self.job = app, None
        s = app.scale
        header, self.sub = page_header(self, "Home", "")
        self.title_label = header.winfo_children()[0].winfo_children()[0]
        ttk.Button(header, text="Join accounts", style="Accent.TButton",
                   command=lambda: app.show_page("accounts")).pack(side="right", anchor="s")
        mode = Segmented(header, MODE_NAMES, mode_of(app.settings), app.set_mode, s)
        mode.pack(side="right", anchor="s", padx=(0, int(10 * s)))
        for key, label in mode.labels.items():
            tip(label, MODE_HELP[key])
        app.mode_controls.append(mode)
        cards = ttk.Frame(self)
        cards.pack(fill="x", pady=(int(14 * s), 0))
        self.cards = {}
        for i, (key, title) in enumerate((("accounts", "Accounts"), ("ingame", "In a game"), ("afk", "Anti-AFK"),
                                          ("memory", "Memory"))):
            self.cards[key] = StatCard(cards, title, s)
            self.cards[key].grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else int(10 * s), 0))
            cards.columnconfigure(i, weight=1, uniform="card")
        self.cards["memory"].blink = False
        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(int(10 * s), 0))
        for i, (icon, title, text, command) in enumerate((
                ("\uE710", "Add account", "Log in by browser", lambda: AddAccountDialog(app)),
                ("\uE7FC", "Pick a game", "Your saved games", lambda: app.show_page("games")),
                ("\uE7F4", "Watch windows", "See every client", lambda: app.show_page("live")),
                ("\uE7C4", "Arrange windows", "Tile on screen", app.tile))):
            ActionTile(actions, app, icon, title, text, command).grid(
                row=0, column=i, sticky="nsew", padx=(0 if i == 0 else int(10 * s), 0))
            actions.columnconfigure(i, weight=1, uniform="action")
        lower = ttk.Frame(self)
        lower.pack(fill="both", expand=True, pady=(int(10 * s), 0))
        lower.columnconfigure(0, weight=2, uniform="lower")
        lower.columnconfigure(1, weight=3, uniform="lower")
        lower.rowconfigure(0, weight=1)
        pad = (int(16 * s), int(12 * s))
        left = ttk.Frame(lower, style="Glass.TFrame", padding=pad)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, int(10 * s)))
        right = ttk.Frame(lower, style="Glass.TFrame", padding=pad)
        right.grid(row=0, column=1, sticky="nsew")
        self.left_title = tk.Label(left, text="Needs attention", bg=C["panel"], fg=C["fg"],
                                   font=("Segoe UI Semibold", 10))
        self.left_title.pack(anchor="w", pady=(0, int(6 * s)))
        self.rows = []
        for _ in range(self.ROWS):
            row = tk.Frame(left, bg=C["panel"])
            row.pack(fill="x", pady=int(3 * s))
            dot = tk.Label(row, text="●", bg=C["panel"], fg=C["dim"], font=("Segoe UI", 7))
            dot.pack(side="left", anchor="n", padx=(0, int(8 * s)), pady=(int(3 * s), 0))
            text = tk.Label(row, text="", bg=C["panel"], fg=C["fg"], font=FONT, justify="left", anchor="w")
            text.pack(side="left", fill="x", expand=True)
            for part in (row, dot, text):
                part.bind("<Button-1>", lambda _e, r=row: self.open_row(r))
            row.target = None
            self.rows.append((row, dot, text))
        left.bind("<Configure>", lambda e: [t.config(wraplength=max(e.width - int(56 * s), 80)) for _, _, t in self.rows])
        tk.Label(right, text="Recent activity", bg=C["panel"], fg=C["fg"],
                 font=("Segoe UI Semibold", 10)).pack(anchor="w", pady=(0, int(6 * s)))
        self.feed = []
        for _ in range(self.FEED):
            row = tk.Frame(right, bg=C["panel"])
            row.pack(fill="x", pady=int(2 * s))
            when = tk.Label(row, text="", bg=C["panel"], fg=C["dim"], font=FONT, width=6, anchor="w")
            when.pack(side="left")
            text = tk.Label(row, text="", bg=C["panel"], fg=C["muted"], font=FONT, anchor="w")
            text.pack(side="left", fill="x", expand=True)
            self.feed.append((when, text))
        self.feed_chars = 60
        right.bind("<Configure>", self.fit_feed)

    def fit_feed(self, event):
        """Fits the feed to its card: as much text as the width allows, and only whole rows, so a short
        window never shows half a line."""
        s = self.app.scale
        self.feed_chars = max((event.width - int(80 * s)) // max(int(6 * s), 1), 24)
        row_h = max(row.winfo_reqheight() for row in (w.master for w, _ in self.feed)) + 2 * int(2 * s)
        rows = max((event.height - int(52 * s)) // row_h, 1)
        for i, (when, _) in enumerate(self.feed):
            when.master.pack(fill="x", pady=int(2 * s)) if i < rows else when.master.pack_forget()

    def attention(self):
        """[(colour, text, page)], most serious first; one reassuring line when all is well."""
        app, items = self.app, []
        windows = len(app.running)
        if app.expired:
            n = len(app.expired)
            items.append((C["red"], f"{n} login{'s' if n != 1 else ''} expired. Right-click, Account, Log in again.", "accounts"))
        if app.lost_flagged:
            n = len(app.lost_flagged)
            items.append((C["red"], f"{n} account{'s' if n != 1 else ''} disconnected from the game.", "live"))
        recent = [i for i in app.doctor.items if i["t"] >= time.time() - 3600 and explain(i)[4] == "red"
                  and i["kind"] not in ("login", "lost")]
        if recent:
            page = app.pages["doctor"]
            items.append((C["red"], f"{page.who(recent[0])}: {explain(recent[0])[0]}.", "doctor"))
        used = memory_used_fraction()
        if windows >= 2 and used >= AFK_PRESSURE:
            items.append((C["red"] if used >= MEMORY_ALERT else C["yellow"],
                          f"Memory is {used:.0%} full. Clients slow down; " + (
                              "close some windows or apps." if mode_of(app.settings) == "max"
                              else "try Max alts mode, or close some windows."), "pulse"))
        if windows and not app.anti_afk_var.get():
            items.append((C["yellow"], "Anti-AFK is off while Roblox is open.", None))
        if app.update_available:
            items.append((C["accent"], f"AltVault {app.update_available[0]} is out. Click to open the download page.", "update"))
        return items or [(C["green"], "Everything looks good.", None)]

    def steps(self):
        """The first-run checklist: [(done, text, page)], or None once it's finished with."""
        app = self.app
        if app.settings.get("onboarded"):
            return None
        added, picked, joined = bool(app.accounts), bool(app.place_var.get().strip()), bool(app.running)
        if added and joined:
            app.settings["onboarded"] = True
            save_settings(app.settings)
            return None
        return [(added, "Add your first account", "accounts"), (picked, "Pick a game or paste a link", "games"),
                (joined, "Select accounts and click Join", "accounts")]

    def open_row(self, row):
        if row.target == "update" and self.app.update_available:
            webbrowser.open(self.app.update_available[1])
        elif row.target and row.target != "update":
            self.app.show_page(row.target)

    def refresh(self):
        app = self.app
        hour = time.localtime().tm_hour
        set_text(self.title_label, "Good morning" if 5 <= hour < 12 else "Good afternoon" if hour < 18 else "Good evening")
        saved, open_now = len(app.accounts), len(app.running)
        ingame = sum(1 for acc_id in app.running.values()
                     if (app.presence.get(acc_id) or {}).get("userPresenceType") == 2)
        set_text(self.sub, f"{saved} account{'s' if saved != 1 else ''} saved · {open_now} open · {ingame} in a game")
        bad = len(app.expired)
        self.cards["accounts"].set(str(saved), f"{bad} need{'s' if bad == 1 else ''} logging in" if bad else "all logged in",
                                   C["red"] if bad else None)
        self.cards["ingame"].set(f"{ingame} / {open_now}", "windows in a game", C["green"] if open_now and ingame == open_now else None)
        on = app.anti_afk_var.get()
        self.cards["afk"].set("On" if on else "Off", f"keeping {len(app.afk_last)} window{'s' if len(app.afk_last) != 1 else ''} active"
                              if on else "accounts can be idle-kicked", C["green"] if on else C["yellow"] if open_now else None)
        free, total = memory_status()
        used = 1 - free / total if total else 0.0
        self.cards["memory"].set(f"{used:.0%}", f"{free / 2 ** 30:.1f} of {total / 2 ** 30:.0f} GB free",
                                 C["red"] if used >= MEMORY_ALERT else C["yellow"] if used >= AFK_PRESSURE else None)
        steps = self.steps()
        set_text(self.left_title, "Get started" if steps else "Needs attention")
        items = [(C["green"] if done else C["dim"], text, page) for done, text, page in steps] if steps else self.attention()
        for i, (row, dot, text) in enumerate(self.rows):
            color, words, page = items[i] if i < len(items) else (C["panel"], "", None)
            row.target = page
            set_text(dot, fg=color)
            set_text(text, words, cursor="hand2" if page else "")
        lines = list(ACTIVITY.items)[:self.FEED]
        for i, (when, text) in enumerate(self.feed):
            if i < len(lines):
                created, level, message = lines[i]
                message = message.removeprefix("Anti-AFK: ").strip() if level < logging.WARNING else message
                short = message if len(message) <= self.feed_chars else message[:self.feed_chars - 1].rstrip() + "…"
                set_text(when, time.strftime("%H:%M", time.localtime(created)))
                set_text(text, short, fg=C["yellow"] if level >= logging.WARNING else C["muted"])
            else:
                set_text(when, "")
                set_text(text, "Nothing yet." if i == 0 and not lines else "", fg=C["muted"])

    def on_show(self):
        self.update_idletasks()
        self.tick()

    def tick(self):
        self.refresh()
        self.job = self.after(self.REFRESH_MS, self.tick)

    def on_hide(self):
        if self.job:
            self.after_cancel(self.job)
            self.job = None


class DoctorPage(ttk.Frame):
    """Every drop AltVault could explain: what happened, why, and what to do."""

    ACTIONS = {"login": "Log in again", "afk": "Turn on Anti-AFK", "live": "Open Live", "pulse": "Open Pulse"}

    def __init__(self, app, parent):
        super().__init__(parent)
        self.app, self.shown = app, {}
        s = app.scale
        header, _ = page_header(self, "Doctor", "Why accounts dropped and what to do, worked out from Roblox's own logs.")
        ttk.Button(header, text="Clear", command=self.clear).pack(side="right", anchor="s")
        cards = ttk.Frame(self)
        cards.pack(fill="x", pady=(int(14 * s), 0))
        self.cards = {}
        for i, (key, title) in enumerate((("dropped", "Dropped, 24 hours"), ("idle", "Idle kicks"),
                                          ("login", "Login problems"), ("moved", "Server moves"))):
            self.cards[key] = StatCard(cards, title, s)
            self.cards[key].grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else int(10 * s), 0))
            cards.columnconfigure(i, weight=1, uniform="card")
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, pady=(int(10 * s), 0))
        body.columnconfigure(0, weight=3, uniform="doctor")
        body.columnconfigure(1, weight=2, uniform="doctor")
        body.rowconfigure(0, weight=1)
        table = ttk.Frame(body)
        table.grid(row=0, column=0, sticky="nsew", padx=(0, int(10 * s)))
        self.tree = ttk.Treeview(table, columns=("when", "who", "what"), show="headings", selectmode="browse",
                                 style="Rows.Treeview")
        for col, title, width in (("when", "When", 95), ("who", "Account", 150), ("what", "What happened", 180)):
            self.tree.heading(col, text="   " + title, anchor="w")
            self.tree.column(col, width=int(width * s), minwidth=int(70 * s), anchor="w", stretch=col == "what")
        self.tree.pack(side="left", fill="both", expand=True)
        auto_scrollbar(self.tree, ttk.Scrollbar(table, orient="vertical", command=self.tree.yview))
        add_row_hover(self.tree)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.show_detail())
        self.empty = ttk.Label(table, text="Nothing to report. If an account drops, the reason appears here.",
                               style="PanelMuted.TLabel", justify="center")
        card = ttk.Frame(body, style="Glass.TFrame", padding=(int(16 * s), int(12 * s)))
        card.grid(row=0, column=1, sticky="nsew")
        make = lambda fg, font: tk.Label(card, text="", bg=C["panel"], fg=fg, font=font, justify="left", anchor="w")
        self.title = make(C["fg"], ("Segoe UI Semibold", 11))
        self.when = make(C["muted"], FONT)
        self.why_head = make(C["muted"], ("Segoe UI Semibold", 9))
        self.why = make(C["fg"], FONT)
        self.fix_head = make(C["muted"], ("Segoe UI Semibold", 9))
        self.fix = make(C["fg"], FONT)
        self.action = ttk.Button(card, command=self.act)
        self.title.pack(fill="x")
        self.when.pack(fill="x")
        self.why_head.pack(fill="x", pady=(int(12 * s), 0))
        self.why.pack(fill="x")
        self.fix_head.pack(fill="x", pady=(int(12 * s), 0))
        self.fix.pack(fill="x")
        self.texts = (self.title, self.when, self.why, self.fix)
        card.bind("<Configure>", lambda e: [t.config(wraplength=max(e.width - int(36 * s), 100)) for t in self.texts])

    def who(self, item):
        if not item.get("id"):
            return "This PC" if item["kind"] == "memory" else "Unknown account"
        acc = self.app.account(item["id"])
        return self.app.who(acc) if acc else "Not in AltVault"

    def refresh(self):
        items, doctor = self.app.doctor.items, self.app.doctor
        dropped = doctor.count(("exit", "login", "lost", "crash"))
        idle = sum(1 for i in items if i.get("code") == 278 and i["t"] >= time.time() - 86400)
        self.cards["dropped"].set(str(dropped), "all clear" if not dropped else "see the list below", C["red"] if dropped else C["green"])
        self.cards["idle"].set(str(idle), "last 24 hours", C["red"] if idle else None)
        logins = doctor.count(("login",))
        self.cards["login"].set(str(logins), "last 24 hours", C["red"] if logins else None)
        moved = doctor.count(("moved",))
        self.cards["moved"].set(str(moved), "last 24 hours", C["yellow"] if moved else None)
        chosen = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        self.shown = {}
        for n, item in enumerate(items):
            iid = f"{item['t']}-{item['kind']}-{item.get('id')}-{n}"
            self.shown[iid] = item
            when = time.strftime("%d %b %H:%M", time.localtime(item["t"]))
            self.tree.insert("", "end", iid=iid, values=("   " + when, "   " + self.who(item), "   " + explain(item)[0].split(" (error")[0]))
        if items:
            self.empty.place_forget()
            self.tree.selection_set(chosen[0] if chosen and self.tree.exists(chosen[0]) else next(iter(self.shown)))
        else:
            self.empty.place(relx=0.5, rely=0.5, anchor="center")
        self.show_detail()

    def show_detail(self):
        chosen = self.tree.selection()
        item = self.shown.get(chosen[0]) if chosen else None
        if item is None:
            self.current = None
            for label in (*self.texts, self.why_head, self.fix_head):
                label.config(text="")
            self.title.config(text="All clear")
            self.when.config(text="Doctor reads Roblox's logs while your accounts run.")
            self.action.pack_forget()
            return
        self.current = item
        title, why, fix, action, color = explain(item)
        self.title.config(text=title, fg=C[color])
        self.when.config(text=f"{self.who(item)}  ·  {time.strftime('%d %b, %H:%M', time.localtime(item['t']))}")
        self.why_head.config(text="Why")
        self.why.config(text=why)
        self.fix_head.config(text="What to do")
        self.fix.config(text=fix)
        self.action_key = action
        if action and not (action == "login" and not self.app.account(item.get("id"))):
            self.action.config(text=self.ACTIONS[action])
            self.action.pack(anchor="w", pady=(int(12 * self.app.scale), 0))
        else:
            self.action.pack_forget()

    def act(self):
        app, key = self.app, self.action_key
        if key == "login":
            acc = app.account(self.current.get("id"))
            if acc:
                AddAccountDialog(app, acc)
        elif key == "afk":
            if not app.anti_afk_var.get():
                app.flip_anti_afk()
        else:
            app.show_page(key)

    def clear(self):
        if self.app.doctor.items and ask_yes_no("Clear Doctor", "Remove everything from this list?", parent=self.app.root, yes="Clear"):
            self.app.doctor.items = []
            self.app.save_doctor()
            self.refresh()

    def on_show(self):
        self.refresh()
        self.app.doctor_scan()


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
            self.app.status.config(text="Paste private server links on the Accounts page; the last one is kept there.")
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
                               selectbackground=C["pick"], selectforeground=C["on_pick"], highlightthickness=0,
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




class App:
    def __init__(self, root, accounts):
        self.root = root
        self.accounts = accounts
        self.settings = load_settings()
        for old in ("hatch", "eggs_total", "eggs_since"):   # the egg calculator's numbers, from before 2.8
            self.settings.pop(old, None)
        if accounts:   # already set up: no first-run checklist
            self.settings["onboarded"] = True
        self.scale = root.winfo_fpixels("1i") / 96
        self.presence = {}     # account id -> presence dict
        self.expired = set()   # account ids whose cookie stopped working
        self.running = {}      # Roblox pid -> account id
        self.saving = {}       # Roblox pid -> (eco, memory_low) AltVault last set for it
        self.saving_refused = set()   # pids Windows wouldn't let AltVault change
        self.mode_controls = []       # the Mode switches on Home and in Settings, kept in step
        self.join_cancel = threading.Event()   # set by the Join button's "stop" while a join runs
        self.join_failed = []         # account ids the last join couldn't start
        self.webhook = load_webhook()
        self.webhook_sent = []        # times of recent Discord alerts, for WEBHOOK_PER_HOUR
        self.hotkey = None
        self.update_available = None  # (version text, release page) once a newer AltVault is out
        self.web = self.web_host = None   # the new interface, once WebView2 has started
        self.web_page, self.web_job, self.web_thumbs = "home", None, {}
        self.web_sent = {}   # the last data sent to each page
        self.web_ranges = {"stats": "day", "pulse": "hour"}
        self.web_settings_tab, self.web_doctor_pick = "general", ""
        self.web_pc, self.web_rbx = PcMeter(), CpuMeter()
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
        self.doctor = Doctor()        # why accounts dropped
        self.doctor_busy = False      # a log scan is running
        self.doctor_closed = []       # (account id, when) of windows that closed unexpectedly, to explain
        self.first_seen = {}          # Roblox pid -> when its window was first seen
        self.last_ingame = {}         # account id -> when presence last said "in a game"
        self.server_of = {}           # Roblox pid -> (server it is in now, times it has been moved to another)
        self.lost_flagged = set()     # account ids currently flagged as disconnected
        self.expired_told = set()     # account ids whose expired login has been announced
        self.memory_hot = 0           # minute readings in a row with memory nearly full
        self.memory_alerted = False
        self.presence_ticks = 0       # counts presence checks made while hidden
        self.recent = collections.deque(maxlen=60)   # (time, measure()) once a minute, for Pulse
        self.pc_meter, self.rbx_meter = PcMeter(), CpuMeter()
        self.next_sample = time.monotonic() + 5
        self.next_save = time.monotonic() + 300
        self.expected_close = set()   # Roblox pids AltVault was asked to close, so they aren't counted
        self.tick_job = None

        new_look = self.settings.get("new_look", True) and os.path.exists(os.path.join(UI_DIR, "index.html"))
        if not hasattr(root, "altvault_images"):   # main() styles the window first when it asks for a key
            if new_look:
                apply_new_look()
            else:
                apply_accent(self.settings.get("accent"))
            apply_theme(root, self.scale)
        root.title(APP_TITLE)
        saved = self.settings.get("geometry")
        if self.settings.get("layout") != LAYOUT_VERSION:   # windows sized for the old layout start fresh
            saved, self.settings["layout"] = None, LAYOUT_VERSION
        root.geometry(fit_to_screen(safe_geometry(saved)))
        root.minsize(*min_size(self.scale))
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.build()
        root.withdraw()   # shown once the dark title bar and the new interface's dark cover are in place
        if new_look:
            self.start_web()
        dark_title_bar(root)
        root.deiconify()

        self.load_cached_avatars()
        self.refresh()
        if self.multi_var.get():
            self.toggle_multi(quiet=True)
        if self.anti_afk_var.get():
            self.toggle_anti_afk(quiet=True)
        threading.Thread(target=self.fetch_missing_avatars, daemon=True).start()
        self.tick_job = self.root.after(500, self.tick)
        self.root.after(1000, self.refresh_presence)
        self.root.after(3000, lambda: self.doctor_scan(quiet=True))
        restore_flags_safely()   # if AltVault stopped mid-launch, an alt's flags may still be in place
        self.root.after(60_000, self.daily_chores)
        self.set_panic_hotkey(self.settings.get("panic_hotkey", False), quiet=True)
        # Nobody sees presence while AltVault is minimised, so it pauses then and catches up on restore.
        self.presence_stale = False
        root.bind("<Map>", self.on_restore)

    # ------------------------------------------------------------ layout
    NAV = (("home", "\uE80F", "Home"), ("accounts", "\uE716", "Accounts"), ("profiles", "\uE77B", "Profiles"), ("games", "\uE7FC", "Games"),
           ("live", "\uE7F4", "Live"), ("stats", "\uE9D2", "Stats"), ("pulse", "\uE95E", "Pulse"),
           ("doctor", "\uE9D9", "Doctor"),
           ("settings", "\uE713", "Settings"))
    NAV_GROUPS = ((None, ("home",)), ("Manage", ("accounts", "profiles", "games")), ("Monitor", ("live", "stats", "pulse", "doctor")))

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
        self.rail = Rail(self.root, self, self.NAV, self.logo, s, self.NAV_GROUPS, self.settings.get("sidebar_wide", True))
        self.rail.pack(side="left", fill="y")

        main = ttk.Frame(self.root, padding=(16, 14, 16, 12))
        main.pack(side="left", fill="both", expand=True)
        # The Anti-AFK switch and the status line, the same on every page.
        footer = ttk.Frame(main)
        footer.pack(side="bottom", fill="x", pady=(10, 0))
        afk_box = ttk.Checkbutton(footer, text="Anti-AFK", variable=self.anti_afk_var, command=self.toggle_anti_afk)
        afk_box.pack(side="left")
        tip(afk_box, "Stops Roblox's 20-minute idle kick. Ctrl+Shift+A")
        self.status = FlashLabel(footer, text="", bg=C["bg"], fg=C["muted"], font=FONT)
        self.status.pack(side="right")

        host = ttk.Frame(main)
        host.pack(fill="both", expand=True)
        self.toast_box = None
        self.pages = {"home": HomePage(self, host), "accounts": self.build_accounts(host), "profiles": ProfilesPage(self, host),
                      "games": GamesPage(self, host), "live": LivePage(self, host), "stats": StatsPage(self, host),
                      "pulse": PulsePage(self, host), "doctor": DoctorPage(self, host), "settings": SettingsPage(self, host)}
        self.page = None
        self.show_page("home")
        for i, (key, _, _) in enumerate(self.NAV[:-1], 1):   # Ctrl+1..8; Settings is Ctrl+,
            self.root.bind(f"<Control-Key-{i}>", lambda _e, k=key: self.show_page(k))
        self.root.bind("<Control-comma>", lambda _e: self.show_page("settings"))
        self.root.bind("<Control-b>", lambda _e: self.set_sidebar(not self.rail.wide))
        self.root.bind("<Control-j>", lambda _e: self.join_selected())
        self.root.bind("<Control-n>", lambda _e: AddAccountDialog(self))
        self.root.bind("<Control-Shift-A>", lambda _e: self.flip_anti_afk())
        self.root.bind("<F1>", lambda _e: self.show_shortcuts())

    def doctor_scan(self, quiet=False):
        """Looks for new evidence on a worker thread. `quiet` files it without alerting (first scan)."""
        if self.doctor_busy:
            return
        self.doctor_busy = True

        def work():
            try:
                events = self.doctor.scan()
            except Exception:
                log.exception("Doctor couldn't read Roblox's logs")
                events = []
            self.ui(self.doctor_absorb, events, quiet)

        threading.Thread(target=work, daemon=True).start()

    def doctor_absorb(self, events, quiet):
        self.doctor_busy = False
        now, fresh = time.time(), []
        for e in events:
            user = e["user"]
            if e["kind"] == "exit" and e["code"] == CLIENT_LEFT:
                continue
            details = {"code": e["code"]} if e["code"] else {}
            if e["memory"]:
                details["memory"] = True
            acc = self.account(user)
            if now - e["t"] <= DOCTOR_FRESH:   # what AltVault is doing now only explains what just happened
                if not self.anti_afk_var.get():
                    details["anti_afk"] = "off"
                elif acc and self.afk_skipped(acc):
                    details["anti_afk"] = "skipped"
                else:
                    details["afk_key"] = str(self.settings.get("afk_key", AFK_DEFAULT_KEY)).lower()
            item = self.doctor.add(e["kind"], user, e["t"], **details)
            if item:
                fresh.append(item)
        for account_id, closed_at in self.doctor_closed:
            left = self.doctor.exits.get(account_id)
            if left is None or abs(left - closed_at) > DOCTOR_FRESH:   # nothing in the log says why it went
                item = self.doctor.add("crash", account_id, closed_at)
                if item:
                    fresh.append(item)
        self.doctor_closed = []
        serious = [i for i in fresh if explain(i)[4] == "red"]
        if serious and not quiet:
            title = explain(serious[0])[0]
            who = self.pages["doctor"].who(serious[0])
            self.alert(f"{who}: {title}" if len(serious) == 1 else f"{len(serious)} accounts dropped",
                       "Open Doctor to see why and what to do.")
        if self.doctor.dirty:
            self.save_doctor()
        if fresh and self.page == "doctor":
            self.pages["doctor"].refresh()

    def doctor_note(self, kind, account_id, **details):
        """A finding AltVault saw itself, rather than one from a log."""
        if self.doctor.add(kind, account_id, **details):
            self.save_doctor()
            if self.page == "doctor":
                self.pages["doctor"].refresh()

    def save_doctor(self):
        try:
            self.doctor.save()
        except OSError as e:
            log.warning("Couldn't save Doctor's findings: %s", type(e).__name__)

    def set_sidebar(self, wide):
        self.rail.set_wide(wide)
        self.settings["sidebar_wide"] = wide
        self.pages["settings"].wide_var.set(wide)
        save_settings(self.settings)

    def flip_anti_afk(self):
        self.anti_afk_var.set(not self.anti_afk_var.get())
        self.toggle_anti_afk()

    def show_shortcuts(self):
        show_info("Keyboard shortcuts", "Ctrl+1 to Ctrl+8   Go to a page\nCtrl+,   Settings\nCtrl+J   Join the selected accounts\n"
                  "Ctrl+N   Add an account\nCtrl+B   Show or hide page names\nCtrl+Shift+A   Anti-AFK on or off\n"
                  "F1   This list", parent=self.root)

    def toast(self, title, text="", warning=False):
        """A note in the corner that doesn't interrupt anything and goes away by itself."""
        if self.toast_box is not None:
            self.toast_box.destroy()
        s = self.scale
        box = self.toast_box = tk.Frame(self.root, bg=C["field"], highlightthickness=1, highlightbackground=C["line_hover"],
                                        padx=int(14 * s), pady=int(10 * s))
        tk.Frame(box, bg=C["yellow"] if warning else C["accent"], width=max(int(3 * s), 3)).pack(side="left", fill="y", padx=(0, int(10 * s)))
        words = tk.Frame(box, bg=C["field"])
        words.pack(side="left")
        tk.Label(words, text=title, bg=C["field"], fg=C["fg"], font=("Segoe UI Semibold", 9), anchor="w").pack(anchor="w")
        if text:
            tk.Label(words, text=text, bg=C["field"], fg=C["muted"], font=FONT, justify="left", wraplength=int(320 * s)).pack(anchor="w")
        box.place(relx=1.0, rely=1.0, x=-int(20 * s), y=-int(48 * s), anchor="se")
        box.after(7000 if warning else 4000, lambda: self.hide_toast(box))
        box.bind("<Button-1>", lambda _e: self.hide_toast(box))

    def hide_toast(self, box):
        if box is self.toast_box:
            self.toast_box = None
        try:
            box.destroy()
        except tk.TclError:
            pass

    def alert(self, title, text):
        """Something worth knowing: a toast if AltVault is open, and a tray alert either way."""
        if self.root.state() == "normal":
            self.toast(title, text, warning=True)
        if self.tray:
            self.tray.notify(title, text, warning=True)
        self.send_alert(title, text)

    def send_alert(self, title, text):
        """Also posts the alert to the user's Discord webhook, if they set one, at most
        WEBHOOK_PER_HOUR times an hour."""
        now = time.time()
        self.webhook_sent = [t for t in self.webhook_sent if now - t < 3600]
        if not self.webhook or len(self.webhook_sent) >= WEBHOOK_PER_HOUR:
            return
        self.webhook_sent.append(now)
        threading.Thread(target=self.post_webhook, args=(self.webhook, title, text), daemon=True).start()

    def post_webhook(self, url, title, text):
        try:
            if not send_webhook(url, title, text):
                log.warning("Discord didn't accept the alert; the webhook may have been deleted")
                return False
            return True
        except RobloxError:
            log.warning("Couldn't reach Discord to send an alert")
            return False

    def export_settings(self):
        path = filedialog.asksaveasfilename(parent=self.root, title="Back up settings", defaultextension=".json",
                                            initialfile="altvault-settings.json", filetypes=[("Settings backup", "*.json")])
        if not path:
            return
        self.save_settings()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(settings_backup(self.settings), f, indent=1)
        except OSError as e:
            return show_error("Back up settings", f"Couldn't write that file: {e.strerror or e}", parent=self.root)
        self.toast("Settings backed up", os.path.basename(path))

    def import_settings(self):
        path = filedialog.askopenfilename(parent=self.root, title="Restore settings", filetypes=[("Settings backup", "*.json")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                count = restore_backup(self.settings, json.load(f))
        except (OSError, ValueError) as e:
            log.warning("Couldn't restore settings: %s", type(e).__name__)
            return show_error("Restore settings", "That file isn't an AltVault settings backup.", parent=self.root)
        for var, key in ((self.multi_var, "multi"), (self.same_server_var, "same_server"), (self.auto_tile_var, "auto_tile"),
                         (self.stream_var, "stream_mode"), (self.hide_taskbar_var, "hide_taskbar")):
            var.set(self.settings.get(key, var.get()))
        self.save_settings()
        self.update_recents_box()
        self.refresh()
        self.toast("Settings restored", f"{count} settings restored. Colours apply the next time you open AltVault.")

    def show_page(self, key):
        if getattr(self, "web", None):   # the new interface draws every page
            self.web.post({"type": "show", "page": key})
            self.show_web_page(key)
            return
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
        self.private_link = load_private_link()
        reopen_link = self.private_link and self.settings.get("place_is_private") and not self.stream_var.get()
        self.place_var = tk.StringVar(value=self.private_link if reopen_link else self.settings.get("place", ""))
        self.place_box = ttk.Combobox(game, textvariable=self.place_var)
        self.place_box.grid(row=1, column=0, sticky="ew", pady=(3, 0))
        add_placeholder(self.place_box, self.place_var, "Paste a game or private server link, or pick one")
        self.star_btn = ttk.Button(game, text="☆", width=3, command=self.toggle_favorite)
        self.star_btn.grid(row=1, column=1, padx=(6, 0), pady=(3, 0), sticky="ns")
        ttk.Button(game, text="Servers...", command=self.open_servers).grid(row=1, column=2, padx=(6, 0), pady=(3, 0),
                                                                           sticky="ns")
        self.place_var.trace_add("write", lambda *_: (self.update_star(), self.keep_private_link()))
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
        self.min_btn = ttk.Button(btns, text="Minimize all", command=self.minimize_all, state="disabled")
        self.min_btn.pack(side="right", padx=(0, 6))
        tip(self.min_btn, "A minimised Roblox draws nothing, so it uses far less CPU and GPU. "
                          "Anti-AFK can't reach minimised windows. Ctrl+M")
        self.root.bind("<Control-m>", lambda _e: self.minimize_all())

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

    def update_window_buttons(self, count, windows=()):
        """Tile and Close all only do something while Roblox windows are open."""
        state = "normal" if count else "disabled"
        for btn in (self.tile_btn, self.close_btn, self.min_btn):
            if str(btn.cget("state")) != state:
                btn.config(state=state)
        if windows:
            set_text(self.min_btn, "Restore all" if all(_user32.IsIconic(h) for h, _, _ in windows) else "Minimize all")

    # ------------------------------------------------------------ helpers
    def ui(self, fn, *args):
        try:
            self.root.after(0, lambda: fn(*args))
        except (RuntimeError, tk.TclError):   # a background job finished after the window closed
            pass

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
        moves = self.server_of.get(pid, (None, 0))[1]
        if moves:
            name += f"  ·  moved ×{moves}"
        p = self.presence.get(acc["id"])
        if p and p.get("userPresenceType") == 2:
            return "green", name
        return ("lost" if acc["id"] in self.lost_flagged else "yellow"), name

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
        private = bool(parse_private_server(place))
        if private:   # kept encrypted by keep_private_link; settings.json is plain text
            place = self.settings.get("place", "")
        self.settings.update(place_is_private=private, multi=self.multi_var.get(), same_server=self.same_server_var.get(),
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
        rules = mode_rules(self.settings, acc["id"])
        cap = self.fps_cap_of(acc) or rules.get("fps")
        extras = (["main"] if acc["id"] == main_account(self.settings) else []) + ([f"{cap} FPS"] if cap else []) + \
            (["low graphics"] if acc["id"] in self.flagged("low_graphics") or rules.get("low_graphics") else []) + \
            (["muted"] if acc["id"] in self.flagged("muted") or rules.get("muted") else [])
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
        if getattr(self, "web", None):
            self.root.after_idle(self.push_web_state)
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

    def keep_private_link(self):
        """Saves a private server link the moment it's in the Game box, so closing or a crash can't
        lose it. Stays saved when the box moves on to another game, until Forget is used."""
        link = self.place_var.get().strip()
        if not parse_private_server(link) or link == self.private_link:
            return
        try:
            save_private_link(link)
        except OSError as e:
            log.warning("Couldn't save the private server link (%s)", type(e).__name__)
            self.status.config(text="Couldn't save that private server link. It still works until you close AltVault.")
            return
        self.private_link = link
        self.settings["place_is_private"] = True
        save_settings(self.settings)
        self.update_recents_box()

    def forget_private_link(self):
        try:
            os.remove(PRIVATE_FILE)
        except FileNotFoundError:
            pass
        except OSError as e:
            log.warning("Couldn't delete the saved private server link (%s)", type(e).__name__)
            self.status.config(text="Couldn't delete the saved private server link.")
            return
        if self.place_var.get().strip() == self.private_link:
            self.place_var.set("")
        self.private_link = None
        self.settings["place_is_private"] = False
        save_settings(self.settings)
        self.update_recents_box()
        self.status.config(text="Forgot the saved private server link.")

    def update_recents_box(self):
        """The saved private server link (kept off screen in Stream mode), favourites (starred), then
        recent games that aren't favourites."""
        favs = self.favorites()
        fav_ids = {f["id"] for f in favs}
        private = [self.private_link] if self.private_link and not self.stream_var.get() else []
        self.place_box["values"] = private + [f"★ {f['name']}  ({f['id']})" for f in favs] + \
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
        if self.web:   # page updates stop while minimised; catch up now
            self.show_web_page(self.web_page)
        elif self.page == "live":
            self.pages["live"].resume()

    def refresh_presence(self, reschedule=True):
        if reschedule:
            self.root.after(PRESENCE_INTERVAL, self.refresh_presence)
        if self.root.state() in ("iconic", "withdrawn"):   # minimised, or hidden in the tray
            self.presence_stale = True
            self.presence_ticks += 1
            # With nothing open there's nothing to watch. With Roblox windows open, look every few minutes
            # so a dropped or moved account is noticed even while nobody is looking at AltVault.
            if not self.running or self.presence_ticks % HIDDEN_PRESENCE_EVERY:
                return
        accounts = list(self.accounts)

        def work():
            for acc in accounts:
                try:
                    self.note_presence(acc["id"], get_presence(acc["cookie"], acc["id"]))
                    self.expired.discard(acc["id"])
                    self.expired_told.discard(acc["id"])
                except CookieExpired:
                    if acc["id"] not in self.expired_told:
                        self.ui(self.note_expired, acc["id"])
                    self.expired.add(acc["id"])
                except (RobloxError, ValueError, KeyError, IndexError) as e:
                    log.warning("Presence for %s failed: %s", acc["name"], e)
            self.ui(self.refresh)

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ watching the accounts
    def note_presence(self, account_id, presence):
        self.presence[account_id] = presence
        if presence.get("userPresenceType") == 2:
            self.last_ingame[account_id] = time.monotonic()

    def seconds_out_of_game(self, acc, pid, now=None):
        """How long this window's account has been out of a game while the window stayed open (0 if it's
        in one). A window that was just opened counts from when it appeared."""
        now = time.monotonic() if now is None else now
        if (self.presence.get(acc["id"]) or {}).get("userPresenceType") == 2:
            return 0.0
        return now - max(self.first_seen.get(pid, now), self.last_ingame.get(acc["id"], 0.0))

    def is_lost(self, acc, pid):
        """A window whose account has sat outside any game for LOST_AFTER seconds. The game re-joining
        a client takes seconds and presence is polled every 30 s, so a normal re-join never counts; and
        accounts with Anti-AFK turned off (ones you play yourself) are never flagged."""
        return (not self.afk_skipped(acc)) and self.seconds_out_of_game(acc, pid) > LOST_AFTER

    def watch_windows(self, windows):
        """Called every tick: notices accounts that dropped out of their game, or were moved to another
        server, and says so (log, tray alert, Stats counts)."""
        now = time.monotonic()
        alive = {pid for _, pid, _ in windows}
        self.first_seen = {pid: t for pid, t in self.first_seen.items() if pid in alive}
        self.server_of = {pid: v for pid, v in self.server_of.items() if pid in alive}
        dropped, moved, still = [], [], set()
        for hwnd, pid, _ in windows:
            self.first_seen.setdefault(pid, now)
            acc = self.account(self.running.get(pid))
            if not acc:
                continue
            p = self.presence.get(acc["id"]) or {}
            server = p.get("gameId") if p.get("userPresenceType") == 2 else None
            if server:
                home, moves = self.server_of.get(pid, (None, 0))
                if home is None:
                    self.server_of[pid] = (server, 0)
                elif server != home:
                    self.server_of[pid] = (server, moves + 1)
                    self.history.add(moved=1)
                    moved.append(self.who(acc))
                    log.warning("%s was moved to a different server (the %s time since this window opened)",
                                self.who(acc), moves + 1)
            if self.is_lost(acc, pid) and responsive(hwnd):
                still.add(acc["id"])
                if acc["id"] not in self.lost_flagged:
                    self.lost_flagged.add(acc["id"])
                    self.history.add(lost=1)
                    dropped.append(self.who(acc))
                    self.doctor_note("lost", acc["id"])
                    log.warning("%s has had a Roblox window open but hasn't been in a game for %d min",
                                self.who(acc), self.seconds_out_of_game(acc, pid, now) // 60)
        for acc_id in self.lost_flagged - still:
            self.lost_flagged.discard(acc_id)
            acc = self.account(acc_id)
            log.info("%s is back in a game (or its window closed)", self.who(acc) if acc else acc_id)
        if dropped:
            self.alert("Roblox window out of its game",
                       f"{', '.join(dropped[:3])}{' and more' if len(dropped) > 3 else ''}: open for over "
                       f"{LOST_AFTER // 60} min but not in a game. Check the Live page.")
        if moved:
            self.alert("Moved to a different server", f"{', '.join(moved[:3])}{' and more' if len(moved) > 3 else ''}: "
                       "the game moved the account to another server.")

    def note_expired(self, account_id):
        acc = self.account(account_id)
        if not acc or account_id in self.expired_told:
            return
        self.expired_told.add(account_id)
        log.warning("The login for %s has expired, %s after it was saved", self.who(acc), self.login_age(account_id))
        self.doctor_note("login", account_id, lasted=self.login_age(account_id))
        self.alert("A login has expired", f"{self.who(acc)} needs to log in again: right-click it, "
                   "Account, Log in again. Its Roblox window can't stay connected without it.")

    def tick(self):
        """Keeps track of which Roblox window belongs to which account and labels each window."""
        try:
            windows = roblox_windows()
            self.update_window_buttons(len(windows), windows)
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
            self.apply_modes()
            gone = set(before) - alive
            unexpected = gone - self.expected_close
            closed = len(unexpected)
            if closed:
                self.history.add(closed=closed)
                self.doctor_closed += [(before[pid], time.time()) for pid in unexpected if before.get(pid)]
                self.root.after(4000, self.doctor_scan)   # Roblox needs a moment to finish its log
            self.expected_close -= gone
            if self.running != before:
                self.refresh()
            self.watch_windows(windows)
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
        used = memory_used_fraction()
        self.memory_hot = self.memory_hot + 1 if (len(linked) >= 2 and used >= MEMORY_ALERT) else 0
        if used < AFK_PRESSURE:
            self.memory_alerted = False
        if self.memory_hot >= 3 and not self.memory_alerted:
            self.memory_alerted = True
            log.warning("Memory is %.0f%% full with %d Roblox windows open", used * 100, len(linked))
            self.doctor_note("memory", None)
            self.alert("Your PC is out of memory", f"Memory is {used:.0%} full with {len(linked)} Roblox windows. "
                       "Clients slow to a crawl and Anti-AFK can miss, so accounts get re-joined or kicked. "
                       + ("Close some windows or apps." if mode_of(self.settings) == "max"
                          else "Try Max alts mode on Home, or close some windows."))
        self.history.sample(open=len(linked), ingame=ingame if linked else None,
                            pc_cpu=reading["pc_cpu"], pc_ram=reading["pc_ram"],
                            rbx_cpu=reading["rbx_cpu"] if windows else None,
                            rbx_ram=reading["rbx_ram"] if windows else None)
        self.recent.append((time.time(), reading))
        if windows:
            self.doctor_scan()
        if self.page == "pulse":
            self.pages["pulse"].draw_charts()

    def save_history(self):
        try:
            self.history.save()
        except OSError as e:
            log.warning("Couldn't save history: %s", e)

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
    def note_login_saved(self, account_ids):
        """Remembers when each login was saved (a time only, never the login) so that a login Roblox
        later rejects can be logged with how long it lasted. That is what shows why one died."""
        saved = self.settings.setdefault("login_saved", {})
        now = time.time()
        for account_id in account_ids:
            saved[str(account_id)] = now
        save_settings(self.settings)

    def login_age(self, account_id):
        t = (self.settings.get("login_saved") or {}).get(str(account_id))
        if not isinstance(t, (int, float)):
            return "an unknown time"
        minutes = max(time.time() - t, 0) / 60
        return f"{minutes:.0f} min" if minutes < 120 else f"{minutes / 60:.1f} h"

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
            self.note_login_saved([user["id"] for user, _ in items])
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
        self.note_login_saved([user["id"]])
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
        if len(accounts) == 1:
            main = accounts[0]["id"] == main_account(self.settings)
            menu.add_command(label=("✓ " if main else "") + "Main account (keeps full quality in every mode)",
                             command=lambda: self.set_main(None if main else accounts[0]))
            menu.add_separator()
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
        rules = mode_rules(self.settings, acc["id"])
        cap = self.fps_cap_of(acc) or rules.get("fps")
        if cap:
            values["FramerateCap"] = str(cap)
        if acc["id"] in self.flagged("low_graphics") or rules.get("low_graphics"):
            values.update(LOW_GRAPHICS)
        if acc["id"] in self.flagged("muted") or rules.get("muted"):
            values.update(MUTED)
        return values

    def client_values_in_use(self):
        """Every value AltVault writes for some account, by setting name."""
        rules = MODES[mode_of(self.settings)]
        used = {"FramerateCap": {str(c) for c in self.fps_caps().values()} | ({str(rules["fps"])} if "fps" in rules else set())}
        if self.flagged("low_graphics") or rules.get("low_graphics"):
            for name, value in LOW_GRAPHICS.items():
                used.setdefault(name, set()).add(value)
        if self.flagged("muted") or rules.get("muted"):
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
        return bool(self.fps_caps() or self.flagged("low_graphics") or self.flagged("muted") or own
                    or mode_of(self.settings) != "normal")

    # ------------------------------------------------------------ modes and the main account
    def set_mode(self, key):
        self.settings["mode"] = key
        save_settings(self.settings)
        for control in self.mode_controls:
            control.set(key)
        self.apply_modes()
        self.refresh()
        main = self.account(main_account(self.settings))
        self.toast(f"{dict(MODE_NAMES)[key]} mode", MODE_HELP[key] + (
            "" if key == "normal" else f" {self.who(main)} is your main account and isn't changed." if main
            else " Right-click the account you play on, Performance, Main account, to keep it at full quality."))

    def set_main(self, acc):
        self.settings["main_account"] = acc["id"] if acc else None
        save_settings(self.settings)
        self.apply_modes()
        self.refresh()
        self.status.config(text=f"{self.who(acc)} is your main account: it keeps your own Roblox settings in every mode."
                           if acc else "No main account: modes apply to every account.")

    def apply_modes(self):
        """Gives each open window the Windows priority its account's mode asks for. Called every tick
        but only calls Windows when something changed, so it costs nothing while all is settled."""
        self.saving = {pid: state for pid, state in self.saving.items() if pid in self.running}
        self.saving_refused &= set(self.running)
        for pid, account_id in self.running.items():
            rules = mode_rules(self.settings, account_id)
            want = (bool(rules.get("eco")), bool(rules.get("memory_low")))
            if want == self.saving.get(pid, (False, False)) or pid in self.saving_refused:
                continue
            if set_process_saving(pid, *want):
                self.saving[pid] = want
            else:   # not retried every tick; a new window or AltVault restart tries again
                self.saving_refused.add(pid)
                log.warning("Windows refused to change the priority of a Roblox window (process %s)", pid)

    # ------------------------------------------------------------ the new interface (WebView2)
    # The Accounts page is drawn as a local web page in Microsoft's WebView2 (see webview2.py and
    # ui/). It covers the window while Accounts is shown; pages not rebuilt yet are the classic ones.
    def start_web(self):
        try:
            import webview2
            self.web_host = tk.Frame(self.root, bg="#151516")
            self.web_host.place(x=0, y=0, relwidth=1, relheight=1)
            self.root.update_idletasks()
            # WebView2 calls back from inside Tk's event loop, where any Tk call leaves tkinter without
            # its thread state and the next Tk event that runs Python aborts the whole process. So the
            # callbacks only drop work in a queue, and a waiting thread hands it to Tk like any job.
            self.web_inbox = queue.SimpleQueue()
            threading.Thread(target=self.web_relay, daemon=True).start()
            self.web = webview2.WebView(self.web_host.winfo_id(), UI_DIR, os.path.join(APP_DIR, "webview"),
                                        lambda msg: self.web_inbox.put((self.on_web_message, msg)),
                                        lambda: self.web_inbox.put((self.web_ready,)),
                                        extra_hosts={"avatars.altvault.example": AVATAR_DIR, "icons.altvault.example": ICON_DIR})
            self.web_host.bind("<Configure>", lambda e: self.web and self.web.resize(e.width, e.height))
            self.root.bind("<Configure>", lambda e: self.web and e.widget is self.root and self.web.moved(), add="+")
        except (ImportError, OSError, AttributeError) as e:
            log.warning("The new interface couldn't start (%s); using the classic one", e)
            self.web = None
            if getattr(self, "web_host", None):
                self.web_host.destroy()
                self.web_host = None

    def web_relay(self):
        """Waits (using nothing) for messages from the page and runs each on Tk's thread."""
        while True:
            job = self.web_inbox.get()
            for _ in range(50):   # Tk's main loop may not be running yet at the very start
                try:
                    self.root.after(0, *job)
                    break
                except RuntimeError:
                    time.sleep(0.1)
                except tk.TclError:   # the window has closed
                    return

    def web_ready(self):
        self.web_sent.clear()   # a fresh page has nothing yet
        log.info("New interface ready, %.2f s after start", time.monotonic() - STARTED)
        self.push_web_state()
        self.web.post({"type": "show", "page": self.web_page})
        self.show_web_page(self.web_page)

    def web_state(self):
        main = main_account(self.settings)
        accounts = []
        for a in self.accounts:
            p = self.presence.get(a["id"]) or {}
            kind = p.get("userPresenceType")
            running = a["id"] in self.running.values()
            if a["id"] in self.expired:
                status, css = "Login expired", "problem"
            elif kind == 2:
                status, css = "In game" if running else "In game (other device)", "ingame"
            elif running:
                status, css = "Joining...", "joining"
            else:
                status, css = {1: ("Online", ""), 3: ("In Studio", ""), 0: ("Offline", "offline")}.get(kind, ("—", "offline"))
            rules = mode_rules(self.settings, a["id"])
            cap = self.fps_cap_of(a) or rules.get("fps")
            perf = [f"{cap} FPS"] if cap else []
            if a["id"] in self.flagged("low_graphics") or rules.get("low_graphics"):
                perf.append("low graphics")
            if a["id"] in self.flagged("muted") or rules.get("muted"):
                perf.append("muted")
            shot = f"{a['id']}_{self.avatar_px}.png"
            stream = self.stream_var.get()
            accounts.append({"id": a["id"], "name": "hidden" if stream else a["name"],
                             "display": self.who(a) if stream else a.get("displayName") or a["name"],
                             "group": self.group_of(a), "note": a.get("note", ""), "main": a["id"] == main,
                             "status": status, "kind": css,
                             "game": clean_name(p.get("lastLocation")) if kind == 2 else "",
                             "perf": "Full quality" if a["id"] == main else " · ".join(perf),
                             "shot": "" if stream or not os.path.exists(os.path.join(AVATAR_DIR, shot))
                             else f"https://avatars.altvault.example/{shot}"})
        ingame = sum(1 for acc_id in self.running.values() if (self.presence.get(acc_id) or {}).get("userPresenceType") == 2)
        return {"type": "state", "accounts": accounts, "mode": mode_of(self.settings), "open": len(self.running),
                "ingame": ingame, "afk": self.anti_afk_var.get(), "joining": self.joining,
                "place": self.place_var.get(), "note": self.status.cget("text")}

    WEB_REFRESH = {"home": 3000, "live": 2000, "pulse": 2000, "stats": 60_000, "doctor": 30_000}   # ms, while shown

    def web_page_data(self, page):
        """What a page draws, as plain numbers and text. Logins, links and window titles of other
        apps never go to the page."""
        tone = {C["red"]: "bad", C["yellow"]: "warn", C["green"]: "ok"}
        gb = 2 ** 30
        if page == "home":
            home = self.pages["home"]
            saved, open_now = len(self.accounts), len(self.running)
            ingame = sum(1 for a in self.running.values() if (self.presence.get(a) or {}).get("userPresenceType") == 2)
            bad = len(self.expired)
            on = self.anti_afk_var.get()
            free, total = memory_status()
            used = 1 - free / total if total else 0.0
            steps = home.steps()
            return {"sub": f"{saved} account{'s' if saved != 1 else ''} saved · {open_now} open · {ingame} in a game",
                    "cards": [{"title": "Accounts", "value": str(saved), "sub": f"{bad} need logging in" if bad else "all logged in", "subTone": "bad" if bad else ""},
                              {"title": "In a game", "value": f"{ingame} / {open_now}", "sub": "windows in a game", "tone": "ok" if open_now and ingame == open_now else ""},
                              {"title": "Anti-AFK", "value": "On" if on else "Off", "sub": f"keeping {len(self.afk_last)} window(s) active" if on else "accounts can be idle-kicked",
                               "tone": "ok" if on else "warn" if open_now else ""},
                              {"title": "Memory", "value": f"{used:.0%}", "sub": f"{free / gb:.1f} of {total / gb:.0f} GB free",
                               "tone": "bad" if used >= MEMORY_ALERT else "warn" if used >= AFK_PRESSURE else ""}],
                    "steps": [{"done": d, "text": t} for d, t, _ in steps] if steps else None,
                    "attention": [{"tone": tone.get(c, "ok"), "text": t, "target": p or ""} for c, t, p in home.attention()],
                    "activity": [{"time": time.strftime("%H:%M", time.localtime(created)), "warn": level >= logging.WARNING,
                                  "text": message.removeprefix("Anti-AFK: ").strip() if level < logging.WARNING else message}
                                 for created, level, message in list(ACTIVITY.items)[:30]]}
        if page == "profiles":
            out = []
            for i, p in enumerate(self.pages["profiles"].profiles()):
                names = [self.who(a) for a in map(self.account, p["accounts"]) if a]
                out.append({"index": i, "name": p["name"], "accounts": f"{len(names)} · " + ", ".join(names) if names else "none left",
                            "game": clean_name(p.get("place_name")) or f"Place {p.get('place')}"})
            return {"profiles": out}
        if page == "games":
            games = self.pages["games"].games()
            missing = [g[0] for g in games if not os.path.exists(os.path.join(ICON_DIR, f"{g[0]}.png"))]
            if missing:
                self.pages["games"].load_icons(missing)   # fetched in the background, shown next time
            return {"games": [{"id": place, "name": name, "starred": starred,
                               "icon": f"https://icons.altvault.example/{place}.png" if place not in missing else ""}
                              for place, name, starred in games]}
        if page == "live":
            windows = roblox_windows()
            reading = measure(windows, self.web_pc, self.web_rbx)
            order = {a["id"]: i for i, a in enumerate(self.accounts)}
            tiles, ingame = [], 0
            for hwnd, pid, title in sorted(windows, key=lambda w: order.get(self.running.get(w[1]), len(order))):
                color, name = self.live_state(hwnd, pid, title)
                ingame += color == "green"
                usage = process_usage(pid)
                frozen, small = color == "red", _user32.IsIconic(hwnd)
                tiles.append({"hwnd": int(hwnd), "name": name, "usage": f"{usage[1] / 2 ** 20:,.0f} MB" if usage and usage[1] else "",
                              "tone": {"green": "ok", "yellow": "warn", "red": "bad", "lost": "bad"}.get(color, "off"),
                              "note": "Minimised: click to restore" if small else "Not responding" if frozen else ""})
            n, cpu, ram = len(tiles), reading["rbx_cpu"], reading["rbx_bytes"]
            cpu_text = "…" if cpu is None else f"{cpu:.0f}%"
            ram_text = "—" if ram is None else f"{ram / gb:.1f} GB"
            return {"windows": tiles, "cols": 1 if n <= 1 else 2 if n <= 4 else 3 if n <= 9 else 4,
                    "allMin": bool(windows) and all(_user32.IsIconic(h) for h, _, _ in windows),
                    "sub": f"{ingame} of {n} in a game · Roblox {cpu_text} CPU, {ram_text} · {reading['free'] / gb:.1f} GB free"
                    if n else "Your Roblox windows, live."}
        if page in ("stats", "pulse"):
            h, key = self.history, self.web_ranges[page]
            if page == "stats":
                hours = RANGE_HOURS[key]
                span = "24 hours" if hours == 24 else "7 days"
                total = lambda f: h.total(f, hours)
                ingame = percent_of(total("ingame"), total("open"))
                ok, failed = total("launch_ok"), total("launch_fail")
                rate = percent_of(ok, ok + failed)
                closed, lost, moved = total("closed"), total("lost"), total("moved")
                return {"range": key, "ends": [f"{span} ago", "now"], "labels": hour_labels(hours),
                        "cards": [{"title": "Time in a game", "value": "—" if ingame is None else f"{ingame:.1f}%", "sub": f"of the time Roblox was open, last {span}",
                                   "tone": "" if ingame is None else "ok" if ingame >= 95 else "warn" if ingame >= 80 else "bad"},
                                  {"title": "Launches that worked", "value": "—" if rate is None else f"{rate:.1f}%", "sub": f"{ok + failed:g} launches, {failed:g} failed"},
                                  {"title": "Anti-AFK pings", "value": short_number(total("pings")), "sub": f"{total('missed'):g} missed and retried"},
                                  {"title": "Closed outside AltVault", "value": f"{closed:g}", "tone": "bad" if closed else "",
                                   "sub": "crashes, kicks or closed by hand" + (f"\n{lost:g} dropped out · {moved:g} moved server" if lost or moved else "")}],
                        "open": h.series("open", hours, per="open_n"), "ingame": h.series("ingame", hours, per="ingame_n"),
                        "pings": h.series("pings", hours), "missed": h.series("missed", hours)}
            sample = measure(roblox_windows(), self.web_pc, self.web_rbx)
            fmt = lambda v: "…" if v is None else f"{v:.0f}%"
            n, ram = sample["windows"], sample["rbx_bytes"]
            free, total = sample["free"], sample["total"]
            data = {"range": key, "cards": [
                {"title": "PC CPU", "value": fmt(sample["pc_cpu"]), "sub": f"{os.cpu_count() or 1} logical processors"},
                {"title": "PC memory", "value": fmt(sample["pc_ram"]), "sub": f"{(total - free) / gb:.1f} of {total / gb:.0f} GB in use",
                 "tone": "bad" if free < 0.1 * total else "warn" if free < 0.25 * total else ""},
                {"title": "Roblox CPU", "value": fmt(sample["rbx_cpu"]) if n else "—", "sub": f"{n} window{'s' if n != 1 else ''}"},
                {"title": "Roblox memory", "value": "—" if ram is None or not n else f"{ram / gb:.1f} GB", "sub": f"{ram / n / 2 ** 20:,.0f} MB per window" if ram and n else ""}]}
            if key == "hour":
                now = time.time()
                slots = {round((now - t) / 60): s for t, s in self.recent}
                points = [slots.get(m) for m in range(59, -1, -1)]
                pick = lambda f: [p[f] if p else None for p in points]
                data.update(ends=["60 min ago", "now"], **{f: pick(f) for f in ("pc_cpu", "rbx_cpu", "pc_ram", "rbx_ram")})
            else:
                hours = RANGE_HOURS[key]
                data.update(ends=["24 hours ago" if key == "day" else "7 days ago", "now"],
                            **{f: h.series(f, hours, per=f + "_n") for f in ("pc_cpu", "rbx_cpu", "pc_ram", "rbx_ram")})
            return data
        if page == "doctor":
            doctor, items = self.doctor, self.doctor.items
            dropped = doctor.count(("exit", "login", "lost", "crash"))
            idle = sum(1 for i in items if i.get("code") == 278 and i["t"] >= time.time() - 86400)
            logins, moved = doctor.count(("login",)), doctor.count(("moved",))
            page_obj, out = self.pages["doctor"], []
            for n, item in enumerate(items):
                title, why, fix, action, color = explain(item)
                if action == "login" and not self.account(item.get("id")):
                    action = None
                out.append({"key": f"{item['t']}-{item['kind']}-{item.get('id')}-{n}", "when": time.strftime("%d %b %H:%M", time.localtime(item["t"])),
                            "who": page_obj.who(item), "title": title, "why": why, "fix": fix,
                            "action": page_obj.ACTIONS.get(action) if action else "", "tone": {"red": "bad", "yellow": "warn", "green": "ok"}.get(color, "")})
            return {"chosen": self.web_doctor_pick, "items": out, "cards": [
                {"title": "Dropped, 24 hours", "value": str(dropped), "sub": "all clear" if not dropped else "see the list below", "tone": "bad" if dropped else "ok"},
                {"title": "Idle kicks", "value": str(idle), "sub": "last 24 hours", "tone": "bad" if idle else ""},
                {"title": "Login problems", "value": str(logins), "sub": "last 24 hours", "tone": "bad" if logins else ""},
                {"title": "Server moves", "value": str(moved), "sub": "last 24 hours", "tone": "warn" if moved else ""}]}
        if page == "settings":
            s = self.settings
            main = main_account(s)
            return {"tab": self.web_settings_tab, "version": APP_VERSION, "panicKey": PANIC_HOTKEY,
                    "webhook": "A webhook is saved. Alerts go to Discord." if self.webhook else "No webhook saved.",
                    "delays": [[d, "No wait" if not d else f"{d} seconds"] for d in LAUNCH_DELAYS],
                    "mains": [["", "None"]] + [[str(a["id"]), self.who(a)] for a in self.accounts],
                    "keys": [[k, k.upper() if len(k) > 1 else k] for k in ["f13", "space"] + list("abcdefghijklmnopqrstuvwxyz")],
                    "values": {"multi": self.multi_var.get(), "same_server": self.same_server_var.get(), "auto_tile": self.auto_tile_var.get(),
                               "anti_afk": self.anti_afk_var.get(), "stream_mode": self.stream_var.get(), "hide_taskbar": self.hide_taskbar_var.get(),
                               "launch_delay": s.get("launch_delay", 0), "mode": mode_of(s), "main_account": "" if main is None else str(main),
                               "afk_key": str(s.get("afk_key", AFK_DEFAULT_KEY)).lower(),
                               **{k: s.get(k, default) is not False for k, default in (("launch_memory_wait", True), ("lighter_rendering", True),
                                                                                       ("clean_logs", True), ("update_check", True), ("panic_hotkey", False))}},
                    "text": WEB_SETTING_TEXT}
        return None

    def push_page(self, page=None):
        page = page or self.web_page
        if self.web and page != "accounts":
            try:
                data = self.web_page_data(page)
                message = {"type": "page", "page": page, "data": data}
                if data is not None and message != self.web_sent.get(page):   # unchanged: the page needn't redraw
                    self.web_sent[page] = message
                    self.web.post(message)
            except OSError as e:
                log.warning("Couldn't update the %s page: %s", page, e)

    def web_tick(self):
        """Keeps the open page current, at that page's pace, and only while AltVault is on screen."""
        self.web_job = None
        if not self.web or self.root.state() in ("iconic", "withdrawn"):
            return
        self.push_page()
        every = self.WEB_REFRESH.get(self.web_page)
        if every:
            self.web_job = self.root.after(every, self.web_tick)

    def show_web_page(self, page):
        if page != "live":
            self.release_web_thumbs()
        self.web_page = page
        if self.web_job:
            self.root.after_cancel(self.web_job)
        self.web_tick()

    def place_web_thumbs(self, tiles):
        """Puts each Roblox window's live preview (drawn by Windows, like a taskbar preview) over its
        tile on the Live page. Minimised and frozen windows have no fresh picture, so they get none."""
        if self.web_page != "live":
            return
        alive = {hwnd for hwnd, _, _ in roblox_windows()}
        dest = _user32.GetParent(self.root.winfo_id())
        ox, oy = client_origin(dest)
        hx, hy = self.web_host.winfo_rootx() - ox, self.web_host.winfo_rooty() - oy
        wanted = {}
        for t in tiles:
            hwnd = t.get("hwnd")
            if hwnd in alive and not _user32.IsIconic(hwnd) and all(isinstance(t.get(k), int) for k in "xywh"):
                wanted[hwnd] = fit_rect(*client_size(hwnd), hx + t["x"], hy + t["y"], t["w"], t["h"])
        for hwnd in set(self.web_thumbs) - set(wanted):
            unregister_thumbnail(self.web_thumbs.pop(hwnd))
        for hwnd, rect in wanted.items():
            if hwnd not in self.web_thumbs:
                thumb = register_thumbnail(dest, hwnd)
                if not thumb:
                    continue
                self.web_thumbs[hwnd] = thumb
            place_thumbnail(self.web_thumbs[hwnd], rect)

    def release_web_thumbs(self):
        for thumb in self.web_thumbs.values():
            unregister_thumbnail(thumb)
        self.web_thumbs.clear()

    def web_set(self, key, value):
        """A setting changed on the Settings page."""
        s = self.settings
        tk_vars = {"multi": (self.multi_var, self.toggle_multi), "same_server": (self.same_server_var, self.save_settings),
                   "auto_tile": (self.auto_tile_var, self.save_settings), "anti_afk": (self.anti_afk_var, self.toggle_anti_afk),
                   "stream_mode": (self.stream_var, self.toggle_stream), "hide_taskbar": (self.hide_taskbar_var, self.toggle_taskbar)}
        if key in tk_vars and isinstance(value, bool):
            var, apply = tk_vars[key]
            var.set(value)
            apply()
        elif key in ("launch_memory_wait", "lighter_rendering", "clean_logs", "update_check", "panic_hotkey") and isinstance(value, bool):
            s[key] = value
            save_settings(s)
            if key == "panic_hotkey":
                self.set_panic_hotkey(value)
        elif key == "launch_delay" and str(value).isdigit() and int(value) in LAUNCH_DELAYS:
            s[key] = int(value)
            save_settings(s)
        elif key == "mode" and value in MODES:
            self.set_mode(value)
        elif key == "main_account":
            self.set_main(self.account(int(value)) if str(value).isdigit() else None)
        elif key == "afk_key" and (value in ("f13", "space") or (len(str(value)) == 1 and str(value).isalpha())):
            s[key] = str(value).lower()
            save_settings(s)

    def web_do(self, name, msg):
        """A button on one of the new pages."""
        def place():
            p = str(msg.get("place", ""))
            return p if p.isdigit() else None
        def hwnd():
            h = int(msg["hwnd"]) if str(msg.get("hwnd", "")).isdigit() else None
            return h if h in {w for w, _, _ in roblox_windows()} else None   # only ever a Roblox window
        profiles = self.pages["profiles"]
        index = int(msg["index"]) if str(msg.get("index", "")).isdigit() else None
        if name == "add":
            AddAccountDialog(self)
        elif name == "goto" and msg.get("target") in self.pages:
            self.show_page(msg["target"])
        elif name == "open":
            if msg.get("target") == "update" and self.update_available:
                webbrowser.open(self.update_available[1])
            elif msg.get("target") in self.pages:
                self.show_page(msg["target"])
        elif name == "tile":
            self.tile()
        elif name == "minimize":
            self.minimize_all()
        elif name == "close-all":
            self.close_all()
        elif name == "show-window" and hwnd():
            focus_window(hwnd())
        elif name == "close-window" and hwnd():
            h = hwnd()
            pid = next(p for w, p, _ in roblox_windows() if w == h)
            if ask_yes_no("Close Roblox", "Close this Roblox window?", parent=self.root, yes="Close", danger=True):
                self.close_windows([pid])
        elif name == "seg" and msg.get("seg") == "tab" and msg.get("value") in ("general", "modes", "afk", "alerts", "privacy", "data"):
            self.web_settings_tab = msg["value"]
        elif name == "seg" and msg.get("seg") == "range" and msg.get("page") in self.web_ranges:
            if msg.get("value") in (("hour",) if msg["page"] == "pulse" else ()) + tuple(RANGE_HOURS):
                self.web_ranges[msg["page"]] = msg["value"]
        elif name == "set":
            self.web_set(msg.get("key"), msg.get("value"))
        elif name == "new-profile":
            profiles.edit(None)
        elif name in ("launch-profile", "edit-profile", "delete-profile") and index is not None and index < len(profiles.profiles()):
            if name == "launch-profile":
                self.launch_profile(profiles.profiles()[index])
            elif name == "edit-profile":
                profiles.edit(str(index))
            else:
                profiles.tree.selection_set(str(index))
                profiles.delete()
        elif name == "pick-game" and place():
            self.place_var.set(f"{self.pages['games'].name_of(place())}  ({place()})")
            self.job_var.set("")
            self.show_page("accounts")
        elif name == "servers" and place():
            ServerBrowser(self, place())
        elif name == "open-game" and place():
            webbrowser.open(f"https://www.roblox.com/games/{int(place())}")
        elif name in ("star", "remove-game") and place():
            games = self.pages["games"]
            games.tree.selection_set(place()) if games.tree.exists(place()) else None
            (games.toggle_star if name == "star" else games.remove)()
        elif name == "add-game":
            self.pages["games"].add_var.set(str(msg.get("text", ""))[:300])
            self.pages["games"].add_game()
        elif name == "doctor-pick":
            self.web_doctor_pick = str(msg.get("key", ""))[:100]
        elif name == "doctor-act":
            data = self.web_page_data("doctor")
            n = next((i for i, item in enumerate(data["items"]) if item["key"] == msg.get("key")), None)
            if n is not None:
                doctor = self.pages["doctor"]
                doctor.current = self.doctor.items[n]
                doctor.action_key = explain(doctor.current)[3]
                doctor.act()
        elif name == "doctor-clear":
            self.pages["doctor"].clear()
        elif name == "webhook-save":
            error = self.set_webhook(str(msg.get("url", "")))
            self.toast("Discord alerts", error or "Webhook saved. Send a test to check it.", warning=bool(error))
        elif name == "webhook-test":
            self.test_webhook(lambda ok: self.toast("Discord alerts", "Sent. Check your Discord channel." if ok
                                                    else "Discord didn't accept it. Check the link, or make a new webhook.", warning=not ok))
        elif name == "webhook-remove":
            self.set_webhook(None)
        else:
            {"forget-link": self.forget_private_link, "export-accounts": self.export_accounts, "import-accounts": self.import_accounts,
             "backup": self.export_settings, "restore": self.import_settings, "github": lambda: webbrowser.open(GITHUB_URL),
             "log-folder": lambda: os.startfile(APP_DIR), "check-updates": lambda: self.check_updates(tell=True),
             "diagnostics": self.copy_diagnostics}.get(name, lambda: None)()

    def push_web_state(self, **extra):
        if self.web:
            try:
                self.web.post({**self.web_state(), **extra})
            except OSError as e:
                log.warning("Couldn't update the new interface: %s", e)

    def on_web_message(self, msg):
        kind = msg.get("type")
        if kind == "page" and msg.get("page") in self.pages:
            self.show_web_page(msg["page"])
            return
        if kind == "tiles" and isinstance(msg.get("tiles"), list):
            self.place_web_thumbs(msg["tiles"][:64])
            return
        if kind == "do" and isinstance(msg.get("name"), str):
            self.web_do(msg["name"], msg)
            self.push_page()
        elif kind == "mode" and msg.get("value") in MODES:
            self.set_mode(msg["value"])
        elif kind == "action":
            if msg.get("name") in ("add", "import"):   # the add dialog also imports from Roblox Account Manager
                AddAccountDialog(self)
        elif kind in ("join", "menu"):
            ids = [str(i) for i in msg.get("ids", []) if isinstance(i, int) and self.tree.exists(str(i))]
            self.tree.selection_set(ids)
            sel = self.selected_accounts()
            if kind == "join":
                self.place_var.set(str(msg.get("place", ""))[:500])
                self.job_var.set(str(msg.get("job", ""))[:100])
                self.join_selected()
            elif sel and msg.get("item") == "join":
                self.join_selected()
            elif sel and msg.get("item") == "main" and len(sel) == 1:
                self.set_main(None if sel[0]["id"] == main_account(self.settings) else sel[0])
            elif sel and msg.get("item") in ("low", "mute"):
                key = "low_graphics" if msg["item"] == "low" else "muted"
                on = not all(a["id"] in self.flagged(key) for a in sel)
                self.set_flag(key, sel, on, "lowest graphics" if key == "low_graphics" else "mute")
            elif sel and msg.get("item") == "login" and len(sel) == 1:
                AddAccountDialog(self, sel[0])
            elif sel and msg.get("item") == "remove":
                self.remove_selected()
        self.push_web_state()

    # ------------------------------------------------------------ windows, hotkey and upkeep
    def minimize_all(self):
        """Minimises every Roblox window, or restores them all if they're all minimised already."""
        hwnds = [hwnd for hwnd, _, _ in roblox_windows()]
        if not hwnds:
            self.status.config(text="No Roblox windows open.")
            return
        restore = all(_user32.IsIconic(h) for h in hwnds)
        minimize_windows(hwnds, not restore)
        if restore:
            self.status.config(text=f"Restored {len(hwnds)} window(s).")
        elif self.anti_afk_var.get():
            self.toast("Windows minimised", "Anti-AFK can't reach minimised windows, so restore them within "
                       "about 15 minutes or Roblox may kick them for being idle.", warning=True)
        else:
            self.status.config(text=f"Minimised {len(hwnds)} window(s). They use far less CPU and GPU now.")
        self.root.after(300, lambda w=None: self.update_window_buttons(len(w := roblox_windows()), w))

    def set_panic_hotkey(self, on, quiet=False):
        if self.hotkey:
            self.hotkey.stop()
            self.hotkey = None
        if on:
            self.hotkey = Hotkey(MOD_CONTROL | MOD_ALT, VK_END, lambda: self.ui(self.panic))
            if not self.hotkey.registered:
                self.hotkey = None
                log.warning("Another program already uses %s, so the panic hotkey is off", PANIC_HOTKEY)
                if not quiet:
                    self.toast("Panic hotkey not available", f"Another program already uses {PANIC_HOTKEY}.", warning=True)
                return False
        return True

    def panic(self):
        """The panic hotkey: stop any join and close every Roblox window at once, no questions."""
        self.join_cancel.set()
        pids = {pid for _, pid, _ in roblox_windows()}
        if not pids:
            return
        self.expected_close |= pids
        close_all_roblox()
        log.warning("Panic hotkey: closed %d Roblox window(s)", len(pids))
        self.toast("Closed every Roblox window", f"{PANIC_HOTKEY} was pressed.")
        if self.tray and self.root.state() != "normal":
            self.tray.notify("Closed every Roblox window", f"{PANIC_HOTKEY} was pressed.")

    def daily_chores(self):
        """A minute after start, then once a day: delete old Roblox logs and look for a newer
        AltVault. Each is one short job on its own thread; nothing runs in between."""
        if self.settings.get("clean_logs", True):
            threading.Thread(target=self.clean_logs, daemon=True).start()
        if self.settings.get("update_check", True) and time.time() - self.settings.get("update_checked", 0) > 20 * 3600:
            self.check_updates()
        self.root.after(24 * 3600 * 1000, self.daily_chores)

    def clean_logs(self):
        files, size = clean_roblox_logs()
        if files:
            log.info("Deleted %d old Roblox log file(s), %.0f MB", files, size / 2 ** 20)
        return files, size

    def check_updates(self, tell=False):
        self.settings["update_checked"] = time.time()
        save_settings(self.settings)

        def work():
            try:
                found = latest_release()
            except (RobloxError, ValueError, AttributeError):
                found = None
            newer = found if found and found[0] > version_tuple(APP_VERSION) else None
            self.ui(self.show_update, newer, tell)
        threading.Thread(target=work, daemon=True).start()

    def show_update(self, found, tell):
        if found:
            version, page = ".".join(map(str, found[0])), found[1]
            self.update_available = (version, page)
            log.info("AltVault %s is out", version)
            self.toast(f"AltVault {version} is out", "Get it from the Releases page on GitHub: Settings, Data.")
        elif tell:
            self.toast("AltVault is up to date", f"You have the newest version, {APP_VERSION}.")

    def diagnostics(self):
        """A short report for bug reports: versions, counts and recent warnings. No logins, links,
        usernames or folder names."""
        w = sys.getwindowsversion()
        free, total = memory_status()
        lines = [f"AltVault {APP_VERSION}, Python {sys.version.split()[0]}, Windows {w.major}.{w.minor} build {w.build}",
                 f"Memory: {free / 2 ** 30:.1f} of {total / 2 ** 30:.0f} GB free",
                 f"Accounts: {len(self.accounts)} ({len(self.expired)} need logging in), Roblox windows: {len(roblox_windows())}",
                 f"Mode: {mode_of(self.settings)}, main account set: {main_account(self.settings) is not None}",
                 "Options: " + ", ".join(f"{k}={self.settings[k]}" for k in (
                     "multi", "same_server", "auto_tile", "anti_afk", "afk_key", "launch_delay", "launch_memory_wait",
                     "lighter_rendering", "clean_logs", "panic_hotkey", "stream_mode", "hide_taskbar") if k in self.settings),
                 f"Roblox player: {'found' if roblox_player_folder() else 'bootstrapper or not found'}, "
                 f"settings file: {'found' if roblox_settings_file() else 'not found'}",
                 "Recent warnings:"]
        names = sorted(((a["name"], f"account {i}") for i, a in enumerate(self.accounts, 1)), key=lambda p: -len(p[0]))
        home = os.path.expanduser("~")
        for created, level, message in reversed(list(ACTIVITY.items)):
            if level < logging.WARNING:
                continue
            for name, alias in names:
                message = re.sub(rf"(?<![\w]){re.escape(name)}(?![\w])", alias, message, flags=re.I)
            lines.append(time.strftime("  %d %b %H:%M  ", time.localtime(created)) + message.replace(home, "~"))
        if lines[-1] == "Recent warnings:":
            lines.append("  none")
        return "\n".join(lines)

    def copy_diagnostics(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.diagnostics())
        self.toast("Diagnostics copied", "Paste them into a bug report. They hold no logins, links or usernames.")

    def set_webhook(self, url):
        """Saves or (url None) removes the Discord webhook. Returns an error message, or None."""
        url = url.strip() if url else None
        if url and not WEBHOOK_RE.fullmatch(url):
            return "That isn't a Discord webhook link. It starts with https://discord.com/api/webhooks/"
        try:
            save_webhook(url)
        except OSError as e:
            return f"Couldn't save it: {e.strerror or e}"
        self.webhook = url
        return None

    def test_webhook(self, done):
        if not self.webhook:
            return done(False)
        threading.Thread(target=lambda: self.ui(done, self.post_webhook(
            self.webhook, "AltVault test", "Alerts from AltVault will show up here.")), daemon=True).start()

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
        if on:   # the feed may name accounts from before Stream mode was on
            ACTIVITY.items.clear()
        self.save_settings()
        self.update_recents_box()
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
        if (oldest < AFK_BUSY_INTERVAL[0] and idle_seconds() < AFK_AWAY
                and not any(h in self.afk_misses for h in targets)):
            self.afk_wait_started = None   # in use, and no window needs it yet: leave the PC alone
            self.schedule_afk(30)
            return
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
        vk = AFK_KEYS.get(str(self.settings.get("afk_key", AFK_DEFAULT_KEY)).lower(), AFK_KEYS[AFK_DEFAULT_KEY])
        self.afk_busy, self.afk_busy_since = True, now
        self.afk_pass_id += 1
        pass_id = self.afk_pass_id

        used = memory_used_fraction()
        strong = used >= AFK_PRESSURE
        if strong:
            log.info("Anti-AFK: memory is %.0f%% full, so Roblox windows are slow; giving them longer before the key press", used * 100)

        def work():
            try:
                result = anti_afk_pass(hwnds, vk, strong)
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
        if self.joining:   # the Join button reads "stop" while a join runs
            if not self.join_cancel.is_set():
                self.join_cancel.set()
                self.status.config(text="Stopping after the account that's starting now...")
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
        self.join_cancel.clear()
        self.join_btn.config(text=f"{SPINNER[0]}  Joining... (stop)")
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
        failed = []   # accounts worth trying again (not ones that need logging in)
        self.join_failed = []
        link_code = ""
        fps_file = None
        if self.needs_client_settings():
            fps_file = roblox_settings_file()
            if fps_file is None:
                problems.append("Couldn't find Roblox's settings file, so FPS caps, graphics and mute weren't applied. "
                                "Start Roblox once normally, then try again.")
        flags_folder = None
        if any(wants_low_flags(self.settings, a["id"]) for a in accounts):
            flags_folder = roblox_player_folder()
            if flags_folder is None:
                log.info("Roblox starts through a bootstrapper, so lighter rendering flags were left to it")
        try:
            if private:
                try:
                    place_id, link_code = self.resolve_private(accounts, private)
                except RobloxError as e:
                    problems.append(str(e))
                    return
            if same_server and not job_id and not link_code:
                self.set_status("Looking for a server with room for everyone...")
                try:
                    job_id = server_with_room(place_id, len(accounts)) or ""
                except RobloxError as e:
                    log.info("Couldn't look for a roomy server (%s); the others will follow the first", e)
                if job_id:
                    same_server = False   # everyone goes straight there, nobody waits for the first
            for i, acc in enumerate(accounts):
                last = i == len(accounts) - 1
                if self.join_cancel.is_set() or (i and not self.pause_before_launch()):
                    problems.append(f"Stopped. {len(accounts) - i} account(s) weren't launched.")
                    failed.extend(accounts[i:])
                    break
                self.set_status(f"Launching {self.who(acc)} ({i + 1}/{len(accounts)})...")
                before = {pid for _, pid, _ in roblox_windows()}
                lighter = False
                if flags_folder:
                    restore_flags_safely()   # an earlier launch that failed may have left an alt's flags
                if flags_folder and wants_low_flags(self.settings, acc["id"]):
                    try:
                        write_flags(flags_folder, LOW_FLAGS)
                        lighter = True
                    except (OSError, ValueError) as e:
                        log.warning("Couldn't add lighter rendering flags: %s", type(e).__name__)
                        flags_folder = None
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
                    log.warning("Roblox rejected the login for %s at launch, %s after it was saved",
                                self.who(acc), self.login_age(acc["id"]))
                    self.expired.add(acc["id"])
                    self.ui(lambda a=acc["id"], age=self.login_age(acc["id"]): self.doctor_note("login", a, lasted=age))
                    problems.append(f"{self.who(acc)}: login expired — right-click > Log in again.")
                    self.ui(lambda: self.history.add(launch_fail=1))
                    continue
                except (RobloxError, OSError) as e:
                    log.warning("Launch of %s failed: %s", acc["name"], e)
                    problems.append(f"{self.who(acc)}: {e}")
                    failed.append(acc)
                    self.ui(lambda: self.history.add(launch_fail=1))
                    continue
                pid = self.wait_for_new_window(before)
                if pid is None:
                    problems.append(f"{self.who(acc)}: Roblox didn't open a window within {WINDOW_TIMEOUT}s.")
                    failed.append(acc)
                    self.ui(lambda: self.history.add(launch_fail=1))
                    continue
                self.ui(lambda: self.history.add(launch_ok=1))
                self.ui(self.running.__setitem__, pid, acc["id"])
                time.sleep(3)
                if lighter:   # the client has read its flags by now
                    restore_flags_safely()
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
            if flags_folder:
                restore_flags_safely()
            self.join_failed = [a["id"] for a in failed]
            self.ui(self.join_finished, problems)

    def pause_before_launch(self):
        """Before each launch after the first: the gap the user chose, then, if memory is nearly
        full, a wait for it to free up (a client started into a full PC stalls the others too).
        False if the user stopped the join meanwhile."""
        delay = self.settings.get("launch_delay", 0)
        if delay in LAUNCH_DELAYS and delay and self.join_cancel.wait(delay):
            return False
        if self.settings.get("launch_memory_wait", True):
            deadline = time.monotonic() + LAUNCH_MEMORY_WAIT
            while (used := memory_used_fraction()) >= LAUNCH_MEMORY and time.monotonic() < deadline:
                self.set_status(f"Waiting for memory to free up ({used:.0%} used)...")
                if self.join_cancel.wait(2):
                    return False
        return not self.join_cancel.is_set()

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
            self.note_presence(acc["id"], p)
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
            set_text(self.join_btn, f"{SPINNER[i % len(SPINNER)]}  Joining... (stop)")
            self.root.after(90, self.spin, i + 1)

    def join_finished(self, problems):
        self.joining = False
        self.join_btn.config(state="normal")
        self.status.config(text="Done." if not problems else "Done, with problems.")
        self.refresh()
        retry = [str(i) for i in self.join_failed if self.tree.exists(str(i))]
        if retry:   # ready to try again with one click on Join
            self.tree.selection_set(retry)
            problems = problems + [f"The {len(retry)} account(s) that didn't start are selected now: click Join to try them again."]
        self.update_join_label()
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
        if self.web:   # in the tray nobody sees the page: stop drawing it and hand memory back
            self.release_web_thumbs()
            self.web.set_hidden(True)
        if not self.settings.get("tray_hint_shown"):
            self.tray.notify("AltVault is still running",
                             "Anti-AFK and your accounts keep working. Click the tray icon to open AltVault, "
                             "or right-click it to quit.")
            self.settings["tray_hint_shown"] = True
            save_settings(self.settings)

    def show_from_tray(self):
        self.root.deiconify()
        if self.web:
            self.web.set_hidden(False)
            self.push_web_state()
            self.show_web_page(self.web_page)
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
        if self.hotkey:
            self.hotkey.stop()
        if self.web:
            self.web.close()
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
