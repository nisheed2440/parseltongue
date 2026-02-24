# Parseltongue

A Harry Potter fan fiction pipeline: scrape → embed → generate → speak → consume.

This repo is a **Turborepo** monorepo: each phase lives in its own app or package.

## Roadmap

| Phase | Goal | Location |
|-------|------|----------|
| **1** | Scraper | `apps/scraper` — Markdown + metadata from AO3 |
| **2** | RAG | `packages/rag` — Vector embeddings and retrieval |
| **3** | Generation | `packages/generate` — Chapter-by-chapter with RAG |
| **4** | TTS | `packages/tts` — ElevenLabs: Markdown → audio |
| **5** | Web app | `apps/web` — Browse, generate, listen |

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
│   ├── scraper/          # Phase 1 — Node + Playwright
│   └── web/              # Phase 5 — TBD
├── packages/
│   ├── rag/              # Phase 2
│   ├── generate/        # Phase 3
│   └── tts/              # Phase 4
└── tests/
```

## Getting started

1. **Install dependencies** (from repo root):
   ```bash
   npm install
   ```
   This installs workspace deps and runs `playwright install chromium` for the scraper.

2. **Run the scraper** (Phase 1) with one or more AO3 work IDs:
   ```bash
   npm run scrape -- 12345 67890
   ```
   Options: `-o` / `--output` (output dir), `--delay` (seconds between stories), `--headless`. See `apps/scraper/README.md`.

**Important**: Respect the target site’s terms and rate limits. Use `--delay` to throttle (default 2s between stories, min 2s at 30 rpm).

## Turbo commands (from root)

- `npm run build` — build all packages that define `build`
- `npm run dev` — run `dev` in all apps (e.g. dev servers)
- `npm run scrape` — run the scraper (forwards args to `apps/scraper`)
- `npm run lint` — lint all packages
