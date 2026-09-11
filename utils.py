"""Shared utilities for RIE figure generation and submission build."""
from pathlib import Path
import matplotlib.pyplot as plt
from PIL import Image

DPI = 600


def save_figure(fig, path, facecolor='white', bbox_inches='tight'):
    """Save a matplotlib figure as both PNG and LZW-compressed TIFF at 600 dpi.

    Parameters
    ----------
    bbox_inches : str or None
        'tight' crops to the tight bounding box of the figure content.
        None uses the standard figure size (override rcParams['savefig.bbox']).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    png_path = path.with_suffix('.png')
    tiff_path = path.with_suffix('.tiff')
    kwargs = {'dpi': DPI, 'facecolor': facecolor}
    if bbox_inches is not None:
        kwargs['bbox_inches'] = bbox_inches
    else:
        # Force a non-cropped save by disabling tight-bbox rcParams for this call.
        with plt.rc_context({'savefig.bbox': None}):
            fig.savefig(png_path, **kwargs)
        with Image.open(png_path) as img:
            img.save(tiff_path, compression='tiff_lzw', dpi=(DPI, DPI))
        print(f"  Saved: {png_path} + {tiff_path}")
        return
    fig.savefig(png_path, **kwargs)
    with Image.open(png_path) as img:
        img.save(tiff_path, compression='tiff_lzw', dpi=(DPI, DPI))
    print(f"  Saved: {png_path} + {tiff_path}")
