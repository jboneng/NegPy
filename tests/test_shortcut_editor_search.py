from negpy.desktop.view.shortcut_editor_search import (
    build_shortcut_editor_targets,
    filter_targets,
)


def test_build_targets_includes_action_description():
    targets = build_shortcut_editor_targets()
    export = next(t for t in targets if t.target_id == "export")
    assert export.label == "Export"
    assert "export" in export.search_text


def test_build_targets_includes_slider_group_label():
    targets = build_shortcut_editor_targets()
    density = next(t for t in targets if t.target_id == "density")
    assert density.label == "Density ↑/↓"
    assert density.row_kind == "slider"
    assert "density" in density.search_text


def test_search_matches_current_key_binding():
    targets = build_shortcut_editor_targets({"density_up": "Q", "density_down": "A"})
    matches = filter_targets(targets, "q")
    assert any(t.target_id == "density" for t in matches)


def test_search_matches_category_name():
    targets = build_shortcut_editor_targets()
    matches = filter_targets(targets, "finishing")
    assert any(t.target_id == "border_size" for t in matches)


def test_search_matches_ctrl_binding():
    targets = build_shortcut_editor_targets({"export": "Ctrl+E"})
    matches = filter_targets(targets, "ctrl+e")
    assert any(t.target_id == "export" for t in matches)
