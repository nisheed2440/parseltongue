"""
Warm Gradio TTS server for parseltongue.

Loads Qwen3-TTS models once at startup and keeps them resident on the GPU,
serving synthesis requests from the Node.js CLI over HTTP.  This avoids the
~30 s model-load penalty on every run that the one-shot synthesize.py incurs.

Start:
    python packages/tts/server.py --port 7860 --device cuda:0 --mode design

The server exposes two Gradio API endpoints:

  /synthesize   — accepts a full chapter manifest (same JSON shape the CLI
                  already builds) and returns a results JSON string.

  /health       — returns "ok" (used by the CLI to auto-detect server).
"""

import argparse
import hashlib
import json
import os
import sys
import time

import gradio as gr
import numpy as np
import soundfile as sf
import torch

from synthesize import (
    INTER_SEGMENT_SILENCE_S,
    concatenate_segments,
    progress,
)

# ---------------------------------------------------------------------------
# Segment-level cache
# ---------------------------------------------------------------------------

DEFAULT_CACHE_DIR = os.path.join(
    os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")),
    "parseltongue",
    "tts",
)


def _cache_key(text: str, instruct: str, mode: str, language: str) -> str:
    blob = f"{text}|{instruct}|{mode}|{language}"
    return hashlib.md5(blob.encode()).hexdigest()


def _cache_get(cache_dir: str, key: str) -> np.ndarray | None:
    p = os.path.join(cache_dir, f"{key}.wav")
    if not os.path.isfile(p):
        return None
    data, _ = sf.read(p, dtype="float32")
    return data


def _cache_put(cache_dir: str, key: str, audio: np.ndarray, sr: int):
    os.makedirs(cache_dir, exist_ok=True)
    sf.write(os.path.join(cache_dir, f"{key}.wav"), audio, sr, format="WAV")


# ---------------------------------------------------------------------------
# Per-segment synthesis with retry + caching
# ---------------------------------------------------------------------------

MAX_RETRIES = 3
RETRY_DELAYS = [2, 4, 8]


def _synthesize_segment_design(
    design_model, voice: str, seg: dict, language: str, cache_dir: str
) -> tuple[np.ndarray, int]:
    """Synthesise one segment in design mode with caching and retry."""
    text = seg["text"]
    instruct = seg.get("instruct", "")
    key = _cache_key(text, f"{voice}. {instruct}" if instruct else voice, "design", language)

    cached = _cache_get(cache_dir, key)
    if cached is not None:
        return cached, design_model.config.sampling_rate if hasattr(design_model, "config") else 24000

    combined = f"{voice}. {instruct}" if instruct else voice
    last_err = None

    for attempt in range(MAX_RETRIES):
        try:
            w, sr = design_model.generate_voice_design(
                text=text, language=language, instruct=combined
            )
            _cache_put(cache_dir, key, w[0], sr)
            return w[0], sr
        except Exception as e:
            last_err = e
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAYS[attempt]
                progress(f"    retry {attempt + 2}/{MAX_RETRIES} in {delay}s ({e})")
                time.sleep(delay)

    raise RuntimeError(f"Segment failed after {MAX_RETRIES} attempts: {last_err}")


def _synthesize_segment_clone(
    clone_model, prompt, seg: dict, language: str, cache_dir: str
) -> tuple[np.ndarray, int]:
    """Synthesise one segment in clone mode with caching and retry."""
    text = seg["text"]
    key = _cache_key(text, "", "clone", language)

    cached = _cache_get(cache_dir, key)
    if cached is not None:
        return cached, 24000

    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            w, sr = clone_model.generate_voice_clone(
                text=text, language=language, voice_clone_prompt=prompt
            )
            _cache_put(cache_dir, key, w[0], sr)
            return w[0], sr
        except Exception as e:
            last_err = e
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAYS[attempt]
                progress(f"    retry {attempt + 2}/{MAX_RETRIES} in {delay}s ({e})")
                time.sleep(delay)

    raise RuntimeError(f"Segment failed after {MAX_RETRIES} attempts: {last_err}")


# ---------------------------------------------------------------------------
# Chapter-level synthesis
# ---------------------------------------------------------------------------


def synthesize_chapter_design_cached(design_model, chapter, language, cache_dir):
    voice = chapter["voice"]
    segments = chapter["segments"]
    clips = []
    sr = None

    for i, seg in enumerate(segments):
        progress(f"  segment {i + 1}/{len(segments)} (design)")
        clip, seg_sr = _synthesize_segment_design(
            design_model, voice, seg, language, cache_dir
        )
        if sr is None:
            sr = seg_sr
        clips.append(clip)

    return concatenate_segments(clips, sr), sr


def synthesize_chapter_clone_cached(
    design_model, clone_model, chapter, language, cache_dir
):
    voice = chapter["voice"]
    segments = chapter["segments"]

    ref_text = segments[0]["text"]
    progress("  generating reference audio for clone prompt …")
    wavs_ref, sr = design_model.generate_voice_design(
        text=ref_text, language=language, instruct=voice
    )
    prompt = clone_model.create_voice_clone_prompt(
        ref_audio=(wavs_ref[0], sr), ref_text=ref_text
    )

    clips = []
    for i, seg in enumerate(segments):
        progress(f"  segment {i + 1}/{len(segments)} (clone)")
        clip, seg_sr = _synthesize_segment_clone(
            clone_model, prompt, seg, language, cache_dir
        )
        if sr is None:
            sr = seg_sr
        clips.append(clip)

    return concatenate_segments(clips, sr), sr


# ---------------------------------------------------------------------------
# Gradio app factory
# ---------------------------------------------------------------------------


def build_app(design_model, clone_model, mode, language_default, cache_dir):
    def handle_synthesize(manifest_json: str) -> str:
        try:
            manifest = json.loads(manifest_json)
        except json.JSONDecodeError as e:
            return json.dumps({"ok": False, "error": f"Invalid JSON: {e}"})

        req_mode = manifest.get("mode", mode)
        language = manifest.get("language", language_default)
        chapters = manifest.get("chapters", [])

        if not chapters:
            return json.dumps({"ok": True, "processed": 0, "results": []})

        results = []
        for ci, chapter in enumerate(chapters):
            out_path = chapter["output"]
            n_segs = len(chapter.get("segments", []))
            progress(f"Chapter {ci + 1}/{len(chapters)}: {n_segs} segments → {out_path}")

            try:
                if req_mode == "clone" and clone_model is not None:
                    audio, sr = synthesize_chapter_clone_cached(
                        design_model, clone_model, chapter, language, cache_dir
                    )
                else:
                    audio, sr = synthesize_chapter_design_cached(
                        design_model, chapter, language, cache_dir
                    )

                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                sf.write(out_path, audio, sr, format="WAV")
                size_mb = len(audio) * audio.itemsize / 1_048_576
                progress(f"  ✓ {size_mb:.1f} MB written\n")
                results.append({"output": out_path, "ok": True})

            except Exception as e:
                progress(f"  ✗ error: {e}\n")
                results.append({"output": out_path, "ok": False, "error": str(e)})

        return json.dumps({"ok": True, "processed": len(results), "results": results})

    def handle_health() -> str:
        return "ok"

    with gr.Blocks() as app:
        gr.Markdown("## parseltongue TTS server")
        gr.Markdown(f"Mode: **{mode}** | Cache: `{cache_dir}`")

        with gr.Tab("Synthesize"):
            manifest_input = gr.Textbox(
                label="Manifest JSON",
                lines=10,
                placeholder='{"mode":"design","language":"English","chapters":[...]}',
            )
            result_output = gr.Textbox(label="Result JSON", lines=6)
            gr.Button("Synthesize").click(
                handle_synthesize,
                inputs=manifest_input,
                outputs=result_output,
                api_name="synthesize",
            )

        with gr.Tab("Health"):
            health_output = gr.Textbox(label="Status")
            gr.Button("Check").click(
                handle_health, outputs=health_output, api_name="health"
            )

        app.queue()

    return app, handle_synthesize, handle_health


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="parseltongue warm TTS server (Gradio)"
    )
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--device", type=str, default="cuda:0")
    parser.add_argument(
        "--mode",
        type=str,
        default="design",
        choices=["design", "clone"],
        help="'design' loads VoiceDesign only; 'clone' loads VoiceDesign + Base",
    )
    parser.add_argument("--language", type=str, default="English")
    parser.add_argument("--cache-dir", type=str, default=DEFAULT_CACHE_DIR)
    args = parser.parse_args()

    os.makedirs(args.cache_dir, exist_ok=True)

    from qwen_tts import Qwen3TTSModel

    dtype = torch.bfloat16
    design_model = None
    clone_model = None

    progress(f"[server] Loading VoiceDesign model on {args.device} …")
    design_model = Qwen3TTSModel.from_pretrained(
        "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
        device_map=args.device,
        dtype=dtype,
    )

    if args.mode == "clone":
        progress(f"[server] Loading Base (clone) model on {args.device} …")
        clone_model = Qwen3TTSModel.from_pretrained(
            "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
            device_map=args.device,
            dtype=dtype,
        )

    progress("[server] Models loaded.\n")

    app, _, _ = build_app(
        design_model, clone_model, args.mode, args.language, args.cache_dir
    )

    app.launch(
        server_name=args.host,
        server_port=args.port,
        share=False,
    )


if __name__ == "__main__":
    main()
