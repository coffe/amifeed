/* net.c - one HTTP/1.0 request per connection, through bsdsocket.library */
#include <exec/types.h>
#include <dos/dos.h>
#include <proto/exec.h>
#include <proto/dos.h>
#include <proto/bsdsocket.h>
#include <sys/socket.h>
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

static int send_all(LONG s, const char *p, long len)
{
	long n;

	while (len > 0) {
		n = send(s, (APTR)p, len, 0);
		if (n <= 0)
			return 0;
		p += n;
		len -= n;
	}
	return 1;
}

long http(const char *method, const char *path)
{
	static char req[1024];
	struct sockaddr_in sa;
	struct timeval tv;
	struct hostent *he;
	LONG s, n = 0;
	long total = 0;
	char *body;

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
	/* full=1 makes the proxy fetch a web page first: give it time */
	tv.tv_sec = 40;
	tv.tv_usec = 0;
	setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
	setsockopt(s, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));

	if (connect(s, (struct sockaddr *)&sa, sizeof(sa)) < 0) {
		sprintf(err_msg, "cannot connect to %s:%d (%ld)", cfg.host, cfg.port, Errno());
		CloseSocket(s);
		return -1;
	}
	sprintf(req, "%s %s%s%s HTTP/1.0\r\nHost: %s\r\nX-Token: %s\r\nConnection: close\r\n\r\n",
	        method, path, cfg.latin1 ? (strchr(path, '?') ? "&" : "?") : "",
	        cfg.latin1 ? "cs=latin1" : "", cfg.host, cfg.token);
	if (!send_all(s, req, strlen(req))) {
		sprintf(err_msg, "sending failed (%ld)", Errno());
		CloseSocket(s);
		return -1;
	}
	while (total < RESP_MAX && (n = recv(s, resp_buf + total, RESP_MAX - total, 0)) > 0)
		total += n;
	if (n < 0 && total == 0) {
		sprintf(err_msg, "no answer from the proxy (%ld)", Errno());
		CloseSocket(s);
		return -1;
	}
	CloseSocket(s);
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
