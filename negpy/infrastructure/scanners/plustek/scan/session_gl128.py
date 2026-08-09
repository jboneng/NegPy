# SPDX-License-Identifier: GPL-3.0-or-later
"""Scan session for the OpticFilm 8200i SE (GL128).

Only the chip-specific steps are overridden; run/assemble/calibration lookup
stay in :class:`~negpy.infrastructure.scanners.plustek.scan.session.ScanSession`. What differs from GL845:

* the register block comes from the model tables plus a small set of
  resolution-dependent values, instead of being computed from a motor profile;
* motor slope tables are replayed from the capture rather than generated, so
  there is no ``zmod`` calculation;
* feeding is a separate, synchronous move before the scan starts;
* the image is announced with a single bulk preamble and then streamed, and the
  source is selected with ``wIndex`` — RAM for calibration passes, the live
  image stream for a scan.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from negpy.infrastructure.scanners.plustek.asic.registers import Gl128Registers
from negpy.infrastructure.scanners.plustek.device.model_8200i_se import MODEL_8200I_SE
from negpy.infrastructure.scanners.plustek.device.protocol import AsicDriver, FilmModel
from negpy.infrastructure.scanners.plustek.exceptions import AsicError, ScanCancelled, ScanError
from negpy.infrastructure.scanners.plustek.logging import get_logger
from negpy.infrastructure.scanners.plustek.scan.calibrate import Calibrator
from negpy.infrastructure.scanners.plustek.scan.geometry import ScanGeometry
from negpy.infrastructure.scanners.plustek.scan.session import DATA_TIMEOUT_S, ScanSession

logger = get_logger(__name__)

#: Bytes per bulk chunk while streaming an image. The captures use 1 MiB reads;
#: this only affects progress granularity and syscall count.
IMAGE_CHUNK_BYTES = 1 << 20

try:
    from negpy.infrastructure.scanners.plustek.asic.gl128 import MOTOR_GATED_HINT as _MOTOR_GATED_HINT
except ImportError:  # pragma: no cover
    _MOTOR_GATED_HINT = "GL128 motor moves are temporarily disabled."


class Gl128ScanSession(ScanSession):
    """GL128 scan state machine for the 8200i SE."""

    def __init__(
        self,
        asic: AsicDriver,
        model: FilmModel = MODEL_8200I_SE,
        calibrator: Calibrator | None = None,
    ) -> None:
        super().__init__(asic, model, calibrator)
        self.se_regs = Gl128Registers()
        #: Set when the image pass armed ``AGOHOME`` — wait for park in ``_end_scan``.
        self._await_agohome_park = False

    def run(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        """Refuse unless the ASIC explicitly arms motor moves."""
        if not getattr(self.asic, "_motor_moves_enabled", False):
            raise AsicError(_MOTOR_GATED_HINT)
        return super().run(*args, **kwargs)

    # --- configure ------------------------------------------------------

    def _configure(self, geometry: ScanGeometry) -> None:
        r = self.se_regs
        model = self.model
        dpi = geometry.resolution
        shading = bool(geometry.disable_buffer_full_move)

        cache = model.boot_register_map()
        cache.update(model.gpo_regs)
        cache.update(model.sensor_custom_regs)

        try:
            cache[0x2B] = model.dummy_by_dpi[dpi]
            cache[0xA5] = model.pixel_clock_by_dpi[dpi]
            cache[0xAB] = model.pixel_clock_by_dpi[dpi]
        except KeyError as exc:
            raise ScanError(
                f"No capture-derived register values for {dpi} dpi on "
                f"{model.model}; supported: {sorted(model.resolutions_dpi)}"
            ) from exc

        # Image: DEPTH8 *registers* (session 11) but 16-bit LE samples on the
        # wire. Calib/shading: DEPTH16 regs + 16-bit samples (sessions 03–04).
        if shading:
            cache[r.REG_DEPTH_A] = r.DEPTH16_A
            cache[r.REG_DEPTH_B] = r.DEPTH16_B
        else:
            cache[r.REG_DEPTH_A] = r.DEPTH8_A
            cache[r.REG_DEPTH_B] = r.DEPTH8_B

        # Host shading: clear DVDSET on calib. Image keeps DVDSET only when a
        # measured ASIC shading table is ready — otherwise boot DVDSET + unity
        # or stale coefficients produce rainbow / clipped garbage.
        reg01 = (cache.get(r.REG_0x01, 0x22) | r.SHDAREA) & ~r.SCAN & ~r.STAGGER
        if shading or not getattr(self.asic, "asic_shading_ready", False):
            reg01 &= ~r.DVDSET
        cache[r.REG_0x01] = reg01

        # Leave lamp / IR LED alone — :meth:`Gl128.lamp_on` already programmed
        # ``0x03`` / ``0x37`` for the scan method; rewriting them from the boot
        # cache would clear IR LED or re-enable the white lamp.
        cache.pop(r.REG_0x03, None)
        cache.pop(r.REG_IR, None)

        motor = r.MTRPWR
        if not shading:
            # Image pass: AGOHOME parks the carriage when the scan ends.
            motor |= r.AGOHOME
        cache[r.REG_0x02] = motor

        self._set24(cache, r.REG_LINCNT, geometry.lincnt_register)
        self._set24(cache, r.REG_LPERIOD, model.line_period_for(dpi))
        # Captures: AFE/shading always use DPISET = optical_resolution/6 (1200).
        dpiset = (
            model.optical_resolution // 6 if shading else geometry.register_dpiset
        )
        self._set16(cache, r.REG_DPISET, dpiset)
        self._set24(cache, r.REG_STRPIXEL, geometry.pixel_startx)
        self._set24(cache, r.REG_ENDPIXEL, geometry.pixel_endx)
        self._set24(cache, r.REG_EXPOSURE, model.exposure_lperiod)
        # Image/calib acquire with FEEDL=1; positioning is a separate feed pair.
        self._set24(cache, r.REG_FEEDL, 1)
        cache.pop(r.REG_CLRCNT, None)
        cache.pop(r.REG_START, None)

        self._await_agohome_park = not shading and bool(motor & r.AGOHOME)

        # Capture-constant feeds from home — never geometry.starty (that was the
        # grinding bug). Calibration passes stay put (no motor). Positioning is
        # skipped while motor moves are gated so configure unit tests stay safe.
        if not shading and getattr(self.asic, "_motor_moves_enabled", False):
            feed_fn = getattr(model, "feed_to_scan_steps_for_area", None)
            scan_steps = (
                feed_fn(geometry.area)
                if callable(feed_fn)
                else model.feed_to_scan_steps
            )
            # The scan must stop at the window end: feed2 + travel <= 27636
            # steps. Overrunning it is what ground the motor in the Lab.
            max_fn = getattr(model, "max_lincnt_for", None)
            max_lc = max_fn(scan_steps, dpi) if callable(max_fn) else None
            if max_lc is not None and geometry.lincnt_register > int(max_lc):
                start_mm = scan_steps * 25.4 / model.feed_steps_per_inch
                raise ScanError(
                    f"Image LINCNT {geometry.lincnt_register} at {dpi} dpi is "
                    f"{geometry.travel_mm:.1f} mm of travel from feed2="
                    f"{scan_steps} ({start_mm:.1f} mm), past the "
                    f"{model.scan_window_end_steps * 25.4 / model.feed_steps_per_inch:.1f} mm "
                    f"scan-window end. Max LINCNT here is {max_lc} "
                    "(see captures/8200i-se/MOTOR.md)."
                )
            self.asic.position_for_full_frame_scan(scan_steps=scan_steps)

        self.asic.upload_tables(resolution=dpi, shading=shading)
        # Do NOT call set_frontend_init() here — boot zeroes FE gains, and
        # replaying that after search_afe undoes calibration. Captures keep the
        # post-calib FE for the image pass. Re-apply the last search result if
        # we have one (covers any FE touch during table upload / strip setup).
        last_afe = getattr(self.asic, "last_afe", None)
        if last_afe is not None:
            self.asic.apply_frontend(last_afe)

        self.asic.protocol.write_registers_batched(sorted(cache.items()))
        self.asic._reg_cache.update(cache)

        if self._lamp_requested:
            self.asic.lamp_on()
        else:
            self.asic.lamp_off()

        # Base class feed-wait uses this; GL128 feeds synchronously above.
        self._feedl = 0

        logger.info(
            "GL128 configured %ddpi dpiset=%d lincnt=%d str=%d end=%d lperiod=%d "
            "shading=%s",
            dpi,
            dpiset,
            geometry.lincnt_register,
            geometry.pixel_startx,
            geometry.pixel_endx,
            model.line_period_for(dpi),
            shading,
        )

    # --- acquire --------------------------------------------------------

    def _begin_scan(self, *, start_motor: bool = True) -> None:
        """Capture order: ``0x0d=0x07`` → set SCAN → ``0x0f`` (session 03)."""
        r = self.se_regs
        proto = self.asic.protocol

        proto.write_register(r.REG_CLRCNT, r.CLRCNT_ALL)
        reg01 = proto.read_register(r.REG_0x01) | r.SCAN
        proto.write_register(r.REG_0x01, reg01)
        self.asic._reg_cache[r.REG_0x01] = reg01
        proto.write_register(r.REG_START, r.START_GO if start_motor else 0x00)
        logger.info("GL128 scan started motor=%s", start_motor)

    def _wait_data(self, cancel: threading.Event | None) -> None:
        """Wait until the ASIC reports data in its buffer.

        GL845 also cross-checks the valid-word counters at ``0x42``-``0x45``;
        the SE captures never touch those, so buffer state is all there is.
        """
        deadline = time.monotonic() + DATA_TIMEOUT_S
        while time.monotonic() < deadline:
            if cancel is not None and cancel.is_set():
                raise ScanCancelled("cancelled waiting for data")
            if not self.asic.read_status().is_buffer_empty:
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
        del wait_feed  # GL128 feeds synchronously in _configure
        self._wait_data(cancel)

        r = self.se_regs
        proto = self.asic.protocol
        total = geometry.total_bytes
        index = (
            r.BULK_INDEX_RAM
            if geometry.disable_buffer_full_move
            else r.BULK_INDEX_IMAGE
        )
        proto.bulk_read_begin(total, index=index)

        buf = bytearray()
        while len(buf) < total:
            if cancel is not None and cancel.is_set():
                raise ScanCancelled("cancelled during bulk read")
            want = min(IMAGE_CHUNK_BYTES, total - len(buf))
            chunk = proto.bulk_read_chunk(want)
            if not chunk:
                raise ScanError(
                    f"Bulk stream ended after {len(buf)} of {total} bytes"
                )
            buf.extend(chunk)
            if progress is not None:
                progress(min(1.0, len(buf) / total))

        if progress is not None:
            progress(1.0)
        return bytes(buf[:total])

    def _end_scan(self) -> None:
        # Capture end/cancel recipe (lamp strobe + clear SCAN + AGOHOME park)
        # lives in Gl128.stop_motor — do not bare-clear 0x01 here or the strobe
        # order is lost and SCAN is cleared twice.
        try:
            super()._end_scan()
        finally:
            self._await_agohome_park = False
