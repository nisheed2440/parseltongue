import * as cheerio from "cheerio";

const AO3_BASE = "https://archiveofourown.org";
const ACTION_DELAY_MS = 2000;
const CONSENT_PAUSE_MS = 1500;

/**
 * Remove unwanted decorative characters and normalize whitespace while preserving paragraphs.
 * - Strips runs of 2+ underscores (e.g. _____ used as separators)
 * - Collapses spaces within each line only; keeps newlines so paragraphs stay separated
 * @param {string} text
 * @returns {string}
 */
function cleanChapterText(text) {
  if (!text || !text.trim()) return "";
  return text
    .replace(/_{2,}/g, "")
    .split("\n")
    .map((line) => line.replace(/\s+/g, " ").trim())
    .filter((line) => line.length > 0)
    .join("\n\n")
    .trim();
}

/**
 * Convert HTML snippet to plain text (minimal markdown-friendly). Preserves paragraph breaks.
 * Excludes .landmark.heading. Uses </p> and <p> to create double-newlines between paragraphs.
 * @param {string} html
 * @returns {string}
 */
function htmlToMarkdown(html) {
  if (!html) return "";
  const $ = cheerio.load(html);
  $(".landmark.heading").remove();
  let text = $.html();
  text = text
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<\/p>\s*<p[^>]*>/gi, "\n\n")
    .replace(/<\/p>/gi, "\n\n")
    .replace(/<p[^>]*>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/[ \t]+/g, " ")
    .replace(/\n\s*\n\s*\n+/g, "\n\n")
    .trim();
  text = text.replace(/&nbsp;/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"');
  return cleanChapterText(text);
}

/** @param {cheerio.CheerioAPI} $ */
function getText($el, defaultVal = "") {
  if (!$el || !$el.length) return defaultVal;
  return $el.text().trim().replace(/\s+/g, " ") || defaultVal;
}

/**
 * Parse metadata from the view full work page HTML.
 * Selectors: title (h2.title.heading), author (h3.byline.heading), summary (div.summary.module > blockquote),
 * rating (dd.rating.tags > ul > li > a), category (dd.category.tags > ul > li > a), tags (dd.freeform.tags > ul > li > a).
 * @param {string} html - full work page HTML
 * @param {string} workId
 * @param {string} workUrl
 * @returns {import('./metadata.js').StoryMeta}
 */
function parseMetadataFromFullWork(html, workId, workUrl) {
  const $ = cheerio.load(html);

  const titleEl = $("h2.title.heading").first();
  const title = getText(titleEl, "Untitled");

  const bylineEl = $("h3.byline.heading").first();
  let author = getText(bylineEl, "Unknown").replace(/^By\s+/i, "").trim() || "Unknown";

  const summaryEl = $("div.summary.module > blockquote").first();
  const summary = summaryEl.length ? htmlToMarkdown(summaryEl.html() || "") : "";

  const rating = [];
  $("dd.rating.tags ul li a").each((_, a) => {
    const t = getText($(a));
    if (t && !rating.includes(t)) rating.push(t);
  });

  const category = [];
  $("dd.category.tags ul li a").each((_, a) => {
    const t = getText($(a));
    if (t && !category.includes(t)) category.push(t);
  });

  const tags = [];
  $("dd.freeform.tags ul li a").each((_, a) => {
    const t = getText($(a));
    if (t && !tags.includes(t)) tags.push(t);
  });

  let wordCount = 0;
  const wordEl = $("dd.words").first();
  if (wordEl.length) {
    const raw = getText(wordEl).replace(/\D/g, "");
    if (raw) wordCount = parseInt(raw, 10) || 0;
  }

  return {
    story_id: String(workId),
    title,
    author,
    summary,
    url: workUrl,
    rating,
    category,
    tags,
    word_count: wordCount,
    chapter_count: 0,
    language: "en",
  };
}

/**
 * Parse the "view full work" page using elements with id starting with "chapter-"
 * (e.g. #chapter-1, #chapter-2). Order by the numeric part of the id.
 * Each block has h3.title (chapter title) and main text in .userstuff or [role="article"].
 * @param {string} html - full work page HTML
 * @returns {[string, string][]} list of [title, content]
 */
function parseChaptersFromFullWork(html) {
  const $ = cheerio.load(html);
  const blocks = $('[id^="chapter-"]').get();
  const withIndex = blocks.map((el) => {
    const id = $(el).attr("id") || "";
    const num = parseInt(id.replace(/^chapter-/, ""), 10);
    return { el, num: Number.isNaN(num) ? 0 : num };
  });
  withIndex.sort((a, b) => a.num - b.num);

  const chapters = [];
  for (const { el } of withIndex) {
    const block = $(el);
    const titleEl = block.find("h3.title").first();
    const title = getText(titleEl, "");
    if (/^\s*notes?\s*$/i.test(title)) continue;
    const articleEl = block.find('[role="article"]').first();
    const contentEl = articleEl.length ? articleEl : block.find(".userstuff").first();
    const raw = contentEl.length ? contentEl.html() || "" : "";
    const content = htmlToMarkdown(raw);
    if (!content.trim() && !title.trim()) continue;
    if (!title.trim() && content.trim().length < 80) continue;
    chapters.push([title, content]);
  }
  return chapters;
}

/**
 * Fetch one work by ID using Playwright. Writes meta and each chapter to disk as soon as
 * they are fetched. Skips chapters that already exist (resume support). Returns story dir.
 * @param {string} workId
 * @param {{ outputDir: string, headless?: boolean, timeout?: number }} opts
 * @returns {Promise<string>} story directory path
 */
export async function fetchWork(workId, opts) {
  const { outputDir, headless = false, timeout = 60_000 } = opts;

  const { ensureStoryDir, writeMeta, writeChapter, hasChapter } = await import("./repository.js");
  const { toYaml } = await import("./metadata.js");

  const storyDir = ensureStoryDir(outputDir, workId);

  const { chromium } = await import("playwright");
  const fullWorkUrl = `${AO3_BASE}/works/${workId}?view_full_work=true&view_adult=true`;

  const browser = await chromium.launch({ headless });
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(timeout);

    await page.goto(fullWorkUrl, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle", { timeout: 15_000 });
    await page.waitForTimeout(CONSENT_PAUSE_MS);

    const tos = page.locator("#tos_agree");
    if (await tos.isVisible()) {
      await tos.check();
      await page.locator("#data_processing_agree").check();
      await page.locator("#accept_tos").click();
      await page.waitForTimeout(ACTION_DELAY_MS);
      await page.waitForLoadState("networkidle", { timeout: 10_000 });
      await page.goto(fullWorkUrl, { waitUntil: "domcontentloaded" });
      await page.waitForLoadState("networkidle", { timeout: 15_000 });
    }

    await page.waitForSelector('[id^="chapter-"]', { state: "visible", timeout: 15_000 });
    await page.waitForTimeout(500);

    const fullWorkHtml = await page.content();
    const meta = parseMetadataFromFullWork(fullWorkHtml, workId, fullWorkUrl);
    const chapters = parseChaptersFromFullWork(fullWorkHtml);
    meta.chapter_count = chapters.length;
    writeMeta(storyDir, meta, toYaml);
    console.log(`[work ${workId}] title=${JSON.stringify(meta.title)} author=${JSON.stringify(meta.author)} rating=${meta.rating.length} category=${meta.category.length} tags=${meta.tags.length} chapters=${chapters.length} url=${meta.url}`);

    for (let i = 0; i < chapters.length; i++) {
      const chapterIndex = i + 1;
      const [chTitle, chContent] = chapters[i];
      if (hasChapter(storyDir, chapterIndex)) {
        console.log(`[work ${workId}] ch ${chapterIndex}/${chapters.length}: already on disk, skipping`);
        continue;
      }
      writeChapter(storyDir, chapterIndex, chTitle, chContent);
      console.log(`[work ${workId}] ch ${chapterIndex}/${chapters.length}: saved title=${JSON.stringify(chTitle)} len=${chContent.length}`);
    }

    console.log(`[work ${workId}] done`);
    await page.waitForTimeout(2000);
  } finally {
    await browser.close();
  }

  return storyDir;
}

/**
 * Fetch one AO3 work and save to outputDir (incremental + resume). Returns story directory path.
 * @param {string} workId
 * @param {string} outputDir - absolute path to data/stories
 * @param {{ headless?: boolean }} [opts]
 * @returns {Promise<string>}
 */
export async function scrapeAo3Work(workId, outputDir, opts = {}) {
  return fetchWork(workId, { outputDir, headless: opts.headless });
}
