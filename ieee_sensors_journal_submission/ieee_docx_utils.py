#!/usr/bin/env python3
"""
Shared Word/OMML helpers for the IEEE Sensors Journal submission package.

Utilities reused from the Results in Engineering manuscript builder:
- OMML equation object construction
- Metric-aware paragraph, figure, table and reference helpers
- Citation handling ([1], [1,2], [1-3]) with metric placeholders {key}
- Section-level column layout helpers for IEEE double-column formatting

Numerical values are loaded from ../results/evaluation_summary.json and
../results/demo_summary.json so the manuscript stays reproducible.
"""

import json
import re
import sys
from lxml import etree
from docx import Document
from docx.shared import Inches, Pt, Cm
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.section import WD_SECTION_START
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
OUT_DIR = SCRIPT_DIR
# Manuscript figures are provided by prepare_ieee_figures.py in this directory.
FIG_DIR = SCRIPT_DIR / 'ieee_figures'

# Global metrics cache; populated by load_results(). Keys may be referenced
# with {key} placeholders in manuscript strings so numerical values are not
# hard-coded.
_METRICS = {}


# =========================================================
# OMML equation helpers (reused from JATIS script)
# =========================================================

def _mr(parent, text, italic=True, bold=False):
    r = etree.SubElement(parent, qn('m:r'))
    if not italic or bold:
        rPr = etree.SubElement(r, qn('m:rPr'))
        if not italic:
            sty = etree.SubElement(rPr, qn('m:sty'))
            sty.set(qn('m:val'), 'p')
        if bold:
            sty = etree.SubElement(rPr, qn('m:sty'))
            sty.set(qn('m:val'), 'b')
    t = etree.SubElement(r, qn('m:t'))
    t.text = text
    t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
    return r


def _sub(parent, base, sub):
    el = etree.SubElement(parent, qn('m:sSub'))
    e = etree.SubElement(el, qn('m:e'))
    _mr(e, base)
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


def _sup_builder(parent, base_builder, sup_text):
    """Superscript where the base is built by a callable (e.g. delimited fraction)."""
    el = etree.SubElement(parent, qn('m:sSup'))
    e = etree.SubElement(el, qn('m:e'))
    base_builder(e)
    s = etree.SubElement(el, qn('m:sup'))
    _mr(s, sup_text)
    return el


def _frac(parent, num_builder, den_builder):
    f = etree.SubElement(parent, qn('m:f'))
    num = etree.SubElement(f, qn('m:num'))
    num_builder(num)
    den = etree.SubElement(f, qn('m:den'))
    den_builder(den)
    return f


def _delim(parent, content_builder, left='(', right=')'):
    d = etree.SubElement(parent, qn('m:d'))
    dPr = etree.SubElement(d, qn('m:dPr'))
    begChr = etree.SubElement(dPr, qn('m:begChr'))
    begChr.set(qn('m:val'), left)
    endChr = etree.SubElement(dPr, qn('m:endChr'))
    endChr.set(qn('m:val'), right)
    e = etree.SubElement(d, qn('m:e'))
    content_builder(e)
    return d


def _hat(parent, text):
    acc = etree.SubElement(parent, qn('m:acc'))
    accPr = etree.SubElement(acc, qn('m:accPr'))
    chrEl = etree.SubElement(accPr, qn('m:chr'))
    chrEl.set(qn('m:val'), '\u0302')
    e = etree.SubElement(acc, qn('m:e'))
    _mr(e, text)
    return acc


def _sqrt(parent, content_builder):
    rad = etree.SubElement(parent, qn('m:rad'))
    radPr = etree.SubElement(rad, qn('m:radPr'))
    degHide = etree.SubElement(radPr, qn('m:degHide'))
    degHide.set(qn('m:val'), '1')
    deg = etree.SubElement(rad, qn('m:deg'))
    e = etree.SubElement(rad, qn('m:e'))
    content_builder(e)
    return rad


def add_display_equation(doc, builder_func, eq_num=None):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(8)
    p.paragraph_format.space_after = Pt(8)
    omathpara = etree.SubElement(p._element, qn('m:oMathPara'))
    omath = etree.SubElement(omathpara, qn('m:oMath'))
    builder_func(omath)
    if eq_num:
        run = p.add_run(f'    ({eq_num})')
        run.font.size = Pt(12)
    return p


# =========================================================
# Equation definitions for integrated manuscript
# =========================================================

def eq_sr_snr(omath):
    """Eq: SNR_out(sigma) proportional to (A/sigma^2)^2 exp(-2 theta^2 / sigma^2)"""
    _sub(omath, 'SNR', 'out')
    def _arg(e):
        _mr(e, '\u03c3')
    _delim(omath, _arg)
    _mr(omath, ' \u221d ')
    def _frac_delim(e):
        def _n(n):
            _mr(n, 'A')
        def _d(d):
            _sup(d, '\u03c3', '2')
        _frac(e, _n, _d)
    _sup_builder(omath, _frac_delim, '2')
    _mr(omath, ' ')
    _mr(omath, 'exp', italic=False)
    def _exp_arg(e):
        _mr(e, '\u22122')
        _frac(e,
               lambda n: _sup(n, '\u03b8', '2'),
               lambda d: _sup(d, '\u03c3', '2'))
    _delim(omath, _exp_arg)


def eq_sigma_eff(omath):
    """Eq: sigma_eff = sigma * sqrt(1 - rho^2)"""
    _sub(omath, '\u03c3', 'eff')
    _mr(omath, ' = \u03c3')
    def _sq(e):
        _mr(e, '1 \u2212 ')
        _sup(e, '\u03c1', '2')
    _sqrt(omath, _sq)


def eq_rho_star(omath):
    """Eq: rho* = sqrt(1 - theta^2 / sigma^2), for sigma > theta"""
    _sup(omath, '\u03c1', '*')
    _mr(omath, ' = ')
    def _sq(e):
        _mr(e, '1 \u2212 ')
        _frac(e,
               lambda n: _sup(n, '\u03b8', '2'),
               lambda d: _sup(d, '\u03c3', '2'))
    _sqrt(omath, _sq)
    _mr(omath, ', \u03c3 > \u03b8', italic=False)


def eq_snr_gain(omath):
    """Eq: SNR gain = (sigma/theta)^4 exp(2(sigma^2 - theta^2)/sigma^2)"""
    def _n(n):
        _sub(n, 'SNR', 'out')
        def _arg(e):
            _mr(e, '\u03c3, ')
            _sup(e, '\u03c1', '*')
        _delim(n, _arg)
    def _d(d):
        _sub(d, 'SNR', 'out')
        def _arg(e):
            _mr(e, '\u03c3, 0')
        _delim(d, _arg)
    _frac(omath, _n, _d)
    _mr(omath, ' = ')
    def _frac_delim(e):
        def _n2(n):
            _mr(n, '\u03c3')
        def _d2(d):
            _mr(d, '\u03b8')
        _frac(e, _n2, _d2)
    _sup_builder(omath, _frac_delim, '4')
    _mr(omath, ' ')
    _mr(omath, 'exp', italic=False)
    def _exp_arg(e):
        _mr(e, '2')
        def _n3(n):
            _sup(n, '\u03c3', '2')
            _mr(n, ' \u2212 ')
            _sup(n, '\u03b8', '2')
        def _d3(d):
            _sup(d, '\u03c3', '2')
        _frac(e, _n3, _d3)
    _delim(omath, _exp_arg)


def eq_a5_model(omath):
    """Eq: lambda_noise(T, I_bg) = I_dark,ref * exp(alpha * DeltaT) * (1 + beta * I_bg)"""
    _sub(omath, '\u03bb', 'noise')
    def _args(e):
        _mr(e, 'T, ')
        _sub(e, 'I', 'bg')
    _delim(omath, _args)
    _mr(omath, ' = ')
    _sub(omath, 'I', 'dark,ref')
    _mr(omath, ' \u22c5 exp')
    def _exp_arg(e):
        _mr(e, '\u03b1 \u22c5 \u0394T')
    _delim(omath, _exp_arg)
    _mr(omath, ' \u22c5 ')
    def _bg(e):
        _mr(e, '1 + \u03b2 \u22c5 ')
        _sub(e, 'I', 'bg')
    _delim(omath, _bg)


def eq_accuracy(omath):
    """Eq: alpha = 1 - ||e_hat - e_true|| / ||e_true||"""
    _mr(omath, '\u03b1 = 1 \u2212 ')
    def _num(n):
        _mr(n, '\u2016')
        _hat(n, 'e')
        _sub(n, '', 'noise')
        _mr(n, ' \u2212 ')
        _sub(n, 'e', 'noise,true')
        _mr(n, '\u2016')
    def _den(d):
        _mr(d, '\u2016')
        _sub(d, 'e', 'noise,true')
        _mr(d, '\u2016')
    _frac(omath, _num, _den)


def eq_residual_noise(omath):
    """Eq: sigma_residual = (1 - alpha) * sigma_original"""
    _sub(omath, '\u03c3', 'residual')
    _mr(omath, ' = ')
    def _factor(e):
        _mr(e, '1 \u2212 \u03b1')
    _delim(omath, _factor)
    _mr(omath, ' \u22c5 ')
    _sub(omath, '\u03c3', 'original')


def eq_snr_improvement(omath):
    """Eq: SNR_after / SNR_before = 1 / (1 - alpha)"""
    def _num(n):
        _sub(n, 'SNR', 'after')
    def _den(d):
        _sub(d, 'SNR', 'before')
    _frac(omath, _num, _den)
    _mr(omath, ' = ')
    def _num2(n):
        _mr(n, '1', italic=False)
    def _den2(d):
        _mr(d, '1 \u2212 \u03b1')
    _frac(omath, _num2, _den2)


def eq_map_estimation(omath):
    """Eq: theta_hat = argmax p(e_cal|theta) * p(theta|theta_prior)"""
    _hat(omath, '\u03b8')
    _mr(omath, ' = ')
    _sub(omath, 'argmax', '\u03b8')
    _mr(omath, ' p')
    def _likelihood(e):
        _sub(e, 'e', 'cal')
        _mr(e, ' | \u03b8')
    _delim(omath, _likelihood)
    _mr(omath, ' \u22c5 p')
    def _prior(e):
        _mr(e, '\u03b8 | ')
        _sub(e, '\u03b8', 'prior')
    _delim(omath, _prior)


def eq_nn_output(omath):
    """Eq: lambda_hat_noise = CNN(R_Fano/R_Fano,max, F/F_max; W)"""
    _hat(omath, '\u03bb')
    _sub(omath, '', 'noise')
    _mr(omath, ' = CNN( ')
    _frac(omath,
          lambda n: _sub(n, 'R', 'Fano'),
          lambda d: _sub(d, 'R', 'Fano,max'))
    _mr(omath, ', ')
    _frac(omath,
          lambda n: _mr(n, 'F'),
          lambda d: _sub(d, 'F', 'max'))
    _mr(omath, '; W )')


def eq_p_noise(omath):
    """Eq: P_noise(e_i) = lambda_hat_noise / (lambda_hat_noise + lambda_hat_signal)"""
    _sub(omath, 'P', 'noise')
    def _ei(e):
        _sub(e, 'e', 'i')
    _delim(omath, _ei)
    _mr(omath, ' = ')
    def _num(n):
        _hat(n, '\u03bb')
        _sub(n, '', 'noise')
        def _coords(e):
            _sub(e, 'x', 'i')
            _mr(e, ', ')
            _sub(e, 'y', 'i')
            _mr(e, ', ')
            _sub(e, 't', 'i')
        _delim(n, _coords)
    def _den(d):
        _hat(d, '\u03bb')
        _sub(d, '', 'noise')
        def _c1(e):
            _mr(e, '\u2026')
        _delim(d, _c1)
        _mr(d, ' + ')
        _hat(d, '\u03bb')
        _sub(d, '', 'signal')
        def _c2(e):
            _mr(e, '\u2026')
        _delim(d, _c2)
    _frac(omath, _num, _den)


def eq_fano(omath):
    """Eq: F = Var(N_k) / Mean(N_k)"""
    _mr(omath, 'F', italic=True)
    _mr(omath, ' = ', italic=False)
    def _num(n):
        _mr(n, 'Var', italic=False)
        def _nk(e):
            _sub(e, 'N', 'k')
        _delim(n, _nk)
    def _den(d):
        _mr(d, 'Mean', italic=False)
        def _nk(e):
            _sub(e, 'N', 'k')
        _delim(d, _nk)
    _frac(omath, _num, _den)


def eq_detection_limit(omath):
    """Eq: Delta_m approx 2.5 log10(1/(1-alpha))"""
    _mr(omath, '\u0394m \u2248 2.5 ')
    _sub(omath, 'log', '10')
    def _arg(e):
        def _n(n):
            _mr(n, '1', italic=False)
        def _d(d):
            _mr(d, '1 \u2212 \u03b1')
        _frac(e, _n, _d)
    _delim(omath, _arg)


# =========================================================
# Document building helpers
# =========================================================

def add_heading(doc, text, level=1):
    return doc.add_heading(text, level=level)


CITATION_RE = re.compile(r'(\{[^}]+\}|\[\d+(?:[-,\u2013]\d+)*\])')


def _add_run_for_part(paragraph, part, base_size=Pt(12), bold=False, italic=False):
    if part.startswith('{') and part.endswith('}'):
        run = paragraph.add_run(part[1:-1])
        run.font.superscript = True
        run.font.size = Pt(9)
        return run
    if part.startswith('[') and part.endswith(']'):
        # Citation: keep brackets in normal size, superscript the numbers inside.
        paragraph.add_run('[').font.size = base_size
        inner = part[1:-1]
        run_inner = paragraph.add_run(inner)
        run_inner.font.superscript = True
        run_inner.font.size = Pt(9)
        paragraph.add_run(']').font.size = base_size
        return run_inner
    # Plain text: apply base font size and optional bold/italic.
    run = paragraph.add_run(part)
    run.font.size = base_size
    if bold:
        run.bold = True
    if italic:
        run.italic = True
    return run


# =========================================================
# IEEE double-column section helpers
# =========================================================

DEFAULT_COL_SPACE = '720'  # 0.5 inch in twips


def set_section_columns(section, n: int, space: str = DEFAULT_COL_SPACE):
    """Set the number of columns for a Section object."""
    sectPr = section._sectPr
    existing = sectPr.findall(qn('w:cols'))
    for c in existing:
        sectPr.remove(c)
    cols = OxmlElement('w:cols')
    cols.set(qn('w:num'), str(n))
    cols.set(qn('w:space'), space)
    sectPr.append(cols)


def add_section(doc, start: WD_SECTION_START = WD_SECTION_START.CONTINUOUS) -> "Section":
    """Add a new section and return it."""
    return doc.add_section(start)


def set_page_size_us_letter(section):
    section.page_width = Inches(8.5)
    section.page_height = Inches(11.0)
    section.top_margin = Inches(0.75)
    section.bottom_margin = Inches(1.0)
    section.left_margin = Inches(0.75)
    section.right_margin = Inches(0.75)


def add_paragraph(doc, text, style='Normal', bold=False, italic=False,
                  alignment=None, space_after=None, space_before=None):
    # Metrics placeholders such as {fano_auc} are replaced from the results files.
    text = text.format(**_METRICS)
    p = doc.add_paragraph(style=style)
    if alignment:
        p.alignment = alignment
    if space_after is not None:
        p.paragraph_format.space_after = Pt(space_after)
    if space_before is not None:
        p.paragraph_format.space_before = Pt(space_before)
    parts = CITATION_RE.split(text)
    for part in parts:
        if part:
            _add_run_for_part(p, part, base_size=Pt(12), bold=bold, italic=italic)
    return p


def add_reference(doc, text, space_after=None):
    p = doc.add_paragraph(style='Normal')
    if space_after is not None:
        p.paragraph_format.space_after = Pt(space_after)
    run = p.add_run(text)
    run.font.size = Pt(12)
    return p


def add_figure(doc, img_path, caption, width=Inches(5.5)):
    caption = caption.format(**_METRICS)
    if not img_path.exists():
        add_paragraph(doc, "[MISSING FIGURE: " + img_path.name + "]")
        return
    p_img = doc.add_paragraph()
    p_img.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_img.paragraph_format.space_before = Pt(12)
    run = p_img.add_run()
    run.add_picture(str(img_path), width=width)
    p_cap = doc.add_paragraph()
    p_cap.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p_cap.paragraph_format.space_before = Pt(6)
    p_cap.paragraph_format.space_after = Pt(12)
    parts = CITATION_RE.split(caption)
    for part in parts:
        if part:
            _add_run_for_part(p_cap, part, base_size=Pt(9))
    return p_cap


def add_table(doc, headers, data, caption=None):
    if caption:
        caption = caption.format(**_METRICS)
        p_cap = doc.add_paragraph()
        p_cap.paragraph_format.space_before = Pt(12)
        p_cap.paragraph_format.space_after = Pt(6)
        parts = CITATION_RE.split(caption)
        for part in parts:
            if part:
                run_cap = _add_run_for_part(p_cap, part, base_size=Pt(9))
                run_cap.italic = True
    table = doc.add_table(rows=len(data) + 1, cols=len(headers))
    table.style = 'Table Grid'
    for i, h in enumerate(headers):
        table.rows[0].cells[i].text = h
        for p in table.rows[0].cells[i].paragraphs:
            for r in p.runs:
                r.font.bold = True
                r.font.size = Pt(10)
    for row_idx, row_data in enumerate(data):
        for col_idx, val in enumerate(row_data):
            table.rows[row_idx + 1].cells[col_idx].text = val
            for p in table.rows[row_idx + 1].cells[col_idx].paragraphs:
                for r in p.runs:
                    r.font.size = Pt(10)
    return table


# =========================================================
def load_results():
    """Load evaluation and demo metrics from results/*.json."""
    results_dir = SCRIPT_DIR.parent / 'results'
    with open(results_dir / 'evaluation_summary.json') as f:
        summary = json.load(f)
    with open(results_dir / 'demo_summary.json') as f:
        demo = json.load(f)

    def fmt(v):
        if isinstance(v, str):
            return v
        # Keep mean values consistent with the published strings (3 decimals).
        return f"{v:.3f}"

    def pct(v):
        return f"{100.0 * float(v):.1f}"

    m = summary['methods']
    return {
        'n_recordings': summary['n_recordings'],
        'n_valid': m['fano_filter']['n_valid'],
        # Table rows
        'temporal_nrr': m['temporal_filter']['nrr'],
        'temporal_spr': m['temporal_filter']['spr'],
        'temporal_f1': m['temporal_filter']['f1'],
        'temporal_auc': m['temporal_filter']['auc'],
        'nearest_nrr': m['nearest_neighbor_filter']['nrr'],
        'nearest_spr': m['nearest_neighbor_filter']['spr'],
        'nearest_f1': m['nearest_neighbor_filter']['f1'],
        'nearest_auc': m['nearest_neighbor_filter']['auc'],
        'bilateral_nrr': m['bilateral_filter']['nrr'],
        'bilateral_spr': m['bilateral_filter']['spr'],
        'bilateral_f1': m['bilateral_filter']['f1'],
        'bilateral_auc': m['bilateral_filter']['auc'],
        'motion_nrr': m['motion_filter']['nrr'],
        'motion_spr': m['motion_filter']['spr'],
        'motion_f1': m['motion_filter']['f1'],
        'motion_auc': m['motion_filter']['auc'],
        'pi_nrr': m['pi_dc_dvs']['nrr'],
        'pi_spr': m['pi_dc_dvs']['spr'],
        'pi_f1': m['pi_dc_dvs']['f1'],
        'pi_auc': m['pi_dc_dvs']['auc'],
        'fano_nrr': m['fano_filter']['nrr'],
        'fano_spr': m['fano_filter']['spr'],
        'fano_f1': m['fano_filter']['f1'],
        'fano_auc': m['fano_filter']['auc'],
        # Scalar values used in the text
        'temporal_auc_value': fmt(m['temporal_filter']['auc_mean']),
        'nearest_auc_value': fmt(m['nearest_neighbor_filter']['auc_mean']),
        'bilateral_auc_value': fmt(m['bilateral_filter']['auc_mean']),
        'motion_auc_value': fmt(m['motion_filter']['auc_mean']),
        'pi_auc_value': fmt(m['pi_dc_dvs']['auc_mean']),
        'fano_auc_value': fmt(m['fano_filter']['auc_mean']),
        'baseline_auc_min': fmt(min(
            m['temporal_filter']['auc_mean'],
            m['nearest_neighbor_filter']['auc_mean'],
            m['bilateral_filter']['auc_mean'],
            m['motion_filter']['auc_mean'],
        )),
        'baseline_auc_max': fmt(max(
            m['temporal_filter']['auc_mean'],
            m['nearest_neighbor_filter']['auc_mean'],
            m['bilateral_filter']['auc_mean'],
            m['motion_filter']['auc_mean'],
        )),
        'fano_spr_value': fmt(m['fano_filter']['spr_mean']),
        'fano_nrr_value': fmt(m['fano_filter']['nrr_mean']),
        'temporal_nrr_pct': pct(m['temporal_filter']['nrr_mean']),
        'temporal_spr_pct': pct(m['temporal_filter']['spr_mean']),
        'fano_spr_pct': pct(m['fano_filter']['spr_mean']),
        'fano_nrr_pct': pct(m['fano_filter']['nrr_mean']),
        'pi_spr_pct': pct(m['pi_dc_dvs']['spr_mean']),
        'a5_mean_snr': f"{summary['a5_simulation']['mean_improvement']:.1f}",
        'a5_max_snr': f"{summary['a5_simulation']['max_improvement']:.1f}",
        # Demo
        'demo_events_in': demo['events_in'],
        'demo_events_residual': demo['events_residual'],
        'demo_removal_pct': demo['removal_pct'],
        'demo_threshold': demo['threshold'],
        'demo_signal_pixels': demo['signal_pixels'],
        'demo_total_pixels': demo['total_pixels'],
    }


# =========================================================
# Build the integrated manuscript
# =========================================================
