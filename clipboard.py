"""Put a saved image on the system clipboard (Windows, macOS, Linux/X11)."""
import io
import subprocess
import sys
import time


def copy_image(path, img):
    """path: the saved PNG; img: the same image as a PIL Image."""
    if sys.platform.startswith("win"):
        _copy_windows(img)
    elif sys.platform == "darwin":
        safe = path.replace("\\", "\\\\").replace('"', '\\"')
        subprocess.run(
            ["osascript", "-e",
             f'set the clipboard to (read (POSIX file "{safe}") as «class PNGf»)'],
            check=True, capture_output=True)
    else:
        subprocess.run(["xclip", "-selection", "clipboard", "-t", "image/png",
                        "-i", path], check=True, capture_output=True)


def _copy_windows(img):
    import ctypes
    from ctypes import wintypes

    k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
    k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k32.GlobalAlloc.restype = wintypes.HGLOBAL
    k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    u32.OpenClipboard.argtypes = [wintypes.HWND]
    u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    u32.SetClipboardData.restype = wintypes.HANDLE
    u32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    u32.RegisterClipboardFormatW.restype = wintypes.UINT

    def to_global(data):
        h = k32.GlobalAlloc(0x0002, len(data))          # GMEM_MOVEABLE
        p = k32.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        k32.GlobalUnlock(h)
        return h

    bmp = io.BytesIO()
    img.convert("RGB").save(bmp, "BMP")
    dib = bmp.getvalue()[14:]                 # drop BITMAPFILEHEADER -> CF_DIB
    png = io.BytesIO()
    img.save(png, "PNG")

    for _ in range(20):                       # another app may hold it briefly
        if u32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("clipboard is busy")
    try:
        u32.EmptyClipboard()
        if not u32.SetClipboardData(8, to_global(dib)):            # CF_DIB
            raise RuntimeError("SetClipboardData failed")
        # PNG too, for apps that prefer it (Slack, Chrome, Office…)
        u32.SetClipboardData(u32.RegisterClipboardFormatW("PNG"),
                             to_global(png.getvalue()))
    finally:
        u32.CloseClipboard()
