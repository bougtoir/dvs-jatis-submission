#!/usr/bin/env python3
"""False-alarm calibration and threshold transfer across recordings.

A detector that reaches a given ROC-AUC is not necessarily usable: operating
it requires choosing a decision threshold, and in space surveillance the
threshold is dictated by the false-alarm rate that the downstream tracker can
absorb.  The proposed test outputs the survival probability of the observed
neighbourhood count under the per-pixel Poisson background, so a threshold can
be set analytically from the nominal false-alarm rate, with no labelled data
and no per-recording tuning.  Heuristic neighbour counts (YNoise, bilateral,
double window) output an uncalibrated score, so their threshold has to be
fitted on labelled recordings and transferred to unseen ones.

This script quantifies the difference on the EBSSA recordings:

* calibration  -- nominal versus achieved false-alarm rate of the analytic
  threshold, per recording;
* transfer     -- achieved false-alarm rate when a threshold fitted on the
  training recordings of a grouped split is applied to the held-out ones;
* stability    -- dispersion of the achieved false-alarm rate across
  recordings, which is what determines whether a single operating point can
  be fixed for a campaign.
"""

import json
from pathlib import Path

import numpy as np

import event_driven as ed
import sp_evaluation as sp

ALPHAS = (1e-3, 1e-2, 5e-2, 1e-1)
RESULTS = Path(__file__).resolve().parent / 'results'

# Heuristic comparators: noise probability in [0, 1], signal score is 1 - p.
HEURISTICS = {
    'ynoise': lambda ev, shape: sp.ynoise_filter(ev, shape),
    'bilateral_ed': lambda ev, shape: ed.bilateral_event_driven(ev, shape, radius=3,
                                                                window_us=100e3),
    'knoise': lambda ev, shape: sp.knoise_filter(ev, shape),
}


def achieved_fpr(signal_score, labels, thr):
    """Fraction of true noise events declared signal at ``thr``."""
    noise = ~labels
    if noise.sum() == 0:
        return np.nan
    return float((signal_score[noise] >= thr).mean())


def detection_rate(signal_score, labels, thr):
    if labels.sum() == 0:
        return np.nan
    return float((signal_score[labels] >= thr).mean())


def threshold_for_fpr(signal_score, labels, alpha):
    """Empirical threshold achieving ``alpha`` false-alarm rate on labels."""
    noise = signal_score[~labels]
    if noise.size == 0:
        return np.inf
    return float(np.quantile(noise, 1.0 - alpha))


def main():
    recs = list(sp.load_recordings())
    print(f'{len(recs)} recordings')

    per_rec = []
    for name, events, labels, shape, _ in recs:
        row = {'recording': name, 'n_events': int(len(events))}

        # Proposed: the score is a p-value under the background hypothesis, so
        # the threshold follows from the nominal false-alarm rate alone.
        pval = ed.sliding_window_lr(events, shape, radius=3, window_us=150e3,
                                    interval_quantile=0.99)
        row['proposed'] = {
            f'{a:g}': {
                'fpr': achieved_fpr(-pval, labels, -a),
                'tpr': detection_rate(-pval, labels, -a),
            }
            for a in ALPHAS
        }

        for key, fn in HEURISTICS.items():
            score = 1.0 - np.asarray(fn(events, shape), dtype=np.float64)
            row[key] = {
                'scores_quantile': {
                    f'{a:g}': threshold_for_fpr(score, labels, a) for a in ALPHAS
                },
                'tpr_at_oracle': {
                    f'{a:g}': detection_rate(
                        score, labels, threshold_for_fpr(score, labels, a))
                    for a in ALPHAS
                },
            }
            row[f'{key}_score_cache'] = score
        row['labels_cache'] = labels
        per_rec.append(row)
        print(f'  {name}: calibrated fpr @1e-2 = '
              f"{row['proposed']['0.01']['fpr']:.4f}", flush=True)

    # Threshold transfer: leave-one-recording-out.  The heuristic threshold is
    # the median of the thresholds that hit the nominal rate on the other
    # recordings; the proposed threshold is the nominal rate itself.
    transfer = {k: {f'{a:g}': [] for a in ALPHAS} for k in HEURISTICS}
    for i, row in enumerate(per_rec):
        labels = row['labels_cache']
        for key in HEURISTICS:
            score = row[f'{key}_score_cache']
            for a in ALPHAS:
                others = [r[key]['scores_quantile'][f'{a:g}']
                          for j, r in enumerate(per_rec) if j != i]
                thr = float(np.median(others))
                transfer[key][f'{a:g}'].append(achieved_fpr(score, labels, thr))

    summary = {'n_recordings': len(per_rec), 'alphas': list(ALPHAS),
               'proposed': {}, 'heuristics': {}}
    for a in ALPHAS:
        key = f'{a:g}'
        fpr = np.array([r['proposed'][key]['fpr'] for r in per_rec], dtype=float)
        tpr = np.array([r['proposed'][key]['tpr'] for r in per_rec], dtype=float)
        summary['proposed'][key] = {
            'nominal': a,
            'fpr_median': float(np.nanmedian(fpr)),
            'fpr_iqr': [float(np.nanpercentile(fpr, 25)),
                        float(np.nanpercentile(fpr, 75))],
            'fpr_log10_error_median': float(np.nanmedian(
                np.abs(np.log10(np.maximum(fpr, 1e-9) / a)))),
            'tpr_median': float(np.nanmedian(tpr)),
        }
    for hkey in HEURISTICS:
        summary['heuristics'][hkey] = {}
        for a in ALPHAS:
            key = f'{a:g}'
            fpr = np.array(transfer[hkey][key], dtype=float)
            summary['heuristics'][hkey][key] = {
                'nominal': a,
                'fpr_median': float(np.nanmedian(fpr)),
                'fpr_iqr': [float(np.nanpercentile(fpr, 25)),
                            float(np.nanpercentile(fpr, 75))],
                'fpr_log10_error_median': float(np.nanmedian(
                    np.abs(np.log10(np.maximum(fpr, 1e-9) / a)))),
            }

    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / 'calibration_advantage.json'
    out.write_text(json.dumps(summary, indent=2))

    print('\n=== nominal vs achieved false-alarm rate (median over recordings) ===')
    print(f"{'alpha':>8} {'proposed':>12} " +
          ' '.join(f'{k:>14}' for k in HEURISTICS))
    for a in ALPHAS:
        key = f'{a:g}'
        line = f"{a:>8g} {summary['proposed'][key]['fpr_median']:>12.4f} "
        line += ' '.join(
            f"{summary['heuristics'][k][key]['fpr_median']:>14.4f}"
            for k in HEURISTICS)
        print(line)
    print('\nmedian |log10(achieved/nominal)| (0 = perfectly calibrated)')
    for a in ALPHAS:
        key = f'{a:g}'
        line = f"{a:>8g} {summary['proposed'][key]['fpr_log10_error_median']:>12.2f} "
        line += ' '.join(
            f"{summary['heuristics'][k][key]['fpr_log10_error_median']:>14.2f}"
            for k in HEURISTICS)
        print(line)
    print(f'\nwrote {out}')


if __name__ == '__main__':
    main()
