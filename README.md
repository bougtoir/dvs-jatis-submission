# DVS Noise Inverse Problem

Reproducible code and data pipeline for event-camera (DVS) noise removal and
faint-object detection on the public EBSSA dataset.

## JATIS submission (current): event-driven Poisson likelihood-ratio detector

```bash
pip install -r requirements.txt
python build_jatis_submission.py          # documents + figures from results/*.json
python build_jatis_submission.py --full   # also rerun the EBSSA analyses (hours, ~12 GB download)
```

| Script | Output |
|--------|--------|
| `event_driven.py` | proposed detector and event-driven ablations (library) |
| `sp_evaluation.py` | `results/sp_evaluation_summary.json`, `results/sp_per_recording.json` (43 recordings, grouped 4-fold CV, paired tests) |
| `sparsity_cost.py` | `results/sparsity_cost.json` (cost versus thinned real stream) |
| `operating_points.py` | `results/partial_auc.json`, `results/self_calibration.json` |
| `zero_knowledge.py` | `results/zero_knowledge.json` (cross-sensor transfer, fixed defaults, threshold transfer, saturation; resumable via `results/sp_cache/`) |
| `hybrid_screening.py` | `results/hybrid_screening.json` (rate-adaptive window handed over to YNoise; two-stage screen-then-confirm front end; resumable via `results/sp_cache/`) |
| `qualitative_example.py` | `results/qualitative_example.json`, `results/qualitative_example.npz` (one median-AUC recording per sensor processed by every stage; per-pixel count images of passed events) |
| `jatis_submission/make_jatis_figures.py` | `jatis_submission/fig1_method.png` ... `fig8_qualitative.png` (300 dpi PNG and LZW TIFF of each) |
| `jatis_submission/create_jatis_docx.py` | `jatis_submission/manuscript_jatis.docx` (figures and tables inline) |
| `jatis_submission/create_cover_letter.py` | `jatis_submission/cover_letter_jatis.docx` |
| `jatis_submission/create_jatis_pptx.py` | `jatis_submission/figures_jatis.pptx` (editable, one figure per slide) |
| `build_jatis_submission.py` (last step) | `jatis_submission/jatis_submission_package.zip` (DOCX, cover letter, PPTX, PNG/TIFF figures, scripts, result JSON) |

Every number in the manuscript, cover letter and PPTX is read from
`results/*.json` through `jatis_submission/manuscript_numbers.py`. The EBSSA
labelled split is downloaded by `tonic` into `data/EBSSA/` (note: tonic 1.6.0
saves the file in the working directory; move `labelled_ebssa.h5` into
`data/EBSSA/` if the loader cannot find it).

## Results in Engineering submission package

### Quick start

```bash
pip install -r requirements.txt
python build_rie_submission.py
```

This regenerates every figure, summary JSON, and submission document from the
public EBSSA dataset and the code in this repository.

### Data

The public EBSSA event-camera dataset is downloaded automatically by
`tonic` on the first run of `noise_inverse_demo.py` and
`systematic_evaluation.py`.  The download is large (~12 GB, one file).

### Pipeline

| Script | Output |
|--------|--------|
| `create_figures_en_pptx.py` | `fig2_g3_pipeline_en.png` (Fig. 3, all-English pipeline) |
| `noise_inverse_demo.py` | `fig3_noise_inverse_demo.png`, `fig4_sn_improvement.png`, `results/demo_summary.json` |
| `systematic_evaluation.py` | `fig5_systematic_evaluation.png`, `fig6_a5_simulation.png`, `fig7_per_recording_comparison.png`, `results/evaluation_summary.json` |
| `results_in_engineering_submission/generate_sr_figures.py` | `sr_figures/fig1_schematic.png`, `fig2_sr_curves.png`, `fig3_optimal_rho.png`, `fig4_detection_probability.png`, `fig5_roc_comparison.png`, `fig7_dvs_application.png` |
| `results_in_engineering_submission/create_rie_*.py` | `manuscript_rie.docx`, `figures_rie.pptx`, `highlights_rie.docx`, `title_page_rie.docx`, `cover_letter_rie.docx` |

All numerical values in the submission documents are loaded from
`results/evaluation_summary.json` and `results/demo_summary.json`; nothing is
hard-coded.

## Requirements

See `requirements.txt`.  The `torch` CPU wheel is pulled from the PyTorch CPU
index.
