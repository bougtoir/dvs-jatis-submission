"""Two-stage (screen, then confirm) front end with zero-knowledge hand-off.

Stage 1 is the zero-knowledge form of the proposed detector
(rate-adaptive window, sp_evaluation.ZERO_KNOWLEDGE_CONFIG).  It passes the
fraction ``f`` of the stream with the smallest noise probability (a quantile
of the stream, no labels).  Stage 2 is the YNoise density filter run on the
passed events only, so its ``last-event`` map and its cost see only that
fraction.  Its parameters come from stage 1 without labels:

  handoff   dt = the adaptive window W chosen by stage 1, radius = stage-1 radius
  default   the published YNoise defaults (dt = 30 ms, r = 2)
  pseudo    dt and radius chosen from the YNoise grid to best separate the
            stage-1 candidates from the rest of the stream (pseudo-labels), so
            the screening stage tunes the confirmation stage

The combined score ranks the rejected events (stage-1 probability) below the
candidates (stage-2 score).  The operating point is again a quantile of the
combined score on the stream.  Per recording we record the AUC of every
variant, the achieved false-positive and detection rates at the requested
budgets, and the wall-clock time of each stage against YNoise run on the
whole stream (for the pseudo-label variant the grid search is part of its
stage-2 time), so the cost of the hybrid can be stated as
time(stage 1) + time(stage 2 on f of the stream).

Writes results/hybrid_screening.json; resumable via results/sp_cache.  The
resume file carries a fingerprint of every setting that determines the
per-recording results and is discarded when the fingerprint changes.
"""
import hashlib
import json
import time
from pathlib import Path

import numpy as np
from scipy.stats import wilcoxon

import event_driven as ed
import sp_evaluation as sp

RESULTS = Path(__file__).resolve().parent / 'results'
CACHE = RESULTS / 'sp_cache'
PARTIAL = CACHE / 'hybrid_pass1.json'

SCREEN_FRACTIONS = (0.02, 0.05, 0.1, 0.2, 0.5)
ALPHAS = (0.001, 0.01, 0.05)
YNOISE_DEFAULT = {'dt_us': 30e3, 'radius': 2}
N_TIMING = 3


def fingerprint():
    cfg = {'zero_knowledge': sp.ZERO_KNOWLEDGE_CONFIG, 'ynoise_grid': sp.YNOISE_GRID,
           'ynoise_default': YNOISE_DEFAULT, 'fractions': SCREEN_FRACTIONS,
           'alphas': ALPHAS, 'max_events': sp.MAX_EVENTS, 'schema': 2}
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:16]


def load_partial():
    if not PARTIAL.exists():
        return []
    saved = json.load(open(PARTIAL))
    if isinstance(saved, dict) and saved.get('fingerprint') == fingerprint():
        return saved['records']
    print(f'discarding {PARTIAL.name}: settings changed')
    return []


def save_partial(recs):
    tmp = PARTIAL.with_suffix('.tmp')
    json.dump({'fingerprint': fingerprint(), 'records': recs}, open(tmp, 'w'))
    tmp.replace(PARTIAL)


def timed(fn):
    best = np.inf
    out = None
    for _ in range(N_TIMING):
        t0 = time.perf_counter()
        out = fn()
        best = min(best, time.perf_counter() - t0)
    return out, best


def ynoise_on(events, shape, dt_us, radius):
    return sp.ynoise_filter(events, shape, dt_us=dt_us, radius=radius)


def combined(p1, cand, p2_cand):
    """Noise probability over the whole stream: candidates in [0, 0.5) by the
    stage-2 score, rejected events in [0.5, 1] by the stage-1 probability."""
    p = 0.5 + 0.5 * p1.astype(np.float64)
    p[cand] = 0.5 * p2_cand.astype(np.float64)
    return p


def operating(p, labels):
    out = {}
    for a in ALPHAS:
        thr = np.quantile(p, a)
        f = p < thr
        out[f'{a:g}'] = {'fpr': float(f[~labels].mean()), 'tpr': float(f[labels].mean())}
    return out


def auc(labels, p):
    return sp.compute_metrics(labels, p)['auc']


def pseudo_tune(events, shape, cand):
    """Pick the YNoise grid point whose density best separates stage-1
    candidates from rejected events.  No labels are used."""
    best, best_cfg = -1.0, None
    for cfg in sp.YNOISE_GRID:
        p = ynoise_on(events, shape, **cfg)
        a = auc(cand, p)
        if a > best:
            best, best_cfg = a, cfg
    return best_cfg, best


def score_pass():
    recs = load_partial()
    done = {r['idx'] for r in recs}
    zc = sp.ZERO_KNOWLEDGE_CONFIG
    for idx, events, labels, shape, rec_id in sp.load_recordings():
        if idx in done:
            continue
        t0 = time.time()
        r = {'idx': idx, 'shape': [int(s) for s in shape], 'n_events': int(len(events)),
             'n_sig': int(labels.sum())}
        # stage 1: zero-knowledge proposal, timed including rate and window choice
        def stage1():
            rate, window_us = ed.adaptive_window_us(
                events, shape, zc['radius'], zc['expected_count'], zc['interval_quantile'])
            p1 = ed.sliding_window_lr(events, shape, radius=zc['radius'],
                                      window_us=window_us, rate=rate)
            return window_us, p1
        (window_us, p1), t1 = timed(stage1)
        p1 = np.asarray(p1, dtype=np.float64)
        r['window_us'] = window_us
        r['stage1'] = {'auc': auc(labels, p1), 'time_s': t1, 'operating': operating(p1, labels)}
        # references: YNoise on the whole stream, default and hand-off parameters
        py_def, ty_def = timed(lambda: ynoise_on(events, shape, **YNOISE_DEFAULT))
        py_hand, _ = timed(lambda: ynoise_on(events, shape, window_us, zc['radius']))
        r['ynoise_full'] = {
            'default': {'auc': auc(labels, py_def), 'time_s': ty_def,
                        'operating': operating(py_def, labels)},
            'handoff': {'auc': auc(labels, py_hand), 'operating': operating(py_hand, labels)},
        }
        r['screen'] = {}
        for f in SCREEN_FRACTIONS:
            thr = np.quantile(p1, f)
            cand = p1 < thr
            if cand.sum() < 0.5 * f * len(p1):
                # the f-quantile sits inside a block of tied (underflowed)
                # probabilities: pass the whole block, and report the fraction
                cand = p1 <= thr
            n_c = int(cand.sum())
            e = {'passed_fraction': n_c / len(p1),
                 'signal_recall': float(cand[labels].mean()),
                 'background_pass': float(cand[~labels].mean())}
            if n_c < 10:
                r['screen'][f'{f:g}'] = e
                continue
            sub = events[cand]
            lab_sub = labels[cand]
            t_search0 = time.perf_counter()
            pcfg, pseudo_auc = pseudo_tune(events, shape, cand)
            t_search = time.perf_counter() - t_search0
            e['pseudo_config'] = pcfg
            e['pseudo_separation_auc'] = pseudo_auc
            e['pseudo_search_time_s'] = t_search
            variants = {
                'handoff': (window_us, zc['radius']),
                'default': (YNOISE_DEFAULT['dt_us'], YNOISE_DEFAULT['radius']),
                'pseudo': (pcfg['dt_us'], pcfg['radius']),
            }
            for name, (dt, rad) in variants.items():
                p2, t2 = timed(lambda: ynoise_on(sub, shape, dt, rad))
                p2 = np.asarray(p2, dtype=np.float64)
                ph = combined(p1, cand, p2)
                e[name] = {
                    'dt_us': float(dt), 'radius': int(rad),
                    'auc': auc(labels, ph),
                    'auc_within_candidates': auc(lab_sub, p2) if 0 < lab_sub.sum() < len(lab_sub) else None,
                    'stage2_time_s': t2 + (t_search if name == 'pseudo' else 0.0),
                    'operating': operating(ph, labels),
                }
            r['screen'][f'{f:g}'] = e
        recs.append(r)
        print(f'rec {idx} {rec_id} {shape} n={len(events)} W={window_us / 1e3:.0f}ms '
              f'edlr_zk={r["stage1"]["auc"]:.3f} ynoise={r["ynoise_full"]["default"]["auc"]:.3f} '
              f'hybrid(f=0.1,handoff)={r["screen"]["0.1"].get("handoff", {}).get("auc", float("nan")):.3f} '
              f'({time.time() - t0:.1f}s)', flush=True)
        save_partial(recs)
    return recs


def summarise(vals):
    v = np.asarray(vals, dtype=np.float64)
    return {'mean': float(v.mean()), 'sd': float(v.std(ddof=1)) if len(v) > 1 else 0.0,
            'median': float(np.median(v)), 'n': int(len(v))}


def paired(a, b):
    d = np.asarray(a) - np.asarray(b)
    if np.allclose(d, 0):
        p = 1.0
    else:
        p = float(wilcoxon(d).pvalue)
    return {'diff_mean': float(d.mean()), 'p_raw': p}


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    recs = score_pass()
    n = len(recs)
    out = {'n_recordings': n, 'screen_fractions': list(SCREEN_FRACTIONS), 'alphas': list(ALPHAS),
           'stage1_config': sp.ZERO_KNOWLEDGE_CONFIG, 'ynoise_default': YNOISE_DEFAULT,
           'ynoise_grid': sp.YNOISE_GRID}
    s1 = [r['stage1']['auc'] for r in recs]
    yd = [r['ynoise_full']['default']['auc'] for r in recs]
    yh = [r['ynoise_full']['handoff']['auc'] for r in recs]
    out['stage1'] = {'auc': summarise(s1), 'time_s': summarise([r['stage1']['time_s'] for r in recs]),
                     'window_ms': summarise([r['window_us'] / 1e3 for r in recs]),
                     'operating': {f'{a:g}': {k: summarise([r['stage1']['operating'][f'{a:g}'][k] for r in recs])
                                              for k in ('fpr', 'tpr')} for a in ALPHAS}}
    out['ynoise_full'] = {
        'default': {'auc': summarise(yd), 'time_s': summarise([r['ynoise_full']['default']['time_s'] for r in recs]),
                    'operating': {f'{a:g}': {k: summarise([r['ynoise_full']['default']['operating'][f'{a:g}'][k] for r in recs])
                                             for k in ('fpr', 'tpr')} for a in ALPHAS}},
        'handoff': {'auc': summarise(yh), 'vs_default': paired(yh, yd),
                    'vs_stage1': paired(yh, s1)},
    }
    # per sensor model, and the label-tuned YNoise reference from the grouped CV
    sensors = sorted({tuple(r['shape']) for r in recs})
    out['per_sensor'] = {}
    for s in sensors:
        ii = [i for i, r in enumerate(recs) if tuple(r['shape']) == s]
        out['per_sensor'][f'{s[0]}x{s[1]}'] = {
            'n': len(ii),
            'stage1_auc': summarise([s1[i] for i in ii]),
            'ynoise_default_auc': summarise([yd[i] for i in ii]),
            'ynoise_handoff_auc': summarise([yh[i] for i in ii]),
            'window_ms': summarise([recs[i]['window_us'] / 1e3 for i in ii]),
        }
    summ_path = RESULTS / 'sp_evaluation_summary.json'
    if summ_path.exists():
        summ = json.load(open(summ_path))
        out['ynoise_cv_reference'] = {
            'auc_mean': summ['methods']['ynoise']['auc']['mean'],
            'selected_config_per_fold': summ['selected_config_per_fold']['ynoise'],
        }
    out['screen'] = {}
    for f in SCREEN_FRACTIONS:
        key = f'{f:g}'
        es = [r['screen'][key] for r in recs if 'handoff' in r['screen'][key]]
        if not es:
            continue
        e = {'n': len(es),
             'passed_fraction': summarise([x['passed_fraction'] for x in es]),
             'signal_recall': summarise([x['signal_recall'] for x in es]),
             'background_pass': summarise([x['background_pass'] for x in es]),
             'pseudo_config_counts': {}}
        for x in es:
            k = json.dumps(x['pseudo_config'], sort_keys=True)
            e['pseudo_config_counts'][k] = e['pseudo_config_counts'].get(k, 0) + 1
        idx_ok = [i for i, r in enumerate(recs) if 'handoff' in r['screen'][key]]
        for name in ('handoff', 'default', 'pseudo'):
            a = [x[name]['auc'] for x in es]
            t2 = [x[name]['stage2_time_s'] for x in es]
            tot = [recs[i]['stage1']['time_s'] + x[name]['stage2_time_s'] for i, x in zip(idx_ok, es)]
            e[name] = {
                'auc': summarise(a),
                'vs_stage1': paired(a, [s1[i] for i in idx_ok]),
                'vs_ynoise_default_full': paired(a, [yd[i] for i in idx_ok]),
                'stage2_time_s': summarise(t2),
                'total_time_s': summarise(tot),
                'stage2_time_over_ynoise_full': summarise(
                    [x[name]['stage2_time_s'] / recs[i]['ynoise_full']['default']['time_s']
                     for i, x in zip(idx_ok, es)]),
                'total_time_over_stage1': summarise(
                    [t / recs[i]['stage1']['time_s'] for i, t in zip(idx_ok, tot)]),
                'operating': {f'{al:g}': {k: summarise([x[name]['operating'][f'{al:g}'][k] for x in es])
                                          for k in ('fpr', 'tpr')} for al in ALPHAS},
            }
        out['screen'][key] = e
    json.dump(out, open(RESULTS / 'hybrid_screening.json', 'w'), indent=1)
    print(f'\n=== Hybrid screening ({n} recordings) ===')
    print(f'stage 1 (edlr_zk) AUC {out["stage1"]["auc"]["mean"]:.3f}   '
          f'YNoise default full AUC {out["ynoise_full"]["default"]["auc"]["mean"]:.3f}   '
          f'YNoise hand-off full AUC {out["ynoise_full"]["handoff"]["auc"]["mean"]:.3f} '
          f'(vs default p={out["ynoise_full"]["handoff"]["vs_default"]["p_raw"]:.2g}, '
          f'vs stage 1 p={out["ynoise_full"]["handoff"]["vs_stage1"]["p_raw"]:.2g}; '
          f'label-tuned CV {out.get("ynoise_cv_reference", {}).get("auc_mean", float("nan")):.3f})')
    for s, v in out['per_sensor'].items():
        print(f'  {s} (n={v["n"]}): stage1 {v["stage1_auc"]["mean"]:.3f}  ynoise default '
              f'{v["ynoise_default_auc"]["mean"]:.3f}  hand-off {v["ynoise_handoff_auc"]["mean"]:.3f}  '
              f'W median {v["window_ms"]["median"]:.0f} ms')
    print(f'time per recording (median): stage 1 {out["stage1"]["time_s"]["median"]:.3f}s, '
          f'YNoise full {out["ynoise_full"]["default"]["time_s"]["median"]:.3f}s')
    for key, e in out['screen'].items():
        print(f'f={key}: recall={e["signal_recall"]["mean"]:.3f}  ' + '  '.join(
            f'{nm} AUC={e[nm]["auc"]["mean"]:.3f} (vs s1 {e[nm]["vs_stage1"]["diff_mean"]:+.3f} p={e[nm]["vs_stage1"]["p_raw"]:.3g}; '
            f'stage2/ynoise_full={e[nm]["stage2_time_over_ynoise_full"]["median"]:.2f})'
            for nm in ('handoff', 'default', 'pseudo')))
        for al in ALPHAS:
            o = e['handoff']['operating'][f'{al:g}']
            print(f'   alpha={al:g} handoff FPR={o["fpr"]["median"]:.4f} TPR={o["tpr"]["median"]:.3f}')


if __name__ == '__main__':
    main()
