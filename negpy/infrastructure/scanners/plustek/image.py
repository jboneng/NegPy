# SPDX-License-Identifier: GPL-3.0-or-later
"""Scan buffer helpers and optional TIFF export."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from negpy.infrastructure.scanners.plustek.exceptions import PlustekError


@dataclass
class ScanImage:
    """Host-side scan result.

    ``rgb`` is HxWx3 uint16 linear data (colour or infrared-illuminated CCD
    frame). ``ir`` is an optional HxW uint16 plane for callers that extract
    one; this driver does not populate or enhance it — iSRD post-process
    belongs in the application. ``save_tiff`` writes only ``rgb``.
    """

    rgb: np.ndarray
    dpi: int
    device_model: str = "PLUSTEK OpticFilm 8200i"
    ir: np.ndarray | None = None

    def save_tiff(self, path: str | Path) -> Path:
        """Write a 16-bit RGB TIFF (the scanned frame only; no ``*_IR`` sidecar)."""
        try:
            import tifffile
        except ImportError as exc:
            raise PlustekError("TIFF export requires tifffile") from exc

        out = Path(path)
        if out.suffix.lower() not in {".tif", ".tiff"}:
            out = out.with_suffix(".tif")

        rgb = np.asarray(self.rgb)
        if rgb.dtype != np.uint16:
            raise PlustekError(f"rgb must be uint16, got {rgb.dtype}")
        if rgb.ndim != 3 or rgb.shape[2] != 3:
            raise PlustekError(f"rgb must be HxWx3, got shape {rgb.shape}")

        tifffile.imwrite(
            out,
            rgb,
            photometric="rgb",
            compression="zlib",
            predictor=True,
            resolution=(self.dpi, self.dpi),
            resolutionunit="inch",
        )

        return out
