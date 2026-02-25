import re

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from .metadata import StoryMeta, to_yaml
from .repository import ensure_story_dir, has_chapter, write_chapter, write_meta

AO3_BASE = "https://archiveofourown.org"
ACTION_DELAY_MS = 2000
CONSENT_PAUSE_MS = 1500


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


async def fetch_work(
    work_id: str,
    output_dir: str,
    headless: bool = False,
    timeout: int = 60_000,
) -> str:
    """
    Fetch one work by ID using Playwright. Writes meta and each chapter to disk as soon as
    they are parsed. Skips chapters that already exist (resume support). Returns story dir.
    """
    story_dir = ensure_story_dir(output_dir, work_id)
    full_work_url = f"{AO3_BASE}/works/{work_id}?view_full_work=true&view_adult=true"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        try:
            page = await browser.new_page()
            page.set_default_timeout(timeout)

            await page.goto(full_work_url, wait_until="domcontentloaded")
            await page.wait_for_load_state("networkidle", timeout=15_000)
            await page.wait_for_timeout(CONSENT_PAUSE_MS)

            tos = page.locator("#tos_agree")
            if await tos.is_visible():
                await tos.check()
                await page.locator("#data_processing_agree").check()
                await page.locator("#accept_tos").click()
                await page.wait_for_timeout(ACTION_DELAY_MS)
                await page.wait_for_load_state("networkidle", timeout=10_000)
                await page.goto(full_work_url, wait_until="domcontentloaded")
                await page.wait_for_load_state("networkidle", timeout=15_000)

            await page.wait_for_selector('[id^="chapter-"]', state="visible", timeout=15_000)
            await page.wait_for_timeout(500)

            full_work_html = await page.content()
            meta = parse_metadata_from_full_work(full_work_html, work_id, full_work_url)
            chapters = parse_chapters_from_full_work(full_work_html)
            meta.chapter_count = len(chapters)
            write_meta(story_dir, meta, to_yaml)

            print(
                f"[work {work_id}] title={meta.title!r} author={meta.author!r} "
                f"rating={len(meta.rating)} category={len(meta.category)} "
                f"tags={len(meta.tags)} chapters={len(chapters)} url={meta.url}"
            )

            for i, (ch_title, ch_content) in enumerate(chapters):
                chapter_index = i + 1
                if has_chapter(story_dir, chapter_index):
                    print(f"[work {work_id}] ch {chapter_index}/{len(chapters)}: already on disk, skipping")
                    continue
                write_chapter(story_dir, chapter_index, ch_title, ch_content)
                print(
                    f"[work {work_id}] ch {chapter_index}/{len(chapters)}: "
                    f"saved title={ch_title!r} len={len(ch_content)}"
                )

            print(f"[work {work_id}] done")
            await page.wait_for_timeout(2000)
        finally:
            await browser.close()

    return story_dir


async def scrape_ao3_work(work_id: str, output_dir: str, headless: bool = False) -> str:
    """Fetch one AO3 work and save to output_dir (incremental + resume). Returns story directory path."""
    return await fetch_work(work_id, output_dir, headless=headless)
