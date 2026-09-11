#!/usr/bin/env python3
"""Generate the JATIS manuscript figures from the tracked result JSON files.

Every number plotted here is read from results/*.json produced by
sp_evaluation.py, sparsity_cost.py, operating_points.py and zero_knowledge.py.
All text in the figures is English.  Outputs: fig1..fig7 PNG (300 dpi) in this
directory.
"""

import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

HERE = Path(__file__).resolve().parent
RESULTS = HERE.parent / 'results'

plt.rcParams.update({
    'font.family': 'DejaVu Sans',
    'font.size': 9,
    'axes.titlesize': 10,
    'axes.labelsize': 9,
    'legend.fontsize': 8,
    'figure.dpi': 300,
    'savefig.dpi': 300,
    'axes.spines.top': False,
    'axes.spines.right': False,
})

SHORT = {
    'edlr': 'Event-driven Poisson LR (proposed)',
    'edlr_adaptive': 'Event-driven Poisson LR, rate-adaptive window (proposed)',
    'edlr_zk': 'Event-driven Poisson LR, zero-knowledge form (proposed)',
    'plr': 'Voxelised Poisson LR (ablation)',
    'temporal': 'Background-activity filter',
    'nearest': 'Nearest-neighbour filter',
    'bilateral': 'Event bilateral (frame-based)',
    'bilateral_ed': 'Event bilateral (event-driven)',
    'motion': 'Motion-compensated proxy',
    'knoise': 'kNoise',
    'dwf': 'Double-window filter',
    'dwf_ed': 'Double-window (event-driven)',
    'ynoise': 'YNoise',
    'pi_dc_dvs': 'PI-DC-DVS CNN',
    'fano': 'Fano-factor score (ablation)',
    'waiting_lr': 'Gamma waiting-time test',
    'knn_pp': 'kNN point-process test',
    'recursive': 'Recursive rate tracker',
    'flow_matched': 'Velocity-adaptive matched filter',
    'mlpf': 'MLPF (supervised)',
    'edncnn': 'EDnCNN-style CNN (supervised)',
    'multiscale': 'Multiscale Fisher combination',
}

# published unsupervised comparators, frame-based ablation, supervised
GROUPS = [
    ('Proposed', ['edlr', 'edlr_adaptive'], '#c0392b'),
    ('Published event-only baselines', ['bilateral', 'ynoise', 'motion', 'temporal',
                                        'nearest', 'dwf', 'knoise'], '#2c3e50'),
    ('Event-driven ablations', ['bilateral_ed', 'waiting_lr', 'knn_pp', 'dwf_ed',
                                'flow_matched', 'recursive'], '#7f8c8d'),
    ('Frame-based / label-free learned ablations', ['plr', 'pi_dc_dvs', 'fano'], '#bdc3c7'),
    ('Supervised (label-trained)', ['mlpf', 'edncnn'], '#2980b9'),
]

COST_COLOURS = {
    'edlr': '#c0392b', 'edlr_zk': '#f1948a', 'bilateral_ed': '#e67e22', 'ynoise': '#8e44ad',
    'recursive': '#16a085', 'plr': '#2c3e50', 'bilateral': '#2980b9',
    'temporal': '#7f8c8d',
}


def load(name):
    return json.load(open(RESULTS / name))


# ---------------------------------------------------------------------------
# Fig. 1  Method schematic (no data)
# ---------------------------------------------------------------------------
def fig1_schematic(out):
    fig, ax = plt.subplots(figsize=(6.5, 2.6))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 3)
    ax.axis('off')

    def box(x, y, w, h, text, fc='#f4f6f7'):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle='round,pad=0.05',
                                    fc=fc, ec='#2c3e50', lw=0.8))
        ax.text(x + w / 2, y + h / 2, text, ha='center', va='center', fontsize=6.2)

    def arrow(x0, y0, x1, y1):
        ax.add_patch(FancyArrowPatch((x0, y0), (x1, y1), arrowstyle='-|>',
                                     mutation_scale=9, lw=0.8, color='#2c3e50'))

    box(0.1, 1.0, 1.7, 1.0, 'Event stream\n$(x_i, y_i, t_i, p_i)$\nsorted by time')
    box(2.3, 1.9, 2.4, 0.9, 'Per-pixel background rate\n$\\hat{\\lambda}_{xy}$ from inter-event\nintervals (quantile)')
    box(2.3, 0.2, 2.4, 0.9, 'Causal neighbourhood count\n$N_i$: events in $(2r+1)^2$ px\nwithin the last $W$ µs, $W = k/\\bar{\\Lambda}$')
    box(5.3, 1.0, 2.2, 1.0, 'Poisson tail probability\n$P_i = \\Pr(N \\geq N_i \\mid \\Lambda_i)$\n$\\Lambda_i = W\\sum_{nbhd}\\hat{\\lambda}$', fc='#fdecea')
    box(8.0, 1.9, 1.9, 0.9, 'Operating point\n$P_i < \\alpha$-quantile of the stream\n(no labels needed)')
    box(8.0, 0.2, 1.9, 0.9, 'Cost per event\n$O(\\log n + (2r+1)^2)$\nno voxel grid')
    arrow(1.8, 1.6, 2.3, 2.3)
    arrow(1.8, 1.4, 2.3, 0.7)
    arrow(4.7, 2.3, 5.3, 1.7)
    arrow(4.7, 0.7, 5.3, 1.3)
    arrow(7.5, 1.7, 8.0, 2.3)
    arrow(7.5, 1.3, 8.0, 0.7)
    fig.savefig(out, bbox_inches='tight')
    plt.close(fig)


# ---------------------------------------------------------------------------
# Fig. 2  Detection performance across methods (mean AUC ± SD, n = 43)
# ---------------------------------------------------------------------------
def fig2_methods(out):
    d = load('sp_evaluation_summary.json')
    m = d['methods']
    names, means, stds, colours = [], [], [], []
    seps = []
    for _, keys, col in GROUPS:
        keys = sorted(keys, key=lambda k: -m[k]['auc']['mean'])
        for k in keys:
            names.append(SHORT[k])
            means.append(m[k]['auc']['mean'])
            stds.append(m[k]['auc']['std'])
            colours.append(col)
        seps.append(len(names))
    y = np.arange(len(names))
    fig, ax = plt.subplots(figsize=(6.5, 5.2))
    ax.barh(y, means, xerr=stds, color=colours, ecolor='#555555', capsize=2,
            error_kw={'lw': 0.7})
    ax.set_yticks(y)
    ax.set_yticklabels(names)
    ax.invert_yaxis()
    ax.set_xlim(0.3, 1.0)
    ax.axvline(0.5, color='k', ls=':', lw=0.7)
    ax.axvline(means[0], color='#c0392b', ls='--', lw=0.8)
    for s in seps[:-1]:
        ax.axhline(s - 0.5, color='#999999', lw=0.5)
    ax.set_xlabel(f"ROC-AUC, mean ± SD over {d['n_recordings']} EBSSA recordings")
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for _, _, c in GROUPS]
    ax.legend(handles, [g for g, _, _ in GROUPS], loc='lower right', frameon=False)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Fig. 3  Per-recording paired comparison
# ---------------------------------------------------------------------------
def fig3_paired(out):
    recs = load('sp_per_recording.json')
    d = load('sp_evaluation_summary.json')
    pairs = [('bilateral', 'a'), ('ynoise', 'b'), ('plr', 'c'), ('mlpf', 'd')]
    fig, axes = plt.subplots(2, 2, figsize=(5.0, 5.2), sharex=True, sharey=True)
    axes = axes.ravel()
    for ax, (k, tag) in zip(axes, pairs):
        x = np.array([r['metrics'][k]['auc'] for r in recs])
        yv = np.array([r['metrics']['edlr']['auc'] for r in recs])
        big = np.array([tuple(r['shape']) == (240, 304) for r in recs])
        ax.plot([0.3, 1], [0.3, 1], 'k:', lw=0.7)
        ax.scatter(x[~big], yv[~big], s=12, c='#c0392b', label='180 × 240 px', zorder=3)
        ax.scatter(x[big], yv[big], s=12, marker='s', c='#2980b9', label='240 × 304 px', zorder=3)
        t = d['paired_tests_vs_proposed'][k]
        ax.set_title(f"({tag}) vs {SHORT[k].split(' (')[0]}\nΔAUC = {t['auc_diff_mean']:+.3f}, "
                     f"p(Holm) = {t['p_holm']:.2g}", fontsize=8)
        ax.set_xlim(0.3, 1.0)
        ax.set_ylim(0.3, 1.0)
        ax.set_xlabel(f'{SHORT[k].split(" (")[0]} AUC')
        ax.set_ylabel('Proposed AUC')
        ax.set_aspect('equal')
        ax.label_outer()
    axes[0].legend(loc='upper left', frameon=False, fontsize=7)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Fig. 4  Computational cost versus event sparsity
# ---------------------------------------------------------------------------
def fig4_sparsity(out):
    d = load('sparsity_cost.json')
    meas = d['measurements']
    fracs = sorted(set(m['keep_fraction'] for m in meas))
    fig, axes = plt.subplots(1, 2, figsize=(6.5, 3.4))
    for k in ['edlr', 'edlr_zk', 'bilateral_ed', 'ynoise', 'recursive', 'plr', 'bilateral', 'temporal']:
        fam = d['scaling'][k]['family']
        sec = [np.median([m['seconds'] for m in meas if m['method'] == k and m['keep_fraction'] == f])
               for f in fracs]
        nse = [np.median([m['ns_per_event'] for m in meas if m['method'] == k and m['keep_fraction'] == f])
               for f in fracs]
        ls = '-' if fam == 'event-driven' else '--'
        lab = f"{SHORT[k].split(' (')[0]}" + (' (ED)' if k == 'bilateral_ed' else '')
        if k == 'edlr_zk':
            lab = 'Proposed, zero-knowledge form'
        axes[0].plot(fracs, sec, ls, marker='o', ms=3, lw=1, color=COST_COLOURS[k],
                     label=f"{lab}, exponent {d['scaling'][k]['cost_exponent_mean']:.2f}")
        axes[1].plot(fracs, nse, ls, marker='o', ms=3, lw=1, color=COST_COLOURS[k])
    for ax in axes:
        ax.set_xscale('log')
        ax.set_yscale('log')
        ax.invert_xaxis()
        ax.set_xlabel('Fraction of events kept\n(thinned real EBSSA stream)')
    axes[0].set_ylabel('Wall-clock time per recording (s)')
    axes[1].set_ylabel('Cost per event (ns)')
    axes[0].set_title('(a) Total cost', fontsize=9)
    axes[1].set_title('(b) Per-event cost', fontsize=9)
    axes[0].set_ylim(2e-5, 3)
    axes[0].legend(frameon=False, fontsize=6, loc='lower left', ncol=1,
                   title='solid: event-driven; dashed: frame/voxel-based', title_fontsize=6)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Fig. 5  Self-calibration and low-FPR performance
# ---------------------------------------------------------------------------
def fig5_calibration(out):
    sc = load('self_calibration.json')
    pa = load('partial_auc.json')
    fig, axes = plt.subplots(1, 3, figsize=(6.5, 2.6))
    cols = {'edlr': '#c0392b', 'multiscale': '#e67e22', 'ynoise': '#8e44ad', 'bilateral_ed': '#2980b9'}
    alphas = sorted(float(a) for a in sc['edlr'])
    ax = axes[0]
    ax.plot(alphas, alphas, 'k:', lw=0.8, label='ideal')
    for k, v in sc.items():
        ax.plot(alphas, [v[f'{a:g}']['fpr_median'] for a in alphas], marker='o', ms=3,
                lw=1, color=cols[k], label=SHORT[k].split(' (')[0])
    ax.set_xscale('log')
    ax.set_yscale('log')
    ax.set_xlabel('Requested false-alarm budget α')
    ax.set_ylabel('Achieved FPR (median)')
    ax.set_title('(a) Self-calibration', fontsize=9)
    ax.legend(frameon=False, fontsize=5.5, loc='upper left')
    ax = axes[1]
    for k, v in sc.items():
        ax.plot(alphas, [v[f'{a:g}']['tpr_median'] for a in alphas], marker='o', ms=3,
                lw=1, color=cols[k])
    ax.set_xscale('log')
    ax.set_xlabel('Requested false-alarm budget α')
    ax.set_ylabel('Detection rate (median)')
    ax.set_title('(b) Detections at budget', fontsize=9)
    ax = axes[2]
    keys = ['edlr', 'bilateral_ed', 'ynoise', 'bilateral', 'dwf', 'knoise']
    x = np.arange(len(keys))
    w = 0.38
    ax.bar(x - w / 2, [pa[k]['p01'] for k in keys], w, color='#c0392b', label='FPR ≤ 0.01')
    ax.bar(x + w / 2, [pa[k]['p001'] for k in keys], w, color='#f5b7b1', label='FPR ≤ 0.001')
    ax.axhline(0.5, color='k', ls=':', lw=0.7)
    ax.set_xticks(x)
    ax.set_xticklabels(['Proposed', 'Bilateral (ED)', 'YNoise', 'Bilateral (frame)', 'DWF', 'kNoise'],
                       fontsize=6, rotation=40, ha='right')
    ax.set_ylim(0.45, 0.7)
    ax.set_ylabel('Standardised partial AUC')
    ax.set_title('(c) Low-FPR region', fontsize=9)
    ax.legend(frameon=False, fontsize=6)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Fig. 6  Zero-knowledge deployment: cross-sensor transfer, window rule, thresholds
# ---------------------------------------------------------------------------
def fig6_zero_knowledge(out):
    zk = load('zero_knowledge.json')
    cost = load('sparsity_cost.json')
    zcfg = cost['configs']['edlr_zk']
    sensors = list(zk['sensors'])
    fig, axes = plt.subplots(1, 3, figsize=(7.2, 3.0))

    # (a) AUC and saturation against the dimensionless window k, both sensors
    ax = axes[0]
    sat = zk['saturation']['edlr_adaptive']
    ks = sorted({json.loads(c)['expected_count'] for c in sat})
    sty = {sensors[0]: ('o', '-'), sensors[1]: ('s', '--')}
    for s in sensors:
        pts = {}
        for c, v in sat.items():
            cfg = json.loads(c)
            if cfg['radius'] == zcfg['radius'] and cfg['interval_quantile'] == zcfg['interval_quantile']:
                pts[cfg['expected_count']] = v[s]
        mk, ls = sty[s]
        ax.plot(ks, [pts[k]['auc_mean'] for k in ks], ls, marker=mk, ms=3, lw=1,
                color='#c0392b', label=f'AUC, {s.replace("x", " × ")} px')
        ax.plot(ks, [pts[k]['frac_saturated_median'] for k in ks], ls, marker=mk, ms=3, lw=1,
                color='#7f8c8d', label=f'saturated fraction, {s.replace("x", " × ")} px')
    ax.axvline(zcfg['expected_count'], color='k', ls=':', lw=0.8)
    ax.set_xscale('log', base=2)
    ax.set_xticks(ks)
    ax.set_xticklabels([f'{k:g}' for k in ks])
    ax.set_xlabel('Expected background count k in window')
    ax.set_ylabel('Mean AUC / fraction of scores = 1')
    ax.set_ylim(0, 1.25)
    ax.set_yticks([0, 0.2, 0.4, 0.6, 0.8, 1.0])
    ax.set_title('(a) Rate-adaptive window rule', fontsize=9)
    ax.legend(frameon=False, fontsize=5, loc='upper left', ncol=1)

    # (b) cross-sensor transfer loss: fixed window versus adaptive window versus comparators
    ax = axes[1]
    keys = ['edlr', 'edlr_adaptive', 'ynoise', 'bilateral', 'bilateral_ed', 'dwf', 'plr']
    names = ['Proposed, fixed W', 'Proposed, adaptive W', 'YNoise', 'Bilateral (frame)',
             'Bilateral (ED)', 'DWF', 'Voxelised LR']
    dirs = list(zk['cross_sensor_transfer'])
    x = np.arange(len(keys))
    w = 0.38
    for i, (d, col) in enumerate(zip(dirs, ('#c0392b', '#2980b9'))):
        ax.bar(x + (i - 0.5) * w, [zk['cross_sensor_transfer'][d][k]['drop'] for k in keys], w,
               color=col, label=d.replace('x', ' × ').replace('->', ' → '))
    ax.axhline(0, color='k', lw=0.6)
    ax.set_xticks(x)
    ax.set_xticklabels(names, fontsize=5.5, rotation=40, ha='right')
    ax.set_ylabel('AUC lost with the configuration\nselected on the other sensor')
    ax.set_title('(b) Cross-sensor transfer', fontsize=9)
    ax.legend(frameon=False, fontsize=5.5, title='source → target', title_fontsize=5.5)

    # (c) threshold transfer versus target-stream self-calibration
    ax = axes[2]
    alphas = [float(a) for a in zk['alphas']]
    ax.plot(alphas, alphas, 'k:', lw=0.8, label='ideal (FPR = α)')
    cols = {'edlr_adaptive': '#c0392b', 'ynoise': '#8e44ad'}
    labs = {'edlr_adaptive': 'Proposed', 'ynoise': 'YNoise'}
    for k in cols:
        for d, mk in zip(dirs, ('o', 's')):
            c = zk['threshold_transfer'][d][k]
            ax.plot(alphas, [c[f'{a:g}']['transferred']['fpr_median'] for a in alphas], '--',
                    marker=mk, ms=3, lw=0.9, color=cols[k], alpha=0.6,
                    label=f'{labs[k]}, threshold from\nother sensor' if mk == 'o' else None)
            ax.plot(alphas, [c[f'{a:g}']['self_calibrated_on_target']['fpr_median'] for a in alphas],
                    '-', marker=mk, ms=3, lw=1, color=cols[k],
                    label=f'{labs[k]}, self-calibrated\non target stream' if mk == 'o' else None)
    ax.set_xscale('log')
    ax.set_yscale('log')
    ax.set_xlabel('Requested false-alarm budget α')
    ax.set_ylabel('Achieved FPR on target sensor (median)')
    ax.set_ylim(top=30)
    ax.set_xticks(alphas)
    ax.set_xticklabels([f'{a:g}' for a in alphas])
    ax.minorticks_off()
    ax.set_title('(c) Threshold transfer', fontsize=9)
    ax.legend(frameon=False, fontsize=4.8, loc='upper left')
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)


def fig7_hybrid(out):
    hyb = load('hybrid_screening.json')
    fig, axes = plt.subplots(1, 2, figsize=(6.8, 2.9))

    # (a) YNoise with default, handed-over and label-tuned parameters
    ax = axes[0]
    groups = ['All'] + [s.replace('x', ' × ') + ' px' for s in hyb['per_sensor']]
    default = [hyb['ynoise_full']['default']['auc']['mean']] + \
        [v['ynoise_default_auc']['mean'] for v in hyb['per_sensor'].values()]
    handoff = [hyb['ynoise_full']['handoff']['auc']['mean']] + \
        [v['ynoise_handoff_auc']['mean'] for v in hyb['per_sensor'].values()]
    stage1 = [hyb['stage1']['auc']['mean']] + \
        [v['stage1_auc']['mean'] for v in hyb['per_sensor'].values()]
    x = np.arange(len(groups))
    w = 0.26
    ax.bar(x - w, stage1, w, color='#c0392b', label='Proposed, zero-knowledge (stage 1)')
    ax.bar(x, default, w, color='#bdc3c7', label='YNoise, published defaults')
    ax.bar(x + w, handoff, w, color='#8e44ad', label='YNoise, dt = W handed over (no labels)')
    ax.axhline(hyb['ynoise_cv_reference']['auc_mean'], color='#8e44ad', ls=':', lw=1,
               label='YNoise, label-tuned CV (all)')
    ax.set_xticks(x)
    ax.set_xticklabels(groups, fontsize=7)
    ax.set_ylabel('Mean ROC-AUC')
    ax.set_ylim(0.5, 1.0)
    ax.set_title('(a) Parameter hand-over to YNoise', fontsize=9)
    ax.legend(frameon=False, fontsize=5.5, loc='upper right')

    # (b) two-stage front end: AUC and stage-2 workload against the screened fraction
    ax = axes[1]
    fr = [float(f) for f in hyb['screen']]
    for name, col, lab in (('handoff', '#8e44ad', 'stage 2 with dt = W'),
                           ('default', '#7f8c8d', 'stage 2 with default dt'),
                           ('pseudo', '#2980b9', 'stage 2 tuned on stage-1 pseudo-labels')):
        ax.plot(fr, [hyb['screen'][f'{f:g}'][name]['auc']['mean'] for f in fr], '-o', ms=3, lw=1,
                color=col, label=lab)
    ax.axhline(hyb['stage1']['auc']['mean'], color='#c0392b', ls='--', lw=1, label='stage 1 alone')
    ax.set_xscale('log')
    ax.set_xticks(fr)
    ax.set_xticklabels([f'{f:g}' for f in fr])
    ax.minorticks_off()
    ax.set_xlabel('Fraction of the stream passed to stage 2')
    ax.set_ylabel('Mean ROC-AUC of the two-stage score')
    ax.set_title('(b) Two-stage front end', fontsize=9)
    ax2 = ax.twinx()
    ax2.plot(fr, [hyb['screen'][f'{f:g}']['handoff']['stage2_time_over_ynoise_full']['median'] for f in fr],
             's:', ms=3, lw=0.9, color='k', label='stage-2 time / YNoise on full stream')
    ax2.set_ylabel('Stage-2 time relative to full stream', fontsize=7)
    ax2.set_ylim(0, 1.3)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, frameon=False, fontsize=5.2, loc='upper left')
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)


def fig8_qualitative(out):
    meta = load('qualitative_example.json')
    imgs = np.load(RESULTS / 'qualitative_example.npz')
    f_screen = meta['screen_fraction']
    panels = [('raw', 'All events\n(input)'), ('object', 'Annotated object\n(reference)'),
              ('proposal_zk', 'Proposed, no labels\n(stage 1 alone)'),
              ('ynoise_default', 'YNoise alone,\ndefault dt'),
              ('ynoise_handoff', 'Hybrid, hand-over:\nYNoise with dt = W'),
              ('two_stage', f'Hybrid, two-stage:\nscreen \u2192 YNoise, f = {f_screen:g}'),
              ('fano', 'Fano-factor score\n(ablation)')]
    keys = list(meta['recordings'])
    fig, axes = plt.subplots(len(keys), len(panels), figsize=(6.8, 1.15 * len(keys) + 0.4))
    letters = 'abcdefghijklmn'
    for i, key in enumerate(keys):
        m = meta['recordings'][key]
        for j, (name, title) in enumerate(panels):
            ax = axes[i, j]
            img = imgs[f'{key}_{name}'].astype(float)
            ax.imshow(np.log1p(img), cmap='gray_r', interpolation='nearest', aspect='equal')
            ax.set_xticks([])
            ax.set_yticks([])
            lab = f'({letters[i * len(panels) + j]}) {title}'
            if name in m['methods']:
                mm = m['methods'][name]
                lab += f'\nTPR {mm["tpr"]:.2f}, FPR {mm["fpr"]:.4f}'
            elif name == 'raw':
                lab += f'\n{m["n_events"]} events, {m["duration_s"]:.1f} s'
            else:
                lab += f'\n{m["n_object"]} events'
            ax.set_title(lab, fontsize=4.9, pad=2)
            if j == 0:
                sensor = key.replace('x', ' \u00d7 ')
                ax.set_ylabel(f'{sensor} px\nW = {m["window_ms"]:.0f} ms', fontsize=6.5)
    fig.tight_layout(h_pad=0.3, w_pad=0.25)
    fig.savefig(out, bbox_inches='tight')
    plt.close(fig)


FIGURES = [
    ('fig1_method.png', fig1_schematic),
    ('fig2_methods_auc.png', fig2_methods),
    ('fig3_paired.png', fig3_paired),
    ('fig4_sparsity_cost.png', fig4_sparsity),
    ('fig5_calibration.png', fig5_calibration),
    ('fig6_zero_knowledge.png', fig6_zero_knowledge),
    ('fig7_hybrid.png', fig7_hybrid),
    ('fig8_qualitative.png', fig8_qualitative),
]


def main():
    for name, fn in FIGURES:
        fn(HERE / name)
        tiff = (HERE / name).with_suffix('.tiff')
        with Image.open(HERE / name) as im:
            im.save(tiff, compression='tiff_lzw', dpi=im.info.get('dpi', (300, 300)))
        print('wrote', name, 'and', tiff.name)


if __name__ == '__main__':
    main()
