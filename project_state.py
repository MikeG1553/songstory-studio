from __future__ import annotations

from typing import Any


PROJECT_KEYS = [
    "project_dir",
    "audio_path",
    "audio_duration",
    "storyboard",
    "pexels_results",
    "pexels_scene",
    "selected_pexels_clips",
    "downloaded_clip_paths",
    "scene_candidates",
    "scene_selection_status",
    "final_video_path",
    "animatic_path",
    "footage_credits",
    "render_status",
    "visual_review_cache",
]


def clear_project_state(session_state: Any) -> None:
    for key in PROJECT_KEYS:
        session_state.pop(key, None)


def ensure_project_state(session_state: Any) -> None:
    session_state.setdefault("storyboard", None)
    session_state.setdefault("project_dir", None)
    session_state.setdefault("selected_pexels_clips", {})
    session_state.setdefault("downloaded_clip_paths", {})
    session_state.setdefault("scene_candidates", {})
    session_state.setdefault("scene_selection_status", {})
    session_state.setdefault("footage_credits", [])


def reset_footage_state(session_state: Any) -> None:
    for key in [
        "pexels_results",
        "pexels_scene",
        "selected_pexels_clips",
        "downloaded_clip_paths",
        "scene_candidates",
        "scene_selection_status",
        "final_video_path",
        "animatic_path",
        "footage_credits",
        "render_status",
        "visual_review_cache",
    ]:
        session_state.pop(key, None)

    ensure_project_state(session_state)
