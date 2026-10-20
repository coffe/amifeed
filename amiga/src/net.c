/* net.c - one HTTP/1.0 request per connection, through bsdsocket.library */
#include <exec/types.h>
#include <dos/dos.h>
#include <proto/exec.h>
#include <proto/dos.h>
#include <proto/bsdsocket.h>
#include <sys/socket.h>
#include <sys/filio.h>
#include <sys/errno.h>
#include <netinet/in.h>
#include <netdb.h>
#include <string.h>
#include <stdio.h>

#include "amifeed.h"

struct Library *SocketBase;

char resp_buf[RESP_MAX + 1];
char err_msg[160];

int net_open(void)
{
	SocketBase = OpenLibrary("bsdsocket.library", 4);
	if (!SocketBase) {
		strcpy(err_msg, "no bsdsocket.library - is the TCP/IP stack (Roadshow) running?");
		return 0;
	}
	return 1;
}

void net_close(void)
{
	if (SocketBase) {
		CloseLibrary(SocketBase);
		SocketBase = NULL;
	}
}

void urlenc(char *dst, const char *src, int size)
{
	static const char hex[] = "0123456789ABCDEF";
	int n = 0;
	unsigned char c;

	while ((c = (unsigned char)*src++) && n < size - 4) {
		if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
		    c == '-' || c == '_' || c == '.' || c == '~') {
			dst[n++] = c;
		} else {
			dst[n++] = '%';
			dst[n++] = hex[c >> 4];
			dst[n++] = hex[c & 15];
		}
	}
	dst[n] = 0;
}

/*
 * The socket is non-blocking and every wait goes through WaitSelect() with a
 * timeout, so a link that drops in the middle of a request (PPP over the
 * serial port does) gives an error instead of a reader that hangs forever.
 * Ctrl-C breaks the wait too.
 */
#define CONNECT_SECS 15
#define IDLE_SECS    40   /* full=1 makes the proxy fetch a web page first */

/* Wait until s can be read (write=0) or written (write=1).
 * Returns 1 when ready, 0 on timeout, -1 on Ctrl-C. */
static int wait_sock(LONG s, int write, long secs)
{
	fd_set fds;
	struct timeval tv;
	ULONG sigs;
	LONG n;

	for (;;) {
		FD_ZERO(&fds);
		FD_SET(s, &fds);
		tv.tv_sec = secs;
		tv.tv_usec = 0;
		sigs = SIGBREAKF_CTRL_C;
		n = WaitSelect(s + 1, write ? NULL : &fds, write ? &fds : NULL, NULL, (APTR)&tv, &sigs);
		if (n > 0)
			return 1;
		if (sigs & SIGBREAKF_CTRL_C)
			return -1;
		if (n == 0)
			return 0;
		if (Errno() != EINTR)
			return 1;    /* let the next call report the error */
	}
}

static int wait_fail(int w, const char *what)
{
	if (w < 0)
		sprintf(err_msg, "%s: cancelled", what);
	else
		sprintf(err_msg, "%s: the proxy stopped answering (link down?)", what);
	return 0;
}

static int send_all(LONG s, const char *p, long len)
{
	long n;
	int w;

	while (len > 0) {
		if ((w = wait_sock(s, 1, IDLE_SECS)) <= 0)
			return wait_fail(w, "sending");
		n = send(s, (APTR)p, len, 0);
		if (n < 0 && Errno() == EWOULDBLOCK)
			continue;
		if (n <= 0) {
			sprintf(err_msg, "sending failed (%ld)", Errno());
			return 0;
		}
		p += n;
		len -= n;
	}
	return 1;
}

static int connect_to(LONG s, struct sockaddr_in *sa)
{
	LONG err = 0, len = sizeof(err);
	int w;

	if (connect(s, (struct sockaddr *)sa, sizeof(*sa)) == 0)
		return 1;
	if (Errno() != EINPROGRESS) {
		err = Errno();
	} else if ((w = wait_sock(s, 1, CONNECT_SECS)) <= 0) {
		if (w < 0)
			return wait_fail(w, "connecting");
		sprintf(err_msg, "cannot connect to %s:%d (timeout)", cfg.host, cfg.port);
		return 0;
	} else if (getsockopt(s, SOL_SOCKET, SO_ERROR, &err, &len) < 0) {
		err = Errno();
	}
	if (err) {
		sprintf(err_msg, "cannot connect to %s:%d (%ld)", cfg.host, cfg.port, err);
		return 0;
	}
	return 1;
}

long http(const char *method, const char *path)
{
	static char req[1024];
	struct sockaddr_in sa;
	struct hostent *he;
	LONG s, n = 0, one = 1;
	long total = 0;
	char *body;
	int w = 1;

	memset(&sa, 0, sizeof(sa));
	sa.sin_family = AF_INET;
	sa.sin_port = htons(cfg.port);
	sa.sin_addr.s_addr = inet_addr((STRPTR)cfg.host);
	if (sa.sin_addr.s_addr == INADDR_NONE) {
		he = gethostbyname((STRPTR)cfg.host);
		if (!he) {
			sprintf(err_msg, "cannot find host %s", cfg.host);
			return -1;
		}
		memcpy(&sa.sin_addr, he->h_addr, 4);
	}

	s = socket(AF_INET, SOCK_STREAM, 0);
	if (s < 0) {
		sprintf(err_msg, "socket() failed (%ld)", Errno());
		return -1;
	}
	IoctlSocket(s, FIONBIO, (APTR)&one);
	if (!connect_to(s, &sa)) {
		CloseSocket(s);
		return -1;
	}
	sprintf(req, "%s %s%s%s HTTP/1.0\r\nHost: %s\r\nX-Token: %s\r\nConnection: close\r\n\r\n",
	        method, path, cfg.latin1 ? (strchr(path, '?') ? "&" : "?") : "",
	        cfg.latin1 ? "cs=latin1" : "", cfg.host, cfg.token);
	if (!send_all(s, req, strlen(req))) {
		CloseSocket(s);
		return -1;
	}
	/* read until the proxy closes; a half answer is no answer */
	while (total < RESP_MAX) {
		if ((w = wait_sock(s, 0, IDLE_SECS)) <= 0)
			break;
		n = recv(s, resp_buf + total, RESP_MAX - total, 0);
		if (n < 0 && Errno() == EWOULDBLOCK)
			continue;
		if (n <= 0)
			break;
		total += n;
	}
	CloseSocket(s);
	if (w <= 0) {
		wait_fail(w, "reading");
		return -1;
	}
	if (n < 0) {
		sprintf(err_msg, "no answer from the proxy (%ld)", Errno());
		return -1;
	}
	resp_buf[total] = 0;

	body = strstr(resp_buf, "\r\n\r\n");
	if (!body) {
		strcpy(err_msg, "broken answer from the proxy");
		return -1;
	}
	body += 4;
	total -= body - resp_buf;
	memmove(resp_buf, body, total + 1);
	return total;
}
