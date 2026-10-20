import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from amifeed import app, cache, config, safefetch, textfmt
from amifeed.localfeed import LocalFeeds

from . import fakereader

TOKEN = "test-token-123456"


# -- text -----------------------------------------------------------------

def test_clean_removes_terminal_codes():
    s = "a\x1b[31mred\x1b[0m b\x9b2J c\x07d\x00e​f"
    assert textfmt.clean(s) == "ared b2J cdef"


def test_field_has_no_separators_and_is_capped():
    assert textfmt.field("a\tb\r\nc &amp; d") == "a b c & d"
    t = textfmt.title("x" * 500)
    assert len(t) == textfmt.TITLE_MAX and t.endswith("...")


def test_html_to_text_paragraphs_lists_and_scripts():
    html = ("<p>One <b>two</b></p><script>evil()</script><ul><li>a</li><li>b</li></ul>"
            "<p>three<br>four</p><img alt='cat'>")
    assert textfmt.html_to_text(html) == "One two\n\n* a\n* b\n\nthree\nfour\n\n[image: cat]"


def test_wrap_keeps_width_and_hanging_indent():
    lines = textfmt.wrap("word " * 40 + "\n\n* " + "item " * 30, 30)
    assert all(len(ln) <= 30 for ln in lines)
    assert "" in lines
    assert lines[-1].startswith("  ")


def test_latin1_transliteration():
    s = "“Hej” – räksmörgås… € ć 中"
    assert textfmt.to_latin1(s) == '"Hej" - räksmörgås... EUR c ?'
    assert textfmt.encode("åäö", "latin1") == b"\xe5\xe4\xf6"
    assert textfmt.encode("å", "utf8") == b"\xc3\xa5"


# -- SSRF -----------------------------------------------------------------

@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://192.168.1.1/feed", "http://[::1]/",
                                 "http://169.254.169.254/latest/meta-data", "http://10.1.2.3/",
                                 "file:///etc/passwd", "ftp://example.com/", "http:///nohost"])
def test_check_url_refuses(url):
    with pytest.raises(safefetch.FetchError):
        safefetch.check_url(url)


def test_check_url_allow_private():
    safefetch.check_url("http://192.168.1.1/feed", allow_private=True)


class _Redirector(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(302)
        self.send_header("Location", "http://localhost:%d/inside" % self.server.server_port)
        self.end_headers()


def test_redirect_hops_are_checked(monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Redirector)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    real = safefetch.check_url

    def check(url, allow_private=False):  # 127.0.0.1 plays "the internet", localhost "inside"
        if "localhost" in url:
            return real("http://127.0.0.1/")
    monkeypatch.setattr(safefetch, "check_url", check)
    with pytest.raises(safefetch.FetchError) as e:
        safefetch.fetch("http://127.0.0.1:%d/" % srv.server_port)
    assert e.value.code == "BADREQ"
    srv.shutdown()


# -- cache ----------------------------------------------------------------

def test_cache_ttl_and_cap(tmp_path, monkeypatch):
    c = cache.Cache(str(tmp_path / "c.sqlite"), ttl=100, max_bytes=1000)
    c.put("a", b"x" * 600)
    assert c.get("a") == b"x" * 600
    c.put("b", b"y" * 600)        # over the cap: the oldest goes
    assert c.get("a") is None and c.get("b") == b"y" * 600
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now + 200)
    assert c.get("b") is None     # expired


# -- the proxy end to end -------------------------------------------------

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Local One</title>
<item><title>Newer</title><link>https://example.com/2</link><guid>g2</guid>
<pubDate>Tue, 20 Oct 2026 10:00:00 GMT</pubDate><description>&lt;p&gt;Hi&lt;/p&gt;</description></item>
<item><title>Older</title><link>https://example.com/1</link><guid>g1</guid>
<pubDate>Mon, 19 Oct 2026 10:00:00 GMT</pubDate><description>Old</description></item>
</channel></rss>"""


class _FeedServer(BaseHTTPRequestHandler):
    hits = 0

    def log_message(self, *a):
        pass

    def do_GET(self):
        type(self).hits += 1
        if self.headers.get("If-None-Match") == '"v1"':
            self.send_response(304)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("ETag", '"v1"')
        self.send_header("Content-Length", str(len(RSS)))
        self.end_headers()
        self.wfile.write(RSS)


@pytest.fixture
def proxy(tmp_path):
    state = fakereader.State()
    fake, url = fakereader.start(state)
    feeds = ThreadingHTTPServer(("127.0.0.1", 0), _FeedServer)
    threading.Thread(target=feeds.serve_forever, daemon=True).start()
    conf = tmp_path / "amifeed.toml"
    conf.write_text('[server]\nlisten = "127.0.0.1"\nport = 0\ntoken = "%s"\n'
                    '[greader]\nurl = "%s"\nuser = "u"\npassword = "secret"\n'
                    '[fetch]\nallow_private_feeds = true\n'
                    '[cache]\npath = "%s"\n' % (TOKEN, url, tmp_path / "cache.sqlite"))
    cfg = config.load(str(conf))
    app.Handler.app = app.Amifeed(cfg)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % httpd.server_port

    def call(path, method="GET", token=TOKEN):
        req = urllib.request.Request(base + path, method=method,
                                     headers={"X-Token": token} if token else {})
        try:
            with urllib.request.urlopen(req) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            return e.read()

    yield call, state, "http://127.0.0.1:%d/feed.xml" % feeds.server_port
    httpd.shutdown()
    fake.shutdown()
    feeds.shutdown()


def lines(body):
    return body.decode("utf-8").split("\n")[:-1]


def test_auth_required(proxy):
    call, _, _ = proxy
    assert call("/cats", token=None) == b"ERR AUTH not allowed\n"
    assert call("/cats", token="wrong") == b"ERR AUTH not allowed\n"
    assert call("/cats?k=" + TOKEN, token=None).startswith(b"ERR AUTH")


def test_cats_and_feeds(proxy):
    call, _, _ = proxy
    out = lines(call("/cats"))
    assert out[0] == "OK n=3"
    assert out[1] == "all\t1\tAll articles" and out[2] == "star\t0\tStarred"
    cid, unread, title = out[3].split("\t")
    assert cid.startswith("c") and len(cid) <= 16 and unread == "1" and title == "News"
    feeds = lines(call("/feeds?c=" + cid))
    assert feeds[1] == "f1\t1\tFeed One\t" + cid


def test_list_flags_and_sanitising(proxy):
    call, _, _ = proxy
    out = lines(call("/list?s=all"))
    assert out[0] == "OK n=2"
    first, second = (r.split("\t") for r in out[1:])
    assert first[0] == "%016x" % 1 and first[1] == "" and first[4] == "First post"
    assert second[1] == "rsx"
    assert "\x1b" not in second[4] and "\x9b" not in second[4] and second[4].startswith("Tab here and red")


def test_list_paging_and_unread(proxy):
    call, _, _ = proxy
    out = lines(call("/list?s=all&n=1"))
    assert out[0] == "OK n=1 more=1"
    out = lines(call("/list?s=all&n=1&c=1"))
    assert out[0] == "OK n=1" and out[1].startswith("%016x" % 2)
    assert lines(call("/list?s=all&unread=1"))[0] == "OK n=1"
    assert call("/list?s=all&n=500").startswith(b"ERR BADREQ")
    assert call("/list?s=nosuch").startswith(b"ERR NOTFOUND")


def test_item_wraps_and_pages(proxy):
    call, _, _ = proxy
    out = lines(call("/item?id=%016x&w=40" % 1))
    assert out[1] == "T\tFirst post" and out[2] == "F\tFeed One"
    assert out[4] == "L\thttps://example.com/a" and out[5] == "X\tcontent" and out[6] == ""
    assert all(len(ln) <= 40 for ln in out[7:])
    assert out[0] == "OK n=%d" % len(out[7:])
    short = lines(call("/item?id=%016x" % 2))
    assert "X\texcerpt" in short


def test_item_latin1(proxy):
    call, state, _ = proxy
    state.items[0]["summary"]["content"] = "<p>räksmörgås – ok</p>"
    body = call("/item?id=%016x&cs=latin1" % 1)
    assert b"r\xe4ksm\xf6rg\xe5s - ok" in body


def test_mark_and_star_renew_token(proxy):
    call, state, _ = proxy
    assert call("/mark?id=%016x&read=1" % 1, "POST") == b"OK\n"
    assert state.edits[-1]["a"] == ["user/-/state/com.google/read"]
    state.tokens = set()          # the T token went stale
    state.next_token = "tok-2"
    assert call("/star?id=%016x&on=0" % 1, "POST") == b"OK\n"
    assert state.edits[-1]["r"] == ["user/-/state/com.google/starred"]
    assert state.edits[-1]["T"] == ["tok-2"]
    assert call("/mark?id=Labc&read=1", "POST").startswith(b"ERR BADREQ")
    assert call("/mark?id=%016x&read=2" % 1, "POST").startswith(b"ERR BADREQ")


def test_relogin_when_auth_expires(proxy):
    call, state, _ = proxy
    assert lines(call("/cats"))[0].startswith("OK")
    logins = state.logins
    state.auth_ok = set()         # the server forgot our session
    state.login_auth = "auth-2"
    assert lines(call("/cats"))[0].startswith("OK")
    assert state.logins == logins + 1


def test_subscribe(proxy):
    call, state, _ = proxy
    out = lines(call("/subscribe?url=https%3A%2F%2Fexample.com%2Ffeed&cat=News", "POST"))
    assert out == ["OK n=1", "f9\tNew Feed"]
    assert state.edits[-1]["a"] == ["user/-/label/News"]


def test_local_feed(proxy):
    call, _, feed = proxy
    q = urllib.parse.quote(feed, safe="")
    out = lines(call("/list?url=" + q))
    assert out[0] == "OK n=2"
    newer = out[1].split("\t")
    assert newer[0].startswith("L") and len(newer[0]) == 16 and newer[4] == "Newer"
    assert lines(call("/list?url=" + q))[1].split("\t")[0] == newer[0]   # stable id
    item = lines(call("/item?id=%s&url=%s" % (newer[0], q)))
    assert "T\tNewer" in item and item[-1] == "Hi"
    assert call("/item?id=" + newer[0]).startswith(b"ERR BADREQ")
    paged = lines(call("/list?url=%s&n=1" % q))
    assert paged[0] == "OK n=1 more=1"


def test_local_feed_conditional_get(tmp_path, monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FeedServer)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/feed.xml" % srv.server_port
    lf = LocalFeeds(ttl=0, allow_private=True)
    a = lf.get(url)
    b = lf.get(url)               # ttl 0: asks again, gets 304, keeps the parsed feed
    assert a is b and _FeedServer.hits >= 2
    srv.shutdown()


def test_local_feed_ssrf_without_allow(proxy):
    call, _, _ = proxy
    app.Handler.app.local.fetch_opts["allow_private"] = False
    assert call("/list?url=http%3A%2F%2F127.0.0.1%2Ffeed").startswith(b"ERR BADREQ")
