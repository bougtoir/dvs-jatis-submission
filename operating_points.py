#!/usr/bin/env python3
"""Operating-point analysis of the frame-free detectors on EBSSA.

ROC-AUC summarises a detector over every threshold, but a surveillance
pipeline runs at one operating point chosen by the false-alarm rate that the
downstream tracker tolerates.  Two quantities characterise a detector there:

* partial AUC  -- area under the ROC curve restricted to the low
  false-positive-rate region (standardised McClish form, 0.5 = chance);
* self-calibrated operating point -- the threshold is the ``alpha`` quantile
  of the detector's own noise-probability output on the recording, i.e. the
  detector is asked to flag a fraction ``alpha`` of the stream.  No labels are
  used to set the threshold.  The achieved false-alarm rate on the labelled
  noise events shows how faithfully each score family (continuous p-value
  versus integer neighbour count) can be steered to a requested budget.

Every grid-tuned detector (the proposed one and the comparators) is run with
the configuration selected most often across the grouped cross-validation
folds of sp_evaluation.py, read from results/sp_evaluation_summary.json; no
per-fold selection is repeated here, so full AUCs differ slightly from the
cross-validated values.

Outputs: results/partial_auc.json, results/self_calibration.json
"""

import json
from pathlib import Path

import numpy as np
from sklearn.metrics import roc_auc_score

import event_driven as ed
import sp_evaluation as sp

RESULTS = Path(__file__).resolve().parent / 'results'


PAUC_NAMES = ('edlr', 'ynoise', 'bilateral_ed', 'bilateral', 'knoise', 'dwf')
SELFCAL_NAMES = ('edlr', 'ynoise', 'bilateral_ed')

ALPHAS = (1e-3, 1e-2, 5e-2)


def main():
    summary = json.load(open(RESULTS / 'sp_evaluation_summary.json'))
    configs = {}
    PAUC_METHODS = {}
    for name in PAUC_NAMES:
        PAUC_METHODS[name], configs[name] = sp.tuned(name, summary)
    SELFCAL_METHODS = {name: PAUC_METHODS[name] for name in SELFCAL_NAMES}
    SELFCAL_METHODS['multiscale'] = lambda e, s: ed.multiscale_lr(e, s)
    pauc = {k: {'p001': [], 'p01': [], 'auc': []} for k in PAUC_METHODS}
    selfcal = {k: {f'{a:g}': {'fpr': [], 'tpr': []} for a in ALPHAS} for k in SELFCAL_METHODS}
    for _, events, labels, shape, _ in sp.load_recordings():
        if labels.sum() == 0 or (~labels).sum() == 0:
            continue
        cache = {}
        for name, fn in {**PAUC_METHODS, **SELFCAL_METHODS}.items():
            if name not in cache:
                cache[name] = np.asarray(fn(events, shape), dtype=np.float64)
        for name in PAUC_METHODS:
            score = 1.0 - cache[name]
            pauc[name]['auc'].append(roc_auc_score(labels, score))
            pauc[name]['p01'].append(roc_auc_score(labels, score, max_fpr=0.01))
            pauc[name]['p001'].append(roc_auc_score(labels, score, max_fpr=0.001))
        for name in SELFCAL_METHODS:
            p_noise = cache[name]
            for a in ALPHAS:
                thr = np.quantile(p_noise, a)
                flagged = p_noise < thr  # strict: ties at the quantile never overshoot alpha
                selfcal[name][f'{a:g}']['fpr'].append(float(flagged[~labels].mean()))
                selfcal[name][f'{a:g}']['tpr'].append(float(flagged[labels].mean()))

    out_pauc = {k: {m: float(np.mean(v[m])) for m in v} for k, v in pauc.items()}
    out_pauc['_configs'] = configs
    out_selfcal = {
        k: {a: {'fpr_median': float(np.median(v[a]['fpr'])),
                'tpr_median': float(np.median(v[a]['tpr']))} for a in v}
        for k, v in selfcal.items()
    }
    RESULTS.mkdir(exist_ok=True)
    json.dump(out_pauc, open(RESULTS / 'partial_auc.json', 'w'), indent=2)
    json.dump(out_selfcal, open(RESULTS / 'self_calibration.json', 'w'), indent=2)
    print('method        AUC   pAUC@1e-2  pAUC@1e-3')
    for k, v in out_pauc.items():
        if k.startswith('_'):
            continue
        print(f"{k:13s} {v['auc']:.3f}  {v['p01']:.3f}     {v['p001']:.3f}")
    print('self-calibrated operating points (median over recordings)')
    for k, v in out_selfcal.items():
        print(f"{k:13s}" + ''.join(f"  a={a}: {d['fpr_median']:.4f}/{d['tpr_median']:.3f}"
                                   for a, d in v.items()))


if __name__ == '__main__':
    main()
