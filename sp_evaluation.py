#!/usr/bin/env python3
"""
Label-based systematic evaluation for the Signal Processing submission.

Differences from systematic_evaluation.py (IEEE Sensors version):

1. Ground truth comes from the EBSSA object annotations (bounding boxes
   supplied with the dataset) instead of a Fano-factor proxy.  The proxy used
   previously was derived from the same statistic as the proposed filter and
   therefore could not support a fair comparison.
2. All labelled EBSSA recordings are evaluated, not a subset of 20.
3. The comparator set is extended with published event-only denoisers
   (kNoise, double-window filter, YNoise) and with two supervised
   learning-based denoisers (MLPF-style and EDnCNN-style) trained on EBSSA
   labels with grouped cross-validation over recordings.
4. Results are stratified by sensor configuration and by background event
   rate so that generalisation can be assessed within the dataset.
5. The proposed detector is the event-driven Poisson likelihood-ratio test of
   ``event_driven.py``.  Its voxelised counterpart, a per-pixel Fano-factor
   score and event-driven reformulations of the established comparators are
   all retained as ablations, which separates the effect of
   the test statistic from the effect of the frame-free implementation.

Outputs: results/sp_evaluation_summary.json, results/sp_per_recording.json
"""

import json
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from numba import njit
from scipy.ndimage import convolve, uniform_filter
from scipy.spatial import cKDTree
from scipy.stats import wilcoxon
from sklearn.metrics import f1_score, roc_auc_score

import event_driven as ed

SEED = 42
OUT_DIR = Path(__file__).resolve().parent
DATA_DIR = OUT_DIR / 'data'
RESULTS_DIR = OUT_DIR / 'results'
MAX_EVENTS = 200000
N_FOLDS = 4
TRAIN_EVENTS_PER_REC = 8000
TAU_US = 50000.0
PATCH_R = 3

np.random.seed(SEED)
torch.manual_seed(SEED)


# ---------------------------------------------------------------------------
# Ground truth from EBSSA annotations
# ---------------------------------------------------------------------------

def event_labels_from_bbox(events, bbox, shape, window_us=10000):
    """Per-event ground truth from the EBSSA object annotations.

    An event is labelled target (signal) when it falls inside an annotated
    bounding box within one annotation window of that annotation timestamp.
    Every other event -- background activity, sensor noise and the static star
    field -- is labelled non-target, following the convention of the dataset.
    """
    if bbox is None or len(bbox) == 0:
        return None
    t0 = int(events['t'].min())
    t1 = int(events['t'].max())
    n_bins = int((t1 - t0) // window_us) + 1
    mask = np.zeros((n_bins, *shape), dtype=bool)
    H, W = shape
    for ann in bbox:
        b = int((ann['t'] - t0) // window_us)
        if b < 0 or b >= n_bins:
            continue
        x0 = max(int(np.floor(ann['x_min'])), 0)
        x1 = min(int(np.ceil(ann['x_max'])) + 1, W)
        y0 = max(int(np.floor(ann['y_min'])), 0)
        y1 = min(int(np.ceil(ann['y_max'])) + 1, H)
        if x1 <= x0 or y1 <= y0:
            continue
        mask[b, y0:y1, x0:x1] = True
    ev_bin = np.clip((events['t'] - t0) // window_us, 0, n_bins - 1).astype(np.intp)
    return mask[ev_bin, events['y'].astype(np.intp), events['x'].astype(np.intp)]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def detect_shape(events):
    return int(events['y'].max()) + 1, int(events['x'].max()) + 1


def compute_rate_cube(events, shape, n_bins=30):
    t_min, t_max = events['t'].min(), events['t'].max()
    bin_edges = np.linspace(t_min, t_max, n_bins + 1)
    bin_idx = np.clip(np.digitize(events['t'], bin_edges) - 1, 0, n_bins - 1)
    cube = np.zeros((n_bins, *shape), dtype=np.float32)
    np.add.at(cube, (bin_idx, events['y'].astype(np.intp), events['x'].astype(np.intp)), 1)
    return cube, bin_edges


def compute_fano_map(rate_cube):
    mean_rate = rate_cube.mean(axis=0)
    var_rate = rate_cube.var(axis=0)
    fano = np.zeros_like(mean_rate)
    active = mean_rate > 0
    fano[active] = var_rate[active] / mean_rate[active]
    return fano, mean_rate


def _events_to_3d_hist(events, shape, n_bins=30):
    t_min, t_max = events['t'].min(), events['t'].max()
    n_bins = max(n_bins, 2)
    bin_edges = np.linspace(t_min, t_max, n_bins + 1)
    bin_idx = np.clip(np.digitize(events['t'], bin_edges) - 1, 0, n_bins - 1)
    hist = np.zeros((n_bins, *shape), dtype=np.float32)
    np.add.at(hist, (bin_idx, events['y'].astype(np.intp), events['x'].astype(np.intp)), 1)
    return hist, bin_idx


# ---------------------------------------------------------------------------
# Event-only baselines
# ---------------------------------------------------------------------------

def fano_filter(events, shape, n_bins=30):
    """Per-pixel Fano-factor overdispersion score, kept as an ablation."""
    rate_cube, _ = compute_rate_cube(events, shape, n_bins)
    fano, mean_rate = compute_fano_map(rate_cube)
    noise_rate = mean_rate.copy()
    high_fano = fano > 2.0
    if high_fano.any():
        noise_rate[high_fano] = rate_cube[:, high_fano].min(axis=0)
    x = events['x'].astype(np.intp)
    y = events['y'].astype(np.intp)
    total_rates = mean_rate[y, x]
    noise_rates = noise_rate[y, x]
    p = np.where(total_rates > 0, np.minimum(1.0, noise_rates / total_rates), 1.0)
    return p.astype(np.float32)


def poisson_lr_filter(events, shape, n_bins=200, quantile=0.25, radius=1, t_half=1):
    """Voxelised Poisson likelihood-ratio detector (frame-based counterpart).

    This is the frame-based form of the proposed test, retained so that the
    event-driven implementation in ``event_driven.sliding_window_lr`` can be
    compared against it at equal test statistic.

    The background hypothesis is the per-pixel Poisson noise process of the
    A5 pixel model.  Its rate is estimated robustly as a low temporal quantile
    of the per-pixel bin counts, so that a target transiting the pixel for a
    small fraction of the recording does not inflate the noise estimate.  Each
    event is then scored by the Poisson survival probability of the count
    observed in its local spatiotemporal window under that background rate,
    which is the noise posterior of a likelihood-ratio test between the
    background-only and background-plus-target hypotheses.
    """
    from scipy.stats import poisson

    cube, bin_edges = compute_rate_cube(events, shape, n_bins)
    lam = np.quantile(cube, quantile, axis=0).astype(np.float32)

    k = 2 * radius + 1
    kt = 2 * t_half + 1
    kernel = np.ones((kt, k, k), dtype=np.float32)
    counts = convolve(cube, kernel, mode='constant')
    lam_window = convolve(np.broadcast_to(lam, cube.shape).copy(), kernel, mode='constant')

    bin_idx = np.clip(np.digitize(events['t'], bin_edges) - 1, 0, n_bins - 1)
    x = events['x'].astype(np.intp)
    y = events['y'].astype(np.intp)
    n_obs = counts[bin_idx, y, x]
    lam_obs = np.maximum(lam_window[bin_idx, y, x], 1e-3)
    # P(N >= n_obs | background) -- small values indicate a target
    p_noise = poisson.sf(np.maximum(n_obs - 1.0, 0.0), lam_obs)
    return p_noise.astype(np.float32)


def temporal_filter(events, shape, dt_us=50000):
    """Background-activity filter of Delbruck (2008), binned implementation."""
    t_min, t_max = events['t'].min(), events['t'].max()
    n_bins = max(int((t_max - t_min) / dt_us), 1)
    bin_edges = np.linspace(t_min, t_max, n_bins + 1)
    p_noise = np.ones(len(events), dtype=np.float32)
    bin_idx = np.clip(np.digitize(events['t'], bin_edges) - 1, 0, n_bins - 1)
    for i in range(n_bins):
        idx = np.where(bin_idx == i)[0]
        if idx.size == 0:
            continue
        activity = np.zeros(shape, dtype=np.float32)
        np.add.at(activity, (events['y'][idx], events['x'][idx]), 1)
        neighbour = uniform_filter(activity, size=3) * 9 - activity
        support = neighbour[events['y'][idx].astype(np.intp), events['x'][idx].astype(np.intp)]
        p_noise[idx] = np.where(support > 0, 0.2, 0.9)
    return p_noise


def nearest_neighbor_filter(events, shape, dt_us=20000, dist_threshold=2.0):
    coords = np.stack([
        events['x'].astype(np.float32),
        events['y'].astype(np.float32),
        events['t'].astype(np.float32) / max(dt_us, 1.0),
    ], axis=1)
    tree = cKDTree(coords)
    dist, _ = tree.query(coords, k=2, workers=-1)
    p = (2.0 / np.pi) * np.arctan(dist[:, 1] / dist_threshold)
    return np.clip(p, 0.0, 1.0).astype(np.float32)


def _logistic_score(count, thr, scale):
    """Map a support count to a noise probability without overflow."""
    z = np.clip((count - thr) / scale, -60.0, 60.0)
    return (1.0 / (1.0 + np.exp(z))).astype(np.float32)


def event_bilateral_filter(events, shape, n_bins=30, spatial_radius=2):
    hist, bin_idx = _events_to_3d_hist(events, shape, n_bins)
    k = 2 * spatial_radius + 1
    support = convolve(hist, np.ones((3, k, k), dtype=np.float32), mode='constant')
    count = np.maximum(support[bin_idx, events['y'].astype(np.intp),
                               events['x'].astype(np.intp)] - 1.0, 0.0)
    return _logistic_score(count, max(1.0, float(np.median(count))), 0.5)


def motion_compensated_filter(events, shape, n_bins=30, spatial_radius=2):
    hist, bin_idx = _events_to_3d_hist(events, shape, n_bins)
    k = 2 * spatial_radius + 1
    spacetime = convolve(hist, np.ones((3, k, k), dtype=np.float32), mode='constant')
    spatial = convolve(hist, np.ones((1, k, k), dtype=np.float32), mode='constant')
    adj = spacetime - spatial
    count = np.maximum(adj[bin_idx, events['y'].astype(np.intp),
                           events['x'].astype(np.intp)], 0.0)
    return _logistic_score(count, max(1.0, float(np.median(count))), 0.5)


@njit(cache=True)
def _knoise_scores(x, y, t, H, W, dt_us):
    """kNoise (Khodamoradi and Kastner): one last-timestamp per row and column."""
    row_t = np.full(H, -1e18)
    row_x = np.zeros(H, dtype=np.int64)
    col_t = np.full(W, -1e18)
    col_y = np.zeros(W, dtype=np.int64)
    out = np.zeros(x.shape[0])
    for i in range(x.shape[0]):
        xi = x[i]
        yi = y[i]
        ti = t[i]
        support = 0.0
        for dy in (-1, 0, 1):
            yy = yi + dy
            if yy < 0 or yy >= H:
                continue
            if ti - row_t[yy] <= dt_us and abs(row_x[yy] - xi) <= 1:
                support += 1.0
        for dx in (-1, 0, 1):
            xx = xi + dx
            if xx < 0 or xx >= W:
                continue
            if ti - col_t[xx] <= dt_us and abs(col_y[xx] - yi) <= 1:
                support += 1.0
        out[i] = support
        row_t[yi] = ti
        row_x[yi] = xi
        col_t[xi] = ti
        col_y[xi] = yi
    return out


def knoise_filter(events, shape, dt_us=50000):
    H, W = shape
    support = _knoise_scores(events['x'].astype(np.int64), events['y'].astype(np.int64),
                             events['t'].astype(np.float64), H, W, float(dt_us))
    return _logistic_score(support, 1.0, 1.0)


@njit(cache=True)
def _dwf_scores(x, y, t, n_window, radius):
    """Double-window filter: spatial support inside the last n_window events."""
    n = x.shape[0]
    out = np.zeros(n)
    for i in range(n):
        start = i - n_window
        if start < 0:
            start = 0
        c = 0.0
        for j in range(start, i):
            if abs(x[j] - x[i]) <= radius and abs(y[j] - y[i]) <= radius:
                c += 1.0
        out[i] = c
    return out


def dwf_filter(events, shape, n_window=200, radius=2):
    support = _dwf_scores(events['x'].astype(np.int64), events['y'].astype(np.int64),
                          events['t'].astype(np.float64), n_window, radius)
    return _logistic_score(support, max(1.0, float(np.median(support))), 0.5)


@njit(cache=True)
def _ynoise_scores(x, y, t, H, W, dt_us, radius):
    """YNoise: density of recent events in a spatial window (Feng et al.)."""
    last = np.full((H, W), -1e18)
    n = x.shape[0]
    out = np.zeros(n)
    for i in range(n):
        xi = x[i]
        yi = y[i]
        ti = t[i]
        density = 0.0
        for dy in range(-radius, radius + 1):
            yy = yi + dy
            if yy < 0 or yy >= H:
                continue
            for dx in range(-radius, radius + 1):
                xx = xi + dx
                if xx < 0 or xx >= W:
                    continue
                if dx == 0 and dy == 0:
                    continue
                if ti - last[yy, xx] <= dt_us:
                    density += 1.0
        out[i] = density
        last[yi, xi] = ti
    return out


def ynoise_filter(events, shape, dt_us=30000, radius=2):
    H, W = shape
    density = _ynoise_scores(events['x'].astype(np.int64), events['y'].astype(np.int64),
                             events['t'].astype(np.float64), H, W, float(dt_us), radius)
    return _logistic_score(density, max(1.0, float(np.median(density))), 1.0)


def pidcdvs_filter(events, shape, n_bins=40, n_epochs=50, rec_idx=0):
    """Physics-informed CNN refinement of the Fano noise-rate map (unsupervised)."""
    import torch.nn.functional as F
    import torch.optim as optim

    torch.manual_seed(SEED + rec_idx)
    rate_cube, bin_edges = compute_rate_cube(events, shape, n_bins)
    fano, mean_rate = compute_fano_map(rate_cube)
    noise_rate_fano = mean_rate.copy()
    high_fano = fano > 2.0
    if high_fano.any():
        noise_rate_fano[high_fano] = rate_cube[:, high_fano].min(axis=0)
    noise_pixels = (fano < 2.0) & (fano > 0) & (mean_rate > 0)
    signal_pixels = high_fano & (mean_rate > 0)

    net = nn.Sequential(
        nn.Conv2d(2, 16, 5, padding=2), nn.LeakyReLU(0.1),
        nn.Conv2d(16, 16, 3, padding=1), nn.LeakyReLU(0.1),
        nn.Conv2d(16, 1, 3, padding=1),
    )
    optimizer = optim.Adam(net.parameters(), lr=0.005)
    feats = np.stack([
        noise_rate_fano / max(noise_rate_fano.max(), 1e-6),
        np.clip(fano, 0, 10) / 10.0,
    ], axis=0).astype(np.float32)
    input_t = torch.tensor(feats).unsqueeze(0)
    target_t = torch.tensor(noise_rate_fano.astype(np.float32))
    mean_t = torch.tensor(mean_rate.astype(np.float32))
    noise_mask = torch.tensor(noise_pixels)
    signal_mask = torch.tensor(signal_pixels)
    active_mask = torch.tensor(mean_rate > 0)

    for _ in range(n_epochs):
        optimizer.zero_grad()
        pred = F.softplus(net(input_t)).squeeze(0).squeeze(0)
        loss = torch.zeros(())
        if noise_mask.any():
            loss = loss + ((pred[noise_mask] - target_t[noise_mask]) ** 2).mean()
        if signal_mask.any():
            loss = loss + 0.5 * ((pred[signal_mask] - target_t[signal_mask]) ** 2).mean()
        loss = loss + 0.005 * (((pred[1:, :] - pred[:-1, :]) ** 2).mean()
                               + ((pred[:, 1:] - pred[:, :-1]) ** 2).mean())
        if active_mask.any():
            loss = loss + 0.1 * torch.relu(pred - mean_t)[active_mask].mean()
        loss.backward()
        optimizer.step()

    with torch.no_grad():
        noise_map = F.softplus(net(input_t)).squeeze(0).squeeze(0).numpy()

    bin_idx = np.clip(np.digitize(events['t'], bin_edges) - 1, 0, n_bins - 1)
    x = events['x'].astype(np.intp)
    y = events['y'].astype(np.intp)
    total = rate_cube[bin_idx, y, x]
    noise_vals = noise_map[y, x]
    p = np.where(total > 0, np.minimum(1.0, noise_vals / total), 1.0)
    return p.astype(np.float32)


# ---------------------------------------------------------------------------
# Supervised learning-based denoisers
# ---------------------------------------------------------------------------

@njit(cache=True)
def _time_surface_features(x, y, t, p, H, W, radius, tau):
    """Local time-surface patch (two polarities) around every event."""
    k = 2 * radius + 1
    n_feat = 2 * k * k
    last = np.full((2, H, W), -1e18)
    out = np.zeros((x.shape[0], n_feat), dtype=np.float32)
    for i in range(x.shape[0]):
        xi = x[i]
        yi = y[i]
        ti = t[i]
        f = 0
        for pol in range(2):
            for dy in range(-radius, radius + 1):
                yy = yi + dy
                for dx in range(-radius, radius + 1):
                    xx = xi + dx
                    if 0 <= yy < H and 0 <= xx < W:
                        dt = ti - last[pol, yy, xx]
                        if dt < 0.0:
                            dt = 0.0
                        out[i, f] = np.exp(-dt / tau)
                    f += 1
        last[p[i], yi, xi] = ti
    return out


def extract_features(events, shape):
    H, W = shape
    pol = events['p'].astype(np.int64)
    pol = np.clip(pol, 0, 1)
    return _time_surface_features(events['x'].astype(np.int64), events['y'].astype(np.int64),
                                  events['t'].astype(np.float64), pol, H, W, PATCH_R, TAU_US)


class MLPF(nn.Module):
    """Multilayer-perceptron denoiser in the style of Guo and Delbruck (2023)."""

    def __init__(self, n_feat):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(n_feat, 20), nn.ReLU(),
            nn.Linear(20, 10), nn.ReLU(),
            nn.Linear(10, 1),
        )

    def forward(self, x):
        return self.net(x).squeeze(-1)


class EDnCNNStyle(nn.Module):
    """Convolutional denoiser in the style of EDnCNN (Baldwin et al., 2020).

    The input is the two-polarity time-surface patch used above rather than the
    original event-probability-mask volume, because EBSSA provides no intensity
    frames from which the original targets could be computed.
    """

    def __init__(self, k):
        super().__init__()
        self.k = k
        self.conv = nn.Sequential(
            nn.Conv2d(2, 16, 3, padding=1), nn.ReLU(),
            nn.Conv2d(16, 32, 3, padding=1), nn.ReLU(),
        )
        self.head = nn.Sequential(
            nn.Flatten(), nn.Linear(32 * k * k, 32), nn.ReLU(), nn.Linear(32, 1),
        )

    def forward(self, x):
        x = x.view(-1, 2, self.k, self.k)
        return self.head(self.conv(x)).squeeze(-1)


def train_supervised(model, feats, labels, n_epochs=15, batch=2048, lr=2e-3):
    """Train a supervised denoiser with class-balanced positive weighting."""
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    n_pos = max(int(labels.sum()), 1)
    pos_weight = torch.tensor(float(len(labels) - n_pos) / n_pos)
    lossf = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    x = torch.from_numpy(feats)
    y = torch.from_numpy(labels.astype(np.float32))
    n = len(x)
    for _ in range(n_epochs):
        perm = torch.randperm(n)
        for i in range(0, n, batch):
            idx = perm[i:i + batch]
            opt.zero_grad()
            loss = lossf(model(x[idx]), y[idx])
            loss.backward()
            opt.step()
    model.eval()
    return model


def predict_supervised(model, feats, batch=65536):
    outs = []
    with torch.no_grad():
        for i in range(0, len(feats), batch):
            logits = model(torch.from_numpy(feats[i:i + batch]))
            outs.append(torch.sigmoid(logits).numpy())
    p_signal = np.concatenate(outs)
    return (1.0 - p_signal).astype(np.float32)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def compute_metrics(is_signal_gt, p_noise, threshold=0.5):
    n_signal = int(is_signal_gt.sum())
    n_noise = int(len(is_signal_gt) - n_signal)
    is_signal_pred = p_noise < threshold
    nrr = float(((~is_signal_gt) & (~is_signal_pred)).sum() / max(n_noise, 1))
    spr = float((is_signal_gt & is_signal_pred).sum() / max(n_signal, 1))
    f1 = float(f1_score(is_signal_gt.astype(int), is_signal_pred.astype(int), zero_division=0))
    try:
        auc = float(roc_auc_score(is_signal_gt.astype(int), 1.0 - p_noise))
    except ValueError:
        auc = 0.5
    return {'nrr': nrr, 'spr': spr, 'f1': f1, 'auc': auc,
            'removal_rate': float((p_noise >= threshold).sum() / len(p_noise)),
            'n_signal_gt': n_signal, 'n_noise_gt': n_noise}


# Hyper-parameter grids.  Every method with a grid (the proposed detector, its
# voxelised counterpart and the published event-only comparators) is tuned by
# the same grouped cross-validation, so no comparator is disadvantaged by
# author-default parameters.  A configuration is never chosen on the
# recordings it is scored on: selection happens inside the folds (see main).
EDLR_GRID = [
    {'radius': r, 'window_us': w, 'interval_quantile': q}
    for r in (2, 3)
    for w in (100e3, 150e3, 300e3)
    for q in (0.95, 0.99)
]
EDLR_ADAPTIVE_GRID = [
    {'radius': r, 'expected_count': k, 'interval_quantile': q}
    for r in (2, 3)
    for k in (2.0, 4.0, 8.0, 16.0, 32.0, 64.0)
    for q in (0.95, 0.99)
]
# deployment configuration used when nothing may be tuned on the target data
ZERO_KNOWLEDGE_CONFIG = {'radius': 3, 'expected_count': 16.0, 'interval_quantile': 0.99}
PLR_GRID = [
    {'n_bins': nb, 'quantile': q, 'radius': r, 't_half': th}
    for nb in (100, 200, 400)
    for q in (0.1, 0.25)
    for (r, th) in ((1, 1), (2, 2))
]
YNOISE_GRID = [
    {'dt_us': dt, 'radius': r}
    for dt in (10e3, 30e3, 100e3, 300e3)
    for r in (1, 2, 3)
]
BILATERAL_GRID = [
    {'n_bins': nb, 'spatial_radius': r}
    for nb in (10, 30, 100, 300)
    for r in (1, 2, 3)
]
KNOISE_GRID = [{'dt_us': dt} for dt in (5e3, 10e3, 20e3, 50e3, 100e3, 300e3)]
DWF_GRID = [
    {'n_window': nw, 'radius': r}
    for nw in (50, 200, 800)
    for r in (1, 2, 3)
]
TEMPORAL_GRID = [{'dt_us': dt} for dt in (5e3, 10e3, 20e3, 50e3, 100e3, 300e3)]
NEAREST_GRID = [
    {'dt_us': dt, 'dist_threshold': d}
    for dt in (5e3, 20e3, 100e3)
    for d in (1.5, 2.0, 3.0)
]
BILATERAL_ED_GRID = [
    {'radius': r, 'window_us': w}
    for r in (2, 3)
    for w in (100e3, 150e3, 300e3)
]
DWF_ED_GRID = [
    {'radius': r, 'tau_us': tau}
    for r in (2, 3)
    for tau in (10e3, 20e3, 50e3)
]
GRID_METHODS = {
    'edlr': (ed.sliding_window_lr, EDLR_GRID),
    'edlr_adaptive': (ed.adaptive_window_lr, EDLR_ADAPTIVE_GRID),
    'plr': (poisson_lr_filter, PLR_GRID),
    'ynoise': (ynoise_filter, YNOISE_GRID),
    'bilateral': (event_bilateral_filter, BILATERAL_GRID),
    'knoise': (knoise_filter, KNOISE_GRID),
    'dwf': (dwf_filter, DWF_GRID),
    'temporal': (temporal_filter, TEMPORAL_GRID),
    'nearest': (nearest_neighbor_filter, NEAREST_GRID),
    'bilateral_ed': (ed.bilateral_event_driven, BILATERAL_ED_GRID),
    'dwf_ed': (ed.dwf_event_driven, DWF_ED_GRID),
}

def modal_config(summary, name):
    """Configuration chosen in the largest number of CV folds (ties -> first)."""
    chosen = list(summary['selected_config_per_fold'][name].values())
    keys = [json.dumps(c, sort_keys=True) for c in chosen]
    return json.loads(
        max(set(keys), key=lambda k: (keys.count(k), -keys.index(k))))


def tuned(name, summary):
    """Callable (events, shape) -> score at the modal CV configuration, and that config."""
    fn = GRID_METHODS[name][0]
    cfg = modal_config(summary, name)
    return lambda e, s: fn(e, s, **cfg), cfg


METHOD_LABELS = {
    'edlr': 'Event-driven Poisson LR filter (proposed)',
    'edlr_adaptive': 'Event-driven Poisson LR filter, rate-adaptive window (proposed)',
    'plr': 'Voxelised Poisson LR filter (frame-based ablation)',
    'waiting_lr': 'Gamma waiting-time test (event-driven)',
    'knn_pp': 'kNN point-process test (event-driven)',
    'recursive': 'Recursive rate tracker (event-driven)',
    'flow_matched': 'Velocity-adaptive matched filter (event-driven)',
    'bilateral_ed': 'Event bilateral filter (event-driven form)',
    'dwf_ed': 'Double-window filter (event-driven form)',
    'temporal': 'Background-activity filter',
    'nearest': 'Nearest-neighbour filter',
    'bilateral': 'Event bilateral filter',
    'motion': 'Motion-compensated proxy',
    'knoise': 'kNoise',
    'dwf': 'Double-window filter',
    'ynoise': 'YNoise',
    'mlpf': 'MLPF (supervised)',
    'edncnn': 'EDnCNN-style CNN (supervised)',
    'pi_dc_dvs': 'PI-DC-DVS CNN',
    'fano': 'Fano-factor score (ablation)',
}
UNSUPERVISED = ['motion', 'pi_dc_dvs', 'fano',
                'waiting_lr', 'knn_pp', 'recursive', 'flow_matched']
CV_SELECTED = list(GRID_METHODS)
SUPERVISED = ['mlpf', 'edncnn']
PROPOSED = 'edlr'
# methods whose implementation never allocates a dense (time bin x pixel) grid
FRAME_FREE = {'edlr', 'edlr_adaptive', 'waiting_lr', 'knn_pp', 'recursive', 'flow_matched',
              'bilateral_ed', 'dwf_ed', 'ynoise', 'knoise', 'dwf', 'nearest'}


def _summarise(values):
    arr = np.asarray(values, dtype=float)
    return {'mean': float(arr.mean()), 'std': float(arr.std()), 'n': int(arr.size)}


def run_unsupervised(events, shape, rec_idx):
    scores = {}
    timings = {}
    runners = {
        'motion': lambda: motion_compensated_filter(events, shape),
        'pi_dc_dvs': lambda: pidcdvs_filter(events, shape, rec_idx=rec_idx),
        'fano': lambda: fano_filter(events, shape),
        'waiting_lr': lambda: ed.waiting_time_lr(events, shape),
        'knn_pp': lambda: ed.knn_point_process(events, shape),
        'recursive': lambda: ed.recursive_rate(events, shape),
        'flow_matched': lambda: ed.flow_matched(events, shape),
    }
    for name, fn in runners.items():
        t0 = time.time()
        scores[name] = fn()
        timings[name] = time.time() - t0
    return scores, timings


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def load_recordings(max_recordings=None, cache_dir=None):
    """Yield (idx, events, labels, shape, recording_id) for labelled EBSSA data."""
    import tonic

    dataset = tonic.datasets.EBSSA(save_to=str(DATA_DIR), split='labelled')
    n = len(dataset) if max_recordings is None else min(max_recordings, len(dataset))
    for idx in range(n):
        events, target = dataset[idx]
        bbox = target['bbox']
        if bbox is None or len(bbox) == 0 or len(events) == 0:
            continue
        shape = detect_shape(events)
        # Restrict the analysis window to the annotated interval: the object is
        # only labelled there, so events outside it carry no usable ground truth.
        t_lo = float(bbox['t'].min()) - 5e5
        t_hi = float(bbox['t'].max()) + 5e5
        lo, hi = np.searchsorted(events['t'], [t_lo, t_hi])
        events = events[lo:hi]
        if len(events) == 0:
            continue
        if len(events) > MAX_EVENTS:
            start = max((len(events) - MAX_EVENTS) // 2, 0)
            events = events[start:start + MAX_EVENTS]
        labels = event_labels_from_bbox(events, target['bbox'], shape)
        if labels is None:
            continue
        n_sig = int(labels.sum())
        if n_sig < 50 or (len(labels) - n_sig) < 50:
            print(f"  skip rec {idx}: n_signal={n_sig}, n_events={len(labels)}", flush=True)
            continue
        yield idx, events, labels, shape, target['recording_id']


def main(max_recordings=None):
    RESULTS_DIR.mkdir(exist_ok=True)
    cache_path = RESULTS_DIR / 'sp_cache'
    cache_path.mkdir(exist_ok=True)

    per_rec = []
    feature_store = {}

    grid_metrics = {name: {} for name in GRID_METHODS}
    for idx, events, labels, shape, rec_id in load_recordings(max_recordings):
        t_start = time.time()
        scores, timings = run_unsupervised(events, shape, idx)
        # the grid-based detectors are scored for every configuration; which
        # configuration is reported is decided per fold on training recordings
        for name, (fn, grid) in GRID_METHODS.items():
            grid_metrics[name][idx] = []
            elapsed = 0.0
            for cfg in grid:
                t0 = time.time()
                p = fn(events, shape, **cfg)
                elapsed += time.time() - t0
                grid_metrics[name][idx].append(compute_metrics(labels, p))
            timings[name] = elapsed / len(grid)
        feats = extract_features(events, shape)
        np.save(cache_path / f'feat_{idx}.npy', feats)
        np.save(cache_path / f'lab_{idx}.npy', labels)
        duration_s = float((events['t'].max() - events['t'].min()) / 1e6)
        entry = {
            'idx': int(idx),
            'recording_id': str(rec_id),
            'shape': [int(shape[0]), int(shape[1])],
            'n_events': int(len(events)),
            'duration_s': duration_s,
            'event_rate_hz': float(len(events) / max(duration_s, 1e-6)),
            'signal_fraction': float(labels.mean()),
            'metrics': {m: compute_metrics(labels, s) for m, s in scores.items()},
            'time_s': {m: float(v) for m, v in timings.items()},
        }
        per_rec.append(entry)
        feature_store[idx] = feats.shape[1]
        print(f"[{len(per_rec)}] rec {idx} {rec_id} n={len(events)} "
              f"sig={labels.mean():.4f} "
              f"edlr_auc={max(m['auc'] for m in grid_metrics['edlr'][idx]):.3f} "
              f"({time.time() - t_start:.1f}s)", flush=True)

    # --- supervised baselines with grouped cross-validation over recordings ---
    indices = [e['idx'] for e in per_rec]
    n_feat = feature_store[indices[0]]
    k = 2 * PATCH_R + 1
    rng = np.random.default_rng(SEED)
    folds = {i: j % N_FOLDS for j, i in enumerate(rng.permutation(indices))}

    sup_scores = {name: {} for name in SUPERVISED}
    for fold in range(N_FOLDS):
        train_idx = [i for i in indices if folds[i] != fold]
        test_idx = [i for i in indices if folds[i] == fold]
        xs, ys = [], []
        for i in train_idx:
            f = np.load(cache_path / f'feat_{i}.npy')
            lab = np.load(cache_path / f'lab_{i}.npy')
            # class-stratified subsample so that rare target events are seen
            pos = np.flatnonzero(lab)
            neg = np.flatnonzero(~lab)
            n_pos = min(len(pos), TRAIN_EVENTS_PER_REC // 2)
            n_neg = min(len(neg), TRAIN_EVENTS_PER_REC - n_pos)
            sel = np.concatenate([rng.choice(pos, n_pos, replace=False),
                                  rng.choice(neg, n_neg, replace=False)])
            xs.append(f[sel])
            ys.append(lab[sel])
        x_train = np.concatenate(xs)
        y_train = np.concatenate(ys)
        del xs, ys
        torch.manual_seed(SEED + fold)
        models = {'mlpf': MLPF(n_feat), 'edncnn': EDnCNNStyle(k)}
        for name, model in models.items():
            t0 = time.time()
            train_supervised(model, x_train, y_train)
            print(f"  fold {fold} {name} trained on {len(x_train)} events "
                  f"({time.time() - t0:.1f}s)", flush=True)
            for i in test_idx:
                f = np.load(cache_path / f'feat_{i}.npy')
                sup_scores[name][i] = predict_supervised(model, f)
        del x_train, y_train

    for entry in per_rec:
        lab = np.load(cache_path / f"lab_{entry['idx']}.npy")
        for name in SUPERVISED:
            entry['metrics'][name] = compute_metrics(lab, sup_scores[name][entry['idx']])
        entry['fold'] = int(folds[entry['idx']])

    # nested selection of hyper-parameters: for each fold the configuration
    # with the highest mean AUC on the training recordings is applied to the
    # held-out recordings, so no held-out recording informs the choice
    selected_cfg = {name: {} for name in GRID_METHODS}
    for name, (_, grid) in GRID_METHODS.items():
        for fold in range(N_FOLDS):
            train_idx = [i for i in indices if folds[i] != fold]
            mean_auc = [float(np.mean([grid_metrics[name][i][c]['auc'] for i in train_idx]))
                        for c in range(len(grid))]
            selected_cfg[name][fold] = int(np.argmax(mean_auc))
        for entry in per_rec:
            cfg_id = selected_cfg[name][entry['fold']]
            entry['metrics'][name] = grid_metrics[name][entry['idx']][cfg_id]
            entry.setdefault('selected_config', {})[name] = grid[cfg_id]

    methods = CV_SELECTED + UNSUPERVISED + SUPERVISED
    aggregate = {}
    for m in methods:
        aggregate[m] = {
            'label': METHOD_LABELS[m],
            'supervised': m in SUPERVISED,
            **{key: _summarise([e['metrics'][m][key] for e in per_rec])
               for key in ('nrr', 'spr', 'f1', 'auc')},
        }

    # paired comparison of every method against the proposed filter
    proposed_auc = np.array([e['metrics'][PROPOSED]['auc'] for e in per_rec])
    tests = {}
    raw_p = {}
    for m in methods:
        if m == PROPOSED:
            continue
        other = np.array([e['metrics'][m]['auc'] for e in per_rec])
        diff = proposed_auc - other
        if np.allclose(diff, 0):
            stat, p = float('nan'), 1.0
        else:
            stat, p = wilcoxon(proposed_auc, other)
        raw_p[m] = float(p)
        tests[m] = {'auc_diff_mean': float(diff.mean()), 'wilcoxon_stat': float(stat),
                    'p_raw': float(p)}
    # Holm-Bonferroni correction across the comparator family
    order = sorted(raw_p, key=raw_p.get)
    n_tests = len(order)
    prev = 0.0
    for rank, m in enumerate(order):
        adj = min(1.0, max(prev, (n_tests - rank) * raw_p[m]))
        prev = adj
        tests[m]['p_holm'] = adj

    # stratification by sensor configuration and by background event rate
    strata = {}
    shapes = sorted({tuple(e['shape']) for e in per_rec})
    for sh in shapes:
        subset = [e for e in per_rec if tuple(e['shape']) == sh]
        strata[f'sensor_{sh[0]}x{sh[1]}'] = {
            'n_recordings': len(subset),
            **{m: _summarise([e['metrics'][m]['auc'] for e in subset]) for m in methods},
        }
    rates = np.array([e['event_rate_hz'] for e in per_rec])
    median_rate = float(np.median(rates))
    for name, subset in (('activity_low', [e for e in per_rec if e['event_rate_hz'] <= median_rate]),
                         ('activity_high', [e for e in per_rec if e['event_rate_hz'] > median_rate])):
        strata[name] = {
            'n_recordings': len(subset),
            'median_event_rate_hz': median_rate,
            **{m: _summarise([e['metrics'][m]['auc'] for e in subset]) for m in methods},
        }

    summary = {
        'proposed_method': PROPOSED,
        'frame_free_methods': sorted(FRAME_FREE),
        'ground_truth': 'EBSSA object annotations (10x10 px bounding boxes, 10 ms windows)',
        'n_recordings': len(per_rec),
        'max_events_per_recording': MAX_EVENTS,
        'threshold': 0.5,
        'seed': SEED,
        'cv': {'type': 'grouped k-fold over recordings', 'n_folds': N_FOLDS,
               'train_events_per_recording': TRAIN_EVENTS_PER_REC},
        'features': {'patch_radius': PATCH_R, 'tau_us': TAU_US, 'n_features': int(n_feat)},
        'methods': aggregate,
        'paired_tests_vs_proposed': tests,
        'strata': strata,
        'runtime_s_per_recording': {m: _summarise([e['time_s'][m] for e in per_rec])
                                    for m in CV_SELECTED + UNSUPERVISED},
        'hyperparameter_grids': {name: grid for name, (_, grid) in GRID_METHODS.items()},
        'selected_config_per_fold': {
            name: {str(f): GRID_METHODS[name][1][c] for f, c in chosen.items()}
            for name, chosen in selected_cfg.items()},
    }
    with open(RESULTS_DIR / 'sp_evaluation_summary.json', 'w') as f:
        json.dump(summary, f, indent=2)
    with open(RESULTS_DIR / 'sp_per_recording.json', 'w') as f:
        json.dump(per_rec, f, indent=2)

    print('\n=== Aggregate (mean over recordings) ===')
    for m in methods:
        a = aggregate[m]
        print(f"{a['label']:52s} NRR={a['nrr']['mean']:.3f} SPR={a['spr']['mean']:.3f} "
              f"F1={a['f1']['mean']:.3f} AUC={a['auc']['mean']:.3f}")
    return summary


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--max-recordings', type=int, default=None)
    args = parser.parse_args()
    main(args.max_recordings)
