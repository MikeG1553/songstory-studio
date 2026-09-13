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
from footage_selector import (
    SelectionContext,
    automatic_select_for_scene,
    create_openai_preview_ranker,
    fallback_queries,
    rank_candidates,
    update_selection_context,
)
from project_state import clear_project_state, ensure_project_state, reset_footage_state
from renderer import render_animatic, render_selected_pexels_clips
from video_provider import (
    PexelsAuthError,
    PexelsError,
    download_clip,
    pexels_search_videos,
)


st.set_page_config(
    page_title="SongStory Studio v0.5",
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

    scenes = storyboard.get("scenes", [])

    if not scenes:
        st.error("The storyboard has no scenes to render.")
        return

    selected: dict[int, dict[str, Any]] = {}
    downloaded: dict[int, str] = {}
    candidates_by_scene: dict[int, list[dict[str, Any]]] = {}
    statuses: dict[int, dict[str, Any]] = {}
    context = SelectionContext(aspect_ratio=aspect_ratio)
    preview_ranker = create_openai_preview_ranker(openai_api_key, openai_model)
    clips_dir = Path(project_dir) / "pexels_clips"
    progress = st.progress(0)
    status = st.empty()

    for index, scene in enumerate(scenes, start=1):
        scene_number = int(scene.get("scene", index))
        status.write(f"Searching footage for scene {scene_number} of {len(scenes)}...")

        try:
            result = automatic_select_for_scene(
                scene,
                lambda query: search_scene(query, api_key, aspect_ratio),
                context,
                preview_ranker=preview_ranker,
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
<h1>🎬 SongStory Studio v0.5</h1>
<p>Upload a song, add lyrics, build a coherent storyboard, and generate a complete first-draft music video from automatically selected Pexels footage.</p>
<p class="subtle">Normal workflow: upload → analyze → create story → find footage → build draft → replace only the scenes that miss.</p>
</div>
""",
    unsafe_allow_html=True,
)

with st.sidebar:
    st.header("Creative Direction")

    style = st.selectbox(
        "Video style",
        [
            "Cinematic realistic",
            "Southern Gothic Western",
            "Southern rock / Americana",
            "Country storytelling",
            "Dreamlike symbolic",
            "Vintage film",
            "Dark dramatic",
            "Animated",
            "Custom",
        ],
    )

    if style == "Custom":
        style = st.text_input(
            "Describe your style",
            "Cinematic, grounded, emotionally authentic",
        )

    interpretation = st.radio(
        "Interpretation",
        [
            "Combination of story + symbolism",
            "Mostly literal story",
            "Mostly symbolic",
        ],
        index=0,
    )

    aspect_ratio = st.selectbox(
        "Output format",
        ["16:9", "9:16", "1:1"],
    )

    st.divider()

    openai_key = st.text_input(
        "Optional OpenAI API key",
        value=get_secret("OPENAI_API_KEY"),
        type="password",
    )

    model = st.text_input(
        "Analysis model",
        value=os.getenv("OPENAI_MODEL", "gpt-5.6-luna"),
    )

    pexels_key = get_secret("PEXELS_API_KEY")

    if pexels_key:
        st.success("Pexels API key detected.")
    else:
        st.warning("PEXELS_API_KEY is required for automatic footage.")

    st.divider()

    if st.button("Start a new song", use_container_width=True):
        clear_project_state(st.session_state)
        st.rerun()


st.subheader("1. Upload Song")

audio = st.file_uploader(
    "Complete original song",
    type=["mp3", "wav", "m4a", "aac", "flac"],
    help="The completed draft uses this file as the final audio track.",
)

if audio:
    st.audio(audio.getvalue())


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
        height=250,
        placeholder="Paste complete lyrics with section labels like Verse 1, Chorus, Bridge, Guitar Solo...",
    )
else:
    lyric_file = st.file_uploader(
        "Lyrics file",
        type=["txt", "md", "docx"],
    )

    if lyric_file:
        lyrics = extract_lyrics(lyric_file)
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
    if not audio:
        st.error("Please upload a song first.")
    elif not lyrics.strip():
        st.error("Please paste or upload lyrics first.")
    else:
        create_project(audio)

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
    if not audio:
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

    st.subheader("4. Story Sequences")
    scenes = storyboard.get("scenes", [])

    edited = st.data_editor(
        scenes,
        hide_index=True,
        use_container_width=True,
        num_rows="dynamic",
        column_config={
            "scene": st.column_config.NumberColumn("Scene", width="small"),
            "section": st.column_config.TextColumn("Song section", width="medium"),
            "start": st.column_config.NumberColumn("Start", format="%.2f"),
            "end": st.column_config.NumberColumn("End", format="%.2f"),
            "duration": st.column_config.NumberColumn("Sec", format="%.2f", disabled=True),
            "lyric_excerpt": st.column_config.TextColumn("Lyric / musical moment", width="medium"),
            "visual": st.column_config.TextColumn("Creative visual concept", width="large"),
            "pexels_query": st.column_config.TextColumn("Short Pexels query", width="medium"),
            "camera": st.column_config.TextColumn("Camera", width="medium"),
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
        "This searches Pexels for every story sequence, ranks the returned clips, "
        "downloads only the selected footage, and renders a complete first draft."
    )

    if st.button(
        "Build Automatic Draft Video",
        type="primary",
        use_container_width=True,
    ):
        run_automatic_draft(pexels_key, aspect_ratio, openai_key, model)

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
    st.subheader("6. Review and Replace Only Bad Scenes")

    selections = st.session_state.get("selected_pexels_clips", {})
    downloaded_paths = st.session_state.get("downloaded_clip_paths", {})
    candidates_by_scene = st.session_state.get("scene_candidates", {})
    statuses = st.session_state.get("scene_selection_status", {})

    if not selections and not statuses:
        st.info("Build an automatic draft first. Review controls appear after the app has selected footage.")
    else:
        for index, scene in enumerate(storyboard.get("scenes", []), start=1):
            scene_number = int(scene.get("scene", index))
            status_info = statuses.get(scene_number, {})
            selected_clip = selections.get(scene_number)

            with st.container():
                st.markdown('<div class="scene-card">', unsafe_allow_html=True)
                st.markdown(f"**Scene {scene_number} | {scene.get('section', 'Song')}**")
                st.caption(
                    f"{scene.get('start', 0):.1f}s-{scene.get('end', 0):.1f}s | "
                    f"{scene_duration(scene):.1f}s"
                )
                st.write(f"Lyric/musical moment: {scene.get('lyric_excerpt', '')}")
                st.write(f"Visual concept: {scene.get('visual', '')}")
                st.write(f"Pexels search: `{status_info.get('query') or scene.get('pexels_query', '')}`")

                if selected_clip:
                    preview_path = downloaded_paths.get(scene_number) or selected_clip.get("video_url")
                    st.video(preview_path)
                    creator = selected_clip.get("creator", "Pexels contributor")
                    page_url = selected_clip.get("page_url")
                    if page_url:
                        st.markdown(f"{creator} - [View on Pexels]({page_url})")
                    else:
                        st.caption(creator)
                else:
                    st.warning(status_info.get("message") or "No clip selected for this scene.")

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
