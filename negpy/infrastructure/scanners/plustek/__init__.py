# SPDX-License-Identifier: GPL-3.0-or-later
"""Plustek USB driver — raw access for OpticFilm (Genesys) scanners."""

from __future__ import annotations

from negpy.infrastructure.scanners.plustek._version import __version__
from negpy.infrastructure.scanners.plustek.device.model_8200i import MODEL_8200I, Model8200i
from negpy.infrastructure.scanners.plustek.device.model_8200i_se import MODEL_8200I_SE, Model8200iSE
from negpy.infrastructure.scanners.plustek.device.select import KNOWN_MODELS
from negpy.infrastructure.scanners.plustek.exceptions import (
    AsicError,
    CalibrationError,
    DeviceNotFoundError,
    DriverBindingError,
    MotorTimeoutError,
    PlustekError,
    ScanCancelled,
    ScanError,
    UnsupportedDeviceError,
    UsbError,
)
from negpy.infrastructure.scanners.plustek.image import ScanImage
from negpy.infrastructure.scanners.plustek.scanner import Scanner

__all__ = [
    "KNOWN_MODELS",
    "MODEL_8200I",
    "MODEL_8200I_SE",
    "AsicError",
    "CalibrationError",
    "DeviceNotFoundError",
    "DriverBindingError",
    "Model8200i",
    "Model8200iSE",
    "MotorTimeoutError",
    "PlustekError",
    "ScanCancelled",
    "ScanError",
    "ScanImage",
    "Scanner",
    "UnsupportedDeviceError",
    "UsbError",
    "__version__",
]
