# SPDX-License-Identifier: GPL-3.0-or-later
"""In-tree PlustekBackend contract tests (no hardware required)."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import numpy as np
import pytest

from negpy.infrastructure.scanners.base import ScannerUnavailable, TransientScanError
from negpy.infrastructure.scanners.params import ScanParams
from negpy.infrastructure.scanners.plustek.exceptions import ScanCancelled, UsbError
from negpy.infrastructure.scanners.plustek.image import ScanImage
from negpy.infrastructure.scanners.plustek.usb.device import (
    PID_OPTICFILM_8200I_SE,
    VID_PLUSTEK,
    UsbDeviceInfo,
)
from negpy.infrastructure.scanners.plustek_backend import PlustekBackend
from negpy.infrastructure.scanners.result import ScanResult

_DEVICE_ID = "plustek:usb:07b3:1825:002:006"
_BACKEND = "negpy.infrastructure.scanners.plustek_backend"


def _params(**kwargs) -> ScanParams:
    base = dict(dpi=1800, depth=16, capture_ir=False)
    base.update(kwargs)
    return ScanParams(**base)


def _info() -> UsbDeviceInfo:
    return UsbDeviceInfo(
        vendor_id=VID_PLUSTEK,
        product_id=PID_OPTICFILM_8200I_SE,
        bus=2,
        address=6,
    )


def _patch_enum(monkeypatch, devices: list[UsbDeviceInfo] | None = None) -> None:
    devices = devices if devices is not None else [_info()]
    monkeypatch.setattr(f"{_BACKEND}.find_devices", lambda supported_only=True: list(devices))
    monkeypatch.setattr(f"{_BACKEND}.list_devices", lambda: list(devices))


def _fake_scanner(*, progress_steps: int = 0, scan_error: Exception | None = None):
    from negpy.infrastructure.scanners.plustek.device.model_8200i_se import MODEL_8200I_SE

    rgb = np.zeros((8, 8, 3), dtype=np.uint16)
    image = ScanImage(rgb=rgb, dpi=1800, device_model="PLUSTEK OpticFilm 8200i SE")

    scanner = MagicMock()
    scanner.model = MODEL_8200I_SE
    scanner.asic._initialized = True
    scanner.asic.is_at_home.return_value = True

    def scan(**kwargs):
        cancel = kwargs.get("cancel")
        if cancel is not None and cancel.is_set():
            raise ScanCancelled("cancelled")
        progress = kwargs.get("progress")
        if progress is not None:
            for i in range(1, progress_steps + 1):
                progress(i / progress_steps)
        if scan_error is not None:
            raise scan_error
        mode = kwargs.get("mode", "color")
        if mode == "infrared":
            return ScanImage(
                rgb=rgb,
                dpi=kwargs.get("resolution", 1800),
                device_model=image.device_model,
                ir=rgb[:, :, 1].copy(),
            )
        return ScanImage(
            rgb=rgb,
            dpi=kwargs.get("resolution", 1800),
            device_model=image.device_model,
        )

    scanner.scan.side_effect = scan
    scanner.close = MagicMock()
    return scanner


class _FakeOpen:
    def __init__(self, scanner):
        self._scanner = scanner

    def __call__(self, *args, **kwargs):
        return self

    def __enter__(self):
        return self._scanner

    def __exit__(self, *exc):
        self._scanner.close()
        return False


def test_backend_list_devices_empty(monkeypatch):
    _patch_enum(monkeypatch, [])
    assert PlustekBackend().list_devices() == []


def test_backend_list_devices_maps_caps(monkeypatch):
    _patch_enum(monkeypatch)
    devices = PlustekBackend().list_devices()
    assert len(devices) == 1
    dev = devices[0]
    assert dev.id == _info().device_id
    assert "Transparency" in [str(s) for s in dev.capabilities.sources]
    assert 3600 in dev.capabilities.supported_dpi
    assert dev.capabilities.can_eject is False
    assert dev.capabilities.exposure_time_us is None
    assert dev.capabilities.ir_channel is True


def test_refresh_devices_re_enumerates(monkeypatch):
    _patch_enum(monkeypatch)
    backend = PlustekBackend()
    assert backend.refresh_devices() == backend.list_devices()


def test_eject_returns_false():
    assert PlustekBackend().eject(_DEVICE_ID) is False


def test_unavailable_without_pyusb(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "usb.core" or (name == "usb" and fromlist):
            raise ImportError("simulated missing pyusb")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ScannerUnavailable, match="PyUSB"):
        PlustekBackend()


def test_scan_returns_well_formed_result(monkeypatch):
    _patch_enum(monkeypatch)
    scanner = _fake_scanner(progress_steps=2)
    monkeypatch.setattr(f"{_BACKEND}.Scanner.open", _FakeOpen(scanner))
    result = PlustekBackend().scan(_DEVICE_ID, _params(), lambda _: None, threading.Event())
    assert isinstance(result, ScanResult)
    assert result.rgb.ndim == 3 and result.rgb.shape[2] == 3
    assert result.dpi == 1800
    assert result.device_model


def test_scan_honours_pre_set_cancel(monkeypatch):
    _patch_enum(monkeypatch)
    scanner = _fake_scanner()
    monkeypatch.setattr(f"{_BACKEND}.Scanner.open", _FakeOpen(scanner))
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Exception, match="[Cc]ancel"):
        PlustekBackend().scan(_DEVICE_ID, _params(), lambda _: None, cancel)


def test_progress_stays_within_unit_range(monkeypatch):
    _patch_enum(monkeypatch)
    scanner = _fake_scanner(progress_steps=4)
    monkeypatch.setattr(f"{_BACKEND}.Scanner.open", _FakeOpen(scanner))
    seen: list[float] = []
    PlustekBackend().scan(_DEVICE_ID, _params(), seen.append, threading.Event())
    assert seen
    assert all(0.0 <= v <= 1.0 for v in seen)


def test_transport_glitches_are_typed_transient(monkeypatch):
    _patch_enum(monkeypatch)
    scanner = _fake_scanner(scan_error=UsbError("Error during device I/O"))
    monkeypatch.setattr(f"{_BACKEND}.Scanner.open", _FakeOpen(scanner))
    with pytest.raises(TransientScanError):
        PlustekBackend().scan(_DEVICE_ID, _params(), lambda _: None, threading.Event())


def test_real_errors_are_not_transient(monkeypatch):
    _patch_enum(monkeypatch)
    scanner = _fake_scanner()
    monkeypatch.setattr(f"{_BACKEND}.Scanner.open", _FakeOpen(scanner))
    with pytest.raises(Exception) as excinfo:
        PlustekBackend().scan(
            _DEVICE_ID,
            _params(frame=3),
            lambda _: None,
            threading.Event(),
        )
    assert not isinstance(excinfo.value, TransientScanError)
    assert "frame" in str(excinfo.value).lower()


def test_capture_ir_returns_ir_plane(monkeypatch):
    _patch_enum(monkeypatch)
    scanner = _fake_scanner()
    monkeypatch.setattr(f"{_BACKEND}.Scanner.open", _FakeOpen(scanner))
    result = PlustekBackend().scan(
        _DEVICE_ID,
        _params(capture_ir=True),
        lambda _: None,
        threading.Event(),
    )
    assert result.ir is not None
    assert result.ir.ndim == 2
    assert scanner.scan.call_count == 2


def test_open_session_shape(monkeypatch):
    _patch_enum(monkeypatch)
    scanner = _fake_scanner()
    monkeypatch.setattr(f"{_BACKEND}.Scanner.open", lambda *a, **k: scanner)
    backend = PlustekBackend()
    session = backend.open_session(_DEVICE_ID)
    try:
        assert session.device_id == _DEVICE_ID
        for method in ("scan", "eject", "close", "__enter__", "__exit__"):
            assert callable(getattr(session, method, None))
    finally:
        session.close()


def test_session_scans_on_held_handle(monkeypatch):
    _patch_enum(monkeypatch)
    scanner = _fake_scanner()
    monkeypatch.setattr(f"{_BACKEND}.Scanner.open", lambda *a, **k: scanner)
    backend = PlustekBackend()
    with backend.open_session(_DEVICE_ID) as session:
        result = session.scan(_params(), lambda _: None, threading.Event())
    assert isinstance(result, ScanResult)
    scanner.close.assert_called()


def test_session_close_is_idempotent(monkeypatch):
    _patch_enum(monkeypatch)
    scanner = _fake_scanner()
    monkeypatch.setattr(f"{_BACKEND}.Scanner.open", lambda *a, **k: scanner)
    backend = PlustekBackend()
    session = backend.open_session(_DEVICE_ID)
    session.close()
    session.close()


def test_backend_scan_refuses_while_session_held(monkeypatch):
    _patch_enum(monkeypatch)
    scanner = _fake_scanner()
    monkeypatch.setattr(f"{_BACKEND}.Scanner.open", lambda *a, **k: scanner)
    backend = PlustekBackend()
    session = backend.open_session(_DEVICE_ID)
    try:
        with pytest.raises(RuntimeError, match="held"):
            backend.scan(_DEVICE_ID, _params(), lambda _: None, threading.Event())
    finally:
        session.close()
