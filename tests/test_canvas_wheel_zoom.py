from __future__ import annotations

import pytest
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
from types import SimpleNamespace
from unittest.mock import patch

from negpy.desktop.view.canvas.widget import WHEEL_ZOOM_NOTCH, apply_wheel_zoom_notches, wheel_notch_delta


def _we(angle_y: int = 0, pixel_y: int = 0, inverted: bool = False) -> QWheelEvent:
    return QWheelEvent(
        QPointF(0, 0),
        QPointF(0, 0),
        QPoint(0, pixel_y),
        QPoint(0, angle_y),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        inverted,
    )


def test_angle_zero_with_pixel_uses_notch_from_pixels() -> None:
    # Natural viewer mapping: negated vs raw Qt
    assert wheel_notch_delta(_we(0, 64)) == pytest.approx(-1.0)
    assert wheel_notch_delta(_we(0, 32)) == pytest.approx(-0.5)


def test_angle_zero_with_no_vertical_delta_returns_zero() -> None:
    assert wheel_notch_delta(_we(0, 0)) == 0.0


def test_120_is_one_notch() -> None:
    assert wheel_notch_delta(_we(120, 0)) == pytest.approx(-1.0)
    assert wheel_notch_delta(_we(-120, 0)) == pytest.approx(1.0)


def test_inverted_flips() -> None:
    assert wheel_notch_delta(_we(120, 0, True)) == pytest.approx(1.0)
    assert wheel_notch_delta(_we(-120, 0, True)) == pytest.approx(-1.0)


@patch("negpy.desktop.view.canvas.widget.APP_CONFIG", new=SimpleNamespace(canvas_zoom_min=0.25, canvas_zoom_max=8.0))
def test_apply_wheel_proportional_notches() -> None:
    assert apply_wheel_zoom_notches(2.0, 0.5) == pytest.approx(2.0 * (WHEEL_ZOOM_NOTCH**0.5))


@patch("negpy.desktop.view.canvas.widget.APP_CONFIG", new=SimpleNamespace(canvas_zoom_min=0.25, canvas_zoom_max=8.0))
def test_apply_wheel_hits_clamp() -> None:
    assert apply_wheel_zoom_notches(7.5, 2.0) == 8.0
    assert apply_wheel_zoom_notches(0.3, -2.0) == 0.25
