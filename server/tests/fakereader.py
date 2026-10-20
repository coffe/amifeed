"""A tiny stand-in for a Google Reader API server (FreshRSS flavour)."""

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PREFIX = "tag:google.com,2005:reader/item/"


def item(n, title, html, read=False, star=False, feed="feed/1", feed_title="Feed One",
         link="https://example.com/a"):
    cats = ["user/-/state/com.google/reading-list", "user/-/label/News"]
    if read:
        cats.append("user/-/state/com.google/read")
    if star:
        cats.append("user/-/state/com.google/starred")
    return {"id": PREFIX + "%016x" % n, "title": title, "published": 1792500000 + n,
            "categories": cats, "alternate": [{"href": link}],
            "summary": {"content": html}, "origin": {"streamId": feed, "title": feed_title}}


class State:
    def __init__(self):
        self.auth_ok = set()
        self.login_auth = "auth-1"
        self.tokens = {"tok-1"}
        self.next_token = "tok-1"
        self.logins = 0
        self.token_calls = 0
        self.edits = []
        self.items = [
            item(1, "First <b>post</b>", "<p>" + "Long text. " * 60 + "</p>"),
            item(2, "Tab\there\nand \x1b[31mred\x1b[0m \x9b2J", "<p>short</p>", read=True, star=True),
        ]


def make(state):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="text/plain"):
            b = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def _authed(self):
            a = self.headers.get("Authorization", "")
            return a.startswith("GoogleLogin auth=") and a[17:] in state.auth_ok

        def _form(self):
            n = int(self.headers.get("Content-Length", 0))
            return urllib.parse.parse_qs(self.rfile.read(n).decode())

        def do_GET(self):
            u = urllib.parse.urlsplit(self.path)
            q = urllib.parse.parse_qs(u.query)
            p = u.path.replace("/api/greader.php", "")
            if not self._authed():
                return self._send(401, "Unauthorized!")
            if p == "/reader/api/0/token":
                state.token_calls += 1
                state.tokens.add(state.next_token)
                return self._send(200, state.next_token + "\n")
            if p == "/reader/api/0/tag/list":
                return self._send(200, json.dumps({"tags": [
                    {"id": "user/-/state/com.google/starred"},
                    {"id": "user/-/label/News", "type": "folder"}]}))
            if p == "/reader/api/0/subscription/list":
                return self._send(200, json.dumps({"subscriptions": [
                    {"id": "feed/1", "title": "Feed One",
                     "categories": [{"id": "user/-/label/News", "label": "News"}]}]}))
            if p == "/reader/api/0/unread-count":
                return self._send(200, json.dumps({"unreadcounts": [
                    {"id": "feed/1", "count": 1}, {"id": "user/-/label/News", "count": 1},
                    {"id": "user/-/state/com.google/reading-list", "count": 1}]}))
            if p.startswith("/reader/api/0/stream/contents/"):
                items = state.items
                if q.get("xt") == ["user/-/state/com.google/read"]:
                    items = [i for i in items if "user/-/state/com.google/read" not in i["categories"]]
                n = int(q.get("n", ["20"])[0])
                start = int(q.get("c", ["0"])[0])
                part = items[start:start + n]
                out = {"items": part}
                if start + n < len(items):
                    out["continuation"] = str(start + n)
                return self._send(200, json.dumps(out))
            if p == "/reader/api/0/stream/items/contents":
                return self._send(400, "FreshRSS wants POST here")
            self._send(404, "nope")

        def do_POST(self):
            p = urllib.parse.urlsplit(self.path).path.replace("/api/greader.php", "")
            f = self._form()
            if p == "/accounts/ClientLogin":
                state.logins += 1
                if f.get("Passwd") == ["secret"]:
                    state.auth_ok.add(state.login_auth)
                    return self._send(200, "SID=x\nLSID=x\nAuth=%s\n" % state.login_auth)
                return self._send(401, "Unauthorized!")
            if not self._authed():
                return self._send(401, "Unauthorized!")
            if p == "/reader/api/0/stream/items/contents":   # a read: no T token needed
                want = set(f.get("i", []))
                return self._send(200, json.dumps({"items": [i for i in state.items if i["id"] in want]}))
            if f.get("T", [""])[0] not in state.tokens:
                return self._send(401, "Unauthorized!")
            if p == "/reader/api/0/edit-tag":
                state.edits.append(f)
                return self._send(200, "OK")
            if p == "/reader/api/0/subscription/quickadd":
                return self._send(200, json.dumps({"numResults": 1, "query": f["quickadd"][0],
                                                   "streamId": "feed/9", "streamName": "New Feed"}))
            if p == "/reader/api/0/subscription/edit":
                state.edits.append(f)
                return self._send(200, "OK")
            self._send(404, "nope")

    return H


def start(state):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make(state))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d/api/greader.php" % srv.server_port
