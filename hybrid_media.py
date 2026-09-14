from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from footage_selector import SelectionContext, automatic_select_for_scene, update_selection_context
from image_generation import DEFAULT_IMAGE_MODEL, generate_still_image


SOURCE_TYPES = {"stock_video", "generated_still", "either"}


def normalize_source_type(value: str | None) -> str:
    if value in SOURCE_TYPES:
        return str(value)
    return "either"


def select_hybrid_media_for_scene(
    scene: dict[str, Any],
    directors_bible: dict[str, Any],
    search_func: Callable[[str], list[dict[str, Any]]],
    selection_context: SelectionContext,
    stills_dir: str | Path,
    visual_reviewer=None,
    openai_image_key: str = "",
    image_model: str = DEFAULT_IMAGE_MODEL,
    protagonist_reference: dict[str, Any] | None = None,
    visual_review_cache: dict[str, dict[str, Any]] | None = None,
    continuity_summary: str = "",
) -> tuple[dict[str, Any], SelectionContext]:
    preferred = normalize_source_type(scene.get("preferred_source_type"))

    if preferred in {"stock_video", "either"}:
        result = automatic_select_for_scene(
            scene,
            search_func,
            selection_context,
            visual_reviewer=visual_reviewer,
            require_visual_review=visual_reviewer is not None,
            project_context={"directors_bible": directors_bible, "visual_world": {
                "name": directors_bible.get("era", ""),
                "positive_cues": directors_bible.get("positive_visual_cues", []),
                "negative_cues": directors_bible.get("negative_visual_cues", []),
                "protagonist_description": directors_bible.get("protagonist_description", ""),
                "era": directors_bible.get("era", ""),
                "locations": directors_bible.get("locations", []),
                "recurring_motifs": directors_bible.get("recurring_motifs", []),
            }},
            continuity_summary=continuity_summary,
            visual_review_cache=visual_review_cache,
        )

        if result.get("selected"):
            selected = dict(result["selected"])
            selected["source_type"] = "stock_video"
            selected["selection_result"] = result
            return selected, update_selection_context(selection_context, selected)

        if preferred == "stock_video":
            scene = dict(scene)
            scene["stock_fallback_reason"] = result.get("message", "No acceptable stock footage.")

    still_path = Path(stills_dir) / f"scene_{int(scene.get('scene_id') or scene.get('scene', 0)):03d}.png"
    generated = generate_still_image(
        scene,
        directors_bible,
        still_path,
        api_key=openai_image_key,
        model=image_model,
        protagonist_reference=protagonist_reference,
    )
    generated["source_type"] = "generated_still"
    generated["stock_fallback_reason"] = scene.get("stock_fallback_reason", "")
    return generated, selection_context


def preserve_scene_media_state(
    existing: dict[int, dict[str, Any]],
    scene_id: int,
    replacement: dict[str, Any],
) -> dict[int, dict[str, Any]]:
    updated = dict(existing)
    updated[scene_id] = replacement
    return updated


def text_overlay(
    text: str = "",
    start: float = 0.0,
    duration: float = 3.0,
    position: str = "lower_third",
    size_preset: str = "medium",
) -> dict[str, Any]:
    return {
        "text": text,
        "start": start,
        "duration": duration,
        "position": position,
        "size_preset": size_preset,
    }
