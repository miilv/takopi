from __future__ import annotations

import re
from html import escape

from markdown_it import MarkdownIt

_md = MarkdownIt("commonmark", {"html": False, "breaks": True})

_CODE_CLASS_RE = re.compile(r'<code class="[^"]+">')
_IMG_ALT_RE = re.compile(r'<img[^>]*alt="([^"]*)"[^>]*/?>')
_IMG_RE = re.compile(r"<img[^>]*>")
_OL_OPEN_RE = re.compile(r'<ol(?: start="\d+")?>\s*')
_TAG_RE = re.compile(r"<[^>]+>")


def strip_tags(html: str) -> str:
    return _TAG_RE.sub("", html)


def render_to_html(text: str) -> str:
    """
    Render Markdown to Telegram-compatible HTML.

    Telegram supports only a subset of HTML tags, so we post-process the
    MarkdownIt output to flatten unsupported block tags (p/ul/li/etc) into
    plain text with newlines and simple bullets.
    """
    html = _md.render(text or "")

    # Paragraphs and line breaks.
    html = html.replace("<p>", "")
    html = html.replace("<br />\n", "\n").replace("<br>\n", "\n")
    html = html.replace("<br />", "\n").replace("<br>", "\n")
    html = html.replace("</p>\n", "\n\n").replace("</p>", "\n\n")

    # Lists -> "- " lines.
    html = html.replace("<ul>\n", "").replace("</ul>\n", "")
    html = _OL_OPEN_RE.sub("", html).replace("</ol>\n", "")
    html = html.replace("<li>", "- ")
    html = html.replace("</li>\n", "\n").replace("</li>", "\n")

    # Headings -> bold line.
    for level in range(1, 7):
        html = html.replace(f"<h{level}>", "<b>")
        html = html.replace(f"</h{level}>\n", "</b>\n\n").replace(
            f"</h{level}>", "</b>\n\n"
        )

    # Code fences may include language class; Telegram doesn't need it.
    html = _CODE_CLASS_RE.sub("<code>", html)

    # Images are not supported: keep alt text if present.
    html = _IMG_ALT_RE.sub(lambda m: escape(m.group(1) or ""), html)
    html = _IMG_RE.sub("", html)

    # <hr> isn't supported; render a separator line.
    html = html.replace("<hr />", "\n----\n\n").replace("<hr>", "\n----\n\n")

    # Flatten blockquotes.
    html = html.replace("<blockquote>\n", "")
    html = html.replace("</blockquote>\n", "\n\n").replace("</blockquote>", "\n\n")

    html = re.sub(r"\n{3,}", "\n\n", html)
    return html.strip()
