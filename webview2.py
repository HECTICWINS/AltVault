"""Microsoft Edge WebView2, hosted from Python with ctypes: no packages, just WebView2Loader.dll
(Microsoft's signed loader, from the Microsoft.Web.WebView2 SDK) beside this file and the WebView2
runtime that Windows 11 already has. AltVault draws its interface as a local web page in it.

Only the few interface methods AltVault needs are declared, by their place in each interface's
method table (taken from the SDK's WebView2.h, version 1.0.4258.31)."""
import ctypes
import json
import os
from ctypes import wintypes as wt

HRESULT = ctypes.c_long
S_OK = 0
COINIT_APARTMENTTHREADED = 0x2


class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, text):
        h = text.replace("-", "")
        return cls(int(h[:8], 16), int(h[8:12], 16), int(h[12:16], 16),
                   (ctypes.c_ubyte * 8)(*bytes.fromhex(h[16:])))


IID_ICoreWebView2_3 = GUID.parse("A0D6DF20-3B92-416D-AA0C-437A9C727857")
IID_ICoreWebView2Controller2 = GUID.parse("c979903e-d4ca-4228-92eb-47ee3fa96eab")
IID_ICoreWebView2_19 = GUID.parse("6921f954-79b0-437f-a997-c85811897c68")


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class COLOR(ctypes.Structure):   # COREWEBVIEW2_COLOR
    _fields_ = [("A", ctypes.c_ubyte), ("R", ctypes.c_ubyte), ("G", ctypes.c_ubyte), ("B", ctypes.c_ubyte)]


# method numbers in each interface's table (IUnknown takes 0-2)
ENV_CREATE_CONTROLLER = 3
CTL_PUT_VISIBLE, CTL_PUT_BOUNDS, CTL_MOVE_FOCUS, CTL_NOTIFY_MOVED, CTL_CLOSE, CTL_GET_WEBVIEW = 4, 6, 12, 23, 24, 25
CTL2_PUT_BACKGROUND = 27
WV_GET_SETTINGS, WV_NAVIGATE, WV_ADD_SCRIPT, WV_POST_JSON, WV_ADD_MESSAGE = 3, 5, 27, 32, 34
WV3_TRY_SUSPEND, WV3_RESUME, WV3_MAP_FOLDER = 68, 69, 71
WV19_PUT_MEMORY_TARGET = 120
SET_SCRIPT_DIALOGS, SET_STATUS_BAR, SET_DEVTOOLS, SET_CONTEXT_MENUS, SET_ZOOM, SET_ERROR_PAGE = 8, 10, 12, 14, 18, 20
ARGS_GET_JSON = 4
MEMORY_LOW, MEMORY_NORMAL = 1, 0
ACCESS_DENY_CORS = 2   # COREWEBVIEW2_HOST_RESOURCE_ACCESS_KIND_DENY_CORS

_ole32 = ctypes.WinDLL("ole32")
_ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]

QI_T = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
REF_T = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
DONE_T = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, HRESULT, ctypes.c_void_p)       # (this, error, result)
EVENT_T = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)  # (this, sender, args)


def call(obj, index, *args, types=()):
    """Calls method `index` of the COM interface `obj`. Raises OSError on a failed HRESULT."""
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    fn = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, *types)(vtbl[index])
    hr = fn(obj, *args)
    if hr < 0:
        raise OSError(f"WebView2 call {index} failed (0x{hr & 0xFFFFFFFF:08X})")
    return hr


def add_ref(obj):
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    REF_T(vtbl[1])(obj)


def release(obj):
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    REF_T(vtbl[2])(obj)


def query(obj, iid):
    out = ctypes.c_void_p()
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    hr = QI_T(vtbl[0])(obj, ctypes.byref(iid), ctypes.byref(out))
    return out.value if hr == S_OK else None


class Handler:
    """A COM callback object made from a Python function. WebView2 only ever needs IUnknown and
    Invoke from these, and keeps them only as long as AltVault does, so counting is a formality."""

    def __init__(self, fn_type, fn):
        self._qi = QI_T(self._query)
        self._ref = REF_T(lambda this: 1)
        self._invoke = fn_type(fn)
        self._vtbl = (ctypes.c_void_p * 4)(*(ctypes.cast(f, ctypes.c_void_p) for f in (self._qi, self._ref, self._ref, self._invoke)))
        self._obj = (ctypes.c_void_p * 1)(ctypes.cast(self._vtbl, ctypes.c_void_p))
        self.ptr = ctypes.cast(self._obj, ctypes.c_void_p)

    def _query(self, this, riid, out):
        out[0] = this
        return S_OK


class WebView:
    """A WebView2 filling the window `hwnd`. `on_message(dict)` gets what the page posts with
    window.chrome.webview.postMessage(object); post(dict) sends the page an object. on_ready runs
    once the page can be used."""

    def __init__(self, hwnd, folder, user_data, on_message, on_ready=None, background=(0x15, 0x15, 0x16), devtools=False,
                 extra_hosts=None):
        self.hwnd, self.folder, self.on_message, self.on_ready = hwnd, folder, on_message, on_ready
        self.extra_hosts = extra_hosts or {}
        self.controller = self.webview = self.webview3 = self.webview19 = None
        self.background, self.devtools = background, devtools
        self._handlers = []
        _ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
        loader = ctypes.WinDLL(os.path.join(os.path.dirname(os.path.abspath(__file__)), "WebView2Loader.dll"))
        create = loader.CreateCoreWebView2EnvironmentWithOptions
        create.argtypes = [wt.LPCWSTR, wt.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p]
        create.restype = HRESULT
        hr = create(None, user_data, None, self._handler(DONE_T, self._env_ready).ptr)
        if hr < 0:
            raise OSError(f"WebView2 isn't available (0x{hr & 0xFFFFFFFF:08X}). Install the WebView2 runtime.")

    def _handler(self, kind, fn):
        h = Handler(kind, fn)
        self._handlers.append(h)
        return h

    def _env_ready(self, this, error, env):
        if error < 0 or not env:
            return S_OK
        call(env, ENV_CREATE_CONTROLLER, self.hwnd, self._handler(DONE_T, self._controller_ready).ptr,
             types=(wt.HWND, ctypes.c_void_p))
        return S_OK

    def _controller_ready(self, this, error, controller):
        if error < 0 or not controller:
            return S_OK
        add_ref(controller)
        self.controller = controller
        c2 = query(controller, IID_ICoreWebView2Controller2)
        if c2:
            r, g, b = self.background
            call(c2, CTL2_PUT_BACKGROUND, COLOR(255, r, g, b), types=(COLOR,))
            release(c2)
        out = ctypes.c_void_p()
        call(controller, CTL_GET_WEBVIEW, ctypes.byref(out), types=(ctypes.c_void_p,))
        self.webview = out.value
        settings = ctypes.c_void_p()
        call(self.webview, WV_GET_SETTINGS, ctypes.byref(settings), types=(ctypes.c_void_p,))
        for index, value in ((SET_DEVTOOLS, self.devtools), (SET_CONTEXT_MENUS, self.devtools), (SET_STATUS_BAR, False),
                             (SET_ZOOM, False), (SET_SCRIPT_DIALOGS, False), (SET_ERROR_PAGE, False)):
            call(settings.value, index, int(value), types=(wt.BOOL,))
        release(settings.value)
        token = ctypes.c_int64()
        call(self.webview, WV_ADD_MESSAGE, self._handler(EVENT_T, self._message).ptr, ctypes.byref(token),
             types=(ctypes.c_void_p, ctypes.c_void_p))
        self.webview3 = query(self.webview, IID_ICoreWebView2_3)
        self.webview19 = query(self.webview, IID_ICoreWebView2_19)
        # The page is served from this folder under a private https name; nothing else is reachable.
        # DENY_CORS: the page may show the other folders' images, but scripts can't read them.
        for name, path in {"altvault.example": self.folder, **self.extra_hosts}.items():
            os.makedirs(path, exist_ok=True)
            call(self.webview3, WV3_MAP_FOLDER, name, path, ACCESS_DENY_CORS, types=(wt.LPCWSTR, wt.LPCWSTR, ctypes.c_int))
        self.resize()
        call(self.webview, WV_NAVIGATE, "https://altvault.example/index.html", types=(wt.LPCWSTR,))
        return S_OK

    def _message(self, this, sender, args):
        text = ctypes.c_wchar_p()
        try:
            call(args, ARGS_GET_JSON, ctypes.byref(text), types=(ctypes.c_void_p,))
            data = json.loads(text.value or "null")
        except (OSError, ValueError):
            return S_OK
        finally:
            if text:
                _ole32.CoTaskMemFree(ctypes.cast(text, ctypes.c_void_p))
        if isinstance(data, dict):
            if data.get("type") == "ready" and self.on_ready:
                self.on_ready()
            else:
                self.on_message(data)
        return S_OK

    def post(self, data):
        if self.webview:
            call(self.webview, WV_POST_JSON, json.dumps(data), types=(wt.LPCWSTR,))

    def resize(self, width=None, height=None):
        if not self.controller:
            return
        if width is None:
            r = RECT()
            ctypes.windll.user32.GetClientRect(wt.HWND(self.hwnd), ctypes.byref(r))
            width, height = r.right, r.bottom
        call(self.controller, CTL_PUT_BOUNDS, RECT(0, 0, width, height), types=(RECT,))

    def moved(self):
        if self.controller:
            call(self.controller, CTL_NOTIFY_MOVED)

    def set_hidden(self, hidden):
        """Hidden in the tray: stop drawing and ask WebView2 to give back memory. Shown again: resume."""
        if not self.controller:
            return
        call(self.controller, CTL_PUT_VISIBLE, int(not hidden), types=(wt.BOOL,))
        if self.webview19:
            call(self.webview19, WV19_PUT_MEMORY_TARGET, MEMORY_LOW if hidden else MEMORY_NORMAL, types=(ctypes.c_int,))
        if self.webview3 and not hidden:
            try:
                call(self.webview3, WV3_RESUME)
            except OSError:
                pass

    def close(self):
        if self.controller:
            call(self.controller, CTL_CLOSE)
            for obj in (self.webview3, self.webview19, self.controller):
                if obj:
                    release(obj)
            self.controller = self.webview = self.webview3 = self.webview19 = None
