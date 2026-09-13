from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from renderer import render_selected_pexels_clips


def test_render_selected_pexels_clips_invokes_ffmpeg_for_each_scene():
    with TemporaryDirectory() as temp_dir:
        tmp_path = Path(temp_dir)
        audio = tmp_path / "song.mp3"
        audio.write_bytes(b"fake audio")
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"fake video")
        output = tmp_path / "draft.mp4"
        storyboard = {
            "scenes": [
                {
                    "scene": 1,
                    "start": 0,
                    "end": 2,
                }
            ]
        }

        def fake_check_call(cmd, stdout=None, stderr=None):
            if str(output) in cmd:
                output.write_bytes(b"fake final video")

        with patch("renderer.subprocess.check_call", side_effect=fake_check_call) as mocked:
            result = render_selected_pexels_clips(
                audio,
                storyboard,
                {1: clip},
                "16:9",
                output,
            )

        assert result == output
        assert output.exists()
        assert mocked.call_count == 3


def test_render_selected_pexels_clips_requires_all_scene_footage():
    with TemporaryDirectory() as temp_dir:
        tmp_path = Path(temp_dir)
        audio = tmp_path / "song.mp3"
        audio.write_bytes(b"fake audio")

        try:
            render_selected_pexels_clips(
                audio,
                {"scenes": [{"scene": 1, "start": 0, "end": 1}]},
                {},
                "16:9",
                tmp_path / "draft.mp4",
            )
        except ValueError as exc:
            assert "No selected clip for scene 1" in str(exc)
        else:
            raise AssertionError("Expected missing footage to raise ValueError")
