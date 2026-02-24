import * as cheerio from "cheerio";

const AO3_BASE = "https://archiveofourown.org";
const ACTION_DELAY_MS = 2000;
const CONSENT_PAUSE_MS = 1500;

/**
 * Remove unwanted decorative characters and normalize whitespace in chapter text.
 * - Strips runs of 2+ underscores (e.g. _____ used as separators)
 * - Collapses multiple spaces to one, trims lines and whole text
 * @param {string} text
 * @returns {string}
 */
function cleanChapterText(text) {
  if (!text || !text.trim()) return "";
  return text
    .replace(/_{2,}/g, "")
    .replace(/\s+/g, " ")
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
    .join("\n\n")
    .trim();
}

/**
 * Convert HTML snippet to plain text (minimal markdown-friendly). Excludes .landmark.heading.
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
    .replace(/<\/p>\s*<p>/gi, "\n\n")
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
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
 * Parse work metadata from work page HTML.
 * @param {string} html
 * @param {string} workId
 * @param {string} workUrl
 * @returns {import('./metadata.js').StoryMeta}
 */
function parseMetadata(html, workId, workUrl) {
  const $ = cheerio.load(html);
  let title = "Untitled";
  let author = "Unknown";

  const h4 = $("h4.heading");
  if (h4.length) {
    const links = h4.find("a");
    if (links.length) {
      title = getText($(links[0]), "Untitled");
      if (links.length > 1) {
        author = links
          .slice(1)
          .map((_, a) => getText($(a)))
          .filter(Boolean)
          .join(", ")
          .trim() || "Unknown";
      }
    }
  }

  if (title === "Untitled") {
    for (const sel of ["h2.title a", "h2.title", ".work .title.heading", ".preface h2.title a"]) {
      const el = $(sel).first();
      if (el.length) {
        title = getText(el, "Untitled");
        if (title) break;
      }
    }
  }
  if (author === "Unknown") {
    const authorLinks = $('a[rel="author"]');
    if (authorLinks.length) {
      author = authorLinks
        .map((_, a) => getText($(a)))
        .get()
        .filter(Boolean)
        .join(", ")
        .trim() || "Unknown";
    }
    if (author === "Unknown") {
      const byline = $("h3.byline.heading, .byline").first();
      if (byline.length) author = getText(byline, "").replace(/^By\s+/i, "").trim() || "Unknown";
    }
  }

  const summaryEl = $("blockquote.summary").first();
  const summary = summaryEl.length ? htmlToMarkdown(summaryEl.html() || "") : "";

  const tags = [];
  $("ul.tags li a").each((_, a) => {
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
    const userstuff = block.find(".userstuff").first();
    const articleEl = block.find('[role="article"]').first();
    const contentEl = userstuff.length ? userstuff : articleEl;
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
  const workUrl = `${AO3_BASE}/works/${workId}?view_adult=true`;
  const fullWorkUrl = `${AO3_BASE}/works/${workId}?view_full_work=true&view_adult=true`;

  const browser = await chromium.launch({ headless });
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(timeout);

    await page.goto(workUrl, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle", { timeout: 15_000 });
    await page.waitForTimeout(CONSENT_PAUSE_MS);

    const tos = page.locator("#tos_agree");
    if (await tos.isVisible()) {
      await tos.check();
      await page.locator("#data_processing_agree").check();
      await page.locator("#accept_tos").click();
      await page.waitForTimeout(ACTION_DELAY_MS);
      await page.waitForLoadState("networkidle", { timeout: 10_000 });
      await page.goto(workUrl, { waitUntil: "domcontentloaded" });
      await page.waitForLoadState("networkidle", { timeout: 15_000 });
    }

    const workHtml = await page.content();
    const meta = parseMetadata(workHtml, workId, workUrl);
    console.log(`[work ${workId}] title=${JSON.stringify(meta.title)} author=${JSON.stringify(meta.author)} word_count=${meta.word_count} tags=${meta.tags.length} url=${meta.url}`);

    await page.waitForTimeout(ACTION_DELAY_MS);
    await page.goto(fullWorkUrl, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle", { timeout: 15_000 });
    await page.waitForTimeout(CONSENT_PAUSE_MS);

    if (await page.locator("#tos_agree").isVisible()) {
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
    const chapters = parseChaptersFromFullWork(fullWorkHtml);
    meta.chapter_count = chapters.length;
    writeMeta(storyDir, meta, toYaml);
    console.log(`[work ${workId}] full work: ${chapters.length} chapter(s), meta written`);

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
