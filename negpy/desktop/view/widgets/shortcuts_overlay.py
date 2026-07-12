from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from negpy.desktop.view.shortcut_registry import (
    REGISTRY,
    EditorRowSingle,
    EditorRowSlider,
    categories_in_order,
    category_editor_rows,
    slider_step_for,
)
from negpy.desktop.view.styles.theme import THEME
from negpy.desktop.view.widgets.collapsible import CollapsibleSection


def _format_key_pair(inc_key: str, dec_key: str) -> str:
    inc = inc_key or "—"
    dec = dec_key or "—"
    return f"{inc} / {dec}"


def _format_step_value(group, value: float) -> str:
    if group.step_decimals == 0:
        text = str(int(value)) if value == int(value) else str(value)
    else:
        text = f"{value:.{group.step_decimals}f}".rstrip("0").rstrip(".")
    suffix = group.step_suffix or ""
    return f"{text}{suffix}" if text else "—"


class ShortcutsOverlay(QDialog):
    """Modal keyboard shortcut reference, opened with '?'."""

    def __init__(self, shortcut_manager, parent=None):
        super().__init__(parent)
        self._shortcut_manager = shortcut_manager
        self.setWindowTitle("Keyboard Shortcuts")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint)
        self.setModal(True)
        self.resize(820, 720)
        self._init_ui()

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        self.setStyleSheet(f"""
            QDialog {{ background-color: {THEME.bg_panel}; }}
            QLabel {{ color: {THEME.text_primary}; font-size: 12px; }}
            QPushButton {{ padding: 6px 14px; }}
        """)

        intro = QLabel(
            "Current keyboard shortcuts and slider step sizes. "
            "Expand a section to browse bindings, or open Customize to change them."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet(f"color: {THEME.border_color};")
        root.addWidget(divider)

        bindings = self._shortcut_manager.bindings
        slider_steps = self._shortcut_manager.slider_steps

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        container = QWidget()
        sections_layout = QVBoxLayout(container)
        sections_layout.setContentsMargins(0, 0, 0, 0)
        sections_layout.setSpacing(THEME.space_sm)

        for category, items in categories_in_order():
            section = CollapsibleSection(category, expanded=False)
            section.set_content(self._build_category_grid(items, bindings, slider_steps))
            sections_layout.addWidget(section)

        sections_layout.addStretch()
        scroll.setWidget(container)
        root.addWidget(scroll, stretch=1)

        actions = QHBoxLayout()
        customize_btn = QPushButton("Customize")
        customize_btn.clicked.connect(self._customize)
        actions.addWidget(customize_btn)
        actions.addStretch()

        close_btn = QPushButton("Close")
        close_btn.setProperty("primary", True)
        close_btn.clicked.connect(self.accept)
        actions.addWidget(close_btn)
        root.addLayout(actions)

    def _customize(self) -> None:
        if self._shortcut_manager.open_editor(self):
            self.accept()

    def _build_category_grid(
        self,
        items: list,
        bindings: dict[str, str],
        slider_steps: dict[str, float],
    ) -> QWidget:
        body = QWidget()
        grid = QGridLayout(body)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)

        header_style = (
            f"color: {THEME.text_muted}; font-size: {THEME.font_size_xs}px; "
            f"font-weight: {THEME.weight_semibold};"
        )
        for col, label in enumerate(("Action", "Default", "Shortcut", "Step")):
            hdr = QLabel(label)
            hdr.setStyleSheet(header_style)
            grid.addWidget(hdr, 0, col)

        mono = f"color: {THEME.text_secondary}; font-family: Consolas, monospace;"
        for row, editor_row in enumerate(category_editor_rows(items), start=1):
            if isinstance(editor_row, EditorRowSlider):
                self._add_slider_row(grid, row, editor_row, bindings, slider_steps, mono)
            else:
                self._add_single_row(grid, row, editor_row, bindings, mono)

        return body

    def _keycap(self, text: str) -> QLabel:
        lbl = QLabel(text or "—")
        lbl.setStyleSheet(f"""
            color: {THEME.text_primary};
            background-color: {THEME.bg_header};
            border: 1px solid {THEME.border_primary};
            border-radius: 3px;
            font-family: Consolas, monospace;
            font-size: 11px;
            padding: 2px 6px;
        """)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return lbl

    def _mono_label(self, text: str, mono: str) -> QLabel:
        lbl = QLabel(text or "—")
        lbl.setStyleSheet(mono)
        return lbl

    def _add_single_row(
        self,
        grid: QGridLayout,
        row: int,
        editor_row: EditorRowSingle,
        bindings: dict[str, str],
        mono: str,
    ) -> None:
        entry = editor_row.entry
        grid.addWidget(QLabel(entry.description), row, 0)
        grid.addWidget(self._mono_label(entry.default_key, mono), row, 1)
        grid.addWidget(self._keycap(bindings.get(editor_row.action_id, "")), row, 2)
        grid.addWidget(QLabel("—"), row, 3)

    def _add_slider_row(
        self,
        grid: QGridLayout,
        row: int,
        editor_row: EditorRowSlider,
        bindings: dict[str, str],
        slider_steps: dict[str, float],
        mono: str,
    ) -> None:
        group = editor_row.group
        inc_entry = REGISTRY[group.inc_action]
        dec_entry = REGISTRY[group.dec_action]

        grid.addWidget(QLabel(group.label), row, 0)
        grid.addWidget(
            self._mono_label(_format_key_pair(inc_entry.default_key, dec_entry.default_key), mono),
            row,
            1,
        )

        shortcuts = QHBoxLayout()
        shortcuts.setContentsMargins(0, 0, 0, 0)
        shortcuts.setSpacing(6)
        shortcuts.addWidget(self._keycap(bindings.get(group.inc_action, "")), 1)
        sep = QLabel("/")
        sep.setStyleSheet(f"color: {THEME.text_muted};")
        shortcuts.addWidget(sep)
        shortcuts.addWidget(self._keycap(bindings.get(group.dec_action, "")), 1)
        shortcuts_host = QWidget()
        shortcuts_host.setLayout(shortcuts)
        grid.addWidget(shortcuts_host, row, 2)

        step_value = slider_step_for(group.id, slider_steps)
        grid.addWidget(QLabel(_format_step_value(group, step_value)), row, 3)
