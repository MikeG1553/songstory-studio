from project_state import clear_project_state, ensure_project_state, reset_footage_state


def test_ensure_project_state_sets_defaults():
    state = {}

    ensure_project_state(state)

    assert state["storyboard"] is None
    assert state["selected_pexels_clips"] == {}
    assert state["downloaded_clip_paths"] == {}


def test_reset_footage_state_preserves_storyboard_and_audio():
    state = {
        "storyboard": {"scenes": []},
        "audio_path": "song.mp3",
        "selected_pexels_clips": {1: {"id": 1}},
        "final_video_path": "draft.mp4",
    }

    reset_footage_state(state)

    assert state["storyboard"] == {"scenes": []}
    assert state["audio_path"] == "song.mp3"
    assert state["selected_pexels_clips"] == {}
    assert "final_video_path" not in state


def test_clear_project_state_removes_project_keys():
    state = {
        "storyboard": {"scenes": []},
        "audio_path": "song.mp3",
        "unrelated": True,
    }

    clear_project_state(state)

    assert "storyboard" not in state
    assert "audio_path" not in state
    assert state["unrelated"] is True
