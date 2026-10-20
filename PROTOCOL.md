# amifeed wire protocol (version 1)

The protocol is made for small clients: a 68020 Amiga with no FPU, a C64 with a
network cartridge, a terminal script. The proxy does everything hard (TLS, the
Google Reader API, feed parsing, HTML to text, character sets). The client only
opens a TCP connection, sends one request and reads plain text lines.

## Transport

- Plain HTTP/1.0. One request per connection. The proxy answers with
  `Connection: close` and closes the socket after the body, so a client can skip
  the header (everything up to the first empty line, `\r\n\r\n`) and read the body
  until `recv()` returns 0. No `Content-Length` or chunked parsing is needed.
- Every request carries the token in a header: `X-Token: <token>`.
  The token is never accepted in the URL, so it does not end up in logs.
- Query parameters are percent-encoded (`%20`, `%3A`, ...). `POST` requests use
  the query string too; the body is ignored.
- The proxy is meant for a home network. The channel is not encrypted; the
  proxy only answers addresses listed in its `allow_nets` setting.

## Response body

The first line is the status:

```
OK [n=<rows>] [more=<cursor>]
ERR <CODE> <human readable text>
```

Error codes are fixed so a client can switch on them:

| Code       | Meaning                                                  |
|------------|----------------------------------------------------------|
| `AUTH`     | missing or wrong `X-Token`, or address not allowed       |
| `BADREQ`   | unknown path, missing or invalid parameter               |
| `NOTFOUND` | no such id, stream or feed                               |
| `UPSTREAM` | the feed server or a web site answered with an error      |
| `TIMEOUT`  | the feed server or a web site did not answer in time      |
| `INTERNAL` | a bug in the proxy                                       |

The HTTP status is 200 for `OK` and for every `ERR` (so a client never has to
read the status line), except that `AUTH` also uses 403.

After the status line come the rows: fields separated by one TAB, rows ending
in LF (`\n`, no CR).

### Guarantees

- A field never contains TAB, CR or LF; the proxy replaces them with a space.
- No control characters reach the client: C0 (0x00-0x1F except TAB/LF used as
  separators), DEL, C1 (0x80-0x9F, including the Amiga CSI 0x9B) and ESC
  sequences are removed from everything that comes from feeds or web pages.
  The proxy never sends terminal codes; colours are the client's business.
- Titles are cut to 200 characters (with `...` at the end).
- A response body is at most 64 KiB. Longer article text continues with `more=`.

### Common parameters

| Param | Values               | Default | Meaning                                  |
|-------|----------------------|---------|------------------------------------------|
| `cs`  | `utf8`, `latin1`     | `utf8`  | character set of the body                |
| `w`   | 20-200               | 76      | wrap width for article text              |

With `cs=latin1` characters outside ISO-8859-1 are transliterated
(`"` for curly quotes, `-` for dashes, `...` for the ellipsis, accents dropped
where needed) or replaced by `?`.

## Ids

Ids are opaque strings of at most 16 characters from `[0-9A-Za-z_-]`.
A client stores and sends them back as they are and never looks inside.

- Streams: `all` (everything), `star` (starred), `c...` (a category),
  `f...` (a feed on the server).
- Articles on the server: 16 hex digits.
- Articles from a local feed: `L` followed by 15 hex digits, the same every time
  the same entry is fetched (a hash of the feed URL and the entry's guid, or of
  its link and title when there is no guid).

## Requests

### `GET /cats`
Categories with unread counts. `all` and `star` come first.

```
OK n=4
all	231	All articles
star	0	Starred
c5f1a09d2	37	Comics
c0b37e1aa	12	Nyheter
```
Fields: `id`, `unread`, `title`. The count for `star` is always 0.

### `GET /feeds[?c=<category id>]`
Feeds on the server, optionally only those in one category.
Fields: `id`, `unread`, `title`, `category id` (empty when none).

### `GET /list?s=<stream id>` or `GET /list?url=<feed url>`
Articles, newest first.

| Param    | Default | Meaning                                       |
|----------|---------|-----------------------------------------------|
| `n`      | 30      | rows per page, 1-100                          |
| `c`      |         | the cursor from a previous `more=`             |
| `unread` | 0       | 1 = only unread (server streams only)         |

```
OK n=2 more=AbC123
0006a1b2c3d4e5f6	s	2026-10-20 07:15	Ars Technica	A title
0006a1b2c3d4e5f7	rx	2026-10-20 06:58	Some blog	Another title
```
Fields: `id`, `flags`, `date` (`YYYY-MM-DD HH:MM`, the proxy's local time),
`feed title`, `title`.
Flags (any order, possibly empty): `r` read, `s` starred, `x` only an excerpt
is available (see `/item` with `full=1`).
For a local feed (`url=`) the proxy knows nothing about read state; the client
keeps it. `c` is then a row offset.

### `GET /item?id=<id>[&full=1][&c=<cursor>]`
One article. For a local article also pass the feed: `&url=<feed url>`.

```
OK n=12 more=40
T	The title
F	The feed title
D	2026-10-20 07:15
L	https://example.com/the/article
X	content

First wrapped line of the text...
```
Header rows (before the empty line): `T` title, `F` feed, `D` date, `L` link,
`X` what the text is:

- `content` - the article text from the feed (the normal case),
- `excerpt` - the feed only has a short summary; ask again with `full=1`,
- `extracted` - the proxy fetched the web page and took out the main text
  (`full=1`, or a feed the proxy is told always to fetch).

The text is wrapped to `w` columns; paragraphs are separated by one empty line.
`n` counts the text lines in this response. When the text goes on, `more=`
gives the cursor for the next part: ask again with `c=<cursor>` (the header rows
come again).

Asking for an article does not mark it read; the client does that with `/mark`.

### `POST /mark?id=<id>&read=0|1`
### `POST /star?id=<id>&on=0|1`
Set read or starred state of a server article. The answer is `OK`.
Local articles give `ERR BADREQ`.

### `POST /subscribe?url=<feed url>[&cat=<category title>]`
Subscribe on the server. The answer is `OK n=1` and one row:
`id`, `title` of the new feed.
