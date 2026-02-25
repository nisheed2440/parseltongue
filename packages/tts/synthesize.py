"""
One-shot Qwen3-TTS synthesiser for parseltongue.

Reads a JSON manifest from stdin, loads models, processes every chapter,
writes WAV files, then exits — freeing the GPU for other work.

Manifest format (written by the Node.js CLI):
{
  "mode": "design",
  "device": "cuda:0",
  "language": "English",
  "chapters": [
    {
      "voice": "A warm, expressive male narrator …",
      "segments": [{"instruct": "…", "text": "…"}, …],
      "output": "/absolute/path/to/01.wav"
    }
  ]
}

Usage:
    echo '<json>' | python packages/tts/synthesize.py
    python packages/tts/synthesize.py < manifest.json
    python packages/tts/synthesize.py --manifest manifest.json
"""

import argparse
import io
import json
import sys

import numpy as np
import soundfile as sf
import torch

INTER_SEGMENT_SILENCE_S = 0.7


def concatenate_segments(clips: list[np.ndarray], sr: int) -> np.ndarray:
    silence = np.zeros(int(sr * INTER_SEGMENT_SILENCE_S), dtype=np.float32)
    parts = []
    for i, clip in enumerate(clips):
        parts.append(clip)
        if i < len(clips) - 1:
            parts.append(silence)
    return np.concatenate(parts)


def synthesize_chapter_clone(design_model, clone_model, chapter, language):
    voice = chapter["voice"]
    segments = chapter["segments"]

    ref_text = segments[0]["text"]
    wavs_ref, sr = design_model.generate_voice_design(
        text=ref_text,
        language=language,
        instruct=voice,
    )
    prompt = clone_model.create_voice_clone_prompt(
        ref_audio=(wavs_ref[0], sr),
        ref_text=ref_text,
    )

    clips = []
    for i, seg in enumerate(segments):
        progress(f"  segment {i + 1}/{len(segments)} (clone)")
        w, sr = clone_model.generate_voice_clone(
            text=seg["text"],
            language=language,
            voice_clone_prompt=prompt,
        )
        clips.append(w[0])

    return concatenate_segments(clips, sr), sr


def synthesize_chapter_design(design_model, chapter, language):
    voice = chapter["voice"]
    segments = chapter["segments"]

    clips = []
    sr = None
    for i, seg in enumerate(segments):
        combined = f"{voice}. {seg['instruct']}" if seg.get("instruct") else voice
        progress(f"  segment {i + 1}/{len(segments)} (design)")
        w, seg_sr = design_model.generate_voice_design(
            text=seg["text"],
            language=language,
            instruct=combined,
        )
        if sr is None:
            sr = seg_sr
        clips.append(w[0])

    return concatenate_segments(clips, sr), sr


def progress(msg):
    """Print progress to stderr so stdout stays clean for the result JSON."""
    print(msg, file=sys.stderr, flush=True)


def main():
    parser = argparse.ArgumentParser(description="parseltongue one-shot TTS synthesiser")
    parser.add_argument(
        "--manifest",
        type=str,
        default=None,
        help="Path to JSON manifest file (reads stdin if omitted)",
    )
    args = parser.parse_args()

    if args.manifest:
        with open(args.manifest) as f:
            manifest = json.load(f)
    else:
        manifest = json.load(sys.stdin)

    mode = manifest.get("mode", "design")
    device = manifest.get("device", "cuda:0")
    language = manifest.get("language", "English")
    chapters = manifest.get("chapters", [])

    if not chapters:
        progress("No chapters in manifest — nothing to do.")
        json.dump({"ok": True, "processed": 0}, sys.stdout)
        return

    from qwen_tts import Qwen3TTSModel

    dtype = torch.bfloat16
    design_model = None
    clone_model = None

    need_design = mode in ("design", "clone")  # clone also needs design for the reference
    need_clone = mode == "clone"

    if need_design:
        progress(f"[tts] Loading VoiceDesign model on {device} …")
        design_model = Qwen3TTSModel.from_pretrained(
            "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
            device_map=device,
            dtype=dtype,
        )

    if need_clone:
        progress(f"[tts] Loading Base (clone) model on {device} …")
        clone_model = Qwen3TTSModel.from_pretrained(
            "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
            device_map=device,
            dtype=dtype,
        )

    progress(f"[tts] Models loaded. Processing {len(chapters)} chapter(s) …\n")

    results = []

    for ci, chapter in enumerate(chapters):
        out_path = chapter["output"]
        n_segs = len(chapter.get("segments", []))
        progress(f"Chapter {ci + 1}/{len(chapters)}: {n_segs} segments → {out_path}")

        try:
            if mode == "clone" and clone_model is not None:
                audio, sr = synthesize_chapter_clone(
                    design_model, clone_model, chapter, language
                )
            else:
                audio, sr = synthesize_chapter_design(design_model, chapter, language)

            sf.write(out_path, audio, sr, format="WAV")
            size_mb = len(audio) * audio.itemsize / 1_048_576
            progress(f"  ✓ {size_mb:.1f} MB written\n")
            results.append({"output": out_path, "ok": True})

        except Exception as e:
            progress(f"  ✗ error: {e}\n")
            results.append({"output": out_path, "ok": False, "error": str(e)})

    # Clean up GPU memory
    del design_model, clone_model
    torch.cuda.empty_cache()

    progress("[tts] Done — GPU memory released.")
    json.dump({"ok": True, "processed": len(results), "results": results}, sys.stdout)


if __name__ == "__main__":
    main()
