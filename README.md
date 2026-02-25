# Parseltongue

Fan fiction pipeline: **scrape → enunciate → speak → consume**

Turn AO3 stories into audio books — scrape chapters to Markdown, add AI voice direction via Qwen3 8b on Ollama, synthesize with Qwen3-TTS, and browse through a web app.

## Roadmap

| Phase | Package | Status | Description |
|-------|---------|--------|-------------|
| 1 | `packages/scraper` | ✅ Done | Scrape AO3 stories → Markdown + `meta.yaml` |
| 2 | `packages/director` | 🔜 Next | AI voice direction using Qwen3 8b on Ollama |
| 3 | `packages/tts` | 🔜 Next | Text-to-speech using Qwen3-TTS |
| 4 | `apps/web` | 🔜 Next | Web app to browse and listen to stories |

## Project layout

```
parseltongue/
├── pyproject.toml              # uv workspace root
├── apps/
│   ├── cli/                    # Typer CLI (parseltongue command)
│   │   └── parseltongue_cli/
│   │       ├── main.py
│   │       └── commands/
│   │           └── scrape.py
│   └── web/                    # FastAPI web app (Phase 4)
│       └── parseltongue_web/
│           └── main.py
└── packages/
    └── scraper/                # AO3 scraper (Playwright + BeautifulSoup)
        └── parseltongue_scraper/
            ├── ao3_adapter.py
            ├── metadata.py
            └── repository.py
```

Scraped data lands in `data/stories/<work_id>/`:
```
data/stories/
└── 12345/
    ├── meta.yaml
    └── chapters/
        ├── 01.md
        ├── 02.md
        └── ...
```

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (`pip install uv` or `curl -Lsf https://astral.sh/uv/install.sh | sh`)

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
# Scrape one story (browser visible by default)
uv run parseltongue scrape 12345678

# Scrape multiple stories
uv run parseltongue scrape 12345678 87654321

# Headless mode, custom output dir
uv run parseltongue scrape --headless --output ./my-stories 12345678

# Adjust rate-limit delay (default 2 s, min 2 s)
uv run parseltongue scrape --delay 5 12345678
```

The AO3 work ID is the number in the URL: `https://archiveofourown.org/works/**12345678**`.

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
