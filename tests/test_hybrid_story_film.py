import base64
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from core import heuristic_storyboard
from directors_bible import create_directors_bible
from footage_selector import SelectionContext
from hybrid_media import preserve_scene_media_state, select_hybrid_media_for_scene, text_overlay
from image_generation import (
    DEFAULT_IMAGE_MODEL,
    HERO_IMAGE_MODEL,
    REFERENCE_IMAGE_MODEL,
    build_still_prompt,
    generate_protagonist_reference,
    generate_still_image,
    scene_includes_protagonist,
)
from renderer import animate_still_image


LAY_YOUR_SOUL_DOWN = """
Instrumental Intro

Verse 1
Had the look of a drifter
Bounty for dead
Bounty for live

Chorus
Just lay your soul down
To the devil surrender

Instrumental Break

Verse 2
He was hardened by living
Always a stranger in town
Murder for hire

Chorus
Just lay your soul down
To the devil surrender

Instrumental Outro
"""


def test_directors_bible_creation_has_required_fields():
    storyboard = heuristic_storyboard(LAY_YOUR_SOUL_DOWN, 220)
    bible = create_directors_bible(storyboard)

    assert bible["story_arc"]
    assert bible["protagonist_description"]
    assert bible["era"]
    assert bible["locations"]
    assert bible["color_palette"]
    assert bible["recurring_motifs"]
    assert bible["positive_visual_cues"]
    assert bible["negative_visual_cues"]
    assert "drifter" in " ".join(bible["positive_visual_cues"]).lower()


def test_storyboard_targets_12_to_16_meaningful_sequences_for_full_song():
    storyboard = heuristic_storyboard(LAY_YOUR_SOUL_DOWN, 220)

    assert 12 <= len(storyboard["scenes"]) <= 16


def test_sequences_include_v06_source_and_overlay_metadata():
    scene = heuristic_storyboard(LAY_YOUR_SOUL_DOWN, 220)["scenes"][0]

    assert scene["scene_id"]
    assert scene["song_section"]
    assert scene["lyric_or_musical_moment"]
    assert scene["story_purpose"]
    assert scene["recommended_visual"]
    assert scene["preferred_source_type"] in {"stock_video", "generated_still", "either"}
    assert scene["text_overlay"]["position"] == "lower_third"
    assert "contains_protagonist" in scene


def test_generated_still_prompt_uses_bible_and_scene_context():
    storyboard = heuristic_storyboard(LAY_YOUR_SOUL_DOWN, 220)
    prompt = build_still_prompt(storyboard["scenes"][0], storyboard["directors_bible"])

    assert "weathered male" in prompt.lower() or "drifter" in prompt.lower()
    assert "negative cues" in prompt.lower()
    assert "no lip sync" in prompt.lower()
    assert "16:9" in prompt


def test_stock_to_still_fallback_when_stock_rejected():
    storyboard = heuristic_storyboard(LAY_YOUR_SOUL_DOWN, 220)
    scene = dict(storyboard["scenes"][0], preferred_source_type="either")

    def reject_all(scene_arg, candidates, project_context, continuity_summary):
        return {
            "evaluations": [
                {"id": item["id"], "fit_score": 5, "accept": False, "reason": "unrelated"}
                for item in candidates
            ],
            "best_id": None,
        }

    with TemporaryDirectory() as temp_dir:
        selected, context = select_hybrid_media_for_scene(
            scene,
            storyboard["directors_bible"],
            lambda query: [{"id": 1, "width": 1920, "height": 1080, "duration": 8, "preview_image": "x", "video_url": "x"}],
            SelectionContext(),
            Path(temp_dir),
            visual_reviewer=reject_all,
        )

    assert selected["source_type"] == "generated_still"
    assert selected["generated_image_path"]
    assert context.used_video_ids == frozenset()


def test_scene_state_preservation_replaces_only_one_scene():
    existing = {1: {"source_type": "stock_video"}, 2: {"source_type": "generated_still"}}
    updated = preserve_scene_media_state(existing, 2, {"source_type": "stock_video", "id": 99})

    assert updated[1] == {"source_type": "stock_video"}
    assert updated[2]["id"] == 99


def test_text_overlay_metadata_shape():
    overlay = text_overlay("Lay Your Soul Down", start=0, duration=4, position="top", size_preset="large")

    assert overlay == {
        "text": "Lay Your Soul Down",
        "start": 0,
        "duration": 4,
        "position": "top",
        "size_preset": "large",
    }


def test_generate_still_image_placeholder_without_api_key():
    storyboard = heuristic_storyboard(LAY_YOUR_SOUL_DOWN, 220)
    with TemporaryDirectory() as temp_dir:
        result = generate_still_image(
            storyboard["scenes"][0],
            storyboard["directors_bible"],
            Path(temp_dir) / "still.png",
            api_key="",
        )

        assert Path(result["generated_image_path"]).exists()
        assert result["generation_metadata"]["provider"] == "placeholder"


def test_default_image_model_value_and_upgrade_option():
    assert DEFAULT_IMAGE_MODEL == "gpt-image-2.5-flare"
    assert HERO_IMAGE_MODEL == "gpt-image-2.5-sunburst"


def test_generate_still_image_records_configurable_model_without_api_call():
    storyboard = heuristic_storyboard(LAY_YOUR_SOUL_DOWN, 220)

    with TemporaryDirectory() as temp_dir:
        result = generate_still_image(
            storyboard["scenes"][0],
            storyboard["directors_bible"],
            Path(temp_dir) / "still.png",
            api_key="",
            model=HERO_IMAGE_MODEL,
        )

    assert result["generation_metadata"]["requested_model"] == HERO_IMAGE_MODEL
    assert result["generation_metadata"]["provider"] == "placeholder"


def test_protagonist_reference_storage_shape_without_api_call():
    storyboard = heuristic_storyboard(LAY_YOUR_SOUL_DOWN, 220)

    with TemporaryDirectory() as temp_dir:
        reference = generate_protagonist_reference(
            storyboard["directors_bible"],
            Path(temp_dir) / "protagonist.png",
            api_key="",
        )

        assert Path(reference["reference_image_path"]).exists()
        assert reference["approved"] is False
        assert reference["generation_metadata"]["requested_model"] == DEFAULT_IMAGE_MODEL
        assert "weathered" in reference["image_prompt"].lower() or "drifter" in reference["image_prompt"].lower()


def test_protagonist_reference_is_used_for_protagonist_scene_prompt():
    storyboard = heuristic_storyboard(LAY_YOUR_SOUL_DOWN, 220)
    scene = {
        "scene_id": 7,
        "contains_protagonist": True,
        "story_purpose": "The weathered male drifter walks toward judgment.",
        "recommended_visual": "weathered male drifter on dusty road",
    }

    prompt = build_still_prompt(
        scene,
        storyboard["directors_bible"],
        protagonist_reference={"reference_image_path": "/tmp/protagonist.png"},
    )

    assert scene_includes_protagonist(scene)
    assert "use protagonist reference image" in prompt.lower()
    assert "facial appearance" in prompt.lower()
    assert "approximate age" in prompt.lower()
    assert "hair/facial hair" in prompt.lower()
    assert "dark duster/coat" in prompt.lower()


def test_protagonist_reference_is_not_required_for_landscape_scene():
    storyboard = heuristic_storyboard(LAY_YOUR_SOUL_DOWN, 220)
    scene = {
        "scene_id": 8,
        "contains_protagonist": False,
        "section": "Instrumental Break",
        "story_purpose": "Set the scale of the empty frontier.",
        "recommended_visual": "wide storm clouds over empty desert landscape",
    }

    prompt = build_still_prompt(
        scene,
        storyboard["directors_bible"],
        protagonist_reference={"reference_image_path": "/tmp/protagonist.png"},
    )

    assert not scene_includes_protagonist(scene)
    assert "use protagonist reference image" not in prompt.lower()
    assert "preserve the protagonist reference" not in prompt.lower()


def test_hybrid_generated_still_receives_reference_for_protagonist_scene():
    storyboard = heuristic_storyboard(LAY_YOUR_SOUL_DOWN, 220)
    scene = dict(
        storyboard["scenes"][0],
        preferred_source_type="generated_still",
        contains_protagonist=True,
        recommended_visual="weathered male drifter on dusty road",
    )

    with TemporaryDirectory() as temp_dir:
        selected, _ = select_hybrid_media_for_scene(
            scene,
            storyboard["directors_bible"],
            lambda query: [],
            SelectionContext(),
            Path(temp_dir),
            protagonist_reference={"reference_image_path": "/tmp/protagonist.png"},
        )

    assert selected["source_type"] == "generated_still"
    assert selected["generation_metadata"]["uses_protagonist_reference"] is True
    assert selected["generation_metadata"]["protagonist_reference_path"] == "/tmp/protagonist.png"


def test_hybrid_generated_still_does_not_require_reference_for_landscape_scene():
    storyboard = heuristic_storyboard(LAY_YOUR_SOUL_DOWN, 220)
    scene = {
        "scene_id": 12,
        "contains_protagonist": False,
        "section": "Instrumental Break",
        "story_purpose": "Set the scale of the empty frontier.",
        "recommended_visual": "wide storm clouds over empty desert landscape",
        "preferred_source_type": "generated_still",
    }

    with TemporaryDirectory() as temp_dir:
        selected, _ = select_hybrid_media_for_scene(
            scene,
            storyboard["directors_bible"],
            lambda query: [],
            SelectionContext(),
            Path(temp_dir),
            protagonist_reference={"reference_image_path": "/tmp/protagonist.png"},
        )

    assert selected["source_type"] == "generated_still"
    assert selected["generation_metadata"]["uses_protagonist_reference"] is False
    assert selected["generation_metadata"]["protagonist_reference_path"] == ""


def _image_response():
    return SimpleNamespace(
        data=[SimpleNamespace(b64_json=base64.b64encode(b"fake image bytes").decode("ascii"))]
    )


class FakeImages:
    def __init__(self, fail_edit=False, fail_generate=False):
        self.fail_edit = fail_edit
        self.fail_generate = fail_generate
        self.edit_calls = []
        self.generate_calls = []

    def edit(self, **kwargs):
        self.edit_calls.append(kwargs)
        if self.fail_edit:
            raise RuntimeError("reference edit failed")
        kwargs["image"].seek(0)
        self.image_bytes = kwargs["image"].read()
        return _image_response()

    def generate(self, **kwargs):
        self.generate_calls.append(kwargs)
        if self.fail_generate:
            error = RuntimeError("invalid image generation request")
            error.code = "bad_request"
            raise error
        return _image_response()


class FakeOpenAIClient:
    def __init__(self, fail_edit=False, fail_generate=False):
        self.images = FakeImages(fail_edit=fail_edit, fail_generate=fail_generate)


def test_reference_image_file_is_passed_to_openai_edit_call():
    storyboard = heuristic_storyboard(LAY_YOUR_SOUL_DOWN, 220)
    scene = {
        "scene_id": 22,
        "contains_protagonist": True,
        "story_purpose": "The weathered male drifter faces judgment.",
        "recommended_visual": "weathered male drifter on dusty road",
    }
    fake_client = FakeOpenAIClient()

    with TemporaryDirectory() as temp_dir:
        reference_path = Path(temp_dir) / "reference.png"
        reference_path.write_bytes(b"reference bytes")
        result = generate_still_image(
            scene,
            storyboard["directors_bible"],
            Path(temp_dir) / "scene.png",
            api_key="test-key",
            model=DEFAULT_IMAGE_MODEL,
            protagonist_reference={"reference_image_path": str(reference_path), "approved": True},
            openai_client=fake_client,
        )

    assert fake_client.images.image_bytes == b"reference bytes"
    assert fake_client.images.edit_calls
    assert "response_format" not in fake_client.images.edit_calls[0]
    assert not fake_client.images.generate_calls
    assert result["generation_metadata"]["generation_mode"] == "reference_edit"


def test_protagonist_reference_workflow_uses_reference_model():
    storyboard = heuristic_storyboard(LAY_YOUR_SOUL_DOWN, 220)
    scene = {
        "scene_id": 23,
        "contains_protagonist": True,
        "story_purpose": "The outlaw rides into the frontier town.",
        "recommended_visual": "rugged outlaw bounty hunter imagery",
    }
    fake_client = FakeOpenAIClient()

    with TemporaryDirectory() as temp_dir:
        reference_path = Path(temp_dir) / "reference.png"
        reference_path.write_bytes(b"reference bytes")
        result = generate_still_image(
            scene,
            storyboard["directors_bible"],
            Path(temp_dir) / "scene.png",
            api_key="test-key",
            model=DEFAULT_IMAGE_MODEL,
            protagonist_reference={"reference_image_path": str(reference_path), "approved": True},
            openai_client=fake_client,
        )

    edit_call = fake_client.images.edit_calls[0]
    assert edit_call["model"] == REFERENCE_IMAGE_MODEL
    assert "input_fidelity" not in edit_call
    assert result["generation_metadata"]["requested_image_model"] == DEFAULT_IMAGE_MODEL
    assert result["generation_metadata"]["actual_image_model"] == REFERENCE_IMAGE_MODEL
    assert result["generation_metadata"]["uses_protagonist_reference"] is True


def test_non_protagonist_scene_uses_text_generation_without_reference():
    storyboard = heuristic_storyboard(LAY_YOUR_SOUL_DOWN, 220)
    scene = {
        "scene_id": 24,
        "contains_protagonist": False,
        "section": "Instrumental Break",
        "story_purpose": "Set the scale of the empty frontier.",
        "recommended_visual": "wide storm clouds over empty desert landscape",
    }
    fake_client = FakeOpenAIClient()

    with TemporaryDirectory() as temp_dir:
        reference_path = Path(temp_dir) / "reference.png"
        reference_path.write_bytes(b"reference bytes")
        result = generate_still_image(
            scene,
            storyboard["directors_bible"],
            Path(temp_dir) / "scene.png",
            api_key="test-key",
            model=DEFAULT_IMAGE_MODEL,
            protagonist_reference={"reference_image_path": str(reference_path), "approved": True},
            openai_client=fake_client,
        )

    assert fake_client.images.generate_calls
    assert not fake_client.images.edit_calls
    assert fake_client.images.generate_calls[0]["model"] == DEFAULT_IMAGE_MODEL
    assert "response_format" not in fake_client.images.generate_calls[0]
    assert result["generation_metadata"]["generation_mode"] == "text_generation"
    assert result["generation_metadata"]["uses_protagonist_reference"] is False


def test_reference_edit_failure_returns_needs_attention_without_fallback():
    storyboard = heuristic_storyboard(LAY_YOUR_SOUL_DOWN, 220)
    scene = {
        "scene_id": 25,
        "contains_protagonist": True,
        "story_purpose": "The drifter stands alone at the graveyard.",
        "recommended_visual": "weathered male drifter in old church graveyard",
    }
    fake_client = FakeOpenAIClient(fail_edit=True)

    with TemporaryDirectory() as temp_dir:
        reference_path = Path(temp_dir) / "reference.png"
        reference_path.write_bytes(b"reference bytes")
        result = generate_still_image(
            scene,
            storyboard["directors_bible"],
            Path(temp_dir) / "scene.png",
            api_key="test-key",
            model=DEFAULT_IMAGE_MODEL,
            protagonist_reference={"reference_image_path": str(reference_path), "approved": True},
            openai_client=fake_client,
        )

    assert result["status"] == "Needs Attention"
    assert "reference edit failed" in result["generation_error"]
    assert fake_client.images.edit_calls
    assert not fake_client.images.generate_calls
    assert result["generation_metadata"]["generation_mode"] == "reference_edit"
    assert result["generated_image_path"] == ""


def test_generate_decodes_b64_json_without_response_format_argument():
    storyboard = heuristic_storyboard(LAY_YOUR_SOUL_DOWN, 220)
    scene = {
        "scene_id": 27,
        "contains_protagonist": False,
        "section": "Instrumental Break",
        "story_purpose": "Set the scale of the empty frontier.",
        "recommended_visual": "wide storm clouds over empty desert landscape",
    }
    fake_client = FakeOpenAIClient()

    with TemporaryDirectory() as temp_dir:
        output_path = Path(temp_dir) / "scene.png"
        result = generate_still_image(
            scene,
            storyboard["directors_bible"],
            output_path,
            api_key="test-key",
            model=DEFAULT_IMAGE_MODEL,
            openai_client=fake_client,
        )

        assert output_path.read_bytes() == b"fake image bytes"

    assert result["generated_image_path"] == str(output_path)
    assert "response_format" not in fake_client.images.generate_calls[0]


def test_ordinary_generation_bad_request_returns_needs_attention_without_crashing():
    storyboard = heuristic_storyboard(LAY_YOUR_SOUL_DOWN, 220)
    scene = {
        "scene_id": 28,
        "contains_protagonist": False,
        "section": "Instrumental Break",
        "story_purpose": "Set the scale of the empty frontier.",
        "recommended_visual": "wide storm clouds over empty desert landscape",
    }
    fake_client = FakeOpenAIClient(fail_generate=True)

    with TemporaryDirectory() as temp_dir:
        result = generate_still_image(
            scene,
            storyboard["directors_bible"],
            Path(temp_dir) / "scene.png",
            api_key="test-key",
            model=DEFAULT_IMAGE_MODEL,
            openai_client=fake_client,
        )

    assert result["status"] == "Needs Attention"
    assert result["generation_error_code"] == "bad_request"
    assert "invalid image generation request" in result["generation_error"]
    assert result["generation_metadata"]["generation_mode"] == "text_generation"
    assert result["generation_metadata"]["generation_error_code"] == "bad_request"
    assert result["generated_image_path"] == ""


def test_reference_generation_metadata_records_resulting_path_and_reference_identifier():
    storyboard = heuristic_storyboard(LAY_YOUR_SOUL_DOWN, 220)
    scene = {
        "scene_id": 26,
        "contains_protagonist": True,
        "story_purpose": "The bounty hunter turns toward the road.",
        "recommended_visual": "rugged bounty hunter on dusty road",
    }
    fake_client = FakeOpenAIClient()

    with TemporaryDirectory() as temp_dir:
        reference_path = Path(temp_dir) / "reference.png"
        output_path = Path(temp_dir) / "scene.png"
        reference_path.write_bytes(b"reference bytes")
        result = generate_still_image(
            scene,
            storyboard["directors_bible"],
            output_path,
            api_key="test-key",
            model=DEFAULT_IMAGE_MODEL,
            protagonist_reference={"reference_image_path": str(reference_path), "approved": True},
            openai_client=fake_client,
        )

        assert Path(result["generated_image_path"]).exists()

    metadata = result["generation_metadata"]
    assert metadata["requested_image_model"] == DEFAULT_IMAGE_MODEL
    assert metadata["actual_image_model"] == REFERENCE_IMAGE_MODEL
    assert metadata["generation_mode"] == "reference_edit"
    assert metadata["reference_image_identifier"] == str(reference_path)
    assert result["generated_image_path"] == str(output_path)


def test_contains_protagonist_true_uses_reference_edit():
    storyboard = heuristic_storyboard(LAY_YOUR_SOUL_DOWN, 220)
    scene = {
        "scene_id": 29,
        "contains_protagonist": True,
        "story_purpose": "He remembers the road.",
        "recommended_visual": "empty road at sunset",
    }
    fake_client = FakeOpenAIClient()

    with TemporaryDirectory() as temp_dir:
        reference_path = Path(temp_dir) / "reference.png"
        reference_path.write_bytes(b"reference bytes")
        generate_still_image(
            scene,
            storyboard["directors_bible"],
            Path(temp_dir) / "scene.png",
            api_key="test-key",
            protagonist_reference={"reference_image_path": str(reference_path), "approved": True},
            openai_client=fake_client,
        )

    assert fake_client.images.edit_calls
    assert not fake_client.images.generate_calls


def test_contains_protagonist_false_uses_flare_without_reference_even_with_character_words():
    storyboard = heuristic_storyboard(LAY_YOUR_SOUL_DOWN, 220)
    scene = {
        "scene_id": 30,
        "contains_protagonist": False,
        "story_purpose": "He is gone and his memory hangs over the scene.",
        "recommended_visual": "empty frontier road where a man once stood",
    }
    fake_client = FakeOpenAIClient()

    with TemporaryDirectory() as temp_dir:
        reference_path = Path(temp_dir) / "reference.png"
        reference_path.write_bytes(b"reference bytes")
        result = generate_still_image(
            scene,
            storyboard["directors_bible"],
            Path(temp_dir) / "scene.png",
            api_key="test-key",
            model=DEFAULT_IMAGE_MODEL,
            protagonist_reference={"reference_image_path": str(reference_path), "approved": True},
            openai_client=fake_client,
        )

    assert fake_client.images.generate_calls
    assert not fake_client.images.edit_calls
    assert fake_client.images.generate_calls[0]["model"] == DEFAULT_IMAGE_MODEL
    assert result["generation_metadata"]["uses_protagonist_reference"] is False


def test_pronouns_and_man_alone_do_not_trigger_protagonist_reference():
    scene = {
        "scene_id": 31,
        "story_purpose": "He loses what was his.",
        "lyric_excerpt": "He was hardened by living",
        "recommended_visual": "empty saloon doorway after a man left town",
    }

    assert not scene_includes_protagonist(scene)


def test_one_failed_reference_scene_does_not_mark_unrelated_scene_failed():
    storyboard = heuristic_storyboard(LAY_YOUR_SOUL_DOWN, 220)
    failing_client = FakeOpenAIClient(fail_edit=True)
    successful_client = FakeOpenAIClient()

    with TemporaryDirectory() as temp_dir:
        reference_path = Path(temp_dir) / "reference.png"
        reference_path.write_bytes(b"reference bytes")
        failed = generate_still_image(
            {
                "scene_id": 32,
                "contains_protagonist": True,
                "recommended_visual": "rugged outlaw in western graveyard",
            },
            storyboard["directors_bible"],
            Path(temp_dir) / "failed.png",
            api_key="test-key",
            protagonist_reference={"reference_image_path": str(reference_path), "approved": True},
            openai_client=failing_client,
        )
        succeeded = generate_still_image(
            {
                "scene_id": 33,
                "contains_protagonist": False,
                "recommended_visual": "storm clouds over empty desert",
            },
            storyboard["directors_bible"],
            Path(temp_dir) / "succeeded.png",
            api_key="test-key",
            protagonist_reference={"reference_image_path": str(reference_path), "approved": True},
            openai_client=successful_client,
        )

    assert failed["status"] == "Needs Attention"
    assert succeeded["status"] == "generated"
    assert successful_client.images.generate_calls
    assert not successful_client.images.edit_calls


def test_animate_still_image_invokes_ffmpeg_with_zoompan():
    with TemporaryDirectory() as temp_dir:
        image = Path(temp_dir) / "still.png"
        image.write_bytes(b"fake")
        output = Path(temp_dir) / "clip.mp4"

        def fake_check_call(cmd, stdout=None, stderr=None):
            assert "zoompan" in " ".join(cmd)
            output.write_bytes(b"fake video")

        with patch("renderer.subprocess.check_call", side_effect=fake_check_call):
            result = animate_still_image(
                image,
                2,
                "16:9",
                output,
                overlay={"text": "Title", "position": "top", "size_preset": "large"},
            )

        assert result == output
        assert output.exists()
