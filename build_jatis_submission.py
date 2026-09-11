#!/usr/bin/env python3
"""Regenerate the JATIS submission package from the public EBSSA data.

    python build_jatis_submission.py            # documents + figures from results/*.json
    python build_jatis_submission.py --full     # also rerun the EBSSA analyses (hours)

The --full run downloads the labelled EBSSA split through tonic (~12 GB) into
data/EBSSA/ on first use and rewrites results/sp_evaluation_summary.json,
results/sp_per_recording.json, results/sparsity_cost.json,
results/partial_auc.json and results/self_calibration.json. The default run
only rebuilds the figures, manuscript, cover letter and PPTX from the tracked
result files.
"""

import argparse
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
JATIS = HERE / 'jatis_submission'

ANALYSES = [
    (HERE, ['sp_evaluation.py']),
    (HERE, ['sparsity_cost.py']),
    (HERE, ['operating_points.py']),
    (HERE, ['zero_knowledge.py']),
    (HERE, ['hybrid_screening.py']),
    (HERE, ['qualitative_example.py']),
]
DOCUMENTS = [
    (JATIS, ['make_jatis_figures.py']),
    (JATIS, ['create_jatis_docx.py']),
    (JATIS, ['create_cover_letter.py']),
    (JATIS, ['create_jatis_pptx.py']),
]


def run(cwd, args):
    print(f'>>> {" ".join(args)}', flush=True)
    subprocess.run([sys.executable, *args], cwd=cwd, check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--full', action='store_true',
                        help='rerun the EBSSA analyses before building the documents')
    args = parser.parse_args()
    steps = (ANALYSES if args.full else []) + DOCUMENTS
    for cwd, cmd in steps:
        run(cwd, cmd)
    make_zip()
    print('JATIS package written to', JATIS)


def make_zip():
    out = JATIS / 'jatis_submission_package.zip'
    files = [JATIS / 'manuscript_jatis.docx', JATIS / 'cover_letter_jatis.docx',
             JATIS / 'figures_jatis.pptx', JATIS / 'README.md']
    files += sorted(JATIS.glob('fig*.png')) + sorted(JATIS.glob('fig*.tiff'))
    files += sorted(JATIS.glob('*.py'))
    files += [HERE / 'build_jatis_submission.py', HERE / 'README.md', HERE / 'requirements.txt']
    files += sorted((HERE / 'results').glob('*.json')) + sorted((HERE / 'results').glob('*.npz'))
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in files:
            if f.exists():
                z.write(f, f.relative_to(HERE))
    print('wrote', out.name, f'({len(files)} files)')


if __name__ == '__main__':
    main()
