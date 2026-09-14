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
        "preview_image": f"https://example.test/{video_id}.jpg",
    }


def visual_context():
    return {
        "visual_world": {
            "name": "Southern Gothic Western",
            "positive_cues": [
                "weathered male drifter",
                "horse",
                "dusty road",
                "old church",
                "graveyard",
                "rugged outlaw",
            ],
            "negative_cues": [
                "flute players",
                "fire performers",
                "woman sitting on modern floor",
                "yoga",
                "meditation",
                "bedroom",
                "sleeping",
            ],
            "protagonist_description": "weathered male drifter / bounty hunter",
            "era": "historical or revisionist Western",
            "locations": ["dusty roads", "frontier town", "old church", "graveyard"],
            "recurring_motifs": ["horse", "revolver", "storm clouds"],
        }
    }


def review_from_scores(scores):
    def reviewer(scene_arg, candidates, project_context, continuity_summary):
        return {
            "evaluations": [
                {
                    "id": item["id"],
                    "fit_score": scores.get(item["id"], 0),
                    "accept": scores.get(item["id"], 0) >= 70,
                    "reason": f"mock reason for {item['id']}",
                }
                for item in candidates
            ],
            "best_id": max(scores, key=scores.get) if scores and max(scores.values()) >= 70 else None,
        }

    return reviewer


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


def test_ai_reviewer_selects_candidate_above_threshold():
    result = automatic_select_for_scene(
        scene(),
        lambda query: [candidate(101), candidate(102)],
        SelectionContext(),
        visual_reviewer=review_from_scores({101: 42, 102: 91}),
        require_visual_review=True,
        project_context=visual_context(),
    )

    assert result["status"] == "selected"
    assert result["selected"]["id"] == 102
    assert result["selected"]["visual_fit_score"] == 91
    assert result["selected"]["visual_review_accept"] is True
    assert result["selected"]["visual_review_reason"] == "mock reason for 102"


def test_candidate_below_threshold_is_rejected_without_deterministic_fallback():
    result = automatic_select_for_scene(
        scene(),
        lambda query: [candidate(201, width=1920, height=1080, duration=20)],
        SelectionContext(),
        visual_reviewer=review_from_scores({201: 69}),
        require_visual_review=True,
        project_context=visual_context(),
    )

    assert result["status"] == "needs_attention"
    assert result["selected"] is None
    assert result["visual_threshold"] == 70


def test_second_search_round_occurs_after_round_one_rejection():
    searched = []

    def search(query):
        searched.append(query)
        if query == "western drifter dusty road":
            return [candidate(301)]
        if query == "cowboy walking desert road":
            return [candidate(302)]
        return []

    result = automatic_select_for_scene(
        scene(),
        search,
        SelectionContext(),
        visual_reviewer=review_from_scores({301: 5, 302: 92}),
        require_visual_review=True,
        project_context=visual_context(),
    )

    assert "cowboy walking desert road" in searched
    assert result["status"] == "selected"
    assert result["selected"]["id"] == 302
    assert result["selected"]["search_round"] == 2


def test_reject_all_in_both_rounds_leaves_scene_unresolved():
    result = automatic_select_for_scene(
        scene(),
        lambda query: [candidate(401)],
        SelectionContext(),
        visual_reviewer=review_from_scores({401: 8}),
        require_visual_review=True,
        project_context=visual_context(),
    )

    assert result["status"] == "needs_attention"
    assert result["selected"] is None
    assert result["message"] == "No candidate exceeded the visual relevance threshold."


def test_round_two_passing_candidate_is_selected():
    def search(query):
        if query == "western drifter dusty road":
            return [candidate(501)]
        if query == "lone horse rider desert":
            return [candidate(502)]
        return []

    result = automatic_select_for_scene(
        scene(),
        search,
        SelectionContext(),
        visual_reviewer=review_from_scores({501: 10, 502: 88}),
        require_visual_review=True,
        project_context=visual_context(),
    )

    assert result["status"] == "selected"
    assert result["selected"]["id"] == 502
    assert result["selected"]["search_round"] == 2


def test_scene_selection_metadata_includes_visual_review_and_query_fields():
    result = automatic_select_for_scene(
        scene(),
        lambda query: [candidate(601)],
        SelectionContext(),
        visual_reviewer=review_from_scores({601: 94}),
        require_visual_review=True,
        project_context=visual_context(),
    )

    selected = result["selected"]
    assert selected["visual_fit_score"] == 94
    assert selected["visual_review_accept"] is True
    assert selected["visual_review_reason"] == "mock reason for 601"
    assert selected["source_query"]
    assert selected["search_round"] == 1


def test_duplicate_video_ids_remain_excluded_with_visual_review():
    result = automatic_select_for_scene(
        scene(),
        lambda query: [candidate(701), candidate(702)],
        SelectionContext(used_video_ids=frozenset({701})),
        visual_reviewer=review_from_scores({701: 99, 702: 80}),
        require_visual_review=True,
        project_context=visual_context(),
    )

    assert result["selected"]["id"] == 702


def test_visual_reviewer_receives_project_context_and_continuity_summary():
    captured = {}

    def reviewer(scene_arg, candidates, project_context, continuity_summary):
        captured["project_context"] = project_context
        captured["continuity_summary"] = continuity_summary
        return review_from_scores({801: 90})(scene_arg, candidates, project_context, continuity_summary)

    automatic_select_for_scene(
        scene(),
        lambda query: [candidate(801)],
        SelectionContext(),
        visual_reviewer=reviewer,
        require_visual_review=True,
        project_context=visual_context(),
        continuity_summary="Scene 1: weathered rider on dusty road",
    )

    world = captured["project_context"]["visual_world"]
    assert world["name"] == "Southern Gothic Western"
    assert world["positive_cues"]
    assert world["negative_cues"]
    assert world["protagonist_description"]
    assert world["era"]
    assert world["locations"]
    assert world["recurring_motifs"]
    assert "weathered rider" in captured["continuity_summary"]


def test_visual_review_cache_avoids_rechecking_same_scene_candidate():
    calls = []
    cache = {}

    def reviewer(scene_arg, candidates, project_context, continuity_summary):
        calls.append([item["id"] for item in candidates])
        return review_from_scores({901: 90})(scene_arg, candidates, project_context, continuity_summary)

    for _ in range(2):
        result = automatic_select_for_scene(
            scene(),
            lambda query: [candidate(901)],
            SelectionContext(),
            visual_reviewer=reviewer,
            require_visual_review=True,
            project_context=visual_context(),
            visual_review_cache=cache,
        )
        assert result["selected"]["id"] == 901

    assert calls == [[901]]


def test_missing_openai_key_blocks_ai_reviewed_mode():
    result = automatic_select_for_scene(
        scene(),
        lambda query: [candidate(1001)],
        SelectionContext(),
        visual_reviewer=None,
        require_visual_review=True,
        project_context=visual_context(),
    )

    assert result["status"] == "needs_attention"
    assert result["selected"] is None
    assert "OpenAI API key" in result["message"]


def test_basic_metadata_mode_is_explicitly_separate_from_ai_reviewed_mode():
    result = automatic_select_for_scene(
        scene(),
        lambda query: [candidate(1101)],
        SelectionContext(),
        visual_reviewer=None,
        require_visual_review=False,
    )

    assert result["status"] == "selected"
    assert result["selected"]["id"] == 1101
    assert result["basic_metadata_mode"] is True
    assert result["ai_preview_selected"] is False


def test_lay_your_soul_down_mismatches_are_rejected_and_western_matches_pass():
    descriptions = {
        1201: "woman playing flute indoors",
        1202: "fire performer spinning flames at festival",
        1203: "woman sitting on modern floor",
        1204: "yoga meditation bedroom sleeping scene",
        1205: "weathered male drifter on dusty road",
        1206: "lone horse rider in western landscape",
        1207: "old church graveyard in western setting",
        1208: "rugged outlaw bounty hunter imagery",
    }

    def reviewer(scene_arg, candidates, project_context, continuity_summary):
        evaluations = []
        for item in candidates:
            good = item["id"] in {1205, 1206, 1207, 1208}
            evaluations.append(
                {
                    "id": item["id"],
                    "fit_score": 90 if good else 5,
                    "accept": good,
                    "reason": descriptions[item["id"]],
                }
            )
        return {"evaluations": evaluations, "best_id": 1205}

    results = [dict(candidate(video_id), description=text) for video_id, text in descriptions.items()]
    result = automatic_select_for_scene(
        scene(),
        lambda query: results,
        SelectionContext(),
        visual_reviewer=reviewer,
        require_visual_review=True,
        project_context=visual_context(),
        visual_shortlist_size=8,
    )

    rejected_reasons = " ".join(
        item["reason"]
        for item in result["visual_evaluations"]
        if not item["accept"]
    )
    assert result["selected"]["id"] in {1205, 1206, 1207, 1208}
    assert "woman playing flute" in rejected_reasons
    assert "fire performer" in rejected_reasons
    assert "modern floor" in rejected_reasons
    assert "yoga meditation bedroom sleeping" in rejected_reasons
