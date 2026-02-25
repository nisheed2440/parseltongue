from dataclasses import dataclass, field

import yaml


@dataclass
class StoryMeta:
    story_id: str
    title: str
    author: str
    summary: str = ""
    url: str = ""
    rating: list[str] = field(default_factory=list)
    category: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    word_count: int = 0
    chapter_count: int = 0
    language: str = "en"


def to_yaml(meta: StoryMeta) -> str:
    obj = {
        "story_id": meta.story_id,
        "title": meta.title,
        "author": meta.author,
        "summary": meta.summary,
        "url": meta.url,
        "rating": meta.rating,
        "category": meta.category,
        "tags": meta.tags,
        "word_count": meta.word_count,
        "chapter_count": meta.chapter_count,
        "language": meta.language,
    }
    return yaml.dump(obj, default_flow_style=False, allow_unicode=True, width=-1)
