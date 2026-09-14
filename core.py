from __future__ import annotations

import json
import math
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from directors_bible import apply_directors_bible
from hybrid_media import text_overlay
from image_generation import scene_includes_protagonist


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
    section: str = ""
    pexels_query: str = ""

    @property
    def duration(self) -> float:
        return max(0.1, self.end - self.start)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["duration"] = round(self.duration, 2)
        return data


def ffprobe_duration(path: str | Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        out = subprocess.check_output(
            cmd,
            stderr=subprocess.STDOUT,
            text=True,
        ).strip()
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

        with tempfile.NamedTemporaryFile(
            suffix=".docx",
            delete=False,
        ) as file_obj:
            file_obj.write(raw)
            tmp = file_obj.name

        try:
            doc = Document(tmp)
            return "\n".join(
                paragraph.text
                for paragraph in doc.paragraphs
                if paragraph.text.strip()
            )
        finally:
            os.unlink(tmp)

    return ""


_SECTION_RE = re.compile(
    r"""
    ^\s*
    (?:
        \[(?P<bracket>[^\]]+)\]
        |
        (?P<plain>
            (?:
                verse(?:\s+\d+)?|
                chorus(?:\s+\d+)?|
                pre[-\s]?chorus(?:\s+\d+)?|
                bridge(?:\s+\d+)?|
                intro|
                outro|
                refrain|
                interlude|
                instrumental(?:\s+(?:intro|break|cut|outro|solo))?|
                guitar\s+solo|
                piano\s+solo|
                solo
            )
        )\s*:?
    )
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _clean_section_name(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return "Song"
    return cleaned


def parse_lyrics_sections(lyrics: str) -> list[dict[str, Any]]:
    """Preserve Verse/Chorus/Bridge/Instrumental labels and their lyric lines."""
    sections: list[dict[str, Any]] = []
    current = {
        "label": "Song",
        "lines": [],
    }
    saw_structural_heading = False

    for raw_line in lyrics.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()

        if not line:
            continue

        match = _SECTION_RE.match(line)

        if match:
            if current["label"] != "Song" or (
                current["lines"] and saw_structural_heading
            ):
                sections.append(current)

            label = (
                match.group("bracket")
                or match.group("plain")
                or "Song"
            )

            saw_structural_heading = True
            current = {
                "label": _clean_section_name(label),
                "lines": [],
            }
            continue

        current["lines"].append(line)

    if current["lines"] or current["label"] != "Song":
        sections.append(current)

    if not sections:
        sections = [
            {
                "label": "Song",
                "lines": ["Instrumental passage"],
            }
        ]

    return sections


def compact_lyrics(lyrics: str) -> list[str]:
    """Backward-compatible helper that keeps lyric text but excludes section labels."""
    lines: list[str] = []

    for section in parse_lyrics_sections(lyrics):
        lines.extend(section["lines"])

    return lines


_STOPWORDS = {
    "a", "about", "after", "again", "against", "all", "am", "an", "and", "any",
    "are", "as", "at", "be", "because", "been", "before", "being", "below", "between",
    "both", "but", "by", "can", "could", "did", "do", "does", "doing", "down", "during",
    "each", "few", "for", "from", "further", "had", "has", "have", "having", "he", "her",
    "here", "hers", "herself", "him", "himself", "his", "how", "i", "if", "in", "into",
    "is", "it", "its", "itself", "just", "me", "more", "most", "my", "myself", "no",
    "nor", "not", "now", "of", "off", "on", "once", "only", "or", "other", "our",
    "ours", "ourselves", "out", "over", "own", "same", "she", "should", "so", "some",
    "such", "than", "that", "the", "their", "theirs", "them", "themselves", "then",
    "there", "these", "they", "this", "those", "through", "to", "too", "under", "until",
    "up", "very", "was", "we", "were", "what", "when", "where", "which", "while", "who",
    "whom", "why", "will", "with", "you", "your", "yours", "yourself", "yourselves",
    "oh", "ooh", "yeah", "hey",
}

_ABSTRACT_WORDS = {
    "soul", "heart", "love", "hope", "dream", "dreams", "pain", "time", "life",
    "feeling", "feel", "spirit", "forever", "memory", "memories", "truth", "way",
    "fate", "guilt", "judgment", "surrender",
}


def infer_visual_world(lyrics: str) -> dict[str, Any]:
    """Infer a broad stock-footage world from repeated concrete cues."""
    lowered = lyrics.lower()
    worlds = [
        {
            "name": "Southern Gothic Western",
            "terms": {
                "outlaw",
                "bounty",
                "hunter",
                "drifter",
                "revolver",
                "gun",
                "horse",
                "desert",
                "dust",
                "dusty",
                "frontier",
                "western",
                "church",
                "grave",
                "graveyard",
                "judgment",
                "soul",
                "sin",
                "blood",
            },
            "motifs": [
                "western drifter dusty road",
                "old western town",
                "desert church sunset",
                "revolver candle table",
                "storm desert landscape",
            ],
            "positive_cues": [
                "weathered male drifter",
                "bounty hunter",
                "outlaw",
                "dusty road",
                "horse",
                "western landscape",
                "desert",
                "frontier town",
                "old wooden buildings",
                "old church",
                "graveyard",
                "revolver",
                "storm clouds",
                "desert sunset",
                "cinematic realism",
            ],
            "negative_cues": [
                "contemporary bedrooms",
                "sleeping",
                "lying on beds",
                "lying on floors",
                "yoga",
                "meditation",
                "generic wellness",
                "modern lifestyle footage",
                "musicians performing",
                "singers",
                "flute players",
                "guitar players",
                "fire performers",
                "dancers",
                "circus performers",
                "festivals",
                "modern city nightlife",
                "modern fashion scenes",
                "unrelated contemporary women indoors",
                "children",
                "office footage",
                "corporate imagery",
            ],
            "protagonist_description": (
                "weathered male drifter or bounty hunter, solitary, hardened, "
                "rugged, period-appropriate western clothing"
            ),
            "era": "historical or revisionist Western, not contemporary",
            "locations": [
                "dusty roads",
                "western landscapes",
                "desert",
                "frontier town",
                "old church",
                "graveyard",
            ],
            "description": (
                "Southern Gothic Western: dusty roads, lonely drifters, horses, "
                "frontier towns, old churches, graveyards, storm light, and "
                "symbols of judgment."
            ),
        },
        {
            "name": "Rural Americana",
            "terms": {
                "road",
                "highway",
                "truck",
                "farm",
                "field",
                "porch",
                "home",
                "river",
                "small town",
            },
            "motifs": [
                "rural road sunset",
                "old farmhouse dusk",
                "empty highway rain",
                "small town street night",
            ],
            "positive_cues": [
                "rural roads",
                "fields",
                "small towns",
                "porches",
                "weathered homes",
                "quiet landscapes",
            ],
            "negative_cues": [
                "corporate imagery",
                "office footage",
                "generic smiling people",
                "unrelated performance footage",
            ],
            "protagonist_description": "grounded rural character, understated and naturalistic",
            "era": "contemporary or timeless rural Americana",
            "locations": ["rural roads", "fields", "farmhouses", "small towns"],
            "description": (
                "Rural Americana: roads, fields, porches, small towns, weather, "
                "and quiet human-scale details."
            ),
        },
    ]

    best = None
    best_score = 0

    for world in worlds:
        score = sum(1 for term in world["terms"] if term in lowered)
        if score > best_score:
            best = world
            best_score = score

    if best and best_score >= 2:
        return best

    return {
        "name": "Grounded Cinematic",
        "motifs": [
            "lonely road dusk",
            "person window rain",
            "wide landscape sunset",
            "storm clouds landscape",
        ],
        "positive_cues": [
            "cinematic realism",
            "lonely roads",
            "weather",
            "landscape",
            "solitary character",
        ],
        "negative_cues": [
            "generic corporate imagery",
            "unrelated performance footage",
            "stock-photo lifestyle posing",
        ],
        "protagonist_description": "solitary grounded protagonist consistent with the song story",
        "era": "visually consistent with the selected treatment",
        "locations": ["roads", "landscapes", "interiors that fit the story"],
        "description": (
            "Grounded cinematic realism with recurring locations, weather, "
            "objects, and emotional continuity."
        ),
    }


def make_pexels_query(
    text: str,
    section: str = "",
    visual_world: dict[str, Any] | None = None,
) -> str:
    """Create a short, concrete stock-footage search phrase."""
    words = re.findall(
        r"[A-Za-z][A-Za-z'-]*",
        text.lower(),
    )

    chosen: list[str] = []

    for word in words:
        word = word.strip("'")

        if len(word) < 3:
            continue

        if word in _STOPWORDS or word in _ABSTRACT_WORDS:
            continue

        if word not in chosen:
            chosen.append(word)

        if len(chosen) >= 5:
            break

    section_lower = section.lower()

    if not chosen and visual_world:
        motifs = visual_world.get("motifs") or []
        if motifs:
            return str(motifs[0])

    if not chosen:
        if "intro" in section_lower:
            chosen = ["open", "road", "sunset"]
        elif "outro" in section_lower:
            chosen = ["quiet", "landscape", "dusk"]
        elif "instrumental" in section_lower or "solo" in section_lower:
            chosen = ["atmospheric", "road", "landscape", "dusk"]
        elif "chorus" in section_lower or "refrain" in section_lower:
            chosen = ["emotional", "person", "outdoors", "sunset"]
        elif "bridge" in section_lower:
            chosen = ["solitary", "person", "window", "rain"]
        else:
            chosen = ["cinematic", "person", "rural", "landscape"]

    query = " ".join(chosen[:6])

    if visual_world and visual_world.get("name") == "Southern Gothic Western":
        query_terms = set(query.split())
        if query_terms & {"drifter", "outlaw", "bounty", "hunter", "gun", "revolver"}:
            return "western drifter dusty road"
        if query_terms & {"church", "sin", "judgment", "grave", "graveyard"}:
            return "desert church graveyard"

    return query


def _target_scene_count(
    duration: float,
    section_count: int,
) -> int:
    """Aim for about 12–16 meaningful sequences for a typical full song."""
    estimated = round(
        max(duration, 180.0) / 16.0
    )

    target = max(
        10,
        min(16, estimated),
    )

    return max(
        section_count,
        target,
    )


def _allocate_scene_counts(
    sections: list[dict[str, Any]],
    target_count: int,
) -> list[int]:
    section_count = len(sections)

    if section_count == 0:
        return []

    counts = [1] * section_count
    remaining = max(
        0,
        target_count - section_count,
    )

    if remaining == 0:
        return counts

    weights = []

    for section in sections:
        line_count = len(section.get("lines", []))

        if line_count:
            weights.append(
                max(1, line_count)
            )
        else:
            weights.append(2)

    total_weight = sum(weights) or 1

    raw_extras = [
        remaining * weight / total_weight
        for weight in weights
    ]

    floor_extras = [
        math.floor(value)
        for value in raw_extras
    ]

    for index, extra in enumerate(floor_extras):
        counts[index] += extra

    leftover = remaining - sum(floor_extras)

    ranked = sorted(
        range(section_count),
        key=lambda i: (
            raw_extras[i] - floor_extras[i],
            weights[i],
        ),
        reverse=True,
    )

    for index in ranked[:leftover]:
        counts[index] += 1

    return counts


def _chunk_section_lines(
    lines: list[str],
    count: int,
    section_label: str,
) -> list[str]:
    if count <= 0:
        return []

    if not lines:
        return [
            f"{section_label} instrumental passage"
            for _ in range(count)
        ]

    chunks: list[str] = []

    for index in range(count):
        start = math.floor(
            index * len(lines) / count
        )
        end = math.floor(
            (index + 1) * len(lines) / count
        )

        if end <= start:
            end = min(
                len(lines),
                start + 1,
            )

        excerpt = " ".join(
            lines[start:end]
        ).strip()

        if not excerpt:
            excerpt = lines[
                min(
                    start,
                    len(lines) - 1,
                )
            ]

        chunks.append(excerpt)

    return chunks


def heuristic_storyboard(
    lyrics: str,
    duration: float,
    target_scene_seconds: float = 16.0,
) -> dict[str, Any]:
    """Build a section-aware local storyboard without an AI API call."""
    sections = parse_lyrics_sections(
        lyrics
    )
    visual_world = infer_visual_world(lyrics)

    target_count = _target_scene_count(
        duration,
        len(sections),
    )

    counts = _allocate_scene_counts(
        sections,
        target_count,
    )

    planned: list[dict[str, str]] = []

    for section, count in zip(
        sections,
        counts,
    ):
        label = section["label"]

        excerpts = _chunk_section_lines(
            section.get("lines", []),
            count,
            label,
        )

        for excerpt in excerpts:
            query = make_pexels_query(
                excerpt,
                label,
                visual_world,
            )

            visual = (
                f"{label}: {excerpt}. "
                f"{visual_world['description']} "
                f"Grounded cinematic scene built around: {query}. "
                "Keep recurring people and locations visually consistent. "
                "No on-screen text and no lip sync."
            )

            planned.append(
                {
                    "section": label,
                    "excerpt": excerpt,
                    "query": query,
                    "visual": visual,
                }
            )

    scene_count = len(planned)

    step = (
        (duration or scene_count * target_scene_seconds)
        / max(scene_count, 1)
    )

    scenes: list[dict[str, Any]] = []

    for index, plan in enumerate(
        planned,
        start=1,
    ):
        start = (index - 1) * step
        end = min(
            index * step,
            duration or index * step,
        )

        section_lower = plan["section"].lower()

        if (
            "chorus" in section_lower
            or "refrain" in section_lower
        ):
            purpose = (
                "Return to the video's strongest recurring emotional motif."
            )
        elif (
            "instrumental" in section_lower
            or "solo" in section_lower
        ):
            purpose = (
                "Let the visuals breathe and extend the established world."
            )
        elif "outro" in section_lower:
            purpose = (
                "Resolve the visual story and leave a final emotional image."
            )
        else:
            purpose = (
                "Advance the visual story of this song section."
            )

        scenes.append(
            Scene(
                scene=index,
                start=round(start, 2),
                end=round(end, 2),
                lyric_excerpt=plan["excerpt"],
                purpose=purpose,
                visual=plan["visual"],
                camera=(
                    "Natural cinematic movement; use wide, medium, "
                    "and close shots as appropriate."
                ),
                mood=(
                    "Match this section's emotional intensity "
                    "while preserving continuity."
                ),
                transition=(
                    "Cut or soft dissolve on the musical phrase."
                ),
                section=plan["section"],
                pexels_query=plan["query"],
            ).as_dict()
        )
        scenes[-1].update(
            {
                "scene_id": index,
                "song_section": scenes[-1]["section"],
                "lyric_or_musical_moment": scenes[-1]["lyric_excerpt"],
                "story_purpose": scenes[-1]["purpose"],
                "recommended_visual": scenes[-1]["visual"],
                "preferred_source_type": (
                    "generated_still"
                    if any(term in scenes[-1]["section"].lower() for term in ["instrumental", "solo", "outro"])
                    else "either"
                ),
                "text_overlay": text_overlay(),
                "excluded": False,
            }
        )
        scenes[-1]["contains_protagonist"] = scene_includes_protagonist(scenes[-1])

    storyboard = {
        "concept": (
            "A coherent cinematic interpretation organized around the song's "
            f"actual sections in a {visual_world['name']} visual world, with "
            "recurring people, places, and visual motifs."
        ),
        "mood": (
            "Emotion follows the verse/chorus/bridge/instrumental progression."
        ),
        "visual_style": (
            f"{visual_world['description']} Cinematic, naturalistic, consistent characters and locations; "
            "no lip sync unless explicitly requested."
        ),
        "story_arc": (
            "Establish → develop through verses → reinforce choruses → "
            "shift at bridge/instrumental → resolve in final chorus/outro."
        ),
        "visual_world": {
            "name": visual_world.get("name", "Grounded Cinematic"),
            "description": visual_world.get("description", ""),
            "positive_cues": visual_world.get("positive_cues", []),
            "negative_cues": visual_world.get("negative_cues", []),
            "protagonist_description": visual_world.get("protagonist_description", ""),
            "era": visual_world.get("era", ""),
            "locations": visual_world.get("locations", []),
            "recurring_motifs": visual_world.get("motifs", []),
        },
        "scenes": scenes,
        "source": "local-section-aware-v0.6",
    }
    return apply_directors_bible(storyboard)


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()

    if text.startswith("```"):
        text = re.sub(
            r"^```(?:json)?",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip()

        text = re.sub(
            r"```$",
            "",
            text,
        ).strip()

    try:
        return json.loads(text)

    except json.JSONDecodeError:
        match = re.search(
            r"\{.*\}",
            text,
            re.DOTALL,
        )

        if not match:
            raise

        return json.loads(
            match.group(0)
        )


def ai_storyboard(
    lyrics: str,
    duration: float,
    style: str,
    interpretation: str,
    aspect_ratio: str,
    api_key: str,
    model: str = "gpt-5.6-luna",
) -> dict[str, Any]:
    from openai import OpenAI

    client = OpenAI(
        api_key=api_key
    )

    sections = parse_lyrics_sections(
        lyrics
    )

    target_scene_count = _target_scene_count(
        duration,
        len(sections),
    )

    section_summary = "\n".join(
        f"[{section['label']}]\n"
        + (
            "\n".join(section["lines"])
            if section["lines"]
            else "(instrumental)"
        )
        for section in sections
    )

    prompt = f"""
You are the creative director for a professional music video.

Analyze the complete song as a coherent film.
Respect the song's actual labeled structure: verses, choruses, bridges,
instrumental breaks, solos, intros, and outros.

Do NOT create a new shot every few seconds.
Create approximately {target_scene_count} meaningful visual sequences for the
entire song. Usually 12–16 sequences is enough for a full-length song.

SONG DURATION: {duration:.1f} seconds
TARGET VISUAL SEQUENCES: approximately {target_scene_count}
VISUAL STYLE: {style}
INTERPRETATION MODE: {interpretation}
ASPECT RATIO: {aspect_ratio}

STRUCTURED LYRICS:
{section_summary}

Return ONLY valid JSON with this exact top-level structure:
{{
  "concept": "one-paragraph concept",
  "mood": "overall mood and progression",
  "visual_style": "consistent palette, setting, characters, wardrobe, lighting",
  "story_arc": "beginning -> development -> climax -> resolution",
  "scenes": [
    {{
      "scene": 1,
      "section": "Verse 1",
      "start": 0.0,
      "end": 14.0,
      "lyric_excerpt": "short relevant excerpt or instrumental",
      "purpose": "why this sequence exists in the story",
      "visual": "detailed visual concept for the user; no on-screen text and no lip sync",
      "pexels_query": "short concrete stock footage search phrase",
      "camera": "camera/framing/movement",
      "mood": "scene mood",
      "transition": "transition into next sequence"
    }}
  ]
}}

Rules:
- Cover the full {duration:.1f}-second song from 0 to the end with no major gaps.
- Use the labeled song sections to guide sequence boundaries.
- Aim for approximately {target_scene_count} sequences, not dozens of micro-scenes.
- A visual sequence may last 10–25 seconds if appropriate.
- Repeated choruses should revisit or evolve a recurring visual motif.
- Recurring people, locations, wardrobe, lighting, and era must remain consistent.
- Avoid illustrating every lyric literally.
- Do not use singers performing to camera and do not use lip sync.
- Instrumental sections should use atmospheric or story-extending visuals.
- The final sequence should resolve the video's central visual idea.
- "pexels_query" must be 3–7 concrete searchable words only.
- "pexels_query" should use nouns/adjectives such as:
  "lonely rural road dusk", "woman window rain", "old church interior candlelight".
- Do NOT put phrases such as "cinematic interpretation", "coherent world",
  "purposeful composition", or instructions into "pexels_query".
"""

    response = client.responses.create(
        model=model,
        input=prompt,
        reasoning={
            "effort": "medium",
        },
    )

    data = _extract_json(
        response.output_text
    )

    data["source"] = (
        f"openai:{model}:section-aware-v0.5"
    )

    return normalize_storyboard(
        data,
        duration,
    )


def normalize_storyboard(
    data: dict[str, Any],
    duration: float,
) -> dict[str, Any]:
    raw_scenes = (
        data.get("scenes")
        or []
    )

    scenes: list[dict[str, Any]] = []

    for index, item in enumerate(
        raw_scenes,
        start=1,
    ):
        try:
            start = float(
                item.get("start", 0)
            )

            end = float(
                item.get(
                    "end",
                    start + 12,
                )
            )

        except (
            TypeError,
            ValueError,
        ):
            continue

        if duration:
            start = max(
                0.0,
                min(
                    start,
                    duration,
                ),
            )

            end = max(
                start + 0.1,
                min(
                    end,
                    duration,
                ),
            )

        section = str(
            item.get(
                "section",
                "",
            )
        ).strip()

        lyric_excerpt = str(
            item.get(
                "lyric_excerpt",
                "",
            )
        ).strip()

        pexels_query = str(
            item.get(
                "pexels_query",
                "",
            )
        ).strip()

        if not pexels_query:
            pexels_query = make_pexels_query(
                lyric_excerpt,
                section,
            )

        normalized_scene = {
                "scene": index,
                "scene_id": int(item.get("scene_id") or item.get("scene") or index),
                "section": section,
                "song_section": str(item.get("song_section") or section),
                "start": round(
                    start,
                    2,
                ),
                "end": round(
                    end,
                    2,
                ),
                "duration": round(
                    max(
                        0.1,
                        end - start,
                    ),
                    2,
                ),
                "lyric_excerpt": lyric_excerpt,
                "lyric_or_musical_moment": str(
                    item.get("lyric_or_musical_moment") or lyric_excerpt
                ),
                "purpose": str(
                    item.get(
                        "purpose",
                        "",
                    )
                ),
                "story_purpose": str(
                    item.get("story_purpose") or item.get("purpose", "")
                ),
                "visual": str(
                    item.get(
                        "visual",
                        "",
                    )
                ),
                "recommended_visual": str(
                    item.get("recommended_visual") or item.get("visual", "")
                ),
                "preferred_source_type": str(
                    item.get("preferred_source_type") or "either"
                ),
                "pexels_query": pexels_query,
                "camera": str(
                    item.get(
                        "camera",
                        "",
                    )
                ),
                "mood": str(
                    item.get(
                        "mood",
                        "",
                    )
                ),
                "transition": str(
                    item.get(
                        "transition",
                        "",
                    )
                ),
                "text_overlay": item.get("text_overlay") or text_overlay(),
                "excluded": bool(item.get("excluded", False)),
            }
        if "contains_protagonist" in item:
            normalized_scene["contains_protagonist"] = bool(item.get("contains_protagonist"))
        else:
            normalized_scene["contains_protagonist"] = scene_includes_protagonist(normalized_scene)
        scenes.append(normalized_scene)

    data["scenes"] = scenes
    return apply_directors_bible(data)


def save_uploaded_file(
    uploaded_file,
    dest_dir: str | Path,
) -> Path:
    dest = Path(dest_dir)
    dest.mkdir(
        parents=True,
        exist_ok=True,
    )

    out = (
        dest
        / Path(
            uploaded_file.name
        ).name
    )

    out.write_bytes(
        uploaded_file.getvalue()
    )

    return out
