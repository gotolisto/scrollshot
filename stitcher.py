"""Find what scrolled between screenshots and join it into one tall image.

Each frame is a full screenshot (H x W x 3, uint8) taken after a scroll.
Often only part of the screen scrolls: a sidebar, the main column of a web
app, or the page inside the browser. Everything else (browser toolbar,
taskbar, a fixed sidebar, sticky headers/footers, floating buttons) stays put.

For each pair of frames we work out:
  * how far the content moved (d pixels), measured over the WHOLE screen
    width, so it never depends on where the user happened to click, and
  * which rows moved (the scrolling panel's top and bottom edge).
The result is always exactly as wide as the screen; only its height depends
on how far the page was scrolled. Rows that never scrolled (browser toolbar,
taskbar) are left out.
"""
import numpy as np

PIXEL_THRESHOLD = 12        # per-channel difference that counts as "changed"
NO_OVERLAP_ERROR = 14.0     # mean gray error above this = frames don't overlap
PIX_EQ = 6.0                # gray difference that still counts as the same pixel
SHIFT_OK = 0.8              # share of pixels that must match after the shift
SAME_OK = 0.995             # share that must match unshifted to count as still
GAP = 48                    # quiet gap (px) still treated as the same area
MARGIN = 16                 # plain margin kept around the moving content
SIDE_BAND = 0.5             # a fixed sidebar's band covers this share of the panel
SIDE_SHARE = 0.7            # how often a fixed pixel must disagree with the scroll


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
STRIPS = 48                 # vertical strips used to vote on the scroll distance
CELL_EQ = 3.0               # brightness difference that still counts as equal
MIN_CELLS = 12              # fewest matching cells that prove an overlap
STRONG_CELLS = 250          # matching cells that settle a distance on their own
SHARP = 0.08                # a real shift beats its neighbours (d +/- 1) by this much


def _edge_cells(p, k=2):
    """Cells that differ from the one k rows above or below, i.e. cells that
    carry picture detail. Flat areas match any shift, so they must not vote."""
    e = np.zeros(p.shape, bool)
    if p.shape[0] > k:
        diff = np.abs(p[k:] - p[:-k]) > CELL_EQ
        e[k:] |= diff
        e[:-k] |= diff
    return e


def _pixel_detail(g):
    """Same idea at pixel level (every second column, to keep it fast)."""
    s = g[:, ::2]
    e = np.zeros(s.shape, bool)
    if s.shape[0] > 2:
        dy = np.abs(s[2:] - s[:-2]) > PIX_EQ
        e[2:] |= dy
        e[:-2] |= dy
    dx = np.abs(s[:, 1:] - s[:, :-1]) > PIX_EQ
    e[:, 1:] |= dx
    e[:, :-1] |= dx
    return e


def _prepare(img):
    g = _gray(img)
    p = _profile(g, STRIPS)
    return {"g": g, "p": p, "e": _edge_cells(p), "pe": _pixel_detail(g)}


def _pair(A, B):
    """B (the newer frame) with the map of what did not move since A."""
    B = dict(B)
    B["still"] = (np.abs(A["p"] - B["p"]) <= CELL_EQ) & (A["e"] | B["e"])
    B["pstill"] = ((np.abs(A["g"][:, ::2] - B["g"][:, ::2]) <= PIX_EQ)
                   & (A["pe"] | B["pe"]))
    return A, B


def _voting_cells(A, B, d):
    """Cells that may vote for shift d: they carry detail in both frames (the
    same content looks detailed wherever it is), and aren't part of something
    that stayed exactly where it was (toolbar, taskbar, sidebar)."""
    h = A["g"].shape[0]
    still = B["still"]
    return (A["e"][d:] & B["e"][:h - d]) & ~still[d:] & ~still[:h - d]


def shift_evidence(A, B):
    """For every possible scroll distance, how many cells of the new frame
    equal the cell that far lower in the old one. Every vertical strip of the
    screen votes, so it never depends on where the user clicked; flat areas
    and anything that did not move at all don't vote, so a toolbar or a
    fixed sidebar can't pull the answer off course.

    Returns (score, peak): score[d-1] is the number of matching cells and
    peak[d-1] says whether d stands out as a sharp peak. (Textured content
    also half-matches itself at a shift of a pixel or two; a real shift is
    exact and drops away on either side.)"""
    h = A["g"].shape[0]
    max_d = h - max(16, h // 12)
    pa, pb = A["p"], B["p"]
    score = np.zeros(max(max_d, 0), np.float64)
    total = np.zeros_like(score)
    for d in range(1, max_d + 1):
        info = _voting_cells(A, B, d)
        total[d - 1] = info.sum()
        score[d - 1] = ((np.abs(pa[d:] - pb[:h - d]) <= CELL_EQ) & info).sum()
    ratio = score / np.maximum(total, 1)
    left = np.concatenate([[0.0], ratio[:-1]])
    right = np.concatenate([ratio[1:], [0.0]])
    peak = (score >= MIN_CELLS) & (ratio - np.maximum(left, right) >= SHARP)
    return score, peak


def choose_shift(ev, usual):
    """Pick the scroll distance for one pair from its evidence.
    usual: the step most pairs of this capture agree on (every click scrolls
    by the same amount), or None. A different distance is only believed when
    its evidence is strong, as for the short last step at the bottom of a
    page. Returns None when the overlap holds nothing to match (a blank
    stretch, or a jump bigger than the screen)."""
    if ev is None:
        return None
    score, peak = ev
    hit = np.where(peak)[0]
    best = int(hit[np.argmax(score[hit])]) + 1 if len(hit) else None
    n_best = float(score[best - 1]) if best else 0.0
    if usual and 1 <= usual <= len(score) and peak[usual - 1] \
            and score[usual - 1] >= 0.5 * n_best:
        return int(usual)                      # fits the usual step
    if best and n_best >= STRONG_CELLS:
        return best                            # clearly a different step
    if usual:
        return None                            # too little to go on
    return best                                # None if nothing at all


# ------------------------------------------------------- moving rectangle
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


def pair_rect(A, B, d, hint=None):
    """Rows (top, bottom) of the panel that scrolled by d between two frames,
    plus the columns it was found in (used only to judge the rows)."""
    h, w = A["g"].shape
    A, B = _pair(A, B)
    pa, pb = A["p"], B["p"]
    match = (np.abs(pa[d:] - pb[:h - d]) <= CELL_EQ) & _voting_cells(A, B, d)
    hits = match.sum(axis=0)
    if hits.max() == 0:
        return None
    strips = np.where(hits >= max(1, 0.25 * hits.max()))[0]
    edges = np.linspace(0, w, pa.shape[1] + 1).astype(int)
    c0, c1 = int(edges[strips[0]]), int(edges[strips[-1] + 1])
    rowhit = match[:, strips].sum(axis=1)
    hy = None if hint is None else min(max(int(hint[1]), 0), h - d - 1)
    run = _pick_run(_close_gaps(rowhit > 0, GAP), rowhit, hy)
    if run is None:
        return None
    top, bottom = run[0], min(h, run[1] + d)

    # Tighten the rows (cut off a fixed header/footer) with the panel's own
    # columns; d is already known so it stays as measured.
    ga, gb = A["g"], B["g"]
    for _ in range(2):
        if bottom - top - d < 8:
            break
        cols = _moving_cols(ga, gb, d, top, bottom,
                            None if hint is None else hint[0])
        if cols:
            c0, c1 = cols
        rows = _moving_rows(ga, gb, d, c0, c1, hy)
        if rows:
            top, bottom = rows
    return top, bottom


# ---------------------------------------------------------------- stitch
def stitch(frames, hint=None):
    """Join consecutive frames into one tall image, always as wide as the
    screen. hint: (x, y) in frame pixels where the user clicked, if known;
    it only helps choose between two panels that scroll independently.

    A page with a scrolling list of its own (a long table in an admin page)
    is captured in two stages: the wheel moves the list first, then, once
    the list is at its end, the page. Those are joined by _stitch_nested; every
    other capture is one stage."""
    if len(frames) == 1:
        return frames[0]
    nested = _stitch_nested(frames, hint)
    if nested is not None:
        return nested
    return _stitch_stage(frames, hint)[0]


def _stitch_stage(frames, hint=None, rows=None):
    """One scrolling panel, scrolled through all the frames. Returns
    (image, (top, bottom), ys): the image, the rows of the screen that
    scrolled, and the image row at which each frame starts. (top, bottom) is
    None when nothing could be told to have scrolled. rows: the rows that
    scrolled, when they are already known."""
    h, w = frames[0].shape[:2]
    n = len(frames)
    same = [False] + [similar(frames[i - 1], frames[i]) for i in range(1, n)]

    # Pass 1: how far did each pair scroll, as far as its pictures can tell?
    evidence = [None] * n
    prev = _prepare(frames[0])
    for i in range(1, n):
        cur = _prepare(frames[i])
        if not same[i]:
            evidence[i] = shift_evidence(*_pair(prev, cur))
        prev = cur

    # Every click scrolls by the same amount, so most pairs agree. Add up
    # each pair's evidence per distance: the real step collects support from
    # every pair, while a look-alike (repeating text, a list) only fools one
    # or two of them. The last step is often a short one (the page ran out),
    # so prefer the pairs before it; fall back to all of them, and only then
    # to weaker evidence.
    pairs = [i for i in range(1, n) if evidence[i] is not None]
    usual = None
    for use, need in (([i for i in pairs if i < n - 1], STRONG_CELLS),
                      (pairs, STRONG_CELLS), (pairs, 3 * MIN_CELLS)):
        if not use:
            continue
        total = sum(evidence[i][0] * evidence[i][1] for i in use)
        if total.max() >= need:
            usual = int(np.argmax(total)) + 1
            break
    ds = [0]
    for i in range(1, n):
        ds.append(0 if same[i] else choose_shift(evidence[i], usual))
    # A stretch with nothing to match (a blank area) scrolled like its
    # neighbours did. If they differ take the larger: too small a step would
    # lose content, too large only adds blank space.
    fallback = usual or int(h * 0.6)
    for i in range(1, n):
        if ds[i] is None:
            before = next((ds[j] for j in range(i - 1, 0, -1) if ds[j]), None)
            after = next((ds[j] for j in range(i + 1, n) if ds[j]), None)
            near = [d for d in (before, after) if d]
            ds[i] = max(near) if near else fallback
    if not any(ds):
        return frames[-1], None, None

    # Pass 2: which rows scrolled? One band for the whole capture, taken
    # across the full screen width.
    rects = []
    prev = _prepare(frames[0])
    for i in range(1, n):
        cur = _prepare(frames[i])
        if rows is None and ds[i] and ds[i] < h and not same[i]:
            r = pair_rect(prev, cur, ds[i], hint)
            if r:
                rects.append(r)
        prev = cur
    if rows is not None:
        top, bottom = rows
    elif rects:
        top = min(r[0] for r in rects)
        bottom = max(r[1] for r in rects)
    else:
        top, bottom = 0, h
    grays = [_gray(f) for f in frames]
    return (compose(frames, grays, (top, bottom, 0, w), ds), (top, bottom),
            np.cumsum(ds))


# ----------------------------------------------------- a list inside a page
NEST_MARGIN = 16            # the list sits at least this far inside the panel
NEST_SLACK = 24             # rows of difference that still count as the same edge


def _nested_split(frames):
    """Tell a capture made of two stages from an ordinary one. In the first
    stage only a part inside the page moved (a list with its own scrollbar),
    in the other the whole page moved, or the other way round. Returns
    (inner_frames, outer_frames, inner_first, rows), where rows is the part
    of the screen the list scrolled in, or None when the capture is
    not that: every pair must clearly belong to one of the two stages, with
    the list stage a run at the start or at the end, and the list well inside
    the part that scrolls with the page (page content above and below it).
    Anything less clear is left to the ordinary single stage."""
    n = len(frames)
    bounds = {}
    for i in range(1, n):
        if not similar(frames[i - 1], frames[i]):
            b = change_bounds(frames[i - 1], frames[i])
            if b:
                bounds[i] = b
    pairs = sorted(bounds)
    if len(pairs) < 2:
        return None
    big = max(pairs, key=lambda i: bounds[i][1] - bounds[i][0])
    ot, ob = bounds[big][0], bounds[big][1]
    kinds = []
    for i in pairs:
        t, b = bounds[i][0], bounds[i][1]
        if abs(t - ot) <= NEST_SLACK and abs(b - ob) <= NEST_SLACK:
            kinds.append("outer")
        elif t >= ot + NEST_MARGIN and b <= ob - NEST_MARGIN:
            kinds.append("inner")
        else:
            return None
    if "inner" not in kinds or "outer" not in kinds:
        return None
    k = kinds.count("inner")
    mine = [i for i, kind in zip(pairs, kinds) if kind == "inner"]
    rows = (min(bounds[i][0] for i in mine), max(bounds[i][1] for i in mine))
    if kinds[:k] == ["inner"] * k:
        p, q = pairs[k - 1], pairs[k]          # last list pair, first page pair
        return frames[:p + 1], frames[q - 1:], True, rows
    if kinds[-k:] == ["inner"] * k:
        e, q = pairs[-k - 1], pairs[-k]        # last page pair, first list pair
        return frames[q - 1:], frames[:e + 1], False, rows
    return None


def _stitch_nested(frames, hint):
    """A page with a list of its own. The page is joined from the stage in
    which it scrolled; the list from the stage in which it scrolled; and the
    list's whole content replaces the part of the page where the list sits,
    which pushes everything below it further down. None when the capture is
    not of that kind or the stages don't fit together (the caller then joins
    it as an ordinary single stage)."""
    split = _nested_split(frames)
    if split is None:
        return None
    inner, outer, inner_first, rows = split
    if len(inner) < 2 or len(outer) < 2:
        return None
    page, prect, pys = _stitch_stage(outer, hint)
    listing, lrect, _ = _stitch_stage(inner, hint, rows)
    if prect is None or lrect is None:
        return None
    (pt, pb), (lt, lb) = prect, lrect
    if not (pt < lt and lb < pb):
        return None                            # the list isn't inside the page
    # Where the list sits in the page image: in the frame both stages share.
    shared = 0 if inner_first else len(outer) - 1
    r0 = int(pys[shared]) + (lt - pt)
    r1 = r0 + (lb - lt)
    if r0 < 0 or r1 > page.shape[0] or listing.shape[0] < r1 - r0:
        return None
    return np.concatenate([page[:r0], listing, page[r1:]], axis=0)


def _fixed_mask(grays, ds, top, bottom, c0, c1, share=1.0, refine=True):
    """Screen pixels inside the rectangle that stayed put in every frame
    while the content scrolled underneath (floating buttons, sticky bars).
    Plain background also looks the same in every frame, but it is also
    consistent with the scroll, so it isn't counted. Only solid areas count,
    not single pixels that match by chance. `share`: how often a pixel must
    disagree with the scroll to count (all the time, by default). `refine`:
    a second look that leaves out comparisons against what is already known
    to be fixed; right for small objects, but it would drop the pixels of a
    large fixed block, whose every comparison is against itself."""
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
        return (total > 0) & (bad >= share * total)

    first = solid(same & unexplained(np.zeros(same.shape, bool)))
    if not refine:
        return first
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


def _longest_run(mask, gap):
    """Length of the longest stretch of True values, where pauses of up to
    `gap` False values don't break it."""
    runs = _runs(_close_gaps(mask, gap))
    return max((e - s for s, e in runs), default=0)


def _fixed_columns(fixed):
    """Columns that belong to something fixed all the way down, like a
    sidebar beside the scrolling part. `fixed` marks pixels that are the same
    in every frame and that the scroll cannot explain (scrolling content
    that merely repeats, like rows of a list, is explained by the scroll).
    A sidebar leaves a continuous band of them covering at least half the
    panel; a floating button, or a sticky header plus a sticky footer, never
    makes a band that long. Lines of text end raggedly, so the columns that
    carry a full band seed the block and it grows through the neighbouring
    columns that hold fixed pixels."""
    h, w = fixed.shape
    count = fixed.sum(axis=0)
    side = np.zeros(w, bool)
    for x in np.where(count >= max(4, h * 0.05))[0]:
        side[x] = _longest_run(fixed[:, x], GAP) >= h * SIDE_BAND
    for x in range(1, w):
        if side[x - 1] and count[x] >= 3:
            side[x] = True
    for x in range(w - 2, -1, -1):
        if side[x + 1] and count[x] >= 3:
            side[x] = True
    return side


def compose(frames, grays, rect, ds):
    """Paint every frame onto one tall canvas at its scroll position.
    Pixels that stayed fixed while the content moved are skipped wherever
    another frame shows the real content there. Columns that are fixed all
    the way down (a sidebar next to the scrolling part) are painted once, from
    the first frame, with their background carried on below."""
    top, bottom, c0, c1 = rect
    h = bottom - top
    ys = np.cumsum(ds)                        # canvas row of each frame
    canvas = np.zeros((int(ys[-1]) + h, c1 - c0, 3), np.uint8)
    filled = np.zeros(canvas.shape[:2], bool)
    fixed = _fixed_mask(grays, ds, top, bottom, c0, c1)
    side = _fixed_columns(_fixed_mask(grays, ds, top, bottom, c0, c1,
                                      share=SIDE_SHARE, refine=False))
    keep = ~fixed
    if side.any():                            # fixed columns: first frame only
        y0 = int(ys[0])
        canvas[y0:y0 + h][:, side] = frames[0][top:bottom, c0:c1][:, side]
        filled[y0:y0 + h][:, side] = True
    # Pass 1: real (moving) content. Pass 2: whatever is still empty.
    for pass_ in (1, 2):
        for i, f in enumerate(frames):
            y = int(ys[i])
            src = f[top:bottom, c0:c1]
            want = ~filled[y:y + h]
            if pass_ == 1:
                want &= keep
            if i > 0 and side.any():
                want &= ~side[None, :]
            canvas[y:y + h][want] = src[want]
            filled[y:y + h] |= want
    if side.any() and not filled.all():
        bg = np.median(frames[0][top:bottom, c0:c1], axis=0).astype(np.uint8)
        empty = ~filled
        canvas[empty] = np.broadcast_to(bg, canvas.shape)[empty]
    return canvas
