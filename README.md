# ScrollShot

Screenshot a whole scrolling page, not just what's visible.

## Install

Download **`ScrollShot-Setup.exe`** from the
[latest release](https://github.com/gotolisto/scrollshot/releases/latest) and run it.
You don't need Python, admin rights or anything else.
In the installer you can choose:
- **Start ScrollShot when Windows starts** (runs quietly in the system tray)
- **Desktop shortcut**

Windows may show *"Windows protected your PC"* because the installer isn't
code-signed. Click **More info → Run anyway**.

To uninstall: **Settings → Apps → ScrollShot → Uninstall**, or use
"Uninstall ScrollShot" in the Start menu.

## Using it

ScrollShot lives in the **system tray** (near the clock; it may be under the **^**).
Click the tray icon for a new screenshot, or right-click it for Settings, Open folder
and Quit. Closing the window keeps it running in the tray.

1. Press **Ctrl+Alt+C, C** (hold Ctrl+Alt and tap C twice) from any app, or click **+**
   or the tray icon. Every screen turns blue with a cross cursor.
2. Click on the part you want to capture: the page, or a panel like a sidebar.
   Scrolling happens at that spot.
3. The screen flashes when the first shot is taken and the cursor turns into a
   **down arrow**.
4. **Click** anywhere to scroll down and capture more, as many times as you like.
   **Double-click** to save. Nothing is saved until you double-click.
   **Esc** or **right-click** cancels.

ScrollShot works out which part of the screen actually scrolled and saves only that
part as one continuous image: no browser toolbar, no taskbar, no repeated sticky
headers, account rows or floating buttons. If only a sidebar scrolled, you get the
sidebar; if the page scrolled, you get the page.

Every screenshot is saved as a PNG **and copied to the clipboard**, ready to paste.

## Settings (⚙)

- **Save screenshots to**: your default folder (default `Pictures\ScrollShot`).
- **Copy to clipboard after saving**: on by default.
- **Scroll per click**: mouse-wheel notches per click (default 5). If an image has
  a gap or a repeated block, lower it.
- **Start with Windows**: turn auto-start on or off.
- **Keyboard shortcut**: works from any app, even when ScrollShot is in the tray.
  Click the shortcut button and press the new keys (it must include Ctrl, Alt or Win,
  or be an F-key). Tick **Press the last key twice** for a double-tap like the default
  Ctrl+Alt+C, C. While ScrollShot runs, the shortcut belongs to it, so other apps won't
  also react to it. If Windows or another app already uses the combination, ScrollShot
  says so and you can pick another.
  (The **Fn** key can't be used: laptops handle it inside the keyboard and never send it
  to Windows.)

## Known limits

- Videos and carousels that keep moving can confuse the alignment.
- Each click takes about a second to scroll and capture (the cursor shows a
  spinner). A double-click during that time saves right after the capture.
- On Windows 10 versions before 2004 the info bar is briefly hidden while each
  shot is taken.

## For developers

| File | What it is |
|---|---|
| `scrollshot.py` | The app: window, tray, settings, capture flow |
| `stitcher.py` | Finds what scrolled between shots and joins it into one image |
| `hotkey.py` | System-wide keyboard shortcut (Windows `RegisterHotKey`) |
| `clipboard.py` | Copies the saved image to the clipboard |
| `test_stitch.py` | Simulated-screen tests for the stitching |
| `installer.nsi` | NSIS installer script |
| `build.sh` | Rebuilds the installer on Linux with Wine (optional) |

**Run from source (Windows):** `pip install -r requirements.txt`, then `python scrollshot.py`.

**Tests:** `pip install numpy`, then `python test_stitch.py`.

**Builds and releases:** GitHub Actions (`.github/workflows/build.yml`) runs the tests and
builds `ScrollShot-Setup.exe` on Windows for every push; the installer is attached to the
run as an artifact. To publish a release, bump `VERSION` in `installer.nsi` and push to
`main`: when that version has no release yet, the workflow creates release `vVERSION` with
the installer attached.
