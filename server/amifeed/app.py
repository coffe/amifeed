"""The HTTP side: PROTOCOL.md turned into code."""

import hashlib
import hmac
import ipaddress
import logging
import re
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import trafilatura

from . import greader as gr
from . import images, safefetch, textfmt
from .cache import Cache
from .localfeed import LocalFeeds

log = logging.getLogger("amifeed")

BODY_MAX = 64 * 1024
IMAGE_BODY_MAX = 1 << 20         # /img answers are sixel bytes, not text rows
TEXT_BUDGET = 60 * 1024          # article text per response, leaves room for the header rows
_HEX16 = re.compile(r"^[0-9a-f]{16}$")
_LOCAL = re.compile(r"^L[0-9a-f]{15}$")
_SEPARATORS = re.compile(r"[\t\r\n]")
_EXCERPT_TAIL = re.compile(r"(\.\.\.|…|\[…\]|\[\.\.\.\]|read more|continue reading|"
                           r"läs mer|weiterlesen)\W*$", re.I)


class ProtoError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def _date(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else ""


def _short_hash(prefix, s):
    return prefix + hashlib.sha1(s.encode("utf-8")).hexdigest()[:8]


class Amifeed:
    """Everything that does not depend on HTTP, so tests can call it directly."""

    def __init__(self, cfg):
        self.cfg = cfg
        g = cfg["greader"]
        self.reader = gr.GReader(g["url"], g["user"], g["password"])
        f = cfg["fetch"]
        self.fetch_opts = {"timeout": f["timeout"], "max_bytes": f["max_bytes"],
                           "allow_private": f["allow_private_feeds"], "user_agent": f["user_agent"]}
        c = cfg["cache"]
        self.cache = Cache(c["path"], c["ttl_days"] * 86400, c["max_mb"] << 20)
        self.local = LocalFeeds(ttl=c["local_feed_ttl"], **self.fetch_opts)
        self.streams = {}       # short stream id -> server stream id
        self.fulltext = set(cfg["text"]["fulltext_feeds"])
        self.image_fetch_opts = dict(self.fetch_opts, max_bytes=cfg["images"]["max_bytes"])

    # -- ids ----------------------------------------------------------------

    def _stream_short(self, sid):
        if sid.startswith("feed/") and sid[5:].isdigit() and len(sid) <= 20:
            short = "f" + sid[5:]
        elif sid.startswith("feed/"):
            short = _short_hash("f", sid)
        else:
            short = _short_hash("c", sid)
        self.streams[short] = sid
        return short

    def _stream_long(self, short):
        if short == "all":
            return gr.READING_LIST
        if short == "star":
            return gr.STARRED
        if short not in self.streams:  # e.g. after a restart: learn the ids again
            self.cats()
            self.feeds()
        if short not in self.streams:
            raise ProtoError("NOTFOUND", "no such stream")
        return self.streams[short]

    # -- text ---------------------------------------------------------------

    def _is_excerpt(self, text):
        return len(text) < self.cfg["text"]["excerpt_chars"] or bool(_EXCERPT_TAIL.search(text))

    def _extract(self, key, link):
        cached = self.cache.get(key)
        if cached is not None:
            return cached.decode("utf-8")
        if not link:
            raise safefetch.FetchError("NOTFOUND", "the article has no link")
        r = safefetch.fetch(link, **self.fetch_opts)
        text = trafilatura.extract(r.body, url=r.url, include_comments=False,
                                   include_tables=True, include_images=True,
                                   favor_recall=True) or ""
        text = "\n\n".join(p.strip() for p in text.split("\n") if p.strip())
        if not text:
            raise safefetch.FetchError("UPSTREAM", "no article text found on the page")
        self.cache.put(key, text.encode("utf-8"))
        return text

    # -- requests -----------------------------------------------------------

    def cats(self):
        counts = self.reader.unread_counts()
        rows = [["all", str(counts.get(gr.READING_LIST, sum(v for k, v in counts.items()
                                                             if k.startswith("feed/")))),
                 "All articles"],
                ["star", "0", "Starred"]]
        for t in self.reader.tags():
            sid = t.get("id", "")
            if "/label/" not in sid:
                continue
            rows.append([self._stream_short(sid), str(counts.get(sid, 0)),
                         textfmt.title(sid.split("/label/", 1)[1])])
        return rows, None

    def feeds(self, cat=None):
        counts = self.reader.unread_counts()
        rows = []
        for s in self.reader.subscriptions():
            cats = [c.get("id", "") for c in s.get("categories", [])]
            cat_short = self._stream_short(cats[0]) if cats else ""
            if cat and cat not in [self._stream_short(c) for c in cats]:
                continue
            rows.append([self._stream_short(s["id"]), str(counts.get(s["id"], 0)),
                         textfmt.title(s.get("title", "")), cat_short])
        return rows, None

    def list(self, s=None, url=None, n=30, c=None, unread=False):
        if url:
            feed = self.local.get(url)
            start = int(c) if c and c.isdigit() else 0
            part = feed.entries[start:start + n]
            rows = [[e.id, "x" if self._is_excerpt(textfmt.html_to_text(e.html)) else "",
                     _date(e.published), textfmt.title(feed.title), textfmt.title(e.title)]
                    for e in part]
            more = str(start + n) if start + n < len(feed.entries) else None
            return rows, more
        items, cont = self.reader.stream(self._stream_long(s), n=n, cont=c, unread_only=unread)
        rows = []
        for it in items:
            cats = it.get("categories", [])
            flags = ("r" if any(x.endswith("/state/com.google/read") for x in cats) else "") + \
                    ("s" if any(x.endswith("/state/com.google/starred") for x in cats) else "")
            if self._is_excerpt(textfmt.html_to_text(self._item_html(it))):
                flags += "x"
            rows.append([gr.short_id(it["id"]), flags, _date(it.get("published", 0)),
                         textfmt.title(it.get("origin", {}).get("title", "")),
                         textfmt.title(it.get("title", ""))])
        return rows, cont

    @staticmethod
    def _item_html(it):
        return (it.get("content") or it.get("summary") or {}).get("content", "")

    @staticmethod
    def _item_link(it):
        for a in it.get("alternate", []) or it.get("canonical", []):
            if a.get("href"):
                return a["href"]
        return ""

    def _article(self, id, url=None, full=False):
        """Returns (header dict, text, kind, picture addresses)."""
        if _LOCAL.match(id):
            if not url:
                raise ProtoError("BADREQ", "a local article needs url=")
            e = next((e for e in self.local.get(url).entries if e.id == id), None)
            if not e:
                raise ProtoError("NOTFOUND", "no such article in the feed")
            head = {"T": e.title, "F": self.local.get(url).title, "D": _date(e.published), "L": e.link}
            body, feed_key = e.html, url
        elif _HEX16.match(id):
            items = self.reader.items([gr.long_id(id)])
            if not items:
                raise ProtoError("NOTFOUND", "no such article")
            it = items[0]
            origin = it.get("origin", {})
            head = {"T": it.get("title", ""), "F": origin.get("title", ""),
                    "D": _date(it.get("published", 0)), "L": self._item_link(it)}
            body, feed_key = self._item_html(it), origin.get("streamId", "")
        else:
            raise ProtoError("BADREQ", "invalid id")

        pics = []
        text = textfmt.html_to_text(body, pics, head["L"])
        kind = "excerpt" if self._is_excerpt(text) else "content"
        if full or feed_key in self.fulltext or url in self.fulltext:
            try:
                page = self._extract("x2:" + id, head["L"])
                pics = []
                text = textfmt.md_images(page, pics, head["L"])
                kind = "extracted"
            except safefetch.FetchError as err:
                text = "[Could not fetch the full article: %s]\n\n%s" % (err, text)
        return head, text, kind, pics

    def item(self, id, url=None, full=False, c=None, width=76):
        head, text, kind, pics = self._article(id, url, full)
        lines = textfmt.wrap(text, width)
        start = int(c) if c and c.isdigit() else 0
        out, size, i = [], 0, start
        while i < len(lines):
            size += len(lines[i].encode("utf-8")) + 1
            if size > TEXT_BUDGET:
                break
            out.append(lines[i])
            i += 1
        rows = [[k, textfmt.title(v) if k != "L" else textfmt.field(v, 1000)] for k, v in head.items()]
        rows += [["X", kind]]
        if pics:
            rows += [["I", str(len(pics))]]
        rows += [[""]] + [[ln] for ln in out]
        return rows, (str(i) if i < len(lines) else None), len(out)

    def image(self, id, n, url=None, full=False, w=320, h=240, kb=60):
        """Picture n (1 based) of an article as sixel, with a status line in front."""
        pics = self._article(id, url, full)[3]
        if not 1 <= n <= len(pics):
            raise ProtoError("NOTFOUND", "the article has %d pictures" % len(pics))
        src = pics[n - 1]
        key = "i:" + hashlib.sha1(("%s\n%d %d %d" % (src, w, h, kb)).encode("utf-8")).hexdigest()
        out = self.cache.get(key)
        if out is None:
            r = safefetch.fetch(src, **self.image_fetch_opts)
            try:
                six, pw, ph = images.to_sixel(r.body, w, h, kb * 1024)
            except images.ImageError as e:
                raise safefetch.FetchError("UPSTREAM", str(e))
            out = b"OK w=%d h=%d\n" % (pw, ph) + six
            self.cache.put(key, out)
        return out

    def _server_id(self, id):
        if _LOCAL.match(id):
            raise ProtoError("BADREQ", "local articles are only marked on the client")
        if not _HEX16.match(id):
            raise ProtoError("BADREQ", "invalid id")
        return gr.long_id(id)

    def mark(self, id, read):
        lid = self._server_id(id)
        self.reader.edit_tag(lid, add=gr.READ if read else None, remove=None if read else gr.READ)
        return [], None

    def star(self, id, on):
        lid = self._server_id(id)
        self.reader.edit_tag(lid, add=gr.STARRED if on else None, remove=None if on else gr.STARRED)
        return [], None

    def subscribe(self, url, cat=None):
        safefetch.check_url(url, self.fetch_opts["allow_private"])
        d = self.reader.quickadd(url)
        if cat:
            self.reader.set_label(d["streamId"], cat)
        return [[self._stream_short(d["streamId"]), textfmt.title(d.get("streamName") or url)]], None


def _int(q, name, default, lo, hi):
    v = q.get(name, str(default))
    if not v.isdigit() or not lo <= int(v) <= hi:
        raise ProtoError("BADREQ", "%s must be %d-%d" % (name, lo, hi))
    return int(v)


def _bool(q, name):
    v = q.get(name, "0")
    if v not in ("0", "1"):
        raise ProtoError("BADREQ", "%s must be 0 or 1" % name)
    return v == "1"


def _need(q, name):
    if not q.get(name):
        raise ProtoError("BADREQ", "%s= is missing" % name)
    return q[name]


def dispatch(app, method, path, q):
    """Returns (rows, more, n), or bytes for /img. Raises ProtoError, GReaderError
    or FetchError."""
    if method == "GET" and path == "/cats":
        return (*app.cats(), None)
    if method == "GET" and path == "/feeds":
        return (*app.feeds(q.get("c")), None)
    if method == "GET" and path == "/list":
        n = _int(q, "n", 30, 1, 100)
        if q.get("url"):
            return (*app.list(url=q["url"], n=n, c=q.get("c")), None)
        return (*app.list(s=_need(q, "s"), n=n, c=q.get("c"), unread=_bool(q, "unread")), None)
    if method == "GET" and path == "/item":
        return app.item(_need(q, "id"), url=q.get("url"), full=_bool(q, "full"), c=q.get("c"),
                        width=_int(q, "w", 76, 20, 200))
    if method == "POST" and path == "/mark":
        return (*app.mark(_need(q, "id"), _bool(q, "read")), None)
    if method == "POST" and path == "/star":
        return (*app.star(_need(q, "id"), _bool(q, "on")), None)
    if method == "POST" and path == "/subscribe":
        return (*app.subscribe(_need(q, "url"), q.get("cat")), None)
    if method == "GET" and path == "/img":
        return app.image(_need(q, "id"), _int(q, "n", 1, 1, 999), url=q.get("url"),
                         full=_bool(q, "full"), w=_int(q, "w", 320, 16, 2000),
                         h=_int(q, "h", 240, 16, 2000), kb=_int(q, "kb", 60, 4, 1000))
    raise ProtoError("BADREQ", "unknown request %s %s" % (method, path))


def render(rows, more, n):
    status = "OK"
    if rows:
        status += " n=%d" % (n if n is not None else len(rows))
    if more:
        status += " more=" + textfmt.field(more, 200).replace(" ", "")
    # Fields were cleaned when they were made; this is the last line of defence.
    lines = [status] + ["\t".join(textfmt.clean(_SEPARATORS.sub(" ", f)) for f in r) for r in rows]
    return "\n".join(lines) + "\n"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "amifeed"
    sys_version = ""
    timeout = 30
    app = None  # set by serve()

    def log_message(self, fmt, *args):
        log.info("%s %s", self.client_address[0], fmt % args)

    def _reply(self, text, charset, status=200):
        if isinstance(text, bytes):  # a picture from /img
            body, ctype = text, "application/octet-stream"
            if len(body) > IMAGE_BODY_MAX:
                body = b"ERR INTERNAL response too large\n"
        else:
            body = textfmt.encode(text, charset)
            if len(body) > BODY_MAX:  # should not happen; never send more than promised
                body = textfmt.encode("ERR INTERNAL response too large\n", charset)
            ctype = "text/plain; charset=%s" % ("iso-8859-1" if charset == "latin1" else "utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _handle(self, method):
        u = urllib.parse.urlsplit(self.path)
        q = {k: v[-1] for k, v in urllib.parse.parse_qs(u.query, keep_blank_values=True).items()}
        charset = "latin1" if q.get("cs") == "latin1" else "utf8"
        cfg = self.app.cfg["server"]
        try:
            ip = ipaddress.ip_address(self.client_address[0])
            if ip.version == 6 and ip.ipv4_mapped:
                ip = ip.ipv4_mapped
            token = self.headers.get("X-Token", "")
            if not any(ip in net for net in cfg["allow_nets"]) or \
                    not hmac.compare_digest(token.encode(), cfg["token"].encode()):
                return self._reply("ERR AUTH not allowed\n", charset, 403)
            if q.get("cs", "utf8") not in ("utf8", "latin1"):
                raise ProtoError("BADREQ", "cs must be utf8 or latin1")
            res = dispatch(self.app, method, u.path, q)
            text = res if isinstance(res, bytes) else render(*res)
        except (ProtoError, gr.GReaderError, safefetch.FetchError) as e:
            text = "ERR %s %s\n" % (e.code, textfmt.field(str(e), 200))
        except Exception:
            log.error("internal error on %s %s\n%s", method, u.path, traceback.format_exc())
            text = "ERR INTERNAL something went wrong in the proxy\n"
        self._reply(text, charset)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")


def serve(cfg):
    Handler.app = Amifeed(cfg)
    httpd = ThreadingHTTPServer((cfg["server"]["listen"], cfg["server"]["port"]), Handler)
    httpd.daemon_threads = True
    log.info("amifeed listening on %s:%d", cfg["server"]["listen"], cfg["server"]["port"])
    httpd.serve_forever()
