from __future__ import annotations

import json
import math
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable


@dataclass
class Scene:
    scene: int
    start: float
    end: float
    lyric_excerpt: str
    purpose: str
    visual: str
    camera: str
    mood: str
    transition: str

    @property
    def duration(self) -> float:
        return max(0.1, self.end - self.start)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["duration"] = round(self.duration, 2)
        return d


def ffprobe_duration(path: str | Path) -> float:
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path)
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True).strip()
        return float(out)
    except Exception:
        return 0.0


def extract_lyrics(uploaded_file) -> str:
    if not uploaded_file:
        return ""
    suffix = Path(uploaded_file.name).suffix.lower()
    raw = uploaded_file.getvalue()
    if suffix in {".txt", ".md"}:
        return raw.decode("utf-8", errors="replace")
    if suffix == ".docx":
        from docx import Document
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
            f.write(raw)
            tmp = f.name
        try:
            doc = Document(tmp)
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        finally:
            os.unlink(tmp)
    return ""


def compact_lyrics(lyrics: str) -> list[str]:
    lines = []
    for line in lyrics.splitlines():
        s = re.sub(r"\s+", " ", line).strip()
        if not s:
            continue
        if re.fullmatch(r"\[?(verse|chorus|bridge|intro|outro|pre-chorus).*?\]?", s, re.I):
            continue
        lines.append(s)
    return lines


def heuristic_storyboard(lyrics: str, duration: float, target_scene_seconds: float = 7.0) -> dict[str, Any]:
    lines = compact_lyrics(lyrics)
    scene_count = max(6, min(48, math.ceil((duration or 180) / target_scene_seconds)))
    step = (duration or scene_count * target_scene_seconds) / scene_count
    if not lines:
        lines = ["Instrumental passage"] * scene_count

    scenes = []
    for i in range(scene_count):
        start = i * step
        end = min((i + 1) * step, duration or (i + 1) * step)
        excerpt = lines[min(len(lines) - 1, math.floor(i * len(lines) / scene_count))]
        visual = (
            f"Cinematic interpretation of: {excerpt}. Build a coherent recurring world, "
            "natural human emotion, purposeful composition, no on-screen text."
        )
        scenes.append(Scene(
            scene=i + 1,
            start=round(start, 2),
            end=round(end, 2),
            lyric_excerpt=excerpt,
            purpose="Advance the emotional story of the song.",
            visual=visual,
            camera="Slow cinematic movement; vary wide, medium, and close shots.",
            mood="Match the emotional intensity of this lyric.",
            transition="Cut or soft dissolve on the musical phrase.",
        ).as_dict())

    return {
        "concept": "A coherent cinematic interpretation of the song, built from the lyric progression.",
        "mood": "Emotion follows the lyrical arc and musical pacing.",
        "visual_style": "Cinematic, naturalistic, consistent characters and locations.",
        "story_arc": "Establish → develop → emotional peak → resolution.",
        "scenes": scenes,
        "source": "local-fallback",
    }


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.I).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def ai_storyboard(
    lyrics: str,
    duration: float,
    style: str,
    interpretation: str,
    aspect_ratio: str,
    api_key: str,
    model: str = "gpt-5.4-mini",
) -> dict[str, Any]:
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    scene_count = max(8, min(48, math.ceil(max(duration, 120) / 7.0)))

    prompt = f"""
You are the creative director for a professional music video.
Analyze the complete song lyrics as a whole before planning individual shots.
The video must feel like one coherent film, not disconnected literal illustrations.

SONG DURATION: {duration:.1f} seconds
TARGET SCENES: approximately {scene_count}
VISUAL STYLE: {style}
INTERPRETATION MODE: {interpretation}
ASPECT RATIO: {aspect_ratio}

LYRICS:
{lyrics}

Return ONLY valid JSON with this exact top-level structure:
{{
  "concept": "one-paragraph concept",
  "mood": "overall mood and progression",
  "visual_style": "consistent palette, setting, characters, wardrobe, lighting",
  "story_arc": "beginning -> development -> climax -> resolution",
  "scenes": [
    {{
      "scene": 1,
      "start": 0.0,
      "end": 6.0,
      "lyric_excerpt": "short relevant excerpt or instrumental",
      "purpose": "why this shot exists in the story",
      "visual": "detailed prompt suitable for a text-to-video model; no on-screen text",
      "camera": "camera/framing/movement",
      "mood": "scene mood",
      "transition": "transition into next shot"
    }}
  ]
}}

Rules:
- Cover the full {duration:.1f}-second song from 0 to the end with no major gaps.
- Most scenes should be 5-9 seconds long.
- Recurring people/places must remain visually consistent.
- Avoid illustrating every lyric literally; interpret metaphor and emotional meaning.
- Build stronger visuals around choruses and emotional peaks.
- The final scene should resolve the video's central visual idea.
"""

    response = client.responses.create(
        model=model,
        input=prompt,
        reasoning={"effort": "medium"},
    )
    data = _extract_json(response.output_text)
    data["source"] = f"openai:{model}"
    return normalize_storyboard(data, duration)


def normalize_storyboard(data: dict[str, Any], duration: float) -> dict[str, Any]:
    raw_scenes = data.get("scenes") or []
    scenes: list[dict[str, Any]] = []
    for i, item in enumerate(raw_scenes, start=1):
        try:
            start = float(item.get("start", 0))
            end = float(item.get("end", start + 6))
        except (TypeError, ValueError):
            continue
        if duration:
            start = max(0.0, min(start, duration))
            end = max(start + 0.1, min(end, duration))
        scenes.append({
            "scene": i,
            "start": round(start, 2),
            "end": round(end, 2),
            "duration": round(max(0.1, end - start), 2),
            "lyric_excerpt": str(item.get("lyric_excerpt", "")),
            "purpose": str(item.get("purpose", "")),
            "visual": str(item.get("visual", "")),
            "camera": str(item.get("camera", "")),
            "mood": str(item.get("mood", "")),
            "transition": str(item.get("transition", "")),
        })
    data["scenes"] = scenes
    return data


def save_uploaded_file(uploaded_file, dest_dir: str | Path) -> Path:
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / Path(uploaded_file.name).name
    out.write_bytes(uploaded_file.getvalue())
    return out
