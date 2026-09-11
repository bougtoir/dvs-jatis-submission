#!/usr/bin/env python3
"""
Event-driven (frame-free) noise discriminants for DVS event streams.

Frame-based denoisers voxelise the stream into a (time bin x height x width)
grid and therefore pay a cost set by the sensor array and the temporal
discretisation, irrespective of how many events actually occurred.  Space
surveillance streams are extremely sparse -- a transiting satellite occupies a
few pixels out of the full array -- so the grid is almost entirely empty.  The
discriminants implemented here operate directly on the event sequence in
continuous time: their cost scales with the number of events, they never
allocate a dense spatiotemporal grid, and the waiting-time and recursive
variants are causal, so no bin boundary has to elapse before an event can be
classified.

All discriminants share the same background hypothesis as the frame-based
formulation: per-pixel Poisson noise arrivals whose rate is estimated from the
stream itself.  The rate estimator is likewise frame-free, taking the median
inter-arrival interval at each pixel (for an exponential interval distribution
the median equals ln 2 / lambda), which is robust to the short bursts produced
by a transit.

Discriminants
-------------
waiting_time_lr   Gamma likelihood-ratio test on the time needed to collect
                  k events in the spatial neighbourhood of an event.
knn_point_process Local k-nearest-neighbour distance statistic on the event
                  cloud in (x, y, beta*t) space, scored against the
                  homogeneous Poisson point process.
recursive_rate    Single-pass causal detector tracking an exponentially
                  forgetting arrival-rate estimate per pixel.
flow_matched      Velocity-adaptive matched filter: a local event-plane fit
                  gives the apparent motion, and support is counted along the
                  estimated velocity.
"""

import numpy as np
from numba import njit
from scipy.spatial import cKDTree
from scipy.special import gammainc
from scipy.stats import chi2, poisson

LN2 = 0.6931471805599453


# ---------------------------------------------------------------------------
# Frame-free per-pixel noise-rate estimation
# ---------------------------------------------------------------------------

@njit(cache=True)
def _quantile_interval_per_pixel(pix, t, n_pixels, min_events, q):
    """Quantile of the inter-arrival intervals per pixel, in one grouped pass.

    ``pix`` must be sorted by pixel and then by time.
    """
    out = np.full(n_pixels, np.nan)
    n = len(pix)
    start = 0
    buf = np.empty(n, dtype=np.float64)
    while start < n:
        end = start
        p = pix[start]
        while end < n and pix[end] == p:
            end += 1
        n_iv = end - start - 1
        if n_iv >= min_events - 1:
            for j in range(n_iv):
                buf[j] = t[start + j + 1] - t[start + j]
            seg = np.sort(buf[:n_iv])
            pos = q * (n_iv - 1)
            lo = int(np.floor(pos))
            hi = min(lo + 1, n_iv - 1)
            frac = pos - lo
            out[p] = seg[lo] * (1.0 - frac) + seg[hi] * frac
        start = end
    return out


def pixel_noise_rate(events, shape, min_events=4, interval_quantile=0.5):
    """Per-pixel noise rate in events per microsecond, estimated frame-free.

    The median inter-arrival interval of a homogeneous Poisson process with
    rate ``lambda`` is ``ln 2 / lambda``, so the median interval observed at a
    pixel gives a rate estimate that a short burst of target events cannot
    inflate.  Raising ``interval_quantile`` weights the quiescent intervals
    more heavily and yields a more conservative background rate.  Pixels with
    too few events fall back to the array-wide median rate.
    """
    W = shape[1]
    pix = (events['y'].astype(np.int64) * W + events['x'].astype(np.int64))
    order = np.argsort(pix, kind='stable')
    med = _quantile_interval_per_pixel(pix[order], events['t'][order].astype(np.float64),
                                       shape[0] * shape[1], min_events,
                                       float(interval_quantile))
    scale = -np.log(1.0 - interval_quantile) if interval_quantile < 1 else LN2
    with np.errstate(divide='ignore', invalid='ignore'):
        rate = scale / med
    finite = np.isfinite(rate)
    if finite.any():
        fallback = float(np.median(rate[finite]))
    else:
        duration = max(float(events['t'].max() - events['t'].min()), 1.0)
        fallback = len(events) / duration / (shape[0] * shape[1])
    rate[~finite] = fallback
    return np.maximum(rate, 1e-12).astype(np.float64)


# ---------------------------------------------------------------------------
# 1. Waiting-time likelihood ratio
# ---------------------------------------------------------------------------

@njit(cache=True)
def _waiting_times(x, y, t, H, W, radius, k, ring):
    """Elapsed time back to the k-th previous event in the spatial neighbourhood.

    A per-pixel ring buffer of the last ``ring`` timestamps is maintained, so
    the pass is causal and its cost per event depends only on the neighbourhood
    size, never on the sensor array size or the recording length.
    """
    n = len(t)
    buf = np.zeros((H * W, ring), dtype=np.int64)
    cnt = np.zeros(H * W, dtype=np.int16)
    head = np.zeros(H * W, dtype=np.int16)
    dt = np.full(n, -1.0)
    cand = np.empty((2 * radius + 1) ** 2 * ring, dtype=np.int64)
    for i in range(n):
        xi = x[i]
        yi = y[i]
        ti = t[i]
        m = 0
        for dy in range(-radius, radius + 1):
            yy = yi + dy
            if yy < 0 or yy >= H:
                continue
            for dx in range(-radius, radius + 1):
                xx = xi + dx
                if xx < 0 or xx >= W:
                    continue
                p = yy * W + xx
                c = cnt[p]
                for j in range(c):
                    cand[m] = buf[p, j]
                    m += 1
        if m >= k:
            seg = np.sort(cand[:m])
            dt[i] = ti - seg[m - k]
        p = yi * W + xi
        h = head[p]
        buf[p, h] = ti
        head[p] = (h + 1) % ring
        if cnt[p] < ring:
            cnt[p] += 1
    return dt


def waiting_time_lr(events, shape, radius=1, k=3, rate=None):
    """Gamma likelihood-ratio test on local waiting times (frame-free).

    Under the background hypothesis the events in the neighbourhood of a pixel
    form a superposition of Poisson processes, i.e. a Poisson process of rate
    ``Lambda`` equal to the summed per-pixel noise rates.  The time needed to
    collect ``k`` events is then Gamma(k, 1/Lambda) distributed, and the
    probability of a waiting time at least as short as the one observed,
    ``P(Gamma(k, 1) <= Lambda * dt)``, is the noise posterior: target events
    arrive in bursts and therefore produce unusually short waiting times.
    """
    if rate is None:
        rate = pixel_noise_rate(events, shape)
    H, W = shape
    lam_map = rate.reshape(H, W)
    pad = np.pad(lam_map, radius, mode='edge')
    csum = pad.cumsum(0).cumsum(1)
    csum = np.pad(csum, ((1, 0), (1, 0)))
    k_side = 2 * radius + 1
    box = (csum[k_side:, k_side:] - csum[:-k_side, k_side:]
           - csum[k_side:, :-k_side] + csum[:-k_side, :-k_side])

    dt = _waiting_times(events['x'].astype(np.int64), events['y'].astype(np.int64),
                        events['t'].astype(np.int64), H, W, radius, k, max(k, 4))
    lam_w = box[events['y'].astype(np.intp), events['x'].astype(np.intp)]
    p_noise = np.ones(len(events))
    ok = dt >= 0
    p_noise[ok] = gammainc(k, np.maximum(lam_w[ok] * dt[ok], 0.0))
    return p_noise.astype(np.float32)


# ---------------------------------------------------------------------------
# 1b. Sliding-window Poisson test (frame-free counterpart of the voxel test)
# ---------------------------------------------------------------------------

@njit(cache=True)
def _window_counts(x, y, t, H, W, radius, window_us):
    """Neighbourhood event count within a trailing window of ``window_us``.

    The window travels with the event instead of being pinned to a grid.  A
    single trailing pointer over the time-ordered stream maintains the set of
    events currently inside the window, so every event is inserted once and
    removed once; the only state is one counter per pixel, and no event time
    is ever discretised or discarded.
    """
    n = len(t)
    live = np.zeros(H * W, dtype=np.int32)
    out = np.zeros(n)
    tail = 0
    for i in range(n):
        xi = x[i]
        yi = y[i]
        ti = t[i]
        while tail < i and ti - t[tail] > window_us:
            live[y[tail] * W + x[tail]] -= 1
            tail += 1
        total = 0.0
        for dy in range(-radius, radius + 1):
            yy = yi + dy
            if yy < 0 or yy >= H:
                continue
            base = yy * W
            for dx in range(-radius, radius + 1):
                xx = xi + dx
                if xx < 0 or xx >= W:
                    continue
                total += live[base + xx]
        out[i] = total
        live[yi * W + xi] += 1
    return out


def _neighbourhood_rate(rate, shape, radius):
    lam_map = rate.reshape(shape)
    pad = np.pad(lam_map, radius, mode='edge')
    csum = np.pad(pad.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    k = 2 * radius + 1
    return (csum[k:, k:] - csum[:-k, k:] - csum[k:, :-k] + csum[:-k, :-k])


def sliding_window_lr(events, shape, radius=1, window_us=100000.0,
                      interval_quantile=0.5, rate=None):
    """Frame-free Poisson likelihood-ratio test on a trailing event window.

    The test statistic is the number of events in the spatiotemporal
    neighbourhood of an event, compared with its expectation under the
    per-pixel Poisson background.  Unlike the voxelised form, the window is
    anchored on the event rather than on a fixed grid, which removes the
    quantisation of event times and the dependence of both cost and memory on
    the sensor array size and the recording length.
    """
    if rate is None:
        rate = pixel_noise_rate(events, shape, interval_quantile=interval_quantile)
    counts = _window_counts(events['x'].astype(np.int64), events['y'].astype(np.int64),
                            events['t'].astype(np.int64), shape[0], shape[1],
                            radius, float(window_us))
    box = _neighbourhood_rate(rate, shape, radius)
    lam = np.maximum(box[events['y'].astype(np.intp), events['x'].astype(np.intp)]
                     * window_us, 1e-3)
    return poisson.sf(np.maximum(counts - 1.0, 0.0), lam).astype(np.float32)


def adaptive_window_lr(events, shape, radius=3, expected_count=16.0,
                       interval_quantile=0.99, min_window_us=1e3,
                       max_window_us=2e6, rate=None):
    """Sliding-window Poisson test with the window set from the background rate.

    The window length is not a time in milliseconds but a dimensionless
    background count: ``window_us`` is chosen so that the median neighbourhood
    background expectation over the events equals ``expected_count``.  The
    rate is the label-free per-pixel estimate, so the rule adapts to the
    sensor and to the observing conditions without any tuning on the target
    stream, and keeps the tail probability away from saturation when the
    background is dense.
    """
    rate, window_us = adaptive_window_us(events, shape, radius, expected_count,
                                         interval_quantile, min_window_us,
                                         max_window_us, rate)
    return sliding_window_lr(events, shape, radius=radius, window_us=window_us,
                             rate=rate)


def adaptive_window_us(events, shape, radius=3, expected_count=16.0,
                       interval_quantile=0.99, min_window_us=1e3,
                       max_window_us=2e6, rate=None):
    """Return (per-pixel rate, window in µs) of the rate-adaptive rule."""
    if rate is None:
        rate = pixel_noise_rate(events, shape, interval_quantile=interval_quantile)
    box = _neighbourhood_rate(rate, shape, radius)
    lam_box = box[events['y'].astype(np.intp), events['x'].astype(np.intp)]
    window_us = float(np.clip(expected_count / max(np.median(lam_box), 1e-12),
                              min_window_us, max_window_us))
    return rate, window_us


def multiscale_lr(events, shape, scales=((2, 50e3), (3, 150e3), (4, 400e3)),
                  interval_quantile=0.99, rate=None):
    """Fisher combination of the trailing-window test over several scales.

    A transit that crosses the array quickly concentrates its events in a
    small neighbourhood over a short interval, while a slow one spreads them
    over a larger neighbourhood and a longer interval, so no single
    (radius, window) pair is matched to every target.  The per-scale tests
    share the background hypothesis, so their p-values combine through
    Fisher's statistic ``-2 sum log p``, which is chi-squared with ``2 m``
    degrees of freedom under the background.  The combination needs no
    training and keeps the output on the probability scale.
    """
    if rate is None:
        rate = pixel_noise_rate(events, shape, interval_quantile=interval_quantile)
    stat = np.zeros(len(events))
    for radius, window_us in scales:
        p = sliding_window_lr(events, shape, radius=radius, window_us=window_us,
                              rate=rate).astype(np.float64)
        stat += -2.0 * np.log(np.clip(p, 1e-300, 1.0))
    return chi2.sf(stat, 2 * len(scales)).astype(np.float32)


@njit(cache=True)
def _polarity_pair_counts(x, y, t, p, H, W, radius, window_us):
    """Opposite-polarity support in a trailing window.

    A transit crossing a pixel produces an ON event on arrival and an OFF
    event on departure, so signal neighbourhoods contain both polarities in
    quick succession; uncorrelated background noise has no such pairing
    beyond chance.  Counting only the neighbours of opposite polarity is the
    event-driven form of that contrast, and needs two counters per pixel.
    """
    n = len(t)
    live_pos = np.zeros(H * W, dtype=np.int32)
    live_neg = np.zeros(H * W, dtype=np.int32)
    out = np.zeros(n)
    tail = 0
    for i in range(n):
        xi = x[i]
        yi = y[i]
        ti = t[i]
        pi = p[i]
        while tail < i and ti - t[tail] > window_us:
            q = y[tail] * W + x[tail]
            if p[tail] > 0:
                live_pos[q] -= 1
            else:
                live_neg[q] -= 1
            tail += 1
        total = 0.0
        for dy in range(-radius, radius + 1):
            yy = yi + dy
            if yy < 0 or yy >= H:
                continue
            base = yy * W
            for dx in range(-radius, radius + 1):
                xx = xi + dx
                if xx < 0 or xx >= W:
                    continue
                q = base + xx
                total += live_neg[q] if pi > 0 else live_pos[q]
        out[i] = total
        q = yi * W + xi
        if pi > 0:
            live_pos[q] += 1
        else:
            live_neg[q] += 1
    return out


def polarity_pair_lr(events, shape, radius=3, window_us=150000.0,
                     interval_quantile=0.99, rate=None):
    """Poisson test on the opposite-polarity neighbourhood count."""
    if rate is None:
        rate = pixel_noise_rate(events, shape, interval_quantile=interval_quantile)
    counts = _polarity_pair_counts(
        events['x'].astype(np.int64), events['y'].astype(np.int64),
        events['t'].astype(np.int64), events['p'].astype(np.int8),
        shape[0], shape[1], radius, float(window_us))
    box = _neighbourhood_rate(rate, shape, radius)
    lam = np.maximum(0.5 * box[events['y'].astype(np.intp),
                               events['x'].astype(np.intp)] * window_us, 1e-3)
    return poisson.sf(np.maximum(counts - 1.0, 0.0), lam).astype(np.float32)


# ---------------------------------------------------------------------------
# 2. k-nearest-neighbour point-process statistic
# ---------------------------------------------------------------------------

def knn_point_process(events, shape, k=4, beta_px_per_s=200.0, rate=None):
    """Local k-nearest-neighbour distance statistic in (x, y, beta t) space.

    The stream is treated as a point cloud in a metric space where time is
    converted to pixels through the characteristic apparent speed
    ``beta_px_per_s``.  For a homogeneous Poisson point process of intensity
    ``mu`` the volume of the ball containing the k nearest neighbours is
    Gamma(k, 1/mu) distributed, which turns the observed k-th neighbour
    distance into a noise posterior without any binning.  This is the local
    form of a Ripley K-function test.
    """
    if rate is None:
        rate = pixel_noise_rate(events, shape)
    beta = beta_px_per_s / 1e6  # pixels per microsecond
    pts = np.empty((len(events), 3), dtype=np.float64)
    pts[:, 0] = events['x']
    pts[:, 1] = events['y']
    pts[:, 2] = (events['t'] - events['t'][0]) * beta
    tree = cKDTree(pts)
    dist, _ = tree.query(pts, k=k + 1, workers=-1)
    r_k = dist[:, -1]
    # background intensity in the same (pixel, pixel, pixel) units
    mu = rate[events['y'].astype(np.intp) * shape[1] + events['x'].astype(np.intp)] / beta
    volume = (4.0 / 3.0) * np.pi * r_k ** 3
    return gammainc(k, np.maximum(mu * volume, 0.0)).astype(np.float32)


# ---------------------------------------------------------------------------
# 3. Recursive (causal, single-pass) rate detector
# ---------------------------------------------------------------------------

@njit(cache=True)
def _recursive_scores(x, y, t, H, W, radius, alpha, tau_us):
    """Exponentially forgetting arrival-rate tracker with neighbourhood support.

    One state pair per pixel is kept, so memory is O(pixels) with no temporal
    dimension, and each event triggers a constant number of updates.
    """
    n = len(t)
    last = np.full(H * W, -1.0)
    mean_iv = np.zeros(H * W)
    inited = np.zeros(H * W, dtype=np.uint8)
    out = np.empty(n)
    for i in range(n):
        xi = x[i]
        yi = y[i]
        ti = t[i]
        # local support: expected vs. observed recent activity
        expected = 0.0
        observed = 0.0
        for dy in range(-radius, radius + 1):
            yy = yi + dy
            if yy < 0 or yy >= H:
                continue
            for dx in range(-radius, radius + 1):
                xx = xi + dx
                if xx < 0 or xx >= W:
                    continue
                p = yy * W + xx
                if inited[p] == 1 and mean_iv[p] > 0.0:
                    expected += tau_us / mean_iv[p]
                if last[p] >= 0.0 and ti - last[p] <= tau_us:
                    observed += 1.0
        if expected < 1e-6:
            expected = 1e-6
        out[i] = (observed - expected) / np.sqrt(expected)
        p = yi * W + xi
        if last[p] >= 0.0:
            iv = ti - last[p]
            if inited[p] == 0:
                mean_iv[p] = iv
                inited[p] = 1
            elif iv > mean_iv[p]:
                # only quiescent intervals update the background estimate, so a
                # burst of target events cannot be absorbed into it
                mean_iv[p] = (1.0 - alpha) * mean_iv[p] + alpha * iv
        last[p] = ti
    return out


def recursive_rate(events, shape, radius=1, alpha=0.05, tau_us=20000.0):
    """Causal single-pass detector with O(1) work and no future dependence."""
    z = _recursive_scores(events['x'].astype(np.int64), events['y'].astype(np.int64),
                          events['t'].astype(np.int64), shape[0], shape[1],
                          radius, alpha, tau_us)
    return (1.0 / (1.0 + np.exp(np.clip(z, -60.0, 60.0)))).astype(np.float32)


# ---------------------------------------------------------------------------
# 4. Velocity-adaptive matched filter
# ---------------------------------------------------------------------------

@njit(cache=True)
def _flow_support(x, y, t, idx, offsets, n_nb, tol_px):
    """Support along the locally fitted event-plane velocity.

    For each event the neighbours returned by the spatial index are used to fit
    the local event plane by least squares, giving the apparent velocity; the
    support is the number of neighbours consistent with motion at that velocity.
    """
    n = len(t)
    out = np.zeros(n)
    for i in range(n):
        lo = offsets[i]
        hi = offsets[i + 1]
        m = hi - lo
        if m < 4:
            out[i] = 0.0
            continue
        sxx = 0.0
        sxy = 0.0
        syy = 0.0
        sxt = 0.0
        syt = 0.0
        for j in range(lo, hi):
            q = idx[j]
            dx = float(x[q] - x[i])
            dy = float(y[q] - y[i])
            dt = float(t[q] - t[i])
            sxx += dx * dx
            sxy += dx * dy
            syy += dy * dy
            sxt += dx * dt
            syt += dy * dt
        det = sxx * syy - sxy * sxy
        if abs(det) < 1e-6:
            out[i] = 0.0
            continue
        # plane t = a*dx + b*dy ; (a, b) is the inverse-velocity vector
        a = (syy * sxt - sxy * syt) / det
        b = (sxx * syt - sxy * sxt) / det
        norm = np.sqrt(a * a + b * b)
        if norm < 1e-12:
            out[i] = 0.0
            continue
        support = 0.0
        for j in range(lo, hi):
            q = idx[j]
            dx = float(x[q] - x[i])
            dy = float(y[q] - y[i])
            dt = float(t[q] - t[i])
            resid = abs(dt - (a * dx + b * dy)) / norm
            if resid <= tol_px:
                support += 1.0
        out[i] = support
    return out


def flow_matched(events, shape, radius_px=4.0, dt_us=30000.0, tol_px=1.0, rate=None):
    """Velocity-adaptive matched filter on the raw event cloud.

    Target events from a transiting object lie on a plane in (x, y, t) space
    because they share an apparent velocity, whereas noise events do not align.
    The neighbourhood is queried in continuous time, so no temporal
    discretisation is involved, and the expected support under the Poisson
    background is used to normalise the statistic.
    """
    if rate is None:
        rate = pixel_noise_rate(events, shape)
    beta = radius_px / dt_us  # pixels per microsecond, isotropic neighbourhood
    pts = np.empty((len(events), 3), dtype=np.float64)
    pts[:, 0] = events['x']
    pts[:, 1] = events['y']
    pts[:, 2] = (events['t'] - events['t'][0]) * beta
    tree = cKDTree(pts)
    neighbours = tree.query_ball_point(pts, radius_px, workers=-1, return_sorted=False)
    lens = np.fromiter((len(v) for v in neighbours), dtype=np.int64, count=len(neighbours))
    offsets = np.zeros(len(neighbours) + 1, dtype=np.int64)
    np.cumsum(lens, out=offsets[1:])
    idx = np.concatenate([np.asarray(v, dtype=np.int64) for v in neighbours]) \
        if offsets[-1] > 0 else np.zeros(0, dtype=np.int64)

    support = _flow_support(events['x'].astype(np.int64), events['y'].astype(np.int64),
                            events['t'].astype(np.int64), idx, offsets,
                            len(neighbours), tol_px)
    # expected aligned support under the Poisson background: the tolerance slab
    # occupies a fraction of the neighbourhood ball
    lam = rate[events['y'].astype(np.intp) * shape[1] + events['x'].astype(np.intp)]
    n_pix_slab = np.pi * radius_px * 2.0 * tol_px
    expected = np.maximum(lam / beta * n_pix_slab, 1e-3)
    return poisson.sf(np.maximum(support - 1.0, 0.0), expected).astype(np.float32)


# ---------------------------------------------------------------------------
# 5. Event-driven reformulations of established comparators
# ---------------------------------------------------------------------------

@njit(cache=True)
def _bilateral_support(x, y, t, p, H, W, radius, window_us):
    """Event-driven bilateral support on a trailing window of ``window_us``.

    The voxelised bilateral filter accumulates neighbours over a temporal bin
    and weights them by bin distance; the event-driven form keeps the exact
    timestamps and accumulates over a window anchored on the event, with a
    Gaussian spatial kernel and polarity agreement as the range term.  Two
    counters per pixel and one trailing pointer over the time-ordered stream
    are sufficient, so each event is inserted and removed exactly once.
    """
    n = len(t)
    live_pos = np.zeros(H * W, dtype=np.int32)
    live_neg = np.zeros(H * W, dtype=np.int32)
    out = np.zeros(n)
    tail = 0
    for i in range(n):
        xi = x[i]
        yi = y[i]
        ti = t[i]
        pi = p[i]
        while tail < i and ti - t[tail] > window_us:
            q = y[tail] * W + x[tail]
            if p[tail] > 0:
                live_pos[q] -= 1
            else:
                live_neg[q] -= 1
            tail += 1
        total = 0.0
        for dy in range(-radius, radius + 1):
            yy = yi + dy
            if yy < 0 or yy >= H:
                continue
            for dx in range(-radius, radius + 1):
                xx = xi + dx
                if xx < 0 or xx >= W:
                    continue
                q = yy * W + xx
                spatial = np.exp(-(dx * dx + dy * dy) / (2.0 * radius * radius + 1e-9))
                if pi > 0:
                    total += spatial * (live_pos[q] + 0.5 * live_neg[q])
                else:
                    total += spatial * (live_neg[q] + 0.5 * live_pos[q])
        out[i] = total
        q = yi * W + xi
        if pi > 0:
            live_pos[q] += 1
        else:
            live_neg[q] += 1
    return out


def bilateral_event_driven(events, shape, radius=2, window_us=100000.0):
    """Event-driven counterpart of the event bilateral filter."""
    sup = _bilateral_support(events['x'].astype(np.int64), events['y'].astype(np.int64),
                             events['t'].astype(np.int64),
                             events['p'].astype(np.int8), shape[0], shape[1],
                             radius, float(window_us))
    thr = np.median(sup)
    scale = max(np.std(sup), 1e-6)
    return (1.0 / (1.0 + np.exp(np.clip((sup - thr) / scale, -60.0, 60.0)))).astype(np.float32)


@njit(cache=True)
def _dwf_event_driven_scores(x, y, t, H, W, radius, tau_us, ring):
    """Double-window filter evaluated in continuous time.

    The original formulation compares the counts in two trailing windows; the
    event-driven form keeps the exact timestamps, so the inner and outer
    windows are defined by elapsed time instead of a fixed number of events.
    """
    n = len(t)
    buf = np.zeros((H * W, ring), dtype=np.int64)
    cnt = np.zeros(H * W, dtype=np.int32)
    head = np.zeros(H * W, dtype=np.int32)
    out = np.zeros(n)
    for i in range(n):
        xi = x[i]
        yi = y[i]
        ti = t[i]
        inner = 0.0
        outer = 0.0
        for dy in range(-radius, radius + 1):
            yy = yi + dy
            if yy < 0 or yy >= H:
                continue
            for dx in range(-radius, radius + 1):
                xx = xi + dx
                if xx < 0 or xx >= W:
                    continue
                q = yy * W + xx
                c = cnt[q]
                h = head[q]
                for j in range(c):
                    pos = h - 1 - j
                    if pos < 0:
                        pos += ring
                    dt = ti - buf[q, pos]
                    if dt > 4.0 * tau_us:
                        break
                    if dt <= tau_us:
                        inner += 1.0
                    else:
                        outer += 1.0
        out[i] = inner - outer / 3.0
        q = yi * W + xi
        h = head[q]
        buf[q, h] = ti
        head[q] = (h + 1) % ring
        if cnt[q] < ring:
            cnt[q] += 1
    return out


def dwf_event_driven(events, shape, radius=2, tau_us=20000.0, ring=64):
    """Event-driven counterpart of the double-window filter."""
    s = _dwf_event_driven_scores(events['x'].astype(np.int64), events['y'].astype(np.int64),
                                 events['t'].astype(np.int64), shape[0], shape[1],
                                 radius, float(tau_us), ring)
    thr = np.median(s)
    scale = max(np.std(s), 1e-6)
    return (1.0 / (1.0 + np.exp(np.clip((s - thr) / scale, -60.0, 60.0)))).astype(np.float32)


EVENT_DRIVEN = {
    'sliding_lr': sliding_window_lr,
    'bilateral_ed': bilateral_event_driven,
    'dwf_ed': dwf_event_driven,
    'waiting_lr': waiting_time_lr,
    'knn_pp': knn_point_process,
    'recursive': recursive_rate,
    'flow_matched': flow_matched,
}
