from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from negpy.desktop.view.shortcut_registry import (
    REGISTRY,
    EditorRowSingle,
    EditorRowSlider,
    ShortcutEntry,
    category_editor_rows,
    default_bindings,
    default_slider_steps,
)
from negpy.desktop.view.widgets.collapsible import CollapsibleSection
from negpy.desktop.view.widgets.key_sequence_edit import KeypadAwareKeySequenceEdit
from negpy.desktop.view.styles.theme import THEME


def _categories_in_order() -> list[tuple[str, list[tuple[str, ShortcutEntry]]]]:
    ordered: list[tuple[str, list[tuple[str, ShortcutEntry]]]] = []
    index: dict[str, int] = {}
    for action_id, entry in REGISTRY.items():
        if entry.category not in index:
            index[entry.category] = len(ordered)
            ordered.append((entry.category, []))
        ordered[index[entry.category]][1].append((action_id, entry))
    return ordered


def _format_default_pair(inc_key: str, dec_key: str) -> str:
    inc = inc_key or "—"
    dec = dec_key or "—"
    return f"{inc} / {dec}"


class ShortcutEditorDialog(QDialog):
    def __init__(self, bindings: dict[str, str], slider_steps: dict[str, float] | None = None, parent=None, session=None):
        super().__init__(parent)
        self._initial_bindings = dict(bindings)
        self._initial_slider_steps = dict(slider_steps or default_slider_steps())
        self._session = session
        self._edits: dict[str, KeypadAwareKeySequenceEdit] = {}
        self._step_edits: dict[str, QDoubleSpinBox] = {}
        self.setWindowTitle("Customize Shortcuts")
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
            "Set shortcuts and keyboard step sizes for slider actions. "
            "Duplicate bindings are rejected. Reset All restores defaults."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        self._invert_zoom_chk = QCheckBox("Reverse scroll-to-zoom direction (scroll up zooms out)")
        self._invert_zoom_chk.setToolTip(
            "Flip the mouse-wheel zoom direction on the image viewer: scroll up to zoom out, scroll down to zoom in."
        )
        if self._session is not None:
            self._invert_zoom_chk.setChecked(bool(getattr(self._session.state, "invert_zoom_scroll", False)))
        else:
            self._invert_zoom_chk.setEnabled(False)
        root.addWidget(self._invert_zoom_chk)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet(f"color: {THEME.border_color};")
        root.addWidget(divider)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        container = QWidget()
        sections_layout = QVBoxLayout(container)
        sections_layout.setContentsMargins(0, 0, 0, 0)
        sections_layout.setSpacing(THEME.space_sm)

        for category, items in _categories_in_order():
            section = CollapsibleSection(category, expanded=False)
            section.set_content(self._build_category_grid(items))
            sections_layout.addWidget(section)

        sections_layout.addStretch()
        scroll.setWidget(container)
        root.addWidget(scroll, stretch=1)

        buttons = QHBoxLayout()
        reset_btn = QPushButton("Reset All")
        reset_btn.clicked.connect(self._reset_all)
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(reset_btn)
        buttons.addStretch()
        buttons.addWidget(cancel_btn)
        buttons.addWidget(save_btn)
        root.addLayout(buttons)

    def _build_category_grid(self, items: list[tuple[str, ShortcutEntry]]) -> QWidget:
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
                self._add_slider_row(grid, row, editor_row, mono)
            else:
                self._add_single_row(grid, row, editor_row, mono)

        return body

    def _add_single_row(self, grid: QGridLayout, row: int, editor_row: EditorRowSingle, mono: str) -> None:
        action_id = editor_row.action_id
        entry = editor_row.entry
        grid.addWidget(QLabel(entry.description), row, 0)
        default_lbl = QLabel(entry.default_key or "—")
        default_lbl.setStyleSheet(mono)
        grid.addWidget(default_lbl, row, 1)
        edit = self._make_key_edit(action_id, entry.default_key)
        grid.addWidget(edit, row, 2)
        grid.addWidget(QLabel("—"), row, 3)

    def _add_slider_row(self, grid: QGridLayout, row: int, editor_row: EditorRowSlider, mono: str) -> None:
        group = editor_row.group
        inc_entry = REGISTRY[group.inc_action]
        dec_entry = REGISTRY[group.dec_action]

        grid.addWidget(QLabel(group.label), row, 0)
        default_lbl = QLabel(_format_default_pair(inc_entry.default_key, dec_entry.default_key))
        default_lbl.setStyleSheet(mono)
        grid.addWidget(default_lbl, row, 1)

        shortcuts = QHBoxLayout()
        shortcuts.setContentsMargins(0, 0, 0, 0)
        shortcuts.setSpacing(6)
        inc_edit = self._make_key_edit(group.inc_action, inc_entry.default_key)
        dec_edit = self._make_key_edit(group.dec_action, dec_entry.default_key)
        sep = QLabel("/")
        sep.setStyleSheet(f"color: {THEME.text_muted};")
        shortcuts.addWidget(inc_edit, 1)
        shortcuts.addWidget(sep)
        shortcuts.addWidget(dec_edit, 1)
        shortcuts_host = QWidget()
        shortcuts_host.setLayout(shortcuts)
        grid.addWidget(shortcuts_host, row, 2)
        grid.addWidget(self._make_step_edit(group), row, 3)

    def _make_key_edit(self, action_id: str, default_key: str) -> KeypadAwareKeySequenceEdit:
        edit = KeypadAwareKeySequenceEdit(QKeySequence(self._initial_bindings.get(action_id, default_key)))
        edit.setClearButtonEnabled(True)
        self._edits[action_id] = edit
        return edit

    def _make_step_edit(self, group) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setDecimals(group.step_decimals)
        spin.setMinimum(10 ** -group.step_decimals)
        spin.setMaximum(10_000.0)
        spin.setSingleStep(group.default_step)
        if group.step_suffix:
            spin.setSuffix(group.step_suffix)
        spin.setValue(self._initial_slider_steps.get(group.id, group.default_step))
        spin.setToolTip("Amount applied per shortcut press")
        self._step_edits[group.id] = spin
        return spin

    def _reset_all(self) -> None:
        for action_id, key in default_bindings().items():
            if action_id in self._edits:
                self._edits[action_id].setKeySequence(QKeySequence(key))
        for group_id, value in default_slider_steps().items():
            if group_id in self._step_edits:
                self._step_edits[group_id].setValue(value)

    def _portable(self, edit: KeypadAwareKeySequenceEdit) -> str:
        return edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText)

    def bindings(self) -> dict[str, str]:
        return {action_id: self._portable(edit) for action_id, edit in self._edits.items()}

    def slider_steps(self) -> dict[str, float]:
        return {group_id: float(spin.value()) for group_id, spin in self._step_edits.items()}

    def _save(self) -> None:
        seen: dict[str, str] = {}
        for action_id, edit in self._edits.items():
            key = self._portable(edit)
            if not key:
                continue
            other = seen.get(key)
            if other is not None:
                QMessageBox.warning(
                    self,
                    "Duplicate Shortcut",
                    f'"{key}" is assigned to both "{REGISTRY[other].description}" and "{REGISTRY[action_id].description}".',
                )
                return
            seen[key] = action_id

        for group_id, spin in self._step_edits.items():
            if spin.value() <= 0:
                QMessageBox.warning(self, "Invalid Step", f"Step size for {group_id} must be greater than zero.")
                return

        if self._session is not None:
            invert = self._invert_zoom_chk.isChecked()
            self._session.state.invert_zoom_scroll = invert
            self._session.repo.save_global_setting("invert_zoom_scroll", invert)

        self.accept()
