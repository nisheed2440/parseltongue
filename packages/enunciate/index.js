import fse from "fs-extra";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROMPT_PATH = path.join(__dirname, "voice-direction-prompt.md");

const DEFAULT_MODEL = "qwen3:8b";
const DEFAULT_BASE_URL = "http://localhost:11434";

let _systemPrompt = null;

function getSystemPrompt() {
  if (!_systemPrompt) {
    _systemPrompt = fse.readFileSync(PROMPT_PATH, "utf-8");
  }
  return _systemPrompt;
}

/**
 * Extract a leading markdown heading from chapter text.
 * Returns { heading, body } where heading may be null.
 */
function extractHeading(text) {
  const match = text.match(/^(#{1,6})\s+(.+)/);
  if (!match) return { heading: null, body: text };
  const title = match[2].trim();
  const body = text.slice(match[0].length).replace(/^\n+/, "");
  return { heading: title, body };
}

/**
 * Inject a chapter-title segment right after the VOICE: line.
 * If the LLM already included it, skip injection.
 */
function ensureChapterHeading(output, heading) {
  if (!heading) return output;

  const titleSegment =
    `\n\n---\n\nINSTRUCT: Read the chapter title in a clear, crisp, announcing tone. Pause after.\nTEXT: ${heading}`;

  const lines = output.split("\n");
  const firstLineLC = lines[0]?.toLowerCase() ?? "";

  if (!firstLineLC.startsWith("voice:")) return output;

  const headingNorm = heading.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  const alreadyPresent = output
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .includes(headingNorm);
  if (alreadyPresent) return output;

  const voiceLine = lines[0];
  const rest = lines.slice(1).join("\n").replace(/^\n*/, "");
  return `${voiceLine}${titleSegment}\n\n${rest}`;
}

/**
 * Enunciate a chapter's markdown text using a local Ollama model.
 *
 * @param {string} chapterText - Raw chapter markdown content
 * @param {object} [opts]
 * @param {string} [opts.model]   - Ollama model name (default: qwen3:8b)
 * @param {string} [opts.baseUrl] - Ollama server URL (default: http://localhost:11434)
 * @returns {Promise<string>} Voice-directed markdown
 */
export async function enunciateChapter(chapterText, opts = {}) {
  const modelName = opts.model || DEFAULT_MODEL;
  const baseUrl = (opts.baseUrl || process.env.OLLAMA_HOST || DEFAULT_BASE_URL).replace(/\/+$/, "");

  const { heading, body } = extractHeading(chapterText.trim());

  const res = await fetch(`${baseUrl}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model: modelName,
      messages: [
        { role: "system", content: getSystemPrompt() },
        { role: "user", content: body },
      ],
      stream: false,
    }),
  });

  if (!res.ok) {
    const errBody = await res.text().catch(() => "");
    throw new Error(`Ollama request failed (${res.status}): ${errBody}`);
  }

  const data = await res.json();
  let output = data.message.content;

  output = output.replace(/^```\w*\n?/, "").replace(/\n?```\s*$/, "");

  return ensureChapterHeading(output.trim(), heading);
}
