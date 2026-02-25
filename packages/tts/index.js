import { spawn } from "child_process";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SYNTHESIZE_SCRIPT = path.join(__dirname, "synthesize.py");

const DEFAULT_SERVER_URL = "http://localhost:7860";

// ---------------------------------------------------------------------------
// Parser — turns enunciated markdown into structured segments
// ---------------------------------------------------------------------------

/**
 * Parse an enunciated chapter (VOICE / INSTRUCT / TEXT blocks separated by ---).
 *
 * @param {string} markdown - Enunciated chapter markdown
 * @returns {{ voice: string|null, segments: Array<{instruct: string, text: string}> }}
 */
export function parseEnunciatedChapter(markdown) {
  const blocks = markdown
    .split(/\n---\s*\n/)
    .map((b) => b.trim())
    .filter(Boolean);

  let voice = null;
  const segments = [];

  for (const block of blocks) {
    const voiceMatch = block.match(/^VOICE:\s*(.+)/i);
    if (voiceMatch && !voice) {
      voice = voiceMatch[1].trim();
      continue;
    }

    const instructMatch = block.match(/^INSTRUCT:\s*(.+)/im);
    const textMatch = block.match(/^TEXT:\s*([\s\S]+)/im);

    if (textMatch) {
      let text = textMatch[1].trim();
      const nextInstruct = text.indexOf("\nINSTRUCT:");
      if (nextInstruct !== -1) text = text.slice(0, nextInstruct).trim();

      segments.push({
        instruct: instructMatch ? instructMatch[1].trim() : "",
        text,
      });
    }
  }

  return { voice, segments };
}

// ---------------------------------------------------------------------------
// One-shot Python synthesiser
// ---------------------------------------------------------------------------

/**
 * Synthesise one or more chapters by spawning the Python script.
 * Models are loaded, chapters processed, WAVs written, then the
 * process exits — freeing the GPU for other work.
 *
 * @param {Array<{voice: string, segments: Array, output: string}>} chapters
 * @param {object} [opts]
 * @param {string} [opts.mode]     - "clone" or "design" (default "design")
 * @param {string} [opts.device]   - torch device (default "cuda:0")
 * @param {string} [opts.language] - target language (default "English")
 * @param {string} [opts.python]   - python binary (default "python")
 * @returns {Promise<{ok: boolean, processed: number, results: Array}>}
 */
export function synthesizeChapters(chapters, opts = {}) {
  const manifest = {
    mode: opts.mode || "design",
    device: opts.device || "cuda:0",
    language: opts.language || "English",
    chapters,
  };

  const python = opts.python || process.env.PYTHON_BIN || "python";

  return new Promise((resolve, reject) => {
    const child = spawn(python, [SYNTHESIZE_SCRIPT], {
      stdio: ["pipe", "pipe", "inherit"], // stdin=pipe, stdout=pipe, stderr→terminal
    });

    let stdout = "";
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString();
    });

    child.on("error", (err) => {
      reject(new Error(`Failed to spawn Python: ${err.message}`));
    });

    child.on("close", (code) => {
      if (code !== 0) {
        reject(new Error(`synthesize.py exited with code ${code}`));
        return;
      }
      try {
        resolve(JSON.parse(stdout));
      } catch {
        reject(new Error(`Bad JSON from synthesize.py: ${stdout.slice(0, 200)}`));
      }
    });

    child.stdin.write(JSON.stringify(manifest));
    child.stdin.end();
  });
}

/**
 * Convenience: synthesise a single chapter from its enunciated markdown.
 *
 * @param {string} enunciatedMarkdown
 * @param {string} outputPath - Where to write the WAV file
 * @param {object} [opts]     - Same as synthesizeChapters opts
 * @returns {Promise<{ok: boolean, processed: number, results: Array}>}
 */
export function synthesizeChapter(enunciatedMarkdown, outputPath, opts = {}) {
  const { voice, segments } = parseEnunciatedChapter(enunciatedMarkdown);

  if (!voice) throw new Error("No VOICE: block found in enunciated chapter");
  if (!segments.length)
    throw new Error("No TEXT segments found in enunciated chapter");

  return synthesizeChapters(
    [{ voice, segments, output: outputPath }],
    opts
  );
}

// ---------------------------------------------------------------------------
// Warm server (Gradio) — keeps models loaded between runs
// ---------------------------------------------------------------------------

/**
 * Check whether the TTS Gradio server is reachable.
 *
 * @param {string} [serverUrl] - Server base URL
 * @returns {Promise<boolean>}
 */
export async function checkServer(serverUrl) {
  const url = serverUrl || process.env.TTS_SERVER_URL || DEFAULT_SERVER_URL;
  try {
    const { Client } = await import("@gradio/client");
    const app = await Client.connect(url);
    const result = await app.predict("/health");
    return result?.data?.[0] === "ok";
  } catch {
    return false;
  }
}

/**
 * Synthesise chapters via the warm Gradio TTS server.
 *
 * Falls back to the one-shot Python spawner if the server is unreachable
 * (unless opts.noFallback is set).
 *
 * @param {Array<{voice: string, segments: Array, output: string}>} chapters
 * @param {object} [opts]
 * @param {string} [opts.mode]       - "clone" or "design" (default "design")
 * @param {string} [opts.device]     - torch device (only used by fallback)
 * @param {string} [opts.language]   - target language (default "English")
 * @param {string} [opts.python]     - python binary (only used by fallback)
 * @param {string} [opts.serverUrl]  - Gradio server URL
 * @param {boolean} [opts.noFallback] - if true, don't fall back to one-shot
 * @returns {Promise<{ok: boolean, processed: number, results: Array}>}
 */
export async function synthesizeViaServer(chapters, opts = {}) {
  const serverUrl =
    opts.serverUrl || process.env.TTS_SERVER_URL || DEFAULT_SERVER_URL;

  const manifest = {
    mode: opts.mode || "design",
    language: opts.language || "English",
    chapters,
  };

  try {
    const { Client } = await import("@gradio/client");
    const app = await Client.connect(serverUrl);

    const result = await app.predict("/synthesize", {
      manifest_json: JSON.stringify(manifest),
    });

    const data = result?.data?.[0];
    if (typeof data === "string") return JSON.parse(data);
    return data;
  } catch (err) {
    if (opts.noFallback) {
      throw new Error(
        `TTS server at ${serverUrl} is not reachable: ${err.message}`
      );
    }

    console.error(
      `[tts] Server at ${serverUrl} not reachable, falling back to one-shot Python …`
    );
    return synthesizeChapters(chapters, opts);
  }
}
