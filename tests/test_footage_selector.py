from footage_selector import (
    SelectionContext,
    automatic_select_for_scene,
    fallback_queries,
    pool_candidates_from_queries,
    rank_candidates,
    update_selection_context,
)


def scene():
    return {
        "scene": 1,
        "section": "Verse 1",
        "start": 0,
        "end": 14,
        "duration": 14,
        "lyric_excerpt": "The outlaw rides into the dust",
        "visual": "A solitary western drifter follows a dusty road",
        "pexels_query": "western drifter dusty road",
    }


def candidate(video_id, width=1920, height=1080, duration=12, creator="A"):
    return {
        "id": video_id,
        "width": width,
        "height": height,
        "duration": duration,
        "creator": creator,
        "video_url": f"https://example.test/{video_id}.mp4",
        "page_url": f"https://pexels.test/{video_id}",
    }


def test_fallback_queries_are_short_and_concrete():
    queries = fallback_queries(scene())

    assert queries[0] == "western drifter dusty road"
    assert all(1 <= len(query.split()) <= 6 for query in queries)
    assert not any("cinematic interpretation" in query for query in queries)


def test_ranking_prefers_usable_landscape_and_avoids_duplicates():
    context = SelectionContext(aspect_ratio="16:9", used_video_ids=frozenset({1}))
    ranked = rank_candidates(
        [
            candidate(1),
            candidate(2, width=640, height=360),
            candidate(3, width=1600, height=900),
        ],
        scene(),
        context,
    )

    assert ranked[0]["id"] == 3
    assert ranked[-1]["id"] == 1


def test_automatic_selection_considers_later_queries_when_first_has_results():
    calls = []

    def search(query):
        calls.append(query)
        if len(calls) == 1:
            return [candidate(5, width=640, height=360, duration=2)]
        if "western" in query:
            return [candidate(9, width=1600, height=900, duration=14)]
        return []

    result = automatic_select_for_scene(scene(), search, SelectionContext())

    assert result["status"] == "selected"
    assert result["selected"]["id"] == 9
    assert len(calls) > 1


def test_pooled_candidates_are_deduplicated_and_keep_source_query():
    def search(query):
        if query == "western drifter dusty road":
            return [candidate(7), candidate(8)]
        return [candidate(7), candidate(9)] if "western" in query else []

    pooled, attempts = pool_candidates_from_queries(scene(), search)
    ids = [item["id"] for item in pooled]

    assert len(ids) == len(set(ids))
    assert attempts[0]["query"] == "western drifter dusty road"
    assert all(item["source_query"] for item in pooled)
    assert next(item for item in pooled if item["id"] == 7)["source_query"] == "western drifter dusty road"


def test_duplicate_clip_avoidance_across_scenes_selects_unused_candidate():
    def search(query):
        return [
            candidate(1, width=1600, height=900, duration=14),
            candidate(2, width=1600, height=900, duration=14),
        ]

    result = automatic_select_for_scene(
        scene(),
        search,
        SelectionContext(used_video_ids=frozenset({1})),
    )

    assert result["selected"]["id"] == 2


def test_deterministic_selection_works_without_openai_preview_ranker():
    result = automatic_select_for_scene(
        scene(),
        lambda query: [candidate(11, width=1600, height=900, duration=14)],
        SelectionContext(),
        preview_ranker=None,
    )

    assert result["status"] == "selected"
    assert result["selected"]["id"] == 11


def test_selection_context_tracks_video_and_creator_reuse():
    context = update_selection_context(
        SelectionContext(),
        candidate(42, creator="Pexels Artist"),
    )

    assert 42 in context.used_video_ids
    assert context.creator_counts["Pexels Artist"] == 1
