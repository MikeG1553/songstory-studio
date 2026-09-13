# SongStory Studio v0.5

SongStory Studio is a Streamlit application for musicians who want a complete first-draft, story-driven music video from an original song. The default workflow uses stock footage from Pexels, not paid generative video.

## Automatic Draft Workflow

1. Upload a complete song.
2. Paste lyrics or upload a lyrics document.
3. Analyze the song as one coherent film.
4. Create 10-16 meaningful story sequences for a typical full-length song.
5. Generate a short Pexels search query for each sequence.
6. Search Pexels automatically for every sequence.
7. Rank candidate clips with transparent metadata-based logic.
8. Download only the selected clip for each sequence.
9. Trim, loop, crop, scale, and assemble footage with FFmpeg.
10. Add the uploaded song as the final audio track.
11. Review the completed draft.
12. Replace only scenes that miss.

The user should not need to manually search and select footage for every scene before seeing a complete draft.

## Creative Approach

The storyboard treats the song as one film. It preserves labels such as `Intro`, `Verse 1`, `Chorus`, `Bridge`, `Guitar Solo`, and `Outro`, then uses them to shape pacing, sequence boundaries, recurring motifs, and intensity.

The app avoids literal word matching when it would create the wrong imagery. For example, a song called "Lay Your Soul Down" about an outlaw, violence, judgment, and surrender should produce searches closer to:

```text
western drifter dusty road
old western town
desert church sunset
revolver candle table
```

It should not search for sleeping people or people lying on beds simply because the title contains "lay down."

## Pexels Setup

Create a free Pexels API key and provide it as:

```bash
export PEXELS_API_KEY="..."
```

For Streamlit Community Cloud, add this secret:

```toml
PEXELS_API_KEY = "..."
```

Pexels keys are never displayed in the app. The app stores returned attribution metadata for each selected clip:

- video ID
- Pexels page URL
- creator name
- creator URL

## Optional OpenAI Setup

The app works without OpenAI by using a local section-aware storyboard generator. If an OpenAI API key is available, the app can use it to create a stronger full-song storyboard.

```bash
export OPENAI_API_KEY="..."
export OPENAI_MODEL="gpt-5.6-luna"
```

For Streamlit Community Cloud:

```toml
OPENAI_API_KEY = "..."
```

OpenAI is used only for story analysis and storyboard creation. It is not required for rendering and does not generate video.

## Automatic Clip Selection

Candidate ranking is deterministic and based on metadata Pexels actually returns:

- aspect ratio compatibility
- resolution range suitable for Streamlit
- clip duration usefulness
- duplicate video ID avoidance
- creator reuse limits
- basic query/text overlap where available

The app does not pretend to understand visual semantics that Pexels metadata does not provide. If a query returns no useful results, it retries with simpler fallback searches and marks unresolved scenes for attention.

## Replacing a Bad Scene

After the automatic draft is built, each sequence shows:

- song section
- lyric or musical moment
- visual concept
- search query
- selected footage preview
- Pexels attribution

Use **Find Alternates** on only the scene you dislike, choose **Use This Clip**, and the app re-renders the draft without restarting the full analysis.

## Rendering

Rendering uses FFmpeg:

- H.264 MP4 video
- uploaded song as audio
- stock-footage audio removed
- crop/scale to the selected output aspect ratio
- loop shorter clips when needed
- trim longer clips to sequence duration

Stability is prioritized over complex transitions.

## Run Locally

Requirements:

- Python 3.11+
- FFmpeg and ffprobe
- Pexels API key for automatic footage

```bash
cd songstory-studio
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Open the local Streamlit URL shown in the terminal.

## Streamlit Community Cloud

This repo includes `packages.txt` with `ffmpeg` for Streamlit Community Cloud. Add secrets in the app settings:

```toml
PEXELS_API_KEY = "..."
OPENAI_API_KEY = "..."  # optional
```

Resource notes:

- The app retrieves candidate metadata first.
- It downloads only selected clips by default.
- Alternates are downloaded only when selected as replacements.
- Video files are kept in the temporary Streamlit session directory.

## Limitations

- Stock footage may not perfectly match every scene.
- Pexels metadata is limited, so ranking is practical rather than truly semantic.
- The local storyboard generator is useful but less nuanced than optional AI analysis.
- Long songs and high-resolution clips can hit Streamlit Community Cloud time or storage limits.
- The app does not perform automatic beat detection or lip sync.
- Runway or other paid video generation is not part of the default workflow.

## Tests

Run syntax checks:

```bash
PYTHONPYCACHEPREFIX=/tmp/songstory_pycache python -m compileall app.py core.py renderer.py video_provider.py footage_selector.py project_state.py tests
```

Run the lightweight tests without adding pytest:

```bash
PYTHONPYCACHEPREFIX=/tmp/songstory_pycache python - <<'PY'
import importlib.util
from pathlib import Path

failures = []
for path in sorted(Path("tests").glob("test_*.py")):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in dir(module):
        if name.startswith("test_"):
            try:
                getattr(module, name)()
                print(f"PASS {path.name}::{name}")
            except Exception as exc:
                failures.append((path.name, name, exc))
                print(f"FAIL {path.name}::{name}: {exc}")

if failures:
    raise SystemExit(1)
PY
```
