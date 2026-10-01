"""Phase 7.3.3: deterministic content-quality metrics (pure counting, no network, no AI).

`compute_metrics(page, html_length=..., image_count=..., link_count=...)` summarises an already parsed page.
Everything is a plain count or one ratio, so the same input always gives the same numbers.

- `character_count`: length of the visible text (`ParsedPage.text`, line breaks included).
- `word_count`: whitespace-separated tokens of the visible text that contain a letter or digit (a lone "-" or
  an emoji is not a word). Persian words joined by ZWNJ stay one word.
- `heading_count`: `h1`-`h6` elements with text. `email_count` / `phone_count`: the page-level `emails` /
  `phones` (JSON-LD values are not included, as in those fields).
- `link_count`: every `<a href>` with a non-blank value, duplicates included, with no cap;
  `unique_link_count`: distinct raw hrefs (`len(ParsedPage.links)`, so it stops at `MAX_LINKS_PER_PAGE`).
- `image_count`: `<img>` elements outside script/style/noscript/template.
- `html_length`: characters of the decoded HTML string the page was parsed from (not bytes).
- `text_html_ratio`: `character_count / html_length`, rounded to 4 places, in [0, 1]; 0.0 for empty HTML.

`html_length` and `text_html_ratio` describe the markup around the content, so they are excluded from `==`:
an empty or whitespace-only document still equals `ParsedPage()` (as before this phase).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid a circular import: html_parser imports this module
    from app.crawler.html_parser import ParsedPage

RATIO_DIGITS = 4


@dataclass(frozen=True, slots=True)
class ContentMetrics:
    """Counts describing a page's content. An empty page is all zeros."""

    character_count: int = 0
    word_count: int = 0
    heading_count: int = 0
    link_count: int = 0
    unique_link_count: int = 0
    image_count: int = 0
    email_count: int = 0
    phone_count: int = 0
    html_length: int = field(default=0, compare=False)  # size of the input, not content: ignored by ==
    text_html_ratio: float = field(default=0.0, compare=False)  # derived from html_length: ignored by ==

    def to_dict(self) -> dict[str, object]:
        return {
            "character_count": self.character_count,
            "word_count": self.word_count,
            "heading_count": self.heading_count,
            "link_count": self.link_count,
            "unique_link_count": self.unique_link_count,
            "image_count": self.image_count,
            "email_count": self.email_count,
            "phone_count": self.phone_count,
            "html_length": self.html_length,
            "text_html_ratio": self.text_html_ratio,
        }


EMPTY_METRICS = ContentMetrics()


def count_words(text: str) -> int:
    return sum(1 for token in text.split() if any(ch.isalnum() for ch in token))


def compute_metrics(page: ParsedPage, *, html_length: int = 0, image_count: int = 0, link_count: int | None = None) -> ContentMetrics:
    """Metrics for `page`. `link_count` defaults to the number of distinct links when the raw anchor count is unknown."""
    characters = len(page.text)
    ratio = round(min(1.0, characters / html_length), RATIO_DIGITS) if html_length > 0 else 0.0
    return ContentMetrics(
        character_count=characters,
        word_count=count_words(page.text),
        heading_count=len(page.headings),
        link_count=len(page.links) if link_count is None else link_count,
        unique_link_count=len(page.links),
        image_count=image_count,
        email_count=len(page.emails),
        phone_count=len(page.phones),
        html_length=html_length,
        text_html_ratio=ratio,
    )
