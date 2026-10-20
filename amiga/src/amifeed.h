/*
 * amifeed - a plain text RSS reader for AmigaOS 3.x (68020+).
 * Talks to the amifeed proxy; see PROTOCOL.md.
 */
#ifndef AMIFEED_H
#define AMIFEED_H

#include <exec/types.h>

#define VERSION_STR  "0.1"
#define ID_MAX       16          /* ids are at most 16 characters (PROTOCOL.md) */
#define RESP_MAX     (68 * 1024) /* a response body is at most 64 KiB, plus the HTTP header */
#define ROWS_MAX     200
#define FIELDS_MAX   6
#define SEEN_MAX     2000
#define LOCAL_MAX    50
#define URL_MAX      400

/* config and local state (store.c) */
struct Config {
	char host[128];
	int  port;
	char token[96];
	int  width, height;          /* 0 = ask the console */
	int  latin1;                 /* CHARSET=latin1: the proxy sends ISO-8859-1 */
};

struct LocalFeed {
	char url[URL_MAX];
	char name[64];
};

extern struct Config cfg;
extern struct LocalFeed local_feeds[LOCAL_MAX];
extern int n_local;

int  load_config(void);
void load_local_feeds(void);
int  add_local_feed(const char *url);
void load_seen(void);
int  is_seen(const char *id);
void set_seen(const char *id, int on);
void save_seen(int archive);     /* archive: also to ENVARC: */

/* network (net.c) */
int  net_open(void);
void net_close(void);
/* method "GET" or "POST"; path with query, without cs=/w= (added here).
 * Returns the body length, or -1 with err_msg set. The body is in resp_buf. */
long http(const char *method, const char *path);
extern char resp_buf[RESP_MAX + 1];
extern char err_msg[160];
void urlenc(char *dst, const char *src, int size);

/* protocol (proto.c) */
struct Resp {
	int   ok;
	char  status[200];           /* the status line, for ERR the message */
	char  more[200];             /* cursor, "" when there is no more */
	int   nrows;
	char *rows[ROWS_MAX];        /* point into resp_buf, fields still TAB separated */
};
/* Parse resp_buf (len bytes). ok=0 with status set on ERR or a broken body. */
void parse_resp(struct Resp *r, long len);
/* Field i (0 based) of a row, copied into out. Returns out. */
char *field(const char *row, int i, char *out, int size);
/* One request + parse. Returns r->ok. */
int  request(struct Resp *r, const char *method, const char *path);

/* text helpers (ui.c) */
int  ucols(const char *s);       /* display columns of s (UTF-8 aware) */

/* user interface (ui.c) */
int  ui_main(void);
int  cmd_list(const char *stream);
int  cmd_read(const char *id, const char *url);
int  cmd_add(const char *url, int local);
int  cmd_sync(void);

#endif
