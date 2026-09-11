#!/usr/bin/env python3
"""Create supplementary material for the RINENG submission."""
from docx import Document
from docx.shared import Inches, Pt
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
FIG_DIR = SCRIPT_DIR / 'rie_figures'
OUT_PATH = SCRIPT_DIR / 'supplementary_material_rie.docx'


def add_paragraph(doc, text, bold=False, space_after=6):
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.font.name = 'Times New Roman'
    run.font.size = Pt(12)
    run.bold = bold
    p.paragraph_format.space_after = Pt(space_after)
    return p


def add_table(doc, headers, rows, caption):
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = 'Table Grid'
    hdr_cells = table.rows[0].cells
    for i, h in enumerate(headers):
        hdr_cells[i].text = h
        for p in hdr_cells[i].paragraphs:
            for r in p.runs:
                r.font.bold = True
                r.font.name = 'Times New Roman'
                r.font.size = Pt(11)
    for row in rows:
        cells = table.add_row().cells
        for i, val in enumerate(row):
            cells[i].text = str(val)
            for p in cells[i].paragraphs:
                for r in p.runs:
                    r.font.name = 'Times New Roman'
                    r.font.size = Pt(11)
    p = doc.add_paragraph()
    p.alignment = 1  # center
    run = p.add_run(caption)
    run.font.name = 'Times New Roman'
    run.font.size = Pt(10)
    run.italic = True


def main():
    doc = Document()
    style = doc.styles['Normal']
    style.font.name = 'Times New Roman'
    style.font.size = Pt(12)

    add_paragraph(doc, 'Supplementary Material', bold=True, space_after=12)

    add_paragraph(doc,
        'Supplementary Table S1. Representative A5 pixel-noise-model parameters used '
        'for the simulations in Section 4.3. These values are the default set from '
        'the physically realistic DVS pixel model of Gra\u00e7a and Delbr\u00fcck [18].',
        bold=True, space_after=6)

    add_table(doc,
        ['Parameter', 'Symbol', 'Value', 'Unit'],
        [
            ['Reference dark current', 'I_dark,ref', '0.01', 'event s\u207b\u00b9 pixel\u207b\u00b9'],
            ['Reference temperature', 'T_ref', '25', '\u00b0C'],
            ['Temperature coefficient', '\u03b1', '0.08', '\u00b0C\u207b\u00b9'],
            ['Background illuminance sensitivity', '\u03b2', '0.05', 'lux\u207b\u00b9'],
            ['Effective log-intensity threshold', '\u03b8_eff', '0.15', 'log-intensity units'],
        ],
        caption='Table S1. Representative A5 model parameters.')

    add_paragraph(doc,
        'Supplementary Figure S1. Per-recording Noise Removal Rate comparison across '
        'all valid EBSSA recordings for the six evaluated methods.',
        bold=True, space_after=6)

    img_path = FIG_DIR / 'supplementary_fig_per_recording.png'
    if img_path.exists():
        doc.add_picture(str(img_path), width=Inches(5.5))
    else:
        add_paragraph(doc, '[MISSING FIGURE: supplementary_fig_per_recording.png]')

    doc.save(str(OUT_PATH))
    print(f"Supplementary material saved: {OUT_PATH}")


if __name__ == '__main__':
    main()
