from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable


ATMOSPHERIC_FALLBACKS = [
    "lonely road dusk",
    "empty landscape sunset",
    "storm clouds landscape",
    "rural road night",
]

_QUERY_STOPWORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "for",
    "from",
    "in",
    "into",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
    "without",
    "coherent",
    "composition",
    "concept",
    "director",
    "emotion",
    "emotional",
    "interpretation",
    "meaning",
    "metaphor",
    "motif",
    "purposeful",
    "representing",
    "symbolic",
    "visual",
}

_ABSTRACT_TERMS = {
    "soul",
    "heart",
    "love",
    "fate",
    "guilt",
    "judgment",
    "surrender",
    "truth",
    "memory",
    "dream",
    "dreams",
}


@dataclass(frozen=True)
class SelectionContext:
    aspect_ratio: str = "16:9"
    used_video_ids: frozenset[int] = frozenset()
    creator_counts: dict[str, int] | None = None


def compact_query(text: str, max_words: int = 6) -> str:
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", text.lower())
    chosen: list[str] = []

    for word in words:
        word = word.strip("'")

        if len(word) < 3:
            continue

        if word in _QUERY_STOPWORDS or word in _ABSTRACT_TERMS:
            continue

        if word not in chosen:
            chosen.append(word)

        if len(chosen) >= max_words:
            break

    return " ".join(chosen)


def fallback_queries(scene: dict[str, Any]) -> list[str]:
    """Return concrete Pexels searches from specific to broad.

    Pexels has limited metadata and responds best to short noun/adjective phrases.
    These fallbacks deliberately avoid sending creative-director language.
    """
    base = compact_query(str(scene.get("pexels_query", "")))
    visual = compact_query(str(scene.get("visual", "")))
    lyric = compact_query(str(scene.get("lyric_excerpt", "")))
    section = str(scene.get("section", "")).lower()

    queries: list[str] = []

    for candidate in [base, visual, lyric]:
        if candidate and candidate not in queries:
            queries.append(candidate)

    if "western" in f"{base} {visual} {lyric}" or "cowboy" in f"{base} {visual} {lyric}":
        western_fallbacks = [
            "western drifter dusty road",
            "cowboy horse desert",
            "old western town",
            "desert church sunset",
        ]
        for query in western_fallbacks:
            if query not in queries:
                queries.append(query)

    if "instrumental" in section or "solo" in section:
        for query in [
            "empty road sunset",
            "wide landscape dusk",
            "storm clouds desert",
        ]:
            if query not in queries:
                queries.append(query)

    for query in ATMOSPHERIC_FALLBACKS:
        if query not in queries:
            queries.append(query)

    return queries[:6]


def score_candidate(
    candidate: dict[str, Any],
    scene: dict[str, Any],
    context: SelectionContext,
) -> float:
    """Score only metadata Pexels exposes.

    This cannot determine true semantic fit. It rewards practical editing traits,
    query coverage in known text fields, and project-wide variety.
    """
    score = 0.0
    width = int(candidate.get("width") or 0)
    height = int(candidate.get("height") or 0)
    duration = float(candidate.get("duration") or 0)
    video_id = candidate.get("id")
    creator = str(candidate.get("creator") or "")
    creator_counts = context.creator_counts or {}

    if width and height:
        if context.aspect_ratio == "9:16":
            score += 25 if height >= width else -25
        elif context.aspect_ratio == "1:1":
            ratio = width / max(height, 1)
            score += max(0, 20 - abs(ratio - 1) * 20)
        else:
            score += 25 if width >= height else -25

        long_edge = max(width, height)
        if 1280 <= long_edge <= 1920:
            score += 25
        elif long_edge > 1920:
            score += 12
        elif long_edge >= 720:
            score += 8
        else:
            score -= 15

    scene_duration = max(
        0.1,
        float(scene.get("duration") or 0)
        or float(scene.get("end", 0)) - float(scene.get("start", 0)),
    )

    if duration:
        if 4 <= duration <= 30:
            score += 18
        elif duration > 30:
            score += 8
        else:
            score -= 10

        if duration >= min(scene_duration, 12):
            score += 8

    text = " ".join(
        str(candidate.get(key, ""))
        for key in ["query", "page_url", "creator"]
    ).lower()
    query_terms = set(compact_query(str(candidate.get("query", ""))).split())
    scene_terms = set(compact_query(str(scene.get("pexels_query", ""))).split())
    score += min(12, len(query_terms & scene_terms) * 4)

    if any(term in text for term in scene_terms):
        score += 4

    if video_id in context.used_video_ids:
        score -= 100

    score -= creator_counts.get(creator, 0) * 12

    return score


def rank_candidates(
    candidates: list[dict[str, Any]],
    scene: dict[str, Any],
    context: SelectionContext,
) -> list[dict[str, Any]]:
    ranked = []

    for candidate in candidates:
        scored = dict(candidate)
        scored["score"] = round(score_candidate(candidate, scene, context), 2)
        ranked.append(scored)

    return sorted(
        ranked,
        key=lambda item: (item.get("score", 0), -int(item.get("id") or 0)),
        reverse=True,
    )


def select_best_candidate(
    candidates: list[dict[str, Any]],
    scene: dict[str, Any],
    context: SelectionContext,
) -> dict[str, Any] | None:
    ranked = rank_candidates(candidates, scene, context)

    for candidate in ranked:
        if candidate.get("id") not in context.used_video_ids:
            return candidate

    return ranked[0] if ranked else None


def automatic_select_for_scene(
    scene: dict[str, Any],
    search_func: Callable[[str], list[dict[str, Any]]],
    context: SelectionContext,
) -> dict[str, Any]:
    attempts = []

    for query in fallback_queries(scene):
        results = search_func(query)
        enriched = [dict(result, query=query) for result in results]
        ranked = rank_candidates(enriched, scene, context)
        attempts.append(
            {
                "query": query,
                "count": len(ranked),
                "candidates": ranked,
            }
        )

        choice = select_best_candidate(enriched, scene, context)

        if choice:
            return {
                "status": "selected",
                "query": query,
                "selected": choice,
                "attempts": attempts,
                "candidates": ranked,
            }

    return {
        "status": "needs_attention",
        "query": fallback_queries(scene)[0],
        "selected": None,
        "attempts": attempts,
        "candidates": [],
        "message": "No usable Pexels footage was found for this scene.",
    }


def update_selection_context(
    context: SelectionContext,
    selected: dict[str, Any] | None,
) -> SelectionContext:
    if not selected:
        return context

    used = set(context.used_video_ids)
    video_id = selected.get("id")
    if video_id is not None:
        used.add(video_id)

    creator_counts = dict(context.creator_counts or {})
    creator = str(selected.get("creator") or "")
    if creator:
        creator_counts[creator] = creator_counts.get(creator, 0) + 1

    return SelectionContext(
        aspect_ratio=context.aspect_ratio,
        used_video_ids=frozenset(used),
        creator_counts=creator_counts,
    )
