import sys
import unittest
from unittest.mock import MagicMock

from PyQt6.QtWidgets import QApplication

from negpy.desktop.view.canvas.toolbar import ActionToolbar

if not QApplication.instance():
    _app = QApplication(sys.argv)


def _make_toolbar() -> ActionToolbar:
    controller = MagicMock()
    controller.session = MagicMock()
    controller.session.state = MagicMock()
    controller.session.state.gpu_enabled = False
    controller.session.state.hq_preview = False
    controller.session.state.compare_mode = False
    controller.session.state.flat_peek = False
    controller.session.state.selected_file_idx = 0
    controller.session.state.undo_index = 0
    controller.session.state.max_history_index = 0
    controller.session.state.clipboard = None
    controller.session.state.config.geometry.flip_horizontal = False
    controller.session.state.config.geometry.flip_vertical = False
    controller.session.state.canvas_bg_index = 0
    controller.session.asset_model.actual_to_display.return_value = 0
    controller.session.asset_model.rowCount.return_value = 1
    controller.session.repo.get_global_setting.return_value = 1.0
    controller.canvas = None
    controller.render_worker.processor.backend_name = "CPU"
    return ActionToolbar(controller)


class TestCanvasToolbarResponsive(unittest.TestCase):
    def test_narrow_canvas_collapses_compare_and_undo_groups(self):
        tb = _make_toolbar()
        tb.show()
        QApplication.processEvents()

        tb.set_available_width(640)
        QApplication.processEvents()

        self.assertFalse(tb.btn_compare.isVisible())
        self.assertFalse(tb.btn_undo.isVisible())
        self.assertTrue(tb.btn_rot_l.isVisible())
        self.assertTrue(tb._ov_compare_action.isVisible())
        self.assertTrue(tb._ov_undo_action.isVisible())
        self.assertLessEqual(tb._pill_width(), tb._toolbar_width_budget(640))

    def test_mid_canvas_keeps_most_controls(self):
        tb = _make_toolbar()
        tb.show()
        QApplication.processEvents()

        tb.set_available_width(800)
        QApplication.processEvents()

        self.assertTrue(tb.btn_undo.isVisible())
        self.assertTrue(tb.btn_hq.isVisible())
        self.assertTrue(tb.btn_rot_l.isVisible())
        self.assertFalse(tb.btn_compare.isVisible())
        self.assertLessEqual(tb._pill_width(), tb._toolbar_width_budget(800))

    def test_wide_canvas_shows_full_toolbar(self):
        tb = _make_toolbar()
        tb.show()
        QApplication.processEvents()

        tb.set_available_width(1200)
        QApplication.processEvents()

        self.assertTrue(tb.btn_compare.isVisible())
        self.assertTrue(tb.btn_undo.isVisible())
        self.assertTrue(tb.btn_zoom_fit.isVisible())
        self.assertFalse(tb._ov_undo_action.isVisible())


if __name__ == "__main__":
    unittest.main()
