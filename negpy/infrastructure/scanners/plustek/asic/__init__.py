# SPDX-License-Identifier: GPL-3.0-or-later
"""ASIC register maps and chip operations."""

from negpy.infrastructure.scanners.plustek.asic.gl128 import Gl128
from negpy.infrastructure.scanners.plustek.asic.gl842 import Gl842
from negpy.infrastructure.scanners.plustek.asic.gl843 import Gl843
from negpy.infrastructure.scanners.plustek.asic.gl845 import Gl845
from negpy.infrastructure.scanners.plustek.asic.registers import Gl845Registers
from negpy.infrastructure.scanners.plustek.asic.status import ScannerStatus

__all__ = ["Gl128", "Gl842", "Gl843", "Gl845", "Gl845Registers", "ScannerStatus"]
