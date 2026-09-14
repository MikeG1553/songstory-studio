import json
import zipfile
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from project_archive import (
    PROJECT_MANIFEST,
    ProjectArchiveError,
    build_project_manifest,
    create_project_archive,
    load_project_archive,
)


def sample_state(tmp_path: Path) -> dict:
    audio = tmp_path / "song.wav"
    audio.write_bytes(b"audio bytes")
    reference = tmp_path / "protagonist.png"
    reference.write_bytes(b"reference bytes")
    still = tmp_path / "scene_001.png"
    still.write_bytes(b"still bytes")

    return {
        "song_filename": "song.wav",
        "audio_path": str(audio),
        "audio_duration": 123.4,
        "lyrics": "Verse 1\nLay your soul down",
        "video_style": "Southern Gothic Western",
        "interpretation": "Mostly symbolic",
        "aspect_ratio": "16:9",
        "analysis_model": "gpt-5.6-luna",
        "image_model": "gpt-image-2.5-flare",
        "storyboard": {
            "concept": "A western reckoning",
            "story_arc": "Drifter faces judgment",
            "mood": "Dusty and haunted",
            "visual_style": "Revisionist western realism",
            "directors_bible": {
                "protagonist_description": "weathered male drifter",
                "era": "historical western",
            },
            "scenes": [
                {
                    "scene_id": 1,
                    "preferred_source_type": "generated_still",
                    "excluded": False,
                    "text_overlay": {"text": "Lay Your Soul Down"},
                }
            ],
        },
        "protagonist_reference_approved": True,
        "protagonist_reference": {
            "reference_image_path": str(reference),
            "approved": True,
            "generation_metadata": {"actual_image_model": "gpt-image-2.5-flare"},
        },
        "hybrid_scene_media": {
            1: {
                "source_type": "generated_still",
                "generated_image_path": str(still),
                "generation_metadata": {"generation_mode": "text_generation"},
            }
        },
        "scene_selection_status": {1: {"status": "selected"}},
        "footage_credits": [],
    }


def manifest_from_package(package: bytes) -> dict:
    with zipfile.ZipFile(BytesIO(package), "r") as archive:
        return json.loads(archive.read(PROJECT_MANIFEST).decode("utf-8"))


def test_project_serialization_creates_songstory_zip_manifest():
    with TemporaryDirectory() as temp_dir:
        package = create_project_archive(sample_state(Path(temp_dir)))
        manifest = manifest_from_package(package)

    assert manifest["project_version"] == "songstory-project-v0.6"
    assert manifest["song_filename"] == "song.wav"
    assert manifest["assets"]["audio_path"].startswith("assets/audio/")


def test_project_restoration_restores_assets_and_state():
    with TemporaryDirectory() as temp_dir:
        package = create_project_archive(sample_state(Path(temp_dir)))
        restored_dir = Path(temp_dir) / "restored"
        restored = load_project_archive(package, restored_dir)

        assert Path(restored["audio_path"]).exists()
        assert Path(restored["protagonist_reference"]["reference_image_path"]).exists()
        media = restored["hybrid_scene_media"]["1"]
        assert Path(media["generated_image_path"]).exists()


def test_directors_bible_survives_round_trip():
    with TemporaryDirectory() as temp_dir:
        package = create_project_archive(sample_state(Path(temp_dir)))
        restored = load_project_archive(package, Path(temp_dir) / "restored")

    assert restored["storyboard"]["directors_bible"]["protagonist_description"] == "weathered male drifter"


def test_lyrics_survive_round_trip():
    with TemporaryDirectory() as temp_dir:
        package = create_project_archive(sample_state(Path(temp_dir)))
        restored = load_project_archive(package, Path(temp_dir) / "restored")

    assert restored["lyrics"] == "Verse 1\nLay your soul down"


def test_creative_settings_survive_round_trip():
    with TemporaryDirectory() as temp_dir:
        package = create_project_archive(sample_state(Path(temp_dir)))
        restored = load_project_archive(package, Path(temp_dir) / "restored")

    assert restored["video_style"] == "Southern Gothic Western"
    assert restored["interpretation"] == "Mostly symbolic"
    assert restored["aspect_ratio"] == "16:9"
    assert restored["analysis_model"] == "gpt-5.6-luna"
    assert restored["image_model"] == "gpt-image-2.5-flare"


def test_storyboard_survives_round_trip():
    with TemporaryDirectory() as temp_dir:
        package = create_project_archive(sample_state(Path(temp_dir)))
        restored = load_project_archive(package, Path(temp_dir) / "restored")

    scene = restored["storyboard"]["scenes"][0]
    assert scene["preferred_source_type"] == "generated_still"
    assert scene["text_overlay"]["text"] == "Lay Your Soul Down"
    assert scene["excluded"] is False


def test_older_project_without_contains_protagonist_backfills_conservatively():
    with TemporaryDirectory() as temp_dir:
        state = sample_state(Path(temp_dir))
        state["storyboard"]["scenes"] = [
            {
                "scene_id": 1,
                "recommended_visual": "weathered drifter on dusty road",
            },
            {
                "scene_id": 2,
                "lyric_excerpt": "He was hardened by living",
                "recommended_visual": "empty desert landscape",
            },
        ]
        package = create_project_archive(state)
        restored = load_project_archive(package, Path(temp_dir) / "restored")

    first, second = restored["storyboard"]["scenes"]
    assert first["contains_protagonist"] is True
    assert second["contains_protagonist"] is False


def test_api_keys_are_never_serialized():
    with TemporaryDirectory() as temp_dir:
        state = sample_state(Path(temp_dir))
        state["openai_api_key"] = "secret"
        state["pexels_api_key"] = "also-secret"
        package = create_project_archive(state)

    assert b"secret" not in package
    assert b"also-secret" not in package


def test_missing_optional_assets_do_not_crash_load():
    with TemporaryDirectory() as temp_dir:
        state = sample_state(Path(temp_dir))
        state["protagonist_reference"]["reference_image_path"] = str(Path(temp_dir) / "missing.png")
        state["hybrid_scene_media"][1]["generated_image_path"] = str(Path(temp_dir) / "missing_still.png")
        package = create_project_archive(state)
        restored = load_project_archive(package, Path(temp_dir) / "restored")

    assert restored["protagonist_reference"]["reference_image_path"].endswith("missing.png")
    assert restored["hybrid_scene_media"]["1"]["generated_image_path"].endswith("missing_still.png")


def test_invalid_project_package_gives_clear_error():
    with TemporaryDirectory() as temp_dir:
        try:
            load_project_archive(b"not a zip", Path(temp_dir) / "restored")
        except ProjectArchiveError as exc:
            assert "Invalid SongStory project" in str(exc)
        else:
            raise AssertionError("Invalid package should fail clearly")
