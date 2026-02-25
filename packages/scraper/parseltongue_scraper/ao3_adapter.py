import asyncio
import re
import traceback
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from parseltongue_logger import get_logger
from playwright.async_api import Page, async_playwright

from .metadata import StoryMeta, to_yaml
from .repository import ensure_story_dir, has_chapter, write_chapter, write_meta

AO3_BASE = "https://archiveofourown.org"
ACTION_DELAY_MS = 2000
CONSENT_PAUSE_MS = 1500

# Non-HTML resource extensions whose failures are not worth surfacing as warnings.
_NOISE_EXTENSIONS = {".js", ".css", ".woff", ".woff2", ".ttf", ".png", ".jpg", ".svg", ".ico", ".gif"}

RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECS = 5  # doubles each attempt: 5s, 10s, 20s

logger = get_logger("scraper")


def clean_chapter_text(text: str) -> str:
    """
    Remove decorative characters and normalize whitespace while preserving paragraphs.
    - Strips runs of 2+ underscores (e.g. _____ used as separators)
    - Collapses spaces within each line; keeps newlines so paragraphs stay separated
    """
    if not text or not text.strip():
        return ""
    result = re.sub(r"_{2,}", "", text)
    lines = result.split("\n")
    lines = [re.sub(r"\s+", " ", line).strip() for line in lines]
    lines = [line for line in lines if line]
    return "\n\n".join(lines).strip()


def html_to_markdown(html: str) -> str:
    """
    Convert HTML snippet to plain text (minimal markdown-friendly). Preserves paragraph breaks.
    Excludes .landmark.heading. Uses </p> and <p> to create double-newlines between paragraphs.
    """
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for el in soup.select(".landmark.heading"):
        el.decompose()
    text = str(soup)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>\s*<p[^>]*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<p[^>]*>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    text = text.strip()
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )
    return clean_chapter_text(text)


def get_text(el, default_val: str = "") -> str:
    if el is None:
        return default_val
    text = el.get_text().strip()
    return re.sub(r"\s+", " ", text) or default_val


def parse_metadata_from_full_work(html: str, work_id: str, work_url: str) -> StoryMeta:
    """
    Parse metadata from the view full work page HTML.
    Selectors: title (h2.title.heading), author (h3.byline.heading),
    summary (div.summary.module > blockquote), rating (dd.rating.tags > ul > li > a),
    category (dd.category.tags > ul > li > a), tags (dd.freeform.tags > ul > li > a).
    """
    soup = BeautifulSoup(html, "html.parser")

    title_el = soup.select_one("h2.title.heading")
    title = get_text(title_el, "Untitled")

    byline_el = soup.select_one("h3.byline.heading")
    author = re.sub(r"^By\s+", "", get_text(byline_el, "Unknown"), flags=re.IGNORECASE).strip() or "Unknown"

    summary_el = soup.select_one("div.summary.module > blockquote")
    summary = html_to_markdown(str(summary_el)) if summary_el else ""

    rating: list[str] = []
    for a in soup.select("dd.rating.tags ul li a"):
        t = get_text(a)
        if t and t not in rating:
            rating.append(t)

    category: list[str] = []
    for a in soup.select("dd.category.tags ul li a"):
        t = get_text(a)
        if t and t not in category:
            category.append(t)

    tags: list[str] = []
    for a in soup.select("dd.freeform.tags ul li a"):
        t = get_text(a)
        if t and t not in tags:
            tags.append(t)

    word_count = 0
    word_el = soup.select_one("dd.words")
    if word_el:
        raw = re.sub(r"\D", "", get_text(word_el))
        if raw:
            word_count = int(raw) or 0

    return StoryMeta(
        story_id=str(work_id),
        title=title,
        author=author,
        summary=summary,
        url=work_url,
        rating=rating,
        category=category,
        tags=tags,
        word_count=word_count,
        chapter_count=0,
        language="en",
    )


def parse_chapters_from_full_work(html: str) -> list[tuple[str, str]]:
    """
    Parse the "view full work" page using elements with id starting with "chapter-"
    (e.g. #chapter-1, #chapter-2). Ordered by the numeric part of the id.
    Each block has h3.title (chapter title) and main text in .userstuff or [role="article"].
    """
    soup = BeautifulSoup(html, "html.parser")
    blocks = soup.select('[id^="chapter-"]')

    with_index: list[tuple[int, any]] = []
    for el in blocks:
        el_id = el.get("id", "")
        num_str = re.sub(r"^chapter-", "", el_id)
        try:
            num = int(num_str)
        except (ValueError, TypeError):
            num = 0
        with_index.append((num, el))

    with_index.sort(key=lambda x: x[0])

    chapters: list[tuple[str, str]] = []
    for _, el in with_index:
        title_el = el.select_one("h3.title")
        title = get_text(title_el, "")
        if re.match(r"^\s*notes?\s*$", title, re.IGNORECASE):
            continue

        article_el = el.select_one('[role="article"]')
        content_el = article_el if article_el else el.select_one(".userstuff")
        raw = str(content_el) if content_el else ""
        content = html_to_markdown(raw)

        if not content.strip() and not title.strip():
            continue
        if not title.strip() and len(content.strip()) < 80:
            continue

        chapters.append((title, content))

    return chapters


def _attach_page_listeners(page: Page, work_id: str) -> None:
    """
    Forward browser-side events to the Python logger.
    - console errors/warnings → WARNING (JS noise like 525 asset failures → DEBUG)
    - requestfailed on AO3 HTML pages → WARNING; JS/CSS/fonts/external → DEBUG
    """
    def _on_console(msg) -> None:
        if msg.type in ("error", "warning"):
            # 525 / asset-load failures are logged at DEBUG — they flood the console
            # but the HTML content we need is unaffected by them.
            text = msg.text
            if "Failed to load resource" in text or "525" in text:
                logger.debug("[work %s] browser %s: %s", work_id, msg.type, text)
            else:
                logger.warning("[work %s] browser %s: %s", work_id, msg.type, text)

    def _on_request_failed(req) -> None:
        url = req.url
        ext = Path(urlparse(url).path).suffix.lower()
        host = urlparse(url).hostname or ""
        # Non-AO3 domains or static asset failures are noise — log at DEBUG only
        if not host.endswith("archiveofourown.org") or ext in _NOISE_EXTENSIONS:
            logger.debug("[work %s] request failed (ignored): %s %s", work_id, req.failure, url)
        else:
            logger.warning("[work %s] request failed: %s %s", work_id, req.failure, url)

    page.on("console", _on_console)
    page.on("requestfailed", _on_request_failed)


async def _save_failure_artifacts(page: Page, context, work_id: str, logs_dir: Path) -> None:
    """On failure, save a full-page screenshot and the Playwright trace to logs/."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Screenshot
    try:
        screenshots_dir = logs_dir / "screenshots"
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        shot_path = screenshots_dir / f"{work_id}_{ts}.png"
        await page.screenshot(path=str(shot_path), full_page=True)
        logger.error("[work %s] screenshot -> %s", work_id, shot_path)
    except Exception as err:
        logger.error("[work %s] could not save screenshot: %s", work_id, err)

    # Playwright trace
    try:
        traces_dir = logs_dir / "traces"
        traces_dir.mkdir(parents=True, exist_ok=True)
        trace_path = traces_dir / f"{work_id}_{ts}.zip"
        await context.tracing.stop(path=str(trace_path))
        logger.error(
            "[work %s] trace -> %s  (view with: uv run playwright show-trace %s)",
            work_id, trace_path, trace_path,
        )
    except Exception as err:
        logger.error("[work %s] could not save trace: %s", work_id, err)


async def fetch_work(
    work_id: str,
    output_dir: str,
    timeout: int = 60_000,
    logs_dir: Path | None = None,
) -> str:
    """
    Fetch one work by ID using Playwright (always headed). Writes meta and each chapter to disk
    as soon as they are parsed. Skips chapters that already exist (resume support).
    On failure: logs the full traceback, saves a full-page screenshot, and saves a Playwright
    trace zip to logs_dir/ (open with: uv run playwright show-trace <file>).
    """
    story_dir = ensure_story_dir(output_dir, work_id)
    full_work_url = f"{AO3_BASE}/works/{work_id}?view_full_work=true&view_adult=true"

    # Linux X11 flags required for a headed window to appear and render correctly
    launch_args = ["--disable-dev-shm-usage", "--disable-gpu-sandbox", "--in-process-gpu"]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=launch_args, slow_mo=50)
        context = await browser.new_context(
            # Prevents TLS handshake failures in headless mode on Linux where
            # Chromium's certificate handling can differ from the system store.
            ignore_https_errors=True,
        )

        if logs_dir is not None:
            await context.tracing.start(screenshots=True, snapshots=True, sources=True)

        page = await context.new_page()
        _attach_page_listeners(page, work_id)

        failed = False
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            failed = False
            try:
                page.set_default_timeout(timeout)

                attempt_label = f"(attempt {attempt}/{RETRY_ATTEMPTS}) " if attempt > 1 else ""
                logger.info("Navigating to work %s %s...", work_id, attempt_label)
                await page.goto(full_work_url, wait_until="domcontentloaded")
                await page.wait_for_load_state("networkidle", timeout=15_000)
                await page.wait_for_timeout(CONSENT_PAUSE_MS)

                tos = page.locator("#tos_agree")
                if await tos.is_visible():
                    logger.info("TOS consent screen detected — accepting...")
                    await tos.check()
                    await page.locator("#data_processing_agree").check()
                    await page.locator("#accept_tos").click()
                    await page.wait_for_timeout(ACTION_DELAY_MS)
                    await page.wait_for_load_state("networkidle", timeout=10_000)
                    logger.info("TOS accepted, reloading full work page...")
                    await page.goto(full_work_url, wait_until="domcontentloaded")
                    await page.wait_for_load_state("networkidle", timeout=15_000)

                logger.info("Waiting for chapter content to appear...")
                await page.wait_for_selector('[id^="chapter-"]', state="visible", timeout=15_000)
                await page.wait_for_timeout(500)

                logger.info("Parsing metadata and chapters...")
                full_work_html = await page.content()
                meta = parse_metadata_from_full_work(full_work_html, work_id, full_work_url)
                chapters = parse_chapters_from_full_work(full_work_html)
                meta.chapter_count = len(chapters)
                write_meta(story_dir, meta, to_yaml)

                logger.info(
                    '"%s" by %s  —  %s | %s | %d tag%s | %s words | %d chapter%s',
                    meta.title,
                    meta.author,
                    ", ".join(meta.rating) or "No rating",
                    ", ".join(meta.category) or "No category",
                    len(meta.tags), "s" if len(meta.tags) != 1 else "",
                    f"{meta.word_count:,}",
                    len(chapters), "s" if len(chapters) != 1 else "",
                )

                saved = skipped = 0
                for i, (ch_title, ch_content) in enumerate(chapters):
                    chapter_index = i + 1
                    if has_chapter(story_dir, chapter_index):
                        logger.info(
                            "  [%02d/%02d] skipped (already on disk)  %s",
                            chapter_index, len(chapters), ch_title or "(untitled)",
                        )
                        skipped += 1
                        continue
                    write_chapter(story_dir, chapter_index, ch_title, ch_content)
                    logger.info(
                        "  [%02d/%02d] saved  %s  (%d words)",
                        chapter_index, len(chapters),
                        ch_title or "(untitled)",
                        len(ch_content.split()),
                    )
                    saved += 1

                logger.info(
                    "Finished work %s — %d saved, %d skipped  →  %s",
                    work_id, saved, skipped, story_dir,
                )
                await page.wait_for_timeout(2000)
                break  # success — exit retry loop

            except Exception:
                failed = True
                if attempt < RETRY_ATTEMPTS:
                    wait = RETRY_BACKOFF_SECS * (2 ** (attempt - 1))
                    logger.warning(
                        "Attempt %d/%d failed for work %s, retrying in %ds:\n%s",
                        attempt, RETRY_ATTEMPTS, work_id, wait, traceback.format_exc(),
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error(
                        "All %d attempts failed for work %s:\n%s",
                        RETRY_ATTEMPTS, work_id, traceback.format_exc(),
                    )
                    if logs_dir is not None:
                        await _save_failure_artifacts(page, context, work_id, logs_dir)
                    raise

        try:
            if not failed and logs_dir is not None:
                # Discard the trace on success — no path means it's dropped
                await context.tracing.stop()
        finally:
            await browser.close()

    return story_dir


async def scrape_ao3_work(
    work_id: str,
    output_dir: str,
    logs_dir: Path | None = None,
) -> str:
    """Fetch one AO3 work and save to output_dir (incremental + resume). Returns story directory path."""
    return await fetch_work(work_id, output_dir, logs_dir=logs_dir)
