from core import heuristic_storyboard, infer_visual_world, parse_lyrics_sections


LAY_YOUR_SOUL_DOWN = """
Title:
Lay Your Soul Down

Instrumental Intro

Verse 1

Had the look of a drifter
A hard life to blame
He didn’t seek fortune
Kept nothing for fame
Bounty for dead
Bounty for live
His will against yours
In a fight to survive

Chorus

A life with no future
Won’t admit to his past
His fate that awaits
The dye has been cast
How his life ended
No one will remember
Just lay your soul down
To the devil surrender

Instrumental Break

Verse 2

He was hardened by living
Always a stranger in town
Had a code that he followed
Track another man down
Murder for hire
Murder for pay
He brought them in dead
The soulless man’s way

Chorus

A life with no future
Won’t admit to his past
It’s done for the need
To kill to the last
How his life ended
No one will remember
Just lay your soul down
To the devil surrender

Instrumental Cut

Verse 3

To pulling the trigger
Just a lifelong slave
What fueled his anguish
He will take to his grave
Bounty for dead
Bounty for live
He will watch your blood flow
As you lay there and die

Instrumental Cut

Chorus

Gods law broken
Gods law denied
He laid his soul down
He felt nothing inside
How his life ended
No one will remember
Just lay your soul down
To the devil surrender

Instrumental Outro
"""


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


def test_lay_your_soul_down_actual_structure_is_preserved():
    sections = parse_lyrics_sections(LAY_YOUR_SOUL_DOWN)
    labels = [section["label"] for section in sections]

    assert labels == [
        "Instrumental Intro",
        "Verse 1",
        "Chorus",
        "Instrumental Break",
        "Verse 2",
        "Chorus",
        "Instrumental Cut",
        "Verse 3",
        "Instrumental Cut",
        "Chorus",
        "Instrumental Outro",
    ]
    assert labels.count("Chorus") == 3
    assert labels.count("Instrumental Cut") == 2
    assert "Lay Your Soul Down" not in sections[0]["lines"]


def test_lay_your_soul_down_acceptance_storyboard_stays_western():
    storyboard = heuristic_storyboard(LAY_YOUR_SOUL_DOWN, duration=230)
    scene_text = " ".join(
        f"{scene['section']} {scene['lyric_excerpt']} {scene['pexels_query']}"
        for scene in storyboard["scenes"]
    ).lower()

    assert "southern gothic western" in storyboard["concept"].lower()
    assert "instrumental intro" in scene_text
    assert "instrumental break" in scene_text
    assert "instrumental cut" in scene_text
    assert "instrumental outro" in scene_text
    assert "bed" not in scene_text
    assert "sleep" not in scene_text
