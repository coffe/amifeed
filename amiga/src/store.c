/* store.c - settings, local feeds and which local articles have been read.
 *
 * Everything lives in ENVARC:amifeed/ (kept on disk) and is read from ENV:
 * (the RAM copy AmigaOS makes at boot). While running, only ENV: is written;
 * ENVARC: is written when leaving the program or on "amifeed sync".
 */
#include <exec/types.h>
#include <dos/dos.h>
#include <proto/exec.h>
#include <proto/dos.h>
#include <string.h>
#include <stdlib.h>

#include "amifeed.h"

struct Config cfg;
struct LocalFeed local_feeds[LOCAL_MAX];
int n_local;

static char seen[SEEN_MAX][ID_MAX + 1];
static int n_seen, seen_head;    /* a ring: the oldest entry is dropped when full */
static int seen_dirty;

static BPTR open_either(const char *name)
{
	char path[64];
	BPTR f;

	strcpy(path, "ENV:amifeed/");
	strcat(path, name);
	f = Open(path, MODE_OLDFILE);
	if (!f) {
		strcpy(path, "ENVARC:amifeed/");
		strcat(path, name);
		f = Open(path, MODE_OLDFILE);
	}
	return f;
}

static int keyeq(const char *a, const char *b)
{
	while (*a && *b) {
		char x = *a++, y = *b++;
		if (x >= 'a' && x <= 'z')
			x -= 32;
		if (y >= 'a' && y <= 'z')
			y -= 32;
		if (x != y)
			return 0;
	}
	return !*a && !*b;
}

static void chomp(char *s)
{
	int n = strlen(s);

	while (n > 0 && (s[n - 1] == '\n' || s[n - 1] == '\r' || s[n - 1] == ' '))
		s[--n] = 0;
}

int load_config(void)
{
	char line[256], *eq, *v;
	BPTR f;

	strcpy(cfg.host, "");
	cfg.port = 8642;
	cfg.token[0] = 0;
	cfg.width = cfg.height = 0;
	cfg.latin1 = 0;

	f = open_either("config");
	if (!f)
		return 0;
	while (FGets(f, line, sizeof(line))) {
		chomp(line);
		if (line[0] == '#' || !(eq = strchr(line, '=')))
			continue;
		*eq = 0;
		v = eq + 1;
		if (keyeq(line, "HOST"))
			strncpy(cfg.host, v, sizeof(cfg.host) - 1);
		else if (keyeq(line, "PORT"))
			cfg.port = atoi(v);
		else if (keyeq(line, "TOKEN"))
			strncpy(cfg.token, v, sizeof(cfg.token) - 1);
		else if (keyeq(line, "WIDTH"))
			cfg.width = atoi(v);
		else if (keyeq(line, "HEIGHT"))
			cfg.height = atoi(v);
		else if (keyeq(line, "CHARSET"))
			cfg.latin1 = keyeq(v, "latin1");
	}
	Close(f);
	return cfg.host[0] && cfg.token[0];
}

void load_local_feeds(void)
{
	char line[URL_MAX + 80], *sp;
	BPTR f;

	n_local = 0;
	f = open_either("feeds");
	if (!f)
		return;
	while (n_local < LOCAL_MAX && FGets(f, line, sizeof(line))) {
		chomp(line);
		if (!line[0] || line[0] == '#')
			continue;
		sp = strchr(line, ' ');
		if (sp) {
			*sp++ = 0;
			while (*sp == ' ')
				sp++;
		}
		strncpy(local_feeds[n_local].url, line, URL_MAX - 1);
		local_feeds[n_local].url[URL_MAX - 1] = 0;
		strncpy(local_feeds[n_local].name, sp && *sp ? sp : line, 63);
		local_feeds[n_local].name[63] = 0;
		n_local++;
	}
	Close(f);
}

static int append_line(const char *path, const char *text)
{
	BPTR f = Open(path, MODE_READWRITE);

	if (!f)
		return 0;
	Seek(f, 0, OFFSET_END);
	FPuts(f, text);
	FPuts(f, "\n");
	Close(f);
	return 1;
}

int add_local_feed(const char *url)
{
	BPTR lock;

	if (n_local >= LOCAL_MAX)
		return 0;
	if ((lock = CreateDir("ENVARC:amifeed")))
		UnLock(lock);
	if ((lock = CreateDir("ENV:amifeed")))
		UnLock(lock);
	if (!append_line("ENVARC:amifeed/feeds", url))
		return 0;
	append_line("ENV:amifeed/feeds", url);
	load_local_feeds();
	return 1;
}

void load_seen(void)
{
	char line[64];
	BPTR f = open_either("seen");

	n_seen = seen_head = 0;
	if (!f)
		return;
	while (FGets(f, line, sizeof(line))) {
		chomp(line);
		if (line[0])
			set_seen(line, 1);
	}
	Close(f);
	seen_dirty = 0;
}

int is_seen(const char *id)
{
	int i;

	for (i = 0; i < n_seen; i++)
		if (!strcmp(seen[i], id))
			return 1;
	return 0;
}

void set_seen(const char *id, int on)
{
	int i;

	for (i = 0; i < n_seen; i++) {
		if (!strcmp(seen[i], id)) {
			if (!on)
				seen[i][0] = 0;     /* an empty slot is skipped when saved */
			seen_dirty = 1;
			return;
		}
	}
	if (!on)
		return;
	if (n_seen < SEEN_MAX) {
		i = n_seen++;
	} else {
		i = seen_head;
		seen_head = (seen_head + 1) % SEEN_MAX;
	}
	strncpy(seen[i], id, ID_MAX);
	seen[i][ID_MAX] = 0;
	seen_dirty = 1;
}

static void write_seen(const char *path)
{
	BPTR f = Open(path, MODE_NEWFILE);
	int i, k;

	if (!f)
		return;
	/* oldest first, so the ring order survives a reload */
	for (k = 0; k < n_seen; k++) {
		i = n_seen < SEEN_MAX ? k : (seen_head + k) % SEEN_MAX;
		if (seen[i][0]) {
			FPuts(f, seen[i]);
			FPuts(f, "\n");
		}
	}
	Close(f);
}

void save_seen(int archive)
{
	BPTR lock;

	if (!seen_dirty && !archive)
		return;
	if ((lock = CreateDir("ENV:amifeed")))
		UnLock(lock);
	write_seen("ENV:amifeed/seen");
	if (archive) {
		if ((lock = CreateDir("ENVARC:amifeed")))
			UnLock(lock);
		write_seen("ENVARC:amifeed/seen");
	}
	seen_dirty = 0;
}
