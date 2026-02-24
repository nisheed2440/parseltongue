import { GoogleGenerativeAI } from "@google/generative-ai";
import fse from "fs-extra";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROMPT_PATH = path.join(__dirname, "voice-direction-prompt.md");

let _systemPrompt = null;

function getSystemPrompt() {
  if (!_systemPrompt) {
    _systemPrompt = fse.readFileSync(PROMPT_PATH, "utf-8");
  }
  return _systemPrompt;
}

/**
 * Enunciate a chapter's markdown text using Google Gemini.
 *
 * @param {string} chapterText - Raw chapter markdown content
 * @param {object} [opts]
 * @param {string} [opts.apiKey] - Gemini API key (falls back to GEMINI_API_KEY env var)
 * @param {string} [opts.model]  - Model name (default: gemini-2.0-flash)
 * @returns {Promise<string>} Voice-directed markdown
 */
export async function enunciateChapter(chapterText, opts = {}) {
  const apiKey = opts.apiKey || process.env.GEMINI_API_KEY;
  if (!apiKey) {
    throw new Error(
      "Gemini API key required. Set GEMINI_API_KEY env var or pass opts.apiKey."
    );
  }

  const modelName = opts.model || "gemini-2.5-flash";
  const genAI = new GoogleGenerativeAI(apiKey);
  const model = genAI.getGenerativeModel({
    model: modelName,
    systemInstruction: getSystemPrompt(),
  });

  const result = await model.generateContent(chapterText);
  const response = result.response;
  return response.text();
}
