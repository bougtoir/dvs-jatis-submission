"""Zero-knowledge evaluation: how much of each method's accuracy and calibration
survives when nothing about the target data is known.

Three protocols, all on the labelled EBSSA recordings used by sp_evaluation.py:

A. Cross-sensor transfer.  Every grid configuration is selected on the
   recordings of one sensor model and applied unchanged to the other.  The
   mean AUC on the target sensor is compared with the (optimistic) best
   in-sensor configuration, so the drop measures what tuning on the wrong
   sensor costs each method.
B. Published defaults.  Every method at the configuration recommended by its
   authors (for the proposed detector, the fixed configuration used
   throughout the paper), with no cross-validation at all.
C. Threshold transfer.  The operating threshold for a false-alarm budget is
   chosen on the source sensor (median per-recording alpha-quantile of the
   score) and applied as a fixed number on the target sensor; the achieved
   false-positive rate is compared with the label-free self-calibration of the
   proposed detector, which recomputes its quantile on the target stream.

Writes results/zero_knowledge.json.  Scores are cached in results/sp_cache
(git-ignored) so the second pass does not recompute them.
"""
import hashlib
import json
import time
from pathlib import Path

import numpy as np

import sp_evaluation as sp

RESULTS = Path(__file__).resolve().parent / 'results'
CACHE = RESULTS / 'sp_cache'

ALPHAS = (0.001, 0.01, 0.05)
TRANSFER_METHODS = ('edlr', 'edlr_adaptive', 'ynoise', 'bilateral_ed')

# configurations recommended in the source publications (all are grid points)
DEFAULTS = {
    'edlr': {'radius': 3, 'window_us': 100e3, 'interval_quantile': 0.99},
    'edlr_adaptive': sp.ZERO_KNOWLEDGE_CONFIG,
    'plr': {'n_bins': 200, 'quantile': 0.25, 'radius': 1, 't_half': 1},
    'ynoise': {'dt_us': 30e3, 'radius': 2},
    'bilateral': {'n_bins': 30, 'spatial_radius': 2},
    'knoise': {'dt_us': 50e3},
    'dwf': {'n_window': 200, 'radius': 2},
    'temporal': {'dt_us': 50e3},
    'nearest': {'dt_us': 20e3, 'dist_threshold': 2.0},
    'bilateral_ed': {'radius': 2, 'window_us': 100e3},
    'dwf_ed': {'radius': 2, 'tau_us': 20e3},
}


def cfg_index(name, cfg):
    grid = sp.GRID_METHODS[name][1]
    keys = [json.dumps(c, sort_keys=True) for c in grid]
    return keys.index(json.dumps(cfg, sort_keys=True))


def score_path(name, c, idx):
    return CACHE / f'zk_{name}_{c}_{idx}.npy'


def fingerprint():
    cfg = {'grids': {name: grid for name, (_, grid) in sp.GRID_METHODS.items()},
           'zero_knowledge': sp.ZERO_KNOWLEDGE_CONFIG, 'defaults': DEFAULTS,
           'alphas': ALPHAS, 'max_events': sp.MAX_EVENTS}
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:16]


def score_pass(partial):
    recs, auc = [], {name: {} for name in sp.GRID_METHODS}
    if partial.exists():
        d = json.load(open(partial))
        if d.get('fingerprint') == fingerprint():
            recs = [{**r, 'shape': tuple(r['shape'])} for r in d['recs']]
            auc = {n: {int(i): v for i, v in m.items()} for n, m in d['auc'].items()}
        else:
            print(f'discarding {partial.name}: settings changed')
    done = {r['idx'] for r in recs}

    def stale(idx):
        return [name for name, (_, grid) in sp.GRID_METHODS.items()
                if len(auc.get(name, {}).get(idx, [])) != len(grid)]

    for idx, events, labels, shape, rec_id in sp.load_recordings():
        if idx in done and not stale(idx):
            continue
        t0 = time.time()
        if idx not in done:
            recs.append({'idx': idx, 'shape': tuple(shape), 'n_bg': int((~labels).sum()),
                         'n_sig': int(labels.sum())})
        np.save(CACHE / f'zk_lab_{idx}.npy', labels)
        for name in stale(idx):
            fn, grid = sp.GRID_METHODS[name]
            auc.setdefault(name, {})[idx] = []
            for c, cfg in enumerate(grid):
                p = np.asarray(fn(events, shape, **cfg), dtype=np.float64)
                auc[name][idx].append(sp.compute_metrics(labels, p)['auc'])
                if name in TRANSFER_METHODS:
                    np.save(score_path(name, c, idx), p.astype(np.float32))
        print(f'rec {idx} {rec_id} {shape} ({time.time() - t0:.1f}s)', flush=True)
        tmp = partial.with_suffix('.tmp')
        json.dump({'fingerprint': fingerprint(), 'recs': recs, 'auc': auc}, open(tmp, 'w'))
        tmp.replace(partial)
    return recs, auc


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    recs, auc = score_pass(CACHE / 'zk_pass1.json')

    sensors = sorted({r['shape'] for r in recs})
    assert len(sensors) == 2, sensors
    by_sensor = {s: [r['idx'] for r in recs if r['shape'] == s] for s in sensors}
    sname = {s: f'{s[0]}x{s[1]}' for s in sensors}

    def mean_auc(name, idxs, c):
        return float(np.mean([auc[name][i][c] for i in idxs]))

    def best_cfg(name, idxs):
        grid = sp.GRID_METHODS[name][1]
        return int(np.argmax([mean_auc(name, idxs, c) for c in range(len(grid))]))

    # --- A: cross-sensor transfer ---
    transfer = {}
    for src, tgt in ((sensors[0], sensors[1]), (sensors[1], sensors[0])):
        key = f'{sname[src]}->{sname[tgt]}'
        transfer[key] = {'n_source': len(by_sensor[src]), 'n_target': len(by_sensor[tgt])}
        for name, (_, grid) in sp.GRID_METHODS.items():
            c_src = best_cfg(name, by_sensor[src])
            c_tgt = best_cfg(name, by_sensor[tgt])
            per_rec = [auc[name][i][c_src] for i in by_sensor[tgt]]
            transfer[key][name] = {
                'config_from_source': grid[c_src],
                'auc_transferred': float(np.mean(per_rec)),
                'auc_transferred_sd': float(np.std(per_rec, ddof=1)),
                'auc_in_sensor_best': mean_auc(name, by_sensor[tgt], c_tgt),
                'per_recording': per_rec,
            }
            transfer[key][name]['drop'] = (
                transfer[key][name]['auc_in_sensor_best'] - transfer[key][name]['auc_transferred'])
    # paired comparison on the target recordings, proposed minus comparator
    for key in transfer:
        ref = np.array(transfer[key]['edlr']['per_recording'])
        for name in sp.GRID_METHODS:
            if name == 'edlr':
                continue
            other = np.array(transfer[key][name]['per_recording'])
            d = ref - other
            if np.allclose(d, 0):
                p = 1.0
            else:
                p = float(sp.wilcoxon(ref, other).pvalue)
            transfer[key][name]['edlr_minus_this_mean'] = float(d.mean())
            transfer[key][name]['p_raw'] = p

    # --- B: published defaults, no tuning ---
    all_idx = [r['idx'] for r in recs]
    defaults = {}
    for name, cfg in DEFAULTS.items():
        c = cfg_index(name, cfg)
        per_rec = [auc[name][i][c] for i in all_idx]
        defaults[name] = {'config': cfg, 'auc_mean': float(np.mean(per_rec)),
                          'auc_sd': float(np.std(per_rec, ddof=1)), 'per_recording': per_rec}
    ref = np.array(defaults['edlr']['per_recording'])
    for name in DEFAULTS:
        if name == 'edlr':
            continue
        other = np.array(defaults[name]['per_recording'])
        d = ref - other
        defaults[name]['edlr_minus_this_mean'] = float(d.mean())
        defaults[name]['p_raw'] = 1.0 if np.allclose(d, 0) else float(sp.wilcoxon(ref, other).pvalue)

    # --- C: threshold transfer versus label-free self-calibration ---
    def flagged_rates(name, c, idxs, thr_fn):
        fpr, tpr = [], []
        for i in idxs:
            p = np.load(score_path(name, c, i)).astype(np.float64)
            lab = np.load(CACHE / f'zk_lab_{i}.npy')
            thr = thr_fn(p)
            # strict inequality: with ties at the quantile (saturated scores
            # equal to 1) the flagged fraction stays at or below alpha instead
            # of jumping to everything tied
            f = p < thr
            fpr.append(float(f[~lab].mean()))
            tpr.append(float(f[lab].mean()))
        return fpr, tpr

    # label-free saturation diagnostic: fraction of scores equal to 1 (tail
    # probability indistinguishable from the null at float precision)
    saturation = {}
    for name in TRANSFER_METHODS:
        if not name.startswith('edlr'):
            continue
        saturation[name] = {}
        for c, cfg in enumerate(sp.GRID_METHODS[name][1]):
            saturation[name][json.dumps(cfg, sort_keys=True)] = {
                sname[s]: {
                    'frac_saturated_median': float(np.median(v)),
                    'frac_saturated_max': float(np.max(v)),
                    'auc_mean': mean_auc(name, by_sensor[s], c),
                }
                for s in sensors
                for v in [[float((np.load(score_path(name, c, i)) >= 1.0).mean())
                           for i in by_sensor[s]]]
            }

    calib = {}
    for src, tgt in ((sensors[0], sensors[1]), (sensors[1], sensors[0])):
        key = f'{sname[src]}->{sname[tgt]}'
        calib[key] = {}
        # the deployment protocol of the paper: one fixed configuration, no
        # tuning on either sensor, threshold from the target stream's quantile
        c_fixed = cfg_index('edlr', DEFAULTS['edlr'])
        calib[key]['edlr_fixed_config'] = {'config': DEFAULTS['edlr']}
        for a in ALPHAS:
            fpr_s, tpr_s = flagged_rates('edlr', c_fixed, by_sensor[tgt],
                                         lambda p, a=a: np.quantile(p, a))
            calib[key]['edlr_fixed_config'][f'{a:g}'] = {
                'self_calibrated_on_target': {'fpr_median': float(np.median(fpr_s)),
                                              'fpr_max': float(np.max(fpr_s)),
                                              'tpr_median': float(np.median(tpr_s))}}
        for name in TRANSFER_METHODS:
            c = best_cfg(name, by_sensor[src])
            calib[key][name] = {'config_from_source': sp.GRID_METHODS[name][1][c]}
            for a in ALPHAS:
                # fixed threshold chosen on the source sensor
                thr_src = float(np.median([
                    np.quantile(np.load(score_path(name, c, i)).astype(np.float64), a)
                    for i in by_sensor[src]]))
                fpr_t, tpr_t = flagged_rates(name, c, by_sensor[tgt], lambda p, t=thr_src: t)
                # label-free recalibration on the target stream itself
                fpr_s, tpr_s = flagged_rates(name, c, by_sensor[tgt],
                                             lambda p, a=a: np.quantile(p, a))
                calib[key][name][f'{a:g}'] = {
                    'threshold_from_source': thr_src,
                    'transferred': {'fpr_median': float(np.median(fpr_t)),
                                    'fpr_max': float(np.max(fpr_t)),
                                    'tpr_median': float(np.median(tpr_t))},
                    'self_calibrated_on_target': {'fpr_median': float(np.median(fpr_s)),
                                                  'fpr_max': float(np.max(fpr_s)),
                                                  'tpr_median': float(np.median(tpr_s))},
                }

    out = {
        'n_recordings': len(recs),
        'sensors': {sname[s]: len(by_sensor[s]) for s in sensors},
        'alphas': list(ALPHAS),
        'cross_sensor_transfer': transfer,
        'published_defaults': defaults,
        'threshold_transfer': calib,
        'saturation': saturation,
    }
    with open(RESULTS / 'zero_knowledge.json', 'w') as f:
        json.dump(out, f, indent=2)

    print('\n=== A. cross-sensor transfer (AUC on target; drop vs best in-sensor cfg) ===')
    for key, d in transfer.items():
        print(key)
        for name in sp.GRID_METHODS:
            v = d[name]
            print(f"  {name:13s} transferred={v['auc_transferred']:.3f} "
                  f"in-sensor best={v['auc_in_sensor_best']:.3f} drop={v['drop']:+.3f}")
    print('\n=== B. published defaults (no tuning) ===')
    for name, v in defaults.items():
        print(f"  {name:13s} AUC={v['auc_mean']:.3f}")
    print('\n=== C. threshold transfer: achieved FPR (median) at requested alpha ===')
    for key, d in calib.items():
        print(key)
        for a in ALPHAS:
            v = d['edlr_fixed_config'][f'{a:g}']['self_calibrated_on_target']
            print(f"  edlr(fixed)   a={a:<6g} self-cal FPR={v['fpr_median']:.4f} TPR={v['tpr_median']:.3f}")
        for name in TRANSFER_METHODS:
            for a in ALPHAS:
                v = d[name][f'{a:g}']
                print(f"  {name:13s} a={a:<6g} transferred FPR={v['transferred']['fpr_median']:.4f} "
                      f"TPR={v['transferred']['tpr_median']:.3f} | self-cal FPR="
                      f"{v['self_calibrated_on_target']['fpr_median']:.4f} "
                      f"TPR={v['self_calibrated_on_target']['tpr_median']:.3f}")


if __name__ == '__main__':
    main()
