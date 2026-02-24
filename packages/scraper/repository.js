import fse from "fs-extra";
import path from "path";

/**
 * Safe directory name from story id.
 * @param {string} storyId
 * @returns {string}
 */
function sanitizeId(storyId) {
  return String(storyId).replace(/[^\w.-]/g, "_").replace(/^_+|_+$/g, "") || "unknown";
}

/**
 * Ensure data/stories/<story_id>/ and chapters/ exist.
 * @param {string} baseDir - absolute path to base (e.g. data/stories)
 * @param {string} storyId
 * @returns {string} story directory path
 */
export function ensureStoryDir(baseDir, storyId) {
  const storyDir = path.join(baseDir, sanitizeId(storyId));
  fse.ensureDirSync(path.join(storyDir, "chapters"));
  return storyDir;
}

/**
 * Write meta.yaml into story dir.
 * @param {string} storyDir
 * @param {import('./metadata.js').StoryMeta} meta
 * @param { (meta: import('./metadata.js').StoryMeta) => string } toYaml
 */
export function writeMeta(storyDir, meta, toYaml) {
  fse.outputFileSync(path.join(storyDir, "meta.yaml"), toYaml(meta));
}

/**
 * Path for a chapter file (1-based index -> 01.md, 02.md, ...).
 * @param {string} storyDir
 * @param {number} chapterIndex - 1-based
 * @returns {string}
 */
export function chapterFilePath(storyDir, chapterIndex) {
  const num = String(chapterIndex).padStart(2, "0");
  return path.join(storyDir, "chapters", `${num}.md`);
}

/**
 * Check if a chapter file already exists (for resume).
 * @param {string} storyDir
 * @param {number} chapterIndex - 1-based
 * @returns {boolean}
 */
export function hasChapter(storyDir, chapterIndex) {
  return fse.pathExistsSync(chapterFilePath(storyDir, chapterIndex));
}

/**
 * Write one chapter as Markdown. 1-based index -> 01.md, 02.md, ...
 * @param {string} storyDir
 * @param {number} chapterIndex - 1-based
 * @param {string} title
 * @param {string} content
 */
export function writeChapter(storyDir, chapterIndex, title, content) {
  const filePath = chapterFilePath(storyDir, chapterIndex);
  let body = content.trim();
  if (title && !body.startsWith("#")) {
    body = `# ${title}\n\n${body}`;
  }
  fse.outputFileSync(filePath, body);
}

/**
 * Save one full story: meta.yaml + chapters/01.md, 02.md, ...
 * @param {string} baseDir - absolute path to data/stories
 * @param {import('./metadata.js').StoryMeta} meta
 * @param {[string, string][]} chapters - list of [title, markdown_content]
 * @param { (meta: import('./metadata.js').StoryMeta) => string } toYaml
 * @returns {string} story directory path
 */
export function saveStory(baseDir, meta, chapters, toYaml) {
  const storyDir = ensureStoryDir(baseDir, meta.story_id);
  const metaWithCount = { ...meta, chapter_count: chapters.length };
  writeMeta(storyDir, metaWithCount, toYaml);
  chapters.forEach(([title, content], i) => {
    writeChapter(storyDir, i + 1, title, content);
  });
  return storyDir;
}
