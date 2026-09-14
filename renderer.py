from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
import textwrap
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


def _font(size: int):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def _bold_font(size: int):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return _font(size)


def _dimensions(aspect_ratio: str) -> tuple[int, int]:
    if aspect_ratio == "9:16":
        return 720, 1280
    if aspect_ratio == "1:1":
        return 1080, 1080
    return 1280, 720


def make_scene_card(scene: dict[str, Any], path: Path, aspect_ratio: str = "16:9") -> None:
    w, h = _dimensions(aspect_ratio)
    img = Image.new("RGB", (w, h), (18, 20, 24))
    d = ImageDraw.Draw(img)

    margin = int(w * 0.07)
    d.rounded_rectangle((margin, margin, w - margin, h - margin), radius=24, outline=(90, 94, 104), width=2)

    title_font = _bold_font(max(30, int(w * 0.035)))
    body_font = _font(max(22, int(w * 0.022)))
    small_font = _font(max(18, int(w * 0.017)))

    d.text((margin * 1.35, margin * 1.4), f"SCENE {scene.get('scene', '')}", font=title_font, fill=(245, 245, 245))
    time_line = f"{float(scene.get('start', 0)):.1f}s – {float(scene.get('end', 0)):.1f}s"
    d.text((margin * 1.35, margin * 2.2), time_line, font=small_font, fill=(180, 185, 195))

    lyric = str(scene.get("lyric_excerpt", "")).strip()
    visual = str(scene.get("visual", "")).strip()
    camera = str(scene.get("camera", "")).strip()

    max_chars = 62 if w >= h else 38
    y = margin * 3.1
    for label, text in [("LYRIC", lyric), ("VISUAL", visual), ("CAMERA", camera)]:
        d.text((margin * 1.35, y), label, font=small_font, fill=(180, 185, 195))
        y += int(h * 0.045)
        wrapped = textwrap.wrap(text, width=max_chars)[:6]
        for line in wrapped:
            d.text((margin * 1.35, y), line, font=body_font, fill=(240, 240, 240))
            y += int(h * 0.045)
        y += int(h * 0.025)
        if y > h - margin * 2:
            break

    img.save(path, "PNG")


def render_animatic(audio_path: str | Path, storyboard: dict[str, Any], aspect_ratio: str, output_path: str | Path) -> Path:
    """Render storyboard cards in one FFmpeg pass and place the original song underneath.

    The earlier MVP encoded every card as a separate video before joining them. This
    single-pass image-concat method is substantially faster and produces the same
    timed review animatic.
    """
    scenes = storyboard.get("scenes", [])
    if not scenes:
        raise ValueError("Storyboard has no scenes")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    w, h = _dimensions(aspect_ratio)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        concat_lines: list[str] = []
        last_card: Path | None = None

        for i, scene in enumerate(scenes, start=1):
            card = tmp / f"scene_{i:03d}.png"
            make_scene_card(scene, card, aspect_ratio)
            duration = max(0.2, float(scene.get("end", 0)) - float(scene.get("start", 0)))
            concat_lines.append(f"file '{card.as_posix()}'")
            concat_lines.append(f"duration {duration:.3f}")
            last_card = card

        # FFmpeg's concat demuxer needs the final still repeated so its duration is honored.
        assert last_card is not None
        concat_lines.append(f"file '{last_card.as_posix()}'")
        concat_file = tmp / "images.txt"
        concat_file.write_text("\n".join(concat_lines), encoding="utf-8")

        subprocess.check_call([
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_file),
            "-i", str(audio_path),
            "-vf", f"scale={w}:{h},format=yuv420p",
            "-r", "24",
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest", str(output_path),
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    return output_path


def assemble_generated_clips(audio_path: str | Path, clips: list[str | Path], output_path: str | Path) -> Path:
    """Join already-generated scene clips in order and put the original song back underneath."""
    if not clips:
        raise ValueError("No clips supplied")
    output_path = Path(output_path)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        concat_file = tmp / "clips.txt"
        concat_file.write_text("\n".join(f"file '{Path(p).resolve().as_posix()}'" for p in clips), encoding="utf-8")
        joined = tmp / "joined.mp4"
        subprocess.check_call([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file),
            "-c", "copy", str(joined)
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.check_call([
            "ffmpeg", "-y", "-i", str(joined), "-i", str(audio_path),
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac",
            "-shortest", str(output_path)
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return output_path


def render_selected_pexels_clips(
    audio_path: str | Path,
    storyboard: dict[str, Any],
    clip_paths: dict[int, str | Path],
    aspect_ratio: str,
    output_path: str | Path,
) -> Path:
    """Create a complete music video from selected Pexels clips."""

    scenes = storyboard.get("scenes", [])

    if not scenes:
        raise ValueError("Storyboard has no scenes")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    w, h = _dimensions(aspect_ratio)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        normalized_clips = []

        for i, scene in enumerate(scenes, start=1):
            scene_number = int(scene.get("scene", i))

            if scene_number not in clip_paths:
                raise ValueError(
                    f"No selected clip for scene {scene_number}"
                )

            source_clip = Path(clip_paths[scene_number])

            if not source_clip.exists():
                raise FileNotFoundError(
                    f"Clip file is missing for scene {scene_number}"
                )

            duration = max(
                0.2,
                float(scene.get("end", 0))
                - float(scene.get("start", 0)),
            )

            normalized = tmp / f"scene_{i:03d}.mp4"

            subprocess.check_call(
                [
                    "ffmpeg",
                    "-y",
                    "-stream_loop",
                    "-1",
                    "-i",
                    str(source_clip),
                    "-t",
                    f"{duration:.3f}",
                    "-vf",
                    (
                        f"scale={w}:{h}:"
                        "force_original_aspect_ratio=increase,"
                        f"crop={w}:{h},"
                        "fps=24,"
                        "format=yuv420p"
                    ),
                    "-an",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "22",
                    str(normalized),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            normalized_clips.append(normalized)

        concat_file = tmp / "clips.txt"

        concat_file.write_text(
            "\n".join(
                f"file '{clip.resolve().as_posix()}'"
                for clip in normalized_clips
            ),
            encoding="utf-8",
        )

        joined_video = tmp / "joined.mp4"

        subprocess.check_call(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
                "-c",
                "copy",
                str(joined_video),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        subprocess.check_call(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(joined_video),
                "-i",
                str(audio_path),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                str(output_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    return output_path


def animate_still_image(
    image_path: str | Path,
    duration: float,
    aspect_ratio: str,
    output_path: str | Path,
    motion: str = "slow_push_in",
    overlay: dict[str, Any] | None = None,
) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    w, h = _dimensions(aspect_ratio)
    frames = max(1, int(duration * 24))

    if motion == "slow_zoom_out":
        zoom_expr = "if(lte(zoom,1.0),1.08,max(1.0,zoom-0.0008))"
    else:
        zoom_expr = "min(zoom+0.0009,1.10)"

    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},"
        f"zoompan=z='{zoom_expr}':d={frames}:s={w}x{h}:fps=24,"
        "format=yuv420p"
    )

    text = (overlay or {}).get("text", "")
    if text:
        safe_text = str(text).replace(":", "\\:").replace("'", "\\'")
        size = {"small": 26, "medium": 38, "large": 54}.get(
            (overlay or {}).get("size_preset", "medium"),
            38,
        )
        position = (overlay or {}).get("position", "lower_third")
        y = "h*0.78" if position == "lower_third" else "h*0.12"
        vf += (
            f",drawtext=text='{safe_text}':"
            f"x=(w-text_w)/2:y={y}:fontsize={size}:"
            "fontcolor=white:box=1:boxcolor=black@0.35:boxborderw=18"
        )

    subprocess.check_call(
        [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-i",
            str(image_path),
            "-t",
            f"{duration:.3f}",
            "-vf",
            vf,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "22",
            str(output_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    return output_path


def render_hybrid_media(
    audio_path: str | Path,
    storyboard: dict[str, Any],
    scene_media: dict[int, dict[str, Any]],
    aspect_ratio: str,
    output_path: str | Path,
) -> Path:
    scenes = [
        scene
        for scene in storyboard.get("scenes", [])
        if not scene.get("excluded")
    ]

    if not scenes:
        raise ValueError("Storyboard has no included scenes")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    w, h = _dimensions(aspect_ratio)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        normalized_clips = []

        for index, scene in enumerate(scenes, start=1):
            scene_id = int(scene.get("scene_id") or scene.get("scene") or index)
            media = scene_media.get(scene_id)

            if not media:
                raise ValueError(f"No media selected for scene {scene_id}")

            duration = max(
                0.2,
                float(scene.get("duration") or 0)
                or float(scene.get("end", 0)) - float(scene.get("start", 0)),
            )
            normalized = tmp / f"scene_{index:03d}.mp4"

            if media.get("source_type") == "generated_still":
                animate_still_image(
                    media["generated_image_path"],
                    duration,
                    aspect_ratio,
                    normalized,
                    overlay=scene.get("text_overlay"),
                )
            else:
                source_clip = Path(media.get("downloaded_path") or media.get("path") or "")
                if not source_clip.exists():
                    raise FileNotFoundError(f"Clip file is missing for scene {scene_id}")
                subprocess.check_call(
                    [
                        "ffmpeg",
                        "-y",
                        "-stream_loop",
                        "-1",
                        "-i",
                        str(source_clip),
                        "-t",
                        f"{duration:.3f}",
                        "-vf",
                        (
                            f"scale={w}:{h}:"
                            "force_original_aspect_ratio=increase,"
                            f"crop={w}:{h},"
                            "fps=24,"
                            "format=yuv420p"
                        ),
                        "-an",
                        "-c:v",
                        "libx264",
                        "-preset",
                        "veryfast",
                        "-crf",
                        "22",
                        str(normalized),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

            normalized_clips.append(normalized)

        concat_file = tmp / "hybrid_clips.txt"
        concat_file.write_text(
            "\n".join(f"file '{clip.resolve().as_posix()}'" for clip in normalized_clips),
            encoding="utf-8",
        )
        joined = tmp / "joined.mp4"
        subprocess.check_call(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(joined)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.check_call(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(joined),
                "-i",
                str(audio_path),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                str(output_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    return output_path
