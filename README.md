# amifeed

A plain text RSS reader for small computers, and a proxy that does the hard
work for them.

- **Amiga client** (`amiga/`): a native AmigaOS 3.x program in C for a 68020 or
  better, no FPU and no ixemul needed. It runs full screen in a console window
  (made for UP-Term and XCON, works in a plain CON: window too) and has
  commands for scripts.
- **Linux client** (`linux/amifeed`): the same reader for any terminal, one
  Python file with no dependencies.
- **Proxy** (`server/`): a small Python service for Linux. It talks to your
  feed server through the Google Reader API, fetches and parses feeds, turns
  HTML into wrapped plain text and answers the clients in a tiny line based
  protocol ([PROTOCOL.md](PROTOCOL.md)). A 68020 cannot do TLS, JSON and HTML
  in any comfortable way; the proxy can.

```
 Amiga / terminal  --HTTP/1.0, plain text-->  amifeed proxy  --HTTPS, JSON-->  FreshRSS
                                                  |
                                                  +--HTTPS--> local feeds, article pages
```

What you get:

- Categories, feeds and articles from your feed server, with unread counts.
- Read and unread, starred, and subscribing to new feeds, synced right away
  with the feed server.
- Whole articles as text in the terminal. When a feed only has an excerpt,
  `f` fetches the web page and extracts the article (with trafilatura).
- Local feeds: feeds kept only on the client. The proxy fetches and parses
  them; which articles you have read is stored on the client.
- UTF-8 or ISO-8859-1 text (the proxy transliterates what does not fit).

Tested with **FreshRSS 1.29**. Other servers with a Google Reader API
(Miniflux, Inoreader, The Old Reader, ...) may work but have not been tried.

## The proxy

You need Docker, or Python 3.11 or newer.

1. Make a settings folder and copy the example:

   ```sh
   mkdir -p ~/.config/amifeed && cd ~/.config/amifeed
   cp /path/to/amifeed/server/config.example.toml amifeed.toml
   python3 -c "import secrets; print(secrets.token_urlsafe(18))" > token
   printf '%s' 'your-api-password' > greader-password
   chmod 600 amifeed.toml token greader-password
   ```

   Edit `amifeed.toml`: the `[greader]` URL and user, and `allow_nets` for
   your home network. In FreshRSS, set an API password under
   *Profile → API management* and allow API access in the
   authentication settings.

2. Start it with Docker:

   ```sh
   cd server
   echo "AMIFEED_CONFIG_DIR=$HOME/.config/amifeed" > .env
   docker compose up -d --build
   ```

   The container runs as uid 1000, so the files must be readable by it. It
   uses host networking, so `allow_nets` sees the real client addresses.

   Or without Docker:

   ```sh
   cd server
   python3 -m venv .venv && .venv/bin/pip install .
   .venv/bin/amifeed -c ~/.config/amifeed/amifeed.toml
   ```

3. Check it:

   ```sh
   curl -H "X-Token: $(cat ~/.config/amifeed/token)" http://127.0.0.1:8642/cats
   ```

   The first line should be `OK`, then one row per category.

**Security.** The clients talk plain HTTP: keep the proxy on your home
network and do not forward its port. Every request needs the token, and only
addresses in `allow_nets` are answered. Local feeds and full article fetches
refuse private and loopback addresses (also after redirects), so a client
cannot use the proxy to look around your network; set `allow_private_feeds`
if you want feeds from your own LAN.

## The Amiga client

Needs AmigaOS 3.0 or newer, a 68020 or better, and a running TCP/IP stack
with `bsdsocket.library` (Roadshow, AmiTCP, Miami). It uses about 600 KB
of memory.

Build it with the [vbcc Docker image](https://github.com/walkero-gr/docker4AmigaVBCC)
(it has the NDK and the Roadshow SDK):

```sh
cd amiga
make
```

Copy `amifeed` to the Amiga, for example to `C:`, and create
`ENVARC:amifeed/config`:

```
HOST=192.168.1.10
PORT=8642
TOKEN=the-token-from-the-proxy
```

Optional keys: `WIDTH=` and `HEIGHT=` if the console does not report its
size, and `CHARSET=latin1` for consoles without UTF-8 (UP-Term is UTF-8; a
plain CON: window wants latin1).

Start `amifeed` in a console window. For scripts:

```
amifeed list [stream]       categories, or the unread articles of a stream
amifeed read <id> [url]     print an article and mark it read
amifeed add <url> [LOCAL]   subscribe on the server, or only on this Amiga
amifeed sync                save the local read state, show unread counts
```

Local feeds are listed in `ENVARC:amifeed/feeds`, one per line, with an
optional name after the URL. The read state of their articles is kept in
`ENV:amifeed/seen` and saved to `ENVARC:` when you quit.

## The Linux client

```sh
ln -s /path/to/amifeed/linux/amifeed ~/.local/bin/amifeed
mkdir -p ~/.config/amifeed
printf 'HOST=127.0.0.1\nPORT=8642\nTOKEN_FILE=token\n' > ~/.config/amifeed/client.conf
```

`TOKEN=` works too. Local feeds and their read state live in the same folder
(`feeds`, `seen`). The commands are the same as on the Amiga.

## Keys

| Where | Key | |
|---|---|---|
| everywhere | up/down, `j`/`k` | move |
| | space, `b`, PgDn/PgUp | page |
| | `<` `>` (Home/End on Linux) | first, last |
| | Enter, right | open |
| | `q`, Esc, left | back |
| | Ctrl-C | quit |
| categories | `f` | the feeds of a category |
| | `a` | add a feed (to the server or local) |
| | `r` | reload |
| articles | `s` | star |
| | `u` | read / unread |
| | `U` | show all / only unread |
| article | space, `b` | page; space at the end goes to the next article |
| | `n` `p` | next, previous article |
| | `f` | fetch the whole article from the web |
| | `l` | show the link |
| | `u` | mark unread and go back |

An article is marked read when you open it.

## Writing another client

Everything a client needs is in [PROTOCOL.md](PROTOCOL.md): one HTTP/1.0
request per connection, a status line, then TAB separated rows. A client
needs a TCP socket and string handling, nothing more.

## Tests

```sh
cd server
python3 -m venv .venv && .venv/bin/pip install . pytest
.venv/bin/python -m pytest -q
```

The tests run against a fake Google Reader server and never touch the
network.

## License

MIT, see [LICENSE](LICENSE).
