# Parseltongue

A Harry Potter fan fiction pipeline: scrape → enunciate → speak → consume.

This repo is a **Turborepo** monorepo. Packages are importable libraries; apps are runnable entry points. A single CLI app (`apps/cli`) orchestrates the pipeline by calling into the packages.

## Roadmap

| Phase | Goal | Location |
|-------|------|----------|
| **1** | Scraper | `packages/scraper` — Markdown + metadata from AO3 |
| **2** | Enunciation | `packages/enunciate` — Voice-direct chapter Markdown for natural spoken delivery |
| **3** | TTS | `packages/tts` — ElevenLabs: Markdown → audio |
| **4** | Web app | `apps/web` — Browse, listen |

## Project layout

```
parseltongue/
├── README.md
├── package.json          # Root workspace + Turbo scripts
├── turbo.json
├── data/                 # Scraped stories (gitignored)
│   └── stories/
│       └── <story_id>/
│           ├── meta.yaml
│           └── chapters/
│               ├── 01.md
│               └── ...
├── apps/
│   ├── cli/              # Pipeline CLI (subcommands: scrape, enunciate, …)
│   └── web/              # Phase 4 — TBD
├── packages/
│   ├── scraper/          # Phase 1 — Node + Playwright
│   ├── enunciate/        # Phase 2
│   └── tts/              # Phase 3
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
   Edit `.env` and add your API keys:
   ```
   GEMINI_API_KEY=your-key-here
   ```
   Get a Gemini API key at [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey).

3. **Run the scraper** (Phase 1) with one or more AO3 work IDs:
   ```bash
   npm run scrape -- 12345 67890
   ```
   Options: `-o` / `--output` (output dir), `--delay` (seconds between stories), `--headless`. See `packages/scraper/README.md`.

4. **Enunciate** (Phase 2) — voice-direct scraped chapters using Gemini:
   ```bash
   npm run enunciate -- 12345        # all chapters
   npm run enunciate -- 12345 3      # just chapter 3
   ```
   Options: `-m` / `--model` (Gemini model, default `gemini-2.5-flash`), `-f` / `--force` (overwrite existing), `-o` / `--output` (data dir).

**Important**: Respect the target site's terms and rate limits. Use `--delay` to throttle (default 2s between stories, min 2s at 30 rpm).

## CLI commands (from root)

- `npm run scrape -- <id> [id ...]` — scrape AO3 stories
- `npm run enunciate -- <id> [chapter]` — voice-direct chapter Markdown via Gemini

## Turbo commands (from root)

- `npm run build` — build all packages that define `build`
- `npm run dev` — run `dev` in all apps (e.g. dev servers)
- `npm run lint` — lint all packages
