# SPDX-License-Identifier: GPL-3.0-or-later
"""OpticFilm 8200i SE model tables (GL128).

SANE genesys has no GL128 command set, so nothing here is ported from SANE.
Every register value is taken from USB captures of the Windows driver stored in
``captures/8200i-se/``; each table below names the session that produced it.
See ``captures/8200i-se/PROTOCOL.md`` for the full protocol synthesis and
``SESSION_LOG.md`` / per-session ``NOTES.md`` for decode detail.

The ASIC is GL124-family, not GL845: the frontend is reached through
``0x51``/``0x5D``/``0x5E``, status lives at ``0x101``, and the geometry
registers are ``LINCNT`` ``0x25``, ``LPERIOD`` ``0x28``, ``DPISET`` ``0x2C``,
``STRPIXEL`` ``0x82`` and ``ENDPIXEL`` ``0x85``.

Two properties of this map are worth knowing before reading the code:

* ``STRPIXEL`` / ``ENDPIXEL`` are in **native 7200 dpi units** and therefore do
  not change with resolution — the captures show byte-identical values for the
  same crop at 1800 and 3600 dpi.
* ``LINCNT`` is **not** in native units. Session 13 shows ``LINCNT / dpi``
  constant at 3.816 across the whole PPI ladder (one crop scanned at eleven
  resolutions) and every capture's bulk buffer holds exactly ``LINCNT / 2``
  rows, but those rows are *not* output lines: the buffer is sampled at twice
  the programmed dpi in Y. The ladder crop is 36.06 x 24.24 mm — a 3:2 35 mm
  frame — so one output line is four ``LINCNT`` units and two buffer rows, and
  Y travel is ``LINCNT x 25.4 / (4 x asic_dpi)``
  (see :attr:`Model8200iSE.image_lincnt_per_line`). Getting this factor wrong
  stretches every scan vertically; the 1200 dpi ladder buffer is 1704 x 2290
  and must render 1704 x 1145.

SilverFast 9 PPI ladder (session ``13_ppi_ladder``): 150, 300, 600, 720, 900,
1200, 1440, 1800, 2400, 3600, 7200. Below 600 dpi the ASIC is programmed like
600 (``DPISET`` floors at 100); the host downsamples. ``STAGGER`` was clear at
every PPI including 7200.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from negpy.infrastructure.scanners.plustek.device.protocol import DEFAULT_GL845_MOTOR, MotorProfile

#: Lowest PPI that gets its own ASIC programming. Session 13: 150 and 300 share
#: the 600 dpi register set (``DPISET=100``).
_MIN_ASIC_DPI = 600

_MM_PER_INCH = 25.4

#: Cold-boot register blast, in ascending address order, from session
#: ``02_cold_boot_open``. The Windows driver sends these 116 registers before
#: touching anything else and performs no soft reset first.
_INIT_REGS: dict[int, int] = {
    0x01: 0x22, 0x02: 0x78, 0x03: 0x20, 0x04: 0x02, 0x05: 0x48, 0x06: 0x18,
    0x07: 0x00, 0x08: 0x00, 0x09: 0x00, 0x0A: 0x40, 0x0B: 0x6C, 0x0C: 0x00,
    0x0D: 0x00, 0x11: 0x00, 0x12: 0x04, 0x13: 0x08, 0x14: 0x01, 0x15: 0x80,
    0x16: 0x27, 0x17: 0x0C, 0x18: 0x10, 0x19: 0x02, 0x1A: 0x00, 0x1B: 0x00,
    0x1C: 0x00, 0x1D: 0x00, 0x1E: 0x10, 0x1F: 0x00, 0x20: 0x0C, 0x21: 0x00,
    0x22: 0x1A, 0x23: 0x00, 0x24: 0x1A, 0x25: 0x00, 0x26: 0x00, 0x27: 0x00,
    0x2B: 0x20, 0x2C: 0x12, 0x2D: 0xC0, 0x30: 0x6F, 0x31: 0x00, 0x32: 0x22,
    0x33: 0x04, 0x34: 0x80, 0x35: 0x2F, 0x36: 0x1C, 0x37: 0xC0, 0x38: 0x44,
    0x39: 0x00, 0x3A: 0x00, 0x3B: 0xFF, 0x3C: 0xFF, 0x3D: 0x00, 0x3E: 0x00,
    0x3F: 0x01, 0x4F: 0x03, 0x52: 0x07, 0x53: 0x09, 0x54: 0x0B, 0x55: 0x01,
    0x56: 0x03, 0x57: 0x05, 0x5A: 0x12, 0x5B: 0x00, 0x5C: 0x40, 0x5E: 0x1F,
    0x5F: 0x05, 0x60: 0x00, 0x61: 0x00, 0x63: 0x20, 0x67: 0x7F, 0x68: 0x7F,
    0x69: 0x01, 0x70: 0x01, 0x71: 0x02, 0x72: 0x03, 0x73: 0x04, 0x74: 0x00,
    0x75: 0x00, 0x76: 0x00, 0x77: 0x00, 0x78: 0x00, 0x79: 0x0F, 0x7A: 0xFF,
    0x7B: 0xFF, 0x7C: 0xFF, 0x7D: 0x00, 0x7E: 0x2A, 0x7F: 0xF8, 0x80: 0x00,
    0x81: 0x22, 0x82: 0x00, 0x83: 0x01, 0x84: 0x18, 0x85: 0x00, 0x86: 0x00,
    0x87: 0x00, 0x93: 0x00, 0x94: 0x00, 0x95: 0x00, 0x9D: 0x08, 0xA0: 0x12,
    0xA4: 0x00, 0xA5: 0x20, 0xA6: 0x00, 0xA7: 0x00, 0xA8: 0x00, 0xA9: 0x00,
    0xAA: 0x00, 0xAB: 0x30, 0xB8: 0x00, 0xB9: 0x38, 0xBA: 0x00, 0xBD: 0x00,
    0xBE: 0x00, 0xBF: 0x00,
}

#: Memory layout block, byte-identical at every resolution captured, so these
#: are per-model constants rather than computed sizes (sessions 03/04/06).
_MEMORY_LAYOUT_REGS: dict[int, int] = {
    0xD0: 0x0A, 0xD1: 0x0A, 0xD2: 0x0A,
    0xE0: 0x00, 0xE1: 0x68, 0xE2: 0x0B, 0xE3: 0x00, 0xE4: 0x0B, 0xE5: 0x01,
    0xE6: 0x15, 0xE7: 0x99, 0xE8: 0x15, 0xE9: 0x9A, 0xEA: 0x20, 0xEB: 0x32,
    0xEC: 0x20, 0xED: 0x33, 0xEE: 0x2A, 0xEF: 0xCB, 0xF0: 0x2A, 0xF1: 0xCC,
    0xF2: 0x35, 0xF3: 0x64, 0xF4: 0x35, 0xF5: 0x65, 0xF6: 0x3F, 0xF7: 0xFD,
    0xF8: 0x05,
}

#: Analog frontend defaults written through ``0x51``/``0x5D``/``0x5E`` during
#: boot. Indices 0x02-0x04 are per-channel offsets and 0x05-0x07 per-channel
#: gains; the driver searches those at calibration time, so boot zeroes them.
_FRONTEND_REGS: dict[int, int] = {
    0x00: 0x00F8,
    0x01: 0x0080,
    0x02: 0x0000,
    0x03: 0x0000,
    0x04: 0x0000,
    0x05: 0x0000,
    0x06: 0x0000,
    0x07: 0x0000,
}

#: GPO block. The SE uses ``0xA2``-``0xAE``; GL845's ``0x6B``-``0x6F`` is never
#: touched. ``0xAF`` is set separately because it doubles as a depth control.
_GPO_REGS: dict[int, int] = {
    0xA2: 0x00, 0xA3: 0x00, 0xA4: 0x00, 0xA6: 0x00, 0xA7: 0x00,
    0xA8: 0x00, 0xA9: 0x00, 0xAA: 0x00, 0xAC: 0x00, 0xAD: 0x01, 0xAE: 0x00,
}

#: Constant overrides the Windows driver applies on top of the boot map before
#: every acquisition, at every resolution captured. Motor, lamp, depth and
#: geometry registers are excluded here — the driver computes those per scan.
_SCAN_REGS: dict[int, int] = {
    0x04: 0x42, 0x05: 0x40, 0x06: 0xF0, 0x0B: 0x4C,
    0x1C: 0x20, 0x1D: 0x80, 0x1E: 0x20,
    0x3B: 0x01,
    0x52: 0x0B, 0x53: 0x0D, 0x54: 0x0F, 0x55: 0x01, 0x56: 0x05, 0x57: 0x07,
    0x5A: 0x31, 0x5B: 0x79,
    0x70: 0x0A, 0x71: 0x0B, 0x72: 0x0C, 0x73: 0x0D,
    0x81: 0x40,
    0x8A: 0x00, 0x8B: 0x00, 0x8C: 0x00, 0x8D: 0x00, 0x8E: 0x00, 0x8F: 0x00,
    0x90: 0x00, 0x91: 0x00, 0x92: 0x00,
    0x114: 0x80, 0x115: 0x80,
}

#: ``DPISET`` (``0x2C``) is ``dpi / 6`` at 600 dpi and above; below 600 the
#: capture programs ``100`` (same as 600). Source: session ``13_ppi_ladder``.
_REGISTER_DPISET_SE: dict[int, int] = {
    150: 100,
    300: 100,
    600: 100,
    720: 120,
    900: 150,
    1200: 200,
    1440: 240,
    1800: 300,
    2400: 400,
    3600: 600,
    7200: 1200,
}

#: Native-unit optical origin is a constant 120, so the per-resolution offset is
#: ``dpi / 60`` (using the ASIC dpi, which floors at 600).
_OUTPUT_PIXEL_OFFSET_SE: dict[int, int] = {
    150: 10,
    300: 10,
    600: 10,
    720: 12,
    900: 15,
    1200: 20,
    1440: 24,
    1800: 30,
    2400: 40,
    3600: 60,
    7200: 120,
}

#: Line period written to ``0x28`` (24-bit BE). Session ``13_ppi_ladder``.
_LPERIOD_BY_DPI: dict[int, int] = {
    150: 11064,
    300: 11064,
    600: 11064,
    720: 11106,
    900: 11170,
    1200: 11277,
    1440: 11362,
    1800: 11490,
    2400: 11703,
    3600: 13407,
    7200: 15963,
}

#: ``0xA5``/``0xAB`` and ``0x2B`` — replayed verbatim from session 13.
_PIXEL_CLOCK_BY_DPI: dict[int, int] = {
    150: 0x02,
    300: 0x02,
    600: 0x02,
    720: 0x02,
    900: 0x02,
    1200: 0x02,
    1440: 0x02,
    1800: 0x02,
    2400: 0x01,
    3600: 0x01,
    7200: 0x01,
}
_DUMMY_BY_DPI: dict[int, int] = {
    150: 0x01,
    300: 0x01,
    600: 0x01,
    720: 0x01,
    900: 0x01,
    1200: 0x02,
    1440: 0x02,
    1800: 0x02,
    2400: 0x03,
    3600: 0x04,
    7200: 0x17,
}

_STAGGER_BY_DPI: dict[int, tuple[int, ...]] = {
    150: (),
    300: (),
    600: (),
    720: (),
    900: (),
    1200: (),
    1440: (),
    1800: (),
    2400: (),
    3600: (),
    7200: (),
}

_ALL_PPI: tuple[int, ...] = (
    7200,
    3600,
    2400,
    1800,
    1440,
    1200,
    900,
    720,
    600,
    300,
    150,
)


@dataclass(frozen=True)
class Model8200iSE:
    name: str = "plustek-opticfilm-8200i-se"
    vendor: str = "PLUSTEK"
    model: str = "OpticFilm 8200i SE"
    asic: str = "GL128"
    usb_vendor_id: int = 0x07B3
    usb_product_id: int = 0x1825

    #: Supported scanner for this release (USB-capture-validated on real HW).
    scan_ready: bool = True

    #: SilverFast 9 PPI list from session ``13_ppi_ladder`` (high → low).
    resolutions_dpi: tuple[int, ...] = _ALL_PPI
    #: Host-facing bit depth after upsample.
    bpp_gray: tuple[int, ...] = (16,)
    bpp_color: tuple[int, ...] = (16,)
    #: USB image samples are 16-bit LE (oracle on session 11a). Registers still
    #: use the DEPTH8 pair (``0x33=0x1F``, ``0xAF=0xFF``); bulk size is
    #: ``LINCNT × width × 3`` so USB line count is ``LINCNT / 2``.
    usb_image_depth: int = 16
    #: When True, image geometry halves ``LINCNT`` for USB rows (session 11).
    usb_image_lincnt_half_lines: bool = True
    #: USB acquire depth for shading / AFE calib passes.
    usb_calib_depth: int = 16
    #: Each USB line is chunky ``RGBRGB…`` (session 11a oracle: film image).
    usb_planar_rgb: bool = False
    #: Host left–right flip so orientation matches SilverFast (sensor order is mirrored).
    mirror_x: bool = True
    #: Captures force DPISET = optical_resolution/6 during shading (always 1200).
    calib_uses_native_dpiset: bool = True
    #: Captures use unaligned widths (e.g. 2478 @ 1800); do not snap to 16.
    pixel_alignment: int = 1
    #: Floor ``ENDPIXEL−STRPIXEL`` to this multiple. Session 13 spans are always
    #: a multiple of 4; without it, mm→pixel math at 1440 yields optical span
    #: 10365 / width 2073 and the USB buffer shears into a diagonal grid.
    optical_span_alignment: int = 4
    supports_infrared: bool = True
    #: PPI below this share the 600 dpi ASIC programming (session 13).
    min_asic_dpi: int = _MIN_ASIC_DPI

    x_size_mm: float = 36.0
    y_size_mm: float = 44.0

    # X geometry is capture-derived: the 1200 dpi full-width preview in session
    # 03 used STRPIXEL 242 / ENDPIXEL 10610, i.e. 1728 output pixels spanning
    # 36.58 mm starting 0.43 mm into the window.
    x_offset_ta_mm: float = 0.43
    x_size_ta_mm: float = 36.58
    # Y window is capture-derived: session 03's preview runs from feed2=13128 to
    # the scan-window end (27636 steps), i.e. 25.59 mm, and sessions 09a/09b are
    # its top and bottom halves. That is one 24 mm frame plus ~0.8 mm of holder
    # at each end, which is the chrome seen above and below the frame in previews.
    # Second-feed steps use :meth:`feed_to_scan_steps_for_area`, not GL845 starty.
    y_offset_ta_mm: float = 28.5
    y_size_ta_mm: float = 25.59

    x_size_calib_mm: float = 36.58
    y_size_calib_ta_mm: float = 2.0
    y_offset_calib_white_ta_mm: float = 0.0
    y_offset_sensor_to_ta_mm: float = 0.0

    # Tri-linear CCD: R leads G leads B by 24 native lines (0.085 mm). Measured
    # on a 1200 dpi Lab scan (scripts/measure_channel_shift.py), where R/G/B sit
    # 4 output lines apart. That is the same spacing as the GL845 8200i's 12/24
    # at base 3600 dpi, i.e. the two models share the sensor.
    ld_shift_r: int = 0
    ld_shift_g: int = 24
    ld_shift_b: int = 48

    #: The captures size ``LINCNT`` for the crop alone — session 03 already stops
    #: exactly on the scan-window end, so there is no room to scan the extra
    #: ``max_shift`` lines a GL845 would. Line-shift alignment crops the output
    #: instead (:meth:`negpy.infrastructure.scanners.plustek.scan.pipeline.ImagePipeline.apply_line_shifts`).
    lincnt_includes_line_shift: bool = False

    #: ``LINCNT`` register units per *output* line on the image pass. The bulk
    #: buffer carries ``LINCNT / 2`` rows, i.e. two rows per output line, because
    #: Y is sampled at twice the programmed dpi; the host averages the pairs
    #: (:func:`negpy.infrastructure.scanners.plustek.scan.pipeline.reduce_y_oversample`). Skipping that
    #: average is what stretched previews 2x vertically.
    image_lincnt_per_line: int = 4

    #: Native lines covered per ASIC output line. Drives exposure and the
    #: shading-pass LINCNT; the image pass uses
    #: :attr:`image_lincnt_per_line` instead.
    y_oversampled: bool = True

    stagger_y_by_dpi: Mapping[int, tuple[int, ...]] = field(
        default_factory=lambda: dict(_STAGGER_BY_DPI)
    )
    register_dpiset_by_dpi: Mapping[int, int] = field(
        default_factory=lambda: dict(_REGISTER_DPISET_SE)
    )
    output_pixel_offset_by_dpi: Mapping[int, int] = field(
        default_factory=lambda: dict(_OUTPUT_PIXEL_OFFSET_SE)
    )
    lperiod_by_dpi: Mapping[int, int] = field(
        default_factory=lambda: dict(_LPERIOD_BY_DPI)
    )
    pixel_clock_by_dpi: Mapping[int, int] = field(
        default_factory=lambda: dict(_PIXEL_CLOCK_BY_DPI)
    )
    dummy_by_dpi: Mapping[int, int] = field(default_factory=lambda: dict(_DUMMY_BY_DPI))

    register_dpihw: int = 1200
    exposure_lperiod: int = 14000
    #: GL845-shaped ``starty`` base only. The SE never feeds ``geometry.starty``;
    #: FEEDL steps use :attr:`feed_steps_per_inch` instead.
    motor_base_ydpi: int = 7200
    optical_resolution: int = 7200

    #: Feed distances written to ``0x3D`` are in ASIC step units. The scale is
    #: pinned by the scan window holding exactly one 35 mm frame: session 03
    #: travels 4836 LINCNT @1200 dpi (25.59 mm) from feed2=13128 to the window
    #: end at 27636, so 14508 steps = 25.59 mm.
    feed_steps_per_inch: int = 14400

    #: Physical Y travel clamp for any *experimental* mm-based feed helper.
    #: Capture-faithful feeds use :attr:`feed_to_reference_steps` /
    #: :attr:`feed_to_scan_steps` and do not go through this clamp.
    max_feed_mm: float = 50.0

    #: First feed from home before every scan (sessions 03–12). Constant.
    feed_to_reference_steps: int = 28292

    #: Second feed for default full-frame colour (sessions 04–06).
    feed_to_scan_steps: int = 13704

    #: Second feed at the top of the TA window (preview / session 09a).
    feed_to_scan_top_steps: int = 13128

    #: Second feed used by session 09b — the *lower half* of the window, not its
    #: bottom edge: 20232 = 13128 + 7104, and 7104 steps is 12.53 mm against the
    #: 13.06 mm height of the 09a crop above it, so the two halves overlap by
    #: half a millimetre and together cover the 25.59 mm window.
    feed_to_scan_bottom_steps: int = 20232

    #: Largest single FEEDL observed in captures; refuse anything larger.
    max_feed_steps: int = 28292

    #: End of the scan window in second-feed step units. Every capture satisfies
    #: ``feed2 + travel_steps <= 27636``; session 03 (preview) and 09b both land
    #: on it to within 0.01 mm, which is what makes it a hard stop rather than a
    #: user crop. The Lab grind overran it by ~12 mm.
    scan_window_end_steps: int = 27636

    #: Capture image ``LINCNT`` for each second-feed distance, kept as a
    #: regression fixture (``scripts/extract_se_feeds.py`` →
    #: ``decoded/ppi_lincnt_feed.json``). These are per-capture *and per-dpi*;
    #: the motor gate uses :meth:`max_lincnt_for`, not this table.
    max_image_lincnt_by_feed2: Mapping[int, int] = field(
        default_factory=lambda: {
            13128: 4836,  # session 03 preview @1200 / 09a @1800 (3700)
            13560: 27476,  # session 13 PPI ladder @7200
            13704: 6628,  # session 04 colour @1800
            20232: 3700,  # session 09b @1800
        }
    )

    #: Session 13 PPI-ladder second feed (crop origin; PPI-independent).
    ladder_feed2_steps: int = 13560

    #: Session 13 image-pass ``LINCNT`` per SilverFast PPI. The UI crop was one
    #: fixed 24.24 mm window (a 35 mm frame); LINCNT tracks PPI because it is in
    #: dpi units.
    ladder_lincnt_by_dpi: Mapping[int, int] = field(
        default_factory=lambda: {
            150: 2292,
            300: 2292,
            600: 2292,
            720: 2748,
            900: 3436,
            1200: 4580,
            1440: 5496,
            1800: 6868,
            2400: 9156,
            3600: 13732,
            7200: 27476,
        }
    )

    motor_profile: MotorProfile = DEFAULT_GL845_MOTOR

    init_regs: Mapping[int, int] = field(default_factory=lambda: dict(_INIT_REGS))
    sensor_custom_regs: Mapping[int, int] = field(
        default_factory=lambda: dict(_SCAN_REGS)
    )
    frontend_regs: Mapping[int, int] = field(default_factory=lambda: dict(_FRONTEND_REGS))
    gpo_regs: Mapping[int, int] = field(default_factory=lambda: dict(_GPO_REGS))
    memory_layout_regs: Mapping[int, int] = field(
        default_factory=lambda: dict(_MEMORY_LAYOUT_REGS)
    )

    @property
    def max_area_mm(self) -> tuple[float, float]:
        return (self.x_size_ta_mm, self.y_size_ta_mm)

    def asic_dpi_for(self, resolution: int) -> int:
        """PPI used for ASIC geometry / tables (floors at :attr:`min_asic_dpi`)."""
        return max(int(resolution), int(self.min_asic_dpi))

    def oversample_for(self, resolution: int) -> int:
        """Native lines the ASIC returns per *ASIC* output line."""
        return max(1, self.optical_resolution // self.asic_dpi_for(resolution))

    def line_period_for(self, resolution: int) -> int:
        """Value for ``LPERIOD`` (``0x28``) at ``resolution``."""
        key = self.asic_dpi_for(resolution)
        return self.lperiod_by_dpi.get(key, self.exposure_lperiod)

    def channel_exposure_for(self, resolution: int) -> int:
        """Per-channel RAM exposure, ``14000 / oversample`` in the captures."""
        return self.exposure_lperiod // self.oversample_for(resolution)

    def feed_to_scan_steps_for_area(
        self,
        area: tuple[float, float, float, float] | None = None,
    ) -> int:
        """Second-feed steps for a normalized TA crop ``(x1,y1,x2,y2)``.

        Default full frame (``area is None``) uses session 04's **13704**.
        Otherwise ``y1`` is a fraction of the scan window, which runs from the
        preview top (**13128**, session 03 / 09a) to the window end
        (:attr:`scan_window_end_steps`) at 14400 steps/inch. Session 09b's
        **20232** falls out of this at ``y1 = 0.49`` — the halfway point of the
        preview, which is exactly the crop it captured.
        """
        if area is None:
            return int(self.feed_to_scan_steps)
        _x1, y1, _x2, _y2 = area
        y1 = max(0.0, min(1.0, float(y1)))
        top = int(self.feed_to_scan_top_steps)
        end = int(self.scan_window_end_steps)
        return int(round(top + y1 * (end - top)))

    def max_lincnt_for_feed2(self, feed2: int) -> int | None:
        """Image ``LINCNT`` captured at this second-feed distance.

        Regression fixture only — the values come from different resolutions, so
        they are not a cap. Use :meth:`max_lincnt_for`. Returns ``None`` when no
        table entry is within 16 steps.
        """
        table = dict(self.max_image_lincnt_by_feed2)
        if not table:
            return None
        if feed2 in table:
            return int(table[feed2])
        nearest = min(table, key=lambda k: abs(int(k) - int(feed2)))
        if abs(int(nearest) - int(feed2)) > 16:
            return None
        return int(table[nearest])

    def max_travel_steps_for_feed2(self, feed2: int) -> int:
        """Steps left between ``feed2`` and the scan-window end."""
        return max(0, int(self.scan_window_end_steps) - int(feed2))

    def max_lincnt_for(self, feed2: int, resolution: int) -> int:
        """Largest image ``LINCNT`` that still stops at the scan-window end.

        Reproduces the captures: ``(13128, 1200)`` → 4836 (session 03) and
        ``(20232, 1800)`` → 3700 (session 09b). The raw step math can leave a
        remainder that is not a multiple of :attr:`image_lincnt_per_line` (four
        for the SE image path); that remainder is floored away so USB row count
        (``LINCNT/2``) and pair averaging stay aligned. Without the snap,
        ``(13128, 1440)`` produced odd ``LINCNT=5803`` and scrambled images.
        """
        asic_dpi = self.asic_dpi_for(resolution)
        steps = self.max_travel_steps_for_feed2(feed2)
        raw = (
            steps * asic_dpi * int(self.image_lincnt_per_line)
        ) // int(self.feed_steps_per_inch)
        per_line = max(1, int(self.image_lincnt_per_line))
        return max(per_line, (int(raw) // per_line) * per_line)

    def travel_mm_for_lincnt(self, lincnt: int, resolution: int) -> float:
        """Physical Y travel of an image pass with ``lincnt`` at ``resolution``."""
        asic_dpi = self.asic_dpi_for(resolution)
        return (
            int(lincnt) * _MM_PER_INCH / (int(self.image_lincnt_per_line) * asic_dpi)
        )

    def lincnt_for_travel_mm(self, travel_mm: float, resolution: int) -> int:
        """Image ``LINCNT`` needed to cover ``travel_mm`` at ``resolution``."""
        asic_dpi = self.asic_dpi_for(resolution)
        lines = round(float(travel_mm) * asic_dpi / _MM_PER_INCH)
        return max(1, lines) * int(self.image_lincnt_per_line)

    def ladder_lincnt_for(self, resolution: int) -> int:
        """Session-13 image ``LINCNT`` for ``resolution`` (exact or nearest PPI)."""
        table = dict(self.ladder_lincnt_by_dpi)
        dpi = int(resolution)
        if dpi in table:
            return int(table[dpi])
        nearest = min(table, key=lambda k: abs(int(k) - dpi))
        return int(table[nearest])

    def boot_register_map(self) -> dict[int, int]:
        """Boot registers: the init blast plus the memory layout block."""
        regs = dict(self.init_regs)
        regs.update(self.memory_layout_regs)
        return regs


MODEL_8200I_SE = Model8200iSE()
