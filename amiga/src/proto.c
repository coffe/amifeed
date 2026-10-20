/* proto.c - the status line and the TAB separated rows (PROTOCOL.md) */
#include <exec/types.h>
#include <string.h>

#include "amifeed.h"

static void copy_word(char *dst, const char *src, int size)
{
	int n = 0;

	while (*src && *src != ' ' && *src != '\n' && n < size - 1)
		dst[n++] = *src++;
	dst[n] = 0;
}

void parse_resp(struct Resp *r, long len)
{
	char *p = resp_buf, *nl, *m;
	int n;

	r->ok = 0;
	r->nrows = 0;
	r->more[0] = 0;
	r->status[0] = 0;

	nl = strchr(p, '\n');
	if (!nl) {
		strcpy(r->status, "empty answer from the proxy");
		return;
	}
	*nl = 0;
	if (!strncmp(p, "ERR ", 4)) {
		n = strlen(p + 4);
		if (n > (int)sizeof(r->status) - 1)
			n = sizeof(r->status) - 1;
		memcpy(r->status, p + 4, n);
		r->status[n] = 0;
		return;
	}
	if (strncmp(p, "OK", 2) || (p[2] && p[2] != ' ')) {
		strcpy(r->status, "unexpected answer from the proxy");
		return;
	}
	strncpy(r->status, p, sizeof(r->status) - 1);
	r->status[sizeof(r->status) - 1] = 0;
	m = strstr(p, " more=");
	if (m)
		copy_word(r->more, m + 6, sizeof(r->more));

	p = nl + 1;
	while (p < resp_buf + len && *p && r->nrows < ROWS_MAX) {
		nl = strchr(p, '\n');
		if (nl)
			*nl = 0;
		r->rows[r->nrows++] = p;
		if (!nl)
			break;
		p = nl + 1;
	}
	r->ok = 1;
}

char *field(const char *row, int i, char *out, int size)
{
	int n = 0;

	while (i > 0 && *row) {
		if (*row++ == '\t')
			i--;
	}
	while (*row && *row != '\t' && n < size - 1)
		out[n++] = *row++;
	out[n] = 0;
	return out;
}

int request(struct Resp *r, const char *method, const char *path)
{
	long len = http(method, path);

	if (len < 0) {
		r->ok = 0;
		r->nrows = 0;
		r->more[0] = 0;
		strncpy(r->status, err_msg, sizeof(r->status) - 1);
		r->status[sizeof(r->status) - 1] = 0;
		return 0;
	}
	parse_resp(r, len);
	return r->ok;
}
