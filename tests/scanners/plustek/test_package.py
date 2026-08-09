# SPDX-License-Identifier: GPL-3.0-or-later
"""Package import and version smoke tests."""

from negpy.infrastructure.scanners.plustek import Scanner, __version__
from negpy.infrastructure.scanners.plustek.device import MODEL_8200I
from negpy.infrastructure.scanners.plustek.usb.device import UsbDeviceHandle, UsbDeviceInfo
from negpy.infrastructure.scanners.plustek.usb.protocol import GenesysUsbProtocol


def test_version():
    assert __version__ == "0.1.1"


def test_public_api_exports():
    from negpy.infrastructure.scanners.plustek import MODEL_8200I, ScanImage, Scanner

    assert MODEL_8200I.usb_product_id == 0x130D
    assert Scanner is not None
    assert ScanImage is not None


def test_model_8200i_identity():
    assert MODEL_8200I.usb_vendor_id == 0x07B3
    assert MODEL_8200I.usb_product_id == 0x130D
    assert MODEL_8200I.asic == "GL845"
    assert 3600 in MODEL_8200I.resolutions_dpi
    assert MODEL_8200I.max_area_mm == (36.33, 25.0)
    assert MODEL_8200I.gpo_regs[0x6B] == 0x30
    assert MODEL_8200I.frontend_regs[0x00] == 0xF8


def test_scanner_wraps_handle_and_exposes_advanced():
    info = UsbDeviceInfo(vendor_id=0x07B3, product_id=0x130D, bus=1, address=1)
    handle = UsbDeviceHandle(info)
    # Pretend claimed without touching real USB.
    handle._dev = object()
    handle._claimed = True

    class TinyTransport:
        def control_msg(self, *a, **k):
            return b"\x30\x55"

        def bulk_read(self, size, **k):
            return b"\x00" * size

        def bulk_write(self, data, **k):
            return len(data)

    scanner = Scanner(handle, GenesysUsbProtocol(TinyTransport()))
    assert scanner.device_id.endswith(":001:001")
    assert scanner.advanced.read_register(0x6B) == 0x30
    scanner.close()
