from __future__ import annotations

import base64
import re
import textwrap
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


DEFAULT_IMAGE_MODEL = "gpt-image-2.5-flare"
HERO_IMAGE_MODEL = "gpt-image-2.5-sunburst"
REFERENCE_IMAGE_MODEL = HERO_IMAGE_MODEL

PROTAGONIST_REFERENCE_CUES = [
    "facial appearance",
    "approximate age",
    "hair/facial hair",
    "hat",
    "dark duster/coat",
    "body type",
    "overall Western-era appearance",
]

PROTAGONIST_TERMS = {
    "protagonist",
    "drifter",
    "outlaw",
    "rider",
    "cowboy",
    "stranger",
    "gunman",
    "gunslinger",
}

PROTAGONIST_VISUAL_PHRASES = {
    "protagonist",
    "bounty hunter",
    "drifter",
    "gunslinger",
    "gunman",
    "cowboy",
    "rider",
    "outlaw",
}


def scene_includes_protagonist(scene: dict[str, Any]) -> bool:
    if "contains_protagonist" in scene:
        return bool(scene.get("contains_protagonist"))

    text = " ".join(
        str(scene.get(key, ""))
        for key in [
            "recommended_visual",
            "visual",
        ]
    ).lower()
    if any(phrase in text for phrase in PROTAGONIST_VISUAL_PHRASES):
        return True
    tokens = set(re.findall(r"[a-z]+", text))
    return any(term in tokens for term in PROTAGONIST_TERMS)


def _reference_path(protagonist_reference: dict[str, Any] | str | Path | None) -> str:
    if not protagonist_reference:
        return ""
    if isinstance(protagonist_reference, (str, Path)):
        return str(protagonist_reference)
    return str(
        protagonist_reference.get("reference_image_path")
        or protagonist_reference.get("generated_image_path")
        or protagonist_reference.get("path")
        or ""
    )


def _base_generation_metadata(
    provider: str,
    requested_model: str,
    actual_model: str,
    generation_mode: str,
    uses_reference: bool,
    reference_path: str = "",
) -> dict[str, Any]:
    return {
        "provider": provider,
        "model": actual_model,
        "requested_model": requested_model,
        "requested_image_model": requested_model,
        "actual_image_model": actual_model,
        "generation_mode": generation_mode,
        "uses_protagonist_reference": uses_reference,
        "protagonist_reference_path": reference_path if uses_reference else "",
        "reference_image_identifier": reference_path if uses_reference else "",
    }


def _decode_image_response(response: Any, output: Path) -> None:
    image_b64 = response.data[0].b64_json
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(base64.b64decode(image_b64))


def _api_error_details(exc: Exception) -> dict[str, str]:
    code = getattr(exc, "code", "") or getattr(exc, "type", "") or exc.__class__.__name__
    return {
        "message": str(exc),
        "code": str(code),
    }


def build_protagonist_reference_prompt(directors_bible: dict[str, Any]) -> str:
    positive = ", ".join(directors_bible.get("positive_visual_cues", []))
    negative = ", ".join(directors_bible.get("negative_visual_cues", []))
    palette = ", ".join(directors_bible.get("color_palette", []))

    return "\n".join(
        [
            "Create one reusable character reference image for the principal protagonist.",
            f"Protagonist description: {directors_bible.get('protagonist_description', '')}",
            f"Era / visual world: {directors_bible.get('era', '')}",
            f"Color palette: {palette}",
            f"Positive cues: {positive}",
            f"Negative cues to avoid: {negative}",
            "Single weathered Western-era male character, neutral pose, clear face and wardrobe.",
            "Preserve a hat, dark duster/coat, rugged body type, and period-authentic styling.",
            "No text, no modern clothing, no music-video performance setup.",
        ]
    )


def build_still_prompt(
    scene: dict[str, Any],
    directors_bible: dict[str, Any],
    protagonist_reference: dict[str, Any] | str | Path | None = None,
) -> str:
    positive = ", ".join(directors_bible.get("positive_visual_cues", []))
    negative = ", ".join(directors_bible.get("negative_visual_cues", []))
    motifs = ", ".join(directors_bible.get("recurring_motifs", []))
    locations = ", ".join(directors_bible.get("locations", []))
    palette = ", ".join(directors_bible.get("color_palette", []))
    includes_protagonist = scene_includes_protagonist(scene)
    reference_path = _reference_path(protagonist_reference) if includes_protagonist else ""
    continuity_lines = []

    if includes_protagonist and reference_path:
        continuity_lines.extend(
            [
                f"Use protagonist reference image for character continuity: {reference_path}",
                "Preserve the protagonist reference: "
                + ", ".join(PROTAGONIST_REFERENCE_CUES)
                + ".",
            ]
        )
    elif includes_protagonist:
        continuity_lines.append(
            "Keep the protagonist consistent with the Director's Bible description, including "
            + ", ".join(PROTAGONIST_REFERENCE_CUES)
            + "."
        )

    return "\n".join(
        [
            "Create one cinematic still frame for a story-driven music film.",
            f"Visual world / era: {directors_bible.get('era', '')}",
            f"Protagonist: {directors_bible.get('protagonist_description', '')}",
            f"Locations: {locations}",
            f"Color palette: {palette}",
            f"Recurring motifs: {motifs}",
            f"Scene purpose: {scene.get('story_purpose') or scene.get('purpose', '')}",
            f"Lyric or musical moment: {scene.get('lyric_or_musical_moment') or scene.get('lyric_excerpt', '')}",
            f"Recommended visual: {scene.get('recommended_visual') or scene.get('visual', '')}",
            f"Camera composition: {scene.get('camera', 'wide cinematic composition, no text')}",
            f"Positive cues: {positive}",
            f"Negative cues to avoid: {negative}",
            *continuity_lines,
            "No lip sync, no performer-to-camera music video setup, no visible text.",
            "16:9 cinematic realism, coherent character and period continuity.",
        ]
    )


def generate_placeholder_still(
    prompt: str,
    output_path: str | Path,
    size: tuple[int, int] = (1280, 720),
) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, (26, 24, 22))
    draw = ImageDraw.Draw(image)

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
        small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
    except Exception:
        font = ImageFont.load_default()
        small = ImageFont.load_default()

    draw.rectangle((0, 0, size[0], size[1]), fill=(30, 28, 26))
    draw.rectangle((40, 40, size[0] - 40, size[1] - 40), outline=(110, 92, 70), width=3)
    draw.text((70, 70), "Generated Still Placeholder", fill=(235, 220, 190), font=font)
    y = 130
    for line in textwrap.wrap(prompt, width=95)[:18]:
        draw.text((70, y), line, fill=(210, 205, 195), font=small)
        y += 28

    image.save(output)
    return output


def generate_still_image(
    scene: dict[str, Any],
    directors_bible: dict[str, Any],
    output_path: str | Path,
    api_key: str = "",
    model: str = DEFAULT_IMAGE_MODEL,
    protagonist_reference: dict[str, Any] | str | Path | None = None,
    reference_model: str = REFERENCE_IMAGE_MODEL,
    openai_client: Any | None = None,
) -> dict[str, Any]:
    reference_path = _reference_path(protagonist_reference)
    uses_reference = scene_includes_protagonist(scene) and bool(reference_path)
    generation_mode = "reference_edit" if uses_reference else "text_generation"
    actual_model = reference_model if uses_reference else model
    prompt = build_still_prompt(scene, directors_bible, protagonist_reference=protagonist_reference)
    output = Path(output_path)

    if not api_key:
        path = generate_placeholder_still(prompt, output)
        return {
            "scene_id": scene.get("scene_id") or scene.get("scene"),
            "image_prompt": prompt,
            "generated_image_path": str(path),
            "generation_metadata": {
                **_base_generation_metadata(
                    "placeholder",
                    model,
                    "local-placeholder",
                    generation_mode,
                    uses_reference,
                    reference_path,
                ),
            },
        }

    if openai_client is None:
        from openai import OpenAI

        openai_client = OpenAI(api_key=api_key)

    metadata = _base_generation_metadata(
        "openai",
        model,
        actual_model,
        generation_mode,
        uses_reference,
        reference_path,
    )

    try:
        if uses_reference:
            with Path(reference_path).open("rb") as reference_file:
                response = openai_client.images.edit(
                    model=actual_model,
                    image=reference_file,
                    prompt=prompt,
                    size="1536x1024",
                )
        else:
            response = openai_client.images.generate(
                model=actual_model,
                prompt=prompt,
                size="1536x864",
            )
        _decode_image_response(response, output)
    except Exception as exc:
        error = _api_error_details(exc)
        metadata["generation_error"] = error["message"]
        metadata["generation_error_code"] = error["code"]
        return {
            "scene_id": scene.get("scene_id") or scene.get("scene"),
            "status": "Needs Attention",
            "image_prompt": prompt,
            "generated_image_path": "",
            "generation_error": error["message"],
            "generation_error_code": error["code"],
            "generation_metadata": metadata,
        }

    return {
        "scene_id": scene.get("scene_id") or scene.get("scene"),
        "status": "generated",
        "image_prompt": prompt,
        "generated_image_path": str(output),
        "generation_metadata": metadata,
    }


def generate_protagonist_reference(
    directors_bible: dict[str, Any],
    output_path: str | Path,
    api_key: str = "",
    model: str = DEFAULT_IMAGE_MODEL,
) -> dict[str, Any]:
    prompt = build_protagonist_reference_prompt(directors_bible)
    output = Path(output_path)

    if not api_key:
        path = generate_placeholder_still(prompt, output)
        return {
            "reference_image_path": str(path),
            "image_prompt": prompt,
            "approved": False,
            "generation_metadata": {
                **_base_generation_metadata(
                    "placeholder",
                    model,
                    "local-placeholder",
                    "text_generation",
                    False,
                ),
            },
        }

    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    response = client.images.generate(
        model=model,
        prompt=prompt,
        size="1536x864",
    )
    image_b64 = response.data[0].b64_json
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(base64.b64decode(image_b64))

    return {
        "reference_image_path": str(output),
        "image_prompt": prompt,
        "approved": False,
        "generation_metadata": {
            **_base_generation_metadata(
                "openai",
                model,
                model,
                "text_generation",
                False,
            ),
        },
    }
