"""A small Google Reader API client (tested against FreshRSS)."""

import json
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request

ITEM_PREFIX = "tag:google.com,2005:reader/item/"
READ = "user/-/state/com.google/read"
STARRED = "user/-/state/com.google/starred"
READING_LIST = "user/-/state/com.google/reading-list"


class GReaderError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


class GReader:
    def __init__(self, url, user, password, timeout=20):
        self.base = url.rstrip("/")
        self.user, self.password, self.timeout = user, password, timeout
        self.auth = None
        self.token = None
        self.lock = threading.Lock()

    # -- plumbing ---------------------------------------------------------

    def _open(self, path, params=None, data=None, auth=True):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        body = urllib.parse.urlencode(data, doseq=True).encode() if data is not None else None
        req = urllib.request.Request(url, data=body)
        if auth:
            req.add_header("Authorization", "GoogleLogin auth=" + self.auth)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise _Unauthorized()
            if e.code == 404:
                raise GReaderError("NOTFOUND", "not found on the feed server")
            raise GReaderError("UPSTREAM", "feed server answered HTTP %d" % e.code)
        except (socket.timeout, TimeoutError):
            raise GReaderError("TIMEOUT", "feed server did not answer")
        except urllib.error.URLError as e:
            if isinstance(e.reason, (socket.timeout, TimeoutError)):
                raise GReaderError("TIMEOUT", "feed server did not answer")
            raise GReaderError("UPSTREAM", "cannot reach the feed server: %s" % e.reason)

    def _login(self):
        try:
            text = self._open("/accounts/ClientLogin",
                              data={"Email": self.user, "Passwd": self.password}, auth=False)
        except _Unauthorized:
            raise GReaderError("UPSTREAM", "feed server refused the login")
        for line in text.splitlines():
            if line.startswith("Auth="):
                self.auth = line[5:].strip()
                self.token = None
                return
        raise GReaderError("UPSTREAM", "no Auth in the login answer")

    def _get(self, path, params=None, data=None):
        """A read. data makes it a POST (some read calls want a form), without T token."""
        with self.lock:
            if not self.auth:
                self._login()
            auth = self.auth
        try:
            return self._open(path, params, data)
        except _Unauthorized:
            with self.lock:
                if self.auth == auth:
                    self._login()
            try:
                return self._open(path, params, data)
            except _Unauthorized:
                raise GReaderError("UPSTREAM", "feed server refused the request")

    def _post(self, path, data):
        """Writes need the short lived T token as well. FreshRSS answers a stale
        T token with 401, so on 401 fetch a new token, and if that is not enough,
        log in again."""
        for attempt in range(3):
            with self.lock:
                if not self.auth or attempt == 2:
                    self._login()
                if not self.token or attempt >= 1:
                    try:
                        self.token = self._open("/reader/api/0/token").strip()
                    except _Unauthorized:
                        self._login()
                        self.token = self._open("/reader/api/0/token").strip()
                token = self.token
            try:
                return self._open(path, data={**data, "T": token})
            except _Unauthorized:
                continue
        raise GReaderError("UPSTREAM", "feed server refused the change")

    def _json(self, path, params=None):
        params = dict(params or {}, output="json")
        try:
            return json.loads(self._get(path, params))
        except ValueError:
            raise GReaderError("UPSTREAM", "feed server sent something that is not JSON")

    # -- the calls amifeed needs ---------------------------------------------

    def tags(self):
        return self._json("/reader/api/0/tag/list").get("tags", [])

    def subscriptions(self):
        return self._json("/reader/api/0/subscription/list").get("subscriptions", [])

    def unread_counts(self):
        return {u["id"]: int(u.get("count", 0))
                for u in self._json("/reader/api/0/unread-count").get("unreadcounts", [])}

    def stream(self, stream_id, n=30, cont=None, unread_only=False):
        params = {"n": n}
        if cont:
            params["c"] = cont
        if unread_only:
            params["xt"] = READ
        path = "/reader/api/0/stream/contents/" + urllib.parse.quote(stream_id, safe="/-")
        d = self._json(path, params)
        return d.get("items", []), d.get("continuation")

    def items(self, long_ids):
        d = json.loads(self._get("/reader/api/0/stream/items/contents", {"output": "json"},
                                 data={"i": list(long_ids)}) or "{}")
        return d.get("items", [])

    def edit_tag(self, long_id, add=None, remove=None):
        data = {"i": long_id}
        if add:
            data["a"] = add
        if remove:
            data["r"] = remove
        if self._post("/reader/api/0/edit-tag", data).strip() != "OK":
            raise GReaderError("UPSTREAM", "feed server did not accept the change")

    def quickadd(self, url):
        d = json.loads(self._post("/reader/api/0/subscription/quickadd", {"quickadd": url}) or "{}")
        if not d.get("streamId"):
            raise GReaderError("UPSTREAM", d.get("error") or "feed server could not add the feed")
        return d

    def set_label(self, stream_id, label):
        self._post("/reader/api/0/subscription/edit",
                   {"ac": "edit", "s": stream_id, "a": "user/-/label/" + label})


class _Unauthorized(Exception):
    pass


def short_id(long_id):
    """'tag:google.com,2005:reader/item/0006...' -> '0006...' (16 hex digits)."""
    if long_id.startswith(ITEM_PREFIX):
        return long_id[len(ITEM_PREFIX):]
    try:  # some servers hand out decimal ids
        return "%016x" % int(long_id)
    except ValueError:
        return long_id


def long_id(sid):
    return ITEM_PREFIX + sid
