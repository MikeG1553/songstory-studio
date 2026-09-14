from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

VISUAL_FIT_THRESHOLD = 70
VISUAL_REVIEW_SHORTLIST_SIZE = 5
MAX_VISUAL_REVIEW_ROUNDS = 2

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


def query_variants(scene: dict[str, Any]) -> list[dict[str, Any]]:
    """Build useful query variants before selection.

    The first query is the primary scene query. Later queries broaden toward
    visual-world, location, instrumental, and atmospheric searches. Selection is
    made only after candidates from these variants have been pooled.
    """
    variants: list[dict[str, Any]] = []

    for index, query in enumerate(fallback_queries(scene)):
        if index == 0:
            role = "primary"
        elif index == 1:
            role = "simplified"
        elif any(term in query for term in ["western", "desert", "church", "town", "road"]):
            role = "visual_world"
        else:
            role = "fallback"

        variants.append(
            {
                "query": query,
                "role": role,
                "rank": index,
            }
        )

    return variants


def retry_query_variants(scene: dict[str, Any]) -> list[dict[str, Any]]:
    text = " ".join(
        str(scene.get(key, ""))
        for key in ["pexels_query", "visual", "lyric_excerpt", "section"]
    ).lower()

    if any(term in text for term in ["church", "grave", "graveyard", "judgment"]):
        queries = [
            "old western church",
            "abandoned church desert",
            "old cemetery western",
            "church graveyard sunset",
        ]
    elif any(term in text for term in ["horse", "rider", "drifter", "bounty", "outlaw", "western"]):
        queries = [
            "cowboy walking desert road",
            "weathered cowboy western landscape",
            "lone horse rider desert",
            "old west bounty hunter",
        ]
    elif "instrumental" in text or "solo" in text:
        queries = [
            "western landscape sunset",
            "dusty road storm clouds",
            "horse rider desert dusk",
            "empty frontier town",
        ]
    else:
        queries = [
            "lonely road dusk",
            "storm clouds landscape",
            "solitary man road",
            "wide desert sunset",
        ]

    return [
        {
            "query": query,
            "role": "retry",
            "rank": index + 10,
        }
        for index, query in enumerate(queries)
    ]


def pool_candidates_from_queries(
    scene: dict[str, Any],
    search_func: Callable[[str], list[dict[str, Any]]],
    variants: list[dict[str, Any]] | None = None,
    search_round: int = 1,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    pooled_by_id: dict[Any, dict[str, Any]] = {}
    attempts = []

    for variant in variants or query_variants(scene):
        query = variant["query"]
        results = search_func(query)
        enriched_results = []

        for result in results:
            enriched = dict(result)
            enriched["query"] = query
            enriched["source_query"] = query
            enriched["source_query_role"] = variant["role"]
            enriched["source_query_rank"] = variant["rank"]
            enriched["search_round"] = search_round
            enriched_results.append(enriched)

            video_id = enriched.get("id")
            key = video_id if video_id is not None else enriched.get("video_url")

            if key is None:
                continue

            existing = pooled_by_id.get(key)
            if existing is None or variant["rank"] < existing.get("source_query_rank", 99):
                pooled_by_id[key] = enriched

        attempts.append(
            {
                "query": query,
                "role": variant["role"],
                "search_round": search_round,
                "count": len(enriched_results),
                "candidates": enriched_results,
            }
        )

    return list(pooled_by_id.values()), attempts


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
    source_query_rank = int(candidate.get("source_query_rank") or 0)
    source_query_role = str(candidate.get("source_query_role") or "")

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

    if source_query_role == "primary":
        score += 10
    elif source_query_role == "simplified":
        score += 6
    elif source_query_role == "visual_world":
        score += 4

    score -= min(source_query_rank, 5) * 1.5

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


def _candidate_key(candidate: dict[str, Any]) -> str:
    value = candidate.get("id") or candidate.get("preview_image") or candidate.get("video_url")
    return str(value)


def _visual_context_text(
    scene: dict[str, Any],
    project_context: dict[str, Any] | None,
    continuity_summary: str = "",
) -> str:
    context = project_context or {}
    visual_world = context.get("visual_world") or {}
    positive = visual_world.get("positive_cues") or context.get("positive_cues") or []
    negative = visual_world.get("negative_cues") or context.get("negative_cues") or []
    motifs = visual_world.get("recurring_motifs") or context.get("recurring_motifs") or []

    return "\n".join(
        [
            f"Visual world: {visual_world.get('name') or context.get('visual_world_name', '')}",
            f"World description: {visual_world.get('description') or context.get('visual_style', '')}",
            f"Protagonist: {visual_world.get('protagonist_description') or context.get('protagonist_description', '')}",
            f"Era: {visual_world.get('era') or context.get('era', '')}",
            f"Locations: {', '.join(visual_world.get('locations') or context.get('locations') or [])}",
            f"Recurring motifs: {', '.join(motifs)}",
            f"Positive visual cues: {', '.join(positive)}",
            f"Strong negative cues to reject: {', '.join(negative)}",
            f"Continuity summary: {continuity_summary}",
            f"Scene section: {scene.get('section', '')}",
            f"Lyric/musical moment: {scene.get('lyric_excerpt', '')}",
            f"Visual concept: {scene.get('visual', '')}",
        ]
    )


def _normalize_visual_review(
    review: dict[str, Any] | None,
) -> dict[str, Any]:
    if not review:
        return {
            "evaluations": [],
            "best_id": None,
        }

    evaluations = []
    for item in review.get("evaluations", []):
        try:
            fit_score = int(item.get("fit_score", 0))
        except (TypeError, ValueError):
            fit_score = 0
        evaluations.append(
            {
                "id": item.get("id"),
                "fit_score": max(0, min(100, fit_score)),
                "accept": bool(item.get("accept")),
                "reason": str(item.get("reason", "")).strip(),
            }
        )

    return {
        "evaluations": evaluations,
        "best_id": review.get("best_id"),
    }


def evaluate_visual_shortlist(
    scene: dict[str, Any],
    shortlist: list[dict[str, Any]],
    visual_reviewer: Callable[
        [dict[str, Any], list[dict[str, Any]], dict[str, Any] | None, str],
        dict[str, Any],
    ],
    project_context: dict[str, Any] | None,
    continuity_summary: str,
    review_cache: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    review_cache = review_cache if review_cache is not None else {}
    scene_key = str(scene.get("scene") or scene.get("section") or "")
    uncached = [
        candidate
        for candidate in shortlist
        if f"{scene_key}:{_candidate_key(candidate)}" not in review_cache
    ]

    if uncached:
        review = _normalize_visual_review(
            visual_reviewer(scene, uncached, project_context, continuity_summary)
        )
        for evaluation in review["evaluations"]:
            if evaluation.get("id") is not None:
                review_cache[f"{scene_key}:{evaluation['id']}"] = evaluation

    evaluations = [
        review_cache.get(f"{scene_key}:{_candidate_key(candidate)}")
        for candidate in shortlist
        if review_cache.get(f"{scene_key}:{_candidate_key(candidate)}")
    ]

    return {
        "evaluations": evaluations,
        "best_id": None,
    }


def choose_ai_accepted_candidate(
    ranked: list[dict[str, Any]],
    evaluations: list[dict[str, Any]],
    threshold: int,
) -> dict[str, Any] | None:
    evaluations_by_id = {
        str(item.get("id")): item
        for item in evaluations
        if item.get("id") is not None
    }

    accepted = [
        item
        for item in evaluations_by_id.values()
        if item.get("accept") and int(item.get("fit_score", 0)) >= threshold
    ]

    if not accepted:
        return None

    accepted.sort(key=lambda item: int(item.get("fit_score", 0)), reverse=True)
    best_id = str(accepted[0].get("id"))

    for candidate in ranked:
        if str(candidate.get("id")) == best_id:
            selected = dict(candidate)
            selected["visual_fit_score"] = accepted[0].get("fit_score")
            selected["visual_review_accept"] = accepted[0].get("accept")
            selected["visual_review_reason"] = accepted[0].get("reason")
            return selected

    return None


def automatic_select_for_scene(
    scene: dict[str, Any],
    search_func: Callable[[str], list[dict[str, Any]]],
    context: SelectionContext,
    preview_ranker: Callable[[dict[str, Any], list[dict[str, Any]]], dict[str, Any] | None] | None = None,
    preview_shortlist_size: int = 3,
    visual_reviewer: Callable[
        [dict[str, Any], list[dict[str, Any]], dict[str, Any] | None, str],
        dict[str, Any],
    ] | None = None,
    require_visual_review: bool = False,
    project_context: dict[str, Any] | None = None,
    continuity_summary: str = "",
    visual_review_cache: dict[str, dict[str, Any]] | None = None,
    visual_threshold: int = VISUAL_FIT_THRESHOLD,
    max_visual_rounds: int = MAX_VISUAL_REVIEW_ROUNDS,
    visual_shortlist_size: int = VISUAL_REVIEW_SHORTLIST_SIZE,
) -> dict[str, Any]:
    all_attempts = []
    all_ranked = []
    all_evaluations = []

    if require_visual_review and visual_reviewer is None:
        return {
            "status": "needs_attention",
            "query": fallback_queries(scene)[0],
            "selected": None,
            "attempts": [],
            "candidates": [],
            "visual_evaluations": [],
            "message": (
                "High-quality automatic footage selection requires an OpenAI API key "
                "for visual review of Pexels preview images."
            ),
        }

    for search_round in range(1, max_visual_rounds + 1):
        variants = query_variants(scene) if search_round == 1 else retry_query_variants(scene)
        pooled, attempts = pool_candidates_from_queries(
            scene,
            search_func,
            variants=variants,
            search_round=search_round,
        )
        ranked = rank_candidates(pooled, scene, context)
        all_attempts.extend(attempts)
        all_ranked.extend(ranked)

        available = [
            candidate
            for candidate in ranked
            if candidate.get("id") not in context.used_video_ids
        ]

        if not available:
            continue

        if require_visual_review and visual_reviewer:
            shortlist = available[: max(1, visual_shortlist_size)]
            review = evaluate_visual_shortlist(
                scene,
                shortlist,
                visual_reviewer,
                project_context,
                continuity_summary,
                visual_review_cache,
            )
            all_evaluations.extend(review["evaluations"])
            choice = choose_ai_accepted_candidate(
                shortlist,
                review["evaluations"],
                visual_threshold,
            )

            if choice:
                choice["search_round"] = search_round
                return {
                    "status": "selected",
                    "query": choice.get("source_query") or choice.get("query"),
                    "selected": choice,
                    "attempts": all_attempts,
                    "candidates": all_ranked,
                    "visual_evaluations": all_evaluations,
                    "ai_preview_selected": True,
                    "visual_threshold": visual_threshold,
                }

            continue

        if preview_ranker:
            shortlist = available[: max(1, preview_shortlist_size)]
            preview_choice = preview_ranker(scene, shortlist)

            if preview_choice and preview_choice.get("id") not in context.used_video_ids:
                preview_choice["search_round"] = search_round
                return {
                    "status": "selected",
                    "query": preview_choice.get("source_query") or preview_choice.get("query"),
                    "selected": preview_choice,
                    "attempts": all_attempts,
                    "candidates": all_ranked,
                    "ai_preview_selected": True,
                }

        choice = available[0]
        choice["search_round"] = search_round
        return {
            "status": "selected",
            "query": choice.get("source_query") or choice.get("query"),
            "selected": choice,
            "attempts": all_attempts,
            "candidates": all_ranked,
            "ai_preview_selected": False,
            "basic_metadata_mode": True,
        }

    return {
        "status": "needs_attention",
        "query": fallback_queries(scene)[0],
        "selected": None,
        "attempts": all_attempts,
        "candidates": all_ranked,
        "visual_evaluations": all_evaluations,
        "visual_threshold": visual_threshold,
        "message": (
            "No candidate exceeded the visual relevance threshold."
            if require_visual_review
            else "No usable Pexels footage was found for this scene."
        ),
    }


def create_openai_visual_reviewer(
    api_key: str,
    model: str = "gpt-5.6-luna",
) -> Callable[
    [dict[str, Any], list[dict[str, Any]], dict[str, Any] | None, str],
    dict[str, Any],
] | None:
    if not api_key:
        return None

    def reviewer(
        scene: dict[str, Any],
        candidates: list[dict[str, Any]],
        project_context: dict[str, Any] | None,
        continuity_summary: str,
    ) -> dict[str, Any]:
        preview_candidates = [
            candidate
            for candidate in candidates
            if candidate.get("preview_image")
        ][:VISUAL_REVIEW_SHORTLIST_SIZE]

        if not preview_candidates:
            return {
                "evaluations": [],
                "best_id": None,
            }

        try:
            from openai import OpenAI

            client = OpenAI(api_key=api_key)
            content: list[dict[str, Any]] = [
                {
                    "type": "input_text",
                    "text": (
                        "You are the visual relevance gate for a story-driven music video. "
                        "Evaluate every Pexels preview image against the storyboard scene, "
                        "visual world, continuity, and explicit rejection rules. "
                        "Reject unrelated modern lifestyle, performance, wellness, bedroom, "
                        "floor-sitting, fire-performer, festival, musician, or performer imagery "
                        "unless the storyboard explicitly asks for it. Do not force a choice. "
                        "Return only JSON with evaluations and best_id. best_id must be null "
                        "when no candidate has fit_score >= 70.\n\n"
                        + _visual_context_text(scene, project_context, continuity_summary)
                    ),
                }
            ]

            for candidate in preview_candidates:
                content.append(
                    {
                        "type": "input_text",
                        "text": (
                            f"Candidate ID: {candidate.get('id')}; "
                            f"source query: {candidate.get('source_query') or candidate.get('query')}; "
                            f"deterministic score: {candidate.get('score', '')}; "
                            f"creator: {candidate.get('creator', '')}"
                        ),
                    }
                )
                content.append(
                    {
                        "type": "input_image",
                        "image_url": candidate["preview_image"],
                    }
                )

            response = client.responses.create(
                model=model,
                input=[
                    {
                        "role": "user",
                        "content": content,
                    }
                ],
            )

            match = re.search(r"\{.*\}", response.output_text, re.DOTALL)
            if not match:
                return {
                    "evaluations": [],
                    "best_id": None,
                }

            return _normalize_visual_review(json.loads(match.group(0)))

        except Exception:
            return {
                "evaluations": [],
                "best_id": None,
            }

    return reviewer


def create_openai_preview_ranker(
    api_key: str,
    model: str = "gpt-5.6-luna",
) -> Callable[[dict[str, Any], list[dict[str, Any]]], dict[str, Any] | None] | None:
    reviewer = create_openai_visual_reviewer(api_key, model)

    if not reviewer:
        return None

    def ranker(
        scene: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        review = reviewer(scene, candidates, None, "")
        return choose_ai_accepted_candidate(
            candidates,
            review.get("evaluations", []),
            VISUAL_FIT_THRESHOLD,
        )

    return ranker


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
