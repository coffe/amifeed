"""Turning feed HTML into clean, wrapped text that is safe to put on a terminal."""

import html
import re
import textwrap
import unicodedata
from html.parser import HTMLParser

TITLE_MAX = 200

# C0 (except TAB and LF, handled by the callers), DEL and C1. 0x9B is the
# 8-bit CSI an Amiga console reacts to, so C1 must never get through.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
# ESC sequences are gone once ESC itself is removed, but drop their bodies too
# so that no "[31m" litter is left in the text.
_ESC_SEQ = re.compile(r"\x1b(\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(\x07|\x1b\\)?|[@-Z\\-_])")
# Invisible formatting characters that only confuse narrow terminals.
_INVISIBLE = re.compile(r"[​-‏‪-‮⁠-⁤﻿]")


def clean(s):
    """Remove terminal control characters and invisible formatting."""
    s = _ESC_SEQ.sub("", s)
    s = _CONTROL.sub("", s)
    return _INVISIBLE.sub("", s)


def field(s, limit=None):
    """One TAB separated field: no TAB, CR or LF, no control characters."""
    s = clean(re.sub(r"[\t\r\n]+", " ", html.unescape(s or ""))).strip()
    s = re.sub(r" {2,}", " ", s)
    if limit and len(s) > limit:
        s = s[: limit - 3].rstrip() + "..."
    return s


def title(s):
    """Feeds put markup in titles now and then; keep only the text."""
    return field(re.sub(r"<[^<>]+>", "", s or ""), TITLE_MAX)


class _TextParser(HTMLParser):
    """A small HTML to text converter: paragraphs, line breaks, lists, headings."""

    BLOCK = {"p", "div", "section", "article", "header", "footer", "blockquote",
             "pre", "table", "tr", "ul", "ol", "dl", "figure", "h1", "h2", "h3",
             "h4", "h5", "h6", "hr"}
    SKIP = {"script", "style", "noscript", "template", "svg", "head", "iframe"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.skip = 0
        self.pre = 0

    def _para(self):
        self.out.append("\n\n")

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif self.skip:
            return
        elif tag == "br":
            self.out.append("\n")
        elif tag == "li":
            self.out.append("\n* ")
        elif tag in ("td", "th"):
            self.out.append("  ")
        elif tag == "img":
            alt = dict(attrs).get("alt") or ""
            self.out.append("[image: %s]" % alt.strip() if alt.strip() else "[image]")
        elif tag in self.BLOCK:
            self._para()
            if tag == "pre":
                self.pre += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif not self.skip and tag in self.BLOCK:
            self._para()
            if tag == "pre":
                self.pre = max(0, self.pre - 1)

    def handle_data(self, data):
        if self.skip:
            return
        if not self.pre:
            data = re.sub(r"\s+", " ", data)
        self.out.append(data)


def html_to_text(s):
    """Feed HTML (or plain text) to paragraphs separated by one empty line."""
    if not s:
        return ""
    if "<" not in s:
        s = html.escape(s).replace("\n\n", "<p>").replace("\n", "<br>")
    p = _TextParser()
    p.feed(s)
    p.close()
    text = clean("".join(p.out).replace("\t", "    ").replace("\r", ""))
    paras = []
    for block in re.split(r"\n\s*\n", text):
        lines = [ln.strip() for ln in block.split("\n")]
        block = "\n".join(ln for ln in lines if ln)
        if block:
            paras.append(block)
    return "\n\n".join(paras)


def wrap(text, width):
    """Wrap paragraphs to width; list items keep a hanging indent."""
    out = []
    for i, para in enumerate(text.split("\n\n")):
        if i:
            out.append("")
        for line in para.split("\n"):
            indent = "  " if line.startswith("* ") else ""
            out.extend(textwrap.wrap(line, width, subsequent_indent=indent,
                                     break_long_words=True, break_on_hyphens=False) or [""])
    return out


# Characters outside Latin-1 that have an obvious stand-in.
_LATIN1_MAP = {
    "‘": "'", "’": "'", "‚": ",", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "′": "'", "″": '"', "‹": "<", "›": ">",
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
    "―": "-", "−": "-", "…": "...", "•": "*", " ": " ",
    " ": " ", " ": " ", " ": " ", " ": " ",
    "€": "EUR", "™": "(TM)", "→": "->", "←": "<-",
    "Œ": "OE", "œ": "oe", "Š": "S", "š": "s",
    "Ž": "Z", "ž": "z", "Ÿ": "Y",
}


def to_latin1(s):
    out = []
    for ch in s:
        if ord(ch) < 256:
            out.append(ch)
        elif ch in _LATIN1_MAP:
            out.append(_LATIN1_MAP[ch])
        else:
            base = unicodedata.normalize("NFKD", ch)
            base = "".join(c for c in base if ord(c) < 256 and not unicodedata.combining(c))
            out.append(base or "?")
    return "".join(out)


def encode(s, charset):
    if charset == "latin1":
        return to_latin1(s).encode("latin-1", "replace")
    return s.encode("utf-8", "replace")
