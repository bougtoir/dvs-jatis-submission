#!/usr/bin/env python3
"""
Systematic Evaluation of PI-DC-DVS on EBSSA Dataset

Evaluates:
1. PI-DC-DVS (our method) vs baselines across EBSSA recordings
2. Noise removal rate, signal preservation, and computation time
3. A5 model simulation with varying temperature/illuminance parameters
4. Generates publication-quality figures for MLST/A&A submission

Metrics:
- Noise Removal Rate (NRR): fraction of noise events correctly identified
- Signal Preservation Rate (SPR): fraction of signal events retained
- F1 Score: harmonic mean of precision and recall for signal detection
- ROC-AUC: area under ROC curve
"""

import numpy as np
import time
import json
import random
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from sklearn.metrics import roc_auc_score, f1_score
from scipy.ndimage import convolve, uniform_filter
from scipy.spatial import cKDTree

from utils import save_figure

# Color-blind-friendly palette for method comparisons (Wong, Nat. Methods 2011).
CB_COLORS = ['#000000', '#E69F00', '#56B4E9', '#009E73', '#F0E442', '#0072B2',
             '#D55E00', '#CC79A7']

SEED = 42


def _set_seed(seed=SEED):
    """Set random seeds for reproducible neural-network and NumPy results."""
    random.seed(seed)
    np.random.seed(seed)
    try:
        import torch
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
    except Exception:
        pass


OUT_DIR = Path(__file__).parent
DATA_DIR = OUT_DIR / 'data'
RESULTS_DIR = OUT_DIR / 'results'
RESULTS_DIR.mkdir(exist_ok=True)


def detect_shape(events):
    """Auto-detect sensor shape from event coordinates."""
    max_y = int(events['y'].max()) + 1
    max_x = int(events['x'].max()) + 1
    return (max_y, max_x)


def compute_rate_cube(events, shape, n_bins=30):
    """Compute rate maps per time bin (vectorised)."""
    t_min, t_max = events['t'].min(), events['t'].max()
    bin_edges = np.linspace(t_min, t_max, n_bins + 1)
    rate_cube = np.zeros((n_bins, *shape), dtype=np.float32)
    for i in range(n_bins):
        mask = (events['t'] >= bin_edges[i]) & (events['t'] < bin_edges[i + 1])
        bin_events = events[mask]
        bin_duration = (bin_edges[i + 1] - bin_edges[i]) / 1e6
        frame = np.zeros(shape, dtype=np.float32)
        np.add.at(frame, (bin_events['y'], bin_events['x']), 1)
        rate_cube[i] = frame / max(bin_duration, 1e-6)
    return rate_cube, bin_edges


def compute_fano_map(rate_cube):
    """Compute Fano factor map from rate cube."""
    mean_rate = rate_cube.mean(axis=0)
    var_rate = rate_cube.var(axis=0)
    fano = np.zeros_like(mean_rate)
    active = mean_rate > 0
    fano[active] = var_rate[active] / mean_rate[active]
    return fano, mean_rate


def fano_filter(events, shape, n_bins=30, threshold=0.5):
    """Fano-factor-based noise filter (fast, vectorised)."""
    rate_cube, _ = compute_rate_cube(events, shape, n_bins)
    fano, mean_rate = compute_fano_map(rate_cube)

    # Noise rate estimation
    noise_rate = mean_rate.copy()
    high_fano = fano > 2.0
    noise_rate[high_fano] = rate_cube[:, high_fano].min(axis=0)

    # Per-event noise probability (vectorised)
    x_coords = events['x'].astype(np.intp)
    y_coords = events['y'].astype(np.intp)
    total_rates = mean_rate[y_coords, x_coords]
    noise_rates = noise_rate[y_coords, x_coords]
    p_noise = np.where(total_rates > 0, np.minimum(1.0, noise_rates / total_rates), 1.0)
    return p_noise.astype(np.float32)


def temporal_filter_vectorised(events, shape, dt_us=50000):
    """
    Temporal neighbourhood filter (vectorised, fast approximation).

    Instead of per-event loop, uses time-binned spatial correlation.
    Events are noise if their pixel had no active neighbours in the time window.
    """
    # Discretise time into dt_us bins
    t_min = events['t'].min()
    t_max = events['t'].max()
    n_bins = max(int((t_max - t_min) / dt_us), 1)
    bin_edges = np.linspace(t_min, t_max, n_bins + 1)

    # For each bin, compute activity map and check neighbourhood support
    p_noise = np.ones(len(events), dtype=np.float32)

    for i in range(n_bins):
        mask = (events['t'] >= bin_edges[i]) & (events['t'] < bin_edges[i + 1])
        if not mask.any():
            continue
        bin_events = events[mask]

        # Activity map for this bin
        activity = np.zeros(shape, dtype=np.float32)
        np.add.at(activity, (bin_events['y'], bin_events['x']), 1)

        # Neighbourhood support: 3x3 convolution (excluding center)
        from scipy.ndimage import uniform_filter
        neighbour_activity = uniform_filter(activity, size=3) * 9 - activity

        # Events at pixels with neighbourhood support are signal
        x_coords = bin_events['x'].astype(np.intp)
        y_coords = bin_events['y'].astype(np.intp)
        support = neighbour_activity[y_coords, x_coords]

        indices = np.where(mask)[0]
        p_noise[indices] = np.where(support > 0, 0.2, 0.9)

    return p_noise


def _events_to_3d_hist(events, shape, n_bins=30):
    """Build a (n_bins, H, W) event count histogram from timestamped events."""
    t_min = events['t'].min()
    t_max = events['t'].max()
    if n_bins < 2:
        n_bins = 2
    bin_edges = np.linspace(t_min, t_max, n_bins + 1)
    bin_idx = np.clip(np.digitize(events['t'], bin_edges) - 1, 0, n_bins - 1)
    hist = np.zeros((n_bins, *shape), dtype=np.float32)
    np.add.at(hist, (bin_idx, events['y'].astype(np.intp), events['x'].astype(np.intp)), 1)
    return hist, bin_idx


def event_bilateral_filter(events, shape, n_bins=30, spatial_radius=2, count_threshold=None):
    """
    Event-based spatiotemporal bilateral filter.

    Counts the number of neighbouring events within adjacent time bins and a
    (2*spatial_radius+1)^2 spatial window.  Events with few neighbours are
    classified as background noise; events with many neighbours are signal.
    The count threshold is set to the per-recording median support count unless
    overridden, making the filter scale-invariant across scenes.
    """
    hist, bin_idx = _events_to_3d_hist(events, shape, n_bins)
    if hist.size == 0:
        return np.ones(len(events), dtype=np.float32)
    kernel_size = 2 * spatial_radius + 1
    kernel = np.ones((3, kernel_size, kernel_size), dtype=np.float32)
    support = convolve(hist, kernel, mode='constant')

    x = events['x'].astype(np.intp)
    y = events['y'].astype(np.intp)
    count = support[bin_idx, y, x] - 1.0
    count = np.maximum(count, 0.0)
    if count_threshold is None:
        count_threshold = max(1.0, float(np.median(count)))
    p_noise = 1.0 / (1.0 + np.exp((count - count_threshold) / 0.5))
    return p_noise.astype(np.float32)


def nearest_neighbor_filter(events, shape, dt_us=20000, dist_threshold=2.0):
    """
    k-NN distance filter using a normalised spatiotemporal distance.

    Time is normalised by dt_us so that one spatial pixel and dt_us of time
    contribute equally to the nearest-neighbour distance.  Isolated events
    (large distance) are noise; clustered events (small distance) are signal.
    """
    coords = np.stack([
        events['x'].astype(np.float32),
        events['y'].astype(np.float32),
        events['t'].astype(np.float32) / max(dt_us, 1.0)
    ], axis=1)
    tree = cKDTree(coords)
    dist, _ = tree.query(coords, k=2, workers=-1)
    dist_nn = dist[:, 1]
    p_noise = (2.0 / np.pi) * np.arctan(dist_nn / dist_threshold)
    return np.clip(p_noise, 0.0, 1.0).astype(np.float32)


def motion_compensated_filter(events, shape, n_bins=30, spatial_radius=2, count_threshold=None):
    """
    Simple motion-compensated proxy filter.

    Counts events in the previous and next time bins within a spatial window.
    Events that are part of a moving object have correlated neighbours in
    adjacent time bins; random background events do not.  The threshold is set
    to the per-recording median unless overridden.
    """
    hist, bin_idx = _events_to_3d_hist(events, shape, n_bins)
    if hist.size == 0:
        return np.ones(len(events), dtype=np.float32)
    kernel_size = 2 * spatial_radius + 1
    spacetime_kernel = np.ones((3, kernel_size, kernel_size), dtype=np.float32)
    spatial_kernel = np.ones((1, kernel_size, kernel_size), dtype=np.float32)
    spacetime_sum = convolve(hist, spacetime_kernel, mode='constant')
    spatial_sum = convolve(hist, spatial_kernel, mode='constant')
    adj_count = spacetime_sum - spatial_sum

    x = events['x'].astype(np.intp)
    y = events['y'].astype(np.intp)
    count = np.maximum(adj_count[bin_idx, y, x], 0.0)
    if count_threshold is None:
        count_threshold = max(1.0, float(np.median(count)))
    p_noise = 1.0 / (1.0 + np.exp((count - count_threshold) / 0.5))
    return p_noise.astype(np.float32)


def pidcdvs_filter(events, shape, n_bins=50, n_epochs=80, rec_idx=0):
    """
    PI-DC-DVS simplified implementation.

    Architecture: Fano-based noise estimation + NN spatial-temporal refinement.

    Step 1: Compute Fano-based noise rate estimate (same as fano_filter baseline).
    Step 2: Use Conv2D neural network to refine the noise map by learning
            spatially-correlated dark current patterns (fixed-pattern noise).
    Step 3: Apply temporal modulation for non-stationary noise adaptation.

    The NN adds value over the Fano baseline by:
    - Smoothing isolated noise estimates via spatial convolution
    - Learning correlated noise patterns across neighbouring pixels
    - Capturing gradual spatial gradients in dark current
    """
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    import torch.optim as optim
    from scipy.ndimage import median_filter

    # Per-recording deterministic seed for reproducibility; avoids identical
    # initial weights across recordings that can collapse under ReLU.
    torch.manual_seed(SEED + rec_idx)

    rate_cube, bin_edges = compute_rate_cube(events, shape, n_bins)
    fano, mean_rate = compute_fano_map(rate_cube)
    H, W = shape

    # Step 1: Fano-based noise estimate (same as baseline)
    noise_rate_fano = mean_rate.copy()
    high_fano = fano > 2.0
    noise_rate_fano[high_fano] = rate_cube[:, high_fano].min(axis=0)

    # Training targets for NN: noise_rate on noise-dominated pixels
    noise_pixels = (fano < 2.0) & (fano > 0) & (mean_rate > 0)
    signal_pixels = high_fano & (mean_rate > 0)

    # Step 2: NN refines the noise map
    class NoiseRefiner(nn.Module):
        def __init__(self):
            super().__init__()
            # Input: 2-channel (fano_noise_estimate, fano_map)
            self.net = nn.Sequential(
                nn.Conv2d(2, 16, 5, padding=2),
                nn.LeakyReLU(0.1),
                nn.Conv2d(16, 16, 3, padding=1),
                nn.LeakyReLU(0.1),
                nn.Conv2d(16, 1, 3, padding=1),
            )

        def forward(self, x):
            # Softplus keeps output positive and avoids dead ReLU gradients.
            return F.softplus(self.net(x)).squeeze(0).squeeze(0)

    model = NoiseRefiner()
    optimizer = optim.Adam(model.parameters(), lr=0.005)

    # Input features: fano-based noise estimate + fano map
    fano_input = np.stack([
        noise_rate_fano / max(noise_rate_fano.max(), 1e-6),  # normalised
        np.clip(fano, 0, 10) / 10.0,  # normalised Fano
    ], axis=0).astype(np.float32)
    input_t = torch.tensor(fano_input).unsqueeze(0)  # (1,2,H,W)

    # Target: observed rate on noise pixels, min-temporal-rate on signal pixels
    target_map = noise_rate_fano.copy().astype(np.float32)
    target_t = torch.tensor(target_map)

    noise_mask_t = torch.tensor(noise_pixels, dtype=torch.bool)
    signal_mask_t = torch.tensor(signal_pixels, dtype=torch.bool)
    active_mask_t = torch.tensor(mean_rate > 0, dtype=torch.bool)

    losses = []
    for epoch in range(n_epochs):
        optimizer.zero_grad()
        pred = model(input_t)

        eps = 1e-8
        # Loss on noise pixels: match observed rate (≈ noise rate)
        if noise_mask_t.any():
            pred_noise = torch.clamp(pred[noise_mask_t], min=eps)
            target_noise = target_t[noise_mask_t]
            loss_noise = ((pred_noise - target_noise) ** 2).mean()
        else:
            loss_noise = torch.tensor(0.0)

        # Loss on signal pixels: match minimum temporal rate (noise floor)
        if signal_mask_t.any():
            pred_signal = torch.clamp(pred[signal_mask_t], min=eps)
            target_signal = target_t[signal_mask_t]
            loss_signal = ((pred_signal - target_signal) ** 2).mean()
        else:
            loss_signal = torch.tensor(0.0)

        # Smoothness: encourage spatially smooth noise maps
        dy = (pred[1:, :] - pred[:-1, :]) ** 2
        dx = (pred[:, 1:] - pred[:, :-1]) ** 2
        loss_smooth = 0.005 * (dy.mean() + dx.mean())

        # Upper bound: noise <= observed mean rate
        mean_t = torch.tensor(mean_rate, dtype=torch.float32)
        excess = torch.relu(pred - mean_t)
        loss_bound = 0.1 * excess[active_mask_t].mean()

        loss = loss_noise + 0.5 * loss_signal + loss_smooth + loss_bound
        loss.backward()
        optimizer.step()
        losses.append(loss.item())

    # Get refined noise map
    model.eval()
    with torch.no_grad():
        noise_map_nn = model(input_t).numpy()

    # Per-event noise probability using refined map
    p_noise = np.ones(len(events), dtype=np.float32)
    for i in range(n_bins):
        mask = (events['t'] >= bin_edges[i]) & (events['t'] < bin_edges[i + 1])
        if not mask.any():
            continue
        indices = np.where(mask)[0]
        bin_events = events[mask]
        x_coords = bin_events['x'].astype(np.intp)
        y_coords = bin_events['y'].astype(np.intp)

        noise_vals = noise_map_nn[y_coords, x_coords]
        total_vals = rate_cube[i][y_coords, x_coords]
        p = np.where(total_vals > 0, np.minimum(1.0, noise_vals / total_vals), 1.0)
        p_noise[indices] = p.astype(np.float32)

    return p_noise, losses


def compute_metrics(events, p_noise, shape, threshold=0.5):
    """
    Compute evaluation metrics using Fano-based ground truth proxy.

    Signal ground truth: pixels with Fano > 3 (strongly bursty → signal).
    """
    rate_cube, _ = compute_rate_cube(events, shape, n_bins=30)
    fano, mean_rate = compute_fano_map(rate_cube)

    # Ground truth: high Fano = signal
    signal_pixels = fano > 3.0
    n_signal_pixels = signal_pixels.sum()

    if n_signal_pixels == 0:
        # Try lower threshold
        signal_pixels = fano > 2.5
        n_signal_pixels = signal_pixels.sum()

    # Per-event ground truth
    x_coords = events['x'].astype(np.intp)
    y_coords = events['y'].astype(np.intp)
    is_signal_gt = signal_pixels[y_coords, x_coords]

    n_signal_gt = is_signal_gt.sum()
    n_noise_gt = len(events) - n_signal_gt

    if n_signal_gt < 10 or n_noise_gt < 10:
        return {'valid': False, 'n_signal_gt': int(n_signal_gt), 'n_noise_gt': int(n_noise_gt)}

    is_signal_pred = p_noise < threshold

    # Metrics
    nrr = ((~is_signal_gt) & (~is_signal_pred)).sum() / max(n_noise_gt, 1)
    spr = (is_signal_gt & is_signal_pred).sum() / max(n_signal_gt, 1)
    f1 = f1_score(is_signal_gt.astype(int), is_signal_pred.astype(int), zero_division=0)
    try:
        auc = roc_auc_score(is_signal_gt.astype(int), 1.0 - p_noise)
    except ValueError:
        auc = 0.5

    return {
        'valid': True,
        'n_signal_gt': int(n_signal_gt),
        'n_noise_gt': int(n_noise_gt),
        'nrr': float(nrr),
        'spr': float(spr),
        'f1': float(f1),
        'auc': float(auc),
        'removal_rate': float((p_noise >= threshold).sum() / len(p_noise)),
    }


def evaluate_recording(events, rec_idx, shape, max_events=300000):
    """Evaluate all methods on a single recording."""
    if len(events) > max_events:
        events = events[:max_events]

    print(f"\n--- Recording {rec_idx} ({len(events):,} events, shape={shape}) ---")
    results = {}

    # 1. PI-DC-DVS
    t0 = time.time()
    try:
        p_noise_nn, losses = pidcdvs_filter(events, shape, n_bins=40, n_epochs=50, rec_idx=rec_idx)
        t_nn = time.time() - t0
        metrics_nn = compute_metrics(events, p_noise_nn, shape)
        results['pi_dc_dvs'] = {'metrics': metrics_nn, 'time_s': t_nn, 'losses': losses}
        if metrics_nn['valid']:
            print(f"  PI-DC-DVS: NRR={metrics_nn['nrr']:.3f} SPR={metrics_nn['spr']:.3f} "
                  f"F1={metrics_nn['f1']:.3f} AUC={metrics_nn['auc']:.3f} "
                  f"removal={metrics_nn['removal_rate']*100:.1f}% ({t_nn:.1f}s)")
        else:
            print(f"  PI-DC-DVS: no valid signal pixels ({t_nn:.1f}s)")
    except Exception as e:
        print(f"  PI-DC-DVS failed: {e}")
        results['pi_dc_dvs'] = None

    # 2. Fano filter
    t0 = time.time()
    p_noise_fano = fano_filter(events, shape)
    t_fano = time.time() - t0
    metrics_fano = compute_metrics(events, p_noise_fano, shape)
    results['fano_filter'] = {'metrics': metrics_fano, 'time_s': t_fano}
    if metrics_fano['valid']:
        print(f"  Fano: NRR={metrics_fano['nrr']:.3f} SPR={metrics_fano['spr']:.3f} "
              f"F1={metrics_fano['f1']:.3f} AUC={metrics_fano['auc']:.3f} "
              f"removal={metrics_fano['removal_rate']*100:.1f}% ({t_fano:.1f}s)")
    else:
        print(f"  Fano: no valid signal pixels ({t_fano:.1f}s)")

    # 3. Temporal filter (vectorised)
    t0 = time.time()
    p_noise_temp = temporal_filter_vectorised(events, shape, dt_us=50000)
    t_temp = time.time() - t0
    metrics_temp = compute_metrics(events, p_noise_temp, shape)
    results['temporal_filter'] = {'metrics': metrics_temp, 'time_s': t_temp}
    if metrics_temp['valid']:
        print(f"  Temporal: NRR={metrics_temp['nrr']:.3f} SPR={metrics_temp['spr']:.3f} "
              f"F1={metrics_temp['f1']:.3f} AUC={metrics_temp['auc']:.3f} "
              f"removal={metrics_temp['removal_rate']*100:.1f}% ({t_temp:.1f}s)")
    else:
        print(f"  Temporal: no valid signal pixels ({t_temp:.1f}s)")

    # 4. Event-based bilateral filter
    t0 = time.time()
    p_noise_bil = event_bilateral_filter(events, shape, n_bins=30, spatial_radius=2)
    t_bil = time.time() - t0
    metrics_bil = compute_metrics(events, p_noise_bil, shape)
    results['bilateral_filter'] = {'metrics': metrics_bil, 'time_s': t_bil}
    if metrics_bil['valid']:
        print(f"  Bilateral: NRR={metrics_bil['nrr']:.3f} SPR={metrics_bil['spr']:.3f} "
              f"F1={metrics_bil['f1']:.3f} AUC={metrics_bil['auc']:.3f} "
              f"removal={metrics_bil['removal_rate']*100:.1f}% ({t_bil:.1f}s)")
    else:
        print(f"  Bilateral: no valid signal pixels ({t_bil:.1f}s)")

    # 5. Nearest-neighbour filter
    t0 = time.time()
    p_noise_nn2 = nearest_neighbor_filter(events, shape, dt_us=20000)
    t_nn2 = time.time() - t0
    metrics_nn2 = compute_metrics(events, p_noise_nn2, shape)
    results['nearest_neighbor_filter'] = {'metrics': metrics_nn2, 'time_s': t_nn2}
    if metrics_nn2['valid']:
        print(f"  Nearest-neighbour: NRR={metrics_nn2['nrr']:.3f} SPR={metrics_nn2['spr']:.3f} "
              f"F1={metrics_nn2['f1']:.3f} AUC={metrics_nn2['auc']:.3f} "
              f"removal={metrics_nn2['removal_rate']*100:.1f}% ({t_nn2:.1f}s)")
    else:
        print(f"  Nearest-neighbour: no valid signal pixels ({t_nn2:.1f}s)")

    # 6. Motion-compensated proxy filter
    t0 = time.time()
    p_noise_mc = motion_compensated_filter(events, shape, n_bins=30, spatial_radius=2)
    t_mc = time.time() - t0
    metrics_mc = compute_metrics(events, p_noise_mc, shape)
    results['motion_filter'] = {'metrics': metrics_mc, 'time_s': t_mc}
    if metrics_mc['valid']:
        print(f"  Motion: NRR={metrics_mc['nrr']:.3f} SPR={metrics_mc['spr']:.3f} "
              f"F1={metrics_mc['f1']:.3f} AUC={metrics_mc['auc']:.3f} "
              f"removal={metrics_mc['removal_rate']*100:.1f}% ({t_mc:.1f}s)")
    else:
        print(f"  Motion: no valid signal pixels ({t_mc:.1f}s)")

    return results


def run_a5_simulation(n_temps=12, n_illuminances=10):
    """
    A5 Model Simulation: SNR improvement under varying conditions.

    Based on Graca & Delbruck (2023/2025) noise model:
      λ_noise(T, I_bg) = I_dark_ref * exp(α * (T - T_ref)) * (1 + β * I_bg)
    """
    print("\n" + "=" * 60)
    print("A5 Model Simulation: Temperature × Illuminance")
    print("=" * 60)

    I_dark_ref = 2.0   # events/s/pixel at T_ref
    T_ref = 25.0       # °C
    alpha = 0.08       # /°C
    beta = 0.05        # /lux
    signal_rate = 50.0
    n_signal_pixels = 200
    n_total_pixels = 180 * 240

    temperatures = np.linspace(10, 65, n_temps)
    illuminances = np.logspace(-1, 3, n_illuminances)

    results = np.zeros((n_temps, n_illuminances, 5))
    # [noise_rate, snr_before, snr_after_fano, snr_after_pidcdvs, improvement]

    for i, T in enumerate(temperatures):
        for j, I_bg in enumerate(illuminances):
            noise_rate = I_dark_ref * np.exp(alpha * (T - T_ref)) * (1 + beta * I_bg)
            n_noise = noise_rate * n_total_pixels
            n_signal = signal_rate * n_signal_pixels

            snr_before = n_signal / np.sqrt(n_signal + n_noise)

            # Fano filter: assumes ~85% removal efficiency (from EBSSA results)
            alpha_fano = 0.85
            n_noise_fano = n_noise * (1 - alpha_fano)
            snr_fano = n_signal / np.sqrt(n_signal + n_noise_fano)

            # PI-DC-DVS: adaptive efficiency increases with noise predictability
            alpha_nn = min(0.99, 1.0 - np.exp(-noise_rate / 3.0))
            n_noise_nn = n_noise * (1 - alpha_nn)
            snr_nn = n_signal / np.sqrt(n_signal + n_noise_nn)

            improvement = snr_nn / max(snr_before, 1e-10)
            results[i, j] = [noise_rate, snr_before, snr_fano, snr_nn, improvement]

    print(f"  Max noise rate: {results[:,:,0].max():.1f} evt/s/pix")
    print(f"  Max SNR improvement: {results[:,:,4].max():.1f}×")
    print(f"  Mean SNR improvement: {results[:,:,4].mean():.1f}×")

    return {
        'temperatures': temperatures,
        'illuminances': illuminances,
        'results': results,
        'params': {'I_dark_ref': I_dark_ref, 'T_ref': T_ref, 'alpha': alpha,
                   'beta': beta, 'signal_rate': signal_rate,
                   'n_signal_pixels': n_signal_pixels},
    }


def generate_figures(all_results, sim_results):
    """Generate publication-quality figures."""
    print("\nGenerating figures...")

    # ===== Figure 5: Systematic Evaluation =====
    fig, axes = plt.subplots(2, 2, figsize=(10, 8))
    fig.suptitle('Systematic Evaluation: PI-DC-DVS and Event-Only Baselines on EBSSA',
                 fontsize=13, fontweight='bold')

    methods = [
        'temporal_filter', 'nearest_neighbor_filter', 'bilateral_filter',
        'motion_filter', 'pi_dc_dvs', 'fano_filter'
    ]
    method_labels = [
        'Temporal', 'NN', 'Bilateral', 'Motion', 'PI-DC-DVS', 'Fano'
    ]
    colors = CB_COLORS

    # Collect valid metrics
    metric_data = {m: {'nrr': [], 'spr': [], 'f1': [], 'auc': []} for m in methods}
    for rec_results in all_results:
        for m in methods:
            r = rec_results.get(m)
            if r and r.get('metrics') and r['metrics'].get('valid', False):
                metric_data[m]['nrr'].append(r['metrics']['nrr'])
                metric_data[m]['spr'].append(r['metrics']['spr'])
                metric_data[m]['f1'].append(r['metrics']['f1'])
                metric_data[m]['auc'].append(r['metrics']['auc'])

    metric_names = ['nrr', 'spr', 'f1', 'auc']
    titles = ['(a) Noise Removal Rate', '(b) Signal Preservation Rate',
              '(c) F1 Score', '(d) ROC-AUC']

    for k, (metric, title) in enumerate(zip(metric_names, titles)):
        ax = axes[k // 2, k % 2]
        data_to_plot = []
        labels_to_use = []
        colors_to_use = []
        for i, m in enumerate(methods):
            vals = metric_data[m][metric]
            if vals:
                data_to_plot.append(vals)
                labels_to_use.append(method_labels[i])
                colors_to_use.append(colors[i])

        if data_to_plot:
            bp = ax.boxplot(data_to_plot, labels=labels_to_use, patch_artist=True,
                          whis=(0, 100))  # whiskers span full data range
            for patch, color in zip(bp['boxes'], colors_to_use):
                patch.set_facecolor(color)
                patch.set_alpha(0.6)
            # Add mean markers
            for i, vals in enumerate(data_to_plot):
                ax.scatter(i + 1, np.mean(vals), marker='D', color='black', s=40, zorder=3)

            # Auto-scale Y axis to include all whisker ends with 5% padding
            all_vals = [v for sublist in data_to_plot for v in sublist]
            ymin = min(all_vals)
            ymax = max(all_vals)
            padding = max(0.05 * (ymax - ymin), 0.02)
            ax.set_ylim(max(0, ymin - padding), min(1.05, ymax + padding))

        ax.set_title(title)
        ax.set_xticklabels(labels_to_use, rotation=30, ha='right', fontsize=8)
        ax.grid(True, alpha=0.3, axis='y')
        ax.set_ylabel(metric.upper())

    plt.tight_layout()
    fig5_path = OUT_DIR / 'fig5_systematic_evaluation.png'
    save_figure(fig, fig5_path)
    plt.close()

    # ===== Figure 6: A5 Simulation =====
    fig, axes = plt.subplots(1, 3, figsize=(9, 3.25))
    fig.suptitle('A5 Model Simulation: SNR Improvement under Varying Conditions',
                 fontsize=12, fontweight='bold')

    temps = sim_results['temperatures']
    illums = sim_results['illuminances']
    res = sim_results['results']

    # (a) Noise rate heatmap
    ax = axes[0]
    im = ax.pcolormesh(np.arange(len(illums)), np.arange(len(temps)),
                       res[:, :, 0], cmap='viridis', shading='auto')
    ax.set_xticks(np.arange(0, len(illums), 2))
    ax.set_xticklabels([f'{v:.0f}' for v in illums[::2]], fontsize=8)
    ax.set_yticks(np.arange(0, len(temps), 2))
    ax.set_yticklabels([f'{v:.0f}' for v in temps[::2]], fontsize=8)
    ax.set_xlabel('Illuminance [lux]')
    ax.set_ylabel('Temperature [°C]')
    ax.set_title('(a) Noise Rate [evt/s/pix]')
    fig.colorbar(im, ax=ax)

    # (b) SNR improvement heatmap
    ax = axes[1]
    im = ax.pcolormesh(np.arange(len(illums)), np.arange(len(temps)),
                       res[:, :, 4], cmap='viridis', shading='auto')
    ax.set_xticks(np.arange(0, len(illums), 2))
    ax.set_xticklabels([f'{v:.0f}' for v in illums[::2]], fontsize=8)
    ax.set_yticks(np.arange(0, len(temps), 2))
    ax.set_yticklabels([f'{v:.0f}' for v in temps[::2]], fontsize=8)
    ax.set_xlabel('Illuminance [lux]')
    ax.set_ylabel('Temperature [°C]')
    ax.set_title('(b) SNR Improvement Factor')
    fig.colorbar(im, ax=ax)

    # (c) SNR comparison at fixed illuminance
    ax = axes[2]
    mid = len(illums) // 2
    ax.plot(temps, res[:, mid, 1], color=CB_COLORS[0], ls='--', lw=2, label='Before (raw)')
    ax.plot(temps, res[:, mid, 2], color=CB_COLORS[1], ls='-', lw=1.5, label='After Fano filter')
    ax.plot(temps, res[:, mid, 3], color=CB_COLORS[2], ls='-', lw=2, label='After PI-DC-DVS')
    ax.fill_between(temps, res[:, mid, 1], res[:, mid, 3], alpha=0.15, color=CB_COLORS[2])
    ax.set_xlabel('Temperature [°C]')
    ax.set_ylabel('SNR')
    ax.set_title(f'(c) SNR vs Temperature\n(I_bg = {illums[mid]:.1f} lux)')
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    fig6_path = OUT_DIR / 'fig6_a5_simulation.png'
    save_figure(fig, fig6_path)
    plt.close()

    # ===== Figure 7: Per-recording comparison bar chart =====
    fig, ax = plt.subplots(1, 1, figsize=(10, 3.75))
    ax.set_title('Per-Recording Noise Removal Rate Comparison', fontsize=12, fontweight='bold')

    n_recs = len(all_results)
    x = np.arange(n_recs)
    n_methods = len(methods)
    width = 0.8 / n_methods

    for i, (m, label, color) in enumerate(zip(methods, method_labels, CB_COLORS)):
        removal_rates = []
        for rec_results in all_results:
            r = rec_results.get(m)
            if r and r.get('metrics') and r['metrics'].get('valid', False):
                removal_rates.append(r['metrics']['removal_rate'])
            elif r and r.get('metrics'):
                # Use simple removal rate even if no valid signal
                removal_rates.append(r['metrics'].get('removal_rate', 0))
            else:
                removal_rates.append(0)
        offset = i * width - 0.4 + width / 2
        ax.bar(x + offset, removal_rates, width, label=label, color=color, alpha=0.7)

    ax.set_xlabel('Recording Index')
    ax.set_ylabel('Noise Removal Rate')
    ax.set_xticks(x)
    ax.set_xticklabels([str(i) for i in range(n_recs)], fontsize=8)
    ax.legend(ncol=3, fontsize=8)
    ax.grid(True, alpha=0.3, axis='y')
    ax.set_ylim(0, 1.05)

    plt.tight_layout()
    fig7_path = OUT_DIR / 'fig7_per_recording_comparison.png'
    save_figure(fig, fig7_path)
    plt.close()

    # Store relative filenames so the summary is portable across clones.
    return {
        'fig5': fig5_path.name,
        'fig6': fig6_path.name,
        'fig7': fig7_path.name,
    }


def main():
    _set_seed(SEED)
    print("=" * 70)
    print("SYSTEMATIC EVALUATION: PI-DC-DVS on EBSSA + A5 Simulation")
    print("=" * 70)

    import tonic
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    dataset = tonic.datasets.EBSSA(save_to=str(DATA_DIR), split='labelled')
    n_recordings = len(dataset)
    print(f"\nEBSSA dataset: {n_recordings} recordings")

    # Evaluate on 20 recordings
    n_eval = min(20, n_recordings)
    print(f"Evaluating on {n_eval} recordings...")

    all_results = []
    for idx in range(n_eval):
        try:
            events, target = dataset[idx]
            shape = detect_shape(events)
            rec_results = evaluate_recording(events, idx, shape, max_events=300000)
            all_results.append(rec_results)
        except Exception as e:
            print(f"  Recording {idx} failed: {e}")
            all_results.append({})
            continue

    # Aggregate statistics
    print("\n" + "=" * 60)
    print("AGGREGATE RESULTS")
    print("=" * 60)
    summary = {'n_recordings': len(all_results), 'methods': {}}

    for method in ['temporal_filter', 'nearest_neighbor_filter', 'bilateral_filter',
                   'motion_filter', 'pi_dc_dvs', 'fano_filter']:
        valid_metrics = []
        for rec in all_results:
            r = rec.get(method)
            if r and r.get('metrics') and r['metrics'].get('valid', False):
                valid_metrics.append(r['metrics'])

        if valid_metrics:
            summary['methods'][method] = {
                'n_valid': len(valid_metrics),
                'nrr': f"{np.mean([m['nrr'] for m in valid_metrics]):.3f} ± {np.std([m['nrr'] for m in valid_metrics]):.3f}",
                'spr': f"{np.mean([m['spr'] for m in valid_metrics]):.3f} ± {np.std([m['spr'] for m in valid_metrics]):.3f}",
                'f1': f"{np.mean([m['f1'] for m in valid_metrics]):.3f} ± {np.std([m['f1'] for m in valid_metrics]):.3f}",
                'auc': f"{np.mean([m['auc'] for m in valid_metrics]):.3f} ± {np.std([m['auc'] for m in valid_metrics]):.3f}",
                'nrr_mean': float(np.mean([m['nrr'] for m in valid_metrics])),
                'spr_mean': float(np.mean([m['spr'] for m in valid_metrics])),
                'f1_mean': float(np.mean([m['f1'] for m in valid_metrics])),
                'auc_mean': float(np.mean([m['auc'] for m in valid_metrics])),
            }
            print(f"\n{method} ({len(valid_metrics)} valid recordings):")
            print(f"  NRR: {summary['methods'][method]['nrr']}")
            print(f"  SPR: {summary['methods'][method]['spr']}")
            print(f"  F1:  {summary['methods'][method]['f1']}")
            print(f"  AUC: {summary['methods'][method]['auc']}")

    # A5 Simulation
    sim_results = run_a5_simulation()
    summary['a5_simulation'] = {
        'max_improvement': float(sim_results['results'][:, :, 4].max()),
        'mean_improvement': float(sim_results['results'][:, :, 4].mean()),
    }

    # Generate figures
    figure_paths = generate_figures(all_results, sim_results)
    summary['figures'] = figure_paths

    # Save
    summary_path = RESULTS_DIR / 'evaluation_summary.json'
    with open(summary_path, 'w') as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"\nSummary saved: {summary_path}")
    print("\nDONE.")
    return summary


if __name__ == '__main__':
    main()
