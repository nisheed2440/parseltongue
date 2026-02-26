import json
import os
import re
from pathlib import Path
from typing import Callable

import ollama
from parseltongue_logger import get_logger
from parseltongue_scraper.repository import sanitize_id

from .prompts import DIRECTION_PROMPT

# Called after each chunk: (chunk_index, total_chunks, chunk_text, instruct)
ChunkCallback = Callable[[int, int, str, str], None]

log = get_logger(__name__)

DEFAULT_MODEL = "qwen3:8b"
DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MAX_WORDS = 200


def _client() -> ollama.Client:
    """Build an Ollama client from env vars (populated by load_dotenv in the CLI)."""
    host = os.environ.get("OLLAMA_BASE_URL", DEFAULT_HOST)
    return ollama.Client(host=host)


def _default_model() -> str:
    return os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL)


def check_ollama(model: str | None = None) -> None:
    """Raise a clear RuntimeError if Ollama is unreachable or the model is missing.

    Performs two checks in order:
    1. Can we reach the Ollama server at all?
    2. Is the requested model already pulled?
    """
    host = os.environ.get("OLLAMA_BASE_URL", DEFAULT_HOST)
    resolved_model = model or _default_model()
    client = _client()

    try:
        available = client.list()
    except Exception:
        raise RuntimeError(
            f"Cannot reach Ollama at {host}. "
            "Make sure it is running (`ollama serve`) and that "
            "OLLAMA_BASE_URL in your .env points to the right address."
        )

    pulled = [m.model for m in available.models]

    def _base(tag: str) -> str:
        parts = tag.split(":")
        return ":".join(parts[:2]) if len(parts) >= 2 else tag

    if not any(_base(p) == _base(resolved_model) for p in pulled):
        raise RuntimeError(
            f"Model '{resolved_model}' is not available in Ollama at {host}. "
            f"Pull it first with: ollama pull {resolved_model}"
        )


# ---------------------------------------------------------------------------
# Chapter ordinal helper
# ---------------------------------------------------------------------------

_ONES = [
    "", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
    "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
    "Seventeen", "Eighteen", "Nineteen",
]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty",
         "Sixty", "Seventy", "Eighty", "Ninety"]


def _chapter_ordinal(n: int) -> str:
    """Return the spoken ordinal form.

    Examples: 1 → 'One', 21 → 'Twenty-One', 100 → 'One Hundred',
              115 → 'One Hundred Fifteen', 121 → 'One Hundred Twenty-One'.
    """
    if 1 <= n < 20:
        return _ONES[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return _TENS[tens] if ones == 0 else f"{_TENS[tens]}-{_ONES[ones]}"
    if n < 1000:
        hundreds, remainder = divmod(n, 100)
        base = f"{_ONES[hundreds]} Hundred"
        if remainder == 0:
            return base
        return f"{base} {_chapter_ordinal(remainder)}"
    return str(n)


# ---------------------------------------------------------------------------
# Text chunking
# ---------------------------------------------------------------------------

def _split_paragraph(para: str, max_words: int) -> list[str]:
    """Split a single over-long paragraph at sentence boundaries."""
    sentences = re.split(r'(?<=[.!?…])\s+', para)
    chunks: list[str] = []
    current: list[str] = []
    current_words = 0

    for sentence in sentences:
        words = len(sentence.split())
        if current and current_words + words > max_words:
            chunks.append(" ".join(current))
            current = [sentence]
            current_words = words
        else:
            current.append(sentence)
            current_words += words

    if current:
        chunks.append(" ".join(current))

    return chunks


def split_into_chunks(
    text: str,
    max_words: int = DEFAULT_MAX_WORDS,
    chapter_index: int | None = None,
) -> list[str]:
    """Split chapter text into chunks, one paragraph per chunk.

    Strategy:
    - Split on blank lines to get natural paragraphs.
    - Any leading Markdown headings (``# …``) are always stripped.
    - If *chapter_index* is provided, ``"Chapter One"`` etc. is prepended as
      the very first chunk regardless of whether a heading was present.
    - Each paragraph is always its own chunk — paragraphs are never merged.
    - If a single paragraph exceeds *max_words*, it is split at sentence
      boundaries to stay within the limit.
    """
    paragraphs: list[str] = []
    current: list[str] = []

    for line in text.split("\n"):
        if line.strip() == "":
            if current:
                para = "\n".join(current).strip()
                if para:
                    paragraphs.append(para)
                current = []
        else:
            current.append(line)

    if current:
        para = "\n".join(current).strip()
        if para:
            paragraphs.append(para)

    # Strip any leading Markdown headings
    while paragraphs and paragraphs[0].lstrip().startswith("#"):
        paragraphs.pop(0)

    chunks: list[str] = []
    if chapter_index is not None:
        chunks.append(f"Chapter {_chapter_ordinal(chapter_index)}")
    for para in paragraphs:
        if len(para.split()) > max_words:
            chunks.extend(_split_paragraph(para, max_words))
        else:
            chunks.append(para)

    return chunks


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------

def _extract_instruct(raw: str) -> str:
    """Pull the instruct string out of a model response like {"instruct": "..."}."""
    raw = raw.strip()

    # Strip thinking blocks some models emit
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

    # Strip markdown code fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    data = json.loads(raw.strip())

    if isinstance(data, dict) and "instruct" in data:
        return str(data["instruct"])

    raise ValueError(f"Model response missing 'instruct' key: {raw[:200]}")


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------

def chunk_chapter(
    chapter_text: str,
    max_words: int = DEFAULT_MAX_WORDS,
    chapter_index: int | None = None,
) -> list[dict]:
    """Split *chapter_text* into chunks and return them without any AI direction.

    Each item contains only ``chunk_index`` and ``text`` — no ``instruct``.
    If *chapter_index* is provided and the file starts with a ``# heading``,
    the heading is replaced by the spoken form ``"Chapter One"`` etc.
    """
    chunks = split_into_chunks(chapter_text, max_words=max_words, chapter_index=chapter_index)
    return [{"chunk_index": i, "text": chunk} for i, chunk in enumerate(chunks, 1)]


def chunk_work(
    story_id: str,
    base_dir: str,
    chapters: list[int] | None = None,
    overwrite: bool = False,
    max_words: int = DEFAULT_MAX_WORDS,
) -> dict[int, str]:
    """Chunk all (or selected) chapters of a work without AI direction.

    Produces the same ``directed/<NN>.json`` files as :func:`direct_work` but
    with only ``chunk_index`` and ``text`` fields — no model is consulted.
    """
    story_dir = os.path.join(base_dir, sanitize_id(story_id))
    os.makedirs(directed_dir(story_dir), exist_ok=True)

    all_chapters = list_chapter_files(story_dir)
    if not all_chapters:
        raise FileNotFoundError(f"No chapter files found in {story_dir}/chapters/")

    target = (
        [(idx, p) for idx, p in all_chapters if idx in chapters]
        if chapters
        else list(all_chapters)
    )

    results: dict[int, str] = {}

    for idx, chapter_path in sorted(target):
        out_path = directed_file_path(story_dir, idx)

        if not overwrite and has_directed(story_dir, idx):
            log.info("Chapter %02d already chunked – skipping (use --overwrite to redo)", idx)
            results[idx] = out_path
            continue

        log.info("Chunking chapter %02d: %s", idx, chapter_path.name)
        chapter_text = chapter_path.read_text(encoding="utf-8")
        chunks = chunk_chapter(chapter_text, max_words=max_words, chapter_index=idx)

        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(chunks, fh, ensure_ascii=False, indent=2)

        log.info("Chapter %02d → %d chunks → %s", idx, len(chunks), out_path)
        results[idx] = out_path

    return results

def direct_chunk(chunk_text: str, model: str | None = None) -> str:
    """Ask the model for a direction instruction for *chunk_text*.

    The model is only asked to produce an ``instruct`` string — it never
    touches the passage text, eliminating any risk of rewriting.
    """
    resolved_model = model or _default_model()
    client = _client()

    response = client.chat(
        model=resolved_model,
        messages=[
            {"role": "system", "content": DIRECTION_PROMPT},
            {"role": "user", "content": chunk_text},
        ],
        options={"temperature": 0.3},
    )

    return _extract_instruct(response.message.content)


_TITLE_INSTRUCT = "Announce the chapter title clearly, steady and measured pace, slight pause after."


def direct_chapter(
    chapter_text: str,
    model: str | None = None,
    max_words: int = DEFAULT_MAX_WORDS,
    chapter_index: int | None = None,
    on_chunk: "ChunkCallback | None" = None,
) -> list[dict]:
    """Chunk *chapter_text* and direct each chunk.

    Text is split into paragraph groups of at most *max_words* words by Python.
    The model only provides the ``instruct`` field — the ``text`` field is set
    verbatim from the source, guaranteeing the manuscript is never rewritten.

    If *chapter_index* is provided and the chapter file starts with a
    ``# heading``, the heading is replaced by ``"Chapter One"`` etc. and given
    a fixed instruct without consulting the model.

    *on_chunk* is called after each chunk with ``(index, total, text, instruct)``.

    Returns a list of dicts with keys: ``chunk_index``, ``text``, ``instruct``.
    """
    resolved_model = model or _default_model()
    chunks = split_into_chunks(chapter_text, max_words=max_words, chapter_index=chapter_index)
    log.debug(
        "Chapter split into %d chunks (max %d words each)", len(chunks), max_words
    )

    results: list[dict] = []
    for i, chunk in enumerate(chunks, 1):
        # Title chunks (e.g. "Chapter One") get a fixed instruct — no model call needed.
        if chapter_index is not None and i == 1 and chunk == f"Chapter {_chapter_ordinal(chapter_index)}":
            instruct = _TITLE_INSTRUCT
        else:
            log.debug("Directing chunk %d/%d (%d words)", i, len(chunks), len(chunk.split()))
            instruct = direct_chunk(chunk, model=resolved_model)
        results.append({"chunk_index": i, "text": chunk, "instruct": instruct})
        if on_chunk:
            on_chunk(i, len(chunks), chunk, instruct)

    return results


def direct_work(
    story_id: str,
    base_dir: str,
    model: str | None = None,
    chapters: list[int] | None = None,
    overwrite: bool = False,
    max_words: int = DEFAULT_MAX_WORDS,
    on_chunk: "ChunkCallback | None" = None,
) -> dict[int, str]:
    """Direct all (or selected) chapters of a work.

    Args:
        story_id:  The work ID used by the scraper.
        base_dir:  Root data directory (contains ``<story_id>/``).
        model:     Ollama model tag to use.
        chapters:  If given, only process these 1-based chapter indices.
        overwrite: Re-process chapters that already have a directed JSON.
        max_words: Maximum words per passage chunk (default 200).

    Returns:
        Mapping of chapter_index → path of the written JSON file.
    """
    story_dir = os.path.join(base_dir, sanitize_id(story_id))
    os.makedirs(directed_dir(story_dir), exist_ok=True)

    all_chapters = list_chapter_files(story_dir)
    if not all_chapters:
        raise FileNotFoundError(f"No chapter files found in {story_dir}/chapters/")

    target = (
        {idx: path for idx, path in all_chapters if idx in chapters}
        if chapters
        else {idx: path for idx, path in all_chapters}
    )

    results: dict[int, str] = {}

    for idx, chapter_path in sorted(target.items()):
        out_path = directed_file_path(story_dir, idx)

        if not overwrite and has_directed(story_dir, idx):
            log.info(
                "Chapter %02d already directed – skipping (use --overwrite to redo)", idx
            )
            results[idx] = out_path
            continue

        log.info("Directing chapter %02d: %s", idx, chapter_path.name)
        chapter_text = chapter_path.read_text(encoding="utf-8")

        sentences = direct_chapter(
            chapter_text,
            model=model,
            max_words=max_words,
            chapter_index=idx,
            on_chunk=on_chunk,
        )

        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(sentences, fh, ensure_ascii=False, indent=2)

        log.info(
            "Chapter %02d → %d chunks → %s", idx, len(sentences), out_path
        )
        results[idx] = out_path

    return results


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------

def directed_dir(story_dir: str) -> str:
    return os.path.join(story_dir, "directed")


def directed_file_path(story_dir: str, chapter_index: int) -> str:
    num = str(chapter_index).zfill(2)
    return os.path.join(directed_dir(story_dir), f"{num}.json")


def has_directed(story_dir: str, chapter_index: int) -> bool:
    return os.path.exists(directed_file_path(story_dir, chapter_index))


def list_chapter_files(story_dir: str) -> list[tuple[int, Path]]:
    """Return (index, path) pairs for every chapter file, sorted by index."""
    chapters_path = Path(story_dir) / "chapters"
    if not chapters_path.is_dir():
        return []
    results: list[tuple[int, Path]] = []
    for f in sorted(chapters_path.iterdir()):
        if f.suffix == ".md":
            try:
                idx = int(f.stem)
                results.append((idx, f))
            except ValueError:
                pass
    return results
