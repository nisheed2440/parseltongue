// ---------------------------------------------------------------------------
// Text helpers
// ---------------------------------------------------------------------------

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
 * If the heading text already appears in the output, skip injection.
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

// ---------------------------------------------------------------------------
// Markdown stripping
// ---------------------------------------------------------------------------

/**
 * Strip markdown formatting from text so it is clean for TTS.
 * Removes headings (#), bold/italic markers, blockquotes, and list bullets.
 */
function stripMarkdown(text) {
  return text
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/\*{1,3}(.+?)\*{1,3}/g, "$1")
    .replace(/_{1,3}(.+?)_{1,3}/g, "$1")
    .replace(/^>\s?/gm, "")
    .replace(/^[-*+]\s+/gm, "")
    .replace(/^\d+\.\s+/gm, "")
    .trim();
}

// ---------------------------------------------------------------------------
// Enunciation
// ---------------------------------------------------------------------------

const DEFAULT_VOICE =
  "A warm, clear narrator with a natural storytelling cadence, " +
  "engaging pacing, and expressive delivery suitable for long-form fiction.";

const INSTRUCTS = {
  first: "Read the opening with a clear, inviting tone. Set the scene.",
  middle: "Continue reading naturally with clear pacing and gentle expression.",
  last: "Read with a sense of closing. Let the final words settle.",
};

/**
 * Produce voice-directed markdown from raw chapter text.
 *
 * Splits the chapter at paragraph boundaries, assigns a VOICE description
 * and position-aware INSTRUCT lines.  The output format is consumed by the
 * TTS pipeline (`packages/tts`).
 *
 * @param {string} chapterText - Raw chapter markdown content
 * @param {object} [opts]
 * @param {string} [opts.voice] - Custom VOICE description (overrides default)
 * @returns {string} Voice-directed markdown
 */
export function enunciateChapter(chapterText, opts = {}) {
  const voice = opts.voice || DEFAULT_VOICE;
  const { heading, body } = extractHeading(chapterText.trim());

  const paragraphs = body
    .split(/\n{2,}/)
    .map((p) => stripMarkdown(p))
    .filter((p) => p.length > 0);

  if (paragraphs.length === 0) {
    return `VOICE: ${voice}\n\n---\n\nINSTRUCT: ${INSTRUCTS.middle}\nTEXT: ${body.trim() || chapterText.trim()}`;
  }

  const segments = paragraphs.map((text, i) => {
    let instruct;
    if (i === 0) instruct = INSTRUCTS.first;
    else if (i === paragraphs.length - 1) instruct = INSTRUCTS.last;
    else instruct = INSTRUCTS.middle;
    return `INSTRUCT: ${instruct}\nTEXT: ${text}`;
  });

  const output = `VOICE: ${voice}\n\n---\n\n${segments.join("\n\n---\n\n")}`;
  return ensureChapterHeading(output, heading);
}
