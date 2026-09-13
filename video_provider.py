from __future__ import annotations

from pathlib import Path
from typing import Any
import requests


class PexelsError(RuntimeError):
    pass


class PexelsAuthError(PexelsError):
    pass


def runway_text_to_video(
    prompt: str,
    api_key: str,
    duration: int = 5,
    ratio: str = "1280:720",
) -> str:
    """Generate one text-to-video scene through Runway Gen-4.5."""

    if not api_key:
        raise ValueError("Runway API key is required")

    from runwayml import RunwayML, TaskFailedError

    client = RunwayML(api_key=api_key)

    try:
        task = client.image_to_video.create(
            model="gen4.5",
            prompt_text=prompt,
            ratio=ratio,
            duration=duration,
        ).wait_for_task_output()

    except TaskFailedError as exc:
        raise RuntimeError(
            f"Runway generation failed: {exc.task_details}"
        ) from exc

    if not task.output:
        raise RuntimeError("Runway returned no video output")

    return task.output[0]


def download_clip(url: str, output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        response = requests.get(url, stream=True, timeout=120)
    except requests.RequestException as exc:
        raise PexelsError(f"Video download failed: {exc}") from exc

    with response as r:
        try:
            r.raise_for_status()
        except requests.RequestException as exc:
            raise PexelsError(f"Video download failed: {exc}") from exc

        with output_path.open("wb") as file_obj:
            for chunk in r.iter_content(
                chunk_size=1024 * 1024
            ):
                if chunk:
                    file_obj.write(chunk)

    return output_path


def _orientation_for_aspect(aspect_ratio: str) -> str:
    if aspect_ratio == "9:16":
        return "portrait"
    if aspect_ratio == "1:1":
        return "square"
    return "landscape"


def _choose_video_file(
    files: list[dict[str, Any]],
    aspect_ratio: str,
) -> dict[str, Any] | None:
    mp4_files = [
        file_obj
        for file_obj in files
        if file_obj.get("file_type") == "video/mp4"
        and file_obj.get("width")
        and file_obj.get("height")
        and file_obj.get("link")
    ]

    if not mp4_files:
        return None

    if aspect_ratio == "9:16":
        compatible = [
            file_obj
            for file_obj in mp4_files
            if file_obj["height"] >= file_obj["width"]
        ]
    elif aspect_ratio == "1:1":
        compatible = mp4_files
    else:
        compatible = [
            file_obj
            for file_obj in mp4_files
            if file_obj["width"] >= file_obj["height"]
        ]

    choices = compatible or mp4_files

    def file_score(file_obj: dict[str, Any]) -> tuple[int, int]:
        width = int(file_obj.get("width") or 0)
        height = int(file_obj.get("height") or 0)
        long_edge = max(width, height)
        size_penalty = abs(long_edge - 1600)
        in_range = 1 if 960 <= long_edge <= 1920 else 0
        return (in_range, -size_penalty)

    return max(choices, key=file_score)


def pexels_search_videos(
    query: str,
    api_key: str,
    per_page: int = 6,
    aspect_ratio: str = "16:9",
) -> list[dict[str, Any]]:
    """Search Pexels for video clip metadata without downloading footage."""

    if not api_key:
        raise PexelsAuthError("Pexels API key is required")

    try:
        response = requests.get(
            "https://api.pexels.com/v1/videos/search",
            headers={"Authorization": api_key},
            params={
                "query": query,
                "orientation": _orientation_for_aspect(aspect_ratio),
                "per_page": per_page,
            },
            timeout=30,
        )
    except requests.RequestException as exc:
        raise PexelsError(f"Pexels request failed: {exc}") from exc

    if response.status_code in {401, 403}:
        raise PexelsAuthError(
            "Pexels authorization failed. Check PEXELS_API_KEY."
        )

    try:
        response.raise_for_status()
    except requests.RequestException as exc:
        raise PexelsError(f"Pexels request failed: {exc}") from exc

    results = []

    for video in response.json().get("videos", []):
        selected = _choose_video_file(
            video.get("video_files", []),
            aspect_ratio,
        )

        if not selected:
            continue

        user = video.get("user", {})

        results.append(
            {
                "id": video.get("id"),
                "query": query,
                "page_url": video.get("url"),
                "preview_image": video.get("image"),
                "duration": video.get("duration"),
                "creator": user.get(
                    "name",
                    "Pexels contributor",
                ),
                "creator_url": user.get("url"),
                "video_url": selected.get("link"),
                "width": selected.get("width"),
                "height": selected.get("height"),
                "file_type": selected.get("file_type"),
            }
        )

    return results
