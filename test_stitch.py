"""Simulated screens: check how scrolled captures are joined.

The contract:
  * The result is ALWAYS exactly as wide as the screen. Where the user
    clicked must never change that; only the height depends on how far the
    page was scrolled.
  * The scrolling part is joined exactly, with nothing missing, whether or
    not the page was scrolled all the way to the bottom.
  * Parts that never scroll (browser toolbar, taskbar, sticky header) are
    not repeated down the page.
"""
import numpy as np
from stitcher import stitch

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


def check(name, out, want_h, virtual=None, x0=0, pw=0, tol=30):
    """Full width, about the right height, and (when `virtual`, the whole
    tall content of the scrolling panel, pw wide, is given) the screen columns
    x0..x0+pw an exact, top-aligned copy of it."""
    h, w = out.shape[:2]
    ok, why = True, ""
    if w != SW:
        ok, why = False, f"width {w}, should be the full {SW}"
    elif abs(h - want_h) > tol:
        ok, why = False, f"height {h}, expected about {want_h}"
    elif virtual is not None:
        n = min(h, virtual.shape[0])
        if not np.array_equal(out[:n, x0:x0 + pw], virtual[:n, :pw]):
            ok, why = False, "scrolling part is not an exact crop of the page"
    print(f"{name:52s} -> {w}x{h}  " + ("OK" if ok else "FAIL: " + why))
    return ok


results = []

# ------------------------------------------------------------------ layouts
PAGE_H = 3000
page = text_block(PAGE_H, 960, 1, gap_rows=[(1200, 1500)])
toolbar, taskbar = noise(90, SW, 2), noise(40, SW, 3)
VIEW = SH - 90 - 40
BOTTOM = PAGE_H - VIEW                   # scroll position of the very end


def screen_a(y, sticky=False):
    """Whole web page in a browser: toolbar, taskbar, scrollbar."""
    s = np.zeros((SH, SW, 3), np.uint8)
    s[:90] = toolbar
    s[90:90 + VIEW, :960] = page[y:y + VIEW]
    if sticky:
        s[90:130, :960] = noise(40, 960, 11)             # sticky header
    s[90:90 + VIEW, 960:] = 200                          # scrollbar track
    t = 90 + int(y / PAGE_H * VIEW)
    s[t:t + 60, 984:996] = 90                            # moving thumb
    s[SH - 40:] = taskbar
    return s


# A) the basics
ys = [0, 250, 500, 750, 1000, 1250, 1500, 1750, 2000, 2250, 2370]
results.append(check("A whole page, even steps",
                     stitch([screen_a(y) for y in ys], (500, 300)),
                     2370 + VIEW, page, 0, 960))
ys = [0, 300, 470, 900, 1150, 1260, 1700, 2100, 2370]
results.append(check("A whole page, uneven steps",
                     stitch([screen_a(y) for y in ys], (500, 300)),
                     2370 + VIEW, page, 0, 960))

# D) Where you click must never change the result. Includes the scrollbar,
#    the very edges, the browser toolbar and the taskbar.
ys = [0, 450, 900, 1350, 1800, 2250]
for hint in [(500, 300), (5, 300), (990, 300), (975, 100), (500, 5),
             (500, 690), (980, 500), (20, 650)]:
    results.append(check(f"D click at {hint}, stops short of the end",
                         stitch([screen_a(y) for y in ys], hint),
                         ys[-1] + VIEW, page, 0, 960))

# E) Scrolled all the way to the bottom: the last step is usually shorter
#    than the others, down to a few pixels.
for last in (1, 5, 30, 120, 300, 450):
    ys = [0, 450, 900, 1350, 1800, 2100, BOTTOM - last, BOTTOM]
    ys = sorted(set(y for y in ys if 0 <= y <= BOTTOM))
    results.append(check(f"E bottom reached, last step {last}px",
                         stitch([screen_a(y) for y in ys], (500, 300)),
                         PAGE_H, page, 0, 960))
ys = [0, 450, 900, 1350, 1800, 2100, 2400, BOTTOM]
for hint in [(5, 300), (990, 300), (500, 690)]:
    results.append(check(f"E bottom reached, click at {hint}",
                         stitch([screen_a(y) for y in ys], hint),
                         PAGE_H, page, 0, 960))

# F) Bottom reached with a sticky header, and with a blank footer
ys = [0, 450, 900, 1350, 1800, 2100, 2400, BOTTOM]
results.append(check("F bottom reached, sticky header",
                     stitch([screen_a(y, True) for y in ys], (500, 300)),
                     PAGE_H - 40))     # the header hides the first 40 rows
page_blank = text_block(PAGE_H, 960, 1, gap_rows=[(1200, 1500),
                                                  (PAGE_H - 420, PAGE_H)])
page_keep = page
page = page_blank
ys = [0, 450, 900, 1350, 1800, 2100, 2400, BOTTOM]
results.append(check("F bottom reached, blank footer",
                     stitch([screen_a(y) for y in ys], (500, 300)),
                     PAGE_H, page_blank, 0, 960))
page = page_keep


# B) A sidebar scrolls, the main area beside it does not.
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
out = stitch([screen_b(y) for y in ys], (100, 300))
ok = check("B sidebar scrolls, main fixed (full width)", out,
           1280 + SIDE_VIEW, side[:1280 + SIDE_VIEW], 10, 230)
if ok:       # the fixed main area appears once, not once per scroll
    below = out[SIDE_VIEW + 40:, 260:1000]
    ok = bool((below > 200).mean() > 0.99)
    print(f"{'B fixed main area is not repeated':52s} -> "
          + ("OK" if ok else "FAIL: copies of it down the page"))
results.append(ok)

# C) The main area scrolls, the sidebar beside it does not.
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
out = stitch([screen_c(y, i) for i, y in enumerate(ys)], (600, 400))
ok = check("C main scrolls, sidebar fixed (full width)", out,
           2010 + VIEW - 40, long_main[:2010 + VIEW - 40], 260, 740)
if ok:
    below = out[VIEW:, 10:240]
    ok = bool((below > 200).mean() > 0.99)
    print(f"{'C fixed sidebar is not repeated':52s} -> "
          + ("OK" if ok else "FAIL: copies of it down the page"))
results.append(ok)

# G) A busier page: solid bands, photos, a dark footer, a floating button, and
#    flat stretches where the overlap holds nothing to match. Click anywhere.
PW = SW - 17


def busy_page(seed, ph, footer=300):
    r = np.random.default_rng(seed)
    pg = np.full((ph, PW, 3), 255, np.uint8)
    y = 0
    while y < ph - footer:
        kind = int(r.integers(0, 4))
        h = min(int(r.integers(120, 520)), ph - footer - y)
        if kind == 0:                                    # coloured band + text
            pg[y:y + h] = r.integers(30, 220, 3)
            for k in range(0, h - 40, 36):
                n, x0 = int(r.integers(100, 600)), int(r.integers(50, 300))
                pg[y + k + 10:y + k + 26, x0:x0 + n] = 255
        elif kind == 1:                                  # text
            for k in range(0, h - 28, 28):
                n = int(r.integers(100, PW - 200))
                pg[y + k + 6:y + k + 18, 100:100 + n] = r.integers(0, 140, (12, n, 3))
        elif kind == 2:                                  # photo
            pg[y + 10:y + h - 10, 150:PW - 150] = r.integers(0, 255, (h - 20, PW - 300, 3))
        y += h                                           # kind 3: blank
    pg[ph - footer:] = (30, 30, 40)                      # dark footer
    for k in range(20, footer - 30, 40):
        pg[ph - footer + k:ph - footer + k + 14, 100:100 + int(r.integers(100, 500))] = 200
    return pg


def screen_g(pg, y, tb, tk):
    ph = pg.shape[0]
    s = np.zeros((800, SW + 200, 3), np.uint8)
    s[:100] = tb
    s[100:100 + 652, :PW] = pg[y:y + 652]
    s[100:100 + 652, PW:] = 235
    t = 100 + int(y / ph * 572)
    s[t:t + 80, PW + 3:PW + 14] = 140
    s[100 + 652 - 90:100 + 652 - 30, PW - 90:PW - 30] = (0, 120, 255)  # button
    s[800 - 48:] = tk
    return s


tb, tk = noise(100, SW + 200, 21), noise(48, SW + 200, 22)
rng = np.random.default_rng(0)
SW_G = SW + 200
for seed, step in [(0, 300), (1, 400), (3, 500), (6, 300), (9, 400)]:
    ph = int(rng.integers(2400, 4200))
    pg = busy_page(seed, ph)
    bottom = ph - 652
    for reach in ("short of the end", "bottom"):
        ys = list(range(0, bottom, step)) + ([bottom] if reach == "bottom" else [])
        hint = (int(rng.integers(0, SW_G)), int(rng.integers(100, 752)))
        out = stitch([screen_g(pg, y, tb, tk) for y in ys], hint)
        h, w = out.shape[:2]
        want = ys[-1] + 652
        # The last step may overshoot by blank space when it is short and
        # the overlap is flat; it must never be narrower or lose content.
        ok = (w == SW_G) and (-30 <= h - want <= step)
        print(f"{'G busy page %d, %s, click %s' % (seed, reach, hint):52s} -> "
              f"{w}x{h}  " + ("OK" if ok else f"FAIL: expected {SW_G}x~{want}"))
        results.append(ok)

# H) Two scrolls: a page with a list of its own in the middle (an admin page
#    with a long table). The wheel scrolls the list first; once the list is
#    at its end the page scrolls. The result is the whole page with the list
#    opened out to its full length.
NP_H, WIN_Y, WIN_H, WIN_X, WIN_W = 1800, 150, 300, 30, 900
inner_list = text_block(1400, WIN_W, 31)
page_h = text_block(NP_H, 960, 30, gap_rows=[(WIN_Y - 28, WIN_Y + WIN_H + 28)])
BOTTOM_H = NP_H - VIEW


def screen_h(y, z):
    pg = page_h.copy()
    pg[WIN_Y:WIN_Y + WIN_H, WIN_X:WIN_X + WIN_W] = inner_list[z:z + WIN_H]
    t = WIN_Y + int(z / (1400 - WIN_H) * (WIN_H - 90))
    pg[t:t + 90, 940:950] = 90                           # list's scroll thumb
    s = np.zeros((SH, SW, 3), np.uint8)
    s[:90] = toolbar
    s[90:90 + VIEW, :960] = pg[y:y + VIEW]
    s[90:90 + VIEW, 960:] = 200
    s[SH - 40:] = taskbar
    return s


def check_nested(name, out, page_rows, below=True):
    """Page above the list, then the list's whole content, then (when the page
    was scrolled past the list) the page below it."""
    want = page_rows + (1400 - WIN_H)
    h, w = out.shape[:2]
    ok, why = True, ""
    if w != SW:
        ok, why = False, f"width {w}, should be the full {SW}"
    elif abs(h - want) > 30:
        ok, why = False, f"height {h}, expected about {want}"
    elif not np.array_equal(out[:WIN_Y - 40, :960], page_h[:WIN_Y - 40]):
        ok, why = False, "page above the list is wrong"
    elif not np.array_equal(out[WIN_Y:WIN_Y + 1400, WIN_X:WIN_X + WIN_W],
                            inner_list):
        ok, why = False, "the list's content is not complete and in order"
    elif below and not np.array_equal(
            out[WIN_Y + 1400 + 40:WIN_Y + 1400 + 300, :960],
            page_h[WIN_Y + WIN_H + 40:WIN_Y + WIN_H + 300]):
        ok, why = False, "page below the list is wrong"
    print(f"{name:52s} -> {w}x{h}  " + ("OK" if ok else "FAIL: " + why))
    return ok


# list first (page still at its top), then the page
zs = [0, 200, 400, 600, 800, 1000, 1100]
frames = [screen_h(0, z) for z in zs]
frames += [screen_h(y, 1100) for y in (300, 600, 900, 1200, BOTTOM_H)]
results.append(check_nested("H list scrolls, then the page", stitch(frames, (500, 300)),
                            NP_H))

# the same, clicking on the page's own margin
results.append(check_nested("H list, then page, click at the margin",
                            stitch(frames, (995, 300)), NP_H))

# page first (down to where the whole list shows), then the list
frames = [screen_h(y, 0) for y in (0, 50, 100)]
frames += [screen_h(100, z) for z in (200, 400, 600, 800, 1000, 1100)]
out = stitch(frames, (500, 300))
results.append(check_nested("H page scrolls, then the list",
                            out, 100 + VIEW, below=False))

# I) A form of near-identical blocks (a checklist editor): every block looks
#    the same except for its label, so a scroll distance one or two blocks too
#    short matches the repeated parts better than the real one. Joined with
#    the wrong distance, the later frames end up stacked on top of each other.
FW, FH = 1070, 600
cream = np.array([245, 240, 235], np.uint8)
f_rng = np.random.default_rng(5)
f_tab, f_task = noise(55, FW, 41), noise(22, FW, 42)
f_side = np.zeros((FH, 145, 3), np.uint8)
f_side[:] = (26, 90, 90)
for yy in range(80, 500, 22):
    f_side[yy:yy + 8, 20:20 + int(f_rng.integers(40, 100))] = 220
f_head = np.full((100, FW - 145, 3), cream, np.uint8)
f_head[20:30, 40:300], f_head[60:70, 40:500] = 60, 90
F_VIEW = FH - 155 - 22


def form_page(rows, pitch, seed=1):
    r = np.random.default_rng(seed)
    pg = np.empty((int(rows * pitch) + 200, FW - 145, 3), np.uint8)
    pg[:] = cream
    pg[:, 231:233] = 200                                 # the card's borders
    pg[:, 683:685] = 200
    for k in range(rows):
        y, x = int(round(100 + k * pitch)), 246
        n = int(r.integers(120, 330))                    # label: differs per block
        pg[y + 6:y + 18, x:x + n] = r.integers(0, 140, (12, n, 3))
        pg[y + 30:y + 40, x:x + 8] = 90
        pg[y + 30:y + 40, x + 16:x + 70] = 150
        for sx in (x, x + 140):                          # two selects: same every time
            pg[y + 48:y + 66, sx:sx + 128] = 255
            pg[y + 48:y + 50, sx:sx + 128] = 120
            pg[y + 64:y + 66, sx:sx + 128] = 120
            pg[y + 54:y + 60, sx + 8:sx + 60] = 100
    return pg


def form_screen(pg, y):
    s = np.zeros((FH, FW, 3), np.uint8)
    s[:55], s[:, :145], s[55:155, 145:] = f_tab, f_side, f_head
    s[155:155 + F_VIEW, 145:] = pg[y:y + F_VIEW]
    s[FH - 22:] = f_task
    return s


for pitch, step in [(93, 250), (93, 300), (90, 270), (111.5, 200)]:
    pg = form_page(40, pitch)
    ys = list(range(0, pg.shape[0] - F_VIEW, step)) + [pg.shape[0] - F_VIEW]
    out = stitch([form_screen(pg, y) for y in ys], (600, 300))
    n = pg.shape[0] - 200
    ok = (out.shape[1] == FW and abs(out.shape[0] - (pg.shape[0] + 100)) <= 30
          and np.array_equal(out[100:100 + n, 377:828], pg[:n, 232:683]))
    print(f"{'I form of repeated blocks, pitch %s, step %d' % (pitch, step):52s} -> "
          f"{out.shape[1]}x{out.shape[0]}  " + ("OK" if ok else "FAIL"))
    results.append(ok)

print("ALL OK" if all(results) else "SOME FAILED")
raise SystemExit(0 if all(results) else 1)
