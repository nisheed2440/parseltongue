# Phase 1: Scraper

Saves fan fiction stories as **one directory per story**: `meta.yaml` (tags, categories, title, author, etc.) and `chapters/01.md`, `02.md`, ...

Uses **Playwright** to load AO3 work pages and **Cheerio** to parse metadata and chapter content. No config file — everything is CLI flags and defaults.

## Setup

1. **From repo root**, install dependencies:
   ```bash
   npm install
   ```
   Playwright Chromium is installed via the scraper app’s `postinstall`.

2. Run from **repo root** so the default output path (`data/stories`) resolves from the repo root. Or pass `-o` from any cwd.

## Usage

From **repo root**:

```bash
# One or more AO3 work IDs (from the URL: .../works/<id>)
npm run scrape -- 12345 67890

# Output directory (default: data/stories under repo root)
npm run scrape -- -o data/stories 12345

# Delay between stories in seconds (default: 2; minimum 2 at 30 rpm)
npm run scrape -- --delay 3 12345

# Run browser headless (no window)
npm run scrape -- --headless 12345
```

Or from this directory:

```bash
cd apps/scraper
node run.js 12345 67890
node run.js -o ../../data/stories --delay 3 --headless 12345
```

## Output layout

```
data/stories/
└── 12345/
    ├── meta.yaml      # title, author, summary, tags, word_count, ...
    └── chapters/
        ├── 01.md
        ├── 02.md
        └── ...
```

## Incremental save and resume

- **Meta** is written as soon as the work and index pages are loaded (before fetching chapters).
- **Chapters** are written to disk as soon as each one is fetched; the scraper does not wait for the full run to finish.
- **Resume**: If you run the scraper again with the same work ID and output dir, it skips any chapter file that already exists (e.g. `01.md`, `02.md`) and only fetches and saves missing chapters. No separate progress file — presence of `chapters/NN.md` is the record.

## Notes

- **AO3**: Uses Playwright to load the work and navigate pages; consent screen is handled when present. Respect rate limits; use `--delay` to throttle.
- **Headless**: Omit `--headless` to see the browser (useful for debugging).
