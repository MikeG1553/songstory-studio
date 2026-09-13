from core import heuristic_storyboard, infer_visual_world, parse_lyrics_sections


def test_parse_lyrics_preserves_section_labels():
    sections = parse_lyrics_sections(
        """
        Intro
        wind on the road

        Verse 1
        he walks into town

        Guitar Solo
        """
    )

    assert [section["label"] for section in sections] == [
        "Intro",
        "Verse 1",
        "Guitar Solo",
    ]


def test_lay_your_soul_down_uses_western_world_not_literal_sleep():
    lyrics = """
    Verse 1
    The outlaw rode with blood on his hands
    A bounty hunter chased him through dust

    Chorus
    Lay your soul down before judgment comes

    Bridge
    The old church bell rings over a graveyard
    """

    world = infer_visual_world(lyrics)
    storyboard = heuristic_storyboard(lyrics, duration=210)
    queries = " ".join(scene["pexels_query"] for scene in storyboard["scenes"])

    assert world["name"] == "Southern Gothic Western"
    assert "western" in queries or "desert" in queries
    assert "bed" not in queries
    assert "sleep" not in queries
