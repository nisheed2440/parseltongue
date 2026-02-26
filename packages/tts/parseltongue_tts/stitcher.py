"""WAV file stitching using soundfile + numpy.

Concatenates a list of WAV files into a single output file, inserting a
configurable silence gap between each segment.  All input files must share
the same sample rate and channel count (guaranteed when they are produced by
the same TTS model instance).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf
from parseltongue_logger import get_logger

log = get_logger(__name__)


def _silence_samples(duration_ms: int, sample_rate: int, channels: int) -> np.ndarray:
    n_samples = int(sample_rate * duration_ms / 1000)
    return np.zeros((n_samples, channels) if channels > 1 else (n_samples,), dtype=np.float32)


def stitch_wav_files(
    paths: list[Path],
    out_path: Path,
    silence_ms: int = 400,
) -> Path:
    """Concatenate WAV files in *paths* into a single *out_path* WAV.

    A silence gap of *silence_ms* milliseconds is inserted between each pair
    of adjacent segments.  Missing or zero-length input files are skipped with
    a warning so that a single bad chunk does not abort the whole chapter.

    Args:
        paths:       Ordered list of per-chunk WAV file paths.
        out_path:    Destination WAV path (created / overwritten).
        silence_ms:  Gap between chunks in milliseconds.

    Returns:
        *out_path* (for chaining).
    """
    segments: list[np.ndarray] = []
    sample_rate: int | None = None
    channels: int | None = None

    valid_paths = [p for p in paths if p.exists() and p.stat().st_size > 0]
    skipped = len(paths) - len(valid_paths)
    if skipped:
        log.warning("%d chunk file(s) missing or empty – they will be skipped", skipped)

    if not valid_paths:
        raise RuntimeError("No valid chunk WAV files to stitch.")

    for path in valid_paths:
        data, sr = sf.read(str(path), dtype="float32", always_2d=False)
        ch = 1 if data.ndim == 1 else data.shape[1]

        if sample_rate is None:
            sample_rate = sr
            channels = ch
        elif sr != sample_rate:
            raise ValueError(
                f"Sample-rate mismatch: expected {sample_rate} Hz, "
                f"got {sr} Hz in {path}"
            )

        segments.append(data)

    assert sample_rate is not None
    assert channels is not None

    silence = _silence_samples(silence_ms, sample_rate, channels)

    combined_parts: list[np.ndarray] = []
    for i, seg in enumerate(segments):
        combined_parts.append(seg)
        if i < len(segments) - 1:
            combined_parts.append(silence)

    combined = np.concatenate(combined_parts, axis=0)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_path), combined, sample_rate, subtype="PCM_16")
    log.debug(
        "Stitched %d segments → %s (%.1f s)",
        len(segments),
        out_path,
        len(combined) / sample_rate,
    )
    return out_path
