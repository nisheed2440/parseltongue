import os
import re
from typing import Callable

from .metadata import StoryMeta


def sanitize_id(story_id: str) -> str:
    return re.sub(r"[^\w.-]", "_", str(story_id)).strip("_") or "unknown"


def ensure_story_dir(base_dir: str, story_id: str) -> str:
    story_dir = os.path.join(base_dir, sanitize_id(story_id))
    os.makedirs(os.path.join(story_dir, "chapters"), exist_ok=True)
    return story_dir


def write_meta(story_dir: str, meta: StoryMeta, to_yaml_fn: Callable[[StoryMeta], str]) -> None:
    with open(os.path.join(story_dir, "meta.yaml"), "w", encoding="utf-8") as f:
        f.write(to_yaml_fn(meta))


def chapter_file_path(story_dir: str, chapter_index: int) -> str:
    num = str(chapter_index).zfill(2)
    return os.path.join(story_dir, "chapters", f"{num}.md")


def has_chapter(story_dir: str, chapter_index: int) -> bool:
    return os.path.exists(chapter_file_path(story_dir, chapter_index))


def write_chapter(story_dir: str, chapter_index: int, title: str, content: str) -> None:
    file_path = chapter_file_path(story_dir, chapter_index)
    body = content.strip()
    if title and not body.startswith("#"):
        body = f"# {title}\n\n{body}"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(body)


def save_story(
    base_dir: str,
    meta: StoryMeta,
    chapters: list[tuple[str, str]],
    to_yaml_fn: Callable[[StoryMeta], str],
) -> str:
    story_dir = ensure_story_dir(base_dir, meta.story_id)
    meta.chapter_count = len(chapters)
    write_meta(story_dir, meta, to_yaml_fn)
    for i, (title, content) in enumerate(chapters):
        write_chapter(story_dir, i + 1, title, content)
    return story_dir
