from __future__ import annotations

import json
import zipfile
from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any


PROJECT_ARCHIVE_VERSION = "songstory-project-v0.6"
PROJECT_MANIFEST = "songstory_project.json"

SECRET_FIELD_NAMES = {
    "api_key",
    "openai_key",
    "openai_api_key",
    "pexels_key",
    "pexels_api_key",
    "streamlit_secrets",
    "secrets",
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


class ProjectArchiveError(ValueError):
    pass


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _reject_secret_fields(data: Any, path: str = "") -> None:
    if isinstance(data, dict):
        for key, value in data.items():
            lowered = str(key).lower()
            current = f"{path}.{key}" if path else str(key)
            if lowered in SECRET_FIELD_NAMES or "api_key" in lowered or "secret" in lowered:
                raise ProjectArchiveError(f"Project archive attempted to serialize secret field: {current}")
            _reject_secret_fields(value, current)
    elif isinstance(data, list):
        for index, value in enumerate(data):
            _reject_secret_fields(value, f"{path}[{index}]")


def _asset_entry(path_value: str | None, prefix: str) -> tuple[Path, str] | None:
    if not path_value:
        return None
    path = Path(path_value)
    if not path.exists() or not path.is_file():
        return None
    return path, f"assets/{prefix}/{path.name}"


def _with_asset(data: dict[str, Any], path_key: str, archive_path: str) -> dict[str, Any]:
    updated = deepcopy(data)
    updated[f"{path_key}_archive_asset"] = archive_path
    return updated


def _infer_contains_protagonist(scene: dict[str, Any]) -> bool:
    text = " ".join(
        str(scene.get(key, ""))
        for key in [
            "recommended_visual",
            "visual",
        ]
    ).lower()
    return any(phrase in text for phrase in PROTAGONIST_VISUAL_PHRASES)


def _backfill_contains_protagonist(storyboard: Any) -> None:
    if not isinstance(storyboard, dict):
        return
    scenes = storyboard.get("scenes")
    if not isinstance(scenes, list):
        return
    for scene in scenes:
        if isinstance(scene, dict) and "contains_protagonist" not in scene:
            scene["contains_protagonist"] = _infer_contains_protagonist(scene)


def build_project_manifest(session_state: Any) -> dict[str, Any]:
    storyboard = _json_safe(session_state.get("storyboard"))
    audio_path = session_state.get("audio_path")
    protagonist_reference = _json_safe(session_state.get("protagonist_reference"))

    manifest = {
        "project_version": PROJECT_ARCHIVE_VERSION,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "song_filename": session_state.get("song_filename") or (Path(audio_path).name if audio_path else ""),
        "lyrics": session_state.get("lyrics", ""),
        "video_style": session_state.get("video_style", "Cinematic realistic"),
        "interpretation": session_state.get("interpretation", "Combination of story + symbolism"),
        "output_format": session_state.get("aspect_ratio", "16:9"),
        "analysis_model": session_state.get("analysis_model", ""),
        "image_model": session_state.get("image_model", ""),
        "audio_duration": session_state.get("audio_duration", 0),
        "creative_treatment": {
            "concept": (storyboard or {}).get("concept", "") if isinstance(storyboard, dict) else "",
            "story_arc": (storyboard or {}).get("story_arc", "") if isinstance(storyboard, dict) else "",
            "mood": (storyboard or {}).get("mood", "") if isinstance(storyboard, dict) else "",
            "visual_style": (storyboard or {}).get("visual_style", "") if isinstance(storyboard, dict) else "",
        },
        "directors_bible": (storyboard or {}).get("directors_bible", {}) if isinstance(storyboard, dict) else {},
        "storyboard": storyboard,
        "protagonist_reference_approved": bool(session_state.get("protagonist_reference_approved", False)),
        "protagonist_reference": protagonist_reference,
        "hybrid_scene_media": _json_safe(session_state.get("hybrid_scene_media", {})),
        "selected_pexels_clips": _json_safe(session_state.get("selected_pexels_clips", {})),
        "downloaded_clip_paths": _json_safe(session_state.get("downloaded_clip_paths", {})),
        "scene_selection_status": _json_safe(session_state.get("scene_selection_status", {})),
        "footage_credits": _json_safe(session_state.get("footage_credits", [])),
        "assets": {},
    }
    _reject_secret_fields(manifest)
    return manifest


def create_project_archive(session_state: Any) -> bytes:
    manifest = build_project_manifest(session_state)
    archive_assets: list[tuple[Path, str]] = []

    audio_asset = _asset_entry(session_state.get("audio_path"), "audio")
    if audio_asset:
        archive_assets.append(audio_asset)
        manifest["assets"]["audio_path"] = audio_asset[1]

    protagonist_reference = manifest.get("protagonist_reference")
    if isinstance(protagonist_reference, dict):
        reference_asset = _asset_entry(protagonist_reference.get("reference_image_path"), "images")
        if reference_asset:
            archive_assets.append(reference_asset)
            manifest["protagonist_reference"] = _with_asset(
                protagonist_reference,
                "reference_image_path",
                reference_asset[1],
            )

    media = manifest.get("hybrid_scene_media", {})
    if isinstance(media, dict):
        updated_media = {}
        for scene_id, item in media.items():
            if isinstance(item, dict):
                still_asset = _asset_entry(item.get("generated_image_path"), "images")
                if still_asset:
                    archive_assets.append(still_asset)
                    item = _with_asset(item, "generated_image_path", still_asset[1])
            updated_media[scene_id] = item
        manifest["hybrid_scene_media"] = updated_media

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(PROJECT_MANIFEST, json.dumps(manifest, indent=2))
        seen = set()
        for source, archive_name in archive_assets:
            if archive_name in seen:
                continue
            seen.add(archive_name)
            archive.write(source, archive_name)

    return buffer.getvalue()


def _extract_asset(
    archive: zipfile.ZipFile,
    manifest: dict[str, Any],
    archive_asset: str | None,
    project_dir: Path,
) -> str:
    if not archive_asset or archive_asset not in archive.namelist():
        return ""
    output = project_dir / archive_asset
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(archive.read(archive_asset))
    return str(output)


def load_project_archive(package: bytes, project_dir: str | Path) -> dict[str, Any]:
    project_path = Path(project_dir)
    project_path.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(BytesIO(package), "r") as archive:
            if PROJECT_MANIFEST not in archive.namelist():
                raise ProjectArchiveError("Invalid SongStory project: missing project manifest.")
            manifest = json.loads(archive.read(PROJECT_MANIFEST).decode("utf-8"))
            if manifest.get("project_version") != PROJECT_ARCHIVE_VERSION:
                raise ProjectArchiveError("Invalid SongStory project: unsupported project version.")
            _backfill_contains_protagonist(manifest.get("storyboard"))

            audio_asset = manifest.get("assets", {}).get("audio_path")
            audio_path = _extract_asset(archive, manifest, audio_asset, project_path)

            protagonist_reference = manifest.get("protagonist_reference")
            if isinstance(protagonist_reference, dict):
                restored = _extract_asset(
                    archive,
                    manifest,
                    protagonist_reference.get("reference_image_path_archive_asset"),
                    project_path,
                )
                if restored:
                    protagonist_reference["reference_image_path"] = restored

            media = manifest.get("hybrid_scene_media", {})
            if isinstance(media, dict):
                for item in media.values():
                    if not isinstance(item, dict):
                        continue
                    restored = _extract_asset(
                        archive,
                        manifest,
                        item.get("generated_image_path_archive_asset"),
                        project_path,
                    )
                    if restored:
                        item["generated_image_path"] = restored
    except ProjectArchiveError:
        raise
    except Exception as exc:
        raise ProjectArchiveError(f"Invalid SongStory project: {exc}") from exc

    return {
        "project_dir": str(project_path),
        "audio_path": audio_path,
        "audio_duration": manifest.get("audio_duration", 0),
        "song_filename": manifest.get("song_filename", ""),
        "lyrics": manifest.get("lyrics", ""),
        "video_style": manifest.get("video_style", "Cinematic realistic"),
        "interpretation": manifest.get("interpretation", "Combination of story + symbolism"),
        "aspect_ratio": manifest.get("output_format", "16:9"),
        "analysis_model": manifest.get("analysis_model", ""),
        "image_model": manifest.get("image_model", ""),
        "storyboard": manifest.get("storyboard"),
        "protagonist_reference": manifest.get("protagonist_reference"),
        "protagonist_reference_approved": bool(manifest.get("protagonist_reference_approved", False)),
        "hybrid_scene_media": manifest.get("hybrid_scene_media", {}),
        "selected_pexels_clips": manifest.get("selected_pexels_clips", {}),
        "downloaded_clip_paths": manifest.get("downloaded_clip_paths", {}),
        "scene_selection_status": manifest.get("scene_selection_status", {}),
        "footage_credits": manifest.get("footage_credits", []),
    }
