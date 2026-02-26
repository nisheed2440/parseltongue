"""Core TTS synthesis using the qwen-tts Python package directly.

No server or Docker required.  The model is loaded in-process on first use
and kept alive for the lifetime of the Python process, so all chunks in a
session share a single model instance.

Voice cloning workflow
----------------------
1. Call ``register_voice()`` once — loads the model, processes your reference
   audio with ``create_voice_clone_prompt``, and saves the resulting speaker
   embeddings to ``~/qwen3-tts/profiles/<name>/prompt.pt``.  This takes about
   30 s the first time (model download + load); subsequent calls are fast
   because the model is already in memory.

2. Call ``synthesize_chapter()`` / ``synthesize_work()`` — the cached voice
   prompt is reused for every chunk, so speaker embeddings are never
   recomputed within a session.  Interrupted runs are resumable: already-
   rendered chunk WAVs are skipped unless ``overwrite=True``.

Configuration (via .env or environment variables)
--------------------------------------------------
TTS_MODEL_ID   HuggingFace model ID  (default: Qwen/Qwen3-TTS-12Hz-1.7B-Base)
TTS_DEVICE     PyTorch device        (default: cuda:0)
TTS_DTYPE      Model dtype           (default: bfloat16)

Output layout
-------------
``<base_dir>/<story_id>/audio/<NN>/``
    ``<chunk_index:04d>.wav``  — per-chunk audio files
``<base_dir>/<story_id>/audio/<NN>.wav``  — stitched chapter audio
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Callable

import numpy as np
import soundfile as sf
from parseltongue_logger import get_logger
from parseltongue_scraper.repository import sanitize_id

from .stitcher import stitch_wav_files

if TYPE_CHECKING:
    from qwen_tts import Qwen3TTSModel

# Called after each chunk: (chunk_index, total_chunks, chunk_text)
ChunkCallback = Callable[[int, int, str, "str | None"], None]

log = get_logger(__name__)

DEFAULT_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
DEFAULT_DEVICE = "cuda:0"
DEFAULT_DTYPE = "bfloat16"
DEFAULT_PROFILES_DIR = Path.home() / "qwen3-tts" / "profiles"
DEFAULT_SILENCE_MS = 400

# ---------------------------------------------------------------------------
# Module-level model + voice-prompt cache
# ---------------------------------------------------------------------------

_model: "Qwen3TTSModel | None" = None
_voice_prompts: dict[str, list] = {}  # voice_name -> prompt_items


def get_model() -> "Qwen3TTSModel":
    """Lazy-load the TTS model (once per process).

    Reads TTS_MODEL_ID, TTS_DEVICE, TTS_DTYPE from the environment.
    """
    global _model
    if _model is not None:
        return _model

    import torch
    from qwen_tts import Qwen3TTSModel

    model_id = os.environ.get("TTS_MODEL_ID", DEFAULT_MODEL_ID)
    device = os.environ.get("TTS_DEVICE", DEFAULT_DEVICE)
    dtype_str = os.environ.get("TTS_DTYPE", DEFAULT_DTYPE).lower()
    dtype = {"bfloat16": torch.bfloat16, "bf16": torch.bfloat16,
             "float16": torch.float16, "fp16": torch.float16,
             "float32": torch.float32, "fp32": torch.float32}.get(dtype_str, torch.bfloat16)

    try:
        import flash_attn  # noqa: F401
        attn_impl = "flash_attention_2"
    except ImportError:
        attn_impl = "sdpa"
        log.info("flash-attn not installed, using sdpa attention (slightly slower)")

    log.info("Loading TTS model %s on %s (%s, attn=%s)…", model_id, device, dtype_str, attn_impl)
    _model = Qwen3TTSModel.from_pretrained(
        model_id,
        device_map=device,
        dtype=dtype,
        attn_implementation=attn_impl,
    )
    log.info("TTS model ready.")
    return _model


# ---------------------------------------------------------------------------
# Voice profile helpers
# ---------------------------------------------------------------------------

def _profiles_dir() -> Path:
    return Path(os.environ.get("TTS_PROFILES_DIR", str(DEFAULT_PROFILES_DIR)))


def _profile_dir(voice_name: str) -> Path:
    return _profiles_dir() / voice_name


def _load_prompt_from_disk(voice_name: str) -> tuple[list, str]:
    """Load a precomputed voice prompt and its language from disk."""
    import torch

    profile = _profile_dir(voice_name)
    pt_path = profile / "prompt.pt"
    meta_path = profile / "meta.json"

    if not pt_path.exists():
        raise FileNotFoundError(
            f"Voice profile '{voice_name}' not found at {profile}. "
            "Run: parseltongue speak register-voice <name> --ref-audio <file>"
        )

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    prompt_items = torch.load(str(pt_path), map_location="cpu", weights_only=False)
    return prompt_items, meta.get("language", "English")


def _get_voice_prompt(voice_name: str) -> tuple[list, str]:
    """Return (prompt_items, language) — from memory cache or disk."""
    if voice_name not in _voice_prompts:
        items, language = _load_prompt_from_disk(voice_name)
        _voice_prompts[voice_name] = (items, language)
    return _voice_prompts[voice_name]


# ---------------------------------------------------------------------------
# Voice registration
# ---------------------------------------------------------------------------

def register_voice(
    voice_name: str,
    ref_audio_path: str | Path,
    ref_text: str = "",
    language: str = "English",
    overwrite: bool = False,
) -> Path:
    """Register a voice profile by precomputing speaker embeddings.

    Loads the TTS model (first call takes ~30 s), processes the reference
    audio clip with ``create_voice_clone_prompt``, and saves the embeddings
    to ``~/qwen3-tts/profiles/<voice_name>/prompt.pt``.  Subsequent synthesis
    calls reuse these embeddings without reprocessing the audio.

    Args:
        voice_name:     Short identifier, e.g. ``"myvoice"``.
        ref_audio_path: Path to reference WAV / MP3 / M4A.
                        Recommended: 10–20 s of clean mono speech at ≥24 kHz.
        ref_text:       Verbatim transcript of the reference audio.
                        Strongly recommended — omitting it reduces clone quality.
        language:       Language spoken in the reference clip (e.g. ``"English"``).
        overwrite:      Replace an existing profile with the same name.

    Returns:
        Path to the profile directory.
    """
    import torch

    profile_dir = _profile_dir(voice_name)

    if profile_dir.exists():
        if not overwrite:
            log.info("Voice profile '%s' already exists — skipping (use --overwrite to redo)", voice_name)
            return profile_dir
        log.info("Overwriting voice profile '%s'", voice_name)
        shutil.rmtree(profile_dir)

    ref_path = Path(ref_audio_path).resolve()
    if not ref_path.exists():
        raise FileNotFoundError(f"Reference audio not found: {ref_path}")

    ref_wav, ref_sr = sf.read(str(ref_path), dtype="float32", always_2d=False)
    if ref_wav.ndim > 1:
        ref_wav = ref_wav.mean(axis=-1)

    model = get_model()
    log.info("Computing voice prompt for '%s'…", voice_name)
    prompt_items = model.create_voice_clone_prompt(
        ref_audio=(ref_wav, ref_sr),
        ref_text=ref_text.strip() or None,
        x_vector_only_mode=(not ref_text.strip()),
    )

    profile_dir.mkdir(parents=True, exist_ok=True)

    dest_audio = profile_dir / f"reference{ref_path.suffix}"
    shutil.copy2(ref_path, dest_audio)

    meta = {
        "name": voice_name,
        "language": language,
        "ref_text": ref_text,
        "ref_audio": dest_audio.name,
    }
    (profile_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    torch.save(prompt_items, str(profile_dir / "prompt.pt"))

    _voice_prompts[voice_name] = (prompt_items, language)
    log.info("Voice profile '%s' saved to %s", voice_name, profile_dir)
    return profile_dir


# ---------------------------------------------------------------------------
# Low-level synthesis
# ---------------------------------------------------------------------------

def _synthesize_chunk(
    text: str,
    voice_name: str,
    out_path: Path,
    language: str | None = None,
    instruct: str | None = None,
) -> None:
    """Synthesize one text chunk and write it as a WAV file.

    If *instruct* is provided it is forwarded to ``generate_voice_clone`` as a
    speaking-style directive (e.g. ``"Emotion: nostalgic, Pacing: measured"``).
    The 1.7B-Base model supports this; leaving it ``None`` uses the model's
    default neutral style.
    """
    prompt_items, default_language = _get_voice_prompt(voice_name)
    resolved_language = language or default_language

    model = get_model()
    kwargs: dict = {}
    if instruct:
        kwargs["instruct"] = instruct

    wavs, sr = model.generate_voice_clone(
        text=text,
        language=resolved_language,
        voice_clone_prompt=prompt_items,
        **kwargs,
    )

    audio: np.ndarray = np.asarray(wavs[0], dtype=np.float32)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_path), audio, sr, subtype="PCM_16")


# ---------------------------------------------------------------------------
# Chapter-level synthesis
# ---------------------------------------------------------------------------

def synthesize_chapter(
    story_id: str,
    chapter_index: int,
    voice_name: str,
    base_dir: str,
    language: str | None = None,
    silence_ms: int = DEFAULT_SILENCE_MS,
    overwrite: bool = False,
    use_instruct: bool = True,
    on_chunk: ChunkCallback | None = None,
) -> str:
    """Synthesize all chunks of one directed chapter and stitch them together.

    Reads ``<base_dir>/<story_id>/directed/<NN>.json``, renders each chunk in
    ``chunk_index`` order, saves individual WAVs under
    ``<base_dir>/<story_id>/audio/<NN>/``, then stitches them into
    ``<base_dir>/<story_id>/audio/<NN>.wav``.

    Already-rendered chunk WAVs are skipped unless ``overwrite=True``, making
    interrupted runs fully resumable.

    Args:
        story_id:       Work ID (same as used by the scraper/director).
        chapter_index:  1-based chapter number.
        voice_name:     Name of a registered voice profile.
        base_dir:       Root data directory (contains ``<story_id>/``).
        language:       Override the language stored in the voice profile.
        silence_ms:     Milliseconds of silence inserted between chunks.
        overwrite:      Re-synthesize chunks that already have WAV files.
        use_instruct:   If ``True`` (default), forward the ``instruct`` field
                        from each directed chunk to the model as a speaking-
                        style directive.  Set to ``False`` to ignore directions
                        and use the model's neutral default style.
        on_chunk:       Optional callback ``(chunk_index, total, text, instruct)`` called
                        after each chunk is synthesised.

    Returns:
        Path to the stitched chapter WAV file.
    """
    story_dir = Path(base_dir) / sanitize_id(story_id)
    directed_path = story_dir / "directed" / f"{chapter_index:02d}.json"

    if not directed_path.exists():
        raise FileNotFoundError(f"Directed JSON not found: {directed_path}")

    chunks: list[dict] = json.loads(directed_path.read_text(encoding="utf-8"))
    chunks = sorted(chunks, key=lambda c: c["chunk_index"])
    total = len(chunks)

    chapter_dir = story_dir / "audio" / f"{chapter_index:02d}"
    chapter_dir.mkdir(parents=True, exist_ok=True)

    # Warm up the voice prompt before the loop so the first chunk isn't slow.
    _get_voice_prompt(voice_name)

    chunk_paths: list[Path] = []

    for chunk in chunks:
        idx = chunk["chunk_index"]
        text = chunk["text"]
        chunk_path = chapter_dir / f"{idx:04d}.wav"
        chunk_paths.append(chunk_path)

        instruct = chunk.get("instruct") if use_instruct else None

        if chunk_path.exists() and not overwrite:
            log.debug("Chunk %04d already exists – skipping", idx)
            if on_chunk:
                on_chunk(idx, total, text, instruct)
            continue

        log.debug(
            "Synthesising chunk %04d/%04d (%d words)%s",
            idx, total, len(text.split()),
            f" [instruct: {instruct[:60]}…]" if instruct else "",
        )
        _synthesize_chunk(text, voice_name, chunk_path, language=language, instruct=instruct)

        if on_chunk:
            on_chunk(idx, total, text, instruct)

    out_path = story_dir / "audio" / f"{chapter_index:02d}.wav"
    stitch_wav_files(chunk_paths, out_path, silence_ms=silence_ms)
    log.info("Chapter %02d → %d chunks → %s", chapter_index, total, out_path)
    return str(out_path)


# ---------------------------------------------------------------------------
# Work-level synthesis
# ---------------------------------------------------------------------------

def synthesize_work(
    story_id: str,
    voice_name: str,
    base_dir: str,
    chapters: list[int] | None = None,
    language: str | None = None,
    silence_ms: int = DEFAULT_SILENCE_MS,
    overwrite: bool = False,
    use_instruct: bool = True,
    on_chunk: ChunkCallback | None = None,
) -> dict[int, str]:
    """Synthesize all (or selected) chapters of a work.

    Args:
        story_id:     Work ID.
        voice_name:   Name of a registered voice profile.
        base_dir:     Root data directory.
        chapters:     If given, only process these 1-based chapter indices.
        language:     Override the language stored in the voice profile.
        silence_ms:   Silence between chunks in milliseconds.
        overwrite:    Re-synthesize existing chunk WAVs.
        use_instruct: Forward the ``instruct`` field from each chunk to the
                      model as a speaking-style directive.
        on_chunk:     Optional callback per chunk.

    Returns:
        Mapping of chapter_index → path of the stitched WAV file.
    """
    story_dir = Path(base_dir) / sanitize_id(story_id)
    directed_root = story_dir / "directed"

    if not directed_root.is_dir():
        raise FileNotFoundError(
            f"No directed/ folder found for story {story_id} in {base_dir}"
        )

    available = sorted(
        int(p.stem) for p in directed_root.glob("*.json") if p.stem.isdigit()
    )
    if not available:
        raise FileNotFoundError(f"No directed JSON files found in {directed_root}")

    target = [ch for ch in available if ch in chapters] if chapters else available
    results: dict[int, str] = {}

    for ch_idx in target:
        if has_chapter_audio(story_dir, ch_idx) and not overwrite:
            log.info(
                "Chapter %02d already synthesised – skipping (use --overwrite to redo)",
                ch_idx,
            )
            results[ch_idx] = str(chapter_audio_path(story_dir, ch_idx))
            continue

        log.info("Synthesising chapter %02d", ch_idx)
        out = synthesize_chapter(
            story_id=story_id,
            chapter_index=ch_idx,
            voice_name=voice_name,
            base_dir=base_dir,
            language=language,
            silence_ms=silence_ms,
            overwrite=overwrite,
            use_instruct=use_instruct,
            on_chunk=on_chunk,
        )
        results[ch_idx] = out

    return results


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------

def audio_dir(story_dir: str | Path) -> Path:
    return Path(story_dir) / "audio"


def chapter_audio_path(story_dir: str | Path, chapter_index: int) -> Path:
    return audio_dir(story_dir) / f"{chapter_index:02d}.wav"


def has_chapter_audio(story_dir: str | Path, chapter_index: int) -> bool:
    return chapter_audio_path(story_dir, chapter_index).exists()
