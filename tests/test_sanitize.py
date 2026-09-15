"""The message-HTML allowlist.

A student's message is rendered in a counsellor's browser and a counsellor's
in a student's, so a stored-XSS here crosses a trust boundary in both
directions. These are the cases that matter, written as the attack rather than
as the API.
"""

from __future__ import annotations

import pytest

from app.core.sanitize import sanitize_message_html as clean


@pytest.mark.parametrize(
    "payload",
    [
        "<script>alert(1)</script>",
        "<img src=x onerror=alert(1)>",
        '<a href="javascript:alert(1)">x</a>',
        '<a href="JaVaScRiPt:alert(1)">x</a>',
        '<a href="data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==">x</a>',
        '<iframe src="https://evil.example"></iframe>',
        '<svg onload=alert(1)>',
        '<body onload=alert(1)>',
        '<style>@import "evil.css"</style>',
        "<!--[if IE]><script>alert(1)</script><![endif]-->",
    ],
)
def test_nothing_executable_survives(payload: str) -> None:
    result = clean(payload) or ""
    lowered = result.lower()
    assert "<script" not in lowered
    assert "javascript:" not in lowered
    assert "onerror" not in lowered
    assert "onload" not in lowered
    assert "<iframe" not in lowered
    assert "<svg" not in lowered


def test_the_composers_own_formatting_is_kept() -> None:
    """The allowlist has to pass the four things the editor can produce, or the
    feature does not work."""
    html = "<p>Hi <b>there</b> and <i>welcome</i></p><ul><li>one</li><li>two</li></ul>"
    assert clean(html) == html


def test_links_are_kept_but_defanged() -> None:
    result = clean('<a href="https://example.com/x">docs</a>')
    assert 'href="https://example.com/x"' in result
    # Opened without handing the target a window reference or a referrer.
    assert 'rel="noopener noreferrer nofollow"' in result
    assert 'target="_blank"' in result


def test_every_attribute_but_href_is_dropped() -> None:
    """The attribute surface is where the interesting attacks live, so it is
    closed rather than filtered."""
    result = clean('<p class="x" style="color:red" data-x="1" onclick="alert(1)">t</p>')
    assert result == "<p>t</p>"


def test_raw_text_contents_go_with_their_tag() -> None:
    """Dropping `<script>` and keeping `alert(1)` as text is inert but reads as
    corruption."""
    assert clean("<script>alert(1)</script>after") == "after"
    assert clean("<style>body{}</style>after") == "after"


def test_malformed_markup_cannot_emit_a_stray_close() -> None:
    assert clean("<b>unclosed") == "<b>unclosed</b>"
    assert clean("</div></b>text") == "text"


def test_plain_text_gets_no_html() -> None:
    assert clean(None) is None
    assert clean("   ") is None


def test_a_relative_link_is_allowed() -> None:
    assert 'href="/applications/1"' in (clean('<a href="/applications/1">mine</a>') or "")
