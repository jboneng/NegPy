# SPDX-License-Identifier: GPL-3.0-or-later
"""Dark / white / shading calibration and on-disk cache."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from negpy.infrastructure.scanners.plustek.device.model_8200i import MODEL_8200I
from negpy.infrastructure.scanners.plustek.device.protocol import AsicDriver, FilmModel
from negpy.infrastructure.scanners.plustek.exceptions import CalibrationError
from negpy.infrastructure.scanners.plustek.logging import get_logger
from negpy.infrastructure.scanners.plustek.scan.geometry import ScanGeometry, compute_calib_geometry, compute_geometry
from negpy.infrastructure.scanners.plustek.scan.pipeline import ImagePipeline

logger = get_logger(__name__)

CACHE_IDENT = "negpy-plustek"
CACHE_VERSION = 1
DARK_SETTLE_S = 0.2
WHITE_SETTLE_S = 0.5


def default_cache_path() -> Path:
    return Path.home() / ".cache" / "negpy-plustek" / "calib_v1.json"


@dataclass
class CalibEntry:
    """One host-side shading result keyed like SANE genesys cache."""

    method: str  # transparency | infrared
    resolution: int
    startx: int
    pixels: int
    dark: np.ndarray  # (pixels, 3) uint16
    white: np.ndarray  # (pixels, 3) uint16
    calibrated_at: float = 0.0
    #: True when this entry was produced by GL128 ASIC shading (skip host stretch).
    asic_shading: bool = False

    def matches(
        self,
        *,
        method: str,
        resolution: int,
        startx: int,
        pixels: int,
    ) -> bool:
        return (
            self.method == method
            and self.resolution == resolution
            and self.startx == startx
            and self.pixels == pixels
        )

    def to_dict(self) -> dict:
        return {
            "method": self.method,
            "resolution": self.resolution,
            "startx": self.startx,
            "pixels": self.pixels,
            "calibrated_at": self.calibrated_at,
            "asic_shading": self.asic_shading,
            "dark": self.dark.astype(np.uint16).ravel().tolist(),
            "white": self.white.astype(np.uint16).ravel().tolist(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> CalibEntry:
        pixels = int(data["pixels"])
        dark = np.asarray(data["dark"], dtype=np.uint16).reshape(pixels, 3)
        white = np.asarray(data["white"], dtype=np.uint16).reshape(pixels, 3)
        return cls(
            method=str(data["method"]),
            resolution=int(data["resolution"]),
            startx=int(data["startx"]),
            pixels=pixels,
            dark=dark,
            white=white,
            calibrated_at=float(data.get("calibrated_at", 0.0)),
            asic_shading=bool(data.get("asic_shading", False)),
        )


class CalibCache:
    """Versioned JSON cache of shading entries."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_cache_path()
        self.entries: list[CalibEntry] = []

    def load(self) -> bool:
        if not self.path.exists():
            self.entries = []
            return False
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CalibrationError(f"Failed to read calib cache: {exc}") from exc
        if raw.get("ident") != CACHE_IDENT or int(raw.get("version", 0)) != CACHE_VERSION:
            logger.warning("Ignoring incompatible calib cache at %s", self.path)
            self.entries = []
            return False
        self.entries = [CalibEntry.from_dict(e) for e in raw.get("entries", [])]
        logger.info("Loaded %d calib entr(y/ies) from %s", len(self.entries), self.path)
        return True

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ident": CACHE_IDENT,
            "version": CACHE_VERSION,
            "entries": [e.to_dict() for e in self.entries],
        }
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        logger.info("Wrote calib cache %s (%d entries)", self.path, len(self.entries))

    def find(
        self,
        *,
        method: str,
        resolution: int,
        startx: int,
        pixels: int,
    ) -> CalibEntry | None:
        for entry in self.entries:
            if entry.matches(
                method=method, resolution=resolution, startx=startx, pixels=pixels
            ):
                return entry
        return None

    def upsert(self, entry: CalibEntry) -> None:
        self.entries = [
            e
            for e in self.entries
            if not e.matches(
                method=entry.method,
                resolution=entry.resolution,
                startx=entry.startx,
                pixels=entry.pixels,
            )
        ]
        self.entries.append(entry)

    def clear(self) -> None:
        self.entries = []
        if self.path.exists():
            self.path.unlink()
            logger.info("Cleared calibration cache %s", self.path)


class Calibrator:
    """Acquire dark/white averages and manage the on-disk cache."""

    def __init__(
        self,
        asic: AsicDriver | None = None,
        *,
        cache_path: Path | None = None,
        model: FilmModel = MODEL_8200I,
    ) -> None:
        self.asic = asic
        self.model = model
        self.cache = CalibCache(cache_path)
        self.cache.load()
        self.pipeline = ImagePipeline(model)
        self._active: CalibEntry | None = None
        #: GL128: after ASIC shading upload, skip host dark/white stretch on scan.
        self.prefer_asic_shading = False

    @property
    def cache_path(self) -> Path | None:
        return self.cache.path

    def should_apply_host_calib(self) -> bool:
        """False when the active entry is ASIC-shading (or prefer flag + ready)."""
        if self._active is not None and self._active.asic_shading:
            return False
        if not self.prefer_asic_shading:
            return True
        asic = self.asic
        return not bool(asic is not None and getattr(asic, "asic_shading_ready", False))

    def load(self) -> bool:
        return self.cache.load()

    def clear(self) -> None:
        self.cache.clear()
        self._active = None

    def find_for_scan(
        self,
        *,
        method: str,
        geometry: ScanGeometry,
    ) -> CalibEntry | None:
        entry = self.cache.find(
            method=method,
            resolution=geometry.resolution,
            startx=geometry.startx,
            pixels=geometry.pixels,
        )
        self._active = entry
        return entry

    def run(
        self,
        *,
        resolution: int = 1800,
        mode: str = "color",
        force: bool = False,
        area: tuple[float, float, float, float] | None = None,
    ) -> CalibEntry:
        """Run dark (if color) + white shading for ``resolution`` / ``mode``."""
        if self.asic is None:
            raise CalibrationError("Calibrator has no ASIC handle")
        if mode == "gray":
            raise ValueError("Grayscale calibration is not implemented")
        if mode not in {"color", "infrared"}:
            raise ValueError(f"Unsupported mode {mode!r}")

        method = "infrared" if mode == "infrared" else "transparency"
        # Match scan geometry keys so apply is 1:1 for full-TA (or given area).
        scan_geo = compute_geometry(resolution, model=self.model, area=area)
        existing = self.cache.find(
            method=method,
            resolution=resolution,
            startx=scan_geo.startx,
            pixels=scan_geo.pixels,
        )
        if existing is not None and not force:
            logger.info(
                "Using cached calib method=%s dpi=%d pixels=%d",
                method,
                resolution,
                scan_geo.pixels,
            )
            self._active = existing
            return existing

        if not self.asic._initialized:
            self.asic.init()

        from negpy.infrastructure.scanners.plustek.scan.session import create_session

        session = create_session(self.asic, self.model)
        calib_geo = compute_calib_geometry(resolution, model=self.model)
        # Force the calib strip to match scan startx/pixels so shading applies
        # 1:1. Everything else — line count, stagger, oversampling — stays as
        # the calibration geometry computed it. USB calib depth stays 16-bit
        # even when the image stream is 8-bit (8200i SE).
        calib_depth = int(getattr(self.model, "usb_calib_depth", calib_geo.depth))
        calib_geo = replace(
            calib_geo,
            pixels=scan_geo.pixels,
            startx=scan_geo.startx,
            pixel_startx=scan_geo.pixel_startx,
            pixel_endx=scan_geo.pixel_endx,
            optical_pixels=scan_geo.optical_pixels,
            output_pixel_offset=scan_geo.output_pixel_offset,
            depth=calib_depth,
            line_bytes=scan_geo.pixels * calib_geo.channels * (calib_depth // 8),
            disable_buffer_full_move=True,
        )

        self.asic.set_scan_method(method)

        is_gl128 = getattr(self.model, "asic", "") == "GL128"

        # SE: stationary AFE dichotomy before host dark/white (motor stays gated).
        if is_gl128:
            search = getattr(self.asic, "search_afe", None)
            if callable(search):
                logger.info("GL128 running stationary AFE search method=%s", method)
                search(method=method)
            shade = getattr(self.asic, "run_asic_shading", None)
            if callable(shade):
                logger.info(
                    "GL128 running stationary ASIC shading dpi=%d method=%s "
                    "window=%d..%d dpiset=%d",
                    resolution,
                    method,
                    scan_geo.pixel_startx,
                    scan_geo.pixel_endx,
                    scan_geo.register_dpiset,
                )
                shade(
                    resolution=resolution,
                    method=method,
                    strpixel=scan_geo.pixel_startx,
                    endpixel=scan_geo.pixel_endx,
                    dpiset=scan_geo.register_dpiset,
                )
                self.prefer_asic_shading = True
                # Marker cache entry: host stretch is skipped while ASIC shading
                # is ready. Values are unused when prefer_asic_shading is set.
                dark = np.zeros((scan_geo.pixels, 3), dtype=np.uint16)
                white = np.full((scan_geo.pixels, 3), 65535, dtype=np.uint16)
                entry = CalibEntry(
                    method=method,
                    resolution=resolution,
                    startx=scan_geo.startx,
                    pixels=scan_geo.pixels,
                    dark=dark,
                    white=white,
                    calibrated_at=time.time(),
                    asic_shading=True,
                )
                self.cache.upsert(entry)
                self.cache.save()
                self._active = entry
                logger.info(
                    "GL128 ASIC shading calib done method=%s dpi=%d (host stretch off)",
                    method,
                    resolution,
                )
                return entry

        def _safe_home() -> None:
            """GL845 repark; SE has no reverse-home — noop only when already home."""
            if is_gl128:
                try:
                    if self.asic.is_at_home():
                        return
                except Exception:  # noqa: BLE001
                    pass
                logger.debug("SE calib: skip home (no capture-proven reverse-home)")
                return
            self.asic.home()

        if mode == "infrared":
            dark = np.zeros((scan_geo.pixels, 3), dtype=np.uint16)
            logger.info("IR calib: skipping dark (genesys behaviour)")
        else:
            dark = self._capture_average(
                session,
                calib_geo,
                method=method,
                lamp_on=False,
                start_motor=False,
                settle_s=DARK_SETTLE_S,
                label="dark",
            )
            _safe_home()

        white = self._capture_average(
            session,
            calib_geo,
            method=method,
            lamp_on=True,
            # SE shading stays put; GL845 white strip may use a short feed.
            start_motor=not is_gl128,
            settle_s=WHITE_SETTLE_S,
            label="white",
        )
        _safe_home()

        entry = CalibEntry(
            method=method,
            resolution=resolution,
            startx=scan_geo.startx,
            pixels=scan_geo.pixels,
            dark=dark,
            white=white,
            calibrated_at=time.time(),
        )
        self.cache.upsert(entry)
        self.cache.save()
        self._active = entry
        logger.info(
            "Calib done method=%s dpi=%d dark_mean=%.0f white_mean=%.0f",
            method,
            resolution,
            float(dark.mean()),
            float(white.mean()),
        )
        return entry

    def _capture_average(
        self,
        session: object,
        geometry: ScanGeometry,
        *,
        method: str,
        lamp_on: bool,
        start_motor: bool,
        settle_s: float,
        label: str,
    ) -> np.ndarray:
        assert self.asic is not None
        from negpy.infrastructure.scanners.plustek.scan.session import ScanSession

        assert isinstance(session, ScanSession)  # Gl128ScanSession is a subclass
        self.asic.set_scan_method(method)
        if lamp_on:
            self.asic.lamp_on()
        else:
            self.asic.lamp_off()
        time.sleep(settle_s)

        raw = session.acquire_raw(
            geometry,
            method=method,
            lamp_on=lamp_on,
            start_motor=start_motor,
        )
        rgb = self.pipeline.assemble(raw, geometry)
        avg = np.median(rgb.astype(np.float32), axis=0)
        out = np.clip(np.rint(avg), 0, 65535).astype(np.uint16)
        logger.info(
            "%s calib average shape=%s mean=%.1f",
            label,
            out.shape,
            float(out.mean()),
        )
        return out
