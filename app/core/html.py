"""Sanitising the rich text written in the admin's article editor.

Articles and guides are written in CKEditor, which produces HTML. The public
site renders that HTML, so it is cleaned here, on the way in, to an allow-list
of the elements the editor can actually produce: headings, paragraphs, lists,
links, quotes, tables, images and code. Scripts, event handlers, iframes,
forms and `javascript:` URLs cannot survive it. The landing sanitises again
when it renders (defence in depth), but this is the copy that is stored.
"""

from __future__ import annotations

import re

import nh3

_TAGS = {
    "p", "br", "hr", "h2", "h3", "h4",
    "strong", "b", "em", "i", "u", "s", "sub", "sup", "mark", "code", "pre",
    "a", "ul", "ol", "li", "blockquote",
    "figure", "figcaption", "img",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "colgroup", "col",
    "span",
}  # fmt: skip

_ATTRIBUTES = {
    "a": {"href", "title", "target"},
    "img": {"src", "alt", "width", "height", "title"},
    "figure": {"class", "style"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
    "ol": {"start", "reversed"},
    "p": {"class"},
    "span": {"class"},
    "pre": {"class"},
    "code": {"class"},
}

#: The layout classes CKEditor's image, table and alignment plugins emit.
_ALLOWED_CLASS = re.compile(
    r"^(image|image_resized|image-style-[a-z-]+|table|text-(left|center|right|justify)|language-[a-z0-9+#-]+)$"
)
#: A resized image is `style="width:42%"` (or px) on its figure; nothing else.
_ALLOWED_WIDTH = re.compile(r"^\s*width\s*:\s*\d{1,4}(\.\d+)?(%|px)\s*;?\s*$", re.IGNORECASE)


def _attribute_filter(tag: str, attribute: str, value: str) -> str | None:
    if attribute == "class":
        kept = [name for name in value.split() if _ALLOWED_CLASS.match(name)]
        return " ".join(kept) or None
    if attribute == "style":
        return value if tag == "figure" and _ALLOWED_WIDTH.match(value) else None
    if attribute == "target":
        return "_blank" if value == "_blank" else None
    return value


def sanitize_rich_html(html: str | None) -> str | None:
    """The stored form of an article body, or None when there is nothing left."""
    if html is None:
        return None
    cleaned = nh3.clean(
        html,
        tags=_TAGS,
        attributes=_ATTRIBUTES,
        attribute_filter=_attribute_filter,
        url_schemes={"http", "https", "mailto", "tel"},
        link_rel="noopener noreferrer",
        strip_comments=True,
    ).strip()
    return cleaned or None


def reading_minutes(html: str | None) -> int | None:
    """Rough reading time at 220 words a minute, never less than one."""
    if not html:
        return None
    words = len(re.sub(r"<[^>]+>", " ", html).split())
    return max(1, round(words / 220)) if words else None
