# Parseltongue

A Harry Potter fan fiction pipeline: scrape → enunciate → speak → consume.

This repo is a **Turborepo** monorepo. Packages are importable libraries; apps are runnable entry points. A single CLI app (`apps/cli`) orchestrates the pipeline by calling into the packages.

## Roadmap

| Phase | Goal | Location |
|-------|------|----------|
| **1** | Scraper | `packages/scraper` — Markdown + metadata from AO3 |
| **2** | Enunciation | `packages/enunciate` — Voice-direct chapter Markdown for natural spoken delivery |
| **3** | TTS | `packages/tts` — Qwen3-TTS: enunciated Markdown → audio |
| **4** | Web app | `apps/web` — Browse, listen |

## Project layout

```
parseltongue/
├── README.md
├── package.json          # Root workspace + Turbo scripts
├── turbo.json
├── requirements.txt      # Python deps (pip install -r requirements.txt)
├── .venv/                # Python virtual environment (gitignored, you create this)
├── data/                 # Scraped stories (gitignored)
│   └── stories/
│       └── <story_id>/
│           ├── meta.yaml
│           └── chapters/
│               ├── 01.md
│               └── ...
├── apps/
│   ├── cli/              # Pipeline CLI (subcommands: scrape, enunciate, speak, …)
│   └── web/              # Phase 4 — TBD
├── packages/
│   ├── scraper/          # Phase 1 — Node + Playwright
│   ├── enunciate/        # Phase 2 — Paragraph splitting + voice directions
│   └── tts/              # Phase 3 — Qwen3-TTS (warm Gradio server or one-shot Python)
└── tests/
```

## Getting started

1. **Install dependencies** (from repo root):
   ```bash
   npm install
   ```
   This installs workspace deps and runs `playwright install chromium` for the scraper.

2. **Set up environment variables:**
   ```bash
   cp .env.example .env
   ```
   Edit `.env` if you need to override defaults (Python path, TTS server URL).

3. **Run the scraper** (Phase 1) with one or more AO3 work IDs:
   ```bash
   npm run scrape -- 12345 67890
   ```
   Options: `-o` / `--output` (output dir), `--delay` (seconds between stories), `--headless`. See `packages/scraper/README.md`.

4. **Enunciate** (Phase 2) — voice-direct scraped chapters:
   ```bash
   npm run enunciate -- 12345              # all chapters
   npm run enunciate -- 12345 3            # just chapter 3
   npm run enunciate -- 12345 --voice "A deep, resonant male narrator…"
   ```
   Options: `-f` / `--force` (overwrite existing), `-o` / `--output` (data dir), `--voice` (custom voice description).

   Splits chapters at paragraph boundaries and assigns position-aware voice directions. No external services required — runs instantly.

5. **Set up a Python virtual environment** (Phase 3) — run from the **repo root**:

   A venv keeps TTS dependencies isolated from your system Python. All Python
   commands below assume you are in the repo root (`parseltongue/`).

   ```bash
   # Create the venv (one-time) — run from the repo root
   python -m venv .venv

   # Activate it (do this every time you open a new terminal)
   source .venv/bin/activate          # Linux / macOS
   # .venv\Scripts\activate           # Windows PowerShell
   ```

   Tell parseltongue to use this venv's Python by adding to `.env`:
   ```
   PYTHON_BIN=.venv/bin/python
   ```
   This ensures the `speak` command (one-shot mode) finds the right Python.

6. **Install Python TTS dependencies** — with the venv **activated**, from the repo root:
   ```bash
   sudo apt install sox libsox-fmt-all                    # system audio dependency (once)
   pip install -r requirements.txt                        # installs qwen-tts, torch, gradio, etc.
   ```
   The Python packages live in `packages/tts/` (`synthesize.py` and `server.py`), but
   you install from the repo root where `requirements.txt` is.

   On first run, [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) model weights
   are downloaded from Hugging Face automatically (~3.5 GB). Requires a GPU.

7. **Start the TTS server** (recommended) — with the venv **activated**, from the repo root:
   ```bash
   npm run tts:serve                                     # default: port 7860, cuda:0, design mode
   npm run tts:serve -- --port 8000 --mode clone         # custom port + clone mode
   ```
   This runs `python packages/tts/server.py` under the hood.

   Then set the server URL in `.env`:
   ```
   TTS_SERVER_URL=http://localhost:7860
   ```
   The server loads Qwen3-TTS models once and keeps them resident on the GPU,
   eliminating the ~30s model-load penalty on every run.

   Alternatively, you can skip the server and use the **one-shot mode** (models
   load per-run, then exit to free the GPU):
   ```bash
   npm run speak -- 78254181 --no-server
   ```

8. **Speak** (Phase 3) — convert enunciated chapters to audio:
   ```bash
   npm run speak -- 78254181              # all enunciated chapters
   npm run speak -- 78254181 1            # just chapter 1
   npm run speak -- 78254181 --mode clone # consistent voice via cloning (~8 GB VRAM)
   ```
   Options: `-m` / `--mode` (`design` or `clone`), `-d` / `--device` (torch device), `-l` / `--language`, `-f` / `--force` (overwrite), `--server <url>` (TTS server URL), `--no-server` (force one-shot Python mode).

   When `TTS_SERVER_URL` is set (or `--server` is passed), the speak command
   sends requests to the warm Gradio server. Otherwise it spawns a one-shot
   Python process (using `PYTHON_BIN` from `.env`).

   Audio files are saved as `data/stories/<id>/chapters/01.wav`, `02.wav`, etc.

**Important**: Respect the target site's terms and rate limits. Use `--delay` to throttle (default 2s between stories, min 2s at 30 rpm).

## CLI commands (from root)

- `npm run check` — verify that all prerequisites (Python, qwen-tts, TTS server) are available
- `npm run scrape -- <id> [id ...]` — scrape AO3 stories
- `npm run enunciate -- <id> [chapter]` — voice-direct chapter Markdown (paragraph splitting + voice directions)
- `npm run speak -- <id> [chapter]` — synthesise audio from enunciated chapters via Qwen3-TTS
- `npm run tts:serve` — start the warm TTS server (keeps models loaded between runs)

## TTS modes

The `speak` command supports two synthesis modes:

| Mode | Flag | How it works | Trade-off |
|------|------|--------------|-----------|
| **design** (default) | `--mode design` | Runs VoiceDesign per segment with `VOICE:` + `INSTRUCT:` combined | Richer per-segment emotion, ~4 GB VRAM, slight voice drift |
| **clone** | `--mode clone` | Designs a voice once, then clones it for every segment | Consistent timbre, ~8 GB VRAM, less per-segment expression |

## TTS server vs one-shot mode

| Backend | How to use | Trade-off |
|---------|-----------|-----------|
| **Server** (recommended) | `npm run tts:serve` in one terminal, set `TTS_SERVER_URL` in `.env` | Models stay warm, no reload per-run, per-segment caching |
| **One-shot** | `npm run speak -- <id> --no-server` | Models load/unload per-run (~30s penalty), frees GPU between runs |

The server also provides **per-segment caching** (hashes segment content to a WAV file in `~/.cache/parseltongue/tts/`) and **automatic retry** with exponential backoff for failed segments.

## Turbo commands (from root)

- `npm run build` — build all packages that define `build`
- `npm run dev` — run `dev` in all apps (e.g. dev servers)
- `npm run lint` — lint all packages
