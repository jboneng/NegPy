# SPDX-License-Identifier: GPL-3.0-or-later
"""Low-level register access for bring-up / debugging."""

from __future__ import annotations

from negpy.infrastructure.scanners.plustek.usb.protocol import GenesysUsbProtocol


class AdvancedRegisters:
    """Explicit advanced namespace exposed as ``Scanner.advanced``."""

    def __init__(self, protocol: GenesysUsbProtocol) -> None:
        self._protocol = protocol

    def read_register(self, address: int) -> int:
        return self._protocol.read_register(address)

    def write_register(self, address: int, value: int) -> None:
        self._protocol.write_register(address, value)

    def write_registers(self, pairs: list[tuple[int, int]]) -> None:
        self._protocol.write_registers(pairs)

    def read_registers(self, addresses: list[int]) -> dict[int, int]:
        return {addr: self.read_register(addr) for addr in addresses}
