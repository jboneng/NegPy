# SPDX-License-Identifier: GPL-3.0-or-later
"""Logging helpers."""

from __future__ import annotations

import logging

_LOGGER_NAME = "negpy.infrastructure.scanners.plustek"


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a child logger under the ``negpy.infrastructure.scanners.plustek`` namespace."""
    if name is None or name == _LOGGER_NAME:
        return logging.getLogger(_LOGGER_NAME)
    if name.startswith(f"{_LOGGER_NAME}."):
        return logging.getLogger(name)
    return logging.getLogger(f"{_LOGGER_NAME}.{name}")


def enable_debug_logging() -> None:
    """Attach a simple stderr handler at DEBUG for bring-up sessions."""
    logger = get_logger()
    if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
