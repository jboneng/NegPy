"""Search index for shortcut dialogs (Customize and overview)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from PyQt6.QtCore import QModelIndex, QPoint, Qt, QSortFilterProxyModel
from PyQt6.QtGui import QStandardItemModel
from PyQt6.QtWidgets import QCompleter, QScrollArea, QWidget

from negpy.desktop.view.shortcut_registry import (
    REGISTRY,
    EditorRowSlider,
    ShortcutEntry,
    category_editor_rows,
)

TARGET_ROLE = Qt.ItemDataRole.UserRole
SEARCH_ROLE = Qt.ItemDataRole.UserRole + 1
HIGHLIGHT_MS = 1800

RowKind = Literal["single", "slider"]


class ShortcutSearchProxy(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._query = ""

    def set_query(self, query: str) -> None:
        needle = (query or "").strip().casefold()
        if needle == self._query:
            return
        self._query = needle
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # noqa: N802
        if not self._query:
            return False
        model = self.sourceModel()
        if model is None:
            return False
        index = model.index(source_row, 0, source_parent)
        search_text = model.data(index, SEARCH_ROLE) or ""
        return self._query in search_text


def target_id_from_completer_index(
    completer: QCompleter,
    search_model: QStandardItemModel,
    search_proxy: ShortcutSearchProxy,
    index: QModelIndex,
) -> str:
    if not index.isValid():
        return ""
    completion_model = completer.completionModel()
    proxy_index = completion_model.mapToSource(index)
    source_index = search_proxy.mapToSource(proxy_index)
    if not source_index.isValid():
        return ""
    target_id = search_model.data(source_index, TARGET_ROLE)
    return str(target_id) if target_id else ""


def first_matching_target_id(
    completer: QCompleter,
    search_model: QStandardItemModel,
    search_proxy: ShortcutSearchProxy,
) -> str:
    completion_model = completer.completionModel()
    for row in range(completion_model.rowCount()):
        target_id = target_id_from_completer_index(completer, search_model, search_proxy, completion_model.index(row, 0))
        if target_id:
            return target_id
    return ""


def scroll_row_to_center(scroll: QScrollArea, row: QWidget) -> None:
    content = scroll.widget()
    if content is None:
        return
    center = row.mapTo(content, QPoint(0, row.height() // 2))
    bar = scroll.verticalScrollBar()
    viewport_h = scroll.viewport().height()
    value = center.y() - viewport_h // 2
    value = max(bar.minimum(), min(bar.maximum(), value))
    bar.setValue(value)


@dataclass(frozen=True)
class ShortcutEditorTarget:
    target_id: str
    label: str
    category: str
    search_text: str
    row_kind: RowKind


def _tokens(*parts: str) -> str:
    return " ".join(p.strip() for p in parts if p and str(p).strip()).casefold()


def _binding_tokens(bindings: dict[str, str], *action_ids: str) -> str:
    return " ".join(bindings.get(action_id, "") for action_id in action_ids if bindings.get(action_id, ""))


def build_shortcut_editor_targets(bindings: dict[str, str] | None = None) -> list[ShortcutEditorTarget]:
    """Build navigable editor rows with precomputed search text (includes current bindings)."""
    resolved = bindings or {}
    targets: list[ShortcutEditorTarget] = []
    seen_categories: dict[str, list[tuple[str, ShortcutEntry]]] = {}
    for action_id, entry in REGISTRY.items():
        seen_categories.setdefault(entry.category, []).append((action_id, entry))

    for category, items in seen_categories.items():
        for editor_row in category_editor_rows(items):
            if isinstance(editor_row, EditorRowSlider):
                group = editor_row.group
                inc = REGISTRY[group.inc_action]
                dec = REGISTRY[group.dec_action]
                targets.append(
                    ShortcutEditorTarget(
                        target_id=group.id,
                        label=group.label,
                        category=category,
                        row_kind="slider",
                        search_text=_tokens(
                            group.label,
                            group.id,
                            category,
                            inc.description,
                            dec.description,
                            inc.default_key,
                            dec.default_key,
                            _binding_tokens(resolved, group.inc_action, group.dec_action),
                        ),
                    )
                )
                continue

            action_id = editor_row.action_id
            entry = editor_row.entry
            targets.append(
                ShortcutEditorTarget(
                    target_id=action_id,
                    label=entry.description,
                    category=category,
                    row_kind="single",
                    search_text=_tokens(
                        entry.description,
                        action_id,
                        category,
                        entry.default_key,
                        _binding_tokens(resolved, action_id),
                    ),
                )
            )

    return targets


def filter_targets(targets: list[ShortcutEditorTarget], query: str) -> list[ShortcutEditorTarget]:
    needle = query.strip().casefold()
    if not needle:
        return list(targets)
    return [target for target in targets if needle in target.search_text]
