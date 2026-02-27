# Parseltongue

Fan fiction pipeline: **scrape → enunciate → speak → consume**

Turn AO3 stories into audio books — scrape chapters to Markdown, add AI voice direction via Qwen3 8b on Ollama, synthesize with Qwen3-TTS voice cloning, and browse through a web app.

## Roadmap

| Phase | Package | Status | Description |
|-------|---------|--------|-------------|
| 1 | `packages/scraper` | ✅ Done | Scrape AO3 stories → Markdown + `meta.yaml` |
| 2 | `packages/director` | ✅ Done | AI voice direction using Qwen3 8b on Ollama |
| 3 | `packages/tts` | ✅ Done | Text-to-speech using Qwen3-TTS voice cloning |
| 4 | `apps/web` | 🔜 Next | Web app to browse and listen to stories |

## Project layout

```
parseltongue/
├── pyproject.toml              # uv workspace root
├── .env                        # local config (copy from .env.example)
├── apps/
│   ├── cli/                    # Typer CLI (parseltongue command)
│   │   └── parseltongue_cli/
│   │       ├── main.py
│   │       └── commands/
│   │           ├── scrape.py
│   │           ├── direct.py
│   │           └── speak.py
│   └── web/                    # FastAPI web app (Phase 4)
│       └── parseltongue_web/
│           └── main.py
└── packages/
    ├── scraper/                # Phase 1 — AO3 scraper (Playwright + BeautifulSoup)
    │   └── parseltongue_scraper/
    │       ├── ao3_adapter.py
    │       ├── metadata.py
    │       └── repository.py
    ├── director/               # Phase 2 — Audiobook direction via Ollama
    │   └── parseltongue_director/
    │       ├── director.py
    │       └── prompts.py
    └── tts/                    # Phase 3 — TTS synthesis via Qwen3-TTS
        └── parseltongue_tts/
            ├── synthesizer.py
            └── stitcher.py
```

Scraped data and generated audio land in `data/stories/<work_id>/`:

```
data/stories/
└── 12345/
    ├── meta.yaml
    ├── chapters/
    │   ├── 01.md
    │   └── ...
    ├── directed/
    │   ├── 01.json             # chunk_index + text + instruct
    │   └── ...
    └── audio/
        ├── 01/
        │   ├── 0001.wav        # per-chunk audio (resumable)
        │   ├── 0002.wav
        │   └── ...
        ├── 01.wav              # stitched chapter audio
        └── ...
```

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (`pip install uv` or `curl -Lsf https://astral.sh/uv/install.sh | sh`)
- [Ollama](https://ollama.com/) with the `qwen3:8b` model pulled — required for the `direct` command
- A running [Qwen3-TTS server](#setting-up-qwen3-tts-locally) — required for the `speak` command

### Why uv instead of venv + pip?

This project uses `uv` as its package manager. If you're used to the classic `python -m venv .venv && pip install -r requirements.txt` workflow, here's the mental mapping:

| Task | venv + pip | uv |
|------|------------|----|
| Create virtualenv | `python -m venv .venv` | automatic (managed for you) |
| Install dependencies | `pip install -r requirements.txt` | `uv sync` |
| Run a script/command | `source .venv/bin/activate && python ...` | `uv run python ...` |
| Add a dependency | edit `requirements.txt`, re-run pip | `uv add <package>` (updates `pyproject.toml`) |
| Install a CLI tool globally | `pip install --user <tool>` | `uv tool install <tool>` |

`uv sync` reads `pyproject.toml` across all workspace members, resolves the full dependency graph once, and writes a `uv.lock` lockfile — the equivalent of `package-lock.json`. You never need to activate the virtualenv manually; `uv run` handles it transparently.

## Getting started

```bash
# Clone and enter the repo
git clone <repo-url>
cd parseltongue

# Install all workspace packages and their dependencies
uv sync

# Install Playwright's Chromium browser
uv run playwright install chromium

# Copy environment template
cp .env.example .env
```

---

## Usage

### CLI — scrape

```bash
# Scrape one story
uv run parseltongue scrape 12345678

# Scrape multiple stories
uv run parseltongue scrape 12345678 87654321

# Custom output dir
uv run parseltongue scrape --output ./my-stories 12345678

# Adjust rate-limit delay (default 2 s, min 2 s)
uv run parseltongue scrape --delay 5 12345678
```

The AO3 work ID is the number in the URL: `https://archiveofourown.org/works/**12345678**`.

---

### CLI — direct

The `direct` command processes scraped chapters into paragraph-level JSON scripts.
Each paragraph in the source Markdown becomes its own chunk. The `text` field is
always copied verbatim — the model only supplies the `instruct` direction string.

#### AI mode (requires Ollama)

Start Ollama first: `ollama serve`

```bash
# Direct all chapters of a story (model + host from .env or defaults)
uv run parseltongue direct 12345678

# Direct only specific chapters
uv run parseltongue direct --chapter 1 --chapter 2 12345678

# Override the model for this run
uv run parseltongue direct --model qwen3:8b 12345678

# Adjust the max words per chunk (default: 200)
uv run parseltongue direct --max-words 150 12345678

# Re-process chapters that already have a directed JSON
uv run parseltongue direct --overwrite 12345678
```

Configuration via `.env` (see `.env.example`):

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server address |
| `OLLAMA_MODEL` | `qwen3:8b` | Model tag to use |

Each chapter produces a `directed/<NN>.json` file. The CLI prints each chunk and
its direction in real time as the model responds:

```
  Chunk 3/18  She could see Pansy Parkinson standing a few feet away…
  → Emotion: Quietly defiant beneath exhaustion, Pacing: Measured, Volume: Low, Texture: Tight-throated
```

Output JSON format:

```json
[
  {
    "chunk_index": 1,
    "text": "She could see Pansy Parkinson standing a few feet away from the corner of her eye. Sneering, of course.",
    "instruct": "Emotion: Quietly defiant beneath exhaustion, Pacing: Measured, Volume: Low, Texture: Tight-throated"
  }
]
```

#### Simple mode (no AI, no Ollama needed)

Chunks the chapter text by paragraph and writes the JSON with `chunk_index` and
`text` only — no model is consulted. Useful for previewing the chunking layout
or building a plain transcript.

```bash
uv run parseltongue direct --simple 12345678

# With options
uv run parseltongue direct --simple --max-words 150 --chapter 1 12345678
```

---

### CLI — speak

The `speak` command synthesises directed chapters into audio using Qwen3-TTS in
voice clone mode. It requires a running Qwen3-TTS server (see
[Setting up Qwen3-TTS locally](#setting-up-qwen3-tts-locally)).

Each chunk is rendered individually and saved as a WAV file. Interrupted runs
are resumable — already-rendered chunks are skipped. When all chunks for a
chapter are done, they are stitched into a single chapter WAV.

Configuration via `.env` (see `.env.example`):

| Variable | Default | Description |
|---|---|---|
| `TTS_MODEL_ID` | `Qwen/Qwen3-TTS-12Hz-1.7B-Base` | HuggingFace model ID |
| `TTS_DEVICE` | `cuda:0` | PyTorch device |
| `TTS_DTYPE` | `bfloat16` | Model dtype (`bfloat16`, `float16`, `float32`) |
| `TTS_PROFILES_DIR` | `~/qwen3-tts/profiles` | Where voice profiles are stored |

#### Step 1 — Register your voice (once)

Record 10–20 seconds of yourself reading clearly with no background noise. Save
it as a WAV, MP3, or M4A file. Then register it as a named profile:

```bash
uv run parseltongue speak register-voice myvoice \
    --ref-audio /path/to/myvoice.wav \
    --ref-text "Exact words you spoke in the recording."
```

The profile is written to `~/qwen3-tts/voice_library/profiles/myvoice/` where
the TTS server looks for it. You only need to do this once per voice; re-run
with `--overwrite` to replace it.

```bash
# Options
uv run parseltongue speak register-voice myvoice \
    --ref-audio myvoice.wav \
    --ref-text "Exact transcript of the clip." \
    --language English \          # default
    --overwrite                   # replace existing profile
```

#### Step 2 — Synthesise

```bash
# Synthesise all directed chapters of a story
uv run parseltongue speak run 12345678 --voice myvoice

# Only specific chapters
uv run parseltongue speak run 12345678 --voice myvoice --chapter 1 --chapter 2

# Register voice and synthesise in one step
uv run parseltongue speak run 12345678 --voice myvoice \
    --ref-audio myvoice.wav \
    --ref-text "Exact transcript."

# Re-synthesise everything from scratch
uv run parseltongue speak run 12345678 --voice myvoice --overwrite

# Adjust silence between chunks (default: 400 ms)
uv run parseltongue speak run 12345678 --voice myvoice --silence-ms 600

# Disable AI voice directions (use model's neutral style)
uv run parseltongue speak run 12345678 --voice myvoice --no-instruct
```

##### Re-doing individual chunks

If a specific chunk sounds off you can re-synthesize it without touching the
rest of the chapter.  Use `--chunk` (repeatable) to target one or more chunk
indices:

```bash
# Redo chunk 7 of chapter 2
uv run parseltongue speak run 12345678 --voice myvoice --chapter 2 --chunk 7

# Redo multiple chunks
uv run parseltongue speak run 12345678 --voice myvoice --chapter 2 --chunk 7 --chunk 12
```

The targeted chunks are always re-synthesized (regardless of `--overwrite`).
After synthesis the chapter is **automatically re-stitched** if every expected
chunk WAV is present on disk.  If some chunks are still missing (e.g. the
original run was interrupted), a warning is printed and stitching is skipped
until the chapter is complete.

Output:

```
data/stories/12345678/audio/
    01/
        0001.wav   0002.wav   ...   (per-chunk, used for resuming)
    01.wav                          (stitched chapter audio)
    02/  02.wav  ...
```

---

## Setting up Qwen3-TTS locally

Parseltongue runs the **Qwen3-TTS-12Hz-1.7B-Base** model directly in Python —
no Docker, no server process.  The model is loaded once per session and kept
in GPU memory while synthesis is running.

| Model | Disk | VRAM | Notes |
|---|---|---|---|
| `Qwen3-TTS-12Hz-1.7B-Base` | ~3.5 GB | ≥8 GB | Voice cloning ← **this one** |
| `Qwen3-TTS-12Hz-0.6B-Base` | ~1.2 GB | ≥4 GB | Lower quality; use for CPU or tight VRAM |

### Prerequisites

- Python **3.12** (recommended; 3.11 may work)
- An NVIDIA GPU with **≥8 GB VRAM** and a matching CUDA toolkit
- `conda` or a virtual environment manager

### 1. Create a dedicated Python environment

```bash
conda create -n qwen3-tts python=3.12 -y
conda activate qwen3-tts
```

### 2. Install PyTorch with CUDA

Replace `cu128` with your installed CUDA version (`cu118`, `cu121`, `cu124`, etc.):

```bash
pip install torch --index-url https://download.pytorch.org/whl/cu128
```

Verify GPU is visible:

```bash
python -c "import torch; print(torch.cuda.get_device_name(0))"
```

### 3. Install qwen-tts and Flash Attention

```bash
pip install qwen-tts

# Flash Attention 2 — recommended (+10–15% speed on Ampere/Ada)
pip install flash-attn --no-build-isolation
```

### 4. Install Parseltongue into the same environment

The repo root is a uv workspace, so `pip install -e .` won't work directly.
Install each package in dependency order instead:

```bash
cd ~/Projects/parseltongue

pip install -e packages/logger
pip install -e packages/scraper
pip install -e packages/director
pip install -e packages/tts
pip install -e apps/cli
```

The model (~3.5 GB) is downloaded from HuggingFace automatically on first use.

### 5. Configure .env

```
TTS_MODEL_ID=Qwen/Qwen3-TTS-12Hz-1.7B-Base
TTS_DEVICE=cuda:0
TTS_DTYPE=bfloat16
```

### Reference audio requirements

For best voice cloning quality, the reference clip should:

- Be **10–20 seconds** of continuous speech (3 s minimum)
- Be **mono**, ≥24 kHz sample rate, WAV (16-bit), MP3, or M4A format
- Have **no background noise**, music, or reverb
- Contain **natural, expressive speech** — not rushed or whispered
- Be accompanied by an accurate `--ref-text` transcript

A quiet room recording on a decent microphone is sufficient.

---

## Development

```bash
# Run CLI directly as a module
uv run python -m parseltongue_cli.main scrape 12345678

# Run web app with auto-reload
uv run uvicorn parseltongue_web.main:app --reload --app-dir apps/web
```
