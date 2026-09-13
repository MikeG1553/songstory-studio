from __future__ import annotations

from pathlib import Path
from typing import Any
import requests


def runway_text_to_video(prompt: str, api_key: str, duration: int = 5, ratio: str = "1280:720") -> str:
    """Generate one text-to-video scene through Runway Gen-4.5.

    This is intentionally scene-by-scene so the UI can put a cost/approval gate in front of each generation.
    Returns the provider URL for the completed clip.
    """
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
        raise RuntimeError(f"Runway generation failed: {exc.task_details}") from exc

    if not task.output:
        raise RuntimeError("Runway returned no video output")
    return task.output[0]


def download_clip(url: str, output_path: str | Path) -> Path:
    output_path = Path(output_path)
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with output_path.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
    return output_path


def pexels_search_videos(
    query: str,
    api_key: str,
    per_page: int = 6,
) -> list[dict[str, Any]]:
    """Search Pexels for landscape video clips."""

    if not api_key:
        raise ValueError("Pexels API key is required")

    response = requests.get(
        "https://api.pexels.com/v1/videos/search",
        headers={"Authorization": api_key},
        params={
            "query": query,
            "orientation": "landscape",
            "per_page": per_page,
        },
        timeout=30,
    )
    response.raise_for_status()

    results = []

    for video in response.json().get("videos", []):
        files = [
            f for f in video.get("video_files", [])
            if f.get("file_type") == "video/mp4"
            and f.get("width")
            and f.get("height")
            and f["width"] >= f["height"]
        ]

        if not files:
            continue

        preferred = [
            f for f in files
            if 1280 <= f["width"] <= 1920
        ]

        choices = preferred or files
        selected = min(
            choices,
            key=lambda f: abs(f["width"] - 1920),
        )

        user = video.get("user", {})

        results.append({
            "id": video.get("id"),
            "page_url": video.get("url"),
            "preview_image": video.get("image"),
            "duration": video.get("duration"),
            "creator": user.get("name", "Pexels contributor"),
            "creator_url": user.get("url"),
            "video_url": selected.get("link"),
            "width": selected.get("width"),
            "height": selected.get("height"),
        })

    return results
