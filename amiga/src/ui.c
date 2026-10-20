/* ui.c - the full screen reader for UP-Term/XCON and the script commands */
#include <exec/types.h>
#include <exec/memory.h>
#include <dos/dos.h>
#include <proto/exec.h>
#include <proto/dos.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>

#include "amifeed.h"

#define K_UP     0x100
#define K_DOWN   0x101
#define K_LEFT   0x102
#define K_RIGHT  0x103
#define K_PGUP   0x104
#define K_PGDN   0x105
#define K_ENTER  0x106
#define K_ESC    0x107
#define K_QUIT   0x108   /* Ctrl-C */

#define ART_MAX     300
#define STREAM_MAX  (ROWS_MAX + LOCAL_MAX + 2)
#define ITEM_BUF    (256 * 1024)
#define ITEM_LINES  6000

/* colours (xterm SGR; the Amiga dialect of XCON understands them too) */
#define C_BAR    "\x1b[0;7m"
#define C_DIM    "\x1b[0;36m"
#define C_BOLD   "\x1b[0m"      /* bold smears the bitmap font in XCON */
#define C_READ   "\x1b[0;36m"   /* read articles, empty streams */
#define C_STAR   "\x1b[0;33m"
#define C_ERR    "\x1b[0;1;31m"
#define C_SEL    "\x1b[0;7m"
#define C_OFF    "\x1b[0m"

struct Stream {
	char id[ID_MAX + 1];
	char title[100];
	char unread[8];
	int  kind;               /* 0 server stream, 1 the "Local feeds" entry, 2 one local feed */
	int  local;              /* index into local_feeds for kind 2 */
};

struct Art {
	char id[ID_MAX + 1];
	char flags[6];
	char date[17];
	char feed[64];
	char title[204];
};

static struct Stream streams[STREAM_MAX];
static struct Art arts[ART_MAX];
static struct Resp resp;
static int rows = 24, cols = 80;
static BPTR in, out;
static int raw_on;

static char ob[8192];
static int obn;

/* ---- output -------------------------------------------------------------- */

static void oflush(void)
{
	if (obn) {
		Write(out, ob, obn);
		obn = 0;
	}
}

static void ostr(const char *s)
{
	int n = strlen(s);

	if (obn + n > (int)sizeof(ob))
		oflush();
	if (n > (int)sizeof(ob)) {
		Write(out, (APTR)s, n);
		return;
	}
	memcpy(ob + obn, s, n);
	obn += n;
}

static int is_cont(unsigned char c)
{
	return !cfg.latin1 && (c & 0xC0) == 0x80;
}

int ucols(const char *s)
{
	int n = 0;

	for (; *s; s++)
		if (!is_cont((unsigned char)*s))
			n++;
	return n;
}

/* Write s cut (or padded with spaces) to exactly w columns. */
static void ocols(const char *s, int w)
{
	char buf[512];
	int n = 0, c = 0;

	while (*s && n < (int)sizeof(buf) - 8) {
		if (!is_cont((unsigned char)*s)) {
			if (c == w)
				break;
			c++;
		}
		buf[n++] = *s++;
	}
	while (c < w && n < (int)sizeof(buf) - 1) {
		buf[n++] = ' ';
		c++;
	}
	buf[n] = 0;
	ostr(buf);
}

static void goto_line(int y)
{
	char b[16];

	sprintf(b, "\x1b[%d;1H", y);
	ostr(b);
}

/* ---- input --------------------------------------------------------------- */

static void raw(int on)
{
	if (on != raw_on) {
		oflush();
		SetMode(in, on ? 1 : 0);
		raw_on = on;
		ostr(on ? "\x1b[?25l" : "\x1b[?25h");
		oflush();
	}
}

static int getc_wait(long usec)
{
	UBYTE c;

	if (usec >= 0 && !WaitForChar(in, usec))
		return -1;
	if (Read(in, &c, 1) != 1)
		return K_QUIT;
	return c;
}

static int read_key(void)
{
	char seq[16];
	int c, n = 0;

	oflush();
	if (SetSignal(0, 0) & SIGBREAKF_CTRL_C)
		return K_QUIT;
	c = getc_wait(-1);
	if (c == 3)
		return K_QUIT;
	if (c == 13 || c == 10)
		return K_ENTER;
	if (c == 0x1b) {
		c = getc_wait(80000);
		if (c < 0)
			return K_ESC;
		if (c != '[' && c != 'O')
			return K_ESC;
	} else if (c != 0x9b) {
		return c;
	}
	/* a CSI sequence: parameters, then a final byte from 0x40 to 0x7e */
	while ((c = getc_wait(200000)) >= 0 && n < (int)sizeof(seq) - 1) {
		seq[n++] = c;
		if (c >= 0x40 && c <= 0x7e)
			break;
	}
	seq[n] = 0;
	if (!strcmp(seq, "A"))
		return K_UP;
	if (!strcmp(seq, "B"))
		return K_DOWN;
	if (!strcmp(seq, "C"))
		return K_RIGHT;
	if (!strcmp(seq, "D"))
		return K_LEFT;
	if (!strcmp(seq, "5~") || !strcmp(seq, "T"))      /* xterm PgUp, Amiga shift-up */
		return K_PGUP;
	if (!strcmp(seq, "6~") || !strcmp(seq, "S"))
		return K_PGDN;
	return 0;
}

/* Ask the console how big the window is: xterm first, then the Amiga way. */
static void query_size(void)
{
	char b[32];
	int n = 0, c, r = 0, w = 0;

	ostr("\x1b[18t");
	oflush();
	while (n < (int)sizeof(b) - 1 && (c = getc_wait(300000)) >= 0) {
		b[n++] = c;
		if (c == 't')
			break;
	}
	b[n] = 0;
	if (sscanf(b, "\x1b[8;%d;%dt", &r, &w) != 2) {
		Write(out, "\x9b" "0 q", 4);
		n = 0;
		while (n < (int)sizeof(b) - 1 && (c = getc_wait(300000)) >= 0) {
			b[n++] = c;
			if (c == 'r')
				break;
		}
		b[n] = 0;
		if (sscanf(b, "\x9b" "1;1;%d;%d r", &r, &w) != 2)
			r = w = 0;
	}
	if (r >= 8 && w >= 30) {
		rows = r;
		cols = w;
	}
	if (cfg.height)
		rows = cfg.height;
	if (cfg.width)
		cols = cfg.width;
}

/* ---- frame --------------------------------------------------------------- */

static void bar(int y, const char *left, const char *right)
{
	int rw = ucols(right);

	goto_line(y);
	ostr(C_BAR);
	ostr(" ");
	ocols(left, cols - rw - 3 > 0 ? cols - rw - 3 : 0);
	ostr(right);
	ostr(" " C_OFF);              /* cols - 1 in all: the last column would scroll */
}

static void status(const char *colour, const char *msg)
{
	goto_line(rows);
	ostr(colour);
	ocols(msg, cols - 1);
	ostr(C_OFF);
	oflush();
}

static void clear(void)
{
	ostr("\x1b[0m\x1b[H\x1b[2J");
}

static int fail(const char *what)
{
	char m[300];

	sprintf(m, "%s: %s  (any key)", what, resp.status);
	status(C_ERR, m);
	return read_key();
}

/* Read a line with the console's own editing. */
static int prompt(const char *q, char *buf, int size)
{
	int n;

	raw(0);
	goto_line(rows);
	ostr("\x1b[0m\x1b[K");
	ostr(q);
	oflush();
	if (!FGets(in, buf, size))
		buf[0] = 0;
	n = strlen(buf);
	while (n > 0 && (buf[n - 1] == '\n' || buf[n - 1] == '\r'))
		buf[--n] = 0;
	raw(1);
	return n;
}

/* ---- list helper --------------------------------------------------------- */

struct Sel {
	int n, sel, top;
};

static int list_height(void)
{
	return rows - 2;
}

/* Handle a movement key; returns 1 when it was one. */
static int list_move(struct Sel *l, int k)
{
	int h = list_height();

	if (k == K_UP || k == 'k')
		l->sel--;
	else if (k == K_DOWN || k == 'j')
		l->sel++;
	else if (k == K_PGUP || k == 'b')
		l->sel -= h;
	else if (k == K_PGDN || k == ' ')
		l->sel += h;
	else if (k == '<')
		l->sel = 0;
	else if (k == '>')
		l->sel = l->n - 1;
	else
		return 0;
	if (l->sel >= l->n)
		l->sel = l->n - 1;
	if (l->sel < 0)
		l->sel = 0;
	if (l->sel < l->top)
		l->top = l->sel;
	if (l->sel >= l->top + h)
		l->top = l->sel - h + 1;
	return 1;
}

/* ---- the article view ---------------------------------------------------- */

static char *item_buf;
static char *item_line[ITEM_LINES];
static int item_n, item_used;
static char item_more[200];
static char item_kind[16], item_link[URL_MAX], item_feed[64], item_date[20];

static int is_local_id(const char *id)
{
	return id[0] == 'L';
}

/* Fetch (part of) an article; cursor NULL starts over. */
static int load_item(struct Art *a, const char *url, int full, const char *cursor)
{
	char path[URL_MAX * 3 + 120], enc[URL_MAX * 3], f[URL_MAX];
	int i, body = 0, len;

	sprintf(path, "/item?id=%s&w=%d%s", a->id, cols - 1, full ? "&full=1" : "");
	if (url) {
		urlenc(enc, url, sizeof(enc));
		strcat(path, "&url=");
		strcat(path, enc);
	}
	if (cursor) {
		strcat(path, "&c=");
		strcat(path, cursor);
	} else {
		item_n = item_used = 0;
	}
	status(C_DIM, full ? "Fetching the whole article..." : "Loading...");
	if (!request(&resp, "GET", path))
		return 0;
	strcpy(item_more, resp.more);
	for (i = 0; i < resp.nrows; i++) {
		if (!body) {
			if (!resp.rows[i][0]) {
				body = 1;
			} else if (resp.rows[i][0] == 'X') {
				field(resp.rows[i], 1, item_kind, sizeof(item_kind));
			} else if (resp.rows[i][0] == 'L') {
				field(resp.rows[i], 1, item_link, sizeof(item_link));
			} else if (resp.rows[i][0] == 'F') {
				field(resp.rows[i], 1, item_feed, sizeof(item_feed));
			} else if (resp.rows[i][0] == 'D') {
				field(resp.rows[i], 1, item_date, sizeof(item_date));
			} else if (resp.rows[i][0] == 'T' && !cursor) {
				field(resp.rows[i], 1, f, sizeof(a->title));
				strcpy(a->title, f);
			}
			continue;
		}
		len = strlen(resp.rows[i]) + 1;
		if (item_n >= ITEM_LINES || item_used + len > ITEM_BUF)
			break;
		item_line[item_n++] = memcpy(item_buf + item_used, resp.rows[i], len);
		item_used += len;
	}
	return 1;
}

static void set_flag(struct Art *a, char flag, int on)
{
	char *p = strchr(a->flags, flag);
	int n;

	if (on && !p) {
		n = strlen(a->flags);
		if (n < (int)sizeof(a->flags) - 1) {
			a->flags[n] = flag;
			a->flags[n + 1] = 0;
		}
	} else if (!on && p) {
		memmove(p, p + 1, strlen(p));
	}
}

static int mark_read(struct Art *a, int read)
{
	char path[80];

	if (is_local_id(a->id)) {
		set_seen(a->id, read);
		save_seen(0);
	} else {
		sprintf(path, "/mark?id=%s&read=%d", a->id, read);
		if (!request(&resp, "POST", path)) {
			fail("Could not mark the article");
			return 0;
		}
	}
	set_flag(a, 'r', read);
	return 1;
}

static int toggle_star(struct Art *a)
{
	char path[80];
	int on = !strchr(a->flags, 's');

	if (is_local_id(a->id)) {
		status(C_ERR, "Local articles cannot be starred (any key)");
		read_key();
		return 0;
	}
	sprintf(path, "/star?id=%s&on=%d", a->id, on);
	if (!request(&resp, "POST", path)) {
		fail("Could not star the article");
		return 0;
	}
	set_flag(a, 's', on);
	return 1;
}

/* Returns -1 for the previous article, 1 for the next, 0 to go back. */
static int read_article(struct Art *a, const char *url)
{
	int top = 0, k, h = rows - 5, i, full = 0;
	char right[40], m[URL_MAX + 20];

	if (!load_item(a, url, 0, NULL)) {
		fail("Could not load the article");
		return 0;
	}
	if (!strchr(a->flags, 'r'))
		mark_read(a, 1);
	for (;;) {
		/* the next part of a long article, when the reader gets near its end */
		if (item_more[0] && top + h >= item_n && !load_item(a, url, full, item_more)) {
			fail("Could not load the rest of the article");
			item_more[0] = 0;
		}
		clear();
		goto_line(1);
		bar(1, a->title, strchr(a->flags, 's') ? "*" : "");
		goto_line(2);
		ostr(C_DIM);
		sprintf(m, "%s  %s", item_feed, item_date);
		ocols(m, cols - 1);
		ostr(C_OFF);
		for (i = 0; i < h && top + i < item_n; i++) {
			goto_line(4 + i);         /* an empty line under feed and date */
			ocols(item_line[top + i], cols - 1);
		}
		sprintf(right, "%d%%", item_n ? ((top + h > item_n ? item_n : top + h) * 100 / item_n) : 100);
		if (!strcmp(item_kind, "excerpt"))
			bar(rows, "Excerpt only - f: whole article  spc/b page  n/p next/prev  s star  q back", right);
		else
			bar(rows, "spc/b page  n/p next/prev  s star  u unread  l link  f full  q back", right);
		k = read_key();
		if (k == K_QUIT || k == 'q' || k == K_LEFT || k == K_ESC)
			return 0;
		else if (k == ' ' || k == K_PGDN) {
			if (top + h < item_n)
				top += h - 1;
			else
				return 1;          /* space at the end goes on, like a pager */
		} else if (k == 'b' || k == K_PGUP)
			top = top > h - 1 ? top - (h - 1) : 0;
		else if (k == K_DOWN || k == 'j' || k == K_ENTER) {
			if (top + h < item_n)
				top++;
		} else if (k == K_UP || k == 'k') {
			if (top > 0)
				top--;
		} else if (k == 'n' || k == K_RIGHT)
			return 1;
		else if (k == 'p')
			return -1;
		else if (k == 's')
			toggle_star(a);
		else if (k == 'u') {
			if (mark_read(a, 0))
				return 0;
		} else if (k == 'l') {
			sprintf(m, "%s  (any key)", item_link);
			status(C_BOLD, m);
			read_key();
		} else if (k == 'f') {
			full = 1;
			top = 0;
			if (!load_item(a, url, 1, NULL)) {
				fail("Could not fetch the whole article");
				load_item(a, url, 0, NULL);
				full = 0;
			}
		}
	}
}

/* ---- the article list ---------------------------------------------------- */

static int n_arts;
static char list_more[200];

static int load_list(const char *stream, const char *url, int unread, int append)
{
	char path[URL_MAX * 3 + 120], enc[URL_MAX * 3];
	int i;

	if (url) {
		urlenc(enc, url, sizeof(enc));
		sprintf(path, "/list?url=%s&n=50", enc);
	} else {
		sprintf(path, "/list?s=%s&n=50%s", stream, unread ? "&unread=1" : "");
	}
	if (append && list_more[0]) {
		strcat(path, "&c=");
		urlenc(enc, list_more, sizeof(enc));
		strcat(path, enc);
	} else {
		n_arts = 0;
	}
	status(C_DIM, "Loading...");
	if (!request(&resp, "GET", path))
		return 0;
	strcpy(list_more, resp.more);
	for (i = 0; i < resp.nrows && n_arts < ART_MAX; i++) {
		struct Art *a = &arts[n_arts++];

		field(resp.rows[i], 0, a->id, sizeof(a->id));
		field(resp.rows[i], 1, a->flags, sizeof(a->flags));
		field(resp.rows[i], 2, a->date, sizeof(a->date));
		field(resp.rows[i], 3, a->feed, sizeof(a->feed));
		field(resp.rows[i], 4, a->title, sizeof(a->title));
		if (url && is_seen(a->id))
			set_flag(a, 'r', 1);
	}
	return 1;
}

static void draw_art(int y, struct Art *a, int selected)
{
	int unread = !strchr(a->flags, 'r');
	int fw = cols > 60 ? 14 : 0;

	goto_line(y);
	ostr(selected ? C_SEL : unread ? C_OFF : C_READ);
	ostr(strchr(a->flags, 's') ? "*" : " ");
	ostr(unread ? "o " : "  ");
	ocols(a->date + 5, 12);          /* "MM-DD HH:MM " */
	if (fw) {
		ocols(a->feed, fw);
		ostr(" ");
	}
	ocols(a->title, cols - 16 - (fw ? fw + 1 : 0) - 1);
	ostr(C_OFF);
}

static int add_feed(void);

static int article_list(const char *stream, const char *url, const char *title)
{
	struct Sel l = {0, 0, 0};
	int k, i, unread = url ? 0 : 1, r;
	char right[40];

	if (!load_list(stream, url, unread, 0)) {
		k = fail("Could not load the list");
		return k == K_QUIT;
	}
	for (;;) {
		l.n = n_arts;
		clear();
		sprintf(right, "%d%s%s", n_arts, list_more[0] ? "+" : "", unread ? " unread" : "");
		bar(1, title, right);
		if (!n_arts) {
			goto_line(3);
			ostr(unread ? "  No unread articles. U shows all." : "  Nothing here.");
		}
		for (i = 0; i < list_height() && l.top + i < n_arts; i++)
			draw_art(2 + i, &arts[l.top + i], l.top + i == l.sel);
		bar(rows, url ? "ret read  u read/unread  r reload  q back"
		              : "ret read  s star  u read/unread  U all/unread  a add feed  r reload  q back", "");
		k = read_key();
		if (k == K_QUIT)
			return 1;
		if (k == 'q' || k == K_LEFT || k == K_ESC)
			return 0;
		if (list_move(&l, k)) {
			if (l.sel == n_arts - 1 && list_more[0] && !load_list(stream, url, unread, 1))
				fail("Could not load more articles");
			continue;
		}
		if (!n_arts && k != 'U' && k != 'r' && k != 'a')
			continue;
		if (k == K_ENTER || k == K_RIGHT) {
			while ((r = read_article(&arts[l.sel], url)) != 0) {
				if (r > 0 && l.sel + 1 >= n_arts && list_more[0] &&
				    !load_list(stream, url, unread, 1))
					fail("Could not load more articles");
				l.n = n_arts;
				if (r > 0 && l.sel + 1 < n_arts)
					l.sel++;
				else if (r < 0 && l.sel > 0)
					l.sel--;
				else
					break;
			}
			list_move(&l, 0);
			if (l.sel < l.top || l.sel >= l.top + list_height())
				l.top = l.sel > list_height() / 2 ? l.sel - list_height() / 2 : 0;
		} else if (k == 's' && !url) {
			toggle_star(&arts[l.sel]);
		} else if (k == 'u') {
			mark_read(&arts[l.sel], !!strchr(arts[l.sel].flags, 'r') ? 0 : 1);
		} else if (k == 'U' && !url) {
			unread = !unread;
			l.sel = l.top = 0;
			if (!load_list(stream, url, unread, 0))
				fail("Could not load the list");
		} else if (k == 'r') {
			l.sel = l.top = 0;
			if (!load_list(stream, url, unread, 0))
				fail("Could not load the list");
		} else if (k == 'a') {
			add_feed();
		}
	}
}

/* ---- streams: categories, feeds, local feeds ----------------------------- */

static int add_feed(void)
{
	char url[URL_MAX], enc[URL_MAX * 3], path[URL_MAX * 3 + 40];
	int k;

	if (!prompt("Feed URL (empty to cancel): ", url, sizeof(url)))
		return 0;
	status(C_BOLD, "Add to the feed server (s) or only to this Amiga (l)? ");
	k = read_key();
	if (k == 'l') {
		if (!add_local_feed(url)) {
			status(C_ERR, "Could not save the feed in ENVARC:amifeed/feeds (any key)");
			read_key();
			return 0;
		}
		return 1;
	}
	if (k != 's')
		return 0;
	urlenc(enc, url, sizeof(enc));
	sprintf(path, "/subscribe?url=%s", enc);
	status(C_DIM, "Subscribing...");
	if (!request(&resp, "POST", path)) {
		fail("Could not subscribe");
		return 0;
	}
	return 1;
}

static int load_cats(int *n)
{
	int i;

	*n = 0;
	status(C_DIM, "Loading...");
	if (!request(&resp, "GET", "/cats"))
		return 0;
	for (i = 0; i < resp.nrows && *n < STREAM_MAX - 1; i++) {
		struct Stream *s = &streams[(*n)++];

		field(resp.rows[i], 0, s->id, sizeof(s->id));
		field(resp.rows[i], 1, s->unread, sizeof(s->unread));
		field(resp.rows[i], 2, s->title, sizeof(s->title));
		s->kind = 0;
	}
	if (n_local) {
		struct Stream *s = &streams[(*n)++];

		strcpy(s->id, "");
		sprintf(s->unread, "%d", n_local);
		strcpy(s->title, "Local feeds (on this Amiga)");
		s->kind = 1;
	}
	return 1;
}

static void draw_stream(int y, struct Stream *s, int selected)
{
	goto_line(y);
	ostr(selected ? C_SEL : (s->unread[0] && strcmp(s->unread, "0")) ? C_OFF : C_READ);
	ostr("  ");
	ocols(s->unread, 6);
	ostr(" ");
	ocols(s->title, cols - 10);
	ostr(C_OFF);
}

static int stream_view(struct Stream *list, int n, const char *title, int level);

/* Feeds in a category (level 1) or the local feeds (level 2) */
static int sub_streams(struct Stream *parent)
{
	static struct Stream subs[ROWS_MAX + LOCAL_MAX];
	char path[64], cat[ID_MAX + 1];
	int i, n = 0;

	if (parent->kind == 1) {
		for (i = 0; i < n_local; i++) {
			struct Stream *s = &subs[n++];

			s->id[0] = 0;
			s->unread[0] = 0;
			strncpy(s->title, local_feeds[i].name, sizeof(s->title) - 1);
			s->title[sizeof(s->title) - 1] = 0;
			s->kind = 2;
			s->local = i;
		}
		return stream_view(subs, n, "Local feeds", 2);
	}
	sprintf(path, "/feeds?c=%s", parent->id);
	status(C_DIM, "Loading...");
	if (!request(&resp, "GET", path))
		return fail("Could not load the feeds") == K_QUIT;
	for (i = 0; i < resp.nrows && n < ROWS_MAX; i++) {
		struct Stream *s = &subs[n++];

		field(resp.rows[i], 0, s->id, sizeof(s->id));
		field(resp.rows[i], 1, s->unread, sizeof(s->unread));
		field(resp.rows[i], 2, s->title, sizeof(s->title));
		field(resp.rows[i], 3, cat, sizeof(cat));
		s->kind = 0;
	}
	return stream_view(subs, n, parent->title, 1);
}

static int open_stream(struct Stream *s)
{
	if (s->kind == 1)
		return sub_streams(s);
	if (s->kind == 2)
		return article_list(NULL, local_feeds[s->local].url, s->title);
	return article_list(s->id, NULL, s->title);
}

/* level 0: categories, 1: feeds of a category, 2: local feeds */
static int stream_view(struct Stream *list, int n, const char *title, int level)
{
	struct Sel l = {0, 0, 0};
	int k, i;

	l.n = n;
	for (;;) {
		clear();
		bar(1, title, level ? "" : "amifeed " VERSION_STR);
		for (i = 0; i < list_height() && l.top + i < n; i++)
			draw_stream(2 + i, &list[l.top + i], l.top + i == l.sel);
		if (!n) {
			goto_line(3);
			ostr("  Nothing here.");
		}
		bar(rows, level == 0 ? "ret articles  f feeds  a add feed  r reload  q quit"
		                     : "ret articles  q back", "");
		k = read_key();
		if (k == K_QUIT)
			return 1;
		if (k == 'q' || k == K_ESC || (level && k == K_LEFT))
			return 0;
		if (list_move(&l, k) || !n)
			continue;
		if (k == K_ENTER || k == K_RIGHT) {
			if (open_stream(&list[l.sel]))
				return 1;
		} else if (k == 'f' && level == 0 && list[l.sel].kind != 2) {
			if (list[l.sel].kind == 1 || list[l.sel].id[0] == 'c') {
				if (sub_streams(&list[l.sel]))
					return 1;
			}
		}
		if (level == 0 && (k == 'r' || k == K_ENTER || k == K_RIGHT || k == 'a' || k == 'f')) {
			if (k == 'a')
				add_feed();
			if (!load_cats(&n))
				fail("Could not load the categories");
			l.n = n;
			if (l.sel >= n)
				l.sel = n ? n - 1 : 0;
		}
	}
}

int ui_main(void)
{
	int n;

	in = Input();
	out = Output();
	item_buf = AllocVec(ITEM_BUF, MEMF_ANY);
	if (!item_buf) {
		PutStr("amifeed: not enough memory\n");
		return RETURN_FAIL;
	}
	if (!IsInteractive(in)) {
		PutStr("amifeed: the reader needs a console window; see amifeed ? for commands\n");
		FreeVec(item_buf);
		return RETURN_WARN;
	}
	raw(1);
	query_size();
	if (!load_cats(&n))
		fail("Could not reach the proxy");
	else
		stream_view(streams, n, "amifeed", 0);
	save_seen(1);
	clear();
	raw(0);
	oflush();
	FreeVec(item_buf);
	return RETURN_OK;
}

/* ---- commands for scripts ------------------------------------------------ */

static void print_rows(void)
{
	int i;

	for (i = 0; i < resp.nrows; i++) {
		PutStr(resp.rows[i]);
		PutStr("\n");
	}
}

static int cmd_fail(void)
{
	Printf("amifeed: %s\n", resp.status);
	return RETURN_ERROR;
}

int cmd_list(const char *stream)
{
	char path[80];

	if (!stream)
		strcpy(path, "/cats");
	else
		sprintf(path, "/list?s=%s&n=100&unread=1", stream);
	if (!request(&resp, "GET", path))
		return cmd_fail();
	print_rows();
	return RETURN_OK;
}

int cmd_read(const char *id, const char *url)
{
	char path[URL_MAX * 3 + 80], enc[URL_MAX * 3];
	int i, body = 0;

	sprintf(path, "/item?id=%s&w=%d", id, cfg.width ? cfg.width - 1 : 76);
	if (url) {
		urlenc(enc, url, sizeof(enc));
		strcat(path, "&url=");
		strcat(path, enc);
	}
	if (!request(&resp, "GET", path))
		return cmd_fail();
	for (i = 0; i < resp.nrows; i++) {
		if (!body && !resp.rows[i][0])
			body = 1;
		if (body || resp.rows[i][0] == 'T' || resp.rows[i][0] == 'L') {
			PutStr(body ? resp.rows[i] : resp.rows[i] + 2);
			PutStr("\n");
		}
	}
	if (resp.more[0])
		PutStr("[...]\n");
	if (id[0] == 'L') {
		set_seen(id, 1);
		save_seen(0);
	} else {
		sprintf(path, "/mark?id=%s&read=1", id);
		if (!request(&resp, "POST", path))
			return cmd_fail();
	}
	return RETURN_OK;
}

int cmd_add(const char *url, int local)
{
	char path[URL_MAX * 3 + 40], enc[URL_MAX * 3];

	if (local) {
		if (!add_local_feed(url)) {
			PutStr("amifeed: could not save ENVARC:amifeed/feeds\n");
			return RETURN_ERROR;
		}
		return RETURN_OK;
	}
	urlenc(enc, url, sizeof(enc));
	sprintf(path, "/subscribe?url=%s", enc);
	if (!request(&resp, "POST", path))
		return cmd_fail();
	print_rows();
	return RETURN_OK;
}

int cmd_sync(void)
{
	save_seen(1);
	return cmd_list(NULL);
}
