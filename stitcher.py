"""Find what scrolled between screenshots and join it into one tall image.

Each frame is a full screenshot (H x W x 3, uint8) taken after a scroll.
Often only part of the screen scrolls: a sidebar, the main column of a web
app, or the page inside the browser. Everything else (browser toolbar,
taskbar, a fixed sidebar, sticky headers/footers, floating buttons) stays put.

For each pair of frames we work out:
  * how far the content moved (d pixels), and
  * the rectangle that moved: the columns and rows whose pixels in the new
    frame equal the old frame shifted up by d.
Then only that rectangle is joined, so nothing that stayed still is repeated.
"""
import numpy as np

PIXEL_THRESHOLD = 12        # per-channel difference that counts as "changed"
NO_OVERLAP_ERROR = 14.0     # mean gray error above this = frames don't overlap
PIX_EQ = 6.0                # gray difference that still counts as the same pixel
SHIFT_OK = 0.8              # share of pixels that must match after the shift
SAME_OK = 0.995             # share that must match unshifted to count as still
GAP = 48                    # quiet gap (px) still treated as the same area
MARGIN = 16                 # plain margin kept around the moving content


# ---------------------------------------------------------------- basics
def _changed_mask(a, b):
    return (np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2)
            > PIXEL_THRESHOLD)


def change_bounds(a, b, min_frac=0.002):
    """(top, bottom, left, right) of the area that differs, or None if the
    frames are effectively identical (a blinking text caret doesn't count)."""
    m = _changed_mask(a, b)
    h, w = m.shape
    rows = np.where(m.sum(axis=1) > max(2, w * min_frac))[0]
    if len(rows) == 0:
        return None
    cols = np.where(m.sum(axis=0) > max(2, h * min_frac))[0]
    if len(cols) == 0:
        return None
    return int(rows[0]), int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1


def similar(a, b):
    """Fast 'nothing moved' check on a downsampled copy."""
    return change_bounds(a[::4, ::4], b[::4, ::4]) is None


def _gray(img):
    return img.astype(np.float32).mean(axis=2)


def _profile(gray, strips=32):
    """Per-row signature: mean brightness of N vertical strips."""
    w = gray.shape[1]
    strips = max(1, min(strips, w))
    edges = np.linspace(0, w, strips + 1).astype(int)
    sums = np.add.reduceat(gray, edges[:-1], axis=1)
    return sums / np.diff(edges)


def _runs(mask):
    """[(start, end), ...] for each run of True values."""
    m = np.concatenate([[0], mask.astype(np.int8), [0]])
    d = np.diff(m)
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))


def _close_gaps(mask, gap):
    out = mask.copy()
    for s, e in _runs(~mask):
        if s > 0 and e < len(mask) and e - s <= gap:
            out[s:e] = True
    return out


def _pick_run(mask, weight, hint=None):
    runs = [r for r in _runs(mask) if weight[r[0]:r[1]].sum() > 0]
    if not runs:
        return None
    if hint is not None:
        for s, e in runs:
            if s <= hint < e:
                return int(s), int(e)
    s, e = max(runs, key=lambda r: weight[r[0]:r[1]].sum())
    return int(s), int(e)


# ---------------------------------------------------------------- offset
def _trimmed(row_errors, keep=0.8):
    n = max(1, int(len(row_errors) * keep))
    return float(np.partition(row_errors, n - 1)[:n].mean())


def find_offset_gray(gp, gc, top, bottom, c0, c1, expected=None):
    """How many pixels the content inside the rectangle moved up between
    two gray frames. Returns (offset, error)."""
    h = bottom - top
    if h < 4 or c1 - c0 < 4:
        return 0, 0.0
    gp = gp[top:bottom, c0:c1]
    gc = gc[top:bottom, c0:c1]
    pp, pc = _profile(gp), _profile(gc)

    min_overlap = max(16, h // 12)
    max_d = h - min_overlap
    if max_d < 1:
        return h, 0.0

    errs = np.empty(max_d, np.float32)
    errs_t = np.empty(max_d, np.float32)
    for d in range(1, max_d + 1):
        row_err = np.abs(pp[d:] - pc[:h - d]).mean(axis=1)
        errs[d - 1] = row_err.mean()
        errs_t[d - 1] = _trimmed(row_err)

    cand = set((np.argsort(errs)[:10] + 1).tolist())
    cand |= set((np.argsort(errs_t)[:5] + 1).tolist())
    if expected and 1 <= expected <= max_d:
        cand.add(int(expected))
    scored = []
    for d in cand:
        d = int(d)
        row_err = np.abs(gp[d:, ::2] - gc[:h - d, ::2]).mean(axis=1)
        scored.append((float(row_err[::2].mean()), d, _trimmed(row_err)))
    scored.sort()
    best_e, best_d, best_t = scored[0]

    # Exact ties (blank stretches, repeating lists): prefer last distance.
    if expected:
        close = [s for s in scored if s[0] <= best_e + max(0.05, best_e * 0.05)]
        best_e, best_d, best_t = min(close, key=lambda s: abs(s[1] - expected))

    # "No overlap" is judged on the best-matching 80% of rows, so a sticky
    # bar or changing label inside the box doesn't fake it.
    best_e = best_t
    if best_e > NO_OVERLAP_ERROR:
        return h, best_e            # no overlap: scrolled more than a screen
    return best_d, best_e


def find_offset(prev, cur, top, bottom, c0, c1, expected=None):
    return find_offset_gray(_gray(prev), _gray(cur), top, bottom, c0, c1,
                            expected)


# ------------------------------------------------------- moving rectangle
def _initial_rect(ga, gb, hint):
    """Rough box to measure the scroll in: a narrow strip around the point
    where the user scrolled (that is where the moving panel is), or else
    the biggest changed area."""
    ch = np.abs(ga - gb) > PIXEL_THRESHOLD
    h, w = ch.shape
    if hint is not None:
        hx = min(max(int(hint[0]), 0), w - 1)
        c0, c1 = max(0, hx - 40), min(w, hx + 40)
        rowc = ch[:, c0:c1].sum(axis=1)
        busy = _close_gaps(rowc > max(2, (c1 - c0) * 0.02), GAP)
        rr = _pick_run(busy, rowc, int(hint[1]))
        if rr and rr[1] - rr[0] > 16:
            return rr[0], rr[1], c0, c1
    colc = ch.sum(axis=0)
    cr = _pick_run(_close_gaps(colc > max(2, h * 0.01), GAP), colc,
                   None if hint is None else hint[0])
    if cr is None:
        return None
    rowc = ch[:, cr[0]:cr[1]].sum(axis=1)
    rr = _pick_run(_close_gaps(rowc > max(2, (cr[1] - cr[0]) * 0.01), GAP),
                   rowc, None if hint is None else hint[1])
    if rr is None:
        return None
    return rr[0], rr[1], cr[0], cr[1]


def _trim(run, moving, limit, edge_strip=0, changed=None):
    """Keep the panel's full width, but cut off a scrollbar at either edge:
    a narrow moving strip that changes in one or two solid blocks (the
    thumb), where text would change in many separate lines."""
    s, e = run
    idx = np.where(moving[s:e])[0]
    if len(idx) == 0 or not edge_strip or changed is None:
        return run

    def thumb_like(group):
        if group[-1] - group[0] >= edge_strip:
            return False
        rows = changed[:, s + group[0]:s + group[-1] + 1].any(axis=1)
        return len(_runs(rows)) <= 2

    groups = np.split(idx, np.where(np.diff(idx) > 4)[0] + 1)
    if len(groups) > 1 and thumb_like(groups[-1]):
        groups.pop()
        e = min(e, s + int(groups[-1][-1]) + 1 + MARGIN, limit)
    if len(groups) > 1 and thumb_like(groups[0]):
        groups.pop(0)
        s = max(s, s + int(groups[0][0]) - MARGIN)
    return s, e


def _moving_cols(ga, gb, d, top, bottom, hint):
    """Columns of the scrolling panel: from the first to the last column
    that clearly slid with the scroll (everything in between belongs to
    the panel, e.g. a floating button over it), widened through completely
    plain columns at either side. Anything else at the sides - fixed text,
    a scrollbar thumb - ends the panel."""
    if bottom - top - d < 8:
        return None
    shifted_bad = (np.abs(ga[top + d:bottom] - gb[top:bottom - d]) >= PIX_EQ)
    still_bad = (np.abs(ga[top:bottom - d] - gb[top:bottom - d]) >= PIX_EQ)
    n = bottom - top - d
    sb, zb = shifted_bad.sum(axis=0), still_bad.sum(axis=0)
    tiny = max(2, n * (1 - SAME_OK))
    moving = (zb > tiny) & (sb * 2 < zb) & (sb <= n * (1 - SHIFT_OK))
    # Plain = a single colour from the top to the bottom of the panel in
    # both frames (background, margins, an empty scrollbar track).
    plain = ((np.ptp(ga[top:bottom], axis=0) < PIX_EQ)
             & (np.ptp(gb[top:bottom], axis=0) < PIX_EQ))
    w = ga.shape[1]

    # Moving columns, grouped (small gaps closed); pick the group under the
    # click (or the biggest), merging neighbours separated only by columns
    # that aren't clearly fixed.
    idx = np.where(moving)[0]
    if len(idx) == 0:
        return None
    groups = np.split(idx, np.where(np.diff(idx) > 4)[0] + 1)
    changed = np.abs(ga[top:bottom] - gb[top:bottom]) >= PIX_EQ

    def thumb_like(g):
        if g[-1] - g[0] >= 20:
            return False
        rows = changed[:, g[0]:g[-1] + 1].any(axis=1)
        return len(_runs(rows)) <= 2

    groups = [g for g in groups if not thumb_like(g)] or groups
    fixedish = ~plain & ~moving & (sb > tiny) & (zb <= tiny)

    def span_ok(a, b):
        """Can groups ending at a and starting at b be joined? Only if the
        columns between hold no clearly fixed content spanning the panel."""
        between = fixedish[a:b]
        return not between.any() or (b - a) <= 40

    merged = [[groups[0][0], groups[0][-1]]]
    for g in groups[1:]:
        if span_ok(merged[-1][1] + 1, g[0]):
            merged[-1][1] = g[-1]
        else:
            merged.append([g[0], g[-1]])
    if hint is not None:
        pick = min(merged, key=lambda m: 0 if m[0] <= hint <= m[1]
                   else min(abs(hint - m[0]), abs(hint - m[1])))
    else:
        pick = max(merged, key=lambda m: moving[m[0]:m[1] + 1].sum())
    c0, c1 = pick[0], pick[1] + 1
    m0, m1 = c0, c1
    # Widen through plain columns of the panel's own background colour
    # (not on past a window border into the desktop).
    colour = ga[top:bottom].mean(axis=0)
    if c0 > 0 and plain[c0 - 1]:
        bg = colour[c0 - 1]
        while c0 > 0 and plain[c0 - 1] and abs(colour[c0 - 1] - bg) < PIX_EQ:
            c0 -= 1
    if c1 < w and plain[c1]:
        bg = colour[c1]
        while c1 < w and plain[c1] and abs(colour[c1] - bg) < PIX_EQ:
            c1 += 1
    # Keep at least a small margin, even across a floating button: allowed
    # unless the column holds fixed content through much of the panel.
    first = np.argmax(shifted_bad, axis=0)
    last = n - 1 - np.argmax(shifted_bad[::-1], axis=0)
    spread = np.where(sb > 0, last - first + 1, 0)
    fixed = (zb <= tiny) & (sb > tiny) & (spread >= n * 0.25)
    while c0 > 0 and m0 - c0 < MARGIN and not fixed[c0 - 1]:
        c0 -= 1
    while c1 < w and c1 - m1 < MARGIN and not fixed[c1]:
        c1 += 1
    return int(c0), int(c1)


def _moving_rows(ga, gb, d, c0, c1, hint):
    """Rows (top, bottom) of the moving area. Row y of the new frame belongs
    to the overlap when it equals row y+d of the old frame; the panel then
    reaches d rows further down. The bottom edge is the last row that
    verifiably moved, so a fixed footer below is never included."""
    h = ga.shape[0]
    if d <= 0 or d >= h:
        return None
    a, b = ga[:, c0:c1], gb[:, c0:c1]
    e = (np.abs(b[:h - d] - a[d:]) < PIX_EQ).mean(axis=1) >= SHIFT_OK
    still = (np.abs(a - b) < PIX_EQ).mean(axis=1) >= SAME_OK
    moving = e & ~still[d:]              # old row y+d really changed
    # A floating button can interrupt a few rows; don't let it split the panel.
    run = _pick_run(_close_gaps(e, GAP), moving.astype(np.int32),
                    None if hint is None else min(max(hint - d, 0), h - d - 1))
    if run is None:
        return None
    top, end = run
    idx = np.where(moving[top:end])[0]
    return top, top + int(idx[-1]) + 1 + d


def analyze_pair(ga, gb, hint=None, expected=None):
    """Return dict(top, bottom, c0, c1, d) for what scrolled, or None."""
    r = _initial_rect(ga, gb, hint)
    if r is None:
        return None
    top, bottom, c0, c1 = r
    d, err = find_offset_gray(ga, gb, top, bottom, c0, c1, expected)
    for _ in range(3):
        if err > NO_OVERLAP_ERROR or d <= 0 or d >= bottom - top:
            break
        cols = _moving_cols(ga, gb, d, top, bottom,
                            None if hint is None else hint[0])
        if cols:
            c0, c1 = cols
        rows = _moving_rows(ga, gb, d, c0, c1,
                            None if hint is None else hint[1])
        if rows:
            top, bottom = rows
        new = find_offset_gray(ga, gb, top, bottom, c0, c1, expected)
        if new[0] == d:
            break
        d, err = new
    return {"top": top, "bottom": bottom, "c0": c0, "c1": c1, "d": d}


# ---------------------------------------------------------------- stitch
def stitch(frames, hint=None):
    """Join the scrolled area of consecutive frames into one image.
    hint: (x, y) in frame pixels where the user scrolled, if known."""
    if len(frames) == 1:
        return frames[0]
    grays = [_gray(f) for f in frames]
    found = []
    expected = None
    for i in range(1, len(frames)):
        p = analyze_pair(grays[i - 1], grays[i], hint, expected)
        if p:
            found.append(p)
            expected = p["d"]
    if not found:
        return frames[-1]

    # One rectangle for the whole capture: every column that moved in any
    # pair, from the highest start to the lowest verified end.
    c0 = min(p["c0"] for p in found)
    c1 = max(p["c1"] for p in found)
    top = min(p["top"] for p in found)
    bottom = max(p["bottom"] for p in found)

    ds = [0]
    expected = None
    for i in range(1, len(frames)):
        d, _ = find_offset_gray(grays[i - 1], grays[i], top, bottom, c0, c1,
                                expected)
        ds.append(max(0, d))
        if d > 0:
            expected = d
    return compose(frames, grays, (top, bottom, c0, c1), ds)


def _fixed_mask(grays, ds, top, bottom, c0, c1):
    """Screen pixels inside the rectangle that stayed put in every frame
    while the content scrolled underneath (floating buttons, sticky bars).
    Plain background also looks the same in every frame, but it is also
    consistent with the scroll, so it isn't counted. Only solid areas count,
    not single pixels that match by chance."""
    h = bottom - top
    cut = [g[top:bottom, c0:c1] for g in grays]
    same = np.ones(cut[0].shape, bool)
    for c in cut[1:]:
        same &= np.abs(c - cut[0]) < PIX_EQ

    def solid(overlay):
        core = _box_frac(overlay, 9) >= 0.6    # solid fixed objects
        near = _box_frac(core, 11) > 0         # ...and their edges
        return core | (same & near)

    def unexplained(ignore):
        """Pixels that never match where the scroll says they came from or
        went to. Real content matches in at least one other frame; a fixed
        object never does. Comparisons against an already-known fixed object
        are skipped."""
        total = np.zeros(same.shape, np.int16)
        bad = np.zeros(same.shape, np.int16)
        for i in range(1, len(cut)):
            d = int(ds[i])
            if not 0 < d < h:
                continue
            diff = np.abs(cut[i][:h - d] - cut[i - 1][d:]) >= PIX_EQ
            # cur[y] vs prev[y + d]
            ok = ~ignore[d:]
            total[:h - d] += ok
            bad[:h - d] += diff & ok
            # prev[y] vs cur[y - d]
            ok = ~ignore[:h - d]
            total[d:] += ok
            bad[d:] += diff & ok
        return (total > 0) & (bad == total)

    first = solid(same & unexplained(np.zeros(same.shape, bool)))
    return solid(same & unexplained(first))


def _box_frac(mask, k):
    """Share of True values in the k x k box around each pixel."""
    m = mask.astype(np.int32)
    ii = np.pad(m, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    h, w = mask.shape
    r = k // 2
    y0 = np.clip(np.arange(h) - r, 0, h)
    y1 = np.clip(np.arange(h) + r + 1, 0, h)
    x0 = np.clip(np.arange(w) - r, 0, w)
    x1 = np.clip(np.arange(w) + r + 1, 0, w)
    total = (ii[y1][:, x1] - ii[y0][:, x1] - ii[y1][:, x0] + ii[y0][:, x0])
    area = (y1 - y0)[:, None] * (x1 - x0)[None, :]
    return total / area


def compose(frames, grays, rect, ds):
    """Paint every frame onto one tall canvas at its scroll position.
    Pixels that stayed fixed while the content moved are skipped wherever
    another frame shows the real content there."""
    top, bottom, c0, c1 = rect
    h = bottom - top
    ys = np.cumsum(ds)                        # canvas row of each frame
    canvas = np.zeros((int(ys[-1]) + h, c1 - c0, 3), np.uint8)
    filled = np.zeros(canvas.shape[:2], bool)
    fixed = _fixed_mask(grays, ds, top, bottom, c0, c1)
    keep = [~fixed] * len(frames)
    # Pass 1: real (moving) content. Pass 2: whatever is still empty.
    for pass_ in (1, 2):
        for i, f in enumerate(frames):
            y = int(ys[i])
            src = f[top:bottom, c0:c1]
            want = ~filled[y:y + h]
            if pass_ == 1:
                want &= keep[i]
            canvas[y:y + h][want] = src[want]
            filled[y:y + h] |= want
    return canvas
