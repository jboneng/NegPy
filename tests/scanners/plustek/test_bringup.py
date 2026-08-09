# SPDX-License-Identifier: GPL-3.0-or-later
"""SE preview_safe / Full-window geometry (no hardware)."""

from __future__ import annotations

from negpy.infrastructure.scanners.plustek.device.model_8200i_se import MODEL_8200I_SE
from negpy.infrastructure.scanners.plustek.scan.bringup import (
    bringup_scan_geometry,
    preview_safe_scan_area,
)


def test_preview_safe_area_1200_uses_window_top_feed2():
    area, meta = preview_safe_scan_area(MODEL_8200I_SE, 1200, y1=0.0)
    assert area[0] == 0.0 and area[2] == 1.0
    assert meta["feed2"] == MODEL_8200I_SE.feed_to_scan_top_steps == 13128
    assert meta["target_lincnt"] == 4836
    assert meta["max_lincnt"] == 4836


def test_bringup_preview_safe_passes_motor_gate_at_1200():
    geometry, meta = bringup_scan_geometry(MODEL_8200I_SE, 1200, profile="preview_safe")
    feed2 = int(meta["feed2"])
    assert feed2 == 13128
    assert geometry.lincnt_register == 4836
    max_lc = MODEL_8200I_SE.max_lincnt_for(feed2, 1200)
    assert geometry.lincnt_register <= max_lc


def test_default_full_frame_without_bringup_trips_gate_at_1200():
    """Regression: area=None + feed2=13704 cannot fit full TA height."""
    from negpy.infrastructure.scanners.plustek.scan.geometry import compute_geometry

    geometry = compute_geometry(1200, model=MODEL_8200I_SE, area=None)
    feed2 = MODEL_8200I_SE.feed_to_scan_steps_for_area(None)
    assert feed2 == 13704
    max_lc = MODEL_8200I_SE.max_lincnt_for(feed2, 1200)
    assert geometry.lincnt_register > max_lc
