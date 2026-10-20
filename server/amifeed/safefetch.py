"""Fetching feeds and web pages for a client, without letting the client reach
into the proxy's own network (SSRF)."""

import ipaddress
import socket
import urllib.error
import urllib.parse
import urllib.request

MAX_REDIRECTS = 5


class FetchError(Exception):
    """code is one of the protocol's error codes (BADREQ, UPSTREAM, TIMEOUT)."""

    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def _blocked(ip):
    ip = ipaddress.ip_address(ip)
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
            or ip.is_reserved or ip.is_unspecified)


def check_url(url, allow_private=False):
    """Raise FetchError unless url is http(s) to a public address."""
    try:
        u = urllib.parse.urlsplit(url)
        port = u.port
    except ValueError:
        raise FetchError("BADREQ", "invalid URL")
    if u.scheme not in ("http", "https") or not u.hostname:
        raise FetchError("BADREQ", "only http and https URLs")
    if allow_private:
        return
    try:
        infos = socket.getaddrinfo(u.hostname, port or (443 if u.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise FetchError("UPSTREAM", "cannot resolve %s" % u.hostname)
    for info in infos:
        if _blocked(info[4][0]):
            raise FetchError("BADREQ", "%s is not a public address" % u.hostname)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


class Response:
    def __init__(self, url, status, headers, body):
        self.url, self.status, self.headers, self.body = url, status, headers, body


def fetch(url, *, timeout=10, max_bytes=2 << 20, allow_private=False,
          user_agent="amifeed", headers=None):
    """GET url, following redirects by hand so every hop is checked.
    A 304 answer comes back as a Response with status 304 and an empty body."""
    for _ in range(MAX_REDIRECTS + 1):
        check_url(url, allow_private)
        req = urllib.request.Request(url, headers={"User-Agent": user_agent, **(headers or {})})
        try:
            r = _opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                url = urllib.parse.urljoin(url, e.headers["Location"])
                continue
            if e.code == 304:
                return Response(url, 304, e.headers, b"")
            raise FetchError("UPSTREAM", "HTTP %d from %s" % (e.code, urllib.parse.urlsplit(url).hostname))
        except (socket.timeout, TimeoutError):
            raise FetchError("TIMEOUT", "no answer from %s" % urllib.parse.urlsplit(url).hostname)
        except (urllib.error.URLError, OSError) as e:
            reason = getattr(e, "reason", e)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise FetchError("TIMEOUT", "no answer from %s" % urllib.parse.urlsplit(url).hostname)
            raise FetchError("UPSTREAM", "cannot fetch: %s" % reason)
        with r:
            body = r.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise FetchError("UPSTREAM", "document larger than %d bytes" % max_bytes)
        return Response(url, r.status, r.headers, body)
    raise FetchError("UPSTREAM", "too many redirects")
