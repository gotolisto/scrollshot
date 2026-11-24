"""Simulated screens: check that only the scrolled panel is joined, exactly."""
import numpy as np
from stitcher import stitch

rng = np.random.default_rng(7)
SW, SH = 1000, 700                       # screen size


def text_block(h, w, seed, gap_rows=()):
    """A 'page' of text-like lines on a light background."""
    r = np.random.default_rng(seed)
    pg = np.full((h, w, 3), 248, np.uint8)
    for y in range(0, h - 28, 28):
        if any(a <= y < b for a, b in gap_rows):
            continue                                     # blank stretch
        n = int(r.integers(20, w - 40))
        pg[y + 6:y + 18, 16:16 + n] = r.integers(0, 140, (12, n, 3))
    return pg


def noise(h, w, seed):
    return np.random.default_rng(seed).integers(0, 255, (h, w, 3)).astype(np.uint8)


def run(name, screens, virtual, panel_x0, hint=None, min_h=None):
    """virtual: the full tall content of the panel, whose left edge is at
    screen column panel_x0. The result must be an exact, continuous crop of
    it: columns inside the panel, starting at the top, complete height."""
    import stitcher
    out = stitch(screens, hint)
    # Plain columns at the edges (background next to the panel) are fine:
    # every one is a single colour from top to bottom. Compare the rest.
    plain = (out == out[:1]).all(axis=(0, 2))
    while out.shape[1] > 1 and plain[0]:
        out, plain = out[:, 1:], plain[1:]
    while out.shape[1] > 1 and plain[-1]:
        out, plain = out[:, :-1], plain[:-1]
    h, w = out.shape[:2]
    want_h = min_h or virtual.shape[0]
    ok, why = False, ""
    # find which columns of the panel the output corresponds to
    for x in range(0, virtual.shape[1] - w + 1):
        if h <= virtual.shape[0] and np.array_equal(out, virtual[:h, x:x + w]):
            ok = True
            break
    if ok:
        rest = np.concatenate([virtual[:h, :x], virtual[:h, x + w:]], axis=1)
        if rest.size and (rest != virtual[0, 0]).any():
            ok, why = False, "content cut off at the sides"
    if not ok and not why:
        why = "not an exact crop of the page"
    elif h < want_h - 30:
        ok, why = False, f"too short ({h} < {want_h})"
    elif w < virtual.shape[1] * 0.7:
        ok, why = False, f"too narrow ({w})"
    print(f"{name:42s} -> {h}x{w}  " + ("OK" if ok else "FAIL: " + why))
    return ok


results = []

# A) Whole web page scrolls inside a browser; toolbar, taskbar, scrollbar.
page = text_block(3000, 960, 1, gap_rows=[(1200, 1500)])
toolbar, taskbar = noise(90, SW, 2), noise(40, SW, 3)
VIEW = SH - 90 - 40


def screen_a(y):
    s = np.zeros((SH, SW, 3), np.uint8)
    s[:90] = toolbar
    s[90:90 + VIEW, :960] = page[y:y + VIEW]
    s[90:90 + VIEW, 960:] = 200                          # scrollbar track
    t = 90 + int(y / 3000 * VIEW)
    s[t:t + 60, 984:996] = 90                            # moving thumb
    s[SH - 40:] = taskbar
    return s


ys = [0, 250, 500, 750, 1000, 1250, 1500, 1750, 2000, 2250, 2370]
results.append(run("A whole page, even steps", [screen_a(y) for y in ys],
                   page[:2370 + VIEW], 0))
ys = [0, 300, 470, 900, 1150, 1260, 1700, 2100, 2370]
results.append(run("A whole page, uneven steps", [screen_a(y) for y in ys],
                   page[:2370 + VIEW], 0))

# B) Dennis's case: sidebar scrolls, main content fixed, sticky row at the
#    sidebar bottom, floating button, browser toolbar and taskbar.
side = text_block(1800, 230, 4)
main = text_block(VIEW, 740, 5)
account = noise(50, 230, 6)
SIDE_VIEW = VIEW - 50


def screen_b(y):
    s = np.zeros((SH, SW, 3), np.uint8)
    s[:90] = toolbar
    s[90:90 + SIDE_VIEW, 10:240] = side[y:y + SIDE_VIEW]
    s[90 + SIDE_VIEW:90 + VIEW, 10:240] = account        # sticky account row
    s[90:90 + VIEW, 260:1000] = main                     # doesn't scroll
    s[500:530, 20:50] = 30                               # floating button
    s[SH - 40:] = taskbar
    return s


ys = [0, 300, 600, 900, 1200, 1280]
exp = side[:1280 + SIDE_VIEW]          # the floating button is left out
results.append(run("B sidebar scrolls, main fixed", [screen_b(y) for y in ys],
                   exp, 10, (100, 300)))

# C) Main content scrolls, sidebar fixed, sticky header in the panel,
#    a label at the top that changes every frame (like the app's info bar).
long_main = text_block(2600, 740, 8, gap_rows=[(900, 1100)])
side_fixed = text_block(VIEW, 230, 9)
sticky = noise(40, 740, 10)


def screen_c(y, i):
    s = np.zeros((SH, SW, 3), np.uint8)
    s[:90] = toolbar
    s[20:50, 400:600] = (i * 37) % 255                   # changing label
    s[90:90 + VIEW, 10:240] = side_fixed
    s[90:130, 260:1000] = sticky                         # sticky header
    s[130:90 + VIEW, 260:1000] = long_main[y:y + VIEW - 40]
    s[SH - 40:] = taskbar
    return s


ys = [0, 260, 520, 600, 860, 1300, 1660, 1920, 2010]
results.append(run("C main scrolls, sidebar fixed, uneven",
                   [screen_c(y, i) for i, y in enumerate(ys)],
                   long_main[:2010 + VIEW - 40], 260, (600, 400)))

print("ALL OK" if all(results) else "SOME FAILED")
raise SystemExit(0 if all(results) else 1)
