"""Feeds the client keeps for itself: fetched and parsed on demand, no state."""

import calendar
import hashlib
import io
import threading
import time

import feedparser

from . import safefetch

MAX_ENTRIES = 200


def entry_id(feed_url, e):
    key = e.get("id") or e.get("guid") or ((e.get("link") or "") + "\n" + (e.get("title") or ""))
    return "L" + hashlib.sha1((feed_url + "\n" + key).encode("utf-8")).hexdigest()[:15]


class Entry:
    def __init__(self, id, title, link, published, html):
        self.id, self.title, self.link, self.published, self.html = id, title, link, published, html


class Feed:
    def __init__(self, url, title, entries):
        self.url, self.title, self.entries = url, title, entries


class LocalFeeds:
    """Parsed feeds in memory for ttl seconds; after that a conditional GET
    (ETag / Last-Modified) decides whether to parse again."""

    def __init__(self, ttl=900, max_feeds=100, **fetch_opts):
        self.ttl, self.max_feeds, self.fetch_opts = ttl, max_feeds, fetch_opts
        self.cache = {}  # url -> (fetched_at, etag, modified, Feed)
        self.lock = threading.Lock()

    def get(self, url):
        with self.lock:
            hit = self.cache.get(url)
        if hit and time.time() - hit[0] < self.ttl:
            return hit[3]
        headers = {}
        if hit and hit[1]:
            headers["If-None-Match"] = hit[1]
        if hit and hit[2]:
            headers["If-Modified-Since"] = hit[2]
        r = safefetch.fetch(url, headers=headers, **self.fetch_opts)
        if r.status == 304 and hit:
            feed = hit[3]
        else:
            feed = self._parse(url, r.body)
        with self.lock:
            if len(self.cache) >= self.max_feeds and url not in self.cache:
                del self.cache[min(self.cache, key=lambda k: self.cache[k][0])]
            self.cache[url] = (time.time(), r.headers.get("ETag"), r.headers.get("Last-Modified"), feed)
        return feed

    @staticmethod
    def _parse(url, body):
        # A file object, never bytes: feedparser fetches anything that looks like a URL.
        d = feedparser.parse(io.BytesIO(body))
        if d.bozo and not d.entries:
            raise safefetch.FetchError("UPSTREAM", "not a feed: %s" % url)
        entries = []
        for e in d.entries[:MAX_ENTRIES]:
            t = e.get("published_parsed") or e.get("updated_parsed")
            content = e.get("content") or []
            html = content[0].get("value") if content else e.get("summary", "")
            entries.append(Entry(entry_id(url, e), e.get("title", ""), e.get("link", ""),
                                 calendar.timegm(t) if t else 0, html or ""))
        entries.sort(key=lambda x: -x.published)
        return Feed(url, d.feed.get("title") or url, entries)
