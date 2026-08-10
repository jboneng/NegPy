# SPDX-License-Identifier: GPL-3.0-or-later
"""Prescan crop coordinate helpers (mirror_x)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from negpy.infrastructure.scanners.plustek.device.model_8200i_se import MODEL_8200I_SE
from negpy.infrastructure.scanners.plustek.scan.bringup import (
    PRESCAN_DPI,
    clamp_area,
    default_frame_crop_norm,
    image_crop_to_scan_area,
    scan_area_to_image_crop,
)


def test_prescan_dpi_is_1200() -> None:
    assert PRESCAN_DPI == 1200


def test_image_crop_flips_x_when_mirror() -> None:
    crop = (0.1, 0.2, 0.4, 0.8)
    ta = image_crop_to_scan_area(MODEL_8200I_SE, crop)
    assert ta == (0.6, 0.2, 0.9, 0.8)
    back = scan_area_to_image_crop(MODEL_8200I_SE, ta)
    assert back == pytest.approx(clamp_area(crop))


def test_image_crop_no_flip_without_mirror() -> None:
    model = SimpleNamespace(mirror_x=False)
    crop = (0.1, 0.2, 0.4, 0.8)
    assert image_crop_to_scan_area(model, crop) == clamp_area(crop)


def test_default_frame_crop_is_in_unit_square() -> None:
    area = default_frame_crop_norm(MODEL_8200I_SE)
    x1, y1, x2, y2 = area
    assert 0.0 <= x1 < x2 <= 1.0
    assert 0.0 <= y1 < y2 <= 1.0
