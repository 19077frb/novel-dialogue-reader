"""Validate model-supplied dependency plans; structure is not scene truth."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WindowDependency:
    window: str
    scene: str
    depends_on: tuple[str, ...]


def validate_scene_plan(payload: dict, windows: tuple[str, ...]) -> tuple[WindowDependency, ...]:
    if not windows or len(set(windows)) != len(windows):
        raise ValueError("Plan needs distinct ordered windows")
    if set(payload) != {"scenes"} or not isinstance(payload["scenes"], list):
        raise ValueError("Expected explicit scenes plan")
    seen_scenes, seen_windows, result = {}, [], []
    for scene in payload["scenes"]:
        if not isinstance(scene, dict) or set(scene) != {"scene", "windows", "depends_on"}:
            raise ValueError("Invalid scene plan fields")
        name, group, parents = scene["scene"], scene["windows"], scene["depends_on"]
        if not isinstance(name, str) or not name or name in seen_scenes:
            raise ValueError("Duplicate or empty scene")
        if not isinstance(group, list) or not group or not all(isinstance(w, str) for w in group):
            raise ValueError("Each scene requires ordered windows")
        if not isinstance(parents, list) or not all(isinstance(p, str) for p in parents):
            raise ValueError("Invalid scene dependencies")
        if len(parents) != len(set(parents)) or any(p not in seen_scenes for p in parents):
            raise ValueError("Scene dependencies must precede this scene")
        offset = len(seen_windows)
        if tuple(group) != windows[offset : offset + len(group)]:
            raise ValueError("Scenes must cover each window once in original order")
        previous = tuple(seen_scenes[p] for p in parents)
        for window in group:
            result.append(WindowDependency(window, name, previous))
            previous = (window,)
        seen_windows.extend(group)
        seen_scenes[name] = group[-1]
    if tuple(seen_windows) != windows:
        raise ValueError("Scene plan does not cover all windows")
    return tuple(result)


def comparison_plan(windows: tuple[str, ...], *, continuous: bool) -> tuple[WindowDependency, ...]:
    """Explicit baselines, not a claim that independent windows are safe scenes."""
    scenes = (
        [{"scene": "continuous", "windows": list(windows), "depends_on": []}]
        if continuous
        else [
            {"scene": f"independent_{i}", "windows": [w], "depends_on": []}
            for i, w in enumerate(windows)
        ]
    )
    return validate_scene_plan({"scenes": scenes}, windows)
