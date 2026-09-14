from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import streamlit as st

from core import (
    ai_storyboard,
    extract_lyrics,
    ffprobe_duration,
    heuristic_storyboard,
    normalize_storyboard,
    save_uploaded_file,
)
from directors_bible import normalize_bible_lists
from footage_selector import (
    SelectionContext,
    automatic_select_for_scene,
    create_openai_visual_reviewer,
    fallback_queries,
    rank_candidates,
    update_selection_context,
)
from hybrid_media import preserve_scene_media_state, select_hybrid_media_for_scene, text_overlay
from image_generation import DEFAULT_IMAGE_MODEL, HERO_IMAGE_MODEL, generate_protagonist_reference, generate_still_image
from project_archive import ProjectArchiveError, create_project_archive, load_project_archive
from project_state import clear_project_state, ensure_project_state, reset_footage_state
from renderer import render_animatic, render_hybrid_media, render_selected_pexels_clips
from video_provider import (
    PexelsAuthError,
    PexelsError,
    download_clip,
    pexels_search_videos,
)


st.set_page_config(
    page_title="SongStory Studio v0.6",
    page_icon="🎬",
    layout="wide",
)

st.markdown(
    """
<style>
.block-container {max-width: 1180px; padding-top: 2rem;}
.hero {
    padding: 1.2rem 1.4rem;
    border: 1px solid rgba(128,128,128,.25);
    border-radius: 8px;
    margin-bottom: 1rem;
}
.hero h1 {margin-bottom: .2rem;}
.subtle {opacity: .72;}
.scene-card {
    border: 1px solid rgba(128,128,128,.25);
    border-radius: 8px;
    padding: 1rem;
    margin: .6rem 0;
}
</style>
""",
    unsafe_allow_html=True,
)

ensure_project_state(st.session_state)

STYLE_OPTIONS = [
    "Cinematic realistic",
    "Southern Gothic Western",
    "Southern rock / Americana",
    "Country storytelling",
    "Dreamlike symbolic",
    "Vintage film",
    "Dark dramatic",
    "Animated",
    "Custom",
]

INTERPRETATION_OPTIONS = [
    "Combination of story + symbolism",
    "Mostly literal story",
    "Mostly symbolic",
]

ASPECT_RATIO_OPTIONS = ["16:9", "9:16", "1:1"]


def get_secret(name: str) -> str:
    try:
        return st.secrets.get(name, os.getenv(name, ""))
    except Exception:
        return os.getenv(name, "")


def create_project(audio_file) -> None:
    project_dir = Path(tempfile.mkdtemp(prefix="songstory_"))
    audio_path = save_uploaded_file(audio_file, project_dir)
    duration = ffprobe_duration(audio_path)

    st.session_state.project_dir = str(project_dir)
    st.session_state.audio_path = str(audio_path)
    st.session_state.audio_duration = duration
    st.session_state.song_filename = Path(audio_file.name).name


def restore_project(package: bytes) -> None:
    project_dir = Path(tempfile.mkdtemp(prefix="songstory_loaded_"))
    restored = load_project_archive(package, project_dir)
    clear_project_state(st.session_state)
    ensure_project_state(st.session_state)
    st.session_state.update(restored)
    st.session_state.lyrics_input = restored.get("lyrics", "")
    st.session_state.video_style_select = (
        restored.get("video_style")
        if restored.get("video_style") in STYLE_OPTIONS
        else "Custom"
    )
    st.session_state.video_style_custom = restored.get("video_style", "")
    st.session_state.interpretation_select = restored.get("interpretation", INTERPRETATION_OPTIONS[0])
    st.session_state.aspect_ratio_select = restored.get("aspect_ratio", "16:9")
    st.session_state.analysis_model_input = restored.get("analysis_model", "")
    st.session_state.image_model_input = restored.get("image_model", DEFAULT_IMAGE_MODEL)


def clear_project_and_widgets() -> None:
    clear_project_state(st.session_state)
    for key in [
        "lyrics_input",
        "video_style_select",
        "video_style_custom",
        "interpretation_select",
        "aspect_ratio_select",
        "analysis_model_input",
        "image_model_input",
        "storyboard_json_import",
        "songstory_project_upload",
    ]:
        st.session_state.pop(key, None)
    ensure_project_state(st.session_state)


def project_download_name() -> str:
    song_name = Path(st.session_state.get("song_filename") or "songstory_project").stem
    safe = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in song_name)
    return f"{safe or 'songstory_project'}.songstory"


def clear_outputs_for_new_storyboard() -> None:
    reset_footage_state(st.session_state)


def scene_duration(scene: dict[str, Any]) -> float:
    return max(
        0.2,
        float(scene.get("duration") or 0)
        or float(scene.get("end", 0)) - float(scene.get("start", 0)),
    )


def search_scene(query: str, api_key: str, aspect_ratio: str) -> list[dict[str, Any]]:
    return pexels_search_videos(
        query,
        api_key,
        per_page=8,
        aspect_ratio=aspect_ratio,
    )


def build_credits(selections: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    credits = []

    for scene_number in sorted(selections):
        clip = selections[scene_number]
        credits.append(
            {
                "scene": scene_number,
                "video_id": clip.get("id"),
                "creator": clip.get("creator"),
                "creator_url": clip.get("creator_url"),
                "page_url": clip.get("page_url"),
            }
        )

    return credits


def run_automatic_draft(
    api_key: str,
    aspect_ratio: str,
    openai_api_key: str = "",
    openai_model: str = "gpt-5.6-luna",
    quality_mode: str = "ai_reviewed",
) -> None:
    storyboard = st.session_state.get("storyboard")
    project_dir = st.session_state.get("project_dir")
    audio_path = st.session_state.get("audio_path")

    if not storyboard:
        st.error("Create or load a storyboard before building the draft video.")
        return

    if not audio_path or not Path(audio_path).exists():
        st.error("Upload the original song before building the draft video.")
        return

    if not api_key:
        st.error("Add PEXELS_API_KEY in Streamlit Secrets or your environment.")
        return

    if quality_mode == "ai_reviewed" and not openai_api_key:
        st.error(
            "High-quality automatic footage selection requires an OpenAI API key "
            "so SongStory Studio can visually evaluate Pexels clips before using them."
        )
        return

    scenes = storyboard.get("scenes", [])

    if not scenes:
        st.error("The storyboard has no scenes to render.")
        return

    selected: dict[int, dict[str, Any]] = {}
    downloaded: dict[int, str] = {}
    candidates_by_scene: dict[int, list[dict[str, Any]]] = {}
    statuses: dict[int, dict[str, Any]] = {}
    context = SelectionContext(aspect_ratio=aspect_ratio)
    visual_reviewer = (
        create_openai_visual_reviewer(openai_api_key, openai_model)
        if quality_mode == "ai_reviewed"
        else None
    )
    visual_review_cache = st.session_state.setdefault("visual_review_cache", {})
    clips_dir = Path(project_dir) / "pexels_clips"
    progress = st.progress(0)
    status = st.empty()
    continuity_notes: list[str] = []

    for index, scene in enumerate(scenes, start=1):
        scene_number = int(scene.get("scene", index))
        status.write(f"Searching footage for scene {scene_number} of {len(scenes)}...")

        try:
            result = automatic_select_for_scene(
                scene,
                lambda query: search_scene(query, api_key, aspect_ratio),
                context,
                visual_reviewer=visual_reviewer,
                require_visual_review=quality_mode == "ai_reviewed",
                project_context=storyboard,
                continuity_summary="; ".join(continuity_notes[-4:]),
                visual_review_cache=visual_review_cache,
            )
        except PexelsAuthError as exc:
            st.error(str(exc))
            return
        except PexelsError as exc:
            statuses[scene_number] = {
                "status": "needs_attention",
                "message": str(exc),
            }
            continue

        candidates_by_scene[scene_number] = result.get("candidates", [])
        statuses[scene_number] = {
            "status": result["status"],
            "query": result.get("query"),
            "message": result.get("message", ""),
            "visual_threshold": result.get("visual_threshold"),
        }

        if result["selected"]:
            choice = result["selected"]
            status.write(f"Downloading footage for scene {scene_number}...")
            output_path = clips_dir / f"scene_{scene_number:03d}_{choice['id']}.mp4"

            try:
                download_clip(choice["video_url"], output_path)
            except PexelsError as exc:
                statuses[scene_number] = {
                    "status": "needs_attention",
                    "query": result.get("query"),
                    "message": str(exc),
                }
                continue

            selected[scene_number] = choice
            downloaded[scene_number] = str(output_path)
            context = update_selection_context(context, choice)
            continuity_notes.append(
                (
                    f"Scene {scene_number}: {choice.get('source_query') or choice.get('query')} "
                    f"fit {choice.get('visual_fit_score', 'metadata-only')} "
                    f"{choice.get('visual_review_reason', '')}"
                ).strip()
            )

        progress.progress(index / len(scenes))

    st.session_state.selected_pexels_clips = selected
    st.session_state.downloaded_clip_paths = downloaded
    st.session_state.scene_candidates = candidates_by_scene
    st.session_state.scene_selection_status = statuses
    st.session_state.footage_credits = build_credits(selected)

    missing = [
        int(scene.get("scene", index))
        for index, scene in enumerate(scenes, start=1)
        if int(scene.get("scene", index)) not in downloaded
    ]

    if missing:
        st.warning(
            "Some scenes still need footage before rendering: "
            + ", ".join(str(number) for number in missing)
        )
        return

    status.write("Rendering complete draft video with the original song...")
    out = Path(project_dir) / "songstory_automatic_draft.mp4"

    try:
        render_selected_pexels_clips(
            audio_path,
            storyboard,
            downloaded,
            aspect_ratio,
            out,
        )
    except Exception as exc:
        st.error(f"Render failed: {exc}")
        st.session_state.render_status = "failed"
        return

    st.session_state.final_video_path = str(out)
    st.session_state.render_status = "complete"
    status.write("Automatic draft video complete.")
    st.success("Your first-draft music video is ready for review.")


def run_hybrid_story_film(
    pexels_key: str,
    aspect_ratio: str,
    openai_api_key: str = "",
    openai_model: str = "gpt-5.6-luna",
    image_model: str = DEFAULT_IMAGE_MODEL,
) -> None:
    storyboard = st.session_state.get("storyboard")
    project_dir = st.session_state.get("project_dir")
    audio_path = st.session_state.get("audio_path")

    if not storyboard:
        st.error("Create or load a storyboard before building the hybrid film.")
        return
    if not audio_path or not Path(audio_path).exists():
        st.error("Upload the original song before building the hybrid film.")
        return
    if not openai_api_key:
        st.error("Hybrid story-film mode requires OPENAI_API_KEY for visual review and still generation.")
        return

    bible = normalize_bible_lists(storyboard.get("directors_bible", {}))
    visual_reviewer = create_openai_visual_reviewer(openai_api_key, openai_model)
    protagonist_reference = (
        st.session_state.get("protagonist_reference")
        if st.session_state.get("protagonist_reference_approved")
        else None
    )
    context = SelectionContext(aspect_ratio=aspect_ratio)
    media_by_scene: dict[int, dict[str, Any]] = {}
    statuses: dict[int, dict[str, Any]] = {}
    continuity_notes: list[str] = []
    visual_review_cache = st.session_state.setdefault("visual_review_cache", {})
    progress = st.progress(0)
    status = st.empty()

    scenes = storyboard.get("scenes", [])
    stills_dir = Path(project_dir) / "generated_stills"
    clips_dir = Path(project_dir) / "pexels_clips"

    for index, scene in enumerate(scenes, start=1):
        scene_id = int(scene.get("scene_id") or scene.get("scene", index))
        status.write(f"Selecting media for sequence {scene_id} of {len(scenes)}...")

        selected, context = select_hybrid_media_for_scene(
            scene,
            bible,
            lambda query: search_scene(query, pexels_key, aspect_ratio) if pexels_key else [],
            context,
            stills_dir,
            visual_reviewer=visual_reviewer,
            openai_image_key=openai_api_key,
            image_model=image_model,
            protagonist_reference=protagonist_reference,
            visual_review_cache=visual_review_cache,
            continuity_summary="; ".join(continuity_notes[-4:]),
        )

        if selected.get("source_type") == "stock_video":
            output_path = clips_dir / f"scene_{scene_id:03d}_{selected['id']}.mp4"
            try:
                download_clip(selected["video_url"], output_path)
                selected["downloaded_path"] = str(output_path)
            except PexelsError as exc:
                selected = generate_still_image(
                    scene,
                    bible,
                    stills_dir / f"scene_{scene_id:03d}.png",
                    api_key=openai_api_key,
                    model=image_model,
                    protagonist_reference=protagonist_reference,
                )
                selected["source_type"] = "generated_still"
                selected["stock_fallback_reason"] = str(exc)

        media_by_scene = preserve_scene_media_state(media_by_scene, scene_id, selected)
        statuses[scene_id] = {
            "status": selected.get("status") or "selected",
            "source_type": selected.get("source_type"),
            "query": selected.get("source_query") or selected.get("query", ""),
            "reason": (
                selected.get("generation_error")
                or selected.get("visual_review_reason")
                or selected.get("stock_fallback_reason", "")
            ),
        }
        continuity_notes.append(
            f"Sequence {scene_id}: {selected.get('source_type')} {statuses[scene_id]['reason']}"
        )
        progress.progress(index / max(len(scenes), 1))

    st.session_state.hybrid_scene_media = media_by_scene
    st.session_state.scene_selection_status = statuses

    needs_attention = {
        scene_id: status_info
        for scene_id, status_info in statuses.items()
        if status_info.get("status") == "Needs Attention"
    }
    if needs_attention:
        status.empty()
        failed_sequences = ", ".join(str(scene_id) for scene_id in sorted(needs_attention))
        first_reason = next(iter(needs_attention.values())).get("reason", "")
        st.error(
            f"Hybrid build stopped because sequence {failed_sequences} needs attention. "
            f"{first_reason}"
        )
        return

    out = Path(project_dir) / "songstory_hybrid_story_film.mp4"
    status.write("Rendering hybrid story film with the original song...")
    try:
        render_hybrid_media(audio_path, storyboard, media_by_scene, aspect_ratio, out)
    except Exception as exc:
        st.error(f"Hybrid render failed: {exc}")
        return

    st.session_state.final_video_path = str(out)
    st.session_state.render_status = "complete"
    st.success("Hybrid story-film draft is ready for review.")


def replace_scene_clip(
    scene: dict[str, Any],
    clip: dict[str, Any],
    aspect_ratio: str,
) -> None:
    project_dir = Path(st.session_state.project_dir)
    scene_number = int(scene["scene"])
    output_path = project_dir / "pexels_clips" / f"scene_{scene_number:03d}_{clip['id']}.mp4"

    with st.spinner(f"Downloading replacement for scene {scene_number}..."):
        download_clip(clip["video_url"], output_path)

    selected = dict(st.session_state.get("selected_pexels_clips", {}))
    downloaded = dict(st.session_state.get("downloaded_clip_paths", {}))
    selected[scene_number] = clip
    downloaded[scene_number] = str(output_path)
    st.session_state.selected_pexels_clips = selected
    st.session_state.downloaded_clip_paths = downloaded
    st.session_state.footage_credits = build_credits(selected)

    out = project_dir / "songstory_automatic_draft.mp4"
    with st.spinner("Re-rendering the draft with this replacement..."):
        render_selected_pexels_clips(
            st.session_state.audio_path,
            st.session_state.storyboard,
            downloaded,
            aspect_ratio,
            out,
        )

    st.session_state.final_video_path = str(out)
    st.session_state.render_status = "complete"


st.markdown(
    """
<div class="hero">
<h1>🎬 SongStory Studio v0.6</h1>
<p>Upload a song and lyrics, build a Director's Bible, then create a hybrid cinematic story film from reviewed stock footage and generated stills.</p>
<p class="subtle">Workflow: understand story → director's bible → 12–16 sequences → stock when good → generated still when stock misses → animated film draft.</p>
</div>
""",
    unsafe_allow_html=True,
)

st.subheader("Project")
project_col1, project_col2, project_col3 = st.columns([1, 2, 2])

with project_col1:
    if st.button("New Project", use_container_width=True):
        clear_project_and_widgets()
        st.rerun()

with project_col2:
    project_upload = st.file_uploader(
        "Load Existing SongStory Project",
        type=["songstory", "zip"],
        key="songstory_project_upload",
    )
    if st.button(
        "Load Selected Project",
        disabled=project_upload is None,
        use_container_width=True,
    ):
        try:
            restore_project(project_upload.getvalue())
            st.success("SongStory project loaded.")
            st.rerun()
        except ProjectArchiveError as exc:
            st.error(str(exc))

with project_col3:
    try:
        project_package = create_project_archive(st.session_state)
        st.download_button(
            "Save SongStory Project",
            data=project_package,
            file_name=project_download_name(),
            mime="application/zip",
            use_container_width=True,
        )
    except ProjectArchiveError as exc:
        st.error(str(exc))

with st.sidebar:
    st.header("Creative Direction")

    saved_style = st.session_state.get("video_style", "Cinematic realistic")
    style_index = STYLE_OPTIONS.index(saved_style) if saved_style in STYLE_OPTIONS else STYLE_OPTIONS.index("Custom")
    style = st.selectbox(
        "Video style",
        STYLE_OPTIONS,
        index=style_index,
        key="video_style_select",
    )

    if style == "Custom":
        style = st.text_input(
            "Describe your style",
            saved_style if saved_style not in STYLE_OPTIONS else "Cinematic, grounded, emotionally authentic",
            key="video_style_custom",
        )
    st.session_state.video_style = style

    interpretation = st.radio(
        "Interpretation",
        INTERPRETATION_OPTIONS,
        index=(
            INTERPRETATION_OPTIONS.index(st.session_state.get("interpretation"))
            if st.session_state.get("interpretation") in INTERPRETATION_OPTIONS
            else 0
        ),
        key="interpretation_select",
    )
    st.session_state.interpretation = interpretation

    aspect_ratio = st.selectbox(
        "Output format",
        ASPECT_RATIO_OPTIONS,
        index=ASPECT_RATIO_OPTIONS.index(st.session_state.get("aspect_ratio", "16:9")),
        key="aspect_ratio_select",
    )
    st.session_state.aspect_ratio = aspect_ratio

    st.divider()

    openai_key = st.text_input(
        "Optional OpenAI API key",
        value=get_secret("OPENAI_API_KEY"),
        type="password",
    )

    model = st.text_input(
        "Analysis model",
        value=st.session_state.get("analysis_model") or os.getenv("OPENAI_MODEL", "gpt-5.6-luna"),
        key="analysis_model_input",
    )
    st.session_state.analysis_model = model

    image_model = st.text_input(
        "Image model",
        value=st.session_state.get("image_model") or os.getenv("OPENAI_IMAGE_MODEL", DEFAULT_IMAGE_MODEL),
        help=f"Default still/reference model. Use {HERO_IMAGE_MODEL} later for important hero scenes.",
        key="image_model_input",
    )
    st.session_state.image_model = image_model

    pexels_key = get_secret("PEXELS_API_KEY")

    if pexels_key:
        st.success("Pexels API key detected.")
    else:
        st.warning("PEXELS_API_KEY is required for automatic footage.")

    st.divider()

    if st.button("Start a new song", use_container_width=True):
        clear_project_state(st.session_state)
        ensure_project_state(st.session_state)
        st.rerun()


st.subheader("1. Upload Song")

audio = st.file_uploader(
    "Complete original song",
    type=["mp3", "wav", "m4a", "aac", "flac"],
    help="The completed draft uses this file as the final audio track.",
)

if audio:
    if (
        not st.session_state.get("audio_path")
        or st.session_state.get("song_filename") != Path(audio.name).name
    ):
        create_project(audio)
    st.audio(audio.getvalue())
elif st.session_state.get("audio_path") and Path(st.session_state.audio_path).exists():
    st.caption(f"Loaded song: {st.session_state.get('song_filename') or Path(st.session_state.audio_path).name}")
    st.audio(st.session_state.audio_path)


st.subheader("2. Add Lyrics")

lyric_mode = st.radio(
    "Lyrics source",
    ["Paste lyrics", "Upload lyrics file"],
    horizontal=True,
)

lyrics = ""

if lyric_mode == "Paste lyrics":
    lyrics = st.text_area(
        "Lyrics",
        value=st.session_state.get("lyrics", ""),
        height=250,
        placeholder="Paste complete lyrics with section labels like Verse 1, Chorus, Bridge, Guitar Solo...",
        key="lyrics_input",
    )
    st.session_state.lyrics = lyrics
else:
    lyric_file = st.file_uploader(
        "Lyrics file",
        type=["txt", "md", "docx"],
    )

    if lyric_file:
        lyrics = extract_lyrics(lyric_file)
        st.session_state.lyrics = lyrics
        st.text_area(
            "Extracted lyrics",
            value=lyrics,
            height=250,
            disabled=True,
        )


left, right = st.columns([1, 1])

with left:
    analyze_clicked = st.button(
        "Analyze Song and Create Storyboard",
        type="primary",
        use_container_width=True,
    )

with right:
    imported_storyboard_file = st.file_uploader(
        "Optional storyboard JSON",
        type=["json"],
        key="storyboard_json_import",
    )
    load_imported = st.button(
        "Load Storyboard JSON",
        disabled=imported_storyboard_file is None,
        use_container_width=True,
    )


if analyze_clicked:
    lyrics = st.session_state.get("lyrics", "")
    if not st.session_state.get("audio_path"):
        st.error("Please upload a song first.")
    elif not lyrics.strip():
        st.error("Please paste or upload lyrics first.")
    else:
        with st.spinner("Analyzing the complete song as one coherent film..."):
            if openai_key:
                try:
                    storyboard = ai_storyboard(
                        lyrics,
                        st.session_state.audio_duration,
                        style,
                        interpretation,
                        aspect_ratio,
                        openai_key,
                        model,
                    )
                except Exception as exc:
                    st.warning(
                        f"AI analysis could not complete ({exc}). "
                        "I created a local storyboard draft instead."
                    )
                    storyboard = heuristic_storyboard(
                        lyrics,
                        st.session_state.audio_duration,
                    )
            else:
                storyboard = heuristic_storyboard(
                    lyrics,
                    st.session_state.audio_duration,
                )

        st.session_state.storyboard = storyboard
        clear_outputs_for_new_storyboard()
        st.success("Storyboard created. Review it, then build the automatic draft.")


if load_imported:
    if not st.session_state.get("audio_path"):
        st.error("Upload the matching song before loading its storyboard.")
    else:
        try:
            imported = json.loads(imported_storyboard_file.getvalue().decode("utf-8"))
            create_project(audio)
            st.session_state.storyboard = normalize_storyboard(
                imported,
                st.session_state.audio_duration,
            )
            clear_outputs_for_new_storyboard()
            st.success("Storyboard loaded.")
        except Exception as exc:
            st.error(f"The storyboard JSON could not be loaded: {exc}")


storyboard = st.session_state.get("storyboard")

if storyboard:
    st.divider()
    st.subheader("3. Creative Treatment")

    col_a, col_b = st.columns(2)

    with col_a:
        st.text_area("Concept", value=storyboard.get("concept", ""), height=110, key="concept_edit")
        st.text_area("Story arc", value=storyboard.get("story_arc", ""), height=100, key="arc_edit")

    with col_b:
        st.text_area("Mood", value=storyboard.get("mood", ""), height=110, key="mood_edit")
        st.text_area(
            "Visual continuity",
            value=storyboard.get("visual_style", ""),
            height=100,
            key="style_edit",
        )

    st.caption(
        f"Storyboard source: {storyboard.get('source', 'unknown')} | "
        f"Song duration: {st.session_state.get('audio_duration', 0):.1f}s"
    )

    st.subheader("4. Director's Bible")
    bible = normalize_bible_lists(storyboard.get("directors_bible", {}))
    bible_a, bible_b = st.columns(2)
    with bible_a:
        bible["story_arc"] = st.text_area("Story arc", value=bible.get("story_arc", ""), height=90, key="bible_story_arc")
        bible["protagonist_description"] = st.text_area("Protagonist", value=bible.get("protagonist_description", ""), height=80, key="bible_protagonist")
        bible["era"] = st.text_input("Era", value=bible.get("era", ""), key="bible_era")
        bible["locations"] = st.text_area("Locations", value=", ".join(bible.get("locations", [])), height=70, key="bible_locations")
        bible["color_palette"] = st.text_area("Color palette", value=", ".join(bible.get("color_palette", [])), height=70, key="bible_palette")
    with bible_b:
        bible["recurring_motifs"] = st.text_area("Recurring motifs", value=", ".join(bible.get("recurring_motifs", [])), height=70, key="bible_motifs")
        bible["positive_visual_cues"] = st.text_area("Positive visual cues", value=", ".join(bible.get("positive_visual_cues", [])), height=90, key="bible_positive")
        bible["negative_visual_cues"] = st.text_area("Negative visual cues", value=", ".join(bible.get("negative_visual_cues", [])), height=90, key="bible_negative")
        bible["tone_progression"] = st.text_area("Tone progression", value=bible.get("tone_progression", ""), height=70, key="bible_tone")
        bible["ending_mood"] = st.text_input("Ending mood", value=bible.get("ending_mood", ""), key="bible_ending")

    if st.button("Apply Director's Bible"):
        storyboard["directors_bible"] = normalize_bible_lists(
            {
                "story_arc": st.session_state.bible_story_arc,
                "protagonist_description": st.session_state.bible_protagonist,
                "era": st.session_state.bible_era,
                "locations": st.session_state.bible_locations,
                "color_palette": st.session_state.bible_palette,
                "recurring_motifs": st.session_state.bible_motifs,
                "positive_visual_cues": st.session_state.bible_positive,
                "negative_visual_cues": st.session_state.bible_negative,
                "tone_progression": st.session_state.bible_tone,
                "ending_mood": st.session_state.bible_ending,
            }
        )
        st.session_state.storyboard = storyboard
        clear_outputs_for_new_storyboard()
        st.success("Director's Bible updated.")

    protagonist_reference = st.session_state.get("protagonist_reference")
    reference_path = ""
    if protagonist_reference:
        reference_path = protagonist_reference.get("reference_image_path") or protagonist_reference.get("generated_image_path") or ""

    st.caption("Protagonist visual reference")
    if reference_path and Path(reference_path).exists():
        st.image(reference_path, caption="Current protagonist reference", width=320)
        if st.session_state.get("protagonist_reference_approved"):
            st.success("Approved character reference will be used for protagonist scenes.")
        else:
            st.info("Approve this character to use it for protagonist continuity.")
    else:
        st.info("Generate one reusable character reference before building generated protagonist scenes.")

    ref_col1, ref_col2, ref_col3 = st.columns(3)
    with ref_col1:
        if st.button("Generate Protagonist Reference", disabled=bool(reference_path)):
            try:
                reference = generate_protagonist_reference(
                    normalize_bible_lists(storyboard.get("directors_bible", {})),
                    Path(st.session_state.project_dir) / "generated_stills" / "protagonist_reference.png",
                    api_key=openai_key,
                    model=image_model,
                )
                st.session_state.protagonist_reference = reference
                st.session_state.protagonist_reference_approved = False
                st.success("Protagonist reference generated.")
                st.rerun()
            except Exception as exc:
                st.error(f"Protagonist reference generation failed: {exc}")
    with ref_col2:
        if st.button("Regenerate Protagonist Reference"):
            try:
                reference = generate_protagonist_reference(
                    normalize_bible_lists(storyboard.get("directors_bible", {})),
                    Path(st.session_state.project_dir) / "generated_stills" / "protagonist_reference.png",
                    api_key=openai_key,
                    model=image_model,
                )
                st.session_state.protagonist_reference = reference
                st.session_state.protagonist_reference_approved = False
                st.success("Protagonist reference regenerated.")
                st.rerun()
            except Exception as exc:
                st.error(f"Protagonist reference regeneration failed: {exc}")
    with ref_col3:
        if st.button("Approve/Use This Character", disabled=not bool(reference_path)):
            reference = dict(st.session_state.get("protagonist_reference") or {})
            reference["approved"] = True
            st.session_state.protagonist_reference = reference
            st.session_state.protagonist_reference_approved = True
            st.success("This character will be used for protagonist scenes.")
            st.rerun()

    st.subheader("5. Story Sequences")
    scenes = storyboard.get("scenes", [])

    edited = st.data_editor(
        scenes,
        hide_index=True,
        use_container_width=True,
        num_rows="dynamic",
        column_config={
            "scene": st.column_config.NumberColumn("Scene", width="small"),
            "scene_id": st.column_config.NumberColumn("Sequence ID", width="small"),
            "section": st.column_config.TextColumn("Song section", width="medium"),
            "song_section": st.column_config.TextColumn("Song section v0.6", width="medium"),
            "start": st.column_config.NumberColumn("Start", format="%.2f"),
            "end": st.column_config.NumberColumn("End", format="%.2f"),
            "duration": st.column_config.NumberColumn("Sec", format="%.2f", disabled=True),
            "lyric_excerpt": st.column_config.TextColumn("Lyric / musical moment", width="medium"),
            "lyric_or_musical_moment": st.column_config.TextColumn("Lyric/musical moment v0.6", width="medium"),
            "story_purpose": st.column_config.TextColumn("Story purpose", width="large"),
            "visual": st.column_config.TextColumn("Creative visual concept", width="large"),
            "recommended_visual": st.column_config.TextColumn("Recommended visual", width="large"),
            "preferred_source_type": st.column_config.SelectboxColumn(
                "Preferred source",
                options=["stock_video", "generated_still", "either"],
                width="medium",
            ),
            "contains_protagonist": st.column_config.CheckboxColumn("Contains protagonist", width="small"),
            "pexels_query": st.column_config.TextColumn("Short Pexels query", width="medium"),
            "camera": st.column_config.TextColumn("Camera", width="medium"),
            "excluded": st.column_config.CheckboxColumn("Exclude", width="small"),
        },
        key="scene_editor",
    )

    if st.button("Apply Storyboard Edits"):
        storyboard["concept"] = st.session_state.concept_edit
        storyboard["story_arc"] = st.session_state.arc_edit
        storyboard["mood"] = st.session_state.mood_edit
        storyboard["visual_style"] = st.session_state.style_edit
        storyboard["scenes"] = edited
        st.session_state.storyboard = normalize_storyboard(
            storyboard,
            st.session_state.get("audio_duration", 0),
        )
        clear_outputs_for_new_storyboard()
        st.success("Storyboard updated. Automatic footage selections were cleared.")

    export_json = json.dumps(st.session_state.storyboard, indent=2).encode("utf-8")
    st.download_button(
        "Download Storyboard JSON",
        export_json,
        file_name="songstory_storyboard.json",
        mime="application/json",
    )

    with st.expander("Optional storyboard-card animatic"):
        st.write(
            "This creates a timing-only MP4 with storyboard cards and the original song."
        )

        if st.button("Render Animatic Preview"):
            out = Path(st.session_state.project_dir) / "songstory_animatic.mp4"

            with st.spinner("Rendering storyboard timing with the original song..."):
                try:
                    render_animatic(
                        st.session_state.audio_path,
                        st.session_state.storyboard,
                        aspect_ratio,
                        out,
                    )
                    st.session_state.animatic_path = str(out)
                except Exception as exc:
                    st.error(f"Animatic render failed: {exc}")

        if st.session_state.get("animatic_path") and Path(st.session_state.animatic_path).exists():
            st.video(st.session_state.animatic_path)
            st.download_button(
                "Download Animatic MP4",
                Path(st.session_state.animatic_path).read_bytes(),
                file_name="songstory_animatic.mp4",
                mime="video/mp4",
            )

    st.divider()
    st.subheader("5. Build Automatic Draft Video")

    st.write(
        "Recommended mode searches Pexels, shortlists metadata-ranked clips, then uses "
        "OpenAI visual review of preview images before downloading only accepted footage."
    )

    if st.button(
        "Build AI-Reviewed Automatic Draft",
        type="primary",
        use_container_width=True,
        disabled=not bool(openai_key),
    ):
        run_automatic_draft(
            pexels_key,
            aspect_ratio,
            openai_key,
            model,
            quality_mode="ai_reviewed",
        )

    if not openai_key:
        st.warning(
            "High-quality automatic footage selection requires an OpenAI API key so "
            "SongStory Studio can visually evaluate Pexels clips before using them."
        )

    with st.expander("Basic metadata-only draft"):
        st.warning(
            "Basic mode uses Pexels metadata only and may choose visually unrelated footage."
        )
        if st.button("Build Basic Automatic Draft", use_container_width=True):
            run_automatic_draft(
                pexels_key,
                aspect_ratio,
                openai_key,
                model,
                quality_mode="basic",
            )

    if st.session_state.get("final_video_path") and Path(st.session_state.final_video_path).exists():
        st.success("Automatic draft available.")
        st.video(st.session_state.final_video_path)
        st.download_button(
            "Download Automatic Draft MP4",
            Path(st.session_state.final_video_path).read_bytes(),
            file_name="songstory_automatic_draft.mp4",
            mime="video/mp4",
        )

    if st.session_state.get("footage_credits"):
        with st.expander("Footage credits"):
            for credit in st.session_state.footage_credits:
                line = f"Scene {credit['scene']}: {credit.get('creator') or 'Pexels contributor'}"
                if credit.get("page_url"):
                    st.markdown(f"{line} - [Pexels video]({credit['page_url']})")
                else:
                    st.write(line)

    st.divider()
    st.subheader("6. Build Hybrid Story Film")
    st.write(
        "Hybrid mode uses good AI-reviewed stock footage when it passes, then falls back "
        "to a generated cinematic still when stock is unrelated."
    )
    if st.button(
        "Build Hybrid Story Film",
        type="primary",
        use_container_width=True,
        disabled=not bool(openai_key),
    ):
        run_hybrid_story_film(pexels_key, aspect_ratio, openai_key, model, image_model)

    if not openai_key:
        st.warning("Hybrid story-film mode requires OPENAI_API_KEY for visual review and still generation.")

    st.divider()
    st.subheader("7. Review and Replace Only Bad Scenes")

    selections = st.session_state.get("selected_pexels_clips", {})
    hybrid_media = st.session_state.get("hybrid_scene_media", {})
    downloaded_paths = st.session_state.get("downloaded_clip_paths", {})
    candidates_by_scene = st.session_state.get("scene_candidates", {})
    statuses = st.session_state.get("scene_selection_status", {})

    if not selections and not statuses and not hybrid_media:
        st.info("Build an automatic draft first. Review controls appear after the app has selected footage.")
    else:
        for index, scene in enumerate(storyboard.get("scenes", []), start=1):
            scene_number = int(scene.get("scene_id") or scene.get("scene", index))
            status_info = statuses.get(scene_number, {})
            selected_clip = selections.get(scene_number)
            selected_media = hybrid_media.get(scene_number)

            with st.container():
                st.markdown('<div class="scene-card">', unsafe_allow_html=True)
                st.markdown(f"**Scene {scene_number} | {scene.get('section', 'Song')}**")
                st.caption(
                    f"{scene.get('start', 0):.1f}s-{scene.get('end', 0):.1f}s | "
                    f"{scene_duration(scene):.1f}s"
                )
                st.write(f"Lyric/musical moment: {scene.get('lyric_excerpt', '')}")
                st.write(f"Visual concept: {scene.get('recommended_visual') or scene.get('visual', '')}")
                st.write(f"Pexels search: `{status_info.get('query') or scene.get('pexels_query', '')}`")

                if selected_media:
                    st.caption(f"Source type: {selected_media.get('source_type')}")
                    if selected_media.get("status") == "Needs Attention":
                        st.error(selected_media.get("generation_error") or "Generated still needs attention.")
                    elif selected_media.get("source_type") == "generated_still":
                        st.image(selected_media.get("generated_image_path"))
                        st.caption(selected_media.get("image_prompt", ""))
                    elif selected_media.get("downloaded_path"):
                        st.video(selected_media["downloaded_path"])
                    if selected_media.get("visual_fit_score") is not None:
                        st.caption(
                            f"Visual fit: {selected_media['visual_fit_score']}/100 | "
                            f"{selected_media.get('visual_review_reason', '')}"
                        )
                    if selected_media.get("stock_fallback_reason"):
                        st.caption(f"Stock fallback: {selected_media['stock_fallback_reason']}")
                elif selected_clip:
                    preview_path = downloaded_paths.get(scene_number) or selected_clip.get("video_url")
                    st.video(preview_path)
                    creator = selected_clip.get("creator", "Pexels contributor")
                    page_url = selected_clip.get("page_url")
                    if page_url:
                        st.markdown(f"{creator} - [View on Pexels]({page_url})")
                    else:
                        st.caption(creator)
                    if selected_clip.get("visual_fit_score") is not None:
                        st.caption(
                            f"Visual fit: {selected_clip['visual_fit_score']}/100 | "
                            f"{selected_clip.get('visual_review_reason', '')}"
                        )
                    elif selected_clip.get("basic_metadata_mode"):
                        st.caption("Selected in basic metadata-only mode.")
                else:
                    st.warning(status_info.get("message") or "No clip selected for this scene.")

                overlay = scene.get("text_overlay") or text_overlay()
                with st.expander("Text overlay"):
                    overlay_text = st.text_input("Overlay text", value=overlay.get("text", ""), key=f"overlay_text_{scene_number}")
                    overlay_position = st.selectbox(
                        "Position",
                        ["lower_third", "top"],
                        index=0 if overlay.get("position", "lower_third") == "lower_third" else 1,
                        key=f"overlay_pos_{scene_number}",
                    )
                    overlay_size = st.selectbox(
                        "Size",
                        ["small", "medium", "large"],
                        index=["small", "medium", "large"].index(overlay.get("size_preset", "medium")),
                        key=f"overlay_size_{scene_number}",
                    )
                    if st.button("Apply overlay", key=f"apply_overlay_{scene_number}"):
                        scene["text_overlay"] = text_overlay(
                            overlay_text,
                            start=0,
                            duration=scene_duration(scene),
                            position=overlay_position,
                            size_preset=overlay_size,
                        )
                        st.session_state.storyboard = storyboard
                        st.success("Overlay updated.")

                c_switch1, c_switch2, c_switch3 = st.columns(3)
                with c_switch1:
                    if st.button("Switch to Generated Still", key=f"switch_still_{scene_number}"):
                        try:
                            generated = generate_still_image(
                                scene,
                                normalize_bible_lists(storyboard.get("directors_bible", {})),
                                Path(st.session_state.project_dir) / "generated_stills" / f"scene_{scene_number:03d}.png",
                                api_key=openai_key,
                                model=image_model,
                                protagonist_reference=(
                                    st.session_state.get("protagonist_reference")
                                    if st.session_state.get("protagonist_reference_approved")
                                    else None
                                ),
                            )
                            generated["source_type"] = "generated_still"
                            st.session_state.hybrid_scene_media = preserve_scene_media_state(
                                hybrid_media,
                                scene_number,
                                generated,
                            )
                            if generated.get("status") == "Needs Attention":
                                st.error(generated.get("generation_error") or "Generated still needs attention.")
                            else:
                                st.success("Scene switched to generated still.")
                        except Exception as exc:
                            st.error(f"Still generation failed: {exc}")
                with c_switch2:
                    if st.button("Regenerate Still", key=f"regen_still_{scene_number}"):
                        try:
                            generated = generate_still_image(
                                scene,
                                normalize_bible_lists(storyboard.get("directors_bible", {})),
                                Path(st.session_state.project_dir) / "generated_stills" / f"scene_{scene_number:03d}_regen.png",
                                api_key=openai_key,
                                model=image_model,
                                protagonist_reference=(
                                    st.session_state.get("protagonist_reference")
                                    if st.session_state.get("protagonist_reference_approved")
                                    else None
                                ),
                            )
                            generated["source_type"] = "generated_still"
                            st.session_state.hybrid_scene_media = preserve_scene_media_state(
                                hybrid_media,
                                scene_number,
                                generated,
                            )
                            if generated.get("status") == "Needs Attention":
                                st.error(generated.get("generation_error") or "Generated still needs attention.")
                            else:
                                st.success("Still regenerated.")
                        except Exception as exc:
                            st.error(f"Still regeneration failed: {exc}")
                with c_switch3:
                    excluded = st.checkbox("Exclude scene", value=bool(scene.get("excluded")), key=f"exclude_{scene_number}")
                    if excluded != bool(scene.get("excluded")):
                        scene["excluded"] = excluded
                        st.session_state.storyboard = storyboard

                replacement_query = st.text_input(
                    "Replacement search",
                    value=(status_info.get("query") or scene.get("pexels_query") or fallback_queries(scene)[0]),
                    key=f"replace_query_{scene_number}",
                )

                c1, c2 = st.columns([1, 1])

                with c1:
                    if st.button("Find Alternates", key=f"find_alt_{scene_number}"):
                        if not pexels_key:
                            st.error("PEXELS_API_KEY is required to search alternates.")
                        else:
                            with st.spinner(f"Searching alternates for scene {scene_number}..."):
                                try:
                                    results = search_scene(replacement_query, pexels_key, aspect_ratio)
                                    context = SelectionContext(aspect_ratio=aspect_ratio)
                                    ranked = rank_candidates(results, scene, context)
                                    candidates_by_scene[scene_number] = ranked
                                    st.session_state.scene_candidates = candidates_by_scene
                                except Exception as exc:
                                    st.error(f"Alternate search failed: {exc}")

                with c2:
                    if selected_clip and st.button("Re-render Current Selections", key=f"rerender_{scene_number}"):
                        out = Path(st.session_state.project_dir) / "songstory_automatic_draft.mp4"
                        try:
                            with st.spinner("Re-rendering current selected clips..."):
                                render_selected_pexels_clips(
                                    st.session_state.audio_path,
                                    st.session_state.storyboard,
                                    downloaded_paths,
                                    aspect_ratio,
                                    out,
                                )
                            st.session_state.final_video_path = str(out)
                            st.success("Draft re-rendered.")
                        except Exception as exc:
                            st.error(f"Render failed: {exc}")

                alternates = candidates_by_scene.get(scene_number, [])

                if alternates:
                    st.caption("Alternates")
                    columns = st.columns(3)

                    for alt_index, clip in enumerate(alternates[:6]):
                        with columns[alt_index % 3]:
                            st.video(clip["video_url"])
                            st.caption(
                                f"{clip.get('creator', 'Pexels contributor')} | "
                                f"{clip.get('duration', '?')}s | score {clip.get('score', '?')}"
                            )
                            if st.button(
                                "Use This Clip",
                                key=f"use_alt_{scene_number}_{clip.get('id')}",
                            ):
                                try:
                                    replace_scene_clip(scene, clip, aspect_ratio)
                                    st.success(f"Scene {scene_number} replaced.")
                                except Exception as exc:
                                    st.error(f"Replacement failed: {exc}")

                st.markdown("</div>", unsafe_allow_html=True)
