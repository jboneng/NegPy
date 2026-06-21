"""Camera-native → ProPhoto RGB colour conversion for the flat master.

Used only by the flat ("for editing elsewhere") export path to give the digital
intermediate a meaningful wide-gamut colour space derived from the camera's own
characterization, instead of the pipeline's default "assume Adobe RGB" relabel.
The print pipeline never calls into this module.
"""

from typing import Optional

import numpy as np

# Linear XYZ (D50) → ProPhoto (ROMM) RGB. ProPhoto is defined relative to D50,
# which is also the DNG/ICC PCS white, so no separate adaptation step is folded
# in here — the neutral axis is pinned by row-normalising the combined matrix.
_XYZ_D50_TO_PROPHOTO = np.array(
    [
        [1.3459433, -0.2556075, -0.0511118],
        [-0.5445989, 1.5081673, 0.0205351],
        [0.0000000, 0.0000000, 1.2118128],
    ],
    dtype=np.float64,
)


def camera_to_prophoto_matrix(rgb_xyz_matrix: object) -> Optional[np.ndarray]:
    """Build a 3×3 camera-native-linear → ProPhoto-linear matrix.

    ``rgb_xyz_matrix`` is rawpy's ``RawPy.rgb_xyz_matrix`` (LibRaw ``cam_xyz``),
    a 4×3 **XYZ→camera** matrix despite its name. We take the 3×3 RGB submatrix,
    invert it to get camera→XYZ, map XYZ→ProPhoto, then normalise each row so a
    neutral camera value (equal RGB, after camera white balance) maps to a neutral
    ProPhoto value. That row-normalisation pins the achromatic axis regardless of
    the matrix's illuminant convention — the dominant error mode for a neutral
    master — while the camera primaries still shape saturated hues.

    Returns ``None`` when the matrix is missing or degenerate (e.g. non-camera
    files such as scanner TIFFs or NegPy's own linear DNGs), so callers can fall
    back to the default behaviour.
    """
    if rgb_xyz_matrix is None:
        return None
    m = np.asarray(rgb_xyz_matrix, dtype=np.float64)
    if m.ndim != 2 or m.shape[1] != 3 or m.shape[0] < 3:
        return None
    m = m[:3, :3]
    if not np.isfinite(m).all() or abs(np.linalg.det(m)) < 1e-8:
        return None

    cam_to_xyz = np.linalg.inv(m)
    cam_to_prophoto = _XYZ_D50_TO_PROPHOTO @ cam_to_xyz

    row_sums = cam_to_prophoto.sum(axis=1, keepdims=True)
    if not np.isfinite(row_sums).all() or np.any(np.abs(row_sums) < 1e-8):
        return None
    cam_to_prophoto = cam_to_prophoto / row_sums  # neutral camera → neutral ProPhoto

    return cam_to_prophoto.astype(np.float32)


def apply_camera_to_prophoto(buffer: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Apply a camera→ProPhoto matrix to a linear float (H, W, 3) buffer.

    Negative (out-of-gamut / imaginary) results are clamped to 0; the upper end is
    left unbounded so the downstream normalization, not a hard clip here, decides
    the white point.
    """
    out = buffer @ matrix.T
    return np.clip(out, 0.0, None).astype(np.float32)
