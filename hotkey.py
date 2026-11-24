"""System-wide keyboard shortcut for Windows (works when the app isn't focused).

Uses the Win32 RegisterHotKey API on one long-lived background thread. Create
GlobalHotkey *before* the Tk window: the thread then owns the shortcut for the
whole session, and changes are sent to it as messages. While ScrollShot runs,
the combination is reserved for it, so other apps won't also react to it.
"""
import sys
import threading

MODS = {"ctrl": 0x0002, "alt": 0x0001, "shift": 0x0004, "win": 0x0008}
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
WM_APP_SET = 0x8000 + 1      # "apply the requested shortcut"
HOTKEY_ID = 1
IS_WIN = sys.platform.startswith("win")


def describe(hk):
    order = ["ctrl", "alt", "shift", "win"]
    names = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}
    parts = [names[m] for m in order if m in hk.get("mods", [])]
    key = hk.get("key", "?")
    text = "+".join(parts + [key])
    if hk.get("double"):
        text += f", {key}"
    return text


class GlobalHotkey:
    """Calls on_press() (from the background thread) each time the combo is hit."""

    def __init__(self, on_press):
        self.on_press = on_press
        self.thread_id = None
        self._request = None
        self._result = False
        self._done = threading.Event()
        if IS_WIN:
            started = threading.Event()
            threading.Thread(target=self._run, args=(started,),
                             daemon=True).start()
            started.wait(3)

    def _run(self, started):
        import ctypes
        from ctypes import wintypes
        u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
        msg = wintypes.MSG()
        u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)   # create queue
        self.thread_id = k32.GetCurrentThreadId()
        started.set()
        registered = False
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                try:
                    self.on_press()
                except Exception:
                    pass
            elif msg.message == WM_APP_SET:
                if registered:
                    u32.UnregisterHotKey(None, HOTKEY_ID)
                    registered = False
                req = self._request
                if req:
                    registered = bool(u32.RegisterHotKey(None, HOTKEY_ID,
                                                         req[0], req[1]))
                    self._result = registered
                else:
                    self._result = True
                self._done.set()
        if registered:
            u32.UnregisterHotKey(None, HOTKEY_ID)

    def _send(self, request):
        if not self.thread_id:
            return False
        import ctypes
        from ctypes import wintypes
        u32 = ctypes.windll.user32
        u32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT,
                                           wintypes.WPARAM, wintypes.LPARAM]
        self._done.clear()
        self._request = request
        u32.PostThreadMessageW(self.thread_id, WM_APP_SET, 0, 0)
        self._done.wait(3)
        return self._result

    def start(self, mods, vk):
        """Use this combination. Returns None on success, or an error message."""
        if not IS_WIN:
            return "Global shortcuts are only supported on Windows"
        flags = MOD_NOREPEAT
        for m in mods:
            flags |= MODS[m]
        if not self._send((flags, int(vk))):
            return "That shortcut is already used by Windows or another app"
        return None

    def stop(self):
        if IS_WIN:
            self._send(None)
