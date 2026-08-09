# SPDX-License-Identifier: GPL-3.0-or-later
"""Device models and selection."""

from negpy.infrastructure.scanners.plustek.device.model_7200 import MODEL_7200, Model7200
from negpy.infrastructure.scanners.plustek.device.model_7200i import MODEL_7200_V2, MODEL_7200I, Model7200i
from negpy.infrastructure.scanners.plustek.device.model_7300 import MODEL_7300, MODEL_7400_V1, Model7300
from negpy.infrastructure.scanners.plustek.device.model_7400 import MODEL_7400, MODEL_8100, Model7400
from negpy.infrastructure.scanners.plustek.device.model_7500i import MODEL_7500I, MODEL_7600I_V1, Model7500i
from negpy.infrastructure.scanners.plustek.device.model_8200i import MODEL_8200I, Model8200i
from negpy.infrastructure.scanners.plustek.device.model_8200i_se import MODEL_8200I_SE, Model8200iSE
from negpy.infrastructure.scanners.plustek.device.protocol import AsicDriver, FilmModel, MotorProfile
from negpy.infrastructure.scanners.plustek.device.select import (
    KNOWN_MODELS,
    MODEL_7600I_V2,
    create_asic,
    model_for_device,
    model_for_pid,
    model_is_scan_ready,
)

__all__ = [
    "AsicDriver",
    "FilmModel",
    "KNOWN_MODELS",
    "MODEL_7200",
    "MODEL_7200I",
    "MODEL_7200_V2",
    "MODEL_7300",
    "MODEL_7400",
    "MODEL_7400_V1",
    "MODEL_7500I",
    "MODEL_7600I_V1",
    "MODEL_7600I_V2",
    "MODEL_8100",
    "MODEL_8200I",
    "MODEL_8200I_SE",
    "Model7200",
    "Model7200i",
    "Model7300",
    "Model7400",
    "Model7500i",
    "Model8200i",
    "Model8200iSE",
    "MotorProfile",
    "create_asic",
    "model_for_device",
    "model_for_pid",
    "model_is_scan_ready",
]
