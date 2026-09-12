from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import streamlit as st

from core import ai_storyboard, extract_lyrics, ffprobe_duration, heuristic_storyboard, normalize_storyboard, save_uploaded_file
from renderer import render_animatic
from video_provider import runway_text_to_video

st.set_page_config(page_title="SongStory Studio", page_icon="🎬", layout="wide")

st.markdown("""
<style>
.block-container {max-width: 1180px; padding-top: 2rem;}
.hero {padding: 1.4rem 1.6rem; border: 1px solid rgba(128,128,128,.25); border-radius: 18px; margin-bottom: 1rem;}
.hero h1 {margin-bottom: .2rem;}
.subtle {opacity: .72;}
.scene-card {border:1px solid rgba(128,128,128,.25); border-radius:14px; padding:1rem; margin:.5rem 0;}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="hero">
<h1>🎬 SongStory Studio</h1>
<p>Create a coherent music-video concept from a complete song — lyrics, mood, story arc, storyboard, and scene prompts.</p>
<p class="subtle">MVP: upload → analyze → edit storyboard → render synchronized animatic → optionally generate selected AI video scenes.</p>
</div>
""", unsafe_allow_html=True)

if "storyboard" not in st.session_state:
    st.session_state.storyboard = None
if "project_dir" not in st.session_state:
    st.session_state.project_dir = None

with st.sidebar:
    st.header("Creative Direction")
    style = st.selectbox("Video style", [
        "Cinematic realistic", "Southern rock / Americana", "Country storytelling",
        "Dreamlike symbolic", "Vintage film", "Dark dramatic", "Animated", "Custom"
    ])
    if style == "Custom":
        style = st.text_input("Describe your style", "Cinematic, grounded, emotionally authentic")
    interpretation = st.radio("Interpretation", ["Combination of story + symbolism", "Mostly literal story", "Mostly symbolic"], index=0)
    aspect_ratio = st.selectbox("Output format", ["16:9", "9:16", "1:1"])
    st.divider()
    st.caption("AI analysis is optional. Without an OpenAI API key the app creates a local draft storyboard so the workflow can still be tested.")
    openai_key = st.text_input("OpenAI API key", value=os.getenv("OPENAI_API_KEY", ""), type="password")
    model = st.text_input("Analysis model", value=os.getenv("OPENAI_MODEL", "gpt-5.4-mini"))
    st.divider()
    runway_key = st.text_input("Runway API key (optional)", value=os.getenv("RUNWAYML_API_SECRET", ""), type="password")

st.subheader("1. Upload the song")
audio = st.file_uploader("Complete song", type=["mp3", "wav", "m4a", "aac", "flac"], help="The final video keeps this original audio track.")
if audio:
    st.audio(audio.getvalue())

st.subheader("2. Add lyrics")
lyric_mode = st.radio("Lyrics source", ["Paste lyrics", "Upload lyrics file"], horizontal=True)
lyrics = ""
if lyric_mode == "Paste lyrics":
    lyrics = st.text_area("Lyrics", height=250, placeholder="Paste the complete lyrics here…")
else:
    lyric_file = st.file_uploader("Lyrics file", type=["txt", "md", "docx"])
    if lyric_file:
        lyrics = extract_lyrics(lyric_file)
        st.text_area("Extracted lyrics", value=lyrics, height=250, disabled=True)

col1, col2 = st.columns([1, 2])
with col1:
    analyze_clicked = st.button("Analyze song & build storyboard", type="primary", use_container_width=True)

st.caption("Already have a storyboard from a prior creative review? Load its JSON here and continue directly to editing or animatic rendering.")
imported_storyboard_file = st.file_uploader("Optional storyboard JSON", type=["json"], key="storyboard_json_import")
load_imported = st.button("Load storyboard JSON", disabled=imported_storyboard_file is None)

if load_imported:
    if not audio:
        st.error("Upload the matching song before loading its storyboard.")
    else:
        try:
            imported = json.loads(imported_storyboard_file.getvalue().decode("utf-8"))
            project_dir = Path(tempfile.mkdtemp(prefix="songstory_"))
            audio_path = save_uploaded_file(audio, project_dir)
            duration = ffprobe_duration(audio_path)
            st.session_state.project_dir = str(project_dir)
            st.session_state.audio_path = str(audio_path)
            st.session_state.audio_duration = duration
            st.session_state.storyboard = normalize_storyboard(imported, duration)
            st.success("Storyboard loaded. You can edit it or render the synchronized animatic below.")
        except Exception as exc:
            st.error(f"The storyboard JSON could not be loaded: {exc}")

if analyze_clicked:
    if not audio:
        st.error("Please upload a song first.")
    elif not lyrics.strip():
        st.error("Please paste or upload the lyrics for this first version of the app.")
    else:
        project_dir = Path(tempfile.mkdtemp(prefix="songstory_"))
        audio_path = save_uploaded_file(audio, project_dir)
        duration = ffprobe_duration(audio_path)
        st.session_state.project_dir = str(project_dir)
        st.session_state.audio_path = str(audio_path)
        st.session_state.audio_duration = duration
        with st.spinner("Interpreting the full song and planning the visual story…"):
            if openai_key:
                try:
                    sb = ai_storyboard(lyrics, duration, style, interpretation, aspect_ratio, openai_key, model)
                except Exception as exc:
                    st.warning(f"AI analysis could not complete ({exc}). I created a local storyboard draft instead.")
                    sb = heuristic_storyboard(lyrics, duration)
            else:
                sb = heuristic_storyboard(lyrics, duration)
        st.session_state.storyboard = sb

sb = st.session_state.storyboard
if sb:
    st.divider()
    st.subheader("3. Creative treatment")
    a, b = st.columns(2)
    with a:
        st.text_area("Concept", value=sb.get("concept", ""), height=120, key="concept_edit")
        st.text_area("Story arc", value=sb.get("story_arc", ""), height=100, key="arc_edit")
    with b:
        st.text_area("Mood", value=sb.get("mood", ""), height=120, key="mood_edit")
        st.text_area("Visual continuity", value=sb.get("visual_style", ""), height=100, key="style_edit")
    st.caption(f"Storyboard source: {sb.get('source', 'unknown')} • Song duration: {st.session_state.get('audio_duration', 0):.1f}s")

    st.subheader("4. Edit the storyboard")
    scenes = sb.get("scenes", [])
    edited = st.data_editor(
        scenes,
        hide_index=True,
        use_container_width=True,
        num_rows="dynamic",
        column_config={
            "scene": st.column_config.NumberColumn("Scene", width="small"),
            "start": st.column_config.NumberColumn("Start", format="%.2f"),
            "end": st.column_config.NumberColumn("End", format="%.2f"),
            "duration": st.column_config.NumberColumn("Sec", format="%.2f", disabled=True),
            "lyric_excerpt": st.column_config.TextColumn("Lyric / moment", width="medium"),
            "visual": st.column_config.TextColumn("Visual prompt", width="large"),
            "camera": st.column_config.TextColumn("Camera", width="medium"),
        },
        key="scene_editor",
    )

    if st.button("Apply storyboard edits"):
        sb["concept"] = st.session_state.concept_edit
        sb["story_arc"] = st.session_state.arc_edit
        sb["mood"] = st.session_state.mood_edit
        sb["visual_style"] = st.session_state.style_edit
        sb["scenes"] = edited
        st.session_state.storyboard = normalize_storyboard(sb, st.session_state.get("audio_duration", 0))
        st.success("Storyboard updated.")

    export_json = json.dumps(st.session_state.storyboard, indent=2).encode("utf-8")
    st.download_button("Download storyboard JSON", export_json, file_name="songstory_storyboard.json", mime="application/json")

    st.subheader("5. Render a synchronized animatic")
    st.write("This creates a complete MP4 using storyboard cards timed to the original song. It is a low-cost way to review pacing before generating expensive AI video clips.")
    if st.button("Render animatic preview", type="primary"):
        out = Path(st.session_state.project_dir) / "songstory_animatic.mp4"
        with st.spinner("Rendering storyboard timing with the original song…"):
            try:
                render_animatic(st.session_state.audio_path, st.session_state.storyboard, aspect_ratio, out)
                st.session_state.animatic_path = str(out)
            except Exception as exc:
                st.error(f"Animatic render failed: {exc}")
    if st.session_state.get("animatic_path") and Path(st.session_state.animatic_path).exists():
        st.video(st.session_state.animatic_path)
        st.download_button("Download animatic MP4", Path(st.session_state.animatic_path).read_bytes(), file_name="songstory_animatic.mp4", mime="video/mp4")

    st.subheader("6. Optional: generate one Runway scene")
    st.warning("Cost guard: this MVP generates only the single scene you select. It never launches the full storyboard automatically.")
    if scenes:
        scene_numbers = [int(s.get("scene", i + 1)) for i, s in enumerate(st.session_state.storyboard.get("scenes", []))]
        selected_num = st.selectbox("Scene to generate", scene_numbers)
        selected = next(s for s in st.session_state.storyboard["scenes"] if int(s.get("scene")) == selected_num)
        prompt = st.text_area("Video prompt", value=selected.get("visual", ""), height=150, key=f"runway_prompt_{selected_num}")
        if st.button("Generate selected Runway scene"):
            if not runway_key:
                st.error("Enter a Runway API key in the sidebar first.")
            else:
                ratio_map = {"16:9": "1280:720", "9:16": "720:1280", "1:1": "960:960"}
                with st.spinner("Generating this one scene with Runway…"):
                    try:
                        url = runway_text_to_video(prompt, runway_key, duration=5, ratio=ratio_map[aspect_ratio])
                        st.session_state.last_runway_url = url
                    except Exception as exc:
                        st.error(f"Runway generation failed: {exc}")
        if st.session_state.get("last_runway_url"):
            st.video(st.session_state.last_runway_url)
            st.write(st.session_state.last_runway_url)

st.divider()
st.caption("MVP v0.2 • The architecture separates song interpretation from video generation so video providers can be swapped as models improve.")
