# SPDX-License-Identifier: GPL-3.0-or-later
"""Whole-pixel IR↔RGB registration for two-pass Plustek USB scans.

The carriage re-homes (AGOHOME) between colour and IR, so the IR plane can land
a few pixels off the visible frame. Same algorithm as the SANE source-IR path,
kept local so the USB backend does not depend on python-sane.
"""

from __future__ import annotations

import cv2
import numpy as np

# Downsample width for the phase-correlation probe.
_IR_ALIGN_PROBE_WIDTH = 1024
# Correlation-failure guard: past this the estimate is noise, not a real offset.
_IR_ALIGN_MAX_SHIFT_FRAC = 0.02


def align_ir_to_rgb(rgb: np.ndarray, ir: np.ndarray) -> np.ndarray:
    """Register a separately-scanned IR plane onto the RGB frame by a whole-pixel shift.

    Whole pixels only — no sub-pixel interpolation — so dust minima in the IR
    ratio stay sharp for retouch.
    """
    if ir.size == 0 or rgb.shape[:2] != ir.shape[:2]:
        return ir
    ref = rgb.astype(np.float32)
    if ref.ndim == 3:
        ref = ref.mean(axis=2)
    mov = ir.astype(np.float32)
    if mov.ndim == 3:
        mov = mov[:, :, 0]
    h, w = ref.shape[:2]
    scale = max(1.0, w / _IR_ALIGN_PROBE_WIDTH)
    if scale > 1.0:
        sz = (_IR_ALIGN_PROBE_WIDTH, max(1, round(h / scale)))
        r = cv2.resize(ref, sz, interpolation=cv2.INTER_AREA)
        m = cv2.resize(mov, sz, interpolation=cv2.INTER_AREA)
    else:
        r, m = ref, mov
    win = cv2.createHanningWindow((r.shape[1], r.shape[0]), cv2.CV_32F)
    (dx, dy), _resp = cv2.phaseCorrelate(np.ascontiguousarray(r), np.ascontiguousarray(m), win)
    dx, dy = dx * scale, dy * scale
    if max(abs(dx), abs(dy)) > max(16.0, _IR_ALIGN_MAX_SHIFT_FRAC * w):
        return ir
    ix, iy = int(round(dx)), int(round(dy))
    if ix == 0 and iy == 0:
        return ir
    x_idx = np.clip(np.arange(w) + ix, 0, w - 1)
    y_idx = np.clip(np.arange(h) + iy, 0, h - 1)
    return ir[y_idx][:, x_idx]
