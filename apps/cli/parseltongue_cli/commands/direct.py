import os
from pathlib import Path
from typing import Annotated

import typer
from parseltongue_logger import setup_logging
from parseltongue_logger.setup import console
from rich.rule import Rule
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn

app = typer.Typer(help="Direct chapter text for audiobook TTS using a local LLM")

_CHUNK_PREVIEW_LEN = 120


def find_repo_root(start: Path | None = None) -> Path:
    """Walk up from start until we find pyproject.toml + apps/ (root marker)."""
    here = (start or Path.cwd()).resolve()
    for directory in [here, *here.parents]:
        if (directory / "pyproject.toml").exists() and (directory / "apps").exists():
            return directory
    return here


def _truncate(text: str, length: int = _CHUNK_PREVIEW_LEN) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= length else text[:length].rstrip() + "…"


@app.callback(invoke_without_command=True)
def direct(
    story_ids: Annotated[
        list[str],
        typer.Argument(help="One or more work IDs to direct"),
    ],
    chapter: Annotated[
        list[int] | None,
        typer.Option("--chapter", help="Process only these chapter numbers (repeatable)"),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", "-m", help="Ollama model tag (default: OLLAMA_MODEL env var or qwen3:8b)"),
    ] = None,
    output: Annotated[
        str | None,
        typer.Option("--output", "-o", help="Stories directory (default: data/stories under repo root)"),
    ] = None,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Re-direct chapters that already have a JSON output"),
    ] = False,
    max_words: Annotated[
        int,
        typer.Option("--max-words", help="Maximum words per passage chunk (default: 200)"),
    ] = 200,
    simple: Annotated[
        bool,
        typer.Option("--simple", help="Chunk only — no AI direction, outputs chunk_index + text"),
    ] = False,
) -> None:
    """Transform scraped chapter Markdown into paragraph-level JSON direction scripts.

    Each paragraph is sent individually to a locally-running Ollama model.
    Use --simple to skip the model entirely and just output the chunked text.
    The resulting JSON is saved in ``<story_dir>/directed/<NN>.json``.
    """
    from parseltongue_director import check_ollama, chunk_work, direct_work

    root = find_repo_root()
    stories_dir = os.path.abspath(output) if output else str(root / "data" / "stories")
    logs_dir = root / "logs"

    setup_logging(logs_dir)

    if simple:
        for story_id in story_ids:
            console.print(Rule(f"[bold cyan]Chunking  ·  work {story_id}[/bold cyan]"))
            try:
                results = chunk_work(
                    story_id=story_id,
                    base_dir=stories_dir,
                    chapters=chapter or None,
                    overwrite=overwrite,
                    max_words=max_words,
                )
            except FileNotFoundError as exc:
                console.print(f"\n[bold red]✗ Not found:[/bold red] {exc}\n")
                raise typer.Exit(1)
            except Exception as exc:
                console.print(f"\n[bold red]✗ Failed:[/bold red] {exc}\n")
                raise typer.Exit(1)

            for idx, path in sorted(results.items()):
                console.print(
                    f"  [bold green]✓[/bold green] Chapter [bold]{idx:02d}[/bold] → [dim]{path}[/dim]"
                )
            console.print()
        console.print(Rule("[bold green]Done[/bold green]"))
        return

    try:
        check_ollama(model)
    except RuntimeError as exc:
        console.print(f"\n[bold red]✗ Ollama check failed:[/bold red] {exc}\n")
        raise typer.Exit(1)

    resolved_model = model or os.environ.get("OLLAMA_MODEL", "qwen3:8b")
    total_stories = len(story_ids)

    for i, story_id in enumerate(story_ids, 1):
        console.print(Rule(f"[bold cyan]Story {i}/{total_stories}  ·  work {story_id}[/bold cyan]"))
        console.print(f"[dim]Model:[/dim] {resolved_model}\n")

        # Progress bar — total is set once we know the chunk count for each chapter
        progress = Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
        )

        chapter_tasks: dict[int, object] = {}

        def on_chunk(
            chunk_idx: int,
            total_chunks: int,
            chunk_text: str,
            instruct: str,
            _chapter_idx: int = 0,
        ) -> None:
            task_id = chapter_tasks.get(_chapter_idx)
            if task_id is None:
                return

            # Print chunk + direction above the progress bar
            progress.console.print(
                f"  [bold]Chunk {chunk_idx}/{total_chunks}[/bold]  "
                f"[dim]{_truncate(chunk_text)}[/dim]"
            )
            progress.console.print(
                f"  [green]→[/green] [italic]{instruct}[/italic]\n"
            )
            progress.update(task_id, completed=chunk_idx, total=total_chunks)  # type: ignore[arg-type]

        try:
            with progress:
                all_results: dict[int, str] = {}

                # We need per-chapter callbacks, so wrap direct_work chapter by chapter
                from parseltongue_director.director import (
                    direct_chapter,
                    directed_dir,
                    directed_file_path,
                    has_directed,
                    list_chapter_files,
                )
                from parseltongue_scraper.repository import sanitize_id
                import json

                story_dir = os.path.join(stories_dir, sanitize_id(story_id))
                os.makedirs(directed_dir(story_dir), exist_ok=True)

                all_chapters = list_chapter_files(story_dir)
                if not all_chapters:
                    raise FileNotFoundError(
                        f"No chapter files found in {story_dir}/chapters/"
                    )

                target = (
                    [(idx, p) for idx, p in all_chapters if idx in chapter]
                    if chapter
                    else list(all_chapters)
                )

                for ch_idx, chapter_path in sorted(target):
                    out_path = directed_file_path(story_dir, ch_idx)

                    if not overwrite and has_directed(story_dir, ch_idx):
                        console.print(
                            f"  [dim]Chapter {ch_idx:02d} already directed — skipping[/dim]"
                        )
                        all_results[ch_idx] = out_path
                        continue

                    task_id = progress.add_task(
                        f"[cyan]Chapter {ch_idx:02d}[/cyan]", total=1
                    )
                    chapter_tasks[ch_idx] = task_id

                    def make_callback(idx: int):
                        def cb(ci: int, tot: int, text: str, instr: str) -> None:
                            on_chunk(ci, tot, text, instr, _chapter_idx=idx)
                        return cb

                    chapter_text = chapter_path.read_text(encoding="utf-8")
                    sentences = direct_chapter(
                        chapter_text,
                        model=resolved_model,
                        max_words=max_words,
                        on_chunk=make_callback(ch_idx),
                    )

                    with open(out_path, "w", encoding="utf-8") as fh:
                        json.dump(sentences, fh, ensure_ascii=False, indent=2)

                    all_results[ch_idx] = out_path

        except FileNotFoundError as exc:
            console.print(f"\n[bold red]✗ Not found:[/bold red] {exc}\n")
            raise typer.Exit(1)
        except Exception as exc:
            console.print(f"\n[bold red]✗ Failed:[/bold red] {exc}\n")
            raise typer.Exit(1)

        for idx, path in sorted(all_results.items()):
            console.print(
                f"  [bold green]✓[/bold green] Chapter [bold]{idx:02d}[/bold] → [dim]{path}[/dim]"
            )
        console.print()

    console.print(Rule("[bold green]Done[/bold green]"))
