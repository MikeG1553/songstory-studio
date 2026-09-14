from __future__ import annotations

from typing import Any


DIRECTORS_BIBLE_FIELDS = [
    "story_arc",
    "protagonist_description",
    "era",
    "locations",
    "color_palette",
    "recurring_motifs",
    "positive_visual_cues",
    "negative_visual_cues",
    "tone_progression",
    "ending_mood",
]


def create_directors_bible(storyboard: dict[str, Any]) -> dict[str, Any]:
    visual_world = storyboard.get("visual_world", {})
    locations = visual_world.get("locations") or []
    motifs = visual_world.get("recurring_motifs") or visual_world.get("motifs") or []

    return {
        "story_arc": storyboard.get("story_arc", ""),
        "protagonist_description": visual_world.get("protagonist_description", ""),
        "era": visual_world.get("era", ""),
        "locations": locations,
        "color_palette": infer_color_palette(visual_world),
        "recurring_motifs": motifs,
        "positive_visual_cues": visual_world.get("positive_cues", []),
        "negative_visual_cues": visual_world.get("negative_cues", []),
        "tone_progression": storyboard.get("mood", ""),
        "ending_mood": infer_ending_mood(storyboard),
    }


def infer_color_palette(visual_world: dict[str, Any]) -> list[str]:
    name = str(visual_world.get("name", "")).lower()

    if "western" in name:
        return [
            "dusty ochre",
            "weathered charcoal",
            "faded denim",
            "storm gray",
            "blood red accents",
            "candle amber",
        ]

    return [
        "natural earth tones",
        "muted blue-gray",
        "warm practical light",
        "deep shadow",
    ]


def infer_ending_mood(storyboard: dict[str, Any]) -> str:
    scenes = storyboard.get("scenes", [])

    if scenes:
        final_scene = scenes[-1]
        return str(final_scene.get("mood") or final_scene.get("purpose") or "").strip()

    return "Resolved but emotionally resonant."


def apply_directors_bible(storyboard: dict[str, Any]) -> dict[str, Any]:
    if not storyboard.get("directors_bible"):
        storyboard["directors_bible"] = create_directors_bible(storyboard)

    return storyboard


def normalize_bible_lists(bible: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(bible)

    for key in [
        "locations",
        "color_palette",
        "recurring_motifs",
        "positive_visual_cues",
        "negative_visual_cues",
    ]:
        value = normalized.get(key, [])
        if isinstance(value, str):
            normalized[key] = [
                item.strip()
                for item in value.split(",")
                if item.strip()
            ]
        elif value is None:
            normalized[key] = []
        else:
            normalized[key] = list(value)

    for key in DIRECTORS_BIBLE_FIELDS:
        normalized.setdefault(key, [] if key in {
            "locations",
            "color_palette",
            "recurring_motifs",
            "positive_visual_cues",
            "negative_visual_cues",
        } else "")

    return normalized
