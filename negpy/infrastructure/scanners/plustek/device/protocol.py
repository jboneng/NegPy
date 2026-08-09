# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared FilmModel / AsicDriver protocols for multi-model Genesys support."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from negpy.infrastructure.scanners.plustek.asic.status import ScannerStatus
    from negpy.infrastructure.scanners.plustek.usb.protocol import GenesysUsbProtocol

ScanMethod = Literal["transparency", "infrared"]


@dataclass(frozen=True)
class MotorProfile:
    """Per-model motor slope parameters (SANE ``tables_motor.cpp``)."""

    initial_w: int
    max_w: int
    slope_steps: int
    step_type: int  # StepType::QUARTER = 2
    vref: int
    slope_table_max: int


# Default GL845 OpticFilm profile (8200i / 7400 / …)
DEFAULT_GL845_MOTOR = MotorProfile(
    initial_w=64102 * 4,
    max_w=400 * 4,
    slope_steps=100,
    step_type=2,
    vref=3,
    slope_table_max=1024,
)


@runtime_checkable
class FilmModel(Protocol):
    """Structural interface for OpticFilm model tables + caps."""

    name: str
    vendor: str
    model: str
    asic: str
    usb_vendor_id: int
    usb_product_id: int
    scan_ready: bool

    resolutions_dpi: tuple[int, ...]
    bpp_gray: tuple[int, ...]
    bpp_color: tuple[int, ...]
    supports_infrared: bool

    x_size_mm: float
    y_size_mm: float
    x_offset_ta_mm: float
    y_offset_ta_mm: float
    x_size_ta_mm: float
    y_size_ta_mm: float

    x_size_calib_mm: float
    y_size_calib_ta_mm: float
    y_offset_calib_white_ta_mm: float
    y_offset_sensor_to_ta_mm: float

    ld_shift_r: int
    ld_shift_g: int
    ld_shift_b: int

    stagger_y_by_dpi: Mapping[int, tuple[int, ...]]
    register_dpiset_by_dpi: Mapping[int, int]
    output_pixel_offset_by_dpi: Mapping[int, int]

    register_dpihw: int
    exposure_lperiod: int
    motor_base_ydpi: int
    optical_resolution: int
    motor_profile: MotorProfile

    init_regs: Mapping[int, int]
    sensor_custom_regs: Mapping[int, int]
    frontend_regs: Mapping[int, int]
    gpo_regs: Mapping[int, int]
    memory_layout_regs: Mapping[int, int]

    @property
    def max_area_mm(self) -> tuple[float, float]: ...

    def boot_register_map(self) -> dict[int, int]: ...


@runtime_checkable
class AsicDriver(Protocol):
    """Chip ops used by Scanner / ScanSession / Calibrator."""

    _initialized: bool
    _reg_cache: dict[int, int]
    _scan_method: ScanMethod
    model: FilmModel
    protocol: GenesysUsbProtocol

    def read_status(self) -> ScannerStatus: ...

    def read_status_reliable(self) -> ScannerStatus: ...

    def is_at_home(self) -> bool: ...

    def is_cold_boot(self) -> bool: ...

    def init(self, *, force: bool = False) -> None: ...

    def set_frontend_init(self) -> None: ...

    def set_scan_method(self, method: ScanMethod) -> None: ...

    def lamp_on(self) -> None: ...

    def lamp_off(self) -> None: ...

    def home(self, *, timeout_s: float = ..., wait: bool = True) -> None: ...

    def park(self, *, timeout_s: float = ...) -> None: ...

    def stop_motor(self) -> None: ...

    def update_home_sensor_gpio(self) -> None: ...
