# SPDX-License-Identifier: GPL-3.0-or-later
"""ASIC status decoded from register 0x41."""

from __future__ import annotations

from dataclasses import dataclass

from negpy.infrastructure.scanners.plustek.asic.registers import Gl845Registers


@dataclass(frozen=True)
class ScannerStatus:
    raw: int
    is_replugged: bool
    is_buffer_empty: bool
    is_feeding_finished: bool
    is_scanning_finished: bool
    is_at_home: bool
    is_lamp_on: bool
    is_front_end_busy: bool
    is_motor_enabled: bool

    @classmethod
    def from_reg41(cls, value: int) -> ScannerStatus:
        r = Gl845Registers()
        v = int(value) & 0xFF
        return cls(
            raw=v,
            is_replugged=not bool(v & r.STATUS_PWRBIT),
            is_buffer_empty=bool(v & r.STATUS_BUFEMPTY),
            is_feeding_finished=bool(v & r.STATUS_FEEDFSH),
            is_scanning_finished=bool(v & r.STATUS_SCANFSH),
            is_at_home=bool(v & r.STATUS_HOMESNR),
            is_lamp_on=bool(v & r.STATUS_LAMPSTS),
            is_front_end_busy=bool(v & r.STATUS_FEBUSY),
            is_motor_enabled=bool(v & r.STATUS_MOTORENB),
        )
