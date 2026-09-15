"""Sanitising the rich text a message body may carry.

## Why this is hand-written

The composer produces four things — bold, italic, a bulleted list, a link —
and nothing else. Sanitising that is an allowlist over a handful of tags, which
`html.parser` from the standard library does correctly. Adding `bleach` or
`nh3` to the dependency set to strip five tags would be a supply-chain
liability bigger than the problem.

The trade is stated plainly: this is **not** a general-purpose HTML sanitiser
and must not be used as one. It is safe for the narrow job it does because it
is an allowlist that rebuilds the document from scratch rather than a filter
that tries to remove the bad parts. Anything not explicitly permitted does not
survive — unknown tags are dropped, all attributes except `href` are dropped,
and comments, CDATA, processing instructions and declarations are discarded
entirely. `<script>` does not need to be blocklisted, because it is simply not
on the list.

If the editor ever grows images, tables or embeds, replace this with a real
library rather than extending the allowlist.
"""

from __future__ import annotations

from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse

#: Tags the composer can produce. `br` and `div` are here because
#: contentEditable inserts them for line breaks whether asked to or not.
ALLOWED_TAGS = frozenset({"b", "strong", "i", "em", "u", "p", "br", "ul", "ol", "li", "a", "div", "span"})

#: Tags that must close. Anything else in ALLOWED_TAGS is void or self-closing.
VOID_TAGS = frozenset({"br"})

#: Only `href`, only on `a`. No `style`, no `class`, no `on*` — the whole
#: attribute surface is the interesting part of an XSS, so it is closed.
ALLOWED_ATTRIBUTES = {"a": frozenset({"href"})}

#: `javascript:` and `data:` are the two that turn a link into script
#: execution. An allowlist again, rather than blocking those two by name.
ALLOWED_URL_SCHEMES = frozenset({"http", "https", "mailto", "tel"})

#: Tags whose *contents* are code or styling, not prose.
#:
#: Dropping the tag is not enough for these: `<script>alert(1)</script>` would
#: otherwise leave `alert(1)` behind as escaped text. It is inert — escaped
#: text cannot execute — but it is not what the author wrote and it would look
#: like corruption. The text is discarded with the tag.
RAW_TEXT_TAGS = frozenset({"script", "style", "template", "noscript", "iframe", "object", "embed"})

#: A body longer than this is not prose, it is an attack or a paste accident.
MAX_HTML_LENGTH = 100_000


def _safe_href(value: str) -> str | None:
    candidate = value.strip()
    if not candidate:
        return None
    # A relative link has no scheme and cannot execute anything.
    parsed = urlparse(candidate)
    if not parsed.scheme:
        return candidate if not candidate.lower().startswith("javascript") else None
    return candidate if parsed.scheme.lower() in ALLOWED_URL_SCHEMES else None


class _Sanitiser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._open: list[str] = []
        #: Depth inside a raw-text tag. A counter rather than a flag, so
        #: `<script><script>` cannot close the suppression early.
        self._suppress = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in RAW_TEXT_TAGS:
            self._suppress += 1
            return
        if tag not in ALLOWED_TAGS:
            return
        permitted = ALLOWED_ATTRIBUTES.get(tag, frozenset())
        rendered = ""
        for name, value in attrs:
            if name not in permitted or value is None:
                continue
            if name == "href":
                href = _safe_href(value)
                if href is None:
                    continue
                rendered += f' href="{escape(href, quote=True)}"'
                # Anything we did not author opens in a new tab with the
                # referrer and opener closed off.
                rendered += ' target="_blank" rel="noopener noreferrer nofollow"'
        if tag in VOID_TAGS:
            self.parts.append(f"<{tag}{rendered} />")
            return
        self._open.append(tag)
        self.parts.append(f"<{tag}{rendered}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in RAW_TEXT_TAGS:
            self._suppress = max(0, self._suppress - 1)
            return
        if tag not in ALLOWED_TAGS or tag in VOID_TAGS:
            return
        # Close only what is actually open, innermost first, so malformed
        # input cannot emit a stray closing tag that escapes a container.
        if tag in self._open:
            while self._open:
                current = self._open.pop()
                self.parts.append(f"</{current}>")
                if current == tag:
                    break

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in VOID_TAGS:
            self.parts.append(f"<{tag} />")

    def handle_data(self, data: str) -> None:
        if self._suppress:
            return
        self.parts.append(escape(data, quote=False))

    # Everything below is explicitly discarded rather than inherited, because
    # the default implementations are no-ops and silence is not the same as a
    # decision. Comments can hide conditional-comment script in old IE, and
    # declarations and processing instructions have no business in a message.
    def handle_comment(self, data: str) -> None:
        return

    def handle_decl(self, decl: str) -> None:
        return

    def handle_pi(self, data: str) -> None:
        return

    def unknown_decl(self, data: str) -> None:
        return

    def result(self) -> str:
        while self._open:
            self.parts.append(f"</{self._open.pop()}>")
        return "".join(self.parts)


def sanitize_message_html(value: str | None) -> str | None:
    """Return `value` with only the composer's own formatting left standing.

    None in, None out — a plain-text message has no HTML and should not be
    given an empty string, which would make every reader check for both.
    """
    if value is None:
        return None
    if not value.strip():
        return None
    parser = _Sanitiser()
    parser.feed(value[:MAX_HTML_LENGTH])
    parser.close()
    cleaned = parser.result().strip()
    return cleaned or None
