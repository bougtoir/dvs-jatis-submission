"""Load every number quoted in the JATIS manuscript from the tracked result JSONs.

No estimate, sample size or statistic is written literally in the manuscript
generators; they all come through this module so that re-running the analysis
scripts (sp_evaluation.py, sparsity_cost.py, operating_points.py,
zero_knowledge.py) and then the
generators reproduces the document end to end.
"""

import inspect
import json
import sys
from pathlib import Path

import numpy as np

RESULTS = Path(__file__).resolve().parents[1] / 'results'
sys.path.insert(0, str(RESULTS.parent))
import sp_evaluation as sp  # noqa: E402  fixed configurations of the ablations without a CV grid


def _defaults(fn):
    return {k: v.default for k, v in inspect.signature(fn).parameters.items()
            if v.default is not inspect.Parameter.empty}


def _load(name):
    with open(RESULTS / name) as f:
        return json.load(f)


def fmt(x, nd=3):
    return f'{x:.{nd}f}'


def pct(x, nd=1):
    return f'{100 * x:.{nd}f}%'


def load_numbers():
    summ = _load('sp_evaluation_summary.json')
    recs = _load('sp_per_recording.json')
    cost = _load('sparsity_cost.json')
    selfcal = _load('self_calibration.json')
    pauc = _load('partial_auc.json')
    zk = _load('zero_knowledge.json')
    hyb = _load('hybrid_screening.json')
    qual = _load('qualitative_example.json')
    fano_def = _defaults(sp.fano_filter)
    pidc_def = _defaults(sp.pidcdvs_filter)

    m = summ['methods']
    tests = summ['paired_tests_vs_proposed']
    n_rec = summ['n_recordings']
    rates = np.array([r['event_rate_hz'] for r in recs])
    sig = np.array([r['signal_fraction'] for r in recs])
    shapes = [tuple(r['shape']) for r in recs]
    selected = summ['selected_config_per_fold']['edlr']
    win_sel = sorted({c['window_us'] for c in selected.values()})
    q_sel = sorted({c['interval_quantile'] for c in selected.values()})
    r_sel = sorted({c['radius'] for c in selected.values()})
    grid = summ['hyperparameter_grids']['edlr']

    N = {
        'summary': summ,
        'methods': m,
        'tests': tests,
        'strata': summ['strata'],
        'runtime': summ['runtime_s_per_recording'],
        'cost': cost,
        'selfcal': selfcal,
        'pauc': pauc,
        'zk': zk,
        'hyb': hyb,
        'qual': qual,
        'fano_bins': fano_def['n_bins'],
        'pidc_bins': pidc_def['n_bins'],
        'pidc_epochs': pidc_def['n_epochs'],
        'zk_config': cost['configs']['edlr_zk'],
        'zk_grid_k': sorted({c['expected_count'] for c in summ['hyperparameter_grids']['edlr_adaptive']}),
        'n_rec': n_rec,
        'n_events_total': int(sum(r['n_events'] for r in recs)),
        'max_events': summ['max_events_per_recording'],
        'n_folds': summ['cv']['n_folds'],
        'train_events_per_rec': summ['cv']['train_events_per_recording'],
        'seed': summ['seed'],
        'n_small': sum(1 for s in shapes if s == (180, 240)),
        'n_large': sum(1 for s in shapes if s == (240, 304)),
        'rate_median': float(np.median(rates)),
        'rate_min': float(rates.min()),
        'rate_max': float(rates.max()),
        'sig_median': float(np.median(sig)),
        'sig_min': float(sig.min()),
        'sig_max': float(sig.max()),
        'dur_median': float(np.median([r['duration_s'] for r in recs])),
        'grid_radii': sorted({c['radius'] for c in grid}),
        'grid_windows_ms': sorted({c['window_us'] / 1e3 for c in grid}),
        'grid_quantiles': sorted({c['interval_quantile'] for c in grid}),
        'sel_windows_ms': [w / 1e3 for w in win_sel],
        'sel_quantiles': q_sel,
        'sel_radii': r_sel,
        'n_grid': len(grid),
        'grid_size_min': min(len(g) for k, g in summ['hyperparameter_grids'].items()
                             if k not in ('edlr', 'edlr_adaptive', 'plr')),
        'grid_size_max': max(len(g) for k, g in summ['hyperparameter_grids'].items()
                             if k not in ('edlr', 'edlr_adaptive', 'plr')),
        'comparator_configs': {k: v for k, v in summ['selected_config_per_fold'].items()},
        'features': summ['features'],
        'keep_fractions': cost['keep_fractions'],
        'cost_repeats': cost['n_repeats'],
        'alphas': sorted(float(a) for a in selfcal['edlr']),
        'n_unsup_edlr_better': int(sum(1 for r in recs
                                       if r['metrics']['edlr']['auc'] > r['metrics']['bilateral']['auc'])),
        'n_edlr_better_ynoise': int(sum(1 for r in recs
                                        if r['metrics']['edlr']['auc'] > r['metrics']['ynoise']['auc'])),
        'n_edlr_better_plr': int(sum(1 for r in recs
                                     if r['metrics']['edlr']['auc'] > r['metrics']['plr']['auc'])),
    }
    return N


def auc(N, key, nd=3):
    return fmt(N['methods'][key]['auc']['mean'], nd)


def auc_sd(N, key, nd=3):
    a = N['methods'][key]['auc']
    return f"{fmt(a['mean'], nd)} ± {fmt(a['std'], nd)}"


def scaling(N, key, field):
    return N['cost']['scaling'][key][field]
