# SPDX-License-Identifier: GPL-3.0-or-later
"""ScanImage TIFF helper tests."""

import numpy as np
import pytest

from negpy.infrastructure.scanners.plustek.exceptions import PlustekError
from negpy.infrastructure.scanners.plustek.image import ScanImage


def test_save_tiff_requires_optional_dep_or_writes(tmp_path):
    rgb = np.zeros((4, 4, 3), dtype=np.uint16)
    image = ScanImage(rgb=rgb, dpi=3600)
    out = tmp_path / "frame.tif"
    try:
        import tifffile  # noqa: F401
    except ImportError:
        with pytest.raises(PlustekError, match="tiff"):
            image.save_tiff(out)
        return

    path = image.save_tiff(out)
    assert path.exists()
    assert path.suffix == ".tif"


def test_save_tiff_rejects_wrong_shape():
    image = ScanImage(rgb=np.zeros((4, 4), dtype=np.uint16), dpi=900)
    with pytest.raises(PlustekError, match="HxWx3"):
        image.save_tiff("x.tif")
