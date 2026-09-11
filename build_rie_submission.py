#!/usr/bin/env python3
"""
One-command reproduction of the Results in Engineering (RINENG) submission package.

From a clean clone, with dependencies installed and the public EBSSA dataset
available (downloaded automatically by tonic on first run), this script
regenerates every figure, summary JSON, and submission document used by the
RINENG submission.

    pip install -r requirements.txt
    python build_rie_submission.py

Outputs:
  - results/demo_summary.json
  - results/evaluation_summary.json
  - fig3_noise_inverse_demo.png, fig4_sn_improvement.png
  - fig5_systematic_evaluation.png, fig6_a5_simulation.png, fig7_per_recording_comparison.png
  - fig2_g3_pipeline_en.png, fig1_gap_map_en.png
  - results_in_engineering_submission/sr_figures/*.png/.tiff
  - results_in_engineering_submission/rie_figures/*.png/.tiff
  - results_in_engineering_submission/manuscript_rie.docx
  - results_in_engineering_submission/figures_rie.pptx
  - results_in_engineering_submission/highlights_rie.docx
  - results_in_engineering_submission/title_page_rie.docx
  - results_in_engineering_submission/cover_letter_rie.docx
  - results_in_engineering_submission/supplementary_material_rie.docx
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RIE_DIR = ROOT / 'results_in_engineering_submission'


def run(script: Path, cwd: Path = ROOT) -> None:
    print(f"\n{'='*70}\nRunning: {script}\n{'='*70}")
    result = subprocess.run([sys.executable, str(script)], cwd=str(cwd))
    if result.returncode != 0:
        raise RuntimeError(f"Script failed: {script} (exit {result.returncode})")


def main():
    # Ensure output directories exist (EBSSA data is downloaded on first use)
    (ROOT / 'results').mkdir(exist_ok=True)
    (ROOT / 'data').mkdir(parents=True, exist_ok=True)

    # (1) English conceptual/pipeline figures (produces fig2_g3_pipeline_en.png)
    run(ROOT / 'create_figures_en_pptx.py')

    # (2) Demo on EBSSA recording #0
    run(ROOT / 'noise_inverse_demo.py')

    # (3) Systematic evaluation on 20 EBSSA recordings
    run(ROOT / 'systematic_evaluation.py')

    # (4) SR conceptual figures (uses results/evaluation_summary.json for Fig. 7)
    run(RIE_DIR / 'generate_sr_figures.py')

    # (5) Compose final, manuscript-number-aligned high-resolution figures
    run(RIE_DIR / 'prepare_rie_figures.py')

    # (6) RINENG submission documents
    run(RIE_DIR / 'create_rie_docx.py')
    run(RIE_DIR / 'create_rie_pptx.py')
    run(RIE_DIR / 'create_rie_highlights.py')
    run(RIE_DIR / 'create_rie_title_page.py')
    run(RIE_DIR / 'create_rie_cover_letter.py')
    run(RIE_DIR / 'create_rie_supplementary.py')

    print("\n" + "=" * 70)
    print("RINENG submission package regenerated successfully.")
    print("=" * 70)


if __name__ == '__main__':
    main()
