# SPDX-License-Identifier: GPL-3.0-or-later
"""Multi-model OpticFilm selection and table smoke tests (no hardware)."""

from __future__ import annotations

from negpy.infrastructure.scanners.plustek.asic.gl842 import Gl842
from negpy.infrastructure.scanners.plustek.asic.gl843 import Gl843
from negpy.infrastructure.scanners.plustek.asic.gl845 import Gl845
from negpy.infrastructure.scanners.plustek.device.model_7200 import MODEL_7200
from negpy.infrastructure.scanners.plustek.device.model_7200i import MODEL_7200_V2, MODEL_7200I
from negpy.infrastructure.scanners.plustek.device.model_7300 import MODEL_7300, MODEL_7400_V1
from negpy.infrastructure.scanners.plustek.device.model_7400 import MODEL_7400, MODEL_8100
from negpy.infrastructure.scanners.plustek.device.model_7500i import MODEL_7500I, MODEL_7600I_V1
from negpy.infrastructure.scanners.plustek.device.model_8200i import MODEL_8200I
from negpy.infrastructure.scanners.plustek.device.select import (
    KNOWN_MODELS,
    MODEL_7600I_V2,
    create_asic,
    model_for_device,
)
from negpy.infrastructure.scanners.plustek.scan.geometry import compute_geometry
from negpy.infrastructure.scanners.plustek.usb.device import (
    PID_OPTICFILM_7200,
    PID_OPTICFILM_7200_V2,
    PID_OPTICFILM_7200I,
    PID_OPTICFILM_7300,
    PID_OPTICFILM_7400,
    PID_OPTICFILM_7500I,
    PID_OPTICFILM_7600I,
    PID_OPTICFILM_8100,
    PID_OPTICFILM_8200I,
    SUPPORTED_IDS,
    VID_PLUSTEK,
)


def test_supported_ids_cover_complete_opticfilm():
    expected = {
        PID_OPTICFILM_8200I,
        PID_OPTICFILM_7200,
        PID_OPTICFILM_7200I,
        PID_OPTICFILM_7200_V2,
        PID_OPTICFILM_7300,
        PID_OPTICFILM_7400,
        PID_OPTICFILM_7500I,
        PID_OPTICFILM_7600I,
        PID_OPTICFILM_8100,
    }
    pids = {pid for vid, pid in SUPPORTED_IDS if vid == VID_PLUSTEK}
    assert expected <= pids


def test_bcd_disambiguation_7400_and_7600i():
    assert model_for_device(PID_OPTICFILM_7400, 0x0400) is MODEL_7400_V1
    assert model_for_device(PID_OPTICFILM_7400, 0x0605).name == MODEL_7400.name
    assert model_for_device(PID_OPTICFILM_7400, 0) is MODEL_7400
    assert model_for_device(PID_OPTICFILM_7600I, 0x0400) is MODEL_7600I_V1
    assert model_for_device(PID_OPTICFILM_7600I, 0x0605).name == MODEL_7600I_V2.name


def test_simple_pid_aliases():
    assert model_for_device(PID_OPTICFILM_8200I) is MODEL_8200I
    assert model_for_device(PID_OPTICFILM_8100) is MODEL_8100
    assert model_for_device(PID_OPTICFILM_7200I) is MODEL_7200I
    assert model_for_device(PID_OPTICFILM_7200_V2) is MODEL_7200_V2
    assert model_for_device(PID_OPTICFILM_7300) is MODEL_7300
    assert model_for_device(PID_OPTICFILM_7500I) is MODEL_7500I
    assert model_for_device(PID_OPTICFILM_7200) is MODEL_7200


def test_create_asic_routing():
    class _Proto:
        pass

    proto = _Proto()  # type: ignore[assignment]
    assert isinstance(create_asic(proto, MODEL_8200I), Gl845)  # type: ignore[arg-type]
    assert isinstance(create_asic(proto, MODEL_7400), Gl845)  # type: ignore[arg-type]
    assert isinstance(create_asic(proto, MODEL_7200I), Gl843)  # type: ignore[arg-type]
    assert isinstance(create_asic(proto, MODEL_7200), Gl842)  # type: ignore[arg-type]


def test_scan_ready_se_only():
    from negpy.infrastructure.scanners.plustek.device.model_8200i_se import MODEL_8200I_SE

    for m in KNOWN_MODELS:
        if m is MODEL_8200I_SE:
            assert m.scan_ready is True
            continue
        assert m.scan_ready is False, f"{m.model} must stay locked out"


def test_geometry_for_each_canonical_model():
    for model in (
        MODEL_8200I,
        MODEL_7400,
        MODEL_7200I,
        MODEL_7300,
        MODEL_7500I,
        MODEL_7200,
    ):
        dpi = model.resolutions_dpi[-1]  # typically lowest
        g = compute_geometry(dpi, model=model)
        assert g.resolution == dpi
        assert g.pixels >= 16
        assert g.lines >= 1
        assert g.register_dpiset == model.register_dpiset_by_dpi[dpi]


def test_infrared_caps():
    assert MODEL_8200I.supports_infrared is True
    assert MODEL_7400.supports_infrared is False
    assert MODEL_7200I.supports_infrared is True
    assert MODEL_7200_V2.supports_infrared is False
    assert MODEL_7300.supports_infrared is False
    assert MODEL_7500I.supports_infrared is True
    assert MODEL_7200.supports_infrared is False


def test_boot_maps_nonempty_for_complete_models():
    for model in (
        MODEL_8200I,
        MODEL_7400,
        MODEL_8100,
        MODEL_7200I,
        MODEL_7300,
        MODEL_7500I,
        MODEL_7200,
    ):
        boot = model.boot_register_map()
        assert boot
        assert 0x05 in boot
