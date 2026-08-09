# SPDX-License-Identifier: GPL-3.0-or-later
"""Scan state machine: configure → acquire → assemble."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from negpy.infrastructure.scanners.plustek.asic.motor import (
    SLOPE_TABLE_AHB,
    calculate_zmod,
    create_fast_slope_table,
    create_scan_slope_table,
    slope_table_to_bytes,
)
from negpy.infrastructure.scanners.plustek.asic.registers import Gl845Registers
from negpy.infrastructure.scanners.plustek.device.model_8200i import MODEL_8200I
from negpy.infrastructure.scanners.plustek.device.protocol import AsicDriver, FilmModel
from negpy.infrastructure.scanners.plustek.exceptions import MotorTimeoutError, ScanCancelled, ScanError
from negpy.infrastructure.scanners.plustek.image import ScanImage
from negpy.infrastructure.scanners.plustek.logging import get_logger
from negpy.infrastructure.scanners.plustek.scan.calibrate import Calibrator
from negpy.infrastructure.scanners.plustek.scan.geometry import ScanGeometry, compute_geometry
from negpy.infrastructure.scanners.plustek.scan.pipeline import ImagePipeline

logger = get_logger(__name__)

FEED_TIMEOUT_S = 60.0
DATA_TIMEOUT_S = 30.0
BULK_CHUNK_LINES = 8


class ScanSession:
    """Owns one color/IR transparency scan from configure → TIFF-ready buffer."""

    def __init__(
        self,
        asic: AsicDriver,
        model: FilmModel = MODEL_8200I,
        calibrator: Calibrator | None = None,
    ) -> None:
        self.asic = asic
        self.model = model
        self.pipeline = ImagePipeline(model)
        self.regs = Gl845Registers()
        self.calibrator = calibrator
        self._lamp_requested = True

    def run(
        self,
        *,
        resolution: int = 1800,
        mode: str = "color",
        area: tuple[float, float, float, float] | None = None,
        geometry: ScanGeometry | None = None,
        progress: Callable[[float], None] | None = None,
        cancel: threading.Event | None = None,
        apply_calib: bool = True,
    ) -> ScanImage:
        if mode == "gray":
            raise ValueError("Grayscale is experimental / not implemented yet")
        if mode not in {"color", "infrared"}:
            raise ValueError(f"Unsupported mode {mode!r} (color|infrared)")
        if mode == "infrared" and not self.model.supports_infrared:
            raise ValueError(f"{self.model.model} does not support infrared")

        if not self.asic._initialized:
            self.asic.init()

        if geometry is None:
            geometry = compute_geometry(resolution, model=self.model, area=area)
        method = "infrared" if mode == "infrared" else "transparency"
        logger.info(
            "scan %sdpi %s pixels=%d lines=%d starty=%d bytes=%d stagger=%d",
            geometry.resolution,
            mode,
            geometry.pixels,
            geometry.lines,
            geometry.starty,
            geometry.total_bytes,
            geometry.num_staggered_lines,
        )

        calib = None
        if apply_calib and self.calibrator is not None:
            if method == "transparency":
                # SilverFast order: home measure/apply before any image feed.
                calib = self.calibrator.ensure_colour_asic_shading(geometry)
            else:
                calib = self.calibrator.find_for_scan(method=method, geometry=geometry)
                if calib is None:
                    logger.warning(
                        "No calib cache for method=%s dpi=%d — scanning uncalibrated.",
                        method,
                        geometry.resolution,
                    )

        raw = self.acquire_raw(
            geometry,
            method=method,
            lamp_on=True,
            start_motor=True,
            progress=progress,
            cancel=cancel,
        )

        use_host = (
            calib is not None
            and self.calibrator is not None
            and self.calibrator.should_apply_host_calib()
        )
        dark = calib.dark if use_host else None
        white = calib.white if use_host else None
        if calib is not None and not use_host:
            logger.info("Using ASIC shading; skipping host dark/white stretch")
        # Prefer the model USB layout (SE film = chunky). ``asic.usb_planar_rgb``
        # is kept in sync with the model after AFE; Comm-test may override it
        # after a scored decode.
        planar = getattr(self.asic, "usb_planar_rgb", None)
        if planar is None:
            planar = bool(getattr(self.model, "usb_planar_rgb", False))
        rgb = self.pipeline.assemble(
            raw, geometry, dark=dark, white=white, planar=bool(planar)
        )

        # Infrared: still RGB-framed CCD data in ``rgb``. Host iSRD enhance /
        # flatten is left to applications — negpy.infrastructure.scanners.plustek does not populate ``ir``.
        return ScanImage(
            rgb=rgb,
            dpi=geometry.resolution,
            device_model=f"{self.model.vendor} {self.model.model}",
            ir=None,
        )

    def acquire_raw(
        self,
        geometry: ScanGeometry,
        *,
        method: str,
        lamp_on: bool,
        start_motor: bool,
        progress: Callable[[float], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> bytes:
        """Configure ASIC, run one capture, return raw optical bytes."""
        self.asic.set_scan_method(method)  # type: ignore[arg-type]
        # Recorded for subclasses that must reflect lamp state in the register
        # block they write during _configure.
        self._lamp_requested = lamp_on
        if lamp_on:
            self.asic.lamp_on()
        else:
            self.asic.lamp_off()

        try:
            self._configure(geometry)
            self._begin_scan(start_motor=start_motor)
            return self._acquire(
                geometry,
                progress=progress,
                cancel=cancel,
                wait_feed=start_motor and getattr(self, "_feedl", 0) > 0,
            )
        finally:
            self._end_scan()

    # --- configure ------------------------------------------------------

    def _configure(self, geometry: ScanGeometry) -> None:
        proto = self.asic.protocol
        r = self.regs
        if self.asic._reg_cache:
            cache = dict(self.asic._reg_cache)
        else:
            cache = self.model.boot_register_map()

        for addr, value in self.model.sensor_custom_regs.items():
            cache[addr] = value

        self.asic.set_frontend_init()

        # Optical: SHDAREA on, DVDSET off (host-side calib), SCAN off
        cache[0x01] = (cache.get(0x01, 0x22) | r.SHDAREA) & ~r.DVDSET & ~r.SCAN

        if self.asic._scan_method == "infrared":
            cache[0x03] = cache.get(0x03, 0xBF) & ~r.LAMPPWR
        else:
            cache[0x03] = cache.get(0x03, 0xBF) | r.LAMPPWR
        cache[0x03] = cache[0x03] & ~0x40  # clear AVEENB

        # 0x04: 16-bit color — BITSET, AFEMOD=2, FESET=2
        cache[0x04] = (cache.get(0x04, 0x22) & ~0x8C) | 0x40 | 0x20

        # dpihw 1200, clear gamma
        cache[0x05] = (cache.get(0x05, 0x48) & ~0xC0 & ~0x08) | 0x40

        cache[0x2E] = 0x7F
        cache[0x2F] = 0x7F

        self._set16(cache, 0x2C, geometry.register_dpiset)
        self._set16(cache, 0x30, geometry.pixel_startx)
        self._set16(cache, 0x32, geometry.pixel_endx)
        self._set16(cache, 0x38, geometry.exposure_lperiod)
        cache[0x34] = geometry.dummy_pixel
        # SANE MAXWD quirk: (line_bytes * channels) >> 2
        maxwd = (geometry.line_bytes * geometry.channels) >> 2
        self._set24(cache, 0x35, maxwd)
        self._set24(cache, 0x25, geometry.optical_line_count)

        step_mult = 1
        mp = self.model.motor_profile
        scan_slope = create_scan_slope_table(
            ydpi=geometry.resolution,
            exposure=geometry.exposure_lperiod,
            base_ydpi=self.model.motor_base_ydpi,
            step_multiplier=step_mult,
            profile=mp,
        )
        fast_slope = create_fast_slope_table(step_multiplier=step_mult, profile=mp)

        cache[0x02] = r.MTRPWR
        if geometry.disable_buffer_full_move:
            cache[0x02] |= r.ACDCDIS

        n_scan = len(scan_slope.table) // step_mult
        n_fast = len(fast_slope.table) // step_mult
        cache[0x21] = n_scan & 0xFF
        cache[0x24] = n_scan & 0xFF
        cache[0x69] = n_scan & 0xFF
        cache[0x6A] = n_fast & 0xFF
        cache[0x5F] = n_fast & 0xFF

        feed_steps = geometry.starty
        feedl = feed_steps << mp.step_type
        dist = len(scan_slope.table)
        feedl = feedl - dist if dist < feedl else 0
        self._set24(cache, 0x3D, feedl)

        min_restep = max(1, (len(scan_slope.table) // step_mult) // 2 - 1)
        cache[0x22] = min_restep & 0xFF
        cache[0x23] = min_restep & 0xFF

        ccdlmt = (cache.get(0x0C, 0) & 0x0F) + 1
        tgtime = 1 << (cache.get(0x1C, 0) & 0x07)
        exposure_mod = geometry.exposure_lperiod * ccdlmt * tgtime
        z1, z2 = calculate_zmod(
            exposure_time=exposure_mod,
            slope_table=scan_slope.table,
            acceleration_steps=len(scan_slope.table),
            move_steps=feedl,
            buffer_acceleration_steps=min_restep * step_mult,
        )
        step_sel = mp.step_type << 5
        self._set24(cache, 0x60, (z1 & 0xFFFF) | (step_sel << 16))
        self._set24(cache, 0x63, (z2 & 0xFFFF) | (step_sel << 16))

        cache[0x1E] = (cache.get(0x1E, 0xF0) & 0xF0) | 0x00
        cache[0x67] = 0x7F
        cache[0x68] = 0x7F

        vref = (
            (mp.vref << 0)
            | (mp.vref << 2)
            | (mp.vref << 4)
            | (mp.vref << 6)
        )
        cache[0x80] = vref & 0xFF

        pairs = [(a, v) for a, v in sorted(cache.items()) if a != 0x0B]
        for addr, value in pairs:
            proto.write_register(addr, value)
        self.asic._reg_cache = cache

        payload = slope_table_to_bytes(scan_slope.table)
        fast_payload = slope_table_to_bytes(fast_slope.table)
        for table_nr in (0, 1, 2):
            proto.write_ahb(SLOPE_TABLE_AHB[table_nr], payload)
        for table_nr in (3, 4):
            proto.write_ahb(SLOPE_TABLE_AHB[table_nr], fast_payload)

        self._feedl = feedl
        logger.info(
            "configured dpiset=%d str=%d end=%d lperiod=%d lincnt=%d feedl=%d slopes=%d",
            geometry.register_dpiset,
            geometry.pixel_startx,
            geometry.pixel_endx,
            geometry.exposure_lperiod,
            geometry.optical_line_count,
            feedl,
            len(scan_slope.table),
        )

    @staticmethod
    def _set16(cache: dict[int, int], addr: int, value: int) -> None:
        value &= 0xFFFF
        cache[addr] = (value >> 8) & 0xFF
        cache[addr + 1] = value & 0xFF

    @staticmethod
    def _set24(cache: dict[int, int], addr: int, value: int) -> None:
        value &= 0xFFFFFF
        cache[addr] = (value >> 16) & 0xFF
        cache[addr + 1] = (value >> 8) & 0xFF
        cache[addr + 2] = value & 0xFF

    # --- acquire --------------------------------------------------------

    def _begin_scan(self, *, start_motor: bool = True) -> None:
        proto = self.asic.protocol
        r = self.regs
        proto.write_register(0x0D, 0x05)  # CLRLNCNT | CLRMCNT
        reg01 = proto.read_register(0x01) | r.SCAN
        proto.write_register(0x01, reg01)
        self.asic._reg_cache[0x01] = reg01
        proto.write_register(0x0F, 0x01 if start_motor else 0x00)
        self.asic.update_home_sensor_gpio()
        logger.info("scan started motor=%s", start_motor)

    def _end_scan(self) -> None:
        try:
            self.asic.stop_motor()
        except Exception as exc:  # noqa: BLE001
            logger.warning("end_scan stop_motor: %s", exc)

    def _read_feed_steps(self) -> int:
        proto = self.asic.protocol
        steps = proto.read_register(0x4A)
        steps += proto.read_register(0x49) * 256
        steps += (proto.read_register(0x48) & 0x1F) * 256 * 256
        return steps

    def _read_valid_words(self) -> int:
        proto = self.asic.protocol
        words = proto.read_register(0x42) & 0x02
        words = words * 256 + proto.read_register(0x43)
        words = words * 256 + proto.read_register(0x44)
        words = words * 256 + proto.read_register(0x45)
        return words

    def _wait_feed(self, cancel: threading.Event | None) -> None:
        deadline = time.monotonic() + FEED_TIMEOUT_S
        target = getattr(self, "_feedl", 0)
        while time.monotonic() < deadline:
            if cancel is not None and cancel.is_set():
                raise ScanCancelled("cancelled during feed")
            if self._read_feed_steps() >= target:
                logger.debug("feed complete (%d)", target)
                return
            time.sleep(0.05)
        raise MotorTimeoutError(f"Feed did not complete within {FEED_TIMEOUT_S:.0f}s")

    def _wait_data(self, cancel: threading.Event | None) -> None:
        deadline = time.monotonic() + DATA_TIMEOUT_S
        while time.monotonic() < deadline:
            if cancel is not None and cancel.is_set():
                raise ScanCancelled("cancelled waiting for data")
            status = self.asic.read_status()
            if not status.is_buffer_empty and self._read_valid_words() >= 1:
                return
            time.sleep(0.02)
        raise ScanError(f"No scan data within {DATA_TIMEOUT_S:.0f}s")

    def _acquire(
        self,
        geometry: ScanGeometry,
        *,
        progress: Callable[[float], None] | None,
        cancel: threading.Event | None,
        wait_feed: bool = True,
    ) -> bytes:
        if wait_feed:
            self._wait_feed(cancel)
        self._wait_data(cancel)

        total = geometry.total_bytes
        chunk = geometry.line_bytes * BULK_CHUNK_LINES
        buf = bytearray()
        proto = self.asic.protocol

        while len(buf) < total:
            if cancel is not None and cancel.is_set():
                raise ScanCancelled("cancelled during bulk read")
            need = min(chunk, total - len(buf))
            data = proto.bulk_read_data(need)
            if not data:
                raise ScanError("Empty bulk read during scan")
            buf.extend(data)
            if progress is not None:
                progress(min(1.0, len(buf) / total))
            logger.debug("acquired %d / %d", len(buf), total)

        if progress is not None:
            progress(1.0)
        return bytes(buf[:total])


def create_session(
    asic: AsicDriver,
    model: FilmModel = MODEL_8200I,
    calibrator: Calibrator | None = None,
) -> ScanSession:
    """Build the scan session matching ``model``'s ASIC.

    GL845/GL843/GL842 share :class:`ScanSession`; the 8200i SE's GL128 needs a
    different register block, bulk framing and feed model, so it gets its own
    subclass.
    """
    if getattr(model, "asic", "") == "GL128":
        from negpy.infrastructure.scanners.plustek.scan.session_gl128 import Gl128ScanSession

        return Gl128ScanSession(asic, model, calibrator)
    return ScanSession(asic, model, calibrator)


# Re-export for type checkers / older imports
__all__ = ["ScanSession", "create_session"]
