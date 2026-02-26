# Parseltongue

Fan fiction pipeline: **scrape → enunciate → speak → consume**

Turn AO3 stories into audio books — scrape chapters to Markdown, add AI voice direction via Qwen3 8b on Ollama, synthesize with Qwen3-TTS, and browse through a web app.

## Roadmap

| Phase | Package | Status | Description |
|-------|---------|--------|-------------|
| 1 | `packages/scraper` | ✅ Done | Scrape AO3 stories → Markdown + `meta.yaml` |
| 2 | `packages/director` | ✅ Done | AI voice direction using Qwen3 8b on Ollama |
| 3 | `packages/tts` | 🔜 Next | Text-to-speech using Qwen3-TTS |
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
│   │           └── direct.py
│   └── web/                    # FastAPI web app (Phase 4)
│       └── parseltongue_web/
│           └── main.py
└── packages/
    ├── scraper/                # Phase 1 — AO3 scraper (Playwright + BeautifulSoup)
    │   └── parseltongue_scraper/
    │       ├── ao3_adapter.py
    │       ├── metadata.py
    │       └── repository.py
    └── director/               # Phase 2 — Audiobook direction via Ollama
        └── parseltongue_director/
            ├── director.py
            └── prompts.py
```

Scraped data lands in `data/stories/<work_id>/`; directed scripts appear alongside it:
```
data/stories/
└── 12345/
    ├── meta.yaml
    ├── chapters/
    │   ├── 01.md
    │   ├── 02.md
    │   └── ...
    └── directed/
        ├── 01.json
        ├── 02.json
        └── ...
```

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (`pip install uv` or `curl -Lsf https://astral.sh/uv/install.sh | sh`)
- [Ollama](https://ollama.com/) with the `qwen3:8b` model pulled (`ollama pull qwen3:8b`) — required for the `direct` command

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

You can still use plain `venv` + `pip` if you prefer — create a virtualenv, then `pip install` the dependencies listed in each `pyproject.toml`. `uv` is just faster and handles the monorepo workspace wiring automatically.

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

Output JSON format:

```json
[
  { "chunk_index": 1, "text": "She could see Pansy Parkinson standing a few feet away…" },
  { "chunk_index": 2, "text": "Platform nine and three quarters was slowly filling up…" }
]
```

### Web app

```bash
uv run parseltongue-web
# Open http://localhost:8000
```

## Development

```bash
# Run CLI directly as a module
uv run python -m parseltongue_cli.main scrape 12345678

# Run web app with auto-reload
uv run uvicorn parseltongue_web.main:app --reload --app-dir apps/web
```
