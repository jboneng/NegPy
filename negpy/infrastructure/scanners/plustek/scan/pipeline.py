# SPDX-License-Identifier: GPL-3.0-or-later
"""Host-side image reconstruction (RGB16, shifts, stagger, shading)."""

from __future__ import annotations

import numpy as np

from negpy.infrastructure.scanners.plustek.device.model_8200i import MODEL_8200I
from negpy.infrastructure.scanners.plustek.device.protocol import FilmModel
from negpy.infrastructure.scanners.plustek.logging import get_logger
from negpy.infrastructure.scanners.plustek.scan.geometry import ScanGeometry

logger = get_logger(__name__)

#: After dark/white stretch, push frame highlights toward sensor white when the
#: home-chrome white reference is brighter than anything in the film window.
#: NegPy metering expects film base near full scale on a negative scan.
HOST_CALIB_PEAK_PERCENTILE = 99.7
HOST_CALIB_PEAK_TARGET = 0xF000
HOST_CALIB_PEAK_TRIGGER = 0.85
#: Drop this fraction from each edge when estimating film highlights / clamping
#: holder chrome so NegPy auto bounds do not latch onto Full-window margins.
HOST_CALIB_BORDER_INSET = 0.04


class ImagePipeline:
    """Convert raw scanner bytes into HxWx3 uint16 RGB."""

    def __init__(self, model: FilmModel = MODEL_8200I) -> None:
        self.model = model

    @staticmethod
    def _inset_slice(h: int, w: int, inset: float) -> tuple[slice, slice] | None:
        """Return ``(ys, xs)`` for a centered inset, or ``None`` if too small."""
        frac = min(max(float(inset), 0.0), 0.2)
        if frac <= 0 or h < 8 or w < 8:
            return None
        cut_h = max(1, int(round(h * frac)))
        cut_w = max(1, int(round(w * frac)))
        if cut_h * 2 >= h or cut_w * 2 >= w:
            return None
        return slice(cut_h, h - cut_h), slice(cut_w, w - cut_w)

    def decode_rgb(
        self,
        raw: bytes,
        *,
        geometry: ScanGeometry,
        planar: bool | None = None,
    ) -> np.ndarray:
        """Decode USB RGB optical buffer → uint16 HxWx3.

        8-bit USB image streams (8200i SE colour/IR) are upsampled to 16-bit
        host samples (``value * 257``). Calib/shading buffers stay native 16-bit.

        Layout is model-dependent: GL845 and 8200i SE film images are chunky
        ``RGBRGB…`` per line (SE session 11). Pass ``planar`` to override.
        AFE strip probes may differ; do not confuse them with image layout.
        """
        expected = geometry.total_bytes
        if len(raw) < expected:
            raise ValueError(f"Short scan buffer: got {len(raw)} want {expected}")
        h = geometry.optical_line_count
        w = geometry.pixels
        c = geometry.channels
        if planar is None:
            planar = bool(getattr(self.model, "usb_planar_rgb", False))
        if geometry.depth == 8:
            flat = np.frombuffer(raw[:expected], dtype=np.uint8)
            arr = flat.reshape(h, c, w).transpose(0, 2, 1) if planar else flat.reshape(h, w, c)
            return (arr.astype(np.uint16) * np.uint16(257)).copy()
        if geometry.depth != 16:
            raise ValueError(f"Unsupported geometry depth {geometry.depth}")
        flat = np.frombuffer(raw[:expected], dtype="<u2")
        arr = flat.reshape(h, c, w).transpose(0, 2, 1) if planar else flat.reshape(h, w, c)
        return np.array(arr, dtype=np.uint16, copy=True)

    def decode_rgb16(
        self,
        raw: bytes,
        *,
        geometry: ScanGeometry,
        planar: bool | None = None,
    ) -> np.ndarray:
        """Backward-compatible alias for :meth:`decode_rgb`."""
        return self.decode_rgb(raw, geometry=geometry, planar=planar)

    def reduce_y_oversample(self, rgb: np.ndarray, geometry: ScanGeometry) -> np.ndarray:
        """Average USB rows down to ``geometry.lines``.

        Only the group size actually present in the buffer is collapsed:
        ``optical_line_count // lines``. The 8200i SE image path samples Y at
        twice the programmed dpi and delivers ``LINCNT/2`` rows for ``LINCNT/4``
        lines, so pairs are averaged here — without it the image comes out
        stretched 2x vertically. Shading passes average ``y_oversample`` rows
        because their ``LINCNT`` is in native units.
        """
        if geometry.lines <= 0:
            return rgb
        n = geometry.optical_line_count // geometry.lines
        if n <= 1:
            return rgb

        height, width, channels = rgb.shape
        groups = height // n
        if groups < 1:
            raise ValueError(
                f"Buffer of {height} rows is shorter than one {n}-row group"
            )
        trimmed = rgb[: groups * n].reshape(groups, n, width, channels)
        out = trimmed.mean(axis=1)
        logger.debug("averaged %d rows -> %d (oversample=%d)", height, groups, n)
        return np.rint(out).astype(np.uint16)

    def apply_host_downsample(self, rgb: np.ndarray, geometry: ScanGeometry) -> np.ndarray:
        """Block-average when the ASIC ran hotter than the requested PPI (SE <600)."""
        factor = int(getattr(geometry, "host_downsample", 1) or 1)
        if factor <= 1:
            return rgb
        h, w, c = rgb.shape
        nh = (h // factor) * factor
        nw = (w // factor) * factor
        if nh == 0 or nw == 0:
            return rgb
        block = rgb[:nh, :nw].reshape(nh // factor, factor, nw // factor, factor, c)
        out = block.mean(axis=(1, 3))
        logger.debug(
            "host downsample %dx%d -> %dx%d (factor=%d)",
            h,
            w,
            out.shape[0],
            out.shape[1],
            factor,
        )
        return np.rint(out).astype(np.uint16)

    def apply_line_shifts(self, rgb: np.ndarray, geometry: ScanGeometry) -> np.ndarray:
        """Align R/G/B using genesys ld_shift scaled to yres.

        Height is ``lines + num_staggered_lines`` when the buffer carries the
        extra ``max_shift`` lines (GL845). Models that size ``LINCNT`` for the
        crop alone — the 8200i SE, which has no travel to spare — lose
        ``max_shift`` lines off the bottom instead.
        """
        shifts = (geometry.shift_r, geometry.shift_g, geometry.shift_b)
        out_h = geometry.lines + geometry.num_staggered_lines
        if max(shifts) == 0:
            return rgb[:out_h].copy()

        height, width, channels = rgb.shape
        assert channels == 3
        out_h = min(out_h, height - max(shifts))
        if out_h < 1:
            raise ValueError("Optical buffer shorter than required after shift")
        out = np.zeros((out_h, width, 3), dtype=np.uint16)
        for c, shift in enumerate(shifts):
            out[:, :, c] = rgb[shift : shift + out_h, :, c]
        logger.debug("applied line shifts r/g/b=%s", shifts)
        return out

    def apply_y_stagger(self, rgb: np.ndarray, geometry: ScanGeometry) -> np.ndarray:
        """Unstagger alternating columns (7200 dpi ``StaggerConfig{4,0}``)."""
        shifts = geometry.stagger_y
        if not shifts or geometry.num_staggered_lines == 0:
            return rgb[: geometry.lines]

        height, width, channels = rgb.shape
        assert channels == 3
        if height < geometry.lines + geometry.num_staggered_lines:
            raise ValueError("Buffer shorter than required for Y stagger")

        out = np.empty((geometry.lines, width, 3), dtype=np.uint16)
        n = len(shifts)
        for x in range(width):
            shift = shifts[x % n]
            out[:, x, :] = rgb[shift : shift + geometry.lines, x, :]
        logger.debug("applied y stagger shifts=%s", shifts)
        return out

    def clamp_host_calib_border_highlights(
        self,
        rgb: np.ndarray,
        *,
        inset: float = HOST_CALIB_BORDER_INSET,
        peak_percentile: float = HOST_CALIB_PEAK_PERCENTILE,
    ) -> np.ndarray:
        """Pull Full-window holder chrome down to the film-window highlight peak.

        NegPy auto Dmin/bounds treat near-white negative margins as film base;
        chrome brighter than the framed film makes the positive too dark until
        the user crops. Interior pixels are unchanged.
        """
        if rgb.ndim != 3 or rgb.shape[2] != 3:
            raise ValueError(f"rgb must be HxWx3, got {rgb.shape}")
        h, w, _ = rgb.shape
        region = self._inset_slice(h, w, inset)
        if region is None:
            return rgb
        ys, xs = region
        inset_peak = float(np.percentile(rgb[ys, xs], float(peak_percentile)))
        if inset_peak <= 0:
            return rgb
        border = np.ones((h, w), dtype=bool)
        border[ys, xs] = False
        if not border.any():
            return rgb
        out = rgb.astype(np.float32, copy=True)
        hot = border & (out.max(axis=2) > inset_peak)
        if not hot.any():
            return rgb
        out[hot] = np.minimum(out[hot], inset_peak)
        n_hot = int(hot.sum())
        logger.info(
            "host calib border highlight clamp inset=%.2f peak_p%.1f=%.0f pixels=%d",
            float(inset),
            float(peak_percentile),
            inset_peak,
            n_hot,
        )
        return np.clip(np.rint(out), 0, 65535).astype(np.uint16)

    def apply_host_calib(
        self,
        rgb: np.ndarray,
        *,
        dark: np.ndarray,
        white: np.ndarray,
        peak_target: int = HOST_CALIB_PEAK_TARGET,
        peak_percentile: float = HOST_CALIB_PEAK_PERCENTILE,
    ) -> np.ndarray:
        """Host-side shading: column flat-field, then expose film base near white.

        ``dark`` / ``white`` are (pixels, 3) uint16 column averages from the home
        chrome strip. Mapping that strip to 65535 leaves the film window dark when
        scan-position light is lower than home — NegPy then meters a thin-looking
        positive. A percentile makeup brings frame highlights up to ``peak_target``.
        Border chrome brighter than the film inset is then clamped so auto bounds
        do not latch onto holder margins.
        """
        if dark.shape != white.shape:
            raise ValueError(f"dark/white shape mismatch: {dark.shape} vs {white.shape}")
        if rgb.ndim != 3 or rgb.shape[2] != 3:
            raise ValueError(f"rgb must be HxWx3, got {rgb.shape}")
        if dark.shape[0] < rgb.shape[1] or dark.shape[1] != 3:
            raise ValueError(
                f"calib width {dark.shape} incompatible with image width {rgb.shape[1]}"
            )

        dark_f = dark[: rgb.shape[1]].astype(np.float32)
        white_f = white[: rgb.shape[1]].astype(np.float32)
        denom = white_f - dark_f
        # Avoid div0 / inverted ranges
        bad = denom <= 0
        denom = np.where(bad, 1.0, denom)
        offset = dark_f / 65535.0
        mult = 65535.0 / denom

        img = rgb.astype(np.float32) / 65535.0
        out = (img - offset) * mult
        out = np.clip(out * 65535.0, 0, 65535)
        if bad.any():
            # Leave original where calib is invalid
            mask = np.broadcast_to(bad, rgb.shape)
            out = np.where(mask, rgb.astype(np.float32), out)

        target = int(peak_target)
        if target > 0:
            h, w, _ = out.shape
            region = self._inset_slice(h, w, HOST_CALIB_BORDER_INSET)
            sample = out[region[0], region[1]] if region is not None else out
            peak = float(np.percentile(sample, float(peak_percentile)))
            trigger = float(target) * float(HOST_CALIB_PEAK_TRIGGER)
            if peak > 1.0 and peak < trigger:
                gain = float(target) / peak
                out = np.clip(out * gain, 0, 65535)
                logger.info(
                    "host calib exposure makeup gain=%.3f peak_p%.1f=%.0f → %d",
                    gain,
                    float(peak_percentile),
                    peak,
                    target,
                )

        stretched = np.clip(np.rint(out), 0, 65535).astype(np.uint16)
        return self.clamp_host_calib_border_highlights(
            stretched, peak_percentile=peak_percentile
        )

    def assemble(
        self,
        raw: bytes,
        geometry: ScanGeometry,
        *,
        dark: np.ndarray | None = None,
        white: np.ndarray | None = None,
        planar: bool | None = None,
    ) -> np.ndarray:
        rgb = self.decode_rgb(raw, geometry=geometry, planar=planar)
        rgb = self.reduce_y_oversample(rgb, geometry)
        rgb = self.apply_line_shifts(rgb, geometry)
        rgb = self.apply_y_stagger(rgb, geometry)
        rgb = self.apply_host_downsample(rgb, geometry)
        if dark is not None and white is not None:
            rgb = self.apply_host_calib(rgb, dark=dark, white=white)
        if getattr(self.model, "mirror_x", False):
            rgb = np.ascontiguousarray(rgb[:, ::-1, :])
        return rgb
