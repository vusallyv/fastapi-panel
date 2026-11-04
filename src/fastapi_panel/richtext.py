from __future__ import annotations

import re
from html import escape
from html.parser import HTMLParser

from markupsafe import Markup

ALLOWED_TAGS = {"p", "br", "strong", "em", "u", "s", "ul", "ol", "li", "h2", "h3", "blockquote", "a", "code", "pre"}
RENAMED_TAGS = {
    "b": "strong",
    "i": "em",
    "strike": "s",
    "div": "p",
    "h1": "h2",
    "h4": "h3",
    "h5": "h3",
    "h6": "h3",
}
# Their contents aren't text a person wrote, so they go along with the tag.
DROPPED_WITH_CONTENT = {
    "script",
    "style",
    "iframe",
    "object",
    "embed",
    "template",
    "noscript",
    "svg",
    "math",
    "textarea",
    "title",
    "xmp",
    "noembed",
    "noframes",
    "select",
    "head",
}
VOID_TAGS = {"br"}
SAFE_LINK = re.compile(r"^(https?:|mailto:)", re.I)
CONTROL_CHARACTERS = re.compile(r"[\x00-\x20\x7f]")
MAX_DEPTH = 50


class _Cleaner(HTMLParser):
    """Rebuilds the HTML from what it understands, instead of trying to remove what's dangerous."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.output: list[str] = []
        self.open_tags: list[str] = []
        self.skipping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in DROPPED_WITH_CONTENT:
            self.skipping += 1
            return
        tag = RENAMED_TAGS.get(tag, tag)
        if self.skipping or tag not in ALLOWED_TAGS:
            return
        if tag in VOID_TAGS:
            self.output.append(f"<{tag}>")
            return
        if len(self.open_tags) >= MAX_DEPTH:
            return
        if tag == "a":
            href = CONTROL_CHARACTERS.sub("", dict(attrs).get("href") or "")
            if not SAFE_LINK.match(href):
                return
            self.output.append(f'<a href="{escape(href, quote=True)}" rel="noopener noreferrer">')
        else:
            self.output.append(f"<{tag}>")
        self.open_tags.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in DROPPED_WITH_CONTENT:
            self.skipping = max(0, self.skipping - 1)
            return
        tag = RENAMED_TAGS.get(tag, tag)
        if self.skipping or tag not in self.open_tags:
            return
        while self.open_tags:
            current = self.open_tags.pop()
            self.output.append(f"</{current}>")
            if current == tag:
                break

    def handle_data(self, data: str) -> None:
        if not self.skipping:
            self.output.append(escape(data, quote=False))


def safe_html(value: str) -> Markup:
    return Markup(sanitize_html(value))  # noqa: S704  (it has just been sanitized)


def plain_text(value: str) -> str:
    return safe_html(value).striptags()


def sanitize_html(value: str) -> str:
    """Keeps basic formatting and links. Use it too if you print a RichTextField's value outside the panel."""
    cleaner = _Cleaner()
    cleaner.feed(value)
    cleaner.close()
    closing = [f"</{tag}>" for tag in reversed(cleaner.open_tags)]
    return "".join(cleaner.output + closing)
