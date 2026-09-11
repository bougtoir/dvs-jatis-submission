#!/usr/bin/env python3
"""
End-to-end build script for the IEEE Sensors Journal submission package.

Running this script regenerates:
- ieee_figures/ (local copies of manuscript-aligned figures)
- editorial_figures/ (renamed figures for separate journal upload)
- manuscript_ieee_sensors.docx
- highlights_ieee_sensors.docx
- title_page_ieee_sensors.docx
- cover_letter_ieee_sensors.docx
- figures_ieee_sensors.pptx
- graphical_abstract_ieee_sensors.png

All numerical values and figures are produced from ../results/*.json and the
reproducible DVS figure pipeline; nothing is hard-coded.
"""

import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent


def run(cmd):
    print(f"$ {cmd}")
    result = subprocess.run(cmd, shell=True, cwd=SCRIPT_DIR)
    if result.returncode != 0:
        print(f"Command failed with exit code {result.returncode}: {cmd}")
        sys.exit(result.returncode)


def main():
    # 1. Local, manuscript-aligned figure copies
    run('python3 prepare_ieee_figures.py')

    # 2. Main manuscript
    run('python3 create_ieee_docx.py')

    # 3. Supporting files (highlights, title page, cover letter, pptx, graphical abstract)
    run('python3 create_ieee_supporting_files.py')

    print('\nIEEE Sensors Journal submission package built successfully.')


if __name__ == '__main__':
    main()
