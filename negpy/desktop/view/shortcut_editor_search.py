"""Search index for the Customize Shortcuts dialog."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from negpy.desktop.view.shortcut_registry import (
    REGISTRY,
    EditorRowSlider,
    ShortcutEntry,
    category_editor_rows,
)


RowKind = Literal["single", "slider"]


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
