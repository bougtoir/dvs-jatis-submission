#!/usr/bin/env python3
"""Generate the JATIS manuscript (manuscript_jatis.docx) with inline figures.

Target: Journal of Astronomical Telescopes, Instruments, and Systems (SPIE).
SPIE journal format: US letter, 1-inch margins, Times New Roman, 16 pt bold
title, 10 pt abstract with 3-6 keywords, numbered sections, superscript
numbered citations in order of first appearance, 10 pt figure/table captions,
Disclosures and Code/Data Availability sections before References.

All numbers are read from results/*.json through manuscript_numbers.py; the
figures are produced by make_jatis_figures.py from the same JSON files.
Citations are written as {cite:key} tokens and numbered automatically in
order of first appearance; the reference list is emitted in that order.
"""

import json
import re
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Inches, Pt
from lxml import etree

from manuscript_numbers import auc, auc_sd, fmt, load_numbers, pct, scaling

OUT_DIR = Path(__file__).resolve().parent
FIG_DIR = OUT_DIR
N = load_numbers()

# =========================================================
# Reference database (keys are referenced in the text; numbering is automatic)
# =========================================================
REFS = {
    'gallego': 'G. Gallego, T. Delbruck, G. Orchard, et al., "Event-based vision: a survey," '
               'IEEE Trans. Pattern Anal. Mach. Intell. 44(1), 154-180 (2022).',
    'lichtsteiner': 'P. Lichtsteiner, C. Posch, and T. Delbruck, "A 128 x 128 120 dB 15 us latency '
                    'asynchronous temporal contrast vision sensor," IEEE J. Solid-State Circuits 43(2), '
                    '566-576 (2008).',
    'cohen': 'G. Cohen, S. Afshar, B. Morreale, et al., "Event-based sensing for space situational '
             'awareness," J. Astronaut. Sci. 66, 125-141 (2019).',
    'afshar': 'S. Afshar, A. P. Nicholson, A. van Schaik, and G. Cohen, "Event-based object detection '
              'and tracking for space situational awareness," IEEE Sens. J. 20(24), 15117-15132 (2020).',
    'ralph': 'N. O. Ralph, A. Marcireau, S. Afshar, et al., "Astrometric calibration and source '
             'characterisation of the latest generation neuromorphic event-based cameras for space '
             'imaging," Astrodynamics 7(4), 415-443 (2023).',
    'nozaki': 'Y. Nozaki and T. Delbruck, "Temperature and parasitic photocurrent effects in dynamic '
              'vision sensors," IEEE Trans. Electron Devices 64(8), 3239-3245 (2017).',
    'guo': 'S. Guo and T. Delbruck, "Low cost and latency event camera background activity denoising," '
           'IEEE Trans. Pattern Anal. Mach. Intell. 45(1), 785-795 (2023).',
    'delbruck2008': 'T. Delbruck, "Frame-free dynamic digital vision," in Proc. Int. Symp. Secure-Life '
                    'Electronics, Advanced Electronics for Quality Life and Society, 21-26, '
                    'University of Tokyo (2008).',
    'khodamoradi': 'A. Khodamoradi and R. Kastner, "O(N)-space spatiotemporal filter for reducing noise '
                   'in neuromorphic vision sensors," IEEE Trans. Emerg. Top. Comput. 9(1), 15-23 (2021).',
    'feng': 'Y. Feng, H. Lv, H. Liu, et al., "Event density based denoising method for dynamic vision '
            'sensor," Appl. Sci. 10(6), 2024 (2020).',
    'baldwin': 'R. W. Baldwin, M. Almatrafi, V. Asari, and K. Hirakawa, "Event probability mask (EPM) '
               'and event denoising convolutional neural network (EDnCNN) for neuromorphic cameras," '
               'in Proc. IEEE/CVF Conf. Computer Vision and Pattern Recognition, 1701-1710 (2020).',
    'neyman': 'J. Neyman and E. S. Pearson, "On the problem of the most efficient tests of statistical '
              'hypotheses," Philos. Trans. R. Soc. A 231, 289-337 (1933).',
    'kay': 'S. M. Kay, Fundamentals of Statistical Signal Processing, Volume II: Detection Theory, '
           'Prentice Hall, Upper Saddle River (1998).',
    'kingman': 'J. F. C. Kingman, Poisson Processes, Oxford University Press, Oxford (1993).',
    'fano': 'U. Fano, "Ionization yield of radiations. II. The fluctuations of the number of ions," '
            'Phys. Rev. 72(1), 26-29 (1947).',
    'fisher': 'R. A. Fisher, Statistical Methods for Research Workers, Oliver and Boyd, Edinburgh (1925).',
    'tonic': 'G. Lenz, K. Chaney, S. B. Shrestha, et al., "Tonic: event-based datasets and '
             'transformations," Zenodo, doi:10.5281/zenodo.5079802 (2021).',
    'numba': 'S. K. Lam, A. Pitrou, and S. Seibert, "Numba: a LLVM-based Python JIT compiler," in Proc. '
             'Second Workshop on the LLVM Compiler Infrastructure in HPC, 1-6 (2015).',
    'sklearn': 'F. Pedregosa, G. Varoquaux, A. Gramfort, et al., "Scikit-learn: machine learning in '
               'Python," J. Mach. Learn. Res. 12, 2825-2830 (2011).',
    'mcclish': 'D. K. McClish, "Analyzing a portion of the ROC curve," Med. Decis. Making 9(3), '
               '190-195 (1989).',
    'wilcoxon': 'F. Wilcoxon, "Individual comparisons by ranking methods," Biometrics Bull. 1(6), '
                '80-83 (1945).',
    'holm': 'S. Holm, "A simple sequentially rejective multiple test procedure," Scand. J. Stat. 6(2), '
            '65-70 (1979).',
}

CITE_ORDER = []  # keys in order of first appearance


def cite_number(key):
    if key not in REFS:
        raise KeyError(f'unknown reference key: {key}')
    if key not in CITE_ORDER:
        CITE_ORDER.append(key)
    return CITE_ORDER.index(key) + 1


def _format_numbers(nums):
    nums = sorted(set(nums))
    out, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        if j - i >= 2:
            out.append(f'{nums[i]}-{nums[j]}')
        else:
            out.extend(str(n) for n in nums[i:j + 1])
        i = j + 1
    return ','.join(out)


# =========================================================
# OMML equation helpers
# =========================================================
def _mr(parent, text, italic=True):
    r = etree.SubElement(parent, qn('m:r'))
    if not italic:
        rPr = etree.SubElement(r, qn('m:rPr'))
        sty = etree.SubElement(rPr, qn('m:sty'))
        sty.set(qn('m:val'), 'p')
    t = etree.SubElement(r, qn('m:t'))
    t.text = text
    t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
    return r


def _sub(parent, base, sub, italic=True):
    el = etree.SubElement(parent, qn('m:sSub'))
    e = etree.SubElement(el, qn('m:e'))
    _mr(e, base, italic)
    s = etree.SubElement(el, qn('m:sub'))
    _mr(s, sub)
    return el


def _sup(parent, base, sup):
    el = etree.SubElement(parent, qn('m:sSup'))
    e = etree.SubElement(el, qn('m:e'))
    _mr(e, base)
    s = etree.SubElement(el, qn('m:sup'))
    _mr(s, sup)
    return el


def _frac(parent, num_builder, den_builder):
    f = etree.SubElement(parent, qn('m:f'))
    num_builder(etree.SubElement(f, qn('m:num')))
    den_builder(etree.SubElement(f, qn('m:den')))
    return f


def _delim(parent, content_builder, left='(', right=')'):
    d = etree.SubElement(parent, qn('m:d'))
    dPr = etree.SubElement(d, qn('m:dPr'))
    etree.SubElement(dPr, qn('m:begChr')).set(qn('m:val'), left)
    etree.SubElement(dPr, qn('m:endChr')).set(qn('m:val'), right)
    content_builder(etree.SubElement(d, qn('m:e')))
    return d


def _hat(parent, text):
    acc = etree.SubElement(parent, qn('m:acc'))
    accPr = etree.SubElement(acc, qn('m:accPr'))
    etree.SubElement(accPr, qn('m:chr')).set(qn('m:val'), '\u0302')
    _mr(etree.SubElement(acc, qn('m:e')), text)
    return acc


def _nary(parent, chr_, sub_builder, sup_builder, body_builder):
    n = etree.SubElement(parent, qn('m:nary'))
    pr = etree.SubElement(n, qn('m:naryPr'))
    etree.SubElement(pr, qn('m:chr')).set(qn('m:val'), chr_)
    etree.SubElement(pr, qn('m:limLoc')).set(qn('m:val'), 'undOvr')
    sub_builder(etree.SubElement(n, qn('m:sub')))
    sup_builder(etree.SubElement(n, qn('m:sup')))
    body_builder(etree.SubElement(n, qn('m:e')))
    return n


def add_display_equation(doc, builder_func, eq_num):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(6)
    omathpara = etree.SubElement(p._element, qn('m:oMathPara'))
    omath = etree.SubElement(omathpara, qn('m:oMath'))
    builder_func(omath)
    run = p.add_run(f'    ({eq_num})')
    run.font.size = Pt(12)
    return p


# Eq. (1): lambda_hat_xy = q / Delta_xy^(q)
def eq_rate(o):
    _hat(o, '\u03bb')
    _sub(o, '', 'xy')
    _mr(o, ' = ')

    def num(n):
        _mr(n, '\u2212ln', italic=False)
        _delim(n, lambda e: _mr(e, '1 \u2212 q'))

    def den(d):
        _sub(d, '\u0394', 'xy')
        _mr(d, '(q)')

    _frac(o, num, den)


# Eq. (2): N_i = #{ j : t_i - W <= t_j < t_i, |x_j - x_i| <= r, |y_j - y_i| <= r }
def eq_count(o):
    _sub(o, 'N', 'i')
    _mr(o, ' = #')

    def body(e):
        _mr(e, 'j : ')
        _sub(e, 't', 'i')
        _mr(e, ' \u2212 W \u2264 ')
        _sub(e, 't', 'j')
        _mr(e, ' < ')
        _sub(e, 't', 'i')
        _mr(e, ', ')
        _mr(e, '\u2016')
        _delim(e, lambda f: (_sub(f, 'x', 'j'), _mr(f, ' \u2212 '), _sub(f, 'x', 'i'), _mr(f, ', '),
                             _sub(f, 'y', 'j'), _mr(f, ' \u2212 '), _sub(f, 'y', 'i')))
        _mr(e, '\u2016')
        _sub(e, '', '\u221e')
        _mr(e, ' \u2264 r')

    _delim(o, body, '{', '}')


# Eq. (3): Lambda_i = W * sum_{|u|,|v| <= r} lambda_hat_{x_i+u, y_i+v}
def eq_lambda(o):
    _sub(o, '\u039b', 'i')
    _mr(o, ' = W ')
    _nary(o, '\u2211', lambda s: _mr(s, '|u|,|v| \u2264 r'), lambda s: None,
          lambda b: (_hat(b, '\u03bb'), _sub(b, '', 'x_i+u, y_i+v')))


# Eq. (4): P_i = Pr(N >= N_i | Lambda_i) = sum_{k >= N_i} e^{-Lambda} Lambda^k / k!
def eq_tail(o):
    _sub(o, 'P', 'i')
    _mr(o, ' = Pr', italic=False)
    _delim(o, lambda e: (_mr(e, 'N \u2265 '), _sub(e, 'N', 'i'), _mr(e, ' | '), _sub(e, '\u039b', 'i')))
    _mr(o, ' = ')
    _nary(o, '\u2211', lambda s: (_mr(s, 'k = '), _sub(s, 'N', 'i')), lambda s: _mr(s, '\u221e'),
          lambda b: (_sup(b, 'e', '\u2212\u039b_i'),
                     _frac(b, lambda n: _sup(n, '\u039b', 'k'), lambda d: _mr(d, 'k!'))))


# Eq. (5): threshold tau_alpha = alpha-quantile of {P_i}
def eq_threshold(o):
    _sub(o, '\u03c4', '\u03b1')
    _mr(o, ' = ')
    _mr(o, 'Q', italic=False)
    _sub(o, '', '\u03b1')
    _delim(o, lambda e: (_mr(e, '{'), _sub(e, 'P', 'i'), _mr(e, '}')))
    _mr(o, ',      flag event i if ', italic=False)
    _sub(o, 'P', 'i')
    _mr(o, ' \u2264 ')
    _sub(o, '\u03c4', '\u03b1')


# Eq. (6): cost exponent  T(f) proportional to f^beta
def eq_cost(o):
    _mr(o, 'T', italic=True)
    _delim(o, lambda e: _mr(e, 'f'))
    _mr(o, ' \u221d ')
    _sup(o, 'f', '\u03b2')
    _mr(o, ',      \u03b2 = ', italic=False)
    _frac(o, lambda n: _mr(n, 'd log T'), lambda d: _mr(d, 'd log f'))


# =========================================================
# Document helpers
# =========================================================
TOKEN = re.compile(r'(\{cite:[^}]+\})')


def add_runs(p, text, size=None, bold=False, italic=False):
    for part in TOKEN.split(text):
        if part.startswith('{cite:'):
            keys = [k.strip() for k in part[6:-1].split(',')]
            run = p.add_run(_format_numbers([cite_number(k) for k in keys]))
            run.font.superscript = True
            if size:
                run.font.size = size
        elif part:
            run = p.add_run(part)
            run.bold = bold
            run.italic = italic
            if size:
                run.font.size = size


def para(doc, text, size=None, bold=False, italic=False, align=None,
         space_after=6, space_before=0, first_line_indent=None):
    p = doc.add_paragraph()
    if align is not None:
        p.alignment = align
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.space_before = Pt(space_before)
    if first_line_indent is not None:
        p.paragraph_format.first_line_indent = first_line_indent
    add_runs(p, text, size=size, bold=bold, italic=italic)
    return p


def heading(doc, text, level=1):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(12 if level == 1 else 8)
    p.paragraph_format.space_after = Pt(4)
    p.paragraph_format.keep_with_next = True
    run = p.add_run(text)
    run.font.name = 'Times New Roman'
    if level == 1:
        run.font.size = Pt(12)
        run.bold = True
    elif level == 2:
        run.font.size = Pt(12)
        run.italic = True
    else:
        run.font.size = Pt(11)
        run.italic = True
    return p


def figure(doc, filename, caption, width=Inches(6.0)):
    path = FIG_DIR / filename
    if not path.exists():
        raise FileNotFoundError(path)
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(12)
    p.paragraph_format.keep_with_next = True
    p.add_run().add_picture(str(path), width=width)
    cap = doc.add_paragraph()
    cap.paragraph_format.space_before = Pt(6)
    cap.paragraph_format.space_after = Pt(14)
    add_runs(cap, caption, size=Pt(10))
    return cap


def table(doc, caption, headers, rows, col_widths=None, font_pt=9):
    cap = doc.add_paragraph()
    cap.paragraph_format.space_before = Pt(12)
    cap.paragraph_format.space_after = Pt(4)
    cap.paragraph_format.keep_with_next = True
    add_runs(cap, caption, size=Pt(10))
    t = doc.add_table(rows=len(rows) + 1, cols=len(headers))
    t.style = 'Table Grid'
    for i, h in enumerate(headers):
        cell = t.rows[0].cells[i]
        cell.text = ''
        run = cell.paragraphs[0].add_run(h)
        run.bold = True
        run.font.size = Pt(font_pt)
    for ri, row in enumerate(rows):
        for ci, val in enumerate(row):
            cell = t.rows[ri + 1].cells[ci]
            cell.text = ''
            run = cell.paragraphs[0].add_run(str(val))
            run.font.size = Pt(font_pt)
    if col_widths:
        for row in t.rows:
            for ci, w in enumerate(col_widths):
                row.cells[ci].width = Inches(w)
    doc.add_paragraph().paragraph_format.space_after = Pt(6)
    return t


# =========================================================
# Manuscript content
# =========================================================
def label(key):
    return N['methods'][key]['label']


SHORT = {
    'bilateral': 'event bilateral filter',
    'ynoise': 'YNoise',
    'motion': 'motion-compensated proxy',
    'temporal': 'background-activity filter',
    'dwf': 'DWF',
    'nearest': 'nearest-neighbour filter',
    'knoise': 'kNoise',
}


def short(key):
    return SHORT.get(key, label(key))


def zk_max_drop(key):
    """Largest AUC lost over both transfer directions when the source-selected configuration
    is applied to the other sensor."""
    return max(v[key]['drop'] for v in N['zk']['cross_sensor_transfer'].values())


def pfmt(p):
    return f'= {p:.3f}' if p >= 0.001 else '< 0.001'


def build():
    doc = Document()
    style = doc.styles['Normal']
    style.font.name = 'Times New Roman'
    style.font.size = Pt(12)
    style.element.rPr.rFonts.set(qn('w:eastAsia'), 'Times New Roman')
    style.paragraph_format.line_spacing = 1.15
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Inches(8.5), Inches(11)
    for side in ('top_margin', 'bottom_margin', 'left_margin', 'right_margin'):
        setattr(sec, side, Inches(1.0))

    m = N['methods']
    tests = N['tests']
    sc = N['selfcal']
    pa = N['pauc']

    # ---------------- Title block ----------------
    para(doc, 'A training-free, event-driven Poisson likelihood-ratio detector for faint object '
              'events in sparse neuromorphic space-imaging streams',
         size=Pt(16), bold=True, space_after=8)
    para(doc, '[Author names]', size=Pt(12), bold=True, space_after=2)
    para(doc, '[Affiliations, addresses]', size=Pt(10), space_after=10)

    # ---------------- Abstract ----------------
    abstract = (
        'Abstract. In event-camera space surveillance, faint objects must be separated from '
        'background-activity noise. '
        'We describe a detector that works on the event stream without frames, voxel grids or labels: '
        'each pixel\'s background rate is estimated from its inter-event intervals, and the Poisson tail '
        'probability of the count in a causal neighbourhood is the noise probability. '
        f'On all {N["n_rec"]} labelled recordings of the Event-Based Space Situational Awareness dataset, '
        'with every comparator tuned by the same grouped cross-validation, it reaches a mean '
        f'area under the receiver operating characteristic curve (ROC-AUC) of {auc(N, "edlr")}, '
        f'indistinguishable from the event bilateral filter ({auc(N, "bilateral")}) and '
        f'{abs(tests["ynoise"]["auc_diff_mean"]):.2f} below YNoise. '
        f'Its cost falls with the number of events (exponent '
        f'{scaling(N, "edlr", "cost_exponent_mean"):.2f}), whereas the voxelised form of the same test '
        f'stays constant ({scaling(N, "plr", "cost_exponent_mean"):.2f}). The operating point follows '
        'a false-alarm budget without labels: a requested 0.01 yields a '
        f'median {sc["edlr"]["0.01"]["fpr_median"]:.4f}. In a zero-knowledge test, '
        'a window set from the estimated background rate transfers between sensor '
        f'models with an AUC loss of at most {zk_max_drop("edlr_adaptive"):.2f}, against '
        f'{zk_max_drop("edlr"):.2f} for a fixed window; handed to YNoise as its time constant, it '
        f'raises that filter from {N["hyb"]["ynoise_full"]["default"]["auc"]["mean"]:.2f} to '
        f'{N["hyb"]["ynoise_full"]["handoff"]["auc"]["mean"]:.2f} without labels. The method follows from the '
        'pixel physics of the sensor and suits surveys of new fields.'
    )
    n_words = len(abstract.replace('Abstract. ', '').split())
    assert n_words <= 200, f'abstract has {n_words} words'
    para(doc, abstract, size=Pt(10), space_after=6)
    para(doc, 'Keywords: event camera; dynamic vision sensor; space situational awareness; '
              'Poisson process; likelihood ratio; denoising.', size=Pt(10), space_after=4)
    para(doc, '*Corresponding author: [e-mail]', size=Pt(10), space_after=12)

    # ---------------- 1 Introduction ----------------
    heading(doc, '1 Introduction')
    para(doc,
         'Event cameras, or dynamic vision sensors (DVS), emit an asynchronous event whenever the '
         'log-intensity at a pixel changes by more than a contrast threshold, giving microsecond timing '
         'and a dynamic range above 120 dB without a global exposure.{cite:gallego,lichtsteiner} These '
         'properties have motivated their use for optical space situational awareness (SSA), where '
         'resident space objects (RSOs) appear as faint, slowly moving point sources against a static '
         'star field, and where the data volume of conventional framing cameras is dominated by empty '
         'sky.{cite:cohen,afshar,ralph} In such observations the event stream is extremely sparse: in the '
         f'recordings analysed here the median rate is {N["rate_median"] / 1e3:.0f} kilo-events per '
         'second over a 180 x 240-pixel or 240 x 304-pixel array, and only '
         f'{pct(N["sig_median"])} of events (median) fall inside the annotated object boxes.')
    para(doc,
         'The dominant contaminant is background activity (BA): spontaneous events produced by junction '
         'leakage and shot noise in the pixel front end, whose rate depends on temperature and '
         'illumination and varies from pixel to pixel.{cite:nozaki,guo} Event-denoising filters are '
         'therefore a mature topic. Spatiotemporal correlation filters pass an event only if a neighbour '
         'fired recently,{cite:delbruck2008,khodamoradi} density filters count neighbours within a '
         'window,{cite:feng} the double-window filter compares an event against the most recent signal '
         'and noise events,{cite:guo} and supervised networks learn the decision from labelled '
         'streams.{cite:baldwin,guo} These methods share two properties that matter for astronomy. '
         'First, they output a heuristic score (a neighbour count or a network logit) whose threshold '
         'must be tuned on labelled data for each sensor and observing condition. Second, several '
         'high-performing variants are implemented, or are most naturally described, on a voxel grid of '
         'time bins and pixels, so their cost is set by the size of the array and the length of the '
         'recording rather than by the number of events; as the stream becomes sparser, most of the '
         'work is spent on empty voxels.')
    para(doc,
         'We take a different starting point. Under the null hypothesis that a pixel produces only BA, '
         'its events form an approximately Poisson point process with a pixel-specific rate that can be '
         'estimated from the stream itself. A faint moving object adds events that are clustered in '
         'space and time relative to that rate. The optimal test for such a hypothesis, by the '
         'Neyman-Pearson lemma, is a likelihood ratio,{cite:neyman,kay} and for a Poisson count it '
         'reduces to a tail probability that is monotone in the observed neighbourhood count given the '
         'expected count. This gives an event-driven detector with three properties: (i) it requires no '
         'labels because the background model is fitted to the same stream it filters; (ii) its output '
         'is a probability on a common scale, so a false-alarm budget can be converted directly into a '
         'threshold; and (iii) it touches each event once with a small neighbourhood lookup, so its cost '
         'scales with the number of events rather than with the voxel grid.')
    para(doc,
         'These properties follow from the physics of the sensor rather than being imposed on it. A DVS '
         'pixel is an independent, asynchronous circuit whose output is a sequence of timestamps, and its '
         'background activity arises from leakage and shot noise in that circuit,{cite:lichtsteiner,nozaki} '
         'so a per-pixel Poisson point process is the natural null model, and the pixel-to-pixel variation '
         'of the noise rate is a reason to estimate the rate per pixel rather than a nuisance. Converting '
         'the stream to frames or voxels discards the microsecond timing the sensor provides and replaces a '
         'data volume proportional to the number of events with one proportional to the array and the '
         'exposure, undoing the two advantages that motivate the use of event cameras in astronomy. A '
         'detector that touches each event once, in arrival order, with state proportional to the number '
         'of pixels, preserves both and maps directly onto the streaming, near-sensor processing for which '
         'the sensor was designed. Finally, because the operating point is derived from a false-alarm '
         'budget on the same stream, the detector needs neither labelled data nor manual re-tuning when '
         'it is pointed at a new field, a new sensor or a new observing condition; this is the situation '
         'of survey and discovery observations, where no catalogue of what should be detected exists.')
    para(doc,
         'This paper makes the following '
         'contributions. (1) We formulate the event-driven Poisson likelihood-ratio (LR) detector and '
         'describe an implementation whose per-event cost is logarithmic in the stream length. (2) We '
         f'evaluate it against {sum(1 for k in m if not m[k]["supervised"]) - 1} unsupervised '
         f'comparators and two supervised networks on all {N["n_rec"]} labelled recordings of the '
         'Event-Based Space Situational Awareness (EBSSA) dataset,{cite:afshar} using the published '
         'object annotations as ground truth and grouped cross-validation for every tuned parameter. '
         '(3) We measure how the cost of event-driven and voxel-based formulations changes when the real '
         'streams are thinned by up to two orders of magnitude. (4) We show that the probability output '
         'can be self-calibrated to a requested false-alarm rate without labels, and quantify the '
         'detection rate obtained at that budget. (5) We test a zero-knowledge deployment, in which the '
         'configuration is fixed on one sensor model and the detector is pointed, unchanged, at the '
         'other, with a temporal window that is set from the estimated background rate rather than in '
         'milliseconds. (6) We show that this rate-derived window, handed to the strongest comparator '
         'as its time constant, recovers most of the accuracy that labelled tuning gives that comparator, and we '
         'measure a two-stage front end in which the proposal screens and the comparator confirms. '
         'The claim is one of equivalence with added properties, '
         'not of superiority: the detector is comparable in accuracy to most published unsupervised '
         'event filters when those filters are given the same cross-validated tuning, while it alone '
         'needs no labels, no per-sensor tuning and no manually chosen threshold in operation, and it is '
         'much cheaper than frame- or voxel-based processing as the stream becomes sparse. We do not claim '
         'to exceed supervised networks trained on labelled data from the same sensors.')

    # ---------------- 2 Methods ----------------
    heading(doc, '2 Methods')
    heading(doc, '2.1 Event stream and hypotheses', 2)
    para(doc,
         'An event camera produces a time-ordered stream of events e_i = (x_i, y_i, t_i, p_i), where '
         '(x_i, y_i) is the pixel address, t_i the timestamp in microseconds and p_i the polarity. We '
         'ignore polarity in the detector. For every event we test the null hypothesis H0 that the '
         'events in a small causal neighbourhood of e_i were produced by the pixel-wise BA process alone, '
         'against the alternative H1 that an additional source contributed to them. Under H0 we model the '
         'BA of pixel (x, y) as a homogeneous Poisson process with rate \u03bb_xy;{cite:kingman} the rates '
         'differ between pixels because leakage and hot-pixel behaviour do.{cite:nozaki} Figure 1 '
         'summarises the pipeline.')
    figure(doc, 'fig1_method.png',
           'Fig. 1 Event-driven Poisson likelihood-ratio detector. Each event is scored from the '
           'estimated background rate of its neighbourhood and the number of events that arrived in '
           'that neighbourhood during the preceding window; the tail probability is used directly as the '
           'noise probability, so the operating point can be set from a false-alarm budget without labels '
           'and no voxel grid is constructed.')

    heading(doc, '2.2 Per-pixel background-rate estimation', 2)
    para(doc,
         'The rate of each pixel is estimated from its own inter-event intervals. Let \u0394_xy(q) be the '
         'q-quantile of the intervals between successive events at pixel (x, y). For an exponential '
         'interval distribution with rate \u03bb, the q-quantile is -ln(1 - q)/\u03bb, so')
    add_display_equation(doc, eq_rate, 1)
    para(doc,
         'is a consistent estimate of the BA rate. A high quantile is deliberately used: object events '
         'shorten a minority of the intervals at the pixels they cross, and a high quantile of the '
         'interval distribution is insensitive to that minority, whereas the mean interval is not. '
         'Pixels with fewer than four events receive the median rate of the array. The quantile q, the '
         'window W and the radius r introduced below are the only tuned parameters and were selected by '
         'cross-validation (Sec. 2.8).')

    heading(doc, '2.3 Event-driven Poisson tail test', 2)
    para(doc,
         'For event i let N_i be the number of events, including e_i itself, that arrived at most W '
         'microseconds earlier within the (2r + 1) x (2r + 1) pixel neighbourhood centred on (x_i, y_i):')
    add_display_equation(doc, eq_count, 2)
    para(doc, 'Under H0 the count is Poisson with mean')
    add_display_equation(doc, eq_lambda, 3)
    para(doc,
         'The likelihood ratio of H1 against H0 for a Poisson count with a larger alternative rate is '
         'monotone increasing in N_i, so by the Neyman-Pearson lemma thresholding N_i given \u039b_i is '
         'the most powerful test at any size, and the size attained by observing N_i is the upper tail '
         'probability{cite:kay}')
    add_display_equation(doc, eq_tail, 4)
    para(doc,
         'P_i is reported as the noise probability of event i; small values indicate an event that is '
         'unlikely under the background model. Because \u039b_i differs between events, the comparison '
         'is made on the probability scale rather than on the raw count, which is what allows one '
         'threshold to serve the whole array. The count includes e_i, so an isolated event in a quiet '
         'region has N_i = 1 and P_i close to one.')
    para(doc,
         'The stream is processed in time order. A per-pixel ring of recent timestamps and a running '
         'pointer into the sorted stream give N_i in O((2r + 1)^2 + log n) operations per event, and the '
         'neighbourhood rate sum in Eq. (3) is a box filter over the rate map computed once. No voxel '
         'grid or dense frame is allocated, so memory is proportional to the number of pixels plus the '
         'number of events. The implementation uses NumPy and Numba.{cite:numba}')

    heading(doc, '2.4 Self-calibrated operating point', 2)
    para(doc,
         'A tracker downstream of the detector typically tolerates a stated false-alarm rate \u03b1 '
         'rather than a stated threshold. Because P_i is a probability, a label-free operating point is '
         'obtained by asking the detector to pass exactly a fraction \u03b1 of the stream:')
    add_display_equation(doc, eq_threshold, 5)
    para(doc,
         'where Q_\u03b1 denotes the empirical \u03b1-quantile. If the object fraction is small and the '
         'background model is adequate, the achieved false-alarm rate on true noise events is close to '
         '\u03b1. The same rule can be applied to any score, so we also apply it to the comparators; the '
         'difference is that an integer neighbour count has few distinct values and cannot be cut at an '
         'arbitrary quantile, whereas a continuous probability can. The inequality is strict, so that '
         'when the quantile falls inside a run of tied scores the tied events are not passed and the '
         'achieved rate stays at or below \u03b1; a tie of this kind arises for the proposed detector '
         'itself when \u039b_i is so large that P_i rounds to one, and the fraction of such saturated '
         'scores is a label-free diagnostic that the window is too long for the background rate.')

    heading(doc, '2.5 Rate-adaptive window for zero-knowledge deployment', 2)
    zc = N['zk_config']
    para(doc,
         'A window stated in milliseconds is a sensor-specific choice: the same W gives a background '
         'expectation \u039b_i that is several times larger on a sensor with a higher BA rate, and the '
         'tail probability saturates. The window is therefore also specified in a dimensionless form, as '
         'the expected number k of background events in the neighbourhood: W is set so that the median of '
         '\u039b_i / W over the events of the stream, multiplied by W, equals k, i.e. W = k / '
         'median(\u03a3\u03bb\u0302) with the sum over the (2r + 1)^2 neighbourhood, clipped to '
         '[1 ms, 2 s]. The rate estimate is the label-free one of Sec. 2.2, so the rule uses nothing but '
         'the target stream. The deployment configuration used in the zero-knowledge analysis is '
         f'r = {zc["radius"]}, k = {zc["expected_count"]:g}, q = {zc["interval_quantile"]}. The value of '
         'k was fixed once, from the trade-off between ranking accuracy and saturation over '
         f'k \u2208 {{{", ".join(f"{x:g}" for x in N["zk_grid_k"])}}} on the EBSSA data (Sec. 3.6); it is '
         'therefore a constant of the method chosen on this dataset, not a per-sensor setting, and the '
         'cross-sensor protocol below tests whether it carries over.')

    heading(doc, '2.6 Data', 2)
    para(doc,
         'We used the labelled split of the EBSSA dataset,{cite:afshar} downloaded through the Tonic '
         'library.{cite:tonic} It contains recordings of RSOs and stars made with two event-camera '
         f'models: {N["n_small"]} recordings from a 180 x 240-pixel sensor and {N["n_large"]} from a '
         '240 x 304-pixel sensor. Ground truth is the published object annotation: an event is labelled '
         'as object if it lies within a 10 x 10-pixel box around an annotated position and within 10 ms '
         'of its timestamp; all other events are labelled as background. For each recording the analysis '
         'window was restricted to the annotated interval (plus 0.5 s on each side) and the central '
         f'{N["max_events"]:,} events of that window were kept, giving {N["n_events_total"]:,} events in '
         f'total. Recordings with fewer than 50 events in either class were excluded, leaving all '
         f'{N["n_rec"]} labelled recordings. Median event rate was {N["rate_median"] / 1e3:.1f} k '
         f'events/s (range {N["rate_min"] / 1e3:.1f}-{N["rate_max"] / 1e3:.1f}); the object fraction '
         f'ranged from {pct(N["sig_min"], 2)} to {pct(N["sig_max"])} (median {pct(N["sig_median"], 2)}). '
         'No synthetic data were used anywhere in the evaluation.')

    heading(doc, '2.7 Comparators and ablations', 2)
    para(doc,
         'Published unsupervised filters were re-implemented from their descriptions, and their '
         'parameters were selected by the same grouped cross-validation as those of the proposed '
         'detector (Sec. 2.8), so that no comparator is handicapped by author defaults chosen for other '
         'sensors: the background-activity filter (temporal '
         'correlation with a neighbour),{cite:delbruck2008} a nearest-neighbour filter, an event '
         'bilateral filter (weighted spatiotemporal neighbour count), a motion-compensated proxy, '
         'kNoise,{cite:khodamoradi} the double-window filter (DWF){cite:guo} and YNoise (event-density '
         'filter).{cite:feng} Two supervised references were trained with the object labels: a multilayer-'
         'perceptron filter (MLPF){cite:guo} and an event-denoising convolutional neural network '
         '(EDnCNN){cite:baldwin} '
         f'operating on {N["features"]["n_features"]}-dimensional time-surface patches of radius '
         f'{N["features"]["patch_radius"]} pixels and time constant {N["features"]["tau_us"] / 1e3:.0f} ms. '
         'They were trained only on recordings outside the test fold, with '
         f'{N["train_events_per_rec"]:,} events sampled per training recording, and are reported to '
         'locate the proposed unsupervised detector relative to what labels can buy.')
    para(doc,
         'Ablations isolate the two design choices of the proposal. A voxelised Poisson LR filter applies '
         'the same tail test to counts on a fixed time-bin x pixel grid, differing only in being frame-'
         'based. Event-driven forms of the bilateral and double-window filters use the same causal '
         'neighbourhood machinery as the proposal but replace the Poisson probability with the original '
         'heuristic score. Further event-driven alternatives (a gamma waiting-time test, a k-nearest-'
         'neighbour point-process test, a recursive rate tracker and a velocity-adaptive matched filter) '
         'are reported for completeness. A multiscale variant combines the tail probabilities of three '
         '(r, W) scales with Fisher\'s method{cite:fisher} and is used only in the operating-point '
         'analysis.')
    para(doc,
         'Two further label-free comparators test the Poisson assumption from the other side, by asking '
         'whether a pixel is overdispersed rather than whether a window is improbable. The Fano-factor '
         f'score{{cite:fano}} bins each recording into {N["fano_bins"]} equal time bins, forms the per-pixel '
         'count series, and computes the Fano factor F = Var(N) / E[N], which is 1 for a Poisson process. '
         'Pixels with F > 2 are treated as containing a source and their background rate is set to the '
         'minimum bin count of that pixel, all other pixels keep their mean count; the score of an event '
         'is then the ratio of this background rate to the total rate of its pixel, so events at '
         'overdispersed pixels receive a low noise probability. Because the Fano factor is a statistic '
         'of the whole recording, this score is not causal and is retained only as an ablation of the '
         'overdispersion idea. The physics-informed network (PI-DC-DVS) takes the same rate cube, with '
         f'{N["pidc_bins"]} time bins, and refines the Fano-derived background-rate map with a small '
         f'convolutional network trained for {N["pidc_epochs"]} epochs per recording on a loss that pulls the '
         'prediction towards the mean count at Poisson-like pixels (0 < F < 2), towards the minimum count '
         'at overdispersed pixels, and penalises spatial roughness and rates above the observed mean; '
         'no object labels enter the loss. Both run at one fixed configuration, as do the motion proxy '
         'and the four further event-driven alternatives, since none of them has a published '
         'default and their grids would be ours; the configurations are listed in the code repository.')

    heading(doc, '2.8 Evaluation protocol', 2)
    para(doc,
         'The unit of analysis is the recording. Event-level ROC-AUC against the object labels was '
         'computed per recording and summarised as mean and standard deviation (SD) over recordings; F1, '
         'noise-removal rate (NRR; fraction of background events with P_i of at least 0.5) and '
         'signal-preservation rate (SPR) are also reported. Tunable parameters of the proposed detector '
         f'(r \u2208 {{{", ".join(str(x) for x in N["grid_radii"])}}}, '
         f'W \u2208 {{{", ".join(f"{x:.0f}" for x in N["grid_windows_ms"])}}} ms, '
         f'q \u2208 {{{", ".join(str(x) for x in N["grid_quantiles"])}}}; {N["n_grid"]} combinations), '
         'of the voxelised ablation, of every published comparator and of the two event-driven ablations '
         'that share their form (time constants, spatial radii, bin counts and window lengths; grids of '
         f'{N["grid_size_min"]} to {N["grid_size_max"]} combinations, listed in the code repository) were '
         f'selected by grouped {N["n_folds"]}-fold cross-validation over recordings: the configuration '
         'with the highest mean AUC on the training folds was applied to the held-out fold, so every '
         'reported number for these methods is out of fold. The supervised networks used the same folds. '
         'The cross-validation is an evaluation device that gives every comparator its best fair '
         'configuration; in operation the proposed detector runs with one fixed configuration and sets '
         'its threshold from the false-alarm budget (Sec. 2.4). Paired '
         'differences between the proposed detector and each comparator were tested with the two-sided '
         'Wilcoxon signed-rank test over recordings,{cite:wilcoxon} with Holm adjustment for the '
         f'{len(tests)} comparisons.' + '{cite:holm} Results are also stratified by sensor model and by '
         'event rate (below or above the median). Partial AUC restricted to false-positive rates below '
         '0.01 and 0.001 is reported in the standardised form of McClish,{cite:mcclish} in which 0.5 is '
         'chance, using scikit-learn.{cite:sklearn} The random seed was fixed at ' + f'{N["seed"]}.')

    heading(doc, '2.9 Cost versus sparsity', 2)
    para(doc,
         f'To measure how cost depends on sparsity, a real EBSSA recording ({N["max_events"]:,} events) '
         'was thinned by keeping a uniformly random fraction f of its events, '
         f'f \u2208 {{{", ".join(f"{x:g}" for x in N["keep_fractions"])}}}, while leaving the sensor '
         'array and the recording interval unchanged, so the voxel grid of the frame-based methods '
         'keeps its size. Thinning a Poisson process yields a Poisson process,{cite:kingman} so the '
         'thinned streams remain realistic sparse backgrounds. Wall-clock time and peak Python-level '
         f'memory were recorded over {N["cost_repeats"]} repeats after a warm-up call, and a cost '
         'exponent \u03b2 was fitted by least squares on log-log axes:')
    add_display_equation(doc, eq_cost, 6)
    para(doc,
         'An exponent near one means cost proportional to the number of events; an exponent near zero '
         'means cost fixed by the grid. Timings were taken on a single processor core and are indicative of '
         'scaling, not of absolute throughput on dedicated hardware. The zero-knowledge form of the '
         'proposed detector (Sec. 2.5) was timed in the same way, with the rate estimate and the window '
         'choice included in its cost.')

    heading(doc, '2.10 Zero-knowledge protocol', 2)
    zks = N['zk']
    sens = list(zks['sensors'])
    para(doc,
         'The cross-validation of Sec. 2.8 still lets each method see labelled recordings from both '
         'sensor models. To emulate a new instrument, the two models were treated as source and target '
         f'({zks["sensors"][sens[0]]} and {zks["sensors"][sens[1]]} recordings) in both directions, and '
         'three questions were asked. (A) Configuration transfer: the configuration with the highest '
         'mean AUC on the source sensor was applied unchanged to the target sensor, and the AUC lost '
         'relative to the best configuration on the target itself was recorded for every method. The '
         'reference is optimistic, since it is selected with the target labels, so the loss is an upper '
         'bound on what a per-sensor tuning could recover. (B) No tuning: every method was run at one '
         'fixed configuration (the publication default of each comparator; the fixed-window and '
         'rate-adaptive forms of the proposal) on all recordings. (C) Threshold transfer: for a requested '
         f'budget \u03b1 \u2208 {{{", ".join(f"{a:g}" for a in zks["alphas"])}}} a numerical threshold '
         'was fixed on the source sensor (median over source recordings of the \u03b1-quantile of the '
         'score) and applied to the target recordings, and compared with the label-free recalibration of '
         'Eq. (5) on each target stream. Achieved false-positive and detection rates are medians over '
         'target recordings. With only two sensor models the transfer directions are two case studies, '
         'not a sample of sensors, and are interpreted as such.')

    heading(doc, '2.11 Parameter hand-over and two-stage front end', 2)
    hyb = N['hyb']
    para(doc,
         'The zero-knowledge form of the proposal produces two label-free by-products that a second '
         'filter can use: the per-pixel rate map and the rate-adaptive window W of Sec. 2.5. We tested '
         'whether they can replace the tuning that the strongest comparator, YNoise, otherwise needs. '
         'YNoise counts the neighbours of an event that fired within a time constant dt; its published '
         f'default is dt = {hyb["ynoise_default"]["dt_us"] / 1e3:g} ms with radius '
         f'{hyb["ynoise_default"]["radius"]}, and the cross-validation of Sec. 2.8 selected it from a grid '
         'with labels. In the hand-over, dt was set equal to W and the radius to that of the proposal '
         f'(r = {hyb["stage1_config"]["radius"]}), per recording and without labels, and YNoise was run on the '
         'whole stream. In the two-stage form, the proposal first passed the fraction f ∈ '
         f'{{{", ".join(f"{x:g}" for x in hyb["screen_fractions"])}}} of the stream with the smallest noise '
         'probability (a quantile of the stream, Eq. (5); when the strict quantile would pass fewer than '
         'half the requested number because it falls inside a block of tied probabilities, the whole '
         'block is passed and the achieved fraction is reported); YNoise was then run on the passed events '
         'alone, so that its recent-event map and its cost saw only that fraction, with three parameter '
         'sources: the hand-over (dt = W), the published default, and a pseudo-label choice in which the '
         'grid point of Sec. 2.8 that best separated the passed from the rejected events was taken '
         '(labels were not used; the screening decision served as the target). The two-stage score '
         'ranks rejected events below passed ones, the former by the stage-1 probability and the latter '
         'by the stage-2 density, and its operating point is again a quantile of the stream. We report '
         'the AUC of every variant, the achieved false-positive and detection rates at the budgets of '
         'Sec. 2.4, and the wall-clock time of each stage relative to YNoise on the whole stream (for the '
         'pseudo-label variant the grid search is charged to stage 2), on all recordings, with Wilcoxon signed-rank tests on paired per-recording AUC. For a visual check '
         'of what each stage passes, one recording per sensor model was selected by a fixed rule, the '
         'one whose stage-1 AUC lies closest to the median of that sensor, and the events that each '
         'method passed at a requested false-alarm rate of '
         f'{N["qual"]["alpha"]:g} (its own quantile, Eq. (5), no labels, with the same tied-block rule as '
         'the screening stage) were accumulated into a per-pixel count image over the whole analysed '
         'interval.')

    # ---------------- 3 Results ----------------
    heading(doc, '3 Results')
    heading(doc, '3.1 Detection performance', 2)
    edlr_vs = tests
    pub = ['bilateral', 'ynoise', 'motion', 'temporal', 'dwf', 'nearest', 'knoise']
    ns_pub = [k for k in pub if tests[k]['p_holm'] >= 0.05]
    better_pub = [k for k in pub if tests[k]['p_holm'] < 0.05 and tests[k]['auc_diff_mean'] < 0]
    worse_pub = [k for k in pub if tests[k]['p_holm'] < 0.05 and tests[k]['auc_diff_mean'] > 0]

    def cmp_list(keys):
        return ', '.join(
            f'{short(k)} {auc(N, k)} ({edlr_vs[k]["auc_diff_mean"]:+.3f}, p {pfmt(edlr_vs[k]["p_holm"])})'
            for k in keys)

    para(doc,
         f'Table 1 lists the mean AUC of every method over the {N["n_rec"]} recordings, and Fig. 2 shows '
         f'the same values with their spread. The proposed detector reached an AUC of {auc_sd(N, "edlr")}. '
         'With every published filter tuned by the same grouped cross-validation, '
         f'{len(ns_pub)} of the {len(pub)} were statistically indistinguishable from it '
         f'(mean paired difference of the proposal minus the comparator and Holm-adjusted p: {cmp_list(ns_pub)}), '
         f'{len(better_pub)} {"was" if len(better_pub) == 1 else "were"} significantly higher '
         f'({cmp_list(better_pub)}) and {len(worse_pub)} were significantly lower ({cmp_list(worse_pub)}). '
         f'The supervised networks were higher still: MLPF {auc(N, "mlpf")} and EDnCNN-style '
         f'{auc(N, "edncnn")} (differences {edlr_vs["mlpf"]["auc_diff_mean"]:+.3f} and '
         f'{edlr_vs["edncnn"]["auc_diff_mean"]:+.3f}; Holm-adjusted p {pfmt(edlr_vs["mlpf"]["p_holm"])} '
         f'and p {pfmt(edlr_vs["edncnn"]["p_holm"])}). The proposed detector is therefore in the group of '
         'the stronger unsupervised filters rather than above it: when the comparators are given labels '
         'to tune their time constants and radii, the best of them, the density filter YNoise, exceeds '
         'it, and the label-trained networks exceed both. The comparison in this section gives every '
         'comparator a label-tuned configuration that the proposed detector does not need (Sec. 3.5).')

    def row(k):
        mm = m[k]
        t = tests.get(k)
        return [label(k),
                'yes' if mm['supervised'] else 'no',
                auc_sd(N, k),
                fmt(mm['f1']['mean']),
                fmt(mm['nrr']['mean']),
                fmt(mm['spr']['mean']),
                '-' if t is None else f"{t['auc_diff_mean']:+.3f}",
                '-' if t is None else (f"{t['p_holm']:.3f}" if t['p_holm'] >= 0.001 else '<0.001')]

    order = ['edlr',
             'bilateral', 'ynoise', 'motion', 'temporal', 'dwf', 'nearest', 'knoise',
             'bilateral_ed', 'knn_pp', 'waiting_lr', 'flow_matched', 'dwf_ed', 'recursive',
             'plr', 'pi_dc_dvs', 'fano',
             'edncnn', 'mlpf']
    order.insert(1, 'edlr_adaptive')
    table(doc,
          f'Table 1 Event-level detection performance on the {N["n_rec"]} labelled EBSSA recordings '
          '(mean over recordings; AUC also with standard deviation). \u0394AUC is the mean paired '
          'difference of the proposed detector minus the comparator; p is the Holm-adjusted two-sided '
          'Wilcoxon signed-rank p-value. NRR: noise-removal rate; SPR: signal-preservation rate at a '
          'probability threshold of 0.5. Rows are grouped as proposed (fixed window; rate-adaptive window of Sec. 2.5), published unsupervised filters, '
          'event-driven ablations, frame-based and label-free learned ablations, and supervised references.',
          ['Method', 'Labels', 'AUC (mean ± SD)', 'F1', 'NRR', 'SPR', '\u0394AUC', 'p (Holm)'],
          [row(k) for k in order],
          col_widths=[2.2, 0.5, 1.05, 0.5, 0.5, 0.5, 0.6, 0.65], font_pt=8)
    figure(doc, 'fig2_methods_auc.png',
           f'Fig. 2 Mean ROC-AUC with standard deviation over the {N["n_rec"]} EBSSA recordings for all '
           'methods in Table 1. The dashed line marks the proposed detector; the dotted line marks chance. '
           'Colours group the proposed method (fixed and rate-adaptive window), published unsupervised event filters, event-driven '
           'ablations, frame-based and label-free learned ablations, and label-trained supervised networks.',
           width=Inches(6.0))
    para(doc,
         'Figure 3 shows the paired per-recording comparison. The proposed detector was above the tuned '
         f'event bilateral filter on {N["n_unsup_edlr_better"]} of {N["n_rec"]} recordings, above tuned '
         f'YNoise on {N["n_edlr_better_ynoise"]}, and above its own voxelised form on {N["n_edlr_better_plr"]}. '
         'Recording-to-recording variation was large for every method (Table 1), and the two sensor '
         'models behaved differently (Sec. 3.3), which is why the recording rather than the event was '
         'used as the statistical unit.')
    figure(doc, 'fig3_paired.png',
           'Fig. 3 Per-recording ROC-AUC of the proposed detector against (a) the event bilateral filter, '
           '(b) YNoise, (c) its voxelised, frame-based form and (d) the supervised MLPF. Circles: '
           '180 x 240-pixel sensor; squares: 240 x 304-pixel sensor. Points above the diagonal favour the '
           'proposed detector. \u0394AUC and Holm-adjusted p refer to the paired Wilcoxon test over '
           f'{N["n_rec"]} recordings.', width=Inches(4.8))

    heading(doc, '3.2 Ablations', 2)
    para(doc,
         f'Replacing the event-driven neighbourhood with a voxel grid lowered AUC from {auc(N, "edlr")} '
         f'to {auc(N, "plr")} (difference {tests["plr"]["auc_diff_mean"]:+.3f}, Holm-adjusted p = '
         f'{tests["plr"]["p_holm"]:.4f}), because the fixed time bins split object tracks across bin '
         'boundaries and because the cross-validated bin count is a compromise across recordings with '
         'very different event rates. Replacing the Poisson probability with a heuristic score while '
         'keeping the event-driven neighbourhood made no significant difference for the bilateral weight '
         f'({auc(N, "bilateral_ed")}, p = {tests["bilateral_ed"]["p_holm"]:.2f}) but degraded the '
         f'double-window rule ({auc(N, "dwf_ed")}). The other event-driven alternatives were '
         f'significantly worse than the proposed detector ({auc(N, "knn_pp")} to {auc(N, "recursive")}). '
         f'The Fano-factor score reached {auc(N, "fano")}, close to chance, and the '
         f'physics-informed network {auc(N, "pi_dc_dvs")} at about '
         f'{N["runtime"]["pi_dc_dvs"]["mean"] / N["runtime"]["edlr"]["mean"]:.0f} times the runtime. The '
         'probability output therefore matters mainly for calibration (Sec. 3.5); the event-driven '
         f'neighbourhood matters for both accuracy and cost. The rate-adaptive window (Sec. 2.5), '
         f'cross-validated over k, reached {auc(N, "edlr_adaptive")} (difference from the fixed window '
         f'{tests["edlr_adaptive"]["auc_diff_mean"]:+.3f}, Holm-adjusted p {pfmt(tests["edlr_adaptive"]["p_holm"])}).')

    heading(doc, '3.3 Stratified results', 2)
    st = N['strata']
    strata_rows = []
    for key, name in (('sensor_180x240', '180 x 240-pixel sensor'),
                      ('sensor_240x304', '240 x 304-pixel sensor'),
                      ('activity_low', 'Event rate below median'),
                      ('activity_high', 'Event rate above median')):
        s = st[key]
        strata_rows.append([name, str(s['n_recordings'])] +
                           [fmt(s[k]['mean']) for k in ('edlr', 'bilateral', 'ynoise', 'plr', 'mlpf', 'edncnn')])
    def swing(k):
        return abs(st['activity_high'][k]['mean'] - st['activity_low'][k]['mean'])

    def gap(stratum, k):
        return st[stratum]['edlr']['mean'] - st[stratum][k]['mean']

    n_strata = sorted(st[k]['n_recordings'] for k in ('sensor_180x240', 'sensor_240x304',
                                                        'activity_low', 'activity_high'))
    para(doc,
         'Table 2 stratifies the main comparison. On the 180 x 240-pixel sensor the proposed detector '
         f'({fmt(st["sensor_180x240"]["edlr"]["mean"])}) was {gap("sensor_180x240", "bilateral"):+.3f} '
         f'from the event bilateral filter and {gap("sensor_180x240", "ynoise"):+.3f} from YNoise; on the '
         f'240 x 304-pixel sensor ({fmt(st["sensor_240x304"]["edlr"]["mean"])}) the gaps were '
         f'{gap("sensor_240x304", "bilateral"):+.3f} and {gap("sensor_240x304", "ynoise"):+.3f}. Across '
         f'the event-rate split the proposed detector moved by {swing("edlr"):.3f} AUC '
         f'({fmt(st["activity_low"]["edlr"]["mean"])} to {fmt(st["activity_high"]["edlr"]["mean"])}), the '
         f'bilateral filter by {swing("bilateral"):.3f} and YNoise by {swing("ynoise"):.3f}. These strata are '
         f'exploratory: they contain {n_strata[0]} to {n_strata[-1]} recordings each and were not '
         'pre-specified.')
    table(doc,
          'Table 2 Mean ROC-AUC by sensor model and by event-rate stratum (split at the median rate of '
          f'{st["activity_low"]["median_event_rate_hz"] / 1e3:.1f} k events/s). Supervised networks are '
          'shown for reference.',
          ['Stratum', 'n', 'Proposed', 'Bilateral', 'YNoise', 'Voxelised LR', 'MLPF', 'EDnCNN-style'],
          strata_rows, col_widths=[1.9, 0.4, 0.8, 0.8, 0.8, 0.9, 0.7, 0.9], font_pt=8)

    heading(doc, '3.4 Cost versus sparsity', 2)
    cs = N['cost']['scaling']
    cost_rows = []
    for k in ('edlr', 'edlr_zk', 'bilateral_ed', 'ynoise', 'recursive', 'plr', 'bilateral', 'temporal'):
        s = cs[k]
        cost_rows.append([('Event-driven Poisson LR filter, zero-knowledge form' if k == 'edlr_zk'
                           else label(k).replace(' (proposed)', '').replace(' (frame-based ablation)', '')),
                          s['family'],
                          f"{s['cost_exponent_mean']:.2f} ± {s['cost_exponent_std']:.2f}",
                          f"{s['seconds_dense'] * 1e3:.1f}", f"{s['seconds_sparse'] * 1e3:.2f}",
                          f"{s['ns_per_event_dense']:.0f}", f"{s['ns_per_event_sparse']:.0f}",
                          f"{s['peak_bytes_dense'] / 1e6:.1f}", f"{s['peak_bytes_sparse'] / 1e6:.2f}"])
    para(doc,
         'Figure 4 and Table 3 show the second claim. The wall-clock time of the proposed detector fell '
         f'with the number of events with an exponent of {cs["edlr"]["cost_exponent_mean"]:.2f}: '
         f'from {cs["edlr"]["seconds_dense"] * 1e3:.0f} ms on the full stream to '
         f'{cs["edlr"]["seconds_sparse"] * 1e3:.1f} ms at one percent of the events, with peak memory '
         f'falling from {cs["edlr"]["peak_bytes_dense"] / 1e6:.1f} to '
         f'{cs["edlr"]["peak_bytes_sparse"] / 1e6:.1f} MB. The voxelised form of the same test had an '
         f'exponent of {cs["plr"]["cost_exponent_mean"]:.2f}: its time stayed at about '
         f'{cs["plr"]["seconds_dense"]:.1f} s and its memory at {cs["plr"]["peak_bytes_dense"] / 1e6:.0f} MB '
         'regardless of sparsity, so that at one percent of the events it was '
         f'{cs["plr"]["seconds_sparse"] / cs["edlr"]["seconds_sparse"]:.0f} times slower and spent '
         f'{cs["plr"]["ns_per_event_sparse"] / 1e3:.0f} \u03bcs per event against '
         f'{cs["edlr"]["ns_per_event_sparse"] / 1e3:.2f} \u03bcs. The frame-based bilateral and '
         f'background-activity filters showed the same plateau (exponents {cs["bilateral"]["cost_exponent_mean"]:.2f} '
         f'and {cs["temporal"]["cost_exponent_mean"]:.2f}), while the event-driven comparators scaled '
         f'like the proposal (YNoise {cs["ynoise"]["cost_exponent_mean"]:.2f}, event-driven bilateral '
         f'{cs["bilateral_ed"]["cost_exponent_mean"]:.2f}). The per-event cost of the proposed detector '
         f'rose from {cs["edlr"]["ns_per_event_dense"]:.0f} to {cs["edlr"]["ns_per_event_sparse"]:.0f} ns '
         'at the sparsest setting because the per-pixel rate map is still computed over the full array; '
         'this fixed term is small compared with the grid cost of the frame-based methods but explains '
         f'the exponent below one. The zero-knowledge form, which additionally estimates the rate map '
         f'and chooses its own window, cost {cs["edlr_zk"]["ns_per_event_dense"]:.0f} ns per event on the '
         f'full stream and {cs["edlr_zk"]["seconds_sparse"] * 1e3:.1f} ms at one percent of the events '
         f'(exponent {cs["edlr_zk"]["cost_exponent_mean"]:.2f}): the window choice is a median over the '
         'events and adds no term that grows with the array.')
    figure(doc, 'fig4_sparsity_cost.png',
           'Fig. 4 Computational cost as the real EBSSA stream is thinned. (a) Wall-clock time per '
           f'recording and (b) time per event, medians over {N["cost_repeats"]} repeats, against the '
           'fraction of events kept (the array and the recording interval are unchanged). Solid lines: '
           'event-driven methods; dashed lines: frame- or voxel-based methods. Legend entries give the '
           'fitted cost exponent \u03b2 of Eq. (6).')
    table(doc,
          'Table 3 Cost-scaling summary from Fig. 4. Exponent: mean ± SD of \u03b2 over repeats. Dense: '
          'all events; sparse: one percent of events. Memory is the peak Python-level allocation.',
          ['Method', 'Family', 'Exponent \u03b2', 'Time dense (ms)', 'Time sparse (ms)',
           'ns/event dense', 'ns/event sparse', 'Memory dense (MB)', 'Memory sparse (MB)'],
          cost_rows, col_widths=[1.5, 0.75, 0.8, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6], font_pt=7)

    heading(doc, '3.5 Self-calibrated operating points', 2)
    ops_rows = []
    for k in ('edlr', 'multiscale', 'ynoise', 'bilateral_ed'):
        r_ = [label(k) if k in m else 'Multiscale Fisher combination (proposed, 3 scales)']
        for a in N['alphas']:
            v = sc[k][f'{a:g}']
            r_ += [f"{v['fpr_median']:.4f}", f"{v['tpr_median']:.3f}"]
        ops_rows.append(r_)
    def budget_phrase(k):
        parts = []
        for a in N['alphas']:
            f = sc[k][f'{a:g}']['fpr_median']
            parts.append(f'{f:.4f}' + ('' if f <= a else f' ({(f / a - 1) * 100:.0f}% over)'))
        return ', '.join(parts)

    para(doc,
         'Figure 5 and Table 4 report the label-free operating points of Eq. (5), with every comparator '
         'at its cross-validated configuration. For requested false-alarm budgets of '
         f'{", ".join(f"{a:g}" for a in N["alphas"])} the proposed detector achieved median false-positive '
         f'rates of {budget_phrase("edlr")} on the true background events, i.e. it stayed within budget at '
         f'every level, as did the multiscale variant ({budget_phrase("multiscale")}). YNoise, whose score '
         f'is an integer density, gave {budget_phrase("ynoise")}: the quantile falls inside a tie and every '
         'tied event is passed, so the budget is met or exceeded depending on where the ties fall. The '
         f'event-driven bilateral score gave {budget_phrase("bilateral_ed")} but detected '
         f'{sc["bilateral_ed"]["0.01"]["tpr_median"]:.3f} of the object events at a requested 0.01. '
         'For completeness the detection rates at the same nominal budgets and the standardised partial '
         'AUC in the two low-false-positive regions (false-positive rate below 0.01 and below 0.001) are '
         f'also reported: at a requested 0.01 the median detection rate was {sc["edlr"]["0.01"]["tpr_median"]:.3f} '
         f'for the proposed detector and {sc["ynoise"]["0.01"]["tpr_median"]:.3f} for YNoise, and the '
         f'partial AUC below 0.01 was {pa["edlr"]["p01"]:.3f} for the proposed detector, '
         f'{pa["ynoise"]["p01"]:.3f} for YNoise and {pa["bilateral"]["p01"]:.3f}, {pa["knoise"]["p01"]:.3f} '
         f'and {pa["dwf"]["p01"]:.3f} for the frame-based bilateral filter, kNoise and DWF (below 0.001: '
         f'{pa["edlr"]["p001"]:.3f} against {pa["ynoise"]["p001"]:.3f}). Where YNoise is tuned on labels '
         'it detects more object events at these budgets. The point of this section is different: with '
         'one fixed configuration and no labels, the proposed detector met the requested budget at every '
         'level, because its score is a continuous probability without the ties of a density count, and '
         'it retained a non-zero detection rate at the tightest budget where the calibrated bilateral '
         'score detected nothing. An operator with no labelled stream, no catalogue of the field and no '
         'experience of the sensor can therefore state the false-alarm rate in advance and obtain it, '
         'together with a usable detection rate.')
    figure(doc, 'fig5_calibration.png',
           'Fig. 5 Self-calibrated operating points and low-false-positive performance. (a) Median achieved '
           'false-positive rate on background events against the requested budget \u03b1 when each score is '
           'cut at its own \u03b1-quantile (Eq. (5)); the dotted line is ideal calibration. (b) Median '
           'detection rate of object events at the same budgets. (c) Standardised partial AUC below '
           'false-positive rates of 0.01 and 0.001 (0.5 is chance).')
    table(doc,
          'Table 4 Median achieved false-positive rate (FPR) and detection rate (TPR) over recordings '
          'when each detector is cut at the \u03b1-quantile of its own score, without labels.',
          ['Method'] + [f'FPR (\u03b1 = {a:g})' if i % 2 == 0 else f'TPR (\u03b1 = {a:g})'
                        for a in N['alphas'] for i in (0, 1)],
          ops_rows, col_widths=[2.0] + [0.7] * 6, font_pt=8)

    heading(doc, '3.6 Zero-knowledge deployment', 2)
    tr = zks['cross_sensor_transfer']
    dfl = zks['published_defaults']
    tt = zks['threshold_transfer']
    dirs = list(tr)
    d01, d10 = dirs

    def dname(d):
        return d.replace('x', ' x ').replace('->', ' to ')

    zk_rows = []
    for k, nm in (('edlr', 'Proposed, fixed window'), ('edlr_adaptive', 'Proposed, rate-adaptive window'),
                  ('ynoise', 'YNoise'), ('bilateral', 'Event bilateral (frame-based)'),
                  ('bilateral_ed', 'Event bilateral (event-driven)'), ('dwf', 'Double-window filter'),
                  ('knoise', 'kNoise'), ('temporal', 'Background-activity filter'),
                  ('plr', 'Voxelised Poisson LR')):
        zk_rows.append([nm, fmt(dfl[k]['auc_mean'])]
                       + [fmt(tr[d][k]['auc_transferred']) + f" ({-tr[d][k]['drop']:+.3f})" for d in dirs])
    sat = zks['saturation']
    def sat_at(key, cfg, sensor):
        return sat[key][json.dumps(cfg, sort_keys=True)][sensor]['frac_saturated_median']
    fixed_cfg = dfl['edlr']['config']
    zc = N['zk_config']
    # AUC and saturation along k at the deployment (r, q), per sensor
    k_curve = {}
    for c, v in sat['edlr_adaptive'].items():
        cfg = json.loads(c)
        if cfg['radius'] == zc['radius'] and cfg['interval_quantile'] == zc['interval_quantile']:
            k_curve[cfg['expected_count']] = v
    ks = sorted(k_curve)
    k_best = {s: max(ks, key=lambda k: k_curve[k][s]['auc_mean']) for s in sens}
    k_next = ks[ks.index(zc['expected_count']) + 1]
    # the transferred fixed-window configuration on the denser sensor, and its saturation there
    dense = sens[1]
    para(doc,
         'Table 5 and Fig. 6 report the zero-knowledge protocol of Sec. 2.10. Without any tuning (protocol B), '
         f'the fixed-window proposal reached {fmt(dfl["edlr"]["auc_mean"])} and the rate-adaptive form '
         f'{fmt(dfl["edlr_adaptive"]["auc_mean"])}, against {fmt(dfl["ynoise"]["auc_mean"])} for YNoise at '
         f'its published defaults and {fmt(dfl["bilateral"]["auc_mean"])} for the bilateral filter; the '
         f'advantage of YNoise in Table 1 ({auc(N, "ynoise")}) therefore rests on its labelled tuning and '
         'was not present at its published defaults on these recordings. Configuration transfer (protocol A) '
         'exposed the weakness of a window stated in milliseconds: the configuration selected on the '
         f'{dname(d01).split(" to ")[0]}-pixel sensor (W = {tr[d01]["edlr"]["config_from_source"]["window_us"] / 1e3:.0f} ms) '
         f'lost {tr[d01]["edlr"]["drop"]:.3f} AUC on the other sensor, and the reverse transfer lost '
         f'{tr[d10]["edlr"]["drop"]:.3f}, because the denser sensor drives the tail probability into '
         f'saturation (median fraction of scores equal to one {pct(sat_at("edlr", fixed_cfg, dense), 0)} '
         f'for the fixed deployment configuration on the {dense.replace("x", " x ")}-pixel sensor, against '
         f'{pct(sat_at("edlr", fixed_cfg, sens[0]), 1)} on the other). The rate-adaptive window lost '
         f'{tr[d01]["edlr_adaptive"]["drop"]:.3f} and {tr[d10]["edlr_adaptive"]["drop"]:.3f} (source-selected '
         f'k = {tr[d01]["edlr_adaptive"]["config_from_source"]["expected_count"]:g} and '
         f'{tr[d10]["edlr_adaptive"]["config_from_source"]["expected_count"]:g}): stated as a count, the '
         'window carried over to a sensor with a different background rate. The density and bilateral filters, whose parameters are also spatial and '
         f'temporal extents, transferred with losses of {tr[d01]["ynoise"]["drop"]:.3f} to '
         f'{tr[d10]["bilateral_ed"]["drop"]:.3f}. Fig. 6(a) shows the trade-off behind the choice of k: '
         f'mean AUC peaked at k = {k_best[sens[0]]:g} on the {sens[0].replace("x", " x ")}-pixel sensor and '
         f'at k = {k_best[sens[1]]:g} on the {sens[1].replace("x", " x ")}-pixel sensor, while the median '
         f'saturated fraction on the denser sensor was {pct(sat_at("edlr_adaptive", zc, dense), 0)} at '
         f'k = {zc["expected_count"]:g} and {pct(k_curve[k_next][dense]["frac_saturated_median"], 0)} at '
         f'k = {k_next:g}, so k = {zc["expected_count"]:g} is the longest window that keeps the '
         'probability scale usable on both sensors.')
    table(doc,
          f'Table 5 Zero-knowledge evaluation. No tuning: mean AUC over all {N["n_rec"]} recordings at one '
          'fixed configuration (publication defaults for the comparators). Transfer: mean AUC on the target '
          'sensor with the configuration selected on the source sensor, and in parentheses the AUC lost '
          'relative to the best configuration on the target itself (an optimistic, label-selected '
          'reference; negative values are losses).',
          ['Method', 'No tuning (all recordings)'] + [f'Transfer {dname(d)}' for d in dirs],
          zk_rows, col_widths=[2.3, 1.3, 1.5, 1.5], font_pt=8)
    a_mid = f'{zks["alphas"][1]:g}'
    para(doc,
         'Threshold transfer (protocol C, Fig. 6(c)) shows why the operating point must be recalibrated on '
         'the target stream rather than carried over as a number. A threshold fixed on the '
         f'{dname(d10).split(" to ")[0]}-pixel sensor for a requested {a_mid} and applied to the other '
         f'gave a median false-positive rate of {tt[d10]["edlr"][a_mid]["transferred"]["fpr_median"]:.3f} '
         f'for the fixed-window proposal, {tt[d10]["edlr_adaptive"][a_mid]["transferred"]["fpr_median"]:.3f} '
         f'for the rate-adaptive form and {tt[d10]["ynoise"][a_mid]["transferred"]["fpr_median"]:.4f} for '
         'YNoise; in the opposite direction the transferred thresholds passed almost nothing '
         f'({tt[d01]["edlr_adaptive"][a_mid]["transferred"]["fpr_median"]:.4f} and '
         f'{tt[d01]["ynoise"][a_mid]["transferred"]["fpr_median"]:.4f}). Recalibrating on the target stream '
         'with Eq. (5), which uses no labels, brought the rate-adaptive proposal to '
         f'{tt[d01]["edlr_adaptive"][a_mid]["self_calibrated_on_target"]["fpr_median"]:.4f} and '
         f'{tt[d10]["edlr_adaptive"][a_mid]["self_calibrated_on_target"]["fpr_median"]:.4f} in the two '
         f'directions at a requested {a_mid}, with detection rates of '
         f'{tt[d01]["edlr_adaptive"][a_mid]["self_calibrated_on_target"]["tpr_median"]:.3f} and '
         f'{tt[d10]["edlr_adaptive"][a_mid]["self_calibrated_on_target"]["tpr_median"]:.3f}; the same '
         f'recalibration applied to YNoise gave {tt[d01]["ynoise"][a_mid]["self_calibrated_on_target"]["fpr_median"]:.4f} '
         f'and {tt[d10]["ynoise"][a_mid]["self_calibrated_on_target"]["fpr_median"]:.4f}, the excess again '
         'coming from ties in the integer density. Taken together, the three protocols separate what is '
         'and is not knowledge-free in the proposal: the threshold is, on the target stream; the temporal '
         'window is, once it is stated as an expected count rather than a time; the radius and the '
         'constant k are choices made once on this dataset whose transfer to a third sensor is untested.')
    figure(doc, 'fig6_zero_knowledge.png',
           'Fig. 6 Zero-knowledge deployment. (a) Mean AUC (red) and median fraction of saturated scores '
           '(grey) of the rate-adaptive proposal against the dimensionless window k on the two sensor '
           'models (circles, solid: 180 x 240 px; squares, dashed: 240 x 304 px); the dotted line marks the '
           'deployment value. (b) AUC lost when the configuration selected on one sensor is applied to the '
           'other, for both directions. (c) Median achieved false-positive rate on the target sensor when a '
           'numerical threshold fixed on the other sensor is carried over (dashed) and when the threshold is '
           'recalibrated on the target stream without labels (solid), for the rate-adaptive proposal and '
           'YNoise; the dotted line is ideal calibration.')

    # ---------------- 3.7 hand-over and two-stage ----------------
    heading(doc, '3.7 Parameter hand-over and two-stage front end', 2)
    hy = N['hyb']
    yf = hy['ynoise_full']
    ps = hy['per_sensor']
    scr = hy['screen']
    f_keys = list(scr)
    f_mid = f_keys[2]
    f_hi = f_keys[-1]
    ps_names = list(ps)
    para(doc,
         'Table 6 and Fig. 7 report the hand-over protocol of Sec. 2.11. Setting the YNoise time '
         'constant to the rate-adaptive window of the proposal, per recording and without labels, raised '
         f'its AUC over all {hy["n_recordings"]} recordings from {fmt(yf["default"]["auc"]["mean"])} at the '
         f'published default to {fmt(yf["handoff"]["auc"]["mean"])} (Wilcoxon p = '
         f'{yf["handoff"]["vs_default"]["p_raw"]:.1e}), within {abs(hy["ynoise_cv_reference"]["auc_mean"] - yf["handoff"]["auc"]["mean"]):.3f} of the '
         f'{fmt(hy["ynoise_cv_reference"]["auc_mean"])} that label-tuned cross-validation reached in Table 1, '
         f'and above the zero-knowledge proposal itself ({fmt(hy["stage1"]["auc"]["mean"])}, p = '
         f'{yf["handoff"]["vs_stage1"]["p_raw"]:.1e}). The gain held on both sensor models '
         f'({fmt(ps[ps_names[0]]["ynoise_default_auc"]["mean"])} to {fmt(ps[ps_names[0]]["ynoise_handoff_auc"]["mean"])} on the '
         f'{dname(ps_names[0])}-pixel sensor, {fmt(ps[ps_names[1]]["ynoise_default_auc"]["mean"])} to '
         f'{fmt(ps[ps_names[1]]["ynoise_handoff_auc"]["mean"])} on the {dname(ps_names[1])}-pixel sensor), where the '
         f'handed-over windows had medians of {ps[ps_names[0]]["window_ms"]["median"]:.0f} and '
         f'{ps[ps_names[1]]["window_ms"]["median"]:.0f} ms. On these recordings, then, the tuning that '
         'YNoise needed to reach its Table 1 value did not have to come from labels: a time constant read '
         'from the background rate of the target stream closed most of the gap.')
    hyb_rows = [['Proposed, zero-knowledge form (stage 1 alone)', fmt(hy['stage1']['auc']['mean']),
                 fmt(ps[ps_names[0]]['stage1_auc']['mean']), fmt(ps[ps_names[1]]['stage1_auc']['mean']),
                 'n/a', 'n/a'],
                ['YNoise, published default', fmt(yf['default']['auc']['mean']),
                 fmt(ps[ps_names[0]]['ynoise_default_auc']['mean']),
                 fmt(ps[ps_names[1]]['ynoise_default_auc']['mean']), 'n/a', '1.00'],
                ['YNoise, dt = W handed over', fmt(yf['handoff']['auc']['mean']),
                 fmt(ps[ps_names[0]]['ynoise_handoff_auc']['mean']),
                 fmt(ps[ps_names[1]]['ynoise_handoff_auc']['mean']), 'n/a', '1.00'],
                ['YNoise, label-tuned CV (Table 1)', fmt(hy['ynoise_cv_reference']['auc_mean']),
                 'n/a', 'n/a', 'n/a', '1.00']]
    for fk in f_keys:
        e = scr[fk]
        hyb_rows.append([f'Two-stage, f = {fk}, stage 2 dt = W', fmt(e['handoff']['auc']['mean']),
                         'n/a', 'n/a', f"{e['signal_recall']['mean']:.2f}",
                         f"{e['handoff']['stage2_time_over_ynoise_full']['median']:.2f}"])
    table(doc,
          f'Table 6 Parameter hand-over and two-stage front end on all {hy["n_recordings"]} recordings. AUC: '
          'mean over recordings (pooled and per sensor model). Recall: fraction of object events passed '
          'by stage 1. Stage-2 time: median wall-clock time of YNoise on the passed events relative to '
          'YNoise on the whole stream. No labels are used by any row except the label-tuned reference.',
          ['Configuration', 'AUC, all', f'AUC, {dname(ps_names[0])}', f'AUC, {dname(ps_names[1])}',
           'Recall of stage 1', 'Stage-2 time'],
          hyb_rows, col_widths=[2.6, 0.8, 0.9, 0.9, 0.9, 0.8], font_pt=8)
    a_mid = f'{hy["alphas"][1]:g}'
    para(doc,
         'The two-stage form (Fig. 7(b)) improved on stage 1 alone by a smaller margin that grew with the '
         f'passed fraction: from {fmt(hy["stage1"]["auc"]["mean"])} to '
         f'{fmt(scr[f_mid]["handoff"]["auc"]["mean"])} at f = {f_mid} (p = '
         f'{scr[f_mid]["handoff"]["vs_stage1"]["p_raw"]:.1e}) and {fmt(scr[f_hi]["handoff"]["auc"]["mean"])} at '
         f'f = {f_hi} (p = {scr[f_hi]["handoff"]["vs_stage1"]["p_raw"]:.1e}), because the score can only '
         'reorder the events that stage 1 passed. With the published default in stage 2 the two-stage '
         f'score did not improve on stage 1 (p = {scr[f_mid]["default"]["vs_stage1"]["p_raw"]:.2f} at f = '
         f'{f_mid}), and the pseudo-label choice tracked the hand-over '
         f'({fmt(scr[f_mid]["pseudo"]["auc"]["mean"])} at f = {f_mid}), so the improvement comes from the '
         'handed-over time constant rather than from the second density count as such. Stage 1 passed '
         f'{scr[f_mid]["signal_recall"]["mean"]:.2f} of the object events at f = {f_mid} and '
         f'{scr[f_hi]["signal_recall"]["mean"]:.2f} at f = {f_hi}, which bounds the detection rate of the '
         'cascade. Its quantile operating point remained calibrated: at a requested '
         f'{a_mid} the two-stage score gave a median false-positive rate of '
         f'{scr[f_mid]["handoff"]["operating"][a_mid]["fpr"]["median"]:.4f} (f = {f_mid}), below the budget '
         'because of ties in the integer density. The stage-2 workload fell in proportion to f '
         f'({scr[f_mid]["handoff"]["stage2_time_over_ynoise_full"]["median"]:.2f} of the full-stream YNoise '
         f'time at f = {f_mid}, with the hand-over; the pseudo-label choice, which must run the whole '
         f'YNoise grid on the passed events, cost {scr[f_mid]["pseudo"]["stage2_time_over_ynoise_full"]["median"]:.0f} '
         'times the full-stream time from a single timing and is not a practical alternative); in this implementation, '
         'however, stage 1 itself '
         f'({hy["stage1"]["time_s"]["median"] * 1e3:.0f} ms per recording, median) costs more than YNoise on '
         f'the whole stream ({yf["default"]["time_s"]["median"] * 1e3:.0f} ms), so the cascade does not lower '
         'the total cost when the second stage is as cheap as YNoise. The cost argument for a cascade '
         'applies when the confirmation stage is expensive, such as a network, or when the screened '
         'stream must leave the sensor; the argument from the data is that the proposal supplies, without '
         'labels, the parameter that the confirmation stage otherwise needs labels to find.')
    figure(doc, 'fig7_hybrid.png',
           'Fig. 7 Parameter hand-over and two-stage front end. (a) Mean AUC of the zero-knowledge '
           'proposal, of YNoise at its published default and of YNoise with its time constant set to the '
           'rate-adaptive window W handed over from the proposal, pooled and per sensor model; the dotted '
           'line is YNoise with label-tuned cross-validation (Table 1). (b) Mean AUC of the two-stage score '
           'against the fraction of the stream passed by stage 1, with stage 2 parameterised by the '
           'hand-over, by the published default and by a pseudo-label choice; the dashed line is stage 1 '
           'alone and the black squares (right axis) the median stage-2 time relative to YNoise on the '
           'whole stream.')
    qual = N['qual']
    qr = qual['recordings']
    qk = list(qr)

    def q(key, name, k):
        return qr[key]['methods'][name][k]

    s0, s1 = (k.replace('x', ' \u00d7 ') for k in qk)
    alpha = qual['alpha']
    fpr_main = {(k, m): q(k, m, 'fpr') for k in qk
                for m in ('proposal_zk', 'ynoise_default', 'ynoise_handoff', 'two_stage')}
    fpr_max = max(fpr_main.values())
    sensor_name = {qk[0]: s0, qk[1]: s1}
    over = [f'{sensor_name[k]}, {m.replace("_", " ")}: {v:.3f}'
            for (k, m), v in fpr_main.items() if v > alpha]
    fano_fpr = [q(k, 'fano', 'fpr') for k in qk]
    budget_sentence = (
        f'The achieved false-positive rates of the four event-window scores lay between '
        f'{min(fpr_main.values()):.4f} and {fpr_max:.4f}'
        + (', all at or below the budget' if not over else
           f'; the budget was exceeded where the quantile fell inside a block of tied scores ({"; ".join(over)})')
        + ', and the object is visible in every output, but the detection rates differ in the direction of Table 6: ')
    para(doc,
         'Fig. 8 shows the same two recordings, one per sensor model, after each stage at a requested '
         f'false-alarm rate of {alpha:g}. ' + budget_sentence + f'on the {s0} recording the zero-knowledge '
         f'proposal passed {q(qk[0], "proposal_zk", "tpr"):.2f} of the object events, YNoise at its default '
         f'{q(qk[0], "ynoise_default", "tpr"):.2f}, YNoise with the handed-over W '
         f'{q(qk[0], "ynoise_handoff", "tpr"):.2f} and the two-stage score at f = '
         f'{qual["screen_fraction"]:g} {q(qk[0], "two_stage", "tpr"):.2f}; on the {s1} recording the '
         f'corresponding values were {q(qk[1], "proposal_zk", "tpr"):.2f}, '
         f'{q(qk[1], "ynoise_default", "tpr"):.2f}, {q(qk[1], "ynoise_handoff", "tpr"):.2f} and '
         f'{q(qk[1], "two_stage", "tpr"):.2f}. The Fano-factor score is shown for contrast: its '
         'per-pixel ratio takes the same value for every event of a pixel and is exactly zero wherever '
         'the minimum bin count is zero, so the alpha-quantile falls inside a block of tied scores and '
         f'the smallest block that can be passed already has a false-positive rate of {fano_fpr[0]:.3f} and '
         f'{fano_fpr[1]:.3f}, with {q(qk[0], "fano", "tpr"):.2f} and {q(qk[1], "fano", "tpr"):.2f} of the '
         'object events; a score with few distinct values cannot be set to a false-alarm budget by a '
         'quantile. For every method the quantile fixes how many events pass, not where they fall, and '
         'the surviving background is scattered over the frame. These are two recordings chosen by rule, '
         'not a summary; the pooled figures are those of Table 6.')
    figure(doc, 'fig8_qualitative.png',
           'Fig. 8 The same two recordings processed by every stage. Rows: one recording per sensor '
           f'model ({", ".join(qr[k]["recording_id"] for k in qk)}), selected as the recording whose '
           'stage-1 AUC is closest to the median of that sensor, with its rate-adaptive window W. '
           'Columns: all events; events inside the annotated object boxes; events passed at a requested '
           f'false-alarm rate of {qual["alpha"]:g} by the zero-knowledge proposal alone (stage 1), by YNoise alone at its '
           'published default, by the hybrid in its hand-over form (YNoise with dt = W taken from the proposal), '
           'by the hybrid in its two-stage form (proposal screens, YNoise confirms) with f = '
           f'{qual["screen_fraction"]:g}, and by the Fano-factor score (Sec. 2.7). Each panel is the per-pixel count of passed events over the '
           'whole analysed interval on a logarithmic grey scale; the achieved detection and '
           'false-positive rates against the annotation are given above each panel.')

    # ---------------- 4 Discussion ----------------
    heading(doc, '4 Discussion')
    para(doc,
         'The results support a modest but useful position for the event-driven Poisson LR detector. On '
         'the only public labelled event-camera SSA dataset its ROC-AUC is statistically indistinguishable '
         'from most of the published unsupervised event filters when those filters are given '
         'cross-validated tuning, and below the best of them, YNoise, by '
         f'{abs(tests["ynoise"]["auc_diff_mean"]):.2f}; without labels that gap closes '
         f'({fmt(dfl["edlr_adaptive"]["auc_mean"])} against {fmt(dfl["ynoise"]["auc_mean"])} at fixed '
         'configurations, Table 5); unlike all of them it can be operated without labels and without a '
         'manually chosen threshold, because its threshold follows from a false-alarm budget and is '
         'recalibrated on whatever stream it is given; and its cost is '
         'proportional to the number of events. The zero-knowledge analysis qualifies the phrase "without '
         'per-sensor tuning": a window stated in milliseconds did not transfer between the two sensor '
         'models, whereas a window stated as an expected background count did, so the property is one of '
         'the rate-adaptive form and of the recalibrated threshold, and is demonstrated on two sensor '
         'models only. The combination matters most where no reference exists: '
         'in a survey of a new field, a blind search for uncatalogued objects, or the first light of a '
         'new sensor, there is no labelled stream on which to tune a neighbour-count threshold or train a '
         'network, whereas a false-alarm budget can always be stated in advance and, as Sec. 3.5 shows, is '
         'honoured on the data. The cost property is the one that distinguishes the detector for '
         'astronomy: in a sparse regime the voxelised '
         f'form of the same statistic spent {cs["plr"]["ns_per_event_sparse"] / 1e3:.0f} \u03bcs per event, '
         'almost all of it on empty voxels, whereas the event-driven form spent under a microsecond. The '
         'same contrast applies to any frame-based filter and grows as sensors become larger and '
         'observing conditions darker.')
    para(doc,
         'Relative to supervised networks, the detector is '
         f'{abs(tests["mlpf"]["auc_diff_mean"]):.2f} to {abs(tests["edncnn"]["auc_diff_mean"]):.2f} AUC lower. '
         'We regard this as the price of not requiring labels rather than a deficiency to be hidden: the '
         'networks were trained on annotated recordings from the same two sensors and would need new '
         'annotations for a new sensor, site or observing mode, whereas the proposed detector fits its '
         'background model to whatever stream it is given. For surveys of unexplored regimes, and for '
         'on-sensor or on-board processing where a training pipeline is not available, that trade is '
         'often the right one. The synergy with the sensor is also worth stating: the detector uses the '
         'DVS output in the form the pixel circuit produces it, as per-pixel timestamp sequences, models '
         'the noise where it originates, in the pixel, and needs only per-pixel state. Because it touches '
         'each event once, in arrival order, with a few arithmetic operations, no training phase and a '
         'memory footprint set by the array rather than by the data, its structure suits the streaming, '
         'near-sensor and edge-processing setting for which event cameras are intended, where a '
         'label-free front end could pass a calibrated fraction of the stream downstream; we have not '
         'measured an embedded implementation, so this is a statement about structure, not about '
         'throughput. Where the highest '
         'detection rate at a very stringent budget is the goal and labelled data for threshold tuning '
         'exist, the density filter YNoise remains a strong choice. Sec. 3.7 shows that the two are not '
         'alternatives so much as stages of one pipeline: the rate-adaptive window that the proposal '
         'derives from the target stream served as the time constant of YNoise, and handing it over '
         f'without labels recovered {fmt(yf["handoff"]["auc"]["mean"])} of the '
         f'{fmt(hy["ynoise_cv_reference"]["auc_mean"])} that label-tuned cross-validation reached. Hand-tuned '
         'defaults are, in this light, priors formed on other sensors and other skies; on an unknown '
         'field they have no more authority than the stream itself, from which the rate, the window and '
         'the threshold can all be taken. The cascade in which the proposal screens at a stated budget '
         'and YNoise confirms on the passed events improved the ranking further, but did not lower the '
         'total cost when the confirmation stage is as cheap as YNoise; its cost case is a network or an '
         'off-sensor link as the second stage, which we have not measured. The cost comparison is also '
         'one-sided in favour of the comparators: the timings of Sec. 3.4 and Table 6 cover inference only, '
         'and the labelled recordings and grid search that the tuned comparators needed before inference '
         'are not charged to them. The one label-free tuning we did time, the pseudo-label grid search '
         'on the passed events, cost '
         f'{scr[f_mid]["pseudo"]["stage2_time_over_ynoise_full"]["median"]:.0f} times a full-stream YNoise '
         'pass, whereas the hand-over involves no search at all; with the cost of acquiring the '
         'parameters included, the case for taking them from the stream is stronger than the inference '
         'timings alone suggest.')
    para(doc,
         'Several limitations bound these conclusions. First, the ground truth is the EBSSA object '
         'annotation, a 10 x 10-pixel box around a tracked position over 10 ms; background events '
         'inside the box are counted as object and object events outside it as background, which '
         'compresses the AUC of every method and may affect them unequally. Second, the recordings come '
         'from two sensor models at one site, and the strata in Table 2 show that the ranking of '
         'unsupervised methods differs between sensors; the comparability claim is made over the pooled '
         'sample and should be re-tested on new hardware, and the zero-knowledge transfer of Sec. 3.6 '
         'consists of two directions between two sensors, with the constant k chosen once on the same '
         'data; the parameter hand-over of Sec. 3.7 was tested for one comparator, and the two-stage '
         'front end was not shown to lower total cost with that comparator. Third, the homogeneous-Poisson null ignores '
         'refractory behaviour and burst noise in the pixel circuit, so the achieved false-alarm rate is '
         'close to but not exactly the requested one, and persistent sources such as tracked stars are '
         'treated as background only because their pixels acquire a high estimated rate. '
         'Fourth, the cost measurements are single-core Python and Numba timings; absolute numbers will '
         'differ on other hardware, though the scaling exponents should not. Fifth, the published '
         'comparators were re-implemented from their descriptions rather than run from the authors\' '
         'code; they were given the same cross-validated tuning as the proposed detector, which removes '
         'the usual bias in favour of the proposal but means the reported comparator accuracies are '
         'upper bounds that an operator without labels could not reach. Finally, the analysis is '
         'retrospective and the strata were not pre-specified.')

    # ---------------- 5 Conclusion ----------------
    heading(doc, '5 Conclusion')
    para(doc,
         'A per-event Poisson tail test, with the background rate of each pixel estimated from its own '
         'inter-event intervals, detects faint object events in neuromorphic space-imaging streams with '
         'an accuracy comparable to most label-tuned unsupervised event filters, without labels, on all '
         f'{N["n_rec"]} labelled EBSSA recordings. Because it never builds a frame or a voxel grid, its '
         'cost falls in proportion to the number of events while that of frame-based processing does '
         'not, and because its output is a probability, the false-alarm rate can be specified in advance '
         'and honoured without labelled data. When its window is stated as an expected background count, '
         'the configuration chosen on one sensor model carried over to the other with an AUC loss of at '
         f'most {zk_max_drop("edlr_adaptive"):.2f}, and the threshold was recovered on the new stream '
         'without labels. The same window, handed to the density filter YNoise as its time constant, brought '
         f'that filter from {fmt(N["hyb"]["ynoise_full"]["default"]["auc"]["mean"])} to '
         f'{fmt(N["hyb"]["ynoise_full"]["handoff"]["auc"]["mean"])} AUC without labels, close to its label-tuned '
         f'{fmt(N["hyb"]["ynoise_cv_reference"]["auc_mean"])}. These properties make it a practical front end for sparse, '
         'unexplored observing regimes. All code, the exact data-access procedure and every number in '
         'this paper are reproducible from the public repository.')

    # ---------------- Disclosures ----------------
    heading(doc, 'Disclosures')
    para(doc,
         'The authors declare that there are no financial interests, commercial affiliations, or other '
         'potential conflicts of interest that could have influenced the objectivity of this research or '
         'the writing of this paper. The manuscript has not been published in a conference proceedings and is not under '
         'consideration elsewhere. An artificial-intelligence coding assistant (Devin, Cognition AI) was used to write the '
         'analysis code, generate the figures from the result files and draft and edit the text under '
         'the authors\' direction; the authors take full responsibility for the content.')

    heading(doc, 'Code and Data Availability')
    para(doc,
         'The EBSSA dataset is publicly available and was accessed through the Tonic library (version '
         '1.6.0), which downloads the labelled split from the authors\' repository. All analysis and '
         'manuscript-generation code, the result files and a one-command build that regenerates every '
         'number, table and figure in this paper are available at '
         'https://github.com/bougtoir/dvs-jatis-submission. No additional data were created.')

    heading(doc, 'Acknowledgments')
    para(doc, '[Funding and acknowledgments to be added by the authors.]')

    # ---------------- References ----------------
    heading(doc, 'References')
    uncited = set(REFS) - set(CITE_ORDER)
    if uncited:
        raise RuntimeError(f'uncited references: {sorted(uncited)}')
    for i, key in enumerate(CITE_ORDER, start=1):
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(3)
        p.paragraph_format.left_indent = Inches(0.3)
        p.paragraph_format.first_line_indent = Inches(-0.3)
        run = p.add_run(f'{i}. {REFS[key]}')
        run.font.size = Pt(10)

    out = OUT_DIR / 'manuscript_jatis.docx'
    doc.save(out)
    print(f'wrote {out}  ({n_words} words in abstract, {len(CITE_ORDER)} references)')
    return out


if __name__ == '__main__':
    build()
