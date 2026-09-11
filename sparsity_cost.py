#!/usr/bin/env python3
"""
Computational cost of frame-based and event-driven noise discriminants as a
function of scene sparsity.

Space surveillance streams are extremely sparse: a transiting object activates
a handful of pixels while the rest of the array stays silent.  A voxelised
discriminant allocates and traverses a dense (time bin x height x width) array
whose size is fixed by the sensor and the temporal discretisation, so its cost
is the same whether the scene contains a million events or a thousand.  An
event-driven discriminant touches only the pixels that fired, so its cost
follows the event count.

This script measures that difference on real EBSSA recordings.  Sparsity is
varied by thinning the event stream, which preserves the spatial and temporal
structure of the data while reducing occupancy, and by evaluating the same
recording at several sensor resolutions.  Reported quantities are wall-clock
time per event, throughput, and peak memory attributable to the discriminant
(measured with tracemalloc, which counts Python-level allocations including the
NumPy buffers allocated by the filters).

Outputs: results/sparsity_cost.json
"""

import json
import time
import tracemalloc
from pathlib import Path

import numpy as np

import event_driven as ed
import sp_evaluation as sp

OUT_DIR = Path(__file__).resolve().parent
RESULTS_DIR = OUT_DIR / 'results'
SEED = 42
KEEP_FRACTIONS = (1.0, 0.5, 0.25, 0.1, 0.05, 0.02, 0.01)
N_REPEATS = 3

# The two implementations of the identical test statistic, plus the established
# comparators, each at the configuration selected by the grouped cross-validation
# of sp_evaluation.py (modal choice over folds).
FAMILY = {
    'edlr': 'event-driven',
    'plr': 'frame-based',
    'bilateral': 'frame-based',
    'bilateral_ed': 'event-driven',
    'ynoise': 'event-driven',
    'temporal': 'frame-based',
}


def build_methods(summary):
    methods = {}
    configs = {}
    for name, family in FAMILY.items():
        fn, configs[name] = sp.tuned(name, summary)
        methods[name] = (family, fn)
    methods['recursive'] = ('event-driven', lambda ev, shape: ed.recursive_rate(ev, shape))
    # the zero-knowledge deployment form of the proposal: rate-adaptive window,
    # nothing tuned on any labelled data; the cost includes the window choice
    configs['edlr_zk'] = sp.ZERO_KNOWLEDGE_CONFIG
    methods['edlr_zk'] = ('event-driven',
                          lambda ev, shape: ed.adaptive_window_lr(ev, shape, **sp.ZERO_KNOWLEDGE_CONFIG))
    return methods, configs


def thin(events, fraction, rng):
    """Keep a random subset of events, preserving temporal order."""
    if fraction >= 1.0:
        return events
    n_keep = max(int(round(len(events) * fraction)), 100)
    sel = np.sort(rng.choice(len(events), n_keep, replace=False))
    return events[sel]


def measure(fn, events, shape):
    """Wall-clock time per event and peak allocated memory of one filter call."""
    fn(events[:2000], shape)  # warm up the JIT so compilation is not measured
    tracemalloc.start()
    best = float('inf')
    for _ in range(N_REPEATS):
        t0 = time.perf_counter()
        fn(events, shape)
        best = min(best, time.perf_counter() - t0)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return best, peak


def main(max_recordings=6):
    RESULTS_DIR.mkdir(exist_ok=True)
    with open(RESULTS_DIR / 'sp_evaluation_summary.json') as f:
        METHODS, configs = build_methods(json.load(f))
    rng = np.random.default_rng(SEED)
    rows = []
    for idx, events, _labels, shape, rec_id in sp.load_recordings(max_recordings):
        duration_s = float((events['t'].max() - events['t'].min()) / 1e6)
        n_pixels = shape[0] * shape[1]
        for frac in KEEP_FRACTIONS:
            sub = thin(events, frac, rng)
            occupancy = float(len(np.unique(sub['y'].astype(np.int64) * shape[1]
                                            + sub['x'].astype(np.int64))) / n_pixels)
            for name, (family, fn) in METHODS.items():
                elapsed, peak = measure(fn, sub, shape)
                rows.append({
                    'recording_id': str(rec_id),
                    'recording_idx': int(idx),
                    'shape': [int(shape[0]), int(shape[1])],
                    'n_pixels': int(n_pixels),
                    'duration_s': duration_s,
                    'keep_fraction': float(frac),
                    'n_events': int(len(sub)),
                    'pixel_occupancy': occupancy,
                    'events_per_pixel': float(len(sub) / n_pixels),
                    'method': name,
                    'family': family,
                    'seconds': float(elapsed),
                    'ns_per_event': float(elapsed / len(sub) * 1e9),
                    'events_per_second': float(len(sub) / elapsed),
                    'peak_bytes': int(peak),
                })
            print(f"rec {idx} frac={frac:<5g} n={len(sub):>7d} "
                  + " ".join(f"{r['method']}={r['ns_per_event']:.0f}ns"
                             for r in rows[-len(METHODS):]), flush=True)

    # scaling exponent of cost with respect to the event count: a cost that is
    # dominated by the dense grid is flat (exponent near zero), a cost that
    # follows the events is linear (exponent near one)
    scaling = {}
    for name, (family, _) in METHODS.items():
        by_rec = []
        for idx in sorted({r['recording_idx'] for r in rows}):
            sel = [r for r in rows if r['method'] == name and r['recording_idx'] == idx]
            if len(sel) < 3:
                continue
            x = np.log(np.array([r['n_events'] for r in sel], dtype=float))
            y = np.log(np.array([r['seconds'] for r in sel], dtype=float))
            by_rec.append(float(np.polyfit(x, y, 1)[0]))
        sparsest = [r for r in rows if r['method'] == name
                    and r['keep_fraction'] == min(KEEP_FRACTIONS)]
        densest = [r for r in rows if r['method'] == name
                   and r['keep_fraction'] == 1.0]
        scaling[name] = {
            'family': family,
            'cost_exponent_mean': float(np.mean(by_rec)) if by_rec else float('nan'),
            'cost_exponent_std': float(np.std(by_rec)) if by_rec else float('nan'),
            'ns_per_event_dense': float(np.mean([r['ns_per_event'] for r in densest])),
            'ns_per_event_sparse': float(np.mean([r['ns_per_event'] for r in sparsest])),
            'seconds_dense': float(np.mean([r['seconds'] for r in densest])),
            'seconds_sparse': float(np.mean([r['seconds'] for r in sparsest])),
            'peak_bytes_dense': float(np.mean([r['peak_bytes'] for r in densest])),
            'peak_bytes_sparse': float(np.mean([r['peak_bytes'] for r in sparsest])),
        }

    out = {
        'seed': SEED,
        'keep_fractions': list(KEEP_FRACTIONS),
        'n_repeats': N_REPEATS,
        'note': ('Sparsity is varied by thinning the real event stream; the '
                 'sensor array and the recording interval are unchanged, so '
                 'the voxel grid of the frame-based methods keeps its size.'),
        'methods': {k: v[0] for k, v in METHODS.items()},
        'configs': configs,
        'scaling': scaling,
        'measurements': rows,
    }
    with open(RESULTS_DIR / 'sparsity_cost.json', 'w') as f:
        json.dump(out, f, indent=2)

    print('\n=== Cost scaling with event count (exponent 1 = follows events, '
          '0 = fixed by the grid) ===')
    for name, s in sorted(scaling.items(), key=lambda kv: kv[1]['cost_exponent_mean']):
        print(f"{name:14s} {s['family']:13s} exponent={s['cost_exponent_mean']:.2f}"
              f" +/- {s['cost_exponent_std']:.2f}"
              f"  dense={s['ns_per_event_dense']:8.0f} ns/ev"
              f"  sparse={s['ns_per_event_sparse']:9.0f} ns/ev"
              f"  peak_sparse={s['peak_bytes_sparse'] / 1e6:7.1f} MB")
    return out


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--max-recordings', type=int, default=6)
    args = parser.parse_args()
    main(args.max_recordings)
