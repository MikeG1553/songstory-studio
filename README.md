# SongStory Studio — MVP v0.2

A working prototype for turning a complete song into a coherent music-video plan and synchronized storyboard preview.

## What works now

- Upload a complete MP3/WAV/M4A/AAC/FLAC song.
- Paste lyrics or upload TXT/MD/DOCX lyrics.
- Read the song duration with `ffprobe`.
- Generate a full-song storyboard:
  - with OpenAI when an API key is supplied, or
  - with a no-cost local fallback when no key is supplied.
- Edit the scene timing, lyric moment, visual prompt, camera direction, mood, and transition.
- Export the storyboard as JSON.
- Reload a previously reviewed storyboard JSON and continue editing/rendering.
- Render a complete synchronized MP4 **animatic** with the original song using a faster single-pass FFmpeg workflow.
- Optionally generate **one selected** text-to-video clip through Runway Gen-4.5.
- Cost guard: the app never generates every Runway scene automatically.

## Run locally

Requirements:
- Python 3.11+
- FFmpeg / ffprobe

```bash
cd song_video_studio
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
streamlit run app.py
```

Then open the local Streamlit address shown in the terminal.

## Optional environment variables

```bash
export OPENAI_API_KEY="..."
export OPENAI_MODEL="gpt-6-astra"
export RUNWAYML_API_SECRET="..."
```

Keys can also be entered temporarily in the app sidebar. This MVP does not save them to disk.

## Product architecture

1. **Ingest** — song + lyrics
2. **Understand** — full-song concept, mood, story arc, visual continuity
3. **Storyboard** — timed shot plan with video-model prompts
4. **Review** — user edits before spending on video generation
5. **Preview** — FFmpeg animatic with original song
6. **Generate** — approved scenes through an interchangeable provider
7. **Assemble** — final scenes + original mastered song

## Why the provider is separate

Video models change quickly. The app's defensible logic should be the song-understanding and creative-director layer. The provider module can later support Runway, fal.ai models, or other generators without rewriting the core product.

## Improvements in v0.2

- Fast single-pass animatic rendering instead of encoding every scene separately.
- Storyboard JSON import for reviewed or externally prepared projects.
- Updated configurable default OpenAI analysis model.

## Recommended next build

- automatic vocal/lyric transcription for songs without supplied lyrics
- beat/section detection (intro, verse, chorus, bridge, outro)
- character reference images and continuity controls
- batch generation with an estimated-cost screen and explicit approval
- retry/regenerate per shot
- final clip timing/stretch/crop and transitions
- persistent projects and cloud storage
- authentication and usage limits
- production deployment (Next.js/FastAPI or containerized Streamlit)

## Current provider note

Runway's current developer docs expose text-to-video through its Python SDK using `client.image_to_video.create(...)` with Gen-4.5. The integration in this MVP intentionally generates only one selected scene at a time.

## Launch-tested FastAPI version

This repository also includes `server.py`, a zero-friction front end that uses the FastAPI/Uvicorn stack. Run it with:

```bash
uvicorn server:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000`. This version exercises the complete local path from upload through storyboard and rendered MP4 without requiring an AI API key.
