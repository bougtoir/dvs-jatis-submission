#!/usr/bin/env python3
"""Qualitative example: the same two EBSSA recordings processed by every stage
of the pipeline reported in Sec. 3.7.

For each sensor model the recording whose zero-knowledge stage-1 AUC is closest
to the median of that sensor (from results/hybrid_screening.json) is selected,
so the example is neither the best nor the worst case.  Each method scores the
whole stream, is thresholded at its own alpha-quantile (Eq. 5, no labels) and
the events it passes are accumulated into a per-pixel count image.

Outputs
    results/qualitative_example.npz   per-pixel count images (raw, object, and
                                      passed events per method) for both recordings
    results/qualitative_example.json  recording ids, thresholds and pass statistics
"""

import json
from pathlib import Path

import numpy as np

import event_driven as ed
import hybrid_screening as hs
import sp_evaluation as sp

HERE = Path(__file__).resolve().parent
RESULTS = HERE / 'results'
ALPHA = 0.01
SCREEN_F = 0.1

METHODS = ('proposal_zk', 'ynoise_default', 'ynoise_handoff', 'two_stage', 'fano')


def select_recordings():
    saved = json.load(open(hs.PARTIAL))
    recs = saved['records']
    chosen = {}
    for shape in sorted({tuple(r['shape']) for r in recs}):
        rr = [r for r in recs if tuple(r['shape']) == shape]
        med = np.median([r['stage1']['auc'] for r in rr])
        best = min(rr, key=lambda r: abs(r['stage1']['auc'] - med))
        chosen[best['idx']] = f'{shape[0]}x{shape[1]}'
    return chosen


def count_image(events, mask, shape):
    img = np.zeros(shape, dtype=np.int32)
    np.add.at(img, (events['y'][mask].astype(np.intp), events['x'][mask].astype(np.intp)), 1)
    return img


def pass_at_alpha(p, alpha):
    """Events below the alpha-quantile; if the quantile falls inside a block of
    tied scores (fewer than half the requested count pass), the whole block is
    passed, as in hybrid_screening.score_pass."""
    thr = np.quantile(p, alpha)
    keep = p < thr
    if keep.sum() < 0.5 * alpha * len(p):
        keep = p <= thr
    return keep


def main():
    chosen = select_recordings()
    zc = sp.ZERO_KNOWLEDGE_CONFIG
    images, meta = {}, {'alpha': ALPHA, 'screen_fraction': SCREEN_F, 'recordings': {}}
    for idx, events, labels, shape, rec_id in sp.load_recordings():
        if idx not in chosen:
            continue
        key = chosen[idx]
        rate, window_us = ed.adaptive_window_us(
            events, shape, zc['radius'], zc['expected_count'], zc['interval_quantile'])
        p1 = np.asarray(ed.sliding_window_lr(events, shape, radius=zc['radius'],
                                             window_us=window_us, rate=rate), dtype=np.float64)
        py_def = np.asarray(hs.ynoise_on(events, shape, **hs.YNOISE_DEFAULT), dtype=np.float64)
        py_hand = np.asarray(hs.ynoise_on(events, shape, window_us, zc['radius']), dtype=np.float64)
        thr = np.quantile(p1, SCREEN_F)
        cand = p1 < thr
        if cand.sum() < 0.5 * SCREEN_F * len(p1):
            cand = p1 <= thr
        p2 = np.asarray(hs.ynoise_on(events[cand], shape, window_us, zc['radius']), dtype=np.float64)
        p_two = hs.combined(p1, cand, p2)
        p_fano = np.asarray(sp.fano_filter(events, shape), dtype=np.float64)
        scores = {'proposal_zk': p1, 'ynoise_default': py_def,
                  'ynoise_handoff': py_hand, 'two_stage': p_two, 'fano': p_fano}
        all_mask = np.ones(len(events), dtype=bool)
        images[f'{key}_raw'] = count_image(events, all_mask, shape)
        images[f'{key}_object'] = count_image(events, labels, shape)
        m = {'idx': int(idx), 'recording_id': str(rec_id), 'shape': [int(s) for s in shape],
             'n_events': int(len(events)), 'n_object': int(labels.sum()),
             'window_ms': window_us / 1e3, 'duration_s': float(events['t'][-1] - events['t'][0]) / 1e6,
             'methods': {}}
        for name, p in scores.items():
            keep = pass_at_alpha(p, ALPHA)
            images[f'{key}_{name}'] = count_image(events, keep, shape)
            m['methods'][name] = {'n_passed': int(keep.sum()),
                                  'fpr': float(keep[~labels].mean()),
                                  'tpr': float(keep[labels].mean()),
                                  'auc': hs.auc(labels, p)}
        meta['recordings'][key] = m
        print(f'rec {idx} {rec_id} {key} W={window_us / 1e3:.0f} ms',
              {k: round(v['tpr'], 3) for k, v in m['methods'].items()}, flush=True)
    np.savez_compressed(RESULTS / 'qualitative_example.npz', **images)
    json.dump(meta, open(RESULTS / 'qualitative_example.json', 'w'), indent=1)
    print('wrote', RESULTS / 'qualitative_example.npz', RESULTS / 'qualitative_example.json')


if __name__ == '__main__':
    main()
