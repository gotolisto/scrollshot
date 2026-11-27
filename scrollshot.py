#!/usr/bin/env python3
"""ScrollShot — screenshot a whole scrolling page, not just what's visible.

Flow:
  1. Click "+".
  2. Cursor becomes a cross: click the screen you want (click on the page
     content itself — that's where scrolling will happen).
  3. Flash = first shot taken. If the page scrolls, cursor becomes an arrow.
  4. Click anywhere  -> scroll down and capture the next part.
     Double-click    -> stitch everything so far and save.
     Reaching bottom -> saves automatically.
     Esc / right-click -> cancel.
"""
import os
import subprocess
import sys
import time
from datetime import datetime
import tkinter as tk
from tkinter import filedialog

import mss
import numpy as np
from PIL import Image
from pynput.mouse import Controller as MouseController

from stitcher import similar, stitch

IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"

if IS_WIN:
    # Use real pixels so Tk, mouse and screen-capture coordinates all agree
    # on scaled (125%, 150%...) displays.
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

import json
import queue
import threading

from clipboard import copy_image
from hotkey import GlobalHotkey, describe

APP_NAME = "ScrollShot"
DOUBLE_CLICK_MS = 300
# A fully transparent window lets clicks fall through on some systems,
# so the click-catching overlay is *almost* transparent.
CATCHER_ALPHA = 0.05 if IS_MAC else 0.01
FONT = "Segoe UI" if IS_WIN else "Helvetica"
DEFAULT_DIR = os.path.join(os.path.expanduser("~"), "Pictures", "ScrollShot")
DEFAULT_HOTKEY = {"enabled": True, "mods": ["ctrl", "alt"], "vk": 0x43,
                  "key": "C", "double": True}          # Ctrl+Alt+C, C
DEFAULTS = {"folder": DEFAULT_DIR, "copy_to_clipboard": True, "scroll_step": 5,
            "hotkey": DEFAULT_HOTKEY}
DOUBLE_PRESS_S = 0.6     # second press must come within this time


def _base_dir():
    """Folder holding bundled files (icon) — works for script and .exe."""
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def _settings_path():
    if IS_WIN:
        root = os.environ.get("APPDATA", os.path.expanduser("~"))
    elif IS_MAC:
        root = os.path.expanduser("~/Library/Application Support")
    else:
        root = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    return os.path.join(root, APP_NAME, "settings.json")


def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(_settings_path(), encoding="utf-8") as fh:
            s.update({k: v for k, v in json.load(fh).items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    hk = s.get("hotkey")
    s["hotkey"] = dict(DEFAULT_HOTKEY, **hk) if isinstance(hk, dict) \
        else dict(DEFAULT_HOTKEY)
    return s


def save_settings(s):
    p = _settings_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(s, fh, indent=2)


# ---------------------------------------------------- start with Windows
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _launch_command():
    if getattr(sys, "frozen", False):                 # installed .exe
        return f'"{sys.executable}" --tray'
    pyw = sys.executable.replace("python.exe", "pythonw.exe")
    return f'"{pyw}" "{os.path.abspath(__file__)}" --tray'


def autostart_enabled():
    if not IS_WIN:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
        return True
    except OSError:
        return False


def set_autostart(on):
    if not IS_WIN:
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                        winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, _launch_command())
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except OSError:
                pass


def alternative_spots(mon, point, count=6):
    """Other places to try the mouse wheel when the click spot didn't scroll:
    the same column, at other heights, from the middle of the screen out. A
    side panel's header and footer stay put and swallow the wheel, while its
    body scrolls."""
    x, y = point
    spots = []
    for f in (0.5, 0.35, 0.65, 0.25, 0.75, 0.15, 0.85):
        sy = int(mon["top"] + mon["height"] * f)
        if abs(sy - y) >= 60:                  # not just next to the failed one
            spots.append((x, sy))
    return spots[:count]


class ScrollShot:
    def __init__(self):
        # Before Tk: the shortcut thread must exist before the first window.
        self.hotkey = GlobalHotkey(self._hotkey_from_thread)
        self.root = tk.Tk()
        self.root.title("ScrollShot")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)

        self.sct = getattr(mss, "MSS", mss.mss)()
        self.mouse = MouseController()
        self.settings = load_settings()
        self.status = tk.StringVar(value="Click + to take a screenshot")
        self.last_file = None

        self.overlays = []      # windows currently on screen
        self.monitor = None
        self.point = None       # where the user clicked
        self.scroll_point = None  # where the wheel works (if not the click)
        self.frames = []
        self.pending_click = None
        self.busy = False
        self.finish_requested = False
        self.note = ""
        self.catcher = None
        self.hud = None
        self.capture_exclusion = True
        self._last_hotkey = 0.0
        self.hint = tk.StringVar()

        self._build_main()

    # ------------------------------------------------------------- main UI
    def _build_main(self):
        r = self.root
        frm = tk.Frame(r, padx=16, pady=14)
        frm.pack()

        tk.Button(frm, text="+", font=(FONT, 34, "bold"), width=3,
                  command=self.start).grid(row=0, column=0, rowspan=2,
                                           padx=(0, 14))
        tk.Label(frm, text="ScrollShot", font=(FONT, 15, "bold")).grid(
            row=0, column=1, sticky="w")
        tk.Button(frm, text="⚙ Settings", command=self.open_settings).grid(
            row=1, column=1, sticky="w")

        tk.Label(frm, textvariable=self.hint, fg="#666").grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))
        tk.Label(frm, textvariable=self.status, fg="#0a5", wraplength=320,
                 justify="left").grid(row=2, column=0, columnspan=2,
                                      sticky="w", pady=(10, 0))
        bar = tk.Frame(frm)
        bar.grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.open_btn = tk.Button(bar, text="Open last", state="disabled",
                                  command=lambda: self.reveal(self.last_file))
        self.open_btn.pack(side="left")
        tk.Button(bar, text="Open folder",
                  command=lambda: self.reveal(self.settings["folder"])).pack(
            side="left", padx=6)

    def open_settings(self):
        win = tk.Toplevel(self.root)
        win.title("ScrollShot settings")
        win.resizable(False, False)
        win.attributes("-topmost", True)
        win.transient(self.root)
        f = tk.Frame(win, padx=16, pady=14)
        f.pack()

        folder = tk.StringVar(value=self.settings["folder"])
        copy = tk.BooleanVar(value=self.settings["copy_to_clipboard"])
        step = tk.IntVar(value=self.settings["scroll_step"])
        auto = tk.BooleanVar(value=autostart_enabled())
        hk = dict(self.settings["hotkey"])
        hk_on = tk.BooleanVar(value=hk["enabled"])
        hk_double = tk.BooleanVar(value=hk["double"])
        hk_text = tk.StringVar()
        recording = {"on": False, "held": set()}
        # Release the shortcut while this window is open so it can be re-recorded.
        self.hotkey.stop()

        def refresh_hk_text():
            hk["double"] = bool(hk_double.get())
            hk_text.set(describe(hk))
        refresh_hk_text()

        tk.Label(f, text="Save screenshots to").grid(row=0, column=0,
                                                     columnspan=2, sticky="w")
        tk.Entry(f, textvariable=folder, width=42).grid(row=1, column=0,
                                                        sticky="we")

        def browse():
            d = filedialog.askdirectory(parent=win, initialdir=folder.get())
            if d:
                folder.set(d)
        tk.Button(f, text="Browse…", command=browse).grid(row=1, column=1,
                                                          padx=(6, 0))

        tk.Checkbutton(f, text="Copy to clipboard after saving",
                       variable=copy).grid(row=2, column=0, columnspan=2,
                                           sticky="w", pady=(12, 0))

        if IS_WIN:
            tk.Checkbutton(f, text="Start with Windows (runs in the tray)",
                           variable=auto).grid(row=5, column=0, columnspan=2,
                                               sticky="w", pady=(8, 0))

        sc = tk.LabelFrame(f, text="Keyboard shortcut (works from any app)",
                           padx=8, pady=6)
        sc.grid(row=4, column=0, columnspan=2, sticky="we", pady=(12, 0))
        tk.Checkbutton(sc, text="Enabled", variable=hk_on).grid(
            row=0, column=0, sticky="w")
        rec_btn = tk.Button(sc, textvariable=hk_text, width=22)
        rec_btn.grid(row=0, column=1, padx=8)
        tk.Checkbutton(sc, text="Press the last key twice", variable=hk_double,
                       command=refresh_hk_text).grid(row=1, column=0,
                                                     columnspan=2, sticky="w")
        hk_msg = tk.StringVar(value="Click the shortcut button, then press the keys.")
        tk.Label(sc, textvariable=hk_msg, fg="#666", font=(FONT, 9),
                 wraplength=330, justify="left").grid(row=2, column=0,
                                                      columnspan=2, sticky="w")

        MOD_KEYS = {"Control_L": "ctrl", "Control_R": "ctrl", "Alt_L": "alt",
                    "Alt_R": "alt", "Shift_L": "shift", "Shift_R": "shift",
                    "Win_L": "win", "Win_R": "win", "Super_L": "win",
                    "Super_R": "win", "Meta_L": "alt", "Meta_R": "alt"}
        SOLO_OK = {f"F{i}" for i in range(1, 25)} | {"Pause", "Scroll_Lock",
                                                     "Print", "Insert"}

        def start_recording():
            recording["on"] = True
            recording["held"] = set()
            hk_text.set("Press keys…")
            hk_msg.set("Hold Ctrl / Alt / Win and press a key. Esc = cancel.")
            win.focus_force()
        rec_btn.config(command=start_recording)

        def key_down(e):
            if not recording["on"]:
                return
            if e.keysym in MOD_KEYS:
                recording["held"].add(MOD_KEYS[e.keysym])
                return "break"
            if e.keysym == "Escape":
                recording["on"] = False
                refresh_hk_text()
                hk_msg.set("Unchanged.")
                return "break"
            mods = recording["held"]
            if not (mods & {"ctrl", "alt", "win"}) and e.keysym not in SOLO_OK:
                hk_msg.set("Add Ctrl, Alt or Win, so normal typing "
                           "doesn't trigger it.")
                return "break"
            key = e.keysym.upper() if len(e.keysym) == 1 else e.keysym
            key = {"Print": "PrintScreen", "Next": "PageDown",
                   "Prior": "PageUp"}.get(key, key)
            hk.update(mods=sorted(mods), vk=int(e.keycode), key=key)
            recording["on"] = False
            refresh_hk_text()
            hk_msg.set("Got it. Click Save to use it.")
            return "break"

        def key_up(e):
            if e.keysym in MOD_KEYS:
                recording["held"].discard(MOD_KEYS[e.keysym])
        win.bind("<KeyPress>", key_down, add="+")
        win.bind("<KeyRelease>", key_up, add="+")

        row = tk.Frame(f)
        row.grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        tk.Label(row, text="Scroll per click (mouse-wheel notches)").pack(
            side="left")
        tk.Spinbox(row, from_=1, to=30, width=4, textvariable=step).pack(
            side="left", padx=6)

        def save():
            try:
                s = max(1, min(30, int(step.get())))
            except (tk.TclError, ValueError):
                s = DEFAULTS["scroll_step"]
            hk["enabled"] = bool(hk_on.get())
            hk["double"] = bool(hk_double.get())
            self.settings.update(folder=folder.get().strip() or DEFAULT_DIR,
                                 copy_to_clipboard=bool(copy.get()),
                                 scroll_step=s, hotkey=dict(hk))
            save_settings(self.settings)
            msg = "Settings saved"
            err = self.apply_hotkey()
            if err:
                msg += f". Shortcut not active: {err}"
            if IS_WIN and auto.get() != autostart_enabled():
                try:
                    set_autostart(auto.get())
                except OSError as e:
                    msg = f"Settings saved, but couldn't change startup: {e}"
            self.status.set(msg)
            closing["saved"] = True
            win.destroy()

        closing = {"saved": False}

        def on_destroy(e):
            # Cancelled or closed: switch the old shortcut back on.
            if e.widget is win and not closing["saved"]:
                self.apply_hotkey()
        win.bind("<Destroy>", on_destroy, add="+")

        btns = tk.Frame(f)
        btns.grid(row=6, column=0, columnspan=2, sticky="e", pady=(14, 0))
        tk.Button(btns, text="Cancel", command=win.destroy).pack(side="right")
        tk.Button(btns, text="Save", command=save, default="active").pack(
            side="right", padx=6)
        win.bind("<Return>", lambda e: None if recording["on"] else save())
        win.bind("<Escape>",
                 lambda e: None if recording["on"] else win.destroy())

    @staticmethod
    def reveal(path):
        if not path:
            return
        if not os.path.isfile(path):
            os.makedirs(path, exist_ok=True)
        if IS_WIN:
            os.startfile(path)
        elif IS_MAC:
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    # ------------------------------------------------------- helpers
    def _overlay(self, mon, alpha, bg="black", cursor="crosshair"):
        w = tk.Toplevel(self.root)
        w.overrideredirect(True)
        w.geometry(f"{mon['width']}x{mon['height']}+{mon['left']}+{mon['top']}")
        w.configure(bg=bg, cursor=cursor)
        w.attributes("-topmost", True)
        w.attributes("-alpha", alpha)
        w.bind("<Escape>", lambda e: self.cancel())
        w.bind("<Button-3>", lambda e: self.cancel())
        if IS_MAC:
            w.bind("<Button-2>", lambda e: self.cancel())
        self.overlays.append(w)
        self._exclude_from_capture(w)
        return w

    def _exclude_from_capture(self, w):
        """Keep our own overlay windows out of the screenshots (Windows 10
        2004+). The overlay still catches clicks and keeps the page from
        showing hover effects (link previews, highlighted rows)."""
        ok = False
        if IS_WIN:
            try:
                w.update_idletasks()
                u32 = ctypes.windll.user32
                hwnd = u32.GetAncestor(w.winfo_id(), 2)    # GA_ROOT
                ok = bool(u32.SetWindowDisplayAffinity(hwnd, 0x11))
            except Exception:
                ok = False
        if not ok:
            self.capture_exclusion = False     # older Windows / other OS
        return ok

    def _clear_overlays(self):
        for w in self.overlays:
            try:
                w.destroy()
            except tk.TclError:
                pass
        self.overlays = []
        self.root.update()

    def _show(self):
        for w in self.overlays:
            w.deiconify()
            w.attributes("-topmost", True)
        if self.overlays:
            self.overlays[0].focus_force()
        self.root.update()

    def grab(self):
        shot = self.sct.grab(self.monitor)
        arr = np.frombuffer(shot.bgra, np.uint8).reshape(
            shot.height, shot.width, 4)
        return arr[:, :, 2::-1].copy()          # BGRA -> RGB

    def grab_settled(self, prev=None, timeout=3.0, quiet_samples=3):
        """Capture once the screen has stopped moving (smooth scrolling eases
        out slowly, so it must stay still for several samples in a row)."""
        hud = getattr(self, "hud", None)
        hide = not self.capture_exclusion and hud is not None
        if hide:
            try:
                hud.withdraw()
                self.root.update()
            except tk.TclError:
                hide = False
        try:
            return self._grab_settled(prev, timeout, quiet_samples)
        finally:
            if hide:
                try:
                    hud.deiconify()
                    hud.attributes("-topmost", True)
                    self.root.update()
                except tk.TclError:
                    pass

    def _grab_settled(self, prev, timeout, quiet_samples):
        start = time.time()
        time.sleep(0.2)
        last = self.grab()
        still = 0
        while time.time() - start < timeout:
            time.sleep(0.08)
            cur = self.grab()
            if similar(last, cur):
                still += 1
                # If nothing has moved yet, give the scroll a moment to start.
                if still >= quiet_samples and (
                        prev is None or not similar(prev, cur)
                        or time.time() - start > 1.0):
                    return cur
            else:
                still = 0
            last = cur
        return last

    def _activate_target(self, point):
        """Bring the window under the scroll point to the front, so the
        mouse wheel reaches it even if Windows' 'scroll inactive windows'
        setting is off."""
        if not IS_WIN:
            return
        try:
            from ctypes import wintypes
            u32 = ctypes.windll.user32
            u32.WindowFromPoint.argtypes = [wintypes.POINT]
            u32.WindowFromPoint.restype = wintypes.HWND
            u32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
            u32.GetAncestor.restype = wintypes.HWND
            u32.SetForegroundWindow.argtypes = [wintypes.HWND]
            hwnd = u32.WindowFromPoint(wintypes.POINT(*map(int, point)))
            top = u32.GetAncestor(hwnd, 2) if hwnd else None   # GA_ROOT
            if top:
                u32.SetForegroundWindow(top)
                time.sleep(0.05)
        except Exception:
            pass

    def scroll(self, notches, point=None):
        # Nudge the pointer so the system re-checks which window is under
        # it (our overlay was just closed), then park it on the scroll point.
        point = point or self.scroll_point or self.point
        x, y = point
        self.mouse.position = (x + 1, y + 1)
        time.sleep(0.03)
        self.mouse.position = point
        self._activate_target(point)
        time.sleep(0.03)
        # One notch at a time, like a real wheel: some apps ignore a single
        # large multi-notch event.
        direction = -1 if notches > 0 else 1    # negative = down
        for _ in range(abs(notches)):
            self.mouse.scroll(0, direction)
            time.sleep(0.02)

    def flash(self):
        w = self._overlay(self.monitor, 0.75, bg="white", cursor="watch")
        self.root.update()
        for a in (0.6, 0.45, 0.3, 0.15, 0.05):
            time.sleep(0.04)
            w.attributes("-alpha", a)
            self.root.update()
        self.overlays.remove(w)
        w.destroy()
        self.root.update()

    # ------------------------------------------------------------- flow
    def start(self):
        self.frames = []
        self.root.withdraw()
        self.root.update()
        time.sleep(0.2)
        for mon in self.sct.monitors[1:]:
            w = self._overlay(mon, 0.25, bg="#1e6fd9", cursor="crosshair")
            tk.Label(w, text="Click the page you want to capture\nEsc to cancel",
                     bg="#1e6fd9", fg="white",
                     font=(FONT, 22, "bold")).place(relx=.5, rely=.5,
                                                           anchor="center")
            w.bind("<Button-1>",
                   lambda e, m=mon: self.on_pick(m, e.x_root, e.y_root))
            for child in w.winfo_children():
                child.bind("<Button-1>",
                           lambda e, m=mon: self.on_pick(m, e.x_root, e.y_root))
        self._show()

    def on_pick(self, mon, x, y):
        self.monitor = dict(mon)
        self.point = (x, y)
        self.scroll_point = None
        self.finish_requested = False
        self.note = ""
        self._clear_overlays()
        self.mouse.position = self.point
        # The click-catcher goes up first: with it on top the page shows no
        # hover effects, and it is left out of the screenshot itself.
        self.enter_scroll_mode(busy=True)
        self.frames = [self.grab_settled()]
        self.flash()
        self._set_busy(False)

    def enter_scroll_mode(self, busy=False):
        self.busy = busy
        catcher = self._overlay(self.monitor, CATCHER_ALPHA, bg="black",
                                cursor="watch" if busy else "sb_down_arrow")
        self.catcher = catcher
        catcher.bind("<Button-1>", self.on_click)
        catcher.bind("<Double-Button-1>", self.on_double)

        hud = tk.Toplevel(self.root)
        hud.overrideredirect(True)
        hud.attributes("-topmost", True)
        hud.attributes("-alpha", 0.88)
        hud.configure(bg="#222", cursor="sb_down_arrow")
        self.hud_text = tk.StringVar()
        lbl = tk.Label(hud, textvariable=self.hud_text, bg="#222", fg="white",
                       font=(FONT, 12), padx=14, pady=7)
        lbl.pack()
        for widget in (hud, lbl):
            widget.bind("<Button-1>", self.on_click)
            widget.bind("<Double-Button-1>", self.on_double)
            widget.bind("<Button-3>", lambda e: self.cancel())
        self._update_hud()
        hud.update_idletasks()
        m = self.monitor
        hud.geometry(f"+{m['left'] + (m['width'] - hud.winfo_reqwidth()) // 2}"
                     f"+{m['top'] + 30}")
        self.overlays.append(hud)
        self.hud = hud
        self._exclude_from_capture(hud)
        self._show()

    def _update_hud(self):
        if self.busy:
            text = f"{len(self.frames)} captured  ·  capturing…"
        else:
            text = (f"{len(self.frames)} captured  ·  click = scroll ↓  ·  "
                    f"double-click = save  ·  Esc = cancel")
        if getattr(self, "note", ""):
            text += f"\n{self.note}"
        self.hud_text.set(text)

    def _set_busy(self, busy):
        self.busy = busy
        try:
            self.catcher.configure(cursor="watch" if busy else "sb_down_arrow")
        except (tk.TclError, AttributeError):
            pass
        self._update_hud()
        self.root.update()
        if not busy and self.finish_requested:
            self.finish()

    def on_click(self, event):
        if self.busy:
            return
        if self.pending_click:
            self.root.after_cancel(self.pending_click)
        self.pending_click = self.root.after(DOUBLE_CLICK_MS, self.do_scroll)

    def on_double(self, event):
        if self.pending_click:
            self.root.after_cancel(self.pending_click)
            self.pending_click = None
        if self.busy:
            self.finish_requested = True     # save as soon as this shot is in
            self.note = "Saving after this capture…"
            self._update_hud()
        else:
            self.finish()

    def do_scroll(self):
        self.pending_click = None
        if not self.frames:
            return
        # Close (not just hide) the overlays: a hidden window can still
        # swallow the mouse wheel, so the page wouldn't scroll.
        self._clear_overlays()
        self.busy = True
        time.sleep(0.4)          # let the overlay fully disappear first
        self.root.update()
        prev = self.frames[-1]
        notches = max(1, int(self.settings["scroll_step"]))
        self.scroll(notches)
        time.sleep(0.15)         # let the wheel reach the page...
        self.enter_scroll_mode(busy=True)   # ...then cover it again (no hover)
        cur = self.grab_settled(prev=prev)
        if similar(prev, cur) and len(self.frames) == 1:
            # Nothing has scrolled yet, so the click probably landed on a part
            # that doesn't scroll (a side panel's header or footer).
            found = self._find_scrollable_spot(prev, notches)
            if found is not None:
                cur = found
        if similar(prev, cur):
            self.note = ("The page didn't move - you may be at the end, or "
                         "this spot can't be scrolled with the mouse wheel. "
                         "Click to try again or double-click to save.")
        else:
            self.note = ""
            self.frames.append(cur)
        self._set_busy(False)

    def _find_scrollable_spot(self, prev, notches):
        """The click spot didn't scroll: try the wheel at other heights in the
        same column. Remembers the spot that works and returns the settled
        screenshot taken there, or None if nothing moved anywhere."""
        self.note = "Nothing moved here - trying other spots…"
        self._update_hud()
        for spot in alternative_spots(self.monitor, self.point):
            self._clear_overlays()
            self.busy = True
            time.sleep(0.2)
            self.scroll(notches, spot)
            time.sleep(0.5)
            if not similar(prev, self.grab()):
                self.scroll_point = spot
                self.note = ""
                self.enter_scroll_mode(busy=True)
                return self.grab_settled(prev=prev)
        self.enter_scroll_mode(busy=True)
        return None

    def finish(self, reason=None):
        self.finish_requested = False
        self.busy = False
        self._clear_overlays()
        self.root.deiconify()
        self.status.set("Stitching…")
        self.root.update()
        try:
            hint = None
            if self.point and self.frames:
                m = self.monitor
                sx = self.frames[0].shape[1] / m["width"]
                sy = self.frames[0].shape[0] / m["height"]
                hint = (int((self.point[0] - m["left"]) * sx),
                        int((self.point[1] - m["top"]) * sy))
            img = Image.fromarray(stitch(self.frames, hint))
            folder = self.settings["folder"]
            os.makedirs(folder, exist_ok=True)
            name = datetime.now().strftime("ScrollShot_%Y-%m-%d_%H-%M-%S.png")
            path = os.path.join(folder, name)
            img.save(path)
            self.last_file = path
            self.open_btn.config(state="normal")
            parts = len(self.frames)
            msg = (f"Saved {name}  ({img.width}×{img.height}, "
                   f"{parts} part{'s' if parts > 1 else ''})")
            if self.settings["copy_to_clipboard"]:
                try:
                    copy_image(path, img)
                    msg += " · copied to clipboard"
                except Exception as e:
                    msg += f" · couldn't copy to clipboard ({e})"
            self.status.set(f"{reason}. {msg}" if reason else msg)
        except Exception as e:
            self.status.set(f"Couldn't save: {e}")
        self.frames = []

    def cancel(self):
        if self.pending_click:
            self.root.after_cancel(self.pending_click)
            self.pending_click = None
        self._clear_overlays()
        self.frames = []
        self.busy = False
        self.finish_requested = False
        self.root.deiconify()
        self.status.set("Cancelled")

    # ------------------------------------------------------------- tray
    def _start_tray(self):
        """System-tray icon. Menu clicks arrive on pystray's thread, so they
        are queued and handled on the Tk thread."""
        try:
            import pystray
        except ImportError:
            return False
        icon_path = os.path.join(_base_dir(), "icon.png")
        image = (Image.open(icon_path) if os.path.exists(icon_path)
                 else Image.new("RGB", (64, 64), "#1e6fd9"))
        q = self.tray_queue
        item = pystray.MenuItem
        menu = pystray.Menu(
            item("New screenshot", lambda: q.put(self.start), default=True),
            item("Show window", lambda: q.put(self.show_window)),
            item("Settings…", lambda: q.put(self.open_settings)),
            item("Open folder",
                 lambda: q.put(lambda: self.reveal(self.settings["folder"]))),
            pystray.Menu.SEPARATOR,
            item("Quit", lambda: q.put(self.quit)),
        )
        self.tray = pystray.Icon(APP_NAME, image, APP_NAME, menu)
        threading.Thread(target=self.tray.run, daemon=True).start()
        return True

    def _poll_tray(self):
        try:
            while True:
                self.tray_queue.get_nowait()()
        except queue.Empty:
            pass
        self.root.after(150, self._poll_tray)

    # ---------------------------------------------------------- shortcut
    def apply_hotkey(self):
        """(Re)register the global shortcut from settings. Returns an error
        message, or None."""
        hk = self.settings["hotkey"]
        self.hotkey.stop()
        if not hk.get("enabled"):
            self.hint.set("")
            return None
        err = self.hotkey.start(hk["mods"], hk["vk"])
        self.hint.set(f"Shortcut: {describe(hk)}" if not err
                      else f"Shortcut {describe(hk)} unavailable")
        return err

    def _hotkey_from_thread(self):
        self.tray_queue.put(self._hotkey_pressed)

    def _hotkey_pressed(self):
        if self.overlays or self.frames or self.busy:
            return                       # a capture is already running
        if self.settings["hotkey"].get("double"):
            now = time.time()
            if now - self._last_hotkey > DOUBLE_PRESS_S:
                self._last_hotkey = now  # first press: wait for the second
                return
            self._last_hotkey = 0.0
        self.start()

    def show_window(self):
        self.root.deiconify()
        self.root.lift()

    def on_close(self):
        if self.tray:
            self.root.withdraw()            # keep running in the tray
        else:
            self.quit()

    def quit(self):
        if self.tray:
            self.tray.stop()
        self.root.destroy()

    def _listen_for_second_launch(self, server):
        def loop():
            while True:
                try:
                    conn, _ = server.accept()
                    conn.close()
                    self.tray_queue.put(self.show_window)
                except OSError:
                    return
        threading.Thread(target=loop, daemon=True).start()

    def run(self, start_hidden=False, server=None):
        self.tray_queue = queue.Queue()
        if server:
            self._listen_for_second_launch(server)
        self.tray = None
        has_tray = self._start_tray()
        err = self.apply_hotkey()
        if err:
            self.status.set(f"Shortcut not active: {err}")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        if start_hidden and has_tray:
            self.root.withdraw()
        self._poll_tray()
        self.root.mainloop()


if __name__ == "__main__":
    import socket
    SINGLE_INSTANCE_PORT = 47823
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        server.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
        server.listen(1)
    except OSError:
        # Already running: ask that copy to show its window, then exit.
        try:
            socket.create_connection(("127.0.0.1", SINGLE_INSTANCE_PORT),
                                     timeout=1).close()
        except OSError:
            pass
        sys.exit(0)
    ScrollShot().run(start_hidden="--tray" in sys.argv, server=server)
