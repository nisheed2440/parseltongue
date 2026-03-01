import os
from pathlib import Path
from typing import Annotated

import typer
from parseltongue_logger import setup_logging
from parseltongue_logger.setup import console
from rich.rule import Rule
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn

app = typer.Typer(
    help="Synthesise directed chapters to audio using Qwen3-TTS voice cloning (Python-native)",
    no_args_is_help=True,
)

_CHUNK_PREVIEW_LEN = 100


def _find_repo_root(start: Path | None = None) -> Path:
    here = (start or Path.cwd()).resolve()
    for directory in [here, *here.parents]:
        if (directory / "pyproject.toml").exists() and (directory / "apps").exists():
            return directory
    return here


def _truncate(text: str, length: int = _CHUNK_PREVIEW_LEN) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= length else text[:length].rstrip() + "…"


# ---------------------------------------------------------------------------
# register-voice sub-command
# ---------------------------------------------------------------------------

@app.command("register-voice")
def register_voice_cmd(
    voice_name: Annotated[str, typer.Argument(help="Short name for the voice profile")],
    ref_audio: Annotated[
        Path,
        typer.Option(
            "--ref-audio", "-a",
            help="Path to reference audio (WAV/MP3/M4A, 10–20 s recommended)",
            exists=True,
        ),
    ],
    ref_text: Annotated[
        str,
        typer.Option("--ref-text", "-t", help="Verbatim transcript of the reference audio"),
    ] = "",
    language: Annotated[
        str,
        typer.Option("--language", "-l", help="Language of the reference audio (default: English)"),
    ] = "English",
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Replace an existing profile with the same name"),
    ] = False,
) -> None:
    """Register a voice profile from a reference audio clip.

    Loads the TTS model (first run downloads ~3.5 GB and takes ~30 s),
    processes the reference clip with create_voice_clone_prompt, and saves
    the speaker embeddings to ~/qwen3-tts/profiles/<name>/prompt.pt.

    Subsequent synthesis calls load the cached embeddings instantly — the
    reference audio is never reprocessed unless you re-register.

    Example
    -------
        parseltongue speak register-voice myvoice \\
            --ref-audio myvoice.wav \\
            --ref-text "The quick brown fox jumps over the lazy dog."
    """
    from parseltongue_tts import register_voice

    root = _find_repo_root()
    setup_logging(root / "logs")

    console.print(Rule(f"[bold cyan]Registering voice  ·  {voice_name}[/bold cyan]"))
    console.print(f"[dim]Model:[/dim]    {os.environ.get('TTS_MODEL_ID', 'Qwen/Qwen3-TTS-12Hz-1.7B-Base')}")
    console.print(f"[dim]Device:[/dim]   {os.environ.get('TTS_DEVICE', 'cuda:0')}\n")

    try:
        profile_dir = register_voice(
            voice_name=voice_name,
            ref_audio_path=ref_audio,
            ref_text=ref_text,
            language=language,
            overwrite=overwrite,
        )
    except FileNotFoundError as exc:
        console.print(f"\n[bold red]✗ Not found:[/bold red] {exc}\n")
        raise typer.Exit(1)
    except Exception as exc:
        console.print(f"\n[bold red]✗ Failed:[/bold red] {exc}\n")
        raise typer.Exit(1)

    console.print(
        f"  [bold green]✓[/bold green] Profile saved → [dim]{profile_dir}[/dim]"
    )
    console.print(Rule("[bold green]Done[/bold green]"))


# ---------------------------------------------------------------------------
# run sub-command (synthesis)
# ---------------------------------------------------------------------------

@app.command("run")
def run_cmd(
    story_ids: Annotated[
        list[str],
        typer.Argument(help="One or more work IDs to synthesise"),
    ],
    voice: Annotated[
        str,
        typer.Option("--voice", "-v", help="Voice profile name or built-in speaker (default: ryan)"),
    ] = "ryan",
    chapter: Annotated[
        list[int] | None,
        typer.Option("--chapter", help="Process only these chapter numbers (repeatable)"),
    ] = None,
    output: Annotated[
        str | None,
        typer.Option("--output", "-o", help="Stories directory (default: data/stories under repo root)"),
    ] = None,
    language: Annotated[
        str | None,
        typer.Option("--language", "-l", help="Override the language stored in the voice profile"),
    ] = None,
    silence_ms: Annotated[
        int,
        typer.Option("--silence-ms", help="Silence between chunks in milliseconds"),
    ] = 400,
    chunk: Annotated[
        list[int] | None,
        typer.Option("--chunk", help="Re-synthesise only these chunk indices (repeatable); stitches when all chunks are ready"),
    ] = None,
    max_retries: Annotated[
        int,
        typer.Option("--max-retries", help="Max retry attempts per chunk after a synthesis error (default: 2)"),
    ] = 2,
    retry_cooldown: Annotated[
        int,
        typer.Option("--retry-cooldown", help="Seconds to wait between retry attempts (default: 300)"),
    ] = 300,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Re-synthesise chunks that already have audio files"),
    ] = False,
    use_instruct: Annotated[
        bool,
        typer.Option("--instruct/--no-instruct", help="Forward AI voice directions to the model (default: on)"),
    ] = True,
    ref_audio: Annotated[
        Path | None,
        typer.Option("--ref-audio", "-a", help="Register this reference audio before synthesising"),
    ] = None,
    ref_text: Annotated[
        str,
        typer.Option("--ref-text", "-t", help="Transcript of the reference audio (used with --ref-audio)"),
    ] = "",
) -> None:
    """Synthesise directed chapter chunks to audio using Qwen3-TTS voice cloning.

    The TTS model runs locally — no Docker or external server needed.
    On the first run the model (~3.5 GB) is downloaded from HuggingFace.

    Quick start
    -----------
    1. Install qwen-tts in a Python 3.12 environment with CUDA torch:

           conda create -n qwen3-tts python=3.12 -y && conda activate qwen3-tts
           pip install torch --index-url https://download.pytorch.org/whl/cu128
           pip install qwen-tts flash-attn --no-build-isolation
           pip install -e .   # install parseltongue into the same env

    2. Register your voice (once):

           parseltongue speak register-voice myvoice --ref-audio clip.wav

    3. Synthesise a story:

           parseltongue speak run 11163924 --voice myvoice

    Output
    ------
    Per-chunk WAVs: data/stories/<id>/audio/<NN>/<chunk_index:04d>.wav
    Stitched chapter: data/stories/<id>/audio/<NN>.wav
    """
    from parseltongue_tts import register_voice, synthesize_chapter, chapter_audio_path, unload_other_models
    from parseltongue_tts.synthesizer import _is_builtin_speaker
    from parseltongue_scraper.repository import sanitize_id
    import json

    root = _find_repo_root()
    stories_dir = os.path.abspath(output) if output else str(root / "data" / "stories")
    setup_logging(root / "logs")

    device = os.environ.get("TTS_DEVICE", "cuda:0")

    unload_other_models()

    if ref_audio is not None:
        console.print(Rule(f"[bold cyan]Registering voice  ·  {voice}[/bold cyan]"))
        try:
            profile_dir = register_voice(
                voice_name=voice,
                ref_audio_path=ref_audio,
                ref_text=ref_text,
                language=language or "English",
                overwrite=overwrite,
            )
            console.print(
                f"  [bold green]✓[/bold green] Profile saved → [dim]{profile_dir}[/dim]\n"
            )
        except Exception as exc:
            console.print(f"\n[bold red]✗ Voice registration failed:[/bold red] {exc}\n")
            raise typer.Exit(1)

    total_stories = len(story_ids)

    for i, story_id in enumerate(story_ids, 1):
        console.print(
            Rule(f"[bold cyan]Story {i}/{total_stories}  ·  work {story_id}[/bold cyan]")
        )
        if _is_builtin_speaker(voice):
            model_id = os.environ.get("TTS_CUSTOM_VOICE_MODEL_ID", "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice")
            voice_label = f"{voice} [dim italic](built-in speaker)[/dim italic]"
        else:
            model_id = os.environ.get("TTS_MODEL_ID", "Qwen/Qwen3-TTS-12Hz-1.7B-Base")
            voice_label = voice
        console.print(f"[dim]Voice:[/dim]    {voice_label}")
        console.print(f"[dim]Model:[/dim]    {model_id}  on  {device}")
        console.print(f"[dim]Instruct:[/dim] {'on' if use_instruct else 'off'}\n")

        story_dir = Path(stories_dir) / sanitize_id(story_id)
        directed_root = story_dir / "directed"

        if not directed_root.is_dir():
            console.print(
                f"[bold red]✗ No directed/ folder found:[/bold red] {directed_root}\n"
                "  Run [bold]parseltongue direct[/bold] first.\n"
            )
            raise typer.Exit(1)

        available = sorted(
            int(p.stem) for p in directed_root.glob("*.json") if p.stem.isdigit()
        )
        target = [ch for ch in available if ch in chapter] if chapter else available

        if not target:
            console.print("[bold yellow]⚠ No chapters to process.[/bold yellow]\n")
            continue

        chapter_results: dict[int, str] = {}

        for ch_idx in target:
            directed_path = directed_root / f"{ch_idx:02d}.json"
            stitched_path = chapter_audio_path(story_dir, ch_idx)

            if stitched_path.exists() and not overwrite and not chunk:
                console.print(
                    f"  [dim]Chapter {ch_idx:02d} already synthesised — skipping[/dim]"
                )
                chapter_results[ch_idx] = str(stitched_path)
                continue

            chunks = sorted(
                json.loads(directed_path.read_text(encoding="utf-8")),
                key=lambda c: c["chunk_index"],
            )
            total_chunks = len(chunks)

            if chunk:
                console.print(
                    f"  [dim]Chapter {ch_idx:02d} — redoing chunk(s): "
                    + ", ".join(str(c) for c in sorted(chunk))
                    + "[/dim]"
                )

            progress = Progress(
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
                console=console,
            )

            with progress:
                task_id = progress.add_task(
                    f"[cyan]Chapter {ch_idx:02d}[/cyan]", total=total_chunks
                )

                def make_callback(tid):
                    def cb(chunk_idx: int, total: int, text: str, instruct: str | None) -> None:
                        instruct_line = (
                            f"\n     [italic dim]{instruct}[/italic dim]" if instruct else ""
                        )
                        progress.console.print(
                            f"  [bold]Chunk {chunk_idx}/{total}[/bold]  "
                            f"[dim]{_truncate(text)}[/dim]"
                            f"{instruct_line}"
                        )
                        progress.update(tid, completed=chunk_idx, total=total)
                    return cb

                def make_retry_callback(tid):
                    def on_retry(attempt: int, max_att: int, cooldown: float, exc: BaseException) -> None:
                        mins = int(cooldown) // 60
                        secs = int(cooldown) % 60
                        duration = f"{mins}m {secs}s" if mins else f"{secs}s"
                        progress.console.print(
                            f"\n  [bold yellow]⚠ Chunk failed (attempt {attempt}/{max_att + 1}):[/bold yellow] "
                            f"[dim]{exc}[/dim]\n"
                            f"  [dim]Cooling down for {duration} before retry…[/dim]\n"
                        )
                    return on_retry

                try:
                    out = synthesize_chapter(
                        story_id=story_id,
                        chapter_index=ch_idx,
                        voice_name=voice,
                        base_dir=stories_dir,
                        language=language,
                        silence_ms=silence_ms,
                        overwrite=overwrite,
                        use_instruct=use_instruct,
                        on_chunk=make_callback(task_id),
                        chunk_indices=chunk if chunk else None,
                        max_retries=max_retries,
                        cooldown_s=retry_cooldown,
                        on_retry=make_retry_callback(task_id),
                    )
                    chapter_results[ch_idx] = out
                except Exception as exc:
                    console.print(
                        f"\n[bold red]✗ Chapter {ch_idx:02d} failed:[/bold red] {exc}\n"
                    )
                    raise typer.Exit(1)

        for idx, path in sorted(chapter_results.items()):
            is_wav = path.endswith(".wav") and not path.endswith(f"{idx:02d}/")
            status = "[bold green]✓[/bold green]" if is_wav else "[bold yellow]~[/bold yellow]"
            note = "" if is_wav else "  [yellow](not all chunks ready — skipped stitch)[/yellow]"
            console.print(
                f"  {status} Chapter [bold]{idx:02d}[/bold] → [dim]{path}[/dim]{note}"
            )
        console.print()

    console.print(Rule("[bold green]Done[/bold green]"))
