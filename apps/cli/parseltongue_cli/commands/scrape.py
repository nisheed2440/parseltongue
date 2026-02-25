import asyncio
import os
import time
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(help="Download AO3 stories as Markdown + metadata")

RATE_LIMIT_RPM = 30
MIN_DELAY_SECS = 60 / RATE_LIMIT_RPM  # 2 seconds


def find_repo_root(start: Path | None = None) -> Path:
    """Walk up from start until we find pyproject.toml (root marker)."""
    here = (start or Path.cwd()).resolve()
    for directory in [here, *here.parents]:
        if (directory / "pyproject.toml").exists() and (directory / "apps").exists():
            return directory
    return here


@app.callback(invoke_without_command=True)
def scrape(
    story_ids: Annotated[list[str], typer.Argument(help="One or more AO3 work IDs (from URL .../works/<id>)")],
    output: Annotated[
        str | None,
        typer.Option("--output", "-o", help="Output directory (default: data/stories under repo root)"),
    ] = None,
    delay: Annotated[
        float,
        typer.Option(help="Seconds between stories (min 2 to respect rate limits)"),
    ] = 2.0,
    headless: Annotated[
        bool,
        typer.Option(help="Run browser without a visible window"),
    ] = False,
) -> None:
    """Download one or more AO3 stories as Markdown chapters with a meta.yaml."""
    from parseltongue_scraper import scrape_ao3_work

    root = find_repo_root()
    output_dir = os.path.abspath(output) if output else str(root / "data" / "stories")
    effective_delay = max(delay, MIN_DELAY_SECS)

    os.makedirs(output_dir, exist_ok=True)

    for i, story_id in enumerate(story_ids):
        if i > 0:
            time.sleep(effective_delay)
        typer.echo(f"Scraping {story_id}...")
        try:
            story_path = asyncio.run(scrape_ao3_work(story_id, output_dir, headless=headless))
            typer.echo(f"  -> {story_path}")
        except Exception as exc:
            typer.echo(f"  Error: {exc}", err=True)
            raise typer.Exit(1)
