# SPDX-License-Identifier: GPL-3.0-or-later
"""GL128 driver for the OpticFilm 8200i SE.

SANE genesys has no GL128 command set, so none of this is ported from SANE.
Every register write below replays what the Windows driver does in the USB
captures under ``captures/8200i-se/``; the model tables it uses live in
``negpy.infrastructure.scanners.plustek.device.model_8200i_se``.

Differences from :class:`~negpy.infrastructure.scanners.plustek.asic.gl845.Gl845` that matter here:

* status is at ``0x101`` (high-address read) rather than ``0x41``, though the
  bit layout is the same;
* the analog frontend is written through ``0x51``/``0x5D``/``0x5E``;
* the white lamp is ``0x03`` bit 4 and infrared is ``0x37`` bit 2 set by
  read-modify-write (``0x03`` bit 5 / ``AVEENB`` is held during lamp-on as in
  the captures);
* positioning is two capture-constant feeds from home, then the image pass
  runs with ``FEEDL=1`` and ``AGOHOME`` so the carriage parks afterwards —
  see ``captures/8200i-se/MOTOR.md``.
"""

from __future__ import annotations

import time
from typing import Literal

from negpy.infrastructure.scanners.plustek.asic.registers import Gl128Registers
from negpy.infrastructure.scanners.plustek.asic.status import ScannerStatus
from negpy.infrastructure.scanners.plustek.device.model_8200i_se import MODEL_8200I_SE, Model8200iSE
from negpy.infrastructure.scanners.plustek.device.tables_8200i_se import (
    SLOPE_TABLE_FAST,
    SLOPE_TABLE_SLOW,
    exposure_table,
)
from negpy.infrastructure.scanners.plustek.exceptions import AsicError, MotorTimeoutError, ScanError
from negpy.infrastructure.scanners.plustek.logging import get_logger
from negpy.infrastructure.scanners.plustek.scan.calib_gl128 import (
    AFE_ENDPIXEL,
    AFE_STRIP_BYTES,
    AFE_STRPIXEL,
    AHB_SHADING,
    SHADING_LINES,
    AfeFrontend,
    AfeSearchConfig,
    adaptive_afe_gain_target,
    average_rgb16_columns,
    build_measured_shading_table,
    channel_means_u16,
    choose_usb_planar,
    declared_shading_size,
    equalize_ir_white_columns,
    make_unity_white_table,
    search_afe_codes,
    shading_acquire_width,
    shading_width_for_resolution,
    validate_ir_shading_table,
)
from negpy.infrastructure.scanners.plustek.usb.protocol import GenesysUsbProtocol

logger = get_logger(__name__)

ScanMethod = Literal["transparency", "infrared"]

#: Session 05 IR pass settled near these FE codes when home IR is too dim for
#: the dichotomy target — prefer them over mid-probe 0x80 / searched offsets.
IR_AFE_FALLBACK_OFFSETS: tuple[int, int, int] = (6, 16, 12)
IR_AFE_FALLBACK_GAINS: tuple[int, int, int] = (0x30, 0x29, 0x31)
#: Pegged at max gain with at least this mean is already usable — keep those
#: gains (dropping to ``IR_AFE_FALLBACK_GAINS`` starved shading whites and
#: DVDSET clipped the image to full scale).
IR_AFE_HEALTHY_MEAN = 8000

BRINGUP_HINT = (
    "GL128 (OpticFilm 8200i SE) support is derived from USB captures of the "
    "Windows driver rather than from SANE — see docs/gl128-bringup.md."
)

#: Raised when code deliberately disarms the motor (e.g. stationary shading).
MOTOR_GATED_HINT = (
    "GL128 motor moves are temporarily disabled on this handle "
    "(disarmed for safety). Re-enable with Scanner.arm_bringup_motor() after "
    "stationary calib, or open a fresh Scanner (motor is on by default for "
    "scan-ready SE)."
)

MM_PER_INCH = 25.4
HOME_POLL_S = 0.05

#: Vendor probe ``wIndex`` polled during fast feeds until it returns this.
_FEED_PROBE_INDEX = 0x21
_FEED_PROBE_DONE = 0x04
#: How long a feed may go without reporting motion before its start-up state is
#: taken at face value. The probe and ``FEEDFSH`` both survive the previous
#: feed, so a completion seen inside this window is the *old* move's.
_FEED_START_TIMEOUT_S = 2.0

#: Register block written immediately before every captured fast feed
#: (session 03 t≈8.98). Values are constants in the captures, not DPI-dependent.
_FEED_SETUP_REGS: dict[int, int] = {
    0x01: 0x22,
    0x04: 0x42,
    0x05: 0x48,
    0xA6: 0x00,
    0xA7: 0x00,
    0xA8: 0x00,
    0xA9: 0x00,
    0x7D: 0x00,
    0x7E: 0x36,
    0x7F: 0xB0,  # exposure = 14000
    0x80: 0x00,
    0x81: 0x40,
    0x82: 0x00,
    0x83: 0x00,
    0x84: 0xF2,
    0x85: 0x00,
    0x86: 0x29,
    0x87: 0x72,
    0x2C: 0x00,
    0x2D: 0xC8,  # DPISET = 200
    0x1D: 0x80,
    0x1C: 0x20,
    0xA4: 0x00,
    0xA5: 0x02,
    0xAA: 0x00,
    0xAB: 0x02,
    0xAE: 0x00,
    0xAF: 0x7F,
}

#: Two 34-byte blobs the Windows driver writes to ``0x000FFF00`` and
#: ``0x000FFF01`` before the register blast. Their meaning is unknown; they are
#: replayed byte-for-byte because boot is not reproducible without them.
_BOOT_BLOB_ADDR_A = 0x000FFF00
_BOOT_BLOB_ADDR_B = 0x000FFF01
_BOOT_BLOB_A = bytes(34)
_BOOT_BLOB_B = bytes(32) + bytes((0x33, 0x00))

#: Image cancel / end-scan lamp writes on ``0x03`` (sessions 03 return-home,
#: 08b/08c Cancel). Pre-clear pair, then clear ``SCAN`` to ``0x22``, then post.
_CANCEL_LAMP_PRE: tuple[int, ...] = (0x30, 0x20)
_CANCEL_LAMP_POST: tuple[int, ...] = (0x10, 0x00, 0x20, 0x30, 0x20, 0x30)
#: Capture writes ``0x01 = 0x22`` (clear ``SCAN``, keep SHDAREA|DVDSET).
_CANCEL_REG01 = 0x22


def _u16_table_bytes(words: tuple[int, ...]) -> bytes:
    """Pack 16-bit table entries little-endian, as the AHB windows expect."""
    out = bytearray(len(words) * 2)
    for i, word in enumerate(words):
        out[2 * i] = word & 0xFF
        out[2 * i + 1] = (word >> 8) & 0xFF
    return bytes(out)


class Gl128:
    """GL128 chip operations for the OpticFilm 8200i SE."""

    def __init__(
        self,
        protocol: GenesysUsbProtocol,
        model: Model8200iSE = MODEL_8200I_SE,
    ) -> None:
        self.protocol = protocol
        self.model = model
        self.registers = Gl128Registers()
        self._initialized = False
        self._reg_cache: dict[int, int] = {}
        self._scan_method: ScanMethod = "transparency"
        #: On for scan-ready SE; Lab/session may temporarily disarm for
        #: stationary shading (ASIC shade while armed caused motor grind).
        self._motor_moves_enabled = bool(getattr(model, "scan_ready", False))
        #: Whether the last SilverFast-style AGOHOME park completed.
        #: If false, the next scan/position is refused because the carriage
        #: origin for the next feed pair is unknown.
        self._park_ok: bool = True
        #: Set after a successful :meth:`run_asic_shading` upload this session.
        #: IR Lab never sets this — ASIC DVDSET clipped IR to full scale.
        self.asic_shading_ready = False
        #: Equalized per-column IR white (one value/column) for host flatten.
        self.last_ir_host_white: list[int] | None = None
        #: True when :attr:`last_ir_host_white` passed the IR validator.
        self.ir_host_flatten_ready: bool = False
        #: Last successful :meth:`search_afe` result; image configure re-applies
        #: it so scan setup does not wipe calibrated FE gains back to boot zero.
        self.last_afe: AfeFrontend | None = None
        #: USB line layout learned from AFE strip means (True=planar RRR…GGG…BBB…).
        self.usb_planar_rgb: bool = False

    def _require_motor_enabled(self) -> None:
        if not self._motor_moves_enabled:
            raise AsicError(MOTOR_GATED_HINT)

    # --- registers ------------------------------------------------------

    def _write(self, address: int, value: int) -> None:
        self.protocol.write_register(address, value)
        self._reg_cache[address] = value & 0xFF

    def _write_many(self, regs: dict[int, int]) -> None:
        pairs = sorted(regs.items())
        self.protocol.write_registers_batched(pairs)
        self._reg_cache.update({a: v & 0xFF for a, v in pairs})

    def _update_bits(self, address: int, *, set_bits: int = 0, clear_bits: int = 0) -> int:
        """Read-modify-write one register and return the value written."""
        current = self.protocol.read_register(address)
        value = (current & ~clear_bits & 0xFF) | set_bits
        self._write(address, value)
        return value

    # --- status ---------------------------------------------------------

    def read_status(self) -> ScannerStatus:
        """Read the status register at ``0x101``."""
        try:
            raw = self.protocol.read_register(self.registers.REG_STATUS)
        except Exception as exc:  # noqa: BLE001
            raise AsicError(f"GL128 status read failed: {exc}") from exc
        return ScannerStatus.from_reg41(raw)

    def read_status_reliable(self) -> ScannerStatus:
        """Read status twice and keep the second value.

        The first read after an operation can still reflect the previous state,
        which is visible in the captures as a single stale sample before the
        driver's poll loops settle.
        """
        self.read_status()
        return self.read_status()

    def is_at_home(self) -> bool:
        return self.read_status_reliable().is_at_home

    def is_cold_boot(self) -> bool:
        """True when the power bit is clear, meaning the ASIC lost its state."""
        return self.read_status().is_replugged

    # --- boot -----------------------------------------------------------

    def set_frontend_init(self) -> None:
        """Load the analog frontend defaults through the GL124 write path."""
        for index, value in sorted(self.model.frontend_regs.items()):
            self.protocol.write_fe_register_gl124(index, value)
        logger.debug("GL128 frontend initialised (%d regs)", len(self.model.frontend_regs))

    def set_frontend_channels(
        self,
        *,
        offsets: tuple[int, int, int] | None = None,
        gains: tuple[int, int, int] | None = None,
    ) -> None:
        """Write per-channel FE offsets (``0x02``–``0x04``) and/or gains (``0x05``–``0x07``)."""
        fe = AfeFrontend(
            offsets=offsets if offsets is not None else (0, 0, 0),
            gains=gains if gains is not None else (0, 0, 0),
        )
        for index, value in fe.as_fe_writes():
            if offsets is None and index <= 0x04:
                continue
            if gains is None and index >= 0x05:
                continue
            self.protocol.write_fe_register_gl124(index, value)

    def apply_frontend(self, frontend: AfeFrontend) -> None:
        """Program a full offset+gain FE state."""
        for index, value in frontend.as_fe_writes():
            self.protocol.write_fe_register_gl124(index, value)

    def _setup_afe_strip_regs(self) -> None:
        """Minimal stationary strip geometry (session 03 AFE window, motor off)."""
        r = self.registers
        dpi_calib = self.model.optical_resolution // 6
        self.protocol.write_u24(r.REG_LINCNT, 1)
        self.protocol.write_u16(r.REG_DPISET, dpi_calib)
        self.protocol.write_u24(r.REG_STRPIXEL, AFE_STRPIXEL)
        self.protocol.write_u24(r.REG_ENDPIXEL, AFE_ENDPIXEL)
        self.protocol.write_u24(r.REG_FEEDL, 1)
        self._write(r.REG_DEPTH_A, r.DEPTH16_A)
        self._write(r.REG_DEPTH_B, r.DEPTH16_B)
        # No motor: keep 0x02 clear of MTRPWR / AGOHOME / FASTFED.
        self._write(r.REG_0x02, 0x00)
        # SHDAREA, no SCAN yet — match calib-style 0x01 before the start recipe.
        reg01 = (self._reg_cache.get(r.REG_0x01, 0x22) | r.SHDAREA) & ~r.SCAN & ~r.DVDSET
        self._write(r.REG_0x01, reg01)

    def acquire_afe_strip(
        self,
        size: int = AFE_STRIP_BYTES,
        *,
        timeout_s: float = 5.0,
    ) -> bytes:
        """Read one stationary 16-bit AFE strip. Does not move the carriage."""
        if not self._initialized:
            self.init()
        r = self.registers
        size = int(size)
        if size <= 0:
            raise ValueError("AFE strip size must be positive")

        self._setup_afe_strip_regs()
        # Capture start recipe: 0x0d → SCAN → 0x0f (no motor).
        self._write(r.REG_CLRCNT, r.CLRCNT_ALL)
        self._update_bits(r.REG_0x01, set_bits=r.SCAN)
        self._write(r.REG_START, r.START_GO)

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if not self.read_status().is_buffer_empty:
                break
            time.sleep(0.01)
        else:
            self._update_bits(r.REG_0x01, clear_bits=r.SCAN)
            raise ScanError(f"AFE strip: no data within {timeout_s:.0f}s")

        self.protocol.bulk_read_begin(size, index=r.BULK_INDEX_RAM, addr=r.AHB_CHANNEL_R)
        buf = bytearray()
        while len(buf) < size:
            chunk = self.protocol.bulk_read_chunk(min(size - len(buf), size))
            if not chunk:
                break
            buf.extend(chunk)
        self._update_bits(r.REG_0x01, clear_bits=r.SCAN)
        if len(buf) < size:
            raise ScanError(f"AFE strip short read: {len(buf)} of {size} bytes")
        return bytes(buf[:size])

    def search_afe(
        self,
        *,
        config: AfeSearchConfig | None = None,
        method: ScanMethod | None = None,
    ) -> AfeFrontend:
        """Run stationary offset then gain dichotomy; leave FE at the result.

        Motor stays gated/off. Lamp is turned off for offsets and on for gains.
        Pass ``method=\"infrared\"`` so gain search runs under the IR LED with the
        white lamp off (session 05: IR has its own AFE, not the colour codes).
        """
        if not self._initialized:
            self.init()
        if method is not None:
            self.set_scan_method(method)
        cfg = config or AfeSearchConfig()

        # AFE strips may look planar or chunky; film *image* USB is chunky on SE
        # (session 11). Never copy the AFE probe into ``usb_planar_rgb`` — that
        # flag drives image assemble + shading averages. Dark IR probes often
        # fail the lag test and wrongly pick planar → barcode IR / rainbow.
        self.lamp_on()
        time.sleep(0.3)
        self.apply_frontend(AfeFrontend(offsets=(0, 0, 0), gains=(0x80, 0x80, 0x80)))
        probe = self.acquire_afe_strip(AFE_STRIP_BYTES)
        mean_planar = channel_means_u16(probe, planar=True)
        mean_chunky = channel_means_u16(probe, planar=False)

        def _imbalance(means: tuple[float, float, float]) -> float:
            lo = max(1.0, min(means))
            return max(means) / lo

        # Lag test plus channel balance: a dark IR strip often fails the lag
        # test and picks planar, which then drives the FE search off a bad
        # mean and leaves colour/IR underexposed after the 0x80 fallback.
        lag_planar = choose_usb_planar(probe)
        afe_planar = lag_planar
        if _imbalance(mean_planar if lag_planar else mean_chunky) > 2.0:
            afe_planar = _imbalance(mean_planar) <= _imbalance(mean_chunky)
        probe_means = mean_planar if afe_planar else mean_chunky
        gain_target = adaptive_afe_gain_target(probe_means)
        self.usb_planar_rgb = bool(getattr(self.model, "usb_planar_rgb", False))
        logger.info(
            "GL128 AFE strip layout → %s (means only); image/shading layout → %s; "
            "probe_means=(%.0f,%.0f,%.0f); gain_target=%#x (capture default %#x)",
            "planar" if afe_planar else "chunky",
            "planar" if self.usb_planar_rgb else "chunky",
            probe_means[0],
            probe_means[1],
            probe_means[2],
            int(gain_target),
            cfg.gain_target,
        )

        def measure(fe: AfeFrontend) -> tuple[float, float, float]:
            self.apply_frontend(fe)
            strip = self.acquire_afe_strip(AFE_STRIP_BYTES)
            return channel_means_u16(strip, planar=afe_planar)

        self.lamp_off()
        time.sleep(0.2)

        def apply_offsets(offsets: tuple[int, int, int]) -> tuple[float, float, float]:
            # Hold gains at the capture mid probe while hunting offsets.
            return measure(AfeFrontend(offsets=offsets, gains=(0x80, 0x80, 0x80)))

        offsets = search_afe_codes(
            initial=(0, 0, 0),
            code_max=cfg.offset_max,
            target=float(cfg.offset_target),
            iterations=cfg.iterations,
            tolerance=cfg.tolerance,
            code_increases_mean=cfg.offset_increases_mean,
            apply=apply_offsets,
        )

        self.lamp_on()
        time.sleep(0.5)

        def apply_gains(gains: tuple[int, int, int]) -> tuple[float, float, float]:
            return measure(AfeFrontend(offsets=offsets, gains=gains))

        gains = search_afe_codes(
            initial=(0x80, 0x80, 0x80),
            code_max=cfg.gain_max,
            target=float(gain_target),
            iterations=cfg.iterations,
            tolerance=cfg.tolerance,
            code_increases_mean=cfg.gain_increases_mean,
            apply=apply_gains,
        )
        result = AfeFrontend(offsets=offsets, gains=gains)
        # If every channel pegged the max code, the target was still unreachable
        # (dark home / lamp). Fall back rather than leaving the FE at maximum.
        if all(g >= cfg.gain_max - 1 for g in result.gains):
            pegged_means = measure(result)
            pegged_gains = result.gains
            infrared = self._scan_method == "infrared"
            if infrared:
                if min(pegged_means) >= IR_AFE_HEALTHY_MEAN:
                    # Target unreachable but signal is fine — keep max gains.
                    result = AfeFrontend(
                        offsets=IR_AFE_FALLBACK_OFFSETS,
                        gains=pegged_gains,
                    )
                    fallback_label = "session-05 offsets + keeping pegged gains"
                else:
                    # Truly dark IR: session 05 settle region.
                    result = AfeFrontend(
                        offsets=IR_AFE_FALLBACK_OFFSETS,
                        gains=IR_AFE_FALLBACK_GAINS,
                    )
                    fallback_label = "session-05 IR offsets+gains"
            else:
                result = AfeFrontend(offsets=offsets, gains=(0x80, 0x80, 0x80))
                fallback_label = "mid-probe 0x80"
            logger.warning(
                "GL128 AFE gains pegged at max %s with means=(%.0f,%.0f,%.0f); "
                "falling back to %s (offsets=%s gains=%s)",
                pegged_gains,
                pegged_means[0],
                pegged_means[1],
                pegged_means[2],
                fallback_label,
                result.offsets,
                result.gains,
            )
        self.apply_frontend(result)
        self.last_afe = result
        final_means = measure(result)
        logger.info(
            "GL128 AFE search done offsets=%s gains=%s means=(%.0f,%.0f,%.0f) "
            "targets offset=%#x gain=%#x planar=%s",
            result.offsets,
            result.gains,
            final_means[0],
            final_means[1],
            final_means[2],
            cfg.offset_target,
            int(gain_target),
            self.usb_planar_rgb,
        )
        return result

    def upload_shading_table(self, blob: bytes) -> None:
        """Write a packed shading coefficient blob to ``0x10014000``."""
        if not blob:
            raise ValueError("shading blob is empty")
        self.protocol.write_ahb(AHB_SHADING, blob)
        logger.info("GL128 uploaded shading table (%d bytes) to 0x%08x", len(blob), AHB_SHADING)

    def _shading_window(
        self,
        *,
        pixels: int,
        resolution: int,
        strpixel: int | None,
        endpixel: int | None,
        dpiset: int | None,
    ) -> tuple[int, int, int]:
        """Resolve the ``(STRPIXEL, ENDPIXEL, DPISET)`` for a shading pass.

        Sessions 04/05: the vendor runs shading through the **image** window and
        the **image** ``DPISET`` (e.g. 578/10490, DPISET=300 at 1800 dpi), then
        scans with the same window. A shading table measured through a different
        window/rate is indexed differently from the image and comes out as
        periodic dropouts, so callers should pass the scan geometry.
        """
        dpi = int(resolution)
        if dpiset is None:
            by_dpi = getattr(self.model, "register_dpiset_by_dpi", None)
            dpiset = int(by_dpi[dpi]) if by_dpi and dpi in by_dpi else max(1, dpi // 6)
        dpiset = int(dpiset)
        factor = max(1, self.model.optical_resolution // max(1, dpiset * 6))
        start = 240 if strpixel is None else int(strpixel)
        end = int(endpixel) if endpixel is not None else start + int(pixels) * factor
        return start, end, dpiset

    def _setup_shading_strip_regs(
        self,
        *,
        pixels: int,
        lines: int,
        resolution: int,
        strpixel: int | None = None,
        endpixel: int | None = None,
        dpiset: int | None = None,
    ) -> None:
        """Stationary multi-line shading geometry (image window, motor off)."""
        r = self.registers
        start, end, dpi_calib = self._shading_window(
            pixels=pixels,
            resolution=resolution,
            strpixel=strpixel,
            endpixel=endpixel,
            dpiset=dpiset,
        )
        self.protocol.write_u24(r.REG_LINCNT, int(lines))
        self.protocol.write_u16(r.REG_DPISET, dpi_calib)
        self.protocol.write_u24(r.REG_STRPIXEL, start)
        self.protocol.write_u24(r.REG_ENDPIXEL, end)
        self.protocol.write_u24(r.REG_FEEDL, 1)
        self._write(r.REG_DEPTH_A, r.DEPTH16_A)
        self._write(r.REG_DEPTH_B, r.DEPTH16_B)
        # Stationary only: clear MTRPWR/AGOHOME/FASTFED. With motor armed in
        # Lab, any residual 0x02 bits + SCAN+LINCNT will advance the carriage
        # and can grind the window end.
        self._write(r.REG_0x02, 0x00)
        reg01 = (self._reg_cache.get(r.REG_0x01, 0x22) | r.SHDAREA) & ~r.SCAN & ~r.DVDSET
        self._write(r.REG_0x01, reg01)

    def _require_carriage_at_home(self, where: str) -> None:
        """Refuse to continue if the head is off the home sensor (grind risk)."""
        status = self.read_status()
        if not status.is_at_home:
            raise ScanError(
                f"{where}: carriage is not at home - refuse to avoid grind. "
                "Park with SilverFast or power-cycle the scanner."
            )

    def acquire_shading_strip(
        self,
        *,
        resolution: int,
        pixels: int | None = None,
        lines: int = SHADING_LINES,
        timeout_s: float = 30.0,
        strpixel: int | None = None,
        endpixel: int | None = None,
        dpiset: int | None = None,
    ) -> bytes:
        """Read a stationary multi-line 16-bit strip for ASIC shading."""
        if not self._initialized:
            self.init()
        n = int(pixels) if pixels is not None else shading_width_for_resolution(resolution)
        lines = int(lines)
        size = n * lines * 6
        r = self.registers
        self._setup_shading_strip_regs(
            pixels=n,
            lines=lines,
            resolution=resolution,
            strpixel=strpixel,
            endpixel=endpixel,
            dpiset=dpiset,
        )
        # Re-assert motor off immediately before START (cache/hardware drift).
        self._write(r.REG_0x02, 0x00)
        self._write(r.REG_CLRCNT, r.CLRCNT_ALL)
        self._update_bits(r.REG_0x01, set_bits=r.SCAN)
        self._write(r.REG_START, r.START_GO)

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if not self.read_status().is_buffer_empty:
                break
            time.sleep(0.01)
        else:
            self._update_bits(r.REG_0x01, clear_bits=r.SCAN)
            raise ScanError(f"Shading strip: no data within {timeout_s:.0f}s")

        self.protocol.bulk_read_begin(size, index=r.BULK_INDEX_RAM, addr=r.AHB_CHANNEL_R)
        buf = bytearray()
        while len(buf) < size:
            chunk = self.protocol.bulk_read_chunk(min(65536, size - len(buf)))
            if not chunk:
                break
            buf.extend(chunk)
        self._update_bits(r.REG_0x01, clear_bits=r.SCAN)
        self._write(r.REG_0x02, 0x00)
        if len(buf) < size:
            raise ScanError(f"Shading strip short read: {len(buf)} of {size} bytes")
        return bytes(buf[:size])

    def run_asic_shading(
        self,
        *,
        resolution: int = 1800,
        method: ScanMethod | None = None,
        strpixel: int | None = None,
        endpixel: int | None = None,
        dpiset: int | None = None,
    ) -> bytes:
        """Dark→unity upload→white→measured upload (stationary; motor off).

        Returns the final measured shading blob. Sets ``asic_shading_ready``.
        Image scans keep ``DVDSET`` when ready so the ASIC applies this table.

        Pass ``method=\"infrared\"`` so the white strip runs under the IR LED
        (session 05: IR table uses zero dark terms + near-equal whites).

        Pass the image ``strpixel``/``endpixel``/``dpiset`` so the acquire uses
        the same window the scan uses. Acquire width is the window pixel count
        (session 04: 128×2478), then the upload is padded to the declared AHB
        width (2517 at 1800 dpi).

        For ``method=\"infrared\"``, the optical dark strip is diagnostic only —
        the uploaded table dark is forced to zero (session 05). Live ASIC DVDSET
        clipped IR to full scale, so infrared **never** sets
        ``asic_shading_ready``; a validated white profile is stored for host
        flatten instead (:attr:`ir_host_flatten_ready`).

        Refuses if ``_motor_moves_enabled`` — Lab must disarm before shading and
        re-arm only for the image feeds (capture order: shade, then motor).
        """
        if not self._initialized:
            self.init()
        if self._motor_moves_enabled:
            raise ScanError(
                "ASIC shading requires motor disarmed — "
                "call disarm_bringup_motor before run_asic_shading"
            )
        if method is not None:
            self.set_scan_method(method)
        infrared = self._scan_method == "infrared"
        self._require_carriage_at_home("ASIC shading start")
        table_n = shading_width_for_resolution(resolution)
        # Resolve window first so acquire width matches STR/END, not table pad.
        start, end, used_dpiset = self._shading_window(
            pixels=table_n,
            resolution=resolution,
            strpixel=strpixel,
            endpixel=endpixel,
            dpiset=dpiset,
        )
        n = shading_acquire_width(
            strpixel=start,
            endpixel=end,
            dpiset=used_dpiset,
            optical_resolution=self.model.optical_resolution,
        )
        window = {"strpixel": start, "endpixel": end, "dpiset": used_dpiset}
        declared = declared_shading_size(table_n)
        self.asic_shading_ready = False
        if infrared:
            self.ir_host_flatten_ready = False
            self.last_ir_host_white = None

        # Capture: shading uses the slow slope table (session 03).
        self.upload_tables(resolution=resolution, shading=True)

        # Dark strip: white lamp + IR LED off. IR table dark is forced to 0.
        r = self.registers
        self.lamp_off()
        self._write(r.REG_0x03, r.XPASEL)
        self._apply_infrared(enabled=False)
        time.sleep(0.5 if infrared else 0.2)
        dark_raw = self.acquire_shading_strip(resolution=resolution, pixels=n, **window)
        dark_measured = average_rgb16_columns(
            dark_raw, pixels=n, lines=SHADING_LINES, planar=self.usb_planar_rgb
        )
        if table_n > n:
            dark_measured = list(dark_measured) + [dark_measured[-1]] * (table_n - n)
        if infrared:
            dark = [(0, 0, 0)] * len(dark_measured)
            logger.info(
                "GL128 IR shading: zero dark terms (session 05); "
                "measured dark0=%s (diagnostic only)",
                dark_measured[0],
            )
        else:
            dark = dark_measured
        unity = make_unity_white_table(dark, declared_size=declared)
        self.upload_shading_table(unity)

        self.lamp_on()
        if infrared:
            # Re-assert capture IR illum: white lamp off, IR LED on (0x37 bit 2).
            self._write(r.REG_0x03, r.XPASEL)
            self._apply_infrared(enabled=True)
            reg03 = self.protocol.read_register(r.REG_0x03)
            reg37 = self.protocol.read_register(r.REG_IR)
            logger.info(
                "GL128 IR white strip illum 0x03=%#04x (LAMPPWR=%s) "
                "0x37=%#04x (IR_LED=%s)",
                reg03,
                bool(reg03 & r.LAMPPWR),
                reg37,
                bool(reg37 & r.IR_LED),
            )
            if reg03 & r.LAMPPWR or not (reg37 & r.IR_LED):
                self._write(r.REG_0x03, r.XPASEL)
                self._apply_infrared(enabled=True)
        if self.last_afe is not None:
            self.apply_frontend(self.last_afe)
        time.sleep(0.5)
        white_raw = self.acquire_shading_strip(resolution=resolution, pixels=n, **window)
        white = average_rgb16_columns(
            white_raw, pixels=n, lines=SHADING_LINES, planar=self.usb_planar_rgb
        )
        raw_white: list[tuple[int, int, int]] | None = None
        if infrared:
            raw0 = white[0]
            raw_spread = max(int(c) for c in raw0) - min(int(c) for c in raw0)
            raw_white = list(white)
            white = equalize_ir_white_columns(white)
            logger.info(
                "GL128 IR shading: equalized white columns (session 05 shape); "
                "raw white0=%s spread=%d → equalized white0=%s",
                raw0,
                raw_spread,
                white[0],
            )
        if table_n > n:
            white = list(white) + [white[-1]] * (table_n - n)
            if raw_white is not None:
                raw_white = list(raw_white) + [raw_white[-1]] * (table_n - n)
        measured = build_measured_shading_table(dark, white, declared_size=declared)
        self.upload_shading_table(measured)
        self._write(self.registers.REG_0x02, 0x00)
        self._require_carriage_at_home("ASIC shading end")
        # IR: never arm ASIC DVDSET (live HW clipped to 0xFFFF). Store a host
        # white profile when the table validates; image flatten uses that.
        if infrared:
            ok, reason = validate_ir_shading_table(
                dark[:n],
                white[:n],
                acquire_width=n,
                raw_white=None if raw_white is None else raw_white[:n],
            )
            white_prefix = white[:n]
            white_mean = (
                sum(int(c) for row in white_prefix for c in row)
                / max(1, len(white_prefix) * 3)
            )
            self.asic_shading_ready = False
            if ok:
                self.last_ir_host_white = [int(row[0]) for row in white_prefix]
                self.ir_host_flatten_ready = True
                logger.info(
                    "GL128 IR host flatten ready dpi=%d acquire=%d "
                    "table=%d window=%d..%d dpiset=%d white0=%s "
                    "white_mean=%.0f measured_dark0=%s (DVDSET off)",
                    resolution,
                    n,
                    table_n,
                    start,
                    end,
                    used_dpiset,
                    white[0],
                    white_mean,
                    dark_measured[0],
                )
            else:
                self.last_ir_host_white = None
                self.ir_host_flatten_ready = False
                logger.warning(
                    "GL128 IR host flatten rejected (%s); DVDSET off "
                    "dpi=%d acquire=%d white0=%s white_mean=%.0f "
                    "measured_dark0=%s",
                    reason,
                    resolution,
                    n,
                    white[0],
                    white_mean,
                    dark_measured[0],
                )
        else:
            self.asic_shading_ready = True
            logger.info(
                "GL128 ASIC shading ready dpi=%d method=%s acquire=%d table=%d "
                "window=%d..%d dpiset=%d dark0=%s white0=%s",
                resolution,
                self._scan_method,
                n,
                table_n,
                start,
                end,
                used_dpiset,
                dark[0],
                white[0],
            )
        return measured

    def upload_tables(self, *, resolution: int, shading: bool = False) -> None:
        """Upload motor slope and per-channel exposure tables to scanner RAM.

        ``shading=True`` loads the slow ramp (session 03); otherwise the fast
        ramp used for feeds and image (sessions 03–06).
        """
        r = self.registers
        slope = _u16_table_bytes(SLOPE_TABLE_SLOW if shading else SLOPE_TABLE_FAST)
        self.protocol.write_ahb(r.AHB_SLOPE_SCAN, slope)
        self.protocol.write_ahb(r.AHB_SLOPE_FAST, slope)

        exposure = _u16_table_bytes(
            exposure_table(self.model.channel_exposure_for(resolution))
        )
        for addr in (r.AHB_CHANNEL_R, r.AHB_CHANNEL_G, r.AHB_CHANNEL_B):
            self.protocol.write_ahb(addr, exposure)
        logger.debug(
            "GL128 uploaded %s slope + exposure tables for %d dpi",
            "slow" if shading else "fast",
            resolution,
        )

    def asic_boot(self, *, cold: bool | None = None) -> None:
        """Replay the captured cold-boot sequence.

        The Windows driver performs no soft reset and never writes ``0x0E``-
        ``0x10``, so neither does this.
        """
        del cold
        self.protocol.write_ahb(_BOOT_BLOB_ADDR_A, _BOOT_BLOB_A)
        self.protocol.write_ahb(_BOOT_BLOB_ADDR_B, _BOOT_BLOB_B)
        self._write_many(dict(self.model.init_regs))
        self._write_many(dict(self.model.memory_layout_regs))
        self.set_frontend_init()
        self._write_many(dict(self.model.gpo_regs))
        logger.info(
            "GL128 boot: %d init + %d layout + %d gpo registers",
            len(self.model.init_regs),
            len(self.model.memory_layout_regs),
            len(self.model.gpo_regs),
        )

    def init(self, *, force: bool = False) -> None:
        if self._initialized and not force:
            return
        self.asic_boot()
        self.upload_tables(resolution=max(self.model.resolutions_dpi))
        self.last_afe = None
        self.asic_shading_ready = False
        self.last_ir_host_white = None
        self.ir_host_flatten_ready = False
        self.usb_planar_rgb = False
        self._initialized = True
        logger.info("GL128 initialised (%s)", self.model.model)

    # --- lamp / infrared ------------------------------------------------

    def set_scan_method(self, method: ScanMethod) -> None:
        if method not in ("transparency", "infrared"):
            raise ValueError(f"Unsupported scan method {method!r}")
        self._scan_method = method
        logger.debug("GL128 scan_method=%s", method)

    def _apply_infrared(self, *, enabled: bool) -> None:
        r = self.registers
        if enabled:
            self._update_bits(r.REG_IR, set_bits=r.IR_LED)
        else:
            self._update_bits(r.REG_IR, clear_bits=r.IR_LED)

    def lamp_on(self) -> None:
        """Power the lamp for the selected method.

        Infrared runs with the white lamp off and ``0x37`` bit 2 set; visible
        passes are the reverse. ``XPASEL`` stays set either way.
        """
        r = self.registers
        infrared = self._scan_method == "infrared"
        lamp = r.XPASEL if infrared else (r.XPASEL | r.LAMPPWR)
        self._write(r.REG_0x03, lamp)
        self._apply_infrared(enabled=infrared)
        logger.info("GL128 lamp on (%s)", self._scan_method)

    def lamp_off(self) -> None:
        r = self.registers
        if not self._initialized:
            logger.debug("GL128 lamp_off before init — nothing to do")
            return
        self._write(r.REG_0x03, r.XPASEL)
        self._apply_infrared(enabled=False)
        logger.info("GL128 lamp off")

    def update_home_sensor_gpio(self) -> None:
        """No-op: the SE captures show no GPIO poke around scan start."""

    # --- motion ---------------------------------------------------------

    def feed_steps_for_mm(self, distance_mm: float) -> int:
        """Convert millimetres to steps (experimental; prefer capture constants)."""
        limit_mm = max(0.0, min(float(distance_mm), self.model.max_feed_mm))
        if limit_mm != distance_mm:
            logger.warning(
                "Clamped feed %.2f mm to %.2f mm (model max_feed_mm)",
                distance_mm,
                limit_mm,
            )
        return int(limit_mm * self.model.feed_steps_per_inch / MM_PER_INCH)

    def _upload_fast_slopes(self) -> None:
        """Upload the fast motor ramp to both AHB slope windows."""
        slope = _u16_table_bytes(SLOPE_TABLE_FAST)
        r = self.registers
        self.protocol.write_ahb(r.AHB_SLOPE_SCAN, slope)
        self.protocol.write_ahb(r.AHB_SLOPE_FAST, slope)

    def wait_until_at_home(self, *, timeout_s: float = 60.0) -> None:
        """Poll ``0x101`` until the carriage is home and the motor is idle.

        Used after an image (or cancel) that armed ``AGOHOME`` — session 08/10
        park walk ``0xa5`` → ``0xad`` → ``0xec``.
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            status = self.read_status_reliable()
            if status.is_at_home and not status.is_motor_enabled:
                self._park_ok = True
                return
            time.sleep(HOME_POLL_S)
        raise MotorTimeoutError(
            f"Carriage did not reach home within {timeout_s:.0f}s (AGOHOME park)"
        )

    def warm_prepare(self) -> None:
        """Session-10 style warm re-init while already at home (``0x02=0x78``)."""
        r = self.registers
        self._write(r.REG_0x01, 0x22)
        self._write(r.REG_0x02, 0x78)

    def _feed_capture(
        self,
        steps: int,
        *,
        timeout_s: float = 30.0,
        require_motion: bool = True,
    ) -> None:
        """Replay the captured fast-feed recipe (see MOTOR.md).

        Does **not** write ``0x0d`` before ``0x0f`` — feeds in the capture start
        with ``0x0f = 0x01`` alone. Completion is the vendor probe at
        ``wIndex=0x21`` returning ``0x04``.
        """
        steps = max(0, int(steps))
        if steps == 0:
            return
        max_steps = int(self.model.max_feed_steps)
        if steps > max_steps:
            raise AsicError(
                f"Refusing FEEDL={steps}: larger than any captured feed "
                f"({max_steps}). See captures/8200i-se/MOTOR.md."
            )

        r = self.registers
        setup = dict(_FEED_SETUP_REGS)
        self._write_many(setup)
        self.protocol.write_u24(r.REG_FEEDL, steps)
        self._write(r.REG_0x02, r.MTRPWR | r.FASTFED)  # 0x18
        self._upload_fast_slopes()
        # This recipe deliberately does not clear the counter, so the probe and
        # FEEDFSH still carry the previous feed's completion. Sample them now so
        # the wait below knows not to believe the first "done" it sees.
        stale_done = self._feed_done_indicated()
        before = self.read_status_reliable()
        # Capture: start feed with 0x0f only — no 0x0d counter clear here.
        self._write(r.REG_START, r.START_GO)

        # Sanity-check: require motion to last at least a fraction of the
        # reference feed time. This caps the minimum to keep tests responsive.
        min_motion_s: float | None = None
        if require_motion:
            ref_steps = max(1, int(self.model.feed_to_reference_steps))
            # Session 03 shows ~1s for 28292 steps; we keep this conservative.
            expected_s = 1.0 * (steps / ref_steps) * 0.9
            min_motion_s = min(0.25, max(0.05, expected_s))

        self._wait_feed_probe_done(
            steps=steps,
            timeout_s=timeout_s,
            stale_done=stale_done,
            require_motion=require_motion,
            min_motion_s=min_motion_s,
        )
        self._write(r.REG_0x02, r.FASTFED)  # 0x08 after move
        self.protocol.write_u24(r.REG_FEEDL, 1)
        after = self.read_status_reliable()
        logger.info(
            "GL128 feed of %d steps complete (status 0x%02x -> 0x%02x, "
            "home %s -> %s)",
            steps,
            before.raw,
            after.raw,
            before.is_at_home,
            after.is_at_home,
        )

    def _read_feed_probe(self) -> int:
        try:
            return self.protocol.read_request_register(_FEED_PROBE_INDEX)
        except Exception:  # noqa: BLE001 — fall back to status
            return -1

    def _feed_done_indicated(self) -> bool:
        """True when probe/status currently claim a feed has finished."""
        if self._read_feed_probe() == _FEED_PROBE_DONE:
            return True
        status = self.read_status_reliable()
        return status.is_feeding_finished and not status.is_motor_enabled

    def _wait_feed_probe_done(
        self,
        *,
        steps: int,
        timeout_s: float,
        stale_done: bool = False,
        require_motion: bool = True,
        min_motion_s: float | None = None,
    ) -> None:
        """Wait for a feed to finish.

        When ``require_motion`` is true (positioning feeds), the wait is
        capture-faithful: it refuses to accept a stale ``0x21=0x04``
        completion. Instead, it requires observing motor motion on the
        `0x101` status register at least once before accepting completion.
        """
        deadline = time.monotonic() + timeout_s
        motion_seen = False
        motion_start_t: float | None = None
        while time.monotonic() < deadline:
            probe = self._read_feed_probe()
            status = self.read_status_reliable()
            if not require_motion:
                if probe == _FEED_PROBE_DONE:
                    return
                if status.is_feeding_finished and not status.is_motor_enabled:
                    return
            else:
                if not motion_seen:
                    # Phase 1: observe motion (use 0x101's MOTORENB bit).
                    if status.is_motor_enabled:
                        motion_seen = True
                        motion_start_t = time.monotonic()
                else:
                    # Phase 2: observe completion, but optionally wait a minimum
                    # motion duration so we do not accept a stale completion
                    # too early.
                    if (
                        min_motion_s is not None
                        and motion_start_t is not None
                        and (time.monotonic() - motion_start_t) < min_motion_s
                    ):
                        time.sleep(HOME_POLL_S)
                        continue
                    if probe == _FEED_PROBE_DONE:
                        return
                    if status.is_feeding_finished and not status.is_motor_enabled:
                        return
            time.sleep(HOME_POLL_S)
        # Feed timed out — clear SCAN and drop motor power (not an AGOHOME park).
        self.stop_motor()
        try:
            self._update_bits(self.registers.REG_0x02, clear_bits=self.registers.MTRPWR)
        except Exception as exc:  # noqa: BLE001
            logger.warning("GL128 feed timeout motor clear: %s", exc)
        if require_motion and stale_done and not motion_seen:
            raise MotorTimeoutError(
                f"Feed of {steps} steps timed out without observing motor "
                f"motion, while stale completion was already visible on the "
                f"vendor probe (wIndex=0x21 -> 0x04)."
            )
        raise MotorTimeoutError(
            f"Feed of {steps} steps did not finish within {timeout_s:.0f}s"
        )

    def feed(self, steps: int, *, timeout_s: float = 30.0) -> None:
        """Move the carriage ``steps`` using the capture-faithful feed recipe."""
        self._require_motor_enabled()
        self._feed_capture(steps, timeout_s=timeout_s)

    def position_for_full_frame_scan(
        self,
        *,
        scan_steps: int | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        """From home: feed to reference, then to the scan-start line.

        Replays ``28292`` then ``scan_steps`` (default session-04 full-frame
        ``13704``; crop-dependent values from session 09). Requires home.
        """
        self._require_motor_enabled()
        if not getattr(self, "_park_ok", True):
            raise AsicError(
                "GL128 park failed in the previous scan (AGOHOME timed out). "
                "Power-cycle the scanner or park with SilverFast before "
                "running another SE scan."
            )
        start = self.read_status_reliable()
        if not start.is_at_home:
            raise AsicError(
                "SE carriage is not at home. There is no capture-proven "
                "standalone reverse-home; park with SilverFast or power-cycle, "
                "then retry from home."
            )
        self.warm_prepare()
        second = (
            int(self.model.feed_to_scan_steps)
            if scan_steps is None
            else int(scan_steps)
        )
        first = int(self.model.feed_to_reference_steps)
        logger.info(
            "GL128 positioning from home (status 0x%02x): %d then %d steps",
            start.raw,
            first,
            second,
        )
        self._feed_capture(first, timeout_s=timeout_s / 2, require_motion=True)
        self._feed_capture(second, timeout_s=timeout_s / 2, require_motion=True)
        end = self.read_status_reliable()
        logger.info("GL128 positioned for scan (status 0x%02x)", end.raw)
        if end.is_at_home:
            raise AsicError(
                "GL128 still reads at-home after feeding the positioning pair "
                f"({first}+{second} steps). The carriage likely did not move, "
                "so the scan would not cover the film."
            )

    def stop_motor(self) -> None:
        """Abort / end an image pass the way SilverFast does (sessions 03 + 08).

        When ``AGOHOME`` is armed: lamp strobe on ``0x03``, write ``0x01=0x22``,
        finish the strobe, leave ``0x02`` / ``FEEDL`` alone, then wait for park.
        Without ``AGOHOME`` (e.g. feed timeout): clear ``SCAN`` only — no
        invented strobe. Mid-feed abort is not capture-proven.
        """
        r = self.registers
        if not self._initialized:
            logger.debug("GL128 stop_motor before init — nothing to do")
            return
        try:
            reg02 = self._reg_cache.get(r.REG_0x02)
            if reg02 is None:
                try:
                    reg02 = self.protocol.read_register(r.REG_0x02)
                except Exception:  # noqa: BLE001
                    reg02 = 0
            if reg02 & r.AGOHOME:
                for value in _CANCEL_LAMP_PRE:
                    self._write(r.REG_0x03, value)
                self._write(r.REG_0x01, _CANCEL_REG01)
                for value in _CANCEL_LAMP_POST:
                    self._write(r.REG_0x03, value)
                try:
                    self.wait_until_at_home(timeout_s=60.0)
                    self._park_ok = True
                    logger.info("GL128 parked at home after the scan")
                except MotorTimeoutError as exc:
                    self._park_ok = False
                    logger.error(
                        "GL128 did not park at home: %s. Home is the origin "
                        "for the next scan's feeds, so power-cycle or park "
                        "with SilverFast before scanning again.",
                        exc,
                    )
            else:
                self._update_bits(r.REG_0x01, clear_bits=r.SCAN)
        except Exception as exc:  # noqa: BLE001
            self._park_ok = False
            logger.error("GL128 stop_motor: %s (carriage position unknown)", exc)

    def home(self, *, timeout_s: float = 30.0, wait: bool = True) -> None:
        """No-op when already home; otherwise refuse.

        Captures return home only via ``AGOHOME`` on the image pass
        (``0x02 = 0x30``). A standalone ``FEEDL=0`` seek is not proven and
        previously caused grinding when invented.
        """
        del timeout_s, wait
        self._require_motor_enabled()
        if self.read_status_reliable().is_at_home:
            logger.debug("GL128 already at home")
            return
        raise AsicError(
            "GL128 has no capture-proven standalone home seek. Park with "
            "SilverFast or power-cycle so the carriage is at home, then continue. "
            "See captures/8200i-se/MOTOR.md."
        )

    def park(self, *, timeout_s: float = 30.0) -> None:
        del timeout_s
        self._require_motor_enabled()
        if not self.read_status_reliable().is_at_home:
            raise AsicError(
                "GL128 park needs the carriage already at home (no reverse-home "
                "recipe in captures). Use SilverFast or power-cycle first."
            )
        self.lamp_off()
