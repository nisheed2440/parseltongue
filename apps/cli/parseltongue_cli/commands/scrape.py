import asyncio
import os
import time
from pathlib import Path
from typing import Annotated

import typer
from parseltongue_logger import setup_logging
from parseltongue_logger.setup import console
from rich.rule import Rule

app = typer.Typer(help="Download AO3 stories as Markdown + metadata")

RATE_LIMIT_RPM = 30
MIN_DELAY_SECS = 60 / RATE_LIMIT_RPM  # 2 seconds


def find_repo_root(start: Path | None = None) -> Path:
    """Walk up from start until we find pyproject.toml + apps/ (root marker)."""
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
) -> None:
    """Download one or more AO3 stories as Markdown chapters with a meta.yaml."""
    from parseltongue_scraper import scrape_ao3_work

    root = find_repo_root()
    output_dir = os.path.abspath(output) if output else str(root / "data" / "stories")
    logs_dir = root / "logs"
    effective_delay = max(delay, MIN_DELAY_SECS)

    os.makedirs(output_dir, exist_ok=True)
    setup_logging(logs_dir)

    total = len(story_ids)
    for i, story_id in enumerate(story_ids, 1):
        if i > 1:
            console.print(f"\n[dim]Waiting {effective_delay:.0f}s before next story (rate limit)...[/dim]")
            time.sleep(effective_delay)

        console.print(Rule(f"[bold cyan]Story {i}/{total}  ·  work {story_id}[/bold cyan]"))

        try:
            story_path = asyncio.run(
                scrape_ao3_work(story_id, output_dir, logs_dir=logs_dir)
            )
            console.print(f"\n[bold green]✓ Saved[/bold green] [dim]{story_path}[/dim]\n")
        except Exception as exc:
            console.print(f"\n[bold red]✗ Failed:[/bold red] {exc}\n", err=True)
            raise typer.Exit(1)

    console.print(Rule("[bold green]Done[/bold green]"))
